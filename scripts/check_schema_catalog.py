#!/usr/bin/env python3
"""Validate the checked-in wire/schema catalog and active bindings.

The catalog is deliberately small and reviewable.  The C++ registry and
Python bridge remain the executable implementations; this checker catches
accidental drift without contacting a broker or mutating the repository.

The checker intentionally parses only stable declaration forms (descriptor
registration calls, descriptor assignment blocks and Python literals).  It
does not try to be a C++ or Python interpreter, so unrelated implementation
refactors do not change the result while a contract edit cannot silently pass.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RAW_PLACE_RE = re.compile(
    r"(?:^|[,\s])(trade\.place|operator\.trade\.place)(?:[,\s]|$)"
)

TARGET_INTENT_TO_WIRE = {
    "target_position": "quantity",
    "max_slippage_bps": "reference_price",
}
CATALOG_WIRE_ALIASES = {
    "max_slippage_bps": "reference_price",
    "mutation_command_id": "tool_call_id",
    "target_position": "quantity",
}
TARGET_INTENT_TOOLS = frozenset({
    "intent.preview_target_position",
    "intent.apply_target_position",
})
# The JSON catalog is intentionally a small, reviewed allow-list rather than
# a second source of truth that can silently grow with the C++ registry.  Keep
# the public name/capability/visibility contract here as well as comparing it
# with the active descriptors below.  This catches the case where both sides
# are edited together (or a descriptor is removed from both) without a review
# of the protocol boundary.
CANONICAL_TOOL_CONTRACT = {
    "system.tools.list": ("system.read", "ordinary", "read"),
    "system.tools.describe": ("system.read", "ordinary", "read"),
    "system.cancel_request": ("system.read", "ordinary", "read"),
    "system.get_health": ("system.read", "ordinary", "read"),
    "market.get_quote": ("market.read", "ordinary", "read"),
    "account.get_summary": ("account.read", "ordinary", "read"),
    "portfolio.list_positions": ("portfolio.read", "ordinary", "read"),
    "orders.list": ("orders.read", "ordinary", "read"),
    "execution.get_command_status": ("orders.read", "ordinary", "read"),
    "risk.get_limits": ("risk.read", "ordinary", "read"),
    "decision.get_snapshot": ("system.read", "ordinary", "read"),
    "intent.preview_target_position": ("risk.read", "ordinary", "read"),
    "intent.apply_target_position": ("intent.apply", "ordinary", "trade"),
    "events.wait": ("events.read", "ordinary", "read"),
    "watch.get_snapshot": ("system.read", "watch", "read"),
    "risk.preview_order": ("operator.risk.preview", "operator", "read"),
    "trade.place_order": ("operator.trade.place", "operator", "trade"),
    "trade.cancel_order": ("trade.cancel", "ordinary", "trade"),
    "risk.preview_flatten": ("trade.flatten", "conditional", "read"),
    "trade.flatten_position": ("trade.flatten", "conditional", "trade"),
}
REQUIRED_FIELD_IDS = {
    "quantity": 12,
    "reference_price": 14,
    "preview_permit": 25,
    "command_id": 26,
}
# Keep the complete v1 typed-field map here.  The four fields used by target
# intents/permits are especially security-sensitive, but checking the whole
# map prevents an unrelated ID collision from changing the meaning of an
# already-issued request after a seemingly harmless protocol edit.
CANONICAL_FIELD_IDS = {
    "session_token": 1,
    "tool_call_id": 2,
    "tool_name": 3,
    "instrument": 4,
    "order_id": 5,
    "symbol": 6,
    "currency": 7,
    "sec_type": 8,
    "exchange": 9,
    "side": 10,
    "order_type": 11,
    "quantity": 12,
    "limit_price": 13,
    "reference_price": 14,
    "expires_at_ms": 15,
    "timeout_ms": 16,
    "after_sequence": 17,
    "tif": 18,
    "queue_deadline_at_ms": 19,
    "cancel_tool_call_id": 20,
    "target_tool_name": 21,
    "protocol_min_version": 22,
    "protocol_max_version": 23,
    "expected_schema_hash": 24,
    "preview_permit": 25,
    "command_id": 26,
}
CPP_FIELD_SYMBOLS = {
    "SessionToken": (1, "session_token"),
    "ToolCallId": (2, "tool_call_id"),
    "ToolName": (3, "tool_name"),
    "Instrument": (4, "instrument"),
    "OrderId": (5, "order_id"),
    "Symbol": (6, "symbol"),
    "Currency": (7, "currency"),
    "SecType": (8, "sec_type"),
    "Exchange": (9, "exchange"),
    "Side": (10, "side"),
    "OrderType": (11, "order_type"),
    "Quantity": (12, "quantity"),
    "LimitPrice": (13, "limit_price"),
    "ReferencePrice": (14, "reference_price"),
    "ExpiresAtMs": (15, "expires_at_ms"),
    "WaitTimeoutMs": (16, "timeout_ms"),
    "AfterEventSequence": (17, "after_sequence"),
    "TimeInForce": (18, "tif"),
    "QueueDeadlineAtMs": (19, "queue_deadline_at_ms"),
    "CancelToolCallId": (20, "cancel_tool_call_id"),
    "TargetToolName": (21, "target_tool_name"),
    "ProtocolMinVersion": (22, "protocol_min_version"),
    "ProtocolMaxVersion": (23, "protocol_max_version"),
    "ExpectedSchemaHash": (24, "expected_schema_hash"),
    "PreviewPermit": (25, "preview_permit"),
    "TargetCommandId": (26, "command_id"),
}
REQUIRED_DESCRIPTOR_FIELDS = {
    "name",
    "description",
    "required_capability",
    "effect",
    "timeout_ms",
    "schema_hash",
    "input_schema",
    "result_schema",
}

# These are the authority-boundary fields that must remain explicit in both
# the C++ descriptor schemas and the canonical catalog's active implementation.
# Ordinary read descriptors are intentionally not listed: their result payload
# is generated by the execution authority and may evolve independently.
REQUIRED_INPUT_FIELDS = {
    "risk.preview_order": {
        "instrument", "side", "quantity", "order_type", "tif", "expires_at_ms",
    },
    "intent.preview_target_position": {
        "instrument", "quantity", "reference_price", "expires_at_ms",
    },
    "intent.apply_target_position": {
        "instrument", "quantity", "reference_price", "expires_at_ms",
        "preview_permit",
    },
    "trade.place_order": {
        "instrument", "side", "quantity", "order_type", "tif",
        "expires_at_ms", "preview_permit",
    },
    "risk.preview_flatten": {"instrument"},
    "trade.flatten_position": {"instrument", "preview_permit"},
}
PERMIT_REQUIRED_TOOLS = {
    "intent.apply_target_position",
    "trade.place_order",
    "trade.flatten_position",
}
PERMIT_FORBIDDEN_TOOLS = {
    "risk.preview_order",
    "intent.preview_target_position",
    "risk.preview_flatten",
}
EXPECTED_TRADE_TOOLS = {
    "intent.apply_target_position",
    "trade.place_order",
    "trade.cancel_order",
    "trade.flatten_position",
}
CANONICAL_UNSUPPORTED_ENVIRONMENTS = frozenset({
    "LIVE",
    "LIVE_REDUCE_ONLY",
    "LIVE_CAPPED",
})


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Decode JSON objects without silently accepting duplicate keys."""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path, errors: list[str], root: Path | None = None) -> Any:
    if root is None:
        root = ROOT
    try:
        return json.loads(
            path.read_text(encoding="utf-8-sig"),
            object_pairs_hook=_strict_object_pairs,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        errors.append(f"{_relative(path, root)} invalid: {error}")
        return None


def _cpp_code_mask(source: str) -> list[bool]:
    """Return positions that are C++ code rather than comments/literals.

    The registry parser is intentionally not a C++ parser, but it must not
    mistake a descriptor-looking sentence in a comment or string for an
    active registration.  Keeping this small lexical pass separate makes the
    balanced-call scanner deterministic while tolerating comments inside real
    calls.  C++ raw strings are not used by the active registry; ordinary
    escaped string literals are handled explicitly.
    """

    mask = [False] * len(source)
    state = "code"
    escaped = False
    index = 0
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""
        if state == "line_comment":
            if char == "\n":
                state = "code"
            index += 1
            continue
        if state == "block_comment":
            if char == "*" and next_char == "/":
                state = "code"
                index += 2
            else:
                index += 1
            continue
        if state in {"string", "char"}:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif (state == "string" and char == '"') or (
                    state == "char" and char == "'"):
                state = "code"
            index += 1
            continue
        if char == "/" and next_char == "/":
            state = "line_comment"
            index += 2
            continue
        if char == "/" and next_char == "*":
            state = "block_comment"
            index += 2
            continue
        if char == '"':
            state = "string"
            index += 1
            continue
        if char == "'":
            state = "char"
            index += 1
            continue
        mask[index] = True
        index += 1
    return mask


def _strip_cpp_comments(source: str) -> str:
    """Replace comments with spaces while preserving literals and offsets."""

    output = list(source)
    state = "code"
    escaped = False
    index = 0
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""
        if state == "line_comment":
            if char == "\n":
                state = "code"
            elif char != "\r":
                output[index] = " "
            index += 1
            continue
        if state == "block_comment":
            if char == "*" and next_char == "/":
                output[index] = output[index + 1] = " "
                state = "code"
                index += 2
            else:
                if char not in "\r\n":
                    output[index] = " "
                index += 1
            continue
        if state in {"string", "char"}:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif (state == "string" and char == '"') or (
                    state == "char" and char == "'"):
                state = "code"
            index += 1
            continue
        if char == "/" and next_char == "/":
            output[index] = output[index + 1] = " "
            state = "line_comment"
            index += 2
            continue
        if char == "/" and next_char == "*":
            output[index] = output[index + 1] = " "
            state = "block_comment"
            index += 2
            continue
        if char == '"':
            state = "string"
        elif char == "'":
            state = "char"
        index += 1
    return "".join(output)


def _find_balanced_calls(source: str, function_name: str) -> list[str]:
    """Extract calls while respecting strings/comments and nested delimiters."""

    calls: list[str] = []
    code_mask = _cpp_code_mask(source)
    position = 0
    while True:
        start = source.find(function_name, position)
        if start < 0:
            break
        if not all(code_mask[start:start + len(function_name)]):
            position = start + len(function_name)
            continue
        if start and (source[start - 1].isalnum() or source[start - 1] == "_"):
            position = start + len(function_name)
            continue
        opening = start + len(function_name)
        while opening < len(source) and source[opening].isspace():
            opening += 1
        if opening >= len(source) or source[opening] != "(":
            position = opening
            continue

        depth = 0
        in_string = False
        in_char = False
        escaped = False
        line_comment = False
        block_comment = False
        cursor = opening
        while cursor < len(source):
            char = source[cursor]
            next_char = source[cursor + 1] if cursor + 1 < len(source) else ""
            if line_comment:
                if char == "\n":
                    line_comment = False
            elif block_comment:
                if char == "*" and next_char == "/":
                    block_comment = False
                    cursor += 1
            elif in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif in_char:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == "'":
                    in_char = False
            else:
                if char == "/" and next_char == "/":
                    line_comment = True
                    cursor += 1
                elif char == "/" and next_char == "*":
                    block_comment = True
                    cursor += 1
                elif char == '"':
                    in_string = True
                elif char == "'":
                    in_char = True
                elif char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0:
                        calls.append(source[start:cursor + 1])
                        position = cursor + 1
                        break
            cursor += 1
        else:
            break
    return calls


def _split_cpp_arguments(call: str) -> list[str]:
    body = call[call.find("(") + 1:-1]
    arguments: list[str] = []
    begin = 0
    nesting = {"(": 0, "{": 0, "[": 0}
    closing = {")": "(", "}": "{", "]": "[",
    }
    in_string = False
    in_char = False
    escaped = False
    for index, char in enumerate(body):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if in_char:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == "'":
                in_char = False
            continue
        if char == '"':
            in_string = True
        elif char == "'":
            in_char = True
        elif char in nesting:
            nesting[char] += 1
        elif char in closing:
            nesting[closing[char]] -= 1
        elif char == "," and not any(nesting.values()):
            arguments.append(body[begin:index].strip())
            begin = index + 1
    arguments.append(body[begin:].strip())
    return arguments


def _cpp_string_parts(expression: str) -> list[str]:
    """Decode adjacent C++ string literals (JSON escaping is compatible)."""

    literals = re.findall(r'"(?:\\.|[^"\\])*"', expression)
    if not literals:
        return []
    remainder = re.sub(r'"(?:\\.|[^"\\])*"', "", expression)
    # Permit only whitespace and comments between adjacent literals.  This
    # avoids treating an arbitrary expression containing one string as a schema.
    remainder = re.sub(r"/\*.*?\*/|//[^\n]*", "", remainder, flags=re.S)
    if remainder.strip():
        return []
    try:
        return [json.loads(literal) for literal in literals]
    except json.JSONDecodeError:
        return []


def _cpp_string(expression: str) -> str | None:
    parts = _cpp_string_parts(expression)
    if not parts:
        return None
    return "".join(parts)


def _extract_cpp_descriptors(source: str, errors: list[str]) -> tuple[
    dict[str, dict[str, Any]], dict[str, str]
]:
    """Return descriptor metadata and each descriptor's schema expression."""

    descriptors: dict[str, dict[str, Any]] = {}
    schema_expressions: dict[str, str] = {}
    for call in _find_balanced_calls(source, "RegisterReadTool"):
        arguments = _split_cpp_arguments(call)
        if len(arguments) != 6:
            # The function declaration itself also contains this token.
            continue
        name = _cpp_string(arguments[0])
        capability = _cpp_string(arguments[2])
        if name is None:
            continue
        if capability is None:
            errors.append(f"C++ read descriptor has no literal capability: {name}")
            continue
        if name in descriptors:
            errors.append(f"C++ descriptor is registered more than once: {name}")
            continue
        descriptors[name] = {
            "capability": capability,
            "effect": "read",
        }
        schema_expressions[name] = arguments[4]

    # Trade descriptors are assigned in bounded local blocks.  Capturing only
    # blocks that end in their own map assignment avoids matching temporary
    # descriptor variables used by read paths.
    block_pattern = re.compile(
        r"TradingToolDescriptor\s+(?P<variable>[A-Za-z_]\w*)\s*;"
        r"(?P<body>.*?m_descriptors\[\s*(?P=variable)\.name\s*\]"
        r"\s*=\s*(?P=variable)\s*;)",
        re.S,
    )
    for match in block_pattern.finditer(_strip_cpp_comments(source)):
        variable = match.group("variable")
        body = match.group("body")
        name_match = re.search(
            rf"\b{re.escape(variable)}\.name\s*=\s*\"([^\"]+)\"", body
        )
        capability_match = re.search(
            rf"\b{re.escape(variable)}\.requiredCapability\s*=\s*\"([^\"]+)\"",
            body,
        )
        if name_match is None or capability_match is None:
            errors.append(f"C++ trade descriptor {variable} is incomplete")
            continue
        name = name_match.group(1)
        if name in descriptors:
            errors.append(f"C++ descriptor is registered more than once: {name}")
            continue
        effect_match = re.search(
            rf"\b{re.escape(variable)}\.effect\s*=\s*"
            r"TradingToolEffect::(Trade|Read)\b",
            body,
        )
        if effect_match is None:
            errors.append(f"C++ trade descriptor has no literal effect: {name}")
            continue
        descriptors[name] = {
            "capability": capability_match.group(1),
            "effect": effect_match.group(1).lower(),
        }
        schema_match = re.search(
            rf"\b{re.escape(variable)}\.inputSchema\s*=\s*(.*?);", body, re.S
        )
        if schema_match is not None:
            schema_expressions[name] = schema_match.group(1)
        else:
            errors.append(f"C++ trade descriptor has no input schema: {name}")
    return descriptors, schema_expressions


def _extract_cpp_schema_constants(source: str) -> dict[str, Any]:
    constants: dict[str, Any] = {}
    pattern = re.compile(
        r"\bconst\s+char\s*\*\s+(?P<name>k[A-Za-z0-9_]+)\s*=\s*"
        r"(?P<body>.*?);",
        re.S,
    )
    for match in pattern.finditer(source):
        text = _cpp_string(match.group("body"))
        if text is None:
            continue
        try:
            constants[match.group("name")] = json.loads(text)
        except json.JSONDecodeError:
            continue
    return constants


def _resolve_cpp_schema(expression: str, constants: dict[str, Any]) -> Any | None:
    expression = expression.strip()
    if re.fullmatch(r"k[A-Za-z0-9_]+", expression):
        return constants.get(expression)
    text = _cpp_string(expression)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _extract_python_literal(tree: ast.AST, name: str) -> Any | None:
    def decode(node: ast.AST) -> Any:
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "frozenset" and len(node.args) == 1:
                value = decode(node.args[0])
                return frozenset(value)
        return ast.literal_eval(node)

    for node in ast.walk(tree):
        value_node: ast.AST | None = None
        target_node: ast.AST | None = None
        if isinstance(node, ast.Assign):
            if len(node.targets) == 1:
                target_node = node.targets[0]
                value_node = node.value
        elif isinstance(node, ast.AnnAssign):
            target_node = node.target
            value_node = node.value
        if isinstance(target_node, ast.Name) and target_node.id == name:
            try:
                return decode(value_node) if value_node is not None else None
            except (ValueError, TypeError):
                return None
    return None


def _check_python_contract(
    path: Path,
    catalog_aliases: Any,
    errors: list[str],
    root: Path,
) -> None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    except (OSError, SyntaxError) as error:
        errors.append(f"{_relative(path, root)} invalid Python: {error}")
        return
    aliases = _extract_python_literal(tree, "TARGET_INTENT_TO_WIRE")
    target_tools = _extract_python_literal(tree, "TARGET_INTENT_TOOLS")
    field_ids = _extract_python_literal(tree, "FIELD_IDS")
    descriptor_fields = _extract_python_literal(tree, "DESCRIPTOR_FIELDS")
    if aliases != TARGET_INTENT_TO_WIRE:
        errors.append("MCP target alias map drift")
    if catalog_aliases != CATALOG_WIRE_ALIASES:
        errors.append("catalog wire_aliases drift")
    if target_tools != TARGET_INTENT_TOOLS:
        errors.append("MCP target tool scope drift")
    if descriptor_fields != REQUIRED_DESCRIPTOR_FIELDS:
        errors.append("MCP descriptor field set drift")
    if not isinstance(field_ids, dict):
        errors.append("MCP FIELD_IDS is not a literal mapping")
        return
    if field_ids != CANONICAL_FIELD_IDS:
        errors.append("MCP FIELD_IDS map drift")
    for field, expected_id in REQUIRED_FIELD_IDS.items():
        if field_ids.get(field) != expected_id:
            errors.append(f"MCP FIELD_IDS drift: {field}")
    if "target_position" in field_ids or "max_slippage_bps" in field_ids:
        errors.append("MCP domain aliases must not become wire field IDs")
    values = [value for value in field_ids.values() if isinstance(value, int)]
    if len(values) != len(set(values)):
        errors.append("MCP FIELD_IDS contains duplicate numeric IDs")
    for alias, wire_field in TARGET_INTENT_TO_WIRE.items():
        if field_ids.get(wire_field) not in REQUIRED_FIELD_IDS.values():
            errors.append(f"MCP alias destination has no canonical field ID: {alias}")
    output_alias = "mutation_command_id"
    if CATALOG_WIRE_ALIASES.get(output_alias) not in field_ids:
        errors.append("catalog mutation-command alias has no MCP wire field")


def _check_input_schemas(
    descriptors: dict[str, dict[str, Any]],
    schema_expressions: dict[str, str],
    constants: dict[str, Any],
    errors: list[str],
) -> None:
    for name, required_fields in REQUIRED_INPUT_FIELDS.items():
        if name not in descriptors:
            errors.append(f"C++ descriptor missing authority-boundary tool: {name}")
            continue
        schema = _resolve_cpp_schema(schema_expressions.get(name, ""), constants)
        if not isinstance(schema, dict):
            errors.append(f"C++ descriptor schema is not JSON: {name}")
            continue
        required = schema.get("required")
        if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
            errors.append(f"C++ descriptor schema has invalid required list: {name}")
            continue
        required_set = set(required)
        missing = sorted(required_fields - required_set)
        if missing:
            errors.append(
                f"C++ descriptor schema missing required fields for {name}: {missing}"
            )
        has_permit = "preview_permit" in required_set
        if name in PERMIT_REQUIRED_TOOLS and not has_permit:
            errors.append(f"C++ descriptor must require preview_permit: {name}")
        if name in PERMIT_FORBIDDEN_TOOLS and has_permit:
            errors.append(f"C++ preview descriptor must not require preview_permit: {name}")
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            errors.append(f"C++ descriptor schema has invalid properties: {name}")
            continue
        if name in PERMIT_REQUIRED_TOOLS:
            permit = properties.get("preview_permit")
            if not isinstance(permit, dict) or permit.get("type") != "string" or \
                    permit.get("minLength") != 71 or permit.get("maxLength") != 71:
                errors.append(f"C++ permit schema is not canonical: {name}")
        elif "preview_permit" in properties:
            errors.append(f"C++ preview schema exposes a permit field: {name}")
        if "mutation_command_id" in properties or "mutation_command_id" in required_set:
            errors.append(f"C++ input schema exposes output alias field: {name}")
        # Domain aliases are accepted only by the MCP target-intent adapter;
        # the typed C++ schemas must expose canonical wire names even for the
        # target tools, and raw-order inputs must never inherit that adapter
        # convenience accidentally.
        if any(
                alias in properties or alias in required_set
                for alias in TARGET_INTENT_TO_WIRE):
            errors.append(f"C++ input schema exposes domain alias fields: {name}")


def _check_cpp_wire_fields(source: str, errors: list[str]) -> None:
    """Ensure the typed C++ wire IDs used by aliases/permits stay canonical."""

    enum_match = re.search(
        r"\benum\s+FieldId\s*\{(?P<body>.*?)\};", source, re.S
    )
    if enum_match is None:
        errors.append("typed C++ protocol FieldId enum is missing")
        return
    enum_body = enum_match.group("body")
    for symbol, (field_id, field_name) in CPP_FIELD_SYMBOLS.items():
        if not re.search(rf"\b{re.escape(symbol)}\s*=\s*{field_id}\b", enum_body):
            errors.append(f"typed C++ FieldId drift: {field_name}")
        if not re.search(
            rf"case\s+{re.escape(symbol)}\s*:\s*return\s+\"{re.escape(field_name)}\"",
            source,
        ):
            errors.append(f"typed C++ FieldName drift: {field_name}")

    # The typed protocol has two per-tool allow/required tables.  Keep the
    # permit boundary explicit in the checker so a field-ID-preserving edit
    # cannot accidentally make a preview accept a permit (or make a mutation
    # omit one).  Whitespace is normalized to make this independent of local
    # formatting while the branch/tool literals remain auditable.
    compact = re.sub(r"\s+", " ", source)

    def function_body(name: str) -> str | None:
        marker = re.search(
            rf"\bbool\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", compact
        )
        if marker is None:
            return None
        opening = compact.find("{", marker.start(), marker.end())
        depth = 0
        for index in range(opening, len(compact)):
            if compact[index] == "{":
                depth += 1
            elif compact[index] == "}":
                depth -= 1
                if depth == 0:
                    return compact[opening + 1:index]
        return None

    allowed_body = function_body("IsToolFieldAllowed")
    required_body = function_body("IsRequiredToolField")
    if allowed_body is None:
        errors.append("typed C++ IsToolFieldAllowed function is missing")
    if required_body is None:
        errors.append("typed C++ IsRequiredToolField function is missing")

    allowed_fragments = {
        "intent.preview_target_position":
            'if (tool == "intent.preview_target_position") return '
            'id == Instrument || id == Quantity || id == ReferencePrice || '
            'id == ExpiresAtMs;',
        "intent.apply_target_position":
            'if (tool == "intent.apply_target_position") return '
            'id == Instrument || id == Quantity || id == ReferencePrice || '
            'id == ExpiresAtMs || id == PreviewPermit;',
        "trade.flatten_position":
            'if (tool == "trade.flatten_position") return '
            'id == Instrument || id == PreviewPermit;',
        "trade.place_order":
            '(tool == "trade.place_order" && id == PreviewPermit)',
    }
    required_fragments = {
        "intent.preview/apply target":
            'if (tool == "intent.preview_target_position" || '
            'tool == "intent.apply_target_position") { return '
            'id == Instrument || id == Quantity || id == ReferencePrice || '
            'id == ExpiresAtMs || '
            '(tool == "intent.apply_target_position" && id == PreviewPermit);',
        "trade.flatten_position":
            'if (tool == "trade.flatten_position") return '
            'id == Instrument || id == PreviewPermit;',
        "trade.place_order":
            '(tool == "trade.place_order" && id == PreviewPermit)',
    }
    if allowed_body is not None:
        for tool, fragment in allowed_fragments.items():
            if fragment not in allowed_body:
                errors.append(f"typed C++ permit scope drift: {tool}")
    if required_body is not None:
        for tool, fragment in required_fragments.items():
            if fragment not in required_body:
                errors.append(f"typed C++ permit requirement drift: {tool}")


def _check_catalog_descriptors(
    catalog_tools: list[Any],
    cpp_descriptors: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    catalog_map: dict[str, dict[str, Any]] = {}
    for index, tool in enumerate(catalog_tools):
        if not isinstance(tool, dict):
            errors.append(f"tool catalog entry {index} is not an object")
            continue
        name = tool.get("name")
        capability = tool.get("capability")
        visibility = tool.get("visibility")
        if not isinstance(name, str) or not name:
            errors.append(f"tool catalog entry {index} has invalid name")
            continue
        if name in catalog_map:
            errors.append(f"tool catalog names are duplicated: {name}")
            continue
        catalog_map[name] = tool
        if not isinstance(capability, str) or not capability:
            errors.append(f"tool catalog entry {index} has invalid capability")
        if visibility not in {"ordinary", "watch", "operator", "conditional"}:
            errors.append(f"tool catalog entry {index} has invalid visibility")
        if visibility == "operator" and not str(capability).startswith("operator."):
            errors.append(f"raw operator tool has non-operator capability: {name}")

    catalog_names = set(catalog_map)
    canonical_names = set(CANONICAL_TOOL_CONTRACT)
    for name in sorted(canonical_names - catalog_names):
        errors.append(f"canonical catalog is missing tool: {name}")
    for name in sorted(catalog_names - canonical_names):
        errors.append(f"canonical catalog has unexpected tool: {name}")
    cpp_names = set(cpp_descriptors)
    for name in sorted(cpp_names - catalog_names):
        errors.append(f"C++ descriptor missing from canonical catalog: {name}")
    for name in sorted(catalog_names - cpp_names):
        errors.append(f"canonical catalog tool missing from C++ registry: {name}")
    for name in sorted(catalog_names & cpp_names):
        tool = catalog_map[name]
        expected = cpp_descriptors[name]
        canonical = CANONICAL_TOOL_CONTRACT.get(name)
        if canonical is not None:
            canonical_capability, canonical_visibility, canonical_effect = canonical
            if tool.get("capability") != canonical_capability:
                errors.append(
                    f"canonical capability mismatch for {name}: "
                    f"catalog={tool.get('capability')} expected={canonical_capability}"
                )
            if tool.get("visibility") != canonical_visibility:
                errors.append(
                    f"canonical visibility mismatch for {name}: "
                    f"catalog={tool.get('visibility')} expected={canonical_visibility}"
                )
            if expected.get("effect") != canonical_effect:
                errors.append(
                    f"canonical C++ effect mismatch for {name}: "
                    f"cpp={expected.get('effect')} expected={canonical_effect}"
                )
        if tool.get("capability") != expected["capability"]:
            errors.append(
                f"capability drift for {name}: catalog={tool.get('capability')} "
                f"cpp={expected['capability']}"
            )
        visibility = tool.get("visibility")
        effect = expected["effect"]
        # Operator risk preview is intentionally a read effect; it still
        # requires an operator capability before any mutation can be sent.
        if visibility == "operator" and effect != "trade" and name != "risk.preview_order":
            errors.append(f"operator visibility is not a trade descriptor: {name}")
        if name in EXPECTED_TRADE_TOOLS and effect != "trade":
            errors.append(f"trade catalog entry is not a trade descriptor: {name}")
        if name not in EXPECTED_TRADE_TOOLS and effect == "trade":
            errors.append(f"read catalog entry is unexpectedly a trade descriptor: {name}")
        # Target apply and cancel are ordinary Agent mutations guarded by
        # server-issued permits/ownership.  Raw place remains operator-only.
        ordinary_trade = {"intent.apply_target_position", "trade.cancel_order"}
        if effect == "trade" and visibility not in {"operator", "conditional"} and name not in ordinary_trade:
            errors.append(f"trade descriptor is not operator/conditional: {name}")

    raw = catalog_map.get("trade.place_order")
    if raw is None:
        errors.append("canonical catalog is missing raw trade.place_order")
    elif raw.get("capability") != "operator.trade.place" or raw.get("visibility") != "operator":
        errors.append("raw trade.place_order must remain operator-only")
    target_entries = {name for name in catalog_map if name in TARGET_INTENT_TOOLS}
    if target_entries != TARGET_INTENT_TOOLS:
        errors.append("canonical catalog target tool scope drift")


def _check_result_alias_bindings(registry: str, mcp: str, errors: list[str]) -> None:
    """Keep the server-issued target command alias visible at both bindings."""

    # The target preview payload is intentionally distinct from transport's
    # ``command_id`` field.  Match the encoded JSON key/value prefix rather
    # than merely searching prose so a renamed output field cannot hide behind
    # a stale description string.
    if '\\"mutation_command_id\\":\\"' not in registry:
        errors.append("C++ target preview is missing mutation_command_id output alias")
    if "mutation_command_id" not in mcp:
        errors.append("MCP target apply contract is missing mutation_command_id alias")


def validate(root: Path | None = None) -> list[str]:
    """Return deterministic contract errors for ``root`` (default repository root)."""

    if root is None:
        root = ROOT
    root = root.resolve()
    errors: list[str] = []
    catalog_path = root / "schemas/tool-catalog-v1.json"
    catalog = _load_json(catalog_path, errors, root)
    if not isinstance(catalog, dict):
        return errors
    if catalog.get("schema") != "heptatrader.tool-catalog.v1":
        errors.append("tool catalog schema mismatch")
    if catalog.get("protocol") != "hepta.agent-tools":
        errors.append("tool catalog protocol mismatch")
    if catalog.get("protocol_version") != 1:
        errors.append("tool catalog protocol version mismatch")
    unsupported = catalog.get("unsupported_environments")
    if not isinstance(unsupported, list) or \
            frozenset(unsupported) != CANONICAL_UNSUPPORTED_ENVIRONMENTS or \
            len(unsupported) != len(CANONICAL_UNSUPPORTED_ENVIRONMENTS):
        errors.append("tool catalog unsupported environment set drift")
    tools = catalog.get("tools")
    if not isinstance(tools, list) or not tools:
        errors.append("tool catalog tools must be a non-empty list")
        return errors

    registry_path = root / "HeptaTrade/tools/trading_tool_registry.cpp"
    typed_protocol_path = root / "HeptaTrade/tool_host/typed_tool_protocol.cpp"
    mcp_path = root / "adapters/mcp/hepta_mcp_server.py"
    try:
        registry = registry_path.read_text(encoding="utf-8-sig")
        typed_protocol = typed_protocol_path.read_text(encoding="utf-8-sig")
        mcp = mcp_path.read_text(encoding="utf-8-sig")
    except OSError as error:
        errors.append(f"active binding unreadable: {error}")
        return errors

    cpp_descriptors, schema_expressions = _extract_cpp_descriptors(registry, errors)
    _check_catalog_descriptors(tools, cpp_descriptors, errors)
    constants = _extract_cpp_schema_constants(registry)
    _check_input_schemas(cpp_descriptors, schema_expressions, constants, errors)
    _check_cpp_wire_fields(typed_protocol, errors)
    _check_python_contract(mcp_path, catalog.get("wire_aliases"), errors, root)
    _check_result_alias_bindings(registry, mcp, errors)

    try:
        recorded = (root / "schemas/tool-catalog-v1.sha256").read_text(
            encoding="utf-8-sig"
        ).strip()
    except OSError as error:
        errors.append(f"tool catalog digest unreadable: {error}")
    else:
        canonical = json.dumps(catalog, sort_keys=True, separators=(",", ":"))
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if recorded != expected:
            errors.append("tool catalog digest drift")

    for forbidden in ("LIVE_REDUCE_ONLY", "LIVE_CAPPED"):
        if forbidden in registry:
            errors.append(f"active Registry accepts dormant LIVE state: {forbidden}")
    if 'place.requiredCapability = "operator.trade.place";' not in registry:
        errors.append("raw place is not operator-only")
    if 'applyTarget.requiredCapability = "intent.apply";' not in registry:
        errors.append("target apply capability binding is missing")
    if "mutation_command_id" not in registry:
        errors.append("C++ target result is missing mutation_command_id alias")
    if "system.tools.list" not in mcp or "system.tools.describe" not in mcp:
        errors.append("MCP bridge does not use runtime discovery")
    if re.search(r"broker.*credential|credential.*broker", mcp, re.IGNORECASE):
        errors.append("MCP bridge contains broker credential surface")

    for path in (root / "systemd").glob("*agent*env.example"):
        if "operator" not in path.name and RAW_PLACE_RE.search(
            path.read_text(encoding="utf-8-sig")
        ):
            errors.append(f"ordinary Agent example exposes raw place: {path.name}")
    return errors


def main() -> int:
    errors = validate()
    for error in errors:
        print(f"[SCHEMA-CATALOG] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[SCHEMA-CATALOG] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
