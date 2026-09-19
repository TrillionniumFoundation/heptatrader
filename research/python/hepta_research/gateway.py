"""Unprivileged strategy adapter using the EXISTING heptactl/native client.

The private outbox remembers requests, not positions or execution truth. It
never generates an execution command ID. After a possible send, every repeated
submit is a read-only query of the original ID, never a second placement.
Linux/POSIX only: fail closed without owner-only files, flock and durable fsync.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
from typing import Iterator

from .model import number

MAX_BYTES = 1024 * 1024
IDENTITY = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
TOOLS = frozenset(("risk.preview_order", "trade.place_order", "execution.get_command_status"))


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


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
    def invalid_constant(_):
        raise ValueError("nonfinite JSON")
    value = json.loads(data, object_pairs_hook=pairs, parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def _identity(value: str) -> str:
    if not isinstance(value, str) or not IDENTITY.fullmatch(value):
        raise ValueError("invalid identity")
    return value


def _private(fd: int, directory: bool = False) -> None:
    s = os.fstat(fd)
    kind = stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode)
    if not kind or s.st_uid != os.geteuid() or stat.S_IMODE(s.st_mode) & 0o077:
        raise ValueError("owner-only regular file/directory required")
    if not directory and s.st_nlink != 1:
        raise ValueError("hard-linked private file")


def _read(fd: int, limit: int = MAX_BYTES) -> bytes:
    result = bytearray()
    while True:
        part = os.read(fd, min(65536, limit + 1 - len(result)))
        if not part:
            return bytes(result)
        result.extend(part)
        if len(result) > limit:
            raise ValueError("private file size bound")


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

    def fields(self) -> dict[str, str]:
        for value in (self.instrument, self.symbol, self.exchange, self.currency):
            _identity(value)
        # The current heptactl surface cannot represent full futures/options
        # identity. Never silently erase expiry/strike/open-close information.
        if self.sec_type not in ("CASH", "STK") or self.side not in ("BUY", "SELL"):
            raise ValueError("unsupported contract/side; no legacy CTP fallback")
        if type(self.expires_at_ms) is not int or not 0 < self.expires_at_ms < 2**63:
            raise ValueError("expiry")
        result = {"instrument": self.instrument, "symbol": self.symbol, "sec_type": self.sec_type,
                  "exchange": self.exchange, "currency": self.currency, "side": self.side,
                  "order_type": "LMT", "tif": "DAY", "expires_at_ms": str(self.expires_at_ms)}
        for key in ("quantity", "limit_price", "reference_price"):
            value = number(getattr(self, key), positive=True)
            if value > number("1e12"):
                raise ValueError("client numeric bound")
            result[key] = format(value, "f")
        return result


class HeptactlTransport:
    def __init__(self, executable: str, socket: str, token_file: str, timeout_ms: int = 5000) -> None:
        if not all(Path(p).is_absolute() for p in (executable, socket, token_file)):
            raise ValueError("explicit absolute client/socket/token paths required")
        if type(timeout_ms) is not int or not 1 <= timeout_ms <= 120000:
            raise ValueError("timeout")
        self.executable, self.socket, self.token_file, self.timeout_ms = executable, socket, token_file, timeout_ms

    def scope(self) -> str:
        fd = os.open(self.token_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        try:
            _private(fd)
            token = _read(fd, 4096).strip()
            if not token:
                raise ValueError("empty session token")
            return hashlib.sha256(self.socket.encode()+b"\0"+token).hexdigest()
        finally:
            os.close(fd)

    def call(self, tool: str, call_id: str, fields: dict[str, str]) -> dict:
        if tool not in TOOLS:
            raise ValueError("unsupported research adapter tool")
        _identity(call_id)
        args = [self.executable, "--socket", self.socket, "--token-file", self.token_file,
                "--call-id", call_id, "--io-timeout-ms", str(self.timeout_ms), "call", tool]
        for key, value in sorted(fields.items()):
            if not isinstance(value, str) or not value or len(value)>4096 or "\0" in value:
                raise ValueError("invalid wire value")
            args.append(key+"="+value)
        # Do not inherit session-token overrides, LD_PRELOAD, broker credentials,
        # or other process environment inputs. The executable is explicit.
        env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as error:
            process = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=output, stderr=error,
                                     env=env, timeout=self.timeout_ms/1000+2, check=False)
            output.seek(0)
            envelope = _loads(output.read(MAX_BYTES+1))
        expected = {"status", "tool", "reason_code", "detail", "order_id", "payload"}
        if set(envelope) != expected or envelope.get("tool") != tool:
            raise ValueError("unexpected heptactl result envelope")
        if envelope["status"] not in ("ok", "permission_denied", "invalid_tool", "rejected", "duplicate", "uncertain", "error"):
            raise ValueError("unknown result status")
        if type(envelope["order_id"]) is not int or envelope["order_id"] < -1:
            raise ValueError("invalid result order identity")
        if not isinstance(envelope["reason_code"], str) or not isinstance(envelope["detail"], str):
            raise ValueError("invalid result detail")
        if envelope["payload"] is not None and not isinstance(envelope["payload"], dict):
            raise ValueError("invalid result payload")
        if ((envelope["status"] == "ok" and process.returncode != 0) or
                (envelope["status"] == "duplicate" and process.returncode != 7)):
            raise RuntimeError("ambiguous heptactl process result")
        return envelope


class Outbox:
    def __init__(self, directory: str) -> None:
        self.path = Path(directory)
        if not self.path.is_absolute():
            raise ValueError("absolute outbox directory required")
        try:
            self.path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            _private(fd, True)
        finally:
            os.close(fd)

    @contextmanager
    def locked(self, key: str) -> Iterator[tuple[int, str]]:
        _identity(key)
        name = hashlib.sha256(key.encode()).hexdigest()
        dfd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        lock = None
        try:
            _private(dfd, True)
            lock = os.open(name+".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                           0o600, dir_fd=dfd)
            _private(lock)
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield dfd, name+".json"
        finally:
            if lock is not None:
                os.close(lock)
            os.close(dfd)

    @staticmethod
    def load(dfd: int, name: str) -> dict | None:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=dfd)
        except FileNotFoundError:
            return None
        try:
            _private(fd)
            return _loads(_read(fd))
        finally:
            os.close(fd)

    @staticmethod
    def save(dfd: int, name: str, value: dict) -> None:
        data = _json(value)
        if len(data) > MAX_BYTES:
            raise ValueError("outbox record size bound")
        temporary = name+"."+os.urandom(16).hex()+".tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=dfd)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, name, src_dir_fd=dfd, dst_dir_fd=dfd)
            os.fsync(dfd)
        finally:
            try:
                os.unlink(temporary, dir_fd=dfd)
            except FileNotFoundError:
                pass


class StrategyGateway:
    """Explicit two-phase client. Construction and prepare NEVER place orders."""
    def __init__(self, transport: HeptactlTransport, outbox: Outbox, clock_ms=None) -> None:
        self.transport, self.outbox = transport, outbox
        self.clock_ms = clock_ms or (lambda: time.time_ns()//1000000)

    def _validate(self, record: dict) -> None:
        if record.get("schema") != "hepta.research.outbox.v1" or record.get("state") not in (
                "prepared", "sending", "uncertain", "answered", "expired"):
            raise ValueError("unsupported/corrupt outbox record")
        if record.get("scope") != self.transport.scope():
            raise ValueError("session/socket changed: explicit recovery required")
        if record.get("intent_sha256") != hashlib.sha256(_json(record.get("fields"))).hexdigest():
            raise ValueError("corrupt normalized intent")
        _identity(record.get("command_id"))
        if not isinstance(record.get("preview_permit"), str) or not record["preview_permit"]:
            raise ValueError("missing server permit")

    def prepare(self, key: str, intent: LimitIntent) -> dict:
        fields = intent.fields()
        digest = hashlib.sha256(_json(fields)).hexdigest()
        with self.outbox.locked(key) as (dfd, name):
            old = self.outbox.load(dfd, name)
            if old is not None:
                self._validate(old)
                if old["intent_sha256"] != digest:
                    raise ValueError("intent key reused with different normalized fields")
                return old
            if intent.expires_at_ms <= self.clock_ms():
                raise ValueError("expired intent")
            scope = self.transport.scope()
            result = self.transport.call("risk.preview_order", "research-preview-"+name[:32], fields)
            payload = result.get("payload")
            if result.get("status") != "ok" or not isinstance(payload, dict) or payload.get("approved") is not True or payload.get("single_use") is not True:
                raise ValueError("preview did not approve a single-use intent")
            command = _identity(payload.get("command_id"))
            permit = payload.get("preview_permit")
            expiry = payload.get("permit_expires_at_ms")
            if not isinstance(permit, str) or not 1 <= len(permit) <= 4096 or "\0" in permit:
                raise ValueError("invalid preview permit")
            if type(expiry) is not int or expiry <= self.clock_ms():
                raise ValueError("expired/invalid preview permit")
            if not isinstance(payload.get("service_epoch"), str) or not payload["service_epoch"] or type(payload.get("service_fencing_generation")) is not int or payload["service_fencing_generation"] <= 0:
                raise ValueError("missing execution service identity")
            if self.transport.scope() != scope:
                raise ValueError("session changed during preview")
            record = {"schema": "hepta.research.outbox.v1", "state": "prepared", "scope": scope,
                      "fields": fields, "intent_sha256": digest, "command_id": command,
                      "preview_permit": permit, "permit_expires_at_ms": expiry,
                      "service_epoch": payload["service_epoch"],
                      "service_fencing_generation": payload["service_fencing_generation"]}
            self.outbox.save(dfd, name, record)
            return record

    def submit(self, key: str) -> dict:
        with self.outbox.locked(key) as (dfd, name):
            record = self.outbox.load(dfd, name)
            if record is None:
                raise ValueError("prepare and durably store the intent first")
            self._validate(record)
            if record["state"] != "prepared":
                # Even 'answered' is not a fill. Preserve the original ID and
                # ask the service; never turn transport error into a new order.
                return self.transport.call("execution.get_command_status", "research-query-"+name[:32],
                                           {"command_id": record["command_id"]})
            if min(int(record["fields"]["expires_at_ms"]), record["permit_expires_at_ms"]) <= self.clock_ms():
                record["state"] = "expired"
                self.outbox.save(dfd, name, record)
                raise ValueError("unsubmitted preview expired")
            fields = dict(record["fields"], preview_permit=record["preview_permit"])
            record["state"] = "sending"
            self.outbox.save(dfd, name, record) # fsync MUST succeed before any placement
            try:
                result = self.transport.call("trade.place_order", record["command_id"], fields)
            except Exception:
                record["state"] = "uncertain"
                self.outbox.save(dfd, name, record)
                raise RuntimeError("placement response unavailable; query original command, do not re-place") from None
            record["state"] = "uncertain" if result["status"] in ("uncertain", "error") else "answered"
            record["response"] = result
            self.outbox.save(dfd, name, record)
            return result
