#!/usr/bin/env python3
"""Application-key policy over the canonical NativeStrategyClient (POSIX only).

HSA1 is application bookkeeping, NOT a request outbox or an OMS. Native HSR1
remains the only request representation. No token, permit, account, position,
fill, socket codec or trading authority is implemented or persisted here.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from typing import Iterator
import uuid

MAX_BYTES = 1024 * 1024
IDENTITY = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
COMMAND = re.compile(r"[A-Za-z0-9_.:-]{8,128}\Z")
BINDING = re.compile(r"sha256:[0-9a-f]{64}\Z")
FORMAT = b"HSA1\n"
SCHEMA = "hepta.strategy-application.v1"


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def _loads(data: bytes) -> dict:
    if len(data) > MAX_BYTES:
        raise ValueError("JSON size bound")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    def invalid(_):
        raise ValueError("nonfinite JSON")
    value = json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def _identity(value: str, pattern=IDENTITY) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError("invalid identity")
    return value


def _absolute(value: str) -> str:
    if not isinstance(value, str) or not 1 < len(value) <= 4096 or not value.startswith("/") or "\0" in value:
        raise ValueError("explicit absolute path required")
    if any(part in ("", ".", "..") for part in value.split("/")[1:]):
        raise ValueError("noncanonical path")
    return value


def _private(fd: int, directory: bool = False) -> None:
    info = os.fstat(fd)
    kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not kind or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600):
        raise ValueError("private owned 0700 directory / 0600 regular file required")
    if not directory and info.st_nlink != 1:
        raise ValueError("hard-linked application file")


def _open_absolute(path: str) -> int:
    _absolute(path)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.split("/")[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read(dfd: int, name: str) -> bytes | None:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dfd)
    except FileNotFoundError:
        return None
    try:
        _private(fd)
        data = bytearray()
        while True:
            part = os.read(fd, min(65536, MAX_BYTES + 1 - len(data)))
            if not part:
                return bytes(data)
            data.extend(part)
            if len(data) > MAX_BYTES:
                raise ValueError("application record size bound")
    finally:
        os.close(fd)


def _publish(dfd: int, name: str, data: bytes) -> None:
    """Immutable no-replace publication. A failed sync never licenses sending."""
    if len(data) > MAX_BYTES:
        raise ValueError("application record size bound")
    old = _read(dfd, name)
    if old is not None:
        if old != data:
            raise ValueError("immutable application record conflict")
        # Do not trust a record merely because a failed previous sync left it.
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dfd)
        try:
            _private(fd)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(dfd)
        return
    temporary = ".publish-" + uuid.uuid4().hex
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600, dir_fd=dfd)
    try:
        _private(fd)
        view = memoryview(data)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise OSError("short application write")
            view = view[count:]
        os.fsync(fd)
        os.link(temporary, name, src_dir_fd=dfd, dst_dir_fd=dfd, follow_symlinks=False)
        os.unlink(temporary, dir_fd=dfd)  # published private record must have one link
        os.fsync(dfd)
    finally:
        os.close(fd)
        try:
            os.unlink(temporary, dir_fd=dfd)
        except FileNotFoundError:
            pass


@contextmanager
def _locked_file(dfd: int, name: str) -> Iterator[None]:
    fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                 0o600, dir_fd=dfd)
    try:
        _private(fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        seen = os.stat(name, dir_fd=dfd, follow_symlinks=False)
        held = os.fstat(fd)
        if (seen.st_dev, seen.st_ino) != (held.st_dev, held.st_ino):
            raise ValueError("application lock replaced")
        yield
    finally:
        os.close(fd)


class ApplicationStore:
    """Dedicated HSA1 directory; existing legacy records never become empty state."""
    def __init__(self, directory: str) -> None:
        self.path = _absolute(directory)
        parent_path, name = self.path.rsplit("/", 1)
        parent = _open_absolute(parent_path) if parent_path else os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        fd = None
        try:
            try:
                os.mkdir(name, mode=0o700, dir_fd=parent)
            except FileExistsError:
                pass
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
            _private(fd, True)
            if _read(fd, ".format") is None and set(os.listdir(fd)) - {".init.lock"}:
                raise ValueError("legacy/unrecognized application store; retain original records")
            with _locked_file(fd, ".init.lock"):
                marker = _read(fd, ".format")
                if marker is None:
                    if set(os.listdir(fd)) != {".init.lock"}:
                        raise ValueError("legacy/unrecognized application store; retain original records")
                    _publish(fd, ".format", FORMAT)
                elif marker != FORMAT:
                    raise ValueError("unsupported application store")
                if any(name not in (".format", ".init.lock") and not re.fullmatch(r"[0-9a-f]{64}(?:\.lock)?", name)
                       for name in os.listdir(fd)):
                    raise ValueError("unrecognized legacy asset in application store")
                os.fsync(fd)
                os.fsync(parent)
        finally:
            if fd is not None:
                os.close(fd)
            os.close(parent)

    @contextmanager
    def locked(self, key: str, create: bool = False) -> Iterator[tuple[int, str]]:
        key = _identity(key)
        name = hashlib.sha256(key.encode("ascii")).hexdigest()
        root = _open_absolute(self.path)
        child = None
        try:
            _private(root, True)
            if _read(root, ".format") != FORMAT:
                raise ValueError("missing/corrupt application store marker")
            with _locked_file(root, name + ".lock"):
                if create:
                    try:
                        os.mkdir(name, mode=0o700, dir_fd=root)
                    except FileExistsError:
                        pass
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root)
                _private(child, True)
                os.fsync(child)
                os.fsync(root)
                yield child, self.path + "/" + name
        finally:
            if child is not None:
                os.close(child)
            os.close(root)


@dataclass(frozen=True)
class LimitIntent:
    instrument: str
    symbol: str
    sec_type: str
    exchange: str
    currency: str
    side: str
    quantity: str
    limit_price: str
    reference_price: str
    expires_at_ms: int

    def fields(self) -> dict:
        result = asdict(self)
        for name in ("instrument", "symbol", "exchange", "currency"):
            _identity(result[name])
        if self.sec_type not in ("STK", "CASH") or self.side not in ("BUY", "SELL"):
            raise ValueError("selected application profile is STK/CASH LMT/DAY BUY/SELL")
        if type(self.expires_at_ms) is not int or not 0 < self.expires_at_ms < 2**63:
            raise ValueError("invalid expiry")
        for name in ("quantity", "limit_price", "reference_price"):
            text = result[name]
            if not isinstance(text, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", text) or len(text) > 128:
                raise ValueError("bounded positive decimal string required")
            try:
                decimal = Decimal(text)
                number = float(decimal)
            except (InvalidOperation, OverflowError, ValueError):
                raise ValueError("unrepresentable native numeric input") from None
            # Accept decimal spellings that round-trip through the shortest
            # binary64 decimal. Do not collapse a wider Decimal onto a new value.
            if not math.isfinite(number) or not 0 < number <= 1e12 or Decimal(str(number)) != decimal:
                raise ValueError("native binary64 round-trip/numeric bound")
            result[name] = format(decimal, "f").rstrip("0").rstrip(".") if "." in format(decimal, "f") else format(decimal, "f")
        return result


class NativeClientError(RuntimeError):
    def __init__(self, response: dict):
        self.response = response
        super().__init__(response.get("reason") or "native client failed; preserve original command")


class NativeStrategyTransport:
    """Invoke the installed SDK adapter, never heptactl's generic mutation API."""
    def __init__(self, executable: str, socket: str, token_file: str, timeout_ms: int = 5000) -> None:
        self.executable, self.socket, self.token_file = map(_absolute, (executable, socket, token_file))
        if type(timeout_ms) is not int or not 1 <= timeout_ms <= 120000:
            raise ValueError("invalid timeout")
        self.timeout_ms = timeout_ms

    def call(self, operation: str, *, directory: str = "", binding: str = "", fields: dict | None = None,
             command_id: str = "", call_id: str = "") -> dict:
        if operation not in ("binding", "prepare", "validate", "submit", "inspect"):
            raise ValueError("unsupported native operation")
        args = [self.executable, operation, "--socket", self.socket, "--token-file", self.token_file,
                "--timeout-ms", str(self.timeout_ms)]
        if operation != "binding":
            _absolute(directory)
            _identity(binding, BINDING)
            if not isinstance(fields, dict) or LimitIntent(**fields).fields() != fields:
                raise ValueError("normalized intent required")
            args += ["--directory", directory, "--binding", binding]
            for name, value in sorted(fields.items()):
                args += ["--" + name.replace("_", "-"), str(value)]
            if operation != "prepare":
                args += ["--command-id", _identity(command_id, COMMAND)]
            if operation in ("prepare", "inspect"):
                args += ["--call-id", _identity(call_id, COMMAND)]
        # No inherited credential overrides, broker environment or loader hooks.
        env = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
        output = {"out": bytearray(), "err": bytearray()}
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=env, start_new_session=True)
        try:
            deadline = time.monotonic() + self.timeout_ms / 1000 + 2
            with selectors.DefaultSelector() as selector:
                for stream, name in ((process.stdout, "out"), (process.stderr, "err")):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ, name)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("native process deadline; query original command")
                    for entry, _ in selector.select(remaining):
                        data = os.read(entry.fileobj.fileno(), 65536)
                        if not data:
                            selector.unregister(entry.fileobj)
                        else:
                            output[entry.data].extend(data)
                            if len(output[entry.data]) > MAX_BYTES:
                                raise ValueError("native process output bound")
                returncode = process.wait(timeout=max(0.001, deadline - time.monotonic()))
            response = _loads(bytes(output["out"]))
            if set(response) != {"schema", "operation", "ok", "command_id", "binding", "reason", "result"} or response["schema"] != "hepta.strategy-client-cli.v1" or response["operation"] != operation:
                raise ValueError("unexpected native process response")
            if type(response["ok"]) is not bool or returncode != (0 if response["ok"] else 2):
                raise ValueError("ambiguous native process result")
            if not isinstance(response["reason"], str):
                raise ValueError("invalid native diagnostic")
            if not response["ok"]:
                raise NativeClientError(response)
            _identity(response["binding"], BINDING)
            if operation != "binding":
                _identity(response["command_id"], COMMAND)
                if response["binding"] != binding or (command_id and response["command_id"] != command_id):
                    raise ValueError("native identity/binding mismatch")
            if operation in ("submit", "inspect"):
                result = response["result"]
                tool = "trade.place_order" if operation == "submit" else "execution.get_command_status"
                if not isinstance(result, dict) or result.get("tool") != tool or result.get("status") not in (
                    "ok", "permission_denied", "invalid_tool", "rejected", "duplicate", "uncertain", "error"):
                    raise ValueError("unexpected native service envelope")
            elif response["result"] is not None:
                raise ValueError("unexpected native preparation payload")
            return response
        finally:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            process.stdout.close()
            process.stderr.close()

    def scope(self) -> str:
        return self.call("binding")["binding"]


