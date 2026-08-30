#!/usr/bin/python3

import copy
import hashlib
import json
import math
import os
import socket
import stat
import struct
import sys
import uuid


PROTOCOL_NAME = "hepta.agent-tools"
PROTOCOL_VERSION = 1
DISCOVERY_SCHEMA_VERSION = 2
MAX_MESSAGE_BYTES = 1_048_576
MAX_REQUEST_BYTES = 65_536
MAX_TOKEN_BYTES = 512
MAX_UNIX_PATH_BYTES = 107
MAX_TOOL_NAME_BYTES = 64
MAX_REASON_CODE_BYTES = 128
MAX_DETAIL_BYTES = 65_536
# Keep the Python response decoder bounded to the same structural limits as
# the native typed-result codec.  The payload is intentionally opaque at this
# layer, but it is still parsed and forwarded to an MCP peer; accepting an
# unbounded tree here would make the two wire bindings disagree (and could
# turn a malformed response into a recursion/memory denial of service).
MAX_RESULT_NODES = 100_000
MAX_RESULT_DEPTH = 64
MAX_RESULT_STRING_BYTES = MAX_MESSAGE_BYTES
# JSON-RPC metadata is not part of the native tool contract.  Keep echoed
# identifiers and error messages substantially smaller than the 1 MiB input
# ceiling so a malformed client request cannot amplify into an equally large
# response or expose an exception/OS diagnostic over the Agent boundary.
MAX_JSONRPC_ID_BYTES = 256
MAX_JSONRPC_ERROR_BYTES = 512
# JSON-RPC permits a numeric id, but values outside the exactly representable
# binary64 range are not interoperable across clients and can be rounded into
# a different retry correlation key.  Keep numeric ids in the same safe range
# used by the native integer protocol.
MAX_JSONRPC_NUMERIC_ID = (1 << 53) - 1
MIN_COMMAND_ID_BYTES = 8
MAX_COMMAND_ID_BYTES = 128
MAX_UINT64 = (1 << 64) - 1
MAX_ORDER_ID = (1 << 63) - 1
VALID_RESULT_STATUSES = {
    "ok", "permission_denied", "invalid_tool", "rejected", "duplicate",
    "uncertain", "error",
}


def _valid_wire_text(value, maximum_bytes=None, allow_empty=True):
    """Validate decoded protocol text without locale/terminal aliases.

    JSON escapes are decoded before this check, so testing Unicode code points
    (rather than only encoded bytes) catches C0/C1 controls and DEL represented
    as ``\\u00xx``.  These characters are rejected on every text field that
    crosses the native Agent boundary; otherwise they can be interpreted as
    framing, log-control, or terminal escape bytes by a downstream consumer.
    """
    if not isinstance(value, str) or (not allow_empty and not value):
        return False
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return False
    if maximum_bytes is not None and len(encoded) > maximum_bytes:
        return False
    return not any(ord(character) < 0x20 or
                   0x7f <= ord(character) <= 0x9f
                   for character in value)
