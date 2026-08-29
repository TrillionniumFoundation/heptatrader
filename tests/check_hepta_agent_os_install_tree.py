#!/usr/bin/env python3

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile


COMPONENT = "hepta-agent-os-runtime"
PAPER_IDENTITY_SOURCE = (
    "usr/share/doc/heptatrader/examples/"
    "hepta-agent-trust-domain-paper-identities-v1.json.example")
PAPER_IDENTITY_SOURCE_SHA256 = (
    "4a94d555cad61a9de67b809cfae301eadd6ebf2511714c93343f10decb34e435")
RELEASE_VALIDATION_COMPANION_NAMES = (
    "aggregate_hepta_execution_native_systemd_gate",
    "build_hepta_execution_native_vm_bundle",
    "build_heptatrader_delivery_closure",
    "build_heptatrader_engineering_closure",
    "build_heptatrader_evidence_index",
    "build_heptatrader_evidence_ingestion_request",
    "build_heptatrader_release_validation_closure",
    "build_heptatrader_verification_evidence",
    "check_hepta_agent_os_provisioned_host",
    "converge_ctp_vendor_headers",
    "hepta_service_identities",
    "heptatrader_secure_artifacts",
    "run_execution_gateway_soak",
    "run_hepta_execution_native_systemd_gate",
    "run_hepta_execution_rootful_systemd_gate",
    "verify_hepta_execution_native_vm_bundle",
    "verify_heptatrader_agent_os_source_bundle",
    "verify_heptatrader_clean_source_bundle",
    "verify_heptatrader_delivery_closure",
    "verify_heptatrader_engineering_closure",
    "verify_heptatrader_evidence_index",
    "verify_heptatrader_evidence_ingestion_receipt",
    "verify_heptatrader_evidence_set",
    "verify_heptatrader_prebuilt_assets",
    "verify_heptatrader_release_validation_closure",
    "verify_heptatrader_runtime_package",
    "verify_heptatrader_vendor_assets",
)
RELEASE_VALIDATION_COMPANION_FILES = {
    f"usr/libexec/{name}.py": 0o644
    for name in RELEASE_VALIDATION_COMPANION_NAMES
}
RELEASE_VALIDATION_PACKAGE_FILES = {
    "usr/libexec/hepta_ops/__init__.py": 0o644,
    "usr/libexec/hepta_ops/agent_os_source.py": 0o644,
    "usr/libexec/hepta_ops/registry.py": 0o644,
}
FILES = {
    "usr/bin/hepta-campaignctl": 0o755,
    "usr/bin/heptactl": 0o755,
    "usr/bin/hepta-sessionctl": 0o755,
    "usr/libexec/hepta-paper-receipt-contracts": 0o755,
    "usr/libexec/hepta-paper-receipt-contracts-v2-compat": 0o755,
    "usr/libexec/hepta-p1-paper-canary-backend-adapter": 0o755,
    "usr/libexec/hepta-p1-paper-canary-crash-emergency-closer": 0o755,
    "usr/libexec/hepta-p1-paper-canary-executor": 0o755,
    "usr/libexec/hepta-p1-paper-canary-handoff-producer": 0o755,
    "usr/libexec/hepta-p1-paper-canary-launch-joiner": 0o755,
    "usr/libexec/hepta-p1-paper-canary-owner-provisioner": 0o755,
    "usr/libexec/hepta-p1-paper-canary-root-coordinator": 0o755,
    "usr/libexec/hepta-p1-paper-canary-terminal-prover": 0o755,
    "usr/libexec/hepta-tool-gatewayd": 0o755,
    "usr/libexec/hepta-mcp-server": 0o755,
    "usr/libexec/hepta-agent-mcp-launcher": 0o755,
    "usr/libexec/hepta-agent-session-bootstrap": 0o755,
    "usr/libexec/hepta-shadow-watch-custodian": 0o755,
    "usr/libexec/hepta_agent_trust_domain.py": 0o755,
    "usr/libexec/hepta-shadow-watch-collector": 0o755,
    "usr/libexec/hepta-shadow-watch-exporter": 0o755,
    "usr/libexec/hepta-shadow-host-installer": 0o755,
    "usr/libexec/hepta-p1-watch-profile-deployer": 0o755,
    "usr/libexec/hepta-p1-watch-activation-transaction": 0o755,
    "usr/libexec/hepta-p1-shadow-host-controller": 0o755,
    "usr/libexec/hepta-p1-load-probe-validator": 0o755,
    "usr/libexec/build-hepta-p1-observation-policy": 0o755,
    "usr/libexec/hepta-p1-shadow-observer-controller": 0o755,
    "usr/libexec/hepta-p1-shadow-admission-launcher": 0o755,
    "usr/libexec/hepta-p1-safety-soak-campaign-freezer": 0o755,
    "usr/libexec/hepta-p1-safety-soak-policy-planner": 0o755,
    "usr/libexec/hepta-p1-safety-soak-campaign-coordinator": 0o755,
    "usr/libexec/hepta-p1-safety-soak-observer-worker": 0o755,
    "usr/libexec/hepta-p1-safety-soak-recorder-worker": 0o755,
    "usr/libexec/hepta-p1-safety-soak-fault-pin-producer": 0o755,
    "usr/libexec/hepta-p1-safety-soak-evidence-recorder": 0o755,
    "usr/libexec/hepta-p1-safety-soak-independent-observer": 0o755,
    "usr/libexec/hepta-p1-safety-soak-root-fault-injector": 0o755,
    "usr/libexec/hepta-p1-safety-soak-auditor": 0o755,
    "usr/libexec/hepta-p1-watch-to-paper-handoff": 0o755,
    "usr/libexec/hepta-p1-paper-kill-switch-bootstrap": 0o755,
    "usr/libexec/hepta-p1-paper-admission-verifier": 0o755,
    "usr/libexec/hepta-p1-paper-zero-exposure-attestor": 0o755,
    "usr/libexec/hepta-p1-paper-zero-exposure-snapshot-producer": 0o755,
    "usr/libexec/hepta-p1-paper-terminal-witness-verifier": 0o755,
    "usr/libexec/hepta-rootful-review-closure-consumer": 0o755,
    "usr/libexec/hepta-rootful-systemd-environment-provenance": 0o755,
    "usr/libexec/hepta_rootful_review_closure_consumer.py": 0o644,
    "usr/libexec/hepta-release-validation-closure-verifier": 0o755,
    **RELEASE_VALIDATION_COMPANION_FILES,
    **RELEASE_VALIDATION_PACKAGE_FILES,
    "usr/libexec/hepta-bounded-shadow-closure-verifier": 0o755,
    "usr/libexec/hepta-official-source-capture": 0o755,
    "usr/libexec/hepta_bounded_shadow_observer.py": 0o755,
    "usr/libexec/hepta_market_context_builder.py": 0o755,
    "usr/libexec/hepta_market_evidence_normalizer.py": 0o755,
    "usr/libexec/hepta_market_official_source_extractor.py": 0o755,
    "usr/libexec/hepta_eurusd_confirmed_momentum_strategy.py": 0o755,
    "usr/libexec/hepta_shadow_market_history.py": 0o755,
    "usr/libexec/hepta_strategy_replay_evaluator.py": 0o755,
    "usr/libexec/hepta_strategy_shadow_runner.py": 0o755,
    "usr/libexec/validate_hepta_strategy_decision_receipt.py": 0o755,
    "usr/libexec/hepta_strategy_contracts.py": 0o644,
    "usr/libexec/hepta-broker-egress-policy": 0o755,
    "usr/libexec/hepta-local-paper-control": 0o755,
    "usr/libexec/check-hepta-agent-os-provisioned-host": 0o755,
    "usr/lib/systemd/system/hepta-broker-egress-policy.service": 0o644,
    "usr/lib/systemd/system/hepta-p1-watch-activation.service": 0o644,
    "usr/lib/systemd/system/hepta-p1-watch-activation-reconcile.service": 0o644,
    "usr/lib/systemd/system/hepta-p1-watch-activation-reconcile.timer": 0o644,
    "usr/lib/systemd/system/hepta-p1-paper-canary-capture.service": 0o644,
    "usr/lib/systemd/system/hepta-p1-paper-canary-executor.service": 0o644,
    "usr/lib/systemd/system/hepta-p1-paper-canary-root-coordinator.service":
        0o644,
    "usr/lib/systemd/system/hepta-local-paper-fail-close@.service": 0o644,
    "usr/lib/systemd/system/hepta-p1-paper-terminal-cutoff@.service": 0o644,
    "usr/lib/systemd/system/hepta-p1-paper-terminal-witness-verifier@.service":
        0o644,
    "usr/lib/systemd/system/hepta-p1-safety-soak-campaign@.service": 0o644,
    "usr/lib/systemd/system/hepta-p1-safety-soak-observer-worker@.service": 0o644,
    "usr/lib/systemd/system/hepta-p1-safety-soak-recorder-worker@.service": 0o644,
    "usr/lib/systemd/system/hepta-p1-safety-soak@.target": 0o644,
    "usr/lib/systemd/system/hepta-tool-gateway.socket": 0o644,
    "usr/lib/systemd/system/hepta-tool-gateway.service": 0o644,
    "usr/lib/systemd/system/hepta-tool-session-supervisor.socket": 0o644,
    "usr/lib/systemd/system/hepta-tool-gateway@.socket": 0o644,
    "usr/lib/systemd/system/hepta-tool-gateway@.service": 0o644,
    "usr/lib/systemd/system/hepta-tool-gateway.service.d/10-hepta-broker-egress-policy.conf": 0o644,
    "usr/lib/systemd/system/hepta-tool-gateway@.service.d/10-hepta-broker-egress-policy.conf": 0o644,
    "usr/lib/systemd/system/hepta-tool-session-supervisor@.socket": 0o644,
    "usr/lib/systemd/system/hepta-shadow-watch-collector@.service": 0o644,
    "usr/lib/systemd/system/hepta-shadow-watch-export@.service": 0o644,
    "usr/lib/systemd/system/hepta-shadow-watch-collector@.timer": 0o644,
    "usr/lib/systemd/system/hepta-shadow-watch-custodian@.service": 0o644,
    "usr/lib/systemd/system/hepta-shadow-watch-custodian-reconcile@.service": 0o644,
    "usr/lib/systemd/system/hepta-shadow-watch-custodian-reconcile@.timer": 0o644,
    "usr/lib/tmpfiles.d/heptatrader-agent-os.conf": 0o644,
    "usr/share/doc/heptatrader/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md": 0o644,
    "usr/share/doc/heptatrader/BROKER-NETWORK-ISOLATION.md": 0o644,
    "usr/share/doc/heptatrader/EURUSD-CONFIRMED-MOMENTUM-SHADOW-V2.md": 0o644,
    "usr/share/doc/heptatrader/RUNBOOK-STARTUP.md": 0o644,
    "usr/share/doc/heptatrader/examples/hepta-tool-gateway.env.example": 0o644,
    "usr/share/doc/heptatrader/examples/hepta-tool-gateway-domain.env.example": 0o644,
    "usr/share/doc/heptatrader/examples/hepta-agent-trust-domain.json.example": 0o644,
    "usr/share/doc/heptatrader/examples/hepta-agent-trust-domain-policy-v1.json": 0o644,
    "usr/share/doc/heptatrader/examples/hepta-agent-host-identity.conf.example": 0o644,
    "usr/share/doc/heptatrader/examples/hepta-agent-broker-egress-policy.conf.example": 0o644,
    "usr/share/doc/heptatrader/examples/hepta-agent-trust-domain-paper-identities-v1.json.example": 0o644,
    "usr/share/doc/heptatrader/examples/hepta-shadow-watch-domain.env.example": 0o644,
    "usr/share/heptatrader/plugins/heptatrader-agent-os/.codex-plugin/plugin.json": 0o644,
    "usr/share/heptatrader/plugins/heptatrader-agent-os/.mcp.json": 0o644,
    "usr/share/heptatrader/plugins/heptatrader-agent-os/README.md": 0o644,
    "usr/share/heptatrader/.agents/plugins/marketplace.json": 0o644,
    "usr/share/heptatrader/hepta-service-identities-v1.json": 0o644,
    "usr/share/heptatrader/hepta-broker-network-policy-v1.json": 0o644,
    "usr/share/heptatrader/systemd/hepta-systemd-gate.apparmor": 0o644,
    "usr/share/heptatrader/strategies/eurusd-confirmed-momentum-shadow-v2.json": 0o644,
}


