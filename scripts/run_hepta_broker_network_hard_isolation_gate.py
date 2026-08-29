#!/usr/bin/env python3

"""Certify the dedicated broker network-namespace boundary on a disposable VM.

This gate is deliberately native-host only.  It never contacts a broker and
never stages an IB binary, credential, broker protocol implementation, or
order surface.  Four protected TCP ports are served by an inert Python
sentinel inside a unique broker namespace.  Unique Execution, Gateway, Agent,
and Simulator namespaces exercise negative and positive reachability.

The default executor is disabled.  Native mutation is possible only after an
explicit ``--run``, euid 0, and four independently reviewed, canonical,
unexpired provenance documents that bind the disposable boot, clean source,
native base, and exact tooling.  In particular, the drill flushes and reloads
the disposable host firewall; it is not suitable for a workstation or a
persistent host.

Every external command is represented by :class:`CommandSpec` and goes
through the injected executor.  Rootless tests use a command-level fake and
cannot reach iproute2, nftables, systemd, cgroups, or the host network.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
from typing import Mapping, Optional, Protocol, Sequence
import uuid

sys.path.insert(0, str(Path(__file__).resolve(strict=True).parent))
import hepta_rootful_review_closure_consumer as ROOT_REVIEW


SCHEMA = "hepta.broker-network-hard-isolation-gate.v1"
PROBE_SCHEMA = "hepta.broker-network-hard-isolation-probe.v1"
PROBE_MARKER = "HEPTA_BROKER_HARD_ISOLATION_PROBE="
PURPOSE = "hepta-broker-network-hard-isolation-gate"
PROTECTED_PORTS = (4001, 4002, 7496, 7497)
ROLES = ("broker", "execution", "gateway", "agent", "simulator")
CLIENT_ROLES = ("execution", "gateway", "agent", "simulator")
UIDS = {
    "broker": 29001,
    "execution": 29002,
    "gateway": 29003,
    "agent": 29004,
    "simulator": 29005,
}
MAX_INPUT = 4 * 1024 * 1024
MAX_OUTPUT = 4 * 1024 * 1024
MAX_REPORT = 4 * 1024 * 1024
CANONICAL_RUN_ID = re.compile(r"^[0-9a-f]{32}$")
CANONICAL_SHA256 = re.compile(r"^[0-9a-f]{64}$")
CANONICAL_TAGGED_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
CANONICAL_COMMIT = re.compile(r"^[0-9a-f]{40}$")
CANONICAL_BOOT_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}$")
CANONICAL_INVOCATION_ID = re.compile(r"^[0-9a-f]{32}$")
COMMAND_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
    "TZ": "UTC",
}
TOOL_PATHS = {
    "ip": "/usr/bin/ip",
    "nft": "/usr/sbin/nft",
    "systemd_run": "/usr/bin/systemd-run",
    "systemd_detect_virt": "/usr/bin/systemd-detect-virt",
    "systemctl": "/usr/bin/systemctl",
    "ss": "/usr/bin/ss",
    "python3": "/usr/bin/python3.12",
    "findmnt": "/usr/bin/findmnt",
    "git": "/usr/bin/git",
    "cat": "/usr/bin/cat",
    "find": "/usr/bin/find",
    "ps": "/usr/bin/ps",
}
PROVENANCE_SCHEMAS = {
    "host": "hepta.broker-hard-gate-disposable-host-provenance.v1",
    "source": "hepta.broker-hard-gate-source-provenance.v1",
    "base": "hepta.broker-hard-gate-native-base-provenance.v1",
    "tooling": "hepta.broker-hard-gate-tooling-provenance.v1",
}
COMMON_PROVENANCE_FIELDS = {
    "schema", "decision", "reviewed", "issued_at_ms", "expires_at_ms",
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "body_sha256",
}
PROVENANCE_FIELDS = {
    "host": COMMON_PROVENANCE_FIELDS | {
        "disposable", "destructive_network_drills_authorized", "host_id",
        "boot_id", "virtualization", "console_access", "expected_euid",
        "host_listener_allowlist", "host_listener_allowlist_sha256",
        "host_netns_allowlist", "host_netns_allowlist_sha256",
        "host_firewall_semantic_sha256", "firewall_reload_unit",
        "reachable_forwarders", "ib_binaries", "broker_credentials",
    },
    "source": COMMON_PROVENANCE_FIELDS | {
        "clean", "source_commit", "source_manifest_sha256", "runner_sha256",
    },
    "base": COMMON_PROVENANCE_FIELDS | {
        "host_id", "boot_id", "native_vm_snapshot_sha256",
        "os_release_sha256", "base_review_sha256", "ib_binaries",
        "broker_credentials", "broker_protocol_clients",
    },
    "tooling": COMMON_PROVENANCE_FIELDS | {
        "host_id", "boot_id", "cgroup_v2", "nft_socket_cgroupv2",
        "netns_supported", "systemd_network_namespace_path_supported",
        "binary_sha256",
    },
}
EXPECTED_CHECKS = frozenset({
    "root_disposable_provenance_bound",
    "clean_frozen_source_bound",
    "native_base_and_tooling_provenance_bound",
    "clean_initial_residue",
    "unique_netns_uid_cgroup_topology",
    "kill_switch_engaged_initially",
    "all_roles_denied_initially_all_protected_ports",
    "exact_execution_uid_cgroup_only_positive",
    "agent_gateway_simulator_all_denied",
    "wrong_execution_uid_denied",
    "wrong_execution_cgroup_denied",
    "no_real_ib_binary_credential_protocol_order",
    "forwarder_proxy_process_socket_inventory_zero_or_allowlisted",
    "host_firewall_flush_preserved_isolation",
    "host_firewall_reload_preserved_isolation",
    "execution_restart_preserved_isolation",
    "execution_sigkill_failed_closed_and_recovered",
    "sentinel_restart_preserved_isolation",
    "route_revoke_regrant_failed_closed",
    "interface_revoke_regrant_failed_closed",
    "execution_outbound_revocation_failed_closed",
    "broker_inbound_revocation_failed_closed",
    "bilateral_revocation_regrant_verified",
    "kill_switch_engaged_throughout",
    "final_deny_all",
    "final_namespaces_veth_cgroups_units_residue_zero",
    "host_firewall_restored",
    "final_forwarder_inventory_unchanged",
})
BOUNDARY = {
    "native_disposable_host": True,
    "dedicated_network_namespaces": 5,
    "dedicated_cgroup_v2_slices": 5,
    "protected_ports": list(PROTECTED_PORTS),
    "inert_ipv4_sentinel_listeners": 16,
    "inert_ipv6_sentinel_listeners": 16,
    "controlled_positive_target": "inert-sentinel-only",
    "kill_switch_state": "engaged",
    "host_firewall_flush_reload_drill": True,
    "forwarder_inventory": "exact-zero-or-reviewed-allowlist",
    "real_ib_binaries": 0,
    "real_broker_credentials": 0,
    "broker_protocol_messages": 0,
    "orders": 0,
    "paper_authorized": False,
    "live_authorized": False,
    "mutation_authorized": False,
    "direct_broker_access": False,
}
FORWARDER_PATTERNS = tuple(re.compile(value, re.IGNORECASE) for value in (
    r"\bsocat\b", r"\bhaproxy\b", r"\bsquid\b", r"\btinyproxy\b",
    r"\bredsocks\b", r"\b3proxy\b", r"\bproxychains\b",
    r"\bkubectl\b.*\bport-forward\b", r"\bssh\b.*(?:-[DLR]|-W\s)",
    r"\bibgateway\b", r"\btws(?:\.exe)?\b", r"\bjts\b.*\bjava\b",
))


SENTINEL_CODE = r'''import json,selectors,signal,socket,sys
run_id,v4_text,v6_text,ports_text=sys.argv[1:5]
ports=[int(value) for value in ports_text.split(",")]
selector=selectors.DefaultSelector(); sockets=[]
for family,addresses in ((socket.AF_INET,v4_text.split(";")),(socket.AF_INET6,v6_text.split(";"))):
    for address in addresses:
        for port in ports:
            item=socket.socket(family,socket.SOCK_STREAM)
            item.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            if family == socket.AF_INET6:
                item.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1)
            item.bind((address,port)); item.listen(16); item.setblocking(False)
            selector.register(item,selectors.EVENT_READ); sockets.append(item)
active=True
def stop(_signal,_frame):
    global active; active=False
signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
ready={"run_id":run_id,"ports":ports}
print("HEPTA_INERT_SENTINEL_READY="+json.dumps(
    ready,sort_keys=True,separators=(",",":")),flush=True)
while active:
    for key,_mask in selector.select(0.25):
        connection,_peer=key.fileobj.accept()
        with connection:
            connection.sendall(b"HEPTA-INERT-SENTINEL-V1\n")
for item in sockets: item.close()
'''

PROBE_CODE = r'''import json,os,socket,sys
run_id,role,expected_text,uid_text,gid_text,v4,v6,ports_text=sys.argv[1:9]
expected=expected_text=="success"; ports=[int(value) for value in ports_text.split(",")]
outcomes=[]
for family,address,label in ((socket.AF_INET,v4,"ipv4"),(socket.AF_INET6,v6,"ipv6")):
    for port in ports:
        connected=False; payload_valid=False
        try:
            item=socket.socket(family,socket.SOCK_STREAM); item.settimeout(0.40)
            item.connect((address,port)); connected=True; payload=b""
            while len(payload) < 64 and not payload.endswith(b"\n"):
                chunk=item.recv(64-len(payload))
                if not chunk: break
                payload+=chunk
            payload_valid=payload==b"HEPTA-INERT-SENTINEL-V1\n"; item.close()
        except OSError:
            connected=False; payload_valid=False
        outcomes.append({
            "family":label,"port":port,"connected":connected,
            "payload_valid":payload_valid})
passed=all(
    value["connected"] is expected and value["payload_valid"] is expected
    for value in outcomes)
record={
    "schema":"hepta.broker-network-hard-isolation-probe.v1",
    "run_id":run_id,"role":role,"expected":expected_text,"passed":passed,
    "uid":os.getuid(),"gid":os.getgid(),
    "cgroup":open("/proc/self/cgroup",encoding="ascii").read().strip(),
    "netns_inode":os.readlink("/proc/self/ns/net"),
    "invocation_id":os.environ.get("INVOCATION_ID",""),
    "outcomes":outcomes}
print("HEPTA_BROKER_HARD_ISOLATION_PROBE="+json.dumps(
    record,sort_keys=True,separators=(",",":")),flush=True)
raise SystemExit(0 if passed and os.getuid()==int(uid_text) and os.getgid()==int(gid_text) else 91)
'''


class GateError(RuntimeError):
    """A fail-closed contract or execution error."""


def fail(message: str) -> None:
    raise GateError(message)


def canonical_json(value: object) -> bytes:
    return (json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True) + "\n").encode("ascii")


def body_sha256(value: Mapping[str, object]) -> str:
    body = dict(value)
    body.pop("body_sha256", None)
    return "sha256:" + hashlib.sha256(canonical_json(body)).hexdigest()


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def duplicate_rejecting_pairs(
        pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            fail(f"duplicate JSON key: {key}")
        result[key] = value
    return result


DIRECTORY_IDENTITY_FIELDS = (
    "st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink")


def directory_identity(metadata: os.stat_result) -> dict[str, int]:
    """Return stable directory identity, deliberately excluding timestamps.

    Directory size, mtime, and ctime can change when an unrelated sibling is
    atomically published.  They are not rebound evidence.  Device, inode,
    owner, mode, and link count remain mandatory.
    """

    return {
        field: int(getattr(metadata, field))
        for field in DIRECTORY_IDENTITY_FIELDS
    }


def open_anchored_directory(
        directory: Path, *, expected_uid: int) -> tuple[int, dict[str, int]]:
    if not directory.is_absolute():
        fail("anchored directory path must be absolute")
    flags = (
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0))
    descriptor = os.open("/", flags)
    try:
        parts = directory.parts[1:]
        for index, part in enumerate(parts):
            if part in {"", ".", ".."} or "/" in part:
                fail("unsafe anchored directory component")
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            metadata = os.fstat(descriptor)
            mode = stat.S_IMODE(metadata.st_mode)
            final = index == len(parts) - 1
            sticky_root_ancestor = (
                not final and metadata.st_uid == 0 and
                bool(mode & stat.S_ISVTX))
            if (
                    not stat.S_ISDIR(metadata.st_mode) or
                    metadata.st_nlink < 1 or
                    metadata.st_uid not in {0, expected_uid} or
                    (mode & 0o022 and not sticky_root_ancestor) or
                    (final and metadata.st_uid != expected_uid) or
                    (final and expected_uid == 0 and metadata.st_gid != 0)):
                fail(f"unsafe anchored directory metadata: {directory}")
        metadata = os.fstat(descriptor)
        return descriptor, directory_identity(metadata)
    except BaseException:
        os.close(descriptor)
        raise


def assert_directory_identity(
        descriptor: int, expected: Mapping[str, int]) -> None:
    if directory_identity(os.fstat(descriptor)) != dict(expected):
        fail("anchored parent directory identity changed")


def read_stable_regular_at(
        parent_descriptor: int, name: str, *,
        expected_uid: Optional[int] = None,
        exact_mode: Optional[int] = None,
        exact_nlink: int = 1,
        max_bytes: int = MAX_INPUT) -> tuple[bytes, os.stat_result]:
    if name in {"", ".", ".."} or "/" in name:
        fail("unsafe anchored filename")
    descriptor = os.open(
        name, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent_descriptor)
    try:
        before = os.fstat(descriptor)
        if (
                not stat.S_ISREG(before.st_mode) or
                before.st_nlink != exact_nlink or
                before.st_size < 1 or before.st_size > max_bytes or
                (expected_uid is not None and before.st_uid != expected_uid) or
                (exact_mode is not None and
                 stat.S_IMODE(before.st_mode) != exact_mode)):
            fail(f"unsafe anchored file metadata: {name}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                fail(f"anchored file exceeds bound: {name}")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in fields):
        fail(f"anchored file changed while reading: {name}")
    return b"".join(chunks), before


def read_stable_regular(
        path: Path, *, expected_uid: Optional[int] = None,
        exact_mode: Optional[int] = None,
        exact_nlink: int = 1,
        max_bytes: int = MAX_INPUT) -> tuple[bytes, os.stat_result]:
    descriptor = os.open(
        path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if (
                not stat.S_ISREG(before.st_mode) or
                before.st_nlink != exact_nlink or
                before.st_size < 1 or before.st_size > max_bytes or
                (expected_uid is not None and before.st_uid != expected_uid) or
                (exact_mode is not None and
                 stat.S_IMODE(before.st_mode) != exact_mode)):
            fail(f"unsafe file metadata: {path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                fail(f"file exceeds bound: {path}")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in fields):
        fail(f"file changed while reading: {path}")
    return b"".join(chunks), before


def read_provenance(
        kind: str, path: Path, *, now_ms: int,
        expected_uid: int = 0) -> tuple[dict[str, object], dict[str, object]]:
    if kind not in PROVENANCE_SCHEMAS:
        fail("unknown provenance kind")
    anchored_path = path.absolute()
    parent_descriptor, parent_before = open_anchored_directory(
        anchored_path.parent, expected_uid=expected_uid)
    try:
        raw, info = read_stable_regular_at(
            parent_descriptor, anchored_path.name,
            expected_uid=expected_uid, exact_mode=0o600)
        assert_directory_identity(parent_descriptor, parent_before)
    finally:
        os.close(parent_descriptor)
    try:
        value = json.loads(
            raw.decode("ascii", errors="strict"),
            object_pairs_hook=duplicate_rejecting_pairs)
    except (UnicodeError, json.JSONDecodeError) as error:
        fail(f"invalid {kind} provenance JSON: {error}")
    if not isinstance(value, dict) or set(value) != PROVENANCE_FIELDS[kind]:
        fail(f"{kind} provenance exact-field contract mismatch")
    if raw != canonical_json(value):
        fail(f"{kind} provenance is not canonical JSON")
    if (
            value.get("schema") != PROVENANCE_SCHEMAS[kind] or
            value.get("decision") != "GO" or
            value.get("reviewed") is not True or
            value.get("body_sha256") != body_sha256(value) or
            value.get("paper_authorized") is not False or
            value.get("live_authorized") is not False or
            value.get("mutation_authorized") is not False or
            value.get("direct_broker_access") is not False):
        fail(f"{kind} provenance is not a reviewed non-authorizing GO")
    issued = value.get("issued_at_ms")
    expires = value.get("expires_at_ms")
    if (
            type(issued) is not int or type(expires) is not int or
            issued > now_ms or expires < now_ms or expires <= issued or
            expires - issued > 24 * 60 * 60 * 1000):
        fail(f"{kind} provenance is outside its bounded validity window")
    return value, {
        "path": str(anchored_path),
        "file_sha256": sha256_bytes(raw),
        "body_sha256": value["body_sha256"],
        "issued_at_ms": issued,
        "expires_at_ms": expires,
        "size": len(raw),
        "device": info.st_dev,
        "inode": info.st_ino,
        "mode": format(stat.S_IMODE(info.st_mode), "04o"),
        "nlink": info.st_nlink,
        "uid": info.st_uid,
        "gid": info.st_gid,
        "mtime_ns": info.st_mtime_ns,
        "ctime_ns": info.st_ctime_ns,
        "parent_identity": dict(parent_before),
    }


def validate_string_list(value: object, label: str) -> list[str]:
    if (
            not isinstance(value, list) or
            any(not isinstance(item, str) or not item for item in value) or
            value != sorted(set(value))):
        fail(f"{label} must be a sorted unique string list")
    return list(value)


@dataclass(frozen=True)
class EvidenceBundle:
    documents: Mapping[str, Mapping[str, object]]
    records: Mapping[str, Mapping[str, object]]
    source_commit: str
    source_manifest_sha256: str
    runner_sha256: str
    host_id: str
    boot_id: str
    virtualization: str
    listener_allowlist: tuple[str, ...]
    netns_allowlist: tuple[str, ...]
    firewall_semantic_sha256: str
    firewall_reload_unit: str
    expires_at_ms: int
    revalidate_paths: bool
    provenance_owner_uid: int


def load_evidence(
        *, host_path: Path, source_path: Path, base_path: Path,
        tooling_path: Path, now_ms: int, expected_uid: int = 0,
        runner_path: Optional[Path] = None,
        validate_tool_files: bool = True) -> EvidenceBundle:
    paths = {
        "host": host_path, "source": source_path,
        "base": base_path, "tooling": tooling_path,
    }
    documents: dict[str, dict[str, object]] = {}
    records: dict[str, dict[str, object]] = {}
    for kind, path in paths.items():
        document, record = read_provenance(
            kind, path, now_ms=now_ms, expected_uid=expected_uid)
        documents[kind] = document
        records[kind] = record
    if expected_uid == 0 and any(
            record["uid"] != 0 or record["gid"] != 0 or
            record["parent_identity"]["st_uid"] != 0 or
            record["parent_identity"]["st_gid"] != 0
            for record in records.values()):
        fail("certifying provenance files and parents must be root:root")
    if len({record["file_sha256"] for record in records.values()}) != 4:
        fail("provenance files do not have four independent SHA-256 values")
    if len({record["body_sha256"] for record in records.values()}) != 4:
        fail("provenance bodies do not have four independent SHA-256 values")
    host = documents["host"]
    source = documents["source"]
    base = documents["base"]
    tooling = documents["tooling"]
    if (
            host.get("disposable") is not True or
            host.get("destructive_network_drills_authorized") is not True or
            host.get("console_access") is not True or
            host.get("expected_euid") != 0 or
            host.get("reachable_forwarders") != 0 or
            host.get("ib_binaries") != 0 or
            host.get("broker_credentials") != 0 or
            not isinstance(host.get("host_id"), str) or
            not host.get("host_id") or
            CANONICAL_BOOT_ID.fullmatch(str(host.get("boot_id"))) is None or
            host.get("virtualization") not in {"kvm", "qemu", "vmware"} or
            host.get("firewall_reload_unit") != "nftables.service"):
        fail("disposable-host provenance is not certification eligible")
    listeners = validate_string_list(
        host.get("host_listener_allowlist"), "host listener allowlist")
    netns = validate_string_list(
        host.get("host_netns_allowlist"), "host netns allowlist")
    if (
            host.get("host_listener_allowlist_sha256") !=
            sha256_bytes(canonical_json(listeners)) or
            host.get("host_netns_allowlist_sha256") !=
            sha256_bytes(canonical_json(netns)) or
            CANONICAL_SHA256.fullmatch(
                str(host.get("host_firewall_semantic_sha256"))) is None):
        fail("host inventory digest binding mismatch")
    if (
            base.get("host_id") != host.get("host_id") or
            base.get("boot_id") != host.get("boot_id") or
            base.get("ib_binaries") != 0 or
            base.get("broker_credentials") != 0 or
            base.get("broker_protocol_clients") != 0 or
            any(CANONICAL_SHA256.fullmatch(str(base.get(field))) is None
                for field in (
                    "native_vm_snapshot_sha256", "os_release_sha256",
                    "base_review_sha256"))):
        fail("native-base provenance does not bind the disposable boot")
    if (
            tooling.get("host_id") != host.get("host_id") or
            tooling.get("boot_id") != host.get("boot_id") or
            tooling.get("cgroup_v2") is not True or
            tooling.get("nft_socket_cgroupv2") is not True or
            tooling.get("netns_supported") is not True or
            tooling.get("systemd_network_namespace_path_supported") is not True):
        fail("tooling provenance lacks required kernel enforcement features")
    binary_hashes = tooling.get("binary_sha256")
    if (
            not isinstance(binary_hashes, dict) or
            set(binary_hashes) != set(TOOL_PATHS) or
            any(CANONICAL_SHA256.fullmatch(str(value)) is None
                for value in binary_hashes.values())):
        fail("tooling binary digest map mismatch")
    if validate_tool_files:
        for name, path_text in TOOL_PATHS.items():
            raw, metadata = read_stable_regular(Path(path_text), expected_uid=0)
            if (
                    stat.S_IMODE(metadata.st_mode) & 0o022 or
                    not stat.S_ISREG(metadata.st_mode) or
                    not metadata.st_mode & stat.S_IXUSR or
                    sha256_bytes(raw) != binary_hashes[name]):
                fail(f"reviewed tooling binary drift: {name}")
    if (
            source.get("clean") is not True or
            CANONICAL_COMMIT.fullmatch(str(source.get("source_commit"))) is None or
            CANONICAL_SHA256.fullmatch(
                str(source.get("source_manifest_sha256"))) is None or
            CANONICAL_SHA256.fullmatch(str(source.get("runner_sha256"))) is None):
        fail("source provenance is not a clean frozen lineage")
    actual_runner = runner_path or Path(__file__).resolve(strict=True)
    runner_raw, _runner_metadata = read_stable_regular(actual_runner)
    if sha256_bytes(runner_raw) != source.get("runner_sha256"):
        fail("runner SHA-256 does not match source provenance")
    return EvidenceBundle(
        documents=documents,
        records=records,
        source_commit=str(source["source_commit"]),
        source_manifest_sha256=str(source["source_manifest_sha256"]),
        runner_sha256=str(source["runner_sha256"]),
        host_id=str(host["host_id"]),
        boot_id=str(host["boot_id"]),
        virtualization=str(host["virtualization"]),
        listener_allowlist=tuple(listeners),
        netns_allowlist=tuple(netns),
        firewall_semantic_sha256=str(host["host_firewall_semantic_sha256"]),
        firewall_reload_unit=str(host["firewall_reload_unit"]),
        expires_at_ms=min(
            int(document["expires_at_ms"]) for document in documents.values()),
        revalidate_paths=True,
        provenance_owner_uid=expected_uid,
    )


@dataclass(frozen=True)
class CommandSpec:
    action: str
    argv: tuple[str, ...]
    stdin: bytes = b""
    timeout: int = 60
    accepted_returncodes: tuple[int, ...] = (0,)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    duration_ms: int


class Executor(Protocol):
    def run(self, spec: CommandSpec) -> CommandResult:
        """Execute exactly one bounded command."""


class DisabledExecutor:
    """The default executor: every command is denied."""

    def run(self, spec: CommandSpec) -> CommandResult:
        del spec
        fail("native command execution is disabled; explicit --run is required")


class ProductionExecutor:
    """Bounded, no-shell native command executor."""

    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = enabled

    def run(self, spec: CommandSpec) -> CommandResult:
        if not self.enabled:
            fail("production executor was not explicitly enabled")
        started = time.monotonic_ns()
        process = subprocess.Popen(
            list(spec.argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, env=COMMAND_ENV, close_fds=True,
            start_new_session=True)
        try:
            stdout_raw, _unused = process.communicate(
                input=spec.stdin, timeout=spec.timeout)
        except BaseException:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=3)
                except (OSError, subprocess.TimeoutExpired):
                    if process.poll() is None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except OSError:
                            pass
                        process.wait()
            raise
        if len(stdout_raw) > MAX_OUTPUT:
            fail(f"bounded command output exceeded: {spec.action}")
        try:
            output = stdout_raw.decode("utf-8", errors="strict")
        except UnicodeError:
            fail(f"non-UTF-8 command output: {spec.action}")
        duration = max(0, (time.monotonic_ns() - started) // 1_000_000)
        return CommandResult(process.returncode, output, duration)


@dataclass(frozen=True)
class Topology:
    run_id: str
    short: str
    namespaces: Mapping[str, str]
    slices: Mapping[str, str]
    units: Mapping[str, str]
    client_ifaces: Mapping[str, str]
    broker_ifaces: Mapping[str, str]
    ipv4: Mapping[str, tuple[str, str]]
    ipv6: Mapping[str, tuple[str, str]]


def make_topology(run_id: str) -> Topology:
    if CANONICAL_RUN_ID.fullmatch(run_id) is None:
        fail("run_id must be 32 lowercase hexadecimal characters")
    short = run_id[:10]
    namespaces = {role: f"hpn-{run_id}-{role}" for role in ROLES}
    # A systemd slice name with '-' creates a hierarchy.  These names contain
    # no hyphen so each unique gate slice is level 1 below cgroup v2 root,
    # matching the exact nft ``socket cgroupv2 level 1`` contract.
    slices = {role: f"heptahn{run_id}{role}.slice" for role in ROLES}
    units = {
        "sentinel": f"hepta-hn-{run_id}-sentinel.service",
        "execution_probe": f"hepta-hn-{run_id}-exec-probe.service",
        "execution_wrong_uid": f"hepta-hn-{run_id}-wrong-uid.service",
        "execution_wrong_cgroup": f"hepta-hn-{run_id}-wrong-cgroup.service",
        "gateway_probe": f"hepta-hn-{run_id}-gateway-probe.service",
        "agent_probe": f"hepta-hn-{run_id}-agent-probe.service",
        "simulator_probe": f"hepta-hn-{run_id}-sim-probe.service",
        "execution_anchor": f"hepta-hn-{run_id}-exec-anchor.service",
    }
    client_ifaces: dict[str, str] = {}
    broker_ifaces: dict[str, str] = {}
    ipv4: dict[str, tuple[str, str]] = {}
    ipv6: dict[str, tuple[str, str]] = {}
    for index, role in enumerate(CLIENT_ROLES, start=1):
        client_ifaces[role] = f"hc{short}{index}"
        broker_ifaces[role] = f"hb{short}{index}"
        ipv4[role] = (f"198.18.{index}.1", f"198.18.{index}.2")
        ipv6[role] = (f"fd42:{short[:4]}:{index}::1", f"fd42:{short[:4]}:{index}::2")
    values = list(namespaces.values()) + list(slices.values()) + list(units.values())
    if len(values) != len(set(values)):
        fail("topology names are not unique")
    if any(len(value) > 15 for value in (*client_ifaces.values(), *broker_ifaces.values())):
        fail("veth interface name exceeds Linux IFNAMSIZ")
    return Topology(
        run_id, short, namespaces, slices, units,
        client_ifaces, broker_ifaces, ipv4, ipv6)


def ports_csv() -> str:
    return ",".join(str(value) for value in PROTECTED_PORTS)


def nft_set(values: Sequence[object]) -> str:
    return "{ " + ", ".join(str(value) for value in values) + " }"


def broker_nft(topology: Topology, *, forward: bool) -> bytes:
    _broker_v4, execution_v4 = topology.ipv4["execution"]
    _broker_v6, execution_v6 = topology.ipv6["execution"]
    interface = topology.broker_ifaces["execution"]
    rules = [
        "flush ruleset",
        "table inet hepta_hard {",
        " chain input { type filter hook input priority -200; policy drop;",
        "  iifname lo accept",
        "  ct state established,related accept",
    ]
    if forward:
        rules.extend((
            f"  iifname {interface} ip saddr {execution_v4} "
            f"tcp dport {nft_set(PROTECTED_PORTS)} accept",
            f"  iifname {interface} ip6 saddr {execution_v6} "
            f"tcp dport {nft_set(PROTECTED_PORTS)} accept",
        ))
    rules.extend((
        f"  tcp dport {nft_set(PROTECTED_PORTS)} reject with tcp reset",
        " }",
        " chain output { type filter hook output priority -200; policy accept; }",
        "}",
    ))
    return ("\n".join(rules) + "\n").encode("ascii")


def client_nft(
        topology: Topology, role: str, *, execution_forward: bool) -> bytes:
    rules = [
        "flush ruleset", "table inet hepta_hard {",
        " chain output { type filter hook output priority -200; policy drop;",
        "  oifname lo accept", "  ct state established,related accept",
    ]
    if role == "execution" and execution_forward:
        broker_v4, _client_v4 = topology.ipv4[role]
        broker_v6, _client_v6 = topology.ipv6[role]
        interface = topology.client_ifaces[role]
        slice_name = topology.slices[role]
        rules.extend((
            f"  meta skuid {UIDS[role]} socket cgroupv2 level 1 "
            f'"{slice_name}" oifname {interface} ip daddr {broker_v4} '
            f"tcp dport {nft_set(PROTECTED_PORTS)} accept",
            f"  meta skuid {UIDS[role]} socket cgroupv2 level 1 "
            f'"{slice_name}" oifname {interface} ip6 daddr {broker_v6} '
            f"tcp dport {nft_set(PROTECTED_PORTS)} accept",
        ))
    rules.extend((
        f"  tcp dport {nft_set(PROTECTED_PORTS)} reject with tcp reset",
        " }", " chain input { type filter hook input priority -200; policy accept; }",
        "}",
    ))
    return ("\n".join(rules) + "\n").encode("ascii")


def normalize_nft(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: normalize_nft(item) for key, item in sorted(value.items())
            if key not in {"handle", "packets", "bytes"}
        }
    if isinstance(value, list):
        return [normalize_nft(item) for item in value]
    return value


def nft_semantic_sha256(output: str) -> str:
    try:
        value = json.loads(output, object_pairs_hook=duplicate_rejecting_pairs)
    except json.JSONDecodeError as error:
        fail(f"host nft JSON is invalid: {error}")
    return sha256_bytes(canonical_json(normalize_nft(value)))


def nft_ruleset_is_empty(output: str) -> bool:
    try:
        value = json.loads(output, object_pairs_hook=duplicate_rejecting_pairs)
    except json.JSONDecodeError as error:
        fail(f"host nft JSON is invalid: {error}")
    if not isinstance(value, dict) or set(value) != {"nftables"}:
        return False
    records = value.get("nftables")
    return (
        isinstance(records, list) and
        all(isinstance(item, dict) and set(item) == {"metainfo"}
            for item in records))


def stable_lines(output: str) -> list[str]:
    return sorted(line.rstrip() for line in output.splitlines() if line.rstrip())


def validate_process_inventory(output: str) -> None:
    for line in output.splitlines():
        if any(pattern.search(line) for pattern in FORWARDER_PATTERNS):
            fail("reachable forwarder, broker client, or proxy process detected")


def validate_reserved_uids_unused(output: str) -> None:
    reserved = set(UIDS.values())
    for line in output.splitlines():
        fields = line.split(None, 3)
        if len(fields) >= 2:
            try:
                uid = int(fields[1])
            except ValueError:
                fail("host process inventory has a malformed UID")
            if uid in reserved:
                fail("a hard-gate dedicated UID is already in use")


def validate_listener_inventory(
        output: str, allowlist: Sequence[str]) -> list[str]:
    lines = stable_lines(output)
    if lines != list(allowlist):
        fail("host listener inventory differs from reviewed exact allowlist")
    protected = tuple(f":{port}" for port in PROTECTED_PORTS)
    if any(token in line for line in lines for token in protected):
        fail("host has a protected broker-port listener")
    return lines


def validate_netns_inventory(output: str, allowlist: Sequence[str]) -> list[str]:
    names = sorted(line.split()[0] for line in output.splitlines() if line.strip())
    if names != list(allowlist):
        fail("initial host network namespace inventory drifted")
    if any(name.startswith("hpn-") for name in names):
        fail("stale Hepta hard-gate namespace exists")
    return names


def validate_kill_switch(
        path: Path, *, expected_uid: Optional[int] = None) -> dict[str, object]:
    if expected_uid is None:
        expected_uid = os.geteuid()
    anchored_path = path.absolute()
    parent_descriptor, parent_before = open_anchored_directory(
        anchored_path.parent, expected_uid=expected_uid)
    try:
        raw, metadata = read_stable_regular_at(
            parent_descriptor, anchored_path.name, exact_mode=0o400)
        assert_directory_identity(parent_descriptor, parent_before)
    finally:
        os.close(parent_descriptor)
    if raw != b"engaged\n":
        fail("hard-gate kill switch is not engaged")
    return {
        "state": "engaged", "sha256": sha256_bytes(raw),
        "device": metadata.st_dev, "inode": metadata.st_ino,
        "mode": "0400",
        "parent_identity": parent_before,
    }


def write_kill_switch(
        runtime: Path, *, expected_uid: Optional[int] = None,
        ) -> tuple[Path, dict[str, object]]:
    runtime.mkdir(mode=0o700, parents=False, exist_ok=False)
    path = runtime / "kill-switch"
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0), 0o400)
    try:
        os.write(descriptor, b"engaged\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(path, 0o400)
    directory = os.open(runtime, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return path, validate_kill_switch(path, expected_uid=expected_uid)


def systemd_properties(
        topology: Topology, slice_role: str, uid: int, *,
        network_role: Optional[str] = None) -> list[str]:
    if network_role is None:
        network_role = slice_role
    return [
        f"--slice={topology.slices[slice_role]}",
        f"--property=User={uid}", f"--property=Group={uid}",
        f"--property=NetworkNamespacePath=/run/netns/{topology.namespaces[network_role]}",
        "--property=NoNewPrivileges=yes", "--property=PrivateTmp=yes",
        "--property=ProtectSystem=strict", "--property=ProtectHome=yes",
        "--property=RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX",
        "--property=CapabilityBoundingSet=", "--property=AmbientCapabilities=",
        "--property=RestrictNamespaces=yes",
        "--property=SystemCallArchitectures=native", "--property=UMask=0077",
        "--property=Restart=no", "--property=KillMode=control-group",
        "--property=TimeoutStopSec=10s",
    ]


def exact_report_fields() -> set[str]:
    return {
        "schema", "run_id", "decision", "passed", "certification_ready",
        "rehearsal_passed",
        "execution_mode",
        "body_sha256",
        "scope", "started_at_ms", "completed_at_ms", "expires_at_ms",
        "duration_ms",
        "lineage", "provenance", "environment", "topology", "phases",
        "environment_review_closure",
        "checks", "exposure", "cleanup", "boundary", "failure",
        "paper_test_admission_authorized", "paper_authorized",
        "live_authorized", "mutation_authorized", "direct_broker_access",
        "order_submission_authorized",
    }


def topology_as_record(topology: Topology) -> dict[str, object]:
    links = {}
    for role in CLIENT_ROLES:
        links[role] = {
            "broker_interface": topology.broker_ifaces[role],
            "client_interface": topology.client_ifaces[role],
            "broker_ipv4": topology.ipv4[role][0],
            "client_ipv4": topology.ipv4[role][1],
            "broker_ipv6": topology.ipv6[role][0],
            "client_ipv6": topology.ipv6[role][1],
        }
    return {
        "namespaces": dict(topology.namespaces),
        "slices": dict(topology.slices),
        "units": dict(topology.units),
        "uids": dict(UIDS), "links": links,
    }


def validate_report(report: Mapping[str, object]) -> None:
    if set(report) != exact_report_fields():
        fail("hard-isolation report exact-field contract mismatch")
    checks = report.get("checks")
    run_id = str(report.get("run_id"))
    lineage = report.get("lineage")
    provenance = report.get("provenance")
    environment = report.get("environment")
    phases = report.get("phases")
    exposure = report.get("exposure")
    cleanup = report.get("cleanup")
    environment_review = report.get("environment_review_closure")
    started = report.get("started_at_ms")
    completed = report.get("completed_at_ms")
    duration = report.get("duration_ms")
    expires = report.get("expires_at_ms")
    if (
            report.get("schema") != SCHEMA or
            CANONICAL_RUN_ID.fullmatch(run_id) is None or
            report.get("decision") not in {
                "GO", "NO_GO", "REHEARSAL_ONLY"} or
            type(report.get("passed")) is not bool or
            type(report.get("rehearsal_passed")) is not bool or
            report.get("certification_ready") is not report.get("passed") or
            report.get("execution_mode") not in {
                "NATIVE_PRODUCTION", "INJECTED_REHEARSAL"} or
            report.get("body_sha256") != body_sha256(report) or
            report.get("scope") !=
            "DEDICATED_BROKER_NETNS_HARD_CERTIFICATION_GATE" or
            type(started) is not int or type(completed) is not int or
            type(expires) is not int or type(duration) is not int or
            completed < started or expires <= completed or
            duration != completed - started or
            not isinstance(checks, dict) or set(checks) != EXPECTED_CHECKS or
            any(type(value) is not bool for value in checks.values()) or
            report.get("boundary") != BOUNDARY or
            any(report.get(field) is not False for field in (
                "paper_test_admission_authorized", "paper_authorized",
                "live_authorized", "mutation_authorized",
                "direct_broker_access", "order_submission_authorized"))):
        fail("hard-isolation report semantic contract mismatch")
    if (
            not isinstance(lineage, dict) or set(lineage) != {
                "host_id", "boot_id", "source_commit",
                "source_manifest_sha256", "runner_sha256"} or
            not isinstance(lineage.get("host_id"), str) or
            not lineage.get("host_id") or
            CANONICAL_BOOT_ID.fullmatch(str(lineage.get("boot_id"))) is None or
            CANONICAL_COMMIT.fullmatch(
                str(lineage.get("source_commit"))) is None or
            CANONICAL_SHA256.fullmatch(
                str(lineage.get("source_manifest_sha256"))) is None or
            CANONICAL_SHA256.fullmatch(
                str(lineage.get("runner_sha256"))) is None):
        fail("hard-isolation report lineage contract mismatch")
    if not isinstance(provenance, dict) or set(provenance) != {
            "host", "source", "base", "tooling"}:
        fail("hard-isolation report provenance cardinality mismatch")
    for name, record in provenance.items():
        if (
                not isinstance(record, dict) or set(record) != {
                    "path", "file_sha256", "body_sha256", "size",
                    "issued_at_ms", "expires_at_ms",
                    "device", "inode", "mode", "nlink", "uid", "gid",
                    "mtime_ns", "ctime_ns", "parent_identity"} or
                not isinstance(record.get("path"), str) or
                not str(record.get("path")).startswith("/") or
                CANONICAL_SHA256.fullmatch(
                    str(record.get("file_sha256"))) is None or
                CANONICAL_TAGGED_SHA256.fullmatch(
                    str(record.get("body_sha256", ""))) is None or
                type(record.get("issued_at_ms")) is not int or
                type(record.get("expires_at_ms")) is not int or
                record["issued_at_ms"] > started or
                record["expires_at_ms"] < expires or
                type(record.get("size")) is not int or record["size"] < 1 or
                type(record.get("device")) is not int or record["device"] < 0 or
                type(record.get("inode")) is not int or record["inode"] < 1 or
                record.get("mode") != "0600" or record.get("nlink") != 1 or
                type(record.get("uid")) is not int or record["uid"] < 0 or
                type(record.get("gid")) is not int or record["gid"] < 0 or
                type(record.get("mtime_ns")) is not int or
                type(record.get("ctime_ns")) is not int or
                not isinstance(record.get("parent_identity"), dict) or
                set(record["parent_identity"]) != set(DIRECTORY_IDENTITY_FIELDS) or
                any(type(value) is not int
                    for value in record["parent_identity"].values())):
            fail(f"hard-isolation report {name} provenance record mismatch")
    if len({record["file_sha256"] for record in provenance.values()}) != 4:
        fail("hard-isolation report provenance SHA values are not independent")
    if len({record["body_sha256"] for record in provenance.values()}) != 4:
        fail("hard-isolation report provenance body SHA values are not independent")
    if min(record["expires_at_ms"] for record in provenance.values()) != expires:
        expected_expiry = min(record["expires_at_ms"] for record in provenance.values())
        if isinstance(environment_review, dict):
            expected_expiry = min(
                expected_expiry, int(environment_review.get("expires_at_ms", 0)))
        if expected_expiry != expires:
            fail("hard-isolation report expiry does not bind evidence minimum")
    if report.get("passed"):
        try:
            ROOT_REVIEW.validate_verification_record(
                environment_review, now_ms=int(completed))
        except ROOT_REVIEW.ReviewClosureError as error:
            raise GateError(str(error)) from error
        assert isinstance(environment_review, dict)
        if (
                environment_review.get("source_commit") !=
                    lineage.get("source_commit") or
                environment_review.get("expires_at_ms", 0) < expires):
            fail("hard-isolation GO is not bound to environment review closure")
    elif environment_review is not None:
        try:
            ROOT_REVIEW.validate_verification_record(
                environment_review, now_ms=int(completed))
        except ROOT_REVIEW.ReviewClosureError as error:
            raise GateError(str(error)) from error
    if report.get("passed") and any(
            record["uid"] != 0 or record["gid"] != 0 or
            record["parent_identity"]["st_uid"] != 0 or
            record["parent_identity"]["st_gid"] != 0
            for record in provenance.values()):
        fail("certifying report provenance is not root:root anchored")
    expected_environment_fields = {
        "boot_id", "cgroup_filesystem", "source_commit",
        "virtualization",
        "source_manifest_sha256", "initial_listener_inventory_sha256",
        "initial_netns_inventory_sha256",
        "initial_firewall_semantic_sha256",
    }
    if (
            not isinstance(environment, dict) or
            set(environment) != expected_environment_fields):
        fail("hard-isolation report environment exact-field mismatch")
    if report.get("rehearsal_passed") and (
            environment.get("boot_id") != lineage.get("boot_id") or
            environment.get("source_commit") != lineage.get("source_commit") or
            environment.get("source_manifest_sha256") !=
            lineage.get("source_manifest_sha256") or
            environment.get("cgroup_filesystem") != "cgroup2" or
            environment.get("virtualization") not in {"kvm", "qemu", "vmware"} or
            any(CANONICAL_SHA256.fullmatch(str(environment.get(field))) is None
                for field in (
                    "initial_listener_inventory_sha256",
                    "initial_netns_inventory_sha256",
                    "initial_firewall_semantic_sha256"))):
        fail("passing hard-isolation report environment is incomplete")
    if report.get("topology") != topology_as_record(make_topology(run_id)):
        fail("hard-isolation report topology does not match run_id")
    if (
            not isinstance(phases, list) or
            any(not isinstance(item, dict) or set(item) != {
                "sequence", "name", "detail", "kill_switch_state"}
                for item in phases) or
            [item["sequence"] for item in phases] !=
            list(range(1, len(phases) + 1)) or
            any(item["kill_switch_state"] not in {
                "not-created", "engaged", "engaged-finally-then-removed"}
                for item in phases)):
        fail("hard-isolation report phase sequence mismatch")
    if report.get("rehearsal_passed") and (
            [item["name"] for item in phases] != [
                "preflight", "setup", "fault-and-revocation-drills",
                "final-deny-all", "cleanup"] or
            [item["kill_switch_state"] for item in phases] != [
                "not-created", "engaged", "engaged", "engaged",
                "engaged-finally-then-removed"]):
        fail("passing report phase/kill-switch history mismatch")
    if (
            not isinstance(exposure, dict) or set(exposure) != {
                "host_listener_allowlist_count", "reachable_forwarders",
                "ib_binaries", "broker_credentials",
                "broker_protocol_messages", "orders",
                "command_transcript_sha256", "command_count", "kill_switch"} or
            any(exposure.get(field) != 0 for field in (
                "reachable_forwarders", "ib_binaries", "broker_credentials",
                "broker_protocol_messages", "orders")) or
            type(exposure.get("host_listener_allowlist_count")) is not int or
            exposure["host_listener_allowlist_count"] < 0 or
            type(exposure.get("command_count")) is not int or
            exposure["command_count"] < 0 or
            CANONICAL_SHA256.fullmatch(
                str(exposure.get("command_transcript_sha256"))) is None):
        fail("hard-isolation report exposure contract mismatch")
    kill_switch = exposure.get("kill_switch")
    if report.get("rehearsal_passed") and (
            not isinstance(kill_switch, dict) or set(kill_switch) != {
                "state", "sha256", "device", "inode", "mode",
                "parent_identity"} or
            kill_switch.get("state") != "engaged" or
            kill_switch.get("mode") != "0400" or
            CANONICAL_SHA256.fullmatch(
                str(kill_switch.get("sha256"))) is None or
            type(kill_switch.get("device")) is not int or
            type(kill_switch.get("inode")) is not int or
            not isinstance(kill_switch.get("parent_identity"), dict) or
            set(kill_switch["parent_identity"]) !=
            set(DIRECTORY_IDENTITY_FIELDS) or
            any(type(value) is not int
                for value in kill_switch["parent_identity"].values())):
        fail("passing report lacks bound engaged kill-switch evidence")
    if (
            not isinstance(cleanup, dict) or set(cleanup) != {
                "attempted", "complete", "firewall_reload_attempted",
                "firewall_restored", "residue"} or
            any(type(cleanup.get(field)) is not bool for field in (
                "attempted", "complete", "firewall_reload_attempted",
                "firewall_restored")) or
            not isinstance(cleanup.get("residue"), list) or
            any(not isinstance(item, str) for item in cleanup["residue"])):
        fail("hard-isolation report cleanup contract mismatch")
    if report.get("rehearsal_passed") and (
            cleanup.get("attempted") is not True or
            cleanup.get("complete") is not True or
            cleanup.get("firewall_restored") is not True or
            cleanup.get("residue") != []):
        fail("passing report has incomplete cleanup")
    failure = report.get("failure")
    if failure is not None and (
            not isinstance(failure, str) or not failure or len(failure) > 2048):
        fail("hard-isolation report failure field is malformed")
    semantic_pass = all(checks.values()) and report.get("failure") is None
    if (
            report.get("rehearsal_passed") is not semantic_pass or
            (report.get("passed") and not semantic_pass) or
            (report.get("passed") and
             report.get("execution_mode") != "NATIVE_PRODUCTION") or
            (report.get("execution_mode") == "INJECTED_REHEARSAL" and
             report.get("passed")) or
            report.get("decision") != (
                "GO" if report.get("passed") else
                "REHEARSAL_ONLY" if semantic_pass else "NO_GO")):
        fail("hard-isolation report decision/check mismatch")


def write_report_no_replace(path: Path, report: Mapping[str, object]) -> None:
    validate_report(report)
    if path.name in {"", ".", ".."} or "/" in path.name:
        fail("unsafe report filename")
    raw = canonical_json(report)
    if len(raw) > MAX_REPORT:
        fail("hard-isolation report exceeds bound")
    parent_descriptor, parent_before = open_anchored_directory(
        path.parent.absolute(), expected_uid=os.geteuid())
    descriptor = -1
    temporary_name = f".{path.name}.{uuid.uuid4().hex}.tmp"
    linked = False
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0),
            0o600, dir_fd=parent_descriptor)
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                fail("short report write")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.link(
            temporary_name, path.name,
            src_dir_fd=parent_descriptor, dst_dir_fd=parent_descriptor,
            follow_symlinks=False)
        linked = True
        os.fsync(parent_descriptor)
        reopened, metadata = read_stable_regular_at(
            parent_descriptor, path.name, exact_mode=0o600, exact_nlink=2)
        if reopened != raw or metadata.st_nlink != 2:
            fail("published report reopen verification failed")
        assert_directory_identity(parent_descriptor, parent_before)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=parent_descriptor)
        except FileNotFoundError:
            pass
        if linked:
            os.fsync(parent_descriptor)
        try:
            assert_directory_identity(parent_descriptor, parent_before)
        finally:
            os.close(parent_descriptor)
    parent_descriptor, parent_after = open_anchored_directory(
        path.parent.absolute(), expected_uid=os.geteuid())
    try:
        if parent_after != parent_before:
            fail("report parent rebound after publication")
        reopened, metadata = read_stable_regular_at(
            parent_descriptor, path.name, exact_mode=0o600)
        if reopened != raw or metadata.st_nlink != 1:
            fail("final report link-count verification failed")
    finally:
        os.close(parent_descriptor)


class HardIsolationGate:
    """Stateful native gate whose only external surface is an Executor."""

    def __init__(
            self, evidence: EvidenceBundle, executor: Executor, *,
            run_id: Optional[str] = None, runtime_parent: Path = Path("/run"),
            now_ms: Optional[int] = None,
            environment_review_session: Optional[
                ROOT_REVIEW.VerificationSession] = None) -> None:
        self.evidence = evidence
        self.executor = executor
        self.filesystem_owner_uid = os.geteuid()
        self.certification_capable = (
            type(executor) is ProductionExecutor and executor.enabled and
            evidence.revalidate_paths and evidence.provenance_owner_uid == 0 and
            self.filesystem_owner_uid == 0 and
            environment_review_session is not None)
        self.environment_review_session = environment_review_session
        self.environment_review_record: Optional[dict[str, object]] = None
        self.run_id = run_id or uuid.uuid4().hex
        self.topology = make_topology(self.run_id)
        if not runtime_parent.is_absolute():
            fail("runtime parent must be an absolute anchored directory")
        self.runtime = runtime_parent / f"hepta-broker-hard-gate-{self.run_id}"
        self.started_at_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        self.checks = {name: False for name in EXPECTED_CHECKS}
        self.phases: list[dict[str, object]] = []
        self.transcript: list[dict[str, object]] = []
        self.environment: dict[str, object] = {
            "boot_id": None,
            "cgroup_filesystem": None,
            "virtualization": None,
            "source_commit": None,
            "source_manifest_sha256": None,
            "initial_listener_inventory_sha256": None,
            "initial_netns_inventory_sha256": None,
            "initial_firewall_semantic_sha256": None,
        }
        self.cleanup: dict[str, object] = {
            "attempted": False, "complete": False,
            "firewall_reload_attempted": False,
            "firewall_restored": False, "residue": [],
        }
        self.kill_switch_path: Optional[Path] = None
        self.kill_switch_evidence: Optional[dict[str, object]] = None
        self.firewall_flushed = False
        self.firewall_drill_started = False
        self.resource_mutation_started = False
        self.failure: Optional[str] = None
        self.first_execution_invocation: Optional[str] = None
        self.first_sentinel_invocation: Optional[str] = None

    def command(
            self, action: str, argv: Sequence[str], *, stdin: bytes = b"",
            timeout: int = 60,
            accepted_returncodes: tuple[int, ...] = (0,)) -> CommandResult:
        spec = CommandSpec(
            action, tuple(argv), stdin, timeout, accepted_returncodes)
        result = self.executor.run(spec)
        if len(result.stdout.encode("utf-8", errors="strict")) > MAX_OUTPUT:
            fail(f"executor returned oversized output: {action}")
        self.transcript.append({
            "sequence": len(self.transcript) + 1,
            "action": action,
            "argv_sha256": sha256_bytes(canonical_json(list(spec.argv))),
            "stdin_sha256": sha256_bytes(spec.stdin),
            "returncode": result.returncode,
            "stdout_sha256": sha256_bytes(result.stdout.encode("utf-8")),
            "stdout_size": len(result.stdout.encode("utf-8")),
            "duration_ms": result.duration_ms,
        })
        if result.returncode not in accepted_returncodes:
            fail(f"command failed closed: {action} rc={result.returncode}")
        return result

    def phase(self, name: str, detail: Mapping[str, object]) -> None:
        if self.kill_switch_evidence is None:
            kill_switch_state = "not-created"
        elif name == "cleanup":
            kill_switch_state = "engaged-finally-then-removed"
        else:
            kill_switch_state = "engaged"
        self.phases.append({
            "sequence": len(self.phases) + 1,
            "name": name,
            "detail": dict(detail),
            "kill_switch_state": kill_switch_state,
        })

    def assert_evidence_fresh(self) -> None:
        now_ms = int(time.time() * 1000)
        if now_ms > self.evidence.expires_at_ms:
            fail("reviewed certification provenance expired during the gate")
        if self.evidence.revalidate_paths:
            for kind in ("host", "source", "base", "tooling"):
                record = self.evidence.records[kind]
                path_value = record.get("path")
                if not isinstance(path_value, str) or not path_value:
                    fail("provenance path record is missing")
                document, reopened = read_provenance(
                    kind, Path(path_value), now_ms=now_ms,
                    expected_uid=self.evidence.provenance_owner_uid)
                if (
                        document != self.evidence.documents[kind] or
                        reopened != record):
                    fail(f"{kind} provenance drifted during the native gate")
            tooling_hashes = self.evidence.documents["tooling"]["binary_sha256"]
            if not isinstance(tooling_hashes, dict):
                fail("tooling provenance digest map disappeared")
            for name, path_text in TOOL_PATHS.items():
                raw, metadata = read_stable_regular(
                    Path(path_text), expected_uid=0)
                if (
                        stat.S_IMODE(metadata.st_mode) & 0o022 or
                        sha256_bytes(raw) != tooling_hashes.get(name)):
                    fail(f"reviewed tooling drifted during gate: {name}")
            runner_raw, _runner_metadata = read_stable_regular(
                Path(__file__).resolve(strict=True))
            if sha256_bytes(runner_raw) != self.evidence.documents[
                    "source"]["runner_sha256"]:
                fail("runner source drifted during the native gate")

    def verify_host_exposure_inventory(self, label: str) -> None:
        listeners = self.command(
            f"inventory.{label}.listeners",
            [TOOL_PATHS["ss"], "-H", "-lntup"]).stdout
        processes = self.command(
            f"inventory.{label}.processes",
            [TOOL_PATHS["ps"], "-eo", "pid=,uid=,comm=,args="]).stdout
        validate_listener_inventory(
            listeners, self.evidence.listener_allowlist)
        validate_process_inventory(processes)

    def assert_kill_switch(self) -> None:
        if self.kill_switch_path is None:
            fail("kill switch is absent")
        current = validate_kill_switch(
            self.kill_switch_path, expected_uid=self.filesystem_owner_uid)
        if self.kill_switch_evidence is None:
            self.kill_switch_evidence = current
        elif current != self.kill_switch_evidence:
            fail("kill-switch inode or content drifted")

    def preflight(self) -> None:
        self.assert_evidence_fresh()
        if os.geteuid() != 0:
            fail("native hard-isolation execution requires euid 0")
        boot_id = self.command(
            "preflight.boot-id", [TOOL_PATHS["cat"], "/proc/sys/kernel/random/boot_id"]
        ).stdout.strip()
        if boot_id != self.evidence.boot_id:
            fail("live boot_id differs from reviewed disposable-host provenance")
        cgroup_fs = self.command(
            "preflight.cgroup-fs",
            [TOOL_PATHS["findmnt"], "-n", "-o", "FSTYPE", "/sys/fs/cgroup"]
        ).stdout.strip()
        if cgroup_fs != "cgroup2":
            fail("native gate requires unified cgroup v2")
        virtualization = self.command(
            "preflight.virtualization",
            [TOOL_PATHS["systemd_detect_virt"], "--vm"]
        ).stdout.strip()
        if virtualization != self.evidence.virtualization:
            fail("live virtualization differs from disposable-host provenance")
        commit = self.command(
            "preflight.git-head",
            [TOOL_PATHS["git"], "-C", str(Path(__file__).resolve().parents[1]),
             "rev-parse", "HEAD"]).stdout.strip()
        if commit != self.evidence.source_commit:
            fail("live source commit differs from reviewed source provenance")
        status = self.command(
            "preflight.git-clean",
            [TOOL_PATHS["git"], "-C", str(Path(__file__).resolve().parents[1]),
             "status", "--porcelain=v1", "--untracked-files=all"]).stdout
        if status != "":
            fail("source worktree is not clean")
        netns_output = self.command(
            "inventory.initial.netns", [TOOL_PATHS["ip"], "netns", "list"]
        ).stdout
        validate_netns_inventory(netns_output, self.evidence.netns_allowlist)
        listeners_output = self.command(
            "inventory.initial.listeners",
            [TOOL_PATHS["ss"], "-H", "-lntup"]).stdout
        listeners = validate_listener_inventory(
            listeners_output, self.evidence.listener_allowlist)
        processes_output = self.command(
            "inventory.initial.processes",
            [TOOL_PATHS["ps"], "-eo", "pid=,uid=,comm=,args="]).stdout
        validate_process_inventory(processes_output)
        validate_reserved_uids_unused(processes_output)
        links_output = self.command(
            "inventory.initial.links",
            [TOOL_PATHS["ip"], "-o", "link", "show"]).stdout
        if re.search(r"\b(?:hc|hb)[0-9a-f]{6,10}[1-4]\b", links_output):
            fail("stale gate veth exists")
        units_output = self.command(
            "inventory.initial.units",
            [TOOL_PATHS["systemctl"], "list-units", "--all", "--no-legend",
             "--plain", "hepta-hn-*", "heptahn*.slice"]).stdout
        cgroups_output = self.command(
            "inventory.initial.cgroups",
            [TOOL_PATHS["find"], "/sys/fs/cgroup", "-maxdepth", "4",
             "-name", "*hepta*hn*", "-print"]).stdout
        if units_output.strip() or cgroups_output.strip():
            fail("stale hard-gate unit or cgroup exists")
        firewall = self.command(
            "firewall.snapshot", [TOOL_PATHS["nft"], "--json", "list", "ruleset"]
        ).stdout
        if nft_semantic_sha256(firewall) != self.evidence.firewall_semantic_sha256:
            fail("host firewall semantic digest differs from reviewed provenance")
        self.environment = {
            "boot_id": boot_id,
            "cgroup_filesystem": cgroup_fs,
            "virtualization": virtualization,
            "source_commit": commit,
            "source_manifest_sha256": self.evidence.source_manifest_sha256,
            "initial_listener_inventory_sha256": sha256_bytes(canonical_json(listeners)),
            "initial_netns_inventory_sha256": sha256_bytes(
                canonical_json(list(self.evidence.netns_allowlist))),
            "initial_firewall_semantic_sha256": self.evidence.firewall_semantic_sha256,
        }
        self.checks["root_disposable_provenance_bound"] = True
        self.checks["clean_frozen_source_bound"] = True
        self.checks["native_base_and_tooling_provenance_bound"] = True
        self.checks["clean_initial_residue"] = True
        self.checks["forwarder_proxy_process_socket_inventory_zero_or_allowlisted"] = True
        self.checks["no_real_ib_binary_credential_protocol_order"] = True
        self.phase("preflight", {"host_id": self.evidence.host_id})

    def setup(self) -> None:
        self.assert_evidence_fresh()
        # Set before the first scoped mutation so a partially successful mkdir
        # or netns command still enters the exact run-id cleanup path.
        self.resource_mutation_started = True
        self.kill_switch_path = self.runtime / "kill-switch"
        created_path, self.kill_switch_evidence = write_kill_switch(
            self.runtime, expected_uid=self.filesystem_owner_uid)
        if created_path != self.kill_switch_path:
            fail("kill-switch publication path mismatch")
        self.checks["kill_switch_engaged_initially"] = True
        for role in ROLES:
            self.command(
                f"setup.netns.{role}",
                [TOOL_PATHS["ip"], "netns", "add", self.topology.namespaces[role]])
            self.command(
                f"setup.loopback.{role}",
                [TOOL_PATHS["ip"], "-n", self.topology.namespaces[role],
                 "link", "set", "lo", "up"])
        for role in CLIENT_ROLES:
            broker_if = self.topology.broker_ifaces[role]
            client_if = self.topology.client_ifaces[role]
            broker_ns = self.topology.namespaces["broker"]
            client_ns = self.topology.namespaces[role]
            broker_v4, client_v4 = self.topology.ipv4[role]
            broker_v6, client_v6 = self.topology.ipv6[role]
            self.command(
                f"setup.veth.{role}",
                [TOOL_PATHS["ip"], "link", "add", broker_if,
                 "type", "veth", "peer", "name", client_if])
            self.command(
                f"setup.veth-broker-move.{role}",
                [TOOL_PATHS["ip"], "link", "set", broker_if, "netns", broker_ns])
            self.command(
                f"setup.veth-client-move.{role}",
                [TOOL_PATHS["ip"], "link", "set", client_if, "netns", client_ns])
            for namespace, interface, v4, v6 in (
                    (broker_ns, broker_if, broker_v4, broker_v6),
                    (client_ns, client_if, client_v4, client_v6)):
                self.command(
                    f"setup.address4.{role}.{interface}",
                    [TOOL_PATHS["ip"], "-n", namespace, "address", "add",
                     f"{v4}/30", "dev", interface])
                self.command(
                    f"setup.address6.{role}.{interface}",
                    [TOOL_PATHS["ip"], "-n", namespace, "-6", "address", "add",
                     f"{v6}/126", "dev", interface, "nodad"])
                self.command(
                    f"setup.link-up.{role}.{interface}",
                    [TOOL_PATHS["ip"], "-n", namespace, "link", "set",
                     interface, "up"])
        self.install_broker_policy(forward=False, label="initial")
        for role in CLIENT_ROLES:
            self.install_client_policy(role, forward=False, label="initial")
        self.assert_kill_switch()
        self.checks["unique_netns_uid_cgroup_topology"] = True
        self.phase("setup", {
            "namespaces": dict(self.topology.namespaces),
            "slices": dict(self.topology.slices), "uids": dict(UIDS)})

    def install_broker_policy(self, *, forward: bool, label: str) -> None:
        self.command(
            f"policy.broker.{label}",
            [TOOL_PATHS["ip"], "netns", "exec",
             self.topology.namespaces["broker"], TOOL_PATHS["nft"], "-f", "-"],
            stdin=broker_nft(self.topology, forward=forward))

    def install_client_policy(
            self, role: str, *, forward: bool, label: str) -> None:
        self.command(
            f"policy.{role}.{label}",
            [TOOL_PATHS["ip"], "netns", "exec",
             self.topology.namespaces[role], TOOL_PATHS["nft"], "-f", "-"],
            stdin=client_nft(
                self.topology, role,
                execution_forward=(role == "execution" and forward)))

    def start_sentinel(self, label: str) -> str:
        broker_v4 = ";".join(
            self.topology.ipv4[role][0] for role in CLIENT_ROLES)
        broker_v6 = ";".join(
            self.topology.ipv6[role][0] for role in CLIENT_ROLES)
        unit = self.topology.units["sentinel"]
        argv = [
            TOOL_PATHS["systemd_run"], "--quiet", f"--unit={unit}",
            *systemd_properties(self.topology, "broker", UIDS["broker"]),
            TOOL_PATHS["python3"], "-I", "-S", "-c", SENTINEL_CODE,
            self.run_id, broker_v4, broker_v6, ports_csv(),
        ]
        self.command(f"sentinel.start.{label}", argv)
        return self.wait_sentinel_ready(label)

    def restart_sentinel(self, label: str) -> str:
        unit = self.topology.units["sentinel"]
        self.command(
            f"sentinel.restart.{label}",
            [TOOL_PATHS["systemctl"], "restart", unit])
        return self.wait_sentinel_ready(label)

    def wait_sentinel_ready(self, label: str) -> str:
        unit = self.topology.units["sentinel"]
        invocation = ""
        for attempt in range(40):
            status = self.command(
                f"sentinel.status.{label}.{attempt}",
                [TOOL_PATHS["systemctl"], "show", unit,
                 "--property=ActiveState", "--property=SubState",
                 "--property=InvocationID", "--property=ControlGroup",
                 "--property=MainPID", "--property=User",
                 "--property=Group", "--no-pager"]).stdout
            fields = {}
            for line in status.splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    fields[key] = value
            invocation = fields.get("InvocationID", "")
            try:
                main_pid = int(fields.get("MainPID", "0"))
            except ValueError:
                main_pid = 0
            sockets = self.command(
                f"sentinel.sockets.{label}.{attempt}",
                [TOOL_PATHS["ip"], "netns", "exec",
                 self.topology.namespaces["broker"], TOOL_PATHS["ss"],
                 "-H", "-lnt"]).stdout
            if (
                    fields.get("ActiveState") == "active" and
                    fields.get("SubState") == "running" and
                    main_pid > 1 and
                    fields.get("User") == str(UIDS["broker"]) and
                    fields.get("Group") == str(UIDS["broker"]) and
                    CANONICAL_INVOCATION_ID.fullmatch(invocation) is not None and
                    fields.get("ControlGroup") == (
                        f"/{self.topology.slices['broker']}/{unit}") and
                    self.validate_sentinel_sockets(sockets)):
                return invocation
            time.sleep(0.10)
        fail("inert sentinel did not reach exact ready state")

    def validate_sentinel_sockets(self, output: str) -> bool:
        lines = stable_lines(output)
        if len(lines) != 8 * len(CLIENT_ROLES):
            return False
        for role in CLIENT_ROLES:
            for address in (
                    self.topology.ipv4[role][0],
                    self.topology.ipv6[role][0]):
                for port in PROTECTED_PORTS:
                    if not any(
                            address in line and f":{port}" in line
                            for line in lines):
                        return False
        return True

    @staticmethod
    def parse_unit_status(output: str) -> dict[str, str]:
        fields: dict[str, str] = {}
        for line in output.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                if key in fields:
                    fail("duplicate systemd show property")
                fields[key] = value
        return fields

    def wait_unit_stopped(self, unit: str, label: str) -> None:
        for attempt in range(40):
            result = self.command(
                f"{label}.inactive.{attempt}",
                [TOOL_PATHS["systemctl"], "show", unit,
                 "--property=ActiveState", "--property=SubState",
                 "--property=MainPID", "--no-pager"],
                accepted_returncodes=(0, 1, 5))
            fields = self.parse_unit_status(result.stdout)
            if (
                    fields.get("ActiveState") in {"inactive", "failed"} and
                    fields.get("MainPID") in {"", "0"}):
                return
            time.sleep(0.10)
        fail(f"unit did not reach a terminal stopped state: {unit}")

    def wait_anchor_active(self, unit: str) -> str:
        for attempt in range(40):
            output = self.command(
                f"execution-anchor.status.{attempt}",
                [TOOL_PATHS["systemctl"], "show", unit,
                 "--property=ActiveState", "--property=SubState",
                 "--property=InvocationID", "--property=ControlGroup",
                 "--property=MainPID", "--property=User",
                 "--property=Group", "--no-pager"]).stdout
            fields = self.parse_unit_status(output)
            invocation = fields.get("InvocationID", "")
            try:
                main_pid = int(fields.get("MainPID", "0"))
            except ValueError:
                main_pid = 0
            if (
                    fields.get("ActiveState") == "active" and
                    fields.get("SubState") == "running" and main_pid > 1 and
                    fields.get("User") == str(UIDS["execution"]) and
                    fields.get("Group") == str(UIDS["execution"]) and
                    CANONICAL_INVOCATION_ID.fullmatch(invocation) is not None and
                    fields.get("ControlGroup") == (
                        f"/{self.topology.slices['execution']}/{unit}")):
                return invocation
            time.sleep(0.10)
        fail("Execution anchor did not enter its reviewed cgroup slice")

    def start_execution_anchor(self) -> str:
        anchor = self.topology.units["execution_anchor"]
        argv = [
            TOOL_PATHS["systemd_run"], "--quiet", f"--unit={anchor}",
            *systemd_properties(
                self.topology, "execution", UIDS["execution"]),
            TOOL_PATHS["python3"], "-I", "-S", "-c",
            "import time; time.sleep(3600)",
        ]
        self.command("execution-anchor.start", argv)
        return self.wait_anchor_active(anchor)

    def stop_sentinel(
            self, label: str, *, signal_name: Optional[str] = None,
            reset_failed: bool = True) -> None:
        unit = self.topology.units["sentinel"]
        if signal_name is None:
            self.command(
                f"sentinel.stop.{label}",
                [TOOL_PATHS["systemctl"], "stop", unit],
                accepted_returncodes=(0, 5))
        else:
            self.command(
                f"sentinel.kill.{label}",
                [TOOL_PATHS["systemctl"], "kill", f"--signal={signal_name}", unit],
                accepted_returncodes=(0, 5))
        self.wait_unit_stopped(unit, f"sentinel.{label}")
        if reset_failed:
            self.command(
                f"sentinel.reset.{label}",
                [TOOL_PATHS["systemctl"], "reset-failed", unit],
                accepted_returncodes=(0, 1, 5))

    def probe(
            self, *, label: str, role: str, expected: bool,
            uid: Optional[int] = None, slice_role: Optional[str] = None,
            unit_key: Optional[str] = None) -> dict[str, object]:
        actual_uid = UIDS[role] if uid is None else uid
        actual_slice_role = role if slice_role is None else slice_role
        if unit_key is None:
            unit_key = f"{role}_probe" if role != "execution" else "execution_probe"
        unit = self.topology.units[unit_key]
        broker_v4, _client_v4 = self.topology.ipv4[role]
        broker_v6, _client_v6 = self.topology.ipv6[role]
        argv = [
            TOOL_PATHS["systemd_run"], "--quiet", "--wait", "--pipe", "--collect",
            f"--unit={unit}",
            *systemd_properties(
                self.topology, actual_slice_role, actual_uid,
                network_role=role),
            TOOL_PATHS["python3"], "-I", "-S", "-c", PROBE_CODE,
            self.run_id, role, "success" if expected else "denied",
            str(actual_uid), str(actual_uid), broker_v4, broker_v6, ports_csv(),
        ]
        result = self.command(f"probe.{label}", argv, timeout=30)
        return self.validate_probe(
            result.stdout, role=role, expected=expected, uid=actual_uid,
            slice_role=actual_slice_role)

    def validate_probe(
            self, output: str, *, role: str, expected: bool,
            uid: int, slice_role: str) -> dict[str, object]:
        lines = output.splitlines()
        if len(lines) != 1 or not lines[0].startswith(PROBE_MARKER):
            fail("probe did not emit exactly one canonical marker")
        try:
            value = json.loads(
                lines[0][len(PROBE_MARKER):],
                object_pairs_hook=duplicate_rejecting_pairs)
        except json.JSONDecodeError as error:
            fail(f"invalid probe JSON: {error}")
        exact = {
            "schema", "run_id", "role", "expected", "passed", "uid", "gid",
            "cgroup", "netns_inode", "invocation_id", "outcomes",
        }
        if (
                not isinstance(value, dict) or set(value) != exact or
                value.get("schema") != PROBE_SCHEMA or
                value.get("run_id") != self.run_id or
                value.get("role") != role or
                value.get("expected") != ("success" if expected else "denied") or
                value.get("passed") is not True or
                value.get("uid") != uid or value.get("gid") != uid or
                not isinstance(value.get("cgroup"), str) or
                re.fullmatch(
                    r"0::/" + re.escape(self.topology.slices[slice_role]) +
                    r"/[A-Za-z0-9_.@:-]+\.service",
                    str(value.get("cgroup"))) is None or
                re.fullmatch(r"net:\[[0-9]+\]", str(value.get("netns_inode"))) is None or
                CANONICAL_INVOCATION_ID.fullmatch(
                    str(value.get("invocation_id"))) is None):
            fail("probe identity/cgroup/namespace contract mismatch")
        outcomes = value.get("outcomes")
        expected_pairs = {
            (family, port) for family in ("ipv4", "ipv6")
            for port in PROTECTED_PORTS
        }
        if (
                not isinstance(outcomes, list) or len(outcomes) != 8 or
                any(not isinstance(item, dict) or set(item) != {
                    "family", "port", "connected", "payload_valid"}
                    for item in outcomes) or
                {(item["family"], item["port"]) for item in outcomes}
                != expected_pairs or
                any(item["connected"] is not expected or
                    item["payload_valid"] is not expected for item in outcomes)):
            fail("probe protected-port outcome matrix mismatch")
        return value

    def assert_negative_roles(self, label: str) -> None:
        for role in ("gateway", "agent", "simulator"):
            self.probe(label=f"{label}.{role}", role=role, expected=False)

    def drills(self) -> None:
        self.assert_evidence_fresh()
        self.first_sentinel_invocation = self.start_sentinel("initial")
        for role in CLIENT_ROLES:
            self.probe(label=f"initial.{role}", role=role, expected=False)
        self.checks["all_roles_denied_initially_all_protected_ports"] = True
        self.assert_kill_switch()
        first_anchor_invocation = self.start_execution_anchor()
        self.install_broker_policy(forward=True, label="controlled-forward")
        self.install_client_policy(
            "execution", forward=True, label="controlled-forward")
        first = self.probe(
            label="controlled.execution", role="execution", expected=True)
        self.first_execution_invocation = str(first["invocation_id"])
        self.assert_negative_roles("controlled")
        self.checks["exact_execution_uid_cgroup_only_positive"] = True
        self.checks["agent_gateway_simulator_all_denied"] = True
        self.probe(
            label="wrong-cgroup", role="execution", expected=False,
            uid=UIDS["execution"], slice_role="gateway",
            unit_key="execution_wrong_cgroup")
        self.checks["wrong_execution_cgroup_denied"] = True
        self.probe(
            label="wrong-uid", role="execution", expected=False,
            uid=UIDS["gateway"], slice_role="execution",
            unit_key="execution_wrong_uid")
        self.checks["wrong_execution_uid_denied"] = True
        self.assert_kill_switch()

        self.firewall_drill_started = True
        self.firewall_flushed = True
        self.command(
            "firewall.flush", [TOOL_PATHS["nft"], "flush", "ruleset"])
        flushed = self.command(
            "firewall.after-flush",
            [TOOL_PATHS["nft"], "--json", "list", "ruleset"]).stdout
        if not nft_ruleset_is_empty(flushed):
            fail("host firewall flush did not produce an empty host ruleset")
        self.verify_host_exposure_inventory("host-firewall-flushed")
        self.probe(
            label="host-firewall-flushed.execution",
            role="execution", expected=True)
        self.assert_negative_roles("host-firewall-flushed")
        self.assert_kill_switch()
        self.checks["host_firewall_flush_preserved_isolation"] = True
        self.command(
            "firewall.reload",
            [TOOL_PATHS["systemctl"], "reload", self.evidence.firewall_reload_unit])
        restored = self.command(
            "firewall.after-reload",
            [TOOL_PATHS["nft"], "--json", "list", "ruleset"]).stdout
        if nft_semantic_sha256(restored) != self.evidence.firewall_semantic_sha256:
            fail("host firewall reload did not restore reviewed semantic state")
        self.firewall_flushed = False
        self.verify_host_exposure_inventory("host-firewall-reloaded")
        self.probe(
            label="host-firewall-reloaded.execution",
            role="execution", expected=True)
        self.assert_negative_roles("host-firewall-reloaded")
        self.assert_kill_switch()
        self.checks["host_firewall_reload_preserved_isolation"] = True
        self.checks["host_firewall_restored"] = True

        restarted = self.probe(
            label="execution-restart", role="execution", expected=True)
        if restarted["invocation_id"] == self.first_execution_invocation:
            fail("execution restart replayed an invocation identity")
        self.assert_negative_roles("execution-restart")
        self.checks["execution_restart_preserved_isolation"] = True

        anchor = self.topology.units["execution_anchor"]
        self.command(
            "execution-anchor.sigkill",
            [TOOL_PATHS["systemctl"], "kill", "--signal=SIGKILL", anchor])
        self.wait_unit_stopped(anchor, "execution-anchor")
        self.install_client_policy(
            "execution", forward=False, label="execution-sigkill-deny")
        self.probe(
            label="execution-sigkill-denied", role="execution", expected=False)
        self.command(
            "execution-anchor.restart",
            [TOOL_PATHS["systemctl"], "restart", anchor])
        second_anchor_invocation = self.wait_anchor_active(anchor)
        if second_anchor_invocation == first_anchor_invocation:
            fail("Execution anchor restart replayed an invocation identity")
        self.install_client_policy(
            "execution", forward=True, label="execution-restart-regrant")
        self.probe(
            label="execution-after-sigkill", role="execution", expected=True)
        self.assert_negative_roles("execution-after-sigkill")
        self.assert_kill_switch()
        self.checks["execution_sigkill_failed_closed_and_recovered"] = True

        self.stop_sentinel(
            "restart", signal_name="SIGKILL", reset_failed=False)
        self.probe(
            label="sentinel-down.execution", role="execution", expected=False)
        second_sentinel = self.restart_sentinel("restart")
        if second_sentinel == self.first_sentinel_invocation:
            fail("sentinel restart replayed an invocation identity")
        self.probe(
            label="sentinel-restarted.execution", role="execution", expected=True)
        self.assert_negative_roles("sentinel-restarted")
        self.assert_kill_switch()
        self.checks["sentinel_restart_preserved_isolation"] = True

        broker_v4, _client_v4 = self.topology.ipv4["execution"]
        broker_v6, _client_v6 = self.topology.ipv6["execution"]
        exec_ns = self.topology.namespaces["execution"]
        self.command(
            "route.revoke.ipv4",
            [TOOL_PATHS["ip"], "-n", exec_ns, "route", "add", "blackhole",
             f"{broker_v4}/32"])
        self.command(
            "route.revoke.ipv6",
            [TOOL_PATHS["ip"], "-n", exec_ns, "-6", "route", "add",
             "blackhole", f"{broker_v6}/128"])
        self.probe(label="route-revoked", role="execution", expected=False)
        self.command(
            "route.regrant.ipv4",
            [TOOL_PATHS["ip"], "-n", exec_ns, "route", "del", "blackhole",
             f"{broker_v4}/32"])
        self.command(
            "route.regrant.ipv6",
            [TOOL_PATHS["ip"], "-n", exec_ns, "-6", "route", "del",
             "blackhole", f"{broker_v6}/128"])
        self.probe(label="route-regranted", role="execution", expected=True)
        self.checks["route_revoke_regrant_failed_closed"] = True

        exec_if = self.topology.client_ifaces["execution"]
        self.command(
            "interface.revoke",
            [TOOL_PATHS["ip"], "-n", exec_ns, "link", "set", exec_if, "down"])
        self.probe(label="interface-revoked", role="execution", expected=False)
        self.command(
            "interface.regrant",
            [TOOL_PATHS["ip"], "-n", exec_ns, "link", "set", exec_if, "up"])
        self.probe(label="interface-regranted", role="execution", expected=True)
        self.assert_kill_switch()
        self.checks["interface_revoke_regrant_failed_closed"] = True

        self.install_client_policy(
            "execution", forward=False, label="outbound-revoked")
        self.probe(label="outbound-revoked", role="execution", expected=False)
        self.install_client_policy(
            "execution", forward=True, label="outbound-regranted")
        self.probe(label="outbound-regranted", role="execution", expected=True)
        self.checks["execution_outbound_revocation_failed_closed"] = True
        self.install_broker_policy(forward=False, label="inbound-revoked")
        self.probe(label="inbound-revoked", role="execution", expected=False)
        self.install_broker_policy(forward=True, label="inbound-regranted")
        self.probe(label="inbound-regranted", role="execution", expected=True)
        self.checks["broker_inbound_revocation_failed_closed"] = True
        self.install_client_policy(
            "execution", forward=False, label="bilateral-revoked")
        self.install_broker_policy(forward=False, label="bilateral-revoked")
        self.probe(label="bilateral-revoked", role="execution", expected=False)
        self.install_client_policy(
            "execution", forward=True, label="bilateral-regranted")
        self.install_broker_policy(forward=True, label="bilateral-regranted")
        self.probe(label="bilateral-regranted", role="execution", expected=True)
        self.assert_negative_roles("bilateral-regranted")
        self.checks["bilateral_revocation_regrant_verified"] = True
        self.assert_kill_switch()
        self.checks["kill_switch_engaged_throughout"] = True
        self.phase("fault-and-revocation-drills", {
            "execution_invocation_rotated": True,
            "sentinel_invocation_rotated": True,
            "host_firewall_flush_reload": True,
            "route_interface_bilateral_revocation": True,
        })

    def final_deny(self) -> None:
        self.assert_evidence_fresh()
        self.install_client_policy(
            "execution", forward=False, label="final-deny")
        self.install_broker_policy(forward=False, label="final-deny")
        self.probe(label="final-deny.execution", role="execution", expected=False)
        self.assert_negative_roles("final-deny")
        self.assert_kill_switch()
        self.checks["final_deny_all"] = True
        self.phase("final-deny-all", {"protected_ports": list(PROTECTED_PORTS)})

    def cleanup_all(self) -> None:
        self.cleanup["attempted"] = True
        errors: list[str] = []

        def cleanup_command(
                action: str, argv: Sequence[str],
                accepted: tuple[int, ...] = (0, 1, 5)) -> str:
            try:
                return self.command(
                    action, argv, accepted_returncodes=accepted).stdout
            except (GateError, OSError, subprocess.SubprocessError) as error:
                errors.append(f"{action}:{type(error).__name__}")
                return ""

        if self.firewall_drill_started:
            if self.firewall_flushed:
                self.cleanup["firewall_reload_attempted"] = True
                cleanup_command(
                    "cleanup.firewall-reload",
                    [TOOL_PATHS["systemctl"], "reload",
                     self.evidence.firewall_reload_unit])
            output = cleanup_command(
                "cleanup.firewall-final-inspect",
                [TOOL_PATHS["nft"], "--json", "list", "ruleset"])
            try:
                restored = (
                    nft_semantic_sha256(output) ==
                    self.evidence.firewall_semantic_sha256)
            except GateError:
                restored = False
            if not restored:
                self.cleanup["firewall_reload_attempted"] = True
                cleanup_command(
                    "cleanup.firewall-reload-retry",
                    [TOOL_PATHS["systemctl"], "reload",
                     self.evidence.firewall_reload_unit])
                output = cleanup_command(
                    "cleanup.firewall-inspect",
                    [TOOL_PATHS["nft"], "--json", "list", "ruleset"])
                try:
                    restored = (
                        nft_semantic_sha256(output) ==
                        self.evidence.firewall_semantic_sha256)
                except GateError:
                    restored = False
            self.cleanup["firewall_restored"] = restored
            if restored:
                self.firewall_flushed = False
                self.checks["host_firewall_restored"] = True
            else:
                self.checks["host_firewall_restored"] = False
                errors.append("host-firewall-not-restored")
        else:
            self.cleanup["firewall_restored"] = False
        if self.resource_mutation_started:
            for unit in dict.fromkeys(self.topology.units.values()):
                cleanup_command(
                    f"cleanup.unit-stop.{unit}",
                    [TOOL_PATHS["systemctl"], "stop", unit])
                cleanup_command(
                    f"cleanup.unit-reset.{unit}",
                    [TOOL_PATHS["systemctl"], "reset-failed", unit])
            for role, slice_name in self.topology.slices.items():
                cleanup_command(
                    f"cleanup.slice-stop.{role}",
                    [TOOL_PATHS["systemctl"], "stop", slice_name])
                cleanup_command(
                    f"cleanup.slice-reset.{role}",
                    [TOOL_PATHS["systemctl"], "reset-failed", slice_name])
            for role in reversed(ROLES):
                cleanup_command(
                    f"cleanup.netns.{role}",
                    [TOOL_PATHS["ip"], "netns", "delete",
                     self.topology.namespaces[role]])
            if self.kill_switch_path is not None:
                try:
                    self.kill_switch_path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as error:
                    errors.append(f"kill-switch-cleanup:{type(error).__name__}")
                try:
                    self.runtime.rmdir()
                except FileNotFoundError:
                    pass
                except OSError as error:
                    errors.append(f"runtime-cleanup:{type(error).__name__}")
        netns_output = cleanup_command(
            "cleanup.verify.netns", [TOOL_PATHS["ip"], "netns", "list"], (0,))
        link_output = cleanup_command(
            "cleanup.verify.links", [TOOL_PATHS["ip"], "-o", "link", "show"], (0,))
        unit_output = cleanup_command(
            "cleanup.verify.units",
            [TOOL_PATHS["systemctl"], "list-units", "--all", "--no-legend",
             "--plain", f"hepta-hn-{self.run_id}-*",
             f"heptahn{self.run_id}*.slice"], (0,))
        cgroup_output = cleanup_command(
            "cleanup.verify.cgroups",
            [TOOL_PATHS["find"], "/sys/fs/cgroup", "-maxdepth", "4", "-name",
             f"*{self.topology.short}*", "-print"], (0,))
        listener_output = cleanup_command(
            "cleanup.verify.listeners", [TOOL_PATHS["ss"], "-H", "-lntup"], (0,))
        process_output = cleanup_command(
            "cleanup.verify.processes",
            [TOOL_PATHS["ps"], "-eo", "pid=,uid=,comm=,args="], (0,))
        source_head = cleanup_command(
            "cleanup.verify.git-head",
            [TOOL_PATHS["git"], "-C",
             str(Path(__file__).resolve().parents[1]),
             "rev-parse", "HEAD"], (0,)).strip()
        source_status = cleanup_command(
            "cleanup.verify.git-clean",
            [TOOL_PATHS["git"], "-C",
             str(Path(__file__).resolve().parents[1]),
             "status", "--porcelain=v1", "--untracked-files=all"], (0,))
        try:
            validate_netns_inventory(netns_output, self.evidence.netns_allowlist)
            validate_listener_inventory(listener_output, self.evidence.listener_allowlist)
            validate_process_inventory(process_output)
            validate_reserved_uids_unused(process_output)
        except GateError as error:
            errors.append(str(error))
        residue_tokens = (
            f"hc{self.topology.short}", f"hb{self.topology.short}",
            f"hepta-hn-{self.topology.short}",
            f"heptahn{self.topology.short}", self.run_id,
        )
        for label, output in (
                ("links", link_output), ("units", unit_output),
                ("cgroups", cgroup_output)):
            if any(token in output for token in residue_tokens):
                errors.append(f"{label}-residue")
        if self.runtime.exists():
            errors.append("runtime-residue")
        if (
                source_head != self.evidence.source_commit or
                source_status != ""):
            self.checks["clean_frozen_source_bound"] = False
            errors.append("source-lineage-drift")
        self.cleanup["residue"] = sorted(set(errors))
        self.cleanup["complete"] = not errors
        if not errors:
            self.checks["final_namespaces_veth_cgroups_units_residue_zero"] = True
            self.checks["final_forwarder_inventory_unchanged"] = True
        self.phase("cleanup", {
            "complete": not errors, "residue_count": len(set(errors))})

    def topology_record(self) -> dict[str, object]:
        return topology_as_record(self.topology)

    def report(self) -> dict[str, object]:
        completed = int(time.time() * 1000)
        rehearsal_passed = all(self.checks.values()) and self.failure is None
        passed = rehearsal_passed and self.certification_capable
        report: dict[str, object] = {
            "schema": SCHEMA, "run_id": self.run_id,
            "decision": (
                "GO" if passed else
                "REHEARSAL_ONLY" if rehearsal_passed else "NO_GO"),
            "passed": passed,
            "certification_ready": passed,
            "rehearsal_passed": rehearsal_passed,
            "execution_mode": (
                "NATIVE_PRODUCTION" if self.certification_capable else
                "INJECTED_REHEARSAL"),
            "body_sha256": "",
            "scope": "DEDICATED_BROKER_NETNS_HARD_CERTIFICATION_GATE",
            "started_at_ms": self.started_at_ms,
            "completed_at_ms": completed,
            "expires_at_ms": min(
                self.evidence.expires_at_ms,
                int(self.environment_review_record["expires_at_ms"])
                if self.environment_review_record is not None else
                self.evidence.expires_at_ms),
            "duration_ms": max(0, completed - self.started_at_ms),
            "lineage": {
                "host_id": self.evidence.host_id,
                "boot_id": self.evidence.boot_id,
                "source_commit": self.evidence.source_commit,
                "source_manifest_sha256": self.evidence.source_manifest_sha256,
                "runner_sha256": self.evidence.runner_sha256,
            },
            "provenance": {
                key: dict(value) for key, value in self.evidence.records.items()},
            "environment_review_closure": self.environment_review_record,
            "environment": dict(self.environment),
            "topology": self.topology_record(),
            "phases": list(self.phases),
            "checks": dict(self.checks),
            "exposure": {
                "host_listener_allowlist_count": len(self.evidence.listener_allowlist),
                "reachable_forwarders": 0, "ib_binaries": 0,
                "broker_credentials": 0, "broker_protocol_messages": 0,
                "orders": 0,
                "command_transcript_sha256": sha256_bytes(canonical_json(self.transcript)),
                "command_count": len(self.transcript),
                "kill_switch": self.kill_switch_evidence,
            },
            "cleanup": dict(self.cleanup), "boundary": dict(BOUNDARY),
            "failure": self.failure,
            "paper_test_admission_authorized": False,
            "paper_authorized": False, "live_authorized": False,
            "mutation_authorized": False, "direct_broker_access": False,
            "order_submission_authorized": False,
        }
        report["body_sha256"] = body_sha256(report)
        validate_report(report)
        return report

    def run(self) -> dict[str, object]:
        try:
            self.preflight()
            self.setup()
            self.drills()
            self.final_deny()
        except (
                GateError, OSError, UnicodeError, ValueError,
                subprocess.SubprocessError) as error:
            message = str(error) or type(error).__name__
            self.failure = message[:2048]
        finally:
            self.cleanup_all()
        if self.failure is None:
            try:
                self.assert_evidence_fresh()
            except (GateError, OSError, UnicodeError, ValueError) as error:
                self.failure = (str(error) or type(error).__name__)[:2048]
        if not self.cleanup.get("complete") and self.failure is None:
            self.failure = "cleanup or final residue verification failed"
        if self.environment_review_session is not None:
            try:
                self.environment_review_session.reopen_at_gate_end()
                self.environment_review_record = (
                    self.environment_review_session.report_record())
            except ROOT_REVIEW.ReviewClosureError as error:
                review_failure = str(error)[:2048]
                self.failure = (
                    review_failure if self.failure is None else
                    (self.failure + "; " + review_failure)[:2048])
        return self.report()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--host-provenance", required=True, type=Path)
    parser.add_argument("--source-provenance", required=True, type=Path)
    parser.add_argument("--base-provenance", required=True, type=Path)
    parser.add_argument("--tooling-provenance", required=True, type=Path)
    parser.add_argument("--base-image")
    parser.add_argument("--buildkit-image")
    ROOT_REVIEW.add_arguments(parser)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--run-id")
    arguments = parser.parse_args(argv)
    try:
        if not arguments.run:
            fail("refusing native hard-isolation execution without explicit --run")
        if os.geteuid() != 0:
            fail("native hard-isolation execution requires euid 0")
        now_ms = int(time.time() * 1000)
        evidence = load_evidence(
            host_path=arguments.host_provenance,
            source_path=arguments.source_provenance,
            base_path=arguments.base_provenance,
            tooling_path=arguments.tooling_provenance,
            now_ms=now_ms)
        try:
            review_inputs = ROOT_REVIEW.inputs_from_arguments(
                arguments, certify=True)
            assert review_inputs is not None
            environment_review_session = ROOT_REVIEW.verify_review_closure(
                inputs=review_inputs,
                base_image=arguments.base_image,
                buildkit_image=arguments.buildkit_image,
                repository_root=Path(__file__).resolve(strict=True).parents[1],
                expected_source_commit=evidence.source_commit)
        except ROOT_REVIEW.ReviewClosureError as error:
            raise GateError(str(error)) from error
        run_id = arguments.run_id or uuid.uuid4().hex
        gate = HardIsolationGate(
            evidence, ProductionExecutor(enabled=True),
            run_id=run_id, now_ms=now_ms,
            environment_review_session=environment_review_session)
        report = gate.run()
        write_report_no_replace(arguments.report, report)
    except (
            GateError, OSError, UnicodeError, ValueError,
            subprocess.SubprocessError) as error:
        message = str(error) or type(error).__name__
        print(
            "hepta_broker_network_hard_isolation_gate: FAIL: "
            + message[:2048], file=sys.stderr)
        return 1
    if not report["passed"]:
        print(
            "hepta_broker_network_hard_isolation_gate: NO_GO: "
            + str(report.get("failure")), file=sys.stderr)
        return 1
    print(
        "hepta_broker_network_hard_isolation_gate: PASS "
        "netns=5 protected_ports=4 broker_protocol=0 orders=0 authority=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
