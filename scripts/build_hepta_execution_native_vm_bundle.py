#!/usr/bin/env python3

"""Build one deterministic broker-free native-VM rootfs payload."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import stat
import sys
import tarfile
import tempfile
from typing import Any, Optional


SCRIPT_DIRECTORY = Path(__file__).resolve(strict=True).parent
REPOSITORY = SCRIPT_DIRECTORY.parent
sys.path.insert(0, str(SCRIPT_DIRECTORY))
import run_hepta_execution_rootful_systemd_gate as shared  # noqa: E402
import verify_heptatrader_clean_source_bundle as clean_source  # noqa: E402
import check_hepta_agent_os_provisioned_host as agent_os_contract  # noqa: E402
import heptatrader_secure_artifacts as secure_artifacts  # noqa: E402
from hepta_service_identities import parse_identity_manifest  # noqa: E402


SCHEMA = "hepta.execution-native-vm-bundle.v7"
PROVISIONING_SCHEMA = "hepta.execution-native-vm-provisioning-manifest.v6"
IMAGE_SCHEMA = "hepta.execution-native-vm-image-manifest.v4"
SOURCE_BUILD_LINEAGE_SCHEMA = (
    "hepta.execution-native-vm-source-build-lineage.v3")
IBAPI_SOURCE_MANIFEST_SCHEMA = "hepta.ibapi-sdk-source-manifest.v1"
CAUSAL_BUILD_RECEIPT_SCHEMA = (
    "hepta.execution-native-vm-fresh-causal-build.v1")
AGENT_OS_INSTALLATION_SCHEMA = (
    "hepta.agent-os-native-vm-installation-manifest.v2")
AGENT_OS_RUNTIME_INPUT_SCHEMA = (
    "hepta.agent-os-native-vm-runtime-input-manifest.v1")
POLICY_SCHEMA = "hepta.execution-native-vm-platform-policy.v1"
IDENTITY_MANIFEST = REPOSITORY / "systemd/hepta-service-identities-v1.json"
VARIANTS = ("real", "sandbox", "stub")
SHARE = Path("usr/local/share/hepta-rootful-systemd-gate")
FORMAL_IB_PATH = Path("usr/libexec/hepta-ib-executiond-formal")
SENTINEL_PATH = Path(
    "etc/heptatrader/hepta-native-systemd-gate.disposable")
CLEAN_SOURCE_PROVENANCE = SHARE / "clean-source-provenance.json"
CLEAN_SOURCE_MANIFEST = SHARE / "source-bundle-manifest.json"
SOURCE_BUILD_LINEAGE = SHARE / "source-build-lineage.json"
BUILD_EVIDENCE_PATHS = {
    "ibapi_on": {
        "cmake_cache": SHARE / "build-ibapi-on-CMakeCache.txt",
        "compile_commands": SHARE / "build-ibapi-on-compile-commands.json",
    },
    "ibapi_off": {
        "cmake_cache": SHARE / "build-ibapi-off-CMakeCache.txt",
        "compile_commands": SHARE / "build-ibapi-off-compile-commands.json",
    },
}
AGENT_OS_INSTALLATION_MANIFEST = (
    SHARE / "agent-os-installation-manifest.json")
AGENT_OS_INSTALLATION_PREFLIGHT = Path(
    "usr/local/libexec/check_hepta_agent_os_provisioned_host.py")
AGENT_OS_INSTALLED_PREFLIGHT = Path(
    "usr/libexec/check-hepta-agent-os-provisioned-host")
AGENT_OS_RUNTIME_INNER_GATE = Path(
    "usr/local/libexec/hepta_agent_os_rootful_inner_gate.py")
AGENT_OS_RUNTIME_INPUT_MANIFEST = (
    SHARE / "agent-os-runtime-input-manifest.json")
AGENT_OS_RUNTIME_INSTALLATION_MARKER = Path(
    "usr/local/share/hepta-agent-os-e2e/installation-preflight")
AGENT_OS_RUNTIME_PROVISIONING = Path(
    "usr/local/share/hepta-agent-os-e2e/provisioning")
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
AGENT_OS_RUNTIME_PATHS = (
    Path("run/hepta-agent/tools.sock"),
    Path("run/hepta-agent/session.token"),
    Path("run/hepta-tool-gateway/session-supervisor.sock"),
)
AGENT_OS_STATIC_PATHS = (
    Path("usr/libexec/hepta-tool-gatewayd"),
    Path("usr/bin/hepta-sessionctl"),
    Path("usr/bin/heptactl"),
    Path("usr/libexec/hepta-mcp-server"),
    Path("usr/libexec/hepta-agent-mcp-launcher"),
    Path("usr/libexec/hepta-agent-session-bootstrap"),
    Path("usr/libexec/hepta_agent_trust_domain.py"),
    Path("usr/libexec/hepta-paper-receipt-contracts"),
    Path("usr/libexec/hepta-shadow-watch-collector"),
    Path("usr/libexec/hepta-shadow-watch-exporter"),
    Path("usr/libexec/hepta-shadow-watch-custodian"),
    Path("usr/libexec/hepta-broker-egress-policy"),
    Path("usr/libexec/hepta-shadow-host-installer"),
    Path("usr/libexec/hepta-p1-shadow-host-controller"),
    Path("usr/libexec/hepta-p1-load-probe-validator"),
    Path("usr/libexec/build-hepta-p1-observation-policy"),
    Path("usr/libexec/hepta-p1-shadow-observer-controller"),
    Path("usr/libexec/hepta-p1-shadow-admission-launcher"),
    Path("usr/libexec/hepta-p1-watch-profile-deployer"),
    Path("usr/libexec/hepta-p1-watch-activation-transaction"),
    Path("usr/libexec/hepta-bounded-shadow-closure-verifier"),
    Path("usr/libexec/hepta-official-source-capture"),
    Path("usr/libexec/hepta_bounded_shadow_observer.py"),
    Path("usr/libexec/hepta_market_context_builder.py"),
    Path("usr/libexec/hepta_market_evidence_normalizer.py"),
    Path("usr/libexec/hepta_market_official_source_extractor.py"),
    Path("usr/libexec/hepta_eurusd_confirmed_momentum_strategy.py"),
    Path("usr/libexec/hepta_shadow_market_history.py"),
    Path("usr/libexec/hepta_strategy_shadow_runner.py"),
    Path("usr/libexec/hepta_strategy_contracts.py"),
    Path("usr/libexec/validate_hepta_strategy_decision_receipt.py"),
    Path("usr/share/heptatrader/strategies/"
         "eurusd-confirmed-momentum-shadow-v2.json"),
    AGENT_OS_INSTALLED_PREFLIGHT,
    AGENT_OS_INSTALLATION_PREFLIGHT,
    Path("usr/lib/systemd/system/hepta-tool-gateway.service"),
    Path("usr/lib/systemd/system/hepta-tool-gateway.socket"),
    Path("usr/lib/systemd/system/hepta-tool-session-supervisor.socket"),
    Path("usr/lib/systemd/system/hepta-broker-egress-policy.service"),
    Path("usr/lib/systemd/system/hepta-p1-watch-activation.service"),
    Path("usr/lib/systemd/system/"
         "hepta-p1-watch-activation-reconcile.service"),
    Path("usr/lib/systemd/system/"
         "hepta-p1-watch-activation-reconcile.timer"),
    Path("usr/lib/systemd/system/hepta-tool-gateway@.service"),
    Path("usr/lib/systemd/system/hepta-tool-gateway@.socket"),
    Path("usr/lib/systemd/system/hepta-tool-session-supervisor@.socket"),
    Path("usr/lib/systemd/system/hepta-shadow-watch-collector@.service"),
    Path("usr/lib/systemd/system/hepta-shadow-watch-collector@.timer"),
    Path("usr/lib/systemd/system/hepta-shadow-watch-export@.service"),
    Path("usr/lib/systemd/system/hepta-shadow-watch-custodian@.service"),
    Path("usr/lib/systemd/system/"
         "hepta-shadow-watch-custodian-reconcile@.service"),
    Path("usr/lib/systemd/system/"
         "hepta-shadow-watch-custodian-reconcile@.timer"),
    Path("usr/lib/tmpfiles.d/heptatrader-agent-os.conf"),
    Path("usr/share/heptatrader/hepta-service-identities-v1.json"),
    Path("usr/share/heptatrader/hepta-broker-network-policy-v1.json"),
    Path("usr/share/heptatrader/.agents/plugins/marketplace.json"),
    Path("usr/share/heptatrader/plugins/heptatrader-agent-os/.mcp.json"),
    Path("usr/share/heptatrader/plugins/heptatrader-agent-os/"
         ".codex-plugin/plugin.json"),
    Path("usr/share/heptatrader/plugins/heptatrader-agent-os/README.md"),
    Path("usr/share/doc/heptatrader/examples/"
         "hepta-agent-host-identity.conf.example"),
    Path("usr/share/doc/heptatrader/examples/"
         "hepta-tool-gateway.env.example"),
    Path("usr/share/doc/heptatrader/examples/"
         "hepta-tool-gateway-domain.env.example"),
    Path("usr/share/doc/heptatrader/examples/"
         "hepta-shadow-watch-domain.env.example"),
    Path("usr/share/doc/heptatrader/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md"),
    Path("usr/share/doc/heptatrader/RUNBOOK-STARTUP.md"),
    Path("etc/heptatrader/hepta-tool-gateway.env"),
    Path("etc/heptatrader/"
         "hepta-agent-trust-domain-paper-identities-v1.json"),
    Path("etc/heptatrader/hepta-supervisor-lease.key"),
    AGENT_OS_RUNTIME_INNER_GATE,
    AGENT_OS_RUNTIME_INSTALLATION_MARKER,
    AGENT_OS_RUNTIME_PROVISIONING / "hepta-tool-gateway.env",
    AGENT_OS_RUNTIME_PROVISIONING / "hepta-execution-simulator.env",
    AGENT_OS_RUNTIME_PROVISIONING / "hepta-execution-simulator-fence",
    AGENT_OS_RUNTIME_PROVISIONING /
    "hepta-agent-trust-domain-paper-identities-v1.json",
    AGENT_OS_RUNTIME_INPUT_MANIFEST,
)
AGENT_OS_RUNTIME_GATE_PATHS = (
    AGENT_OS_RUNTIME_INNER_GATE,
    AGENT_OS_INSTALLED_PREFLIGHT,
    Path("usr/libexec/hepta-agent-session-bootstrap"),
    Path("usr/libexec/hepta-agent-mcp-launcher"),
    Path("usr/libexec/hepta_agent_trust_domain.py"),
    Path("usr/libexec/hepta-shadow-watch-collector"),
    Path("usr/libexec/hepta-broker-egress-policy"),
    Path("usr/libexec/hepta-shadow-host-installer"),
    Path("usr/libexec/hepta-p1-shadow-host-controller"),
    Path("usr/libexec/hepta-p1-load-probe-validator"),
    Path("usr/libexec/build-hepta-p1-observation-policy"),
    Path("usr/libexec/hepta-p1-shadow-observer-controller"),
    Path("usr/libexec/hepta-p1-shadow-admission-launcher"),
    Path("usr/libexec/hepta-p1-watch-profile-deployer"),
    Path("usr/libexec/hepta-p1-watch-activation-transaction"),
    Path("usr/libexec/hepta-bounded-shadow-closure-verifier"),
    Path("usr/libexec/hepta-official-source-capture"),
    Path("usr/libexec/hepta_bounded_shadow_observer.py"),
    Path("usr/libexec/hepta_market_context_builder.py"),
    Path("usr/libexec/hepta_market_evidence_normalizer.py"),
    Path("usr/libexec/hepta_market_official_source_extractor.py"),
    Path("usr/libexec/hepta_eurusd_confirmed_momentum_strategy.py"),
    Path("usr/libexec/hepta_shadow_market_history.py"),
    Path("usr/libexec/hepta_strategy_shadow_runner.py"),
    Path("usr/libexec/hepta_strategy_contracts.py"),
    Path("usr/libexec/validate_hepta_strategy_decision_receipt.py"),
    Path("usr/share/heptatrader/strategies/"
         "eurusd-confirmed-momentum-shadow-v2.json"),
    Path("usr/libexec/hepta-mcp-server"),
    Path("usr/libexec/hepta-tool-gatewayd"),
    Path("usr/libexec/hepta-executiond"),
    Path("usr/bin/hepta-sessionctl"),
    Path("usr/lib/systemd/system/hepta-tool-gateway.service"),
    Path("usr/lib/systemd/system/hepta-tool-gateway.socket"),
    Path("usr/lib/systemd/system/hepta-tool-session-supervisor.socket"),
    Path("usr/lib/systemd/system/hepta-broker-egress-policy.service"),
    Path("usr/lib/systemd/system/hepta-p1-watch-activation.service"),
    Path("usr/lib/systemd/system/"
         "hepta-p1-watch-activation-reconcile.service"),
    Path("usr/lib/systemd/system/"
         "hepta-p1-watch-activation-reconcile.timer"),
    Path("usr/lib/systemd/system/hepta-execution-simulator.service"),
    Path("usr/lib/systemd/system/hepta-execution-simulator.socket"),
    Path("usr/lib/systemd/system/hepta-execution-events-simulator.socket"),
    Path("usr/lib/systemd/system/hepta-execution-simulator@.service"),
    Path("usr/lib/systemd/system/hepta-execution-simulator@.socket"),
    Path("usr/lib/systemd/system/hepta-execution-events-simulator@.socket"),
    Path("usr/lib/tmpfiles.d/heptatrader-agent-os.conf"),
    Path("usr/share/heptatrader/hepta-service-identities-v1.json"),
    Path("usr/share/heptatrader/hepta-broker-network-policy-v1.json"),
    Path("usr/share/heptatrader/plugins/heptatrader-agent-os/.mcp.json"),
    AGENT_OS_RUNTIME_PROVISIONING /
    "hepta-agent-trust-domain-paper-identities-v1.json",
    AGENT_OS_RUNTIME_INSTALLATION_MARKER,
    AGENT_OS_RUNTIME_PROVISIONING / "hepta-tool-gateway.env",
    AGENT_OS_RUNTIME_PROVISIONING / "hepta-execution-simulator.env",
    AGENT_OS_RUNTIME_PROVISIONING / "hepta-execution-simulator-fence",
)

# Every repository byte copied into the final rootfs is closed here.  The
# destination mode is an installation decision; the source mode and bytes are
# independently bound to the verified clean-source manifest.
SOURCE_STAGE_BINDINGS: dict[str, tuple[tuple[str, str], ...]] = {
    "systemd/hepta-service-identities-v1.json": (
        ("usr/share/heptatrader/hepta-service-identities-v1.json", "0644"),),
    "adapters/mcp/hepta_mcp_server.py": (
        ("usr/libexec/hepta-mcp-server", "0755"),),
    "scripts/hepta_agent_mcp_launcher.py": (
        ("usr/libexec/hepta-agent-mcp-launcher", "0755"),),
    "scripts/hepta_broker_egress_policy.py": (
        ("usr/libexec/hepta-broker-egress-policy", "0755"),),
    "scripts/hepta_ib_paper_domain_authority.py": (
        ("usr/libexec/hepta-ib-paper-domain-authority", "0755"),),
    "systemd/hepta-execution-simulator.service": (
        ("usr/lib/systemd/system/hepta-execution-simulator.service", "0644"),),
    "systemd/hepta-execution-simulator.socket": (
        ("usr/lib/systemd/system/hepta-execution-simulator.socket", "0644"),),
    "systemd/hepta-execution-events-simulator.socket": (
        ("usr/lib/systemd/system/"
         "hepta-execution-events-simulator.socket", "0644"),),
    "systemd/hepta-execution-simulator@.service": (
        ("usr/lib/systemd/system/"
         "hepta-execution-simulator@.service", "0644"),),
    "systemd/hepta-execution-simulator@.socket": (
        ("usr/lib/systemd/system/"
         "hepta-execution-simulator@.socket", "0644"),),
    "systemd/hepta-execution-events-simulator@.socket": (
        ("usr/lib/systemd/system/"
         "hepta-execution-events-simulator@.socket", "0644"),),
    "systemd/hepta-execution-ib-paper.service": (
        ("usr/lib/systemd/system/hepta-execution-ib-paper.service", "0644"),),
    "systemd/hepta-execution-ib-paper.socket": (
        ("usr/lib/systemd/system/hepta-execution-ib-paper.socket", "0644"),),
    "systemd/hepta-execution-events-ib-paper.socket": (
        ("usr/lib/systemd/system/"
         "hepta-execution-events-ib-paper.socket", "0644"),),
    "systemd/hepta-execution-ib-paper@.service": (
        ("usr/lib/systemd/system/"
         "hepta-execution-ib-paper@.service", "0644"),),
    "systemd/hepta-execution-ib-paper@.socket": (
        ("usr/lib/systemd/system/"
         "hepta-execution-ib-paper@.socket", "0644"),),
    "systemd/hepta-execution-events-ib-paper@.socket": (
        ("usr/lib/systemd/system/"
         "hepta-execution-events-ib-paper@.socket", "0644"),),
    "systemd/hepta-broker-egress-policy.service": (
        ("usr/lib/systemd/system/"
         "hepta-broker-egress-policy.service", "0644"),),
    "systemd/hepta-p1-watch-activation.service": (
        ("usr/lib/systemd/system/hepta-p1-watch-activation.service",
         "0644"),),
    "systemd/hepta-p1-watch-activation-reconcile.service": (
        ("usr/lib/systemd/system/"
         "hepta-p1-watch-activation-reconcile.service", "0644"),),
    "systemd/hepta-p1-watch-activation-reconcile.timer": (
        ("usr/lib/systemd/system/"
         "hepta-p1-watch-activation-reconcile.timer", "0644"),),
    "systemd/hepta-ib-paper-domain-preflight@.service": (
        ("usr/lib/systemd/system/"
         "hepta-ib-paper-domain-preflight@.service", "0644"),),
    "systemd/hepta-execution-ib-paper.service.d/"
    "10-hepta-broker-egress-policy.conf": (
        ("usr/lib/systemd/system/hepta-execution-ib-paper.service.d/"
         "10-hepta-broker-egress-policy.conf", "0644"),),
    "systemd/hepta-execution-ib-paper@.service.d/"
    "10-hepta-broker-egress-policy.conf": (
        ("usr/lib/systemd/system/hepta-execution-ib-paper@.service.d/"
         "10-hepta-broker-egress-policy.conf", "0644"),),
    "tmpfiles.d/heptatrader-ib-paper.conf": (
        ("usr/lib/tmpfiles.d/heptatrader-ib-paper.conf", "0644"),),
    "docs/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md": (
        ("usr/share/doc/heptatrader/"
         "AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md", "0644"),),
    "docs/BROKER-NETWORK-ISOLATION.md": (
        ("usr/share/doc/heptatrader/"
         "BROKER-NETWORK-ISOLATION.md", "0644"),),
    "systemd/hepta-broker-network-policy-v1.json": (
        ("usr/share/heptatrader/"
         "hepta-broker-network-policy-v1.json", "0644"),),
    "systemd/hepta-agent-trust-domain-paper-identities-v1.json.example": (
        ("etc/heptatrader/"
         "hepta-agent-trust-domain-paper-identities-v1.json", "0600"),
        ("usr/local/share/hepta-agent-os-e2e/provisioning/"
         "hepta-agent-trust-domain-paper-identities-v1.json", "0600"),),
    "systemd/hepta-execution-simulator.env.example": (
        ("usr/share/doc/heptatrader/examples/"
         "hepta-execution-simulator.env.example", "0644"),
        ("usr/local/share/hepta-agent-os-e2e/provisioning/"
         "hepta-execution-simulator.env", "0644")),
    "systemd/hepta-execution-ib-paper.env.example": (
        ("usr/share/doc/heptatrader/examples/"
         "hepta-execution-ib-paper.env.example", "0644"),),
    "systemd/hepta-execution-gateway-paper.env.example": (
        ("usr/share/doc/heptatrader/examples/"
         "hepta-execution-gateway-paper.env.example", "0644"),),
    "systemd/hepta-execution-ib-paper-domain.env.example": (
        ("usr/share/doc/heptatrader/examples/"
         "hepta-execution-ib-paper-domain.env.example", "0644"),),
    "systemd/hepta-execution-gateway-paper-domain.env.example": (
        ("usr/share/doc/heptatrader/examples/"
         "hepta-execution-gateway-paper-domain.env.example", "0644"),),
    "scripts/check_hepta_execution_provisioned_host.py": (
        ("usr/local/libexec/check_hepta_execution_provisioned_host.py",
         "0755"),),
    "scripts/run_hepta_execution_rootful_systemd_gate.py": (
        ("usr/local/libexec/run_hepta_execution_rootful_systemd_gate.py",
         "0755"),),
    "scripts/run_hepta_execution_native_systemd_gate.py": (
        ("usr/local/libexec/run_hepta_execution_native_systemd_gate.py",
         "0755"),),
    "tests/rootful_systemd/hepta_execution_rootful_inner_gate.py": (
        ("usr/local/libexec/hepta_execution_rootful_inner_gate.py", "0755"),),
    "tests/agent_os_rootful_systemd/hepta_agent_os_rootful_inner_gate.py": (
        ("usr/local/libexec/hepta_agent_os_rootful_inner_gate.py", "0755"),),
    "scripts/hepta_agent_session_bootstrap.py": (
        ("usr/libexec/hepta-agent-session-bootstrap", "0755"),),
    "scripts/hepta_agent_trust_domain.py": (
        ("usr/libexec/hepta_agent_trust_domain.py", "0755"),),
    "scripts/hepta_paper_receipt_contracts.py": (
        ("usr/libexec/hepta-paper-receipt-contracts", "0755"),),
    "scripts/hepta_shadow_watch_collector.py": (
        ("usr/libexec/hepta-shadow-watch-collector", "0755"),),
    "scripts/hepta_shadow_watch_exporter.py": (
        ("usr/libexec/hepta-shadow-watch-exporter", "0755"),),
    "scripts/hepta_shadow_watch_custodian.py": (
        ("usr/libexec/hepta-shadow-watch-custodian", "0755"),),
    "scripts/hepta_shadow_host_installer.py": (
        ("usr/libexec/hepta-shadow-host-installer", "0755"),),
    "scripts/hepta_p1_shadow_host_controller.py": (
        ("usr/libexec/hepta-p1-shadow-host-controller", "0755"),),
    "scripts/hepta_p1_load_probe_validator.py": (
        ("usr/libexec/hepta-p1-load-probe-validator", "0755"),),
    "scripts/build_hepta_p1_observation_policy.py": (
        ("usr/libexec/build-hepta-p1-observation-policy", "0755"),),
    "scripts/hepta_p1_shadow_observer_controller.py": (
        ("usr/libexec/hepta-p1-shadow-observer-controller", "0755"),),
    "scripts/hepta_p1_shadow_admission_launcher.py": (
        ("usr/libexec/hepta-p1-shadow-admission-launcher", "0755"),),
    "scripts/hepta_p1_watch_profile_deployer.py": (
        ("usr/libexec/hepta-p1-watch-profile-deployer", "0755"),),
    "scripts/hepta_p1_watch_activation_transaction.py": (
        ("usr/libexec/hepta-p1-watch-activation-transaction", "0755"),),
    "scripts/hepta_bounded_shadow_closure_verifier.py": (
        ("usr/libexec/hepta-bounded-shadow-closure-verifier", "0755"),),
    "scripts/hepta_official_source_capture.py": (
        ("usr/libexec/hepta-official-source-capture", "0755"),),
    "scripts/hepta_bounded_shadow_observer.py": (
        ("usr/libexec/hepta_bounded_shadow_observer.py", "0755"),),
    "scripts/hepta_market_context_builder.py": (
        ("usr/libexec/hepta_market_context_builder.py", "0755"),),
    "scripts/hepta_market_evidence_normalizer.py": (
        ("usr/libexec/hepta_market_evidence_normalizer.py", "0755"),),
    "scripts/hepta_market_official_source_extractor.py": (
        ("usr/libexec/hepta_market_official_source_extractor.py", "0755"),),
    "scripts/hepta_eurusd_confirmed_momentum_strategy.py": (
        ("usr/libexec/hepta_eurusd_confirmed_momentum_strategy.py", "0755"),),
    "scripts/hepta_shadow_market_history.py": (
        ("usr/libexec/hepta_shadow_market_history.py", "0755"),),
    "scripts/hepta_strategy_shadow_runner.py": (
        ("usr/libexec/hepta_strategy_shadow_runner.py", "0755"),),
    "scripts/hepta_strategy_contracts.py": (
        ("usr/libexec/hepta_strategy_contracts.py", "0644"),),
    "scripts/validate_hepta_strategy_decision_receipt.py": (
        ("usr/libexec/validate_hepta_strategy_decision_receipt.py", "0755"),),
    "strategies/eurusd-confirmed-momentum-shadow-v2.json": (
        ("usr/share/heptatrader/strategies/"
         "eurusd-confirmed-momentum-shadow-v2.json", "0644"),),
    "scripts/check_hepta_agent_os_provisioned_host.py": (
        ("usr/libexec/check-hepta-agent-os-provisioned-host", "0755"),
        ("usr/local/libexec/check_hepta_agent_os_provisioned_host.py",
         "0755")),
    "systemd/hepta-tool-gateway.service": (
        ("usr/lib/systemd/system/hepta-tool-gateway.service", "0644"),),
    "systemd/hepta-tool-gateway.socket": (
        ("usr/lib/systemd/system/hepta-tool-gateway.socket", "0644"),),
    "systemd/hepta-tool-session-supervisor.socket": (
        ("usr/lib/systemd/system/hepta-tool-session-supervisor.socket",
         "0644"),),
    "systemd/hepta-tool-gateway@.service": (
        ("usr/lib/systemd/system/hepta-tool-gateway@.service", "0644"),),
    "systemd/hepta-tool-gateway@.socket": (
        ("usr/lib/systemd/system/hepta-tool-gateway@.socket", "0644"),),
    "systemd/hepta-tool-session-supervisor@.socket": (
        ("usr/lib/systemd/system/hepta-tool-session-supervisor@.socket",
         "0644"),),
    "systemd/hepta-shadow-watch-collector@.service": (
        ("usr/lib/systemd/system/hepta-shadow-watch-collector@.service",
         "0644"),),
    "systemd/hepta-shadow-watch-collector@.timer": (
        ("usr/lib/systemd/system/hepta-shadow-watch-collector@.timer",
         "0644"),),
    "systemd/hepta-shadow-watch-export@.service": (
        ("usr/lib/systemd/system/hepta-shadow-watch-export@.service",
         "0644"),),
    "systemd/hepta-shadow-watch-custodian@.service": (
        ("usr/lib/systemd/system/hepta-shadow-watch-custodian@.service",
         "0644"),),
    "systemd/hepta-shadow-watch-custodian-reconcile@.service": (
        ("usr/lib/systemd/system/"
         "hepta-shadow-watch-custodian-reconcile@.service", "0644"),),
    "systemd/hepta-shadow-watch-custodian-reconcile@.timer": (
        ("usr/lib/systemd/system/"
         "hepta-shadow-watch-custodian-reconcile@.timer", "0644"),),
    "systemd/hepta-tool-gateway-domain.env.example": (
        ("usr/share/doc/heptatrader/examples/"
         "hepta-tool-gateway-domain.env.example", "0644"),),
    "systemd/hepta-shadow-watch-domain.env.example": (
        ("usr/share/doc/heptatrader/examples/"
         "hepta-shadow-watch-domain.env.example", "0644"),),
    "tmpfiles.d/heptatrader-agent-os.conf": (
        ("usr/lib/tmpfiles.d/heptatrader-agent-os.conf", "0644"),),
    ".agents/plugins/marketplace.json": (
        ("usr/share/heptatrader/.agents/plugins/marketplace.json", "0644"),),
    "plugins/heptatrader-agent-os/.mcp.json": (
        ("usr/share/heptatrader/plugins/heptatrader-agent-os/.mcp.json",
         "0644"),),
    "plugins/heptatrader-agent-os/.codex-plugin/plugin.json": (
        ("usr/share/heptatrader/plugins/heptatrader-agent-os/"
         ".codex-plugin/plugin.json", "0644"),),
    "plugins/heptatrader-agent-os/README.md": (
        ("usr/share/heptatrader/plugins/heptatrader-agent-os/README.md",
         "0644"),),
    "systemd/hepta-agent-host-identity.conf.example": (
        ("usr/share/doc/heptatrader/examples/"
         "hepta-agent-host-identity.conf.example", "0644"),),
    "systemd/hepta-tool-gateway.env.example": (
        ("usr/share/doc/heptatrader/examples/"
         "hepta-tool-gateway.env.example", "0644"),
        ("etc/heptatrader/hepta-tool-gateway.env", "0644"),
        ("usr/local/share/hepta-agent-os-e2e/provisioning/"
         "hepta-tool-gateway.env", "0644")),
    "docs/RUNBOOK-STARTUP.md": (
        ("usr/share/doc/heptatrader/RUNBOOK-STARTUP.md", "0644"),),
    "tests/native_systemd/platform-policy-v1.json": (
        ((SHARE / "platform-policy.json").as_posix(), "0444"),),
}

REVIEWED_BUILD_SOURCE_PATHS = (
    "tests/execution_systemd_client_probe.cpp",
    "tests/execution_systemd_sandbox_probe.cpp",
)

MAX_IBAPI_SOURCE_FILES = 65536
MAX_IBAPI_SOURCE_BYTES = 2 * 1024 * 1024 * 1024
CAUSAL_BUILD_OUTPUTS = {
    "ibapi_on": (
        "hepta-executiond",
        "hepta-ib-executiond",
        "hepta_execution_systemd_client_probe",
        "hepta_execution_systemd_sandbox_probe",
        "hepta-tool-gatewayd",
        "hepta-sessionctl",
        "heptactl",
    ),
    "ibapi_off": (
        "hepta-ib-executiond-disabled",
        "hepta-tool-gatewayd",
        "hepta-sessionctl",
        "heptactl",
    ),
}
CAUSAL_BUILD_TARGETS = (
    "hepta_executiond",
    "hepta_ib_executiond",
    "hepta_execution_systemd_client_probe",
    "hepta_execution_systemd_sandbox_probe",
    "hepta_tool_gatewayd",
    "hepta_sessionctl",
    "heptactl",
)


class BundleError(RuntimeError):
    """A fail-closed native-VM payload build error."""


def fail(message: str) -> None:
    raise BundleError(message)


def canonical_json(value: Any) -> bytes:
    try:
        return secure_artifacts.canonical_json(
            value, trailing_newline=True)
    except secure_artifacts.SecureArtifactError as error:
        raise BundleError(str(error)) from error


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def strict_json(contents: bytes, label: str) -> Any:
    try:
        return json.loads(
            contents.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON value: {value}")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        fail(f"{label} is not strict UTF-8 JSON: {error}")


def _file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_nlink,
        metadata.st_uid, metadata.st_gid, metadata.st_size,
        metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def _directory_flags() -> int:
    return (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
        getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )


def _canonical_relative(value: str) -> str:
    if not value or "\0" in value or "\\" in value:
        fail("source lineage path is unsafe")
    relative = Path(value)
    if (relative.is_absolute() or relative.as_posix() != value or
            any(part in {"", ".", ".."} for part in relative.parts)):
        fail(f"source lineage path is unsafe: {value}")
    return value


def _open_relative_parent(
        root_descriptor: int, relative: str) -> tuple[int, str]:
    parts = Path(_canonical_relative(relative)).parts
    parent_descriptor = os.dup(root_descriptor)
    try:
        for component in parts[:-1]:
            child = os.open(
                component, _directory_flags(), dir_fd=parent_descriptor)
            metadata = os.fstat(child)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(child)
                fail(f"source lineage parent is not a directory: {relative}")
            os.close(parent_descriptor)
            parent_descriptor = child
        return parent_descriptor, parts[-1]
    except BaseException:
        os.close(parent_descriptor)
        raise


def stable_tree_file(
        root_descriptor: int, relative: str,
        maximum: int = 256 * 1024 * 1024,
) -> tuple[os.stat_result, bytes, str]:
    """Read an anchored source path and then re-walk its pathname."""
    parent_descriptor, name = _open_relative_parent(root_descriptor, relative)
    descriptor = -1
    try:
        before = os.stat(
            name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (stat.S_ISLNK(before.st_mode) or
                not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_size < 0 or before.st_size > maximum):
            fail(f"source lineage file metadata is unsafe: {relative}")
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
            getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        opened = os.fstat(descriptor)
        if _file_identity(before) != _file_identity(opened):
            fail(f"source lineage file changed before open: {relative}")
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                fail(f"source lineage file was truncated: {relative}")
            chunks.append(chunk)
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            fail(f"source lineage file grew while reading: {relative}")
        after = os.fstat(descriptor)
        if _file_identity(opened) != _file_identity(after):
            fail(f"source lineage file changed while reading: {relative}")
    except OSError as error:
        fail(f"cannot securely read source lineage path {relative}: {error}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)
    verification_parent, verification_name = _open_relative_parent(
        root_descriptor, relative)
    try:
        final = os.stat(
            verification_name, dir_fd=verification_parent,
            follow_symlinks=False)
    except OSError as error:
        fail(f"source lineage path disappeared: {relative}: {error}")
    finally:
        os.close(verification_parent)
    if _file_identity(after) != _file_identity(final):
        fail(f"source lineage path changed while reading: {relative}")
    return opened, b"".join(chunks), digest.hexdigest()


def _observe_ibapi_source_tree(
        root: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    files: set[str] = set()
    directories: set[str] = set()

    def walk_error(error: OSError) -> None:
        fail(f"cannot walk IBAPI_ROOT: {error}")

    for directory, child_directories, child_files in os.walk(
            root, topdown=True, followlinks=False, onerror=walk_error):
        directory_path = Path(directory)
        try:
            relative_directory = directory_path.relative_to(root)
            directory_metadata = os.lstat(directory_path)
        except (OSError, ValueError) as error:
            fail(f"IBAPI_ROOT directory changed while walking: {error}")
        directory_mode = stat.S_IMODE(directory_metadata.st_mode)
        if (not stat.S_ISDIR(directory_metadata.st_mode) or
                stat.S_ISLNK(directory_metadata.st_mode) or
                directory_mode & 0o7022):
            fail(f"IBAPI_ROOT directory metadata is unsafe: {directory_path}")
        if relative_directory != Path("."):
            relative_value = _canonical_relative(
                relative_directory.as_posix())
            if relative_value in directories:
                fail("IBAPI_ROOT contains a duplicate directory path")
            directories.add(relative_value)
        child_directories.sort()
        child_files.sort()
        for name in child_directories:
            child = directory_path / name
            try:
                metadata = os.lstat(child)
            except OSError as error:
                fail(f"IBAPI_ROOT directory changed while walking: {error}")
            if (stat.S_ISLNK(metadata.st_mode) or
                    not stat.S_ISDIR(metadata.st_mode)):
                fail(f"IBAPI_ROOT contains a symlink or non-directory: {child}")
            _canonical_relative(child.relative_to(root).as_posix())
        for name in child_files:
            child = directory_path / name
            relative = _canonical_relative(
                child.relative_to(root).as_posix())
            try:
                metadata = os.lstat(child)
            except OSError as error:
                fail(f"IBAPI_ROOT file changed while walking: {error}")
            mode = stat.S_IMODE(metadata.st_mode)
            if (stat.S_ISLNK(metadata.st_mode) or
                    not stat.S_ISREG(metadata.st_mode) or
                    metadata.st_nlink != 1 or mode & 0o7022 or
                    not mode & 0o400):
                fail(f"IBAPI_ROOT contains an unsafe regular file: {child}")
            if relative in files:
                fail("IBAPI_ROOT contains a duplicate file path")
            files.add(relative)
            if len(files) > MAX_IBAPI_SOURCE_FILES:
                fail("IBAPI_ROOT contains too many files")
    if not files:
        fail("IBAPI_ROOT source closure is empty")
    return tuple(sorted(files)), tuple(sorted(directories))


def scan_ibapi_source_tree(
        ibapi_root: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Build a stable byte-level manifest of the exact external SDK tree."""
    try:
        root_metadata = os.lstat(ibapi_root)
        root_descriptor = os.open(ibapi_root, _directory_flags())
    except OSError as error:
        fail(f"cannot securely open IBAPI_ROOT: {error}")
    try:
        opened_root = os.fstat(root_descriptor)
        if (_file_identity(root_metadata) != _file_identity(opened_root) or
                not stat.S_ISDIR(opened_root.st_mode) or
                stat.S_ISLNK(root_metadata.st_mode)):
            fail("IBAPI_ROOT changed before secure open")
        observed_files, observed_directories = _observe_ibapi_source_tree(
            ibapi_root)
        records: list[dict[str, Any]] = []
        total_size = 0
        for relative in observed_files:
            metadata, contents, digest = stable_tree_file(
                root_descriptor, relative)
            mode = stat.S_IMODE(metadata.st_mode)
            total_size += len(contents)
            if total_size > MAX_IBAPI_SOURCE_BYTES:
                fail("IBAPI_ROOT source closure exceeds the size limit")
            records.append({
                "path": relative,
                "mode": format(mode, "04o"),
                "size": len(contents),
                "sha256": digest,
            })

        final_files, final_directories = _observe_ibapi_source_tree(ibapi_root)
        if (final_files != observed_files or
                final_directories != observed_directories):
            fail("IBAPI_ROOT file or directory closure changed while scanning")
        for expected in records:
            metadata, contents, digest = stable_tree_file(
                root_descriptor, expected["path"])
            if (format(stat.S_IMODE(metadata.st_mode), "04o") !=
                    expected["mode"] or len(contents) != expected["size"] or
                    digest != expected["sha256"]):
                fail(f"IBAPI_ROOT file changed while scanning: "
                     f"{expected['path']}")
        final_root = os.lstat(ibapi_root)
        if _file_identity(opened_root) != _file_identity(final_root):
            fail("IBAPI_ROOT changed while scanning")
    finally:
        os.close(root_descriptor)

    files_sha256 = hashlib.sha256(json.dumps(
        records, ensure_ascii=True, separators=(",", ":"),
        sort_keys=True).encode("utf-8")).hexdigest()
    manifest = {
        "schema": IBAPI_SOURCE_MANIFEST_SCHEMA,
        "root": ibapi_root.name,
        "file_count": len(records),
        "files_sha256": files_sha256,
        "files": records,
    }
    return manifest, {record["path"]: record for record in records}