def reject_duplicate_json_keys(
        pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_non_finite_json_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON number: {value}")


def strict_json_loads(value: str, label: str) -> object:
    try:
        return json.loads(
            value,
            object_pairs_hook=reject_duplicate_json_keys,
            parse_constant=reject_non_finite_json_constant)
    except (json.JSONDecodeError, ValueError) as error:
        raise AssertionError(f"{label}: invalid JSON: {error}") from error


def parent_directories(paths: set[str]) -> set[str]:
    result: set[str] = set()
    for name in paths:
        parent = Path(name).parent
        while parent != Path("."):
            result.add(parent.as_posix())
            parent = parent.parent
    return result


def parse_environment(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
            path.read_text(encoding="utf-8", errors="strict").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise AssertionError(f"{path}:{line_number}: invalid assignment")
        key, value = line.split("=", 1)
        if not key or key in values:
            raise AssertionError(f"{path}:{line_number}: invalid or duplicate key")
        values[key] = value
    return values


def verify(root: Path) -> None:
    expected_files = set(FILES)
    expected_entries = expected_files | parent_directories(expected_files)
    actual_paths = list(root.rglob("*"))
    actual_entries = {path.relative_to(root).as_posix() for path in actual_paths}
    if actual_entries != expected_entries:
        raise AssertionError(
            f"agent OS install allowlist mismatch missing="
            f"{sorted(expected_entries - actual_entries)} unexpected="
            f"{sorted(actual_entries - expected_entries)}")
    for path in actual_paths:
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise AssertionError(f"install tree contains symlink {relative}")
        mode = stat.S_IMODE(metadata.st_mode)
        if mode & 0o022:
            raise AssertionError(f"install tree entry is writable by group/world: {relative}")
        if relative in FILES:
            if not stat.S_ISREG(metadata.st_mode) or mode != FILES[relative]:
                raise AssertionError(f"invalid installed file {relative} mode={mode:04o}")
        elif not stat.S_ISDIR(metadata.st_mode):
            raise AssertionError(f"unexpected non-directory {relative}")

    service = (root / "usr/lib/systemd/system/hepta-tool-gateway.service").read_text(
        encoding="utf-8", errors="strict")
    tool_socket = (root / "usr/lib/systemd/system/hepta-tool-gateway.socket").read_text(
        encoding="utf-8", errors="strict")
    supervisor = (
        root / "usr/lib/systemd/system/hepta-tool-session-supervisor.socket").read_text(
            encoding="utf-8", errors="strict")
    for required in (
        "ExecStart=/usr/libexec/hepta-tool-gatewayd",
        "RestrictAddressFamilies=AF_UNIX",
        "PrivateNetwork=yes",
        "NoNewPrivileges=yes",
        "CapabilityBoundingSet=",
        "EnvironmentFile=/etc/heptatrader/hepta-tool-gateway.env",
        "Sockets=hepta-tool-gateway.socket hepta-tool-session-supervisor.socket",
        "Requires=hepta-tool-gateway.socket hepta-tool-session-supervisor.socket",
        "After=hepta-tool-gateway.socket hepta-tool-session-supervisor.socket",
        "RuntimeDirectory=hepta-tool-gateway",
        "RuntimeDirectoryMode=0700",
        "RuntimeDirectoryPreserve=yes",
        "Restart=on-failure",
    ):
        if required not in service:
            raise AssertionError(f"gateway service misses {required}")
    for required in (
        "ListenStream=/run/hepta-tool-gateway/session-supervisor.sock",
        "DirectoryMode=0700",
        "SocketUser=hepta-gateway",
        "SocketGroup=hepta-gateway",
        "SocketMode=0600",
        "Service=hepta-tool-gateway.service",
        "RemoveOnStop=yes",
    ):
        if required not in supervisor:
            raise AssertionError(
                f"supervisor reactivation contract misses {required}")
    for required in (
        "SocketUser=hepta-agent", "SocketGroup=hepta-agent", "SocketMode=0600",
        "DirectoryMode=0711", "ListenStream=/run/hepta-agent/tools.sock",
        "FileDescriptorName=hepta-tool", "Service=hepta-tool-gateway.service",
        "RemoveOnStop=yes",
    ):
        if required not in tool_socket:
            raise AssertionError(f"Agent tool socket misses {required}")

    mcp = strict_json_loads((
        root / "usr/share/heptatrader/plugins/heptatrader-agent-os/"
        ".mcp.json").read_text(encoding="utf-8", errors="strict"),
        "plugin MCP config")
    if set(mcp) != {"mcpServers"}:
        raise AssertionError("plugin MCP config is not an exact Codex wrapper")
    servers = mcp["mcpServers"]
    if not isinstance(servers, dict) or set(servers) != {"heptatrader"}:
        raise AssertionError("plugin MCP config has an unexpected server map")
    server = servers["heptatrader"]
    if server.get("command") != "/usr/libexec/hepta-agent-mcp-launcher":
        raise AssertionError("plugin MCP command is not the fixed-identity launcher")
    if server.get("env") != {}:
        raise AssertionError("plugin MCP config contains unexpected environment fields")

    plugin = strict_json_loads((
        root / "usr/share/heptatrader/plugins/heptatrader-agent-os/"
        ".codex-plugin/plugin.json").read_text(
            encoding="utf-8", errors="strict"), "plugin manifest")
    if "skills" in plugin:
        skills = (
            root / "usr/share/heptatrader/plugins/heptatrader-agent-os" /
            plugin["skills"])
        if not skills.is_dir():
            raise AssertionError("plugin manifest contains a dangling skills path")
    if plugin.get("mcpServers") != "./.mcp.json":
        raise AssertionError("plugin manifest does not bind the installed MCP config")
    if plugin.get("name") != "heptatrader-agent-os":
        raise AssertionError("installed plugin manifest has the wrong identity")
    prompts = plugin.get("interface", {}).get("defaultPrompt")
    if (not isinstance(prompts, list) or not 1 <= len(prompts) <= 3 or
            any(not isinstance(prompt, str) or not prompt or len(prompt) > 128
                for prompt in prompts)):
        raise AssertionError("installed plugin default prompts violate Codex schema")
    marketplace = strict_json_loads((
        root / "usr/share/heptatrader/.agents/plugins/marketplace.json").read_text(
            encoding="utf-8", errors="strict"), "Codex marketplace")
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
        raise AssertionError("installed Codex marketplace contract is not exact")
    marketplace_plugin = (
        root / "usr/share/heptatrader/plugins/heptatrader-agent-os")
    if (not marketplace_plugin.is_dir() or marketplace_plugin.is_symlink()):
        raise AssertionError("installed marketplace source is unavailable")

    tmpfiles = (
        root / "usr/lib/tmpfiles.d/heptatrader-agent-os.conf").read_text(
            encoding="utf-8", errors="strict")
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
        raise AssertionError("installed Agent tmpfiles contract is not exact")

    bootstrap = (
        root / "usr/libexec/hepta-agent-session-bootstrap").read_text(
            encoding="utf-8", errors="strict")
    for required in (
        'AGENT_UID = 2004',
        'RUNTIME_PARENT = Path("/run/hepta-agent")',
        'SUPERVISOR_SOCKET = "/run/hepta-tool-gateway/session-supervisor.sock"',
        '"provision", "--template", "watch"',
        '"paper_authorized": False',
        '"live_authorized": False',
    ):
        if required not in bootstrap:
            raise AssertionError(f"Agent bootstrap misses fixed contract {required}")
    for forbidden in ("--template paper", "PAPER-V", "placeOrder", "ReqOrderInsert"):
        if forbidden in bootstrap:
            raise AssertionError(
                f"Agent bootstrap contains forbidden authority {forbidden}")

    launcher = (
        root / "usr/libexec/hepta-agent-mcp-launcher").read_text(
            encoding="utf-8", errors="strict")
    for required in (
        "AGENT_UID = 2004", "AGENT_GID = 2004",
        'MCP_SERVER = "/usr/libexec/hepta-mcp-server"',
        'DOMAIN_CONFIG_ROOT = Path("/etc/heptatrader/trust-domains")',
        'f"uid-{uid}.json"',
        '"HEPTA_AGENT_DOMAIN_CONFIG"',
        '"HEPTA_AGENT_SINGLE_DOMAIN_COMPAT"',
        '"HEPTA_TOOL_SOCKET": socket_path',
        '"HEPTA_TOOL_EXPECTED_UID": str(agent_uid)',
        "os.execve(MCP_SERVER",
    ):
        if required not in launcher:
            raise AssertionError(f"Agent MCP launcher misses {required}")
    for forbidden in ("setuid(", "setgid(", "sudo", "placeOrder", "ReqOrderInsert"):
        if forbidden in launcher:
            raise AssertionError(
                f"Agent MCP launcher contains forbidden surface {forbidden}")

    campaign_client = (
        root / "usr/bin/hepta-campaignctl").read_text(
            encoding="utf-8", errors="strict")
    for required in (
            'REQUEST_SCHEMA = "hepta.ib-paper-campaign-request.v1"',
            'RESPONSE_SCHEMA = "hepta.ib-paper-campaign-response.v1"',
            'f"/run/hepta-agent-{arguments.domain}/campaign.sock"',
            '"intent_sha256": _sha256(canonical_intent)',
            '"preflight_sha256": _sha256(preflight_raw)'):
        if required not in campaign_client:
            raise AssertionError(
                f"campaign client misses fixed boundary {required}")
    for forbidden in (
            "EClientSocket", "placeOrder(", "ReqOrderInsert", "sudo",
            "live_authorized\": True"):
        if forbidden in campaign_client:
            raise AssertionError(
                f"campaign client contains forbidden surface {forbidden}")

    receipt_validator = (
        root / "usr/libexec/hepta-paper-receipt-contracts").read_text(
            encoding="utf-8", errors="strict")
    for required in (
            'BINDINGS_SCHEMA = "hepta.paper-receipt-bindings.v3"',
            'EVIDENCE_BINDINGS_SCHEMA = '
            '"hepta.paper-receipt-evidence-bindings.v3"',
            'DECISION_SCHEMA = "hepta.paper-decision-receipt.v3"',
            'CYCLE_SCHEMA = "hepta.paper-cycle-receipt.v3"',
            '"--expected-evidence-bindings"',
            "object_pairs_hook=_unique_object",
            "parse_constant=_reject_constant",
            "parse_float=_reject_float",
            '"authority_granted": False'):
        if required not in receipt_validator:
            raise AssertionError(
                f"PAPER receipt validator misses {required}")
    for forbidden in (
            "placeOrder(", "ReqOrderInsert", "EClientSocket", "sudo",
            '"authority_granted": True'):
        if forbidden in receipt_validator:
            raise AssertionError(
                "PAPER receipt validator contains forbidden authority "
                f"surface {forbidden}")

    receipt_validator_v2 = (
        root / "usr/libexec/hepta-paper-receipt-contracts-v2-compat"
    ).read_bytes()
    if hashlib.sha256(receipt_validator_v2).hexdigest() != (
            "944757976e1a86c2a39f4b800f7987d3b0382e086d90d90b5f3ba6d204692817"):
        raise AssertionError(
            "historical PAPER v2 compatibility validator bytes drifted")

    canary_executor = (
        root / "usr/libexec/hepta-p1-paper-canary-executor"
    ).read_text(encoding="utf-8", errors="strict")
    for required in (
            'HANDOFF_SCHEMA = "hepta.p1-paper-canary-execution-handoff.v1"',
            'RECOVERY_SCHEMA = "hepta.p1-paper-canary-recovery-record.v1"',
            'HISTORICAL_V2_BLOB = "b854aa90eab1cabe8742c99d09253bd337c09613"',
            'class InjectedBackend(Protocol)',
            '"trade.place_order", "MUTATION", "PLACE"',
            '"campaign.close_cycle", "CONTROL", "CLOSE"',
            '"authority_granted": False'):
        if required not in canary_executor:
            raise AssertionError(
                f"external-P1 PAPER canary executor misses {required}")
    for forbidden in (
            "placeOrder(", "ReqOrderInsert", "EClientSocket", "sudo",
            '"authority_granted": True'):
        if forbidden in canary_executor:
            raise AssertionError(
                "external-P1 PAPER canary executor contains forbidden "
                f"authority surface {forbidden}")

    preflight = (
        root / "usr/libexec/check-hepta-agent-os-provisioned-host").read_text(
            encoding="utf-8", errors="strict")
    for required in (
        'MCP_LAUNCHER = "/usr/libexec/hepta-agent-mcp-launcher"',
        '"method": "initialize"',
        '"method": "tools/list"',
        '("system.get_health", {})',
        '"market.get_quote", {"instrument": "EUR.USD"}',
        'name.startswith("trade.")',
        '"risk.preview_order"',
        "preexec_fn=_drop_to_agent_identity",
        'health.get("gateway_ready") is not True',
        'health.get("remote_execution_ready") is not True',
        'health.get("execution_mode") != "SIMULATOR"',
    ):
        if required not in preflight:
            raise AssertionError(
                f"Agent OS runtime preflight misses real MCP probe {required}")

    host_dropin = (
        root / "usr/share/doc/heptatrader/examples/"
        "hepta-agent-host-identity.conf.example").read_text(
            encoding="utf-8", errors="strict")
    for required in (
        "User=hepta-agent", "Group=hepta-agent", "SupplementaryGroups=",
        "UMask=0077", "NoNewPrivileges=yes",
    ):
        if required not in host_dropin:
            raise AssertionError(f"Agent host identity drop-in misses {required}")

    broker_dropin = (
        root / "usr/share/doc/heptatrader/examples/"
        "hepta-agent-broker-egress-policy.conf.example").read_text(
            encoding="utf-8", errors="strict")
    for required in (
            "BindsTo=hepta-broker-egress-policy.service",
            "After=hepta-broker-egress-policy.service",
            "CapabilityBoundingSet=",
            "AmbientCapabilities=",
            "RestrictNamespaces=yes",
            "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6"):
        if required not in broker_dropin:
            raise AssertionError(
                f"Agent broker boundary drop-in misses {required}")

    collector = (root / "usr/libexec/hepta-shadow-watch-collector").read_text(
        encoding="utf-8", errors="strict")
    exporter = (root / "usr/libexec/hepta-shadow-watch-exporter").read_text(
        encoding="utf-8", errors="strict")
    collector_unit = (root / "usr/lib/systemd/system/"
                      "hepta-shadow-watch-collector@.service").read_text(
                          encoding="utf-8", errors="strict")
    export_unit = (root / "usr/lib/systemd/system/"
                   "hepta-shadow-watch-export@.service").read_text(
                       encoding="utf-8", errors="strict")
    timer_unit = (root / "usr/lib/systemd/system/"
                  "hepta-shadow-watch-collector@.timer").read_text(
                      encoding="utf-8", errors="strict")
    for required in (
            '"system.get_health"', '"account.get_summary"',
            '"portfolio.list_positions"', '"orders.list"',
            '"risk.get_limits"', '"market.get_quote"',
            '"mutation_attempted": False', '"live_authorized": False'):
        if required not in collector:
            raise AssertionError(f"WATCH collector misses {required}")
    for forbidden in (
            "risk.preview_order\"", "risk.preview_flatten\"",
            "trade.place_order\"", "trade.cancel_order\"",
            "trade.flatten_position\""):
        if collector.count(forbidden) != 1:
            raise AssertionError(
                f"WATCH collector forbidden-tool denylist drifted: {forbidden}")
    for required in (
            'document.get("mutation_attempted") is False',
            'document.get("live_authorized") is False',
            'document.get("direct_broker_access") is False',
            '0 if require_root else os.geteuid()'):
        if required not in exporter:
            raise AssertionError(f"WATCH exporter misses {required}")
    for required in (
            "User=hepta-agent-%i", "Group=hepta-agent-%i",
            "SupplementaryGroups=", "PrivateNetwork=yes",
            "RestrictAddressFamilies=AF_UNIX", "CapabilityBoundingSet=",
            "OnSuccess=hepta-shadow-watch-export@%i.service",
            "EnvironmentFile=/etc/heptatrader/trust-domains/%i.shadow-watch.env",
            "uid-${HEPTA_SHADOW_AGENT_UID}.json"):
        if required not in collector_unit:
            raise AssertionError(f"WATCH collector unit misses {required}")
    for required in (
            "User=root", "PrivateNetwork=yes",
            "EnvironmentFile=/etc/heptatrader/trust-domains/%i.shadow-watch.env",
            "CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER",
            "--lease-receipt-source /run/hepta-agent-%i/sessions/"
            "shadow-watch-lease-receipt.json",
            "--lease-receipt-destination /run/hepta-shadow-watch-export-%i/"
            "shadow-watch-lease-receipt.json",
            "--export-receipt-destination /run/hepta-shadow-watch-export-%i/"
            "shadow-watch-export-receipt.json"):
        if required not in export_unit:
            raise AssertionError(f"WATCH export unit misses {required}")
    if "Persistent=false" not in timer_unit or "[Install]" in timer_unit:
        raise AssertionError("WATCH timer must be static and non-persistent")
    custodian = (
        root / "usr/libexec/hepta-shadow-watch-custodian").read_text(
            encoding="utf-8", errors="strict")
    custodian_unit = (
        root / "usr/lib/systemd/system/"
        "hepta-shadow-watch-custodian@.service").read_text(
            encoding="utf-8", errors="strict")
    reconcile_unit = (
        root / "usr/lib/systemd/system/"
        "hepta-shadow-watch-custodian-reconcile@.service").read_text(
            encoding="utf-8", errors="strict")
    reconcile_timer = (
        root / "usr/lib/systemd/system/"
        "hepta-shadow-watch-custodian-reconcile@.timer").read_text(
            encoding="utf-8", errors="strict")
    for required in (
            'commands.add_parser("provision")',
            'commands.add_parser("rotate")',
            'commands.add_parser("supervise")',
            'commands.add_parser("reconcile")',
            'commands.add_parser("close")',
            '"PROVISION_PREPARING"', '"ROTATION_PREPARING"',
            '"PENDING_EXPIRY"', '"CLEANING"',
            'Raw bearer material remains',
            'runtime-only under /run',
            '"paper_authorized": False',
            '"live_authorized": False',
            '"mutation_authorized": False',
            '"direct_broker_access": False'):
        if required not in custodian:
            raise AssertionError(
                f"WATCH custodian misses {required}")
    for forbidden in (
            'commands.add_parser("register")',
            'commands.add_parser("prepare-rotation")',
            'commands.add_parser("commit-rotation")'):
        if forbidden in custodian:
            raise AssertionError(
                f"WATCH custodian exposes low-level command {forbidden}")
    if "recovery.token" in custodian:
        raise AssertionError(
            "WATCH custodian persists forbidden recovery bearer")
    for required in (
            "User=root", "Group=root",
            "ExecStart=/usr/libexec/hepta-shadow-watch-custodian "
            "--domain-config /etc/heptatrader/trust-domains/%i.json supervise",
            "ExecStop=/usr/libexec/hepta-shadow-watch-custodian "
            "--domain-config /etc/heptatrader/trust-domains/%i.json close "
            "--reason service-stop",
            "ExecStopPost=/usr/libexec/hepta-shadow-watch-custodian "
            "--domain-config /etc/heptatrader/trust-domains/%i.json close "
            "--reason service-stop-post",
            "Restart=on-failure", "PrivateNetwork=yes",
            "RestrictAddressFamilies=AF_UNIX",
            "CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER",
            "StateDirectory=hepta-shadow-watch-custodian",
            "ReadWritePaths=-/run/hepta-agent-%i/sessions",
            "ReadWritePaths=-/run/hepta-shadow-watch-export-%i",
            "ReadWritePaths=/var/lib/hepta-shadow-watch-%i/private"):
        if required not in custodian_unit:
            raise AssertionError(
                f"WATCH custodian unit misses {required}")
    if "[Install]" in custodian_unit or "[Install]" in reconcile_unit:
        raise AssertionError(
            "WATCH custodian services must require explicit operation")
    for required in (
            "Type=oneshot", "User=root",
            "ExecStart=/usr/libexec/hepta-shadow-watch-custodian "
            "--domain-config /etc/heptatrader/trust-domains/%i.json reconcile",
            "PrivateNetwork=yes", "RestrictAddressFamilies=AF_UNIX",
            "ReadWritePaths=-/var/lib/hepta-shadow-watch-%i/private"):
        if required not in reconcile_unit:
            raise AssertionError(
                f"WATCH custodian reconcile unit misses {required}")
    for required in (
            "OnBootSec=15s", "OnUnitActiveSec=15s",
            "Persistent=true",
            "Unit=hepta-shadow-watch-custodian-reconcile@%i.service",
            "[Install]", "WantedBy=timers.target"):
        if required not in reconcile_timer:
            raise AssertionError(
                f"WATCH custodian timer misses {required}")
    watch_environment = parse_environment(
        root / "usr/share/doc/heptatrader/examples/"
        "hepta-shadow-watch-domain.env.example")
    if watch_environment != {
            "HEPTA_SHADOW_AGENT_UID": "2104",
            "HEPTA_SHADOW_AGENT_GID": "2104",
            "HEPTA_SHADOW_READER_UID": "1000",
            "HEPTA_SHADOW_READER_GID": "1000"}:
        raise AssertionError("WATCH export environment example drifted")
    dependency = (
        "[Unit]\n"
        "BindsTo=hepta-broker-egress-policy.service\n"
        "After=hepta-broker-egress-policy.service\n")
    for relative in (
            "usr/lib/systemd/system/hepta-tool-gateway.service.d/"
            "10-hepta-broker-egress-policy.conf",
            "usr/lib/systemd/system/hepta-tool-gateway@.service.d/"
            "10-hepta-broker-egress-policy.conf"):
        if (root / relative).read_text(
                encoding="utf-8", errors="strict") != dependency:
            raise AssertionError(
                "Gateway broker policy dependency drop-in drifted")
    policy_unit = (
        root / "usr/lib/systemd/system/"
        "hepta-broker-egress-policy.service").read_text(
            encoding="utf-8", errors="strict")
    for required in (
            "Type=notify",
            "NotifyAccess=main",
            "LoadCredential=hepta-broker-egress-policy.py:"
            "/usr/libexec/hepta-broker-egress-policy",
            "LoadCredential=hepta-local-paper-control.py:"
            "/usr/libexec/hepta-local-paper-control",
            "ExecStartPre=/usr/bin/python3.12 -I -S "
            "${CREDENTIALS_DIRECTORY}/hepta-local-paper-control.py "
            "reconcile-before-broker",
            "ExecStart=/usr/bin/python3.12 -I -S "
            "${CREDENTIALS_DIRECTORY}/hepta-broker-egress-policy.py "
            "--supervise-deny-all --paper-identities ",
            "ExecStopPost=/usr/bin/python3.12 -I -S "
            "${CREDENTIALS_DIRECTORY}/hepta-broker-egress-policy.py "
            "--tighten-deny-all",
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
            "ReadWritePaths=/etc/systemd/system/"
            "hepta-broker-egress-policy.service.d",
            "ReadWritePaths=-/run/hepta-local-paper-control"):
        if required not in policy_unit:
            raise AssertionError(
                f"broker policy lifecycle unit misses {required}")

    activation_helper = (
        root / "usr/libexec/hepta-p1-watch-activation-transaction").read_text(
            encoding="utf-8", errors="strict")
    for required in (
            'ROUND = 114', 'PREDECESSOR_ROUND = 95',
            'ANCESTOR_ROUND = 86', 'DOMAIN = "alpha"',
            'RECEIPT_SCHEMA = "hepta.p1-watch-activation-receipt.v4"',
            'RECEIPT_VERSION = 4',
            '"hepta.p1-watch-activation-failed-receipt.v3"',
            '"p1-watch-activation-round114-receipt-v4.json"',
            '"p1-watch-activation-round114-failed-receipt-v3.json"',
            '"predecessor_activation_success"',
            '"fresh_activation_transaction"',
            '"paper_authorized": False', '"live_authorized": False',
            '"watch_authority_provisioned": False',
            '"campaign_launched": False',
            '"paper_prerequisite_satisfied": False',
            'HEPTA_ACTIVATION_REQUIRE_CREDENTIALS'):
        if required not in activation_helper:
            raise AssertionError(
                f"WATCH activation helper misses fixed contract {required}")
    for forbidden in (
            '"paper_authorized": True', '"live_authorized": True',
            '"watch_authority_provisioned": True',
            '"campaign_launched": True'):
        if forbidden in activation_helper:
            raise AssertionError(
                f"WATCH activation helper contains authority {forbidden}")

    profile_helper = (
        root / "usr/libexec/hepta-p1-watch-profile-deployer").read_text(
            encoding="utf-8", errors="strict")
    admission_helper = (
        root / "usr/libexec/hepta-p1-shadow-admission-launcher").read_text(
            encoding="utf-8", errors="strict")
    shadow_installer = (
        root / "usr/libexec/hepta-shadow-host-installer").read_text(
            encoding="utf-8", errors="strict")
    for label, helper in (
            ("WATCH profile", profile_helper),
            ("WATCH activation", activation_helper),
            ("WATCH admission", admission_helper),
            ("SHADOW installer", shadow_installer)):
        for required in (
                "/var/lib/hepta/shadow-runtime-install-state/"
                "current-install-v1.json",
                '"current_install_pointer_path"',
                '"current_install_pointer_file_sha256"',
                '"install_generation"',
                '"predecessor_install_generation"',
                '"predecessor_current_install_pointer_file_sha256"'):
            if required not in helper:
                raise AssertionError(
                    f"{label} helper misses current-install contract "
                    f"{required}")
    for label, helper in (
            ("WATCH activation", activation_helper),
            ("WATCH admission", admission_helper),
            ("SHADOW installer", shadow_installer)):
        if "hepta.shadow-runtime-install-consumption-evidence.v3" not in helper:
            raise AssertionError(
                f"{label} helper misses exact consumption-evidence v3 schema")
    for required in (
            'ROUND114_RECEIPT_SCHEMA = '
            '"hepta.p1-watch-profile-deployment-receipt.v8"',
            'ROUND114_RECEIPT_VERSION = 8',
            'ROUND114_RECEIPT_STATUS = '
            '"OFFLINE_PASSIVE_WATCH_PROFILE_REATTESTED"',
            '"predecessor_profile_receipt"',
            'round114-generation22.json',
            'evidence_version not in {2, 3}',
            '"hepta.shadow-runtime-install-consumption-evidence.v" +',
            '"--expected-install-manifest-sha256"',
            '"--expected-install-receipt-sha256"'):
        if required not in profile_helper:
            raise AssertionError(
                f"WATCH profile helper misses fixed contract {required}")
    for required in (
            'RECEIPT_SCHEMA = '
            '"hepta.shadow-runtime-install-receipt.v4"',
            'CONSUMPTION_EVIDENCE_SCHEMA = (\n'
            '    "hepta.shadow-runtime-install-consumption-evidence.v3")',
            'CURRENT_INSTALL_POINTER_SCHEMA = '
            '"hepta.shadow-runtime-current-install.v1"',
            "INSTALLATION_BLOCKING_UNITS",
            '"--expected-current-install-generation"',
            '"--expected-current-install-pointer-sha256"',
            '"INSTALL_CURRENT_LINEAGE_MISMATCH"'):
        if required not in shadow_installer:
            raise AssertionError(
                f"SHADOW installer misses fixed contract {required}")

    current_install_contracts = {
        "WATCH profile": (
            profile_helper,
            "CURRENT_SHADOW_INSTALL_GENERATION = 22",
            "CURRENT_SHADOW_PREDECESSOR_INSTALL_GENERATION = 21",
            "SHADOW_INSTALL_FILE_COUNT = 128",
        ),
        "WATCH activation": (
            activation_helper,
            "EXPECTED_SHADOW_INSTALL_GENERATION = 22",
            "EXPECTED_PREDECESSOR_SHADOW_INSTALL_GENERATION = 21",
            "SHADOW_INSTALL_FILE_COUNT = 128",
        ),
        "WATCH admission": (
            admission_helper,
            "EXPECTED_SHADOW_INSTALL_GENERATION = 22",
            "EXPECTED_PREDECESSOR_SHADOW_INSTALL_GENERATION = 21",
            "SHADOW_INSTALL_FILE_COUNT = 128",
        ),
        "SHADOW installer": (
            shadow_installer,
            "EXPECTED_SHADOW_FILE_COUNT = 128",
        ),
    }
    predecessor_pointer = (
        "sha256:2beeb507fcafbbfc2c93d2e4756fddf0b27e9872733ff97d28af47006461d406")
    for label, (helper, *required_fragments) in current_install_contracts.items():
        for required in required_fragments:
            if required not in helper:
                raise AssertionError(
                    f"{label} helper misses current Round114 contract "
                    f"{required}")
        if label != "SHADOW installer" and predecessor_pointer not in helper:
            raise AssertionError(
                f"{label} helper misses exact generation-21 pointer")

    round114_consumers = {
        "safety auditor": "hepta-p1-safety-soak-auditor",
        "independent observer": "hepta-p1-safety-soak-independent-observer",
        "evidence recorder": "hepta-p1-safety-soak-evidence-recorder",
        "WATCH handoff": "hepta-p1-watch-to-paper-handoff",
        "PAPER admission": "hepta-p1-paper-admission-verifier",
    }
    for label, leaf in round114_consumers.items():
        helper = (root / "usr/libexec" / leaf).read_text(
            encoding="utf-8", errors="strict")
        for required in (
                "round114", "generation22", "128", predecessor_pointer):
            if required not in helper:
                raise AssertionError(
                    f"{label} helper misses unified Round114 contract "
                    f"{required}")
    for leaf in (
            "hepta-p1-safety-soak-campaign-freezer",
            "hepta-p1-paper-kill-switch-bootstrap",
            "hepta-p1-paper-zero-exposure-attestor",
            "hepta-p1-paper-zero-exposure-snapshot-producer"):
        helper = (root / "usr/libexec" / leaf).read_text(
            encoding="utf-8", errors="strict")
        if "ROUND = 114" not in helper:
            raise AssertionError(
                f"{leaf} does not consume the unified Round114 contract")
    fault_pin = (
        root / "usr/libexec/hepta-p1-safety-soak-fault-pin-producer"
    ).read_text(encoding="utf-8", errors="strict")
    if "hepta-p1-root-fault-injector-round114" not in fault_pin:
        raise AssertionError("fault pin producer still targets an old round")

    campaign_helpers = {
        "policy planner": (
            "hepta-p1-safety-soak-policy-planner",
            'INSTALLED_EXECUTABLE = Path(\n'
            '    "/usr/libexec/hepta-p1-safety-soak-policy-planner")'),
        "coordinator": (
            "hepta-p1-safety-soak-campaign-coordinator",
            'RUNTIME_SCHEMA = "hepta.p1-safety-soak-campaign-runtime.v1"'),
        "observer worker": (
            "hepta-p1-safety-soak-observer-worker",
            'PRODUCTION_MODE = "PRODUCTION_ROOT_OBSERVER_WORKER"'),
        "recorder worker": (
            "hepta-p1-safety-soak-recorder-worker",
            'RECORDER = "/usr/libexec/hepta-p1-safety-soak-evidence-recorder"'),
        "fault pin producer": (
            "hepta-p1-safety-soak-fault-pin-producer",
            'PRODUCTION_MODE = "PRODUCTION_ROOT_PINNING"'),
    }
    for label, (leaf, required) in campaign_helpers.items():
        helper = (root / "usr/libexec" / leaf).read_text(
            encoding="utf-8", errors="strict")
        if required not in helper:
            raise AssertionError(
                f"P1 safety-soak {label} installed contract drifted")

    paper_admission_helpers = {
        "hepta-p1-paper-admission-verifier": (
            'INSTALLED_EXECUTABLE = Path(\n'
            '    "/usr/libexec/hepta-p1-paper-admission-verifier")'),
        "hepta-p1-paper-zero-exposure-attestor": (
            'INSTALLED_EXECUTABLE = Path(\n'
            '    "/usr/libexec/hepta-p1-paper-zero-exposure-attestor")'),
        "hepta-p1-paper-zero-exposure-snapshot-producer": (
            'INSTALLED_EXECUTABLE = Path(\n'
            '    "/usr/libexec/'
            'hepta-p1-paper-zero-exposure-snapshot-producer")'),
        "hepta-rootful-review-closure-consumer": (
            'INSTALLED_VERIFIER = Path(\n'
            '    "/usr/libexec/'
            'hepta-rootful-systemd-environment-provenance")'),
        "hepta-rootful-systemd-environment-provenance": (
            'INSTALLED_EXECUTABLE = Path(\n'
            '    "/usr/libexec/'
            'hepta-rootful-systemd-environment-provenance")'),
    }
    for leaf, required in paper_admission_helpers.items():
        helper = (root / "usr/libexec" / leaf).read_text(
            encoding="utf-8", errors="strict")
        if required not in helper:
            raise AssertionError(
                f"P1 PAPER admission helper {leaf} fixed identity drifted")
    consumer_payload = (
        root / "usr/libexec/hepta-rootful-review-closure-consumer"
    ).read_bytes()
    if consumer_payload != (
            root / "usr/libexec/hepta_rootful_review_closure_consumer.py"
    ).read_bytes():
        raise AssertionError(
            "rootful review closure consumer companion differs from executable")

    kill_bootstrap = (
        root / "usr/libexec/hepta-p1-paper-kill-switch-bootstrap"
    ).read_text(encoding="utf-8", errors="strict")
    for required in (
            'INSTALLED_EXECUTABLE = Path(\n'
            '    "/usr/libexec/hepta-p1-paper-kill-switch-bootstrap")',
            'MARKER_BYTES = b"engaged"',
            '"paper_authorized": False', '"live_authorized": False',
            '"mutation_authorized": False',
            '"direct_broker_access": False',
            '"order_submission_authorized": False'):
        if required not in kill_bootstrap:
            raise AssertionError(
                f"P1 PAPER kill-switch bootstrap misses {required}")

    release_cli = (
        root / "usr/libexec/hepta-release-validation-closure-verifier"
    ).read_bytes()
    release_module = (
        root / "usr/libexec/verify_heptatrader_release_validation_closure.py"
    ).read_bytes()
    if release_cli != release_module:
        raise AssertionError(
            "release-validation fixed verifier and import companion drifted")
    companion_modules = set(RELEASE_VALIDATION_COMPANION_NAMES)
    reached: set[str] = set()
    pending = ["verify_heptatrader_release_validation_closure"]
    imports_hepta_ops = False
    while pending:
        module = pending.pop()
        if module in reached:
            continue
        reached.add(module)
        payload = (
            root / "usr/libexec" / f"{module}.py"
        ).read_text(encoding="utf-8", errors="strict")
        tree = ast.parse(payload, filename=f"{module}.py")
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(
                    alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
        imports_hepta_ops = imports_hepta_ops or "hepta_ops" in imported
        pending.extend(sorted(imported & companion_modules - reached))
    if reached != companion_modules or not imports_hepta_ops:
        raise AssertionError(
            "release-validation recursive installed dependency closure drifted")
    for relative in RELEASE_VALIDATION_PACKAGE_FILES:
        compile((root / relative).read_bytes(), relative, "exec")

    apparmor = (
        root / "usr/share/heptatrader/systemd/hepta-systemd-gate.apparmor"
    ).read_text(encoding="ascii", errors="strict")
    if (
            "profile hepta-systemd-gate "
            "flags=(attach_disconnected,mediate_deleted) {" not in apparmor or
            "deny mount," not in apparmor or
            "deny /sys/kernel/security/** rwklx," not in apparmor):
        raise AssertionError("rootful systemd AppArmor contract drifted")

    campaign_units = {
        "hepta-p1-safety-soak-campaign@.service": (
            "Type=notify\n", "WatchdogSec=45s\n",
            "ExecStart=/usr/libexec/hepta-p1-safety-soak-campaign-coordinator "),
        "hepta-p1-safety-soak-observer-worker@.service": (
            "Type=notify\n", "WatchdogSec=30s\n",
            "ExecStart=/usr/libexec/hepta-p1-safety-soak-observer-worker "),
        "hepta-p1-safety-soak-recorder-worker@.service": (
            "Type=notify\n", "WatchdogSec=30s\n",
            "ExecStart=/usr/libexec/hepta-p1-safety-soak-recorder-worker "),
        "hepta-p1-safety-soak@.target": (
            "StopWhenUnneeded=yes\n",
            "Requires=hepta-p1-safety-soak-campaign@%i.service\n"),
    }
    for leaf, required_fragments in campaign_units.items():
        unit = (root / "usr/lib/systemd/system" / leaf).read_text(
            encoding="utf-8", errors="strict")
        if "[Install]" in unit:
            raise AssertionError(
                f"P1 safety-soak unit must remain explicit: {leaf}")
        for required in required_fragments:
            if required not in unit:
                raise AssertionError(
                    f"P1 safety-soak unit {leaf} misses {required!r}")
        for forbidden in ("AF_INET", "AF_INET6", "paper_authorized=true",
                          "live_authorized=true"):
            if forbidden in unit:
                raise AssertionError(
                    f"P1 safety-soak unit {leaf} weakens boundary: {forbidden}")

    activation_credentials = (
        "LoadCredential=hepta-p1-watch-activation-transaction.py:"
        "/usr/libexec/hepta-p1-watch-activation-transaction\n"
        "LoadCredential=hepta-p1-watch-profile-deployer.py:"
        "/usr/libexec/hepta-p1-watch-profile-deployer\n"
        "LoadCredential=hepta-broker-egress-policy.py:"
        "/usr/libexec/hepta-broker-egress-policy\n"
        "LoadCredential=hepta-shadow-host-installer.py:"
        "/usr/libexec/hepta-shadow-host-installer\n")
    for leaf, action, timeout in (
            ("hepta-p1-watch-activation.service", "activate", "5min"),
            ("hepta-p1-watch-activation-reconcile.service",
             "reconcile", "3min")):
        unit = (root / "usr/lib/systemd/system" / leaf).read_text(
            encoding="utf-8", errors="strict")
        if "[Install]" in unit:
            raise AssertionError(
                "WATCH activation services must not be installable")
        for required in (
                activation_credentials,
                "Environment=HEPTA_ACTIVATION_REQUIRE_CREDENTIALS=1\n",
                "ExecStart=/usr/bin/python3.12 -I -S "
                "${CREDENTIALS_DIRECTORY}/"
                "hepta-p1-watch-activation-transaction.py "
                f"{action}\n",
                f"TimeoutStartSec={timeout}\n",
                "CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE "
                "CAP_DAC_READ_SEARCH CAP_FOWNER CAP_SYS_PTRACE "
                "CAP_NET_ADMIN\n",
                "AmbientCapabilities=\n",
                "RestrictAddressFamilies=AF_UNIX AF_NETLINK\n",
                "ReadOnlyPaths=/usr/libexec\n",
                "ReadOnlyPaths=/usr/lib/systemd/system\n",
                "ReadWritePaths=/etc/systemd/system\n",
                "ReadWritePaths=/run/systemd/system\n"):
            if required not in unit:
                raise AssertionError(
                    f"WATCH activation unit {leaf} misses {required!r}")
        if action == "activate":
            if (
                    "Requires=hepta-p1-watch-activation-reconcile.timer\n"
                    in unit or
                    "After=local-fs.target systemd-remount-fs.service\n"
                    not in unit):
                raise AssertionError(
                    "WATCH activation service pre-starts its reboot backstop")
        else:
            required = (
                "After=local-fs.target systemd-remount-fs.service "
                "hepta-p1-watch-activation.service\n")
            if required not in unit:
                raise AssertionError(
                    "WATCH reconcile service can race activation")
    activation_timer = (
        root / "usr/lib/systemd/system/"
        "hepta-p1-watch-activation-reconcile.timer").read_text(
            encoding="utf-8", errors="strict")
    for required in (
            "OnActiveSec=30s\n", "OnUnitActiveSec=30s\n",
            "AccuracySec=1s\n",
            "Unit=hepta-p1-watch-activation-reconcile.service\n",
            "[Install]\n", "WantedBy=timers.target\n"):
        if required not in activation_timer:
            raise AssertionError(
                f"WATCH activation reconcile timer misses {required!r}")
    if "OnBootSec=" in activation_timer or "Persistent=" in activation_timer:
        raise AssertionError(
            "WATCH activation timer can elapse before activation ExecStart")

    paper_example_path = root / PAPER_IDENTITY_SOURCE
    paper_example_bytes = paper_example_path.read_bytes()
    if (
            len(paper_example_bytes) != 257 or
            hashlib.sha256(paper_example_bytes).hexdigest() !=
            PAPER_IDENTITY_SOURCE_SHA256):
        raise AssertionError(
            "installed PAPER identity example is not the exact deny-all source")
    paper_manifest = strict_json_loads(
        paper_example_bytes.decode("utf-8", errors="strict"),
        "PAPER identity example")
    if (
            not isinstance(paper_manifest, dict) or
            set(paper_manifest) != {
                "schema", "version", "source_policy_sha256",
                "paper_authorized", "live_authorized", "identities"} or
            paper_manifest.get("paper_authorized") is not False or
            paper_manifest.get("live_authorized") is not False or
            paper_manifest.get("identities") != []):
        raise AssertionError(
            "installed PAPER identity example is not default-deny")
    policy_manifest = strict_json_loads((
        root / "usr/share/heptatrader/"
        "hepta-broker-network-policy-v1.json"
    ).read_text(encoding="utf-8", errors="strict"), "broker network policy")
    if (
            not isinstance(policy_manifest, dict) or
            policy_manifest.get("paper_identity_manifest", {}).get(
                "max_identities") != 1):
        raise AssertionError(
            "installed policy does not cap templated PAPER at one domain")

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
            "--paper-identities",
            str(paper_example_path),
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
            "meta skuid 2003 counter return" not in rendered.stdout or
            "meta skuid !=" in rendered.stdout):
        raise AssertionError(
            "installed broker policy did not render the exact boundary")

    profile = parse_environment(
        root / "usr/share/doc/heptatrader/examples/hepta-tool-gateway.env.example")
    expected = {
        "HEPTA_EXECUTION_REMOTE_MODE": "SIMULATOR",
        "HEPTA_EXECUTION_SOCKET": "/run/hepta-execution/execution.sock",
        "HEPTA_EXECUTION_EVENT_SOCKET": "/run/hepta-execution/events.sock",
        "HEPTA_EXECUTION_SERVICE_UID": "2002",
        "HEPTA_EXECUTION_IO_TIMEOUT_MS": "2500",
        "HEPTA_EXECUTION_MAX_RESPONSE_BYTES": "32768",
        "HEPTA_TOOL_ACCOUNT": "SIM",
        "HEPTA_TOOL_AGENT_ID": "codex-agent-os-e2e",
        "HEPTA_EXECUTION_DOMAIN_ID": "SIM:codex-agent-os-e2e",
        "HEPTA_TOOL_ALLOW_TRADE": "0",
        "HEPTA_TOOL_SESSION_TEMPLATES": "watch",
        "HEPTA_TOOL_CONTRACT_BINDINGS":
            "EUR.USD|EUR|CASH|IDEALPRO|USD",
        "HEPTA_TOOL_AGENT_UID": "2004",
        "HEPTA_TOOL_SUPERVISOR_UID": "0",
        "HEPTA_TOOL_SUPERVISOR_MAX_TTL_SEC": "86400",
        "HEPTA_TOOL_SERVER_WORKERS": "4",
        "HEPTA_TOOL_SERVER_MAX_PENDING": "32",
        "HEPTA_TOOL_SERVER_MAX_CONCURRENT_PER_OWNER": "1",
        "HEPTA_TOOL_SERVER_MAX_PENDING_PER_OWNER": "8",
        "HEPTA_TOOL_SERVER_INGRESS_WORKERS": "2",
    }
    if profile != expected:
        raise AssertionError("gateway example differs from reviewed WATCH-only profile")
    for key in profile:
        if any(word in key for word in
               ("PASSWORD", "TOKEN", "SECRET", "AUTHORIZATION", "FENCE", "CREDENTIAL")):
            raise AssertionError(f"gateway example contains secret-bearing key {key}")


def smoke_installed_mcp(root: Path) -> None:
    executable = root / "usr/libexec/hepta-mcp-server"
    requests = (
        '{"jsonrpc":"2.0","id":1,"method":"initialize",'
        '"params":{"protocolVersion":"2025-03-26"}}\n'
        '{"jsonrpc":"2.0","id":2,"method":"ping","params":{}}\n')
    environment = os.environ.copy()
    environment["HEPTA_TOOL_EXPECTED_UID"] = str(os.geteuid())
    completed = subprocess.run(
        [str(executable)], input=requests, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, check=False, timeout=10,
        env=environment)
    if completed.returncode != 0 or completed.stderr:
        raise AssertionError(
            "installed MCP bridge initialize/ping smoke failed: " +
            completed.stderr)
    responses = [
        strict_json_loads(line, "installed MCP smoke response")
        for line in completed.stdout.splitlines()]
    if (len(responses) != 2 or
            responses[0].get("result", {}).get("serverInfo", {}).get("name")
            != "heptatrader" or responses[1].get("result") != {}):
        raise AssertionError("installed MCP bridge returned invalid smoke responses")

    if os.geteuid() != 2004:
        fixed_environment = os.environ.copy()
        fixed_environment["HEPTA_TOOL_EXPECTED_UID"] = "2004"
        rejected = subprocess.run(
            [str(executable)], input=requests, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, check=False, timeout=10,
            env=fixed_environment)
        if (rejected.returncode != 78 or rejected.stdout or
                "effective UID does not match hepta-agent" not in rejected.stderr):
            raise AssertionError(
                "installed MCP bridge did not reject a non-Agent identity")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", required=True, type=Path)
    parser.add_argument("--cmake", default="cmake")
    args = parser.parse_args()
    build = args.build_dir.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="hepta-agent-os-install-") as directory:
        root = Path(directory) / "root"
        environment = os.environ.copy()
        environment["DESTDIR"] = str(root)
        previous_umask = os.umask(0o022)
        try:
            subprocess.run([
                args.cmake, "--install", str(build), "--prefix", "/usr",
                "--component", COMPONENT,
            ], check=True, env=environment, stdout=subprocess.PIPE,
               stderr=subprocess.STDOUT, text=True)
        finally:
            os.umask(previous_umask)
        verify(root)
        smoke_installed_mcp(root)
    print("hepta_agent_os_install_tree: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
