#!/usr/bin/env python3

import hashlib
import os
from pathlib import Path
import re
import shutil
import shlex
import subprocess
import sys
import tempfile

from hepta_service_identities import parse_identity_manifest


ROOT = Path(__file__).resolve().parents[1]
SYSTEMD = ROOT / "systemd"
SOCKET = SYSTEMD / "hepta-tool-session-supervisor.socket"
TOOL_SOCKET = SYSTEMD / "hepta-tool-gateway.socket"
GATEWAY_SERVICE = SYSTEMD / "hepta-tool-gateway.service"
GATEWAY_ENV_EXAMPLE = SYSTEMD / "hepta-tool-gateway.env.example"
DOMAIN_SOCKET = SYSTEMD / "hepta-tool-gateway@.socket"
DOMAIN_SUPERVISOR_SOCKET = (
    SYSTEMD / "hepta-tool-session-supervisor@.socket")
DOMAIN_GATEWAY_SERVICE = SYSTEMD / "hepta-tool-gateway@.service"
DOMAIN_GATEWAY_ENV_EXAMPLE = (
    SYSTEMD / "hepta-tool-gateway-domain.env.example")
DOMAIN_EXECUTION_SERVICE = (
    SYSTEMD / "hepta-execution-simulator@.service")
DOMAIN_EXECUTION_SOCKET = (
    SYSTEMD / "hepta-execution-simulator@.socket")
DOMAIN_EXECUTION_EVENT_SOCKET = (
    SYSTEMD / "hepta-execution-events-simulator@.socket")
IDENTITY_MANIFEST = SYSTEMD / "hepta-service-identities-v1.json"
AGENT_TMPFILES = ROOT / "tmpfiles.d" / "heptatrader-agent-os.conf"
EXECUTION_SERVICE = SYSTEMD / "hepta-execution-simulator.service"
EXECUTION_SOCKET = SYSTEMD / "hepta-execution-simulator.socket"
EXECUTION_EVENT_SOCKET = SYSTEMD / "hepta-execution-events-simulator.socket"
IB_SERVICE = SYSTEMD / "hepta-execution-ib-paper.service"
IB_SOCKET = SYSTEMD / "hepta-execution-ib-paper.socket"
IB_EVENT_SOCKET = SYSTEMD / "hepta-execution-events-ib-paper.socket"
IB_ENV_EXAMPLE = SYSTEMD / "hepta-execution-ib-paper.env.example"
DOMAIN_IB_SERVICE = SYSTEMD / "hepta-execution-ib-paper@.service"
DOMAIN_IB_SOCKET = SYSTEMD / "hepta-execution-ib-paper@.socket"
DOMAIN_IB_EVENT_SOCKET = (
    SYSTEMD / "hepta-execution-events-ib-paper@.socket")
DOMAIN_IB_PREFLIGHT = (
    SYSTEMD / "hepta-ib-paper-domain-preflight@.service")
DOMAIN_CAMPAIGN_SERVICE = (
    SYSTEMD / "hepta-ib-paper-campaign-operator@.service")
DOMAIN_CAMPAIGN_SOCKET = (
    SYSTEMD / "hepta-ib-paper-campaign-operator@.socket")
LOCAL_AI_PAPER_AGENT_SERVICE = (
    SYSTEMD / "hepta-local-ai-paper-agent.service")
LOCAL_AI_PAPER_AGENT_ENV_EXAMPLE = (
    SYSTEMD / "hepta-local-ai-paper-agent.env.example")
LOCAL_PAPER_SAFE_RECOVER_SERVICE = (
    SYSTEMD / "hepta-local-paper-safe-recover.service")
LOCAL_PAPER_SAFE_RECOVER_TIMER = (
    SYSTEMD / "hepta-local-paper-safe-recover.timer")
LOCAL_PAPER_SESSION_RENEW_SERVICE = (
    SYSTEMD / "hepta-local-paper-session-renew.service")
LOCAL_PAPER_SESSION_RENEW_TIMER = (
    SYSTEMD / "hepta-local-paper-session-renew.timer")
LOCAL_PAPER_SUPERVISOR_SERVICE = (
    SYSTEMD / "hepta-local-paper-supervisor.service")
LOCAL_PAPER_SUPERVISOR_TIMER = (
    SYSTEMD / "hepta-local-paper-supervisor.timer")
WATCH_COLLECTOR_SERVICE = (
    SYSTEMD / "hepta-shadow-watch-collector@.service")
WATCH_COLLECTOR_TIMER = (
    SYSTEMD / "hepta-shadow-watch-collector@.timer")
WATCH_EXPORT_SERVICE = (
    SYSTEMD / "hepta-shadow-watch-export@.service")
WATCH_CUSTODIAN_SERVICE = (
    SYSTEMD / "hepta-shadow-watch-custodian@.service")
WATCH_CUSTODIAN_RECONCILE_SERVICE = (
    SYSTEMD / "hepta-shadow-watch-custodian-reconcile@.service")
WATCH_CUSTODIAN_RECONCILE_TIMER = (
    SYSTEMD / "hepta-shadow-watch-custodian-reconcile@.timer")
BROKER_POLICY_SERVICE = (
    SYSTEMD / "hepta-broker-egress-policy.service")
WATCH_ACTIVATION_SERVICE = (
    SYSTEMD / "hepta-p1-watch-activation.service")
WATCH_ACTIVATION_RECONCILE_SERVICE = (
    SYSTEMD / "hepta-p1-watch-activation-reconcile.service")
WATCH_ACTIVATION_RECONCILE_TIMER = (
    SYSTEMD / "hepta-p1-watch-activation-reconcile.timer")
LOCAL_PAPER_AUTHORITY_SERVICE = (
    SYSTEMD / "hepta-local-paper-authority@.service")
LOCAL_PAPER_FAIL_CLOSE_SERVICE = (
    SYSTEMD / "hepta-local-paper-fail-close@.service")
P1_CANARY_CAPTURE_SERVICE = (
    SYSTEMD / "hepta-p1-paper-canary-capture.service")
P1_CANARY_EXECUTOR_SERVICE = (
    SYSTEMD / "hepta-p1-paper-canary-executor.service")
P1_CANARY_FINALIZER_SOCKET = (
    SYSTEMD / "hepta-p1-paper-canary-finalizer.socket")
P1_CANARY_FINALIZER_SERVICE = (
    SYSTEMD / "hepta-p1-paper-canary-finalizer@.service")
P1_CANARY_ROOT_COORDINATOR_SERVICE = (
    SYSTEMD / "hepta-p1-paper-canary-root-coordinator.service")
P1_TERMINAL_CUTOFF_SERVICE = (
    SYSTEMD / "hepta-p1-paper-terminal-cutoff@.service")
P1_TERMINAL_WITNESS_SERVICE = (
    SYSTEMD / "hepta-p1-paper-terminal-witness-verifier@.service")
DOMAIN_IB_ENV_EXAMPLE = (
    SYSTEMD / "hepta-execution-ib-paper-domain.env.example")
GATEWAY_PAPER_ENV_EXAMPLE = (
    SYSTEMD / "hepta-execution-gateway-paper.env.example")
DOMAIN_GATEWAY_PAPER_ENV_EXAMPLE = (
    SYSTEMD / "hepta-execution-gateway-paper-domain.env.example")
IB_TMPFILES = ROOT / "tmpfiles.d" / "heptatrader-ib-paper.conf"
IB_CONTROL_DIRECTORY = "/run/hepta/ib-paper-control"
IB_KILL_SWITCH_MARKER = IB_CONTROL_DIRECTORY + "/kill-switch"
SYSTEMD_VERIFY_UNIT_PATHS = (
    GATEWAY_SERVICE, TOOL_SOCKET, SOCKET,
    DOMAIN_GATEWAY_SERVICE, DOMAIN_SOCKET, DOMAIN_SUPERVISOR_SOCKET,
    EXECUTION_SERVICE, EXECUTION_SOCKET, EXECUTION_EVENT_SOCKET,
    DOMAIN_EXECUTION_SERVICE, DOMAIN_EXECUTION_SOCKET,
    DOMAIN_EXECUTION_EVENT_SOCKET,
    IB_SERVICE, IB_SOCKET, IB_EVENT_SOCKET,
    DOMAIN_IB_SERVICE, DOMAIN_IB_SOCKET, DOMAIN_IB_EVENT_SOCKET,
    DOMAIN_IB_PREFLIGHT, DOMAIN_CAMPAIGN_SERVICE, DOMAIN_CAMPAIGN_SOCKET,
    LOCAL_AI_PAPER_AGENT_SERVICE, LOCAL_PAPER_SAFE_RECOVER_SERVICE,
    LOCAL_PAPER_SAFE_RECOVER_TIMER, LOCAL_PAPER_SESSION_RENEW_SERVICE,
    LOCAL_PAPER_SESSION_RENEW_TIMER, LOCAL_PAPER_SUPERVISOR_SERVICE,
    LOCAL_PAPER_SUPERVISOR_TIMER,
    BROKER_POLICY_SERVICE, WATCH_ACTIVATION_SERVICE,
    WATCH_ACTIVATION_RECONCILE_SERVICE,
    WATCH_ACTIVATION_RECONCILE_TIMER,
    LOCAL_PAPER_AUTHORITY_SERVICE,
    LOCAL_PAPER_FAIL_CLOSE_SERVICE,
    P1_CANARY_CAPTURE_SERVICE, P1_CANARY_EXECUTOR_SERVICE,
    P1_CANARY_FINALIZER_SOCKET, P1_CANARY_FINALIZER_SERVICE,
    P1_CANARY_ROOT_COORDINATOR_SERVICE,
    P1_TERMINAL_CUTOFF_SERVICE, P1_TERMINAL_WITNESS_SERVICE,
    WATCH_COLLECTOR_SERVICE, WATCH_COLLECTOR_TIMER, WATCH_EXPORT_SERVICE,
    WATCH_CUSTODIAN_SERVICE, WATCH_CUSTODIAN_RECONCILE_SERVICE,
    WATCH_CUSTODIAN_RECONCILE_TIMER,
)
LOCAL_DEPENDENCY_SETTINGS = (
    ("Unit", "Requires"),
    ("Unit", "OnFailure"),
    ("Unit", "OnSuccess"),
    ("Socket", "Service"),
    ("Timer", "Unit"),
)

