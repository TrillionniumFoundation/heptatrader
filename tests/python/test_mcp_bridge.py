from __future__ import annotations

import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "adapters/mcp/hepta_mcp_server.py"
spec = importlib.util.spec_from_file_location("hepta_mcp_bridge_tests_subject", BRIDGE)
mcp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mcp)


def descriptor():
    item = {
        "name": "trade.cancel_order", "description": "Cancel an owned order",
        "required_capability": "trade.cancel", "effect": "trade", "timeout_ms": 1000,
        "input_schema": {"type": "object", "properties": {"order_id": {"type": "integer"}},
                         "required": ["order_id"], "additionalProperties": False},
        "result_schema": {"type": "object"}, "schema_hash": "",
    }
    item["schema_hash"] = mcp.descriptor_schema_hash(item)
    return item


def discovery():
    item = descriptor()
    return {"protocol": mcp.PROTOCOL_NAME, "protocol_version": 1,
            "protocol_min_version": 1, "protocol_max_version": 1, "schema_version": 2,
            "catalog_schema_hash": mcp.catalog_schema_hash([item]), "tools": [item]}


def envelope(tool, payload=None, status="ok"):
    return {"status": status, "tool": tool, "reason_code": "", "detail": "",
            "order_id": -1, "payload": payload}


def decode_fields(body):
    if body[:4] != b"HTT1":
        raise AssertionError("wrong wire magic")
    fields, offset = {}, 4
    while offset < len(body):
        key, length = struct.unpack("!HI", body[offset:offset + 6])
        offset += 6
        if key in fields or offset + length > len(body):
            raise AssertionError("invalid TLV")
        fields[key] = body[offset:offset + length].decode("utf-8")
        offset += length
    return fields


