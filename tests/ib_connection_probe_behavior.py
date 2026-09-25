#!/usr/bin/env python3
"""Exercise the real SDK-linked read-only probe against a synthetic TWS peer."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import socket
import struct
import subprocess
import threading
import time
import unittest

PROBE: Path
CANARY = "SYNTHETIC-ACCOUNT-AND-ERROR-DO-NOT-LOG"


def frame(*fields: object) -> bytes:
    body = ("\0".join(str(value) for value in fields) + "\0").encode("ascii")
    return struct.pack("!I", len(body)) + body


class Peer:
    def __init__(self, mode: str, fragmented: bool = False):
        self.mode, self.fragmented = mode, fragmented
        self.stop = threading.Event()
        self.messages: list[list[bytes]] = []
        self.errors: list[BaseException] = []
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(("127.0.0.1", 0))
        self.port = self.socket.getsockname()[1]
        self.socket.listen(1)
        self.socket.settimeout(0.2)
        self.thread = threading.Thread(target=self.run)

    def exact(self, conn: socket.socket, length: int) -> bytes:
        result = bytearray()
        while len(result) < length and not self.stop.is_set():
            try:
                data = conn.recv(length - len(result))
            except socket.timeout:
                continue
            if not data:
                raise EOFError()
            result.extend(data)
        if len(result) != length:
            raise EOFError()
        return bytes(result)

    def receive(self, conn: socket.socket) -> bytes:
        length, = struct.unpack("!I", self.exact(conn, 4))
        if not 0 < length <= 4096:
            raise AssertionError(f"unexpected outbound length {length}")
        return self.exact(conn, length)

    def send(self, conn: socket.socket, value: bytes) -> None:
        if not self.fragmented:
            conn.sendall(value)
        else:
            for offset in range(0, len(value), 3):
                conn.sendall(value[offset:offset + 3])
                time.sleep(0.001)

    def run(self) -> None:
        try:
            while not self.stop.is_set():
                try:
                    conn, _ = self.socket.accept()
                    break
                except socket.timeout:
                    continue
            else:
                return
            with conn:
                conn.settimeout(0.2)
                if self.exact(conn, 4) != b"API\0":
                    raise AssertionError("missing v100 SDK handshake")
                supported = self.receive(conn)
                if not re.fullmatch(rb"v[0-9]+\.\.[0-9]+", supported):
                    raise AssertionError(f"unexpected version request {supported!r}")
                if self.mode == "stalled_handshake":
                    while not self.stop.wait(0.05):
                        pass
                    return
                version = min(178, int(supported.split(b"..", 1)[1]))
                self.send(conn, frame(version, "20260925 00:00:00 UTC"))
                start = self.receive(conn).split(b"\0")
                self.messages.append(start)
                if start[0] != b"71":
                    raise AssertionError("only startApi may precede startup callbacks")
                if self.mode == "disconnect":
                    return
                if self.mode != "accounts_only":
                    self.send(conn, frame(9, 1, -1 if self.mode == "negative_id" else 100))
                if self.mode != "id_only":
                    self.send(conn, frame(15, 1, "" if self.mode == "empty_accounts" else CANARY))
                if self.mode == "unsolicited_time":
                    self.send(conn, frame(49, 1, 1790294400))
                if self.mode == "secret_error":
                    self.send(conn, frame(4, 2, -1, 2104, CANARY, CANARY))
                while not self.stop.is_set():
                    request = self.receive(conn).split(b"\0")
                    self.messages.append(request)
                    if request[0] != b"49":
                        raise AssertionError(f"unexpected operation {request[0]!r}: zero mutation contract")
                    if self.mode not in ("no_roundtrip", "unsolicited_time"):
                        self.send(conn, frame(49, 1, 1790294400))
        except (EOFError, ConnectionResetError, BrokenPipeError):
            pass
        except BaseException as error:
            self.errors.append(error)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *unused):
        self.stop.set()
        self.thread.join(timeout=3)
        self.socket.close()
        if self.thread.is_alive():
            raise AssertionError("synthetic peer did not stop")
        if self.errors:
            raise self.errors[0]


class ProbeTests(unittest.TestCase):
    def invoke(self, args: list[str]):
        started = time.monotonic()
        process = subprocess.run([str(PROBE), *args], text=True, capture_output=True,
                                 timeout=8, check=False)
        self.assertNotIn(CANARY, process.stdout + process.stderr)
        self.assertEqual(process.stderr, "")
        record = json.loads(process.stdout)
        self.assertEqual(record["schema"], "heptatrader.ib-connectivity.v1")
        self.assertEqual(record["broker_mutations"], 0)
        for field in ("broker_mode_verified", "paper_authorized", "live_authorized"):
            self.assertIs(record[field], False)
        return process.returncode, record, time.monotonic() - started

    def run_peer(self, mode: str, *, fragmented: bool = False, timeout_ms: int = 750):
        with Peer(mode, fragmented) as peer:
            result = self.invoke(["127.0.0.1", str(peer.port), "19001", str(timeout_ms)])
        for message in peer.messages:
            self.assertIn(message[0], (b"71", b"49"))
        return result, peer.messages

    def test_invalid_values_and_live_defaults_never_connect(self):
        with Peer("ready") as peer:
            valid = ["127.0.0.1", str(peer.port), "19001", "750"]
            for index, values in ((0, ["localhost", "192.0.2.1", ""]),
                                  (1, ["4001", "7496", "0", "65536", "-1", "1x"]),
                                  (2, ["0", "-1", "2147483648", "1e3", ""]),
                                  (3, ["0", "-1", "30001", "1x"])):
                for value in values:
                    with self.subTest(index=index, value=value):
                        args = valid.copy(); args[index] = value
                        code, record, _ = self.invoke(args)
                        self.assertEqual(code, 64)
                        self.assertEqual(record["status"], "INVALID_ARGUMENT")
            code, _, _ = self.invoke(valid + ["unexpected"])
            self.assertEqual(code, 64)
            self.assertEqual(peer.messages, [])

    def test_stalled_handshake_is_inside_the_total_deadline(self):
        (code, record, duration), messages = self.run_peer("stalled_handshake", timeout_ms=250)
        self.assertEqual(code, 2)
        self.assertEqual(record["reason"], "CONNECT_HANDSHAKE_TIMEOUT")
        self.assertFalse(record["handshake_received"])
        self.assertLess(duration, 5)
        self.assertEqual(messages, [])

    def test_partial_startup_is_not_readiness(self):
        for mode in ("id_only", "accounts_only", "empty_accounts", "negative_id"):
            with self.subTest(mode=mode):
                (code, record, _), messages = self.run_peer(mode)
                self.assertEqual(code, 2)
                self.assertEqual(record["reason"], "STARTUP_CALLBACK_TIMEOUT")
                self.assertEqual([m[0] for m in messages], [b"71"])

    def test_startup_without_read_response_is_not_readiness(self):
        (code, record, _), messages = self.run_peer("no_roundtrip")
        self.assertEqual(code, 2)
        self.assertEqual(record["reason"], "READ_ROUNDTRIP_TIMEOUT")
        self.assertEqual([m[0] for m in messages], [b"71", b"49"])

    def test_fragmented_real_sdk_roundtrip(self):
        (code, record, _), messages = self.run_peer("ready", fragmented=True, timeout_ms=2000)
        self.assertEqual(code, 0)
        self.assertEqual(record["status"], "READY")
        for key in ("handshake_received", "next_valid_id_received", "managed_accounts_received", "current_time_received"):
            self.assertTrue(record[key])
        self.assertEqual([m[0] for m in messages], [b"71", b"49"])

    def test_broker_error_and_account_text_are_never_logged(self):
        (code, record, _), _ = self.run_peer("secret_error", timeout_ms=2000)
        self.assertEqual(code, 0)
        self.assertEqual(record["sdk_error_code"], 2104)

    def test_disconnect_is_not_success(self):
        (code, record, _), _ = self.run_peer("disconnect")
        self.assertNotEqual(code, 0)
        self.assertNotEqual(record["status"], "READY")


def main() -> int:
    global PROBE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", required=True, type=Path)
    args = parser.parse_args()
    PROBE = args.probe.resolve(strict=True)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ProbeTests))
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
