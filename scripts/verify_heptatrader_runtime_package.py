#!/usr/bin/env python3
"""Verify the deterministic passive Agent/Simulator runtime package."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import pathlib
import re
import stat
import struct
import tarfile
from typing import Any


SCHEMA = "hepta.runtime-package.v1"
PACKAGE_CLASS = "passive-agent-simulator-runtime"
INTERNAL_MANIFEST = ".hepta/runtime-package-manifest.json"
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_PACKAGE_BYTES = 512 * 1024 * 1024
HEX64 = re.compile(r"[0-9a-f]{64}")
VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,95}")
PAPER_IDENTITY_SOURCE_PATH = (
    "usr/share/doc/heptatrader/examples/"
    "hepta-agent-trust-domain-paper-identities-v1.json.example"
)
PAPER_IDENTITY_SOURCE_SHA256 = (
    "sha256:4a94d555cad61a9de67b809cfae301eadd6ebf2511714c93343f10decb34e435"
)
PAPER_IDENTITY_SOURCE_BYTES = b"""{
  "identities": [],
  "live_authorized": false,
  "paper_authorized": false,
  "schema": "hepta.agent-trust-domain-paper-identities.v1",
  "source_policy_sha256": "sha256:08d430d53e4813cd0a43a23beeb92344af2130dca425814cbf7285059d90f90c",
  "version": 1
}
"""

# The fixed release-validation verifier deliberately imports only this exact
# same-directory module closure when run by ``python3 -I -S``.  Keep the list
# explicit: adding a transitive import without adding its immutable installed
# companion must fail the installed-layout contract instead of falling back to
# a repository checkout or PYTHONPATH.
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

AGENT_FILES: dict[str, int] = {
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
    "usr/libexec/hepta-p1-watch-profile-deployer": 0o755,
    "usr/libexec/hepta-p1-watch-activation-transaction": 0o755,
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
    "usr/lib/systemd/system/hepta-p1-watch-activation-reconcile.service":
        0o644,
    "usr/lib/systemd/system/hepta-p1-watch-activation-reconcile.timer":
        0o644,
    "usr/lib/systemd/system/hepta-p1-paper-canary-capture.service": 0o644,
    "usr/lib/systemd/system/hepta-p1-paper-canary-executor.service": 0o644,
    "usr/lib/systemd/system/hepta-p1-paper-canary-root-coordinator.service":
        0o644,
    "usr/lib/systemd/system/hepta-local-paper-fail-close@.service": 0o644,
    "usr/lib/systemd/system/hepta-p1-paper-terminal-cutoff@.service": 0o644,
    "usr/lib/systemd/system/hepta-p1-paper-terminal-witness-verifier@.service":
        0o644,
    "usr/lib/systemd/system/hepta-p1-safety-soak-campaign@.service":
        0o644,
    "usr/lib/systemd/system/hepta-p1-safety-soak-observer-worker@.service":
        0o644,
    "usr/lib/systemd/system/hepta-p1-safety-soak-recorder-worker@.service":
        0o644,
    "usr/lib/systemd/system/hepta-p1-safety-soak@.target": 0o644,
    "usr/lib/systemd/system/hepta-tool-gateway.socket": 0o644,
    "usr/lib/systemd/system/hepta-tool-gateway.service": 0o644,
    "usr/lib/systemd/system/hepta-tool-session-supervisor.socket": 0o644,
    "usr/lib/systemd/system/hepta-tool-gateway@.socket": 0o644,
    "usr/lib/systemd/system/hepta-tool-gateway@.service": 0o644,
    "usr/lib/systemd/system/hepta-tool-gateway.service.d/"
    "10-hepta-broker-egress-policy.conf": 0o644,
    "usr/lib/systemd/system/hepta-tool-gateway@.service.d/"
    "10-hepta-broker-egress-policy.conf": 0o644,
    "usr/lib/systemd/system/hepta-tool-session-supervisor@.socket": 0o644,
    "usr/lib/systemd/system/hepta-shadow-watch-collector@.service": 0o644,
    "usr/lib/systemd/system/hepta-shadow-watch-export@.service": 0o644,
    "usr/lib/systemd/system/hepta-shadow-watch-collector@.timer": 0o644,
    "usr/lib/systemd/system/hepta-shadow-watch-custodian@.service": 0o644,
    "usr/lib/systemd/system/hepta-shadow-watch-custodian-reconcile@.service":
        0o644,
    "usr/lib/systemd/system/hepta-shadow-watch-custodian-reconcile@.timer":
        0o644,
    "usr/lib/tmpfiles.d/heptatrader-agent-os.conf": 0o644,
    "usr/share/doc/heptatrader/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md":
        0o644,
    "usr/share/doc/heptatrader/BROKER-NETWORK-ISOLATION.md": 0o644,
    "usr/share/doc/heptatrader/EURUSD-CONFIRMED-MOMENTUM-SHADOW-V2.md":
        0o644,
    "usr/share/doc/heptatrader/RUNBOOK-STARTUP.md": 0o644,
    "usr/share/doc/heptatrader/examples/hepta-tool-gateway.env.example":
        0o644,
    "usr/share/doc/heptatrader/examples/"
    "hepta-tool-gateway-domain.env.example": 0o644,
    "usr/share/doc/heptatrader/examples/"
    "hepta-agent-trust-domain.json.example": 0o644,
    "usr/share/doc/heptatrader/examples/"
    "hepta-agent-trust-domain-policy-v1.json": 0o644,
    "usr/share/doc/heptatrader/examples/"
    "hepta-agent-host-identity.conf.example": 0o644,
    "usr/share/doc/heptatrader/examples/"
    "hepta-agent-broker-egress-policy.conf.example": 0o644,
    "usr/share/doc/heptatrader/examples/"
    "hepta-agent-trust-domain-paper-identities-v1.json.example": 0o644,
    "usr/share/doc/heptatrader/examples/"
    "hepta-shadow-watch-domain.env.example": 0o644,
    "usr/share/heptatrader/plugins/heptatrader-agent-os/"
    ".codex-plugin/plugin.json": 0o644,
    "usr/share/heptatrader/plugins/heptatrader-agent-os/.mcp.json": 0o644,
    "usr/share/heptatrader/plugins/heptatrader-agent-os/README.md": 0o644,
    "usr/share/heptatrader/.agents/plugins/marketplace.json": 0o644,
    "usr/share/heptatrader/hepta-service-identities-v1.json": 0o644,
    "usr/share/heptatrader/hepta-broker-network-policy-v1.json": 0o644,
    "usr/share/heptatrader/systemd/hepta-systemd-gate.apparmor": 0o644,
    "usr/share/heptatrader/strategies/"
    "eurusd-confirmed-momentum-shadow-v2.json": 0o644,
}
EXECUTION_FILES: dict[str, int] = {
    "usr/libexec/hepta-executiond": 0o755,
    "usr/share/heptatrader/hepta-service-identities-v1.json": 0o644,
    "usr/share/doc/heptatrader/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md":
        0o644,
    "usr/share/doc/heptatrader/examples/"
    "hepta-execution-simulator.env.example": 0o644,
    "usr/lib/systemd/system/hepta-execution-simulator.service": 0o644,
    "usr/lib/systemd/system/hepta-execution-simulator.socket": 0o644,
    "usr/lib/systemd/system/hepta-execution-events-simulator.socket": 0o644,
    "usr/lib/systemd/system/hepta-execution-simulator@.service": 0o644,
    "usr/lib/systemd/system/hepta-execution-simulator@.socket": 0o644,
    "usr/lib/systemd/system/hepta-execution-events-simulator@.socket": 0o644,
}
PRODUCT_FILES = AGENT_FILES | EXECUTION_FILES
PRODUCT_FILE_COUNT = len(PRODUCT_FILES)
ELF_FILES = frozenset({
    "usr/bin/heptactl",
    "usr/bin/hepta-sessionctl",
    "usr/libexec/hepta-tool-gatewayd",
    "usr/libexec/hepta-executiond",
})
PYTHON_FILES = frozenset({
    "usr/bin/hepta-campaignctl",
    "usr/libexec/hepta-paper-receipt-contracts",
    "usr/libexec/hepta-paper-receipt-contracts-v2-compat",
    "usr/libexec/hepta-p1-paper-canary-backend-adapter",
    "usr/libexec/hepta-p1-paper-canary-crash-emergency-closer",
    "usr/libexec/hepta-p1-paper-canary-executor",
    "usr/libexec/hepta-p1-paper-canary-handoff-producer",
    "usr/libexec/hepta-p1-paper-canary-launch-joiner",
    "usr/libexec/hepta-p1-paper-canary-owner-provisioner",
    "usr/libexec/hepta-p1-paper-canary-root-coordinator",
    "usr/libexec/hepta-p1-paper-canary-terminal-prover",
    "usr/libexec/hepta-mcp-server",
    "usr/libexec/hepta-agent-mcp-launcher",
    "usr/libexec/hepta-agent-session-bootstrap",
    "usr/libexec/hepta-shadow-watch-custodian",
    "usr/libexec/hepta_agent_trust_domain.py",
    "usr/libexec/hepta-shadow-watch-collector",
    "usr/libexec/hepta-shadow-watch-exporter",
    "usr/libexec/hepta-shadow-host-installer",
    "usr/libexec/hepta-p1-shadow-host-controller",
    "usr/libexec/hepta-p1-load-probe-validator",
    "usr/libexec/build-hepta-p1-observation-policy",
    "usr/libexec/hepta-p1-shadow-observer-controller",
    "usr/libexec/hepta-p1-shadow-admission-launcher",
    "usr/libexec/hepta-p1-safety-soak-campaign-freezer",
    "usr/libexec/hepta-p1-safety-soak-policy-planner",
    "usr/libexec/hepta-p1-safety-soak-campaign-coordinator",
    "usr/libexec/hepta-p1-safety-soak-observer-worker",
    "usr/libexec/hepta-p1-safety-soak-recorder-worker",
    "usr/libexec/hepta-p1-safety-soak-fault-pin-producer",
    "usr/libexec/hepta-p1-safety-soak-evidence-recorder",
    "usr/libexec/hepta-p1-safety-soak-independent-observer",
    "usr/libexec/hepta-p1-safety-soak-root-fault-injector",
    "usr/libexec/hepta-p1-safety-soak-auditor",
    "usr/libexec/hepta-p1-watch-to-paper-handoff",
    "usr/libexec/hepta-p1-paper-kill-switch-bootstrap",
    "usr/libexec/hepta-p1-paper-admission-verifier",
    "usr/libexec/hepta-p1-paper-zero-exposure-attestor",
    "usr/libexec/hepta-p1-paper-zero-exposure-snapshot-producer",
    "usr/libexec/hepta-p1-paper-terminal-witness-verifier",
    "usr/libexec/hepta-rootful-review-closure-consumer",
    "usr/libexec/hepta-rootful-systemd-environment-provenance",
    "usr/libexec/hepta_rootful_review_closure_consumer.py",
    "usr/libexec/hepta-release-validation-closure-verifier",
    *RELEASE_VALIDATION_COMPANION_FILES,
    "usr/libexec/hepta-p1-watch-profile-deployer",
    "usr/libexec/hepta-p1-watch-activation-transaction",
    "usr/libexec/hepta-bounded-shadow-closure-verifier",
    "usr/libexec/hepta-official-source-capture",
    "usr/libexec/hepta_bounded_shadow_observer.py",
    "usr/libexec/hepta_market_context_builder.py",
    "usr/libexec/hepta_market_evidence_normalizer.py",
    "usr/libexec/hepta_market_official_source_extractor.py",
    "usr/libexec/hepta_eurusd_confirmed_momentum_strategy.py",
    "usr/libexec/hepta_shadow_market_history.py",
    "usr/libexec/hepta_strategy_replay_evaluator.py",
    "usr/libexec/hepta_strategy_shadow_runner.py",
    "usr/libexec/validate_hepta_strategy_decision_receipt.py",
    "usr/libexec/hepta_strategy_contracts.py",
    "usr/libexec/hepta-broker-egress-policy",
    "usr/libexec/hepta-local-paper-control",
    "usr/libexec/check-hepta-agent-os-provisioned-host",
})
PYTHON_MODULE_FILES = frozenset(RELEASE_VALIDATION_PACKAGE_FILES)
PYTHON_SHEBANGS = frozenset({
    "#!/usr/bin/python3",
    "#!/usr/bin/env python3",
    "#!/usr/bin/env -S /usr/bin/python3.12 -I -S",
})
SYSTEMD_EXECSTART_CLOSURE = {
    "usr/lib/systemd/system/hepta-tool-gateway.service":
        ("/usr/libexec/hepta-tool-gatewayd",
         "/usr/libexec/hepta-tool-gatewayd"),
    "usr/lib/systemd/system/hepta-tool-gateway@.service":
        ("/usr/libexec/hepta-tool-gatewayd",
         "/usr/libexec/hepta-tool-gatewayd"),
    "usr/lib/systemd/system/hepta-execution-simulator.service":
        ("/usr/libexec/hepta-executiond",
         "/usr/libexec/hepta-executiond"),
    "usr/lib/systemd/system/hepta-execution-simulator@.service":
        ("/usr/libexec/hepta-executiond",
         "/usr/libexec/hepta-executiond"),
    "usr/lib/systemd/system/hepta-broker-egress-policy.service":
        ("/usr/bin/python3.12 -I -S ${CREDENTIALS_DIRECTORY}/"
         "hepta-broker-egress-policy.py --supervise-deny-all "
         "--paper-identities /etc/heptatrader/"
         "hepta-agent-trust-domain-paper-identities-v1.json",
         "/usr/bin/python3.12"),
    "usr/lib/systemd/system/hepta-p1-paper-canary-capture.service":
        ("/usr/bin/python3.12 -I -S /run/credentials/"
         "hepta-p1-paper-canary-capture/"
         "hepta-p1-paper-canary-launch-joiner.py --peer-capture",
         "/usr/bin/python3.12"),
    "usr/lib/systemd/system/hepta-p1-paper-canary-executor.service":
        ("/usr/bin/python3.12 -I -S /run/credentials/"
         "hepta-p1-paper-canary-executor/"
         "hepta-p1-paper-canary-executor.py",
         "/usr/bin/python3.12"),
    "usr/lib/systemd/system/"
    "hepta-p1-paper-canary-root-coordinator.service":
        ("/usr/bin/python3.12 -I -S /run/credentials/"
         "hepta-p1-paper-canary-root-coordinator/"
         "hepta-p1-paper-canary-root-coordinator.py --service-run",
         "/usr/bin/python3.12"),
    "usr/lib/systemd/system/hepta-p1-watch-activation.service":
        ("/usr/bin/python3.12 -I -S ${CREDENTIALS_DIRECTORY}/"
         "hepta-p1-watch-activation-transaction.py activate",
         "/usr/bin/python3.12"),
    "usr/lib/systemd/system/hepta-p1-watch-activation-reconcile.service":
        ("/usr/bin/python3.12 -I -S ${CREDENTIALS_DIRECTORY}/"
         "hepta-p1-watch-activation-transaction.py reconcile",
         "/usr/bin/python3.12"),
    "usr/lib/systemd/system/hepta-p1-safety-soak-campaign@.service":
        ("/usr/libexec/hepta-p1-safety-soak-campaign-coordinator --run "
         "--launch-contract /etc/heptatrader/p1-safety-soak/%i.json",
         "/usr/libexec/hepta-p1-safety-soak-campaign-coordinator"),
    "usr/lib/systemd/system/hepta-p1-safety-soak-observer-worker@.service":
        ("/usr/libexec/hepta-p1-safety-soak-observer-worker --run "
         "--runtime-manifest /var/lib/hepta/p1-safety-soak/%i/"
         "runtime-manifest.json --expected-runtime-manifest-file-sha256 "
         "${HEPTA_P1_RUNTIME_FILE_SHA256}",
         "/usr/libexec/hepta-p1-safety-soak-observer-worker"),
    "usr/lib/systemd/system/hepta-p1-safety-soak-recorder-worker@.service":
        ("/usr/libexec/hepta-p1-safety-soak-recorder-worker --run "
         "--runtime-manifest /var/lib/hepta/p1-safety-soak/%i/"
         "runtime-manifest.json --expected-runtime-manifest-file-sha256 "
         "${HEPTA_P1_RUNTIME_FILE_SHA256}",
         "/usr/libexec/hepta-p1-safety-soak-recorder-worker"),
    "usr/lib/systemd/system/hepta-shadow-watch-collector@.service":
        ("/usr/libexec/hepta-shadow-watch-collector --domain-config "
         "/etc/heptatrader/trust-domains/uid-${HEPTA_SHADOW_AGENT_UID}.json --output "
         "/var/lib/hepta-shadow-watch-%i/private/snapshot.json "
         "--instrument EUR.USD",
         "/usr/libexec/hepta-shadow-watch-collector"),
    "usr/lib/systemd/system/hepta-shadow-watch-export@.service":
        ("/usr/libexec/hepta-shadow-watch-exporter --source "
         "/var/lib/hepta-shadow-watch-%i/private/snapshot.json "
         "--destination /run/hepta-shadow-watch-export-%i/snapshot.json "
         "--agent-uid ${HEPTA_SHADOW_AGENT_UID} --agent-gid "
         "${HEPTA_SHADOW_AGENT_GID} --reader-uid "
         "${HEPTA_SHADOW_READER_UID} --reader-gid "
         "${HEPTA_SHADOW_READER_GID} --lease-receipt-source "
         "/run/hepta-agent-%i/sessions/shadow-watch-lease-receipt.json "
         "--lease-receipt-destination /run/hepta-shadow-watch-export-%i/"
         "shadow-watch-lease-receipt.json --export-receipt-destination "
         "/run/hepta-shadow-watch-export-%i/"
         "shadow-watch-export-receipt.json",
         "/usr/libexec/hepta-shadow-watch-exporter"),
    "usr/lib/systemd/system/hepta-shadow-watch-custodian@.service":
        ("/usr/libexec/hepta-shadow-watch-custodian --domain-config "
         "/etc/heptatrader/trust-domains/%i.json supervise",
         "/usr/libexec/hepta-shadow-watch-custodian"),
    "usr/lib/systemd/system/"
    "hepta-shadow-watch-custodian-reconcile@.service":
        ("/usr/libexec/hepta-shadow-watch-custodian --domain-config "
         "/etc/heptatrader/trust-domains/%i.json reconcile",
         "/usr/libexec/hepta-shadow-watch-custodian"),
    "usr/lib/systemd/system/hepta-local-paper-fail-close@.service":
        ("/usr/bin/python3.12 -I -S ${CREDENTIALS_DIRECTORY}/"
         "hepta-local-paper-control.py guardian-fail-close --domain %i",
         "/usr/bin/python3.12"),
    "usr/lib/systemd/system/hepta-p1-paper-terminal-cutoff@.service":
        ("/usr/bin/python3.12 -I -S %d/hepta-p1-paper-terminal-witness-verifier "
         "--record-cutoff --request %d/hepta-p1-paper-terminal-cutoff-request",
         "/usr/bin/python3.12"),
    "usr/lib/systemd/system/hepta-p1-paper-terminal-witness-verifier@.service":
        ("/usr/bin/python3.12 -I -S %d/hepta-p1-paper-terminal-witness-verifier "
         "--run --request %d/hepta-p1-paper-terminal-witness-request",
         "/usr/bin/python3.12"),
}

# Credential-loaded Python services intentionally execute the fixed system
# interpreter while systemd snapshots the reviewed packaged helper into the
# unit credential directory.  Keep that source edge explicit instead of
# pretending the first ExecStart token is the packaged helper.
SYSTEMD_CREDENTIAL_SOURCE_CLOSURE = {
    "usr/lib/systemd/system/hepta-broker-egress-policy.service":
        (("hepta-broker-egress-policy.py",
          "/usr/libexec/hepta-broker-egress-policy"),
         ("hepta-local-paper-control.py",
          "/usr/libexec/hepta-local-paper-control")),
    "usr/lib/systemd/system/hepta-p1-paper-canary-capture.service": (
        ("hepta-p1-paper-canary-launch-joiner.py",
         "/usr/libexec/hepta-p1-paper-canary-launch-joiner"),
    ),
    "usr/lib/systemd/system/hepta-p1-paper-canary-executor.service": (
        ("hepta-p1-paper-canary-executor.py",
         "/usr/libexec/hepta-p1-paper-canary-executor"),
    ),
    "usr/lib/systemd/system/"
    "hepta-p1-paper-canary-root-coordinator.service": (
        ("hepta-p1-paper-canary-root-coordinator.py",
         "/usr/libexec/hepta-p1-paper-canary-root-coordinator"),
        ("hepta-p1-paper-canary-launch-joiner.py",
         "/usr/libexec/hepta-p1-paper-canary-launch-joiner"),
        ("hepta-p1-paper-canary-owner-provisioner.py",
         "/usr/libexec/hepta-p1-paper-canary-owner-provisioner"),
        ("hepta-p1-paper-canary-executor.py",
         "/usr/libexec/hepta-p1-paper-canary-executor"),
        ("hepta-p1-paper-canary-crash-emergency-closer.py",
         "/usr/libexec/hepta-p1-paper-canary-crash-emergency-closer"),
        ("hepta-p1-paper-canary-terminal-prover.py",
         "/usr/libexec/hepta-p1-paper-canary-terminal-prover"),
        ("hepta-local-paper-control.py",
         "/usr/libexec/hepta-local-paper-control"),
    ),
    "usr/lib/systemd/system/hepta-p1-watch-activation.service": (
        ("hepta-p1-watch-activation-transaction.py",
         "/usr/libexec/hepta-p1-watch-activation-transaction"),
        ("hepta-p1-watch-profile-deployer.py",
         "/usr/libexec/hepta-p1-watch-profile-deployer"),
        ("hepta-broker-egress-policy.py",
         "/usr/libexec/hepta-broker-egress-policy"),
        ("hepta-shadow-host-installer.py",
         "/usr/libexec/hepta-shadow-host-installer"),
    ),
    "usr/lib/systemd/system/hepta-p1-watch-activation-reconcile.service": (
        ("hepta-p1-watch-activation-transaction.py",
         "/usr/libexec/hepta-p1-watch-activation-transaction"),
        ("hepta-p1-watch-profile-deployer.py",
         "/usr/libexec/hepta-p1-watch-profile-deployer"),
        ("hepta-broker-egress-policy.py",
         "/usr/libexec/hepta-broker-egress-policy"),
        ("hepta-shadow-host-installer.py",
         "/usr/libexec/hepta-shadow-host-installer"),
    ),
    "usr/lib/systemd/system/hepta-local-paper-fail-close@.service": (
        ("hepta-local-paper-control.py",
         "/usr/libexec/hepta-local-paper-control"),
    ),
    "usr/lib/systemd/system/hepta-p1-paper-terminal-cutoff@.service": (
        ("hepta-p1-paper-terminal-witness-verifier",
         "/usr/libexec/hepta-p1-paper-terminal-witness-verifier"),
    ),
    "usr/lib/systemd/system/hepta-p1-paper-terminal-witness-verifier@.service": (
        ("hepta-p1-paper-terminal-witness-verifier",
         "/usr/libexec/hepta-p1-paper-terminal-witness-verifier"),
    ),
}

SYSTEMD_RUNTIME_CREDENTIAL_SOURCE_CLOSURE = {
    "usr/lib/systemd/system/hepta-p1-paper-canary-capture.service": (
        ("capture-request.v1.json",
         "/run/hepta-p1-paper-canary/active-capture-request.v1.json"),
        ("session.token",
         "/run/hepta-p1-paper-canary/read-only-capture-session.token"),
    ),
    "usr/lib/systemd/system/hepta-p1-paper-canary-executor.service": (
        ("execution-handoff.v1.json",
         "/run/hepta-p1-paper-canary/active-execution-handoff.v1.json"),
    ),
    "usr/lib/systemd/system/"
    "hepta-p1-paper-canary-root-coordinator.service": (
        ("active-coordinator-request.v1.json",
         "/run/hepta-p1-paper-canary/active-coordinator-request.v1.json"),
    ),
    "usr/lib/systemd/system/hepta-p1-paper-terminal-cutoff@.service": (
        ("hepta-p1-paper-terminal-cutoff-request",
         "/run/hepta/paper-terminal-witness/%i/cutoff-request.v1.json"),
    ),
    "usr/lib/systemd/system/hepta-p1-paper-terminal-witness-verifier@.service": (
        ("hepta-p1-paper-terminal-witness-request",
         "/run/hepta/paper-terminal-witness/%i/verifier-request.v1.json"),
    ),
}


def _contract_lines(value: str) -> tuple[str, ...]:
    return tuple(
        line.strip() for line in value.strip().splitlines()
        if line.strip() and not line.lstrip().startswith("#"))


# This is a semantic allowlist, not a byte hash. Comments and blank lines may
# change, but every effective section and directive must remain reviewed.
# Gateway [Install] metadata is intentionally present: putting a regular unit
# file in a passive archive neither enables nor starts it.
APPROVED_SYSTEMD_SEMANTICS = {
    "usr/lib/systemd/system/hepta-broker-egress-policy.service":
        _contract_lines("""
            [Unit]
            Description=HeptaTrader broker-port egress boundary
            Documentation=file:/usr/share/doc/heptatrader/BROKER-NETWORK-ISOLATION.md
            DefaultDependencies=no
            After=systemd-modules-load.service local-fs.target
            Before=network-pre.target hepta-tool-gateway.service hepta-execution-ib-paper.service
            Wants=network-pre.target
            [Service]
            Type=notify
            NotifyAccess=main
            User=root
            Group=root
            LoadCredential=hepta-broker-egress-policy.py:/usr/libexec/hepta-broker-egress-policy
            LoadCredential=hepta-local-paper-control.py:/usr/libexec/hepta-local-paper-control
            ExecStartPre=/usr/bin/python3.12 -I -S ${CREDENTIALS_DIRECTORY}/hepta-local-paper-control.py reconcile-before-broker
            ExecStart=/usr/bin/python3.12 -I -S ${CREDENTIALS_DIRECTORY}/hepta-broker-egress-policy.py --supervise-deny-all --paper-identities /etc/heptatrader/hepta-agent-trust-domain-paper-identities-v1.json
            ExecStopPost=/usr/bin/python3.12 -I -S ${CREDENTIALS_DIRECTORY}/hepta-broker-egress-policy.py --tighten-deny-all
            UMask=0077
            RuntimeDirectory=hepta-broker-egress-policy
            RuntimeDirectoryMode=0700
            RuntimeDirectoryPreserve=yes
            WatchdogSec=15s
            WatchdogSignal=SIGTERM
            TimeoutStopSec=30s
            KillSignal=SIGTERM
            Restart=no
            NoNewPrivileges=yes
            PrivateTmp=yes
            PrivateDevices=yes
            ProtectSystem=strict
            ProtectHome=yes
            ProtectKernelTunables=yes
            ProtectKernelModules=yes
            ProtectKernelLogs=yes
            ProtectControlGroups=yes
            ProtectClock=yes
            ProtectHostname=yes
            RestrictSUIDSGID=yes
            RestrictRealtime=yes
            RestrictNamespaces=yes
            LockPersonality=yes
            MemoryDenyWriteExecute=yes
            CapabilityBoundingSet=CAP_NET_ADMIN
            AmbientCapabilities=
            RestrictAddressFamilies=AF_UNIX AF_NETLINK
            ReadOnlyPaths=/usr/share/heptatrader
            ReadWritePaths=/etc/heptatrader
            BindReadOnlyPaths=-/etc/heptatrader/credentials
            BindReadOnlyPaths=-/etc/heptatrader/paper-campaigns
            BindReadOnlyPaths=-/etc/heptatrader/p1-safety-soak
            BindReadOnlyPaths=-/etc/heptatrader/trust-domains
            BindReadOnlyPaths=-/etc/heptatrader/hepta-agent-trust-domain-policy-v1.json
            BindReadOnlyPaths=-/etc/heptatrader/hepta-agent-trust-domain.json
            BindReadOnlyPaths=-/etc/heptatrader/hepta-broker-network-policy-v1.json
            BindReadOnlyPaths=-/etc/heptatrader/hepta-ib-paper-domain-authorizations-v1.json
            BindReadOnlyPaths=-/etc/heptatrader/hepta-service-identities-v1.json
            BindReadOnlyPaths=-/etc/heptatrader/local-ai-paper-agent.env
            BindReadOnlyPaths=-/etc/heptatrader/local-ai-paper-deployment-v1.json
            BindReadOnlyPaths=-/etc/heptatrader/local-ai-paper-certified-install-closure-v1.json
            BindReadOnlyPaths=-/etc/heptatrader/hepta-tool-gateway.env
            BindReadOnlyPaths=-/etc/heptatrader/hepta-execution-simulator.env
            BindReadOnlyPaths=-/etc/heptatrader/hepta-execution-ib-paper.env
            BindReadOnlyPaths=-/etc/heptatrader/hepta-supervisor-lease.key
            BindReadOnlyPaths=-/etc/heptatrader/p1-paper-account-evidence-ed25519.pub
            BindReadOnlyPaths=-/etc/heptatrader/rootful-systemd-review-ed25519.pub
            BindReadOnlyPaths=-/etc/heptatrader/paper-account-authority.pub
            BindReadOnlyPaths=-/etc/heptatrader/release-causal-openssl.cnf
            BindReadOnlyPaths=-/etc/heptatrader/heptatrader-evidence-receipt-trust-v1.json
            ReadOnlyPaths=-/var/lib/hepta-local-ai-paper-agent
            ReadWritePaths=/etc/systemd/system/hepta-broker-egress-policy.service.d
            ReadWritePaths=-/run/hepta-local-paper-control
            ReadWritePaths=-/run/hepta/ib-paper-host-authority
            StandardOutput=journal
            StandardError=journal
            [Install]
            WantedBy=multi-user.target
        """),
    "usr/lib/systemd/system/hepta-p1-watch-activation.service":
        _contract_lines("""
            [Unit]
            Description=HeptaTrader fixed round114 alpha WATCH Gateway activation
            Documentation=file:/usr/share/doc/heptatrader/BROKER-NETWORK-ISOLATION.md
            DefaultDependencies=no
            After=local-fs.target systemd-remount-fs.service
            OnFailure=hepta-p1-watch-activation-reconcile.service
            [Service]
            Type=oneshot
            User=root
            Group=root
            LoadCredential=hepta-p1-watch-activation-transaction.py:/usr/libexec/hepta-p1-watch-activation-transaction
            LoadCredential=hepta-p1-watch-profile-deployer.py:/usr/libexec/hepta-p1-watch-profile-deployer
            LoadCredential=hepta-broker-egress-policy.py:/usr/libexec/hepta-broker-egress-policy
            LoadCredential=hepta-shadow-host-installer.py:/usr/libexec/hepta-shadow-host-installer
            Environment=HEPTA_ACTIVATION_REQUIRE_CREDENTIALS=1
            ExecStart=/usr/bin/python3.12 -I -S ${CREDENTIALS_DIRECTORY}/hepta-p1-watch-activation-transaction.py activate
            UMask=0077
            TimeoutStartSec=5min
            NoNewPrivileges=yes
            PrivateTmp=yes
            PrivateDevices=yes
            ProtectSystem=strict
            ProtectHome=yes
            ProtectKernelTunables=yes
            ProtectKernelModules=yes
            ProtectKernelLogs=yes
            ProtectControlGroups=yes
            ProtectClock=yes
            ProtectHostname=yes
            RestrictSUIDSGID=yes
            RestrictRealtime=yes
            RestrictNamespaces=yes
            LockPersonality=yes
            MemoryDenyWriteExecute=yes
            CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE CAP_DAC_READ_SEARCH CAP_FOWNER CAP_SYS_PTRACE CAP_NET_ADMIN
            AmbientCapabilities=
            RestrictAddressFamilies=AF_UNIX AF_NETLINK
            ReadOnlyPaths=/etc/heptatrader
            ReadOnlyPaths=/usr/libexec
            ReadOnlyPaths=/usr/lib/systemd/system
            ReadWritePaths=/etc/systemd/system
            ReadWritePaths=/run/systemd/system
            ReadWritePaths=/var/lib/hepta/p1-admission
            ReadWritePaths=/var/lib/hepta/shadow-observation
            ReadWritePaths=/var/lib/heptatrader
            StandardOutput=journal
            StandardError=journal
        """),
    "usr/lib/systemd/system/hepta-p1-watch-activation-reconcile.service":
        _contract_lines("""
            [Unit]
            Description=Reconcile HeptaTrader fixed round114 alpha WATCH activation
            Documentation=file:/usr/share/doc/heptatrader/BROKER-NETWORK-ISOLATION.md
            DefaultDependencies=no
            After=local-fs.target systemd-remount-fs.service hepta-p1-watch-activation.service
            [Service]
            Type=oneshot
            User=root
            Group=root
            LoadCredential=hepta-p1-watch-activation-transaction.py:/usr/libexec/hepta-p1-watch-activation-transaction
            LoadCredential=hepta-p1-watch-profile-deployer.py:/usr/libexec/hepta-p1-watch-profile-deployer
            LoadCredential=hepta-broker-egress-policy.py:/usr/libexec/hepta-broker-egress-policy
            LoadCredential=hepta-shadow-host-installer.py:/usr/libexec/hepta-shadow-host-installer
            Environment=HEPTA_ACTIVATION_REQUIRE_CREDENTIALS=1
            ExecStart=/usr/bin/python3.12 -I -S ${CREDENTIALS_DIRECTORY}/hepta-p1-watch-activation-transaction.py reconcile
            UMask=0077
            TimeoutStartSec=3min
            NoNewPrivileges=yes
            PrivateTmp=yes
            PrivateDevices=yes
            ProtectSystem=strict
            ProtectHome=yes
            ProtectKernelTunables=yes
            ProtectKernelModules=yes
            ProtectKernelLogs=yes
            ProtectControlGroups=yes
            ProtectClock=yes
            ProtectHostname=yes
            RestrictSUIDSGID=yes
            RestrictRealtime=yes
            RestrictNamespaces=yes
            LockPersonality=yes
            MemoryDenyWriteExecute=yes
            CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE CAP_DAC_READ_SEARCH CAP_FOWNER CAP_SYS_PTRACE CAP_NET_ADMIN
            AmbientCapabilities=
            RestrictAddressFamilies=AF_UNIX AF_NETLINK
            ReadOnlyPaths=/etc/heptatrader
            ReadOnlyPaths=/usr/libexec
            ReadOnlyPaths=/usr/lib/systemd/system
            ReadWritePaths=/etc/systemd/system
            ReadWritePaths=/run/systemd/system
            ReadWritePaths=/var/lib/hepta/p1-admission
            ReadWritePaths=/var/lib/hepta/shadow-observation
            ReadWritePaths=/var/lib/heptatrader
            StandardOutput=journal
            StandardError=journal
        """),
    "usr/lib/systemd/system/hepta-p1-watch-activation-reconcile.timer":
        _contract_lines("""
            [Unit]
            Description=Periodically reconcile HeptaTrader round114 WATCH activation
            [Timer]
            OnActiveSec=30s
            OnUnitActiveSec=30s
            AccuracySec=1s
            Unit=hepta-p1-watch-activation-reconcile.service
            [Install]
            WantedBy=timers.target
        """),
    "usr/lib/systemd/system/hepta-tool-gateway.socket": _contract_lines("""
        [Unit]
        Description=HeptaTrader Agent-exclusive Tool Gateway socket
        [Socket]
        ListenStream=/run/hepta-agent/tools.sock
        FileDescriptorName=hepta-tool
        SocketMode=0600
        DirectoryMode=0711
        SocketUser=hepta-agent
        SocketGroup=hepta-agent
        Service=hepta-tool-gateway.service
        RemoveOnStop=yes
        [Install]
        WantedBy=sockets.target
    """),
    "usr/lib/systemd/system/hepta-tool-gateway.service": _contract_lines("""
        [Unit]
        Description=HeptaTrader Agent OS remote Tool Gateway
        Documentation=file:/usr/share/doc/heptatrader/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md
        Requires=hepta-tool-gateway.socket hepta-tool-session-supervisor.socket
        After=hepta-tool-gateway.socket hepta-tool-session-supervisor.socket
        [Service]
        Type=simple
        User=hepta-gateway
        Group=hepta-gateway
        ExecStart=/usr/libexec/hepta-tool-gatewayd
        EnvironmentFile=/etc/heptatrader/hepta-tool-gateway.env
        Environment=HEPTA_TOOL_SOCKET=/run/hepta-agent/tools.sock
        Sockets=hepta-tool-gateway.socket hepta-tool-session-supervisor.socket
        Environment=HEPTA_TOOL_SUPERVISOR_LEASE_STORE=/var/lib/hepta-tool-gateway/session-leases.hsl2
        Environment=HEPTA_TOOL_SUPERVISOR_AUDIT_JOURNAL=/var/lib/hepta-tool-gateway/session-audit.jsonl
        LoadCredential=hepta-supervisor-lease-key:/etc/heptatrader/hepta-supervisor-lease.key
        RuntimeDirectory=hepta-tool-gateway
        RuntimeDirectoryMode=0700
        RuntimeDirectoryPreserve=yes
        StateDirectory=hepta-tool-gateway
        StateDirectoryMode=0700
        UMask=0077
        Restart=on-failure
        RestartSec=2s
        TimeoutStopSec=20s
        KillMode=mixed
        NoNewPrivileges=yes
        PrivateTmp=yes
        PrivateDevices=yes
        PrivateNetwork=yes
        ProtectSystem=strict
        ProtectHome=yes
        ProtectKernelTunables=yes
        ProtectKernelModules=yes
        ProtectKernelLogs=yes
        ProtectControlGroups=yes
        ProtectClock=yes
        ProtectHostname=yes
        RestrictSUIDSGID=yes
        RestrictRealtime=yes
        LockPersonality=yes
        MemoryDenyWriteExecute=yes
        CapabilityBoundingSet=
        AmbientCapabilities=
        RestrictAddressFamilies=AF_UNIX
        SystemCallArchitectures=native
        SystemCallFilter=@system-service
        SystemCallErrorNumber=EPERM
        ReadWritePaths=/run/hepta-tool-gateway /var/lib/hepta-tool-gateway
        [Install]
        WantedBy=multi-user.target
    """),
    "usr/lib/systemd/system/hepta-tool-session-supervisor.socket":
        _contract_lines("""
            [Unit]
            Description=HeptaTrader OS-only session supervisor socket
            [Socket]
            ListenStream=/run/hepta-tool-gateway/session-supervisor.sock
            FileDescriptorName=hepta-supervisor
            SocketMode=0600
            DirectoryMode=0700
            SocketUser=hepta-gateway
            SocketGroup=hepta-gateway
            Service=hepta-tool-gateway.service
            RemoveOnStop=yes
            [Install]
            WantedBy=sockets.target
        """),
    "usr/lib/systemd/system/hepta-tool-gateway@.socket": _contract_lines("""
        [Unit]
        Description=HeptaTrader trust-domain Tool Gateway socket (%i)
        [Socket]
        ListenStream=/run/hepta-agent-%i/tools.sock
        FileDescriptorName=hepta-tool
        SocketMode=0600
        DirectoryMode=0711
        SocketUser=hepta-agent-%i
        SocketGroup=hepta-agent-%i
        Service=hepta-tool-gateway@%i.service
        RemoveOnStop=yes
        [Install]
        WantedBy=sockets.target
    """),
    "usr/lib/systemd/system/hepta-tool-gateway@.service": _contract_lines("""
        [Unit]
        Description=HeptaTrader trust-domain Tool Gateway (%i)
        Documentation=file:/usr/share/doc/heptatrader/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md
        Requires=hepta-tool-gateway@%i.socket hepta-tool-session-supervisor@%i.socket
        After=hepta-tool-gateway@%i.socket hepta-tool-session-supervisor@%i.socket
        [Service]
        Type=simple
        User=hepta-gw-%i
        Group=hepta-gw-%i
        SupplementaryGroups=
        ExecStart=/usr/libexec/hepta-tool-gatewayd
        EnvironmentFile=/etc/heptatrader/trust-domains/%i.env
        Environment=HEPTA_TOOL_SOCKET=/run/hepta-agent-%i/tools.sock
        Environment=HEPTA_TOOL_AGENT_ID=%i
        Sockets=hepta-tool-gateway@%i.socket hepta-tool-session-supervisor@%i.socket
        Environment=HEPTA_TOOL_SUPERVISOR_LEASE_STORE=/var/lib/hepta-tool-gateway-%i/session-leases.hsl2
        Environment=HEPTA_TOOL_SUPERVISOR_AUDIT_JOURNAL=/var/lib/hepta-tool-gateway-%i/session-audit.jsonl
        LoadCredential=hepta-supervisor-lease-key:/etc/heptatrader/credentials/trust-domains/%i/hepta-supervisor-lease.key
        RuntimeDirectory=hepta-tool-gateway-%i
        RuntimeDirectoryMode=0700
        RuntimeDirectoryPreserve=yes
        StateDirectory=hepta-tool-gateway-%i
        StateDirectoryMode=0700
        UMask=0077
        Restart=on-failure
        RestartSec=2s
        TimeoutStopSec=20s
        KillMode=mixed
        NoNewPrivileges=yes
        PrivateTmp=yes
        PrivateDevices=yes
        PrivateNetwork=yes
        ProtectSystem=strict
        ProtectHome=yes
        ProtectKernelTunables=yes
        ProtectKernelModules=yes
        ProtectKernelLogs=yes
        ProtectControlGroups=yes
        ProtectClock=yes
        ProtectHostname=yes
        RestrictSUIDSGID=yes
        RestrictRealtime=yes
        LockPersonality=yes
        MemoryDenyWriteExecute=yes
        CapabilityBoundingSet=
        AmbientCapabilities=
        RestrictAddressFamilies=AF_UNIX
        SystemCallArchitectures=native
        SystemCallFilter=@system-service
        SystemCallErrorNumber=EPERM
        ReadWritePaths=/run/hepta-tool-gateway-%i /var/lib/hepta-tool-gateway-%i
        [Install]
        WantedBy=multi-user.target
    """),
    "usr/lib/systemd/system/hepta-tool-session-supervisor@.socket":
        _contract_lines("""
            [Unit]
            Description=HeptaTrader trust-domain session supervisor socket (%i)
            [Socket]
            ListenStream=/run/hepta-tool-gateway-%i/session-supervisor.sock
            FileDescriptorName=hepta-supervisor
            SocketMode=0600
            DirectoryMode=0700
            SocketUser=hepta-gw-%i
            SocketGroup=hepta-gw-%i
            Service=hepta-tool-gateway@%i.service
            RemoveOnStop=yes
            [Install]
            WantedBy=sockets.target
        """),
    "usr/lib/systemd/system/hepta-execution-simulator.service":
        _contract_lines("""
            [Unit]
            Description=HeptaTrader Simulator-only execution authority
            Documentation=file:/usr/share/doc/heptatrader/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md
            Requires=hepta-execution-simulator.socket hepta-execution-events-simulator.socket
            After=hepta-execution-simulator.socket hepta-execution-events-simulator.socket
            Conflicts=hepta-execution-ib-paper.service hepta-execution-ib-paper.socket hepta-execution-events-ib-paper.socket
            [Service]
            Type=simple
            Sockets=hepta-execution-simulator.socket hepta-execution-events-simulator.socket
            User=hepta-exec
            Group=hepta-exec
            WorkingDirectory=/
            ExecStart=/usr/libexec/hepta-executiond
            Environment=HEPTA_EXECUTION_SERVICE_MODE=SIMULATOR
            EnvironmentFile=/etc/heptatrader/hepta-execution-simulator.env
            LoadCredential=hepta-execution-fence:/etc/heptatrader/credentials/hepta-execution-simulator-fence
            StateDirectory=hepta-execution
            StateDirectoryMode=0700
            UMask=0077
            Restart=on-failure
            RestartSec=2s
            TimeoutStopSec=10s
            KillMode=control-group
            NoNewPrivileges=yes
            PrivateTmp=yes
            PrivateDevices=yes
            PrivateNetwork=yes
            ProtectSystem=strict
            ProtectHome=yes
            ProtectKernelTunables=yes
            ProtectKernelModules=yes
            ProtectKernelLogs=yes
            ProtectControlGroups=yes
            ProtectClock=yes
            ProtectHostname=yes
            ProtectProc=invisible
            ProcSubset=pid
            RestrictSUIDSGID=yes
            RestrictRealtime=yes
            RestrictNamespaces=yes
            LockPersonality=yes
            MemoryDenyWriteExecute=yes
            RemoveIPC=yes
            CapabilityBoundingSet=
            AmbientCapabilities=
            RestrictAddressFamilies=AF_UNIX
            IPAddressDeny=any
            SystemCallArchitectures=native
            SystemCallFilter=@system-service
            SystemCallErrorNumber=EPERM
            ReadWritePaths=/var/lib/hepta-execution
            StandardOutput=journal
            StandardError=journal
        """),
    "usr/lib/systemd/system/hepta-execution-simulator.socket":
        _contract_lines("""
            [Unit]
            Description=HeptaTrader Simulator-only execution authority socket
            Documentation=file:/usr/share/doc/heptatrader/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md
            Conflicts=hepta-execution-ib-paper.service hepta-execution-ib-paper.socket hepta-execution-events-ib-paper.socket
            [Socket]
            ListenStream=/run/hepta-execution/execution.sock
            Accept=no
            Backlog=16
            SocketUser=hepta-gateway
            SocketGroup=hepta-gateway
            SocketMode=0660
            DirectoryMode=0755
            FileDescriptorName=execution
            Service=hepta-execution-simulator.service
            RemoveOnStop=yes
        """),
    "usr/lib/systemd/system/hepta-execution-events-simulator.socket":
        _contract_lines("""
            [Unit]
            Description=HeptaTrader Simulator-only execution event feed socket
            Documentation=file:/usr/share/doc/heptatrader/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md
            Conflicts=hepta-execution-ib-paper.service hepta-execution-ib-paper.socket hepta-execution-events-ib-paper.socket
            [Socket]
            ListenStream=/run/hepta-execution/events.sock
            Accept=no
            Backlog=32
            SocketUser=hepta-gateway
            SocketGroup=hepta-gateway
            SocketMode=0660
            DirectoryMode=0755
            FileDescriptorName=events
            Service=hepta-execution-simulator.service
            RemoveOnStop=yes
        """),
    "usr/lib/systemd/system/hepta-execution-simulator@.service":
        _contract_lines("""
            [Unit]
            Description=HeptaTrader trust-domain Simulator execution authority (%i)
            Documentation=file:/usr/share/doc/heptatrader/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md
            Requires=hepta-execution-simulator@%i.socket hepta-execution-events-simulator@%i.socket
            After=hepta-execution-simulator@%i.socket hepta-execution-events-simulator@%i.socket
            Conflicts=hepta-execution-ib-paper.service hepta-execution-ib-paper.socket hepta-execution-events-ib-paper.socket hepta-execution-ib-paper@%i.service hepta-execution-ib-paper@%i.socket hepta-execution-events-ib-paper@%i.socket
            [Service]
            Type=simple
            Sockets=hepta-execution-simulator@%i.socket hepta-execution-events-simulator@%i.socket
            User=hepta-exec-%i
            Group=hepta-exec-%i
            WorkingDirectory=/
            ExecStart=/usr/libexec/hepta-executiond
            Environment=HEPTA_EXECUTION_SERVICE_MODE=SIMULATOR
            EnvironmentFile=/etc/heptatrader/trust-domains/%i.execution.env
            LoadCredential=hepta-execution-fence:/etc/heptatrader/credentials/trust-domains/%i/hepta-execution-simulator-fence
            StateDirectory=hepta-execution-%i
            StateDirectoryMode=0700
            UMask=0077
            Restart=on-failure
            RestartSec=2s
            TimeoutStopSec=10s
            KillMode=control-group
            NoNewPrivileges=yes
            PrivateTmp=yes
            PrivateDevices=yes
            PrivateNetwork=yes
            ProtectSystem=strict
            ProtectHome=yes
            ProtectKernelTunables=yes
            ProtectKernelModules=yes
            ProtectKernelLogs=yes
            ProtectControlGroups=yes
            ProtectClock=yes
            ProtectHostname=yes
            ProtectProc=invisible
            ProcSubset=pid
            RestrictSUIDSGID=yes
            RestrictRealtime=yes
            RestrictNamespaces=yes
            LockPersonality=yes
            MemoryDenyWriteExecute=yes
            RemoveIPC=yes
            CapabilityBoundingSet=
            AmbientCapabilities=
            RestrictAddressFamilies=AF_UNIX
            IPAddressDeny=any
            SystemCallArchitectures=native
            SystemCallFilter=@system-service
            SystemCallErrorNumber=EPERM
            ReadWritePaths=/var/lib/hepta-execution-%i
            StandardOutput=journal
            StandardError=journal
        """),
    "usr/lib/systemd/system/hepta-execution-simulator@.socket":
        _contract_lines("""
            [Unit]
            Description=HeptaTrader trust-domain Simulator execution socket (%i)
            Documentation=file:/usr/share/doc/heptatrader/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md
            Conflicts=hepta-execution-ib-paper.service hepta-execution-ib-paper.socket hepta-execution-events-ib-paper.socket hepta-execution-ib-paper@%i.service hepta-execution-ib-paper@%i.socket hepta-execution-events-ib-paper@%i.socket
            [Socket]
            ListenStream=/run/hepta-execution-%i/execution.sock
            Accept=no
            Backlog=16
            SocketUser=hepta-gw-%i
            SocketGroup=hepta-gw-%i
            SocketMode=0600
            DirectoryMode=0711
            FileDescriptorName=execution
            Service=hepta-execution-simulator@%i.service
            RemoveOnStop=yes
        """),
    "usr/lib/systemd/system/hepta-execution-events-simulator@.socket":
        _contract_lines("""
            [Unit]
            Description=HeptaTrader trust-domain Simulator event feed socket (%i)
            Documentation=file:/usr/share/doc/heptatrader/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md
            Conflicts=hepta-execution-ib-paper.service hepta-execution-ib-paper.socket hepta-execution-events-ib-paper.socket hepta-execution-ib-paper@%i.service hepta-execution-ib-paper@%i.socket hepta-execution-events-ib-paper@%i.socket
            [Socket]
            ListenStream=/run/hepta-execution-%i/events.sock
            Accept=no
            Backlog=32
            SocketUser=hepta-gw-%i
            SocketGroup=hepta-gw-%i
            SocketMode=0600
            DirectoryMode=0711
            FileDescriptorName=events
            Service=hepta-execution-simulator@%i.service
            RemoveOnStop=yes
    """),
    "usr/lib/systemd/system/hepta-shadow-watch-collector@.service":
        _contract_lines("""
            [Unit]
            Description=HeptaTrader read-only SHADOW WATCH collector (%i)
            Documentation=file:/usr/share/doc/heptatrader/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md
            Requires=hepta-tool-gateway@%i.socket hepta-tool-session-supervisor@%i.socket
            After=hepta-tool-gateway@%i.socket hepta-tool-session-supervisor@%i.socket
            OnSuccess=hepta-shadow-watch-export@%i.service
            [Service]
            Type=oneshot
            User=hepta-agent-%i
            Group=hepta-agent-%i
            SupplementaryGroups=
            EnvironmentFile=/etc/heptatrader/trust-domains/%i.shadow-watch.env
            ExecStart=/usr/libexec/hepta-shadow-watch-collector --domain-config /etc/heptatrader/trust-domains/uid-${HEPTA_SHADOW_AGENT_UID}.json --output /var/lib/hepta-shadow-watch-%i/private/snapshot.json --instrument EUR.USD
            StateDirectory=hepta-shadow-watch-%i
            StateDirectoryMode=0700
            UMask=0077
            NoNewPrivileges=yes
            PrivateTmp=yes
            PrivateDevices=yes
            PrivateNetwork=yes
            ProtectSystem=strict
            ProtectHome=yes
            ProtectKernelTunables=yes
            ProtectKernelModules=yes
            ProtectKernelLogs=yes
            ProtectControlGroups=yes
            ProtectClock=yes
            ProtectHostname=yes
            RestrictSUIDSGID=yes
            RestrictRealtime=yes
            LockPersonality=yes
            MemoryDenyWriteExecute=yes
            CapabilityBoundingSet=
            AmbientCapabilities=
            RestrictAddressFamilies=AF_UNIX
            SystemCallArchitectures=native
            SystemCallFilter=@system-service
            SystemCallErrorNumber=EPERM
            ReadWritePaths=/var/lib/hepta-shadow-watch-%i
        """),
    "usr/lib/systemd/system/hepta-shadow-watch-export@.service":
        _contract_lines("""
            [Unit]
            Description=HeptaTrader validated SHADOW WATCH snapshot exporter (%i)
            Documentation=file:/usr/share/doc/heptatrader/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md
            After=hepta-shadow-watch-collector@%i.service
            [Service]
            Type=oneshot
            User=root
            Group=root
            EnvironmentFile=/etc/heptatrader/trust-domains/%i.shadow-watch.env
            ExecStart=/usr/libexec/hepta-shadow-watch-exporter --source /var/lib/hepta-shadow-watch-%i/private/snapshot.json --destination /run/hepta-shadow-watch-export-%i/snapshot.json --agent-uid ${HEPTA_SHADOW_AGENT_UID} --agent-gid ${HEPTA_SHADOW_AGENT_GID} --reader-uid ${HEPTA_SHADOW_READER_UID} --reader-gid ${HEPTA_SHADOW_READER_GID} --lease-receipt-source /run/hepta-agent-%i/sessions/shadow-watch-lease-receipt.json --lease-receipt-destination /run/hepta-shadow-watch-export-%i/shadow-watch-lease-receipt.json --export-receipt-destination /run/hepta-shadow-watch-export-%i/shadow-watch-export-receipt.json
            UMask=0077
            NoNewPrivileges=yes
            PrivateTmp=yes
            PrivateDevices=yes
            PrivateNetwork=yes
            ProtectSystem=strict
            ProtectHome=yes
            ProtectKernelTunables=yes
            ProtectKernelModules=yes
            ProtectKernelLogs=yes
            ProtectControlGroups=yes
            ProtectClock=yes
            ProtectHostname=yes
            RestrictSUIDSGID=yes
            RestrictRealtime=yes
            LockPersonality=yes
            MemoryDenyWriteExecute=yes
            CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER
            AmbientCapabilities=
            RestrictAddressFamilies=AF_UNIX
            SystemCallArchitectures=native
            SystemCallFilter=@system-service chown
            SystemCallErrorNumber=EPERM
            ReadWritePaths=-/run/hepta-shadow-watch-export-%i
        """),
    "usr/lib/systemd/system/hepta-shadow-watch-custodian@.service":
        _contract_lines("""
            [Unit]
            Description=HeptaTrader root WATCH authority cleanup custodian (%i)
            Documentation=file:/usr/share/doc/heptatrader/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md
            Requires=hepta-tool-session-supervisor@%i.socket
            After=hepta-tool-session-supervisor@%i.socket
            ConditionPathExists=/var/lib/hepta-shadow-watch-custodian/%i/transaction.json
            StartLimitIntervalSec=0
            [Service]
            Type=simple
            User=root
            Group=root
            ExecStart=/usr/libexec/hepta-shadow-watch-custodian --domain-config /etc/heptatrader/trust-domains/%i.json supervise
            ExecStop=/usr/libexec/hepta-shadow-watch-custodian --domain-config /etc/heptatrader/trust-domains/%i.json close --reason service-stop
            ExecStopPost=/usr/libexec/hepta-shadow-watch-custodian --domain-config /etc/heptatrader/trust-domains/%i.json close --reason service-stop-post
            Restart=on-failure
            RestartSec=1s
            TimeoutStartSec=15s
            TimeoutStopSec=30s
            StateDirectory=hepta-shadow-watch-custodian
            StateDirectoryMode=0700
            UMask=0077
            NoNewPrivileges=yes
            PrivateTmp=yes
            PrivateDevices=yes
            PrivateNetwork=yes
            ProtectSystem=strict
            ProtectHome=yes
            ProtectKernelTunables=yes
            ProtectKernelModules=yes
            ProtectKernelLogs=yes
            ProtectControlGroups=yes
            ProtectClock=yes
            ProtectHostname=yes
            RestrictSUIDSGID=yes
            RestrictRealtime=yes
            LockPersonality=yes
            MemoryDenyWriteExecute=yes
            CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER
            AmbientCapabilities=
            RestrictAddressFamilies=AF_UNIX
            SystemCallArchitectures=native
            SystemCallFilter=@system-service
            SystemCallErrorNumber=EPERM
            ReadWritePaths=/var/lib/hepta-shadow-watch-custodian
            ReadWritePaths=-/run/hepta-agent-%i/sessions
            ReadWritePaths=-/run/hepta-shadow-watch-export-%i
            ReadWritePaths=/var/lib/hepta-shadow-watch-%i/private
        """),
    "usr/lib/systemd/system/"
    "hepta-shadow-watch-custodian-reconcile@.service":
        _contract_lines("""
            [Unit]
            Description=HeptaTrader WATCH custodian durable reconcile (%i)
            Documentation=file:/usr/share/doc/heptatrader/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md
            Requires=hepta-tool-session-supervisor@%i.socket
            After=hepta-tool-session-supervisor@%i.socket
            [Service]
            Type=oneshot
            User=root
            Group=root
            ExecStart=/usr/libexec/hepta-shadow-watch-custodian --domain-config /etc/heptatrader/trust-domains/%i.json reconcile
            TimeoutStartSec=30s
            StateDirectory=hepta-shadow-watch-custodian
            StateDirectoryMode=0700
            UMask=0077
            NoNewPrivileges=yes
            PrivateTmp=yes
            PrivateDevices=yes
            PrivateNetwork=yes
            ProtectSystem=strict
            ProtectHome=yes
            ProtectKernelTunables=yes
            ProtectKernelModules=yes
            ProtectKernelLogs=yes
            ProtectControlGroups=yes
            ProtectClock=yes
            ProtectHostname=yes
            RestrictSUIDSGID=yes
            RestrictRealtime=yes
            LockPersonality=yes
            MemoryDenyWriteExecute=yes
            CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER
            AmbientCapabilities=
            RestrictAddressFamilies=AF_UNIX
            SystemCallArchitectures=native
            SystemCallFilter=@system-service
            SystemCallErrorNumber=EPERM
            ReadWritePaths=/var/lib/hepta-shadow-watch-custodian
            ReadWritePaths=-/run/hepta-agent-%i/sessions
            ReadWritePaths=-/run/hepta-shadow-watch-export-%i
            ReadWritePaths=-/var/lib/hepta-shadow-watch-%i/private
        """),
    "usr/lib/systemd/system/"
    "hepta-shadow-watch-custodian-reconcile@.timer":
        _contract_lines("""
            [Unit]
            Description=HeptaTrader WATCH custodian crash/reboot reconcile timer (%i)
            [Timer]
            OnBootSec=15s
            OnUnitActiveSec=15s
            AccuracySec=1s
            RandomizedDelaySec=0
            Persistent=true
            Unit=hepta-shadow-watch-custodian-reconcile@%i.service
            [Install]
            WantedBy=timers.target
        """),
    "usr/lib/systemd/system/hepta-shadow-watch-collector@.timer":
        _contract_lines("""
            [Unit]
            Description=HeptaTrader bounded SHADOW WATCH collection timer (%i)
            [Timer]
            OnBootSec=2min
            OnUnitActiveSec=15min
            AccuracySec=1s
            RandomizedDelaySec=0
            Persistent=false
            Unit=hepta-shadow-watch-collector@%i.service
        """),
    "usr/lib/systemd/system/hepta-local-paper-fail-close@.service":
        _contract_lines("""
            [Unit]
            Description=HeptaTrader static local PAPER fail-close custodian (%i)
            Documentation=file:/usr/share/doc/heptatrader/AUTONOMOUS-PAPER-CAMPAIGN.md
            Conflicts=hepta-local-paper-authority@%i.service hepta-execution-ib-paper@%i.service hepta-execution-ib-paper@%i.socket hepta-execution-events-ib-paper@%i.socket hepta-ib-paper-domain-preflight@%i.service
            After=hepta-local-paper-authority@%i.service hepta-execution-ib-paper@%i.service hepta-ib-paper-domain-preflight@%i.service
            [Service]
            Type=oneshot
            User=root
            Group=root
            LoadCredential=hepta-local-paper-control.py:/usr/libexec/hepta-local-paper-control
            ExecStart=/usr/bin/python3.12 -I -S ${CREDENTIALS_DIRECTORY}/hepta-local-paper-control.py guardian-fail-close --domain %i
            UMask=0077
            TimeoutStartSec=5min
            TimeoutStopSec=10s
            KillMode=control-group
            NoNewPrivileges=yes
            PrivateNetwork=yes
            PrivateTmp=yes
            PrivateDevices=yes
            ProtectSystem=strict
            ProtectHome=yes
            ProtectKernelTunables=yes
            ProtectKernelModules=yes
            ProtectKernelLogs=yes
            ProtectControlGroups=yes
            ProtectClock=yes
            ProtectHostname=yes
            RestrictSUIDSGID=yes
            RestrictRealtime=yes
            RestrictNamespaces=yes
            LockPersonality=yes
            MemoryDenyWriteExecute=yes
            RemoveIPC=yes
            CapabilityBoundingSet=
            AmbientCapabilities=
            RestrictAddressFamilies=AF_UNIX
            IPAddressDeny=any
            SystemCallArchitectures=native
            SystemCallFilter=@system-service
            SystemCallErrorNumber=EPERM
            ReadOnlyPaths=/usr/share/heptatrader
            ReadWritePaths=/etc/heptatrader
            BindReadOnlyPaths=-/etc/heptatrader/credentials
            BindReadOnlyPaths=-/etc/heptatrader/paper-campaigns
            BindReadOnlyPaths=-/etc/heptatrader/p1-safety-soak
            BindReadOnlyPaths=-/etc/heptatrader/trust-domains
            BindReadOnlyPaths=-/etc/heptatrader/hepta-agent-trust-domain-policy-v1.json
            BindReadOnlyPaths=-/etc/heptatrader/hepta-agent-trust-domain.json
            BindReadOnlyPaths=-/etc/heptatrader/hepta-broker-network-policy-v1.json
            BindReadOnlyPaths=-/etc/heptatrader/hepta-ib-paper-domain-authorizations-v1.json
            BindReadOnlyPaths=-/etc/heptatrader/hepta-service-identities-v1.json
            BindReadOnlyPaths=-/etc/heptatrader/local-ai-paper-agent.env
            BindReadOnlyPaths=-/etc/heptatrader/local-ai-paper-deployment-v1.json
            BindReadOnlyPaths=-/etc/heptatrader/local-ai-paper-certified-install-closure-v1.json
            BindReadOnlyPaths=-/etc/heptatrader/hepta-tool-gateway.env
            BindReadOnlyPaths=-/etc/heptatrader/hepta-execution-simulator.env
            BindReadOnlyPaths=-/etc/heptatrader/hepta-execution-ib-paper.env
            BindReadOnlyPaths=-/etc/heptatrader/hepta-supervisor-lease.key
            BindReadOnlyPaths=-/etc/heptatrader/p1-paper-account-evidence-ed25519.pub
            BindReadOnlyPaths=-/etc/heptatrader/rootful-systemd-review-ed25519.pub
            BindReadOnlyPaths=-/etc/heptatrader/paper-account-authority.pub
            BindReadOnlyPaths=-/etc/heptatrader/release-causal-openssl.cnf
            BindReadOnlyPaths=-/etc/heptatrader/heptatrader-evidence-receipt-trust-v1.json
            ReadWritePaths=/var/lib/hepta-local-ai-paper-agent
            ReadWritePaths=/etc/systemd/system/hepta-broker-egress-policy.service.d
            ReadWritePaths=/run/hepta-local-paper-control
            ReadWritePaths=/run/hepta/ib-paper-host-authority
            StandardOutput=journal
            StandardError=journal
        """),
    "usr/lib/systemd/system/hepta-p1-paper-terminal-cutoff@.service":
        _contract_lines("""
            [Unit]
            Description=HeptaTrader root recovery-only PAPER transport cutoff (%i)
            Documentation=file:/usr/share/doc/heptatrader/RUNBOOK-STARTUP.md
            Conflicts=hepta-p1-paper-terminal-witness-verifier@%i.service
            After=hepta-broker-egress-policy.service hepta-tool-session-supervisor@%i.socket
            [Service]
            Type=oneshot
            User=root
            Group=root
            WorkingDirectory=/
            ExecStart=/usr/bin/python3.12 -I -S %d/hepta-p1-paper-terminal-witness-verifier --record-cutoff --request %d/hepta-p1-paper-terminal-cutoff-request
            LoadCredential=hepta-p1-paper-terminal-witness-verifier:/usr/libexec/hepta-p1-paper-terminal-witness-verifier
            LoadCredential=hepta-p1-paper-terminal-cutoff-request:/run/hepta/paper-terminal-witness/%i/cutoff-request.v1.json
            RuntimeDirectory=hepta/paper-terminal-witness/%i
            RuntimeDirectoryMode=0700
            RuntimeDirectoryPreserve=yes
            UMask=0077
            TimeoutStartSec=5min
            TimeoutStopSec=10s
            KillMode=control-group
            NoNewPrivileges=yes
            PrivateNetwork=yes
            PrivateTmp=yes
            PrivateDevices=yes
            ProtectSystem=strict
            ProtectHome=yes
            ProtectKernelTunables=yes
            ProtectKernelModules=yes
            ProtectKernelLogs=yes
            ProtectControlGroups=yes
            ProtectClock=yes
            ProtectHostname=yes
            RestrictSUIDSGID=yes
            RestrictRealtime=yes
            RestrictNamespaces=yes
            LockPersonality=yes
            MemoryDenyWriteExecute=yes
            RemoveIPC=yes
            CapabilityBoundingSet=
            AmbientCapabilities=
            RestrictAddressFamilies=AF_UNIX
            IPAddressDeny=any
            SystemCallArchitectures=native
            SystemCallFilter=@system-service
            SystemCallErrorNumber=EPERM
            ReadOnlyPaths=/etc /usr /var/lib /run /proc
            ReadWritePaths=/run/hepta/paper-terminal-witness/%i /run/hepta/ib-paper-host-authority
            StandardOutput=journal
            StandardError=journal
        """),
    "usr/lib/systemd/system/hepta-p1-paper-terminal-witness-verifier@.service":
        _contract_lines("""
            [Unit]
            Description=HeptaTrader root recovery-only PAPER terminal witness verifier (%i)
            Documentation=file:/usr/share/doc/heptatrader/RUNBOOK-STARTUP.md
            Conflicts=hepta-execution-ib-paper@%i.service hepta-execution-ib-paper@%i.socket hepta-execution-events-ib-paper@%i.socket hepta-ib-paper-domain-preflight@%i.service hepta-ib-paper-campaign-operator@%i.service hepta-ib-paper-campaign-operator@%i.socket hepta-p1-paper-canary-capture.service hepta-p1-paper-canary-executor.service hepta-p1-paper-canary-root-coordinator.service
            After=hepta-broker-egress-policy.service
            Requires=hepta-broker-egress-policy.service
            [Service]
            Type=oneshot
            User=root
            Group=root
            WorkingDirectory=/
            ExecStart=/usr/bin/python3.12 -I -S %d/hepta-p1-paper-terminal-witness-verifier --run --request %d/hepta-p1-paper-terminal-witness-request
            LoadCredential=hepta-p1-paper-terminal-witness-verifier:/usr/libexec/hepta-p1-paper-terminal-witness-verifier
            LoadCredential=hepta-p1-paper-terminal-witness-request:/run/hepta/paper-terminal-witness/%i/verifier-request.v1.json
            RuntimeDirectory=hepta/paper-terminal-witness/%i
            RuntimeDirectoryMode=0700
            RuntimeDirectoryPreserve=yes
            UMask=0077
            TimeoutStartSec=5min
            TimeoutStopSec=10s
            KillMode=control-group
            NoNewPrivileges=yes
            PrivateNetwork=yes
            PrivateTmp=yes
            PrivateDevices=yes
            ProtectSystem=strict
            ProtectHome=yes
            ProtectKernelTunables=yes
            ProtectKernelModules=yes
            ProtectKernelLogs=yes
            ProtectControlGroups=yes
            ProtectClock=yes
            ProtectHostname=yes
            RestrictSUIDSGID=yes
            RestrictRealtime=yes
            RestrictNamespaces=yes
            LockPersonality=yes
            MemoryDenyWriteExecute=yes
            RemoveIPC=yes
            CapabilityBoundingSet=
            AmbientCapabilities=
            RestrictAddressFamilies=AF_UNIX
            IPAddressDeny=any
            SystemCallArchitectures=native
            SystemCallFilter=@system-service
            SystemCallErrorNumber=EPERM
            ReadOnlyPaths=/etc /usr /var/lib /run /proc
            ReadWritePaths=/run/hepta/paper-terminal-witness/%i /run/hepta/ib-paper-host-authority
            StandardOutput=journal
            StandardError=journal
        """),
}

APPROVED_SYSTEMD_SEMANTICS.update({
    "usr/lib/systemd/system/hepta-p1-safety-soak-campaign@.service":
        _contract_lines("""
            [Unit]
            Description=HeptaTrader P1 safety-soak coordinator (%i)
            Documentation=man:systemd.service(5)
            After=local-fs.target dbus.service
            Wants=dbus.service
            Conflicts=hepta-execution-ib-paper.service hepta-execution-ib-paper.socket hepta-execution-events-ib-paper.socket
            Conflicts=hepta-execution-ib-paper@alpha.service hepta-execution-ib-paper@alpha.socket hepta-execution-events-ib-paper@alpha.socket
            Conflicts=hepta-ib-paper-domain-preflight@alpha.service hepta-ib-paper-campaign-operator@alpha.service hepta-ib-paper-campaign-operator@alpha.socket
            Conflicts=hepta-execution-ib-live.service hepta-execution-ib-live.socket hepta-execution-events-ib-live.socket
            Conflicts=hepta-execution-ib-live@alpha.service hepta-execution-ib-live@alpha.socket hepta-execution-events-ib-live@alpha.socket
            Conflicts=hepta-ib-live-domain-preflight@alpha.service hepta-ib-live-campaign-operator@alpha.service hepta-ib-live-campaign-operator@alpha.socket
            StartLimitIntervalSec=300
            StartLimitBurst=5
            [Service]
            Type=notify
            NotifyAccess=main
            User=root
            Group=root
            UMask=0077
            ExecStart=/usr/libexec/hepta-p1-safety-soak-campaign-coordinator --run --launch-contract /etc/heptatrader/p1-safety-soak/%i.json
            Restart=on-failure
            RestartSec=5s
            WatchdogSec=45s
            TimeoutStartSec=infinity
            TimeoutStopSec=6min
            KillMode=mixed
            StateDirectory=hepta/p1-safety-soak
            StateDirectoryMode=0700
            ReadWritePaths=/var/lib/hepta/p1-safety-soak
            NoNewPrivileges=yes
            PrivateTmp=yes
            PrivateDevices=yes
            ProtectSystem=strict
            ProtectHome=yes
            ProtectHostname=yes
            ProtectKernelTunables=yes
            ProtectKernelModules=yes
            ProtectKernelLogs=yes
            ProtectControlGroups=yes
            ProtectClock=yes
            RestrictNamespaces=yes
            RestrictSUIDSGID=yes
            RestrictRealtime=yes
            LockPersonality=yes
            MemoryDenyWriteExecute=yes
            KeyringMode=private
            RemoveIPC=yes
            CapabilityBoundingSet=
            AmbientCapabilities=
            RestrictAddressFamilies=AF_UNIX
            IPAddressDeny=any
            SystemCallArchitectures=native
            Environment=PATH=/usr/sbin:/usr/bin:/sbin:/bin
            Environment=LANG=C
            Environment=LC_ALL=C
        """),
    "usr/lib/systemd/system/hepta-p1-safety-soak-observer-worker@.service":
        _contract_lines("""
            [Unit]
            Description=HeptaTrader P1 recurring independent-observer worker (%i)
            After=local-fs.target
            PartOf=hepta-p1-safety-soak@%i.target
            [Service]
            Type=notify
            NotifyAccess=main
            User=root
            Group=root
            UMask=0077
            EnvironmentFile=/run/hepta-p1-safety-soak/%i-worker.env
            ExecStart=/usr/libexec/hepta-p1-safety-soak-observer-worker --run --runtime-manifest /var/lib/hepta/p1-safety-soak/%i/runtime-manifest.json --expected-runtime-manifest-file-sha256 ${HEPTA_P1_RUNTIME_FILE_SHA256}
            Restart=on-failure
            RestartSec=1s
            WatchdogSec=30s
            TimeoutStartSec=45s
            TimeoutStopSec=30s
            KillMode=mixed
            ReadWritePaths=/var/lib/hepta/p1-safety-soak/%i
            NoNewPrivileges=yes
            PrivateTmp=yes
            PrivateDevices=yes
            ProtectSystem=strict
            ProtectHome=yes
            ProtectHostname=yes
            ProtectKernelTunables=yes
            ProtectKernelModules=yes
            ProtectKernelLogs=yes
            ProtectControlGroups=yes
            ProtectClock=yes
            RestrictNamespaces=yes
            RestrictSUIDSGID=yes
            LockPersonality=yes
            MemoryDenyWriteExecute=yes
            KeyringMode=private
            RemoveIPC=yes
            CapabilityBoundingSet=CAP_DAC_READ_SEARCH CAP_SYS_PTRACE CAP_NET_ADMIN
            AmbientCapabilities=
            RestrictAddressFamilies=AF_UNIX AF_NETLINK
            IPAddressDeny=any
            SystemCallArchitectures=native
            Environment=PATH=/usr/sbin:/usr/bin:/sbin:/bin
            Environment=LANG=C
            Environment=LC_ALL=C
        """),
    "usr/lib/systemd/system/hepta-p1-safety-soak-recorder-worker@.service":
        _contract_lines("""
            [Unit]
            Description=HeptaTrader P1 restartable evidence-recorder worker (%i)
            After=local-fs.target
            PartOf=hepta-p1-safety-soak@%i.target
            [Service]
            Type=notify
            NotifyAccess=main
            User=root
            Group=root
            UMask=0077
            EnvironmentFile=/run/hepta-p1-safety-soak/%i-worker.env
            ExecStart=/usr/libexec/hepta-p1-safety-soak-recorder-worker --run --runtime-manifest /var/lib/hepta/p1-safety-soak/%i/runtime-manifest.json --expected-runtime-manifest-file-sha256 ${HEPTA_P1_RUNTIME_FILE_SHA256}
            Restart=on-failure
            RestartSec=1s
            WatchdogSec=30s
            TimeoutStartSec=45s
            TimeoutStopSec=30s
            KillMode=mixed
            ReadWritePaths=/var/lib/hepta/p1-safety-soak/%i
            NoNewPrivileges=yes
            PrivateTmp=yes
            PrivateDevices=yes
            ProtectSystem=strict
            ProtectHome=yes
            ProtectHostname=yes
            ProtectKernelTunables=yes
            ProtectKernelModules=yes
            ProtectKernelLogs=yes
            ProtectControlGroups=yes
            ProtectClock=yes
            RestrictNamespaces=yes
            RestrictSUIDSGID=yes
            LockPersonality=yes
            MemoryDenyWriteExecute=yes
            KeyringMode=private
            RemoveIPC=yes
            CapabilityBoundingSet=CAP_DAC_READ_SEARCH
            AmbientCapabilities=
            RestrictAddressFamilies=AF_UNIX
            IPAddressDeny=any
            SystemCallArchitectures=native
            Environment=PATH=/usr/sbin:/usr/bin:/sbin:/bin
            Environment=LANG=C
            Environment=LC_ALL=C
        """),
    "usr/lib/systemd/system/hepta-p1-safety-soak@.target":
        _contract_lines("""
            [Unit]
            Description=HeptaTrader P1 non-authorizing safety-soak lifecycle (%i)
            Requires=hepta-p1-safety-soak-campaign@%i.service
            After=hepta-p1-safety-soak-campaign@%i.service
            Conflicts=hepta-execution-ib-paper.service hepta-execution-ib-paper.socket hepta-execution-events-ib-paper.socket
            Conflicts=hepta-execution-ib-paper@alpha.service hepta-execution-ib-paper@alpha.socket hepta-execution-events-ib-paper@alpha.socket
            Conflicts=hepta-ib-paper-domain-preflight@alpha.service hepta-ib-paper-campaign-operator@alpha.service hepta-ib-paper-campaign-operator@alpha.socket
            Conflicts=hepta-execution-ib-live.service hepta-execution-ib-live.socket hepta-execution-events-ib-live.socket
            Conflicts=hepta-execution-ib-live@alpha.service hepta-execution-ib-live@alpha.socket hepta-execution-events-ib-live@alpha.socket
            Conflicts=hepta-ib-live-domain-preflight@alpha.service hepta-ib-live-campaign-operator@alpha.service hepta-ib-live-campaign-operator@alpha.socket
            StopWhenUnneeded=yes
        """),
})

APPROVED_SYSTEMD_SEMANTICS.update({
    "usr/lib/systemd/system/hepta-p1-paper-canary-capture.service":
        _contract_lines("""
            [Unit]
            Description=HeptaTrader fixed read-only external P1 PAPER canary capture
            ConditionPathExists=/run/hepta-p1-paper-canary/active-capture-request.v1.json
            [Service]
            Type=oneshot
            User=hepta-agent-alpha
            Group=hepta-agent-alpha
            UMask=0077
            LoadCredential=hepta-p1-paper-canary-launch-joiner.py:/usr/libexec/hepta-p1-paper-canary-launch-joiner
            LoadCredential=capture-request.v1.json:/run/hepta-p1-paper-canary/active-capture-request.v1.json
            LoadCredential=session.token:/run/hepta-p1-paper-canary/read-only-capture-session.token
            ExecStart=/usr/bin/python3.12 -I -S /run/credentials/hepta-p1-paper-canary-capture/hepta-p1-paper-canary-launch-joiner.py --peer-capture
            WorkingDirectory=/
            Environment=LC_ALL=C
            NoNewPrivileges=yes
            PrivateTmp=yes
            PrivateDevices=yes
            PrivateNetwork=yes
            ProtectSystem=strict
            ProtectHome=yes
            ProtectKernelTunables=yes
            ProtectKernelModules=yes
            ProtectKernelLogs=yes
            ProtectControlGroups=yes
            RestrictAddressFamilies=AF_UNIX
            RestrictNamespaces=yes
            LockPersonality=yes
            MemoryDenyWriteExecute=yes
            CapabilityBoundingSet=
            AmbientCapabilities=
            ReadWritePaths=/var/lib/hepta/p1-paper-canary
            ReadOnlyPaths=/var/lib/hepta/p1-paper-canary-control /var/lib/hepta/p1-admission /etc/heptatrader /usr/libexec /usr/bin /run/hepta-agent-alpha
            TimeoutStartSec=60
            TimeoutStopSec=10
            KillMode=mixed
            RemainAfterExit=no
            Restart=no
            [Install]
            WantedBy=multi-user.target
        """),
    "usr/lib/systemd/system/hepta-p1-paper-canary-executor.service":
        _contract_lines("""
            [Unit]
            Description=HeptaTrader fixed external P1 PAPER canary executor
            Requires=hepta-p1-paper-canary-finalizer.socket
            After=hepta-p1-paper-canary-finalizer.socket
            ConditionPathExists=/run/hepta-p1-paper-canary/active-execution-handoff.v1.json
            [Service]
            Type=exec
            User=hepta-agent-alpha
            Group=hepta-agent-alpha
            UMask=0077
            LoadCredential=hepta-p1-paper-canary-executor.py:/usr/libexec/hepta-p1-paper-canary-executor
            LoadCredential=execution-handoff.v1.json:/run/hepta-p1-paper-canary/active-execution-handoff.v1.json
            ExecStart=/usr/bin/python3.12 -I -S /run/credentials/hepta-p1-paper-canary-executor/hepta-p1-paper-canary-executor.py
            WorkingDirectory=/
            Environment=LC_ALL=C
            NoNewPrivileges=yes
            PrivateTmp=yes
            PrivateDevices=yes
            PrivateNetwork=yes
            ProtectSystem=strict
            ProtectHome=yes
            ProtectKernelTunables=yes
            ProtectKernelModules=yes
            ProtectKernelLogs=yes
            ProtectControlGroups=yes
            RestrictAddressFamilies=AF_UNIX
            RestrictNamespaces=yes
            LockPersonality=yes
            MemoryDenyWriteExecute=yes
            CapabilityBoundingSet=
            AmbientCapabilities=
            ReadWritePaths=/var/lib/hepta/p1-paper-canary
            ReadOnlyPaths=/var/lib/hepta/p1-paper-canary-control /etc/heptatrader /usr/libexec /usr/bin /run/hepta-agent-alpha /run/hepta-execution-alpha
            RuntimeMaxSec=10min
            TimeoutStopSec=15
            KillMode=mixed
            Restart=no
            [Install]
            WantedBy=multi-user.target
        """),
    "usr/lib/systemd/system/"
    "hepta-p1-paper-canary-root-coordinator.service":
        _contract_lines("""
            [Unit]
            Description=HeptaTrader fixed external P1 PAPER canary root coordinator
            Documentation=file:/usr/share/doc/heptatrader/AUTONOMOUS-PAPER-CAMPAIGN.md
            ConditionPathExists=/run/hepta-p1-paper-canary/active-coordinator-request.v1.json
            [Service]
            Type=exec
            User=root
            Group=root
            SupplementaryGroups=
            UMask=0077
            LoadCredential=hepta-p1-paper-canary-root-coordinator.py:/usr/libexec/hepta-p1-paper-canary-root-coordinator
            LoadCredential=hepta-p1-paper-canary-launch-joiner.py:/usr/libexec/hepta-p1-paper-canary-launch-joiner
            LoadCredential=hepta-p1-paper-canary-owner-provisioner.py:/usr/libexec/hepta-p1-paper-canary-owner-provisioner
            LoadCredential=hepta-p1-paper-canary-executor.py:/usr/libexec/hepta-p1-paper-canary-executor
            LoadCredential=hepta-p1-paper-canary-crash-emergency-closer.py:/usr/libexec/hepta-p1-paper-canary-crash-emergency-closer
            LoadCredential=hepta-p1-paper-canary-terminal-prover.py:/usr/libexec/hepta-p1-paper-canary-terminal-prover
            LoadCredential=hepta-local-paper-control.py:/usr/libexec/hepta-local-paper-control
            LoadCredential=active-coordinator-request.v1.json:/run/hepta-p1-paper-canary/active-coordinator-request.v1.json
            ExecStart=/usr/bin/python3.12 -I -S /run/credentials/hepta-p1-paper-canary-root-coordinator/hepta-p1-paper-canary-root-coordinator.py --service-run
            ExecStopPost=/usr/bin/python3.12 -I -S /run/credentials/hepta-p1-paper-canary-root-coordinator/hepta-p1-paper-canary-crash-emergency-closer.py --exec-stop-post
            WorkingDirectory=/
            Environment=LANG=C
            Environment=LC_ALL=C
            Environment=PATH=/usr/bin:/usr/sbin
            RuntimeMaxSec=15min
            TimeoutStartSec=15min
            TimeoutStopSec=2min
            KillMode=mixed
            Restart=no
            NoNewPrivileges=yes
            PrivateTmp=yes
            PrivateDevices=yes
            PrivateNetwork=yes
            ProtectSystem=strict
            ProtectHome=yes
            ProtectKernelTunables=yes
            ProtectKernelModules=yes
            ProtectKernelLogs=yes
            ProtectControlGroups=yes
            ProtectClock=yes
            ProtectHostname=yes
            RestrictSUIDSGID=yes
            RestrictRealtime=yes
            RestrictNamespaces=yes
            LockPersonality=yes
            MemoryDenyWriteExecute=yes
            RestrictAddressFamilies=AF_UNIX
            SystemCallArchitectures=native
            SystemCallFilter=@system-service
            SystemCallErrorNumber=EPERM
            ReadWritePaths=/var/lib/hepta-local-ai-paper-agent
            ReadWritePaths=/var/lib/hepta/p1-paper-canary-control
            ReadWritePaths=/var/lib/hepta/p1-paper-canary
            ReadWritePaths=/etc/heptatrader
            ReadWritePaths=/etc/systemd/system/hepta-broker-egress-policy.service.d
            ReadWritePaths=/run/hepta
            ReadWritePaths=/run/hepta-agent-alpha/sessions
            ReadWritePaths=/run/hepta-local-paper-control
            ReadOnlyPaths=/usr/libexec /usr/bin /usr/sbin /usr/share/heptatrader
            [Install]
            WantedBy=multi-user.target
        """),
})


APPROVED_SYSTEMD_DROPIN_SEMANTICS = {
    "usr/lib/systemd/system/hepta-tool-gateway.service.d/"
    "10-hepta-broker-egress-policy.conf": _contract_lines("""
        [Unit]
        BindsTo=hepta-broker-egress-policy.service
        After=hepta-broker-egress-policy.service
    """),
    "usr/lib/systemd/system/hepta-tool-gateway@.service.d/"
    "10-hepta-broker-egress-policy.conf": _contract_lines("""
        [Unit]
        BindsTo=hepta-broker-egress-policy.service
        After=hepta-broker-egress-policy.service
    """),
}
COMPONENTS = [
    "hepta-agent-os-runtime",
    "hepta-execution-runtime",
]
BOUNDARY = {
    "components": COMPONENTS,
    "build_type": "Release",
    "ibapi_enabled": False,
    "legacy_0dte_bridge_enabled": False,
    "legacy_monolith_enabled": False,
    "legacy_simulator_enabled": False,
    "passive_provisioning": True,
    "paper_authorized": False,
    "live_authorized": False,
    "sdk_included": False,
    "vendor_payload_included": False,
    "prebuilt_payload_included": False,
    "host_state_paths_included": False,
}


class RuntimePackageError(ValueError):
    """A runtime package did not satisfy the closed release contract."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, separators=(",", ":"),
        sort_keys=True).encode("ascii")


