#!/usr/bin/env python3

from pathlib import Path
import hashlib
import json
import re
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))
sys.path.insert(1, str(REPOSITORY / "scripts"))

from hepta_ops import agent_os_source  # noqa: E402
import check_hepta_agent_os_product_boundary as product_boundary  # noqa: E402
import check_hepta_agent_os_units as agent_os_units  # noqa: E402


POLICY = REPOSITORY / "policies/heptatrader-agent-os-source-v2.json"
PAPER_IDENTITY_SOURCE = (
    REPOSITORY /
    "systemd/hepta-agent-trust-domain-paper-identities-v1.json.example")
ROUND114_REQUIRED_FILES = (
    "scripts/hepta_paper_receipt_contracts_v2_compat.py",
    "scripts/hepta_p1_paper_canary_backend_adapter.py",
    "scripts/hepta_p1_paper_canary_crash_emergency_closer.py",
    "scripts/hepta_p1_paper_canary_executor.py",
    "scripts/hepta_p1_paper_canary_handoff_producer.py",
    "scripts/hepta_p1_paper_canary_launch_joiner.py",
    "scripts/hepta_p1_paper_canary_owner_provisioner.py",
    "scripts/hepta_p1_paper_canary_root_coordinator.py",
    "scripts/hepta_p1_paper_canary_root_finalizer.py",
    "scripts/hepta_p1_paper_canary_terminal_prover.py",
    "systemd/hepta-local-paper-authority@.service",
    "systemd/hepta-p1-paper-canary-capture.service",
    "systemd/hepta-p1-paper-canary-executor.service",
    "systemd/hepta-p1-paper-canary-finalizer.socket",
    "systemd/hepta-p1-paper-canary-finalizer@.service",
    "systemd/hepta-p1-paper-canary-root-coordinator.service",
    "tests/hepta_p1_paper_canary_executor_tests.py",
    "tests/hepta_p1_paper_canary_launch_tests.py",
    "tests/hepta_p1_paper_canary_root_finalizer_tests.py",
    "tests/hepta_local_paper_atomic_replace_sandbox_tests.py",
)
ROUND114_EXPLICIT_INCLUDE_FILES = ROUND114_REQUIRED_FILES[:16]