Unit = dict[str, dict[str, list[str]]]
REPEATABLE_SETTINGS = {
    ("Service", "Environment"),
    ("Service", "LoadCredential"),
    ("Service", "IPAddressAllow"),
    ("Service", "ReadOnlyPaths"),
    ("Service", "ReadWritePaths"),
    ("Service", "BindReadOnlyPaths"),
}


def settings(path: Path) -> Unit:
    sections: Unit = {}
    section = ""
    for line_number, raw_line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            sections.setdefault(section, {})
            continue
        if not section or "=" not in line:
            raise AssertionError(f"{path}:{line_number}: invalid unit directive")
        key, value = line.split("=", 1)
        if (key in sections[section] and
                (section, key) not in REPEATABLE_SETTINGS):
            raise AssertionError(
                f"{path}:{line_number}: duplicate [{section}] {key} is forbidden")
        sections[section].setdefault(key, []).append(value)
    return sections


def check_local_dependency_closure(unit_paths: tuple[Path, ...]) -> None:
    names = [path.name for path in unit_paths]
    if len(names) != len(set(names)):
        raise AssertionError(
            "systemd verify unit tuple contains duplicate unit names")
    available = set(names)
    missing: list[str] = []
    for source in unit_paths:
        unit = settings(source)
        for section, key in LOCAL_DEPENDENCY_SETTINGS:
            for value in unit.get(section, {}).get(key, []):
                for dependency in value.split():
                    normalized = dependency.replace("@%i.", "@.")
                    normalized = re.sub(
                        r"@[^.]+(?=\.)", "@", normalized)
                    if (normalized.startswith("hepta-") and
                            normalized not in available):
                        missing.append(
                            f"{source.name} [{section}] {key} -> "
                            f"{normalized}")
    if missing:
        raise AssertionError(
            "systemd verify local dependency closure is incomplete: " +
            ", ".join(sorted(missing)))


def require(unit: Unit, section: str, key: str, expected: str) -> None:
    if expected not in unit.get(section, {}).get(key, []):
        raise AssertionError(
            f"[{section}] {key} must contain {expected!r}")


def require_no_install(unit: Unit, name: str) -> None:
    if "Install" in unit:
        raise AssertionError(f"{name} must not contain an [Install] section")


def check_gateway_restart_reactivation_contract(
        gateway_service: Unit, tool_socket: Unit,
        supervisor_socket: Unit) -> None:
    """Check the source-level ownership contract for socket reactivation.

    Both socket units remain active when the gateway service is restarted.
    The supervisor socket is inside the service RuntimeDirectory, so that
    directory must survive every service stop; the socket units remain
    responsible for removing their own leaves when they are stopped.
    """
    require(gateway_service, "Unit", "Requires",
            "hepta-tool-gateway.socket hepta-tool-session-supervisor.socket")
    require(gateway_service, "Unit", "After",
            "hepta-tool-gateway.socket hepta-tool-session-supervisor.socket")
    require(gateway_service, "Service", "RuntimeDirectory",
            "hepta-tool-gateway")
    require(gateway_service, "Service", "RuntimeDirectoryMode", "0700")
    require(gateway_service, "Service", "RuntimeDirectoryPreserve", "yes")
    require(gateway_service, "Service", "Restart", "on-failure")
    require(supervisor_socket, "Socket", "ListenStream",
            "/run/hepta-tool-gateway/session-supervisor.sock")
    require(supervisor_socket, "Socket", "Service",
            "hepta-tool-gateway.service")
    require(supervisor_socket, "Socket", "RemoveOnStop", "yes")
    require(tool_socket, "Socket", "ListenStream",
            "/run/hepta-agent/tools.sock")
    require(tool_socket, "Socket", "Service",
            "hepta-tool-gateway.service")
    require(tool_socket, "Socket", "RemoveOnStop", "yes")


def joined(unit: Unit) -> str:
    return "\n".join(
        value
        for section in unit.values()
        for values in section.values()
        for value in values)


def environment_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise AssertionError(
                f"{path}:{line_number}: invalid environment assignment")
        key, value = line.split("=", 1)
        if (not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or
                key in values or not value):
            raise AssertionError(
                f"{path}:{line_number}: invalid environment assignment")
        values[key] = value
    return values


def tmpfiles_entries(path: Path) -> list[list[str]]:
    entries: list[list[str]] = []
    for line_number, raw_line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            fields = shlex.split(line, comments=True, posix=True)
        except ValueError as error:
            raise AssertionError(f"{path}:{line_number}: {error}") from error
        if len(fields) < 6 or len(fields) > 7:
            raise AssertionError(
                f"{path}:{line_number}: tmpfiles entry must have 6 or 7 fields")
        entries.append(fields)
    return entries


def require_tmpfiles_entry(entries: list[list[str]], expected: list[str]) -> None:
    if expected not in entries:
        raise AssertionError("tmpfiles config must contain: " + " ".join(expected))


