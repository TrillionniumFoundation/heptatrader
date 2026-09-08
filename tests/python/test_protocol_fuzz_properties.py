#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import random
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
MCP_DIR = ROOT / "adapters/mcp"
sys.path.insert(0, str(MCP_DIR))
SPEC = importlib.util.spec_from_file_location(
    "hepta_mcp_server_fuzz_target", MCP_DIR / "hepta_mcp_server.py"
)
assert SPEC is not None and SPEC.loader is not None
MCP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MCP)


class ProtocolFuzzProperties(unittest.TestCase):
    def test_random_response_bytes_fail_only_with_typed_runtime_error(self) -> None:
        randomizer = random.Random(0x4845505441)
        for _ in range(2500):
            size = randomizer.randrange(0, 513)
            body = bytes(randomizer.randrange(0, 256) for _ in range(size))
            try:
                result = MCP.validate_envelope(body)
            except RuntimeError:
                continue
            self.assertIsInstance(result, dict)
            self.assertIn(result["status"], MCP.VALID_RESULT_STATUSES)

    def test_hostile_envelope_mutations_are_rejected(self) -> None:
        base = {
            "status": "ok",
            "tool": "system.health",
            "reason_code": "OK",
            "detail": "",
            "order_id": 0,
            "payload": {"healthy": True},
        }
        encoded = json.dumps(base, separators=(",", ":")).encode("utf-8")
        self.assertEqual(base, MCP.validate_envelope(encoded))

        for key in base:
            candidate = dict(base)
            del candidate[key]
            with self.assertRaises(RuntimeError, msg=key):
                MCP.validate_envelope(json.dumps(candidate).encode("utf-8"))

        invalid_values = {
            "status": ["future-status", 1, None],
            "tool": [1, None, {}],
            "reason_code": [1, None, []],
            "detail": [1, None, {}],
            "order_id": [True, 1.5, "1", None],
        }
        for key, values in invalid_values.items():
            for value in values:
                candidate = dict(base)
                candidate[key] = value
                with self.assertRaises(RuntimeError, msg=f"{key}={value!r}"):
                    MCP.validate_envelope(json.dumps(candidate).encode("utf-8"))

    def test_request_encoder_preserves_unique_bounded_field_ids(self) -> None:
        self.assertEqual(26, len(MCP.FIELD_IDS))
        self.assertEqual(set(range(1, 27)), set(MCP.FIELD_IDS.values()))
        excluded = {
            "session_token", "tool_call_id", "tool_name",
            "protocol_min_version", "protocol_max_version",
            "expected_schema_hash", "target_tool_name", "cancel_tool_call_id",
        }
        for field in sorted(set(MCP.FIELD_IDS) - excluded):
            value = 1.25 if field in {"quantity", "limit_price", "reference_price"} else "v"
            body = MCP.encode_request(
                "token", "test.tool", "call-0001", {field: value}, ""
            )
            self.assertTrue(body.startswith(b"HTT1"), field)
            self.assertLessEqual(len(body), MCP.MAX_REQUEST_BYTES)
            seen = []
            offset = 4
            while offset < len(body):
                field_id, length = struct.unpack("!HI", body[offset:offset + 6])
                offset += 6
                self.assertLessEqual(length, 32768)
                offset += length
                seen.append(field_id)
            self.assertEqual(len(seen), len(set(seen)), field)
            self.assertIn(MCP.FIELD_IDS[field], seen)

    def test_unknown_fields_and_non_scalar_values_never_enter_wire_frame(self) -> None:
        randomizer = random.Random(0x544F4F4C)
        for index in range(500):
            unknown = f"unknown_{randomizer.randrange(1 << 30)}_{index}"
            with self.assertRaises(ValueError):
                MCP.encode_request(
                    "token", "test.tool", "call-0001", {unknown: "value"}, ""
                )
        for value in ({}, [], (1,), object(), complex(1, 2)):
            with self.assertRaises(ValueError):
                MCP.encode_request(
                    "token", "test.tool", "call-0001", {"instrument": value}, ""
                )

    def test_protocol_size_boundaries(self) -> None:
        exact = "x" * 32768
        body = MCP.encode_request(
            "token", "test.tool", "call-0001", {"instrument": exact}, ""
        )
        self.assertLessEqual(len(body), MCP.MAX_REQUEST_BYTES)
        with self.assertRaises(ValueError):
            MCP.encode_request(
                "token", "test.tool", "call-0001",
                {"instrument": exact + "x"}, "",
            )


if __name__ == "__main__":
    unittest.main()