class StrategyGateway:
    def __init__(self, transport: NativeStrategyTransport, store: ApplicationStore, clock_ms=None) -> None:
        self.transport, self.store = transport, store
        self.clock_ms = clock_ms or (lambda: time.time_ns() // 1000000)

    def _record(self, dfd: int, key: str) -> dict:
        data = _read(dfd, "intent.json")
        if data is None:
            raise ValueError("prepare the application intent first")
        record = _loads(data)
        if set(record) != {"schema", "key", "fields", "binding"} or record["schema"] != SCHEMA or record["key"] != key:
            raise ValueError("invalid application intent record")
        if not isinstance(record["fields"], dict) or LimitIntent(**record["fields"]).fields() != record["fields"]:
            raise ValueError("invalid stored normalized intent")
        _identity(record["binding"], BINDING)
        if record["binding"] != self.transport.scope():
            raise ValueError("session/endpoint/UID changed; explicit recovery required")
        return record

    def _invoke(self, operation: str, path: str, record: dict, command: str = "") -> dict:
        return self.transport.call(operation, directory=path + "/requests", binding=record["binding"],
            fields=record["fields"], command_id=command,
            call_id="application-" + ("preview-" if operation == "prepare" else "query-") + uuid.uuid4().hex)

    @staticmethod
    def _command(dfd: int) -> str:
        data = _read(dfd, "command.id")
        if data is None:
            raise ValueError("PREPARATION_INCOMPLETE: retain records; explicitly adopt the original command ID")
        return _identity(data.decode("ascii"), COMMAND)

    def prepare(self, key: str, intent: LimitIntent) -> dict:
        fields = intent.fields()
        with self.store.locked(key, create=True) as (dfd, path):
            if _read(dfd, "intent.json") is not None:
                record = self._record(dfd, key)
                if record["fields"] != fields:
                    raise ValueError("application key reused with a different intent")
                command = self._command(dfd)
                self._invoke("validate", path, record, command)
                return {"command_id": command, "prepared": True}
            if set(os.listdir(dfd)):
                raise ValueError("unrecognized incomplete application directory")
            if intent.expires_at_ms <= self.clock_ms():
                raise ValueError("expired unprepared intent")
            record = {"schema": SCHEMA, "key": key, "fields": fields, "binding": self.transport.scope()}
            _identity(record["binding"], BINDING)
            # Intent is the conservative PREPARING marker: a crash or ambiguous
            # preview/persistence never obtains a replacement preview implicitly.
            _publish(dfd, "intent.json", _json(record))
            os.mkdir("requests", mode=0o700, dir_fd=dfd)
            rfd = os.open("requests", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dfd)
            try:
                _private(rfd, True)
                os.fsync(rfd)
                os.fsync(dfd)
            finally:
                os.close(rfd)
            response = self._invoke("prepare", path, record)
            command = _identity(response["command_id"], COMMAND)
            _publish(dfd, "command.id", command.encode("ascii"))
            return {"command_id": command, "prepared": True}

    def adopt_preparation(self, key: str, command_id: str) -> dict:
        """Explicit recovery after lost Prepare output; no preview or send."""
        command = _identity(command_id, COMMAND)
        with self.store.locked(key) as (dfd, path):
            record = self._record(dfd, key)
            self._invoke("validate", path, record, command)
            _publish(dfd, "command.id", command.encode("ascii"))
            return {"command_id": command, "prepared": True}

    def submit(self, key: str) -> dict:
        with self.store.locked(key) as (dfd, path):
            record, command = self._record(dfd, key), self._command(dfd)
            marker = _read(dfd, "possibly-sent")
            if marker is not None:
                if marker != command.encode("ascii"):
                    raise ValueError("corrupt possibly-sent marker")
                return self._invoke("inspect", path, record, command)["result"]
            self._invoke("validate", path, record, command)
            if record["fields"]["expires_at_ms"] <= self.clock_ms():
                raise ValueError("unsubmitted intent expired")
            _publish(dfd, "possibly-sent", command.encode("ascii"))
            # Never clear this marker, even for unknown/rejected/transport error
            # or death BEFORE the child sends. Conservatively prefer no resend.
            return self._invoke("submit", path, record, command)["result"]

    def inspect(self, key: str) -> dict:
        with self.store.locked(key) as (dfd, path):
            record, command = self._record(dfd, key), self._command(dfd)
            return self._invoke("inspect", path, record, command)["result"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "submit", "inspect", "adopt-preparation"))
    parser.add_argument("--native", required=True)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--store", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--timeout-ms", type=int, default=5000)
    parser.add_argument("--command-id")
    for name in LimitIntent.__dataclass_fields__:
        parser.add_argument("--" + name.replace("_", "-"), type=int if name == "expires_at_ms" else str)
    args = parser.parse_args(argv)
    try:
        supplied = {name: getattr(args, name) for name in LimitIntent.__dataclass_fields__}
        if args.operation == "prepare":
            if any(value is None for value in supplied.values()) or args.command_id is not None:
                raise ValueError("prepare requires exactly one complete intent and no command ID")
            intent = LimitIntent(**supplied)
            intent.fields()  # Reject invalid input before initializing any state.
        elif any(value is not None for value in supplied.values()) or ((args.command_id is not None) != (args.operation == "adopt-preparation")):
            raise ValueError("unexpected intent or command-ID arguments")
        gateway = StrategyGateway(NativeStrategyTransport(args.native, args.socket, args.token_file, args.timeout_ms),
                                  ApplicationStore(args.store))
        if args.operation == "prepare":
            response = gateway.prepare(args.key, intent)
        elif args.operation == "adopt-preparation":
            response = gateway.adopt_preparation(args.key, args.command_id)
        else:
            response = getattr(gateway, args.operation)(args.key)
        print(_json(response).decode("ascii"), flush=True)
        return 0
    except (OSError, ValueError, RuntimeError, TypeError, subprocess.SubprocessError) as error:
        # A failed process is never an assertion that no order was sent.
        print(_json({"error": str(error), "recovery": "preserve original ID; no automatic resend"}).decode("ascii"),
              file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
