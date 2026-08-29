#!/usr/bin/python3

"""Read-only, fail-closed preflight for a provisioned Agent OS host."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Callable, Optional


AGENT_UID = 2004
AGENT_GID = 2004
GATEWAY_UID = 2001
GATEWAY_GID = 2001
EXEC_UID = 2002
EXEC_GID = 2002
IB_EXEC_UID = 2003
IB_EXEC_GID = 2003
MCP_LAUNCHER = "/usr/libexec/hepta-agent-mcp-launcher"
MCP_PROBE_TIMEOUT_SEC = 25
MCP_PROBE_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
WATCH_SNAPSHOT_TOOL = "watch.get_snapshot"
WATCH_SNAPSHOT_DESCRIPTOR_TOOLS = (
    "system.get_health",
    "account.get_summary",
    "portfolio.list_positions",
    "orders.list",
    "risk.get_limits",
    "market.get_quote",
)
WATCH_SNAPSHOT_READ_TOOLS = (
    "account.get_summary",
    "portfolio.list_positions",
    "orders.list",
    "risk.get_limits",
    "market.get_quote",
    "system.get_health",
)
WATCH_TOOL_NAMES = frozenset({
    "system.tools.list",
    "system.tools.describe",
    "system.cancel_request",
    "market.get_quote",
    "account.get_summary",
    "portfolio.list_positions",
    "orders.list",
    "risk.get_limits",
    "events.wait",
    "system.get_health",
    WATCH_SNAPSHOT_TOOL,
})
RUNTIME_READ_PROBES = (
    ("system.get_health", {}),
    ("market.get_quote", {"instrument": "EUR.USD"}),
    ("account.get_summary", {}),
    ("portfolio.list_positions", {}),
    ("orders.list", {}),
    ("risk.get_limits", {}),
    (WATCH_SNAPSHOT_TOOL, {"instrument": "EUR.USD"}),
)
UNPROVISIONED_SUPERVISOR_LEASE = (
    b"HEPTA_AGENT_OS_UNPROVISIONED_SUPERVISOR_LEASE_V1\n")
REVIEWED_WATCH_PROFILE_SHA256 = {
    "usr/lib/systemd/system/hepta-tool-gateway.socket":
        "124b2ae17bbde59044a898059794c68c2670e19514bbaad4cb0b80a74e48e035",
    "usr/lib/systemd/system/hepta-tool-session-supervisor.socket":
        "c9957deecd2dc22b85cd6094212df8aaec6e00a0242d6eaa0a516d8b3f5e2971",
    "usr/lib/systemd/system/hepta-tool-gateway.service":
        "2f027d427ff48ab48e90073763bf5eb3f23e2877b1ecde68cf48dca963d0fc1c",
    "usr/share/doc/heptatrader/examples/"
    "hepta-agent-host-identity.conf.example":
        "7deb8b9a88e8b184e0f5c2a6c57714dbf1095ff4e00f38d571c2caaefde20c52",
    "etc/heptatrader/hepta-tool-gateway.env":
        "5e47b32b417d41dfff119820b68ca0c5cc46ecd091c2ad08483dda007c7fa026",
}
REVIEWED_SHADOW_CLOSURE_SHA256 = {
    "usr/libexec/hepta-agent-session-bootstrap":
        "1df11cf63c9c4f84d30dac7d435f9ab57aa57bf4281c7ab957333a0f4d71e9af",
    "usr/libexec/hepta_agent_trust_domain.py":
        "50cd914355f2d2d000cdd2ff2aca45abe1e7520a5178112b062b2d4f248ca0c1",
    "usr/libexec/hepta-paper-receipt-contracts":
        "e0c50ab06f9edb3777ac50b9083bcbfd4df3baaec638b11665c9bd8d103050f8",
    "usr/libexec/hepta-shadow-watch-collector":
        "e6c6824d46dd978793cc517b509def54c3f050ab2e4c83502bb77f3640a9385a",
    "usr/libexec/hepta-shadow-watch-exporter":
        "8ebcd820559e15619c7a205ba415e56da0dcbdbecfecf31ba9e5fb84b00dc245",
    "usr/libexec/hepta-shadow-watch-custodian":
        "6f68ec87c484373383752058770d2b8fdc090a11672af67944be0ff91c07f9dc",
    "usr/libexec/hepta-broker-egress-policy":
        "d879e106d98a8ef6c8e78cc6e420383dfbcc614bd0a193c906658dc2520e1e9a",
    "usr/libexec/hepta-local-paper-control":
        "a25d745102add6e476aecd6ae4c3839220e086a8ec520f1cf38d1e7d3675af2a",
    "usr/libexec/hepta-shadow-host-installer":
        "8199666a489036a460ce2ff811fd4a8467cc5e83f39a6665f9ee64e2c16ec45a",
    "usr/libexec/hepta-p1-watch-profile-deployer":
        "1247c2b0234bc0e0d71419bc4d664a0d1066bbf13b04774ad66db456ccb68dd2",
    "usr/libexec/hepta-p1-watch-activation-transaction":
        "3464f4307f67ec9dd986542f6b87c8d15cec8dedf45ae810d7f713151c2bcf48",
    "usr/libexec/hepta-p1-shadow-host-controller":
        "affc62aaad8529164f71e279066d5d92a85153dbc6b3dc2a03a7f6a7ebce121c",
    "usr/libexec/hepta-p1-load-probe-validator":
        "d611259a5126815ade997822c27ac9c2b490d5d505a0f4b60b41fef9e7130bd2",
    "usr/libexec/build-hepta-p1-observation-policy":
        "8d49649b0a1581dfb373f059351986e4f8fe64b212f275ce2d22387a036acfde",
    "usr/libexec/hepta-p1-shadow-observer-controller":
        "63a1c5434e6ef2672afad64444e67b4436aee709dbde521585392afea9922280",
    "usr/libexec/hepta-p1-shadow-admission-launcher":
        "ec7c2dfa5b974837ec0f25f79204ccbc4ab0cdc34c45489da63cec5774731b69",
    "usr/libexec/hepta-bounded-shadow-closure-verifier":
        "c956cfa742ac7dca02318d6fc119ca17ae167f80aa3511ab3f09821d388a069c",
    "usr/libexec/hepta-official-source-capture":
        "2f2ac78a28050c3d4d25e2b6e7461778f400f7b719fb36de0271f834a9948efe",
    "usr/libexec/hepta_bounded_shadow_observer.py":
        "16ef92be5a89f0943afd282cbf01e90c1f60e72cf7ac1e86fd49027d7994fb0f",
    "usr/libexec/hepta_market_context_builder.py":
        "6828c78e5ef675b3dd7995a7df0d993ee41bef57f31d0a90b9e56e52e51d548e",
    "usr/libexec/hepta_market_evidence_normalizer.py":
        "055cd2ab7b4f9cabec9920470ad578b3ec290f7bd5bf9520738a088d27708bc9",
    "usr/libexec/hepta_market_official_source_extractor.py":
        "fea8620b44ddc53a5729a3d99cc12967d885ffccf32b04019cfcea59b8122d7d",
    "usr/libexec/hepta_eurusd_confirmed_momentum_strategy.py":
        "a9ac9eaf6e3e88affda50ba2c7d6e858512a37ff5adca81fae98a2fc7989333d",
    "usr/libexec/hepta_shadow_market_history.py":
        "3079deda6539ba46823fde8089eedc5e8fce72d588eb083308f8f7a32f5b1b74",
    "usr/libexec/hepta_strategy_shadow_runner.py":
        "d1f5202dadb3db6ba93e6b7bd029ea05071b3d08ef900ccf015fe0c942c76ef1",
    "usr/libexec/hepta_strategy_contracts.py":
        "ec03f64b708eb1fbff212917582cbe539ff5af8c9eaae6aae7536e55d7faf1a1",
    "usr/libexec/validate_hepta_strategy_decision_receipt.py":
        "da4768e541a1e0087e6bfac82c56c80754452cfaa9dc5987cd7769cb010eaeb4",
    "usr/share/heptatrader/strategies/"
    "eurusd-confirmed-momentum-shadow-v2.json":
        "7d66ab21eb7e70854c50cd4a0b5938d9cffcb28aa7f3a1159681efece6115bd8",
    "usr/lib/systemd/system/hepta-tool-gateway@.service":
        "7dbc0b301a0355751686ef3d46cac192c9a4d6144d9d01043c83499dcac731ba",
    "usr/lib/systemd/system/hepta-tool-gateway@.socket":
        "fbf487f603dfc4633f2fdea199501a56eb433b5e431d3834d261604330b8f468",
    "usr/lib/systemd/system/hepta-tool-session-supervisor@.socket":
        "8bce50b5d07c745a18b5b26c000db54f1178b91ba3660d62b249ab30c7af5727",
    "usr/lib/systemd/system/hepta-broker-egress-policy.service":
        "79a573ae1b21907b0a9392dc0220db5f41178af295abb5daf90f02cbba159280",
    "usr/lib/systemd/system/hepta-p1-watch-activation.service":
        "a66558770ec66c00088669c0fe5e7e78b87448d20fa6675f4164395a29f04171",
    "usr/lib/systemd/system/"
    "hepta-p1-watch-activation-reconcile.service":
        "5bcd1e9b790309958c5d28f258149ec1047b4e20bee58e0a17b66a91d814151a",
    "usr/lib/systemd/system/"
    "hepta-p1-watch-activation-reconcile.timer":
        "7bac2ec099c1b680a98d0ca89811c540b0214211116ca252867ecb3fe7e84045",
    "usr/lib/systemd/system/hepta-shadow-watch-collector@.service":
        "3362f1857c85ca7f6f10373b94295464ab1ea1bfb99ef4ac1ac7a261ba03fa51",
    "usr/lib/systemd/system/hepta-shadow-watch-collector@.timer":
        "dec1d2a2e321e5e4a7b6d314cf65de53596fe91addc252da61dfd42945ac55b6",
    "usr/lib/systemd/system/hepta-shadow-watch-export@.service":
        "0720f80da29b1394fe5ec8355f766698012bb2d0c3bdcc80f93869300acebbf4",
    "usr/lib/systemd/system/hepta-shadow-watch-custodian@.service":
        "c8d77d0a4a5aad593f6b040d2ae3f5b5917f606bcc4bed11eeb12ffc6b37a655",
    "usr/lib/systemd/system/"
    "hepta-shadow-watch-custodian-reconcile@.service":
        "f87dc6a9d2bbe5243c80f90683a1b3e8d9a5870d3f8c83c53107f786b37cd2e6",
    "usr/lib/systemd/system/"
    "hepta-shadow-watch-custodian-reconcile@.timer":
        "c359f03991161fa4ec445a23d50c4e2f6c7cc4c0c9b3bdbfc843566ec07c96c1",
    "usr/share/doc/heptatrader/examples/"
    "hepta-tool-gateway-domain.env.example":
        "13388545909e61be3d4ed8b47ce80b1ecf6ef14b037fe64c11944e1312cf0bdd",
    "usr/share/doc/heptatrader/examples/"
    "hepta-shadow-watch-domain.env.example":
        "2fe3fb891690cc286e352f6f03e6f61f90c642d8624298d513af790ef32e58da",
    "usr/share/heptatrader/hepta-broker-network-policy-v1.json":
        "08d430d53e4813cd0a43a23beeb92344af2130dca425814cbf7285059d90f90c",
    "etc/heptatrader/"
    "hepta-agent-trust-domain-paper-identities-v1.json":
        "4a94d555cad61a9de67b809cfae301eadd6ebf2511714c93343f10decb34e435",
}
OwnershipProvider = Callable[[str, os.stat_result], tuple[int, int]]
RuntimeProber = Callable[[Path], None]


class ContractFailure(RuntimeError):
    pass


def fail(message: str) -> None:
    raise ContractFailure(message)


def _reject_duplicate_json_keys(
        pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite_json_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON number: {value}")


def _strict_json_loads(value: str, label: str) -> object:
    try:
        return json.loads(
            value,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_non_finite_json_constant)
    except (json.JSONDecodeError, ValueError) as error:
        raise ContractFailure(f"{label}: invalid JSON: {error}") from error


def _path(root: Path, relative: str) -> Path:
    if not relative or relative.startswith("/") or ".." in Path(relative).parts:
        fail("unsafe relative path")
    current = root
    root_metadata = os.lstat(root)
    if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(root_metadata.st_mode):
        fail("root must be a non-symlink directory")
    parts = Path(relative).parts
    for index, part in enumerate(parts):
        current = current / part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            fail(f"{relative}: required path is absent")
        if stat.S_ISLNK(metadata.st_mode):
            fail(f"{relative}: symlink is forbidden")
        if index + 1 < len(parts) and not stat.S_ISDIR(metadata.st_mode):
            fail(f"{relative}: non-directory ancestor")
    return current


def _owner(
        relative: str, metadata: os.stat_result,
        provider: Optional[OwnershipProvider]) -> tuple[int, int]:
    return ((metadata.st_uid, metadata.st_gid) if provider is None
            else provider(relative, metadata))


def _require_regular(
        root: Path, relative: str, mode: int, uid: int, gid: int,
        provider: Optional[OwnershipProvider], *, executable: bool = False) -> bytes:
    path = _path(root, relative)
    before = os.lstat(path)
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
            stat.S_IMODE(before.st_mode) != mode or
            _owner(relative, before, provider) != (uid, gid)):
        fail(f"{relative}: regular file metadata mismatch")
    descriptor = os.open(
        path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        chunks: list[bytes] = []
        total = 0
        maximum = 256 * 1024 * 1024 if executable else 1024 * 1024
        while True:
            chunk = os.read(descriptor, min(65536, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                fail(f"{relative}: file exceeds size limit")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, field) != getattr(opened, field) or
           getattr(opened, field) != getattr(after, field) for field in fields):
        fail(f"{relative}: file changed during read")
    contents = b"".join(chunks)
    if executable and (
            len(contents) < 64 or not contents.startswith(b"\x7fELF")):
        fail(f"{relative}: executable is not ELF")
    return contents


def _require_directory(
        root: Path, relative: str, mode: int, uid: int, gid: int,
        provider: Optional[OwnershipProvider]) -> None:
    metadata = os.lstat(_path(root, relative))
    if (not stat.S_ISDIR(metadata.st_mode) or
            stat.S_IMODE(metadata.st_mode) != mode or
            _owner(relative, metadata, provider) != (uid, gid)):
        fail(f"{relative}: directory metadata mismatch")


def _require_socket(
        root: Path, relative: str, mode: int, uid: int, gid: int,
        provider: Optional[OwnershipProvider]) -> None:
    metadata = os.lstat(_path(root, relative))
    if (not stat.S_ISSOCK(metadata.st_mode) or
            stat.S_IMODE(metadata.st_mode) != mode or
            _owner(relative, metadata, provider) != (uid, gid)):
        fail(f"{relative}: socket metadata mismatch")


def _require_absent(root: Path, relative: str) -> None:
    """Require a runtime path to be absent without following any symlink."""
    if not relative or relative.startswith("/") or ".." in Path(relative).parts:
        fail("unsafe relative path")
    current = root
    for part in Path(relative).parts:
        current = current / part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            return
        if stat.S_ISLNK(metadata.st_mode):
            fail(f"{relative}: symlink is forbidden")
    fail(f"{relative}: runtime artifact must be absent in installation-only mode")


def _text(contents: bytes, relative: str) -> str:
    try:
        value = contents.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ContractFailure(f"{relative}: invalid UTF-8") from error
    if "\x00" in value:
        fail(f"{relative}: NUL is forbidden")
    return value


def _identities(root: Path, provider: Optional[OwnershipProvider]) -> None:
    passwd = _text(_require_regular(
        root, "etc/passwd", 0o644, 0, 0, provider), "etc/passwd")
    groups = _text(_require_regular(
        root, "etc/group", 0o644, 0, 0, provider), "etc/group")
    passwd_records = {}
    for line in passwd.splitlines():
        fields = line.split(":")
        if len(fields) == 7:
            passwd_records[fields[0]] = fields
    group_records = {}
    for line in groups.splitlines():
        fields = line.split(":")
        if len(fields) == 4:
            group_records[fields[0]] = fields
    expected = {
        "hepta-agent": (AGENT_UID, AGENT_GID),
        "hepta-gateway": (GATEWAY_UID, GATEWAY_GID),
        "hepta-exec": (EXEC_UID, EXEC_GID),
        "hepta-ib-exec": (IB_EXEC_UID, IB_EXEC_GID),
    }
    for name, (uid, gid) in expected.items():
        passwd_record = passwd_records.get(name)
        group_record = group_records.get(name)
        if (passwd_record is None or group_record is None or
                int(passwd_record[2]) != uid or int(passwd_record[3]) != gid or
                int(group_record[2]) != gid or
                passwd_record[5] != "/nonexistent" or
                not passwd_record[6].endswith("/nologin") or group_record[3]):
            fail(f"{name}: fixed non-login identity mismatch")
        for other_name, record in group_records.items():
            members = [member for member in record[3].split(",") if member]
            if name in members:
                fail(f"{name}: supplementary group membership in {other_name}")
        if any(
                other_name != name and
                (int(record[2]) == uid or int(record[3]) == gid)
                for other_name, record in passwd_records.items()):
            fail(f"{name}: UID/GID collides with another identity")
        if any(
                other_name != name and int(record[2]) == gid
                for other_name, record in group_records.items()):
            fail(f"{name}: GID collides with another group")


def _mcp_probe_request() -> str:
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"},
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        },
    ]
    requests.extend({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }
        for request_id, (tool_name, arguments) in enumerate(
            RUNTIME_READ_PROBES, 3))
    return "".join(
        json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n"
        for request in requests)


def _successful_read_payload(
        response: dict[str, object], expected_tool: str) -> dict[str, object]:
    called = response.get("result")
    if not isinstance(called, dict):
        fail("AGENT_RUNTIME_READ_CALL_REJECTED")
    content = called.get("content")
    envelope = called.get("structuredContent")
    if (set(called) != {"content", "structuredContent", "isError"} or
            called.get("isError") is not False or
            not isinstance(content, list) or len(content) != 1 or
            not isinstance(content[0], dict) or
            set(content[0]) != {"type", "text"} or
            content[0].get("type") != "text" or
            not isinstance(content[0].get("text"), str) or
            not isinstance(envelope, dict) or
            set(envelope) != {
                "status", "tool", "reason_code", "detail", "order_id",
                "payload"} or
            envelope.get("status") != "ok" or
            envelope.get("tool") != expected_tool or
            envelope.get("reason_code") != "" or
            envelope.get("detail") != "" or
            envelope.get("order_id") != -1 or
            not isinstance(envelope.get("payload"), dict)):
        fail("AGENT_RUNTIME_READ_CALL_REJECTED")
    content_envelope = _strict_json_loads(
        content[0]["text"], "AGENT_RUNTIME_READ_CALL_REJECTED")
    if content_envelope != envelope:
        fail("AGENT_RUNTIME_READ_CALL_REJECTED")
    return envelope["payload"]


def _watch_snapshot_reads(value: object) -> dict[str, object]:
    if (not isinstance(value, dict) or set(value) != {
            "schema", "catalog", "descriptors", "reads",
            "read_finished_at_ms"} or
            value.get("schema") != "hepta.watch-read-set.v1"):
        fail("AGENT_RUNTIME_WATCH_SNAPSHOT_INVALID")
    catalog = value["catalog"]
    descriptors = value["descriptors"]
    reads = value["reads"]
    finished = value["read_finished_at_ms"]
    if (not isinstance(catalog, dict) or
            catalog.get("protocol") != "hepta.agent-tools" or
            catalog.get("protocol_version") != 1 or
            catalog.get("protocol_min_version") != 1 or
            catalog.get("protocol_max_version") != 1 or
            catalog.get("schema_version") != 2 or
            not isinstance(catalog.get("catalog_schema_hash"), str) or
            re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                catalog["catalog_schema_hash"]) is None or
            not isinstance(catalog.get("tools"), list)):
        fail("AGENT_RUNTIME_WATCH_SNAPSHOT_INVALID")
    catalog_names = [
        descriptor.get("name")
        for descriptor in catalog["tools"]
        if isinstance(descriptor, dict)
    ]
    if (len(catalog_names) != len(catalog["tools"]) or
            len(catalog_names) != len(set(catalog_names)) or
            set(catalog_names) != WATCH_TOOL_NAMES):
        fail("AGENT_RUNTIME_WATCH_SNAPSHOT_INVALID")
    if (not isinstance(descriptors, dict) or
            set(descriptors) != set(WATCH_SNAPSHOT_DESCRIPTOR_TOOLS)):
        fail("AGENT_RUNTIME_WATCH_SNAPSHOT_INVALID")
    for tool_name in WATCH_SNAPSHOT_DESCRIPTOR_TOOLS:
        described = descriptors[tool_name]
        if (not isinstance(described, dict) or
                described.get("protocol") != "hepta.agent-tools" or
                described.get("protocol_version") != 1 or
                described.get("protocol_min_version") != 1 or
                described.get("protocol_max_version") != 1 or
                described.get("schema_version") != 2 or
                described.get("catalog_schema_hash") !=
                catalog["catalog_schema_hash"] or
                not isinstance(described.get("tool"), dict) or
                described["tool"].get("name") != tool_name or
                described["tool"].get("effect") != "read"):
            fail("AGENT_RUNTIME_WATCH_SNAPSHOT_INVALID")
    if (not isinstance(reads, dict) or
            set(reads) != set(WATCH_SNAPSHOT_READ_TOOLS) or
            any(not isinstance(reads[name], dict)
                for name in WATCH_SNAPSHOT_READ_TOOLS) or
            not isinstance(finished, dict) or
            set(finished) != set(WATCH_SNAPSHOT_READ_TOOLS)):
        fail("AGENT_RUNTIME_WATCH_SNAPSHOT_INVALID")
    timestamps = [finished[name] for name in WATCH_SNAPSHOT_READ_TOOLS]
    if (any(not isinstance(timestamp, int) or isinstance(timestamp, bool) or
            timestamp <= 0 for timestamp in timestamps) or
            timestamps != sorted(timestamps)):
        fail("AGENT_RUNTIME_WATCH_SNAPSHOT_INVALID")
    return reads


def _require_ready_health(health: object) -> None:
    if not isinstance(health, dict):
        fail("AGENT_RUNTIME_REMOTE_EXECUTION_NOT_READY")
    generation = health.get("execution_service_fencing_generation")
    if (health.get("gateway_ready") is not True or
            health.get("remote_execution") is not True or
            health.get("remote_execution_configured") is not True or
            health.get("remote_execution_ready") is not True or
            health.get("execution_mode") != "SIMULATOR" or
            health.get("read_model") != "execution_authoritative_v1" or
            health.get("paper_template_enabled") is not False or
            not isinstance(health.get("execution_service_epoch"), str) or
            not health["execution_service_epoch"] or
            not isinstance(generation, int) or isinstance(generation, bool) or
            generation < 1 or health.get("remote_execution_reason") != ""):
        fail("AGENT_RUNTIME_REMOTE_EXECUTION_NOT_READY")


def _runtime_probe_responses(
        returncode: int, stdout: str, stderr: str) -> dict[str, object]:
    if (returncode != 0 or stderr or
            len(stdout.encode("utf-8")) > MCP_PROBE_MAX_OUTPUT_BYTES):
        fail("AGENT_RUNTIME_MCP_PROCESS_REJECTED")
    lines = stdout.splitlines()
    expected_response_count = 2 + len(RUNTIME_READ_PROBES)
    if len(lines) != expected_response_count or any(not line for line in lines):
        fail("AGENT_RUNTIME_MCP_RESPONSE_COUNT_INVALID")
    responses = []
    for line in lines:
        response = _strict_json_loads(
            line, "AGENT_RUNTIME_MCP_RESPONSE_INVALID")
        if not isinstance(response, dict):
            fail("AGENT_RUNTIME_MCP_RESPONSE_INVALID")
        responses.append(response)

    for expected_id, response in enumerate(responses, 1):
        if (response.get("jsonrpc") != "2.0" or
                response.get("id") != expected_id or "error" in response or
                not isinstance(response.get("result"), dict)):
            fail("AGENT_RUNTIME_MCP_REQUEST_FAILED")

    initialized = responses[0]["result"]
    server_info = initialized.get("serverInfo")
    if (not isinstance(server_info, dict) or
            server_info.get("name") != "heptatrader"):
        fail("AGENT_RUNTIME_MCP_INITIALIZE_INVALID")

    listed = responses[1]["result"].get("tools")
    if not isinstance(listed, list):
        fail("AGENT_RUNTIME_MCP_TOOL_DISCOVERY_INVALID")
    discovered_names = []
    for tool in listed:
        if (not isinstance(tool, dict) or
                not isinstance(tool.get("name"), str) or
                not isinstance(tool.get("inputSchema"), dict) or
                tool.get("annotations") != {
                    "readOnlyHint": True,
                    "destructiveHint": False,
                    "idempotentHint": False,
                    "openWorldHint": False,
                }):
            fail("AGENT_RUNTIME_WATCH_TOOL_SURFACE_INVALID")
        discovered_names.append(tool["name"])
    if (len(discovered_names) != len(WATCH_TOOL_NAMES) or
            len(set(discovered_names)) != len(discovered_names) or
            set(discovered_names) != WATCH_TOOL_NAMES or
            any(name.startswith("trade.") for name in discovered_names) or
            "risk.preview_order" in discovered_names):
        fail("AGENT_RUNTIME_WATCH_TOOL_SURFACE_INVALID")

    read_payloads = {
        tool_name: _successful_read_payload(response, tool_name)
        for response, (tool_name, _arguments) in zip(
            responses[2:], RUNTIME_READ_PROBES)
    }
    health = read_payloads["system.get_health"]
    _require_ready_health(health)
    quote = read_payloads["market.get_quote"]
    if quote.get("instrument") != "EUR.USD":
        fail("AGENT_RUNTIME_QUOTE_IDENTITY_MISMATCH")
    snapshot_reads = _watch_snapshot_reads(
        read_payloads[WATCH_SNAPSHOT_TOOL])
    snapshot_health = snapshot_reads["system.get_health"]
    _require_ready_health(snapshot_health)
    if (snapshot_health.get("execution_service_epoch") !=
            health.get("execution_service_epoch") or
            snapshot_health.get("execution_service_fencing_generation") !=
            health.get("execution_service_fencing_generation")):
        fail("AGENT_RUNTIME_WATCH_SNAPSHOT_IDENTITY_MISMATCH")
    if snapshot_reads["market.get_quote"].get("instrument") != "EUR.USD":
        fail("AGENT_RUNTIME_WATCH_SNAPSHOT_IDENTITY_MISMATCH")
    return health


def _drop_to_agent_identity() -> None:
    os.setgroups([])
    os.setgid(AGENT_GID)
    os.setuid(AGENT_UID)


def _probe_runtime(root: Path) -> None:
    if root != Path("/") or os.geteuid() != 0 or os.getegid() != 0:
        fail("AGENT_RUNTIME_PROBE_REQUIRES_ROOT_HOST")
    environment = {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HEPTA_MCP_TIMEOUT_SEC": "10",
    }
    try:
        completed = subprocess.run(
            [MCP_LAUNCHER],
            input=_mcp_probe_request(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            env=environment,
            cwd="/",
            close_fds=True,
            preexec_fn=_drop_to_agent_identity,
            timeout=MCP_PROBE_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ContractFailure("AGENT_RUNTIME_MCP_PROCESS_FAILED") from error
    _runtime_probe_responses(
        completed.returncode, completed.stdout, completed.stderr)


def validate(
        root: Path,
        ownership_provider: Optional[OwnershipProvider] = None,
        *, installation_only: bool = False,
        runtime_prober: Optional[RuntimeProber] = None) -> None:
    if not root.is_absolute():
        fail("root must be absolute")
    regular_files = {
        "usr/libexec/hepta-mcp-server": 0o755,
        "usr/libexec/hepta-agent-mcp-launcher": 0o755,
        "usr/libexec/hepta-agent-session-bootstrap": 0o755,
        "usr/libexec/hepta_agent_trust_domain.py": 0o755,
        "usr/libexec/hepta-paper-receipt-contracts": 0o755,
        "usr/libexec/hepta-shadow-watch-collector": 0o755,
        "usr/libexec/hepta-shadow-watch-exporter": 0o755,
        "usr/libexec/hepta-shadow-watch-custodian": 0o755,
        "usr/libexec/hepta-broker-egress-policy": 0o755,
        "usr/libexec/hepta-local-paper-control": 0o755,
        "usr/libexec/hepta-shadow-host-installer": 0o755,
        "usr/libexec/hepta-p1-watch-profile-deployer": 0o755,
        "usr/libexec/hepta-p1-watch-activation-transaction": 0o755,
        "usr/libexec/hepta-p1-shadow-host-controller": 0o755,
        "usr/libexec/hepta-p1-load-probe-validator": 0o755,
        "usr/libexec/build-hepta-p1-observation-policy": 0o755,
        "usr/libexec/hepta-p1-shadow-observer-controller": 0o755,
        "usr/libexec/hepta-p1-shadow-admission-launcher": 0o755,
        "usr/libexec/hepta-bounded-shadow-closure-verifier": 0o755,
        "usr/libexec/hepta-official-source-capture": 0o755,
        "usr/libexec/hepta_bounded_shadow_observer.py": 0o755,
        "usr/libexec/hepta_market_context_builder.py": 0o755,
        "usr/libexec/hepta_market_evidence_normalizer.py": 0o755,
        "usr/libexec/hepta_market_official_source_extractor.py": 0o755,
        "usr/libexec/hepta_eurusd_confirmed_momentum_strategy.py": 0o755,
        "usr/libexec/hepta_shadow_market_history.py": 0o755,
        "usr/libexec/hepta_strategy_shadow_runner.py": 0o755,
        "usr/libexec/hepta_strategy_contracts.py": 0o644,
        "usr/libexec/validate_hepta_strategy_decision_receipt.py": 0o755,
        "usr/share/heptatrader/strategies/"
        "eurusd-confirmed-momentum-shadow-v2.json": 0o644,
        "usr/libexec/check-hepta-agent-os-provisioned-host": 0o755,
        "usr/lib/systemd/system/hepta-tool-gateway.service": 0o644,
        "usr/lib/systemd/system/hepta-tool-gateway.socket": 0o644,
        "usr/lib/systemd/system/hepta-tool-session-supervisor.socket": 0o644,
        "usr/lib/systemd/system/hepta-broker-egress-policy.service": 0o644,
        "usr/lib/systemd/system/hepta-p1-watch-activation.service": 0o644,
        "usr/lib/systemd/system/"
        "hepta-p1-watch-activation-reconcile.service": 0o644,
        "usr/lib/systemd/system/"
        "hepta-p1-watch-activation-reconcile.timer": 0o644,
        "usr/lib/systemd/system/"
        "hepta-shadow-watch-collector@.service": 0o644,
        "usr/lib/systemd/system/"
        "hepta-shadow-watch-collector@.timer": 0o644,
        "usr/lib/systemd/system/"
        "hepta-shadow-watch-export@.service": 0o644,
        "usr/lib/systemd/system/hepta-tool-gateway@.service": 0o644,
        "usr/lib/systemd/system/hepta-tool-gateway@.socket": 0o644,
        "usr/lib/systemd/system/"
        "hepta-tool-session-supervisor@.socket": 0o644,
        "usr/lib/systemd/system/"
        "hepta-shadow-watch-custodian@.service": 0o644,
        "usr/lib/systemd/system/"
        "hepta-shadow-watch-custodian-reconcile@.service": 0o644,
        "usr/lib/systemd/system/"
        "hepta-shadow-watch-custodian-reconcile@.timer": 0o644,
        "usr/lib/tmpfiles.d/heptatrader-agent-os.conf": 0o644,
        "usr/share/heptatrader/hepta-service-identities-v1.json": 0o644,
        "usr/share/heptatrader/hepta-broker-network-policy-v1.json": 0o644,
        "etc/heptatrader/"
        "hepta-agent-trust-domain-paper-identities-v1.json": 0o600,
        "usr/share/heptatrader/plugins/heptatrader-agent-os/.mcp.json": 0o644,
        "usr/share/heptatrader/plugins/heptatrader-agent-os/"
        ".codex-plugin/plugin.json": 0o644,
        "usr/share/heptatrader/plugins/heptatrader-agent-os/README.md": 0o644,
        "usr/share/heptatrader/.agents/plugins/marketplace.json": 0o644,
        "usr/share/doc/heptatrader/examples/"
        "hepta-agent-host-identity.conf.example": 0o644,
        "usr/share/doc/heptatrader/examples/"
        "hepta-tool-gateway-domain.env.example": 0o644,
        "usr/share/doc/heptatrader/examples/"
        "hepta-shadow-watch-domain.env.example": 0o644,
        "etc/heptatrader/hepta-tool-gateway.env": 0o644,
        "etc/heptatrader/hepta-supervisor-lease.key": 0o400,
    }
    contents = {
        relative: _require_regular(
            root, relative, mode, 0, 0, ownership_provider)
        for relative, mode in regular_files.items()
    }
    for relative, expected_sha256 in REVIEWED_WATCH_PROFILE_SHA256.items():
        if hashlib.sha256(contents[relative]).hexdigest() != expected_sha256:
            fail(f"{relative}: reviewed WATCH profile digest mismatch")
    for relative, expected_sha256 in REVIEWED_SHADOW_CLOSURE_SHA256.items():
        if hashlib.sha256(contents[relative]).hexdigest() != expected_sha256:
            fail(f"{relative}: reviewed SHADOW closure digest mismatch")
    for relative in (
            "usr/libexec/hepta-tool-gatewayd", "usr/bin/hepta-sessionctl",
            "usr/bin/heptactl"):
        _require_regular(
            root, relative, 0o755, 0, 0, ownership_provider, executable=True)
    if not installation_only:
        cleanup_lock = _require_regular(
            root, "run/hepta-agent/session-lease-terminal-cleanup.lock",
            0o644, 0, 0, ownership_provider)
        if cleanup_lock:
            fail("supervisor cleanup interlock must be empty")

    _identities(root, ownership_provider)
    identity_manifest = _strict_json_loads(_text(
        contents["usr/share/heptatrader/hepta-service-identities-v1.json"],
        "identity manifest"), "identity manifest")
    expected_identities = {
        "hepta-agent": {
            "uid": AGENT_UID, "gid": AGENT_GID,
            "role": "agent-tool-client",
        },
        "hepta-gateway": {
            "uid": GATEWAY_UID, "gid": GATEWAY_GID,
            "role": "tool-gateway",
        },
        "hepta-exec": {
            "uid": EXEC_UID, "gid": EXEC_GID,
            "role": "simulator-execution-authority",
        },
        "hepta-ib-exec": {
            "uid": IB_EXEC_UID, "gid": IB_EXEC_GID,
            "role": "ib-paper-execution-authority",
        },
    }
    if identity_manifest != {
            "schema": "hepta.service-identities.v1",
            "identities": expected_identities,
            }:
        fail("identity manifest mismatch")

    key = contents["etc/heptatrader/hepta-supervisor-lease.key"]
    if installation_only:
        if key != UNPROVISIONED_SUPERVISOR_LEASE:
            fail("installation-only supervisor lease placeholder mismatch")
        for relative in (
                "run/hepta-agent/tools.sock",
                "run/hepta-agent/session.token",
                "run/hepta-tool-gateway/session-supervisor.sock"):
            _require_absent(root, relative)
    else:
        if (key == UNPROVISIONED_SUPERVISOR_LEASE or
                len(key) < 24 or len(key) > 512):
            fail("supervisor lease key is absent, placeholder, or invalid")
        _require_directory(
            root, "run/hepta-agent", 0o711, 0, 0, ownership_provider)
        _require_directory(
            root, "run/hepta-tool-gateway", 0o700,
            GATEWAY_UID, GATEWAY_GID, ownership_provider)
        _require_socket(
            root, "run/hepta-agent/tools.sock", 0o600,
            AGENT_UID, AGENT_GID, ownership_provider)
        _require_socket(
            root, "run/hepta-tool-gateway/session-supervisor.sock", 0o600,
            GATEWAY_UID, GATEWAY_GID, ownership_provider)
        token = _require_regular(
            root, "run/hepta-agent/session.token", 0o600,
            AGENT_UID, AGENT_GID, ownership_provider)
        stripped = token.rstrip(b"\r\n")
        if (len(stripped) < 24 or len(stripped) > 512 or
                any(byte < 0x21 or byte > 0x7e for byte in stripped)):
            fail("Agent session token content contract mismatch")

    tool_socket = _text(contents[
        "usr/lib/systemd/system/hepta-tool-gateway.socket"], "tool socket")
    supervisor = _text(contents[
        "usr/lib/systemd/system/hepta-tool-session-supervisor.socket"],
        "supervisor socket")
    service = _text(contents[
        "usr/lib/systemd/system/hepta-tool-gateway.service"], "gateway service")
    for value in (
            "ListenStream=/run/hepta-agent/tools.sock", "DirectoryMode=0711",
            "SocketUser=hepta-agent", "SocketGroup=hepta-agent",
            "SocketMode=0600", "FileDescriptorName=hepta-tool"):
        if value not in tool_socket:
            fail(f"tool socket misses {value}")
    for value in (
            "ListenStream=/run/hepta-tool-gateway/session-supervisor.sock",
            "DirectoryMode=0700", "SocketUser=hepta-gateway",
            "SocketGroup=hepta-gateway", "SocketMode=0600",
            "FileDescriptorName=hepta-supervisor"):
        if value not in supervisor:
            fail(f"supervisor socket misses {value}")
    for value in (
            "User=hepta-gateway", "Group=hepta-gateway",
            "ExecStart=/usr/libexec/hepta-tool-gatewayd",
            "Environment=HEPTA_TOOL_SOCKET=/run/hepta-agent/tools.sock",
            "PrivateNetwork=yes", "RestrictAddressFamilies=AF_UNIX",
            "CapabilityBoundingSet=", "NoNewPrivileges=yes"):
        if value not in service:
            fail(f"gateway service misses {value}")

    broker_service = _text(contents[
        "usr/lib/systemd/system/hepta-broker-egress-policy.service"],
        "broker egress service")
    broker_contract = (
        "LoadCredential=hepta-broker-egress-policy.py:"
        "/usr/libexec/hepta-broker-egress-policy",
        "ExecStart=/usr/bin/python3.12 -I -S "
        "${CREDENTIALS_DIRECTORY}/hepta-broker-egress-policy.py "
        "--supervise-deny-all --paper-identities "
        "/etc/heptatrader/"
        "hepta-agent-trust-domain-paper-identities-v1.json",
        "ExecStopPost=/usr/bin/python3.12 -I -S "
        "${CREDENTIALS_DIRECTORY}/hepta-broker-egress-policy.py "
        "--tighten-deny-all",
    )
    broker_lines = broker_service.splitlines()
    if any(broker_lines.count(value) != 1 for value in broker_contract):
        fail("broker egress service credential/deny-all contract mismatch")
    if " --supervise --paper-identities " in broker_service:
        fail("broker egress service enables PAPER opt-in supervision")
    deny_all_identity = _strict_json_loads(_text(contents[
        "etc/heptatrader/"
        "hepta-agent-trust-domain-paper-identities-v1.json"],
        "deny-all PAPER identity manifest"),
        "deny-all PAPER identity manifest")
    if deny_all_identity != {
            "schema": "hepta.agent-trust-domain-paper-identities.v1",
            "version": 1,
            "source_policy_sha256":
                "sha256:08d430d53e4813cd0a43a23beeb92344"
                "af2130dca425814cbf7285059d90f90c",
            "paper_authorized": False,
            "live_authorized": False,
            "identities": [],
            }:
        fail("deny-all PAPER identity manifest contract mismatch")

    tmpfiles = _text(contents[
        "usr/lib/tmpfiles.d/heptatrader-agent-os.conf"], "Agent tmpfiles")
    directives = [
        line.split() for line in tmpfiles.splitlines()
        if line.strip() and not line.lstrip().startswith("#")]
    if directives != [
            ["d", "/run/hepta-agent", "0711", "root", "root", "-", "-"],
            ["f", "/run/hepta-agent/session-lease-terminal-cleanup.lock",
             "0644", "root", "root", "-", "-"],
            ["d", "/var/lib/hepta/p1-admission", "0755", "root", "root",
             "-", "-"],
            ["d", "/var/lib/hepta/p1-admission/private", "0700", "root",
             "root", "-", "-"],
            ["d", "/var/lib/hepta/p1-admission/public", "0755", "root",
             "root", "-", "-"],
            ["d", "/var/lib/hepta/p1-admission/readers", "0755", "root",
             "root", "-", "-"],
            ]:
        fail("Agent tmpfiles exact contract mismatch")

    plugin = _strict_json_loads(_text(contents[
        "usr/share/heptatrader/plugins/heptatrader-agent-os/"
        ".codex-plugin/plugin.json"], "plugin manifest"), "plugin manifest")
    if plugin.get("mcpServers") != "./.mcp.json" or "skills" in plugin:
        fail("plugin manifest contains a dangling or invalid surface")
    marketplace = _strict_json_loads(_text(contents[
        "usr/share/heptatrader/.agents/plugins/marketplace.json"],
        "Codex marketplace"), "Codex marketplace")
    expected_marketplace = {
        "name": "heptatrader",
        "interface": {"displayName": "HeptaTrader"},
        "plugins": [{
            "name": "heptatrader-agent-os",
            "source": {
                "source": "local",
                "path": "./plugins/heptatrader-agent-os",
            },
            "policy": {
                "installation": "AVAILABLE",
                "authentication": "ON_INSTALL",
            },
            "category": "Developer Tools",
        }],
    }
    if marketplace != expected_marketplace:
        fail("installed Codex marketplace contract mismatch")
    mcp = _strict_json_loads(_text(contents[
        "usr/share/heptatrader/plugins/heptatrader-agent-os/.mcp.json"],
        "MCP config"), "MCP config")
    expected_server = {
        "command": "/usr/libexec/hepta-agent-mcp-launcher",
        "env": {},
    }
    if mcp != {"mcpServers": {"heptatrader": expected_server}}:
        fail("installed MCP config mismatch")

    launcher = _text(contents[
        "usr/libexec/hepta-agent-mcp-launcher"], "MCP launcher")
    bootstrap = _text(contents[
        "usr/libexec/hepta-agent-session-bootstrap"], "session bootstrap")
    if ("os.execve(MCP_SERVER" not in launcher or "setuid(" in launcher or
            "setgid(" in launcher):
        fail("MCP launcher identity contract mismatch")
    if ('"provision", "--template", "watch"' not in bootstrap or
            "paper_authorized" not in bootstrap or
            "live_authorized" not in bootstrap or "--template paper" in bootstrap):
        fail("session bootstrap WATCH-only contract mismatch")
    environment = _text(contents[
        "etc/heptatrader/hepta-tool-gateway.env"], "Gateway environment")
    if ("HEPTA_TOOL_AGENT_UID=2004" not in environment or
            re.search(r"(?im)^(?:.*TOKEN|.*PASSWORD|.*SECRET)=", environment)):
        fail("Gateway environment identity/secret contract mismatch")
    dropin = _text(contents[
        "usr/share/doc/heptatrader/examples/"
        "hepta-agent-host-identity.conf.example"], "Agent host drop-in")
    for value in (
            "User=hepta-agent", "Group=hepta-agent",
            "SupplementaryGroups=", "NoNewPrivileges=yes"):
        if value not in dropin:
            fail(f"Agent host identity drop-in misses {value}")
    if not installation_only:
        (runtime_prober or _probe_runtime)(root)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/"))
    parser.add_argument(
        "--installation-only", action="store_true",
        help=("validate the installed Agent OS payload and require runtime "
              "socket/token state to remain absent"))
    arguments = parser.parse_args()
    try:
        validate(
            arguments.root, installation_only=arguments.installation_only)
    except (ContractFailure, OSError, ValueError, json.JSONDecodeError) as error:
        print("hepta_agent_os_provisioned_host: FAIL: " + str(error),
              file=sys.stderr)
        return 78
    print("hepta_agent_os_provisioned_host: PASS "
          f"mode={'installation-only' if arguments.installation_only else 'runtime'} "
          f"runtime_probe={'not-executed' if arguments.installation_only else 'passed'} "
          "agent_uid=2004 gateway_uid=2001 paper_authorized=false "
          "live_authorized=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
