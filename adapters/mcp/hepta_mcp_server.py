#!/usr/bin/python3

import copy
import hashlib
import json
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
MIN_COMMAND_ID_BYTES = 8
MAX_COMMAND_ID_BYTES = 128
VALID_RESULT_STATUSES = {
    "ok", "permission_denied", "invalid_tool", "rejected", "duplicate",
    "uncertain", "error",
}
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


def _stable_metadata(metadata):
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_uid,
        metadata.st_size, metadata.st_mtime_ns,
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
    if stat.S_IMODE(before.st_mode) & 0o077:
        raise RuntimeError("session token file must not be accessible by group or world")
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
    if not token or len(token.encode("utf-8")) > MAX_TOKEN_BYTES or "\x00" in token:
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
    if isinstance(value, (str, int, float)) and not isinstance(value, complex):
        return str(value)
    raise ValueError("tool arguments must be scalar values")


def validate_command_id(value):
    if not isinstance(value, str):
        raise ValueError("command_id must be a string")
    encoded = value.encode("ascii", errors="strict")
    if len(encoded) < MIN_COMMAND_ID_BYTES or len(encoded) > MAX_COMMAND_ID_BYTES:
        raise ValueError("command_id length is invalid")
    if any(not (
            48 <= byte <= 57 or 65 <= byte <= 90 or 97 <= byte <= 122 or
            byte in (45, 46, 58, 95)) for byte in encoded):
        raise ValueError("command_id contains an invalid character")
    return value


def valid_sha256(value):
    return (
        isinstance(value, str) and len(value) == 71 and
        value.startswith("sha256:") and
        all(character in "0123456789abcdef" for character in value[7:])
    )


def canonical_schema(value):
    try:
        return json.dumps(
            value, ensure_ascii=True, separators=(",", ":"),
            allow_nan=False)
    except (TypeError, ValueError) as error:
        raise RuntimeError("tool discovery returned an invalid JSON schema") from error


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
    if (
            not isinstance(name, str) or not name or len(name) > 64 or
            not isinstance(description, str) or
            not isinstance(capability, str) or not capability or
            effect not in {"read", "trade"} or
            isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or
            timeout_ms < 1 or timeout_ms > 120_000 or
            not isinstance(input_schema, dict) or
            not isinstance(result_schema, dict)):
        raise RuntimeError("tool discovery descriptor fields are invalid")
    canonical = "\0".join((
        name, description, capability, effect, str(timeout_ms),
        canonical_schema(input_schema), canonical_schema(result_schema),
    )).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def catalog_schema_hash(descriptors):
    canonical = "".join(
        descriptor["name"] + "=" + descriptor["schema_hash"] + "\n"
        for descriptor in sorted(descriptors, key=lambda item: item["name"])
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def encode_request(token, tool_name, tool_call_id, arguments, schema_hash):
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be a JSON object")
    fields = {
        FIELD_IDS["session_token"]: token,
        FIELD_IDS["tool_call_id"]: tool_call_id,
        FIELD_IDS["tool_name"]: tool_name,
        FIELD_IDS["protocol_min_version"]: str(PROTOCOL_VERSION),
        FIELD_IDS["protocol_max_version"]: str(PROTOCOL_VERSION),
    }
    if schema_hash:
        fields[FIELD_IDS["expected_schema_hash"]] = schema_hash
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
        fields[field_id] = scalar_text(value)
    body = bytearray(b"HTT1")
    for field_id in sorted(fields):
        encoded = fields[field_id].encode("utf-8")
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
    try:
        envelope = json.loads(body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("invalid tool gateway response JSON") from error
    if not isinstance(envelope, dict):
        raise RuntimeError("invalid tool gateway response envelope")
    for key in ("status", "tool", "reason_code", "detail", "order_id", "payload"):
        if key not in envelope:
            raise RuntimeError("tool gateway response misses " + key)
    if envelope["status"] not in VALID_RESULT_STATUSES:
        raise RuntimeError("tool gateway returned an unknown status")
    if not all(isinstance(envelope[key], str) for key in ("status", "tool", "reason_code", "detail")):
        raise RuntimeError("tool gateway response has invalid string fields")
    if not isinstance(envelope["order_id"], int) or isinstance(envelope["order_id"], bool):
        raise RuntimeError("tool gateway response has invalid order_id")
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
            raise RuntimeError("tool discovery failed: " + json.dumps(envelope, separators=(",", ":")))
        payload = envelope.get("payload") or {}
        if not isinstance(payload, dict) or payload.get("protocol") != PROTOCOL_NAME:
            raise RuntimeError("unexpected tool protocol")
        minimum = int(payload.get("protocol_min_version", payload.get("protocol_version", 0)))
        maximum = int(payload.get("protocol_max_version", payload.get("protocol_version", 0)))
        selected = int(payload.get("protocol_version", 0))
        if not minimum <= PROTOCOL_VERSION <= maximum or selected != PROTOCOL_VERSION:
            raise RuntimeError("unsupported HeptaTrader protocol version")
        schema_version = payload.get("schema_version")
        if (isinstance(schema_version, bool) or
                not isinstance(schema_version, int) or
                schema_version != DISCOVERY_SCHEMA_VERSION):
            raise RuntimeError("unsupported HeptaTrader discovery schema version")
        advertised_catalog_hash = payload.get("catalog_schema_hash")
        if not valid_sha256(advertised_catalog_hash):
            raise RuntimeError("tool discovery catalog schema hash is invalid")
        tools = payload.get("tools")
        if not isinstance(tools, list):
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
                    else CLIENT_COMMAND_ID_SCHEMA)
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
        native_arguments = dict(arguments)
        command_id = None
        if descriptor.get("effect", "read") == "trade":
            if "command_id" not in native_arguments:
                raise ValueError("required tool argument is missing: command_id")
            command_id = validate_command_id(
                native_arguments.pop("command_id"))
        schema = descriptor.get("input_schema") or {}
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        if any(key not in native_arguments for key in required):
            raise ValueError("required tool argument is missing")
        if schema.get("additionalProperties") is False and any(
                key not in properties for key in native_arguments):
            raise ValueError("tool arguments contain an unknown property")
        return self._call_native(
            name, native_arguments, descriptor["schema_hash"], command_id)


def success(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def failure(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


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
    request_id = request.get("id")
    method = request.get("method")
    if method == "initialize":
        return success(request_id, {
            "protocolVersion": request.get("params", {}).get("protocolVersion", "2025-03-26"),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "heptatrader", "version": "1"},
        })
    if method == "ping":
        return success(request_id, {})
    if method == "tools/list":
        return success(request_id, {"tools": gateway.mcp_tools()})
    if method == "tools/call":
        params = request.get("params") or {}
        envelope = gateway.call(params.get("name", ""), params.get("arguments") or {})
        text = json.dumps(envelope, separators=(",", ":"), sort_keys=True)
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
    except (ValueError, RuntimeError) as error:
        sys.stderr.write("hepta-mcp-server: " + str(error) + "\n")
        return 78
    for raw_message in bounded_stdin_messages(
            sys.stdin.buffer, MAX_MESSAGE_BYTES):
        request_id = None
        if raw_message is None:
            response = failure(None, -32600, "request exceeds adapter limit")
        else:
            try:
                line = raw_message.decode("utf-8", errors="strict")
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("request must be a JSON object")
                request_id = request.get("id")
                response = handle(gateway, request)
            except (
                    KeyError, TypeError, UnicodeDecodeError, ValueError,
                    RuntimeError, OSError) as error:
                response = failure(request_id, -32603, str(error))
        if response is not None:
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