def check_static_contract() -> None:
    socket = settings(SOCKET)
    tool_socket = settings(TOOL_SOCKET)
    gateway_service = settings(GATEWAY_SERVICE)
    domain_socket = settings(DOMAIN_SOCKET)
    domain_supervisor = settings(DOMAIN_SUPERVISOR_SOCKET)
    domain_service = settings(DOMAIN_GATEWAY_SERVICE)
    domain_execution_service = settings(DOMAIN_EXECUTION_SERVICE)
    domain_execution_socket = settings(DOMAIN_EXECUTION_SOCKET)
    domain_execution_event_socket = settings(DOMAIN_EXECUTION_EVENT_SOCKET)
    domain_ib_service = settings(DOMAIN_IB_SERVICE)
    domain_ib_socket = settings(DOMAIN_IB_SOCKET)
    domain_ib_event_socket = settings(DOMAIN_IB_EVENT_SOCKET)
    domain_ib_preflight = settings(DOMAIN_IB_PREFLIGHT)
    domain_campaign_service = settings(DOMAIN_CAMPAIGN_SERVICE)
    domain_campaign_socket = settings(DOMAIN_CAMPAIGN_SOCKET)
    local_agent = settings(LOCAL_AI_PAPER_AGENT_SERVICE)
    local_recover = settings(LOCAL_PAPER_SAFE_RECOVER_SERVICE)
    local_recover_timer = settings(LOCAL_PAPER_SAFE_RECOVER_TIMER)
    local_session_renew = settings(LOCAL_PAPER_SESSION_RENEW_SERVICE)
    local_session_renew_timer = settings(LOCAL_PAPER_SESSION_RENEW_TIMER)
    local_supervisor = settings(LOCAL_PAPER_SUPERVISOR_SERVICE)
    local_supervisor_timer = settings(LOCAL_PAPER_SUPERVISOR_TIMER)
    watch_collector = settings(WATCH_COLLECTOR_SERVICE)
    watch_collector_timer = settings(WATCH_COLLECTOR_TIMER)
    watch_export = settings(WATCH_EXPORT_SERVICE)
    watch_custodian = settings(WATCH_CUSTODIAN_SERVICE)
    watch_custodian_reconcile = settings(
        WATCH_CUSTODIAN_RECONCILE_SERVICE)
    watch_custodian_timer = settings(
        WATCH_CUSTODIAN_RECONCILE_TIMER)
    watch_activation = settings(WATCH_ACTIVATION_SERVICE)
    watch_activation_reconcile = settings(
        WATCH_ACTIVATION_RECONCILE_SERVICE)
    watch_activation_timer = settings(WATCH_ACTIVATION_RECONCILE_TIMER)
    terminal_cutoff = settings(P1_TERMINAL_CUTOFF_SERVICE)
    terminal_witness = settings(P1_TERMINAL_WITNESS_SERVICE)
    for unit, name, operation in (
            (terminal_cutoff, "terminal cutoff", "--record-cutoff"),
            (terminal_witness, "terminal witness verifier", "--run")):
        require_no_install(unit, name)
        require(unit, "Service", "Type", "oneshot")
        require(unit, "Service", "User", "root")
        require(unit, "Service", "Group", "root")
        require(unit, "Service", "PrivateNetwork", "yes")
        require(unit, "Service", "RestrictAddressFamilies", "AF_UNIX")
        require(unit, "Service", "IPAddressDeny", "any")
        require(unit, "Service", "CapabilityBoundingSet", "")
        require(unit, "Service", "AmbientCapabilities", "")
        require(unit, "Service", "RuntimeDirectoryPreserve", "yes")
        if operation not in " ".join(
                unit.get("Service", {}).get("ExecStart", [])):
            raise AssertionError(f"{name} operation drifted")
    if "hepta-execution-ib-paper" in " ".join(
            terminal_cutoff.get("Unit", {}).get("Conflicts", [])):
        raise AssertionError(
            "terminal cutoff must prepare HPT1 before stopping execution")
    require(
        local_agent, "Unit", "OnFailure",
        "hepta-local-paper-safe-recover.service")
    expected_agent_requisites = (
        "hepta-tool-gateway@alpha.service "
        "hepta-execution-ib-paper@alpha.service "
        "hepta-ib-paper-campaign-operator@alpha.socket "
        "hepta-local-paper-safe-recover.timer "
        "hepta-local-paper-session-renew.timer "
        "hepta-local-paper-supervisor.timer "
        "hepta-local-ai-paper-24h-stop.timer "
        "hepta-local-ai-paper-end-flat-retry.timer")
    if local_agent.get("Unit", {}).get("Requisite") != [
            expected_agent_requisites]:
        raise AssertionError(
            "local PAPER agent requisite safety closure drifted")
    if "Requires" in local_agent.get("Unit", {}):
        raise AssertionError(
            "local PAPER agent must use Requisite, not start dependencies")
    require_no_install(local_agent, "local PAPER agent service")
    require(local_agent, "Service", "User", "root")
    require(local_agent, "Service", "Group", "root")
    require(
        local_agent, "Service", "EnvironmentFile",
        "/etc/heptatrader/local-ai-paper-agent.env")
    if local_agent.get("Service", {}).get("ExecCondition") != [
            "/usr/libexec/hepta-local-paper-repair pre-start-guard"]:
        raise AssertionError(
            "local PAPER agent pre-start repair guard drifted")
    if local_agent.get("Service", {}).get("Restart") != ["no"]:
        raise AssertionError(
            "local PAPER agent must never be automatically restarted")
    if "RestartPreventExitStatus" in local_agent.get("Service", {}):
        raise AssertionError(
            "local PAPER agent retains obsolete restart policy")
    require(
        local_agent, "Service", "StateDirectory",
        "hepta-local-ai-paper-agent")
    expected_agent_exec = (
        "/usr/libexec/hepta-local-ai-paper-agent "
        "--campaign-id ${HEPTA_LOCAL_AI_CAMPAIGN_ID} "
        "--strategy-id ${HEPTA_LOCAL_AI_STRATEGY_ID} "
        "--strategy-version ${HEPTA_LOCAL_AI_STRATEGY_VERSION} "
        "--strategy-sha256 ${HEPTA_LOCAL_AI_STRATEGY_SHA256} "
        "--auth-generation ${HEPTA_LOCAL_AI_AUTH_GENERATION} "
        "--auth-profile-id ${HEPTA_LOCAL_AI_AUTH_PROFILE_ID} "
        "--auth-profile-allowlist-sha256 "
        "${HEPTA_LOCAL_AI_AUTH_PROFILE_ALLOWLIST_SHA256}")
    if local_agent.get("Service", {}).get("ExecStart") != [
            expected_agent_exec]:
        raise AssertionError(
            "local PAPER agent generation-bound ExecStart drifted")
    if local_agent.get("Service", {}).get("InaccessiblePaths") != [
            "/var/lib/hepta-local-ai-paper-agent/session-authority"]:
        raise AssertionError(
            "local PAPER agent can access the session authority custodian")
    expected_agent_hardening = {
        "CapabilityBoundingSet": [
            "CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER CAP_KILL "
            "CAP_SETGID CAP_SETUID"],
        "AmbientCapabilities": [""],
        "RestrictNamespaces": ["yes"],
        "SystemCallFilter": ["~@mount"],
    }
    for key, expected in expected_agent_hardening.items():
        if local_agent.get("Service", {}).get(key) != expected:
            raise AssertionError(
                f"local PAPER agent {key} hardening drifted")
    require_no_install(local_recover, "local PAPER safe-recovery service")
    require(local_recover, "Service", "Type", "oneshot")
    require(local_recover, "Service", "User", "root")
    require(local_recover, "Service", "Group", "root")
    require(
        local_recover, "Service", "ExecStart",
        "/usr/libexec/hepta-local-paper-safe-recover-guard")
    require(local_recover, "Service", "TimeoutStartSec", "300s")
    require(local_recover, "Service", "ProtectSystem", "strict")
    require(local_recover, "Service", "UMask", "0077")
    require(
        local_recover, "Service", "ReadWritePaths",
        "/etc/heptatrader /etc/systemd/system "
        "/var/lib/hepta-local-ai-paper-agent -/run/hepta-agent-alpha")
    require(local_recover_timer, "Timer", "OnBootSec", "60s")
    require(
        local_recover_timer, "Timer", "OnUnitInactiveSec", "60s")
    require(local_recover_timer, "Timer", "AccuracySec", "5s")
    require(local_recover_timer, "Timer", "RandomizedDelaySec", "0")
    require(
        local_recover_timer, "Timer", "Unit",
        "hepta-local-paper-safe-recover.service")
    require(
        local_recover_timer, "Install", "WantedBy", "timers.target")
    require_no_install(
        local_session_renew, "local PAPER session-renew service")
    require(local_session_renew, "Unit", "After",
            "hepta-tool-gateway@alpha.service")
    require(
        local_session_renew, "Unit", "OnFailure",
        "hepta-local-paper-safe-recover.service")
    require(local_session_renew, "Service", "Type", "oneshot")
    require(local_session_renew, "Service", "User", "root")
    require(local_session_renew, "Service", "Group", "root")
    require(
        local_session_renew, "Service", "ExecStart",
        "/usr/libexec/hepta-local-paper-session-renew")
    require(local_session_renew, "Service", "TimeoutStartSec", "60s")
    require(local_session_renew, "Service", "ProtectSystem", "strict")
    require(
        local_session_renew, "Service", "ReadWritePaths",
        "/var/lib/hepta-local-ai-paper-agent -/run/hepta-agent-alpha")
    require(local_session_renew_timer, "Timer", "OnBootSec", "60s")
    require(
        local_session_renew_timer, "Timer", "OnUnitInactiveSec", "1h")
    require(local_session_renew_timer, "Timer", "AccuracySec", "5s")
    require(local_session_renew_timer, "Timer", "RandomizedDelaySec", "0")
    require(
        local_session_renew_timer, "Timer", "Unit",
        "hepta-local-paper-session-renew.service")
    require(
        local_session_renew_timer, "Install", "WantedBy", "timers.target")
    require_no_install(local_supervisor, "local PAPER supervisor service")
    require(
        local_supervisor, "Unit", "After",
        "hepta-local-ai-paper-agent.service hepta-tool-gateway@alpha.service")
    require(local_supervisor, "Service", "Type", "oneshot")
    require(local_supervisor, "Service", "User", "root")
    require(local_supervisor, "Service", "Group", "root")
    require(
        local_supervisor, "Service", "ExecStart",
        "/usr/libexec/hepta-local-paper-supervisor")
    require(local_supervisor, "Service", "TimeoutStartSec", "45s")
    require(local_supervisor, "Service", "ProtectSystem", "strict")
    require(
        local_supervisor, "Service", "ReadWritePaths",
        "/var/lib/hepta-local-ai-paper-agent")
    require(local_supervisor_timer, "Timer", "OnBootSec", "60s")
    require(
        local_supervisor_timer, "Timer", "OnUnitInactiveSec", "60s")
    require(local_supervisor_timer, "Timer", "AccuracySec", "5s")
    require(local_supervisor_timer, "Timer", "RandomizedDelaySec", "0")
    require(
        local_supervisor_timer, "Timer", "Unit",
        "hepta-local-paper-supervisor.service")
    require(
        local_supervisor_timer, "Install", "WantedBy", "timers.target")
    agent_environment = environment_values(
        LOCAL_AI_PAPER_AGENT_ENV_EXAMPLE)
    expected_agent_environment = {
        "HEPTA_LOCAL_AI_CAMPAIGN_ID":
            "replace-with-new-paper-campaign-id",
        "HEPTA_LOCAL_AI_STRATEGY_ID":
            "hepta-local-ai-paper-strategy-v3",
        "HEPTA_LOCAL_AI_STRATEGY_VERSION": "3",
        "HEPTA_LOCAL_AI_STRATEGY_SHA256": "sha256:" + hashlib.sha256(
            (ROOT / "configs/hepta-local-ai-paper-strategy-v3.json").
            read_bytes()).hexdigest(),
        "HEPTA_LOCAL_AI_AUTH_GENERATION":
            "replace-with-new-auth-generation",
        "HEPTA_LOCAL_AI_AUTH_PROFILE_ID":
            "replace-with-reviewed-auth-profile-id",
        "HEPTA_LOCAL_AI_AUTH_PROFILE_ALLOWLIST_SHA256":
            "sha256:" + "0" * 64,
    }
    if agent_environment != expected_agent_environment:
        raise AssertionError(
            "local PAPER agent environment example drifted")
    require_no_install(watch_activation, "WATCH activation service")
    require_no_install(
        watch_activation_reconcile, "WATCH activation reconcile service")
    require(
        watch_activation, "Unit", "OnFailure",
        "hepta-p1-watch-activation-reconcile.service")
    require(
        watch_activation, "Unit", "After",
        "local-fs.target systemd-remount-fs.service")
    if "Requires" in watch_activation.get("Unit", {}):
        raise AssertionError(
            "WATCH activation must not pre-start its reconcile timer")
    require(
        watch_activation_reconcile, "Unit", "After",
        "local-fs.target systemd-remount-fs.service "
        "hepta-p1-watch-activation.service")
    expected_activation_credentials = [
        "hepta-p1-watch-activation-transaction.py:"
        "/usr/libexec/hepta-p1-watch-activation-transaction",
        "hepta-p1-watch-profile-deployer.py:"
        "/usr/libexec/hepta-p1-watch-profile-deployer",
        "hepta-broker-egress-policy.py:"
        "/usr/libexec/hepta-broker-egress-policy",
        "hepta-shadow-host-installer.py:"
        "/usr/libexec/hepta-shadow-host-installer",
    ]
    for unit, action, timeout in (
            (watch_activation, "activate", "5min"),
            (watch_activation_reconcile, "reconcile", "3min")):
        require(unit, "Unit", "DefaultDependencies", "no")
        require(unit, "Service", "Type", "oneshot")
        require(unit, "Service", "User", "root")
        require(unit, "Service", "Group", "root")
        if unit.get("Service", {}).get("LoadCredential") != \
                expected_activation_credentials:
            raise AssertionError(
                "WATCH activation credential source closure drifted")
        require(
            unit, "Service", "Environment",
            "HEPTA_ACTIVATION_REQUIRE_CREDENTIALS=1")
        require(
            unit, "Service", "ExecStart",
            "/usr/bin/python3.12 -I -S ${CREDENTIALS_DIRECTORY}/"
            f"hepta-p1-watch-activation-transaction.py {action}")
        require(unit, "Service", "TimeoutStartSec", timeout)
        for key, expected in {
                "UMask": "0077",
                "NoNewPrivileges": "yes",
                "PrivateDevices": "yes",
                "ProtectSystem": "strict",
                "ProtectHome": "yes",
                "RestrictNamespaces": "yes",
                "MemoryDenyWriteExecute": "yes",
                "CapabilityBoundingSet":
                    "CAP_CHOWN CAP_DAC_OVERRIDE CAP_DAC_READ_SEARCH "
                    "CAP_FOWNER CAP_SYS_PTRACE CAP_NET_ADMIN",
                "AmbientCapabilities": "",
                "RestrictAddressFamilies": "AF_UNIX AF_NETLINK",
        }.items():
            require(unit, "Service", key, expected)
        if unit.get("Service", {}).get("ReadOnlyPaths") != [
                "/etc/heptatrader", "/usr/libexec",
                "/usr/lib/systemd/system"]:
            raise AssertionError(
                "WATCH activation read-only path closure drifted")
        if unit.get("Service", {}).get("ReadWritePaths") != [
                "/etc/systemd/system", "/run/systemd/system",
                "/var/lib/hepta/p1-admission",
                "/var/lib/hepta/shadow-observation",
                "/var/lib/heptatrader"]:
            raise AssertionError(
                "WATCH activation write path closure drifted")
    require(
        watch_activation_timer, "Timer", "Unit",
        "hepta-p1-watch-activation-reconcile.service")
    require(watch_activation_timer, "Timer", "OnActiveSec", "30s")
    require(watch_activation_timer, "Timer", "OnUnitActiveSec", "30s")
    require(watch_activation_timer, "Timer", "AccuracySec", "1s")
    if any(
            key in watch_activation_timer.get("Timer", {})
            for key in ("OnBootSec", "Persistent")):
        raise AssertionError(
            "WATCH activation timer must be relative to its own activation")
    require(
        watch_activation_timer, "Install", "WantedBy", "timers.target")
    if any(
            forbidden in joined(unit)
            for unit in (
                watch_activation, watch_activation_reconcile,
                watch_activation_timer)
            for forbidden in (
                "hepta-execution-ib-paper", "hepta-ib-paper-campaign",
                "HEPTA_TOOL_ALLOW_TRADE=1", "HEPTA_TOOL_ACCOUNT=LIVE")):
        raise AssertionError(
            "WATCH activation units contain PAPER/LIVE authority surface")
    check_gateway_restart_reactivation_contract(
        gateway_service, tool_socket, socket)
    require_no_install(
        domain_campaign_service, "domain campaign operator service")
    require_no_install(
        domain_campaign_socket, "domain campaign operator socket")
    require_no_install(
        watch_collector, "WATCH collector service")
    require_no_install(
        watch_collector_timer, "WATCH collector timer")
    require_no_install(
        watch_export, "WATCH export service")
    require(
        watch_collector, "Unit", "Requires",
        "hepta-tool-gateway@%i.socket "
        "hepta-tool-session-supervisor@%i.socket")
    require(
        watch_collector, "Unit", "OnSuccess",
        "hepta-shadow-watch-export@%i.service")
    require(
        watch_collector, "Service", "ExecStart",
        "/usr/libexec/hepta-shadow-watch-collector --domain-config "
        "/etc/heptatrader/trust-domains/"
        "uid-${HEPTA_SHADOW_AGENT_UID}.json --output "
        "/var/lib/hepta-shadow-watch-%i/private/snapshot.json "
        "--instrument EUR.USD")
    require(
        watch_collector_timer, "Timer", "Unit",
        "hepta-shadow-watch-collector@%i.service")
    require(
        watch_export, "Unit", "After",
        "hepta-shadow-watch-collector@%i.service")
    require(watch_export, "Service", "User", "root")
    require(watch_export, "Service", "Group", "root")
    require(
        watch_export, "Service", "ExecStart",
        "/usr/libexec/hepta-shadow-watch-exporter --source "
        "/var/lib/hepta-shadow-watch-%i/private/snapshot.json "
        "--destination /run/hepta-shadow-watch-export-%i/snapshot.json "
        "--agent-uid ${HEPTA_SHADOW_AGENT_UID} "
        "--agent-gid ${HEPTA_SHADOW_AGENT_GID} "
        "--reader-uid ${HEPTA_SHADOW_READER_UID} "
        "--reader-gid ${HEPTA_SHADOW_READER_GID} "
        "--lease-receipt-source /run/hepta-agent-%i/sessions/"
        "shadow-watch-lease-receipt.json --lease-receipt-destination "
        "/run/hepta-shadow-watch-export-%i/"
        "shadow-watch-lease-receipt.json --export-receipt-destination "
        "/run/hepta-shadow-watch-export-%i/"
        "shadow-watch-export-receipt.json")
    require(watch_export, "Service", "PrivateNetwork", "yes")
    require(
        watch_export, "Service", "RestrictAddressFamilies", "AF_UNIX")
    require(
        watch_export, "Service", "CapabilityBoundingSet",
        "CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER")
    require(
        watch_export, "Service", "ReadWritePaths",
        "-/run/hepta-shadow-watch-export-%i")
    require_no_install(
        watch_custodian, "WATCH custodian service")
    require_no_install(
        watch_custodian_reconcile, "WATCH custodian reconcile service")
    require(
        watch_custodian, "Service", "ExecStart",
        "/usr/libexec/hepta-shadow-watch-custodian --domain-config "
        "/etc/heptatrader/trust-domains/%i.json supervise")
    require(
        watch_custodian, "Service", "ExecStop",
        "/usr/libexec/hepta-shadow-watch-custodian --domain-config "
        "/etc/heptatrader/trust-domains/%i.json close "
        "--reason service-stop")
    require(
        watch_custodian, "Service", "ExecStopPost",
        "/usr/libexec/hepta-shadow-watch-custodian --domain-config "
        "/etc/heptatrader/trust-domains/%i.json close "
        "--reason service-stop-post")
    require(watch_custodian, "Service", "User", "root")
    require(watch_custodian, "Service", "Group", "root")
    require(watch_custodian, "Service", "Restart", "on-failure")
    for unit in (watch_custodian, watch_custodian_reconcile):
        require(unit, "Service", "PrivateNetwork", "yes")
        require(unit, "Service", "ProtectSystem", "strict")
        require(
            unit, "Service", "RestrictAddressFamilies", "AF_UNIX")
        require(
            unit, "Service", "CapabilityBoundingSet",
            "CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER")
        require(unit, "Service", "AmbientCapabilities", "")
        require(
            unit, "Service", "StateDirectory",
            "hepta-shadow-watch-custodian")
        require(
            unit, "Service", "ReadWritePaths",
            "/var/lib/hepta-shadow-watch-custodian")
        require(
            unit, "Service", "ReadWritePaths",
            "-/run/hepta-agent-%i/sessions")
        require(
            unit, "Service", "ReadWritePaths",
            "-/run/hepta-shadow-watch-export-%i")
    require(
        watch_custodian, "Service", "ReadWritePaths",
        "/var/lib/hepta-shadow-watch-%i/private")
    require(
        watch_custodian_reconcile, "Service", "ReadWritePaths",
        "-/var/lib/hepta-shadow-watch-%i/private")
    require(
        watch_custodian_reconcile, "Service", "ExecStart",
        "/usr/libexec/hepta-shadow-watch-custodian --domain-config "
        "/etc/heptatrader/trust-domains/%i.json reconcile")
    require(watch_custodian_reconcile, "Service", "Type", "oneshot")
    require(
        watch_custodian_timer, "Timer", "Unit",
        "hepta-shadow-watch-custodian-reconcile@%i.service")
    require(watch_custodian_timer, "Timer", "Persistent", "true")
    require(
        watch_custodian_timer, "Install", "WantedBy", "timers.target")
    if any(
            forbidden in joined(unit)
            for unit in (
                watch_custodian, watch_custodian_reconcile,
                watch_custodian_timer)
            for forbidden in (
                "provision-watch", " rotate", " renew",
                "risk.preview", "trade.")):
        raise AssertionError(
            "WATCH custodian units contain authority-granting surface")
    require(
        domain_campaign_socket, "Unit", "BindsTo",
        "hepta-ib-paper-domain-preflight@%i.service")
    require(
        domain_campaign_socket, "Socket", "ListenStream",
        "/run/hepta-agent-%i/campaign.sock")
    require(
        domain_campaign_socket, "Socket", "SocketUser",
        "hepta-agent-%i")
    require(
        domain_campaign_socket, "Socket", "SocketGroup",
        "hepta-agent-%i")
    require(domain_campaign_socket, "Socket", "SocketMode", "0600")
    require(
        domain_campaign_socket, "Socket", "Service",
        "hepta-ib-paper-campaign-operator@%i.service")
    require(domain_campaign_service, "Service", "User", "root")
    require(domain_campaign_service, "Service", "Group", "root")
    require(
        domain_campaign_service, "Service", "ExecStart",
        "/usr/libexec/hepta-ib-paper-campaign-operator "
        "--serve-once --domain %i")
    require(domain_campaign_service, "Service", "NoNewPrivileges", "yes")
    require(domain_campaign_service, "Service", "PrivateNetwork", "yes")
    require(domain_campaign_service, "Service", "ProtectSystem", "strict")
    require(
        domain_campaign_service, "Service", "RestrictAddressFamilies",
        "AF_UNIX")
    require(
        domain_campaign_service, "Service", "CapabilityBoundingSet",
        "CAP_CHOWN")
    require(domain_campaign_service, "Service", "AmbientCapabilities", "")
    require(socket, "Socket", "Service", "hepta-tool-gateway.service")
    require(socket, "Socket", "SocketMode", "0600")
    require(socket, "Socket", "DirectoryMode", "0700")
    require(socket, "Socket", "SocketUser", "hepta-gateway")
    require(socket, "Socket", "SocketGroup", "hepta-gateway")
    require(socket, "Socket", "FileDescriptorName", "hepta-supervisor")
    require(tool_socket, "Socket", "Service", "hepta-tool-gateway.service")
    require(tool_socket, "Socket", "FileDescriptorName", "hepta-tool")
    require(tool_socket, "Socket", "ListenStream", "/run/hepta-agent/tools.sock")
    require(tool_socket, "Socket", "SocketMode", "0600")
    require(tool_socket, "Socket", "DirectoryMode", "0711")
    require(tool_socket, "Socket", "SocketUser", "hepta-agent")
    require(tool_socket, "Socket", "SocketGroup", "hepta-agent")
    require(gateway_service, "Service", "ExecStart", "/usr/libexec/hepta-tool-gatewayd")
    require(gateway_service, "Service", "EnvironmentFile",
            "/etc/heptatrader/hepta-tool-gateway.env")
    require(gateway_service, "Service", "Environment",
            "HEPTA_TOOL_SOCKET=/run/hepta-agent/tools.sock")
    require(gateway_service, "Service", "Sockets",
            "hepta-tool-gateway.socket hepta-tool-session-supervisor.socket")
    require(gateway_service, "Service", "PrivateNetwork", "yes")
    require(gateway_service, "Service", "CapabilityBoundingSet", "")
    for key, expected in {
        "UMask": "0077", "NoNewPrivileges": "yes", "PrivateDevices": "yes",
        "ProtectSystem": "strict", "ProtectHome": "yes",
        "RestrictSUIDSGID": "yes", "MemoryDenyWriteExecute": "yes",
        "RestrictAddressFamilies": "AF_UNIX",
    }.items():
        require(gateway_service, "Service", key, expected)
    if any("AF_INET" in value for value in
           gateway_service.get("Service", {}).get("RestrictAddressFamilies", [])):
        raise AssertionError("Tool Gateway must not permit broker network families")
    require(domain_socket, "Socket", "ListenStream",
            "/run/hepta-agent-%i/tools.sock")
    require(domain_socket, "Socket", "SocketUser", "hepta-agent-%i")
    require(domain_socket, "Socket", "Service",
            "hepta-tool-gateway@%i.service")
    require(domain_supervisor, "Socket", "ListenStream",
            "/run/hepta-tool-gateway-%i/session-supervisor.sock")
    require(domain_supervisor, "Socket", "Service",
            "hepta-tool-gateway@%i.service")
    require(domain_supervisor, "Socket", "SocketUser", "hepta-gw-%i")
    require(domain_supervisor, "Socket", "SocketGroup", "hepta-gw-%i")
    require(domain_service, "Service", "EnvironmentFile",
            "/etc/heptatrader/trust-domains/%i.env")
    require(domain_service, "Service", "User", "hepta-gw-%i")
    require(domain_service, "Service", "Group", "hepta-gw-%i")
    require(domain_service, "Service", "SupplementaryGroups", "")
    require(domain_service, "Service", "Environment",
            "HEPTA_TOOL_SOCKET=/run/hepta-agent-%i/tools.sock")
    require(domain_service, "Service", "Environment",
            "HEPTA_TOOL_AGENT_ID=%i")
    require(domain_service, "Service", "LoadCredential",
            "hepta-supervisor-lease-key:/etc/heptatrader/credentials/"
            "trust-domains/%i/hepta-supervisor-lease.key")
    require(domain_service, "Service", "PrivateNetwork", "yes")
    require(domain_service, "Service", "CapabilityBoundingSet", "")
    if any(
            token in joined(domain_service)
            for token in ("PAPER-V", "LIVE_", "AF_INET")):
        raise AssertionError(
            "trust-domain Gateway template contains forbidden authority")
    require(domain_execution_service, "Service", "User", "hepta-exec-%i")
    require(domain_execution_service, "Service", "Group", "hepta-exec-%i")
    require(
        domain_execution_service, "Service", "EnvironmentFile",
        "/etc/heptatrader/trust-domains/%i.execution.env")
    require(
        domain_execution_service, "Service", "LoadCredential",
        "hepta-execution-fence:/etc/heptatrader/credentials/"
        "trust-domains/%i/hepta-execution-simulator-fence")
    require(
        domain_execution_service, "Service", "StateDirectory",
        "hepta-execution-%i")
    require(
        domain_execution_service, "Service", "Sockets",
        "hepta-execution-simulator@%i.socket "
        "hepta-execution-events-simulator@%i.socket")
    require_no_install(
        domain_execution_service, DOMAIN_EXECUTION_SERVICE.name)
    for unit, path, descriptor in (
            (domain_execution_socket,
             "/run/hepta-execution-%i/execution.sock", "execution"),
            (domain_execution_event_socket,
             "/run/hepta-execution-%i/events.sock", "events")):
        require(unit, "Socket", "ListenStream", path)
        require(unit, "Socket", "SocketUser", "hepta-gw-%i")
        require(unit, "Socket", "SocketGroup", "hepta-gw-%i")
        require(unit, "Socket", "SocketMode", "0600")
        require(unit, "Socket", "FileDescriptorName", descriptor)
        require(
            unit, "Socket", "Service",
            "hepta-execution-simulator@%i.service")
        require_no_install(unit, path)
        if "hepta-gateway" in joined(unit):
            raise AssertionError(
                "domain Execution socket must not use the shared connect group")
    if any(
            token in joined(domain_execution_service)
            for token in ("PAPER-V", "LIVE_", "AF_INET")):
        raise AssertionError(
            "trust-domain Execution template contains forbidden authority")
    require(domain_ib_service, "Service", "User", "hepta-ib-exec-%i")
    require(domain_ib_service, "Service", "Group", "hepta-ib-exec-%i")
    if "User=hepta-exec-%i" in DOMAIN_IB_SERVICE.read_text(
            encoding="utf-8", errors="strict"):
        raise AssertionError(
            "domain IB PAPER must not reuse the Simulator execution identity")
    require(
        domain_ib_service, "Service", "EnvironmentFile",
        "/etc/heptatrader/trust-domains/%i.ib-paper.env")
    require(
        domain_ib_service, "Service", "LoadCredential",
        "hepta-execution-fence:/etc/heptatrader/credentials/"
        "trust-domains/%i/hepta-execution-ib-paper-fence")
    require(
        domain_ib_service, "Service", "LoadCredential",
        "hepta-ib-paper-authorization:/etc/heptatrader/credentials/"
        "trust-domains/%i/hepta-ib-paper-authorization")
    require(
        domain_ib_service, "Service", "LoadCredential",
        "hepta-fx-cash-baseline:/etc/heptatrader/credentials/"
        "trust-domains/%i/hepta-fx-cash-baseline")
    require(
        domain_ib_service, "Service", "StateDirectory",
        "hepta-ib-execution-%i")
    require(
        domain_ib_service, "Service", "Sockets",
        "hepta-execution-ib-paper@%i.socket "
        "hepta-execution-events-ib-paper@%i.socket")
    require(
        domain_ib_service, "Unit", "Requires",
        "hepta-execution-ib-paper@%i.socket "
        "hepta-execution-events-ib-paper@%i.socket")
    require(
        domain_ib_service, "Unit", "StartLimitIntervalSec", "1800s")
    require(domain_ib_service, "Unit", "StartLimitBurst", "5")
    require(
        domain_ib_service, "Service", "RestartPreventExitStatus", "9")
    require(
        domain_ib_service, "Unit", "BindsTo",
        "hepta-ib-paper-domain-preflight@%i.service")
    require(
        domain_ib_service, "Unit", "After",
        "hepta-execution-ib-paper@%i.socket "
        "hepta-execution-events-ib-paper@%i.socket "
        "hepta-ib-paper-domain-preflight@%i.service network.target")
    require(
        domain_ib_preflight, "Service", "ExecStart",
        "/usr/libexec/hepta-ib-paper-domain-authority "
        "--guard --domain %i")
    require(
        domain_ib_preflight, "Service", "ExecStopPost",
        "/usr/libexec/hepta-ib-paper-domain-authority "
        "--finalize-stop --domain %i")
    require(
        domain_ib_preflight, "Unit", "BindsTo",
        "hepta-broker-egress-policy.service "
        "hepta-execution-ib-paper@%i.service")
    require(
        domain_ib_preflight, "Unit", "StartLimitIntervalSec", "1800s")
    require(domain_ib_preflight, "Unit", "StartLimitBurst", "5")
    require(
        domain_ib_preflight, "Unit", "Before",
        "hepta-execution-ib-paper@%i.service "
        "hepta-execution-ib-paper@%i.socket "
        "hepta-execution-events-ib-paper@%i.socket")
    require(
        domain_ib_preflight, "Unit", "PartOf",
        "hepta-execution-ib-paper@%i.service")
    require(domain_ib_preflight, "Unit", "StopWhenUnneeded", "yes")
    require(domain_ib_preflight, "Unit", "RefuseManualStart", "yes")
    require(domain_ib_preflight, "Service", "Type", "notify")
    require(domain_ib_preflight, "Service", "NotifyAccess", "main")
    require(domain_ib_preflight, "Service", "User", "root")
    require(
        domain_ib_preflight, "Service", "CapabilityBoundingSet",
        "CAP_NET_ADMIN")
    require(
        domain_ib_preflight, "Service", "RuntimeDirectory",
        "hepta/ib-paper-host-authority")
    require(domain_ib_preflight, "Service", "WatchdogSec", "15s")
    require(domain_ib_preflight, "Service", "TimeoutStopSec", "30s")
    require_no_install(domain_ib_preflight, DOMAIN_IB_PREFLIGHT.name)
    require_no_install(domain_ib_service, DOMAIN_IB_SERVICE.name)
    for unit, path, descriptor in (
            (domain_ib_socket,
             "/run/hepta-execution-%i/execution.sock", "execution"),
            (domain_ib_event_socket,
             "/run/hepta-execution-%i/events.sock", "events")):
        require(unit, "Socket", "ListenStream", path)
        require(unit, "Socket", "SocketUser", "hepta-gw-%i")
        require(unit, "Socket", "SocketGroup", "hepta-gw-%i")
        require(unit, "Socket", "SocketMode", "0600")
        require(unit, "Socket", "FileDescriptorName", descriptor)
        require(
            unit, "Socket", "Service",
            "hepta-execution-ib-paper@%i.service")
        require(
            unit, "Unit", "PartOf",
            "hepta-execution-ib-paper@%i.service")
        require(
            unit, "Unit", "BindsTo",
            "hepta-ib-paper-domain-preflight@%i.service")
        require(
            unit, "Unit", "After",
            "hepta-ib-paper-domain-preflight@%i.service")
        require(unit, "Unit", "StopWhenUnneeded", "yes")
        require(unit, "Unit", "RefuseManualStart", "yes")
        require_no_install(unit, path)
        if "hepta-gateway" in joined(unit):
            raise AssertionError(
                "domain IB PAPER socket must not use the shared connect group")
    domain_simulator_names = (
        "hepta-execution-simulator@%i.service "
        "hepta-execution-simulator@%i.socket "
        "hepta-execution-events-simulator@%i.socket "
        "hepta-execution-ib-paper.service "
        "hepta-execution-ib-paper.socket "
        "hepta-execution-events-ib-paper.socket")
    domain_paper_names = (
        "hepta-execution-ib-paper@%i.service "
        "hepta-execution-ib-paper@%i.socket "
        "hepta-execution-events-ib-paper@%i.socket")
    for unit in (domain_ib_service, domain_ib_socket, domain_ib_event_socket):
        require(unit, "Unit", "Conflicts", domain_simulator_names)
    for unit in (
            domain_execution_service, domain_execution_socket,
            domain_execution_event_socket):
        conflicts = joined(unit)
        for name in domain_paper_names.split():
            if name not in conflicts:
                raise AssertionError(
                    "domain Simulator must conflict with matching PAPER instance")

    agent_tmpfiles = tmpfiles_entries(AGENT_TMPFILES)
    if agent_tmpfiles != [
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
        raise AssertionError("Agent tmpfiles contract is not exact")

    gateway_profile: dict[str, str] = {}
    for raw_line in GATEWAY_ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key in gateway_profile:
            raise AssertionError(f"Tool Gateway profile duplicates {key}")
        gateway_profile[key] = value
    if (gateway_profile.get("HEPTA_TOOL_ALLOW_TRADE") != "0" or
            gateway_profile.get("HEPTA_TOOL_SESSION_TEMPLATES") != "watch" or
            gateway_profile.get("HEPTA_TOOL_CONTRACT_BINDINGS") !=
            "EUR.USD|EUR|CASH|IDEALPRO|USD"):
        raise AssertionError("installed Tool Gateway example must remain WATCH-only")
    if gateway_profile.get("HEPTA_EXECUTION_REMOTE_MODE") != "SIMULATOR":
        raise AssertionError("installed Tool Gateway example must use Simulator")
    identities = parse_identity_manifest(
        IDENTITY_MANIFEST.read_bytes())["identities"]
    if gateway_profile.get("HEPTA_EXECUTION_SERVICE_UID") != str(
            identities["hepta-exec"]["uid"]):
        raise AssertionError("Tool Gateway Simulator UID differs from identity manifest")
    if gateway_profile.get("HEPTA_TOOL_AGENT_UID") != str(
            identities["hepta-agent"]["uid"]):
        raise AssertionError("Tool Gateway Agent UID differs from identity manifest")
    domain_profile = {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in DOMAIN_GATEWAY_ENV_EXAMPLE.read_text(
            encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    if (domain_profile.get("HEPTA_TOOL_ALLOW_TRADE") != "0" or
            domain_profile.get("HEPTA_TOOL_SESSION_TEMPLATES") != "watch" or
            domain_profile.get("HEPTA_TOOL_CONTRACT_BINDINGS") !=
            "EUR.USD|EUR|CASH|IDEALPRO|USD"):
        raise AssertionError(
            "trust-domain Gateway profile must remain WATCH-only")

    execution_service = settings(EXECUTION_SERVICE)
    execution_socket = settings(EXECUTION_SOCKET)
    execution_event_socket = settings(EXECUTION_EVENT_SOCKET)
    for unit, descriptor, backlog in (
        (execution_socket, "execution", "16"),
        (execution_event_socket, "events", "32"),
    ):
        require(unit, "Socket", "Service", "hepta-execution-simulator.service")
        require(unit, "Socket", "Accept", "no")
        require(unit, "Socket", "FileDescriptorName", descriptor)
        require(unit, "Socket", "Backlog", backlog)
        require(unit, "Socket", "SocketUser", "hepta-gateway")
        require(unit, "Socket", "SocketGroup", "hepta-gateway")
        require(unit, "Socket", "SocketMode", "0660")
        require(unit, "Socket", "DirectoryMode", "0755")
        require(unit, "Socket", "RemoveOnStop", "yes")
    require(execution_service, "Service", "User", "hepta-exec")
    require(execution_service, "Service", "Group", "hepta-exec")
    require(execution_service, "Service", "ExecStart", "/usr/libexec/hepta-executiond")
    require(execution_service, "Service", "Sockets",
            "hepta-execution-simulator.socket hepta-execution-events-simulator.socket")
    require(execution_service, "Service", "Environment",
            "HEPTA_EXECUTION_SERVICE_MODE=SIMULATOR")
    require(execution_service, "Service", "EnvironmentFile",
            "/etc/heptatrader/hepta-execution-simulator.env")
    require(execution_service, "Service", "LoadCredential",
            "hepta-execution-fence:/etc/heptatrader/credentials/hepta-execution-simulator-fence")
    require(execution_service, "Service", "StateDirectory", "hepta-execution")
    require(execution_service, "Service", "StateDirectoryMode", "0700")
    for key, expected in {
        "UMask": "0077", "NoNewPrivileges": "yes", "PrivateDevices": "yes",
        "KillMode": "control-group",
        "PrivateNetwork": "yes", "ProtectSystem": "strict", "ProtectHome": "yes",
        "RestrictNamespaces": "yes", "RestrictSUIDSGID": "yes",
        "MemoryDenyWriteExecute": "yes", "RestrictAddressFamilies": "AF_UNIX",
        "IPAddressDeny": "any", "ReadWritePaths": "/var/lib/hepta-execution",
    }.items():
        require(execution_service, "Service", key, expected)
    if execution_service["Service"].get("User") == execution_socket["Socket"].get("SocketUser"):
        raise AssertionError("execution daemon and gateway must use different OS identities")
    if any("AF_INET" in value for value in
           execution_service["Service"].get("RestrictAddressFamilies", [])):
        raise AssertionError("Simulator execution unit must not permit broker network families")
    for forbidden in ("PAPER", "LIVE", "IB_", "CTP", "XT_", "PASSWORD=", "TOKEN="):
        if forbidden in joined(execution_service):
            raise AssertionError(f"Simulator execution unit contains forbidden surface {forbidden!r}")

    ib_service = settings(IB_SERVICE)
    ib_socket = settings(IB_SOCKET)
    ib_event_socket = settings(IB_EVENT_SOCKET)
    require(
        ib_socket, "Unit", "PartOf",
        "hepta-execution-ib-paper.service")
    require(
        ib_event_socket, "Unit", "PartOf",
        "hepta-execution-ib-paper.service")
    for unit, descriptor, pathname, backlog in (
        (ib_socket, "execution", "/run/hepta-execution/execution.sock", "16"),
        (ib_event_socket, "events", "/run/hepta-execution/events.sock", "32"),
    ):
        require(unit, "Socket", "Service", "hepta-execution-ib-paper.service")
        require(unit, "Socket", "ListenStream", pathname)
        require(unit, "Socket", "Accept", "no")
        require(unit, "Socket", "FileDescriptorName", descriptor)
        require(unit, "Socket", "Backlog", backlog)
        require(unit, "Socket", "SocketUser", "hepta-gateway")
        require(unit, "Socket", "SocketGroup", "hepta-gateway")
        require(unit, "Socket", "SocketMode", "0660")
        require(unit, "Socket", "DirectoryMode", "0755")
        require(unit, "Socket", "RemoveOnStop", "yes")
    require(ib_service, "Service", "Sockets",
            "hepta-execution-ib-paper.socket hepta-execution-events-ib-paper.socket")
    require(ib_service, "Service", "User", "hepta-ib-exec")
    require(ib_service, "Service", "Group", "hepta-ib-exec")
    require(ib_service, "Service", "ExecStart", "/usr/libexec/hepta-ib-executiond")
    require(ib_service, "Service", "Environment",
            "HEPTA_IB_PAPER_CONTROL_DIRECTORY=" + IB_CONTROL_DIRECTORY)
    require(ib_service, "Service", "EnvironmentFile",
            "/etc/heptatrader/hepta-execution-ib-paper.env")
    require(ib_service, "Service", "LoadCredential",
            "hepta-execution-fence:/etc/heptatrader/credentials/hepta-execution-ib-paper-fence")
    require(ib_service, "Service", "LoadCredential",
            "hepta-ib-paper-authorization:/etc/heptatrader/credentials/hepta-ib-paper-authorization")
    require(ib_service, "Service", "LoadCredential",
            "hepta-fx-cash-baseline:/etc/heptatrader/credentials/hepta-fx-cash-baseline")
    require(ib_service, "Service", "StateDirectory", "hepta-ib-execution")
    require(ib_service, "Service", "StateDirectoryMode", "0700")
    for key, expected in {
        "UMask": "0077", "NoNewPrivileges": "yes", "PrivateDevices": "yes",
        "KillMode": "control-group",
        "ProtectSystem": "strict", "ProtectHome": "yes", "RestrictNamespaces": "yes",
        "RestrictSUIDSGID": "yes", "MemoryDenyWriteExecute": "yes",
        "RestrictAddressFamilies": "AF_UNIX AF_INET AF_INET6",
        "IPAddressDeny": "any", "IPAddressAllow": "127.0.0.0/8",
        "ReadWritePaths": "/var/lib/hepta-ib-execution",
        "ReadOnlyPaths": IB_CONTROL_DIRECTORY,
    }.items():
        require(ib_service, "Service", key, expected)
    require(ib_service, "Service", "IPAddressAllow", "::1/128")
    if "PrivateNetwork" in ib_service.get("Service", {}):
        raise AssertionError("IB PAPER must reach only the host loopback Gateway")
    if ib_service["Service"].get("User") == ib_socket["Socket"].get("SocketUser"):
        raise AssertionError("IB daemon and gateway must use different OS identities")

    tmpfiles = tmpfiles_entries(IB_TMPFILES)
    if len(tmpfiles) != 2:
        raise AssertionError(
            "IB PAPER tmpfiles config must contain exactly the control directory "
            "and default-engaged marker entries")
    require_tmpfiles_entry(tmpfiles, [
        "d", IB_CONTROL_DIRECTORY, "0750", "root", "hepta-ib-exec", "-"])
    require_tmpfiles_entry(tmpfiles, [
        "f", IB_KILL_SWITCH_MARKER, "0440", "root", "hepta-ib-exec", "-",
        "engaged"])
    if any(entry[0] in ("f+", "w", "w+") and
           entry[1] == IB_KILL_SWITCH_MARKER for entry in tmpfiles):
        raise AssertionError(
            "tmpfiles must create-if-absent rather than overwrite the kill-switch marker")

    simulator_names = (
        "hepta-execution-simulator.service hepta-execution-simulator.socket "
        "hepta-execution-events-simulator.socket")
    paper_names = (
        "hepta-execution-ib-paper.service hepta-execution-ib-paper.socket "
        "hepta-execution-events-ib-paper.socket")
    for unit in (ib_service, ib_socket, ib_event_socket):
        require(unit, "Unit", "Conflicts", simulator_names)
    for unit in (execution_service, execution_socket, execution_event_socket):
        require(unit, "Unit", "Conflicts", paper_names)

    for path, unit in (
        (EXECUTION_SERVICE, execution_service), (EXECUTION_SOCKET, execution_socket),
        (EXECUTION_EVENT_SOCKET, execution_event_socket), (IB_SERVICE, ib_service),
        (IB_SOCKET, ib_socket), (IB_EVENT_SOCKET, ib_event_socket),
    ):
        require_no_install(unit, path.name)

    env_values: dict[str, str] = {}
    for raw_line in IB_ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key in env_values:
            raise AssertionError(
                f"IB PAPER environment example duplicates {key}")
        env_values[key] = value
    if env_values.get("HEPTA_IB_EXECUTION_MODE") != "PAPER":
        raise AssertionError("IB PAPER environment example must be PAPER-only")
    if env_values.get("HEPTA_IB_PAPER_HOST") != "127.0.0.1":
        raise AssertionError("IB PAPER environment example must use loopback")
    if env_values.get("HEPTA_IB_PAPER_PORT") != "4002":
        raise AssertionError("IB PAPER environment example must match ibgateway port 4002")

    domain_env_values: dict[str, str] = {}
    for raw_line in DOMAIN_IB_ENV_EXAMPLE.read_text(
            encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key in domain_env_values:
            raise AssertionError(
                f"domain IB PAPER environment example duplicates {key}")
        domain_env_values[key] = value
    if (
            domain_env_values.get("HEPTA_IB_EXECUTION_MODE") != "PAPER" or
            domain_env_values.get("HEPTA_IB_EXECUTION_GATEWAY_UID") != "2101" or
            domain_env_values.get("HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID") !=
            "alpha" or
            domain_env_values.get("HEPTA_IB_EXECUTION_DOMAIN_ID") !=
            "PAPER:alpha"):
        raise AssertionError(
            "domain IB PAPER environment example is not exactly bound")

    gateway_values: dict[str, str] = {}
    for raw_line in GATEWAY_PAPER_ENV_EXAMPLE.read_text(
            encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key in gateway_values:
            raise AssertionError(
                f"Gateway PAPER environment example duplicates {key}")
        gateway_values[key] = value
    expected_gateway_values = {
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
    if gateway_values != expected_gateway_values:
        raise AssertionError(
            "Gateway PAPER environment example must be the reviewed fixed profile")
    domain_gateway_values: dict[str, str] = {}
    for raw_line in DOMAIN_GATEWAY_PAPER_ENV_EXAMPLE.read_text(
            encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key in domain_gateway_values:
            raise AssertionError(
                f"domain Gateway PAPER environment example duplicates {key}")
        domain_gateway_values[key] = value
    expected_domain_gateway_values = dict(expected_gateway_values)
    expected_domain_gateway_values.update({
        "HEPTA_EXECUTION_SOCKET":
            "/run/hepta-execution-alpha/execution.sock",
        "HEPTA_EXECUTION_EVENT_SOCKET":
            "/run/hepta-execution-alpha/events.sock",
        "HEPTA_EXECUTION_SERVICE_UID": "2121",
        "HEPTA_TOOL_AGENT_ID": "alpha",
        "HEPTA_EXECUTION_DOMAIN_ID": "PAPER:alpha",
        "HEPTA_TOOL_AGENT_UID": "2104",
    })
    if domain_gateway_values != expected_domain_gateway_values:
        raise AssertionError(
            "domain Gateway PAPER environment example must be exactly bound")
    for forbidden in ("PASSWORD", "TOKEN", "SECRET", "AUTHORIZATION", "FENCE", "CREDENTIAL"):
        if any(forbidden in key.upper()
               for profile in (
                   env_values, gateway_values, domain_env_values,
                   domain_gateway_values)
               for key in profile):
            raise AssertionError(
                f"PAPER environment example contains secret field {forbidden}")
    if (gateway_values["HEPTA_TOOL_ALLOW_TRADE"] != "0" or
            gateway_values["HEPTA_TOOL_SESSION_TEMPLATES"] != "watch"):
        raise AssertionError(
            "shipped PAPER routing profile must remain WATCH-only and "
            "non-authorizing")
    require(gateway_service, "Service", "EnvironmentFile",
            "/etc/heptatrader/hepta-tool-gateway.env")


def check_systemd_verify() -> None:
    check_local_dependency_closure(SYSTEMD_VERIFY_UNIT_PATHS)
    executable = shutil.which("systemd-analyze")
    if executable is None:
        print("hepta_agent_os_systemd_verify: SKIP (systemd-analyze unavailable)")
        return
    with tempfile.TemporaryDirectory(prefix="hepta-systemd-verify-") as directory:
        temporary = Path(directory)
        copied: list[Path] = []
        for name in (
                "hepta-local-ai-paper-24h-stop.timer",
                "hepta-local-ai-paper-end-flat-retry.timer"):
            generated = temporary / name
            generated.write_text(
                "[Unit]\n"
                "Description=Generated campaign timer syntax placeholder\n"
                "[Timer]\n"
                "OnActiveSec=1h\n",
                encoding="utf-8")
            copied.append(generated)
        for source in SYSTEMD_VERIFY_UNIT_PATHS:
            text = source.read_text(encoding="utf-8")
            # Verify unit syntax independently of whether project binaries have
            # already been installed on this build host.
            text = text.replace(
                "ExecStart=/usr/libexec/hepta-local-ai-paper-agent "
                "--campaign-id ${HEPTA_LOCAL_AI_CAMPAIGN_ID} "
                "--strategy-id ${HEPTA_LOCAL_AI_STRATEGY_ID} "
                "--strategy-version ${HEPTA_LOCAL_AI_STRATEGY_VERSION} "
                "--strategy-sha256 ${HEPTA_LOCAL_AI_STRATEGY_SHA256} "
                "--auth-generation ${HEPTA_LOCAL_AI_AUTH_GENERATION} "
                "--auth-profile-id ${HEPTA_LOCAL_AI_AUTH_PROFILE_ID}",
                "ExecStart=/bin/true")
            text = text.replace(
                "ExecStart=/usr/libexec/"
                "hepta-local-paper-safe-recover-guard",
                "ExecStart=/bin/true")
            text = text.replace(
                "ExecStart=/usr/libexec/hepta-local-paper-session-renew",
                "ExecStart=/bin/true")
            text = text.replace(
                "ExecStart=/usr/libexec/hepta-local-paper-supervisor",
                "ExecStart=/bin/true")
            text = text.replace("ExecStart=/usr/libexec/hepta-executiond",
                                "ExecStart=/bin/true")
            text = text.replace("ExecStart=/usr/libexec/hepta-ib-executiond",
                                "ExecStart=/bin/true")
            text = text.replace("ExecStart=/usr/libexec/hepta-tool-gatewayd",
                                "ExecStart=/bin/true")
            text = text.replace(
                "ExecStart=/usr/libexec/hepta-ib-paper-domain-authority "
                "--guard --domain %i",
                "ExecStart=/bin/true")
            text = text.replace(
                "ExecStart=/usr/libexec/"
                "hepta-ib-paper-campaign-operator "
                "--serve-once --domain %i",
                "ExecStart=/bin/true")
            text = text.replace(
                "ExecStart=/usr/bin/python3.12 -I -S "
                "${CREDENTIALS_DIRECTORY}/"
                "hepta-broker-egress-policy.py --supervise-deny-all "
                "--paper-identities /etc/heptatrader/"
                "hepta-agent-trust-domain-paper-identities-v1.json",
                "ExecStart=/bin/true")
            text = text.replace(
                "ExecStopPost=/usr/bin/python3.12 -I -S "
                "${CREDENTIALS_DIRECTORY}/"
                "hepta-broker-egress-policy.py "
                "--tighten-deny-all",
                "ExecStopPost=/bin/true")
            text = text.replace(
                "ExecStart=/usr/bin/python3.12 -I -S "
                "${CREDENTIALS_DIRECTORY}/"
                "hepta-p1-watch-activation-transaction.py activate",
                "ExecStart=/bin/true")
            text = text.replace(
                "ExecStart=/usr/bin/python3.12 -I -S "
                "${CREDENTIALS_DIRECTORY}/"
                "hepta-p1-watch-activation-transaction.py reconcile",
                "ExecStart=/bin/true")
            text = text.replace(
                "ExecStopPost=/usr/libexec/"
                "hepta-ib-paper-domain-authority "
                "--finalize-stop --domain %i",
                "ExecStopPost=/bin/true")
            text = text.replace(
                "ExecStart=/usr/libexec/hepta-shadow-watch-collector "
                "--domain-config /etc/heptatrader/trust-domains/"
                "uid-${HEPTA_SHADOW_AGENT_UID}.json --output "
                "/var/lib/hepta-shadow-watch-%i/private/snapshot.json "
                "--instrument EUR.USD",
                "ExecStart=/bin/true")
            text = text.replace(
                "ExecStart=/usr/libexec/hepta-shadow-watch-exporter "
                "--source /var/lib/hepta-shadow-watch-%i/private/"
                "snapshot.json --destination "
                "/run/hepta-shadow-watch-export-%i/snapshot.json "
                "--agent-uid ${HEPTA_SHADOW_AGENT_UID} "
                "--agent-gid ${HEPTA_SHADOW_AGENT_GID} "
                "--reader-uid ${HEPTA_SHADOW_READER_UID} "
                "--reader-gid ${HEPTA_SHADOW_READER_GID} "
                "--lease-receipt-source /run/hepta-agent-%i/sessions/"
                "shadow-watch-lease-receipt.json "
                "--lease-receipt-destination "
                "/run/hepta-shadow-watch-export-%i/"
                "shadow-watch-lease-receipt.json "
                "--export-receipt-destination "
                "/run/hepta-shadow-watch-export-%i/"
                "shadow-watch-export-receipt.json",
                "ExecStart=/bin/true")
            text = text.replace(
                "ExecStart=/usr/libexec/hepta-shadow-watch-custodian "
                "--domain-config /etc/heptatrader/trust-domains/%i.json "
                "supervise",
                "ExecStart=/bin/true")
            text = text.replace(
                "ExecStart=/usr/libexec/hepta-shadow-watch-custodian "
                "--domain-config /etc/heptatrader/trust-domains/%i.json "
                "reconcile",
                "ExecStart=/bin/true")
            text = text.replace(
                "ExecStop=/usr/libexec/hepta-shadow-watch-custodian "
                "--domain-config /etc/heptatrader/trust-domains/%i.json "
                "close --reason service-stop",
                "ExecStop=/bin/true")
            text = text.replace(
                "ExecStopPost=/usr/libexec/hepta-shadow-watch-custodian "
                "--domain-config /etc/heptatrader/trust-domains/%i.json "
                "close --reason service-stop-post",
                "ExecStopPost=/bin/true")
            text = text.replace("User=hepta-gateway", "User=root")
            text = text.replace("Group=hepta-gateway", "Group=root")
            text = text.replace("SocketUser=hepta-gateway", "SocketUser=root")
            text = text.replace("SocketGroup=hepta-gateway", "SocketGroup=root")
            text = text.replace("SocketUser=hepta-agent", "SocketUser=root")
            text = text.replace("SocketGroup=hepta-agent", "SocketGroup=root")
            target = temporary / source.name
            target.write_text(text, encoding="utf-8")
            copied.append(target)
        environment = os.environ.copy()
        environment["SYSTEMD_UNIT_PATH"] = str(temporary) + ":"
        result = subprocess.run([executable, "verify", *map(str, copied)],
                                check=False, text=True, capture_output=True,
                                env=environment)
        if result.returncode != 0:
            raise AssertionError("systemd-analyze verify failed:\n" +
                                 result.stdout + result.stderr)
    print("hepta_agent_os_systemd_verify: PASS")


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] == "--systemd-verify-only":
        check_systemd_verify()
        return
    if len(sys.argv) != 1:
        raise SystemExit("usage: check_hepta_agent_os_units.py [--systemd-verify-only]")
    check_static_contract()
    print("hepta_agent_os_unit_check: PASS")


if __name__ == "__main__":
    main()