class AgentOsSourceBundleTests(unittest.TestCase):
    def local_paper_unit_fixture(self, root: Path) -> None:
        for relative in (
                "systemd/hepta-local-ai-paper-agent.service",
                "systemd/hepta-local-paper-session-renew.service",
                "systemd/hepta-local-paper-session-renew.timer"):
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((REPOSITORY / relative).read_bytes())

    def local_paper_manual_start_fixture(self, root: Path) -> None:
        for relative in (
                "scripts/hepta_local_ai_paper_agent.py",
                "scripts/run_paper_repair.py",
                "scripts/run_paper_safe_recover.py",
                "scripts/run_paper_safe_recover_guard.py",
                "docs/RUNBOOK-STARTUP.md"):
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((REPOSITORY / relative).read_bytes())

    def plugin_boundary_fixture(self, root: Path) -> None:
        plugin = root / "plugins/heptatrader-agent-os"
        scripts = root / "scripts"
        plugin.mkdir(parents=True)
        scripts.mkdir(parents=True)
        (plugin / ".mcp.json").write_bytes((
            REPOSITORY / "plugins/heptatrader-agent-os/.mcp.json"
        ).read_bytes())
        (scripts / "hepta_agent_mcp_launcher.py").write_bytes((
            REPOSITORY / "scripts/hepta_agent_mcp_launcher.py"
        ).read_bytes())

    def fixture(self) -> tuple[dict, dict[str, bytes]]:
        files = {
            "CMakeLists.txt": b"cmake_minimum_required(VERSION 3.8)\n",
            "HeptaTrade/CMakeLists.txt": b"add_library(fixture STATIC x.cpp)\n",
            "HeptaTrade/adapter_ib/ib_order_lifecycle.cpp": b"lifecycle\n",
            "HeptaTrade/adapter_ib/ib_order_lifecycle.h": b"#pragma once\n",
            "HeptaTrade/client/native_tool_discovery_contract.cpp":
                b"discovery\n",
            "HeptaTrade/client/native_tool_discovery_contract.h":
                b"#pragma once\n",
            "HeptaTrade/client/native_tool_client.cpp": b"client\n",
            "HeptaTrade/execution/execution_event_feed.cpp": b"protocol\n",
            "HeptaTrade/execution/execution_event_feed_client.cpp": b"client\n",
            "HeptaTrade/execution/execution_event_feed_client.h":
                b"#pragma once\n",
            "HeptaTrade/execution/execution_event_feed_contract.h":
                b"#pragma once\n",
            "HeptaTrade/execution/execution_event_feed_server.cpp": b"server\n",
            "HeptaTrade/execution/execution_event_feed_server.h":
                b"#pragma once\n",
            "HeptaTrade/execution/execution_event_feed_transport.cpp":
                b"transport\n",
            "HeptaTrade/execution/execution_event_feed_transport.h":
                b"#pragma once\n",
            "HeptaTrade/execution/execution_gateway_context_binding.h":
                b"#pragma once\n",
            "HeptaTrade/execution/unix_execution_service.cpp": b"execution\n",
            "HeptaTrade/execution/unix_execution_service_client.cpp": b"client\n",
            "HeptaTrade/execution/unix_execution_service_client.h":
                b"#pragma once\n",
            "HeptaTrade/execution/unix_execution_service_internal.h": b"internal\n",
            "HeptaTrade/execution/unix_execution_service_server.h":
                b"#pragma once\n",
            "HeptaTrade/execution/unix_execution_service_transport.cpp":
                b"transport\n",
            "HeptaTrade/tool_host/hepta_tool_gatewayd.cpp": b"gateway\n",
            "HeptaTrade/tool_host/typed_tool_framing.cpp": b"framing\n",
            "HeptaTrade/tool_host/typed_tool_protocol.cpp": b"protocol\n",
            "HeptaTrade/tool_host/typed_tool_result_codec.cpp": b"result\n",
            "HeptaTrade/tools/trading_tool_registry.cpp": b"tools\n",
            "README.md": b"readme\n",
            "VERSION": b"fixture\n",
            "adapters/mcp/hepta_mcp_server.py": b"server\n",
            "cmake/verify_gateway_forbidden_symbols.cmake":
                b"message(STATUS \"fixture\")\n",
            "plugins/heptatrader-agent-os/.mcp.json": b"{}\n",
            "scripts/check_heptatrader_ctest_inventory.py":
                b"check inventory\n",
            "scripts/check_no_direct_broker_paths.py": b"check\n",
            "scripts/check_hepta_agent_trust_domains.py": b"check trust\n",
            "scripts/hepta_agent_trust_domain.py": b"runtime trust\n",
            "scripts/run_execution_gateway_soak.py": b"soak\n",
            "systemd/hepta-agent-trust-domain-policy-v1.json": b"{}\n",
            "systemd/hepta-agent-trust-domain.json.example": b"{}\n",
            "systemd/hepta-execution-gateway-paper.env.example":
                b"HEPTA_TOOL_ALLOW_TRADE=0\n",
            "systemd/hepta-execution-events-simulator@.socket":
                b"[Socket]\n",
            "systemd/hepta-execution-simulator@.service":
                b"[Service]\n",
            "systemd/hepta-execution-simulator@.socket":
                b"[Socket]\n",
            "systemd/hepta-tool-gateway-domain.env.example":
                b"HEPTA_AGENT_TRUST_DOMAIN=fixture\n",
            "systemd/hepta-tool-gateway.service": b"[Service]\n",
            "systemd/hepta-tool-gateway.socket": b"[Socket]\n",
            "systemd/hepta-tool-gateway@.service": b"[Service]\n",
            "systemd/hepta-tool-gateway@.socket": b"[Socket]\n",
            "systemd/hepta-tool-session-supervisor@.socket": b"[Socket]\n",
            "systemd/hepta-trader.service": b"[Service]\nExecStart=/legacy\n",
            "systemd/hepta-openclaw-agent-trader-ai-native.service":
                b"[Service]\nExecStart=/legacy-openclaw\n",
            "systemd/ibgateway.service": b"[Service]\nExecStart=/legacy-ib\n",
            "systemd/hepta-scalping.service":
                b"[Service]\nExecStart=/legacy-scalping\n",
            "HeptaTrade/HeptaDemoStrategyTrader.cpp": b"legacy\n",
            "HeptaStrategy/legacy.cpp": b"legacy\n",
            "hepta_ops/cli.py": b"operations\n",
            "tests/fixtures/hepta-agent-trust-domains-v1.json": b"{}\n",
            "tests/heptatrader-agent-os-ctest-inventory-v1.json": b"{}\n",
            "tests/heptatrader-repository-ctest-inventory-v1.json": b"{}\n",
            "tests/hepta_agent_trust_domain_tests.py": b"tests\n",
            "tests/heptatrader_ctest_inventory_tests.py": b"repository tests\n",
            "tests/heptatrader_delivery_closure_tests.py": b"evidence\n",
        }
        files.update({
            "HeptaTrade/adapter_ib/ib_gateway_adapter_reduce_only.cpp":
                b"reduce only\n",
            "HeptaTrade/execution/execution_authoritative_flatten.cpp":
                b"flatten\n",
            "HeptaTrade/execution/execution_authoritative_flatten_dispatch.cpp":
                b"flatten dispatch\n",
            "HeptaTrade/execution/execution_authority.h": b"#pragma once\n",
            "HeptaTrade/execution/execution_coordinator.cpp":
                b"coordinator\n",
            "HeptaTrade/execution/execution_coordinator.h":
                b"#pragma once\n",
            "HeptaTrade/execution/execution_place_order_dispatch.cpp":
                b"place dispatch\n",
            "HeptaTrade/execution/execution_service_protocol.cpp":
                b"service protocol\n",
            "HeptaTrade/execution/execution_service_protocol.h":
                b"#pragma once\n",
            "HeptaTrade/execution/ib_paper_authoritative_flatten.cpp":
                b"paper flatten\n",
            "HeptaTrade/execution/ib_paper_execution_flatten_guard.cpp":
                b"flatten guard\n",
            "HeptaTrade/execution/ib_paper_execution_hook_authority.cpp":
                b"hook authority\n",
            "HeptaTrade/execution/ib_paper_execution_hook_authority.h":
                b"#pragma once\n",
            "HeptaTrade/execution/ib_paper_execution_runtime_flatten.cpp":
                b"runtime flatten\n",
            "HeptaTrade/execution/ib_paper_flatten_plan_binding.cpp":
                b"flatten binding\n",
            "HeptaTrade/execution/ib_paper_flatten_plan_binding.h":
                b"#pragma once\n",
            "HeptaTrade/execution/unix_execution_service_flatten.cpp":
                b"unix flatten\n",
            "HeptaTrade/execution/unix_execution_service_flatten_client.cpp":
                b"unix flatten client\n",
            "HeptaTrade/execution/unix_execution_service_flatten_permit.cpp":
                b"unix flatten permit\n",
            "scripts/check_hepta_broker_network_policy.py":
                b"check broker network\n",
            "scripts/hepta_broker_egress_policy.py":
                b"broker egress policy\n",
            "scripts/hepta_shadow_host_installer.py":
                b"shadow host installer\n",
            "scripts/hepta_campaignctl.py":
                b"campaign client\n",
            "scripts/hepta_ib_paper_campaign_operator.py":
                b"campaign operator\n",
            "scripts/hepta_ib_paper_domain_authority.py":
                b"paper domain authority\n",
            "scripts/hepta_paper_receipt_contracts.py":
                b"paper receipt contracts\n",
            "scripts/hepta_p1_shadow_host_controller.py":
                b"P1 root host controller\n",
            "scripts/hepta_p1_load_probe_validator.py":
                b"P1 load probe validator\n",
            "scripts/build_hepta_p1_observation_policy.py":
                b"P1 observation policy builder\n",
            "scripts/hepta_p1_shadow_observer_controller.py":
                b"P1 observer controller\n",
            "scripts/hepta_p1_shadow_admission_launcher.py":
                b"P1 admission launcher\n",
            "scripts/hepta_p1_safety_soak_policy_planner.py":
                b"P1 safety-soak policy planner\n",
            "scripts/hepta_p1_safety_soak_campaign_coordinator.py":
                b"P1 safety-soak campaign coordinator\n",
            "scripts/hepta_p1_safety_soak_observer_worker.py":
                b"P1 safety-soak observer worker\n",
            "scripts/hepta_p1_safety_soak_recorder_worker.py":
                b"P1 safety-soak recorder worker\n",
            "scripts/hepta_p1_safety_soak_fault_pin_producer.py":
                b"P1 safety-soak fault pin producer\n",
            "scripts/hepta_p1_safety_soak_evidence_recorder.py":
                b"P1 safety-soak evidence recorder\n",
            "scripts/hepta_p1_safety_soak_independent_observer.py":
                b"P1 safety-soak independent observer\n",
            "scripts/hepta_p1_safety_soak_root_fault_injector.py":
                b"P1 safety-soak root fault injector\n",
            "scripts/hepta_p1_safety_soak_auditor.py":
                b"P1 cumulative safety-soak auditor\n",
            "scripts/hepta_p1_watch_to_paper_handoff.py":
                b"P1 WATCH-to-PAPER handoff\n",
            "scripts/hepta_local_paper_control.py":
                b"local PAPER control\n",
            "scripts/hepta_p1_watch_profile_deployer.py":
                b"P1 WATCH profile deployer\n",
            "scripts/hepta_p1_watch_activation_transaction.py":
                b"P1 WATCH activation transaction\n",
            "scripts/hepta_bounded_shadow_closure_verifier.py":
                b"bounded SHADOW closure verifier\n",
            "scripts/hepta_bounded_shadow_observer.py":
                b"bounded SHADOW observer\n",
            "scripts/hepta_market_context_builder.py":
                b"market context builder\n",
            "scripts/hepta_market_evidence_normalizer.py":
                b"market evidence normalizer\n",
            "scripts/hepta_market_official_source_extractor.py":
                b"market official source extractor\n",
            "scripts/hepta_eurusd_confirmed_momentum_strategy.py":
                b"confirmed momentum strategy\n",
            "scripts/hepta_shadow_market_history.py":
                b"SHADOW market history\n",
            "scripts/hepta_strategy_shadow_runner.py":
                b"strategy SHADOW runner\n",
            "scripts/hepta_strategy_contracts.py":
                b"strategy contracts\n",
            "scripts/validate_hepta_strategy_decision_receipt.py":
                b"decision receipt validator\n",
            "scripts/hepta_official_source_capture.py":
                b"official source capture\n",
            "scripts/hepta_shadow_watch_collector.py":
                b"shadow WATCH collector\n",
            "scripts/hepta_shadow_watch_exporter.py":
                b"shadow WATCH exporter\n",
            "scripts/hepta_shadow_watch_custodian.py":
                b"shadow WATCH custodian\n",
            "scripts/run_hepta_broker_network_rootful_gate.py":
                b"rootful broker network gate\n",
            "scripts/run_hepta_broker_network_hard_isolation_gate.py":
                b"native broker network hard-isolation gate\n",
            "scripts/run_hepta_paper_domain_rootful_systemd_gate.py":
                b"rootful PAPER systemd gate\n",
            "scripts/run_hepta_p1_dual_domain_rootful_gate.py":
                b"P1 dual-domain rootful rehearsal\n",
            "scripts/run_hepta_p1_campaign_rootful_liveness_gate.py":
                b"P1 campaign rootful liveness rehearsal\n",
            "tests/hepta_p1_shadow_host_controller_tests.py":
                b"P1 root host controller tests\n",
            "tests/hepta_p1_load_probe_validator_tests.py":
                b"P1 load probe validator tests\n",
            "tests/hepta_p1_observer_controller_tests.py":
                b"P1 observer controller tests\n",
            "tests/hepta_p1_shadow_admission_launcher_tests.py":
                b"P1 admission launcher tests\n",
            "tests/hepta_p1_safety_soak_policy_planner_tests.py":
                b"P1 safety-soak policy planner tests\n",
            "tests/hepta_p1_safety_soak_campaign_coordinator_tests.py":
                b"P1 safety-soak campaign coordinator tests\n",
            "tests/hepta_p1_safety_soak_fault_pin_producer_tests.py":
                b"P1 safety-soak fault pin producer tests\n",
            "tests/hepta_p1_safety_soak_evidence_recorder_tests.py":
                b"P1 safety-soak evidence recorder tests\n",
            "tests/hepta_p1_safety_soak_independent_observer_tests.py":
                b"P1 safety-soak independent observer tests\n",
            "tests/hepta_p1_safety_soak_root_fault_injector_tests.py":
                b"P1 safety-soak root fault injector tests\n",
            "tests/hepta_p1_safety_soak_auditor_tests.py":
                b"P1 cumulative safety-soak auditor tests\n",
            "tests/hepta_p1_watch_to_paper_handoff_tests.py":
                b"P1 WATCH-to-PAPER handoff tests\n",
            "tests/hepta_local_paper_control_tests.py":
                b"local PAPER control tests\n",
            "tests/hepta_rootful_systemd_base_tests.py":
                b"rootful base contract tests\n",
            "tests/hepta_systemd_gate_apparmor_tests.py":
                b"rootful AppArmor contract tests\n",
            "tests/hepta_p1_watch_profile_deployer_tests.py":
                b"P1 WATCH profile deployer tests\n",
            "tests/hepta_p1_watch_activation_transaction_tests.py":
                b"P1 WATCH activation transaction tests\n",
            "tests/hepta_bounded_shadow_closure_verifier_tests.py":
                b"bounded SHADOW closure verifier tests\n",
            "tests/run_hepta_broker_network_hard_isolation_gate_fixture.py":
                b"native broker hard-isolation command-fake tests\n",
            "tests/run_hepta_paper_domain_rootful_systemd_gate_fixture.py":
                b"PAPER-domain fake-Docker tests\n",
            "tests/paper_domain_rootful_systemd/Dockerfile":
                b"FROM fixture\n",
            "tests/paper_domain_rootful_systemd/"
            "hepta-paper-domain-systemd-entrypoint":
                b"#!/bin/sh\n",
            "tests/paper_domain_rootful_systemd/"
            "hepta-paper-domain-rootful-systemd.target": b"[Unit]\n",
            "tests/paper_domain_rootful_systemd/"
            "hepta_paper_domain_rootful_inner_gate.py": b"inner gate\n",
            "tests/paper_domain_rootful_systemd/"
            "hepta_paper_inert_execution_stub.py": b"inert execution\n",
            "tests/run_hepta_p1_dual_domain_rootful_gate_fixture.py":
                b"P1 dual-domain fake-Docker tests\n",
            "tests/p1_dual_domain_rootful_systemd/Dockerfile":
                b"FROM fixture\n",
            "tests/p1_dual_domain_rootful_systemd/"
            "hepta-p1-dual-domain-systemd-entrypoint":
                b"#!/bin/sh\n",
            "tests/p1_dual_domain_rootful_systemd/"
            "hepta-p1-dual-domain-rootful.target":
                b"[Unit]\n",
            "tests/p1_dual_domain_rootful_systemd/"
            "hepta-p1-dual-watch@.service": b"[Service]\n",
            "tests/p1_dual_domain_rootful_systemd/"
            "hepta-p1-dual-watch@.socket": b"[Socket]\n",
            "tests/p1_dual_domain_rootful_systemd/"
            "hepta-p1-dual-paper@.service": b"[Service]\n",
            "tests/p1_dual_domain_rootful_systemd/"
            "hepta-p1-dual-paper@.socket": b"[Socket]\n",
            "tests/p1_dual_domain_rootful_systemd/"
            "hepta_p1_dual_domain_daemon.py": b"dual-domain daemon\n",
            "tests/p1_dual_domain_rootful_systemd/"
            "hepta_p1_dual_domain_inner_gate.py": b"inner gate\n",
            "tests/run_hepta_p1_campaign_rootful_liveness_gate_fixture.py":
                b"P1 campaign liveness fake-Docker tests\n",
            "tests/p1_campaign_rootful_liveness_systemd/Dockerfile":
                b"FROM fixture\n",
            "tests/p1_campaign_rootful_liveness_systemd/"
            "hepta-p1-liveness-systemd-entrypoint": b"#!/bin/sh\n",
            "tests/p1_campaign_rootful_liveness_systemd/"
            "hepta-p1-campaign-rootful-liveness.target": b"[Unit]\n",
            "tests/p1_campaign_rootful_liveness_systemd/"
            "hepta-p1-liveness-coordinator.service": b"[Service]\n",
            "tests/p1_campaign_rootful_liveness_systemd/"
            "hepta-p1-liveness-watchdog.service": b"[Service]\n",
            "tests/p1_campaign_rootful_liveness_systemd/"
            "hepta-p1-liveness-worker.service": b"[Service]\n",
            "tests/p1_campaign_rootful_liveness_systemd/"
            "hepta_p1_liveness_daemon.py": b"liveness daemon\n",
            "tests/p1_campaign_rootful_liveness_systemd/"
            "hepta_p1_liveness_inner_gate.py": b"liveness inner gate\n",
            "tests/rootful_systemd_base/Dockerfile": b"FROM fixture\n",
            "strategies/eurusd-confirmed-momentum-shadow-v2.json":
                b"{}\n",
            "systemd/hepta-agent-trust-domain-paper-identities-v1.json.example":
                PAPER_IDENTITY_SOURCE.read_bytes(),
            "systemd/hepta-broker-egress-policy.service":
                b"[Service]\n",
            "systemd/hepta-broker-network-policy-v1.json": b"{}\n",
            "systemd/hepta-p1-watch-activation.service":
                b"[Service]\n",
            "systemd/hepta-p1-watch-activation-reconcile.service":
                b"[Service]\n",
            "systemd/hepta-p1-watch-activation-reconcile.timer":
                b"[Timer]\n",
            "systemd/hepta-p1-safety-soak-campaign@.service":
                b"[Service]\n",
            "systemd/hepta-p1-safety-soak-observer-worker@.service":
                b"[Service]\n",
            "systemd/hepta-p1-safety-soak-recorder-worker@.service":
                b"[Service]\n",
            "systemd/hepta-p1-safety-soak@.target": b"[Unit]\n",
            "systemd/hepta-systemd-gate.apparmor": b"profile fixture {}\n",
            "systemd/hepta-ib-paper-campaign-operator@.service":
                b"[Service]\n",
            "systemd/hepta-ib-paper-campaign-operator@.socket":
                b"[Socket]\n",
            "systemd/hepta-ib-paper-campaign-policy-v1.json.example":
                b"{}\n",
            "systemd/hepta-ib-paper-campaign-policy-local-v4.json.example":
                b"{}\n",
            "systemd/hepta-ib-paper-campaign-policy-p1-v5.json.example":
                b"{}\n",
            "systemd/hepta-ib-paper-domain-authorizations-v1.json.example":
                b"{}\n",
            "systemd/hepta-ib-paper-domain-preflight@.service":
                b"[Service]\n",
            "systemd/hepta-shadow-watch-collector@.service":
                b"[Service]\n",
            "systemd/hepta-shadow-watch-collector@.timer":
                b"[Timer]\n",
            "systemd/hepta-shadow-watch-domain.env.example":
                b"HEPTA_SHADOW_AGENT_UID=2104\n",
            "systemd/hepta-shadow-watch-export@.service":
                b"[Service]\n",
            "systemd/hepta-shadow-watch-custodian-reconcile@.service":
                b"[Service]\n",
            "systemd/hepta-shadow-watch-custodian-reconcile@.timer":
                b"[Timer]\n",
            "systemd/hepta-shadow-watch-custodian@.service":
                b"[Service]\n",
            "systemd/hepta-service-identities-v1.json": b"{}\n",
            "systemd/hepta-tool-gateway.service.d/"
            "10-hepta-broker-egress-policy.conf":
                b"[Unit]\n",
            "systemd/hepta-tool-gateway@.service.d/"
            "10-hepta-broker-egress-policy.conf":
                b"[Unit]\n",
        })
        release_validation_sources = (
            "scripts/aggregate_hepta_execution_native_systemd_gate.py",
            "scripts/build_hepta_execution_native_vm_bundle.py",
            "scripts/build_heptatrader_delivery_closure.py",
            "scripts/build_heptatrader_engineering_closure.py",
            "scripts/build_heptatrader_evidence_index.py",
            "scripts/build_heptatrader_evidence_ingestion_request.py",
            "scripts/build_heptatrader_release_validation_closure.py",
            "scripts/build_heptatrader_verification_evidence.py",
            "scripts/check_hepta_agent_os_provisioned_host.py",
            "scripts/converge_ctp_vendor_headers.py",
            "scripts/hepta_service_identities.py",
            "scripts/heptatrader_secure_artifacts.py",
            "scripts/hepta_agent_mcp_launcher.py",
            "scripts/hepta_agent_session_bootstrap.py",
            "scripts/hepta_strategy_replay_evaluator.py",
            "scripts/run_execution_gateway_soak.py",
            "scripts/run_hepta_execution_native_systemd_gate.py",
            "scripts/run_hepta_execution_rootful_systemd_gate.py",
            "scripts/verify_hepta_execution_native_vm_bundle.py",
            "scripts/verify_heptatrader_agent_os_source_bundle.py",
            "scripts/verify_heptatrader_clean_source_bundle.py",
            "scripts/verify_heptatrader_delivery_closure.py",
            "scripts/verify_heptatrader_engineering_closure.py",
            "scripts/verify_heptatrader_evidence_index.py",
            "scripts/verify_heptatrader_evidence_ingestion_receipt.py",
            "scripts/verify_heptatrader_evidence_set.py",
            "scripts/verify_heptatrader_prebuilt_assets.py",
            "scripts/verify_heptatrader_release_validation_closure.py",
            "scripts/verify_heptatrader_runtime_package.py",
            "scripts/verify_heptatrader_vendor_assets.py",
            "hepta_ops/__init__.py",
            "hepta_ops/agent_os_source.py",
            "hepta_ops/registry.py",
            "scripts/hepta_p1_paper_kill_switch_bootstrap.py",
            "scripts/hepta_p1_safety_soak_campaign_freezer.py",
            "scripts/run_hepta_agent_os_rootful_systemd_e2e_gate.py",
            "tests/hepta_p1_paper_kill_switch_bootstrap_tests.py",
            "tests/hepta_p1_safety_soak_campaign_freezer_tests.py",
            "tests/heptatrader_release_validation_installed_layout_tests.py",
            "tests/run_execution_gateway_soak_provenance_fixture.py",
            "tests/run_hepta_agent_os_rootful_systemd_e2e_gate_fixture.py",
            "tests/agent_os_rootful_systemd/Dockerfile",
            "tests/agent_os_rootful_systemd/hepta-agent-os-systemd-entrypoint",
            "tests/agent_os_rootful_systemd/hepta-agent-os-rootful-e2e.target",
            "tests/agent_os_rootful_systemd/hepta_agent_os_rootful_inner_gate.py",
            "tests/agent_os_rootful_systemd/hepta_broker_network_rootful_probe.py",
        )
        for relative in release_validation_sources:
            files.setdefault(
                relative, f"release validation fixture: {relative}\n".encode())
        for relative in agent_os_source.load_policy(POLICY).required_files:
            files.setdefault(
                relative, f"required source fixture: {relative}\n".encode())
        records = [
            {
                "path": path,
                "mode": "0644",
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            for path, data in sorted(files.items())
        ]
        strict = {
            "schema": "hepta.clean-source-bundle.v2",
            "version": "fixture",
            "git_head": "1" * 40,
            "root": "heptatrader-fixture",
            "file_count": len(records),
            "files_sha256": "fixture-files",
            "files": records,
        }
        return strict, files

    def test_default_paper_identity_source_is_exact_deny_all(self) -> None:
        payload = PAPER_IDENTITY_SOURCE.read_bytes()
        self.assertEqual(len(payload), 257)
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            "4a94d555cad61a9de67b809cfae301eadd6ebf2511714c93343f10decb34e435")
        document = json.loads(payload.decode("utf-8"))
        self.assertEqual(
            document,
            {
                "identities": [],
                "live_authorized": False,
                "paper_authorized": False,
                "schema": "hepta.agent-trust-domain-paper-identities.v1",
                "source_policy_sha256": (
                    "sha256:08d430d53e4813cd0a43a23beeb92344af2130dca425814cbf7285059d90f90c"),
                "version": 1,
            })

    def test_policy_excludes_legacy_and_builds_canonical_tar(self) -> None:
        policy = agent_os_source.load_policy(POLICY)
        strict, files = self.fixture()
        records = agent_os_source.selected_records(strict, policy)
        paths = {item["path"] for item in records}
        self.assertNotIn("HeptaTrade/HeptaDemoStrategyTrader.cpp", paths)
        self.assertNotIn("HeptaStrategy/legacy.cpp", paths)
        # The canonical release CLI is part of the portable Agent-OS bundle;
        # its sandbox/registry dependencies must travel with it rather than
        # being hidden behind a repository-only import path.
        self.assertIn("hepta_ops/cli.py", paths)
        self.assertIn("hepta_ops/registry.py", paths)
        self.assertIn("hepta_ops/sandbox.py", paths)
        self.assertNotIn("systemd/hepta-trader.service", paths)
        self.assertNotIn(
            "systemd/hepta-openclaw-agent-trader-ai-native.service", paths)
        self.assertNotIn("systemd/ibgateway.service", paths)
        self.assertNotIn("systemd/hepta-scalping.service", paths)
        self.assertEqual(
            {path for path in paths if path.startswith("systemd/")},
            {
                "systemd/hepta-agent-trust-domain-policy-v1.json",
                "systemd/hepta-agent-trust-domain-paper-identities-v1.json.example",
                "systemd/hepta-agent-trust-domain.json.example",
                "systemd/hepta-agent-broker-egress-policy.conf.example",
                "systemd/hepta-agent-host-identity.conf.example",
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
                "systemd/hepta-execution-events-ib-paper.socket",
                "systemd/hepta-execution-events-ib-paper@.socket",
                "systemd/hepta-execution-events-simulator.socket",
                "systemd/hepta-execution-gateway-paper.env.example",
                "systemd/hepta-execution-gateway-paper-domain.env.example",
                "systemd/hepta-execution-ib-paper.env.example",
                "systemd/hepta-execution-ib-paper-domain.env.example",
                "systemd/hepta-execution-ib-paper.service",
                "systemd/hepta-execution-ib-paper.service.d/"
                "10-hepta-broker-egress-policy.conf",
                "systemd/hepta-execution-ib-paper.socket",
                "systemd/hepta-execution-ib-paper@.service",
                "systemd/hepta-execution-ib-paper@.service.d/"
                "10-hepta-broker-egress-policy.conf",
                "systemd/hepta-execution-ib-paper@.socket",
                "systemd/hepta-execution-simulator.env.example",
                "systemd/hepta-execution-simulator.service",
                "systemd/hepta-execution-simulator.socket",
                "systemd/hepta-execution-events-simulator@.socket",
                "systemd/hepta-execution-simulator@.service",
                "systemd/hepta-execution-simulator@.socket",
                "systemd/hepta-ib-paper-campaign-operator@.service",
                "systemd/hepta-ib-paper-campaign-operator@.socket",
                "systemd/hepta-ib-paper-campaign-policy-v1.json.example",
                "systemd/hepta-ib-paper-campaign-policy-local-v4.json.example",
                "systemd/hepta-ib-paper-campaign-policy-p1-v5.json.example",
                "systemd/hepta-local-ai-paper-agent.service",
                "systemd/hepta-local-ai-paper-agent.env.example",
                "systemd/hepta-local-paper-safe-recover.service",
                "systemd/hepta-local-paper-safe-recover.timer",
                "systemd/hepta-local-paper-session-renew.service",
                "systemd/hepta-local-paper-session-renew.timer",
                "systemd/hepta-local-paper-supervisor.service",
                "systemd/hepta-local-paper-supervisor.timer",
                "systemd/hepta-ib-paper-domain-authorizations-v1.json.example",
                "systemd/hepta-ib-paper-domain-preflight@.service",
                "systemd/hepta-shadow-watch-collector@.service",
                "systemd/hepta-shadow-watch-collector@.timer",
                "systemd/hepta-shadow-watch-domain.env.example",
                "systemd/hepta-shadow-watch-export@.service",
                "systemd/hepta-shadow-watch-custodian-reconcile@.service",
                "systemd/hepta-shadow-watch-custodian-reconcile@.timer",
                "systemd/hepta-shadow-watch-custodian@.service",
                "systemd/hepta-service-identities-v1.json",
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
            })
        self.assertNotIn(
            "tests/heptatrader_delivery_closure_tests.py", paths)
        self.assertNotIn(
            "tests/heptatrader_ctest_inventory_tests.py", paths)
        self.assertNotIn(
            "tests/heptatrader-repository-ctest-inventory-v1.json", paths)
        self.assertIn(
            "cmake/verify_gateway_forbidden_symbols.cmake", paths)
        self.assertIn(
            "scripts/check_heptatrader_ctest_inventory.py", paths)
        self.assertIn(
            "scripts/run_hepta_paper_domain_rootful_systemd_gate.py",
            paths)
        self.assertIn(
            "scripts/run_hepta_broker_network_hard_isolation_gate.py",
            paths)
        self.assertIn(
            "scripts/run_hepta_p1_dual_domain_rootful_gate.py", paths)
        self.assertIn(
            "tests/heptatrader-agent-os-ctest-inventory-v1.json", paths)

        allowed_systemd = {
            "systemd/hepta-agent-host-identity.conf.example",
            "systemd/hepta-agent-broker-egress-policy.conf.example",
            "systemd/hepta-agent-trust-domain-paper-identities-v1.json.example",
            "systemd/hepta-agent-trust-domain.json.example",
            "systemd/hepta-agent-trust-domain-policy-v1.json",
            "systemd/hepta-broker-egress-policy.service",
            "systemd/hepta-broker-network-policy-v1.json",
            "systemd/hepta-local-paper-authority@.service",
            "systemd/hepta-p1-paper-canary-capture.service",
            "systemd/hepta-p1-paper-canary-executor.service",
            "systemd/hepta-p1-paper-canary-finalizer.socket",
            "systemd/hepta-p1-paper-canary-finalizer@.service",
            "systemd/hepta-p1-paper-canary-root-coordinator.service",
            "systemd/hepta-p1-watch-activation.service",
            "systemd/hepta-p1-watch-activation-reconcile.service",
            "systemd/hepta-p1-watch-activation-reconcile.timer",
            "systemd/hepta-p1-safety-soak-campaign@.service",
            "systemd/hepta-p1-safety-soak-observer-worker@.service",
            "systemd/hepta-p1-safety-soak-recorder-worker@.service",
            "systemd/hepta-p1-safety-soak@.target",
            "systemd/hepta-systemd-gate.apparmor",
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
            "systemd/hepta-ib-paper-campaign-operator@.service",
            "systemd/hepta-ib-paper-campaign-operator@.socket",
            "systemd/hepta-ib-paper-campaign-policy-v1.json.example",
            "systemd/hepta-ib-paper-campaign-policy-local-v4.json.example",
            "systemd/hepta-ib-paper-campaign-policy-p1-v5.json.example",
            "systemd/hepta-local-ai-paper-agent.service",
            "systemd/hepta-local-ai-paper-agent.env.example",
            "systemd/hepta-local-paper-safe-recover.service",
            "systemd/hepta-local-paper-safe-recover.timer",
            "systemd/hepta-local-paper-session-renew.service",
            "systemd/hepta-local-paper-session-renew.timer",
            "systemd/hepta-local-paper-supervisor.service",
            "systemd/hepta-local-paper-supervisor.timer",
            "systemd/hepta-ib-paper-domain-authorizations-v1.json.example",
            "systemd/hepta-ib-paper-domain-preflight@.service",
            "systemd/hepta-local-paper-fail-close@.service",
            "systemd/hepta-p1-paper-terminal-cutoff@.service",
            "systemd/hepta-p1-paper-terminal-witness-verifier@.service",
            "systemd/hepta-paper-terminal-latch-committer@.service",
            "systemd/hepta-shadow-watch-collector@.service",
            "systemd/hepta-shadow-watch-collector@.timer",
            "systemd/hepta-shadow-watch-domain.env.example",
            "systemd/hepta-shadow-watch-export@.service",
            "systemd/hepta-shadow-watch-custodian-reconcile@.service",
            "systemd/hepta-shadow-watch-custodian-reconcile@.timer",
            "systemd/hepta-shadow-watch-custodian@.service",
            "systemd/hepta-service-identities-v1.json",
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
        self.assertEqual(
            {path for path in policy.include_files
             if path.startswith("systemd/")},
            allowed_systemd)
        self.assertFalse(any(
            prefix == "systemd/" for prefix in policy.include_prefixes))
        selected = {path: files[path] for path in paths}
        manifest = agent_os_source.manifest_document(
            "fixture", strict, "a" * 64, "b" * 64, policy, records)
        first = agent_os_source.build_tar(manifest, selected)
        second = agent_os_source.build_tar(manifest, selected)
        self.assertEqual(first, second)
        with tempfile.TemporaryDirectory(prefix="hepta-agent-source-") as temp:
            bundle = Path(temp) / "bundle.tar"
            bundle.write_bytes(first)
            agent_os_source.verify_tar(bundle, manifest, selected)

    def test_manifest_mutation_is_rejected(self) -> None:
        policy = agent_os_source.load_policy(POLICY)
        strict, files = self.fixture()
        records = agent_os_source.selected_records(strict, policy)
        selected = {item["path"]: files[item["path"]] for item in records}
        manifest = agent_os_source.manifest_document(
            "fixture", strict, "a" * 64, "b" * 64, policy, records)
        with tempfile.TemporaryDirectory(prefix="hepta-agent-source-") as temp:
            bundle = Path(temp) / "bundle.tar"
            bundle.write_bytes(agent_os_source.build_tar(manifest, selected))
            forged = json.loads(json.dumps(manifest))
            forged["paper_authorized"] = True
            with self.assertRaisesRegex(
                    agent_os_source.AgentOsSourceError, "canonical"):
                agent_os_source.verify_tar(bundle, forged, selected)

    def test_round114_canary_source_closure_is_exact_and_required(self) -> None:
        raw_policy = json.loads(POLICY.read_text(encoding="utf-8"))
        include_files = raw_policy["include_files"]
        required_files = raw_policy["required_files"]
        self.assertEqual(len(include_files), len(set(include_files)))
        self.assertEqual(len(required_files), len(set(required_files)))
        for relative in ROUND114_EXPLICIT_INCLUDE_FILES:
            with self.subTest(include=relative):
                self.assertEqual(include_files.count(relative), 1)
        for relative in ROUND114_REQUIRED_FILES:
            with self.subTest(required=relative):
                self.assertEqual(required_files.count(relative), 1)

        policy = agent_os_source.load_policy(POLICY)
        intact, _files = self.fixture()
        selected = {
            record["path"]
            for record in agent_os_source.selected_records(intact, policy)
        }
        self.assertTrue(set(ROUND114_REQUIRED_FILES).issubset(selected))
        for missing in ROUND114_REQUIRED_FILES:
            with self.subTest(omitted=missing):
                strict, _files = self.fixture()
                strict["files"] = [
                    record for record in strict["files"]
                    if record["path"] != missing
                ]
                with self.assertRaisesRegex(
                        agent_os_source.AgentOsSourceError,
                        re.escape(missing)):
                    agent_os_source.selected_records(strict, policy)

    def test_shadow_dependency_source_closure_is_required(self) -> None:
        policy = agent_os_source.load_policy(POLICY)
        required = (
            "scripts/hepta_p1_shadow_host_controller.py",
            "scripts/hepta_p1_load_probe_validator.py",
            "scripts/build_hepta_p1_observation_policy.py",
            "scripts/hepta_p1_shadow_observer_controller.py",
            "scripts/hepta_p1_shadow_admission_launcher.py",
            "scripts/hepta_p1_safety_soak_campaign_freezer.py",
            "scripts/hepta_p1_safety_soak_policy_planner.py",
            "scripts/hepta_p1_safety_soak_campaign_coordinator.py",
            "scripts/hepta_p1_safety_soak_observer_worker.py",
            "scripts/hepta_p1_safety_soak_recorder_worker.py",
            "scripts/hepta_p1_safety_soak_fault_pin_producer.py",
            "scripts/hepta_p1_safety_soak_evidence_recorder.py",
            "scripts/hepta_p1_safety_soak_independent_observer.py",
            "scripts/hepta_p1_safety_soak_root_fault_injector.py",
            "scripts/hepta_p1_safety_soak_auditor.py",
            "scripts/hepta_p1_watch_to_paper_handoff.py",
            "scripts/hepta_local_paper_control.py",
            "scripts/hepta_p1_paper_kill_switch_bootstrap.py",
            "scripts/hepta_p1_watch_profile_deployer.py",
            "scripts/hepta_p1_watch_activation_transaction.py",
            "scripts/hepta_shadow_host_installer.py",
            "scripts/hepta_bounded_shadow_closure_verifier.py",
            "scripts/hepta_bounded_shadow_observer.py",
            "scripts/hepta_market_context_builder.py",
            "scripts/hepta_market_evidence_normalizer.py",
            "scripts/hepta_market_official_source_extractor.py",
            "scripts/hepta_eurusd_confirmed_momentum_strategy.py",
            "scripts/hepta_shadow_market_history.py",
            "scripts/hepta_strategy_replay_evaluator.py",
            "scripts/hepta_strategy_shadow_runner.py",
            "scripts/hepta_strategy_contracts.py",
            "scripts/validate_hepta_strategy_decision_receipt.py",
            "scripts/hepta_official_source_capture.py",
            "tests/hepta_p1_shadow_host_controller_tests.py",
            "tests/hepta_p1_load_probe_validator_tests.py",
            "tests/hepta_p1_observer_controller_tests.py",
            "tests/hepta_p1_shadow_admission_launcher_tests.py",
            "tests/hepta_p1_safety_soak_campaign_freezer_tests.py",
            "tests/hepta_p1_safety_soak_policy_planner_tests.py",
            "tests/hepta_p1_safety_soak_campaign_coordinator_tests.py",
            "tests/hepta_p1_safety_soak_fault_pin_producer_tests.py",
            "tests/hepta_p1_safety_soak_evidence_recorder_tests.py",
            "tests/hepta_p1_safety_soak_independent_observer_tests.py",
            "tests/hepta_p1_safety_soak_root_fault_injector_tests.py",
            "tests/hepta_p1_safety_soak_auditor_tests.py",
            "tests/hepta_p1_watch_to_paper_handoff_tests.py",
            "tests/hepta_local_paper_control_tests.py",
            "configs/hepta-local-ai-paper-strategy-v2.json",
            "configs/hepta-local-ai-paper-strategy-v3.json",
            "scripts/hepta_local_ai_paper_agent.py",
            "scripts/prepare_repair_campaign.py",
            "scripts/run_paper_repair.py",
            "scripts/run_paper_safe_recover.py",
            "scripts/run_paper_safe_recover_guard.py",
            "scripts/run_paper_session_renew.py",
            "scripts/run_paper_supervisor.py",
            "systemd/hepta-local-ai-paper-agent.env.example",
            "systemd/hepta-local-ai-paper-agent.service",
            "systemd/hepta-local-paper-safe-recover.service",
            "systemd/hepta-local-paper-safe-recover.timer",
            "systemd/hepta-local-paper-session-renew.service",
            "systemd/hepta-local-paper-session-renew.timer",
            "systemd/hepta-local-paper-supervisor.service",
            "systemd/hepta-local-paper-supervisor.timer",
            "tests/hepta_local_paper_recovery_guard_tests.py",
            "tests/hepta_local_paper_repair_tests.py",
            "tests/hepta_local_paper_supervisor_tests.py",
            "tests/hepta_p1_paper_kill_switch_bootstrap_tests.py",
            "scripts/run_hepta_agent_os_rootful_systemd_e2e_gate.py",
            "tests/run_hepta_agent_os_rootful_systemd_e2e_gate_fixture.py",
            "tests/run_execution_gateway_soak_provenance_fixture.py",
            "tests/agent_os_rootful_systemd/Dockerfile",
            "tests/agent_os_rootful_systemd/"
            "hepta-agent-os-systemd-entrypoint",
            "tests/agent_os_rootful_systemd/"
            "hepta-agent-os-rootful-e2e.target",
            "tests/agent_os_rootful_systemd/"
            "hepta_agent_os_rootful_inner_gate.py",
            "tests/agent_os_rootful_systemd/"
            "hepta_broker_network_rootful_probe.py",
            "tests/hepta_rootful_systemd_base_tests.py",
            "tests/hepta_systemd_gate_apparmor_tests.py",
            "tests/hepta_p1_watch_profile_deployer_tests.py",
            "tests/hepta_p1_watch_activation_transaction_tests.py",
            "tests/hepta_bounded_shadow_closure_verifier_tests.py",
            "scripts/run_hepta_broker_network_hard_isolation_gate.py",
            "tests/run_hepta_broker_network_hard_isolation_gate_fixture.py",
            "scripts/run_hepta_paper_domain_rootful_systemd_gate.py",
            "tests/run_hepta_paper_domain_rootful_systemd_gate_fixture.py",
            "tests/paper_domain_rootful_systemd/Dockerfile",
            "tests/paper_domain_rootful_systemd/"
            "hepta-paper-domain-systemd-entrypoint",
            "tests/paper_domain_rootful_systemd/"
            "hepta-paper-domain-rootful-systemd.target",
            "tests/paper_domain_rootful_systemd/"
            "hepta_paper_domain_rootful_inner_gate.py",
            "tests/paper_domain_rootful_systemd/"
            "hepta_paper_inert_execution_stub.py",
            "scripts/run_hepta_p1_dual_domain_rootful_gate.py",
            "tests/run_hepta_p1_dual_domain_rootful_gate_fixture.py",
            "tests/p1_dual_domain_rootful_systemd/Dockerfile",
            "tests/p1_dual_domain_rootful_systemd/"
            "hepta-p1-dual-domain-systemd-entrypoint",
            "tests/p1_dual_domain_rootful_systemd/"
            "hepta-p1-dual-domain-rootful.target",
            "tests/p1_dual_domain_rootful_systemd/"
            "hepta-p1-dual-watch@.service",
            "tests/p1_dual_domain_rootful_systemd/"
            "hepta-p1-dual-watch@.socket",
            "tests/p1_dual_domain_rootful_systemd/"
            "hepta-p1-dual-paper@.service",
            "tests/p1_dual_domain_rootful_systemd/"
            "hepta-p1-dual-paper@.socket",
            "tests/p1_dual_domain_rootful_systemd/"
            "hepta_p1_dual_domain_daemon.py",
            "tests/p1_dual_domain_rootful_systemd/"
            "hepta_p1_dual_domain_inner_gate.py",
            "scripts/run_hepta_p1_campaign_rootful_liveness_gate.py",
            "tests/run_hepta_p1_campaign_rootful_liveness_gate_fixture.py",
            "tests/p1_campaign_rootful_liveness_systemd/Dockerfile",
            "tests/p1_campaign_rootful_liveness_systemd/"
            "hepta-p1-liveness-systemd-entrypoint",
            "tests/p1_campaign_rootful_liveness_systemd/"
            "hepta-p1-campaign-rootful-liveness.target",
            "tests/p1_campaign_rootful_liveness_systemd/"
            "hepta-p1-liveness-coordinator.service",
            "tests/p1_campaign_rootful_liveness_systemd/"
            "hepta-p1-liveness-watchdog.service",
            "tests/p1_campaign_rootful_liveness_systemd/"
            "hepta-p1-liveness-worker.service",
            "tests/p1_campaign_rootful_liveness_systemd/"
            "hepta_p1_liveness_daemon.py",
            "tests/p1_campaign_rootful_liveness_systemd/"
            "hepta_p1_liveness_inner_gate.py",
            "tests/rootful_systemd_base/Dockerfile",
            "strategies/eurusd-confirmed-momentum-shadow-v2.json",
            "scripts/hepta_shadow_watch_collector.py",
            "scripts/hepta_shadow_watch_exporter.py",
            "scripts/hepta_shadow_watch_custodian.py",
            "systemd/hepta-tool-gateway@.service",
            "systemd/hepta-tool-gateway@.socket",
            "systemd/hepta-tool-session-supervisor@.socket",
            "systemd/hepta-shadow-watch-collector@.service",
            "systemd/hepta-shadow-watch-collector@.timer",
            "systemd/hepta-shadow-watch-domain.env.example",
            "systemd/hepta-shadow-watch-export@.service",
            "systemd/hepta-shadow-watch-custodian@.service",
            "systemd/hepta-shadow-watch-custodian-reconcile@.service",
            "systemd/hepta-shadow-watch-custodian-reconcile@.timer",
            "systemd/hepta-service-identities-v1.json",
            "systemd/hepta-p1-watch-activation.service",
            "systemd/hepta-p1-watch-activation-reconcile.service",
            "systemd/hepta-p1-watch-activation-reconcile.timer",
            "systemd/hepta-p1-safety-soak-campaign@.service",
            "systemd/hepta-p1-safety-soak-observer-worker@.service",
            "systemd/hepta-p1-safety-soak-recorder-worker@.service",
            "systemd/hepta-p1-safety-soak@.target",
            "systemd/hepta-systemd-gate.apparmor",
            ".agents/plugins/marketplace.json",
            "docs/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md",
            "docs/BROKER-NETWORK-ISOLATION.md",
            "docs/RUNBOOK-STARTUP.md",
            "plugins/heptatrader-agent-os/.codex-plugin/plugin.json",
            "plugins/heptatrader-agent-os/README.md",
            "scripts/check_hepta_execution_provisioned_host.py",
            "systemd/hepta-agent-broker-egress-policy.conf.example",
            "systemd/hepta-agent-host-identity.conf.example",
            "systemd/hepta-execution-events-ib-paper.socket",
            "systemd/hepta-execution-events-ib-paper@.socket",
            "systemd/hepta-execution-events-simulator.socket",
            "systemd/hepta-execution-gateway-paper-domain.env.example",
            "systemd/hepta-execution-ib-paper-domain.env.example",
            "systemd/hepta-execution-ib-paper.env.example",
            "systemd/hepta-execution-ib-paper.service",
            "systemd/hepta-execution-ib-paper.service.d/"
            "10-hepta-broker-egress-policy.conf",
            "systemd/hepta-execution-ib-paper.socket",
            "systemd/hepta-execution-ib-paper@.service",
            "systemd/hepta-execution-ib-paper@.service.d/"
            "10-hepta-broker-egress-policy.conf",
            "systemd/hepta-execution-ib-paper@.socket",
            "systemd/hepta-execution-simulator.env.example",
            "systemd/hepta-execution-simulator.service",
            "systemd/hepta-execution-simulator.socket",
            "systemd/hepta-tool-gateway.env.example",
            "systemd/hepta-tool-session-supervisor.socket",
            "tests/CMakeLists.txt",
            "tests/broker_network_rootful/Dockerfile",
            "tests/broker_network_rootful/"
            "hepta_broker_network_opt_in_gate.py",
            "tests/execution_systemd_client_probe.cpp",
            "tests/execution_systemd_sandbox_probe.cpp",
            "tests/hepta_agent_os_identity_permissions.py",
            "tests/native_systemd/platform-policy-v1.json",
            "tests/rootful_systemd/Dockerfile",
            "tests/rootful_systemd/hepta-rootful-systemd-gate.target",
            "tests/rootful_systemd/hepta-systemd-entrypoint",
            "tests/rootful_systemd/hepta_execution_rootful_inner_gate.py",
            "tmpfiles.d/heptatrader-agent-os.conf",
            "tmpfiles.d/heptatrader-ib-paper.conf",
        )
        self.assertTrue(set(required).issubset(policy.required_files))
        intact, _files = self.fixture()
        agent_os_source.selected_records(intact, policy)
        for missing in sorted(policy.required_files):
            with self.subTest(missing=missing):
                strict, _files = self.fixture()
                strict["files"] = [
                    record for record in strict["files"]
                    if record["path"] != missing
                ]
                with self.assertRaisesRegex(
                        agent_os_source.AgentOsSourceError,
                        re.escape(missing)):
                    agent_os_source.selected_records(strict, policy)

    def test_systemd_verify_tuple_rejects_dangling_shadow_dependencies(
            self) -> None:
        agent_os_units.check_local_dependency_closure(
            agent_os_units.SYSTEMD_VERIFY_UNIT_PATHS)
        cases = (
            (
                agent_os_units.WATCH_EXPORT_SERVICE,
                r"OnSuccess -> hepta-shadow-watch-export@\.service",
            ),
            (
                agent_os_units.DOMAIN_SUPERVISOR_SOCKET,
                r"Requires -> hepta-tool-session-supervisor@\.socket",
            ),
            (
                agent_os_units.LOCAL_PAPER_SAFE_RECOVER_SERVICE,
                r"OnFailure -> hepta-local-paper-safe-recover\.service",
            ),
            (
                agent_os_units.LOCAL_PAPER_SESSION_RENEW_SERVICE,
                r"Unit -> hepta-local-paper-session-renew\.service",
            ),
            (
                agent_os_units.LOCAL_PAPER_SUPERVISOR_SERVICE,
                r"Unit -> hepta-local-paper-supervisor\.service",
            ),
        )
        for missing, message in cases:
            with self.subTest(missing=missing.name):
                paths = tuple(
                    path
                    for path in agent_os_units.SYSTEMD_VERIFY_UNIT_PATHS
                    if path != missing)
                with self.assertRaisesRegex(AssertionError, message):
                    agent_os_units.check_local_dependency_closure(paths)
        self.assertIn(
            agent_os_units.LOCAL_AI_PAPER_AGENT_SERVICE,
            agent_os_units.SYSTEMD_VERIFY_UNIT_PATHS)
        self.assertIn(
            agent_os_units.LOCAL_PAPER_SAFE_RECOVER_TIMER,
            agent_os_units.SYSTEMD_VERIFY_UNIT_PATHS)
        self.assertIn(
            agent_os_units.LOCAL_PAPER_SESSION_RENEW_TIMER,
            agent_os_units.SYSTEMD_VERIFY_UNIT_PATHS)
        self.assertIn(
            agent_os_units.LOCAL_PAPER_SUPERVISOR_TIMER,
            agent_os_units.SYSTEMD_VERIFY_UNIT_PATHS)

    def test_packaged_plugin_uses_per_uid_trust_domain_by_default(self) -> None:
        product_boundary.check_plugin_trust_domain_boundary(REPOSITORY)

    def test_local_paper_manual_start_boundary_is_enforced(self) -> None:
        product_boundary.check_local_paper_manual_start_boundary(REPOSITORY)
        with tempfile.TemporaryDirectory(
                prefix="hepta-local-paper-manual-start-") as temporary:
            root = Path(temporary)
            self.local_paper_manual_start_fixture(root)
            guard = root / "scripts/run_paper_safe_recover_guard.py"
            text = guard.read_text(encoding="utf-8")
            guard.write_text(text.replace(
                '"SAFE_RECOVERY_BLOCKED manual_start_required=true "',
                '"SAFE_RECOVERY_OK automatic_restart_allowed=true "', 1),
                encoding="utf-8")
            with self.assertRaisesRegex(
                    product_boundary.ProductBoundaryError,
                    "manual-start boundary drifted"):
                product_boundary.check_local_paper_manual_start_boundary(root)
        with tempfile.TemporaryDirectory(
                prefix="hepta-local-paper-v4-quarantine-") as temporary:
            root = Path(temporary)
            self.local_paper_manual_start_fixture(root)
            runbook = root / "docs/RUNBOOK-STARTUP.md"
            text = runbook.read_text(encoding="utf-8")
            runbook.write_text(text.replace(
                "CAMPAIGN_POLICY_V4_ACTIVE_P1_ADMISSION_REQUIRED",
                "CAMPAIGN_POLICY_V4_ACTIVE_ALLOWED"), encoding="utf-8")
            with self.assertRaisesRegex(
                    product_boundary.ProductBoundaryError,
                    "manual-start boundary drifted"):
                product_boundary.check_local_paper_manual_start_boundary(root)

    def test_local_paper_unit_boundary_is_enforced(self) -> None:
        product_boundary.check_local_paper_unit_boundary(REPOSITORY)
        mutations = (
            (
                "systemd/hepta-local-ai-paper-agent.service",
                "Restart=no",
                "Restart=on-failure",
                "static start-authority unit boundary drifted",
            ),
            (
                "systemd/hepta-local-ai-paper-agent.service",
                "Restart=no",
                "Restart=no\n\n[Install]\nWantedBy=multi-user.target",
                "static start-authority unit boundary drifted",
            ),
            (
                "systemd/hepta-local-ai-paper-agent.service",
                "ExecCondition=/usr/libexec/hepta-local-paper-repair "
                "pre-start-guard",
                "ExecCondition=/bin/true",
                "static start-authority unit boundary drifted",
            ),
            (
                "systemd/hepta-local-ai-paper-agent.service",
                "Requisite=hepta-tool-gateway@alpha.service "
                "hepta-execution-ib-paper@alpha.service "
                "hepta-ib-paper-campaign-operator@alpha.socket "
                "hepta-local-paper-safe-recover.timer "
                "hepta-local-paper-session-renew.timer "
                "hepta-local-paper-supervisor.timer "
                "hepta-local-ai-paper-24h-stop.timer "
                "hepta-local-ai-paper-end-flat-retry.timer",
                "Requisite=hepta-tool-gateway@alpha.service "
                "hepta-execution-ib-paper@alpha.service "
                "hepta-ib-paper-campaign-operator@alpha.socket "
                "hepta-local-paper-safe-recover.timer "
                "hepta-local-paper-session-renew.timer "
                "hepta-local-ai-paper-24h-stop.timer",
                "static start-authority unit boundary drifted",
            ),
            (
                "systemd/hepta-local-ai-paper-agent.service",
                "InaccessiblePaths=/var/lib/hepta-local-ai-paper-agent/"
                "session-authority",
                "InaccessiblePaths=/var/lib/hepta-local-ai-paper-agent/"
                "session-authority-missing",
                "static start-authority unit boundary drifted",
            ),
            (
                "systemd/hepta-local-ai-paper-agent.service",
                "CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER "
                "CAP_KILL CAP_SETGID CAP_SETUID\n",
                "CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER "
                "CAP_KILL CAP_SETGID CAP_SETUID CAP_SYS_ADMIN CAP_SYS_PTRACE "
                "CAP_DAC_READ_SEARCH CAP_SYS_CHROOT\n",
                "static start-authority unit boundary drifted",
            ),
            (
                "systemd/hepta-local-ai-paper-agent.service",
                "AmbientCapabilities=\n",
                "AmbientCapabilities=CAP_SYS_ADMIN\n",
                "static start-authority unit boundary drifted",
            ),
            (
                "systemd/hepta-local-ai-paper-agent.service",
                "RestrictNamespaces=yes",
                "RestrictNamespaces=no",
                "static start-authority unit boundary drifted",
            ),
            (
                "systemd/hepta-local-ai-paper-agent.service",
                "SystemCallFilter=~@mount",
                "SystemCallFilter=@mount",
                "static start-authority unit boundary drifted",
            ),
            (
                "systemd/hepta-local-paper-session-renew.service",
                "OnFailure=hepta-local-paper-safe-recover.service",
                "OnFailure=",
                "session-renew recovery unit boundary drifted",
            ),
            (
                "systemd/hepta-local-paper-session-renew.service",
                "ReadWritePaths=/var/lib/hepta-local-ai-paper-agent "
                "-/run/hepta-agent-alpha",
                "ReadWritePaths=-/run/hepta-agent-alpha",
                "session-renew recovery unit boundary drifted",
            ),
            (
                "systemd/hepta-local-paper-session-renew.timer",
                "OnUnitInactiveSec=1h",
                "OnUnitInactiveSec=6h",
                "session-renew recovery unit boundary drifted",
            ),
        )
        for relative, old, new, message in mutations:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory(
                    prefix="hepta-local-paper-unit-") as temporary:
                root = Path(temporary)
                self.local_paper_unit_fixture(root)
                path = root / relative
                text = path.read_text(encoding="utf-8")
                self.assertIn(old, text)
                path.write_text(text.replace(old, new, 1), encoding="utf-8")
                with self.assertRaisesRegex(
                        product_boundary.ProductBoundaryError, message):
                    product_boundary.check_local_paper_unit_boundary(root)

    def test_packaged_plugin_compatibility_injection_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-plugin-boundary-") as temporary:
            root = Path(temporary)
            self.plugin_boundary_fixture(root)
            path = root / "plugins/heptatrader-agent-os/.mcp.json"
            document = json.loads(path.read_text(encoding="utf-8"))
            document["mcpServers"]["heptatrader"]["env"] = {
                "HEPTA_AGENT_SINGLE_DOMAIN_COMPAT": "1",
            }
            path.write_text(
                json.dumps(document, sort_keys=True) + "\n",
                encoding="utf-8")
            with self.assertRaisesRegex(
                    product_boundary.ProductBoundaryError,
                    "must not inject compatibility"):
                product_boundary.check_plugin_trust_domain_boundary(root)

    def test_launcher_fixed_uid_default_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-plugin-boundary-") as temporary:
            root = Path(temporary)
            self.plugin_boundary_fixture(root)
            path = root / "scripts/hepta_agent_mcp_launcher.py"
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "default_domain_config_path(os.getuid())",
                    "default_domain_config_path(AGENT_UID)"),
                encoding="utf-8")
            with self.assertRaisesRegex(
                    product_boundary.ProductBoundaryError,
                    "protected per-UID"):
                product_boundary.check_plugin_trust_domain_boundary(root)

    def test_launcher_unprotected_domain_config_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-plugin-boundary-") as temporary:
            root = Path(temporary)
            self.plugin_boundary_fixture(root)
            path = root / "scripts/hepta_agent_mcp_launcher.py"
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "Path(domain_config),\n"
                    "                expected_agent_identity=(",
                    "Path(domain_config),\n"
                    "                require_root_metadata=False,\n"
                    "                expected_agent_identity=("),
                encoding="utf-8")
            with self.assertRaisesRegex(
                    product_boundary.ProductBoundaryError,
                    "protected per-UID|metadata"):
                product_boundary.check_plugin_trust_domain_boundary(root)


if __name__ == "__main__":
    unittest.main(verbosity=2)
