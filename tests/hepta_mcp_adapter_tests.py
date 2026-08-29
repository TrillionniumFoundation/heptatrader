#!/usr/bin/env python3

import hashlib
import json
import os
import pathlib
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading


PREVIEW_PERMIT = "sha256:" + "b" * 64
MUTATION_COMMAND_ID = "hexec-command-" + "c" * 32


def recv_exact(connection, size):
    data = bytearray()
    while len(data) < size:
        chunk = connection.recv(size - len(data))
        if not chunk:
            raise AssertionError("client closed request early")
        data.extend(chunk)
    return bytes(data)


def decode_request(body):
    assert body[:4] == b"HTT1"
    fields = {}
    offset = 4
    while offset < len(body):
        field_id, length = struct.unpack("!HI", body[offset:offset + 6])
        offset += 6
        assert field_id not in fields
        fields[field_id] = body[offset:offset + length].decode("utf-8")
        offset += length
    assert offset == len(body)
    return fields


def envelope(tool, payload):
    return {
        "status": "ok", "tool": tool, "reason_code": "", "detail": "",
        "order_id": -1, "payload": payload,
    }


def descriptor_hash(descriptor):
    canonical = "\0".join((
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
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def seal_catalog(catalog):
    for descriptor in catalog["tools"]:
        descriptor["schema_hash"] = descriptor_hash(descriptor)
    canonical = "".join(
        descriptor["name"] + "=" + descriptor["schema_hash"] + "\n"
        for descriptor in sorted(
            catalog["tools"], key=lambda item: item["name"])
    ).encode("utf-8")
    catalog["catalog_schema_hash"] = (
        "sha256:" + hashlib.sha256(canonical).hexdigest())
    return catalog


class FakeToolServer:
    def __init__(self, path, responses):
        self.path = path
        self.responses = list(responses)
        self.requests = []
        self.broker_send_ids = set()
        self.broker_sends = 0
        self.error = None
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self.thread.start()
        assert self.ready.wait(5)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.thread.join(5)
        assert not self.thread.is_alive()
        if self.error is not None:
            raise self.error

    def _run(self):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(self.path))
                listener.listen(8)
                self.ready.set()
                for response in self.responses:
                    with listener.accept()[0] as connection:
                        size = struct.unpack("!I", recv_exact(connection, 4))[0]
                        request = decode_request(recv_exact(connection, size))
                        self.requests.append(request)
                        if request.get(3) == "trade.place_order":
                            command_id = request.get(2)
                            if command_id not in self.broker_send_ids:
                                self.broker_send_ids.add(command_id)
                                self.broker_sends += 1
                        if response is None:
                            continue
                        if isinstance(response, int):
                            connection.sendall(struct.pack("!I", response))
                        else:
                            body = json.dumps(response, separators=(",", ":")).encode("utf-8")
                            connection.sendall(struct.pack("!I", len(body)) + body)
        except BaseException as error:
            self.error = error
            self.ready.set()


def run_adapter(adapter, environment, requests):
    return subprocess.run(
        [sys.executable, str(adapter)],
        input="".join(json.dumps(item) + "\n" for item in requests),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        timeout=10,
        check=True,
    )


def main():
    source_root = pathlib.Path(__file__).resolve().parents[1]
    adapter = source_root / "adapters" / "mcp" / "hepta_mcp_server.py"
    source = adapter.read_text(encoding="utf-8", errors="strict")
    assert "import subprocess" not in source
    assert "HEPTACTL_BIN" not in source
    with tempfile.TemporaryDirectory(prefix="hepta-mcp-test-") as directory:
        root = pathlib.Path(directory)
        token_path = root / "token"
        token_path.write_text("offline-token\n", encoding="utf-8")
        token_path.chmod(0o600)
        socket_path = root / "tool.sock"
        order_properties = {
            "instrument": {"type": "string"},
            "side": {"type": "string"},
            "quantity": {"type": "number"},
            "order_type": {"type": "string"},
            "limit_price": {"type": "number"},
            "reference_price": {"type": "number"},
            "expires_at_ms": {"type": "integer"},
            "tif": {"type": "string"},
            "preview_permit": {"type": "string"},
        }
        catalog = {
            "protocol": "hepta.agent-tools",
            "protocol_version": 1,
            "protocol_min_version": 1,
            "protocol_max_version": 1,
            "schema_version": 2,
            "tools": [{
                "name": "market.get_quote",
                "description": "quote",
                "required_capability": "market.read",
                "effect": "read",
                "timeout_ms": 8000,
                "input_schema": {
                    "type": "object",
                    "properties": {"instrument": {"type": "string"}},
                    "required": ["instrument"],
                    "additionalProperties": False,
                },
                "result_schema": {
                    "type": "object",
                    "additionalProperties": True,
                },
            }, {
                "name": "risk.preview_order",
                "description": "authoritative preview",
                "required_capability": "risk.preview",
                "effect": "read",
                "timeout_ms": 16000,
                "input_schema": {
                    "type": "object",
                    "properties": order_properties,
                    "required": [
                        "instrument", "side", "quantity", "order_type",
                        "limit_price", "reference_price", "expires_at_ms", "tif",
                    ],
                    "additionalProperties": False,
                },
                "result_schema": {
                    "type": "object",
                    "additionalProperties": True,
                },
            }, {
                "name": "trade.place_order",
                "description": "authorized place",
                "required_capability": "trade.place",
                "effect": "trade",
                "timeout_ms": 16000,
                "input_schema": {
                    "type": "object",
                    "properties": order_properties,
                    "required": [
                        "instrument", "side", "quantity", "order_type",
                        "limit_price", "reference_price", "expires_at_ms", "tif",
                        "preview_permit",
                    ],
                    "additionalProperties": False,
                },
                "result_schema": {
                    "type": "object",
                    "required": ["status", "command_id", "order_id"],
                    "additionalProperties": False,
                },
            }, {
                "name": "trade.cancel_order",
                "description": "authorized cancel",
                "required_capability": "trade.cancel",
                "effect": "trade",
                "timeout_ms": 16000,
                "input_schema": {
                    "type": "object",
                    "properties": {"order_id": {"type": "integer"}},
                    "required": ["order_id"],
                    "additionalProperties": False,
                },
                "result_schema": {
                    "type": "object",
                    "required": ["status", "command_id", "order_id"],
                    "additionalProperties": False,
                },
            }, {
                "name": "execution.get_command_status",
                "description": "owner-scoped durable command status",
                "required_capability": "orders.read",
                "effect": "read",
                "timeout_ms": 8000,
                "input_schema": {
                    "type": "object",
                    "properties": {"command_id": {
                        "type": "string", "minLength": 8, "maxLength": 128,
                    }},
                    "required": ["command_id"],
                    "additionalProperties": False,
                },
                "result_schema": {
                    "type": "object",
                    "required": [
                        "authoritative", "command_id", "command_status",
                        "order_id", "reason_code", "execution_service_epoch",
                        "execution_service_fencing_generation",
                    ],
                    "additionalProperties": False,
                },
            }],
        }
        seal_catalog(catalog)
        placed = envelope("trade.place_order", {"order_id": 100})
        placed["order_id"] = 100
        command_status = envelope("execution.get_command_status", {
            "authoritative": True,
            "command_id": MUTATION_COMMAND_ID,
            "command_status": "accepted",
            "order_id": 100,
            "reason_code": "DURABLE_ACCEPTED",
            "execution_service_epoch": "epoch-a",
            "execution_service_fencing_generation": 7,
        })
        command_status["order_id"] = 100
        responses = [
            envelope("system.tools.list", catalog),
            envelope("market.get_quote", {"bid": 1.1, "ask": 1.2}),
            envelope("risk.preview_order", {
                "approved": True, "preview_permit": PREVIEW_PERMIT,
                "command_id": MUTATION_COMMAND_ID,
            }),
            placed,
            command_status,
            envelope("system.tools.list", catalog),
        ]
        environment = dict(os.environ)
        environment.update({
            "HEPTA_TOOL_SOCKET": str(socket_path),
            "HEPTA_TOOL_SESSION_TOKEN_FILE": str(token_path),
        })
        order_arguments = {
            "instrument": "EUR.USD",
            "side": "BUY",
            "quantity": 100,
            "order_type": "LMT",
            "limit_price": 1.099,
            "reference_price": 1.1001,
            "expires_at_ms": 9999999999999,
            "tif": "DAY",
        }
        place_arguments = dict(order_arguments)
        place_arguments["preview_permit"] = PREVIEW_PERMIT
        place_arguments["command_id"] = MUTATION_COMMAND_ID
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "market.get_quote", "arguments": {"instrument": "EUR.USD"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "risk.preview_order", "arguments": order_arguments}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "trade.place_order", "arguments": place_arguments}},
            {
                "jsonrpc": "2.0", "id": 6, "method": "tools/call",
                "params": {
                    "name": "execution.get_command_status",
                    "arguments": {"command_id": MUTATION_COMMAND_ID},
                },
            },
            {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "trade.hidden", "arguments": {}}},
        ]
        with FakeToolServer(socket_path, responses) as server:
            completed = run_adapter(adapter, environment, requests)
        result = [json.loads(line) for line in completed.stdout.splitlines()]
        assert len(result) == 7
        assert result[1]["result"]["tools"][0]["name"] == "market.get_quote"
        place_schema = result[1]["result"]["tools"][2]["inputSchema"]
        assert "command_id" in place_schema["required"]
        assert place_schema["properties"]["command_id"]["minLength"] == 8
        assert place_schema["properties"]["command_id"]["maxLength"] == 128
        assert "Execution-issued" in (
            place_schema["properties"]["command_id"]["description"])
        cancel_schema = result[1]["result"]["tools"][3]["inputSchema"]
        assert "caller-generated" in (
            cancel_schema["properties"]["command_id"]["description"])
        command_status_tool = result[1]["result"]["tools"][4]
        assert command_status_tool["name"] == "execution.get_command_status"
        assert command_status_tool["inputSchema"]["required"] == ["command_id"]
        assert command_status_tool["inputSchema"]["properties"]["command_id"][
            "minLength"] == 8
        assert command_status_tool["annotations"]["readOnlyHint"] is True
        assert command_status_tool["annotations"]["idempotentHint"] is False
        assert result[1]["result"]["tools"][2]["annotations"]["idempotentHint"] is True
        assert result[2]["result"]["isError"] is False
        assert result[3]["result"]["isError"] is False
        assert result[4]["result"]["isError"] is False
        assert result[5]["result"]["isError"] is False
        assert result[5]["result"]["structuredContent"]["payload"][
            "command_id"] == MUTATION_COMMAND_ID
        assert result[6]["error"]["code"] == -32603
        assert len(server.requests) == 6
        assert server.requests[0][1] == "offline-token"
        assert server.requests[0][3] == "system.tools.list"
        assert server.requests[1][3] == "market.get_quote"
        assert server.requests[1][4] == "EUR.USD"
        assert server.requests[1][24] == catalog["tools"][0]["schema_hash"]
        assert server.requests[2][3] == "risk.preview_order"
        assert server.requests[3][3] == "trade.place_order"
        assert server.requests[4][3] == "execution.get_command_status"
        assert server.requests[2][2].startswith("mcp-")
        assert server.requests[3][2] == MUTATION_COMMAND_ID
        assert server.requests[4][2].startswith("mcp-")
        assert server.requests[4][2] != MUTATION_COMMAND_ID
        assert server.requests[2][2] != server.requests[3][2]
        assert 25 not in server.requests[2]
        assert server.requests[3][25] == PREVIEW_PERMIT
        assert 26 not in server.requests[3]
        assert server.requests[4][26] == MUTATION_COMMAND_ID
        assert server.requests[2][4] == server.requests[3][4] == "EUR.USD"
        assert server.broker_sends == 1

        for suffix, mutation, expected_error in (
                ("schema-v1", lambda value: value.update(schema_version=1),
                 "discovery schema version"),
                ("descriptor-hash",
                 lambda value: value["tools"][0].update(
                     schema_hash="sha256:" + "0" * 64),
                 "descriptor schema hash mismatch"),
                ("catalog-hash",
                 lambda value: value.update(
                     catalog_schema_hash="sha256:" + "0" * 64),
                 "catalog schema hash mismatch")):
            rejected_catalog = json.loads(json.dumps(catalog))
            mutation(rejected_catalog)
            rejected_socket = root / (suffix + ".sock")
            rejected_environment = dict(environment)
            rejected_environment["HEPTA_TOOL_SOCKET"] = str(rejected_socket)
            with FakeToolServer(
                    rejected_socket,
                    [envelope("system.tools.list", rejected_catalog)]):
                rejected = run_adapter(
                    adapter, rejected_environment, [requests[1]])
            rejection = json.loads(rejected.stdout)
            assert rejection["error"]["code"] == -32603
            assert expected_error in rejection["error"]["message"]

        retry_socket = root / "retry.sock"
        retry_environment = dict(environment)
        retry_environment["HEPTA_TOOL_SOCKET"] = str(retry_socket)
        duplicate = envelope("trade.place_order", {"order_id": 701})
        duplicate["status"] = "duplicate"
        duplicate["reason_code"] = "DURABLE_ACCEPTED_REPLAY"
        duplicate["order_id"] = 701
        rejected = envelope("market.get_quote", None)
        rejected["status"] = "rejected"
        rejected["reason_code"] = "AUTHORITATIVE_QUOTE_STALE"
        uncertain = envelope("market.get_quote", None)
        uncertain["status"] = "uncertain"
        uncertain["reason_code"] = "EXECUTION_SERVICE_UNAVAILABLE"
        retry_arguments = dict(place_arguments)
        retry_command_id = "hexec-command-" + "d" * 32
        retry_arguments["command_id"] = retry_command_id
        retry_preview_payload = {
            "approved": True,
            "preview_permit": PREVIEW_PERMIT,
            "command_id": retry_command_id,
        }
        retry_requests = [
            requests[0],
            {"jsonrpc": "2.0", "id": 20, "method": "tools/call",
             "params": {"name": "risk.preview_order",
                        "arguments": order_arguments}},
            {"jsonrpc": "2.0", "id": 21, "method": "tools/call",
             "params": {"name": "trade.place_order",
                        "arguments": retry_arguments}},
            {"jsonrpc": "2.0", "id": 22, "method": "tools/call",
             "params": {"name": "trade.place_order",
                        "arguments": retry_arguments}},
            {"jsonrpc": "2.0", "id": 23, "method": "tools/call",
             "params": {"name": "market.get_quote",
                        "arguments": {"instrument": "EUR.USD"}}},
            {"jsonrpc": "2.0", "id": 24, "method": "tools/call",
             "params": {"name": "market.get_quote",
                        "arguments": {"instrument": "EUR.USD"}}},
        ]
        with FakeToolServer(
                retry_socket,
                [envelope("system.tools.list", catalog),
                 envelope("risk.preview_order", retry_preview_payload),
                 None, duplicate, rejected, uncertain]) as retry_server:
            retried = run_adapter(adapter, retry_environment, retry_requests)
        retry_result = [
            json.loads(line) for line in retried.stdout.splitlines()]
        assert retry_result[1]["result"]["isError"] is False
        assert retry_result[2]["error"]["code"] == -32603
        assert "closed the response early" in retry_result[2]["error"]["message"]
        assert retry_result[3]["result"]["structuredContent"]["status"] == "duplicate"
        assert retry_result[3]["result"]["isError"] is False
        assert retry_result[4]["result"]["structuredContent"]["status"] == "rejected"
        assert retry_result[4]["result"]["isError"] is True
        assert retry_result[5]["result"]["structuredContent"]["status"] == "uncertain"
        assert retry_result[5]["result"]["isError"] is True
        assert retry_server.requests[1][2] != retry_command_id
        assert retry_server.requests[2][2] == retry_command_id
        assert retry_server.requests[3][2] == retry_command_id
        assert retry_server.broker_sends == 1

        invalid_command_socket = root / "invalid-command.sock"
        invalid_command_environment = dict(environment)
        invalid_command_environment["HEPTA_TOOL_SOCKET"] = str(
            invalid_command_socket)
        missing_command = dict(place_arguments)
        missing_command.pop("command_id")
        invalid_command_requests = [
            requests[0],
            {"jsonrpc": "2.0", "id": 30, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 31, "method": "tools/call",
             "params": {"name": "trade.place_order",
                        "arguments": missing_command}},
        ]
        for request_id, invalid_id in (
                (32, "short"),
                (33, "bad command"),
                (34, "é" * 8),
                (35, "x" * 129)):
            invalid_arguments = dict(place_arguments)
            invalid_arguments["command_id"] = invalid_id
            invalid_command_requests.append({
                "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                "params": {"name": "trade.place_order",
                           "arguments": invalid_arguments},
            })
        with FakeToolServer(
                invalid_command_socket,
                [envelope("system.tools.list", catalog)]):
            invalid_commands = run_adapter(
                adapter, invalid_command_environment, invalid_command_requests)
        invalid_results = [
            json.loads(line) for line in invalid_commands.stdout.splitlines()]
        assert len(invalid_results) == len(invalid_command_requests)
        assert all(
            response["error"]["code"] == -32603
            for response in invalid_results[2:])

        token_path.chmod(0o644)
        unsafe = run_adapter(adapter, environment, [requests[1]])
        assert "must not be accessible" in json.loads(unsafe.stdout)["error"]["message"]
        token_path.chmod(0o600)
        token_link = root / "token-link"
        token_link.symlink_to(token_path)
        linked_environment = dict(environment)
        linked_environment["HEPTA_TOOL_SESSION_TOKEN_FILE"] = str(token_link)
        linked = run_adapter(adapter, linked_environment, [requests[1]])
        assert "non-symlink" in json.loads(linked.stdout)["error"]["message"]

        invalid_uid_environment = dict(environment)
        invalid_uid_environment["HEPTA_TOOL_EXPECTED_UID"] = "not-a-uid"
        invalid_uid = subprocess.run(
            [sys.executable, str(adapter)], input=json.dumps(requests[0]) + "\n",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=invalid_uid_environment, timeout=10, check=False)
        assert invalid_uid.returncode == 78
        assert "EXPECTED_UID is invalid" in invalid_uid.stderr

        wrong_uid_environment = dict(environment)
        wrong_uid_environment["HEPTA_TOOL_EXPECTED_UID"] = str(
            os.geteuid() + 1 if os.geteuid() != 4_294_967_295 else 1)
        wrong_uid = subprocess.run(
            [sys.executable, str(adapter)], input=json.dumps(requests[0]) + "\n",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=wrong_uid_environment, timeout=10, check=False)
        assert wrong_uid.returncode == 78
        assert "effective UID does not match hepta-agent" in wrong_uid.stderr

        oversized_input = (
            b"{" + b"x" * 1_048_576 + b"}\n" +
            json.dumps(requests[0]).encode("utf-8") + b"\n")
        bounded = subprocess.run(
            [sys.executable, str(adapter)], input=oversized_input,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=environment, timeout=10, check=True)
        bounded_responses = [
            json.loads(line)
            for line in bounded.stdout.decode("utf-8").splitlines()]
        assert len(bounded_responses) == 2
        assert bounded_responses[0]["error"]["code"] == -32600
        assert "exceeds adapter limit" in bounded_responses[0]["error"]["message"]
        assert bounded_responses[1]["result"]["serverInfo"]["name"] == "heptatrader"

        oversized_socket = root / "oversized.sock"
        oversized_environment = dict(environment)
        oversized_environment["HEPTA_TOOL_SOCKET"] = str(oversized_socket)
        with FakeToolServer(oversized_socket, [1_048_577]):
            oversized = run_adapter(adapter, oversized_environment, [requests[1]])
        assert "exceeds adapter limit" in json.loads(oversized.stdout)["error"]["message"]
    print("hepta_mcp_adapter_tests: PASS")


if __name__ == "__main__":
    main()