def validate_ibapi_source_tree_unchanged(
        ibapi_root: Path, expected_manifest: dict[str, Any]) -> None:
    observed_manifest, _observed_index = scan_ibapi_source_tree(ibapi_root)
    if observed_manifest != expected_manifest:
        fail("IBAPI_ROOT source closure changed while bundling")


def parse_clean_source_manifest(
        manifest_bytes: bytes, source_provenance: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest = strict_json(manifest_bytes, "clean-source manifest")
    if (not isinstance(manifest, dict) or
            manifest.get("schema") != "hepta.clean-source-bundle.v2" or
            manifest.get("bundle_class") != "strict-source-only" or
            not isinstance(manifest.get("root"), str) or
            manifest.get("root") !=
            f"heptatrader-{manifest.get('version', '')}" or
            hashlib.sha256(manifest_bytes).hexdigest() !=
            source_provenance.get("manifest_sha256")):
        fail("clean-source manifest identity mismatch")
    entries = manifest.get("files")
    if (not isinstance(entries, list) or
            manifest.get("file_count") != len(entries) or
            manifest.get("file_count") !=
            source_provenance.get("file_count") or
            manifest.get("version") != source_provenance.get("version") or
            manifest.get("git_head") != source_provenance.get("git_head") or
            manifest.get("files_sha256") !=
            source_provenance.get("files_sha256")):
        fail("clean-source manifest closure identity mismatch")
    canonical_entries = json.dumps(
        entries, ensure_ascii=True, separators=(",", ":"),
        sort_keys=True).encode("utf-8")
    if hashlib.sha256(canonical_entries).hexdigest() != manifest["files_sha256"]:
        fail("clean-source manifest file-list digest mismatch")
    index: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {
                "path", "mode", "size", "sha256"}):
            fail("clean-source manifest file record is invalid")
        path = _canonical_relative(entry.get("path", ""))
        if (path in index or entry.get("mode") not in {"0644", "0755"} or
                type(entry.get("size")) is not int or entry["size"] < 0 or
                not isinstance(entry.get("sha256"), str) or
                len(entry["sha256"]) != 64):
            fail("clean-source manifest file record metadata is invalid")
        index[path] = entry
    if len(index) != len(entries):
        fail("clean-source manifest contains duplicate paths")
    return manifest, index