def _reject_duplicate_keys(
        pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimePackageError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> Any:
    raise RuntimePackageError(f"non-finite JSON number: {value}")


def strict_json(data: bytes, label: str) -> Any:
    try:
        text = data.decode("utf-8", errors="strict")
        return json.loads(
            text, object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimePackageError(f"{label} is not strict UTF-8 JSON") from error


def _identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_nlink, metadata.st_uid, metadata.st_gid,
        metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def stable_private_bytes(
        path: pathlib.Path, label: str, maximum: int) -> bytes:
    """Read one private single-link input without following its final path."""
    path = pathlib.Path(os.path.abspath(path))
    try:
        before = path.lstat()
        if (not stat.S_ISREG(before.st_mode) or
                stat.S_ISLNK(before.st_mode) or before.st_nlink != 1 or
                stat.S_IMODE(before.st_mode) != 0o600 or
                before.st_size < 0 or before.st_size > maximum):
            raise RuntimePackageError(
                f"{label} must be a regular single-link 0600 file")
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
            getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise RuntimePackageError(f"{label} is unavailable or unsafe") from error
    try:
        opened = os.fstat(descriptor)
        if _identity(before) != _identity(opened):
            raise RuntimePackageError(f"{label} changed before open")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise RuntimePackageError(f"{label} was truncated")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise RuntimePackageError(f"{label} grew while reading")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        final = path.lstat()
    except OSError as error:
        raise RuntimePackageError(f"{label} disappeared while reading") from error
    if _identity(opened) != _identity(after) or _identity(after) != _identity(final):
        raise RuntimePackageError(f"{label} changed while reading")
    return b"".join(chunks)


def canonical_relative(value: Any, label: str) -> str:
    if (not isinstance(value, str) or not value or "\0" in value or
            "\\" in value):
        raise RuntimePackageError(f"unsafe {label}")
    path = pathlib.PurePosixPath(value)
    if (path.is_absolute() or path.as_posix() != value or
            any(part in {"", ".", ".."} for part in path.parts)):
        raise RuntimePackageError(f"unsafe {label}")
    return value


def _slice(data: bytes, offset: int, size: int, label: str) -> bytes:
    if (offset < 0 or size < 0 or offset > len(data) or
            size > len(data) - offset):
        raise RuntimePackageError(f"ELF {label} is out of bounds")
    return data[offset:offset + size]


def _cstring(data: bytes, offset: int, label: str) -> str:
    if offset < 0 or offset >= len(data):
        raise RuntimePackageError(f"ELF {label} offset is out of bounds")
    end = data.find(b"\0", offset)
    if end < 0:
        raise RuntimePackageError(f"ELF {label} is unterminated")
    try:
        value = data[offset:end].decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise RuntimePackageError(f"ELF {label} is not UTF-8") from error
    if not value:
        raise RuntimePackageError(f"ELF {label} is empty")
    return value


def inspect_elf(data: bytes) -> dict[str, Any]:
    """Return portable dynamic ELF identity while enforcing runtime safety."""
    if len(data) < 64 or data[:4] != b"\x7fELF":
        raise RuntimePackageError("runtime executable is not ELF")
    elf_class_raw = data[4]
    endian_raw = data[5]
    if elf_class_raw not in {1, 2} or endian_raw not in {1, 2}:
        raise RuntimePackageError("unsupported ELF class or endianness")
    elf_class = "ELF32" if elf_class_raw == 1 else "ELF64"
    endian = "little" if endian_raw == 1 else "big"
    prefix = "<" if endian_raw == 1 else ">"
    header_format = (
        prefix + "HHIIIIIHHHHHH" if elf_class_raw == 1
        else prefix + "HHIQQQIHHHHHH")
    header_size = struct.calcsize(header_format)
    header = struct.unpack(
        header_format, _slice(data, 16, header_size, "header"))
    elf_type, machine_number = header[0], header[1]
    program_offset = header[4]
    elf_header_size = header[7]
    program_entry_size = header[8]
    program_count = header[9]
    if (elf_type not in {2, 3} or elf_header_size < 16 + header_size or
            program_count <= 0 or program_count > 4096):
        raise RuntimePackageError("ELF header contract is invalid")
    program_format = (
        prefix + "IIIIIIII" if elf_class_raw == 1
        else prefix + "IIQQQQQQ")
    expected_program_size = struct.calcsize(program_format)
    if program_entry_size < expected_program_size:
        raise RuntimePackageError("ELF program header size is invalid")
    machine_names = {
        3: "i386",
        40: "arm",
        62: "x86_64",
        183: "aarch64",
        243: "riscv",
    }
    machine = machine_names.get(machine_number)
    if machine is None:
        raise RuntimePackageError(
            f"unsupported ELF machine number {machine_number}")

    programs: list[dict[str, int]] = []
    for index in range(program_count):
        raw = _slice(
            data, program_offset + index * program_entry_size,
            expected_program_size, "program header")
        values = struct.unpack(program_format, raw)
        if elf_class_raw == 1:
            kind, offset, virtual, _, file_size, memory_size, flags, align = values
        else:
            kind, flags, offset, virtual, _, file_size, memory_size, align = values
        _slice(data, offset, file_size, "program segment")
        programs.append({
            "kind": kind,
            "offset": offset,
            "virtual": virtual,
            "file_size": file_size,
            "memory_size": memory_size,
            "flags": flags,
            "align": align,
        })

    interpreter_segments = [item for item in programs if item["kind"] == 3]
    if len(interpreter_segments) != 1:
        raise RuntimePackageError(
            "runtime ELF must contain exactly one interpreter")
    interpreter_data = _slice(
        data, interpreter_segments[0]["offset"],
        interpreter_segments[0]["file_size"], "interpreter")
    if not interpreter_data.endswith(b"\0") or b"\0" in interpreter_data[:-1]:
        raise RuntimePackageError("ELF interpreter is not canonical")
    try:
        interpreter = interpreter_data[:-1].decode("ascii", errors="strict")
    except UnicodeDecodeError as error:
        raise RuntimePackageError("ELF interpreter is not ASCII") from error
    if (not interpreter.startswith(("/lib", "/usr/lib")) or
            pathlib.PurePosixPath(interpreter).as_posix() != interpreter or
            any(part in {"", ".", ".."} for part in
                pathlib.PurePosixPath(interpreter).parts[1:])):
        raise RuntimePackageError("ELF interpreter path is unsafe")

    dynamic_segments = [item for item in programs if item["kind"] == 2]
    if len(dynamic_segments) != 1:
        raise RuntimePackageError(
            "runtime ELF must contain exactly one dynamic segment")
    dynamic = dynamic_segments[0]
    dynamic_format = prefix + ("iI" if elf_class_raw == 1 else "qQ")
    dynamic_size = struct.calcsize(dynamic_format)
    if dynamic["file_size"] % dynamic_size:
        raise RuntimePackageError("ELF dynamic segment is misaligned")
    needed_offsets: list[int] = []
    string_table_virtual: int | None = None
    terminated = False
    for offset in range(
            dynamic["offset"],
            dynamic["offset"] + dynamic["file_size"],
            dynamic_size):
        tag, value = struct.unpack(
            dynamic_format, _slice(data, offset, dynamic_size, "dynamic entry"))
        if tag == 0:
            terminated = True
            break
        if tag == 1:
            needed_offsets.append(value)
        elif tag == 5:
            string_table_virtual = value
        elif tag in {15, 29}:
            raise RuntimePackageError("runtime ELF contains RPATH or RUNPATH")
    if not terminated or string_table_virtual is None or not needed_offsets:
        raise RuntimePackageError("ELF dynamic dependency closure is incomplete")

    string_table_offset: int | None = None
    for program in programs:
        if (program["kind"] == 1 and
                program["virtual"] <= string_table_virtual <
                program["virtual"] + program["file_size"]):
            string_table_offset = (
                program["offset"] +
                string_table_virtual - program["virtual"])
            break
    if string_table_offset is None:
        raise RuntimePackageError("ELF dynamic string table is unmapped")
    needed: list[str] = []
    for offset in needed_offsets:
        name = _cstring(data, string_table_offset + offset, "DT_NEEDED")
        if ("/" in name or not re.fullmatch(
                r"[A-Za-z0-9_+.-]{1,255}", name)):
            raise RuntimePackageError("ELF DT_NEEDED entry is unsafe")
        needed.append(name)
    if len(set(needed)) != len(needed):
        raise RuntimePackageError("ELF contains duplicate DT_NEEDED entries")

    build_ids: list[str] = []
    for program in programs:
        if program["kind"] != 4:
            continue
        note = _slice(
            data, program["offset"], program["file_size"], "note segment")
        cursor = 0
        while cursor < len(note):
            if len(note) - cursor < 12:
                if any(note[cursor:]):
                    raise RuntimePackageError("ELF note tail is malformed")
                break
            name_size, description_size, note_type = struct.unpack(
                prefix + "III", note[cursor:cursor + 12])
            cursor += 12
            name = _slice(note, cursor, name_size, "note name")
            cursor += (name_size + 3) & ~3
            description = _slice(
                note, cursor, description_size, "note description")
            cursor += (description_size + 3) & ~3
            if name.rstrip(b"\0") == b"GNU" and note_type == 3:
                if not description:
                    raise RuntimePackageError("ELF GNU build-id is empty")
                build_ids.append(description.hex())
    if len(set(build_ids)) != 1:
        raise RuntimePackageError(
            "runtime ELF must contain one unambiguous GNU build-id")

    return {
        "kind": "elf",
        "class": elf_class,
        "endian": endian,
        "machine": machine,
        "interpreter": interpreter,
        "needed": sorted(needed),
        "build_id": build_ids[0],
    }


def payload_record(path: str, data: bytes) -> dict[str, Any]:
    if path in ELF_FILES:
        return inspect_elf(data)
    if path in PYTHON_MODULE_FILES:
        if data.startswith(b"#!"):
            raise RuntimePackageError(
                f"Python library must remain non-executable source: {path}")
        try:
            compile(data, path, "exec")
        except (SyntaxError, UnicodeDecodeError) as error:
            raise RuntimePackageError(
                f"Python library is not valid source: {path}") from error
        return {"kind": "python-module"}
    if path in PYTHON_FILES:
        first_line = data.split(b"\n", 1)[0]
        try:
            shebang = first_line.decode("ascii", errors="strict")
        except UnicodeDecodeError as error:
            raise RuntimePackageError(
                f"Python shebang is not ASCII: {path}") from error
        if shebang not in PYTHON_SHEBANGS:
            raise RuntimePackageError(
                f"Python runtime has an unapproved shebang: {path}")
        return {"kind": "python", "shebang": shebang}
    if data.startswith(b"#!"):
        raise RuntimePackageError(f"unexpected script payload: {path}")
    return {"kind": "data"}


def file_record(path: str, mode: int, data: bytes) -> dict[str, Any]:
    return {
        "path": path,
        "mode": f"{mode:04o}",
        "size": len(data),
        "sha256": "sha256:" + sha256(data),
        "payload": payload_record(path, data),
    }


def _parse_systemd_unit(
        path: str, data: bytes) -> tuple[
            tuple[str, ...], dict[str, dict[str, list[str]]]]:
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise RuntimePackageError(
            f"runtime systemd unit is not UTF-8: {path}") from error
    if "\0" in text or "\r" in text:
        raise RuntimePackageError(
            f"runtime systemd unit has unsafe text encoding: {path}")
    lines: list[str] = []
    sections: dict[str, dict[str, list[str]]] = {}
    section: str | None = None
    for line_number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            raise RuntimePackageError(
                f"runtime systemd unit uses unsupported continuation: "
                f"{path}:{line_number}")
        lines.append(line)
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1]
            if (re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", name) is None or
                    name in sections):
                raise RuntimePackageError(
                    f"runtime systemd unit has invalid section: "
                    f"{path}:{line_number}")
            sections[name] = {}
            section = name
            continue
        if section is None or "=" not in line:
            raise RuntimePackageError(
                f"runtime systemd unit has invalid directive: "
                f"{path}:{line_number}")
        key, value = line.split("=", 1)
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", key) is None:
            raise RuntimePackageError(
                f"runtime systemd unit has invalid directive key: "
                f"{path}:{line_number}")
        sections[section].setdefault(key, []).append(value)
    if not lines:
        raise RuntimePackageError(f"runtime systemd unit is empty: {path}")
    return tuple(lines), sections


def validate_systemd_semantics(payloads: dict[str, bytes]) -> None:
    """Validate the exact passive unit surface and executable references."""
    unit_paths = {
        path for path in payloads
        if path.endswith((".service", ".socket", ".timer", ".target"))
    }
    if unit_paths != set(APPROVED_SYSTEMD_SEMANTICS):
        raise RuntimePackageError(
            "runtime systemd unit closure does not match the approved surface")
    parsed: dict[str, dict[str, dict[str, list[str]]]] = {}
    for path in sorted(unit_paths):
        lines, sections = _parse_systemd_unit(path, payloads[path])
        if lines != APPROVED_SYSTEMD_SEMANTICS[path]:
            raise RuntimePackageError(
                f"runtime systemd semantics drift: {path}")
        parsed[path] = sections
    dropin_paths = {
        path for path in payloads
        if (path.startswith("usr/lib/systemd/system/") and
            path.endswith(".conf"))
    }
    if dropin_paths != set(APPROVED_SYSTEMD_DROPIN_SEMANTICS):
        raise RuntimePackageError(
            "runtime systemd drop-in closure does not match "
            "the approved surface")
    for path in sorted(dropin_paths):
        lines, _sections = _parse_systemd_unit(path, payloads[path])
        if lines != APPROVED_SYSTEMD_DROPIN_SEMANTICS[path]:
            raise RuntimePackageError(
                f"runtime systemd drop-in semantics drift: {path}")
    for service, (command, executable) in SYSTEMD_EXECSTART_CLOSURE.items():
        values = parsed[service].get("Service", {}).get("ExecStart", [])
        if values != [command]:
            raise RuntimePackageError(
                f"runtime systemd ExecStart closure drift: {service}")
        credentials = SYSTEMD_CREDENTIAL_SOURCE_CLOSURE.get(service)
        if credentials is not None:
            runtime_credentials = (
                SYSTEMD_RUNTIME_CREDENTIAL_SOURCE_CLOSURE.get(service, ()))
            all_credentials = credentials + runtime_credentials
            load_values = parsed[service].get(
                "Service", {}).get("LoadCredential", [])
            unit_name = pathlib.PurePosixPath(service).name.removesuffix(
                ".service")
            first_credential = credentials[0][0]
            credential_exec_paths = (
                f"${{CREDENTIALS_DIRECTORY}}/{first_credential}",
                f"%d/{first_credential}",
                f"/run/credentials/{unit_name}/{first_credential}",
            )
            if (
                    executable != "/usr/bin/python3.12" or
                    load_values != [
                        f"{name}:{source}" for name, source in all_credentials
                    ] or
                    not any(path in command for path in credential_exec_paths)
            ):
                raise RuntimePackageError(
                    "runtime systemd credential source is not bound: "
                    f"{service}")
            for _name, source in credentials:
                relative = source.removeprefix("/")
                if (not source.startswith("/") or relative == source or
                        PRODUCT_FILES.get(relative) != 0o755 or
                        relative not in payloads):
                    raise RuntimePackageError(
                        "runtime systemd credential source is not packaged: "
                        f"{service}")
            if (
                    len({name for name, _source in all_credentials}) !=
                    len(all_credentials) or
                    any(
                        not source.startswith("/run/") or
                        source.removeprefix("/") in PRODUCT_FILES
                        for _name, source in runtime_credentials
                    )
            ):
                raise RuntimePackageError(
                    "runtime systemd ephemeral credential closure drift: "
                    f"{service}")
            continue
        relative = executable.removeprefix("/")
        if (not executable.startswith("/") or relative == executable or
                PRODUCT_FILES.get(relative) != 0o755 or
                relative not in payloads):
            raise RuntimePackageError(
                f"runtime systemd ExecStart target is not packaged: {service}")


def validate_default_deny_identity_source(payloads: dict[str, bytes]) -> None:
    """Bind the packaged projection source to the reviewed deny-all default."""
    data = payloads.get(PAPER_IDENTITY_SOURCE_PATH)
    if (
        data != PAPER_IDENTITY_SOURCE_BYTES
        or len(data) != 257
        or "sha256:" + sha256(data) != PAPER_IDENTITY_SOURCE_SHA256
    ):
        raise RuntimePackageError(
            "runtime PAPER identity source is not the exact deny-all default")
    document = strict_json(data, "runtime PAPER identity source")
    if document != {
        "identities": [],
        "live_authorized": False,
        "paper_authorized": False,
        "schema": "hepta.agent-trust-domain-paper-identities.v1",
        "source_policy_sha256": (
            "sha256:08d430d53e4813cd0a43a23beeb92344af2130dca425814cbf7285059d90f90c"
        ),
        "version": 1,
    }:
        raise RuntimePackageError(
            "runtime PAPER identity source grants authority or drifts schema")


def _digest_field(value: Any, label: str) -> str:
    if (not isinstance(value, str) or not value.startswith("sha256:") or
            HEX64.fullmatch(value[7:]) is None):
        raise RuntimePackageError(f"{label} is not a SHA-256 identity")
    return value


def validate_manifest(manifest: Any) -> dict[str, Any]:
    fields = {
        "schema", "package_class", "release_version", "root",
        "source_ref", "vendor_ref", "target", "boundary",
        "file_count", "files_sha256", "files",
    }
    if not isinstance(manifest, dict) or set(manifest) != fields:
        raise RuntimePackageError("runtime manifest fields do not match schema")
    if (manifest["schema"] != SCHEMA or
            manifest["package_class"] != PACKAGE_CLASS):
        raise RuntimePackageError("unsupported runtime package schema")
    release = manifest["release_version"]
    if not isinstance(release, str) or VERSION.fullmatch(release) is None:
        raise RuntimePackageError("runtime release version is invalid")

    source = manifest["source_ref"]
    source_fields = {
        "schema", "bundle_sha256", "manifest_sha256", "files_sha256",
        "security_manifest_sha256", "git_head", "root",
    }
    if (not isinstance(source, dict) or set(source) != source_fields or
            source["schema"] != "hepta.clean-source-bundle.v2"):
        raise RuntimePackageError("runtime source_ref is invalid")
    for key in (
            "bundle_sha256", "manifest_sha256", "files_sha256",
            "security_manifest_sha256"):
        _digest_field(source[key], f"source_ref.{key}")
    if (not isinstance(source["git_head"], str) or
            HEX64.fullmatch(source["git_head"] + "0" * 24) is None):
        raise RuntimePackageError("source_ref.git_head is invalid")
    canonical_relative(source["root"], "source_ref.root")

    vendor = manifest["vendor_ref"]
    if (not isinstance(vendor, dict) or set(vendor) != {
            "schema", "descriptor_sha256", "release_version", "overlay_count",
            "required_overlay_ids"} or
            vendor["schema"] != "hepta.vendor-overlay-set.v1" or
            vendor["release_version"] != release or
            vendor["overlay_count"] != 3 or
            vendor["required_overlay_ids"] != []):
        raise RuntimePackageError("runtime vendor_ref is invalid")
    _digest_field(vendor["descriptor_sha256"], "vendor_ref.descriptor_sha256")

    target = manifest["target"]
    if (not isinstance(target, dict) or set(target) != {
            "os", "elf_class", "endian", "machine"} or
            target["os"] != "linux" or
            target["elf_class"] not in {"ELF32", "ELF64"} or
            target["endian"] not in {"little", "big"} or
            target["machine"] not in {
                "i386", "arm", "x86_64", "aarch64", "riscv"}):
        raise RuntimePackageError("runtime target is invalid")
    expected_root = (
        f"heptatrader-runtime-{release}-linux-{target['machine']}")
    if manifest["root"] != expected_root:
        raise RuntimePackageError("runtime canonical root is invalid")
    canonical_relative(manifest["root"], "runtime root")
    if manifest["boundary"] != BOUNDARY:
        raise RuntimePackageError("runtime passive boundary is invalid")

    records = manifest["files"]
    if (not isinstance(records, list) or
            manifest["file_count"] != PRODUCT_FILE_COUNT or
            len(records) != PRODUCT_FILE_COUNT):
        raise RuntimePackageError(
            f"runtime package must contain exactly {PRODUCT_FILE_COUNT} files")
    seen: set[str] = set()
    ordered_paths: list[str] = []
    target_identity = (
        target["elf_class"], target["endian"], target["machine"])
    for record in records:
        if not isinstance(record, dict) or set(record) != {
                "path", "mode", "size", "sha256", "payload"}:
            raise RuntimePackageError("runtime file record is invalid")
        path = canonical_relative(record["path"], "runtime file path")
        if path in seen or path not in PRODUCT_FILES:
            raise RuntimePackageError(
                f"duplicate or unapproved runtime file: {path}")
        seen.add(path)
        ordered_paths.append(path)
        if record["mode"] != f"{PRODUCT_FILES[path]:04o}":
            raise RuntimePackageError(f"runtime file mode drift: {path}")
        if (not isinstance(record["size"], int) or
                isinstance(record["size"], bool) or record["size"] < 0):
            raise RuntimePackageError(f"runtime file size is invalid: {path}")
        _digest_field(record["sha256"], f"runtime file {path}")
        if path == PAPER_IDENTITY_SOURCE_PATH and (
                record["size"] != len(PAPER_IDENTITY_SOURCE_BYTES) or
                record["sha256"] != PAPER_IDENTITY_SOURCE_SHA256):
            raise RuntimePackageError(
                "runtime PAPER identity source record drift")
        payload = record["payload"]
        if path in ELF_FILES:
            if (not isinstance(payload, dict) or set(payload) != {
                    "kind", "class", "endian", "machine", "interpreter",
                    "needed", "build_id"} or payload["kind"] != "elf" or
                    (payload["class"], payload["endian"], payload["machine"])
                    != target_identity or
                    not isinstance(payload["interpreter"], str) or
                    not isinstance(payload["needed"], list) or
                    not payload["needed"] or
                    payload["needed"] != sorted(set(payload["needed"])) or
                    not isinstance(payload["build_id"], str) or
                    not payload["build_id"] or
                    re.fullmatch(r"[0-9a-f]+", payload["build_id"]) is None):
                raise RuntimePackageError(
                    f"runtime ELF record is invalid: {path}")
        elif path in PYTHON_MODULE_FILES:
            if payload != {"kind": "python-module"}:
                raise RuntimePackageError(
                    f"runtime Python library record is invalid: {path}")
        elif path in PYTHON_FILES:
            if (not isinstance(payload, dict) or
                    set(payload) != {"kind", "shebang"} or
                    payload["kind"] != "python" or
                    payload["shebang"] not in PYTHON_SHEBANGS):
                raise RuntimePackageError(
                    f"runtime Python record is invalid: {path}")
        elif payload != {"kind": "data"}:
            raise RuntimePackageError(
                f"runtime data record is invalid: {path}")
    if seen != set(PRODUCT_FILES):
        raise RuntimePackageError("runtime product file closure is incomplete")
    if ordered_paths != sorted(ordered_paths):
        raise RuntimePackageError("runtime file records are not sorted")
    files_digest = "sha256:" + sha256(canonical_json(records))
    if manifest["files_sha256"] != files_digest:
        raise RuntimePackageError("runtime file record digest is invalid")
    return manifest


def verify_package(
        package_path: pathlib.Path,
        manifest_path: pathlib.Path) -> dict[str, Any]:
    manifest_bytes = stable_private_bytes(
        manifest_path, "external runtime manifest", MAX_MANIFEST_BYTES)
    package_bytes = stable_private_bytes(
        package_path, "runtime package", MAX_PACKAGE_BYTES)
    manifest = validate_manifest(strict_json(
        manifest_bytes, "external runtime manifest"))
    if manifest_bytes != canonical_json(manifest) + b"\n":
        raise RuntimePackageError(
            "external runtime manifest is not canonical JSON")
    root = manifest["root"]
    prefix = root + "/"
    internal_path = prefix + INTERNAL_MANIFEST
    records = {item["path"]: item for item in manifest["files"]}
    seen: set[str] = set()
    payloads: dict[str, bytes] = {}
    internal_count = 0
    elf_target: tuple[str, str, str] | None = None
    try:
        archive = tarfile.open(fileobj=io.BytesIO(package_bytes), mode="r:")
    except tarfile.TarError as error:
        raise RuntimePackageError("runtime package is not a plain tar") from error
    with archive:
        for member in archive.getmembers():
            canonical_relative(member.name, "tar member path")
            if (not member.isfile() or member.type not in {
                    tarfile.REGTYPE, tarfile.AREGTYPE} or member.linkname or
                    member.pax_headers or member.uid != 0 or member.gid != 0 or
                    member.uname != "root" or member.gname != "root" or
                    member.mtime != 0 or member.devmajor != 0 or
                    member.devminor != 0):
                raise RuntimePackageError(
                    f"unsafe runtime tar metadata: {member.name}")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise RuntimePackageError(
                    f"unreadable runtime tar member: {member.name}")
            data = extracted.read()
            if member.size != len(data):
                raise RuntimePackageError(
                    f"runtime tar member size drift: {member.name}")
            if member.name == internal_path:
                internal_count += 1
                if member.mode != 0o644 or data != manifest_bytes:
                    raise RuntimePackageError(
                        "internal and external runtime manifests differ")
                continue
            if not member.name.startswith(prefix):
                raise RuntimePackageError(
                    f"runtime tar member escapes canonical root: {member.name}")
            relative = member.name[len(prefix):]
            record = records.get(relative)
            if record is None or relative in seen:
                raise RuntimePackageError(
                    f"unregistered or duplicate runtime member: {relative}")
            if (member.mode != int(record["mode"], 8) or
                    member.size != record["size"] or
                    "sha256:" + sha256(data) != record["sha256"]):
                raise RuntimePackageError(
                    f"runtime payload or metadata drift: {relative}")
            observed_payload = payload_record(relative, data)
            if observed_payload != record["payload"]:
                raise RuntimePackageError(
                    f"runtime portable payload identity drift: {relative}")
            if relative in ELF_FILES:
                identity = (
                    observed_payload["class"],
                    observed_payload["endian"],
                    observed_payload["machine"])
                if elf_target is None:
                    elf_target = identity
                elif elf_target != identity:
                    raise RuntimePackageError(
                        "runtime package mixes incompatible ELF targets")
            payloads[relative] = data
            seen.add(relative)
    if internal_count != 1:
        raise RuntimePackageError(
            "runtime package must contain one internal manifest")
    if seen != set(records):
        raise RuntimePackageError("runtime package file closure is incomplete")
    validate_systemd_semantics(payloads)
    validate_default_deny_identity_source(payloads)
    if elf_target != (
            manifest["target"]["elf_class"],
            manifest["target"]["endian"],
            manifest["target"]["machine"]):
        raise RuntimePackageError("runtime target does not match ELF payloads")
    return {
        "schema": SCHEMA,
        "release_version": manifest["release_version"],
        "root": root,
        "file_count": PRODUCT_FILE_COUNT,
        "package_sha256": "sha256:" + sha256(package_bytes),
        "manifest_sha256": "sha256:" + sha256(manifest_bytes),
        "source_ref": manifest["source_ref"],
        "vendor_ref": manifest["vendor_ref"],
        "target": manifest["target"],
        "boundary": manifest["boundary"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True, type=pathlib.Path)
    parser.add_argument("--manifest", required=True, type=pathlib.Path)
    args = parser.parse_args()
    result = verify_package(args.package, args.manifest)
    print(
        "PASS: hepta.runtime-package.v1 "
        f"release={result['release_version']} files={result['file_count']} "
        f"package_sha256={result['package_sha256']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimePackageError as error:
        print(f"FAIL: {error}", file=os.sys.stderr)
        raise SystemExit(1)
