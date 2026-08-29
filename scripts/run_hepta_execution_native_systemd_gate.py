#!/usr/bin/env python3

"""Run one Hepta execution systemd gate variant in a disposable native VM.

This runner is intentionally host-local.  It does not provision the VM, alter
networking, install files, or create the disposable sentinel.  A separately
reviewed image provisioner must perform those steps before invoking this file.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from typing import Any, Optional


REPOSITORY = Path(__file__).resolve(strict=True).parents[1]
sys.path.insert(0, str(Path(__file__).resolve(strict=True).parent))
import run_hepta_execution_rootful_systemd_gate as shared  # noqa: E402


SCHEMA = "hepta.execution-native-systemd-gate.v6"
NATIVE_SCOPE = "native_disposable_vm_rootful_systemd"
SENTINEL_HEADER = "HEPTA_DISPOSABLE_NATIVE_SYSTEMD_GATE_V1"
SENTINEL = Path("/etc/heptatrader/hepta-native-systemd-gate.disposable")
INNER_SENTINEL = Path("/run/hepta-rootful-systemd-gate.disposable")
INNER_RUNNER = Path(
    "/usr/local/libexec/hepta_execution_rootful_inner_gate.py")
PREFLIGHT = Path(
    "/usr/local/libexec/check_hepta_execution_provisioned_host.py")
AGENT_OS_PREFLIGHT = Path(
    "/usr/local/libexec/check_hepta_agent_os_provisioned_host.py")
VARIANT_PATH = Path("/usr/local/share/hepta-rootful-systemd-gate/variant")
IMAGE_DIGEST_PATH = Path(
    "/usr/local/share/hepta-rootful-systemd-gate/image-manifest.sha256")
IMAGE_MANIFEST_PATH = Path(
    "/usr/local/share/hepta-rootful-systemd-gate/image-manifest.json")
PROVISIONING_MANIFEST_PATH = Path(
    "/usr/local/share/hepta-rootful-systemd-gate/provisioning-manifest.json")
PLATFORM_POLICY_PATH = Path(
    "/usr/local/share/hepta-rootful-systemd-gate/platform-policy.json")
CLEAN_SOURCE_PROVENANCE_PATH = Path(
    "/usr/local/share/hepta-rootful-systemd-gate/clean-source-provenance.json")
INSTANCE_TRUST_POLICY = Path(
    "/etc/heptatrader/hepta-native-instance-provisioner-trust-v1.json")
INSTANCE_OPENSSL = Path("/usr/bin/openssl")
INSTANCE_RECEIPT_SCHEMA = "hepta.native-instance-provisioner-receipt.v1"
INSTANCE_STATEMENT_SCHEMA = "hepta.native-instance-provisioner-statement.v1"
INSTANCE_TRUST_SCHEMA = "hepta.native-instance-provisioner-trust-policy.v1"
INSTANCE_VERIFICATION_SCHEMA = (
    "hepta.native-instance-provisioner-receipt-verification.v1")
INSTANCE_SIGNATURE_DOMAIN = (
    "HEPTA-NATIVE-INSTANCE-PROVISIONER-RECEIPT-V1")
MAX_INSTANCE_RECEIPT_BYTES = 256 * 1024
MAX_INSTANCE_TRUST_BYTES = 256 * 1024
MAX_INSTANCE_KEY_BYTES = 64 * 1024
MAX_INSTANCE_OPENSSL_BYTES = 32 * 1024 * 1024
AGENT_OS_INSTALLATION_MANIFEST_PATH = Path(
    "/usr/local/share/hepta-rootful-systemd-gate/"
    "agent-os-installation-manifest.json")
AGENT_OS_RUNTIME_INPUT_MANIFEST_PATH = Path(
    "/usr/local/share/hepta-rootful-systemd-gate/"
    "agent-os-runtime-input-manifest.json")
AGENT_OS_INSTALLATION_SCHEMA = (
    "hepta.agent-os-native-vm-installation-manifest.v2")
AGENT_OS_RUNTIME_INPUT_SCHEMA = (
    "hepta.agent-os-native-vm-runtime-input-manifest.v1")
AGENT_OS_RUNTIME_RESULT_SCHEMA = (
    "hepta.agent-os-rootful-systemd-e2e-inner.v1")
AGENT_OS_RUNTIME_INNER = Path(
    "/usr/local/libexec/hepta_agent_os_rootful_inner_gate.py")
AGENT_OS_RUNTIME_SENTINEL = Path(
    "/run/hepta-agent-os-rootful-e2e.disposable")
AGENT_OS_RUNTIME_INSTALLATION_MARKER = Path(
    "/usr/local/share/hepta-agent-os-e2e/installation-preflight")
AGENT_OS_RUNTIME_PROVISIONING = Path(
    "/usr/local/share/hepta-agent-os-e2e/provisioning")
AGENT_OS_WATCH_TOOLS = (
    "account.get_summary",
    "events.wait",
    "market.get_quote",
    "orders.list",
    "portfolio.list_positions",
    "risk.get_limits",
    "system.cancel_request",
    "system.get_health",
    "system.tools.describe",
    "system.tools.list",
    "watch.get_snapshot",
)
AGENT_OS_READ_PROBES = (
    "system.get_health",
    "market.get_quote",
    "account.get_summary",
    "portfolio.list_positions",
    "orders.list",
    "risk.get_limits",
)
AGENT_OS_RUNTIME_HIDDEN_SURFACES = (
    Path("/usr/libexec/hepta-ib-executiond"),
    Path("/usr/lib/systemd/system/hepta-execution-ib-paper.service"),
    Path("/usr/lib/systemd/system/hepta-execution-ib-paper.socket"),
    Path("/usr/lib/systemd/system/"
         "hepta-execution-events-ib-paper.socket"),
)
UNPROVISIONED_SUPERVISOR_LEASE = (
    b"HEPTA_AGENT_OS_UNPROVISIONED_SUPERVISOR_LEASE_V1\n")
AGENT_OS_STATIC_MODES = {
    "/usr/libexec/hepta-tool-gatewayd": 0o755,
    "/usr/bin/hepta-sessionctl": 0o755,
    "/usr/bin/heptactl": 0o755,
    "/usr/libexec/hepta-mcp-server": 0o755,
    "/usr/libexec/hepta-agent-mcp-launcher": 0o755,
    "/usr/libexec/hepta-agent-session-bootstrap": 0o755,
    "/usr/libexec/hepta_agent_trust_domain.py": 0o755,
    "/usr/libexec/hepta-paper-receipt-contracts": 0o755,
    "/usr/libexec/hepta-shadow-watch-collector": 0o755,
    "/usr/libexec/hepta-shadow-watch-exporter": 0o755,
    "/usr/libexec/hepta-shadow-watch-custodian": 0o755,
    "/usr/libexec/hepta-broker-egress-policy": 0o755,
    "/usr/libexec/hepta-shadow-host-installer": 0o755,
    "/usr/libexec/hepta-p1-shadow-host-controller": 0o755,
    "/usr/libexec/hepta-p1-load-probe-validator": 0o755,
    "/usr/libexec/build-hepta-p1-observation-policy": 0o755,
    "/usr/libexec/hepta-p1-shadow-observer-controller": 0o755,
    "/usr/libexec/hepta-p1-shadow-admission-launcher": 0o755,
    "/usr/libexec/hepta-p1-watch-profile-deployer": 0o755,
    "/usr/libexec/hepta-p1-watch-activation-transaction": 0o755,
    "/usr/libexec/hepta-bounded-shadow-closure-verifier": 0o755,
    "/usr/libexec/hepta-official-source-capture": 0o755,
    "/usr/libexec/hepta_bounded_shadow_observer.py": 0o755,
    "/usr/libexec/hepta_market_context_builder.py": 0o755,
    "/usr/libexec/hepta_market_evidence_normalizer.py": 0o755,
    "/usr/libexec/hepta_market_official_source_extractor.py": 0o755,
    "/usr/libexec/hepta_eurusd_confirmed_momentum_strategy.py": 0o755,
    "/usr/libexec/hepta_shadow_market_history.py": 0o755,
    "/usr/libexec/hepta_strategy_shadow_runner.py": 0o755,
    "/usr/libexec/hepta_strategy_contracts.py": 0o644,
    "/usr/libexec/validate_hepta_strategy_decision_receipt.py": 0o755,
    "/usr/share/heptatrader/strategies/"
    "eurusd-confirmed-momentum-shadow-v2.json": 0o644,
    "/usr/libexec/check-hepta-agent-os-provisioned-host": 0o755,
    str(AGENT_OS_PREFLIGHT): 0o755,
    "/usr/lib/systemd/system/hepta-tool-gateway.service": 0o644,
    "/usr/lib/systemd/system/hepta-tool-gateway.socket": 0o644,
    "/usr/lib/systemd/system/hepta-tool-session-supervisor.socket": 0o644,
    "/usr/lib/systemd/system/hepta-broker-egress-policy.service": 0o644,
    "/usr/lib/systemd/system/hepta-p1-watch-activation.service": 0o644,
    "/usr/lib/systemd/system/"
    "hepta-p1-watch-activation-reconcile.service": 0o644,
    "/usr/lib/systemd/system/"
    "hepta-p1-watch-activation-reconcile.timer": 0o644,
    "/usr/lib/systemd/system/hepta-tool-gateway@.service": 0o644,
    "/usr/lib/systemd/system/hepta-tool-gateway@.socket": 0o644,
    "/usr/lib/systemd/system/hepta-tool-session-supervisor@.socket": 0o644,
    "/usr/lib/systemd/system/hepta-shadow-watch-collector@.service": 0o644,
    "/usr/lib/systemd/system/hepta-shadow-watch-collector@.timer": 0o644,
    "/usr/lib/systemd/system/hepta-shadow-watch-export@.service": 0o644,
    "/usr/lib/systemd/system/hepta-shadow-watch-custodian@.service": 0o644,
    "/usr/lib/systemd/system/"
    "hepta-shadow-watch-custodian-reconcile@.service": 0o644,
    "/usr/lib/systemd/system/"
    "hepta-shadow-watch-custodian-reconcile@.timer": 0o644,
    "/usr/lib/tmpfiles.d/heptatrader-agent-os.conf": 0o644,
    "/usr/share/heptatrader/hepta-service-identities-v1.json": 0o644,
    "/usr/share/heptatrader/hepta-broker-network-policy-v1.json": 0o644,
    "/usr/share/heptatrader/plugins/heptatrader-agent-os/.mcp.json": 0o644,
    "/usr/share/heptatrader/plugins/heptatrader-agent-os/"
    ".codex-plugin/plugin.json": 0o644,
    "/usr/share/heptatrader/plugins/heptatrader-agent-os/README.md": 0o644,
    "/usr/share/doc/heptatrader/examples/"
    "hepta-agent-host-identity.conf.example": 0o644,
    "/usr/share/doc/heptatrader/examples/"
    "hepta-tool-gateway.env.example": 0o644,
    "/usr/share/doc/heptatrader/examples/"
    "hepta-tool-gateway-domain.env.example": 0o644,
    "/usr/share/doc/heptatrader/examples/"
    "hepta-shadow-watch-domain.env.example": 0o644,
    "/usr/share/doc/heptatrader/"
    "AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md": 0o644,
    "/usr/share/doc/heptatrader/RUNBOOK-STARTUP.md": 0o644,
    "/etc/heptatrader/hepta-tool-gateway.env": 0o644,
    "/etc/heptatrader/"
    "hepta-agent-trust-domain-paper-identities-v1.json": 0o600,
    "/etc/heptatrader/hepta-supervisor-lease.key": 0o400,
    str(AGENT_OS_RUNTIME_INNER): 0o755,
    str(AGENT_OS_RUNTIME_INSTALLATION_MARKER): 0o444,
    str(AGENT_OS_RUNTIME_PROVISIONING / "hepta-tool-gateway.env"): 0o644,
    str(AGENT_OS_RUNTIME_PROVISIONING /
        "hepta-execution-simulator.env"): 0o644,
    str(AGENT_OS_RUNTIME_PROVISIONING /
        "hepta-execution-simulator-fence"): 0o400,
    str(AGENT_OS_RUNTIME_PROVISIONING /
        "hepta-agent-trust-domain-paper-identities-v1.json"): 0o600,
    str(AGENT_OS_RUNTIME_INPUT_MANIFEST_PATH): 0o444,
}
AGENT_OS_RUNTIME_GATE_MODES = {
    str(AGENT_OS_RUNTIME_INNER): 0o755,
    "/usr/libexec/check-hepta-agent-os-provisioned-host": 0o755,
    "/usr/libexec/hepta-agent-session-bootstrap": 0o755,
    "/usr/libexec/hepta-agent-mcp-launcher": 0o755,
    "/usr/libexec/hepta_agent_trust_domain.py": 0o755,
    "/usr/libexec/hepta-paper-receipt-contracts": 0o755,
    "/usr/libexec/hepta-shadow-watch-collector": 0o755,
    "/usr/libexec/hepta-broker-egress-policy": 0o755,
    "/usr/libexec/hepta-shadow-host-installer": 0o755,
    "/usr/libexec/hepta-shadow-watch-custodian": 0o755,
    "/usr/libexec/hepta-p1-shadow-host-controller": 0o755,
    "/usr/libexec/hepta-p1-load-probe-validator": 0o755,
    "/usr/libexec/build-hepta-p1-observation-policy": 0o755,
    "/usr/libexec/hepta-p1-shadow-observer-controller": 0o755,
    "/usr/libexec/hepta-p1-shadow-admission-launcher": 0o755,
    "/usr/libexec/hepta-p1-watch-profile-deployer": 0o755,
    "/usr/libexec/hepta-p1-watch-activation-transaction": 0o755,
    "/usr/libexec/hepta-bounded-shadow-closure-verifier": 0o755,
    "/usr/libexec/hepta-official-source-capture": 0o755,
    "/usr/libexec/hepta_bounded_shadow_observer.py": 0o755,
    "/usr/libexec/hepta_market_context_builder.py": 0o755,
    "/usr/libexec/hepta_market_evidence_normalizer.py": 0o755,
    "/usr/libexec/hepta_market_official_source_extractor.py": 0o755,
    "/usr/libexec/hepta_eurusd_confirmed_momentum_strategy.py": 0o755,
    "/usr/libexec/hepta_shadow_market_history.py": 0o755,
    "/usr/libexec/hepta_strategy_shadow_runner.py": 0o755,
    "/usr/libexec/hepta_strategy_contracts.py": 0o644,
    "/usr/libexec/validate_hepta_strategy_decision_receipt.py": 0o755,
    "/usr/share/heptatrader/strategies/"
    "eurusd-confirmed-momentum-shadow-v2.json": 0o644,
    "/usr/libexec/hepta-mcp-server": 0o755,
    "/usr/libexec/hepta-tool-gatewayd": 0o755,
    "/usr/libexec/hepta-executiond": 0o755,
    "/usr/bin/hepta-sessionctl": 0o755,
    "/usr/lib/systemd/system/hepta-tool-gateway.service": 0o644,
    "/usr/lib/systemd/system/hepta-tool-gateway.socket": 0o644,
    "/usr/lib/systemd/system/hepta-tool-session-supervisor.socket": 0o644,
    "/usr/lib/systemd/system/hepta-broker-egress-policy.service": 0o644,
    "/usr/lib/systemd/system/hepta-p1-watch-activation.service": 0o644,
    "/usr/lib/systemd/system/"
    "hepta-p1-watch-activation-reconcile.service": 0o644,
    "/usr/lib/systemd/system/"
    "hepta-p1-watch-activation-reconcile.timer": 0o644,
    "/usr/lib/systemd/system/hepta-shadow-watch-collector@.service": 0o644,
    "/usr/lib/systemd/system/hepta-shadow-watch-collector@.timer": 0o644,
    "/usr/lib/systemd/system/hepta-shadow-watch-custodian@.service": 0o644,
    "/usr/lib/systemd/system/"
    "hepta-shadow-watch-custodian-reconcile@.service": 0o644,
    "/usr/lib/systemd/system/"
    "hepta-shadow-watch-custodian-reconcile@.timer": 0o644,
    "/usr/lib/systemd/system/hepta-execution-simulator.service": 0o644,
    "/usr/lib/systemd/system/hepta-execution-simulator.socket": 0o644,
    "/usr/lib/systemd/system/"
    "hepta-execution-events-simulator.socket": 0o644,
    "/usr/lib/tmpfiles.d/heptatrader-agent-os.conf": 0o644,
    "/usr/share/heptatrader/hepta-service-identities-v1.json": 0o644,
    "/usr/share/heptatrader/hepta-broker-network-policy-v1.json": 0o644,
    "/usr/share/heptatrader/plugins/heptatrader-agent-os/.mcp.json": 0o644,
    str(AGENT_OS_RUNTIME_PROVISIONING /
        "hepta-agent-trust-domain-paper-identities-v1.json"): 0o600,
    str(AGENT_OS_RUNTIME_INSTALLATION_MARKER): 0o444,
    str(AGENT_OS_RUNTIME_PROVISIONING / "hepta-tool-gateway.env"): 0o644,
    str(AGENT_OS_RUNTIME_PROVISIONING /
        "hepta-execution-simulator.env"): 0o644,
    str(AGENT_OS_RUNTIME_PROVISIONING /
        "hepta-execution-simulator-fence"): 0o400,
}
VARIANTS = ("real", "sandbox", "stub")
VM_TYPES = frozenset({
    "amazon", "bochs", "google", "kvm", "microsoft", "oracle", "qemu",
    "vmware", "xen",
})
HEX_32 = re.compile(r"[0-9a-f]{32}")
HEX_64 = re.compile(r"[0-9a-f]{64}")
BOOT_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
INSTANCE_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}")
INSTANCE_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,127}")
INSTANCE_KEY_ID = re.compile(r"sha256/[0-9a-f]{64}")
INSTANCE_SIGNATURE = re.compile(r"[0-9a-f]{128}")


class NativeGateError(RuntimeError):
    """A fail-closed native disposable-VM gate error."""


@dataclass
class NativeGateProgress:
    phase: str = "local_host_validation"
    inner_gate_started: bool = False
    inner_gate_completed: bool = False
    agent_os_installation_preflight_completed: bool = False
    agent_os_runtime_gate_started: bool = False
    agent_os_runtime_gate_completed: bool = False


def fail(message: str) -> None:
    raise NativeGateError(message)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("ascii", errors="strict")).hexdigest()


def canonical_json(value: Any) -> bytes:
    return (json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True) +
        "\n").encode("utf-8")


@dataclass(frozen=True)
class InstanceFileCapture:
    path: Path
    payload: bytes
    reference: dict[str, Any]
    identity: tuple[int, ...]


def _instance_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_nlink, metadata.st_uid, metadata.st_gid,
        metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def _strict_instance_json(payload: bytes, label: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                fail(f"{label} contains a duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        fail(f"{label} contains a non-finite JSON value: {value}")

    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=unique, parse_constant=reject_constant)
    except NativeGateError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        fail(f"{label} is not strict UTF-8 JSON")
    if not isinstance(value, dict) or canonical_json(value) != payload:
        fail(f"{label} is not a canonical JSON object")
    return value


def _capture_instance_file(
        path: Path, *, modes: frozenset[int], maximum: int,
        trusted_owner_pairs: frozenset[tuple[int, int]],
        label: str, json_body: bool = False) -> InstanceFileCapture:
    absolute = Path(os.path.abspath(path))
    try:
        canonical = absolute.resolve(strict=True)
    except OSError as error:
        fail(f"{label} is unavailable: {error.strerror}")
    if canonical != absolute or not absolute.is_absolute():
        fail(f"{label} path is not canonical")
    try:
        for parent in reversed((canonical.parent, *canonical.parent.parents)):
            metadata = os.lstat(parent)
            if (not stat.S_ISDIR(metadata.st_mode) or
                    (metadata.st_uid, metadata.st_gid) not in
                    trusted_owner_pairs or
                    stat.S_IMODE(metadata.st_mode) & 0o022 or
                    metadata.st_mode & 0o7000):
                fail(f"{label} has an unsafe ancestor")
        descriptor = os.open(
            canonical, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except NativeGateError:
        raise
    except OSError as error:
        fail(f"{label} cannot be securely opened: {error.strerror}")
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                (before.st_uid, before.st_gid) not in trusted_owner_pairs or
                stat.S_IMODE(before.st_mode) not in modes or
                before.st_size <= 0 or before.st_size > maximum):
            fail(f"{label} metadata is unsafe")
        payload = b""
        while len(payload) <= maximum:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload += chunk
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (_instance_identity(before) != _instance_identity(after) or
            len(payload) != before.st_size):
        fail(f"{label} changed while reading")
    body_sha256 = hashlib.sha256(payload).hexdigest()
    if json_body:
        body_sha256 = hashlib.sha256(
            canonical_json(_strict_instance_json(payload, label))).hexdigest()
    reference = {
        "path": str(canonical),
        "file_sha256": hashlib.sha256(payload).hexdigest(),
        "body_sha256": body_sha256,
        "size": len(payload),
        "mode": f"0{stat.S_IMODE(before.st_mode):03o}",
        "device": before.st_dev,
        "inode": before.st_ino,
    }
    return InstanceFileCapture(
        canonical, payload, reference, _instance_identity(before))


def _reopen_instance_file(
        capture: InstanceFileCapture, *, modes: frozenset[int], maximum: int,
        trusted_owner_pairs: frozenset[tuple[int, int]], label: str,
        json_body: bool = False) -> None:
    reopened = _capture_instance_file(
        capture.path, modes=modes, maximum=maximum,
        trusted_owner_pairs=trusted_owner_pairs, label=label,
        json_body=json_body)
    if (reopened.identity != capture.identity or
            reopened.payload != capture.payload or
            reopened.reference != capture.reference):
        fail(f"{label} changed after verification")


def _instance_signature_payload(statement: dict[str, Any]) -> bytes:
    return (INSTANCE_SIGNATURE_DOMAIN.encode("ascii") + b"\n" +
            canonical_json(statement))


def _openssl_memfd(name: str, payload: bytes) -> int:
    try:
        descriptor = os.memfd_create(name, os.MFD_CLOEXEC)
        written = 0
        while written < len(payload):
            written += os.write(descriptor, payload[written:])
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except (AttributeError, OSError) as error:
        fail(f"native instance signature memfd failed: {error}")


def _run_instance_openssl(
        openssl: InstanceFileCapture, arguments: list[str],
        descriptors: tuple[int, ...]) -> subprocess.CompletedProcess[bytes]:
    environment = {
        "PATH": "/usr/bin", "LANG": "C", "LC_ALL": "C", "TZ": "UTC0",
        "HOME": "/nonexistent", "OPENSSL_CONF": "/dev/null",
    }
    try:
        return subprocess.run(
            [str(openssl.path), *arguments], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            close_fds=True, pass_fds=descriptors, env=environment, timeout=20)
    except (OSError, subprocess.SubprocessError) as error:
        fail(f"native instance signature verifier failed: {error}")


def _instance_public_key_spki_sha256(
        openssl: InstanceFileCapture, key: InstanceFileCapture) -> str:
    key_fd = _openssl_memfd("hepta-native-instance-key", key.payload)
    try:
        completed = _run_instance_openssl(
            openssl,
            ["pkey", "-pubin", "-in", f"/proc/self/fd/{key_fd}",
             "-outform", "DER"], (key_fd,))
    finally:
        os.close(key_fd)
    if completed.returncode != 0 or not completed.stdout:
        fail("native instance provisioner public key is not valid Ed25519")
    return hashlib.sha256(completed.stdout).hexdigest()


def _verify_instance_signature(
        openssl: InstanceFileCapture, key: InstanceFileCapture,
        statement: dict[str, Any], signature_hex: str) -> None:
    try:
        signature = bytes.fromhex(signature_hex)
    except ValueError:
        fail("native instance receipt signature encoding is invalid")
    payload_fd = _openssl_memfd(
        "hepta-native-instance-statement", _instance_signature_payload(statement))
    signature_fd = _openssl_memfd(
        "hepta-native-instance-signature", signature)
    key_fd = _openssl_memfd("hepta-native-instance-key", key.payload)
    try:
        completed = _run_instance_openssl(
            openssl,
            ["pkeyutl", "-verify", "-pubin", "-inkey",
             f"/proc/self/fd/{key_fd}", "-rawin", "-in",
             f"/proc/self/fd/{payload_fd}", "-sigfile",
             f"/proc/self/fd/{signature_fd}"],
            (payload_fd, signature_fd, key_fd))
    finally:
        os.close(payload_fd)
        os.close(signature_fd)
        os.close(key_fd)
    if completed.returncode != 0:
        fail("native instance receipt Ed25519 signature is invalid")


def verify_instance_receipt(
        receipt_path: Path, *, evaluated_at_ms: Optional[int] = None,
        trusted_owner_pairs: frozenset[tuple[int, int]] =
            frozenset({(0, 0)})) -> dict[str, Any]:
    now_ms = (time.time_ns() // 1_000_000 if evaluated_at_ms is None
              else evaluated_at_ms)
    if type(now_ms) is not int or now_ms <= 0:
        fail("native instance receipt evaluation time is invalid")
    if Path(os.path.abspath(INSTANCE_TRUST_POLICY)) != INSTANCE_TRUST_POLICY:
        fail("native instance trust policy path is not fixed")
    receipt = _capture_instance_file(
        receipt_path, modes=frozenset({0o400, 0o440, 0o444}),
        maximum=MAX_INSTANCE_RECEIPT_BYTES,
        trusted_owner_pairs=trusted_owner_pairs,
        label="native instance receipt", json_body=True)
    trust = _capture_instance_file(
        INSTANCE_TRUST_POLICY, modes=frozenset({0o400, 0o440, 0o444}),
        maximum=MAX_INSTANCE_TRUST_BYTES,
        trusted_owner_pairs=trusted_owner_pairs,
        label="native instance trust policy", json_body=True)
    openssl = _capture_instance_file(
        INSTANCE_OPENSSL,
        modes=frozenset({0o500, 0o555, 0o700, 0o755}),
        maximum=MAX_INSTANCE_OPENSSL_BYTES,
        trusted_owner_pairs=trusted_owner_pairs,
        label="native instance signature verifier")
    receipt_document = _strict_instance_json(
        receipt.payload, "native instance receipt")
    trust_document = _strict_instance_json(
        trust.payload, "native instance trust policy")
    if set(receipt_document) != {
            "schema", "version", "statement", "signature", "body_sha256"}:
        fail("native instance receipt fields do not match schema")
    body = {key: receipt_document[key] for key in (
        "schema", "version", "statement", "signature")}
    if (receipt_document.get("schema") != INSTANCE_RECEIPT_SCHEMA or
            receipt_document.get("version") != 1 or
            receipt_document.get("body_sha256") !=
            hashlib.sha256(canonical_json(body)).hexdigest()):
        fail("native instance receipt body seal is invalid")
    statement = receipt_document.get("statement")
    statement_fields = {
        "schema", "challenge", "instance_uuid", "instance_state",
        "provisioner_id", "hypervisor_id", "variant", "vm_type",
        "boot_id", "run_id", "vm_image_manifest_sha256",
        "provisioning_manifest_sha256", "source_lineage",
        "issued_at_ms", "expires_at_ms",
    }
    if (not isinstance(statement, dict) or set(statement) != statement_fields or
            statement.get("schema") != INSTANCE_STATEMENT_SCHEMA or
            HEX_64.fullmatch(str(statement.get("challenge", ""))) is None or
            INSTANCE_UUID.fullmatch(
                str(statement.get("instance_uuid", ""))) is None or
            statement.get("instance_state") != "running" or
            INSTANCE_IDENTITY.fullmatch(
                str(statement.get("provisioner_id", ""))) is None or
            INSTANCE_IDENTITY.fullmatch(
                str(statement.get("hypervisor_id", ""))) is None or
            statement.get("variant") not in VARIANTS or
            statement.get("vm_type") not in VM_TYPES or
            BOOT_ID.fullmatch(str(statement.get("boot_id", ""))) is None or
            HEX_32.fullmatch(str(statement.get("run_id", ""))) is None or
            any(HEX_64.fullmatch(str(statement.get(field, ""))) is None
                for field in (
                    "vm_image_manifest_sha256",
                    "provisioning_manifest_sha256"))):
        fail("native instance receipt statement is invalid")
    source = statement.get("source_lineage")
    if (not isinstance(source, dict) or set(source) != {
            "bundle_sha256", "manifest_sha256", "files_sha256"} or
            any(HEX_64.fullmatch(str(source.get(field, ""))) is None
                for field in source)):
        fail("native instance receipt source lineage is invalid")
    issued = statement.get("issued_at_ms")
    expires = statement.get("expires_at_ms")
    if (type(issued) is not int or type(expires) is not int or
            issued <= 0 or expires <= issued):
        fail("native instance receipt validity interval is invalid")
    signature = receipt_document.get("signature")
    if (not isinstance(signature, dict) or set(signature) != {
            "algorithm", "key_id", "value_hex"} or
            signature.get("algorithm") != "ed25519" or
            INSTANCE_KEY_ID.fullmatch(str(signature.get("key_id", ""))) is None or
            INSTANCE_SIGNATURE.fullmatch(
                str(signature.get("value_hex", ""))) is None):
        fail("native instance receipt signature fields are invalid")
    trust_fields = {
        "schema", "version", "production_status", "signature_domain",
        "maximum_receipt_lifetime_ms", "maximum_clock_skew_ms", "keys",
    }
    if (set(trust_document) != trust_fields or
            trust_document.get("schema") != INSTANCE_TRUST_SCHEMA or
            trust_document.get("version") != 1 or
            trust_document.get("production_status") != "configured-external" or
            trust_document.get("signature_domain") !=
            INSTANCE_SIGNATURE_DOMAIN):
        fail("native instance production trust is not configured")
    lifetime = trust_document.get("maximum_receipt_lifetime_ms")
    skew = trust_document.get("maximum_clock_skew_ms")
    if (type(lifetime) is not int or lifetime <= 0 or
            lifetime > 24 * 60 * 60 * 1000 or
            type(skew) is not int or skew < 0 or skew > 5 * 60 * 1000 or
            expires - issued > lifetime or now_ms < issued - skew or
            now_ms >= expires):
        fail("native instance receipt is stale or outside policy")
    keys = trust_document.get("keys")
    if not isinstance(keys, list) or not keys:
        fail("native instance trust policy has no external keys")
    parsed_keys: dict[str, dict[str, Any]] = {}
    for record in keys:
        expected_key_fields = {
            "key_id", "algorithm", "public_key_path",
            "public_key_spki_sha256", "valid_from_ms", "valid_until_ms",
            "revoked", "allowed_provisioner_ids", "allowed_hypervisor_ids",
        }
        if (not isinstance(record, dict) or set(record) != expected_key_fields or
                INSTANCE_KEY_ID.fullmatch(
                    str(record.get("key_id", ""))) is None or
                record.get("algorithm") != "ed25519" or
                record.get("public_key_spki_sha256") !=
                    str(record.get("key_id", ""))[7:] or
                type(record.get("valid_from_ms")) is not int or
                record["valid_from_ms"] <= 0 or
                (record.get("valid_until_ms") is not None and
                 (type(record["valid_until_ms"]) is not int or
                  record["valid_until_ms"] <= record["valid_from_ms"])) or
                type(record.get("revoked")) is not bool):
            fail("native instance trust key record is invalid")
        relative = record.get("public_key_path")
        relative_path = Path(relative) if isinstance(relative, str) else Path()
        if (not isinstance(relative, str) or not relative or
                relative_path.is_absolute() or ".." in relative_path.parts or
                relative_path.as_posix() != relative):
            fail("native instance trust key path is unsafe")
        for allowed_field in (
                "allowed_provisioner_ids", "allowed_hypervisor_ids"):
            values = record.get(allowed_field)
            if (not isinstance(values, list) or not values or
                    values != sorted(set(values)) or
                    any(INSTANCE_IDENTITY.fullmatch(str(value)) is None
                        for value in values)):
                fail("native instance trust key scope is invalid")
        if record["key_id"] in parsed_keys:
            fail("native instance trust policy contains a duplicate key")
        parsed_keys[record["key_id"]] = record
    key_record = parsed_keys.get(signature["key_id"])
    if (key_record is None or key_record["revoked"] is not False or
            issued < key_record["valid_from_ms"] or
            (key_record["valid_until_ms"] is not None and
             (expires > key_record["valid_until_ms"] or
              now_ms >= key_record["valid_until_ms"])) or
            statement["provisioner_id"] not in
                key_record["allowed_provisioner_ids"] or
            statement["hypervisor_id"] not in
                key_record["allowed_hypervisor_ids"]):
        fail("native instance receipt key is untrusted for this statement")
    key_path = trust.path.parent / key_record["public_key_path"]
    key = _capture_instance_file(
        key_path, modes=frozenset({0o400, 0o440, 0o444}),
        maximum=MAX_INSTANCE_KEY_BYTES,
        trusted_owner_pairs=trusted_owner_pairs,
        label="native instance provisioner public key")
    if _instance_public_key_spki_sha256(openssl, key) != \
            key_record["public_key_spki_sha256"]:
        fail("native instance provisioner public key identity mismatch")
    _verify_instance_signature(
        openssl, key, statement, signature["value_hex"])
    for capture, modes, maximum, label, json_body in (
        (receipt, frozenset({0o400, 0o440, 0o444}),
         MAX_INSTANCE_RECEIPT_BYTES, "native instance receipt", True),
        (trust, frozenset({0o400, 0o440, 0o444}),
         MAX_INSTANCE_TRUST_BYTES, "native instance trust policy", True),
        (key, frozenset({0o400, 0o440, 0o444}),
         MAX_INSTANCE_KEY_BYTES, "native instance provisioner public key", False),
        (openssl, frozenset({0o500, 0o555, 0o700, 0o755}),
         MAX_INSTANCE_OPENSSL_BYTES, "native instance signature verifier", False),
    ):
        _reopen_instance_file(
            capture, modes=modes, maximum=maximum,
            trusted_owner_pairs=trusted_owner_pairs, label=label,
            json_body=json_body)
    return {
        "schema": INSTANCE_VERIFICATION_SCHEMA,
        "verified": True,
        "verified_at_ms": now_ms,
        "statement": statement,
        "receipt": {
            **receipt.reference,
            "body_sha256": receipt_document["body_sha256"],
        },
        "trust_policy": trust.reference,
        "verification_key": key.reference,
        "signature_verifier": openssl.reference,
        "key_id": signature["key_id"],
    }


def parse_agent_os_runtime_result(stdout: str) -> dict[str, Any]:
    prefix = "HEPTA_AGENT_OS_ROOTFUL_E2E_RESULT="
    lines = stdout.splitlines()
    if len(lines) != 1 or not lines[0].startswith(prefix):
        fail("Agent OS runtime gate must emit exactly one result line")
    try:
        result = json.loads(lines[0][len(prefix):])
    except json.JSONDecodeError:
        fail("Agent OS runtime gate result is invalid JSON")
    expected_top = {
        "schema", "passed", "identities", "checks", "lifecycle", "boundary"}
    if (not isinstance(result, dict) or set(result) != expected_top or
            result.get("schema") != AGENT_OS_RUNTIME_RESULT_SCHEMA or
            result.get("passed") is not True):
        fail("Agent OS runtime gate top-level contract mismatch")
    identities = result.get("identities")
    expected_identities = {
        "agent_uid": 2004,
        "gateway_uid": 2001,
        "simulator_execution_uid": 2002,
        "ib_execution_uid_reserved_not_started": 2003,
    }
    if (not isinstance(identities, dict) or
            set(identities) != set(expected_identities) or
            any(type(identities.get(field)) is not int or
                identities[field] != expected
                for field, expected in expected_identities.items())):
        fail("Agent OS runtime gate four-UID identity contract mismatch")
    expected_checks = {
        "systemd_pid1", "network_none_loopback_only",
        "no_host_mount_or_docker_socket", "fixed_identity_isolation",
        "ib_paper_surface_absent", "installation_preflight",
        "simulator_dual_socket_activation", "gateway_dual_socket_activation",
        "root_watch_bootstrap", "uid_2004_mcp_initialize",
        "uid_2004_exact_watch_tool_list", "uid_2004_read_only_probes",
        "gateway_service_socket_reactivation",
        "simulator_service_socket_reactivation",
        "socket_stop_removes_paths", "socket_restart_recreates_paths",
        "watch_restart_fails_closed", "runtime_preflight_after_restart",
        "watch_session_revoked",
        "all_runtime_paths_removed",
    }
    checks = result.get("checks")
    if (not isinstance(checks, dict) or set(checks) != expected_checks or
            any(value is not True for value in checks.values())):
        fail("Agent OS runtime gate check contract mismatch")
    lifecycle = result.get("lifecycle")
    if (not isinstance(lifecycle, dict) or set(lifecycle) != {
            "watch_generation", "initial", "service_reactivation",
            "socket_reactivation"}):
        fail("Agent OS runtime gate lifecycle contract mismatch")
    generation = lifecycle.get("watch_generation")
    if (type(generation) is not int or generation < 1):
        fail("Agent OS runtime WATCH generation is invalid")
    lifecycle_fields = {
        "gateway_pid", "simulator_pid", "tool_socket_inode",
        "supervisor_socket_inode", "execution_socket_inode",
        "events_socket_inode",
    }
    for phase in ("initial", "service_reactivation", "socket_reactivation"):
        record = lifecycle.get(phase)
        if (not isinstance(record, dict) or set(record) != lifecycle_fields or
                any(type(value) is not int or value <= 0
                    for value in record.values())):
            fail(f"Agent OS runtime {phase} lifecycle record is invalid")
    initial = lifecycle["initial"]
    service = lifecycle["service_reactivation"]
    sockets = lifecycle["socket_reactivation"]
    if (initial["gateway_pid"] == service["gateway_pid"] or
            initial["simulator_pid"] == service["simulator_pid"] or
            service["gateway_pid"] == sockets["gateway_pid"] or
            service["simulator_pid"] == sockets["simulator_pid"]):
        fail("Agent OS runtime service lifecycle did not restart")
    for key in (
            "tool_socket_inode", "supervisor_socket_inode",
            "execution_socket_inode", "events_socket_inode"):
        if initial[key] != service[key] or service[key] == sockets[key]:
            fail("Agent OS runtime socket lifecycle did not restart")
    boundary = result.get("boundary")
    expected_boundary_fields = {
        "container_network", "real_broker_connections", "paper_orders",
        "paper_authorized", "live_authorized", "ib_adapter_staged",
        "host_hepta_units_started", "host_bind_mounts",
        "raw_session_token_recorded",
    }
    if (not isinstance(boundary, dict) or
            set(boundary) != expected_boundary_fields or
            boundary.get("container_network") != "none" or
            any(type(boundary.get(field)) is not int or
                boundary[field] != 0
                for field in (
                    "real_broker_connections", "paper_orders",
                    "host_bind_mounts")) or
            any(boundary.get(field) is not False
                for field in (
                    "paper_authorized", "live_authorized",
                    "ib_adapter_staged", "host_hepta_units_started",
                    "raw_session_token_recorded"))):
        fail("Agent OS runtime gate crossed the offline WATCH boundary")
    return result


def input_manifest_sha256(records: list[dict[str, Any]]) -> str:
    return hashlib.sha256(canonical_json(records)).hexdigest()


def input_content_manifest_sha256(records: list[dict[str, Any]]) -> str:
    content_records = [
        {
            "path": record["path"],
            "mode": record["mode"],
            "size": record["size"],
            "sha256": record["sha256"],
        }
        for record in records
    ]
    return hashlib.sha256(canonical_json(content_records)).hexdigest()


def sentinel_content(
        machine_id: str, boot_id: str, vm_image_manifest_sha256: str,
        provisioning_manifest_sha256: str, platform_policy_sha256: str,
        clean_source_bundle_sha256: str,
        clean_source_manifest_sha256: str,
        clean_source_files_sha256: str, variant: str, run_id: str,
        instance_challenge: str) -> bytes:
    return (
        SENTINEL_HEADER + "\n" +
        f"machine_id={machine_id}\n" +
        f"boot_id={boot_id}\n" +
        f"vm_image_manifest_sha256={vm_image_manifest_sha256}\n" +
        f"provisioning_manifest_sha256={provisioning_manifest_sha256}\n" +
        f"platform_policy_sha256={platform_policy_sha256}\n" +
        f"clean_source_bundle_sha256={clean_source_bundle_sha256}\n" +
        f"clean_source_manifest_sha256={clean_source_manifest_sha256}\n" +
        f"clean_source_files_sha256={clean_source_files_sha256}\n" +
        f"variant={variant}\n" +
        f"run_id={run_id}\n" +
        f"instance_challenge={instance_challenge}\n").encode("ascii")


def sentinel_record(
        metadata: os.stat_result, contents: bytes, *,
        expected_machine_id: str, expected_boot_id: str,
        expected_variant: str) -> dict[str, Any]:
    try:
        text = contents.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        fail("native disposable sentinel encoding is invalid")
    lines = text.splitlines()
    values: dict[str, str] = {}
    if len(lines) == 12 and text.endswith("\n"):
        for line in lines[1:]:
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in values:
                fail("native disposable sentinel has duplicate fields")
            values[key] = value
    expected_keys = {
        "machine_id", "boot_id", "vm_image_manifest_sha256",
        "provisioning_manifest_sha256", "platform_policy_sha256",
        "clean_source_bundle_sha256", "clean_source_manifest_sha256",
        "clean_source_files_sha256",
        "variant", "run_id", "instance_challenge",
    }
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or
            metadata.st_gid != 0 or stat.S_IMODE(metadata.st_mode) != 0o400 or
            metadata.st_nlink != 1 or not lines or
            lines[0] != SENTINEL_HEADER or set(values) != expected_keys or
            HEX_32.fullmatch(values.get("machine_id", "")) is None or
            BOOT_ID.fullmatch(values.get("boot_id", "")) is None or
            any(HEX_64.fullmatch(values.get(key, "")) is None for key in (
                "vm_image_manifest_sha256", "provisioning_manifest_sha256",
                "platform_policy_sha256", "clean_source_bundle_sha256",
                "clean_source_manifest_sha256", "clean_source_files_sha256")) or
            values.get("variant") not in VARIANTS or
            HEX_32.fullmatch(values.get("run_id", "")) is None or
            HEX_64.fullmatch(values.get("instance_challenge", "")) is None or
            values.get("machine_id") != expected_machine_id or
            values.get("boot_id") != expected_boot_id or
            values.get("variant") != expected_variant):
        fail("native disposable sentinel contract is not satisfied")
    return {
        "root_owned": True,
        "mode": "0400",
        "single_link": True,
        "contract": SENTINEL_HEADER,
        **values,
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
    }


def read_anchored_sentinel(
        expected_machine_id: str, expected_boot_id: str,
        expected_variant: str) -> dict[str, Any]:
    descriptors: list[int] = []
    try:
        parent = os.open(
            "/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
        descriptors.append(parent)
        for component in ("etc", "heptatrader"):
            parent = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=parent)
            descriptors.append(parent)
            metadata = os.fstat(parent)
            if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or
                    metadata.st_gid != 0 or
                    stat.S_IMODE(metadata.st_mode) & 0o022):
                fail("native disposable sentinel ancestor is unsafe")
        descriptor = os.open(
            SENTINEL.name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=parent)
        descriptors.append(descriptor)
        before = os.fstat(descriptor)
        contents = os.read(descriptor, 2049)
        after = os.fstat(descriptor)
        fields = (
            "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
            "st_size", "st_mtime_ns", "st_ctime_ns")
        if (any(getattr(before, field) != getattr(after, field)
                for field in fields) or len(contents) != before.st_size):
            fail("native disposable sentinel changed while reading")
        return sentinel_record(
            before, contents, expected_machine_id=expected_machine_id,
            expected_boot_id=expected_boot_id,
            expected_variant=expected_variant)
    except OSError as error:
        fail(f"cannot securely open native disposable sentinel: {error.strerror}")
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def parse_network_isolation(
        addresses: Any, routes: Any) -> dict[str, Any]:
    if not isinstance(addresses, list) or not isinstance(routes, list):
        fail("native VM network evidence is not a JSON list")
    loopback_seen = False
    loopback_addresses: set[str] = set()
    non_loopback_addresses: list[str] = []
    unsafe_links: list[str] = []
    for interface in addresses:
        if not isinstance(interface, dict):
            fail("native VM address record is invalid")
        name = interface.get("ifname")
        entries = interface.get("addr_info", [])
        if not isinstance(name, str) or not isinstance(entries, list):
            fail("native VM address fields are invalid")
        if name == "lo":
            loopback_seen = True
            for entry in entries:
                if (not isinstance(entry, dict) or
                        entry.get("local") not in {"127.0.0.1", "::1"}):
                    fail("loopback carries a non-loopback address")
                loopback_addresses.add(entry["local"])
            continue
        if interface.get("operstate") != "DOWN":
            unsafe_links.append(name)
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(
                    entry.get("local"), str):
                fail("native VM non-loopback address record is invalid")
            non_loopback_addresses.append(entry["local"])
    default_routes = []
    non_loopback_routes = []
    for route in routes:
        if not isinstance(route, dict):
            fail("native VM route record is invalid")
        destination = route.get("dst")
        if destination in {"default", "0.0.0.0/0", "::/0"}:
            default_routes.append(destination)
        if route.get("dev") not in {None, "lo"}:
            non_loopback_routes.append(str(destination))
    if (not loopback_seen or
            loopback_addresses != {"127.0.0.1", "::1"} or
            non_loopback_addresses or unsafe_links or default_routes or
            non_loopback_routes):
        fail("native VM network is not loopback-only and route-isolated")
    return {
        "loopback_present": True,
        "non_loopback_addresses": 0,
        "non_loopback_links_up": 0,
        "default_routes": 0,
        "non_loopback_routes": 0,
    }


def json_command(arguments: list[str], label: str) -> Any:
    completed = shared.command(arguments, timeout=20)
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        fail(f"{label} did not emit valid JSON")


def local_host_record() -> dict[str, Any]:
    if os.geteuid() != 0 or os.getegid() != 0:
        fail("native VM gate requires root")
    if (os.readlink("/proc/1/exe") not in {
            "/usr/lib/systemd/systemd", "/lib/systemd/systemd"} or
            shared.read_virtual_text(Path("/proc/1/comm"), 64).strip() !=
            "systemd"):
        fail("native VM gate requires systemd PID 1")
    container = shared.command(
        ["systemd-detect-virt", "--container", "--quiet"],
        check=False, timeout=10)
    if container.returncode != 1:
        fail("native VM gate must not run in a container")
    vm = shared.command(
        ["systemd-detect-virt", "--vm"], check=False, timeout=10)
    vm_type = vm.stdout.strip()
    if vm.returncode != 0 or vm_type not in VM_TYPES:
        fail("native VM gate requires an approved virtual machine type")
    if Path("/run/docker.sock").exists() or Path("/var/run/docker.sock").exists():
        fail("native VM gate forbids an exposed Docker socket")
    try:
        machine_id = shared.read_regular_file(
            Path("/etc/machine-id"), maximum=128)[1].decode(
                "ascii", errors="strict").strip()
    except UnicodeDecodeError:
        fail("native VM machine-id encoding is invalid")
    boot_id = shared.read_virtual_text(
        Path("/proc/sys/kernel/random/boot_id"), 128).strip()
    if (HEX_32.fullmatch(machine_id) is None or
            BOOT_ID.fullmatch(boot_id) is None):
        fail("native VM machine or boot identity is invalid")
    cgroup = shared.read_virtual_text(Path("/proc/1/cgroup"), 1024).strip()
    if cgroup != "0::/" or not Path(
            "/sys/fs/cgroup/cgroup.controllers").is_file():
        fail("native VM requires systemd at the cgroup v2 root")
    return {
        "machine_id": machine_id,
        "boot_id": boot_id,
        "vm_type": vm_type,
        "systemd_pid1": True,
        "cgroup_v2_root": True,
        "docker_socket_absent": True,
        "kernel_release": os.uname().release,
    }


def stable_root_file(
        path: Path, mode: int, *, executable: bool = False) -> dict[str, Any]:
    absolute = Path(os.path.abspath(path))
    try:
        canonical = absolute.resolve(strict=True)
    except OSError:
        fail(f"native VM input path is unavailable: {path}")
    if absolute != canonical:
        fail(f"native VM input path is not canonical: {path}")
    metadata, _contents, digest = shared.read_regular_file(
        canonical, executable=executable)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != mode):
        fail(f"native VM input metadata is unsafe: {path}")
    return {
        "path": str(canonical),
        "sha256": digest,
        "size": metadata.st_size,
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "mode": format(stat.S_IMODE(metadata.st_mode), "04o"),
    }


def validate_agent_os_installation_manifest(
        manifest_record: dict[str, Any]) -> dict[str, Any]:
    try:
        manifest = json.loads(shared.read_regular_file(
            AGENT_OS_INSTALLATION_MANIFEST_PATH,
            maximum=4 * 1024 * 1024)[1].decode(
                "utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("native VM Agent OS installation manifest is invalid")
    expected = {
        "schema", "profile", "preflight", "files", "runtime",
        "paper_authorized", "live_enabled",
    }
    if (not isinstance(manifest, dict) or set(manifest) != expected or
            manifest.get("schema") != AGENT_OS_INSTALLATION_SCHEMA or
            manifest.get("profile") != "static-installation-only" or
            manifest.get("preflight") != {
                "path": str(AGENT_OS_PREFLIGHT),
                "arguments": ["--root", "/", "--installation-only"],
            } or
            manifest.get("runtime") != {
                "tool_socket_staged": False,
                "session_token_staged": False,
                "supervisor_socket_staged": False,
                "runtime_preflight_executed": False,
                "runtime_preflight_required": True,
                "runtime_gate_inputs_staged": True,
                "runtime_input_manifest_sha256":
                    manifest.get("runtime", {}).get(
                        "runtime_input_manifest_sha256"),
                "runtime_state_provisioned_by_bundle": False,
                "runtime_sentinel_staged": False,
                "supervisor_credential":
                    "unprovisioned-non-authorizing-placeholder",
            } or
            manifest.get("paper_authorized") is not False or
            manifest.get("live_enabled") is not False or
            not isinstance(manifest.get("files"), list)):
        fail("native VM Agent OS installation manifest contract mismatch")
    runtime_manifest_sha256 = manifest["runtime"].get(
        "runtime_input_manifest_sha256")
    if (not isinstance(runtime_manifest_sha256, str) or
            HEX_64.fullmatch(runtime_manifest_sha256) is None or
            runtime_manifest_sha256 != stable_root_file(
                AGENT_OS_RUNTIME_INPUT_MANIFEST_PATH, 0o444)["sha256"]):
        fail("native VM Agent OS runtime input manifest binding mismatch")
    expected_paths = {
        path.removeprefix("/") for path in AGENT_OS_STATIC_MODES}
    records: dict[str, dict[str, Any]] = {}
    for record in manifest["files"]:
        if (not isinstance(record, dict) or set(record) != {
                "path", "mode", "uid", "gid", "size", "sha256"} or
                not isinstance(record.get("path"), str) or
                record["path"] in records):
            fail("native VM Agent OS installation file record mismatch")
        records[record["path"]] = record
    if set(records) != expected_paths:
        fail("native VM Agent OS installation file closure mismatch")
    stable_records: list[dict[str, Any]] = []
    for absolute_text, mode in sorted(AGENT_OS_STATIC_MODES.items()):
        absolute = Path(absolute_text)
        record = stable_root_file(
            absolute, mode, executable=bool(mode & stat.S_IXUSR))
        expected_record = records[absolute_text.removeprefix("/")]
        if (record["size"] != expected_record["size"] or
                record["sha256"] != expected_record["sha256"] or
                record["mode"] != expected_record["mode"] or
                expected_record.get("uid") != 0 or
                expected_record.get("gid") != 0):
            fail("native VM Agent OS installation file digest mismatch")
        stable_records.append(record)
    placeholder = shared.read_regular_file(
        Path("/etc/heptatrader/hepta-supervisor-lease.key"),
        maximum=1024)[1]
    installed_checker = shared.read_regular_file(
        Path("/usr/libexec/check-hepta-agent-os-provisioned-host"),
        executable=True, maximum=1024 * 1024)[1]
    runner_checker = shared.read_regular_file(
        AGENT_OS_PREFLIGHT, executable=True, maximum=1024 * 1024)[1]
    for binary in (
            Path("/usr/libexec/hepta-tool-gatewayd"),
            Path("/usr/bin/hepta-sessionctl"), Path("/usr/bin/heptactl")):
        if not shared.read_regular_file(
                binary, executable=True, maximum=64 * 1024 * 1024)[1].startswith(
                    b"\x7fELF"):
            fail("native VM Agent OS executable is not ELF")
    if (placeholder != UNPROVISIONED_SUPERVISOR_LEASE or
            installed_checker != runner_checker):
        fail("native VM Agent OS placeholder/checker identity mismatch")
    return {
        "installation_manifest_sha256": manifest_record["sha256"],
        "installation_file_count": len(records),
        "gateway_sha256": records[
            "usr/libexec/hepta-tool-gatewayd"]["sha256"],
        "sessionctl_sha256": records["usr/bin/hepta-sessionctl"]["sha256"],
        "mcp_server_sha256": records[
            "usr/libexec/hepta-mcp-server"]["sha256"],
        "installation_preflight": True,
        "runtime_preflight_executed": False,
        "runtime_preflight_required": True,
        "runtime_gate_inputs_staged": True,
        "runtime_input_manifest_sha256": runtime_manifest_sha256,
        "runtime_artifacts_staged": False,
    }


def validate_agent_os_runtime_input_manifest() -> dict[str, Any]:
    record = stable_root_file(AGENT_OS_RUNTIME_INPUT_MANIFEST_PATH, 0o444)
    try:
        manifest = json.loads(shared.read_regular_file(
            AGENT_OS_RUNTIME_INPUT_MANIFEST_PATH,
            maximum=4 * 1024 * 1024)[1].decode(
                "utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("native VM Agent OS runtime input manifest is invalid")
    expected = {
        "schema", "profile", "inputs", "identities", "watch_tools",
        "read_probes", "lifecycle", "runtime", "paper_authorized",
        "live_enabled", "ib_adapter_runtime_authorized",
    }
    if (not isinstance(manifest, dict) or set(manifest) != expected or
            manifest.get("schema") != AGENT_OS_RUNTIME_INPUT_SCHEMA or
            manifest.get("profile") !=
            "native-vm-four-uid-watch-runtime-required" or
            manifest.get("identities") != {
                "gateway_uid": 2001,
                "simulator_execution_uid": 2002,
                "ib_execution_uid_reserved_not_started": 2003,
                "agent_uid": 2004,
            } or
            manifest.get("watch_tools") != list(AGENT_OS_WATCH_TOOLS) or
            manifest.get("read_probes") != list(AGENT_OS_READ_PROBES) or
            manifest.get("lifecycle") != {
                "service_restart_required": True,
                "socket_restart_required": True,
                "watch_revoke_required": True,
                "runtime_cleanup_required": True,
            } or
            manifest.get("runtime") != {
                "inner_gate_path": str(AGENT_OS_RUNTIME_INNER),
                "runtime_preflight_executed": False,
                "runtime_preflight_required": True,
                "runtime_state_provisioned_by_bundle": False,
                "runtime_sentinel_staged": False,
                "runtime_artifacts_staged": False,
            } or
            manifest.get("paper_authorized") is not False or
            manifest.get("live_enabled") is not False or
            manifest.get("ib_adapter_runtime_authorized") is not False or
            not isinstance(manifest.get("inputs"), list)):
        fail("native VM Agent OS runtime input manifest contract mismatch")
    manifest_records: dict[str, dict[str, Any]] = {}
    stable_records: list[dict[str, Any]] = []
    for raw in manifest["inputs"]:
        if (not isinstance(raw, dict) or set(raw) != {
                "path", "mode", "uid", "gid", "size", "sha256"} or
                not isinstance(raw.get("path"), str) or
                raw["path"] in manifest_records or
                raw.get("uid") != 0 or raw.get("gid") != 0 or
                type(raw.get("size")) is not int or raw["size"] < 0 or
                not isinstance(raw.get("sha256"), str) or
                HEX_64.fullmatch(raw["sha256"]) is None):
            fail("native VM Agent OS runtime input record mismatch")
        manifest_records[raw["path"]] = raw
    expected_paths = {
        path.removeprefix("/") for path in AGENT_OS_RUNTIME_GATE_MODES}
    if set(manifest_records) != expected_paths:
        fail("native VM Agent OS runtime input file closure mismatch")
    for absolute_text, mode in sorted(AGENT_OS_RUNTIME_GATE_MODES.items()):
        current = stable_root_file(
            Path(absolute_text), mode, executable=bool(mode & stat.S_IXUSR))
        expected_record = manifest_records[absolute_text.removeprefix("/")]
        if (current["mode"] != expected_record["mode"] or
                current["size"] != expected_record["size"] or
                current["sha256"] != expected_record["sha256"]):
            fail("native VM Agent OS runtime input digest mismatch")
        stable_records.append(current)
    return {
        "runtime_input_manifest_sha256": record["sha256"],
        "runtime_input_file_count": len(stable_records),
        "runtime_inputs": stable_records,
        "runtime_input_records_sha256": input_manifest_sha256(stable_records),
        "runtime_input_content_sha256":
            input_content_manifest_sha256(stable_records),
    }


def gate_input_records(
        runner_path: Path, shared_runner_path: Path) -> list[dict[str, Any]]:
    records = [
        stable_root_file(runner_path, 0o755, executable=True),
        stable_root_file(shared_runner_path, 0o755, executable=True),
        stable_root_file(INNER_RUNNER, 0o755, executable=True),
        stable_root_file(PREFLIGHT, 0o755, executable=True),
        stable_root_file(VARIANT_PATH, 0o444),
        stable_root_file(IMAGE_DIGEST_PATH, 0o444),
        stable_root_file(IMAGE_MANIFEST_PATH, 0o444),
        stable_root_file(PROVISIONING_MANIFEST_PATH, 0o444),
        stable_root_file(PLATFORM_POLICY_PATH, 0o444),
        stable_root_file(CLEAN_SOURCE_PROVENANCE_PATH, 0o444),
        stable_root_file(AGENT_OS_INSTALLATION_MANIFEST_PATH, 0o444),
        stable_root_file(INNER_SENTINEL, 0o400),
    ]
    records.extend(
        stable_root_file(
            Path(path), mode, executable=bool(mode & stat.S_IXUSR))
        for path, mode in sorted(AGENT_OS_STATIC_MODES.items()))
    return records


def validate_image_manifest(
        sentinel: dict[str, Any], image_manifest_record: dict[str, Any],
        provisioning_record: dict[str, Any],
        platform_policy_record: dict[str, Any],
        clean_source_record: dict[str, Any],
        agent_os_installation_record: dict[str, Any],
        clean_source: dict[str, Any]) -> None:
    try:
        manifest = json.loads(shared.read_regular_file(
            IMAGE_MANIFEST_PATH, maximum=4 * 1024 * 1024)[1].decode(
                "utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("native VM image manifest is not valid UTF-8 JSON")
    expected = {
        "schema", "variant", "platform_policy_sha256",
        "clean_source_provenance_sha256", "clean_source",
        "provisioning_manifest_sha256",
        "agent_os_installation_manifest_sha256",
        "agent_os_runtime_input_manifest_sha256",
        "agent_os_installation_preflight_staged",
        "agent_os_runtime_gate_inputs_staged",
        "agent_os_runtime_preflight_required",
        "agent_os_runtime_artifacts_staged", "files",
        "formal_ibapi_elf_staged", "instance_identity_staged",
        "paper_authorized", "live_enabled",
    }
    if (not isinstance(manifest, dict) or set(manifest) != expected or
            manifest.get("schema") !=
            "hepta.execution-native-vm-image-manifest.v4" or
            manifest.get("variant") != sentinel["variant"] or
            manifest.get("platform_policy_sha256") !=
            sentinel["platform_policy_sha256"] or
            manifest.get("clean_source_provenance_sha256") !=
            clean_source_record["sha256"] or
            manifest.get("clean_source") != clean_source or
            manifest.get("provisioning_manifest_sha256") !=
            sentinel["provisioning_manifest_sha256"] or
            manifest.get("agent_os_installation_manifest_sha256") !=
            agent_os_installation_record["sha256"] or
            manifest.get("agent_os_runtime_input_manifest_sha256") !=
            stable_root_file(
                AGENT_OS_RUNTIME_INPUT_MANIFEST_PATH, 0o444)["sha256"] or
            manifest.get("agent_os_installation_preflight_staged") is not
            True or
            manifest.get("agent_os_runtime_gate_inputs_staged") is not True or
            manifest.get("agent_os_runtime_preflight_required") is not True or
            manifest.get("agent_os_runtime_artifacts_staged") is not False or
            any(manifest.get(key) is not False for key in (
                "formal_ibapi_elf_staged", "instance_identity_staged",
                "paper_authorized", "live_enabled")) or
            not isinstance(manifest.get("files"), list) or
            not manifest["files"]):
        fail("native VM image manifest contract mismatch")
    if (image_manifest_record["sha256"] !=
            sentinel["vm_image_manifest_sha256"] or
            provisioning_record["sha256"] !=
            sentinel["provisioning_manifest_sha256"] or
            platform_policy_record["sha256"] !=
            sentinel["platform_policy_sha256"]):
        fail("native VM image manifest binding mismatch")

    observed: set[str] = set()
    forbidden = {
        "etc/heptatrader/hepta-native-systemd-gate.disposable",
        "run/hepta-rootful-systemd-gate.disposable",
        "usr/libexec/hepta-ib-executiond-formal",
        "usr/local/share/hepta-rootful-systemd-gate/image-manifest.json",
        "usr/local/share/hepta-rootful-systemd-gate/image-manifest.sha256",
    }
    required = {
        "usr/libexec/hepta-executiond",
        "usr/libexec/hepta-ib-executiond",
        "usr/local/libexec/check_hepta_execution_provisioned_host.py",
        "usr/local/libexec/run_hepta_execution_rootful_systemd_gate.py",
        "usr/local/libexec/run_hepta_execution_native_systemd_gate.py",
        "usr/local/libexec/hepta_execution_rootful_inner_gate.py",
        "usr/local/libexec/hepta_execution_systemd_client_probe",
        "usr/local/libexec/hepta_execution_systemd_sandbox_probe",
        "usr/local/libexec/hepta-ib-executiond-disabled",
        "usr/local/share/hepta-rootful-systemd-gate/formal-ibapi.sha256",
        "usr/local/share/hepta-rootful-systemd-gate/clean-source-provenance.json",
        "usr/local/share/hepta-rootful-systemd-gate/"
        "agent-os-installation-manifest.json",
        "usr/local/share/hepta-rootful-systemd-gate/platform-policy.json",
        "usr/local/share/hepta-rootful-systemd-gate/provisioning-manifest.json",
        "usr/local/share/hepta-rootful-systemd-gate/variant",
    } | {
        path.removeprefix("/")
        for path in set(AGENT_OS_STATIC_MODES) |
        set(AGENT_OS_RUNTIME_GATE_MODES)
    }
    for raw_record in manifest["files"]:
        if not isinstance(raw_record, dict) or set(raw_record) != {
                "path", "mode", "uid", "gid", "size", "sha256"}:
            fail("native VM image manifest file record mismatch")
        path = raw_record.get("path")
        mode = raw_record.get("mode")
        if (not isinstance(path, str) or not path or path.startswith("/") or
                ".." in Path(path).parts or path in forbidden or
                path in observed or
                not isinstance(mode, str) or
                re.fullmatch(r"0[0-7]{3}", mode) is None or
                raw_record.get("uid") != 0 or raw_record.get("gid") != 0 or
                type(raw_record.get("size")) is not int or
                raw_record["size"] < 0 or
                not isinstance(raw_record.get("sha256"), str) or
                HEX_64.fullmatch(raw_record["sha256"]) is None):
            fail("native VM image manifest file value mismatch")
        absolute = Path("/") / path
        record = stable_root_file(
            absolute, int(mode, 8),
            executable=bool(int(mode, 8) & stat.S_IXUSR))
        if (record["size"] != raw_record["size"] or
                record["sha256"] != raw_record["sha256"]):
            fail("native VM image manifest file digest mismatch")
        observed.add(path)
    if not required.issubset(observed):
        fail("native VM image manifest is missing a required payload file")


def validate_baked_metadata(sentinel: dict[str, Any]) -> dict[str, Any]:
    stable_root_file(VARIANT_PATH, 0o444)
    stable_root_file(IMAGE_DIGEST_PATH, 0o444)
    image_manifest = stable_root_file(IMAGE_MANIFEST_PATH, 0o444)
    provisioning_manifest = stable_root_file(
        PROVISIONING_MANIFEST_PATH, 0o444)
    platform_policy = stable_root_file(PLATFORM_POLICY_PATH, 0o444)
    clean_source_record = stable_root_file(
        CLEAN_SOURCE_PROVENANCE_PATH, 0o444)
    agent_os_installation_record = stable_root_file(
        AGENT_OS_INSTALLATION_MANIFEST_PATH, 0o444)
    try:
        clean_source = json.loads(shared.read_regular_file(
            CLEAN_SOURCE_PROVENANCE_PATH, maximum=1024 * 1024)[1].decode(
                "utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("native VM clean source provenance is invalid")
    if (not isinstance(clean_source, dict) or
            clean_source.get("bundle_sha256") !=
            sentinel["clean_source_bundle_sha256"] or
            clean_source.get("manifest_sha256") !=
            sentinel["clean_source_manifest_sha256"] or
            clean_source.get("files_sha256") !=
            sentinel["clean_source_files_sha256"] or
            clean_source.get("paper_authorized") is not False or
            clean_source.get("live_authorized") is not False):
        fail("native VM clean source provenance does not match sentinel")
    stable_root_file(INNER_SENTINEL, 0o400)
    variant = shared.read_regular_file(VARIANT_PATH, maximum=64)[1].decode(
        "ascii", errors="strict").strip()
    image_digest = shared.read_regular_file(
        IMAGE_DIGEST_PATH, maximum=128)[1].decode(
            "ascii", errors="strict").strip()
    inner_run_id = shared.read_regular_file(
        INNER_SENTINEL, maximum=128)[1].decode(
            "ascii", errors="strict").strip()
    if (variant != sentinel["variant"] or
            image_digest != sentinel["vm_image_manifest_sha256"] or
            image_manifest["sha256"] !=
            sentinel["vm_image_manifest_sha256"] or
            provisioning_manifest["sha256"] !=
            sentinel["provisioning_manifest_sha256"] or
            platform_policy["sha256"] !=
            sentinel["platform_policy_sha256"] or
            inner_run_id != sentinel["run_id"]):
        fail("native VM baked metadata does not match the disposable sentinel")
    validate_image_manifest(
        sentinel, image_manifest, provisioning_manifest, platform_policy,
        clean_source_record, agent_os_installation_record, clean_source)
    installation = validate_agent_os_installation_manifest(
        agent_os_installation_record)
    runtime_inputs = validate_agent_os_runtime_input_manifest()
    if (installation["runtime_input_manifest_sha256"] !=
            runtime_inputs["runtime_input_manifest_sha256"]):
        fail("native VM Agent OS runtime input lineage mismatch")
    return {**installation, **runtime_inputs}


def network_record() -> dict[str, Any]:
    addresses = json_command(["/usr/sbin/ip", "-json", "address", "show"],
                             "ip address")
    routes = json_command(
        ["/usr/sbin/ip", "-json", "route", "show", "table", "all"],
        "ip route")
    return parse_network_isolation(addresses, routes)


def _runtime_write(path: Path, contents: bytes, mode: int) -> None:
    shared.write_private(path, contents, mode)
    os.chown(path, 0, 0)
    os.chmod(path, mode)


def _runtime_copy(source: Path, destination: Path, mode: int) -> None:
    contents = shared.read_regular_file(source, maximum=1024 * 1024)[1]
    _runtime_write(destination, contents, mode)


def run_agent_os_runtime_gate(
        run_id: str, progress: NativeGateProgress) -> dict[str, Any]:
    if HEX_32.fullmatch(run_id) is None:
        fail("native VM Agent OS runtime run identity is invalid")
    if AGENT_OS_RUNTIME_SENTINEL.exists() or \
            AGENT_OS_RUNTIME_SENTINEL.is_symlink():
        fail("native VM Agent OS runtime sentinel already exists")
    for path in (
            Path("/run/hepta-agent/tools.sock"),
            Path("/run/hepta-agent/session.token"),
            Path("/run/hepta-tool-gateway/session-supervisor.sock"),
            Path("/run/hepta-execution/execution.sock"),
            Path("/run/hepta-execution/events.sock")):
        if path.exists() or path.is_symlink():
            fail("native VM Agent OS runtime artifact exists before execution")

    hidden: list[tuple[Path, Path]] = []
    mounted = False
    sentinel_created = False
    result: Optional[dict[str, Any]] = None
    primary_error: Optional[BaseException] = None
    cleanup_errors: list[str] = []
    try:
        shared.command([
            "/usr/bin/systemctl", "stop",
            "hepta-tool-gateway.service",
            "hepta-execution-simulator.service",
            "hepta-tool-gateway.socket",
            "hepta-tool-session-supervisor.socket",
            "hepta-execution-simulator.socket",
            "hepta-execution-events-simulator.socket",
        ], timeout=60, check=False)
        for source in AGENT_OS_RUNTIME_HIDDEN_SURFACES:
            metadata = os.lstat(source)
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or
                    metadata.st_gid != 0 or metadata.st_nlink != 1):
                fail("native VM broker surface cannot be safely isolated")
            destination = source.with_name(
                "." + source.name + ".hepta-watch-runtime-" + run_id)
            if destination.exists() or destination.is_symlink():
                fail("native VM broker-surface quarantine path already exists")
            os.rename(source, destination)
            hidden.append((source, destination))
        shared.command(
            ["/usr/bin/systemctl", "daemon-reload"], timeout=30)

        shared.command([
            "/usr/bin/mount", "-t", "tmpfs", "-o",
            "rw,nosuid,nodev,noexec,mode=0755,size=4m",
            "tmpfs", "/etc/heptatrader",
        ], timeout=30)
        mounted = True
        credentials = Path("/etc/heptatrader/credentials")
        credentials.mkdir(mode=0o700)
        os.chown(credentials, 0, 0)
        os.chmod(credentials, 0o700)
        _runtime_copy(
            AGENT_OS_RUNTIME_PROVISIONING / "hepta-tool-gateway.env",
            Path("/etc/heptatrader/hepta-tool-gateway.env"), 0o644)
        _runtime_copy(
            AGENT_OS_RUNTIME_PROVISIONING /
            "hepta-execution-simulator.env",
            Path("/etc/heptatrader/hepta-execution-simulator.env"), 0o644)
        _runtime_copy(
            AGENT_OS_RUNTIME_PROVISIONING /
            "hepta-execution-simulator-fence",
            credentials / "hepta-execution-simulator-fence", 0o400)
        _runtime_write(
            Path("/etc/heptatrader/hepta-supervisor-lease.key"),
            os.urandom(32).hex().encode("ascii") + b"\n", 0o400)
        _runtime_write(
            AGENT_OS_RUNTIME_SENTINEL,
            (run_id + "\n").encode("ascii"), 0o400)
        sentinel_created = True

        progress.phase = "agent_os_four_uid_watch_runtime"
        progress.agent_os_runtime_gate_started = True
        inner = shared.command([
            "/usr/bin/python3", str(AGENT_OS_RUNTIME_INNER),
        ], timeout=420, check=False)
        if inner.returncode != 0:
            fail(f"native Agent OS runtime gate exited {inner.returncode}")
        result = parse_agent_os_runtime_result(inner.stdout)
        progress.agent_os_runtime_gate_completed = True
    except BaseException as error:
        primary_error = error
    finally:
        stopped = shared.command([
            "/usr/bin/systemctl", "stop",
            "hepta-tool-gateway.service",
            "hepta-execution-simulator.service",
            "hepta-tool-gateway.socket",
            "hepta-tool-session-supervisor.socket",
            "hepta-execution-simulator.socket",
            "hepta-execution-events-simulator.socket",
        ], timeout=60, check=False)
        if stopped.returncode != 0:
            cleanup_errors.append("systemd runtime cleanup failed")
        if sentinel_created:
            try:
                AGENT_OS_RUNTIME_SENTINEL.unlink()
            except OSError:
                cleanup_errors.append("runtime sentinel cleanup failed")
        if mounted:
            unmounted = shared.command(
                ["/usr/bin/umount", "/etc/heptatrader"],
                timeout=30, check=False)
            if unmounted.returncode != 0:
                cleanup_errors.append("runtime tmpfs cleanup failed")
        for source, destination in reversed(hidden):
            try:
                if source.exists() or source.is_symlink():
                    cleanup_errors.append(
                        "broker-surface restore target is occupied")
                    continue
                os.rename(destination, source)
            except OSError:
                cleanup_errors.append("broker-surface restore failed")
        reloaded = shared.command(
            ["/usr/bin/systemctl", "daemon-reload"],
            timeout=30, check=False)
        if reloaded.returncode != 0:
            cleanup_errors.append("systemd reload after runtime failed")
    if cleanup_errors:
        fail("native Agent OS runtime cleanup did not close exactly")
    if primary_error is not None:
        raise primary_error
    if result is None:
        fail("native Agent OS runtime completed without a result")
    return result


def validate_report_path(path: Path, variant: str) -> Path:
    absolute = Path(os.path.abspath(path))
    expected_name = f"execution-native-systemd-{variant}.json"
    if absolute.name != expected_name:
        fail("native VM report name is outside the explicit allowlist")
    parent = absolute.parent.resolve(strict=True)
    metadata = os.lstat(parent)
    if (absolute.parent != parent or not stat.S_ISDIR(metadata.st_mode) or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) & 0o022):
        fail("native VM report directory is unsafe")
    try:
        target = os.lstat(absolute)
    except FileNotFoundError:
        target = None
    if target is not None and (
            not stat.S_ISREG(target.st_mode) or target.st_nlink != 1 or
            target.st_uid != 0 or target.st_gid != 0 or
            stat.S_IMODE(target.st_mode) != 0o600):
        fail("existing native VM report is unsafe")
    return absolute


def failure_report(
        error: Exception, progress: NativeGateProgress,
        variant: str) -> dict[str, Any]:
    exact = not progress.inner_gate_started
    return {
        "schema": SCHEMA,
        "passed": False,
        "certification_level":
            "native-disposable-vm-agent-os-watch-runtime-systemd-variant",
        "variant": variant if variant in VARIANTS else "invalid",
        "error_type": type(error).__name__,
        "error": str(error).replace(str(REPOSITORY), ".")[:512],
        "failure_stage": {
            "phase": progress.phase,
            "inner_gate_started": progress.inner_gate_started,
            "inner_gate_completed": progress.inner_gate_completed,
            "agent_os_runtime_gate_started":
                progress.agent_os_runtime_gate_started,
            "agent_os_runtime_gate_completed":
                progress.agent_os_runtime_gate_completed,
        },
        "boundary": {
            "real_ibapi_elf_executed": False if exact else "unknown",
            "real_broker_connections": 0 if exact else "unknown",
            "paper_orders": 0 if exact else "unknown",
            "live_enabled": False if exact else "unknown",
            "paper_authorized": False,
            "agent_os_installation_preflight":
                progress.agent_os_installation_preflight_completed,
            "agent_os_runtime_preflight_executed": False,
            "agent_os_runtime_preflight_required": True,
            "agent_os_runtime_evidence_fabricated": False,
        },
    }


def _validate_instance_receipt_binding(
        verification: dict[str, Any], sentinel: dict[str, Any],
        host: dict[str, Any], variant: str) -> None:
    statement = verification.get("statement")
    expected_source = {
        "bundle_sha256": sentinel["clean_source_bundle_sha256"],
        "manifest_sha256": sentinel["clean_source_manifest_sha256"],
        "files_sha256": sentinel["clean_source_files_sha256"],
    }
    if (not isinstance(statement, dict) or
            statement.get("challenge") != sentinel["instance_challenge"] or
            statement.get("variant") != variant or
            statement.get("vm_type") != host["vm_type"] or
            statement.get("boot_id") != host["boot_id"] or
            statement.get("run_id") != sentinel["run_id"] or
            statement.get("vm_image_manifest_sha256") !=
                sentinel["vm_image_manifest_sha256"] or
            statement.get("provisioning_manifest_sha256") !=
                sentinel["provisioning_manifest_sha256"] or
            statement.get("source_lineage") != expected_source):
        fail("native instance receipt does not bind this exact gate run")


def execute(
        variant: str, progress: NativeGateProgress,
        instance_receipt_path: Path) -> dict[str, Any]:
    host = local_host_record()
    progress.phase = "disposable_sentinel_validation"
    sentinel = read_anchored_sentinel(
        host["machine_id"], host["boot_id"], variant)
    expected_receipt_name = (
        f"execution-native-systemd-{variant}-instance-receipt.json")
    if (Path(os.path.abspath(instance_receipt_path)).name !=
            expected_receipt_name):
        fail("native instance receipt name is outside the explicit allowlist")
    progress.phase = "external_instance_identity_validation"
    instance_identity = verify_instance_receipt(instance_receipt_path)
    _validate_instance_receipt_binding(
        instance_identity, sentinel, host, variant)
    agent_os = validate_baked_metadata(sentinel)
    progress.phase = "network_isolation_validation"
    before_network = network_record()

    runner_path = Path(__file__).absolute()
    shared_runner_path = Path(shared.__file__).absolute()
    inputs = gate_input_records(runner_path, shared_runner_path)
    progress.phase = "provisioned_host_preflight"
    preflight = shared.command(
        ["/usr/bin/python3", str(PREFLIGHT), "--root", "/"], timeout=30)
    if (not preflight.stdout.startswith(
            "hepta_execution_provisioned_host: PASS ") or
            preflight.stdout.count("\n") != 1):
        fail("native VM provisioned-host preflight contract failed")

    progress.phase = "agent_os_installation_preflight"
    agent_preflight = shared.command([
        "/usr/bin/python3", str(AGENT_OS_PREFLIGHT),
        "--root", "/", "--installation-only",
    ], timeout=30)
    if (not agent_preflight.stdout.startswith(
            "hepta_agent_os_provisioned_host: PASS "
            "mode=installation-only ") or
            agent_preflight.stdout.count("\n") != 1):
        fail("native VM Agent OS installation preflight contract failed")
    progress.agent_os_installation_preflight_completed = True

    progress.phase = "inner_systemd_gate"
    progress.inner_gate_started = True
    environment = dict(shared.COMMAND_ENV)
    environment.update({
        "HEPTA_ROOTFUL_GATE_DISPOSABLE": "1",
        "HEPTA_ROOTFUL_GATE_MODE": variant,
        "HEPTA_ROOTFUL_GATE_SCOPE": NATIVE_SCOPE,
        "HEPTA_ROOTFUL_GATE_RUN_ID": sentinel["run_id"],
    })
    inner = shared.command([
        "/usr/bin/python3", str(INNER_RUNNER),
        "--mode", variant, "--scope", NATIVE_SCOPE,
    ], timeout=300, check=False, environment=environment)
    if inner.returncode != 0:
        fail(f"native inner gate exited {inner.returncode}")
    parsed = shared.parse_inner_result(
        inner.stdout, variant, expected_scope=NATIVE_SCOPE)
    progress.inner_gate_completed = True

    runtime_inputs = agent_os["runtime_inputs"]
    runtime_result = run_agent_os_runtime_gate(
        sentinel["run_id"], progress)
    runtime_result_sha256 = hashlib.sha256(
        canonical_json(runtime_result)).hexdigest()
    runtime_lifecycle_sha256 = hashlib.sha256(
        canonical_json(runtime_result["lifecycle"])).hexdigest()

    progress.phase = "post_gate_revalidation"
    after_host = local_host_record()
    after_sentinel = read_anchored_sentinel(
        after_host["machine_id"], after_host["boot_id"], variant)
    after_instance_identity = verify_instance_receipt(instance_receipt_path)
    _validate_instance_receipt_binding(
        after_instance_identity, after_sentinel, after_host, variant)
    after_agent_os = validate_baked_metadata(after_sentinel)
    if after_host != host or after_sentinel != sentinel:
        fail("native VM host identity changed during the gate")
    if ({key: value for key, value in after_instance_identity.items()
         if key != "verified_at_ms"} !=
            {key: value for key, value in instance_identity.items()
             if key != "verified_at_ms"}):
        fail("native instance receipt changed during the gate")
    if after_agent_os != agent_os:
        fail("native VM Agent OS installation/runtime input closure changed")
    if network_record() != before_network:
        fail("native VM network isolation changed during the gate")
    after_inputs = gate_input_records(runner_path, shared_runner_path)
    if after_inputs != inputs:
        fail("native VM gate inputs changed during execution")
    if after_agent_os["runtime_inputs"] != runtime_inputs:
        fail("native VM Agent OS runtime inputs changed during execution")
    agent_os_summary = {
        key: value for key, value in agent_os.items()
        if key not in {
            "runtime_inputs", "runtime_input_records_sha256",
            "runtime_input_content_sha256",
        }
    }
    agent_os_summary["runtime_preflight_executed"] = True
    runtime_inner_sha256 = next(
        record["sha256"] for record in runtime_inputs
        if record["path"] == str(AGENT_OS_RUNTIME_INNER))
    progress.phase = "complete"
    return {
        "schema": SCHEMA,
        "passed": True,
        "certification_level":
            "native-disposable-vm-agent-os-watch-runtime-systemd-variant",
        "variant": variant,
        "host": {
            "vm_type": host["vm_type"],
            "systemd_pid1": True,
            "cgroup_v2_root": True,
            "docker_socket_absent": True,
            "kernel_release": host["kernel_release"],
        },
        "instance_identity": after_instance_identity,
        "disposable_sentinel": {
            "contract": SENTINEL_HEADER,
            "root_owned": True,
            "mode": "0400",
            "single_link": True,
            "machine_id_bound": True,
            "boot_id_bound": True,
            "machine_id_sha256": sha256_text(sentinel["machine_id"]),
            "boot_id_sha256": sha256_text(sentinel["boot_id"]),
            "vm_image_manifest_sha256":
                sentinel["vm_image_manifest_sha256"],
            "provisioning_manifest_sha256":
                sentinel["provisioning_manifest_sha256"],
            "platform_policy_sha256": sentinel["platform_policy_sha256"],
            "clean_source_bundle_sha256":
                sentinel["clean_source_bundle_sha256"],
            "clean_source_manifest_sha256":
                sentinel["clean_source_manifest_sha256"],
            "clean_source_files_sha256":
                sentinel["clean_source_files_sha256"],
            "run_id_bound": True,
            "run_id_sha256": sha256_text(sentinel["run_id"]),
            "instance_challenge_bound": True,
            "instance_challenge_sha256":
                sha256_text(sentinel["instance_challenge"]),
        },
        "network_isolation": before_network,
        "agent_os": agent_os_summary,
        "agent_os_runtime": {
            "source": "real-native-vm-rootful-inner-process",
            "result_schema": AGENT_OS_RUNTIME_RESULT_SCHEMA,
            "result_parse_verified": True,
            "runtime_preflight_executed": True,
            "runtime_preflight_required": True,
            "runtime_input_manifest_sha256":
                agent_os["runtime_input_manifest_sha256"],
            "runtime_input_records_sha256":
                agent_os["runtime_input_records_sha256"],
            "runtime_input_content_sha256":
                agent_os["runtime_input_content_sha256"],
            "runtime_inner_gate_sha256": runtime_inner_sha256,
            "runtime_result_sha256": runtime_result_sha256,
            "runtime_lifecycle_sha256": runtime_lifecycle_sha256,
            "identities": runtime_result["identities"],
            "watch_tools": list(AGENT_OS_WATCH_TOOLS),
            "read_probes": list(AGENT_OS_READ_PROBES),
            "lifecycle": runtime_result["lifecycle"],
            "checks": runtime_result["checks"],
            "watch_session_revoked": True,
            "runtime_cleanup_complete": True,
            "ib_adapter_visible_during_runtime": False,
            "paper_authorized": False,
            "live_authorized": False,
            "real_broker_connections": 0,
            "paper_orders": 0,
            "inner": runtime_result,
        },
        "runtime_inputs": runtime_inputs,
        "runtime_input_stability": True,
        "inputs": inputs,
        "input_stability": True,
        "inner": parsed,
        "boundary": {
            "real_ibapi_elf_executed": False,
            "real_broker_connections": 0,
            "paper_orders": 0,
            "live_enabled": False,
            "paper_authorized": False,
            "agent_os_installation_preflight": True,
            "agent_os_runtime_preflight_executed": True,
            "agent_os_runtime_preflight_required": True,
            "agent_os_runtime_evidence_fabricated": False,
            "final_native_gate":
                "four_uid_watch_runtime_variant_requires_three_distinct_"
                "native_vm_runtime_aggregation",
        },
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="one disposable native-VM Hepta systemd gate variant")
    parser.add_argument("--variant", choices=VARIANTS, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--instance-receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report_path = validate_report_path(args.report, args.variant)
    except Exception as error:
        print(f"hepta_native_systemd_gate: unsafe report path: {error}",
              file=sys.stderr)
        return 2
    progress = NativeGateProgress()
    exit_code = 0
    try:
        report = execute(args.variant, progress, args.instance_receipt)
    except Exception as error:
        exit_code = 1
        report = failure_report(error, progress, args.variant)
    try:
        shared.atomic_report(report_path, report)
    except Exception as error:
        print(f"hepta_native_systemd_gate: report failure: {error}",
              file=sys.stderr)
        return 2
    if exit_code:
        print("hepta_native_systemd_gate: FAIL", file=sys.stderr)
        return exit_code
    print("hepta_native_systemd_gate: PASS "
          f"variant={args.variant} "
          "level=native-disposable-vm-agent-os-watch-runtime-systemd-variant")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