def read_verified_clean_source_manifest(
        manifest_path: Path, source_provenance: dict[str, Any],
) -> tuple[bytes, dict[str, Any], dict[str, dict[str, Any]]]:
    try:
        manifest_bytes = clean_source.stable_private_bytes(
            manifest_path, "external source bundle manifest",
            clean_source.MAX_MANIFEST_BYTES)
    except SystemExit as error:
        fail(str(error))
    manifest, index = parse_clean_source_manifest(
        manifest_bytes, source_provenance)
    return manifest_bytes, manifest, index


def scan_exact_source_tree(
        source_root: Path,
        manifest_bytes: bytes,
        manifest: dict[str, Any],
        source_index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Prove a no-git extracted tree is exactly the verified tar closure."""
    root = source_root.resolve(strict=True)
    if root.name != manifest["root"] or root.is_symlink():
        fail("CMAKE_HOME_DIRECTORY is not the canonical clean-source root")
    if (root / ".git").exists() or (root / ".git").is_symlink():
        fail("CMAKE_HOME_DIRECTORY must be a no-git clean-source tree")
    observed_files: set[str] = set()
    observed_directories: set[str] = set()
    for directory, child_directories, files in os.walk(
            root, topdown=True, followlinks=False):
        directory_path = Path(directory)
        for name in child_directories:
            child = directory_path / name
            metadata = os.lstat(child)
            if not stat.S_ISDIR(metadata.st_mode):
                fail(f"clean-source tree has an unsafe ancestor: {child}")
            observed_directories.add(child.relative_to(root).as_posix())
        for name in files:
            child = directory_path / name
            metadata = os.lstat(child)
            if (not stat.S_ISREG(metadata.st_mode) or
                    stat.S_ISLNK(metadata.st_mode) or metadata.st_nlink != 1):
                fail(f"clean-source tree has an unsafe file: {child}")
            observed_files.add(child.relative_to(root).as_posix())
    internal = ".hepta/source-bundle-manifest.json"
    expected_files = set(source_index) | {internal}
    expected_directories = {
        parent.as_posix()
        for relative in expected_files
        for parent in Path(relative).parents
        if parent != Path(".")
    }
    if observed_files != expected_files:
        extra = sorted(observed_files - expected_files)
        missing = sorted(expected_files - observed_files)
        fail("clean-source tree file closure mismatch "
             f"extra={extra[:3]} missing={missing[:3]}")
    if observed_directories != expected_directories:
        fail("clean-source tree directory closure mismatch")
    root_descriptor = os.open(root, _directory_flags())
    try:
        for relative, expected in sorted(source_index.items()):
            metadata, contents, digest = stable_tree_file(
                root_descriptor, relative)
            if (format(stat.S_IMODE(metadata.st_mode), "04o") !=
                    expected["mode"] or len(contents) != expected["size"] or
                    digest != expected["sha256"]):
                fail(f"clean-source tree file drift: {relative}")
        internal_metadata, internal_bytes, internal_sha256 = stable_tree_file(
            root_descriptor, internal, maximum=clean_source.MAX_MANIFEST_BYTES)
    finally:
        os.close(root_descriptor)
    if (stat.S_IMODE(internal_metadata.st_mode) != 0o644 or
            internal_bytes != manifest_bytes):
        fail("no-git internal source-bundle manifest differs from input")
    return {
        "root": manifest["root"],
        "file_count": manifest["file_count"],
        "files_sha256": manifest["files_sha256"],
        "manifest_sha256": internal_sha256,
        "internal_manifest_path": internal,
        "internal_manifest_mode": "0644",
        "git_metadata_present": False,
        "exact_file_closure": True,
    }


def validate_repository_source_inputs(
        repository: Path,
        source_index: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    root = repository.resolve(strict=True)
    root_descriptor = os.open(root, _directory_flags())
    staged_records: list[dict[str, Any]] = []
    reviewed_records: list[dict[str, Any]] = []
    try:
        for relative, destinations in sorted(SOURCE_STAGE_BINDINGS.items()):
            expected = source_index.get(relative)
            if expected is None:
                fail(f"staged repository source is absent from clean bundle: "
                     f"{relative}")
            metadata, contents, digest = stable_tree_file(
                root_descriptor, relative)
            if (format(stat.S_IMODE(metadata.st_mode), "04o") !=
                    expected["mode"] or len(contents) != expected["size"] or
                    digest != expected["sha256"]):
                fail(f"staged repository source differs from clean bundle: "
                     f"{relative}")
            staged_records.append({
                "path": relative,
                "mode": expected["mode"],
                "size": expected["size"],
                "sha256": expected["sha256"],
                "destinations": [
                    {"path": path, "mode": mode}
                    for path, mode in destinations
                ],
            })
        for relative in REVIEWED_BUILD_SOURCE_PATHS:
            expected = source_index.get(relative)
            if expected is None:
                fail(f"reviewed build source is absent from clean bundle: "
                     f"{relative}")
            metadata, contents, digest = stable_tree_file(
                root_descriptor, relative)
            if (format(stat.S_IMODE(metadata.st_mode), "04o") !=
                    expected["mode"] or len(contents) != expected["size"] or
                    digest != expected["sha256"]):
                fail(f"reviewed build source differs from clean bundle: "
                     f"{relative}")
            reviewed_records.append(dict(expected))
    finally:
        os.close(root_descriptor)
    return staged_records, reviewed_records


def validate_platform_policy_source(
        platform_policy: Path, source_index: dict[str, dict[str, Any]],
) -> None:
    expected = source_index["tests/native_systemd/platform-policy-v1.json"]
    metadata, contents, digest = shared.read_regular_file(
        platform_policy, maximum=1024 * 1024)
    if (format(stat.S_IMODE(metadata.st_mode), "04o") != expected["mode"] or
            len(contents) != expected["size"] or digest != expected["sha256"]):
        fail("--platform-policy differs from the clean-source manifest")


def parse_cmake_cache_bytes(contents: bytes, label: str) -> dict[str, str]:
    try:
        text = contents.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        fail(f"{label} is not valid UTF-8")
    values: dict[str, str] = {}
    for raw in text.splitlines():
        if not raw or raw.startswith(("#", "//")) or "=" not in raw:
            continue
        left, value = raw.split("=", 1)
        if ":" not in left:
            continue
        key, _kind = left.split(":", 1)
        if key in values:
            fail(f"{label} contains duplicate CMake cache key: {key}")
        values[key] = value
    return values


def _resolve_compile_path(value: Any, directory: Path, label: str) -> Path:
    if not isinstance(value, str) or not value or "\0" in value:
        fail(f"{label} path is invalid")
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = directory / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        fail(f"{label} path is unavailable: {candidate}")
    return resolved


def validate_compile_commands(
        contents: bytes,
        *,
        build: Path,
        source_root: Path,
        source_index: dict[str, dict[str, Any]],
        ibapi: bool,
        ibapi_root: Optional[Path],
        ibapi_source_index: Optional[dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    commands = strict_json(contents, "compile_commands.json")
    if not isinstance(commands, list) or not commands:
        fail("compile_commands.json must be a non-empty array")
    records: list[dict[str, str]] = []
    clean_count = 0
    ibapi_count = 0
    if ((ibapi_root is None) != (ibapi_source_index is None) or
            (ibapi and (ibapi_root is None or not ibapi_source_index)) or
            (not ibapi and (ibapi_root is not None or
                            ibapi_source_index is not None))):
        fail("IBAPI compile source manifest profile mismatch")
    ibapi_descriptor = (
        os.open(ibapi_root, _directory_flags())
        if ibapi_root is not None else -1)
    try:
        for item in commands:
            if (not isinstance(item, dict) or
                    not {"directory", "file"}.issubset(item) or
                    ("command" in item) == ("arguments" in item)):
                fail("compile_commands.json entry contract mismatch")
            directory = _resolve_compile_path(
                item["directory"], build, "compile directory")
            try:
                directory.relative_to(build)
            except ValueError:
                fail("compile command directory escapes its exact build tree")
            source = _resolve_compile_path(
                item["file"], directory, "compile source")
            try:
                relative = source.relative_to(source_root).as_posix()
                expected = source_index.get(relative)
                if expected is None:
                    fail(f"compile source is absent from clean bundle: "
                         f"{relative}")
                source_id = "source/" + relative
                source_digest = expected["sha256"]
                clean_count += 1
            except ValueError:
                if (not ibapi or ibapi_root is None or
                        ibapi_source_index is None):
                    fail(f"external compile source is forbidden: {source}")
                try:
                    ibapi_relative = _canonical_relative(
                        source.relative_to(ibapi_root).as_posix())
                except ValueError:
                    fail(f"compile source escapes clean source and "
                         f"IBAPI_ROOT: {source}")
                expected = ibapi_source_index.get(ibapi_relative)
                if expected is None:
                    fail(f"IBAPI compile source is absent from the exact SDK "
                         f"manifest: {ibapi_relative}")
                metadata, source_bytes, source_digest = stable_tree_file(
                    ibapi_descriptor, ibapi_relative)
                if (format(stat.S_IMODE(metadata.st_mode), "04o") !=
                        expected["mode"] or len(source_bytes) !=
                        expected["size"] or source_digest !=
                        expected["sha256"] or not source_bytes):
                    fail(f"IBAPI compile source differs from the exact SDK "
                         f"manifest: {ibapi_relative}")
                source_id = "ibapi/" + ibapi_relative
                ibapi_count += 1
            tokens: list[str]
            if "arguments" in item:
                if (not isinstance(item["arguments"], list) or
                        not all(isinstance(value, str)
                                for value in item["arguments"])):
                    fail("compile command arguments are invalid")
                tokens = item["arguments"]
            else:
                try:
                    tokens = shlex.split(item["command"], posix=True)
                except (TypeError, ValueError):
                    fail("compile command string is invalid")
            token_sources: set[Path] = set()
            for token in tokens:
                candidate = Path(token)
                if not candidate.is_absolute() and not token.startswith("."):
                    continue
                try:
                    token_sources.add(
                        (candidate if candidate.is_absolute()
                         else directory / candidate).resolve(strict=True))
                except OSError:
                    continue
            if source not in token_sources:
                fail("compile command does not compile its declared source "
                     "file")
            records.append({"path": source_id, "sha256": source_digest})
    finally:
        if ibapi_descriptor >= 0:
            os.close(ibapi_descriptor)
    if clean_count <= 0 or (ibapi and ibapi_count <= 0) or (
            not ibapi and ibapi_count != 0):
        fail("compile source lineage counts do not match the IBAPI profile")
    canonical = json.dumps(
        sorted(records, key=lambda record: (
            record["path"], record["sha256"])),
        ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return {
        "translation_unit_count": len(commands),
        "clean_source_translation_unit_count": clean_count,
        "ibapi_translation_unit_count": ibapi_count,
        "compile_sources_sha256": hashlib.sha256(canonical).hexdigest(),
    }


def validate_lineage_build(
        build_argument: Path,
        *,
        ibapi: bool,
        source_root: Path,
        source_provenance: dict[str, Any],
        source_index: dict[str, dict[str, Any]],
) -> tuple[Path, dict[str, Any], dict[str, Path]]:
    try:
        build = build_argument.resolve(strict=True)
    except OSError:
        fail(f"build directory is unavailable: {build_argument}")
    if not build.is_dir() or build.is_symlink():
        fail(f"build directory must be a real directory: {build}")
    cache_path = build / "CMakeCache.txt"
    compile_path = build / "compile_commands.json"
    _cache_metadata, cache_contents, cache_sha256 = shared.read_regular_file(
        cache_path, maximum=8 * 1024 * 1024)
    _compile_metadata, compile_contents, compile_sha256 = (
        shared.read_regular_file(compile_path, maximum=32 * 1024 * 1024))
    values = parse_cmake_cache_bytes(cache_contents, str(cache_path))
    raw_home = values.get("CMAKE_HOME_DIRECTORY", "")
    raw_cache = values.get("CMAKE_CACHEFILE_DIR", "")
    try:
        configured_source = Path(raw_home).resolve(strict=True)
        configured_build = Path(raw_cache).resolve(strict=True)
    except OSError:
        fail(f"{build}: CMake source/build directory is unavailable")
    if (configured_source != source_root or
            Path(os.path.abspath(raw_home)) != source_root):
        fail(f"{build}: CMAKE_HOME_DIRECTORY is not the exact clean-source tree")
    if (configured_build != build or
            Path(os.path.abspath(raw_cache)) != build):
        fail(f"{build}: CMAKE_CACHEFILE_DIR is not the exact build tree")
    expected = {
        "CMAKE_BUILD_TYPE": "Release",
        "BUILD_TESTING": "ON",
        "CMAKE_EXPORT_COMPILE_COMMANDS": "ON",
        "HEPTA_ENABLE_LEGACY_0DTE_BRIDGE": "OFF",
        "HEPTA_ENABLE_IBAPI": "ON" if ibapi else "OFF",
    }
    for key, required in expected.items():
        if values.get(key, "").upper() != required.upper():
            fail(f"{build}: {key} must be exactly {required}")
    raw_ibapi_root = values.get("IBAPI_ROOT", "")
    ibapi_root: Optional[Path] = None
    ibapi_source_manifest: Optional[dict[str, Any]] = None
    ibapi_source_index: Optional[dict[str, dict[str, Any]]] = None
    if ibapi:
        try:
            ibapi_root = Path(raw_ibapi_root).resolve(strict=True)
        except OSError:
            fail(f"{build}: IBAPI_ROOT is unavailable")
        if (not ibapi_root.is_dir() or ibapi_root.is_symlink() or
                Path(os.path.abspath(raw_ibapi_root)) != ibapi_root):
            fail(f"{build}: IBAPI_ROOT must be a real canonical directory")
        ibapi_source_manifest, ibapi_source_index = scan_ibapi_source_tree(
            ibapi_root)
    elif raw_ibapi_root:
        fail(f"{build}: IBAPI_ROOT must be empty when IBAPI is disabled")
    compile_lineage = validate_compile_commands(
        compile_contents, build=build, source_root=source_root,
        source_index=source_index, ibapi=ibapi, ibapi_root=ibapi_root,
        ibapi_source_index=ibapi_source_index)
    ibapi_manifest_sha256 = (
        hashlib.sha256(canonical_json(ibapi_source_manifest)).hexdigest()
        if ibapi_source_manifest is not None else None)
    evidence_key = "ibapi_on" if ibapi else "ibapi_off"
    record = {
        "path": build.name,
        "source_root": source_root.name,
        "source_manifest_sha256": source_provenance["manifest_sha256"],
        "source_files_sha256": source_provenance["files_sha256"],
        "source_file_count": source_provenance["file_count"],
        "cmake_cache_path":
            BUILD_EVIDENCE_PATHS[evidence_key]["cmake_cache"].as_posix(),
        "cmake_cache_sha256": cache_sha256,
        "compile_commands_path":
            BUILD_EVIDENCE_PATHS[evidence_key]["compile_commands"].as_posix(),
        "compile_commands_sha256": compile_sha256,
        "build_type": "Release",
        "ibapi_enabled": ibapi,
        "ibapi_source_manifest": ibapi_source_manifest,
        "ibapi_source_manifest_sha256": ibapi_manifest_sha256,
        "ibapi_source_file_count": (
            ibapi_source_manifest["file_count"]
            if ibapi_source_manifest is not None else 0),
        "ibapi_source_files_sha256": (
            ibapi_source_manifest["files_sha256"]
            if ibapi_source_manifest is not None else None),
        "generator": values.get("CMAKE_GENERATOR", ""),
        "compiler": Path(values.get("CMAKE_CXX_COMPILER", "")).name,
        **compile_lineage,
    }
    local = {
        "cmake_cache": cache_path,
        "compile_commands": compile_path,
    }
    if ibapi_root is not None:
        local["ibapi_root"] = ibapi_root
    return build, record, local


def _secure_tool(path_value: str, role: str) -> tuple[Path, dict[str, Any],
                                                       dict[str, Any]]:
    if not isinstance(path_value, str) or not path_value or "\0" in path_value:
        fail(f"causal build {role} path is invalid")
    try:
        path = Path(path_value).resolve(strict=True)
    except OSError:
        fail(f"causal build {role} is unavailable")
    metadata, _contents, digest = shared.read_regular_file(
        path, executable=True, maximum=256 * 1024 * 1024)
    if (metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o022):
        fail(f"causal build {role} is not root-owned immutable host input")
    record = {
        "role": role,
        "path": path.as_posix(),
        "mode": format(stat.S_IMODE(metadata.st_mode), "04o"),
        "size": metadata.st_size,
        "sha256": digest,
    }
    local = {
        "path": path,
        "identity": _file_identity(metadata),
        "sha256": digest,
    }
    return path, record, local


def _causal_environment(build_root: Path) -> tuple[dict[str, str],
                                                    dict[str, str]]:
    home = build_root / ".home"
    temporary = build_root / ".tmp"
    home.mkdir(mode=0o700, exist_ok=True)
    temporary.mkdir(mode=0o700, exist_ok=True)
    for path in (home, temporary):
        metadata = os.lstat(path)
        if (not stat.S_ISDIR(metadata.st_mode) or
                stat.S_IMODE(metadata.st_mode) != 0o700 or
                metadata.st_uid != os.geteuid() or
                metadata.st_gid != os.getegid()):
            fail("causal build private environment directory is unsafe")
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": home.as_posix(),
        "TMPDIR": temporary.as_posix(),
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
        "SOURCE_DATE_EPOCH": "0",
        "CFLAGS": "",
        "CXXFLAGS": "",
        "LDFLAGS": "",
    }
    receipt = dict(environment)
    receipt["HOME"] = "$CAUSAL_ROOT/.home"
    receipt["TMPDIR"] = "$CAUSAL_ROOT/.tmp"
    return environment, receipt


def _normalized_configure_argv(
        generator: str, *, ibapi: bool) -> list[str]:
    return [
        "$CMAKE", "-S", "$SOURCE_ROOT", "-B", "$BUILD_ROOT",
        "-G", generator,
        "-DCMAKE_BUILD_TYPE=Release",
        "-DBUILD_TESTING=ON",
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        "-DHEPTA_ENABLE_LEGACY_0DTE_BRIDGE=OFF",
        f"-DHEPTA_ENABLE_IBAPI={'ON' if ibapi else 'OFF'}",
        ("-DIBAPI_ROOT=$IBAPI_ROOT" if ibapi else "-DIBAPI_ROOT="),
        "-DCMAKE_C_COMPILER=$C_COMPILER",
        "-DCMAKE_CXX_COMPILER=$CXX_COMPILER",
        "-DCMAKE_MAKE_PROGRAM=$BUILD_PROGRAM",
    ]


def _actual_configure_argv(
        cmake: Path, source_root: Path, build_root: Path, generator: str,
        c_compiler: Path, cxx_compiler: Path, build_program: Path, *,
        ibapi: bool, ibapi_root: Optional[Path]) -> list[str]:
    result = _normalized_configure_argv(generator, ibapi=ibapi)
    substitutions = {
        "$CMAKE": cmake.as_posix(),
        "$SOURCE_ROOT": source_root.as_posix(),
        "$BUILD_ROOT": build_root.as_posix(),
        "$C_COMPILER": c_compiler.as_posix(),
        "$CXX_COMPILER": cxx_compiler.as_posix(),
        "$BUILD_PROGRAM": build_program.as_posix(),
        "$IBAPI_ROOT": (
            ibapi_root.as_posix() if ibapi_root is not None else ""),
    }
    return [
        next((value.replace(key, replacement)
              for key, replacement in substitutions.items() if key in value),
             value)
        for value in result
    ]


def _causal_output_records(
        build: Path, artifact_names: tuple[str, ...]) -> tuple[
            list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    local: list[dict[str, Any]] = []
    for artifact in artifact_names:
        path = shared.find_binary(build, artifact)
        metadata, contents, digest = shared.read_regular_file(
            path, executable=True)
        if not contents.startswith(b"\x7fELF"):
            fail(f"fresh causal build output is not ELF: {artifact}")
        if stat.S_IMODE(metadata.st_mode) != 0o755:
            fail(f"causal build output mode is not canonical 0755: {artifact}")
        records.append({
            "artifact": artifact,
            "build_path": path.relative_to(build).as_posix(),
            "mode": format(stat.S_IMODE(metadata.st_mode), "04o"),
            "size": metadata.st_size,
            "sha256": digest,
        })
        local.append({
            "path": path,
            "identity": _file_identity(metadata),
            "sha256": digest,
        })
    return sorted(records, key=lambda item: item["artifact"]), local


def fresh_causal_rebuild_lane(
        profile_build: Path,
        fresh_build: Path,
        *,
        source_root: Path,
        ibapi: bool,
        ibapi_root: Optional[Path],
        source_manifest_sha256: str,
        ibapi_source_manifest_sha256: Optional[str],
        artifact_names: tuple[str, ...],
        build_targets: tuple[str, ...] = CAUSAL_BUILD_TARGETS,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Rebuild one lane in an empty owned tree and reject stale inputs.

    The supplied build is only a reviewed profile and an independent output
    cross-check.  All bytes admitted to native evidence come from fresh_build.
    """
    if fresh_build.exists() or fresh_build.is_symlink():
        fail("causal build directory must not pre-exist")
    fresh_build.mkdir(mode=0o700)
    cache_contents = shared.read_regular_file(
        profile_build / "CMakeCache.txt", maximum=8 * 1024 * 1024)[1]
    profile = parse_cmake_cache_bytes(
        cache_contents, str(profile_build / "CMakeCache.txt"))
    generator = profile.get("CMAKE_GENERATOR", "")
    if not generator:
        fail("causal build profile has no exact generator")
    cmake_raw = shutil.which("cmake", path="/usr/bin:/bin")
    if cmake_raw is None:
        fail("causal build requires cmake in the reviewed host tool path")
    cmake, cmake_record, cmake_local = _secure_tool(cmake_raw, "cmake")
    c_compiler, c_record, c_local = _secure_tool(
        profile.get("CMAKE_C_COMPILER", ""), "c_compiler")
    cxx_compiler, cxx_record, cxx_local = _secure_tool(
        profile.get("CMAKE_CXX_COMPILER", ""), "cxx_compiler")
    build_program, program_record, program_local = _secure_tool(
        profile.get("CMAKE_MAKE_PROGRAM", ""), "build_program")
    causal_root = fresh_build.parent
    environment, environment_record = _causal_environment(causal_root)
    configure_argv = _actual_configure_argv(
        cmake, source_root, fresh_build, generator, c_compiler,
        cxx_compiler, build_program, ibapi=ibapi, ibapi_root=ibapi_root)
    configure = shared.command(
        configure_argv, timeout=900, environment=environment)
    fresh_cache = parse_cmake_cache_bytes(
        shared.read_regular_file(
            fresh_build / "CMakeCache.txt", maximum=8 * 1024 * 1024)[1],
        str(fresh_build / "CMakeCache.txt"))
    for key, expected in (
            ("CMAKE_C_COMPILER", c_compiler),
            ("CMAKE_CXX_COMPILER", cxx_compiler),
            ("CMAKE_MAKE_PROGRAM", build_program)):
        try:
            observed = Path(fresh_cache.get(key, "")).resolve(strict=True)
        except OSError:
            fail(f"fresh causal build omitted {key}")
        if observed != expected:
            fail(f"fresh causal build drifted {key}")

    tool_records = [cmake_record, c_record, cxx_record, program_record]
    tool_locals = [cmake_local, c_local, cxx_local, program_local]
    optional_tools = (
        ("CMAKE_AR", "ar"),
        ("CMAKE_LINKER", "linker"),
        ("CMAKE_NM", "nm"),
        ("CMAKE_OBJCOPY", "objcopy"),
        ("CMAKE_OBJDUMP", "objdump"),
        ("CMAKE_RANLIB", "ranlib"),
        ("CMAKE_STRIP", "strip"),
    )
    for key, role in optional_tools:
        raw = fresh_cache.get(key, "")
        if not raw:
            continue
        _path, record, local = _secure_tool(raw, role)
        tool_records.append(record)
        tool_locals.append(local)
    if len({record["role"] for record in tool_records}) != len(tool_records):
        fail("fresh causal toolchain contains duplicate roles")

    build_argv = [
        cmake.as_posix(), "--build", fresh_build.as_posix(),
        "--config", "Release", "--parallel", "1", "--target",
        *build_targets,
    ]
    build = shared.command(build_argv, timeout=3600, environment=environment)
    # The host's interactive umask must not leak into signed native-VM
    # lineage.  These files are newly created inside our private causal tree;
    # normalize them before hashing and require supplied profile artifacts to
    # have the same canonical mode.
    for artifact in artifact_names:
        os.chmod(shared.find_binary(fresh_build, artifact), 0o755)
    fresh_outputs, output_locals = _causal_output_records(
        fresh_build, artifact_names)
    supplied_outputs, _supplied_locals = _causal_output_records(
        profile_build, artifact_names)
    if fresh_outputs != supplied_outputs:
        fail("prebuilt artifact differs from exact fresh causal rebuild")

    configure_log = configure.stdout.encode("utf-8", errors="replace")
    build_log = build.stdout.encode("utf-8", errors="replace")
    receipt = {
        "schema": CAUSAL_BUILD_RECEIPT_SCHEMA,
        "fresh_build_directory_created_empty": True,
        "prebuilt_artifacts_exactly_matched": True,
        "source_manifest_sha256": source_manifest_sha256,
        "ibapi_source_manifest_sha256": ibapi_source_manifest_sha256,
        "configure_argv": _normalized_configure_argv(
            generator, ibapi=ibapi),
        "build_argv": [
            "$CMAKE", "--build", "$BUILD_ROOT", "--config", "Release",
            "--parallel", "1", "--target", *build_targets,
        ],
        "environment": environment_record,
        "toolchain": sorted(tool_records, key=lambda item: item["role"]),
        "configure_log_size": len(configure_log),
        "configure_log_sha256": hashlib.sha256(configure_log).hexdigest(),
        "build_log_size": len(build_log),
        "build_log_sha256": hashlib.sha256(build_log).hexdigest(),
        "outputs": fresh_outputs,
    }
    return receipt, tool_locals, output_locals


def revalidate_causal_tools(local_records: list[dict[str, Any]]) -> None:
    for record in local_records:
        metadata, _contents, digest = shared.read_regular_file(
            record["path"], executable=True, maximum=256 * 1024 * 1024)
        if (_file_identity(metadata) != record["identity"] or
                digest != record["sha256"]):
            fail(f"causal build tool changed: {record['path']}")


def _build_binary_record(
        path: Path, build: Path, artifact: str, build_key: str,
        destinations: list[str], *, cross_build: Optional[str] = None,
        cross_build_path: Optional[Path] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    metadata, contents, digest = shared.read_regular_file(
        path, executable=True)
    if not contents.startswith(b"\x7fELF"):
        fail(f"build lineage artifact is not ELF: {path}")
    record = {
        "artifact": artifact,
        "build": build_key,
        "build_path": path.relative_to(build).as_posix(),
        "destinations": sorted(destinations),
        "mode": "0755",
        "size": metadata.st_size,
        "sha256": digest,
        "cross_build": cross_build,
        "cross_build_path": (
            cross_build_path.as_posix() if cross_build_path is not None
            else None),
    }
    local = {
        "path": path,
        "identity": _file_identity(metadata),
        "sha256": digest,
    }
    return record, local


def build_binary_lineage(
        variant: str,
        ib_build: Path,
        disabled_build: Path,
        simulator: Path,
        formal_ib: Path,
        disabled: Path,
        client_probe: Path,
        sandbox_probe: Path,
        agent_binaries: dict[str, tuple[Path, Path, str]],
) -> tuple[list[dict[str, Any]], dict[str, Any],
           list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    local_records: list[dict[str, Any]] = []

    def add(
            path: Path, build: Path, artifact: str, build_key: str,
            destinations: list[str], *, cross_build: Optional[str] = None,
            cross_build_path: Optional[Path] = None) -> None:
        record, local = _build_binary_record(
            path, build, artifact, build_key, destinations,
            cross_build=cross_build, cross_build_path=cross_build_path)
        records.append(record)
        local_records.append(local)

    add(simulator, ib_build, "hepta-executiond", "ibapi_on",
        ["usr/libexec/hepta-executiond"])
    disabled_destinations = [
        "usr/local/libexec/hepta-ib-executiond-disabled"]
    sandbox_destinations = [
        "usr/local/libexec/hepta_execution_systemd_sandbox_probe"]
    if variant == "sandbox":
        sandbox_destinations.append("usr/libexec/hepta-ib-executiond")
    else:
        disabled_destinations.append("usr/libexec/hepta-ib-executiond")
    add(disabled, disabled_build, "hepta-ib-executiond-disabled",
        "ibapi_off", disabled_destinations)
    add(client_probe, ib_build, "hepta_execution_systemd_client_probe",
        "ibapi_on",
        ["usr/local/libexec/hepta_execution_systemd_client_probe"])
    add(sandbox_probe, ib_build, "hepta_execution_systemd_sandbox_probe",
        "ibapi_on", sandbox_destinations)
    for name, destination in (
            ("hepta-tool-gatewayd", "usr/libexec/hepta-tool-gatewayd"),
            ("hepta-sessionctl", "usr/bin/hepta-sessionctl"),
            ("heptactl", "usr/bin/heptactl")):
        enabled_path, disabled_path, _digest = agent_binaries[name]
        add(
            enabled_path, ib_build, name, "ibapi_on", [destination],
            cross_build="ibapi_off",
            cross_build_path=disabled_path.relative_to(disabled_build))
        disabled_metadata, _disabled_contents, disabled_sha256 = (
            shared.read_regular_file(disabled_path, executable=True))
        if disabled_sha256 != _digest:
            fail(f"venue-neutral Agent OS binary cross-build drift: {name}")
        local_records.append({
            "path": disabled_path,
            "identity": _file_identity(disabled_metadata),
            "sha256": disabled_sha256,
        })
    formal_metadata, formal_contents, formal_sha256 = shared.read_regular_file(
        formal_ib, executable=True)
    if not formal_contents.startswith(b"\x7fELF"):
        fail("formal IBAPI build artifact is not ELF")
    formal = {
        "artifact": "hepta-ib-executiond",
        "build": "ibapi_on",
        "build_path": formal_ib.relative_to(ib_build).as_posix(),
        "size": formal_metadata.st_size,
        "sha256": formal_sha256,
        "digest_path": (SHARE / "formal-ibapi.sha256").as_posix(),
        "elf_staged": False,
    }
    local_records.append({
        "path": formal_ib,
        "identity": _file_identity(formal_metadata),
        "sha256": formal_sha256,
    })
    return sorted(records, key=lambda item: item["artifact"]), formal, local_records


def validate_staged_lineage_payloads(
        rootfs: Path,
        staged_sources: list[dict[str, Any]],
        staged_binaries: list[dict[str, Any]],
) -> None:
    for source in staged_sources:
        for destination in source["destinations"]:
            path = rootfs / destination["path"]
            metadata, contents, digest = shared.read_regular_file(path)
            if (format(stat.S_IMODE(metadata.st_mode), "04o") !=
                    destination["mode"] or len(contents) != source["size"] or
                    digest != source["sha256"]):
                fail("staged rootfs source differs from clean-source lineage: "
                     f"{destination['path']}")
    for binary in staged_binaries:
        for destination in binary["destinations"]:
            path = rootfs / destination
            metadata, contents, digest = shared.read_regular_file(
                path, executable=True)
            if (stat.S_IMODE(metadata.st_mode) != 0o755 or
                    len(contents) != binary["size"] or
                    digest != binary["sha256"]):
                fail(f"staged binary differs from build lineage: {destination}")


def revalidate_binary_inputs(local_records: list[dict[str, Any]]) -> None:
    for record in local_records:
        metadata, _contents, digest = shared.read_regular_file(
            record["path"], executable=True)
        if (_file_identity(metadata) != record["identity"] or
                digest != record["sha256"]):
            fail(f"build artifact changed while bundling: {record['path']}")


def validate_platform_policy(contents: bytes) -> dict[str, Any]:
    try:
        policy = json.loads(contents.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("native VM platform policy is not valid UTF-8 JSON")
    expected = {
        "schema", "allowed_vm_types", "systemd_pid1", "cgroup",
        "docker_socket", "network", "service_identities", "paper_authorized",
    }
    if (not isinstance(policy, dict) or set(policy) != expected or
            policy.get("schema") != POLICY_SCHEMA or
            policy.get("systemd_pid1") is not True or
            policy.get("cgroup") != "v2-root" or
            policy.get("docker_socket") != "absent" or
            policy.get("paper_authorized") is not False):
        fail("native VM platform policy contract mismatch")
    if policy.get("allowed_vm_types") != sorted(
            {"amazon", "bochs", "google", "kvm", "microsoft", "oracle",
             "qemu", "vmware", "xen"}):
        fail("native VM platform policy VM allowlist mismatch")
    identities = parse_identity_manifest(
        IDENTITY_MANIFEST.read_bytes())["identities"]
    if policy.get("service_identities") != {
            name: record["uid"] for name, record in identities.items()}:
        fail("native VM platform policy identity mismatch")
    if policy.get("network") != {
            "loopback_addresses": ["127.0.0.1", "::1"],
            "non_loopback_addresses": 0,
            "non_loopback_links_up": 0,
            "non_loopback_routes": 0,
            "default_routes": 0}:
        fail("native VM platform policy network mismatch")
    return policy


def rootfs_file_record(rootfs: Path, path: Path) -> dict[str, Any]:
    relative = path.relative_to(rootfs).as_posix()
    metadata, _contents, digest = shared.read_regular_file(path)
    return {
        "path": relative,
        "mode": format(stat.S_IMODE(metadata.st_mode), "04o"),
        "uid": 0,
        "gid": 0,
        "size": metadata.st_size,
        "sha256": digest,
    }


def rootfs_records(rootfs: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(rootfs.rglob("*")):
        metadata = os.lstat(path)
        if stat.S_ISDIR(metadata.st_mode):
            if stat.S_IMODE(metadata.st_mode) & 0o022:
                fail(f"rootfs directory is group/world writable: {path}")
            continue
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            fail(f"rootfs contains a non-regular or linked file: {path}")
        records.append(rootfs_file_record(rootfs, path))
    return records


def normalize_rootfs_directories(rootfs: Path) -> None:
    credentials = rootfs / "etc/heptatrader/credentials"
    os.chmod(rootfs, 0o755)
    for path in sorted(rootfs.rglob("*")):
        metadata = os.lstat(path)
        if stat.S_ISDIR(metadata.st_mode):
            os.chmod(path, 0o700 if path == credentials else 0o755)


def ensure_forbidden_absent(rootfs: Path) -> None:
    forbidden = (
        FORMAL_IB_PATH,
        SENTINEL_PATH,
        Path("run/hepta-rootful-systemd-gate.disposable"),
        Path("run/docker.sock"),
        *AGENT_OS_RUNTIME_PATHS,
    )
    for relative in forbidden:
        path = rootfs / relative
        if path.exists() or path.is_symlink():
            fail(f"forbidden native VM payload path is present: {relative}")


def stage_file(source: Path, rootfs: Path, relative: Path, mode: int) -> None:
    destination = rootfs / relative
    shared.copy_stable_file(source, destination, mode)
    parent = destination.parent
    while parent != rootfs:
        os.chmod(parent, 0o755)
        parent = parent.parent


def write_rootfs(rootfs: Path, relative: Path,
                 contents: bytes, mode: int) -> None:
    destination = rootfs / relative
    shared.write_private(destination, contents, mode)
    parent = destination.parent
    while parent != rootfs:
        os.chmod(parent, 0o755)
        parent = parent.parent


def deterministic_tar(rootfs: Path, archive: Path) -> None:
    descriptor = os.open(
        archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            with tarfile.open(
                    fileobj=output, mode="w",
                    format=tarfile.USTAR_FORMAT) as bundle:
                for path in sorted(rootfs.rglob("*")):
                    relative = path.relative_to(rootfs).as_posix()
                    metadata = os.lstat(path)
                    info = tarfile.TarInfo(relative)
                    info.uid = 0
                    info.gid = 0
                    info.uname = "root"
                    info.gname = "root"
                    info.mtime = 0
                    info.mode = stat.S_IMODE(metadata.st_mode)
                    if stat.S_ISDIR(metadata.st_mode):
                        info.type = tarfile.DIRTYPE
                        info.size = 0
                        bundle.addfile(info)
                    elif stat.S_ISREG(metadata.st_mode):
                        info.type = tarfile.REGTYPE
                        info.size = metadata.st_size
                        with path.open("rb") as source:
                            bundle.addfile(info, source)
                    else:
                        fail("native VM rootfs tar input type is unsafe")
            output.flush()
            os.fsync(output.fileno())
    finally:
        os.close(descriptor)
    os.chmod(archive, 0o600)


def validate_tar(archive: Path, expected_files: list[dict[str, Any]]) -> None:
    expected = {record["path"]: record for record in expected_files}
    expected_directories = {
        parent.as_posix()
        for path in expected
        for parent in Path(path).parents
        if parent != Path(".")
    }
    observed: set[str] = set()
    observed_entries: set[str] = set()
    with tarfile.open(archive, mode="r:") as bundle:
        for member in bundle.getmembers():
            if (member.name.startswith("/") or ".." in Path(member.name).parts or
                    member.uid != 0 or member.gid != 0 or member.uname != "root" or
                    member.gname != "root" or member.mtime != 0 or
                    member.issym() or member.islnk() or
                    member.name in observed_entries):
                fail("native VM archive metadata contract mismatch")
            observed_entries.add(member.name)
            if member.isdir():
                expected_mode = (
                    0o700 if member.name == "etc/heptatrader/credentials"
                    else 0o755)
                if (member.name not in expected_directories or
                        member.mode != expected_mode):
                    fail("native VM archive directory contract mismatch")
                continue
            if not member.isfile() or member.name not in expected:
                fail("native VM archive file allowlist mismatch")
            extracted = bundle.extractfile(member)
            if extracted is None:
                fail("native VM archive file is unreadable")
            contents = extracted.read()
            record = expected[member.name]
            if (len(contents) != record["size"] or
                    shared.hashlib.sha256(contents).hexdigest() !=
                    record["sha256"] or
                    format(member.mode, "04o") != record["mode"]):
                fail("native VM archive file digest or mode mismatch")
            observed.add(member.name)
    if observed != set(expected):
        fail("native VM archive is missing an expected file")


def validate_output_directory(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    parent = absolute.parent.resolve(strict=True)
    metadata = os.lstat(parent)
    if (absolute.parent != parent or not stat.S_ISDIR(metadata.st_mode) or
            stat.S_IMODE(metadata.st_mode) & 0o022 or absolute.exists() or
            absolute.is_symlink()):
        fail("native VM bundle output directory is unsafe or already exists")
    absolute.mkdir(mode=0o700)
    return absolute


def build_bundle(args: argparse.Namespace) -> dict[str, Any]:
    try:
        source_provenance = clean_source.verify_bundle(
            args.clean_source_bundle, args.clean_source_manifest)
    except SystemExit as error:
        fail(str(error))
    source_manifest_bytes, source_manifest, source_index = (
        read_verified_clean_source_manifest(
            args.clean_source_manifest, source_provenance))
    source_provenance_bytes = canonical_json(source_provenance)
    source_provenance_sha256 = hashlib.sha256(
        source_provenance_bytes).hexdigest()

    # Resolve both build caches before accepting either.  A normal Git
    # worktree, two different source extractions, or an in-source build is not
    # an eligible source of release bytes.
    preliminary_roots: list[Path] = []
    for build_argument in (
            args.ibapi_build_dir, args.ib_disabled_build_dir):
        build = build_argument.resolve(strict=True)
        cache_contents = shared.read_regular_file(
            build / "CMakeCache.txt", maximum=8 * 1024 * 1024)[1]
        values = parse_cmake_cache_bytes(
            cache_contents, str(build / "CMakeCache.txt"))
        try:
            preliminary_roots.append(
                Path(values.get("CMAKE_HOME_DIRECTORY", "")).resolve(
                    strict=True))
        except OSError:
            fail(f"{build}: CMAKE_HOME_DIRECTORY is unavailable")
    if preliminary_roots[0] != preliminary_roots[1]:
        fail("IBAPI-on/off builds do not share one exact clean-source tree")
    source_root = preliminary_roots[0]
    source_tree_record = scan_exact_source_tree(
        source_root, source_manifest_bytes, source_manifest, source_index)
    staged_source_records, reviewed_source_records = (
        validate_repository_source_inputs(REPOSITORY, source_index))
    validate_platform_policy_source(args.platform_policy, source_index)

    profile_ib_build, profile_ib_record, profile_ib_evidence = (
        validate_lineage_build(
        args.ibapi_build_dir, ibapi=True, source_root=source_root,
        source_provenance=source_provenance, source_index=source_index))
    profile_disabled_build, profile_disabled_record, _profile_disabled_evidence = (
        validate_lineage_build(
        args.ib_disabled_build_dir, ibapi=False, source_root=source_root,
        source_provenance=source_provenance, source_index=source_index))

    policy_metadata, policy_contents, policy_sha256 = shared.read_regular_file(
        args.platform_policy, maximum=1024 * 1024)
    if stat.S_IMODE(policy_metadata.st_mode) & 0o022:
        fail("native VM platform policy is group/world writable")
    policy = validate_platform_policy(policy_contents)
    output = validate_output_directory(args.output_dir)
    causal_root = output / ".fresh-causal-builds"
    causal_root.mkdir(mode=0o700)
    ib_build = causal_root / "fresh-ibapi-on"
    disabled_build = causal_root / "fresh-ibapi-off"
    ib_causal, ib_tool_locals, _ib_output_locals = (
        fresh_causal_rebuild_lane(
            profile_ib_build, ib_build, source_root=source_root, ibapi=True,
            ibapi_root=profile_ib_evidence["ibapi_root"],
            source_manifest_sha256=source_provenance["manifest_sha256"],
            ibapi_source_manifest_sha256=profile_ib_record[
                "ibapi_source_manifest_sha256"],
            artifact_names=CAUSAL_BUILD_OUTPUTS["ibapi_on"]))
    disabled_causal, disabled_tool_locals, _off_output_locals = (
        fresh_causal_rebuild_lane(
            profile_disabled_build, disabled_build, source_root=source_root,
            ibapi=False, ibapi_root=None,
            source_manifest_sha256=source_provenance["manifest_sha256"],
            ibapi_source_manifest_sha256=None,
            artifact_names=CAUSAL_BUILD_OUTPUTS["ibapi_off"]))
    ib_build, ib_record, ib_evidence = validate_lineage_build(
        ib_build, ibapi=True, source_root=source_root,
        source_provenance=source_provenance, source_index=source_index)
    disabled_build, disabled_record, disabled_evidence = validate_lineage_build(
        disabled_build, ibapi=False, source_root=source_root,
        source_provenance=source_provenance, source_index=source_index)
    if (ib_record["ibapi_source_manifest"] !=
            profile_ib_record["ibapi_source_manifest"] or
            ib_record["ibapi_source_manifest_sha256"] !=
            profile_ib_record["ibapi_source_manifest_sha256"] or
            profile_disabled_record["ibapi_source_manifest"] is not None):
        fail("exact IBAPI SDK profile changed across the fresh causal rebuild")
    ib_record["causal_build"] = ib_causal
    disabled_record["causal_build"] = disabled_causal
    causal_tool_locals = ib_tool_locals + disabled_tool_locals

    simulator = shared.find_binary(ib_build, "hepta-executiond")
    formal_ib = shared.find_binary(ib_build, "hepta-ib-executiond")
    disabled = shared.find_binary(
        disabled_build, "hepta-ib-executiond-disabled")
    client_probe = shared.find_binary(
        ib_build, "hepta_execution_systemd_client_probe")
    sandbox_probe = shared.find_binary(
        ib_build, "hepta_execution_systemd_sandbox_probe")
    agent_binaries: dict[str, tuple[Path, Path, str]] = {}
    for name in ("hepta-tool-gatewayd", "hepta-sessionctl", "heptactl"):
        enabled_binary = shared.find_binary(ib_build, name)
        disabled_binary = shared.find_binary(disabled_build, name)
        enabled_sha256 = shared.sha256_file(enabled_binary)
        if shared.sha256_file(disabled_binary) != enabled_sha256:
            fail(f"venue-neutral Agent OS binary drifts across IB builds: {name}")
        agent_binaries[name] = (
            enabled_binary, disabled_binary, enabled_sha256)
    staged_binary_records, formal_binary_record, local_binary_records = (
        build_binary_lineage(
            args.variant, ib_build, disabled_build, simulator, formal_ib,
            disabled, client_probe, sandbox_probe, agent_binaries))
    shared.validate_broker_free_artifacts(
        REPOSITORY / "tests/execution_systemd_client_probe.cpp",
        REPOSITORY / "tests/execution_systemd_sandbox_probe.cpp",
        sandbox_probe, disabled)

    with tempfile.TemporaryDirectory(
            prefix="hepta-native-vm-bundle-") as temporary:
        context = Path(temporary)
        shared.provision_context(
            context, simulator, formal_ib, disabled, client_probe,
            sandbox_probe)
        rootfs = context / "rootfs"
        os.rename(context / "install-root", rootfs)
        os.chmod(rootfs, 0o755)
        artifacts = context / "artifacts"
        scripts = context / "scripts"
        inner = (context / "tests/rootful_systemd" /
                 "hepta_execution_rootful_inner_gate.py")

        if args.variant == "sandbox":
            canonical_ib = rootfs / "usr/libexec/hepta-ib-executiond"
            canonical_ib.unlink()
            shared.copy_stable_file(
                artifacts / "hepta_execution_systemd_sandbox_probe",
                canonical_ib, 0o755)

        staged = (
            (scripts / "check_hepta_execution_provisioned_host.py",
             Path("usr/local/libexec/check_hepta_execution_provisioned_host.py"),
             0o755),
            (REPOSITORY / "scripts/run_hepta_execution_rootful_systemd_gate.py",
             Path("usr/local/libexec/run_hepta_execution_rootful_systemd_gate.py"),
             0o755),
            (REPOSITORY / "scripts/run_hepta_execution_native_systemd_gate.py",
             Path("usr/local/libexec/run_hepta_execution_native_systemd_gate.py"),
             0o755),
            (REPOSITORY / "tests/agent_os_rootful_systemd/"
             "hepta_agent_os_rootful_inner_gate.py",
             AGENT_OS_RUNTIME_INNER_GATE, 0o755),
            (inner,
             Path("usr/local/libexec/hepta_execution_rootful_inner_gate.py"),
             0o755),
            (artifacts / "hepta_execution_systemd_client_probe",
             Path("usr/local/libexec/hepta_execution_systemd_client_probe"),
             0o755),
            (artifacts / "hepta_execution_systemd_sandbox_probe",
             Path("usr/local/libexec/hepta_execution_systemd_sandbox_probe"),
             0o755),
            (artifacts / "hepta-ib-executiond-disabled",
             Path("usr/local/libexec/hepta-ib-executiond-disabled"), 0o755),
            (artifacts / "formal-ibapi.sha256",
             SHARE / "formal-ibapi.sha256", 0o444),
            (args.platform_policy, SHARE / "platform-policy.json", 0o444),
            (agent_binaries["hepta-tool-gatewayd"][0],
             Path("usr/libexec/hepta-tool-gatewayd"), 0o755),
            (agent_binaries["hepta-sessionctl"][0],
             Path("usr/bin/hepta-sessionctl"), 0o755),
            (agent_binaries["heptactl"][0],
             Path("usr/bin/heptactl"), 0o755),
            (REPOSITORY / "scripts/hepta_agent_session_bootstrap.py",
             Path("usr/libexec/hepta-agent-session-bootstrap"), 0o755),
            (REPOSITORY / "scripts/hepta_agent_trust_domain.py",
             Path("usr/libexec/hepta_agent_trust_domain.py"), 0o755),
            (REPOSITORY / "scripts/hepta_paper_receipt_contracts.py",
             Path("usr/libexec/hepta-paper-receipt-contracts"), 0o755),
            (REPOSITORY / "scripts/hepta_shadow_watch_collector.py",
             Path("usr/libexec/hepta-shadow-watch-collector"), 0o755),
            (REPOSITORY / "scripts/hepta_shadow_watch_exporter.py",
             Path("usr/libexec/hepta-shadow-watch-exporter"), 0o755),
            (REPOSITORY / "scripts/hepta_shadow_watch_custodian.py",
             Path("usr/libexec/hepta-shadow-watch-custodian"), 0o755),
            (REPOSITORY / "scripts/hepta_shadow_host_installer.py",
             Path("usr/libexec/hepta-shadow-host-installer"), 0o755),
            (REPOSITORY / "scripts/hepta_p1_shadow_host_controller.py",
             Path("usr/libexec/hepta-p1-shadow-host-controller"), 0o755),
            (REPOSITORY / "scripts/hepta_p1_load_probe_validator.py",
             Path("usr/libexec/hepta-p1-load-probe-validator"), 0o755),
            (REPOSITORY / "scripts/build_hepta_p1_observation_policy.py",
             Path("usr/libexec/build-hepta-p1-observation-policy"), 0o755),
            (REPOSITORY / "scripts/hepta_p1_shadow_observer_controller.py",
             Path("usr/libexec/hepta-p1-shadow-observer-controller"), 0o755),
            (REPOSITORY / "scripts/hepta_p1_shadow_admission_launcher.py",
             Path("usr/libexec/hepta-p1-shadow-admission-launcher"), 0o755),
            (REPOSITORY / "scripts/hepta_p1_watch_profile_deployer.py",
             Path("usr/libexec/hepta-p1-watch-profile-deployer"), 0o755),
            (REPOSITORY /
             "scripts/hepta_p1_watch_activation_transaction.py",
             Path("usr/libexec/hepta-p1-watch-activation-transaction"),
             0o755),
            (REPOSITORY / "scripts/hepta_bounded_shadow_closure_verifier.py",
             Path("usr/libexec/hepta-bounded-shadow-closure-verifier"), 0o755),
            (REPOSITORY / "scripts/hepta_official_source_capture.py",
             Path("usr/libexec/hepta-official-source-capture"), 0o755),
            (REPOSITORY / "scripts/hepta_bounded_shadow_observer.py",
             Path("usr/libexec/hepta_bounded_shadow_observer.py"), 0o755),
            (REPOSITORY / "scripts/hepta_market_context_builder.py",
             Path("usr/libexec/hepta_market_context_builder.py"), 0o755),
            (REPOSITORY / "scripts/hepta_market_evidence_normalizer.py",
             Path("usr/libexec/hepta_market_evidence_normalizer.py"), 0o755),
            (REPOSITORY / "scripts/hepta_market_official_source_extractor.py",
             Path("usr/libexec/hepta_market_official_source_extractor.py"),
             0o755),
            (REPOSITORY / "scripts/hepta_eurusd_confirmed_momentum_strategy.py",
             Path("usr/libexec/hepta_eurusd_confirmed_momentum_strategy.py"),
             0o755),
            (REPOSITORY / "scripts/hepta_shadow_market_history.py",
             Path("usr/libexec/hepta_shadow_market_history.py"), 0o755),
            (REPOSITORY / "scripts/hepta_strategy_shadow_runner.py",
             Path("usr/libexec/hepta_strategy_shadow_runner.py"), 0o755),
            (REPOSITORY / "scripts/hepta_strategy_contracts.py",
             Path("usr/libexec/hepta_strategy_contracts.py"), 0o644),
            (REPOSITORY /
             "scripts/validate_hepta_strategy_decision_receipt.py",
             Path("usr/libexec/validate_hepta_strategy_decision_receipt.py"),
             0o755),
            (REPOSITORY /
             "strategies/eurusd-confirmed-momentum-shadow-v2.json",
             Path("usr/share/heptatrader/strategies/"
                  "eurusd-confirmed-momentum-shadow-v2.json"), 0o644),
            (REPOSITORY / "scripts/check_hepta_agent_os_provisioned_host.py",
             AGENT_OS_INSTALLED_PREFLIGHT, 0o755),
            (REPOSITORY / "scripts/check_hepta_agent_os_provisioned_host.py",
             AGENT_OS_INSTALLATION_PREFLIGHT, 0o755),
            (REPOSITORY / "systemd/hepta-tool-gateway.service",
             Path("usr/lib/systemd/system/hepta-tool-gateway.service"), 0o644),
            (REPOSITORY / "systemd/hepta-tool-gateway.socket",
             Path("usr/lib/systemd/system/hepta-tool-gateway.socket"), 0o644),
            (REPOSITORY / "systemd/hepta-tool-session-supervisor.socket",
             Path("usr/lib/systemd/system/"
                  "hepta-tool-session-supervisor.socket"), 0o644),
            (REPOSITORY / "systemd/hepta-p1-watch-activation.service",
             Path("usr/lib/systemd/system/"
                  "hepta-p1-watch-activation.service"), 0o644),
            (REPOSITORY /
             "systemd/hepta-p1-watch-activation-reconcile.service",
             Path("usr/lib/systemd/system/"
                  "hepta-p1-watch-activation-reconcile.service"), 0o644),
            (REPOSITORY /
             "systemd/hepta-p1-watch-activation-reconcile.timer",
             Path("usr/lib/systemd/system/"
                  "hepta-p1-watch-activation-reconcile.timer"), 0o644),
            (REPOSITORY / "systemd/hepta-tool-gateway@.service",
             Path("usr/lib/systemd/system/"
                  "hepta-tool-gateway@.service"), 0o644),
            (REPOSITORY / "systemd/hepta-tool-gateway@.socket",
             Path("usr/lib/systemd/system/"
                  "hepta-tool-gateway@.socket"), 0o644),
            (REPOSITORY / "systemd/hepta-tool-session-supervisor@.socket",
             Path("usr/lib/systemd/system/"
                  "hepta-tool-session-supervisor@.socket"), 0o644),
            (REPOSITORY /
             "systemd/hepta-shadow-watch-collector@.service",
             Path("usr/lib/systemd/system/"
                  "hepta-shadow-watch-collector@.service"), 0o644),
            (REPOSITORY /
             "systemd/hepta-shadow-watch-collector@.timer",
             Path("usr/lib/systemd/system/"
                  "hepta-shadow-watch-collector@.timer"), 0o644),
            (REPOSITORY /
             "systemd/hepta-shadow-watch-export@.service",
             Path("usr/lib/systemd/system/"
                  "hepta-shadow-watch-export@.service"), 0o644),
            (REPOSITORY /
             "systemd/hepta-shadow-watch-custodian@.service",
             Path("usr/lib/systemd/system/"
                  "hepta-shadow-watch-custodian@.service"), 0o644),
            (REPOSITORY /
             "systemd/hepta-shadow-watch-custodian-reconcile@.service",
             Path("usr/lib/systemd/system/"
                  "hepta-shadow-watch-custodian-reconcile@.service"), 0o644),
            (REPOSITORY /
             "systemd/hepta-shadow-watch-custodian-reconcile@.timer",
             Path("usr/lib/systemd/system/"
                  "hepta-shadow-watch-custodian-reconcile@.timer"), 0o644),
            (REPOSITORY /
             "systemd/hepta-tool-gateway-domain.env.example",
             Path("usr/share/doc/heptatrader/examples/"
                  "hepta-tool-gateway-domain.env.example"), 0o644),
            (REPOSITORY /
             "systemd/hepta-shadow-watch-domain.env.example",
             Path("usr/share/doc/heptatrader/examples/"
                  "hepta-shadow-watch-domain.env.example"), 0o644),
            (REPOSITORY / "tmpfiles.d/heptatrader-agent-os.conf",
             Path("usr/lib/tmpfiles.d/heptatrader-agent-os.conf"), 0o644),
            (REPOSITORY / ".agents/plugins/marketplace.json",
             Path("usr/share/heptatrader/.agents/plugins/marketplace.json"),
             0o644),
            (REPOSITORY / "plugins/heptatrader-agent-os/.mcp.json",
             Path("usr/share/heptatrader/plugins/heptatrader-agent-os/"
                  ".mcp.json"), 0o644),
            (REPOSITORY /
             "plugins/heptatrader-agent-os/.codex-plugin/plugin.json",
             Path("usr/share/heptatrader/plugins/heptatrader-agent-os/"
                  ".codex-plugin/plugin.json"), 0o644),
            (REPOSITORY / "plugins/heptatrader-agent-os/README.md",
             Path("usr/share/heptatrader/plugins/heptatrader-agent-os/"
                  "README.md"), 0o644),
            (REPOSITORY / "systemd/hepta-agent-host-identity.conf.example",
             Path("usr/share/doc/heptatrader/examples/"
                  "hepta-agent-host-identity.conf.example"), 0o644),
            (REPOSITORY / "systemd/hepta-tool-gateway.env.example",
             Path("usr/share/doc/heptatrader/examples/"
                  "hepta-tool-gateway.env.example"), 0o644),
            (REPOSITORY / "systemd/hepta-tool-gateway.env.example",
             Path("etc/heptatrader/hepta-tool-gateway.env"), 0o644),
            (REPOSITORY / "systemd/hepta-tool-gateway.env.example",
             AGENT_OS_RUNTIME_PROVISIONING / "hepta-tool-gateway.env",
             0o644),
            (REPOSITORY / "systemd/hepta-execution-simulator.env.example",
             AGENT_OS_RUNTIME_PROVISIONING /
             "hepta-execution-simulator.env", 0o644),
            (REPOSITORY / "systemd/"
             "hepta-agent-trust-domain-paper-identities-v1.json.example",
             Path("etc/heptatrader/"
                  "hepta-agent-trust-domain-paper-identities-v1.json"),
             0o600),
            (REPOSITORY / "systemd/"
             "hepta-agent-trust-domain-paper-identities-v1.json.example",
             AGENT_OS_RUNTIME_PROVISIONING /
             "hepta-agent-trust-domain-paper-identities-v1.json", 0o600),
            (REPOSITORY / "docs/RUNBOOK-STARTUP.md",
             Path("usr/share/doc/heptatrader/RUNBOOK-STARTUP.md"), 0o644),
        )
        for source, relative, mode in staged:
            stage_file(source, rootfs, relative, mode)
        write_rootfs(
            rootfs, CLEAN_SOURCE_MANIFEST, source_manifest_bytes, 0o444)
        for build_key, evidence in (
                ("ibapi_on", ib_evidence),
                ("ibapi_off", disabled_evidence)):
            stage_file(
                evidence["cmake_cache"], rootfs,
                BUILD_EVIDENCE_PATHS[build_key]["cmake_cache"], 0o444)
            stage_file(
                evidence["compile_commands"], rootfs,
                BUILD_EVIDENCE_PATHS[build_key]["compile_commands"], 0o444)
        write_rootfs(rootfs, SHARE / "variant",
                     (args.variant + "\n").encode("ascii"), 0o444)
        write_rootfs(
            rootfs, CLEAN_SOURCE_PROVENANCE, source_provenance_bytes, 0o444)
        write_rootfs(
            rootfs, Path("etc/heptatrader/hepta-supervisor-lease.key"),
            agent_os_contract.UNPROVISIONED_SUPERVISOR_LEASE, 0o400)
        write_rootfs(
            rootfs, AGENT_OS_RUNTIME_INSTALLATION_MARKER,
            b"HEPTA_AGENT_OS_INSTALLATION_PREFLIGHT_V1\n", 0o444)
        simulator_fence = shared.read_regular_file(
            rootfs / "etc/heptatrader/credentials/"
            "hepta-execution-simulator-fence", maximum=1024)[1]
        write_rootfs(
            rootfs,
            AGENT_OS_RUNTIME_PROVISIONING /
            "hepta-execution-simulator-fence",
            simulator_fence, 0o400)
        normalize_rootfs_directories(rootfs)
        validate_staged_lineage_payloads(
            rootfs, staged_source_records, staged_binary_records)

        lineage_manifest = {
            "schema": SOURCE_BUILD_LINEAGE_SCHEMA,
            "variant": args.variant,
            "clean_source": source_provenance,
            "source_manifest": {
                "path": CLEAN_SOURCE_MANIFEST.as_posix(),
                "schema": source_manifest["schema"],
                "bundle_class": source_manifest["bundle_class"],
                "root": source_manifest["root"],
                "version": source_manifest["version"],
                "git_head": source_manifest["git_head"],
                "file_count": source_manifest["file_count"],
                "files_sha256": source_manifest["files_sha256"],
                "bundle_sha256": source_provenance["bundle_sha256"],
                "manifest_sha256": source_provenance["manifest_sha256"],
            },
            "source_tree": source_tree_record,
            "builds": {
                "ibapi_on": ib_record,
                "ibapi_off": disabled_record,
            },
            "reviewed_build_sources": reviewed_source_records,
            "staged_sources": staged_source_records,
            "staged_binaries": staged_binary_records,
            "formal_ibapi": formal_binary_record,
            "boundary": {
                "source_tree_exact": True,
                "source_tree_git_metadata_present": False,
                "build_source_tree_shared": True,
                "repository_staged_sources_match_clean_source": True,
                "formal_ibapi_elf_staged": False,
                "paper_authorized": False,
                "live_enabled": False,
            },
        }
        lineage_bytes = canonical_json(lineage_manifest)
        lineage_sha256 = hashlib.sha256(lineage_bytes).hexdigest()
        write_rootfs(
            rootfs, SOURCE_BUILD_LINEAGE, lineage_bytes, 0o444)

        runtime_input_records = [
            rootfs_file_record(rootfs, rootfs / relative)
            for relative in AGENT_OS_RUNTIME_GATE_PATHS
        ]
        runtime_input_manifest = {
            "schema": AGENT_OS_RUNTIME_INPUT_SCHEMA,
            "profile": "native-vm-four-uid-watch-runtime-required",
            "inputs": runtime_input_records,
            "identities": {
                "gateway_uid": 2001,
                "simulator_execution_uid": 2002,
                "ib_execution_uid_reserved_not_started": 2003,
                "agent_uid": 2004,
            },
            "watch_tools": list(AGENT_OS_WATCH_TOOLS),
            "read_probes": list(AGENT_OS_READ_PROBES),
            "lifecycle": {
                "service_restart_required": True,
                "socket_restart_required": True,
                "watch_revoke_required": True,
                "runtime_cleanup_required": True,
            },
            "runtime": {
                "inner_gate_path":
                    "/" + AGENT_OS_RUNTIME_INNER_GATE.as_posix(),
                "runtime_preflight_executed": False,
                "runtime_preflight_required": True,
                "runtime_state_provisioned_by_bundle": False,
                "runtime_sentinel_staged": False,
                "runtime_artifacts_staged": False,
            },
            "paper_authorized": False,
            "live_enabled": False,
            "ib_adapter_runtime_authorized": False,
        }
        runtime_input_manifest_bytes = canonical_json(runtime_input_manifest)
        runtime_input_manifest_sha256 = hashlib.sha256(
            runtime_input_manifest_bytes).hexdigest()
        write_rootfs(
            rootfs, AGENT_OS_RUNTIME_INPUT_MANIFEST,
            runtime_input_manifest_bytes, 0o444)

        agent_os_records = [
            rootfs_file_record(rootfs, rootfs / relative)
            for relative in AGENT_OS_STATIC_PATHS
        ]
        agent_os_installation = {
            "schema": AGENT_OS_INSTALLATION_SCHEMA,
            "profile": "static-installation-only",
            "preflight": {
                "path": "/" + AGENT_OS_INSTALLATION_PREFLIGHT.as_posix(),
                "arguments": ["--root", "/", "--installation-only"],
            },
            "files": agent_os_records,
            "runtime": {
                "tool_socket_staged": False,
                "session_token_staged": False,
                "supervisor_socket_staged": False,
                "runtime_preflight_executed": False,
                "runtime_preflight_required": True,
                "runtime_gate_inputs_staged": True,
                "runtime_input_manifest_sha256":
                    runtime_input_manifest_sha256,
                "runtime_state_provisioned_by_bundle": False,
                "runtime_sentinel_staged": False,
                "supervisor_credential":
                    "unprovisioned-non-authorizing-placeholder",
            },
            "paper_authorized": False,
            "live_enabled": False,
        }
        agent_os_installation_bytes = canonical_json(agent_os_installation)
        agent_os_installation_sha256 = hashlib.sha256(
            agent_os_installation_bytes).hexdigest()
        write_rootfs(
            rootfs, AGENT_OS_INSTALLATION_MANIFEST,
            agent_os_installation_bytes, 0o444)

        provisioning = {
            "schema": PROVISIONING_SCHEMA,
            "variant": args.variant,
            "builds": {"ibapi_on": ib_record, "ibapi_off": disabled_record},
            "platform_policy_sha256": policy_sha256,
            "clean_source_provenance_sha256": source_provenance_sha256,
            "clean_source": source_provenance,
            "formal_ibapi_sha256": formal_binary_record["sha256"],
            "agent_os_installation_manifest_sha256":
                agent_os_installation_sha256,
            "agent_os_runtime_input_manifest_sha256":
                runtime_input_manifest_sha256,
            "agent_os_installation_preflight_staged": True,
            "agent_os_runtime_gate_inputs_staged": True,
            "agent_os_runtime_preflight_required": True,
            "agent_os_runtime_artifacts_staged": False,
            "formal_ibapi_elf_staged": False,
            "instance_identity_staged": False,
            "paper_authorized": False,
            "live_enabled": False,
        }
        provisioning_bytes = canonical_json(provisioning)
        provisioning_sha256 = hashlib.sha256(
            provisioning_bytes).hexdigest()
        write_rootfs(
            rootfs, SHARE / "provisioning-manifest.json",
            provisioning_bytes, 0o444)

        ensure_forbidden_absent(rootfs)
        pre_manifest_records = rootfs_records(rootfs)
        image_manifest = {
            "schema": IMAGE_SCHEMA,
            "variant": args.variant,
            "platform_policy_sha256": policy_sha256,
            "clean_source_provenance_sha256": source_provenance_sha256,
            "clean_source": source_provenance,
            "provisioning_manifest_sha256": provisioning_sha256,
            "agent_os_installation_manifest_sha256":
                agent_os_installation_sha256,
            "agent_os_runtime_input_manifest_sha256":
                runtime_input_manifest_sha256,
            "agent_os_installation_preflight_staged": True,
            "agent_os_runtime_gate_inputs_staged": True,
            "agent_os_runtime_preflight_required": True,
            "agent_os_runtime_artifacts_staged": False,
            "files": pre_manifest_records,
            "formal_ibapi_elf_staged": False,
            "instance_identity_staged": False,
            "paper_authorized": False,
            "live_enabled": False,
        }
        image_bytes = canonical_json(image_manifest)
        image_sha256 = hashlib.sha256(image_bytes).hexdigest()
        write_rootfs(rootfs, SHARE / "image-manifest.json", image_bytes, 0o444)
        write_rootfs(
            rootfs, SHARE / "image-manifest.sha256",
            (image_sha256 + "\n").encode("ascii"), 0o444)
        normalize_rootfs_directories(rootfs)
        ensure_forbidden_absent(rootfs)
        validate_staged_lineage_payloads(
            rootfs, staged_source_records, staged_binary_records)

        # Re-run all external lineage checks after the complete rootfs has been
        # staged.  This closes replacement races across source tar/manifest,
        # extracted tree, checkout inputs, build metadata, and build outputs.
        try:
            final_source_provenance = clean_source.verify_bundle(
                args.clean_source_bundle, args.clean_source_manifest)
        except SystemExit as error:
            fail(str(error))
        if final_source_provenance != source_provenance:
            fail("clean-source bundle changed while building")
        final_manifest_bytes, final_manifest, final_index = (
            read_verified_clean_source_manifest(
                args.clean_source_manifest, source_provenance))
        if (final_manifest_bytes != source_manifest_bytes or
                final_manifest != source_manifest or
                final_index != source_index):
            fail("clean-source manifest changed while building")
        if scan_exact_source_tree(
                source_root, source_manifest_bytes, source_manifest,
                source_index) != source_tree_record:
            fail("clean-source tree changed while building")
        final_staged_sources, final_reviewed_sources = (
            validate_repository_source_inputs(REPOSITORY, source_index))
        if (final_staged_sources != staged_source_records or
                final_reviewed_sources != reviewed_source_records):
            fail("repository source inputs changed while building")
        validate_platform_policy_source(args.platform_policy, source_index)
        validate_ibapi_source_tree_unchanged(
            ib_evidence["ibapi_root"],
            ib_record["ibapi_source_manifest"])
        _ib_build, final_ib_record, _ib_evidence = validate_lineage_build(
            args.ibapi_build_dir, ibapi=True, source_root=source_root,
            source_provenance=source_provenance, source_index=source_index)
        _off_build, final_disabled_record, _off_evidence = (
            validate_lineage_build(
                args.ib_disabled_build_dir, ibapi=False,
                source_root=source_root,
                source_provenance=source_provenance,
                source_index=source_index))
        if (final_ib_record != profile_ib_record or
                final_disabled_record != profile_disabled_record):
            fail("input build profile changed while bundling")
        _fresh_ib, final_fresh_ib_record, _fresh_ib_evidence = (
            validate_lineage_build(
                ib_build, ibapi=True, source_root=source_root,
                source_provenance=source_provenance,
                source_index=source_index))
        _fresh_off, final_fresh_disabled_record, _fresh_off_evidence = (
            validate_lineage_build(
                disabled_build, ibapi=False, source_root=source_root,
                source_provenance=source_provenance,
                source_index=source_index))
        final_fresh_ib_record["causal_build"] = ib_causal
        final_fresh_disabled_record["causal_build"] = disabled_causal
        if (final_fresh_ib_record != ib_record or
                final_fresh_disabled_record != disabled_record):
            fail("fresh build cache or compile commands changed while bundling")
        revalidate_causal_tools(causal_tool_locals)
        revalidate_binary_inputs(local_binary_records)
        shutil.rmtree(causal_root)
        if causal_root.exists() or causal_root.is_symlink():
            fail("fresh causal build residue could not be removed")
        final_records = rootfs_records(rootfs)

        archive = output / f"hepta-native-vm-{args.variant}.rootfs.tar"
        deterministic_tar(rootfs, archive)
        validate_tar(archive, final_records)
        archive_record = shared.stable_file(archive)
        result = {
            "schema": SCHEMA,
            "passed": True,
            "variant": args.variant,
            "platform_policy": policy,
            "platform_policy_sha256": policy_sha256,
            "clean_source_provenance_sha256": source_provenance_sha256,
            "clean_source": source_provenance,
            "clean_source_manifest_sha256":
                source_provenance["manifest_sha256"],
            "source_build_lineage_sha256": lineage_sha256,
            "provisioning_manifest_sha256": provisioning_sha256,
            "agent_os_installation_manifest_sha256":
                agent_os_installation_sha256,
            "agent_os_runtime_input_manifest_sha256":
                runtime_input_manifest_sha256,
            "agent_os_runtime_input_file_count":
                len(runtime_input_records),
            "agent_os_installation_file_count": len(agent_os_records),
            "vm_image_manifest_sha256": image_sha256,
            "rootfs_file_count": len(final_records),
            "archive": archive_record,
            "boundary": {
                "formal_ibapi_elf_staged": False,
                "instance_identity_staged": False,
                "agent_os_installation_preflight_staged": True,
                "agent_os_runtime_preflight_executed": False,
                "agent_os_runtime_preflight_required": True,
                "agent_os_runtime_gate_inputs_staged": True,
                "agent_os_runtime_state_provisioned": False,
                "agent_os_runtime_sentinel_staged": False,
                "agent_os_runtime_artifacts_staged": False,
                "paper_authorized": False,
                "live_enabled": False,
                "broker_connections": 0,
                "orders": 0,
            },
        }
        shared.atomic_report(
            output / f"hepta-native-vm-{args.variant}.bundle.json", result)
        shared.write_private(
            output / "provisioning-manifest.json", provisioning_bytes, 0o600)
        shared.write_private(
            output / "image-manifest.json", image_bytes, 0o600)
        return result


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="build a deterministic broker-free native VM rootfs bundle")
    parser.add_argument("--variant", choices=VARIANTS, required=True)
    parser.add_argument("--ibapi-build-dir", type=Path, required=True)
    parser.add_argument("--ib-disabled-build-dir", type=Path, required=True)
    parser.add_argument("--platform-policy", type=Path, required=True)
    parser.add_argument("--clean-source-bundle", type=Path, required=True)
    parser.add_argument("--clean-source-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    output_existed = args.output_dir.exists() or args.output_dir.is_symlink()
    try:
        result = build_bundle(args)
    except Exception as error:
        if not output_existed:
            try:
                metadata = os.lstat(args.output_dir)
                if (stat.S_ISDIR(metadata.st_mode) and
                        not stat.S_ISLNK(metadata.st_mode) and
                        stat.S_IMODE(metadata.st_mode) == 0o700 and
                        metadata.st_uid == os.geteuid() and
                        metadata.st_gid == os.getegid()):
                    shutil.rmtree(args.output_dir)
            except FileNotFoundError:
                pass
        print(f"hepta_native_vm_bundle: FAIL {error}", file=sys.stderr)
        return 1
    print("hepta_native_vm_bundle: PASS "
          f"variant={args.variant} files={result['rootfs_file_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
