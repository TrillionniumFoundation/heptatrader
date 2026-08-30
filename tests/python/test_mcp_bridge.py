from __future__ import annotations

import struct
import sys
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.mcp import hepta_mcp_server as bridge  # noqa: E402


def decode_fields(body: bytes) -> dict[int, str]:
    assert body[:4] == b"HTT1"
    offset = 4
    fields: dict[int, str] = {}
    while offset < len(body):
        field_id, length = struct.unpack("!HI", body[offset:offset + 6])
        offset += 6
        fields[field_id] = body[offset:offset + length].decode("utf-8")
        offset += length
    return fields


class McpBridgeTests(unittest.TestCase):
    def test_jsonrpc_error_messages_are_bounded_and_safe(self) -> None:
        huge = "x" * (bridge.MAX_JSONRPC_ERROR_BYTES + 200)
        response = bridge.failure("request-1", -32603, huge)
        message = response["error"]["message"]
        self.assertLessEqual(
            len(message.encode("utf-8")), bridge.MAX_JSONRPC_ERROR_BYTES)
        self.assertEqual(
            bridge.failure("request-1", -32603, "bad\npath")["error"]["message"],
            "internal adapter error")
        self.assertEqual(
            bridge.failure("request-1", -32603, "bad\ud800")["error"]["message"],
            "internal adapter error")

    def test_jsonrpc_string_ids_are_bounded(self) -> None:
        self.assertTrue(bridge._valid_jsonrpc_id(""))
        self.assertTrue(bridge._valid_jsonrpc_id(
            "x" * bridge.MAX_JSONRPC_ID_BYTES))
        self.assertFalse(bridge._valid_jsonrpc_id(
            "x" * (bridge.MAX_JSONRPC_ID_BYTES + 1)))
        self.assertFalse(bridge._valid_jsonrpc_id("bad\nidentifier"))
        self.assertFalse(bridge._valid_jsonrpc_id(1 << 54))
        self.assertFalse(bridge._valid_jsonrpc_id(-(1 << 54)))
        # Avoid an OverflowError from a giant Python integer becoming a
        # process-level crash in the JSON-RPC envelope validator.
        self.assertFalse(bridge._valid_jsonrpc_id(10 ** 1000))

    def test_discovery_exception_does_not_embed_native_envelope(self) -> None:
        gateway = bridge.NativeToolGateway.__new__(bridge.NativeToolGateway)

        def failed_discovery(_name, _arguments):
            return {
                "status": "error", "tool": "system.tools.list",
                "reason_code": "INTERNAL_SECRET",
                "detail": "/private/path/with/credential=secret",
                "order_id": -1, "payload": None,
            }

        gateway._call_native = failed_discovery
        with self.assertRaisesRegex(RuntimeError, "^tool discovery failed$"):
            gateway.discover()

    def test_tools_call_validation_error_is_bounded(self) -> None:
        class Gateway:
            def call(self, _name, _arguments):
                raise ValueError("x" * (bridge.MAX_JSONRPC_ERROR_BYTES + 100))

        response = bridge.handle(Gateway(), {
            "jsonrpc": "2.0", "id": "request-1", "method": "tools/call",
            "params": {"name": "market.get_quote", "arguments": {}},
        })
        self.assertEqual(response["error"]["code"], -32602)
        self.assertLessEqual(
            len(response["error"]["message"].encode("utf-8")),
            bridge.MAX_JSONRPC_ERROR_BYTES)

    def test_direct_handle_contains_gateway_exceptions(self) -> None:
        class ListingGateway:
            def mcp_tools(self):
                raise RuntimeError("/private/socket credential=secret")

        response = bridge.handle(ListingGateway(), {
            "jsonrpc": "2.0", "id": "request-1", "method": "tools/list",
        })
        self.assertEqual(response["error"]["code"], -32603)
        self.assertEqual(response["error"]["message"],
                         "internal adapter error")

        class CallingGateway:
            def call(self, _name, _arguments):
                raise RuntimeError("/private/socket credential=secret")

        response = bridge.handle(CallingGateway(), {
            "jsonrpc": "2.0", "id": "request-1", "method": "tools/call",
            "params": {"name": "market.get_quote", "arguments": {}},
        })
        self.assertEqual(response["error"]["code"], -32603)
        self.assertEqual(response["error"]["message"],
                         "internal adapter error")

    def test_discovery_rejects_control_text(self) -> None:
        descriptor = {
            "name": "market.get_quote",
            "description": "bad\u007fdescription",
            "required_capability": "market.read",
            "effect": "read",
            "timeout_ms": 1000,
            "schema_hash": "sha256:" + "0" * 64,
            "input_schema": {"type": "object"},
            "result_schema": {"type": "object"},
        }
        with self.assertRaises(RuntimeError):
            bridge.descriptor_schema_hash(descriptor)

    def test_encode_request_rejects_untrusted_envelope_values(self) -> None:
        valid_args = {
            "instrument": "EUR.USD",
            "target_position": 0,
            "max_slippage_bps": 1,
            "expires_at_ms": 123456,
        }
        with self.assertRaisesRegex(ValueError, "session token"):
            bridge.encode_request("", "intent.preview_target_position",
                                  "preview-001", valid_args, "")
        with self.assertRaisesRegex(ValueError, "canonical tool name"):
            bridge.encode_request("session-token", "Intent.Bad",
                                  "preview-001", valid_args, "")
        with self.assertRaisesRegex(ValueError, "length"):
            bridge.encode_request("session-token", "intent.preview_target_position",
                                  "short", valid_args, "")
        with self.assertRaisesRegex(ValueError, "alphanumeric"):
            bridge.encode_request("session-token", "intent.preview_target_position",
                                  "--------", valid_args, "")
        with self.assertRaisesRegex(ValueError, "sha256"):
            bridge.encode_request("session-token", "intent.preview_target_position",
                                  "preview-001", valid_args, "not-a-digest")
        with self.assertRaisesRegex(ValueError, "finite"):
            bad_args = dict(valid_args)
            bad_args["target_position"] = float("nan")
            bridge.encode_request("session-token", "intent.preview_target_position",
                                  "preview-001", bad_args, "")

    def test_target_domain_aliases_encode_to_canonical_wire_fields(self) -> None:
        body = bridge.encode_request(
            "session-token",
            "intent.preview_target_position",
            "preview-1",
            {
                "instrument": "EUR.USD",
                "target_position": 0,
                "max_slippage_bps": 5,
                "expires_at_ms": 123456,
            },
            "",
        )
        fields = decode_fields(body)
        self.assertEqual(fields[bridge.FIELD_IDS["quantity"]], "0")
        self.assertEqual(fields[bridge.FIELD_IDS["reference_price"]], "5")
        self.assertNotIn("target_position", fields.values())
        self.assertNotIn("max_slippage_bps", fields.values())

    def test_alias_and_wire_spelling_cannot_both_be_supplied(self) -> None:
        with self.assertRaisesRegex(ValueError, "alias and wire field"):
            bridge.encode_request(
                "session-token",
                "intent.apply_target_position",
                "apply-01",
                {
                    "instrument": "EUR.USD",
                    "target_position": 1,
                    "quantity": 1,
                    "max_slippage_bps": 5,
                    "expires_at_ms": 123456,
                    "preview_permit": "sha256:" + "a" * 64,
                },
                "",
            )

    def test_aliases_are_not_accepted_for_raw_order_tools(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid or unknown tool argument"):
            bridge.encode_request(
                "session-token",
                "trade.place_order",
                "place-01",
                {"target_position": 1},
                "",
            )

    def test_aliases_cannot_overwrite_a_wire_field(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate wire fields"):
            bridge.encode_request(
                "session-token",
                "system.tools.describe",
                "describe-001",
                {"tool_name": "market.get_quote",
                 "target_tool_name": "market.get_quote"},
                "",
            )

    def test_direct_encoder_enforces_tool_shape_and_numeric_ranges(self) -> None:
        with self.assertRaisesRegex(ValueError, "required tool argument"):
            bridge.encode_request(
                "session-token", "market.get_quote", "quote-001", {}, "")
        with self.assertRaisesRegex(ValueError, "unknown tool argument"):
            bridge.encode_request(
                "session-token", "market.get_quote", "quote-001",
                {"instrument": "EUR.USD", "order_id": 7}, "")
        with self.assertRaisesRegex(ValueError, "canonical instrument"):
            bridge.encode_request(
                "session-token", "market.get_quote", "quote-001",
                {"instrument": "EUR..USD"}, "")
        with self.assertRaisesRegex(ValueError, "integer"):
            bridge.encode_request(
                "session-token", "trade.cancel_order", "cancel-001",
                {"order_id": 1.0}, "")

    def test_direct_encoder_rejects_raw_order_semantic_smuggling(self) -> None:
        base = {
            "instrument": "EUR.USD",
            "side": "BUY",
            "quantity": 1,
            "order_type": "MKT",
            "tif": "DAY",
            "expires_at_ms": 123456,
            "preview_permit": "sha256:" + "a" * 64,
        }
        invalid = dict(base)
        invalid["limit_price"] = 1.1
        with self.assertRaisesRegex(ValueError, "MKT"):
            bridge.encode_request(
                "session-token", "trade.place_order", "place-001",
                invalid, "")

        invalid = dict(base)
        invalid["quantity"] = 0
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            bridge.encode_request(
                "session-token", "trade.place_order", "place-001",
                invalid, "")
        invalid = dict(base)
        invalid["expires_at_ms"] = 1 << 80
        with self.assertRaisesRegex(ValueError, "maximum"):
            bridge.encode_request(
                "session-token", "trade.place_order", "place-001",
                invalid, "")

    def test_gateway_validates_public_aliases_before_wire_translation(self) -> None:
        gateway = bridge.NativeToolGateway.__new__(bridge.NativeToolGateway)
        gateway.descriptors = {
            "system.tools.describe": {
                "effect": "read",
                "schema_hash": "",
                "input_schema": {
                    "type": "object", "required": ["tool_name"],
                    "properties": {"tool_name": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
        }
        captured = {}

        def fake_call(tool_name, arguments, schema_hash="", tool_call_id=None):
            captured["tool_name"] = tool_name
            captured["arguments"] = arguments
            return {"status": "ok"}

        gateway._call_native = fake_call
        gateway.call("system.tools.describe", {"tool_name": "market.get_quote"})
        self.assertEqual(captured["tool_name"], "system.tools.describe")
        self.assertEqual(captured["arguments"],
                         {"target_tool_name": "market.get_quote"})

    def test_response_envelope_is_strict_json(self) -> None:
        valid = (
            '{"status":"ok","tool":"market.get_quote","reason_code":"",'
            '"detail":"","order_id":-1,"payload":{}}'
        ).encode()
        self.assertEqual(bridge.validate_envelope(valid)["status"], "ok")
        for invalid in (
            b'{"status":"ok","status":"ok","tool":"market.get_quote","reason_code":"","detail":"","order_id":-1,"payload":{}}',
            b'{"status":"ok","tool":"market.get_quote","reason_code":"","detail":"","order_id":-1,"payload":{"x":NaN}}',
            b'{"status":"ok","tool":"Market.Get","reason_code":"","detail":"","order_id":-1,"payload":{}}',
            b'{"status":"ok","tool":"market.get_quote","reason_code":"","detail":"","order_id":-2,"payload":{}}',
            b'{"status":"ok","tool":"market.get_quote","reason_code":"","detail":"\\u007f","order_id":-1,"payload":{}}',
            b'{"status":"ok","tool":"market.get_quote","reason_code":"","detail":"\\u0085","order_id":-1,"payload":{}}',
        ):
            with self.assertRaises(RuntimeError):
                bridge.validate_envelope(invalid)

    def test_response_payload_is_bounded_and_control_free(self) -> None:
        prefix = (
            b'{"status":"ok","tool":"market.get_quote",'
            b'"reason_code":"","detail":"","order_id":-1,'
            b'"payload":')

        # Escaped controls and lone surrogates must be rejected after JSON
        # decoding, including when they are nested below the opaque payload.
        for payload in (b'{"message":"\\u007f"}',
                        b'{"message":"\\u0085"}',
                        b'{"message":"\\ud800"}',
                        b'{"message":"ok\\nno"}'):
            with self.assertRaises(RuntimeError):
                bridge.validate_envelope(prefix + payload + b'}')

        # Python's decoder materializes 1e999 as infinity unless the finite
        # check is applied recursively; the native result codec rejects it.
        with self.assertRaises(RuntimeError):
            bridge.validate_envelope(prefix + b'{"value":1e999}' + b'}')
        with self.assertRaises(RuntimeError):
            bridge.validate_envelope(prefix + b'{"value":1e-999}' + b'}')
        with self.assertRaises(RuntimeError):
            bridge.validate_envelope(prefix + b'{"value":-0.0}' + b'}')
        with self.assertRaises(RuntimeError):
            bridge.validate_envelope(prefix + b'{"value":-0}' + b'}')
        with self.assertRaises(RuntimeError):
            bridge.validate_envelope(
                prefix.replace(b'"order_id":-1', b'"order_id":-0') +
                b'{}' + b'}')

        # Match the native depth guard (root payload object is depth one).
        deep = b'{"x":' * (bridge.MAX_RESULT_DEPTH + 1) + b'0' + b'}' * (
            bridge.MAX_RESULT_DEPTH + 1)
        with self.assertRaises(RuntimeError):
            bridge.validate_envelope(prefix + deep + b'}')

        # The payload itself may contain arrays, but the total value-node
        # budget must remain bounded so a megabyte response cannot fan out
        # into an unbounded Python object graph.
        values = b','.join(
            b'0' for _ in range(bridge.MAX_RESULT_NODES))
        oversized_nodes = b'{"values":[' + values + b']}'
        with self.assertRaises(RuntimeError):
            bridge.validate_envelope(prefix + oversized_nodes + b'}')

        valid = prefix + b'{"values":[0,true,null,{"text":"ok"}]}' + b'}'
        self.assertEqual(bridge.validate_envelope(valid)["status"], "ok")


if __name__ == "__main__":
    unittest.main()
