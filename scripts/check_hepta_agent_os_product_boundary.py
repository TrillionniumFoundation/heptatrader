#!/usr/bin/env python3
"""Static fail-closed gate for the distributable Agent OS product boundary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import stat
from typing import Any

import check_hepta_agent_trust_domains as trust_domains
import check_hepta_broker_network_policy as broker_network


ROOT = Path(__file__).resolve().parents[1]
SOURCE_POLICY = Path("policies/heptatrader-agent-os-source-v2.json")
SYSTEMD_ALLOWLIST = {
    "systemd/hepta-agent-broker-egress-policy.conf.example",
    "systemd/hepta-agent-host-identity.conf.example",
    "systemd/hepta-agent-trust-domain-paper-identities-v1.json.example",
    "systemd/hepta-agent-trust-domain.json.example",
    "systemd/hepta-agent-trust-domain-policy-v1.json",
    "systemd/hepta-broker-egress-policy.service",
    "systemd/hepta-broker-network-policy-v1.json",
    "systemd/hepta-local-paper-authority@.service",
    "systemd/hepta-local-paper-fail-close@.service",
    "systemd/hepta-p1-paper-canary-capture.service",
    "systemd/hepta-p1-paper-canary-executor.service",
    "systemd/hepta-p1-paper-canary-finalizer.socket",
    "systemd/hepta-p1-paper-canary-finalizer@.service",
    "systemd/hepta-p1-paper-canary-root-coordinator.service",
    "systemd/hepta-p1-paper-terminal-cutoff@.service",
    "systemd/hepta-p1-paper-terminal-witness-verifier@.service",
    "systemd/hepta-p1-watch-activation.service",
    "systemd/hepta-p1-watch-activation-reconcile.service",
    "systemd/hepta-p1-watch-activation-reconcile.timer",
    "systemd/hepta-p1-safety-soak-campaign@.service",
    "systemd/hepta-p1-safety-soak-observer-worker@.service",
    "systemd/hepta-p1-safety-soak-recorder-worker@.service",
    "systemd/hepta-p1-safety-soak@.target",
    "systemd/hepta-paper-terminal-latch-committer@.service",
    "systemd/hepta-systemd-gate.apparmor",
    "systemd/hepta-ib-paper-campaign-operator@.service",
    "systemd/hepta-ib-paper-campaign-operator@.socket",
    "systemd/hepta-ib-paper-campaign-policy-v1.json.example",
    "systemd/hepta-ib-paper-campaign-policy-local-v4.json.example",
    "systemd/hepta-ib-paper-campaign-policy-p1-v5.json.example",
    "systemd/hepta-ib-paper-domain-authorizations-v1.json.example",
    "systemd/hepta-ib-paper-domain-preflight@.service",
    "systemd/hepta-execution-events-ib-paper.socket",
    "systemd/hepta-execution-events-ib-paper@.socket",
    "systemd/hepta-execution-events-simulator.socket",
    "systemd/hepta-execution-events-simulator@.socket",
    "systemd/hepta-execution-gateway-paper.env.example",
    "systemd/hepta-execution-gateway-paper-domain.env.example",
    "systemd/hepta-execution-ib-paper.env.example",
    "systemd/hepta-execution-ib-paper-domain.env.example",
    "systemd/hepta-execution-ib-paper.service",
    "systemd/hepta-execution-ib-paper.service.d/10-hepta-broker-egress-policy.conf",
    "systemd/hepta-execution-ib-paper.socket",
    "systemd/hepta-execution-ib-paper@.service",
    "systemd/hepta-execution-ib-paper@.service.d/10-hepta-broker-egress-policy.conf",
    "systemd/hepta-execution-ib-paper@.socket",
    "systemd/hepta-execution-simulator.env.example",
    "systemd/hepta-execution-simulator.service",
    "systemd/hepta-execution-simulator.socket",
    "systemd/hepta-execution-simulator@.service",
    "systemd/hepta-execution-simulator@.socket",
    "systemd/hepta-service-identities-v1.json",
    "systemd/hepta-local-ai-paper-agent.service",
    "systemd/hepta-local-ai-paper-agent.env.example",
    "systemd/hepta-local-paper-safe-recover.service",
    "systemd/hepta-local-paper-safe-recover.timer",
    "systemd/hepta-local-paper-session-renew.service",
    "systemd/hepta-local-paper-session-renew.timer",
    "systemd/hepta-local-paper-supervisor.service",
    "systemd/hepta-local-paper-supervisor.timer",
    "systemd/hepta-shadow-watch-collector@.service",
    "systemd/hepta-shadow-watch-collector@.timer",
    "systemd/hepta-shadow-watch-domain.env.example",
    "systemd/hepta-shadow-watch-export@.service",
    "systemd/hepta-shadow-watch-custodian-reconcile@.service",
    "systemd/hepta-shadow-watch-custodian-reconcile@.timer",
    "systemd/hepta-shadow-watch-custodian@.service",
    "systemd/hepta-tool-gateway.env.example",
    "systemd/hepta-tool-gateway-domain.env.example",
    "systemd/hepta-tool-gateway.service",
    "systemd/hepta-tool-gateway.service.d/10-hepta-broker-egress-policy.conf",
    "systemd/hepta-tool-gateway.socket",
    "systemd/hepta-tool-gateway@.service",
    "systemd/hepta-tool-gateway@.service.d/10-hepta-broker-egress-policy.conf",
    "systemd/hepta-tool-gateway@.socket",
    "systemd/hepta-tool-session-supervisor.socket",
    "systemd/hepta-tool-session-supervisor@.socket",
}
LEGACY_UNIT_NAME = re.compile(
    r"^(?:hepta-trader|hepta-openclaw-|ibgateway|.*scalping).*(?:\.service|\.socket)$")
PAPER_PROFILE_KEYS = {
    "HEPTA_EXECUTION_REMOTE_MODE",
    "HEPTA_EXECUTION_SOCKET",
    "HEPTA_EXECUTION_EVENT_SOCKET",
    "HEPTA_EXECUTION_SERVICE_UID",
    "HEPTA_EXECUTION_IO_TIMEOUT_MS",
    "HEPTA_EXECUTION_MAX_RESPONSE_BYTES",
    "HEPTA_TOOL_ACCOUNT",
    "HEPTA_TOOL_AGENT_ID",
    "HEPTA_EXECUTION_DOMAIN_ID",
    "HEPTA_TOOL_ALLOW_TRADE",
    "HEPTA_TOOL_SESSION_TEMPLATES",
    "HEPTA_TOOL_CONTRACT_BINDINGS",
    "HEPTA_TOOL_MAX_ORDER_QTY",
    "HEPTA_TOOL_MAX_TRADE_CALLS_PER_MIN",
    "HEPTA_TOOL_DECISION_LEASE_TTL_MS",
    "HEPTA_TOOL_AGENT_UID",
    "HEPTA_TOOL_SUPERVISOR_UID",
    "HEPTA_TOOL_SUPERVISOR_MAX_TTL_SEC",
    "HEPTA_TOOL_SERVER_WORKERS",
    "HEPTA_TOOL_SERVER_MAX_PENDING",
    "HEPTA_TOOL_SERVER_MAX_CONCURRENT_PER_OWNER",
    "HEPTA_TOOL_SERVER_MAX_PENDING_PER_OWNER",
    "HEPTA_TOOL_SERVER_INGRESS_WORKERS",
}


class ProductBoundaryError(RuntimeError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProductBoundaryError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _json(path: Path, label: str) -> dict[str, Any]:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ProductBoundaryError(f"{label} must be a regular non-symlink file")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8", errors="strict"),
            object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProductBoundaryError(f"{label} is not strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ProductBoundaryError(f"{label} root must be an object")
    return value


def _text(root: Path, relative: str) -> str:
    path = root / relative
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ProductBoundaryError(f"{relative} must be a regular non-symlink file")
    return path.read_text(encoding="utf-8", errors="strict")


def _unit(root: Path, relative: str) -> dict[str, dict[str, list[str]]]:
    sections: dict[str, dict[str, list[str]]] = {}
    section = ""
    for line_number, raw in enumerate(_text(root, relative).splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            if not section or section in sections:
                raise ProductBoundaryError(
                    f"{relative}:{line_number}: invalid or duplicate section")
            sections[section] = {}
            continue
        if not section or "=" not in line:
            raise ProductBoundaryError(
                f"{relative}:{line_number}: invalid unit directive")
        key, value = line.split("=", 1)
        sections[section].setdefault(key, []).append(value)
    return sections


def _env(text: str, label: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ProductBoundaryError(f"{label}:{line_number}: invalid environment line")
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in values:
            raise ProductBoundaryError(f"{label}:{line_number}: invalid or duplicate key")
        values[key] = value
    return values


def check_systemd_source_allowlist(root: Path) -> None:
    policy = _json(root / SOURCE_POLICY, "Agent OS source policy")
    include_prefixes = policy.get("include_prefixes")
    include_files = policy.get("include_files")
    if not isinstance(include_prefixes, list) or not isinstance(include_files, list):
        raise ProductBoundaryError("Agent OS source policy include fields are invalid")
    if "systemd/" in include_prefixes:
        raise ProductBoundaryError("systemd must be a file allowlist, not a prefix")
    selected = {
        value for value in include_files
        if isinstance(value, str) and value.startswith("systemd/")
    }
    if selected != SYSTEMD_ALLOWLIST:
        raise ProductBoundaryError("Agent OS systemd source allowlist drifted")
    if any(LEGACY_UNIT_NAME.fullmatch(Path(value).name) for value in selected):
        raise ProductBoundaryError("legacy systemd unit entered Agent OS allowlist")
    forbidden = set(policy.get("forbidden_files") or [])
    for required in {
            "systemd/hepta-trader.service",
            "systemd/hepta-openclaw-agent-trader-ai-native.service"}:
        if required not in forbidden:
            raise ProductBoundaryError(
                f"tracked legacy unit is not explicitly forbidden: {required}")


def check_discovery_v2(root: Path) -> None:
    registry = _text(root, "HeptaTrade/tools/trading_tool_registry.cpp")
    registry_header = _text(root, "HeptaTrade/tools/trading_tool_registry.h")
    native = _text(
        root, "HeptaTrade/client/native_tool_discovery_contract.h")
    native_implementation = _text(
        root, "HeptaTrade/client/native_tool_discovery_contract.cpp")
    native_client = _text(
        root, "HeptaTrade/client/native_tool_client.cpp")
    tool_host = _text(
        root, "HeptaTrade/tool_host/trading_tool_host.cpp")
    unix_server = _text(
        root, "HeptaTrade/tool_host/unix_tool_server.cpp")
    mcp = _text(root, "adapters/mcp/hepta_mcp_server.py")
    architecture = _text(
        root, "docs/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md")
    required = (
        ("registry", registry, "return 2;"),
        ("registry header", registry_header, "DiscoverySchemaVersion"),
        ("native client", native, "kSchemaVersion = 2"),
        ("native client", native_implementation,
         "DISCOVERY_DESCRIPTOR_SCHEMA_HASH_MISMATCH"),
        ("native client", native_implementation,
         "DISCOVERY_CATALOG_SCHEMA_HASH_MISMATCH"),
        ("native client", native_implementation,
         "DISCOVERY_DUPLICATE_TOOL"),
        ("native SDK", native_client, "EnsureDiscoveryCatalog("),
        ("native SDK", native_client,
         "DISCOVERY_REQUEST_SCHEMA_HASH_MISMATCH"),
        ("native SDK", native_client,
         "request.expectedSchemaHash = discoveredSchemaHash;"),
        ("Tool Host", tool_host, "IsDiscoveryToolName"),
        ("Tool Host", tool_host, "SCHEMA_HASH_REQUIRED"),
        ("Unix Tool Server", unix_server, "AuthorizeControlRequest("),
        ("MCP client", mcp, "DISCOVERY_SCHEMA_VERSION = 2"),
        ("MCP client", mcp, "descriptor schema hash mismatch"),
        ("MCP client", mcp, "catalog schema hash mismatch"),
        ("architecture", architecture, "discovery schema version `2`"),
    )
    for label, source, token in required:
        if token not in source:
            raise ProductBoundaryError(
                f"{label} does not enforce discovery schema v2: {token}")


def check_live_unreachable(root: Path) -> None:
    # LIVE enums remain available for legacy characterization, but none of the
    # production session/configuration paths may construct a LIVE label.
    production_reachability_files = (
        "HeptaTrade/tool_host/tool_gateway_session_policy.cpp",
        "HeptaTrade/tool_host/execution_gateway_runtime_config.cpp",
        "HeptaTrade/tool_host/agent_os_runtime_config.cpp",
        "systemd/hepta-tool-gateway.env.example",
        "systemd/hepta-execution-gateway-paper.env.example",
    )
    for relative in production_reachability_files:
        source = _text(root, relative)
        for forbidden in ("LIVE_CAPPED", "LIVE_REDUCE_ONLY"):
            if forbidden in source:
                raise ProductBoundaryError(
                    f"production path {relative} can name {forbidden}")
    policy_source = _text(
        root, "HeptaTrade/tool_host/tool_gateway_session_policy.cpp")
    for token in (
            'templates != "watch" && templates != "watch,paper"',
            'binding.session.environment = paper ? "PAPER" : "WATCH"'):
        if token not in policy_source:
            raise ProductBoundaryError(
                "production session environment allowlist drifted")
    bootstrap = _text(root, "scripts/hepta_agent_session_bootstrap.py")
    for forbidden in ("provision-paper", "provision-live"):
        if forbidden in bootstrap:
            raise ProductBoundaryError(
                f"root bootstrap unexpectedly exposes {forbidden}")
    for token in ('"paper_authorized": False', '"live_authorized": False'):
        if token not in bootstrap:
            raise ProductBoundaryError(
                "root bootstrap authorization result is not fail-closed")


def check_paper_staging_profile(root: Path) -> None:
    relative = "systemd/hepta-execution-gateway-paper.env.example"
    source = _text(root, relative)
    values = _env(source, relative)
    if set(values) != PAPER_PROFILE_KEYS:
        raise ProductBoundaryError("PAPER Gateway staging profile is incomplete")
    expected = {
        "HEPTA_EXECUTION_REMOTE_MODE": "PAPER",
        "HEPTA_EXECUTION_SERVICE_UID": "2003",
        "HEPTA_TOOL_ACCOUNT": "DU000000",
        "HEPTA_TOOL_AGENT_ID": "codex-agent-os-e2e",
        "HEPTA_EXECUTION_DOMAIN_ID": "PAPER",
        "HEPTA_TOOL_ALLOW_TRADE": "0",
        "HEPTA_TOOL_SESSION_TEMPLATES": "watch",
        "HEPTA_TOOL_CONTRACT_BINDINGS":
            "EUR.USD|EUR|CASH|IDEALPRO|USD",
        "HEPTA_TOOL_MAX_ORDER_QTY": "25000",
        "HEPTA_TOOL_MAX_TRADE_CALLS_PER_MIN": "2",
    }
    if any(values.get(key) != value for key, value in expected.items()):
        raise ProductBoundaryError(
            "PAPER Gateway staging profile is authorizing or drifted")
    if "/etc/heptatrader/hepta-tool-gateway.env" not in source:
        raise ProductBoundaryError(
            "PAPER Gateway profile does not name the unit-loaded target")
    gateway_unit = _text(root, "systemd/hepta-tool-gateway.service")
    if ("EnvironmentFile=/etc/heptatrader/hepta-tool-gateway.env" not in
            gateway_unit):
        raise ProductBoundaryError("Gateway unit environment target drifted")
    for key in values:
        if any(secret in key for secret in (
                "PASSWORD", "TOKEN", "SECRET", "AUTHORIZATION",
                "FENCE", "CREDENTIAL")):
            raise ProductBoundaryError(
                f"PAPER staging profile contains secret field {key}")

    domain_relative = (
        "systemd/hepta-execution-gateway-paper-domain.env.example")
    domain_source = _text(root, domain_relative)
    domain_values = _env(domain_source, domain_relative)
    if set(domain_values) != PAPER_PROFILE_KEYS:
        raise ProductBoundaryError(
            "domain PAPER Gateway staging profile is incomplete")
    domain_expected = dict(expected)
    domain_expected.update({
        "HEPTA_EXECUTION_SOCKET":
            "/run/hepta-execution-alpha/execution.sock",
        "HEPTA_EXECUTION_EVENT_SOCKET":
            "/run/hepta-execution-alpha/events.sock",
        "HEPTA_EXECUTION_SERVICE_UID": "2121",
        "HEPTA_TOOL_AGENT_ID": "alpha",
        "HEPTA_EXECUTION_DOMAIN_ID": "PAPER:alpha",
        "HEPTA_TOOL_AGENT_UID": "2104",
    })
    if any(
            domain_values.get(key) != value
            for key, value in domain_expected.items()):
        raise ProductBoundaryError(
            "domain PAPER Gateway staging profile is authorizing or drifted")
    if "/etc/heptatrader/trust-domains/alpha.env" not in domain_source:
        raise ProductBoundaryError(
            "domain PAPER Gateway profile does not name its unit target")
    for key in domain_values:
        if any(secret in key for secret in (
                "PASSWORD", "TOKEN", "SECRET", "AUTHORIZATION",
                "FENCE", "CREDENTIAL")):
            raise ProductBoundaryError(
                f"domain PAPER staging profile contains secret field {key}")


def check_plugin_trust_domain_boundary(root: Path) -> None:
    mcp = _json(
        root / "plugins/heptatrader-agent-os/.mcp.json",
        "Agent OS plugin MCP config")
    if set(mcp) != {"mcpServers"}:
        raise ProductBoundaryError(
            "Agent OS plugin MCP config fields are not exact")
    servers = mcp["mcpServers"]
    if not isinstance(servers, dict) or set(servers) != {"heptatrader"}:
        raise ProductBoundaryError(
            "Agent OS plugin MCP server map is not exact")
    server = servers["heptatrader"]
    if not isinstance(server, dict) or set(server) != {"command", "env"}:
        raise ProductBoundaryError(
            "Agent OS plugin MCP server fields are not exact")
    if server["command"] != "/usr/libexec/hepta-agent-mcp-launcher":
        raise ProductBoundaryError(
            "Agent OS plugin bypasses the fixed MCP launcher")
    if server["env"] != {}:
        raise ProductBoundaryError(
            "Agent OS plugin must not inject compatibility or domain state")

    launcher = _text(root, "scripts/hepta_agent_mcp_launcher.py")
    required = (
        'DOMAIN_CONFIG_ROOT = Path("/etc/heptatrader/trust-domains")',
        'return DOMAIN_CONFIG_ROOT / f"uid-{uid}.json"',
        'domain_config = str(default_domain_config_path(os.getuid()))',
        'if domain_config and compatibility:',
        'if not domain_config and compatibility != "1":',
        'domain = load_runtime_config(',
        'Path(domain_config),',
        'expected_agent_identity=(',
        'process_identity[0], process_identity[2])',
        'supplementary groups are forbidden',
        'elif compatibility == "1":',
    )
    for token in required:
        if token not in launcher:
            raise ProductBoundaryError(
                "Agent OS launcher no longer resolves a protected per-UID "
                f"trust-domain config: {token}")
    if (
            "require_root_metadata=False" in launcher or
            'os.environ.get("HEPTA_AGENT_SINGLE_DOMAIN_COMPAT", "1")'
            in launcher):
        raise ProductBoundaryError(
            "Agent OS launcher weakens trust-domain metadata or defaults "
            "to compatibility mode")


def check_local_paper_manual_start_boundary(root: Path) -> None:
    agent = _text(root, "scripts/hepta_local_ai_paper_agent.py")
    repair = _text(root, "scripts/run_paper_repair.py")
    recovery = _text(root, "scripts/run_paper_safe_recover.py")
    guard = _text(root, "scripts/run_paper_safe_recover_guard.py")
    runbook = _text(root, "docs/RUNBOOK-STARTUP.md")
    required = (
        ("agent auth profile pin", agent,
         "_verify_effective_auth_profile(arguments)"),
        ("agent auth profile latch", agent,
         '"auth_profile_sha256_rearmed"'),
        ("agent auth profile allowlist CLI", agent,
         '"--auth-profile-allowlist-sha256"'),
        ("agent auth profile allowlist latch", agent,
         '"auth_profile_allowlist_sha256_rearmed"'),
        ("agent", agent, 'state.get("manual_start_required") is True'),
        ("agent", agent, '"AUTH_REARM_MANUAL_START_CONSUMED"'),
        ("agent broker mutation lock", agent,
         'BROKER_MUTATION_LOCK = Path('),
        ("agent broker mutation lock", agent,
         "with _broker_mutation_lock():"),
        ("auth rearm", repair,
         'rearmed_state["manual_start_required"] = True'),
        ("auth rearm profile pin", repair,
         "_verify_effective_auth_order(profile_id)"),
        ("auth rearm order-independent allowlist", repair,
         "_auth_profile_allowlist_sha256"),
        ("auth rearm trajectory binding", repair,
         'started_data.get("authProfileId")'),
        ("auth rearm profile latch", repair,
         'rearmed_state["auth_profile_sha256_rearmed"]'),
        ("auth rearm profile allowlist latch", repair,
         'rearmed_state["auth_profile_allowlist_sha256_rearmed"]'),
        ("auth rearm profile allowlist env", repair,
         '"HEPTA_LOCAL_AI_AUTH_PROFILE_ALLOWLIST_SHA256"'),
        ("auth rearm receipt", repair,
         '"manual_start_required": True'),
        ("session rematerialization", repair,
         "def _ensure_active_session_materialized("),
        ("renew broker mutation exclusion", repair,
         "with _broker_mutation_lock(blocking=False) as mutation_lock:"),
        ("normal recovery", recovery,
         "require_no_manual_rearm_start()"),
        ("normal recovery", recovery,
         'raise Deferred("AUTH_REARM_MANUAL_START_REQUIRED")'),
        ("recovery guard", guard,
         '"SAFE_RECOVERY_BLOCKED manual_start_required=true "'),
        ("runbook active-v4 quarantine", runbook,
         "active-v4 五步启动链"),
        ("runbook prepare quarantine", runbook,
         "REPAIR_P1_ADMISSION_REQUIRED"),
        ("runbook operator quarantine", runbook,
         "CAMPAIGN_POLICY_V4_ACTIVE_P1_ADMISSION_REQUIRED"),
        ("runbook WAL rollback", runbook,
         "rollback 完成前不得删除 WAL"),
        ("runbook LIVE prohibition", runbook,
         "任何 LIVE 标志仍永久拒绝"),
    )
    for label, source, token in required:
        if token not in source:
            raise ProductBoundaryError(
                f"local PAPER {label} manual-start boundary drifted: "
                f"{token}")
    agent_main = agent[agent.index("def main() -> int:"):]
    if agent_main.index('state.get("manual_start_required") is True') > \
            agent_main.index("while True:"):
        raise ProductBoundaryError(
            "local PAPER agent consumes manual-start marker after entering "
            "the market loop")
    recovery_cycle = recovery[
        recovery.index("def recover_once() -> int:"):
        recovery.index("def main() -> int:")]
    if "require_no_manual_rearm_start()" not in recovery_cycle:
        raise ProductBoundaryError(
            "normal recovery does not check the manual-start marker")
    # Recurring recovery must never start the agent at all. The explicit
    # operator start after auth-rearm is the only allowed consumer of the
    # manual-start marker.
    if ('"/usr/bin/systemctl", "start", CAMPAIGN_SOCKET, '
            'AGENT_SERVICE') in recovery_cycle or \
            '"/usr/bin/systemctl", "start", AGENT_SERVICE' in recovery_cycle:
        raise ProductBoundaryError(
            "normal recovery can start the agent")
    if "systemctl start hepta-local-ai-paper-agent.service" in runbook:
        raise ProductBoundaryError(
            "runbook bypasses the fail-closed start-campaign transaction")


def check_local_paper_unit_boundary(root: Path) -> None:
    agent = _unit(root, "systemd/hepta-local-ai-paper-agent.service")
    renew = _unit(root, "systemd/hepta-local-paper-session-renew.service")
    renew_timer = _unit(
        root, "systemd/hepta-local-paper-session-renew.timer")
    expected_requisites = (
        "hepta-tool-gateway@alpha.service "
        "hepta-execution-ib-paper@alpha.service "
        "hepta-ib-paper-campaign-operator@alpha.socket "
        "hepta-local-paper-safe-recover.timer "
        "hepta-local-paper-session-renew.timer "
        "hepta-local-paper-supervisor.timer "
        "hepta-local-ai-paper-24h-stop.timer "
        "hepta-local-ai-paper-end-flat-retry.timer")
    agent_unit = agent.get("Unit", {})
    agent_service = agent.get("Service", {})
    if (
            "Install" in agent or
            agent_unit.get("Requisite") != [expected_requisites] or
            "Requires" in agent_unit or
            agent_service.get("ExecCondition") != [
                "/usr/libexec/hepta-local-paper-repair pre-start-guard"] or
            agent_service.get("Restart") != ["no"] or
            "RestartPreventExitStatus" in agent_service or
            agent_service.get("InaccessiblePaths") != [
                "/var/lib/hepta-local-ai-paper-agent/session-authority"] or
            agent_service.get("CapabilityBoundingSet") != [
                "CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER CAP_KILL "
                "CAP_SETGID CAP_SETUID"] or
            agent_service.get("AmbientCapabilities") != [""] or
            agent_service.get("RestrictNamespaces") != ["yes"] or
            agent_service.get("SystemCallFilter") != ["~@mount"]):
        raise ProductBoundaryError(
            "local PAPER agent static start-authority unit boundary drifted")
    renew_unit = renew.get("Unit", {})
    renew_service = renew.get("Service", {})
    renew_timer_contract = renew_timer.get("Timer", {})
    if (
            renew_unit.get("OnFailure") != [
                "hepta-local-paper-safe-recover.service"] or
            renew_service.get("ReadWritePaths") != [
                "/var/lib/hepta-local-ai-paper-agent "
                "-/run/hepta-agent-alpha"] or
            renew_timer_contract.get("OnBootSec") != ["60s"] or
            renew_timer_contract.get("OnUnitInactiveSec") != ["1h"] or
            renew_timer_contract.get("AccuracySec") != ["5s"] or
            renew_timer_contract.get("RandomizedDelaySec") != ["0"] or
            renew_timer_contract.get("Unit") != [
                "hepta-local-paper-session-renew.service"]):
        raise ProductBoundaryError(
            "local PAPER session-renew recovery unit boundary drifted")


def validate(root: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    check_systemd_source_allowlist(root)
    try:
        broker_network.check(root)
    except (
            broker_network.CheckFailure,
            broker_network.POLICY_HELPER.PolicyError,
            OSError, UnicodeDecodeError, ValueError) as error:
        raise ProductBoundaryError(
            f"broker network boundary is invalid: {error}") from error
    check_discovery_v2(root)
    check_live_unreachable(root)
    check_paper_staging_profile(root)
    check_plugin_trust_domain_boundary(root)
    check_local_paper_manual_start_boundary(root)
    check_local_paper_unit_boundary(root)
    trust_result = trust_domains.validate(
        root / "systemd/hepta-agent-trust-domain-policy-v1.json",
        root / "tests/fixtures/hepta-agent-trust-domains-v1.json",
        root / "systemd/hepta-service-identities-v1.json")
    return {
        "passed": True,
        "systemd_allowlist_count": len(SYSTEMD_ALLOWLIST),
        "discovery_schema_version": 2,
        "trust_domain_count": trust_result["domain_count"],
        "paper_authorized": False,
        "live_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    arguments = parser.parse_args()
    result = validate(arguments.root)
    print(
        "hepta_agent_os_product_boundary: PASS "
        f"systemd_allowlist={result['systemd_allowlist_count']} "
        f"discovery_schema={result['discovery_schema_version']} "
        f"trust_domains={result['trust_domain_count']} "
        "paper_authorized=false live_authorized=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
