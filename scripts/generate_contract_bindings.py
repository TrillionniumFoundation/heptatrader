#!/usr/bin/env python3
"""Generate active C++ and Python/MCP wire bindings from one catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "schemas/tool-catalog-v1.json"
DIGEST = ROOT / "schemas/tool-catalog-v1.sha256"
CPP = ROOT / "HeptaTrade/tool_host/typed_tool_protocol.cpp"
PYTHON = ROOT / "adapters/mcp/hepta_mcp_server.py"

CPP_BEGIN = "// HEPTA-GENERATED-WIRE-CATALOG-BEGIN"
CPP_END = "// HEPTA-GENERATED-WIRE-CATALOG-END"
PY_BEGIN = "# HEPTA-GENERATED-WIRE-CATALOG-BEGIN"
PY_END = "# HEPTA-GENERATED-WIRE-CATALOG-END"


class CatalogError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def load_catalog(path: Path = CATALOG) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CatalogError(str(error)) from error
    validate_catalog(value)
    return value


def validate_catalog(value: Any) -> None:
    if not isinstance(value, dict):
        raise CatalogError("catalog root must be an object")
    if value.get("schema") != "heptatrader.tool-catalog.v1":
        raise CatalogError("catalog schema mismatch")
    if value.get("protocol") != "hepta.agent-tools":
        raise CatalogError("protocol mismatch")
    if type(value.get("protocol_version")) is not int or \
            value.get("protocol_version") != 1:
        raise CatalogError("protocol version mismatch")
    if value.get("numeric_policy") != {
        "id": "hepta.numeric.fixed-v1",
        "scale": 1_000_000,
        "maximum_raw": 9_000_000_000_000_000,
    }:
        raise CatalogError("numeric policy mismatch")

    fields = value.get("fields")
    if not isinstance(fields, list) or len(fields) != 26:
        raise CatalogError("fields must contain the complete v1 map")
    ids: set[int] = set()
    names: set[str] = set()
    symbols: set[str] = set()
    for field in fields:
        if not isinstance(field, dict) or set(field) != {"id", "name", "symbol"}:
            raise CatalogError("invalid field entry")
        field_id = field["id"]
        name = field["name"]
        symbol = field["symbol"]
        if type(field_id) is not int or not 1 <= field_id <= 65535:
            raise CatalogError("invalid field id")
        if not isinstance(name, str) or not name:
            raise CatalogError("invalid field name")
        if not isinstance(symbol, str) or not symbol.isidentifier():
            raise CatalogError("invalid field symbol")
        if field_id in ids or name in names or symbol in symbols:
            raise CatalogError("duplicate field identity")
        ids.add(field_id)
        names.add(name)
        symbols.add(symbol)
    if ids != set(range(1, 27)):
        raise CatalogError("field ids must be contiguous 1..26")

    aliases = value.get("target_intent_aliases")
    if aliases != {
        "max_slippage_bps": "reference_price",
        "target_position": "quantity",
    }:
        raise CatalogError("target intent alias mismatch")
    target_tools = value.get("target_intent_tools")
    if target_tools != [
        "intent.apply_target_position",
        "intent.preview_target_position",
    ]:
        raise CatalogError("target intent tool scope mismatch")
    if any(destination not in names for destination in aliases.values()):
        raise CatalogError("alias destination is not a field")

    tools = value.get("tools")
    if not isinstance(tools, list) or not tools:
        raise CatalogError("tools must be non-empty")
    tool_names = [item.get("name") for item in tools if isinstance(item, dict)]
    if len(tool_names) != len(tools) or len(set(tool_names)) != len(tool_names):
        raise CatalogError("tool names invalid or duplicated")


def cpp_region(value: dict[str, Any]) -> str:
    fields = sorted(value["fields"], key=lambda item: item["id"])
    enum_lines = []
    for index, field in enumerate(fields):
        suffix = "," if index + 1 < len(fields) else ""
        enum_lines.append(f"    {field['symbol']} = {field['id']}{suffix}")
    cases = "\n".join(
        f'    case {field["symbol"]}: return "{field["name"]}";'
        for field in fields
    )
    numeric = value["numeric_policy"]
    return "\n".join([
        CPP_BEGIN,
        "enum FieldId",
        "{",
        "\n".join(enum_lines),
        "};",
        "",
        "const char* FieldName(unsigned int id)",
        "{",
        "    switch (id)",
        "    {",
        cases,
        "    }",
        '    return "unknown";',
        "}",
        "",
        f"const long long kWireNumericScale = {numeric['scale']}LL;",
        f'const char* const kWireNumericPolicy = "{numeric["id"]}";',
        CPP_END,
    ])


def python_region(value: dict[str, Any]) -> str:
    fields = sorted(value["fields"], key=lambda item: item["id"])
    field_lines = "\n".join(
        f'    "{field["name"]}": {field["id"]},'
        for field in fields
    )
    aliases = value["target_intent_aliases"]
    alias_lines = "\n".join(
        f'    "{name}": "{aliases[name]}",'
        for name in sorted(aliases)
    )
    tools = "\n".join(
        f'    "{name}",' for name in value["target_intent_tools"]
    )
    numeric = value["numeric_policy"]
    return "\n".join([
        PY_BEGIN,
        f'PROTOCOL_NAME = "{value["protocol"]}"',
        f'PROTOCOL_VERSION = {value["protocol_version"]}',
        f'NUMERIC_POLICY = "{numeric["id"]}"',
        f'NUMERIC_SCALE = {numeric["scale"]}',
        f'NUMERIC_MAXIMUM_RAW = {numeric["maximum_raw"]}',
        "FIELD_IDS = {",
        field_lines,
        "}",
        "TARGET_INTENT_TO_WIRE = {",
        alias_lines,
        "}",
        "TARGET_INTENT_TOOLS = frozenset({",
        tools,
        "})",
        PY_END,
    ])


def replace_region(text: str, begin: str, end: str, rendered: str) -> str:
    start = text.find(begin)
    finish = text.find(end)
    if start < 0 or finish < start:
        raise CatalogError(f"generated markers missing: {begin}")
    finish += len(end)
    return text[:start] + rendered + text[finish:]


def update_file(path: Path, begin: str, end: str,
                rendered: str, write: bool) -> bool:
    current = path.read_text(encoding="utf-8")
    expected = replace_region(current, begin, end, rendered)
    if expected == current:
        return True
    if write:
        path.write_text(expected, encoding="utf-8")
        return True
    print(f"[CONTRACT-BINDINGS] generated drift: {path.relative_to(ROOT)}",
          file=sys.stderr)
    return False


def run(write: bool) -> bool:
    value = load_catalog()
    ok = update_file(CPP, CPP_BEGIN, CPP_END, cpp_region(value), write)
    ok = update_file(PYTHON, PY_BEGIN, PY_END, python_region(value), write) and ok
    expected_digest = hashlib.sha256(
        canonical_json(value).encode("utf-8")
    ).hexdigest() + "\n"
    actual = DIGEST.read_text(encoding="utf-8") if DIGEST.exists() else ""
    if actual != expected_digest:
        if write:
            DIGEST.write_text(expected_digest, encoding="utf-8")
        else:
            print("[CONTRACT-BINDINGS] catalog digest drift", file=sys.stderr)
            ok = False
    if ok:
        print("[CONTRACT-BINDINGS] PASS")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        return 0 if run(args.write) else 1
    except CatalogError as error:
        print(f"[CONTRACT-BINDINGS] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
