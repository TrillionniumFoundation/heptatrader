from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import check_schema_catalog  # noqa: E402


class SchemaCatalogTests(unittest.TestCase):
    def test_catalog_matches_cpp_and_mcp(self) -> None:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts/check_schema_catalog.py")],
            cwd=ROOT,
            check=True,
        )

    def _contract_copy(self, temporary: Path) -> None:
        for relative in (
            "schemas/tool-catalog-v1.json",
            "schemas/tool-catalog-v1.sha256",
            "HeptaTrade/tools/trading_tool_registry.cpp",
            "HeptaTrade/tool_host/typed_tool_protocol.cpp",
            "adapters/mcp/hepta_mcp_server.py",
        ):
            destination = temporary / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)

    def test_catalog_capability_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            self._contract_copy(temporary)
            catalog_path = temporary / "schemas/tool-catalog-v1.json"
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            next(
                tool for tool in catalog["tools"]
                if tool["name"] == "intent.apply_target_position"
            )["capability"] = "risk.read"
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

            errors = check_schema_catalog.validate(temporary)
            self.assertTrue(
                any("capability drift for intent.apply_target_position" in error
                    for error in errors),
                errors,
            )

    def test_mcp_alias_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            self._contract_copy(temporary)
            mcp_path = temporary / "adapters/mcp/hepta_mcp_server.py"
            source = mcp_path.read_text(encoding="utf-8")
            source = source.replace(
                '"max_slippage_bps": "reference_price"',
                '"max_slippage_bps": "quantity"',
                1,
            )
            mcp_path.write_text(source, encoding="utf-8")

            errors = check_schema_catalog.validate(temporary)
            self.assertIn("MCP target alias map drift", errors)

    def test_catalog_visibility_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            self._contract_copy(temporary)
            catalog_path = temporary / "schemas/tool-catalog-v1.json"
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            next(
                tool for tool in catalog["tools"]
                if tool["name"] == "trade.place_order"
            )["visibility"] = "ordinary"
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

            errors = check_schema_catalog.validate(temporary)
            self.assertTrue(
                any("canonical visibility mismatch for trade.place_order" in error
                    for error in errors),
                errors,
            )

    def test_mcp_full_field_id_map_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            self._contract_copy(temporary)
            mcp_path = temporary / "adapters/mcp/hepta_mcp_server.py"
            source = mcp_path.read_text(encoding="utf-8")
            source = source.replace('"tool_name": 3', '"tool_name": 27', 1)
            mcp_path.write_text(source, encoding="utf-8")

            errors = check_schema_catalog.validate(temporary)
            self.assertIn("MCP FIELD_IDS map drift", errors)

    def test_raw_place_permit_requirement_is_rejected_when_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            self._contract_copy(temporary)
            registry_path = temporary / "HeptaTrade/tools/trading_tool_registry.cpp"
            source = registry_path.read_text(encoding="utf-8")
            source = source.replace(
                ',\\"preview_permit\\"],',
                '],',
                1,
            )
            registry_path.write_text(source, encoding="utf-8")

            errors = check_schema_catalog.validate(temporary)
            self.assertTrue(
                any("schema missing required fields for trade.place_order" in error
                    for error in errors),
                errors,
            )

    def test_typed_wire_permit_field_id_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            self._contract_copy(temporary)
            typed_path = temporary / "HeptaTrade/tool_host/typed_tool_protocol.cpp"
            source = typed_path.read_text(encoding="utf-8")
            source = source.replace("PreviewPermit = 25", "PreviewPermit = 27", 1)
            typed_path.write_text(source, encoding="utf-8")

            errors = check_schema_catalog.validate(temporary)
            self.assertIn("typed C++ FieldId drift: preview_permit", errors)

    def test_typed_wire_target_permit_scope_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            self._contract_copy(temporary)
            typed_path = temporary / "HeptaTrade/tool_host/typed_tool_protocol.cpp"
            source = typed_path.read_text(encoding="utf-8")
            source = source.replace(
                '(tool == "intent.apply_target_position" && id == PreviewPermit)',
                '(tool == "intent.preview_target_position" && id == PreviewPermit)',
                1,
            )
            typed_path.write_text(source, encoding="utf-8")

            errors = check_schema_catalog.validate(temporary)
            self.assertIn(
                "typed C++ permit requirement drift: intent.preview/apply target",
                errors,
            )

    def test_comment_descriptor_is_not_treated_as_active(self) -> None:
        """The bounded C++ scanner ignores descriptor-shaped comments."""

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            self._contract_copy(temporary)
            registry_path = temporary / "HeptaTrade/tools/trading_tool_registry.cpp"
            source = registry_path.read_text(encoding="utf-8")
            source += (
                "\n// RegisterReadTool(\"comment.only\", \"ignored\", "
                "\"not.a.capability\", 1, kReadResultSchema, ReadHandler());\n"
            )
            registry_path.write_text(source, encoding="utf-8")

            self.assertEqual(check_schema_catalog.validate(temporary), [])

    def test_raw_schema_cannot_adopt_target_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            self._contract_copy(temporary)
            registry_path = temporary / "HeptaTrade/tools/trading_tool_registry.cpp"
            source = registry_path.read_text(encoding="utf-8")
            source = source.replace(
                r'\"reference_price\"', r'\"max_slippage_bps\"', 1
            )
            registry_path.write_text(source, encoding="utf-8")

            errors = check_schema_catalog.validate(temporary)
            self.assertTrue(
                any("C++ input schema exposes domain alias fields" in error
                    for error in errors),
                errors,
            )

    def test_target_preview_must_publish_mutation_command_alias(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            self._contract_copy(temporary)
            registry_path = temporary / "HeptaTrade/tools/trading_tool_registry.cpp"
            source = registry_path.read_text(encoding="utf-8")
            source = source.replace(
                r'\"mutation_command_id\":\"',
                r'\"mutation_command\":\"',
                1,
            )
            registry_path.write_text(source, encoding="utf-8")

            errors = check_schema_catalog.validate(temporary)
            self.assertIn(
                "C++ target preview is missing mutation_command_id output alias",
                errors,
            )

    def test_trade_descriptor_effect_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            self._contract_copy(temporary)
            registry_path = temporary / "HeptaTrade/tools/trading_tool_registry.cpp"
            source = registry_path.read_text(encoding="utf-8")
            source = source.replace(
                "place.effect = TradingToolEffect::Trade;",
                "place.effect = TradingToolEffect::Read;",
                1,
            )
            registry_path.write_text(source, encoding="utf-8")

            errors = check_schema_catalog.validate(temporary)
            self.assertIn(
                "trade catalog entry is not a trade descriptor: trade.place_order",
                errors,
            )


if __name__ == "__main__":
    unittest.main()
