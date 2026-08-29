#!/usr/bin/env python3

"""Broker-free socket-activated daemon used only by the P1 dual gate.

The daemon implements a tiny AF_UNIX lifecycle protocol.  It has no order,
broker or network code.  Its persistent counter and cleanup tombstone let the
inner gate prove watchdog/crash restart fencing and stale-generation rejection
under effective systemd.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import stat
import sys
import time
from typing import Optional
import uuid


DOMAINS = {
    ("WATCH", "codex-a"): (2211, 2211),
    ("WATCH", "openclaw-b"): (2212, 2212),
    ("PAPER_INERT", "codex-a"): (2231, 2231),
    ("PAPER_INERT", "openclaw-b"): (2232, 2232),
}
DOMAIN = re.compile(r"^(?:codex-a|openclaw-b)$")
MAX_REQUEST = 4096
STOP = False


class FixtureError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise FixtureError(message)


def plane_leaf(plane: str) -> str:
    return "watch" if plane == "WATCH" else "paper"


def runtime_directory(plane: str, domain: str) -> Path:
    return Path(f"/run/hepta-p1-{plane_leaf(plane)}-{domain}")


def state_directory(plane: str, domain: str) -> Path:
    return Path(f"/var/lib/hepta-p1-{plane_leaf(plane)}-{domain}")


def socket_path(plane: str, domain: str) -> Path:
    return Path(f"/run/hepta-p1-dual/{plane_leaf(plane)}-{domain}.sock")


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_atomic(path: Path, raw: bytes, mode: int = 0o600) -> None:
    temporary = path.with_name("." + path.name + ".tmp-" + uuid.uuid4().hex)
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                fail("short fixture write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(temporary, mode)
    os.replace(temporary, path)
    fsync_directory(path.parent)


def read_regular(path: Path, maximum: int = 4096) -> bytes:
    descriptor = os.open(
        path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if (
                not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_size < 1 or before.st_size > maximum):
            fail("fixture input metadata mismatch")
        raw = os.read(descriptor, maximum + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if len(raw) > maximum:
        fail("fixture input exceeds bound")
    fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, name) != getattr(after, name) for name in fields):
        fail("fixture input changed while reading")
    return raw


def require_owned_directory(path: Path, uid: int, gid: int) -> None:
    metadata = os.lstat(path)
    if (
            not stat.S_ISDIR(metadata.st_mode) or
            stat.S_ISLNK(metadata.st_mode) or
            metadata.st_uid != uid or metadata.st_gid != gid or
            stat.S_IMODE(metadata.st_mode) != 0o700):
        fail("systemd managed directory metadata mismatch")


def require_identity(plane: str, domain: str) -> tuple[int, int]:
    if DOMAIN.fullmatch(domain) is None or (plane, domain) not in DOMAINS:
        fail("unknown fixture plane/domain")
    uid, gid = DOMAINS[(plane, domain)]
    if os.geteuid() != uid or os.getegid() != gid or os.getgroups():
        fail("fixture process identity mismatch")
    return uid, gid


def require_inert_inputs(plane: str, domain: str) -> None:
    credentials = os.environ.get("CREDENTIALS_DIRECTORY", "")
    if not credentials.startswith("/run/credentials/"):
        fail("systemd credential directory missing")
    fixture = Path(credentials) / "fixture"
    expected = (
        b"INERT_P1_WATCH_FIXTURE_NO_EXTERNAL_AUTHORITY\n"
        if plane == "WATCH" else
        b"INERT_P1_PAPER_FIXTURE_NO_BROKER_CREDENTIAL\n")
    if read_regular(fixture) != expected:
        fail("inert credential mismatch")
    if plane == "PAPER_INERT":
        for name in (
                "HEPTA_P1_PAPER_AUTHORIZED", "HEPTA_P1_LIVE_AUTHORIZED",
                "HEPTA_P1_MUTATION_AUTHORIZED",
                "HEPTA_P1_DIRECT_BROKER_ACCESS"):
            if os.environ.get(name) != "0":
                fail("PAPER fixture authority environment mismatch")
        kill_switch = Path(
            f"/run/hepta-p1-dual/control/paper-{domain}/kill-switch")
        if read_regular(kill_switch) != b"engaged\n":
            fail("PAPER kill switch is not engaged")


def next_generation(state: Path) -> int:
    counter = state / "generation.counter"
    if counter.exists():
        raw = read_regular(counter, maximum=32)
        if re.fullmatch(rb"[1-9][0-9]{0,9}\n", raw) is None:
            fail("generation counter malformed")
        previous = int(raw[:-1], 10)
    else:
        previous = 0
    generation = previous + 1
    if generation > 1_000_000_000:
        fail("generation bound exceeded")
    write_atomic(counter, f"{generation}\n".encode("ascii"))
    return generation


def active_payload(plane: str, domain: str, generation: int) -> bytes:
    return (json.dumps({
        "schema": "hepta.p1-dual-domain-active-generation.v1",
        "plane": plane,
        "domain_id": domain,
        "generation": generation,
    }, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def parse_active(raw: bytes, plane: str, domain: str) -> int:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FixtureError("active generation malformed") from error
    if (
            not isinstance(value, dict) or set(value) != {
                "schema", "plane", "domain_id", "generation"} or
            value.get("schema") !=
            "hepta.p1-dual-domain-active-generation.v1" or
            value.get("plane") != plane or value.get("domain_id") != domain or
            type(value.get("generation")) is not int or
            value["generation"] <= 0):
        fail("active generation contract mismatch")
    return value["generation"]


def prepare_runtime(plane: str, domain: str) -> int:
    uid, gid = require_identity(plane, domain)
    runtime = runtime_directory(plane, domain)
    state = state_directory(plane, domain)
    require_owned_directory(runtime, uid, gid)
    require_owned_directory(state, uid, gid)
    generation = next_generation(state)
    write_atomic(
        runtime / "active-generation.json",
        active_payload(plane, domain, generation))
    token = ("INERT_P1_SESSION_TOKEN_V1\n" + uuid.uuid4().hex + "\n").encode(
        "ascii")
    write_atomic(runtime / "session.token", token)
    return generation


def cleanup(plane: str, domain: str) -> None:
    require_identity(plane, domain)
    runtime = runtime_directory(plane, domain)
    state = state_directory(plane, domain)
    active = runtime / "active-generation.json"
    token = runtime / "session.token"
    if active.exists():
        generation = parse_active(read_regular(active), plane, domain)
        tombstone = (json.dumps({
            "schema": "hepta.p1-dual-domain-generation-tombstone.v1",
            "plane": plane,
            "domain_id": domain,
            "generation": generation,
            "authority_empty": True,
        }, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        write_atomic(state / "generation.tombstone.json", tombstone)
    for path in (token, active):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    if runtime.exists():
        fsync_directory(runtime)


def notify(payload: str) -> None:
    address = os.environ.get("NOTIFY_SOCKET", "")
    if not address:
        fail("NOTIFY_SOCKET missing")
    if address.startswith("@"):
        address = "\0" + address[1:]
    notifier = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        notifier.connect(address)
        notifier.sendall(payload.encode("ascii"))
    finally:
        notifier.close()


def inherited_listener(plane: str, domain: str, uid: int, gid: int) -> socket.socket:
    if (
            os.environ.get("LISTEN_PID") != str(os.getpid()) or
            os.environ.get("LISTEN_FDS") != "1"):
        fail("systemd socket activation contract mismatch")
    expected_name = "p1-watch" if plane == "WATCH" else "p1-paper-inert"
    if os.environ.get("LISTEN_FDNAMES") != expected_name:
        fail("systemd socket activation name mismatch")
    listener = socket.socket(fileno=3)
    if (
            listener.family != socket.AF_UNIX or
            listener.type & socket.SOCK_STREAM != socket.SOCK_STREAM or
            listener.getsockname() != str(socket_path(plane, domain))):
        fail("inherited socket mismatch")
    metadata = os.lstat(socket_path(plane, domain))
    if (
            not stat.S_ISSOCK(metadata.st_mode) or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            metadata.st_uid != uid or metadata.st_gid != gid):
        fail("socket metadata mismatch")
    listener.setblocking(False)
    return listener


def response(plane: str, domain: str, generation: int) -> dict[str, object]:
    return {
        "schema": "hepta.p1-dual-domain-daemon-response.v1",
        "status": "ok",
        "plane": plane,
        "domain_id": domain,
        "generation": generation,
        "kill_switch": "engaged" if plane == "PAPER_INERT" else "n/a",
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
        "broker_connections": 0,
        "paper_orders": 0,
    }


def receive_request(connection: socket.socket) -> dict[str, object]:
    raw = b""
    while b"\n" not in raw:
        chunk = connection.recv(min(1024, MAX_REQUEST + 1 - len(raw)))
        if not chunk:
            break
        raw += chunk
        if len(raw) > MAX_REQUEST:
            fail("request exceeds bound")
    if raw.count(b"\n") != 1 or not raw.endswith(b"\n"):
        fail("request framing mismatch")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FixtureError("request is not canonical JSON") from error
    if not isinstance(value, dict):
        fail("request is not an object")
    return value


def send_response(connection: socket.socket, value: object) -> None:
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8")
    connection.sendall(raw)


def peer_uid(connection: socket.socket) -> int:
    raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    return int.from_bytes(raw[4:8], byteorder=sys.byteorder, signed=True)


def serve(plane: str, domain: str) -> None:
    global STOP
    uid, gid = require_identity(plane, domain)
    require_inert_inputs(plane, domain)
    generation = prepare_runtime(plane, domain)
    listener = inherited_listener(plane, domain, uid, gid)
    watchdog_raw = os.environ.get("WATCHDOG_USEC", "")
    if re.fullmatch(r"[1-9][0-9]{0,15}", watchdog_raw) is None:
        fail("systemd watchdog interval missing")
    watchdog_interval = int(watchdog_raw, 10) / 1_000_000 / 3
    if watchdog_interval <= 0 or watchdog_interval > 1:
        fail("systemd watchdog interval out of fixture bound")

    def stop_handler(_number: int, _frame: object) -> None:
        global STOP
        STOP = True

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    selector = selectors.DefaultSelector()
    selector.register(listener, selectors.EVENT_READ)
    watchdog_enabled = True
    next_watchdog = time.monotonic()
    notify(
        f"READY=1\nSTATUS={plane}:{domain}:generation={generation}\n"
        "WATCHDOG=1")
    try:
        while not STOP:
            now = time.monotonic()
            if watchdog_enabled and now >= next_watchdog:
                notify("WATCHDOG=1")
                next_watchdog = now + watchdog_interval
            timeout = max(0.02, min(0.1, next_watchdog - now))
            for key, _mask in selector.select(timeout):
                connection, _address = key.fileobj.accept()
                with connection:
                    connection.settimeout(2)
                    if peer_uid(connection) != uid:
                        send_response(connection, {"status": "identity-denied"})
                        continue
                    request = receive_request(connection)
                    command = request.get("command")
                    if command == "ping" and set(request) == {"command"}:
                        send_response(
                            connection, response(plane, domain, generation))
                    elif (
                            command == "assert_generation" and
                            set(request) == {"command", "generation"} and
                            type(request.get("generation")) is int):
                        result = response(plane, domain, generation)
                        result["status"] = (
                            "current" if request["generation"] == generation
                            else "stale-generation-rejected")
                        send_response(connection, result)
                    elif (
                            command == "stop_watchdog" and
                            set(request) == {"command"}):
                        result = response(plane, domain, generation)
                        result["status"] = "watchdog-stopped"
                        send_response(connection, result)
                        watchdog_enabled = False
                    elif command == "crash" and set(request) == {"command"}:
                        result = response(plane, domain, generation)
                        result["status"] = "crashing"
                        send_response(connection, result)
                        connection.shutdown(socket.SHUT_WR)
                        time.sleep(0.02)
                        os._exit(73)
                    else:
                        send_response(connection, {"status": "request-denied"})
    finally:
        selector.close()
        listener.close()
        notify("STOPPING=1")


def main() -> int:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--serve", action="store_true")
    action.add_argument("--cleanup", action="store_true")
    parser.add_argument("--plane", choices=("WATCH", "PAPER_INERT"), required=True)
    parser.add_argument("--domain", required=True)
    arguments = parser.parse_args()
    try:
        if arguments.serve:
            serve(arguments.plane, arguments.domain)
        else:
            cleanup(arguments.plane, arguments.domain)
    except (FixtureError, OSError, ValueError) as error:
        print(
            "hepta_p1_dual_domain_daemon: FAIL: " +
            (str(error) or type(error).__name__)[:1024],
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