DESCRIPTOR_FIELDS = {
    "name", "description", "required_capability", "effect", "timeout_ms",
    "schema_hash", "input_schema", "result_schema",
}
FIELD_IDS = {
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

# The public Agent contract uses domain names for target-position intents,
# while the compact typed wire reuses the existing quantity/reference_price
# field ids.  Keep this translation explicit and scoped to the two intent
# tools; accepting these aliases on a raw order would make it possible for a
# caller to accidentally cross the authority boundary with a different
# meaning for the same number.
TARGET_INTENT_TO_WIRE = {
    "target_position": "quantity",
    "max_slippage_bps": "reference_price",
}
TARGET_INTENT_TOOLS = frozenset({
    "intent.preview_target_position",
    "intent.apply_target_position",
})

CLIENT_COMMAND_ID_SCHEMA = {
    "type": "string",
    "description": (
        "Stable caller-generated idempotency key. Reuse the exact value when "
        "retrying the same mutation after an uncertain or lost response."
    ),
    "minLength": MIN_COMMAND_ID_BYTES,
    "maxLength": MAX_COMMAND_ID_BYTES,
    "pattern": r"^[A-Za-z0-9._:-]+$",
}

PLACE_COMMAND_ID_SCHEMA = {
    "type": "string",
    "description": (
        "Must exactly equal the Execution-issued command_id returned by the "
        "matching risk.preview_order. Reuse it unchanged when retrying the "
        "same place mutation after an uncertain or lost response."
    ),
    "minLength": MIN_COMMAND_ID_BYTES,
    "maxLength": MAX_COMMAND_ID_BYTES,
    "pattern": r"^[A-Za-z0-9._:-]+$",
}

TARGET_APPLY_COMMAND_ID_SCHEMA = {
    "type": "string",
    "description": (
        "Must exactly equal the Execution-issued mutation_command_id returned "
        "by the matching intent.preview_target_position. Reuse it unchanged "
        "when retrying the same target mutation after an uncertain or lost response."
    ),
    "minLength": MIN_COMMAND_ID_BYTES,
    "maxLength": MAX_COMMAND_ID_BYTES,
    "pattern": r"^[A-Za-z0-9._:-]+$",
}


def _stable_metadata(metadata):
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_uid,
        metadata.st_gid, metadata.st_nlink, metadata.st_size,
        metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def read_session_token(path):
    if not path:
        raise RuntimeError("HEPTA_TOOL_SESSION_TOKEN_FILE is required")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    before = os.lstat(path)
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RuntimeError("session token path must be a regular non-symlink file")
    if before.st_uid not in (0, os.geteuid()):
        raise RuntimeError("session token file has an untrusted owner")
    # Match the native SDK's exact credential-file contract.  Merely checking
    # group/world bits would permit owner-executable or owner-writable files
    # whose contents can be changed by a compromised helper between calls.
    if stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1:
        raise RuntimeError("session token file must have mode 0600 and one link")
    if before.st_size < 1 or before.st_size > MAX_TOKEN_BYTES + 2:
        raise RuntimeError("session token file size is invalid")
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if _stable_metadata(before) != _stable_metadata(opened):
            raise RuntimeError("session token file changed before open")
        data = os.read(descriptor, MAX_TOKEN_BYTES + 3)
        if os.read(descriptor, 1):
            raise RuntimeError("session token file exceeds the size limit")
        after = os.fstat(descriptor)
        if _stable_metadata(opened) != _stable_metadata(after):
            raise RuntimeError("session token file changed while reading")
    finally:
        os.close(descriptor)
    try:
        token = data.decode("utf-8", errors="strict").rstrip("\r\n")
    except UnicodeDecodeError as error:
        raise RuntimeError("session token file is not valid UTF-8") from error
    if not _valid_wire_text(token, MAX_TOKEN_BYTES, allow_empty=False):
        raise RuntimeError("session token is invalid")
    return token


def enforce_expected_uid(value):
    if not value:
        return
    if not value.isascii() or not value.isdecimal() or len(value) > 10:
        raise RuntimeError("HEPTA_TOOL_EXPECTED_UID is invalid")
    expected = int(value, 10)
    if expected < 1 or expected > 4_294_967_295:
        raise RuntimeError("HEPTA_TOOL_EXPECTED_UID is invalid")
    if os.geteuid() != expected:
        raise RuntimeError("MCP bridge effective UID does not match hepta-agent")


def scalar_text(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        if not _valid_wire_text(value):
            raise ValueError("tool arguments must be valid UTF-8")
        if not value:
            raise ValueError("tool arguments must contain non-empty text without NUL")
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("tool arguments must contain finite numbers")
        if value == 0.0 and math.copysign(1.0, value) < 0.0:
            raise ValueError("tool arguments must use canonical zero")
        return str(value)
    raise ValueError("tool arguments must be scalar values")


def validate_command_id(value):
    if not isinstance(value, str):
        raise ValueError("command_id must be a string")
    encoded = value.encode("ascii", errors="strict")
    if len(encoded) < MIN_COMMAND_ID_BYTES or len(encoded) > MAX_COMMAND_ID_BYTES:
        raise ValueError("command_id length is invalid")
    saw_alphanumeric = False
    if any(not (
            48 <= byte <= 57 or 65 <= byte <= 90 or 97 <= byte <= 122 or
            byte in (45, 46, 58, 95)) for byte in encoded):
        raise ValueError("command_id contains an invalid character")
    saw_alphanumeric = any(
        48 <= byte <= 57 or 65 <= byte <= 90 or 97 <= byte <= 122
        for byte in encoded)
    if not saw_alphanumeric:
        raise ValueError("command_id must contain an alphanumeric character")
    return value


def valid_sha256(value):
    return (
        isinstance(value, str) and len(value) == 71 and
        value.startswith("sha256:") and
        all(character in "0123456789abcdef" for character in value[7:])
    )


def valid_tool_name(value):
    """Match the C++ canonical dotted lower-case tool-name grammar."""

    if not isinstance(value, str):
        return False
    try:
        encoded = value.encode("ascii", errors="strict")
    except UnicodeEncodeError:
        return False
    if len(encoded) < 3 or len(encoded) > MAX_TOOL_NAME_BYTES:
        return False
    segments = value.split(".")
    if len(segments) < 2 or any(not segment for segment in segments):
        return False
    for segment in segments:
        if not segment or not ("a" <= segment[0] <= "z"):
            return False
        if any(not (("a" <= character <= "z") or
                    ("0" <= character <= "9") or character == "_")
               for character in segment):
            return False
    return True


def validate_session_token(value):
    """Validate the byte-level token contract before binary encoding."""

    if not isinstance(value, str) or not value:
        raise ValueError("session token must be a non-empty string")
    if not _valid_wire_text(value, MAX_TOKEN_BYTES, allow_empty=False):
        raise ValueError("session token must be valid UTF-8 and contain no controls")
    return value


def _strict_object(pairs):
    """Decode JSON objects without silently accepting duplicate keys."""

    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def _reject_json_constant(value):
    raise ValueError("non-finite JSON constants are not allowed")


def _parse_result_integer(token):
    """Decode a result integer using the native canonical-number grammar."""

    # ``json.loads`` otherwise normalizes ``-0`` to the same Python integer as
    # ``0``.  The native codec rejects that spelling to avoid two wire forms
    # for one value, so reject it while the original lexical token is still
    # available.
    if token == "-0":
        raise ValueError("signed zero is not a canonical result number")
    value = int(token)
    try:
        if not math.isfinite(float(value)):
            raise ValueError("result integer exceeds binary64 range")
    except (OverflowError, ValueError) as error:
        raise ValueError("result integer is not finite") from error
    return value


def _parse_result_float(token):
    """Decode a result float while preserving finite/underflow invariants."""

    try:
        value = float(token)
    except (OverflowError, ValueError) as error:
        raise ValueError("result number is not finite") from error
    if not math.isfinite(value):
        raise ValueError("result number is not finite")
    if value == 0.0:
        if token.startswith("-"):
            raise ValueError("signed zero is not a canonical result number")
        mantissa = token.split("e", 1)[0].split("E", 1)[0]
        if any(character not in "0." for character in mantissa):
            # A non-zero mantissa that rounded to zero is an underflow; the
            # native parser rejects it rather than silently changing value.
            raise ValueError("result number underflowed to zero")
    return value


def _load_strict_json(text, result_numbers=False):
    """Parse one JSON value with duplicate-key and finite-number checks."""

    options = {
        "object_pairs_hook": _strict_object,
        "parse_constant": _reject_json_constant,
    }
    if result_numbers:
        options["parse_int"] = _parse_result_integer
        options["parse_float"] = _parse_result_float
    return json.loads(text, **options)


def _valid_result_payload(value):
    """Validate the decoded result payload against native codec bounds.

    ``TypedToolResultCodec`` parses payload objects recursively even though it
    leaves their lexical JSON unchanged.  Mirror its UTF-8/control, finite
    number, depth, node, and decoded-string limits after Python's strict JSON
    decoder has materialized the value.  An explicit stack keeps this check
    independent of the interpreter recursion limit for hostile documents.
    The envelope root is not counted as a payload node by the native parser;
    child values are counted exactly once.
    """
    stack = [(value, 1, False)]
    nodes = 0
    decoded_string_bytes = 0
    while stack:
        current, depth, count_node = stack.pop()
        if depth > MAX_RESULT_DEPTH:
            return False
        if count_node:
            nodes += 1
            if nodes > MAX_RESULT_NODES:
                return False

        if isinstance(current, str):
            if not _valid_wire_text(current, MAX_RESULT_STRING_BYTES):
                return False
            try:
                decoded_string_bytes += len(current.encode("utf-8"))
            except UnicodeEncodeError:
                return False
            if decoded_string_bytes > MAX_RESULT_STRING_BYTES:
                return False
            continue

        if current is None or isinstance(current, bool):
            continue

        if isinstance(current, int):
            # Python integers have arbitrary precision, while the native
            # parser converts every JSON number through a finite binary64.
            try:
                if not math.isfinite(float(current)):
                    return False
            except (OverflowError, ValueError):
                return False
            continue

        if isinstance(current, float):
            if (not math.isfinite(current) or
                    (current == 0.0 and
                     math.copysign(1.0, current) < 0.0)):
                return False
            continue

        if isinstance(current, dict):
            for key, child in current.items():
                if not _valid_wire_text(key, MAX_RESULT_STRING_BYTES):
                    return False
                try:
                    decoded_string_bytes += len(key.encode("utf-8"))
                except UnicodeEncodeError:
                    return False
                if decoded_string_bytes > MAX_RESULT_STRING_BYTES:
                    return False
                stack.append((child, depth + 1, True))
            continue

        if isinstance(current, list):
            for child in current:
                stack.append((child, depth + 1, True))
            continue

        # ``json.loads`` should only produce the types handled above.  Keep a
        # defensive default so future decoder hooks cannot widen the wire
        # contract accidentally.
        return False
    return True


def _valid_jsonrpc_id(value):
    """JSON-RPC ids are scalar strings/numbers or null, never booleans."""

    if value is None:
        return True
    if isinstance(value, str):
        return _valid_wire_text(value, MAX_JSONRPC_ID_BYTES)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, int):
        return abs(value) <= MAX_JSONRPC_NUMERIC_ID
    return math.isfinite(value) and abs(value) <= MAX_JSONRPC_NUMERIC_ID


def canonical_schema(value):
    try:
        return json.dumps(
            value, ensure_ascii=True, separators=(",", ":"),
            allow_nan=False)
    except (TypeError, ValueError) as error:
        raise RuntimeError("tool discovery returned an invalid JSON schema") from error


def normalize_wire_arguments(tool_name, arguments):
    """Return a detached argument mapping with documented aliases resolved.

    Alias handling happens before schema validation as well as before binary
    encoding, so direct callers of ``encode_request`` and normal MCP calls
    observe identical behavior.  Supplying both spellings is rejected rather
    than silently choosing one value.
    """
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be a JSON object")
    normalized = dict(arguments)
    if tool_name not in TARGET_INTENT_TOOLS:
        # These two public names are intentionally aliases for the compact
        # field ids used by the native control protocol. Resolve them here so
        # direct ``encode_request`` callers cannot accidentally emit two
        # values for one binary field (the normal MCP path uses the same
        # function).
        if tool_name == "system.tools.describe" and "tool_name" in normalized:
            if "target_tool_name" in normalized:
                raise ValueError(
                    "tool arguments contain duplicate wire fields")
            normalized["target_tool_name"] = normalized.pop("tool_name")
        elif tool_name == "system.cancel_request" and "tool_call_id" in normalized:
            if "cancel_tool_call_id" in normalized:
                raise ValueError(
                    "tool arguments contain duplicate wire fields")
            normalized["cancel_tool_call_id"] = normalized.pop("tool_call_id")
        return normalized
    for alias, wire_key in TARGET_INTENT_TO_WIRE.items():
        if alias not in normalized:
            continue
        if wire_key in normalized:
            raise ValueError(
                "tool arguments contain both alias and wire field: " +
                alias + "/" + wire_key)
        normalized[wire_key] = normalized.pop(alias)
    return normalized


def valid_instrument(value):
    """Match the C++ bounded ASCII instrument-key grammar."""

    if not isinstance(value, str):
        return False
    try:
        encoded = value.encode("ascii", errors="strict")
    except UnicodeEncodeError:
        return False
    if not encoded or len(encoded) > 128:
        return False
    previous_separator = False
    saw_alphanumeric = False
    separators = frozenset(".-_/:" )
    for index, character in enumerate(value):
        alphanumeric = (
            "a" <= character <= "z" or "A" <= character <= "Z" or
            "0" <= character <= "9")
        if alphanumeric:
            saw_alphanumeric = True
            previous_separator = False
            continue
        if (character not in separators or previous_separator or
                index == 0 or index + 1 == len(value)):
            return False
        previous_separator = True
    return saw_alphanumeric and not previous_separator


def _argument_number(value, field, integer=False, minimum=None, maximum=None):
    if integer:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(field + " must be an integer")
        number = value
    else:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(field + " must be a number")
        try:
            number = float(value)
        except (OverflowError, ValueError):
            raise ValueError(field + " must be finite")
        if not math.isfinite(number):
            raise ValueError(field + " must be finite")
        if number == 0.0 and math.copysign(1.0, number) < 0.0:
            raise ValueError(field + " must use canonical zero")
    if minimum is not None and number < minimum:
        raise ValueError(field + " is below its minimum")
    if maximum is not None and number > maximum:
        raise ValueError(field + " exceeds its maximum")
    return number


def _validate_direct_arguments(tool_name, arguments):
    """Apply the same shape/value boundary as C++ EncodeRequest.

    ``NativeToolGateway.call`` normally validates against the discovered JSON
    schema first, but ``encode_request`` is public and is also used directly
    by tests and embedded clients. Keeping this check here prevents a direct
    caller from constructing a wire-valid field set with the wrong tool
    meaning or an out-of-range numeric value.
    """
    known = {
        "market.get_quote", "watch.get_snapshot", "risk.preview_flatten",
        "decision.get_snapshot", "account.get_summary",
        "portfolio.list_positions", "orders.list", "risk.get_limits",
        "system.get_health", "system.tools.list", "system.tools.describe",
        "events.wait", "intent.preview_target_position",
        "intent.apply_target_position", "trade.cancel_order",
        "trade.flatten_position", "trade.place_order", "risk.preview_order",
        "execution.get_command_status", "system.cancel_request",
    }
    if tool_name not in known:
        if arguments:
            raise ValueError("invalid or unknown tool argument: " +
                             sorted(arguments)[0])
        return

    shapes = {
        "market.get_quote": ({"instrument"}, {"instrument"}),
        "watch.get_snapshot": ({"instrument"}, {"instrument"}),
        "risk.preview_flatten": ({"instrument"}, {"instrument"}),
        "decision.get_snapshot": ({"instrument"}, {"instrument"}),
        "account.get_summary": (set(), set()),
        "portfolio.list_positions": (set(), set()),
        "orders.list": (set(), set()),
        "risk.get_limits": (set(), set()),
        "system.get_health": (set(), set()),
        "system.tools.list": (set(), set()),
        "system.tools.describe": ({"target_tool_name"}, {"target_tool_name"}),
        "events.wait": ({"after_sequence", "timeout_ms"}, set()),
        "execution.get_command_status": ({"command_id"}, {"command_id"}),
        "system.cancel_request": ({"cancel_tool_call_id"},
                                    {"cancel_tool_call_id"}),
        "trade.cancel_order": ({"order_id"}, {"order_id"}),
        "trade.flatten_position": (
            {"instrument", "preview_permit"}, {"instrument", "preview_permit"}),
        "intent.preview_target_position": (
            {"instrument", "quantity", "reference_price", "expires_at_ms"},
            {"instrument", "quantity", "reference_price", "expires_at_ms"}),
        "intent.apply_target_position": (
            {"instrument", "quantity", "reference_price", "expires_at_ms",
             "preview_permit"},
            {"instrument", "quantity", "reference_price", "expires_at_ms",
             "preview_permit"}),
        "trade.place_order": (
            {"instrument", "side", "quantity", "order_type", "tif",
             "expires_at_ms", "preview_permit", "limit_price",
             "reference_price", "symbol", "currency", "sec_type", "exchange"},
            {"instrument", "side", "quantity", "order_type", "tif",
             "expires_at_ms", "preview_permit"}),
        "risk.preview_order": (
            {"instrument", "side", "quantity", "order_type", "tif",
             "expires_at_ms", "limit_price", "reference_price", "symbol",
             "currency", "sec_type", "exchange"},
            {"instrument", "side", "quantity", "order_type", "tif",
             "expires_at_ms"}),
    }
    allowed, required = shapes[tool_name]
    unknown = sorted(set(arguments).difference(allowed))
    if unknown:
        raise ValueError("invalid or unknown tool argument: " + unknown[0])
    missing = sorted(required.difference(arguments))
    if missing:
        raise ValueError("required tool argument is missing: " + missing[0])

    if "instrument" in arguments and not valid_instrument(arguments["instrument"]):
        raise ValueError("instrument is not a canonical instrument")
    if "target_tool_name" in arguments and not valid_tool_name(
            arguments["target_tool_name"]):
        raise ValueError("target_tool_name is not a canonical tool name")
    for field in ("symbol", "currency", "sec_type", "exchange", "side",
                  "order_type", "tif"):
        if field in arguments:
            value = arguments[field]
            if (not isinstance(value, str) or not value or "\x00" in value or
                    len(value.encode("utf-8", errors="strict")) > 128):
                raise ValueError(field + " must be non-empty text")

    if tool_name == "events.wait":
        if "after_sequence" in arguments:
            _argument_number(arguments["after_sequence"], "after_sequence",
                             integer=True, minimum=0, maximum=MAX_UINT64)
        if "timeout_ms" in arguments:
            _argument_number(arguments["timeout_ms"], "timeout_ms",
                             integer=True, minimum=0, maximum=30000)
    elif tool_name == "execution.get_command_status":
        validate_command_id(arguments["command_id"])
    elif tool_name == "system.cancel_request":
        validate_command_id(arguments["cancel_tool_call_id"])
    elif tool_name == "trade.cancel_order":
        _argument_number(arguments["order_id"], "order_id", integer=True,
                         minimum=0, maximum=MAX_ORDER_ID)
    elif tool_name in {"intent.preview_target_position",
                       "intent.apply_target_position"}:
        _argument_number(arguments["quantity"], "quantity")
        _argument_number(arguments["reference_price"], "reference_price",
                         minimum=0.0, maximum=1000.0)
        _argument_number(arguments["expires_at_ms"], "expires_at_ms",
                         integer=True, minimum=1, maximum=MAX_ORDER_ID)
        if tool_name == "intent.apply_target_position" and not valid_sha256(
                arguments["preview_permit"]):
            raise ValueError("preview_permit is not a canonical sha256 digest")
    elif tool_name == "trade.flatten_position":
        if not valid_sha256(arguments["preview_permit"]):
            raise ValueError("preview_permit is not a canonical sha256 digest")
    elif tool_name in {"trade.place_order", "risk.preview_order"}:
        if arguments["side"] not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        _argument_number(arguments["quantity"], "quantity", minimum=0.0)
        if float(arguments["quantity"]) <= 0.0:
            raise ValueError("quantity must be greater than zero")
        if arguments["order_type"] not in {"MKT", "LMT"}:
            raise ValueError("order_type must be MKT or LMT")
        if arguments["tif"] != "DAY":
            raise ValueError("tif must be DAY")
        if arguments["order_type"] == "LMT":
            if "limit_price" not in arguments:
                raise ValueError("limit_price is required for LMT")
            _argument_number(arguments["limit_price"], "limit_price",
                             minimum=0.0)
            if float(arguments["limit_price"]) <= 0.0:
                raise ValueError("limit_price must be greater than zero")
        elif "limit_price" in arguments:
            raise ValueError("MKT must not include limit_price")
        if "reference_price" in arguments:
            _argument_number(arguments["reference_price"], "reference_price",
                             minimum=0.0)
        _argument_number(arguments["expires_at_ms"], "expires_at_ms",
                         integer=True, minimum=1, maximum=MAX_ORDER_ID)
        if tool_name == "trade.place_order" and not valid_sha256(
                arguments["preview_permit"]):
            raise ValueError("preview_permit is not a canonical sha256 digest")


def descriptor_schema_hash(descriptor):
    if not isinstance(descriptor, dict) or set(descriptor) != DESCRIPTOR_FIELDS:
        raise RuntimeError("tool discovery returned an invalid descriptor")
    name = descriptor["name"]
    description = descriptor["description"]
    capability = descriptor["required_capability"]
    effect = descriptor["effect"]
    timeout_ms = descriptor["timeout_ms"]
    input_schema = descriptor["input_schema"]
    result_schema = descriptor["result_schema"]
    if (not valid_tool_name(name) or
            not isinstance(description, str) or
            not isinstance(capability, str) or not capability or
            not isinstance(effect, str) or effect not in {"read", "trade"} or
            isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or
            timeout_ms < 1 or timeout_ms > 120_000 or
            not isinstance(input_schema, dict) or
            not isinstance(result_schema, dict)):
        raise RuntimeError("tool discovery descriptor fields are invalid")
    # Discovery text is copied into MCP responses and may be rendered/logged
    # by an untrusted client.  Validate the decoded Unicode scalar stream as
    # well as its encoded byte length; a native descriptor containing C0/C1
    # controls or a lone surrogate must fail closed before schema hashing.
    if (not _valid_wire_text(description, MAX_DETAIL_BYTES) or
            not _valid_wire_text(capability, MAX_REASON_CODE_BYTES,
                                 allow_empty=False)):
        raise RuntimeError("tool discovery descriptor fields are invalid")
    input_schema_text = canonical_schema(input_schema)
    result_schema_text = canonical_schema(result_schema)
    canonical = "\0".join((
        name, description, capability, effect, str(timeout_ms),
        input_schema_text, result_schema_text,
    )).encode("utf-8")
    if len(canonical) > MAX_MESSAGE_BYTES:
        raise RuntimeError("tool discovery schema exceeds adapter limit")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def catalog_schema_hash(descriptors):
    canonical = "".join(
        descriptor["name"] + "=" + descriptor["schema_hash"] + "\n"
        for descriptor in sorted(descriptors, key=lambda item: item["name"])
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def encode_request(token, tool_name, tool_call_id, arguments, schema_hash):
    validate_session_token(token)
    if not valid_tool_name(tool_name):
        raise ValueError("tool_name is not a canonical tool name")
    validate_command_id(tool_call_id)
    if schema_hash and not valid_sha256(schema_hash):
        raise ValueError("schema_hash is not a canonical sha256 digest")
    arguments = normalize_wire_arguments(tool_name, arguments)
    if any(not isinstance(key, str) for key in arguments):
        raise ValueError("tool argument keys must be strings")
    _validate_direct_arguments(tool_name, arguments)
    fields = {
        FIELD_IDS["session_token"]: token,
        FIELD_IDS["tool_call_id"]: tool_call_id,
        FIELD_IDS["tool_name"]: tool_name,
        FIELD_IDS["protocol_min_version"]: str(PROTOCOL_VERSION),
        FIELD_IDS["protocol_max_version"]: str(PROTOCOL_VERSION),
    }
    if schema_hash:
        fields[FIELD_IDS["expected_schema_hash"]] = schema_hash
    emitted_argument_fields = set()
    for key, value in arguments.items():
        wire_key = key
        if tool_name == "system.tools.describe" and key == "tool_name":
            wire_key = "target_tool_name"
        elif tool_name == "system.cancel_request" and key == "tool_call_id":
            wire_key = "cancel_tool_call_id"
        field_id = FIELD_IDS.get(wire_key)
        if field_id is None or wire_key in {
                "session_token", "tool_call_id", "tool_name", "protocol_min_version",
                "protocol_max_version", "expected_schema_hash"}:
            raise ValueError("invalid or unknown tool argument: " + key)
        if field_id in emitted_argument_fields:
            raise ValueError("tool arguments contain duplicate wire fields")
        emitted_argument_fields.add(field_id)
        fields[field_id] = scalar_text(value)
    body = bytearray(b"HTT1")
    for field_id in sorted(fields):
        encoded = fields[field_id].encode("utf-8")
        if not encoded or b"\x00" in encoded:
            raise ValueError("tool request fields must be non-empty and NUL-free")
        if len(encoded) > 32_768:
            raise ValueError("tool argument exceeds protocol field limit")
        body.extend(struct.pack("!HI", field_id, len(encoded)))
        body.extend(encoded)
    if len(body) > MAX_REQUEST_BYTES:
        raise ValueError("tool request exceeds protocol limit")
    return bytes(body)


def recv_exact(connection, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise RuntimeError("tool gateway closed the response early")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def validate_envelope(body):
    if (not isinstance(body, (bytes, bytearray)) or not body or
            len(body) > MAX_MESSAGE_BYTES):
        raise RuntimeError("invalid tool gateway response size")

    try:
        envelope = _load_strict_json(
            bytes(body).decode("utf-8", errors="strict"),
            result_numbers=True)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError,
            RecursionError) as error:
        raise RuntimeError("invalid tool gateway response JSON") from error
    if not isinstance(envelope, dict):
        raise RuntimeError("invalid tool gateway response envelope")
    required = {"status", "tool", "reason_code", "detail", "order_id", "payload"}
    if set(envelope) != required:
        missing = required.difference(envelope)
        if missing:
            raise RuntimeError("tool gateway response misses " + sorted(missing)[0])
        raise RuntimeError("tool gateway response has an unexpected field")
    if envelope["status"] not in VALID_RESULT_STATUSES:
        raise RuntimeError("tool gateway returned an unknown status")
    if not all(isinstance(envelope[key], str) for key in ("status", "tool", "reason_code", "detail")):
        raise RuntimeError("tool gateway response has invalid string fields")
    if (not valid_tool_name(envelope["tool"]) or
            not _valid_wire_text(envelope["status"], 32, allow_empty=False) or
            not _valid_wire_text(envelope["tool"], MAX_TOOL_NAME_BYTES,
                                 allow_empty=False) or
            not _valid_wire_text(envelope["reason_code"],
                                 MAX_REASON_CODE_BYTES) or
            not _valid_wire_text(envelope["detail"], MAX_DETAIL_BYTES)):
        raise RuntimeError("tool gateway response string field exceeds its limit")
    if (not isinstance(envelope["order_id"], int) or
            isinstance(envelope["order_id"], bool)):
        raise RuntimeError("tool gateway response has invalid order_id")
    if envelope["order_id"] < -1 or envelope["order_id"] > MAX_ORDER_ID:
        raise RuntimeError("tool gateway response has invalid order_id")
    if envelope["payload"] is not None and not isinstance(envelope["payload"], dict):
        raise RuntimeError("tool gateway response payload must be an object or null")
    if (envelope["payload"] is not None and
            not _valid_result_payload(envelope["payload"])):
        raise RuntimeError("tool gateway response payload exceeds its limits")
    return envelope


class NativeToolGateway:
    def __init__(self):
        enforce_expected_uid(os.environ.get("HEPTA_TOOL_EXPECTED_UID", ""))
        self.socket_path = os.environ.get("HEPTA_TOOL_SOCKET", "")
        self.token_file = os.environ.get("HEPTA_TOOL_SESSION_TOKEN_FILE", "")
        self.timeout_seconds = max(1, min(120, int(os.environ.get("HEPTA_MCP_TIMEOUT_SEC", "35"))))
        self.descriptors = {}
        self.catalog_hash = ""

    def _call_native(self, tool_name, arguments, schema_hash="",
                     tool_call_id=None):
        if not self.socket_path:
            raise RuntimeError("HEPTA_TOOL_SOCKET is required")
        if not os.path.isabs(self.socket_path):
            raise RuntimeError("HEPTA_TOOL_SOCKET must be an absolute path")
        if len(os.fsencode(self.socket_path)) > MAX_UNIX_PATH_BYTES:
            raise RuntimeError("HEPTA_TOOL_SOCKET exceeds the AF_UNIX path limit")
        token = read_session_token(self.token_file)
        if tool_call_id is None:
            tool_call_id = "mcp-" + uuid.uuid4().hex
        else:
            validate_command_id(tool_call_id)
        body = encode_request(
            token, tool_name, tool_call_id, arguments, schema_hash)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(self.timeout_seconds)
            connection.connect(self.socket_path)
            connection.sendall(struct.pack("!I", len(body)) + body)
            response_size = struct.unpack("!I", recv_exact(connection, 4))[0]
            if response_size < 1 or response_size > MAX_MESSAGE_BYTES:
                raise RuntimeError("tool gateway response exceeds adapter limit")
            response = recv_exact(connection, response_size)
        envelope = validate_envelope(response)
        if envelope["tool"] != tool_name:
            raise RuntimeError("tool gateway response tool mismatch")
        return envelope

    def discover(self):
        envelope = self._call_native("system.tools.list", {})
        if envelope["status"] != "ok":
            # The native detail is authority-controlled and may contain
            # adapter/venue diagnostics.  Discovery failure is an internal
            # bridge condition; do not copy the full envelope into an error
            # that can be returned to an MCP peer.
            raise RuntimeError("tool discovery failed")
        payload = envelope.get("payload") or {}
        if not isinstance(payload, dict) or payload.get("protocol") != PROTOCOL_NAME:
            raise RuntimeError("unexpected tool protocol")
        def strict_int(value, field):
            if isinstance(value, bool) or not isinstance(value, int):
                raise RuntimeError("tool discovery " + field + " is invalid")
            return value

        minimum = strict_int(
            payload.get("protocol_min_version", payload.get("protocol_version", 0)),
            "protocol_min_version")
        maximum = strict_int(
            payload.get("protocol_max_version", payload.get("protocol_version", 0)),
            "protocol_max_version")
        selected = strict_int(payload.get("protocol_version", 0),
                              "protocol_version")
        if (minimum != PROTOCOL_VERSION or maximum != PROTOCOL_VERSION or
                selected != PROTOCOL_VERSION):
            raise RuntimeError("unsupported HeptaTrader protocol version")
        schema_version = payload.get("schema_version")
        if (isinstance(schema_version, bool) or
                not isinstance(schema_version, int) or
                schema_version != DISCOVERY_SCHEMA_VERSION):
            raise RuntimeError("unsupported HeptaTrader discovery schema version")
        advertised_catalog_hash = payload.get("catalog_schema_hash")
        if not valid_sha256(advertised_catalog_hash):
            raise RuntimeError("tool discovery catalog schema hash is invalid")
        expected_payload_fields = {
            "protocol", "protocol_version", "protocol_min_version",
            "protocol_max_version", "schema_version", "catalog_schema_hash",
            "tools",
        }
        if set(payload) != expected_payload_fields:
            raise RuntimeError("tool discovery payload has unexpected fields")
        tools = payload.get("tools")
        if not isinstance(tools, list) or not tools:
            raise RuntimeError("tool discovery tools must be an array")
        descriptors = {}
        for descriptor in tools:
            expected_schema_hash = descriptor_schema_hash(descriptor)
            schema_hash = descriptor["schema_hash"]
            if not valid_sha256(schema_hash) or schema_hash != expected_schema_hash:
                raise RuntimeError(
                    "tool discovery descriptor schema hash mismatch")
            name = descriptor["name"]
            if name in descriptors:
                raise RuntimeError("tool discovery returned a duplicate tool")
            descriptors[name] = descriptor
        if catalog_schema_hash(list(descriptors.values())) != advertised_catalog_hash:
            raise RuntimeError("tool discovery catalog schema hash mismatch")
        if self.catalog_hash and self.catalog_hash != advertised_catalog_hash:
            raise RuntimeError("tool discovery catalog changed during MCP session")
        self.catalog_hash = advertised_catalog_hash
        self.descriptors = descriptors
        return payload

    def mcp_tools(self):
        payload = self.discover()
        tools = []
        for descriptor in payload.get("tools", []):
            effect = descriptor.get("effect", "read")
            input_schema = copy.deepcopy(
                descriptor.get("input_schema", {"type": "object"}))
            if effect == "trade":
                properties = input_schema.setdefault("properties", {})
                if "command_id" in properties:
                    raise RuntimeError(
                        "native tool schema reserves adapter command_id")
                command_schema = (
                    PLACE_COMMAND_ID_SCHEMA
                    if descriptor.get("name") == "trade.place_order"
                    else (TARGET_APPLY_COMMAND_ID_SCHEMA
                          if descriptor.get("name") == "intent.apply_target_position"
                          else CLIENT_COMMAND_ID_SCHEMA))
                properties["command_id"] = copy.deepcopy(command_schema)
                required = list(input_schema.get("required") or [])
                if "command_id" not in required:
                    required.append("command_id")
                input_schema["required"] = required
            tools.append({
                "name": descriptor["name"],
                "description": descriptor.get("description", ""),
                "inputSchema": input_schema,
                "annotations": {
                    "readOnlyHint": effect == "read",
                    "destructiveHint": effect == "trade",
                    "idempotentHint": effect == "trade",
                    "openWorldHint": effect == "trade",
                },
            })
        return tools

    def call(self, name, arguments):
        if name not in self.descriptors:
            self.discover()
        descriptor = self.descriptors.get(name)
        if descriptor is None:
            raise ValueError("tool is not visible to this session: " + name)
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be a JSON object")

        # Validate against the public MCP schema before translating compact
        # wire aliases.  The target-position aliases intentionally mirror the
        # descriptor's quantity/reference fields, so they are normalized for
        # schema lookup.  Control-plane aliases (tool_name/tool_call_id), on
        # the other hand, are the names published by the descriptor and must
        # remain in that spelling until the wire encoder runs; normalizing
        # them first would make a valid MCP call look like an unknown schema
        # property.
        schema_arguments = dict(arguments)
        if name in TARGET_INTENT_TOOLS:
            schema_arguments = normalize_wire_arguments(name, schema_arguments)
        native_arguments = normalize_wire_arguments(name, arguments)
        command_id = None
        if descriptor.get("effect", "read") == "trade":
            if "command_id" not in native_arguments:
                raise ValueError("required tool argument is missing: command_id")
            command_id = validate_command_id(
                native_arguments.pop("command_id"))
            schema_arguments.pop("command_id", None)
        schema = descriptor.get("input_schema") or {}
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        if any(key not in schema_arguments for key in required):
            raise ValueError("required tool argument is missing")
        if schema.get("additionalProperties") is False and any(
                key not in properties for key in schema_arguments):
            raise ValueError("tool arguments contain an unknown property")
        return self._call_native(
            name, native_arguments, descriptor["schema_hash"], command_id)


def success(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _bounded_error_message(message, fallback="internal adapter error"):
    """Return a safe, bounded JSON-RPC error message.

    Exception strings are not a wire contract: they can contain filesystem
    paths, socket addresses, credentials, arbitrary control bytes, or an
    attacker-controlled megabyte-sized field name.  Callers should pass only
    deliberately selected user-facing text; malformed/internal values are
    collapsed to a stable fallback and capped by UTF-8 bytes.
    """
    if not isinstance(message, str):
        return fallback
    if not _valid_wire_text(message, MAX_JSONRPC_ERROR_BYTES,
                            allow_empty=False):
        return fallback
    encoded = message.encode("utf-8")
    return encoded.decode("utf-8")


def failure(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code,
                       "message": _bounded_error_message(message)}}


def bounded_stdin_messages(stream, limit):
    """Yield one bounded raw JSON message, draining oversized lines exactly once."""
    while True:
        first = stream.readline(limit + 2)
        if not first:
            return
        if len(first) > limit or not first.endswith(b"\n"):
            complete = first.endswith(b"\n")
            while not complete:
                chunk = stream.readline(limit + 2)
                if not chunk:
                    complete = True
                elif chunk.endswith(b"\n"):
                    complete = True
            yield None
            continue
        yield first[:-1]


def handle(gateway, request):
    if not isinstance(request, dict):
        raise ValueError("request must be a JSON object")
    # Reject malformed JSON-RPC envelopes before dispatch.  In particular,
    # never echo an object/boolean/NaN id or let a non-object params value
    # reach the method handlers and trigger an AttributeError.
    if request.get("jsonrpc") != "2.0":
        return failure(None, -32600, "invalid JSON-RPC request")
    request_id = request.get("id")
    if "id" in request and not _valid_jsonrpc_id(request_id):
        return failure(None, -32600, "invalid JSON-RPC id")
    method = request.get("method")
    if (not isinstance(method, str) or
            not _valid_wire_text(method, 128, allow_empty=False)):
        return failure(request_id, -32600, "method must be a string")
    params = request.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return failure(request_id, -32602, "method params must be an object")
    if method == "initialize":
        requested_protocol = params.get("protocolVersion", "2025-03-26")
        if (not _valid_wire_text(requested_protocol, 128,
                                 allow_empty=False)):
            return failure(request_id, -32602, "invalid protocolVersion")
        return success(request_id, {
            "protocolVersion": requested_protocol,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "heptatrader", "version": "1"},
        })
    if method == "ping":
        return success(request_id, {})
    if method == "tools/list":
        try:
            tools = gateway.mcp_tools()
        except Exception:
            # Discovery/descriptor diagnostics are native implementation
            # details.  Keep direct callers of ``handle`` on the same safe
            # boundary as the stdin loop in ``main``.
            return failure(request_id, -32603, "internal adapter error")
        if not isinstance(tools, list):
            return failure(request_id, -32603, "internal adapter error")
        return success(request_id, {"tools": tools})
    if method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments", {})
        if arguments is None:
            arguments = {}
        if (not isinstance(name, str) or
                not _valid_wire_text(name, MAX_TOOL_NAME_BYTES,
                                     allow_empty=False) or
                not valid_tool_name(name) or not isinstance(arguments, dict)):
            return failure(request_id, -32602,
                           "tools/call requires a string name and object arguments")
        try:
            envelope = gateway.call(name, arguments)
        except ValueError:
            # Argument/schema failures are client errors. Keep malformed tool
            # input from escaping to ``main`` as an internal (-32603) error.
            return failure(request_id, -32602, "invalid tool arguments")
        except Exception:
            return failure(request_id, -32603, "internal adapter error")
        try:
            text = json.dumps(
                envelope, separators=(",", ":"), sort_keys=True,
                allow_nan=False)
        except (TypeError, ValueError, OverflowError):
            return failure(request_id, -32603, "internal adapter error")
        return success(request_id, {
            "content": [{"type": "text", "text": text}],
            "structuredContent": envelope,
            # A durable accepted replay is the successful exactly-once
            # outcome of a stable mutation retry, not an MCP transport/tool
            # failure. Preserve the duplicate status for auditability.
            "isError": envelope["status"] not in ("ok", "duplicate"),
        })
    if method and method.startswith("notifications/"):
        return None
    return failure(request_id, -32601, "method not found")


def main():
    try:
        gateway = NativeToolGateway()
    except (ValueError, RuntimeError):
        # Startup diagnostics stay local and deliberately omit exception text;
        # the same process may be supervised with stderr routed to an Agent.
        sys.stderr.write("hepta-mcp-server: startup failed\n")
        return 78
    for raw_message in bounded_stdin_messages(
            sys.stdin.buffer, MAX_MESSAGE_BYTES):
        request_id = None
        if raw_message is None:
            response = failure(None, -32600, "request exceeds adapter limit")
        else:
            try:
                line = raw_message.decode("utf-8", errors="strict")
                request = _load_strict_json(line)
                if not isinstance(request, dict):
                    raise ValueError("request must be a JSON object")
                if "id" in request and _valid_jsonrpc_id(request.get("id")):
                    request_id = request.get("id")
                response = handle(gateway, request)
            except (
                    KeyError, TypeError, UnicodeDecodeError, ValueError,
                    RuntimeError, OSError, AttributeError, RecursionError):
                # Internal/native exceptions must never cross the MCP wire;
                # their text may expose paths, socket state, or credentials.
                response = failure(request_id, -32603,
                                   "internal adapter error")
        if response is not None:
            sys.stdout.write(json.dumps(
                response, separators=(",", ":"), allow_nan=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