class McpBridgeTests(unittest.TestCase):
    def gateway(self, payload=None):
        with mock.patch.dict(os.environ, {"HEPTA_TOOL_EXPECTED_UID": ""}):
            gateway = mcp.NativeToolGateway()
        gateway._call_native = mock.Mock(return_value=envelope("system.tools.list", payload or discovery()))
        return gateway

    def test_wire_vector_and_reserved_fields(self) -> None:
        actual = mcp.encode_request("token", "trade.cancel_order", "command-0001", {"order_id": 42}, "")
        expected_fields = {1: "token", 2: "command-0001", 3: "trade.cancel_order", 5: "42", 22: "1", 23: "1"}
        expected = b"HTT1" + b"".join(struct.pack("!HI", key, len(value)) + value.encode()
                                      for key, value in sorted(expected_fields.items()))
        self.assertEqual(actual, expected)
        for arguments in ({"session_token": "steal"}, {"unknown": 1}, {"quantity": float("inf")}):
            with self.assertRaises(ValueError):
                mcp.encode_request("token", "trade.cancel_order", "command-0001", arguments, "")

    def test_strict_response_rejects_duplicate_nonfinite_and_bad_status(self) -> None:
        for body in (b'{"status":"ok","status":"uncertain"}',
                     json.dumps(envelope("t", {"price": float("nan")})).encode(),
                     json.dumps(envelope("t", {"price": 1})).replace('"price": 1', '"price": 1e309').encode(),
                     json.dumps(envelope("t", status=[])).encode()):
            with self.subTest(body=body), self.assertRaises(RuntimeError):
                mcp.validate_envelope(body)

    def test_discovery_hash_and_catalog_drift(self) -> None:
        gateway = self.gateway()
        gateway.discover()
        altered = discovery()
        altered["tools"][0]["description"] = "changed without rehashing"
        gateway._call_native.return_value = envelope("system.tools.list", altered)
        with self.assertRaises(RuntimeError):
            gateway.discover()
        altered["tools"][0]["schema_hash"] = mcp.descriptor_schema_hash(altered["tools"][0])
        altered["catalog_schema_hash"] = mcp.catalog_schema_hash(altered["tools"])
        gateway._call_native.return_value = envelope("system.tools.list", altered)
        with self.assertRaisesRegex(RuntimeError, "catalog changed"):
            gateway.discover()

    def test_duplicate_descriptor_and_noninteger_version_rejected(self) -> None:
        payload = discovery()
        payload["tools"].append(copy.deepcopy(payload["tools"][0]))
        with self.assertRaises(RuntimeError):
            self.gateway(payload).discover()
        for version in (True, "1", 1.5, 2):
            payload = discovery()
            payload["protocol_version"] = version
            with self.subTest(version=version), self.assertRaises(RuntimeError):
                self.gateway(payload).discover()

    def test_unknown_argument_and_missing_mutation_id_do_not_forward(self) -> None:
        gateway = self.gateway()
        gateway.discover()
        gateway._call_native.reset_mock()
        for args in ({"order_id": 42}, {"order_id": 42, "command_id": "command-0001", "extra": 1}):
            with self.assertRaises(ValueError):
                gateway.call("trade.cancel_order", args)
        gateway._call_native.assert_not_called()

    def test_trade_schema_adds_stable_id_without_mutating_native_descriptor(self) -> None:
        gateway = self.gateway()
        tools = gateway.mcp_tools()
        self.assertIn("command_id", tools[0]["inputSchema"]["required"])
        self.assertNotIn("command_id", gateway.descriptors["trade.cancel_order"]["input_schema"]["properties"])

    def test_token_permissions_links_and_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token"
            path.write_bytes(b"safe-token\n")
            path.chmod(0o600)
            self.assertEqual(mcp.read_session_token(str(path)), "safe-token")
            path.chmod(0o644)
            with self.assertRaises(RuntimeError):
                mcp.read_session_token(str(path))
            path.chmod(0o600)
            alias = path.with_name("alias")
            os.link(path, alias)
            with self.assertRaises(RuntimeError):
                mcp.read_session_token(str(path))
            alias.unlink()
            alias.symlink_to(path)
            with self.assertRaises(RuntimeError):
                mcp.read_session_token(str(alias))
            path.write_bytes(b"\xff")
            with self.assertRaises(RuntimeError):
                mcp.read_session_token(str(path))

    def test_oversized_line_drains_once_and_next_message_survives(self) -> None:
        self.assertEqual(list(mcp.bounded_stdin_messages(io.BytesIO(b"x" * 50 + b"\n{}\n"), 8)), [None, b"{}"])

    def test_actual_process_preserves_id_through_uncertain_retry_over_unix_socket(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mcp-test-") as directory:
            socket_path = str(Path(directory) / "gateway.sock")
            token = Path(directory) / "token"
            token.write_text("secret-session-token\n")
            token.chmod(0o600)
            observed, errors = [], []
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(server.close)
            server.bind(socket_path)
            server.listen(4)
            server.settimeout(5)
            def serve():
                try:
                    for index in range(3):
                        connection, _ = server.accept()
                        with connection:
                            connection.settimeout(3)
                            size = struct.unpack("!I", mcp.recv_exact(connection, 4))[0]
                            fields = decode_fields(mcp.recv_exact(connection, size))
                            observed.append(fields)
                            reply = envelope(fields[3], discovery() if index == 0 else {},
                                             "ok" if index == 0 else ("uncertain" if index == 1 else "duplicate"))
                            encoded = json.dumps(reply).encode()
                            # Fragmented writes exercise recv_exact, not a fake transport.
                            connection.sendall(struct.pack("!I", len(encoded)))
                            for offset in range(0, len(encoded), 13):
                                connection.sendall(encoded[offset:offset + 13])
                except BaseException as error:
                    errors.append(error)
            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            requests = [{"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {
                "name": "trade.cancel_order", "arguments": {"order_id": 42, "command_id": "command-0001"}}}
                for i in (1, 2)]
            result = subprocess.run(
                [sys.executable, str(BRIDGE)],
                input="".join(json.dumps(item) + "\n" for item in requests),
                text=True, capture_output=True, timeout=10,
                env={**os.environ, "HEPTA_TOOL_EXPECTED_UID": "", "HEPTA_TOOL_SOCKET": socket_path,
                     "HEPTA_TOOL_SESSION_TOKEN_FILE": str(token), "HEPTA_MCP_TIMEOUT_SEC": "2"},
            )
            thread.join(timeout=6)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(result.returncode, 0, result.stderr)
            responses = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(len(responses), 2)
            self.assertTrue(responses[0]["result"]["isError"])
            self.assertFalse(responses[1]["result"]["isError"])
            self.assertEqual(observed[1][2], "command-0001")
            self.assertEqual(observed[1], observed[2])
            self.assertEqual(observed[1][24], descriptor()["schema_hash"])
            self.assertNotIn("secret-session-token", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
