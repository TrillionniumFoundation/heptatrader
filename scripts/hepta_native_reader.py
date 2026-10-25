"""Bounded native snapshot transport shared by the driver and PnL observer.

The resident CLI keeps only NativeToolClient's credential-bound discovery cache.
Every business read still reaches the authoritative owner. This module has no
mutation, preview, permission-cache or retry path.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import time

INSTRUMENTS = frozenset({"EUR.USD", "BTC.USD"})
READ_TOOLS = frozenset({"system.get_health", "orders.list", "portfolio.list_positions", "market.get_quote"})


def validate_binary_binding(binding):
    if (not isinstance(binding, dict) or set(binding) != {"path", "sha256"}
            or type(binding["path"]) is not str or not Path(binding["path"]).is_absolute()
            or "\x00" in binding["path"] or type(binding["sha256"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", binding["sha256"]) is None):
        raise ValueError("NATIVE_READ_BINARY_BINDING_INVALID")


@contextmanager
def verified_binary(binding):
    """Keep the verified executable open through spawn; do not resolve a new path."""
    validate_binary_binding(binding)
    fd = os.open(binding["path"], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or before.st_nlink != 1 or before.st_mode & 0o022
                or not before.st_mode & stat.S_IXUSR or before.st_size > 256 * 1024 * 1024):
            raise ValueError("NATIVE_READ_BINARY_IDENTITY_INVALID")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > before.st_size:
                raise ValueError("NATIVE_READ_BINARY_CHANGED")
            digest.update(chunk)
        after = os.fstat(fd)
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
                                  value.st_ctime_ns, value.st_mode, value.st_uid, value.st_nlink)
        if identity(before) != identity(after) or digest.hexdigest() != binding["sha256"]:
            raise ValueError("NATIVE_READ_BINARY_CHANGED")
        os.lseek(fd, 0, os.SEEK_SET)
        yield fd
    finally:
        os.close(fd)


class NativeReader:
    read_tools = READ_TOOLS

    def __init__(self, prefix: Path, config: Path, socket: Path, *, persistent: bool = False,
                 parse_json, error_type=ValueError, timeout_seconds=4.0, io_timeout_ms=2000,
                 binary_binding=None):
        self.prefix, self.config, self.socket = prefix, config, socket
        self.persistent = persistent
        self._processes = {}
        self._closing = set()
        self.parse_json, self.error_type = parse_json, error_type
        self.timeout_seconds, self.io_timeout_ms = timeout_seconds, io_timeout_ms
        self.binary_binding = None
        if binary_binding is not None:
            try:
                validate_binary_binding(binary_binding)
                if not persistent:
                    raise ValueError("NATIVE_READ_BINARY_REQUIRES_STREAM")
                self.binary_binding = dict(binary_binding)
            except ValueError as error:
                raise self.error_type(str(error)) from error

    def _close_process(self, instrument):
        process = self._processes.get(instrument)
        if process is None:
            self._closing.discard(instrument)
            return
        self._closing.add(instrument)
        # Only the owned read-only CLI is terminated, never a trading service.
        if process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                # Keep the original handle and pipes. A later read may reap it
                # but must not start another CLI while cleanup remains pending.
                raise self.error_type("PNL_READ_STREAM_CLEANUP_PENDING") from None
        self._processes.pop(instrument)
        self._closing.discard(instrument)
        try:
            process.stdin.close()
        finally:
            process.stdout.close()

    def close(self):
        failure = None
        for instrument in list(self._processes):
            try:
                self._close_process(instrument)
            except Exception as error:
                if failure is None:
                    failure = error
        if failure is not None:
            raise failure

    def _start_stream(self, token):
        binary = self.prefix / "bin/heptactl" if self.binary_binding is None else self.binary_binding["path"]
        args = [str(binary), "--socket", str(self.socket), "--token-file", str(token),
                "--io-timeout-ms", str(self.io_timeout_ms), "pnl-read-stream"]
        options = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        if self.binary_binding is None:
            return subprocess.Popen(args, **options)
        try:
            with verified_binary(self.binary_binding) as fd:
                # Pocket4 is Linux. Execute the verified open inode so replacing
                # the selected path cannot substitute another executable.
                return subprocess.Popen(args, executable="/proc/self/fd/" + str(fd),
                                        pass_fds=(fd,), **options)
        except (OSError, ValueError) as error:
            raise self.error_type(str(error)) from error

    def _stream_read(self, instrument, tool, token):
        if instrument in self._closing:
            self._close_process(instrument)
        process = self._processes.get(instrument)
        if process is None:
            process = self._start_stream(token)
            self._processes[instrument] = process
        request = tool + (" " + instrument if tool == "market.get_quote" else "") + "\n"
        try:
            if process.poll() is not None:
                raise self.error_type("PNL_READ_STREAM_CLOSED")
            process.stdin.write(request.encode("ascii"))
            deadline = time.monotonic() + self.timeout_seconds
            data = bytearray()
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise self.error_type("PNL_READ_STREAM_TIMEOUT")
                    chunk = os.read(process.stdout.fileno(), min(65536, 1048578 - len(data)))
                    if not chunk:
                        raise self.error_type("PNL_READ_STREAM_CLOSED")
                    data.extend(chunk)
                    if len(data) > 1048577:
                        raise self.error_type("PNL_READ_STREAM_RESPONSE_TOO_LARGE")
                    if b"\n" in data:
                        line, extra = bytes(data).split(b"\n", 1)
                        if extra:
                            raise self.error_type("PNL_READ_STREAM_UNEXPECTED_RESPONSE")
                        value = self.parse_json(line.decode("utf-8"))
                        if not isinstance(value, dict):
                            raise self.error_type("PNL_READ_STREAM_RESPONSE_INVALID")
                        if value.get("tool") != tool:
                            raise self.error_type("PNL_READ_STREAM_TOOL_MISMATCH")
                        if value.get("status") != "ok" or not isinstance(value.get("payload"), dict):
                            raise self.error_type(str(value.get("reason_code") or "NATIVE_READ_FAILED"))
                        return value["payload"]
        except (OSError, ValueError, self.error_type):
            # The failed read is never retried. A later read may start a new
            # reader and obtain a newly verified discovery catalog.
            self._close_process(instrument)
            raise

    def __call__(self, instrument: str, tool: str) -> dict:
        if instrument not in INSTRUMENTS or tool not in READ_TOOLS:
            raise self.error_type("PNL_READ_ONLY_TOOL_ALLOWLIST")
        token = self.config / ("crypto.token" if instrument == "BTC.USD" else "paper.token")
        if self.persistent:
            return self._stream_read(instrument, tool, token)
        # Never read/log the token's contents; pass the existing private path.
        args = [str(self.prefix / "bin/heptactl"), "--socket", str(self.socket),
                "--token-file", str(token), "--io-timeout-ms", str(self.io_timeout_ms), "call", tool]
        if tool == "market.get_quote":
            args.append("instrument=" + instrument)
        result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=self.timeout_seconds)
        try:
            value = self.parse_json(result.stdout)
        except (ValueError, UnicodeError, self.error_type):
            raise self.error_type("NATIVE_READ_TRANSPORT_FAILED") from None
        if not isinstance(value, dict):
            raise self.error_type("NATIVE_READ_RESPONSE_INVALID")
        if result.returncode or value.get("status") != "ok" or not isinstance(value.get("payload"), dict):
            raise self.error_type(str(value.get("reason_code") or "NATIVE_READ_FAILED"))
        return value["payload"]
