#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import threading
import time


def recv_exact(connection: socket.socket, size: int) -> bytes:
    result = bytearray()
    while len(result) < size:
        chunk = connection.recv(size - len(result))
        if not chunk:
            raise AssertionError("heptactl request frame ended early")
        result.extend(chunk)
    return bytes(result)


def call_with_response(executable: Path, response: bytes) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="heptactl-response-boundary-") as temporary:
        socket_path = Path(temporary) / "tool.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(socket_path))
        listener.listen(1)
        failures: list[BaseException] = []

        def serve() -> None:
            try:
                connection, _address = listener.accept()
                with connection:
                    length = struct.unpack("!I", recv_exact(connection, 4))[0]
                    if length < 1 or length > 65536:
                        raise AssertionError("heptactl emitted an invalid request length")
                    recv_exact(connection, length)
                    connection.sendall(struct.pack("!I", len(response)) + response)
            except BaseException as error:  # surface thread failures to the test
                failures.append(error)

        server = threading.Thread(target=serve, daemon=True)
        server.start()
        environment = {
            "PATH": "/usr/bin:/bin",
            "LANG": "C",
            "LC_ALL": "C",
            "TZ": "UTC",
            "HEPTA_TOOL_SESSION_TOKEN": "fixture-session-token",
        }
        result = subprocess.run(
            [str(executable), "--socket", str(socket_path), "tools", "list"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
            env=environment,
        )
        server.join(timeout=5)
        listener.close()
        if server.is_alive():
            raise AssertionError("heptactl fixture server did not stop")
        if failures:
            raise failures[0]
        return result


def watch_descriptor(name: str) -> dict[str, object]:
    input_schema: dict[str, object] = {}
    if name == "watch.get_snapshot":
        input_schema = {
            "type": "object",
            "required": ["instrument"],
            "properties": {"instrument": {"type": "string"}},
            "additionalProperties": False,
        }
    descriptor: dict[str, object] = {
        "name": name,
        "description": "WATCH fixture read tool.",
        "required_capability": "system.read",
        "effect": "read",
        "timeout_ms": 1000,
        "input_schema": input_schema,
        "result_schema": {},
    }
    canonical = "\0".join((
        name,
        str(descriptor["description"]),
        str(descriptor["required_capability"]),
        str(descriptor["effect"]),
        str(descriptor["timeout_ms"]),
        json.dumps(input_schema, ensure_ascii=True, separators=(",", ":")),
        "{}",
    )).encode("utf-8")
    descriptor["schema_hash"] = (
        "sha256:" + hashlib.sha256(canonical).hexdigest())
    return descriptor


def watch_response(tool: str, payload: object,
                   status: str = "ok", reason_code: str = "") -> bytes:
    return json.dumps({
        "status": status,
        "tool": tool,
        "reason_code": reason_code,
        "detail": "",
        "order_id": -1,
        "payload": payload,
    }, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def watch_success_script() -> list[bytes]:
    descriptor_names = [
        "system.tools.list",
        "system.tools.describe",
        "system.get_health",
        "account.get_summary",
        "portfolio.list_positions",
        "orders.list",
        "risk.get_limits",
        "market.get_quote",
        "watch.get_snapshot",
    ]
    descriptors = {
        name: watch_descriptor(name) for name in descriptor_names
    }
    catalog_canonical = "".join(
        f"{name}={descriptors[name]['schema_hash']}\n"
        for name in sorted(descriptors)
    ).encode("ascii")
    catalog_hash = "sha256:" + hashlib.sha256(catalog_canonical).hexdigest()
    catalog = {
        "protocol": "hepta.agent-tools",
        "protocol_version": 1,
        "protocol_min_version": 1,
        "protocol_max_version": 1,
        "schema_version": 2,
        "catalog_schema_hash": catalog_hash,
        "tools": list(descriptors.values()),
    }
    described = {}
    for name in (
            "system.get_health", "account.get_summary",
            "portfolio.list_positions", "orders.list",
            "risk.get_limits", "market.get_quote"):
        described[name] = {
            "protocol": "hepta.agent-tools",
            "protocol_version": 1,
            "protocol_min_version": 1,
            "protocol_max_version": 1,
            "schema_version": 2,
            "catalog_schema_hash": catalog_hash,
            "tool": descriptors[name],
        }
    read_payloads = {
        "account.get_summary": {
            "authoritative": True, "account_complete": True},
        "portfolio.list_positions": {
            "authoritative": True, "positions": []},
        "orders.list": {
            "authoritative": True, "active_order_ids": []},
        "risk.get_limits": {
            "authoritative": True, "gross_absolute_position": 0},
        "market.get_quote": {
            "authoritative": True, "stale": False},
        "system.get_health": {
            "gateway_ready": True, "execution_mode": "SIMULATOR"},
    }
    read_order = (
        "account.get_summary", "portfolio.list_positions", "orders.list",
        "risk.get_limits", "market.get_quote", "system.get_health")
    snapshot = {
        "schema": "hepta.watch-read-set.v1",
        "catalog": catalog,
        "descriptors": described,
        "reads": read_payloads,
        "read_finished_at_ms": {
            name: 1700000000000 + index
            for index, name in enumerate(read_order)
        },
    }
    return [
        watch_response("system.tools.list", catalog),
        watch_response("watch.get_snapshot", snapshot),
    ]


def call_watch_script(
        executable: Path,
        responses: list[bytes | None | tuple[float, bytes]],
        io_timeout_ms: int = 300,
        ) -> tuple[subprocess.CompletedProcess[str], int]:
    with tempfile.TemporaryDirectory(prefix="heptactl-watch-retry-") as temporary:
        socket_path = Path(temporary) / "tool.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(socket_path))
        listener.listen(16)
        accepted = [0]
        failures: list[BaseException] = []

        def serve() -> None:
            try:
                for response in responses:
                    connection, _address = listener.accept()
                    accepted[0] += 1
                    with connection:
                        length = struct.unpack(
                            "!I", recv_exact(connection, 4))[0]
                        recv_exact(connection, length)
                        if isinstance(response, tuple):
                            delay_seconds, response_body = response
                            time.sleep(delay_seconds)
                            connection.sendall(
                                struct.pack("!I", len(response_body)) +
                                response_body)
                        elif response is not None:
                            connection.sendall(
                                struct.pack("!I", len(response)) + response)
                listener.settimeout(0.4)
                try:
                    connection, _address = listener.accept()
                except TimeoutError:
                    return
                accepted[0] += 1
                connection.close()
            except BaseException as error:
                failures.append(error)

        server = threading.Thread(target=serve, daemon=True)
        server.start()
        result = subprocess.run(
            [
                str(executable), "--socket", str(socket_path),
                "--io-timeout-ms", str(io_timeout_ms),
                "watch", "snapshot", "EUR.USD",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
            env={
                "PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
                "TZ": "UTC", "HEPTA_TOOL_SESSION_TOKEN": "fixture-session-token",
            },
        )
        server.join(timeout=5)
        listener.close()
        if server.is_alive():
            raise AssertionError("heptactl WATCH fixture server did not stop")
        if failures:
            raise failures[0]
        return result, accepted[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--heptactl", type=Path, required=True)
    arguments = parser.parse_args()
    executable = arguments.heptactl.resolve(strict=True)

    descriptor = {
        "name": "system.tools.list",
        "description": "List versioned tools visible to this session.",
        "required_capability": "system.read",
        "effect": "read",
        "timeout_ms": 1000,
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
        },
        "result_schema": {
            "type": "object",
            "additionalProperties": True,
        },
    }
    descriptor_canonical = "\0".join((
        descriptor["name"],
        descriptor["description"],
        descriptor["required_capability"],
        descriptor["effect"],
        str(descriptor["timeout_ms"]),
        json.dumps(
            descriptor["input_schema"], ensure_ascii=True,
            separators=(",", ":")),
        json.dumps(
            descriptor["result_schema"], ensure_ascii=True,
            separators=(",", ":")),
    )).encode("utf-8")
    descriptor["schema_hash"] = (
        "sha256:" + hashlib.sha256(descriptor_canonical).hexdigest())
    catalog_canonical = (
        descriptor["name"] + "=" + descriptor["schema_hash"] + "\n"
    ).encode("utf-8")
    digest = (
        "sha256:" + hashlib.sha256(catalog_canonical).hexdigest()
    ).encode("ascii")
    descriptor_body = json.dumps(
        descriptor, ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")
    discovery = (
        b'{"protocol":"hepta.agent-tools","protocol_version":1,'
        b'"protocol_min_version":1,"protocol_max_version":1,'
        b'"schema_version":2,"catalog_schema_hash":"' + digest +
        b'","tools":[' + descriptor_body + b']}'
    )
    valid = (
        b'{"status":"ok","tool":"system.tools.list","reason_code":"",'
        b'"detail":"","order_id":-1,"payload":' + discovery + b"}"
    )
    result = call_with_response(executable, valid)
    assert result.returncode == 0, result
    assert result.stdout.strip().encode() == valid

    wrong_schema = valid.replace(
        b'"schema_version":2', b'"schema_version":1', 1)
    result = call_with_response(executable, wrong_schema)
    assert result.returncode == 10, result
    assert result.stderr.strip() == "DISCOVERY_SCHEMA_VERSION_UNSUPPORTED"

    wrong_hash = valid.replace(digest, b"sha256:short", 1)
    result = call_with_response(executable, wrong_hash)
    assert result.returncode == 10, result
    assert result.stderr.strip() == "DISCOVERY_CATALOG_SCHEMA_HASH_INVALID"

    malformed = valid + b" trailing"
    result = call_with_response(executable, malformed)
    assert result.returncode == 10, result
    assert result.stderr.strip() == "INVALID_RESULT_ENVELOPE"

    mismatched = (
        b'{"status":"ok","tool":"market.get_quote","reason_code":"",'
        b'"detail":"","order_id":-1,"payload":null}'
    )
    result = call_with_response(executable, mismatched)
    assert result.returncode == 10, result
    assert result.stderr.strip() == "RESULT_TOOL_MISMATCH"

    with tempfile.TemporaryDirectory(prefix="heptactl-transport-boundary-") as temporary:
        environment = {
            "PATH": "/usr/bin:/bin",
            "LANG": "C",
            "LC_ALL": "C",
            "TZ": "UTC",
            "HEPTA_TOOL_SESSION_TOKEN": "fixture-session-token",
        }
        result = subprocess.run(
            [
                str(executable), "--socket", str(Path(temporary) / "missing.sock"),
                "tools", "list",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
            env=environment,
        )
    assert result.returncode == 4, result
    assert result.stderr.strip() == "SOCKET_CONNECT_FAILED"

    watch_script = watch_success_script()
    delayed_script: list[bytes | None | tuple[float, bytes]] = [
        (3.0, watch_script[0]), *watch_script[1:]]
    result, accepted = call_watch_script(
        executable, delayed_script, io_timeout_ms=5000)
    assert result.returncode == 0, result
    assert accepted == 2
    assert json.loads(result.stdout)["schema"] == "hepta.watch-read-set.v1"

    result, accepted = call_watch_script(
        executable, [None] + watch_script)
    assert result.returncode == 0, result
    assert accepted == 3
    snapshot = json.loads(result.stdout)
    assert snapshot["schema"] == "hepta.watch-read-set.v1"
    assert set(snapshot["reads"]) == {
        "account.get_summary", "portfolio.list_positions", "orders.list",
        "risk.get_limits", "market.get_quote", "system.get_health",
    }

    rejected_snapshot = watch_response(
        "watch.get_snapshot", None, "rejected", "FIXTURE_REJECTED")
    result, accepted = call_watch_script(
        executable, [watch_script[0], rejected_snapshot])
    assert result.returncode == 6, result
    assert accepted == 2

    rejected = watch_response(
        "system.tools.list", None, "rejected", "FIXTURE_REJECTED")
    result, accepted = call_watch_script(executable, [rejected])
    assert result.returncode == 6, result
    assert accepted == 1

    result, accepted = call_watch_script(executable, [b"{}"])
    assert result.returncode == 10, result
    assert result.stderr.strip() == "INVALID_RESULT_ENVELOPE"
    assert accepted == 1

    result, accepted = call_watch_script(executable, [None, None])
    assert result.returncode == 4, result
    assert result.stderr.strip() == "FRAME_HEADER_TIMEOUT"
    assert accepted == 2

    print("heptactl_response_boundary_fixture: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
