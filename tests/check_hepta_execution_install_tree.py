#!/usr/bin/env python3

"""Validate the passive execution deployment component in a temporary root."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tempfile


COMPONENT = "hepta-execution-runtime"
DOC = "usr/share/doc/heptatrader/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md"
BROKER_DOC = "usr/share/doc/heptatrader/BROKER-NETWORK-ISOLATION.md"
CAMPAIGN_DOC = "usr/share/doc/heptatrader/AUTONOMOUS-PAPER-CAMPAIGN.md"
RUNBOOK_DOC = "usr/share/doc/heptatrader/RUNBOOK-STARTUP.md"
SIMULATOR_UNITS = {
    "usr/lib/systemd/system/hepta-execution-simulator.service",
    "usr/lib/systemd/system/hepta-execution-simulator.socket",
    "usr/lib/systemd/system/hepta-execution-events-simulator.socket",
    "usr/lib/systemd/system/hepta-execution-simulator@.service",
    "usr/lib/systemd/system/hepta-execution-simulator@.socket",
    "usr/lib/systemd/system/hepta-execution-events-simulator@.socket",
}
IB_UNITS = {
    "usr/lib/systemd/system/hepta-execution-ib-paper.service",
    "usr/lib/systemd/system/hepta-execution-ib-paper.socket",
    "usr/lib/systemd/system/hepta-execution-events-ib-paper.socket",
    "usr/lib/systemd/system/hepta-execution-ib-paper@.service",
    "usr/lib/systemd/system/hepta-execution-ib-paper@.socket",
    "usr/lib/systemd/system/hepta-execution-events-ib-paper@.socket",
    "usr/lib/systemd/system/hepta-ib-paper-campaign-operator@.service",
    "usr/lib/systemd/system/hepta-ib-paper-campaign-operator@.socket",
    "usr/lib/systemd/system/hepta-local-paper-authority@.service",
    "usr/lib/systemd/system/hepta-p1-paper-canary-finalizer.socket",
    "usr/lib/systemd/system/hepta-p1-paper-canary-finalizer@.service",
    "usr/lib/systemd/system/hepta-local-paper-fail-close@.service",
    "usr/lib/systemd/system/hepta-p1-paper-terminal-cutoff@.service",
    "usr/lib/systemd/system/hepta-p1-paper-terminal-witness-verifier@.service",
    "usr/lib/systemd/system/hepta-paper-terminal-latch-committer@.service",
}


def fail(message: str) -> None:
    raise AssertionError(message)


def configured_ibapi(build_directory: Path) -> bool:
    cache = build_directory / "CMakeCache.txt"
    if not cache.is_file():
        fail(f"configured build tree is missing {cache}")
    matches = []
    for line in cache.read_text(encoding="utf-8", errors="strict").splitlines():
        if line.startswith("HEPTA_ENABLE_IBAPI:BOOL="):
            matches.append(line.split("=", 1)[1].strip().upper())
    if len(matches) != 1 or matches[0] not in {"ON", "OFF"}:
        fail("CMakeCache.txt must contain one canonical HEPTA_ENABLE_IBAPI BOOL")
    return matches[0] == "ON"


def parent_directories(files: set[str]) -> set[str]:
    directories: set[str] = set()
    for name in files:
        parent = Path(name).parent
        while parent != Path("."):
            directories.add(parent.as_posix())
            parent = parent.parent
    return directories


def parse_environment(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
            path.read_text(encoding="utf-8", errors="strict").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            fail(f"{path}:{line_number}: invalid environment assignment")
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            fail(f"{path}:{line_number}: invalid environment key {key!r}")
        if key in values:
            fail(f"{path}:{line_number}: duplicate environment key {key!r}")
        values[key] = value
    return values


def parse_unit_sections(path: Path) -> dict[str, dict[str, list[str]]]:
    sections: dict[str, dict[str, list[str]]] = {}
    section = ""
    for line_number, raw_line in enumerate(
            path.read_text(encoding="utf-8", errors="strict").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            if not section or section in sections:
                fail(f"{path}:{line_number}: invalid or duplicate unit section")
            sections[section] = {}
            continue
        if not section or "=" not in line:
            fail(f"{path}:{line_number}: invalid unit directive")
        key, value = line.split("=", 1)
        sections[section].setdefault(key, []).append(value)
    return sections


def verify_preflight_retry_limit(path: Path) -> None:
    sections = parse_unit_sections(path)
    if (
            sections.get("Unit", {}).get(
                "StartLimitIntervalSec") != ["1800s"] or
            sections.get("Unit", {}).get(
                "StartLimitBurst") != ["5"]):
        fail("installed per-domain PAPER preflight retry limit drifted")


def verify_units(root: Path, unit_names: set[str], ibapi_enabled: bool) -> None:
    document = root / DOC
    if not document.is_file():
        fail("the Documentation= target is absent from the install component")
    campaign_document = root / CAMPAIGN_DOC
    if ibapi_enabled and not campaign_document.is_file():
        fail("the PAPER campaign Documentation= target is absent")
    runbook_document = root / RUNBOOK_DOC
    if ibapi_enabled and not runbook_document.is_file():
        fail("the PAPER recovery runbook Documentation= target is absent")
    campaign_document_units = {
        "usr/lib/systemd/system/hepta-local-paper-authority@.service",
        "usr/lib/systemd/system/hepta-local-paper-fail-close@.service",
        "usr/lib/systemd/system/hepta-p1-paper-canary-finalizer.socket",
        "usr/lib/systemd/system/hepta-p1-paper-canary-finalizer@.service",
    }
    runbook_document_units = {
        "usr/lib/systemd/system/hepta-p1-paper-terminal-cutoff@.service",
        "usr/lib/systemd/system/hepta-p1-paper-terminal-witness-verifier@.service",
    }
    for name in sorted(unit_names):
        path = root / name
        sections = parse_unit_sections(path)
        if "Install" in sections:
            fail(f"{name} must not contain an [Install] section")
        documentation = sections.get("Unit", {}).get("Documentation", [])
        if name in campaign_document_units:
            documentation_target = "AUTONOMOUS-PAPER-CAMPAIGN.md"
        elif name in runbook_document_units:
            documentation_target = "RUNBOOK-STARTUP.md"
        else:
            documentation_target = "AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md"
        expected_documentation = [
            "file:/usr/share/doc/heptatrader/" + documentation_target]
        if documentation != expected_documentation:
            fail(f"{name} has an unresolvable Documentation= contract")

    services = {
        "usr/lib/systemd/system/hepta-execution-simulator.service":
            "/usr/libexec/hepta-executiond",
        "usr/lib/systemd/system/hepta-execution-simulator@.service":
            "/usr/libexec/hepta-executiond",
    }
    if ibapi_enabled:
        services[
            "usr/lib/systemd/system/hepta-execution-ib-paper.service"
        ] = "/usr/libexec/hepta-ib-executiond"
        services[
            "usr/lib/systemd/system/hepta-execution-ib-paper@.service"
        ] = "/usr/libexec/hepta-ib-executiond"
    for name, executable in services.items():
        values = parse_unit_sections(root / name).get("Service", {}).get(
            "ExecStart", [])
        if values != [executable]:
            fail(f"{name} must have exactly ExecStart={executable}")
        staged_executable = root / executable.lstrip("/")
        if not staged_executable.is_file() or not os.access(staged_executable, os.X_OK):
            fail(f"{name} references a missing or non-executable staged binary")

    if ibapi_enabled:
        campaign_service = parse_unit_sections(
            root / "usr/lib/systemd/system/"
            "hepta-ib-paper-campaign-operator@.service")
        if campaign_service.get("Service", {}).get("ExecStart") != [
                "/usr/libexec/hepta-ib-paper-campaign-operator "
                "--serve-once --domain %i"]:
            fail("campaign operator service executable contract drifted")
        campaign_executable = (
            root / "usr/libexec/hepta-ib-paper-campaign-operator")
        if (
                not campaign_executable.is_file() or
                not os.access(campaign_executable, os.X_OK)):
            fail("campaign operator executable is absent or non-executable")
        campaign_socket = parse_unit_sections(
            root / "usr/lib/systemd/system/"
            "hepta-ib-paper-campaign-operator@.socket")
        if (
                campaign_socket.get("Unit", {}).get("BindsTo") != [
                    "hepta-ib-paper-domain-preflight@%i.service"] or
                campaign_socket.get("Unit", {}).get("After") != [
                    "hepta-ib-paper-domain-preflight@%i.service"] or
                campaign_socket.get("Socket", {}).get("ListenStream") != [
                    "/run/hepta-agent-%i/campaign.sock"] or
                campaign_socket.get("Socket", {}).get("SocketUser") != [
                    "hepta-agent-%i"] or
                campaign_socket.get("Socket", {}).get("SocketGroup") != [
                    "hepta-agent-%i"] or
                campaign_socket.get("Socket", {}).get("SocketMode") != [
                    "0600"] or
                campaign_socket.get("Socket", {}).get("Service") != [
                    "hepta-ib-paper-campaign-operator@%i.service"]):
            fail("campaign operator socket isolation contract drifted")
        required_service = {
            "User": ["root"],
            "Group": ["root"],
            "SupplementaryGroups": [""],
            "NoNewPrivileges": ["yes"],
            "PrivateNetwork": ["yes"],
            "ProtectSystem": ["strict"],
            "ProtectHome": ["yes"],
            "RestrictAddressFamilies": ["AF_UNIX"],
            "CapabilityBoundingSet": ["CAP_CHOWN"],
            "AmbientCapabilities": [""],
            "RuntimeDirectory": [
                "hepta/ib-paper-campaign hepta/ib-paper-one-shot"],
            "StateDirectory": [
                "hepta/ib-paper-campaign hepta-ib-paper-one-shot"],
        }
        for key, expected in required_service.items():
            if campaign_service.get("Service", {}).get(key) != expected:
                fail(f"campaign operator service {key} drifted")

    domain_service = parse_unit_sections(
        root / "usr/lib/systemd/system/"
        "hepta-execution-simulator@.service")
    if (
            domain_service.get("Service", {}).get("User") !=
            ["hepta-exec-%i"] or
            domain_service.get("Service", {}).get("EnvironmentFile") !=
            ["/etc/heptatrader/trust-domains/%i.execution.env"]):
        fail("domain Execution service identity/environment contract drifted")
    for name, leaf in (
            ("hepta-execution-simulator@.socket", "execution.sock"),
            ("hepta-execution-events-simulator@.socket", "events.sock")):
        socket = parse_unit_sections(
            root / "usr/lib/systemd/system" / name).get("Socket", {})
        if (
                socket.get("ListenStream") !=
                [f"/run/hepta-execution-%i/{leaf}"] or
                socket.get("SocketUser") != ["hepta-gw-%i"] or
                socket.get("SocketGroup") != ["hepta-gw-%i"] or
                socket.get("SocketMode") != ["0600"]):
            fail("domain Execution socket isolation contract drifted")
    if ibapi_enabled:
        for name in (
                "hepta-execution-ib-paper.socket",
                "hepta-execution-events-ib-paper.socket"):
            sections = parse_unit_sections(
                root / "usr/lib/systemd/system" / name)
            if sections.get("Unit", {}).get("PartOf") != [
                    "hepta-execution-ib-paper.service"]:
                fail("fixed IB PAPER socket lifecycle contract drifted")
        paper_domain_service = parse_unit_sections(
            root / "usr/lib/systemd/system/"
            "hepta-execution-ib-paper@.service")
        if (
                paper_domain_service.get("Service", {}).get("User") !=
                ["hepta-ib-exec-%i"] or
                paper_domain_service.get("Service", {}).get("Group") !=
                ["hepta-ib-exec-%i"] or
                paper_domain_service.get("Service", {}).get("EnvironmentFile") !=
                ["/etc/heptatrader/trust-domains/%i.ib-paper.env"] or
                paper_domain_service.get("Service", {}).get("StateDirectory") !=
                ["hepta-ib-execution-%i"] or
                paper_domain_service.get("Unit", {}).get(
                    "StartLimitIntervalSec") != ["1800s"] or
                paper_domain_service.get("Unit", {}).get(
                    "StartLimitBurst") != ["5"] or
                paper_domain_service.get("Service", {}).get(
                    "RestartPreventExitStatus") != ["9"]):
            fail("domain IB PAPER service identity/environment contract drifted")
        expected_paper_conflicts = [
            "hepta-execution-simulator@%i.service "
            "hepta-execution-simulator@%i.socket "
            "hepta-execution-events-simulator@%i.socket "
            "hepta-execution-ib-paper.service "
            "hepta-execution-ib-paper.socket "
            "hepta-execution-events-ib-paper.socket"]
        if (
                paper_domain_service.get("Unit", {}).get("Requires") != [
                    "hepta-execution-ib-paper@%i.socket "
                    "hepta-execution-events-ib-paper@%i.socket"] or
                paper_domain_service.get("Unit", {}).get("BindsTo") != [
                    "hepta-ib-paper-domain-preflight@%i.service"] or
                paper_domain_service.get("Unit", {}).get("After") != [
                    "hepta-execution-ib-paper@%i.socket "
                    "hepta-execution-events-ib-paper@%i.socket "
                    "hepta-ib-paper-domain-preflight@%i.service "
                    "network.target"] or
                paper_domain_service.get("Unit", {}).get("Conflicts") !=
                expected_paper_conflicts):
            fail("domain IB PAPER authority/mutual-exclusion contract drifted")
        for name, leaf in (
                ("hepta-execution-ib-paper@.socket", "execution.sock"),
                ("hepta-execution-events-ib-paper@.socket", "events.sock")):
            sections = parse_unit_sections(
                root / "usr/lib/systemd/system" / name)
            socket = sections.get("Socket", {})
            if (
                    sections.get("Unit", {}).get("Conflicts") !=
                    expected_paper_conflicts or
                    sections.get("Unit", {}).get("PartOf") != [
                        "hepta-execution-ib-paper@%i.service"] or
                    sections.get("Unit", {}).get("BindsTo") != [
                        "hepta-ib-paper-domain-preflight@%i.service"] or
                    sections.get("Unit", {}).get("After") != [
                        "hepta-ib-paper-domain-preflight@%i.service"] or
                    sections.get("Unit", {}).get("StopWhenUnneeded") !=
                    ["yes"] or
                    sections.get("Unit", {}).get("RefuseManualStart") !=
                    ["yes"] or
                    socket.get("ListenStream") !=
                    [f"/run/hepta-execution-%i/{leaf}"] or
                    socket.get("SocketUser") != ["hepta-gw-%i"] or
                    socket.get("SocketGroup") != ["hepta-gw-%i"] or
                    socket.get("SocketMode") != ["0600"]):
                fail("domain IB PAPER socket isolation contract drifted")


def verify_tmpfiles(root: Path) -> None:
    path = root / "usr/lib/tmpfiles.d/heptatrader-ib-paper.conf"
    entries: list[list[str]] = []
    for line_number, raw_line in enumerate(
            path.read_text(encoding="utf-8", errors="strict").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            entries.append(shlex.split(line, comments=True, posix=True))
        except ValueError as error:
            fail(f"{path}:{line_number}: invalid tmpfiles syntax: {error}")
    expected = [
        ["d", "/run/hepta/ib-paper-control", "0750", "root",
         "hepta-ib-exec", "-"],
        ["f", "/run/hepta/ib-paper-control/kill-switch", "0440", "root",
         "hepta-ib-exec", "-", "engaged"],
    ]
    if entries != expected:
        fail("installed tmpfiles contract must contain only the reviewed "
             "default-engaged kill-switch declarations")


def verify_environment_examples(root: Path, ibapi_enabled: bool) -> None:
    simulator = parse_environment(
        root / "usr/share/doc/heptatrader/examples/"
               "hepta-execution-simulator.env.example")
    if simulator != {
        "HEPTA_EXECUTION_GATEWAY_UID": "2001",
        "HEPTA_EXECUTION_GATEWAY_AGENT_ID": "codex-agent-os-e2e",
        "HEPTA_EXECUTION_MAX_REQUEST_BYTES": "16384",
        "HEPTA_EXECUTION_IO_TIMEOUT_MS": "2500",
    }:
        fail("installed Simulator environment example is not the reviewed profile")

    profiles = [simulator]
    if ibapi_enabled:
        paper = parse_environment(
            root / "usr/share/doc/heptatrader/examples/"
                   "hepta-execution-ib-paper.env.example")
        expected_paper = {
            "HEPTA_IB_EXECUTION_MODE": "PAPER",
            "HEPTA_IB_PAPER_ACCOUNT": "DU000000",
            "HEPTA_IB_PAPER_HOST": "127.0.0.1",
            "HEPTA_IB_PAPER_PORT": "4002",
            "HEPTA_IB_PAPER_CLIENT_ID": "701",
            "HEPTA_IB_PAPER_MAX_ORDER_QTY": "25000",
            "HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL": "35000",
            "HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE": "2",
            "HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS": "3",
            "HEPTA_IB_PAPER_MAX_GROSS_POSITION": "25000",
            "HEPTA_IB_PAPER_QUOTE_CONTRACTS":
                "EUR.USD|EUR|CASH|IDEALPRO|USD",
            "HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT": "EUR.USD",
            "HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS": "5000",
            "HEPTA_IB_EXECUTION_GATEWAY_UID": "2001",
            "HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID": "codex-agent-os-e2e",
            "HEPTA_IB_EXECUTION_MAX_REQUEST_BYTES": "16384",
            "HEPTA_IB_EXECUTION_IO_TIMEOUT_MS": "2500",
            "HEPTA_IB_EXECUTION_READINESS_TIMEOUT_MS": "12000",
            "HEPTA_IB_EXECUTION_RECONNECT_TIMEOUT_MS": "180000",
        }
        if paper != expected_paper:
            fail("installed IB PAPER environment example is not the reviewed profile")
        gateway_paper = parse_environment(
            root / "usr/share/doc/heptatrader/examples/"
                   "hepta-execution-gateway-paper.env.example")
        expected_gateway_paper = {
            "HEPTA_EXECUTION_REMOTE_MODE": "PAPER",
            "HEPTA_EXECUTION_SOCKET": "/run/hepta-execution/execution.sock",
            "HEPTA_EXECUTION_EVENT_SOCKET": "/run/hepta-execution/events.sock",
            "HEPTA_EXECUTION_SERVICE_UID": "2003",
            "HEPTA_EXECUTION_IO_TIMEOUT_MS": "2500",
            "HEPTA_EXECUTION_MAX_RESPONSE_BYTES": "32768",
            "HEPTA_TOOL_ACCOUNT": "DU000000",
            "HEPTA_TOOL_AGENT_ID": "codex-agent-os-e2e",
            "HEPTA_EXECUTION_DOMAIN_ID": "PAPER",
            "HEPTA_TOOL_ALLOW_TRADE": "0",
            "HEPTA_TOOL_SESSION_TEMPLATES": "watch",
            "HEPTA_TOOL_CONTRACT_BINDINGS":
                "EUR.USD|EUR|CASH|IDEALPRO|USD",
            "HEPTA_TOOL_MAX_ORDER_QTY": "25000",
            "HEPTA_TOOL_MAX_TRADE_CALLS_PER_MIN": "2",
            "HEPTA_TOOL_DECISION_LEASE_TTL_MS": "5000",
            "HEPTA_TOOL_AGENT_UID": "2004",
            "HEPTA_TOOL_SUPERVISOR_UID": "0",
            "HEPTA_TOOL_SUPERVISOR_MAX_TTL_SEC": "86400",
            "HEPTA_TOOL_SERVER_WORKERS": "4",
            "HEPTA_TOOL_SERVER_MAX_PENDING": "32",
            "HEPTA_TOOL_SERVER_MAX_CONCURRENT_PER_OWNER": "1",
            "HEPTA_TOOL_SERVER_MAX_PENDING_PER_OWNER": "8",
            "HEPTA_TOOL_SERVER_INGRESS_WORKERS": "2",
        }
        if gateway_paper != expected_gateway_paper:
            fail("installed Gateway PAPER environment example is not the reviewed profile")
        domain_paper = parse_environment(
            root / "usr/share/doc/heptatrader/examples/"
                   "hepta-execution-ib-paper-domain.env.example")
        expected_domain_paper = dict(expected_paper)
        expected_domain_paper.update({
            "HEPTA_IB_EXECUTION_GATEWAY_UID": "2101",
            "HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID": "alpha",
            "HEPTA_IB_EXECUTION_DOMAIN_ID": "PAPER:alpha",
        })
        if domain_paper != expected_domain_paper:
            fail("installed domain IB PAPER environment example drifted")
        domain_gateway = parse_environment(
            root / "usr/share/doc/heptatrader/examples/"
                   "hepta-execution-gateway-paper-domain.env.example")
        expected_domain_gateway = dict(expected_gateway_paper)
        expected_domain_gateway.update({
            "HEPTA_EXECUTION_SOCKET":
                "/run/hepta-execution-alpha/execution.sock",
            "HEPTA_EXECUTION_EVENT_SOCKET":
                "/run/hepta-execution-alpha/events.sock",
            "HEPTA_EXECUTION_SERVICE_UID": "2121",
            "HEPTA_TOOL_AGENT_ID": "alpha",
            "HEPTA_EXECUTION_DOMAIN_ID": "PAPER:alpha",
            "HEPTA_TOOL_AGENT_UID": "2104",
        })
        if domain_gateway != expected_domain_gateway:
            fail("installed domain Gateway PAPER environment example drifted")
        profiles.extend(
            (paper, gateway_paper, domain_paper, domain_gateway))

    forbidden = ("PASSWORD", "TOKEN", "SECRET", "AUTHORIZATION", "FENCE",
                 "CREDENTIAL")
    for profile in profiles:
        for key in profile:
            if any(word in key.upper() for word in forbidden):
                fail(f"environment example contains secret-bearing field {key!r}")


def verify_broker_network_assets(root: Path) -> None:
    policy_path = (
        root / "usr/share/heptatrader/"
        "hepta-broker-network-policy-v1.json")
    try:
        policy = json.loads(
            policy_path.read_text(encoding="utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"installed broker policy JSON is invalid: {error}")
    if (
            not isinstance(policy, dict) or
            policy.get("paper_identity_manifest", {}).get(
                "max_identities") != 1):
        fail("installed policy does not cap templated PAPER at one domain")
    dependency = (
        "[Unit]\n"
        "BindsTo=hepta-broker-egress-policy.service\n"
        "After=hepta-broker-egress-policy.service\n")
    for relative in (
            "usr/lib/systemd/system/hepta-execution-ib-paper.service.d/"
            "10-hepta-broker-egress-policy.conf",
            "usr/lib/systemd/system/hepta-execution-ib-paper@.service.d/"
            "10-hepta-broker-egress-policy.conf"):
        if (root / relative).read_text(
                encoding="utf-8", errors="strict") != dependency:
            fail("IB PAPER broker policy dependency drop-in drifted")
    unit = (root / "usr/lib/systemd/system/"
            "hepta-broker-egress-policy.service").read_text(
                encoding="utf-8", errors="strict")
    for token in (
            "LoadCredential=hepta-broker-egress-policy.py:"
            "/usr/libexec/hepta-broker-egress-policy",
            "LoadCredential=hepta-local-paper-control.py:"
            "/usr/libexec/hepta-local-paper-control",
            "ExecStartPre=/usr/bin/python3.12 -I -S "
            "${CREDENTIALS_DIRECTORY}/hepta-local-paper-control.py "
            "reconcile-before-broker",
            "ExecStart=/usr/bin/python3.12 -I -S "
            "${CREDENTIALS_DIRECTORY}/hepta-broker-egress-policy.py "
            "--supervise-deny-all "
            "--paper-identities /etc/heptatrader/"
            "hepta-agent-trust-domain-paper-identities-v1.json",
            "ExecStopPost=/usr/bin/python3.12 -I -S "
            "${CREDENTIALS_DIRECTORY}/hepta-broker-egress-policy.py "
            "--tighten-deny-all",
            "Type=notify",
            "WatchdogSec=15s",
            "CapabilityBoundingSet=CAP_NET_ADMIN",
            "RestrictAddressFamilies=AF_UNIX AF_NETLINK",
            "ReadOnlyPaths=/usr/share/heptatrader",
            "ReadWritePaths=/etc/heptatrader",
            "BindReadOnlyPaths=-/etc/heptatrader/credentials",
            "BindReadOnlyPaths=-/etc/heptatrader/trust-domains",
            "BindReadOnlyPaths=-/etc/heptatrader/"
            "local-ai-paper-certified-install-closure-v1.json",
            "ReadOnlyPaths=-/var/lib/hepta-local-ai-paper-agent",
            "ReadWritePaths=-/run/hepta-local-paper-control"):
        if token not in unit:
            fail("installed broker policy unit drifted")
    guardian = (root / "usr/lib/systemd/system/"
                "hepta-local-paper-authority@.service").read_text(
                    encoding="utf-8", errors="strict")
    for token in (
            "Type=notify", "Restart=no", "WatchdogSec=15s",
            "LoadCredential=hepta-local-paper-control.py:"
            "/usr/libexec/hepta-local-paper-control",
            "ExecStart=/usr/bin/python3.12 -I -S "
            "${CREDENTIALS_DIRECTORY}/hepta-local-paper-control.py "
            "guardian --domain %i",
            "ExecStopPost=/usr/bin/python3.12 -I -S "
            "${CREDENTIALS_DIRECTORY}/hepta-local-paper-control.py "
            "guardian-fail-close --domain %i",
            "RuntimeDirectory=hepta-local-paper-control/%i",
            "ReadWritePaths=/var/lib/hepta-local-ai-paper-agent"):
        if token not in guardian:
            fail("installed local PAPER guardian unit drifted")
    if "[Install]" in guardian:
        fail("local PAPER guardian must never auto-enable")
    finalizer_socket = (
        root / "usr/lib/systemd/system/"
        "hepta-p1-paper-canary-finalizer.socket"
    ).read_text(encoding="utf-8", errors="strict")
    for token in (
            "ListenStream=/run/hepta-p1-paper-canary-finalizer.sock",
            "Accept=yes", "MaxConnections=1",
            "SocketUser=hepta-agent-alpha",
            "SocketGroup=hepta-agent-alpha", "SocketMode=0600"):
        if token not in finalizer_socket:
            fail("installed external-P1 finalizer socket drifted")
    if "Service=" in finalizer_socket:
        fail("external-P1 finalizer socket must use its matching template")
    finalizer_service = (
        root / "usr/lib/systemd/system/"
        "hepta-p1-paper-canary-finalizer@.service"
    ).read_text(encoding="utf-8", errors="strict")
    for token in (
            "Type=oneshot", "User=root", "Group=root",
            "LoadCredential=hepta-p1-paper-canary-finalizer.py:"
            "/usr/libexec/hepta-p1-paper-canary-finalizer",
            "LoadCredential=hepta-local-paper-control.py:"
            "/usr/libexec/hepta-local-paper-control",
            "ExecStart=/usr/bin/python3.12 -I -S "
            "${CREDENTIALS_DIRECTORY}/"
            "hepta-p1-paper-canary-finalizer.py serve",
            "ExecStopPost=/usr/bin/python3.12 -I -S "
            "${CREDENTIALS_DIRECTORY}/"
            "hepta-p1-paper-canary-finalizer.py fail-close-on-exit",
            "StandardInput=socket", "TimeoutStartSec=5min",
            "PrivateNetwork=yes",
            "ReadWritePaths=/var/lib/hepta/p1-paper-canary-control"):
        if token not in finalizer_service:
            fail("installed external-P1 finalizer service drifted")
    preflight_path = (
        root / "usr/lib/systemd/system/"
        "hepta-ib-paper-domain-preflight@.service")
    preflight = preflight_path.read_text(
        encoding="utf-8", errors="strict")
    verify_preflight_retry_limit(preflight_path)
    for token in (
            "ExecStart=/usr/libexec/hepta-ib-paper-domain-authority "
            "--guard --domain %i",
            "ExecStopPost=/usr/libexec/hepta-ib-paper-domain-authority "
            "--finalize-stop --domain %i",
            "Type=notify",
            "BindsTo=hepta-broker-egress-policy.service "
            "hepta-execution-ib-paper@%i.service",
            "Before=hepta-execution-ib-paper@%i.service "
            "hepta-execution-ib-paper@%i.socket "
            "hepta-execution-events-ib-paper@%i.socket",
            "PartOf=hepta-execution-ib-paper@%i.service",
            "StopWhenUnneeded=yes",
            "RefuseManualStart=yes",
            "User=root",
            "CapabilityBoundingSet=CAP_NET_ADMIN",
            "RuntimeDirectory=hepta/ib-paper-host-authority",
            "ReadOnlyPaths=/usr/share/heptatrader "
            "/etc/heptatrader /run/hepta"):
        if token not in preflight:
            fail("installed per-domain PAPER preflight drifted")
    paper_example = (
        root / "usr/share/doc/heptatrader/examples/"
        "hepta-agent-trust-domain-paper-identities-v1.json.example")
    try:
        paper = json.loads(
            paper_example.read_text(encoding="utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"installed PAPER identity example is invalid: {error}")
    if (
            not isinstance(paper, dict) or
            paper.get("paper_authorized") is not False or
            paper.get("live_authorized") is not False or
            paper.get("identities") != []):
        fail("installed PAPER identity example is not default-deny")

    helper = root / "usr/libexec/hepta-broker-egress-policy"
    rendered = subprocess.run(
        [
            str(helper),
            "--policy",
            str(root / "usr/share/heptatrader/"
                "hepta-broker-network-policy-v1.json"),
            "--identity-manifest",
            str(root / "usr/share/heptatrader/"
                "hepta-service-identities-v1.json"),
            "--paper-identities", str(paper_example),
            "--render",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        env={
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        cwd="/",
        timeout=15,
        check=False,
    )
    if (
            rendered.returncode != 0 or rendered.stderr or
            "fib daddr type local meta l4proto tcp "
            "tcp dport { 4001, 4002, 7496, 7497 } jump ib_guard"
            not in rendered.stdout or
            "meta skuid 2003 counter return" not in rendered.stdout):
        fail("installed broker policy did not render the exact boundary")
    authority = subprocess.run(
        [
            str(root / "usr/libexec/hepta-ib-paper-domain-authority"),
            "--network-identities", str(paper_example),
            "--authorizations",
            str(root / "usr/share/doc/heptatrader/examples/"
                "hepta-ib-paper-domain-authorizations-v1.json.example"),
            "--render-tmpfiles",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        env={
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        cwd="/",
        timeout=15,
        check=False,
    )
    if (
            authority.returncode != 1 or authority.stdout or
            "tmpfiles rendering requires explicit PAPER authorization"
            not in authority.stderr):
        fail("default PAPER authority example did not fail closed")
    campaign_example = (
        root / "usr/share/doc/heptatrader/examples/"
        "hepta-ib-paper-campaign-policy-v1.json.example")
    try:
        campaign = json.loads(
            campaign_example.read_text(encoding="utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"installed campaign policy example is invalid: {error}")
    if (
            not isinstance(campaign, dict) or
            campaign.get("enabled") is not False or
            campaign.get("mutations_authorized") is not False or
            campaign.get("paper_only") is not True or
            campaign.get("live_authorized") is not False or
            campaign.get("max_active_orders") != 1 or
            campaign.get("order_type") != "LMT" or
            campaign.get("tif") != "DAY"):
        fail("installed campaign policy example is not default-deny")

    local_campaign_example = (
        root / "usr/share/doc/heptatrader/examples/"
        "hepta-ib-paper-campaign-policy-local-v4.json.example")
    p1_campaign_example = (
        root / "usr/share/doc/heptatrader/examples/"
        "hepta-ib-paper-campaign-policy-p1-v5.json.example")
    legacy_strategy_example = (
        root / "usr/share/heptatrader/"
        "hepta-local-ai-paper-strategy-v2.json")
    strategy_example = (
        root / "usr/share/heptatrader/"
        "hepta-local-ai-paper-strategy-v3.json")
    agent_env_example = (
        root / "usr/share/doc/heptatrader/examples/"
        "hepta-local-ai-paper-agent.env.example")
    try:
        local_campaign_raw = local_campaign_example.read_bytes()
        local_campaign = json.loads(local_campaign_raw)
        p1_campaign_raw = p1_campaign_example.read_bytes()
        p1_campaign = json.loads(p1_campaign_raw)
        legacy_strategy = json.loads(legacy_strategy_example.read_text(
            encoding="utf-8", errors="strict"))
        strategy = json.loads(strategy_example.read_text(
            encoding="utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"installed bounded PAPER example is invalid: {error}")
    if (
            not isinstance(local_campaign, dict) or
            local_campaign.get("schema") !=
                "hepta.ib-paper-campaign-policy.v5" or
            local_campaign.get("version") != 5 or
            local_campaign.get("admission_mode") != "local-only" or
            local_campaign.get("enabled") is not False or
            local_campaign.get("mutations_authorized") is not False or
            local_campaign.get("paper_only") is not True or
            local_campaign.get("live_authorized") is not False or
            local_campaign.get("source_baseline_sha256") !=
                "sha256:" + "0" * 64 or
            local_campaign.get("strategy_sha256") !=
                "sha256:" + "0" * 64 or
            local_campaign.get(
                "deployment_evidence_file_sha256") !=
                "sha256:" + "0" * 64 or
            local_campaign.get(
                "deployment_evidence_body_sha256") !=
                "sha256:" + "0" * 64 or
            local_campaign.get(
                "deployment_install_transaction_id") !=
                "replace-with-certified-install-transaction" or
            local_campaign.get("max_cycles") != 720 or
            local_campaign.get("max_quantity") != 25000 or
            local_campaign.get("max_holding_ms") != 0 or
            local_campaign.get("max_active_orders") != 1 or
            local_campaign.get("order_type") != "MKT" or
            local_campaign.get("tif") != "DAY" or
            local_campaign.get("strategy_id") !=
                "hepta-local-ai-paper-strategy-v3" or
            local_campaign.get("strategy_version") != "3" or
            "limit_price" in local_campaign):
        fail("installed local v5 campaign example is not MKT/DAY default-deny")
    local_campaign_canonical = (
        json.dumps(
            local_campaign, ensure_ascii=True, sort_keys=True,
            separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")
    if local_campaign_raw != local_campaign_canonical:
        fail("installed local v5 campaign example is not compact canonical JSON")
    if (
            not isinstance(legacy_strategy, dict) or
            legacy_strategy.get("schema") !=
                "hepta.local-ai-paper-strategy.v2" or
            legacy_strategy.get("version") != 2 or
            legacy_strategy.get("paper_only") is not True or
            legacy_strategy.get("live_authorized") is not False or
            legacy_strategy.get("order_type") != "MKT" or
            "limit_price" in legacy_strategy):
        fail("installed local PAPER strategy v2 lineage drifted")
    p1_pin_fields = {
        "source_baseline_sha256", "strategy_sha256",
        "admission_receipt_file_sha256", "admission_receipt_body_sha256",
        "admission_finalization_current_pointer_file_sha256",
        "admission_finalization_current_pointer_body_sha256",
        "admission_finalization_tombstone_file_sha256",
        "admission_finalization_tombstone_body_sha256",
        "deployment_evidence_file_sha256",
        "deployment_evidence_body_sha256",
        "p1_audit_receipt_file_sha256",
        "p1_audit_receipt_body_sha256",
        "watch_handoff_receipt_file_sha256",
        "watch_handoff_receipt_body_sha256",
    }
    p1_policy_fields = {
        "schema", "version", "campaign_id", "domain_id", "enabled",
        "mutations_authorized", "paper_only", "live_authorized",
        "strategy_id", "strategy_version", "strategy_sha256",
        "valid_after_ms", "expires_at_ms", "allowed_instruments",
        "max_cycles", "max_quantity", "min_cycle_interval_ms",
        "operator_ttl_seconds", "max_intent_horizon_ms", "max_holding_ms",
        "max_active_orders", "order_type", "tif", "end_flat_required",
        "source_baseline_sha256", "admission_receipt_name",
        "admission_receipt_file_sha256",
        "admission_receipt_body_sha256",
        "admission_finalization_current_pointer_path",
        "admission_finalization_current_pointer_file_sha256",
        "admission_finalization_current_pointer_body_sha256",
        "admission_finalization_tombstone_path",
        "admission_finalization_tombstone_file_sha256",
        "admission_finalization_tombstone_body_sha256",
        "admission_mode", "deployment_evidence_file_sha256",
        "deployment_evidence_body_sha256",
        "deployment_install_transaction_id",
        "p1_audit_receipt_path", "p1_audit_receipt_file_sha256",
        "p1_audit_receipt_body_sha256", "watch_handoff_receipt_path",
        "watch_handoff_receipt_file_sha256",
        "watch_handoff_receipt_body_sha256",
    }
    if (
            not isinstance(p1_campaign, dict) or
            set(p1_campaign) != p1_policy_fields or
            p1_campaign.get("schema") !=
                "hepta.ib-paper-campaign-policy.v5" or
            p1_campaign.get("version") != 5 or
            p1_campaign.get("admission_mode") !=
                "external-p1-finalized" or
            p1_campaign.get("enabled") is not False or
            p1_campaign.get("mutations_authorized") is not False or
            p1_campaign.get("paper_only") is not True or
            p1_campaign.get("live_authorized") is not False or
            p1_campaign.get("max_cycles") != 1 or
            p1_campaign.get("max_quantity") != 1 or
            p1_campaign.get("max_holding_ms") != 0 or
            p1_campaign.get("max_active_orders") != 1 or
            p1_campaign.get("order_type") != "LMT" or
            p1_campaign.get("tif") != "DAY" or
            p1_campaign.get("strategy_id") !=
                "hepta-local-ai-paper-strategy-v3" or
            p1_campaign.get("strategy_version") != "3" or
            p1_campaign.get("watch_handoff_receipt_path") !=
                "/var/lib/hepta/p1-admission/"
                "p1-watch-to-paper-handoff-receipt-v2.json" or
            any(p1_campaign.get(field) != "sha256:" + "0" * 64
                for field in p1_pin_fields) or
            "limit_price" in p1_campaign):
        fail("installed P1 v5 campaign example is not LMT/DAY default-deny")
    p1_campaign_canonical = (
        json.dumps(
            p1_campaign, ensure_ascii=True, sort_keys=True,
            separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")
    if p1_campaign_raw != p1_campaign_canonical:
        fail("installed P1 v5 campaign example is not compact canonical JSON")
    if (
            not isinstance(strategy, dict) or
            strategy.get("schema") != "hepta.local-ai-paper-strategy.v3" or
            strategy.get("version") != 3 or
            strategy.get("paper_only") is not True or
            strategy.get("live_authorized") is not False or
            strategy.get("max_order_quantity") != 25000 or
            strategy.get("max_holding_seconds") != 0 or
            strategy.get("exit_mode") != "MODEL_REVERSAL" or
            strategy.get("rate_limit_fail_closed") is not True or
            strategy.get("emergency_reduce_only_recovery") is not True or
            strategy.get("auth_rearm_required_after_rate_limit") is not True or
            strategy.get("campaign_end_flat_required") is not True or
            strategy.get("order_type") != "MKT" or
            "limit_price" in strategy):
        fail("installed local PAPER strategy v3 is not MKT/DAY-only")
    agent_env = parse_environment(agent_env_example)
    expected_agent_env = {
        "HEPTA_LOCAL_AI_CAMPAIGN_ID":
            "replace-with-new-paper-campaign-id",
        "HEPTA_LOCAL_AI_STRATEGY_ID":
            "hepta-local-ai-paper-strategy-v3",
        "HEPTA_LOCAL_AI_STRATEGY_VERSION": "3",
        "HEPTA_LOCAL_AI_STRATEGY_SHA256": "sha256:" + hashlib.sha256(
            strategy_example.read_bytes()).hexdigest(),
        "HEPTA_LOCAL_AI_AUTH_GENERATION":
            "replace-with-new-auth-generation",
        "HEPTA_LOCAL_AI_AUTH_PROFILE_ID":
            "replace-with-reviewed-auth-profile-id",
        "HEPTA_LOCAL_AI_AUTH_PROFILE_ALLOWLIST_SHA256":
            "sha256:" + "0" * 64,
    }
    if agent_env != expected_agent_env:
        fail("installed local PAPER agent environment example drifted")

    safe_recover_service = parse_unit_sections(
        root / "usr/lib/systemd/system/"
        "hepta-local-paper-safe-recover.service")
    if safe_recover_service.get("Service", {}).get("ExecStart") != [
            "/usr/libexec/hepta-local-paper-safe-recover-guard"]:
        fail("installed local PAPER safe-recovery service contract drifted")
    safe_recover_timer = parse_unit_sections(
        root / "usr/lib/systemd/system/"
        "hepta-local-paper-safe-recover.timer")
    if (
            safe_recover_timer.get("Timer", {}).get("OnBootSec") !=
                ["60s"] or
            safe_recover_timer.get("Timer", {}).get("OnUnitInactiveSec") !=
                ["60s"] or
            safe_recover_timer.get("Timer", {}).get("Unit") !=
                ["hepta-local-paper-safe-recover.service"] or
            safe_recover_timer.get("Install", {}).get("WantedBy") !=
                ["timers.target"]):
        fail("installed local PAPER safe-recovery timer contract drifted")
    session_renew_service = parse_unit_sections(
        root / "usr/lib/systemd/system/"
        "hepta-local-paper-session-renew.service")
    if (
            session_renew_service.get("Unit", {}).get("After") !=
                ["hepta-tool-gateway@alpha.service"] or
            session_renew_service.get("Unit", {}).get("OnFailure") !=
                ["hepta-local-paper-safe-recover.service"] or
            session_renew_service.get("Service", {}).get("ExecStart") !=
                ["/usr/libexec/hepta-local-paper-session-renew"] or
            session_renew_service.get("Service", {}).get("User") !=
                ["root"] or
            session_renew_service.get("Service", {}).get("Group") !=
                ["root"] or
            session_renew_service.get("Service", {}).get("ProtectSystem") !=
                ["strict"] or
            session_renew_service.get("Service", {}).get("ReadWritePaths") !=
                ["/var/lib/hepta-local-ai-paper-agent "
                 "-/run/hepta-agent-alpha"]):
        fail("installed local PAPER session-renew service contract drifted")
    session_renew_timer = parse_unit_sections(
        root / "usr/lib/systemd/system/"
        "hepta-local-paper-session-renew.timer")
    if (
            session_renew_timer.get("Timer", {}).get("OnBootSec") !=
                ["60s"] or
            session_renew_timer.get("Timer", {}).get("OnUnitInactiveSec") !=
                ["1h"] or
            session_renew_timer.get("Timer", {}).get("AccuracySec") !=
                ["5s"] or
            session_renew_timer.get("Timer", {}).get("Unit") !=
                ["hepta-local-paper-session-renew.service"] or
            session_renew_timer.get("Install", {}).get("WantedBy") !=
                ["timers.target"]):
        fail("installed local PAPER session-renew timer contract drifted")
    supervisor_service = parse_unit_sections(
        root / "usr/lib/systemd/system/hepta-local-paper-supervisor.service")
    if (
            supervisor_service.get("Unit", {}).get("After") != [
                "hepta-local-ai-paper-agent.service "
                "hepta-tool-gateway@alpha.service"] or
            supervisor_service.get("Service", {}).get("ExecStart") !=
                ["/usr/libexec/hepta-local-paper-supervisor"] or
            supervisor_service.get("Service", {}).get("User") != ["root"] or
            supervisor_service.get("Service", {}).get("Group") != ["root"] or
            supervisor_service.get("Service", {}).get("ProtectSystem") !=
                ["strict"] or
            supervisor_service.get("Service", {}).get("ReadWritePaths") !=
                ["/var/lib/hepta-local-ai-paper-agent"]):
        fail("installed local PAPER supervisor service contract drifted")
    supervisor_timer = parse_unit_sections(
        root / "usr/lib/systemd/system/hepta-local-paper-supervisor.timer")
    if (
            supervisor_timer.get("Timer", {}).get("OnBootSec") != ["60s"] or
            supervisor_timer.get("Timer", {}).get("OnUnitInactiveSec") !=
                ["60s"] or
            supervisor_timer.get("Timer", {}).get("Unit") !=
                ["hepta-local-paper-supervisor.service"] or
            supervisor_timer.get("Install", {}).get("WantedBy") !=
                ["timers.target"]):
        fail("installed local PAPER supervisor timer contract drifted")
    agent_service = parse_unit_sections(
        root / "usr/lib/systemd/system/hepta-local-ai-paper-agent.service")
    expected_agent_requisites = (
        "hepta-tool-gateway@alpha.service "
        "hepta-execution-ib-paper@alpha.service "
        "hepta-ib-paper-campaign-operator@alpha.socket "
        "hepta-local-paper-safe-recover.timer "
        "hepta-local-paper-session-renew.timer "
        "hepta-local-paper-supervisor.timer "
        "hepta-local-ai-paper-24h-stop.timer "
        "hepta-local-ai-paper-end-flat-retry.timer")
    if (
            "Install" in agent_service or
            agent_service.get("Unit", {}).get("OnFailure") !=
                ["hepta-local-paper-safe-recover.service"] or
            agent_service.get("Unit", {}).get("Requisite") !=
                [expected_agent_requisites] or
            "Requires" in agent_service.get("Unit", {}) or
            agent_service.get("Service", {}).get("EnvironmentFile") !=
                ["/etc/heptatrader/local-ai-paper-agent.env"] or
            agent_service.get("Service", {}).get("ExecCondition") !=
                ["/usr/libexec/hepta-local-paper-repair pre-start-guard"] or
            agent_service.get("Service", {}).get("Restart") != ["no"] or
            "RestartPreventExitStatus" in
                agent_service.get("Service", {}) or
            agent_service.get("Service", {}).get("InaccessiblePaths") !=
                ["/var/lib/hepta-local-ai-paper-agent/session-authority"] or
            agent_service.get("Service", {}).get("CapabilityBoundingSet") !=
                ["CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER CAP_KILL "
                 "CAP_SETGID CAP_SETUID"] or
            agent_service.get("Service", {}).get("AmbientCapabilities") !=
                [""] or
            agent_service.get("Service", {}).get("RestrictNamespaces") !=
                ["yes"] or
            agent_service.get("Service", {}).get("SystemCallFilter") !=
                ["~@mount"] or
            "--auth-generation ${HEPTA_LOCAL_AI_AUTH_GENERATION}" not in
                agent_service.get("Service", {}).get("ExecStart", [""])[0] or
            "--auth-profile-id ${HEPTA_LOCAL_AI_AUTH_PROFILE_ID}" not in
                agent_service.get("Service", {}).get("ExecStart", [""])[0]):
        fail("installed local PAPER agent fail-closed recovery contract drifted")


def verify_tree(root: Path, ibapi_enabled: bool) -> None:
    expected_modes: dict[str, int] = {
        "usr/libexec/hepta-executiond": 0o755,
        "usr/share/heptatrader/hepta-service-identities-v1.json": 0o644,
        DOC: 0o644,
        "usr/share/doc/heptatrader/examples/"
        "hepta-execution-simulator.env.example": 0o644,
    }
    for unit in SIMULATOR_UNITS:
        expected_modes[unit] = 0o644

    unit_names = set(SIMULATOR_UNITS)
    if ibapi_enabled:
        expected_modes.update({
            "usr/libexec/hepta-ib-executiond": 0o755,
            "usr/libexec/hepta-broker-egress-policy": 0o755,
            "usr/libexec/hepta-ib-paper-domain-authority": 0o755,
            "usr/libexec/hepta-ib-paper-campaign-operator": 0o755,
            "usr/libexec/hepta-local-paper-control": 0o755,
            "usr/libexec/hepta-p1-paper-canary-finalizer": 0o755,
            "usr/libexec/hepta-p1-paper-terminal-witness-verifier": 0o755,
            "usr/libexec/hepta-paper-terminal-latch-committer": 0o755,
            "usr/libexec/hepta-local-ai-paper-agent": 0o755,
            "usr/libexec/hepta-local-paper-safe-recover": 0o755,
            "usr/libexec/hepta-local-paper-repair": 0o755,
            "usr/libexec/hepta-local-paper-safe-recover-guard": 0o755,
            "usr/libexec/hepta-local-paper-session-renew": 0o755,
            "usr/libexec/hepta-local-paper-supervisor": 0o755,
            "usr/libexec/hepta-prepare-paper-campaign": 0o755,
            "usr/share/heptatrader/"
            "hepta-broker-network-policy-v1.json": 0o644,
            "usr/share/heptatrader/"
            "hepta-local-ai-paper-strategy-v1.json": 0o644,
            "usr/share/heptatrader/"
            "hepta-local-ai-paper-strategy-v2.json": 0o644,
            "usr/share/heptatrader/"
            "hepta-local-ai-paper-strategy-v3.json": 0o644,
            BROKER_DOC: 0o644,
            CAMPAIGN_DOC: 0o644,
            RUNBOOK_DOC: 0o644,
            "usr/lib/systemd/system/"
            "hepta-broker-egress-policy.service": 0o644,
            "usr/lib/systemd/system/"
            "hepta-local-paper-authority@.service": 0o644,
            "usr/lib/systemd/system/"
            "hepta-ib-paper-domain-preflight@.service": 0o644,
            "usr/lib/systemd/system/"
            "hepta-execution-ib-paper.service.d/"
            "10-hepta-broker-egress-policy.conf": 0o644,
            "usr/lib/systemd/system/"
            "hepta-execution-ib-paper@.service.d/"
            "10-hepta-broker-egress-policy.conf": 0o644,
            "usr/share/doc/heptatrader/examples/"
            "hepta-execution-ib-paper.env.example": 0o644,
            "usr/share/doc/heptatrader/examples/"
            "hepta-execution-gateway-paper.env.example": 0o644,
            "usr/share/doc/heptatrader/examples/"
            "hepta-execution-ib-paper-domain.env.example": 0o644,
            "usr/share/doc/heptatrader/examples/"
            "hepta-execution-gateway-paper-domain.env.example": 0o644,
            "usr/share/doc/heptatrader/examples/"
            "hepta-local-ai-paper-agent.env.example": 0o644,
            "usr/share/doc/heptatrader/examples/"
            "hepta-agent-trust-domain-paper-identities-v1.json.example":
                0o644,
            "usr/share/doc/heptatrader/examples/"
            "hepta-ib-paper-domain-authorizations-v1.json.example": 0o644,
            "usr/share/doc/heptatrader/examples/"
            "hepta-ib-paper-campaign-policy-v1.json.example": 0o644,
            "usr/share/doc/heptatrader/examples/"
            "hepta-ib-paper-campaign-policy-local-v4.json.example": 0o644,
            "usr/share/doc/heptatrader/examples/"
            "hepta-ib-paper-campaign-policy-p1-v5.json.example": 0o644,
            "usr/lib/systemd/system/"
            "hepta-local-ai-paper-agent.service": 0o644,
            "usr/lib/systemd/system/"
            "hepta-local-paper-safe-recover.service": 0o644,
            "usr/lib/systemd/system/"
            "hepta-local-paper-safe-recover.timer": 0o644,
            "usr/lib/systemd/system/"
            "hepta-local-paper-session-renew.service": 0o644,
            "usr/lib/systemd/system/"
            "hepta-local-paper-session-renew.timer": 0o644,
            "usr/lib/systemd/system/"
            "hepta-local-paper-supervisor.service": 0o644,
            "usr/lib/systemd/system/"
            "hepta-local-paper-supervisor.timer": 0o644,
            "usr/lib/tmpfiles.d/heptatrader-ib-paper.conf": 0o644,
        })
        for unit in IB_UNITS:
            expected_modes[unit] = 0o644
        unit_names.update(IB_UNITS)

    if not root.is_dir() or root.is_symlink():
        fail("DESTDIR install did not create a real staging root")
    expected_files = set(expected_modes)
    expected_directories = parent_directories(expected_files)
    expected_entries = expected_files | expected_directories
    actual_paths = list(root.rglob("*"))
    actual_entries = {path.relative_to(root).as_posix() for path in actual_paths}
    if actual_entries != expected_entries:
        missing = sorted(expected_entries - actual_entries)
        unexpected = sorted(actual_entries - expected_entries)
        fail(f"install tree allowlist mismatch; missing={missing}, "
             f"unexpected={unexpected}")

    for path in actual_paths:
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            fail(f"install tree must not contain symlink {relative}")
        mode = stat.S_IMODE(metadata.st_mode)
        if mode & 0o022:
            fail(f"install tree entry is group/world writable: {relative} {mode:o}")
        if relative in expected_modes:
            if not stat.S_ISREG(metadata.st_mode):
                fail(f"expected regular file at {relative}")
            if mode != expected_modes[relative]:
                fail(f"unexpected mode for {relative}: {mode:04o}")
        else:
            if not stat.S_ISDIR(metadata.st_mode):
                fail(f"expected directory at {relative}")
            if mode != 0o755:
                fail(f"unexpected directory mode for {relative}: {mode:04o}")

    for forbidden in ("etc", "run", "var"):
        candidate = root / forbidden
        if candidate.exists() or candidate.is_symlink():
            fail(f"passive install component must not create /{forbidden} host state")

    binaries = {"usr/libexec/hepta-executiond"}
    if ibapi_enabled:
        binaries.add("usr/libexec/hepta-ib-executiond")
    for binary in binaries:
        if (root / binary).read_bytes()[:4] != b"\x7fELF":
            fail(f"installed Linux executable is not ELF: {binary}")

    verify_units(root, unit_names, ibapi_enabled)
    verify_environment_examples(root, ibapi_enabled)
    if ibapi_enabled:
        verify_tmpfiles(root)
        verify_broker_network_assets(root)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--cmake", default="cmake")
    parser.add_argument("--config")
    parser.add_argument("--ibapi-enabled", action="store_true")
    args = parser.parse_args()

    if not sys.platform.startswith("linux"):
        fail("the execution install-tree contract is Linux-only")
    build_directory = args.build_dir.resolve(strict=True)
    configured = configured_ibapi(build_directory)
    if configured != args.ibapi_enabled:
        fail("--ibapi-enabled must exactly match the configured build tree")

    with tempfile.TemporaryDirectory(prefix="hepta-execution-install-") as directory:
        root = Path(directory) / "stage"
        wrong_prefix_root = Path(directory) / "wrong-prefix-stage"
        wrong_prefix_environment = os.environ.copy()
        wrong_prefix_environment["DESTDIR"] = str(wrong_prefix_root)
        wrong_prefix = subprocess.run(
            [args.cmake, "--install", str(build_directory),
             "--prefix", "/usr/local", "--component", COMPONENT],
            check=False, text=True, capture_output=True,
            env=wrong_prefix_environment)
        if wrong_prefix.returncode == 0:
            fail("execution component must fail closed for a non-/usr prefix")
        if wrong_prefix_root.exists() and any(wrong_prefix_root.rglob("*")):
            fail("prefix validation must run before staging any deployment file")

        command = [args.cmake, "--install", str(build_directory),
                   "--prefix", "/usr", "--component", COMPONENT]
        if args.config:
            command.extend(["--config", args.config])
        environment = os.environ.copy()
        environment["DESTDIR"] = str(root)
        # CMake applies explicit permissions to files, while implicitly created
        # install directories inherit the invoking process umask. Exercise and
        # verify the release contract under a deterministic, non-writable umask.
        previous_umask = os.umask(0o022)
        try:
            result = subprocess.run(command, check=False, text=True,
                                    capture_output=True, env=environment)
        finally:
            os.umask(previous_umask)
        if result.returncode != 0:
            fail("component install failed:\n" + result.stdout + result.stderr)
        verify_tree(root, args.ibapi_enabled)

    print("hepta_execution_install_tree: PASS "
          f"component={COMPONENT} ibapi_enabled={int(args.ibapi_enabled)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as error:
        print(f"hepta_execution_install_tree: FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
