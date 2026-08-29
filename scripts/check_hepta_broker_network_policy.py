#!/usr/bin/env python3

"""Static fail-closed gate for the Agent-to-broker OS network boundary."""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import stat
import sys
from typing import Optional

import hepta_ib_paper_domain_authority as PAPER_AUTHORITY


ROOT = Path(__file__).resolve().parents[1]
POLICY_HELPER_PATH = ROOT / "scripts/hepta_broker_egress_policy.py"
SPEC = importlib.util.spec_from_file_location(
    "hepta_broker_egress_policy_for_checker", POLICY_HELPER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot import broker egress policy helper")
POLICY_HELPER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = POLICY_HELPER
SPEC.loader.exec_module(POLICY_HELPER)

POLICY_PATH = Path("systemd/hepta-broker-network-policy-v1.json")
IDENTITIES_PATH = Path("systemd/hepta-service-identities-v1.json")
PAPER_IDENTITIES_EXAMPLE = Path(
    "systemd/"
    "hepta-agent-trust-domain-paper-identities-v1.json.example")
PAPER_AUTHORITY_EXAMPLE = Path(
    "systemd/hepta-ib-paper-domain-authorizations-v1.json.example")
PAPER_PREFLIGHT_UNIT = Path(
    "systemd/hepta-ib-paper-domain-preflight@.service")
UNIT_PATH = Path("systemd/hepta-broker-egress-policy.service")
FIXED_IB_SOCKET = Path("systemd/hepta-execution-ib-paper.socket")
FIXED_IB_EVENT_SOCKET = Path(
    "systemd/hepta-execution-events-ib-paper.socket")
DOMAIN_IB_SERVICE = Path("systemd/hepta-execution-ib-paper@.service")
DOMAIN_IB_SOCKET = Path("systemd/hepta-execution-ib-paper@.socket")
DOMAIN_IB_EVENT_SOCKET = Path(
    "systemd/hepta-execution-events-ib-paper@.socket")
DOMAIN_IB_ENV_EXAMPLE = Path(
    "systemd/hepta-execution-ib-paper-domain.env.example")
AGENT_DROPIN = Path(
    "systemd/hepta-agent-broker-egress-policy.conf.example")
DEPENDENCY_DROPINS = (
    Path("systemd/hepta-tool-gateway.service.d/"
         "10-hepta-broker-egress-policy.conf"),
    Path("systemd/hepta-tool-gateway@.service.d/"
         "10-hepta-broker-egress-policy.conf"),
    Path("systemd/hepta-execution-ib-paper.service.d/"
         "10-hepta-broker-egress-policy.conf"),
    Path("systemd/hepta-execution-ib-paper@.service.d/"
         "10-hepta-broker-egress-policy.conf"),
)
DOCUMENTATION_PATH = Path("docs/BROKER-NETWORK-ISOLATION.md")

Unit = dict[str, dict[str, list[str]]]


class CheckFailure(RuntimeError):
    pass


def fail(message: str) -> None:
    raise CheckFailure(message)


def safe_bytes(
        root: Path, relative: Path, maximum: int = 1024 * 1024) -> bytes:
    path = root / relative
    metadata = path.lstat()
    if (
            not stat.S_ISREG(metadata.st_mode) or
            stat.S_ISLNK(metadata.st_mode) or
            metadata.st_nlink != 1 or
            metadata.st_size < 1 or
            metadata.st_size > maximum or
            stat.S_IMODE(metadata.st_mode) & 0o002):
        fail(f"{relative}: unsafe source metadata")
    descriptor = os.open(
        path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        contents = bytearray()
        while len(contents) <= maximum:
            chunk = os.read(descriptor, min(65536, maximum + 1 - len(contents)))
            if not chunk:
                break
            contents.extend(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns")
    if (
            len(contents) > maximum or
            any(
                getattr(metadata, field) != getattr(opened, field) or
                getattr(opened, field) != getattr(after, field)
                for field in fields)):
        fail(f"{relative}: source changed while reading")
    return bytes(contents)


def safe_text(root: Path, relative: Path, maximum: int = 1024 * 1024) -> str:
    return safe_bytes(root, relative, maximum).decode(
        "utf-8", errors="strict")


def parse_unit(text: str, relative: Path) -> Unit:
    sections: Unit = {}
    current = ""
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            if not current or current in sections:
                fail(f"{relative}:{line_number}: invalid/duplicate section")
            sections[current] = {}
            continue
        if not current or "=" not in line:
            fail(f"{relative}:{line_number}: invalid directive")
        key, value = line.split("=", 1)
        if not key:
            fail(f"{relative}:{line_number}: empty directive")
        sections[current].setdefault(key, []).append(value)
    return sections


def exact_dependency_dropin(root: Path, relative: Path) -> None:
    observed = parse_unit(safe_text(root, relative), relative)
    expected = {
        "Unit": {
            "BindsTo": ["hepta-broker-egress-policy.service"],
            "After": ["hepta-broker-egress-policy.service"],
        },
    }
    if observed != expected:
        fail(f"{relative}: broker policy dependency drifted")


def check(root: Path) -> None:
    policy_raw = safe_bytes(root, POLICY_PATH)
    identity_raw = safe_bytes(root, IDENTITIES_PATH)
    paper_identity_example = safe_bytes(root, PAPER_IDENTITIES_EXAMPLE)
    paper_authority_example = safe_bytes(root, PAPER_AUTHORITY_EXAMPLE)
    parsed = POLICY_HELPER.parse_policy(
        policy_raw, identity_raw, paper_identity_example)
    if (
            POLICY_HELPER.MAX_PAPER_IDENTITIES != 1 or
            PAPER_AUTHORITY.MAX_DOMAINS != 1):
        fail("per-host templated PAPER domain limit is not exactly one")
    rendered = POLICY_HELPER.render_transaction(parsed).decode("ascii")
    if PAPER_AUTHORITY.parse_authorities(
            paper_identity_example, paper_authority_example) != ():
        fail("default per-domain PAPER authority is not empty")
    if (
            parsed.authorized_uids != (2003,) or
            parsed.authorized_connectors[0].identity != "hepta-ib-exec" or
            parsed.ports != (4001, 4002, 7496, 7497) or
            "jump ib_guard" not in rendered or
            "meta skuid 2003 counter return" not in rendered or
            "meta skuid !=" in rendered or
            "policy accept" not in rendered or
            "policy drop" in rendered):
        fail("effective broker network policy contract drifted")

    unit = parse_unit(safe_text(root, UNIT_PATH), UNIT_PATH)
    expected_unit = {
        "Unit": {
            "Description": ["HeptaTrader broker-port egress boundary"],
            "Documentation": [
                "file:/usr/share/doc/heptatrader/"
                "BROKER-NETWORK-ISOLATION.md"],
            "DefaultDependencies": ["no"],
            "After": ["systemd-modules-load.service local-fs.target"],
            "Before": [
                "network-pre.target hepta-tool-gateway.service "
                "hepta-execution-ib-paper.service"],
            "Wants": ["network-pre.target"],
        },
        "Service": {
            "Type": ["notify"],
            "NotifyAccess": ["main"],
            "User": ["root"],
            "Group": ["root"],
            "LoadCredential": [
                "hepta-broker-egress-policy.py:"
                "/usr/libexec/hepta-broker-egress-policy",
                "hepta-local-paper-control.py:"
                "/usr/libexec/hepta-local-paper-control",
            ],
            "ExecStartPre": [
                "/usr/bin/python3.12 -I -S "
                "${CREDENTIALS_DIRECTORY}/"
                "hepta-local-paper-control.py reconcile-before-broker"],
            "ExecStart": [
                "/usr/bin/python3.12 -I -S "
                "${CREDENTIALS_DIRECTORY}/"
                "hepta-broker-egress-policy.py --supervise-deny-all "
                "--paper-identities /etc/heptatrader/"
                "hepta-agent-trust-domain-paper-identities-v1.json"],
            "ExecStopPost": [
                "/usr/bin/python3.12 -I -S "
                "${CREDENTIALS_DIRECTORY}/"
                "hepta-broker-egress-policy.py "
                "--tighten-deny-all"],
            "UMask": ["0077"],
            "RuntimeDirectory": ["hepta-broker-egress-policy"],
            "RuntimeDirectoryMode": ["0700"],
            "RuntimeDirectoryPreserve": ["yes"],
            "WatchdogSec": ["15s"],
            "WatchdogSignal": ["SIGTERM"],
            "TimeoutStopSec": ["30s"],
            "KillSignal": ["SIGTERM"],
            "Restart": ["no"],
            "NoNewPrivileges": ["yes"],
            "PrivateTmp": ["yes"],
            "PrivateDevices": ["yes"],
            "ProtectSystem": ["strict"],
            "ProtectHome": ["yes"],
            "ProtectKernelTunables": ["yes"],
            "ProtectKernelModules": ["yes"],
            "ProtectKernelLogs": ["yes"],
            "ProtectControlGroups": ["yes"],
            "ProtectClock": ["yes"],
            "ProtectHostname": ["yes"],
            "RestrictSUIDSGID": ["yes"],
            "RestrictRealtime": ["yes"],
            "RestrictNamespaces": ["yes"],
            "LockPersonality": ["yes"],
            "MemoryDenyWriteExecute": ["yes"],
            "CapabilityBoundingSet": ["CAP_NET_ADMIN"],
            "AmbientCapabilities": [""],
            "RestrictAddressFamilies": ["AF_UNIX AF_NETLINK"],
            "ReadOnlyPaths": [
                "/usr/share/heptatrader",
                "-/var/lib/hepta-local-ai-paper-agent",
            ],
            "ReadWritePaths": [
                "/etc/heptatrader",
                "/etc/systemd/system/"
                "hepta-broker-egress-policy.service.d",
                "-/run/hepta-local-paper-control",
                "-/run/hepta/ib-paper-host-authority",
            ],
            "BindReadOnlyPaths": [
                "-/etc/heptatrader/credentials",
                "-/etc/heptatrader/paper-campaigns",
                "-/etc/heptatrader/p1-safety-soak",
                "-/etc/heptatrader/trust-domains",
                "-/etc/heptatrader/"
                "hepta-agent-trust-domain-policy-v1.json",
                "-/etc/heptatrader/hepta-agent-trust-domain.json",
                "-/etc/heptatrader/hepta-broker-network-policy-v1.json",
                "-/etc/heptatrader/"
                "hepta-ib-paper-domain-authorizations-v1.json",
                "-/etc/heptatrader/hepta-service-identities-v1.json",
                "-/etc/heptatrader/local-ai-paper-agent.env",
                "-/etc/heptatrader/local-ai-paper-deployment-v1.json",
                "-/etc/heptatrader/"
                "local-ai-paper-certified-install-closure-v1.json",
                "-/etc/heptatrader/hepta-tool-gateway.env",
                "-/etc/heptatrader/hepta-execution-simulator.env",
                "-/etc/heptatrader/hepta-execution-ib-paper.env",
                "-/etc/heptatrader/hepta-supervisor-lease.key",
                "-/etc/heptatrader/"
                "p1-paper-account-evidence-ed25519.pub",
                "-/etc/heptatrader/"
                "rootful-systemd-review-ed25519.pub",
                "-/etc/heptatrader/paper-account-authority.pub",
                "-/etc/heptatrader/release-causal-openssl.cnf",
                "-/etc/heptatrader/"
                "heptatrader-evidence-receipt-trust-v1.json",
            ],
            "StandardOutput": ["journal"],
            "StandardError": ["journal"],
        },
        "Install": {"WantedBy": ["multi-user.target"]},
    }
    if unit != expected_unit:
        fail("broker policy unit exact contract drifted")

    agent = parse_unit(safe_text(root, AGENT_DROPIN), AGENT_DROPIN)
    if agent != {
            "Unit": {
                "BindsTo": ["hepta-broker-egress-policy.service"],
                "After": ["hepta-broker-egress-policy.service"],
            },
            "Service": {
                "CapabilityBoundingSet": [""],
                "AmbientCapabilities": [""],
                "RestrictNamespaces": ["yes"],
                "RestrictAddressFamilies": ["AF_UNIX AF_INET AF_INET6"],
            },
            }:
        fail("Agent broker policy drop-in drifted")
    joined_agent = "\n".join(
        value
        for section in agent.values()
        for values in section.values()
        for value in values)
    if "PrivateNetwork=yes" in joined_agent or "IPAddressDeny=any" in joined_agent:
        fail("Agent broker drop-in disables required model network")

    for relative in DEPENDENCY_DROPINS:
        exact_dependency_dropin(root, relative)

    domain_ib_service = parse_unit(
        safe_text(root, DOMAIN_IB_SERVICE), DOMAIN_IB_SERVICE)
    if (
            domain_ib_service.get("Service", {}).get("User") !=
            ["hepta-ib-exec-%i"] or
            domain_ib_service.get("Service", {}).get("Group") !=
            ["hepta-ib-exec-%i"] or
            "hepta-exec-%i" in "\n".join(
                value
                for section in domain_ib_service.values()
                for values in section.values()
                for value in values)):
        fail("per-domain PAPER reuses a Simulator execution identity")
    if (
            domain_ib_service.get("Unit", {}).get("Requires") != [
                "hepta-execution-ib-paper@%i.socket "
                "hepta-execution-events-ib-paper@%i.socket"] or
            domain_ib_service.get("Unit", {}).get(
                "StartLimitIntervalSec") != ["1800s"] or
            domain_ib_service.get("Unit", {}).get(
                "StartLimitBurst") != ["5"] or
            domain_ib_service.get("Service", {}).get(
                "RestartPreventExitStatus") != ["9"] or
            domain_ib_service.get("Unit", {}).get("BindsTo") != [
                "hepta-ib-paper-domain-preflight@%i.service"] or
            domain_ib_service.get("Unit", {}).get("After") != [
                "hepta-execution-ib-paper@%i.socket "
                "hepta-execution-events-ib-paper@%i.socket "
                "hepta-ib-paper-domain-preflight@%i.service "
                "network.target"]):
        fail("per-domain PAPER does not bind its authority guard")
    expected_conflicts = [
        "hepta-execution-simulator@%i.service "
        "hepta-execution-simulator@%i.socket "
        "hepta-execution-events-simulator@%i.socket "
        "hepta-execution-ib-paper.service "
        "hepta-execution-ib-paper.socket "
        "hepta-execution-events-ib-paper.socket"]
    for relative in (
            DOMAIN_IB_SERVICE, DOMAIN_IB_SOCKET, DOMAIN_IB_EVENT_SOCKET):
        observed = parse_unit(safe_text(root, relative), relative)
        if observed.get("Unit", {}).get("Conflicts") != expected_conflicts:
            fail(f"{relative}: fixed/templated PAPER mutual exclusion drifted")
    for relative, expected in (
            (FIXED_IB_SOCKET, "hepta-execution-ib-paper.service"),
            (FIXED_IB_EVENT_SOCKET, "hepta-execution-ib-paper.service"),
            (DOMAIN_IB_SOCKET, "hepta-execution-ib-paper@%i.service"),
            (DOMAIN_IB_EVENT_SOCKET, "hepta-execution-ib-paper@%i.service")):
        observed = parse_unit(safe_text(root, relative), relative)
        if observed.get("Unit", {}).get("PartOf") != [expected]:
            fail(f"{relative}: PAPER socket lifecycle binding drifted")
        if relative in (DOMAIN_IB_SOCKET, DOMAIN_IB_EVENT_SOCKET):
            unit = observed.get("Unit", {})
            if (
                    unit.get("BindsTo") != [
                        "hepta-ib-paper-domain-preflight@%i.service"] or
                    unit.get("After") != [
                        "hepta-ib-paper-domain-preflight@%i.service"] or
                    unit.get("StopWhenUnneeded") != ["yes"] or
                    unit.get("RefuseManualStart") != ["yes"]):
                fail(
                    f"{relative}: PAPER socket can outlive or bypass "
                    "its authority guard")
    preflight = parse_unit(
        safe_text(root, PAPER_PREFLIGHT_UNIT), PAPER_PREFLIGHT_UNIT)
    if (
            preflight.get("Unit", {}).get(
                "StartLimitIntervalSec") != ["1800s"] or
            preflight.get("Unit", {}).get(
                "StartLimitBurst") != ["5"] or
            preflight.get("Unit", {}).get("BindsTo") != [
                "hepta-broker-egress-policy.service "
                "hepta-execution-ib-paper@%i.service"] or
            preflight.get("Unit", {}).get("Before") != [
                "hepta-execution-ib-paper@%i.service "
                "hepta-execution-ib-paper@%i.socket "
                "hepta-execution-events-ib-paper@%i.socket"] or
            preflight.get("Unit", {}).get("PartOf") != [
                "hepta-execution-ib-paper@%i.service"] or
            preflight.get("Unit", {}).get("StopWhenUnneeded") != ["yes"] or
            preflight.get("Unit", {}).get("RefuseManualStart") != ["yes"] or
            preflight.get("Service", {}).get("Type") != ["notify"] or
            preflight.get("Service", {}).get("NotifyAccess") != ["main"] or
            preflight.get("Service", {}).get("User") != ["root"] or
            preflight.get("Service", {}).get("ExecStart") != [
                "/usr/libexec/hepta-ib-paper-domain-authority "
                "--guard --domain %i"] or
            preflight.get("Service", {}).get("ExecStopPost") != [
                "/usr/libexec/hepta-ib-paper-domain-authority "
                "--finalize-stop --domain %i"] or
            preflight.get("Service", {}).get("CapabilityBoundingSet") !=
            ["CAP_NET_ADMIN"] or
            preflight.get("Service", {}).get("RuntimeDirectory") != [
                "hepta/ib-paper-host-authority"] or
            preflight.get("Service", {}).get("WatchdogSec") != ["15s"] or
            preflight.get("Service", {}).get("TimeoutStopSec") != ["30s"] or
            preflight.get("Service", {}).get("ReadOnlyPaths") != [
                "/usr/share/heptatrader /etc/heptatrader /run/hepta"] or
            preflight.get("Service", {}).get("ReadWritePaths") != [
                "/run/hepta/ib-paper-host-authority"] or
            "Install" in preflight):
        fail("per-domain PAPER authority preflight drifted")
    domain_ib_example = safe_text(root, DOMAIN_IB_ENV_EXAMPLE)
    for token in (
            "hepta-ib-exec-alpha",
            "must never reuse hepta-exec-alpha",
            "grants no identity, credential, or service activation"):
        if token not in domain_ib_example:
            fail("per-domain PAPER example identity boundary drifted")
    domain_gateway_example = safe_text(
        root,
        Path("systemd/hepta-execution-gateway-paper-domain.env.example"))
    for token in (
            "hepta-ib-exec-alpha",
            "UID/GID 2121",
            "HEPTA_EXECUTION_SERVICE_UID=2121"):
        if token not in domain_gateway_example:
            fail("per-domain PAPER Gateway identity boundary drifted")
    if "HEPTA_EXECUTION_SERVICE_UID=2111" in domain_gateway_example:
        fail("per-domain PAPER Gateway points at the Simulator UID")

    helper = safe_text(root, Path("scripts/hepta_broker_egress_policy.py"))
    for token in (
            "meta skuid", "jump", "return", "delete table",
            "verify_active_policy_json", "--json", "effective_sha256",
            "source_fingerprint_matches", "WATCHDOG=1",
            "deny-all emergency tightening"):
        if token not in helper:
            fail("broker policy helper source contract drifted")
    documentation = safe_text(root, DOCUMENTATION_PATH)
    for token in (
            "model-provider egress",
            "paper_authorized=false",
            "`hepta-ib-exec-<domain>`",
            "`hepta-exec-<domain>`",
            "at most one",
            "separate authority manifest",
            "default-engaged",
            "exact mode `0600`",
            "both staged",
            "polling is not a network namespace",
            "cgroup-BPF",
            "hard capped-PAPER certification",
            "`StopWhenUnneeded=yes`",
            "rejects an independent manual start",
            "before either of its socket paths can listen",
            "startup failure",
            "start-limit exhaustion",
            "`StartLimitIntervalSec=1800s`",
            "`StartLimitBurst=5`",
            "`4001`",
            "`4002`",
            "`7496`",
            "`7497`",
            "performs no broker protocol",
    ):
        if token not in documentation:
            fail("broker network isolation documentation is incomplete")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    arguments = parser.parse_args(argv)
    try:
        check(arguments.root.resolve(strict=True))
    except (
            CheckFailure, POLICY_HELPER.PolicyError, OSError,
            UnicodeDecodeError, ValueError) as error:
        print(f"hepta_broker_network_policy: FAIL: {error}", file=sys.stderr)
        return 1
    print(
        "hepta_broker_network_policy: PASS "
        "protected_ports=4 authorized_uid=2003 model_egress=preserved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
