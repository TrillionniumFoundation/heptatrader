#!/usr/bin/env python3

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]


def load_checker():
    path = REPOSITORY / "scripts/check_hepta_agent_os_provisioned_host.py"
    spec = importlib.util.spec_from_file_location(
        "hepta_agent_os_preflight_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECKER = load_checker()


READ_ONLY_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": False,
}


def tool_descriptor(name: str) -> dict[str, object]:
    schema: dict[str, object] = {
        "type": "object",
        "additionalProperties": False,
    }
    if name in {"market.get_quote", CHECKER.WATCH_SNAPSHOT_TOOL}:
        schema.update({
            "required": ["instrument"],
            "properties": {"instrument": {"type": "string"}},
        })
    return {
        "name": name,
        "inputSchema": schema,
        "annotations": dict(READ_ONLY_ANNOTATIONS),
    }


def native_tool_descriptor(name: str) -> dict[str, object]:
    return {
        "name": name,
        "description": "fixture WATCH read tool",
        "required_capability": "system.read",
        "effect": "read",
        "timeout_ms": 1000,
        "schema_hash": "sha256:" + "b" * 64,
        "input_schema": {},
        "result_schema": {},
    }


def watch_snapshot_payload(
        payloads: dict[str, dict[str, object]]) -> dict[str, object]:
    catalog_hash = "sha256:" + "a" * 64
    catalog = {
        "protocol": "hepta.agent-tools",
        "protocol_version": 1,
        "protocol_min_version": 1,
        "protocol_max_version": 1,
        "schema_version": 2,
        "catalog_schema_hash": catalog_hash,
        "tools": [
            native_tool_descriptor(name)
            for name in sorted(CHECKER.WATCH_TOOL_NAMES)
        ],
    }
    descriptors = {
        name: {
            "protocol": "hepta.agent-tools",
            "protocol_version": 1,
            "protocol_min_version": 1,
            "protocol_max_version": 1,
            "schema_version": 2,
            "catalog_schema_hash": catalog_hash,
            "tool": native_tool_descriptor(name),
        }
        for name in CHECKER.WATCH_SNAPSHOT_DESCRIPTOR_TOOLS
    }
    return {
        "schema": "hepta.watch-read-set.v1",
        "catalog": catalog,
        "descriptors": descriptors,
        "reads": {
            name: payloads[name]
            for name in CHECKER.WATCH_SNAPSHOT_READ_TOOLS
        },
        "read_finished_at_ms": {
            name: 1700000000000 + index
            for index, name in enumerate(CHECKER.WATCH_SNAPSHOT_READ_TOOLS)
        },
    }


def read_call_response(
        request_id: int, tool: str, payload: dict[str, object],
        *, rejected: bool = False) -> dict[str, object]:
    envelope: dict[str, object] = {
        "status": "error" if rejected else "ok",
        "tool": tool,
        "reason_code": "FIXTURE_READ_FAILED" if rejected else "",
        "detail": "fixture rejected the read" if rejected else "",
        "order_id": -1,
        "payload": payload,
    }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{
                "type": "text",
                "text": json.dumps(
                    envelope, sort_keys=True, separators=(",", ":")),
            }],
            "structuredContent": envelope,
            "isError": rejected,
        },
    }


def runtime_probe_stdout(
        *, health_overrides: dict[str, object] | None = None,
        tool_names: list[str] | None = None,
        failed_tool: str | None = None,
        snapshot_payload: dict[str, object] | None = None) -> str:
    health: dict[str, object] = {
        "gateway_ready": True,
        "remote_execution": True,
        "remote_execution_configured": True,
        "remote_execution_ready": True,
        "execution_mode": "SIMULATOR",
        "execution_service_epoch": "round34-runtime-probe-epoch",
        "execution_service_fencing_generation": 7,
        "remote_execution_reason": "",
        "read_model": "execution_authoritative_v1",
        "paper_template_enabled": False,
    }
    if health_overrides:
        health.update(health_overrides)
    payloads: dict[str, dict[str, object]] = {
        "system.get_health": health,
        "market.get_quote": {
            "instrument": "EUR.USD",
            "source": "SIMULATOR",
            "bid": 1.1,
            "ask": 1.2,
        },
        "account.get_summary": {"authoritative": True, "account": "SIM"},
        "portfolio.list_positions": {
            "authoritative": True,
            "positions": [],
        },
        "orders.list": {"authoritative": True, "orders": []},
        "risk.get_limits": {"authoritative": True, "max_order_quantity": 1000},
    }
    payloads[CHECKER.WATCH_SNAPSHOT_TOOL] = (
        watch_snapshot_payload(payloads)
        if snapshot_payload is None else snapshot_payload
    )
    responses: list[dict[str, object]] = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2025-03-26",
                "serverInfo": {"name": "heptatrader", "version": "1"},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "tools": [
                    tool_descriptor(name)
                    for name in (
                        sorted(CHECKER.WATCH_TOOL_NAMES)
                        if tool_names is None else tool_names)
                ],
            },
        },
    ]
    responses.extend(
        read_call_response(
            request_id, tool_name, payloads[tool_name],
            rejected=tool_name == failed_tool)
        for request_id, (tool_name, _arguments) in enumerate(
            CHECKER.RUNTIME_READ_PROBES, 3))
    return "".join(
        json.dumps(response, sort_keys=True, separators=(",", ":")) + "\n"
        for response in responses)


def rejected_runtime_stdout(reason: str) -> str:
    responses = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2025-03-26",
                "serverInfo": {"name": "heptatrader", "version": "1"},
            },
        },
    ]
    responses.extend({
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32603, "message": reason},
        }
        for request_id in range(2, 3 + len(CHECKER.RUNTIME_READ_PROBES)))
    return "".join(
        json.dumps(response, sort_keys=True, separators=(",", ":")) + "\n"
        for response in responses)


def write(root: Path, relative: str, contents: bytes, mode: int) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)
    path.chmod(mode)
    return path


def copy(root: Path, relative: str, source: Path, mode: int) -> None:
    write(root, relative, source.read_bytes(), mode)


@contextmanager
def provisioned_fixture(*, installation_only: bool = False):
    with tempfile.TemporaryDirectory(
            prefix="hepta-agent-os-preflight-") as directory:
        root = Path(directory)
        root.chmod(0o755)
        passwd = (
            "root:x:0:0:root:/root:/bin/sh\n"
            "hepta-gateway:x:2001:2001:gateway:/nonexistent:/usr/sbin/nologin\n"
            "hepta-exec:x:2002:2002:exec:/nonexistent:/usr/sbin/nologin\n"
            "hepta-ib-exec:x:2003:2003:ib-exec:/nonexistent:/usr/sbin/nologin\n"
            "hepta-agent:x:2004:2004:agent:/nonexistent:/usr/sbin/nologin\n")
        groups = (
            "root:x:0:\n"
            "hepta-gateway:x:2001:\n"
            "hepta-exec:x:2002:\n"
            "hepta-ib-exec:x:2003:\n"
            "hepta-agent:x:2004:\n")
        write(root, "etc/passwd", passwd.encode(), 0o644)
        write(root, "etc/group", groups.encode(), 0o644)
        write(
            root, "usr/libexec/hepta-tool-gatewayd",
            b"\x7fELF" + b"\0" * 124, 0o755)
        write(
            root, "usr/bin/hepta-sessionctl",
            b"\x7fELF" + b"\0" * 124, 0o755)
        write(
            root, "usr/bin/heptactl",
            b"\x7fELF" + b"\0" * 124, 0o755)
        copy(root, "usr/libexec/hepta-mcp-server",
             REPOSITORY / "adapters/mcp/hepta_mcp_server.py", 0o755)
        copy(root, "usr/libexec/hepta-agent-mcp-launcher",
             REPOSITORY / "scripts/hepta_agent_mcp_launcher.py", 0o755)
        copy(root, "usr/libexec/hepta-agent-session-bootstrap",
             REPOSITORY / "scripts/hepta_agent_session_bootstrap.py", 0o755)
        copy(root, "usr/libexec/hepta_agent_trust_domain.py",
             REPOSITORY / "scripts/hepta_agent_trust_domain.py", 0o755)
        copy(root, "usr/libexec/hepta-paper-receipt-contracts",
             REPOSITORY / "scripts/hepta_paper_receipt_contracts.py", 0o755)
        copy(root, "usr/libexec/hepta-shadow-watch-collector",
             REPOSITORY / "scripts/hepta_shadow_watch_collector.py", 0o755)
        copy(root, "usr/libexec/hepta-shadow-watch-exporter",
             REPOSITORY / "scripts/hepta_shadow_watch_exporter.py", 0o755)
        copy(root, "usr/libexec/hepta-shadow-watch-custodian",
             REPOSITORY / "scripts/hepta_shadow_watch_custodian.py", 0o755)
        copy(root, "usr/libexec/hepta-broker-egress-policy",
             REPOSITORY / "scripts/hepta_broker_egress_policy.py", 0o755)
        copy(root, "usr/libexec/hepta-local-paper-control",
             REPOSITORY / "scripts/hepta_local_paper_control.py", 0o755)
        copy(root, "usr/libexec/hepta-shadow-host-installer",
             REPOSITORY / "scripts/hepta_shadow_host_installer.py", 0o755)
        copy(root, "usr/libexec/hepta-p1-watch-profile-deployer",
             REPOSITORY / "scripts/hepta_p1_watch_profile_deployer.py", 0o755)
        copy(root, "usr/libexec/hepta-p1-watch-activation-transaction",
             REPOSITORY /
             "scripts/hepta_p1_watch_activation_transaction.py", 0o755)
        copy(root, "usr/libexec/hepta-p1-shadow-host-controller",
             REPOSITORY / "scripts/hepta_p1_shadow_host_controller.py", 0o755)
        copy(root, "usr/libexec/hepta-p1-load-probe-validator",
             REPOSITORY / "scripts/hepta_p1_load_probe_validator.py", 0o755)
        copy(root, "usr/libexec/build-hepta-p1-observation-policy",
             REPOSITORY / "scripts/build_hepta_p1_observation_policy.py", 0o755)
        copy(root, "usr/libexec/hepta-p1-shadow-observer-controller",
             REPOSITORY / "scripts/hepta_p1_shadow_observer_controller.py", 0o755)
        copy(root, "usr/libexec/hepta-p1-shadow-admission-launcher",
             REPOSITORY / "scripts/hepta_p1_shadow_admission_launcher.py", 0o755)
        copy(root, "usr/libexec/hepta-bounded-shadow-closure-verifier",
             REPOSITORY / "scripts/hepta_bounded_shadow_closure_verifier.py",
             0o755)
        copy(root, "usr/libexec/hepta-official-source-capture",
             REPOSITORY / "scripts/hepta_official_source_capture.py", 0o755)
        for script in (
                "hepta_bounded_shadow_observer.py",
                "hepta_market_context_builder.py",
                "hepta_market_evidence_normalizer.py",
                "hepta_market_official_source_extractor.py",
                "hepta_eurusd_confirmed_momentum_strategy.py",
                "hepta_shadow_market_history.py",
                "hepta_strategy_shadow_runner.py",
                "validate_hepta_strategy_decision_receipt.py"):
            copy(root, "usr/libexec/" + script,
                 REPOSITORY / "scripts" / script, 0o755)
        copy(root, "usr/libexec/hepta_strategy_contracts.py",
             REPOSITORY / "scripts/hepta_strategy_contracts.py", 0o644)
        copy(
            root,
            "usr/share/heptatrader/strategies/"
            "eurusd-confirmed-momentum-shadow-v2.json",
            REPOSITORY /
            "strategies/eurusd-confirmed-momentum-shadow-v2.json",
            0o644)
        copy(root, "usr/libexec/check-hepta-agent-os-provisioned-host",
             REPOSITORY / "scripts/check_hepta_agent_os_provisioned_host.py",
             0o755)
        for name in (
                "hepta-tool-gateway.service",
                "hepta-tool-gateway.socket",
                "hepta-tool-session-supervisor.socket",
                "hepta-broker-egress-policy.service",
                "hepta-p1-watch-activation.service",
                "hepta-p1-watch-activation-reconcile.service",
                "hepta-p1-watch-activation-reconcile.timer",
                "hepta-tool-gateway@.service",
                "hepta-tool-gateway@.socket",
                "hepta-tool-session-supervisor@.socket",
                "hepta-shadow-watch-collector@.service",
                "hepta-shadow-watch-collector@.timer",
                "hepta-shadow-watch-export@.service",
                "hepta-shadow-watch-custodian@.service",
                "hepta-shadow-watch-custodian-reconcile@.service",
                "hepta-shadow-watch-custodian-reconcile@.timer"):
            copy(root, "usr/lib/systemd/system/" + name,
                 REPOSITORY / "systemd" / name, 0o644)
        copy(root, "usr/lib/tmpfiles.d/heptatrader-agent-os.conf",
             REPOSITORY / "tmpfiles.d/heptatrader-agent-os.conf", 0o644)
        copy(root, "usr/share/heptatrader/hepta-service-identities-v1.json",
             REPOSITORY / "systemd/hepta-service-identities-v1.json", 0o644)
        copy(root,
             "usr/share/heptatrader/hepta-broker-network-policy-v1.json",
             REPOSITORY / "systemd/hepta-broker-network-policy-v1.json",
             0o644)
        copy(root,
             "etc/heptatrader/"
             "hepta-agent-trust-domain-paper-identities-v1.json",
             REPOSITORY / "systemd/"
             "hepta-agent-trust-domain-paper-identities-v1.json.example",
             0o600)
        copy(root, "usr/share/heptatrader/.agents/plugins/marketplace.json",
             REPOSITORY / ".agents/plugins/marketplace.json", 0o644)
        plugin_root = "usr/share/heptatrader/plugins/heptatrader-agent-os/"
        copy(root, plugin_root + ".mcp.json",
             REPOSITORY / "plugins/heptatrader-agent-os/.mcp.json", 0o644)
        copy(root, plugin_root + ".codex-plugin/plugin.json",
             REPOSITORY / "plugins/heptatrader-agent-os/.codex-plugin/plugin.json",
             0o644)
        copy(root, plugin_root + "README.md",
             REPOSITORY / "plugins/heptatrader-agent-os/README.md", 0o644)
        copy(
            root,
            "usr/share/doc/heptatrader/examples/"
            "hepta-agent-host-identity.conf.example",
            REPOSITORY / "systemd/hepta-agent-host-identity.conf.example",
            0o644)
        for name in (
                "hepta-tool-gateway-domain.env.example",
                "hepta-shadow-watch-domain.env.example"):
            copy(
                root, "usr/share/doc/heptatrader/examples/" + name,
                REPOSITORY / "systemd" / name, 0o644)
        copy(root, "etc/heptatrader/hepta-tool-gateway.env",
             REPOSITORY / "systemd/hepta-tool-gateway.env.example", 0o644)
        write(
            root, "etc/heptatrader/hepta-supervisor-lease.key",
            (CHECKER.UNPROVISIONED_SUPERVISOR_LEASE if installation_only else
             b"offline-supervisor-lease-key-0001\n"),
            0o400)

        ownership = {
            "run/hepta-agent/tools.sock": (2004, 2004),
            "run/hepta-agent/session.token": (2004, 2004),
            "run/hepta-tool-gateway": (2001, 2001),
            "run/hepta-tool-gateway/session-supervisor.sock": (2001, 2001),
        }
        tool = None
        supervisor = None
        if not installation_only:
            agent_dir = root / "run/hepta-agent"
            gateway_dir = root / "run/hepta-tool-gateway"
            agent_dir.mkdir(parents=True)
            gateway_dir.mkdir(parents=True)
            agent_dir.chmod(0o711)
            gateway_dir.chmod(0o700)
            write(root, "run/hepta-agent/session.token",
                  b"offline-agent-session-token-0001\n", 0o600)
            write(root,
                  "run/hepta-agent/session-lease-terminal-cleanup.lock",
                  b"", 0o644)
            tool = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            supervisor = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            tool.bind(str(agent_dir / "tools.sock"))
            supervisor.bind(str(gateway_dir / "session-supervisor.sock"))
            (agent_dir / "tools.sock").chmod(0o600)
            (gateway_dir / "session-supervisor.sock").chmod(0o600)

        def provider(relative: str, _metadata: os.stat_result) -> tuple[int, int]:
            return ownership.get(relative, (0, 0))

        try:
            yield root, ownership, provider, (tool, supervisor)
        finally:
            if tool is not None:
                tool.close()
            if supervisor is not None:
                supervisor.close()


class ProvisionedHostTests(unittest.TestCase):
    def test_valid_fixture_requires_and_executes_runtime_probe(self) -> None:
        with provisioned_fixture() as (root, _owners, provider, _sockets):
            probed: list[Path] = []
            CHECKER.validate(
                root, provider, runtime_prober=lambda value: probed.append(value))
            self.assertEqual(probed, [root])

    def test_valid_installation_only_fixture_has_no_runtime_artifacts(self) -> None:
        with provisioned_fixture(
                installation_only=True) as (root, _owners, provider, _sockets):
            CHECKER.validate(
                root, provider, installation_only=True,
                runtime_prober=lambda _root: self.fail(
                    "installation-only validation executed runtime probe"))

    def test_runtime_probe_accepts_ready_uid_bound_mcp_chain(self) -> None:
        health = CHECKER._runtime_probe_responses(
            0, runtime_probe_stdout(), "")
        self.assertTrue(health["gateway_ready"])
        self.assertTrue(health["remote_execution_ready"])

    def test_runtime_probe_rejects_duplicate_key_with_good_value_last(
            self) -> None:
        lines = runtime_probe_stdout().splitlines()
        lines[0] = '{"id":999,' + lines[0][1:]
        with self.assertRaisesRegex(
                CHECKER.ContractFailure, "duplicate JSON key: id"):
            CHECKER._runtime_probe_responses(
                0, "\n".join(lines) + "\n", "")

    def test_runtime_probe_requests_exact_read_sequence(self) -> None:
        requests = [
            json.loads(line)
            for line in CHECKER._mcp_probe_request().splitlines()
        ]
        self.assertEqual(len(requests), 2 + len(CHECKER.RUNTIME_READ_PROBES))
        self.assertEqual(requests[0]["method"], "initialize")
        self.assertEqual(requests[1]["method"], "tools/list")
        self.assertEqual(
            [
                (
                    request["params"]["name"],
                    request["params"]["arguments"],
                )
                for request in requests[2:]
            ],
            list(CHECKER.RUNTIME_READ_PROBES),
        )
        self.assertEqual(
            CHECKER.RUNTIME_READ_PROBES[-1],
            (CHECKER.WATCH_SNAPSHOT_TOOL, {"instrument": "EUR.USD"}),
        )

    def test_runtime_probe_rejects_malformed_composite_snapshot(self) -> None:
        response = json.loads(runtime_probe_stdout().splitlines()[-1])
        baseline = response["result"]["structuredContent"]["payload"]
        cases: dict[str, dict[str, object]] = {}

        wrong_schema = deepcopy(baseline)
        wrong_schema["schema"] = "hepta.watch-read-set.v0"
        cases["schema"] = wrong_schema

        catalog_drift = deepcopy(baseline)
        catalog_drift["catalog"]["tools"] = [
            item for item in catalog_drift["catalog"]["tools"]
            if item["name"] != CHECKER.WATCH_SNAPSHOT_TOOL
        ]
        cases["catalog"] = catalog_drift

        descriptor_drift = deepcopy(baseline)
        descriptor_drift["descriptors"]["orders.list"]["tool"]["name"] = (
            "orders.lists")
        cases["descriptor"] = descriptor_drift

        reads_drift = deepcopy(baseline)
        reads_drift["reads"].pop("risk.get_limits")
        cases["reads"] = reads_drift

        timestamp_drift = deepcopy(baseline)
        timestamp_drift["read_finished_at_ms"]["orders.list"] = True
        cases["timestamp"] = timestamp_drift

        for name, payload in cases.items():
            with self.subTest(name=name), self.assertRaisesRegex(
                    CHECKER.ContractFailure,
                    "AGENT_RUNTIME_WATCH_SNAPSHOT_INVALID"):
                CHECKER._runtime_probe_responses(
                    0, runtime_probe_stdout(snapshot_payload=payload), "")

    def test_runtime_probe_rejects_composite_identity_drift(self) -> None:
        response = json.loads(runtime_probe_stdout().splitlines()[-1])
        payload = response["result"]["structuredContent"]["payload"]
        payload["reads"]["system.get_health"][
            "execution_service_epoch"] = "other-epoch"
        with self.assertRaisesRegex(
                CHECKER.ContractFailure,
                "AGENT_RUNTIME_WATCH_SNAPSHOT_IDENTITY_MISMATCH"):
            CHECKER._runtime_probe_responses(
                0, runtime_probe_stdout(snapshot_payload=payload), "")

    def test_runtime_probe_rejects_mutation_surface(self) -> None:
        for exposed in ("trade.place_order", "risk.preview_order"):
            with self.subTest(exposed=exposed):
                tool_names = sorted(CHECKER.WATCH_TOOL_NAMES) + [exposed]
                with self.assertRaisesRegex(
                        CHECKER.ContractFailure,
                        "AGENT_RUNTIME_WATCH_TOOL_SURFACE_INVALID"):
                    CHECKER._runtime_probe_responses(
                        0, runtime_probe_stdout(tool_names=tool_names), "")

    def test_runtime_probe_rejects_watch_surface_drift(self) -> None:
        cases = {
            "missing": sorted(
                CHECKER.WATCH_TOOL_NAMES - {"events.wait"}),
            "unexpected": (
                sorted(CHECKER.WATCH_TOOL_NAMES) + ["system.unreviewed"]),
        }
        for name, tool_names in cases.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(
                        CHECKER.ContractFailure,
                        "AGENT_RUNTIME_WATCH_TOOL_SURFACE_INVALID"):
                    CHECKER._runtime_probe_responses(
                        0, runtime_probe_stdout(tool_names=tool_names), "")

    def test_runtime_probe_rejects_each_failed_read(self) -> None:
        for tool_name, _arguments in CHECKER.RUNTIME_READ_PROBES:
            with self.subTest(tool=tool_name):
                with self.assertRaisesRegex(
                        CHECKER.ContractFailure,
                        "AGENT_RUNTIME_READ_CALL_REJECTED"):
                    CHECKER._runtime_probe_responses(
                        0, runtime_probe_stdout(failed_tool=tool_name), "")

    def test_runtime_probe_rejects_dead_unlistened_tool_socket(self) -> None:
        with self.assertRaisesRegex(
                CHECKER.ContractFailure, "MCP_REQUEST_FAILED"):
            CHECKER._runtime_probe_responses(
                0, rejected_runtime_stdout("Connection refused"), "")

    def test_runtime_probe_rejects_fake_token(self) -> None:
        with self.assertRaisesRegex(
                CHECKER.ContractFailure, "MCP_REQUEST_FAILED"):
            CHECKER._runtime_probe_responses(
                0, rejected_runtime_stdout("SESSION_TOKEN_MISMATCH"), "")

    def test_runtime_probe_rejects_unregistered_session(self) -> None:
        with self.assertRaisesRegex(
                CHECKER.ContractFailure, "MCP_REQUEST_FAILED"):
            CHECKER._runtime_probe_responses(
                0, rejected_runtime_stdout("SESSION_NOT_FOUND"), "")

    def test_runtime_probe_rejects_execution_down(self) -> None:
        with self.assertRaisesRegex(
                CHECKER.ContractFailure,
                "AGENT_RUNTIME_REMOTE_EXECUTION_NOT_READY"):
            CHECKER._runtime_probe_responses(
                0, runtime_probe_stdout(health_overrides={
                    "remote_execution_ready": False,
                    "execution_service_epoch": "",
                    "execution_service_fencing_generation": 0,
                    "remote_execution_reason": "CONNECT_FAILED",
                }), "")

    def test_installation_only_rejects_fabricated_runtime_token(self) -> None:
        with provisioned_fixture(
                installation_only=True) as (root, owners, provider, _sockets):
            write(root, "run/hepta-agent/session.token",
                  b"fabricated-runtime-token-must-not-pass\n", 0o600)
            owners["run/hepta-agent/session.token"] = (2004, 2004)
            with self.assertRaisesRegex(
                    CHECKER.ContractFailure, "must be absent"):
                CHECKER.validate(root, provider, installation_only=True)

    def test_runtime_rejects_unprovisioned_placeholder(self) -> None:
        with provisioned_fixture() as (root, _owners, provider, _sockets):
            (root / "etc/heptatrader/hepta-supervisor-lease.key").unlink()
            write(
                root, "etc/heptatrader/hepta-supervisor-lease.key",
                CHECKER.UNPROVISIONED_SUPERVISOR_LEASE, 0o400)
            with self.assertRaisesRegex(
                    CHECKER.ContractFailure, "placeholder"):
                CHECKER.validate(root, provider)

    def test_agent_parent_must_be_root_owned_0711(self) -> None:
        with provisioned_fixture() as (root, _owners, provider, _sockets):
            (root / "run/hepta-agent").chmod(0o700)
            with self.assertRaisesRegex(
                    CHECKER.ContractFailure, "directory metadata mismatch"):
                CHECKER.validate(root, provider)

    def test_cleanup_interlock_metadata_and_content_fail_closed(self) -> None:
        relative = "run/hepta-agent/session-lease-terminal-cleanup.lock"
        for mutation in ("missing", "mode", "content"):
            with self.subTest(mutation=mutation), provisioned_fixture() as (
                    root, _owners, provider, _sockets):
                path = root / relative
                if mutation == "missing":
                    path.unlink()
                elif mutation == "mode":
                    path.chmod(0o666)
                else:
                    path.write_bytes(b"not-empty")
                    path.chmod(0o644)
                with self.assertRaises(CHECKER.ContractFailure):
                    CHECKER.validate(root, provider)

    def test_session_token_symlink_fails_closed(self) -> None:
        with provisioned_fixture() as (root, owners, provider, _sockets):
            token = root / "run/hepta-agent/session.token"
            target = root / "run/hepta-agent/target"
            token.rename(target)
            token.symlink_to("target")
            owners["run/hepta-agent/target"] = (2004, 2004)
            with self.assertRaisesRegex(
                    CHECKER.ContractFailure, "symlink is forbidden"):
                CHECKER.validate(root, provider)

    def test_plugin_command_drift_fails_closed(self) -> None:
        with provisioned_fixture() as (root, _owners, provider, _sockets):
            path = (
                root / "usr/share/heptatrader/plugins/heptatrader-agent-os/"
                ".mcp.json")
            document = json.loads(path.read_text())
            document["mcpServers"]["heptatrader"]["command"] = "/bin/sh"
            path.write_text(json.dumps(document))
            path.chmod(0o644)
            with self.assertRaisesRegex(
                CHECKER.ContractFailure, "MCP config mismatch"):
                CHECKER.validate(root, provider)

    def test_plugin_duplicate_key_with_good_value_last_fails_closed(
            self) -> None:
        with provisioned_fixture() as (root, _owners, provider, _sockets):
            path = (
                root / "usr/share/heptatrader/plugins/heptatrader-agent-os/"
                ".mcp.json")
            document = json.loads(path.read_text())
            duplicate = (
                '{"mcpServers":{"attacker":{"command":"/bin/sh"}},'
                '"mcpServers":' +
                json.dumps(
                    document["mcpServers"],
                    sort_keys=True, separators=(",", ":")) +
                "}")
            path.write_text(duplicate, encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaisesRegex(
                    CHECKER.ContractFailure,
                    "duplicate JSON key: mcpServers"):
                CHECKER.validate(root, provider)

    def test_plugin_old_direct_map_fails_closed(self) -> None:
        with provisioned_fixture() as (root, _owners, provider, _sockets):
            path = (
                root / "usr/share/heptatrader/plugins/heptatrader-agent-os/"
                ".mcp.json")
            document = json.loads(path.read_text())
            path.write_text(
                json.dumps(
                    document["mcpServers"],
                    sort_keys=True, separators=(",", ":")),
                encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaisesRegex(
                    CHECKER.ContractFailure, "MCP config mismatch"):
                CHECKER.validate(root, provider)

    def test_paper_capable_environment_cannot_claim_watch_preflight(self) -> None:
        with provisioned_fixture() as (root, _owners, provider, _sockets):
            path = root / "etc/heptatrader/hepta-tool-gateway.env"
            text = path.read_text(encoding="utf-8")
            text = text.replace(
                "HEPTA_EXECUTION_REMOTE_MODE=SIMULATOR",
                "HEPTA_EXECUTION_REMOTE_MODE=PAPER")
            text = text.replace(
                "HEPTA_TOOL_ALLOW_TRADE=0",
                "HEPTA_TOOL_ALLOW_TRADE=1")
            text = text.replace(
                "HEPTA_TOOL_SESSION_TEMPLATES=watch",
                "HEPTA_TOOL_SESSION_TEMPLATES=paper")
            path.write_text(text, encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaisesRegex(
                    CHECKER.ContractFailure,
                    "reviewed WATCH profile digest mismatch"):
                CHECKER.validate(root, provider)

    def test_conflicting_systemd_directive_fails_closed(self) -> None:
        with provisioned_fixture() as (root, _owners, provider, _sockets):
            path = (
                root / "usr/lib/systemd/system/"
                "hepta-tool-gateway.service")
            path.write_text(
                path.read_text(encoding="utf-8") +
                "\n[Service]\nUser=root\nPrivateNetwork=no\n",
                encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaisesRegex(
                    CHECKER.ContractFailure,
                    "reviewed WATCH profile digest mismatch"):
                CHECKER.validate(root, provider)

    def test_setuid_launcher_fails_closed(self) -> None:
        with provisioned_fixture() as (root, _owners, provider, _sockets):
            (root / "usr/libexec/hepta-agent-mcp-launcher").chmod(0o4755)
            with self.assertRaisesRegex(
                    CHECKER.ContractFailure, "metadata mismatch"):
                CHECKER.validate(root, provider)

    def test_missing_control_plane_artifacts_fail_closed(self) -> None:
        required = (
            "usr/bin/heptactl",
            "usr/libexec/hepta-agent-session-bootstrap",
            "usr/libexec/hepta_agent_trust_domain.py",
            "usr/libexec/hepta-paper-receipt-contracts",
            "usr/libexec/hepta-shadow-watch-collector",
            "usr/libexec/hepta-shadow-watch-exporter",
            "usr/libexec/hepta-shadow-watch-custodian",
            "usr/libexec/hepta-broker-egress-policy",
            "usr/libexec/hepta-local-paper-control",
            "usr/libexec/hepta-shadow-host-installer",
            "usr/libexec/hepta-p1-watch-profile-deployer",
            "usr/libexec/hepta-p1-watch-activation-transaction",
            "usr/libexec/hepta-p1-shadow-host-controller",
            "usr/libexec/hepta-p1-load-probe-validator",
            "usr/libexec/build-hepta-p1-observation-policy",
            "usr/libexec/hepta-p1-shadow-observer-controller",
            "usr/libexec/hepta-p1-shadow-admission-launcher",
            "usr/libexec/hepta-bounded-shadow-closure-verifier",
            "usr/libexec/hepta-official-source-capture",
            "usr/libexec/hepta_bounded_shadow_observer.py",
            "usr/libexec/hepta_market_context_builder.py",
            "usr/libexec/hepta_market_evidence_normalizer.py",
            "usr/libexec/hepta_market_official_source_extractor.py",
            "usr/libexec/hepta_eurusd_confirmed_momentum_strategy.py",
            "usr/libexec/hepta_shadow_market_history.py",
            "usr/libexec/hepta_strategy_shadow_runner.py",
            "usr/libexec/hepta_strategy_contracts.py",
            "usr/libexec/validate_hepta_strategy_decision_receipt.py",
            "usr/share/heptatrader/strategies/"
            "eurusd-confirmed-momentum-shadow-v2.json",
            "usr/lib/systemd/system/hepta-tool-gateway@.service",
            "usr/lib/systemd/system/hepta-tool-gateway@.socket",
            "usr/lib/systemd/system/"
            "hepta-tool-session-supervisor@.socket",
            "usr/lib/systemd/system/hepta-broker-egress-policy.service",
            "usr/lib/systemd/system/hepta-p1-watch-activation.service",
            "usr/lib/systemd/system/"
            "hepta-p1-watch-activation-reconcile.service",
            "usr/lib/systemd/system/"
            "hepta-p1-watch-activation-reconcile.timer",
            "usr/lib/systemd/system/"
            "hepta-shadow-watch-collector@.service",
            "usr/lib/systemd/system/"
            "hepta-shadow-watch-collector@.timer",
            "usr/lib/systemd/system/"
            "hepta-shadow-watch-export@.service",
            "usr/lib/systemd/system/"
            "hepta-shadow-watch-custodian@.service",
            "usr/lib/systemd/system/"
            "hepta-shadow-watch-custodian-reconcile@.service",
            "usr/lib/systemd/system/"
            "hepta-shadow-watch-custodian-reconcile@.timer",
            "usr/share/doc/heptatrader/examples/"
            "hepta-tool-gateway-domain.env.example",
            "usr/share/doc/heptatrader/examples/"
            "hepta-shadow-watch-domain.env.example",
            "usr/share/heptatrader/hepta-broker-network-policy-v1.json",
            "etc/heptatrader/"
            "hepta-agent-trust-domain-paper-identities-v1.json",
        )
        for relative in required:
            with self.subTest(relative=relative), provisioned_fixture() as (
                    root, _owners, provider, _sockets):
                (root / relative).unlink()
                with self.assertRaisesRegex(
                        CHECKER.ContractFailure, "required path is absent"):
                    CHECKER.validate(root, provider)

    def test_shadow_closure_same_mode_content_tampering_fails_closed(
            self) -> None:
        expected_exact = {
            "usr/libexec/hepta-agent-session-bootstrap",
            "usr/libexec/hepta_agent_trust_domain.py",
            "usr/libexec/hepta-paper-receipt-contracts",
            "usr/libexec/hepta-shadow-watch-collector",
            "usr/libexec/hepta-shadow-watch-exporter",
            "usr/libexec/hepta-shadow-watch-custodian",
            "usr/libexec/hepta-broker-egress-policy",
            "usr/libexec/hepta-local-paper-control",
            "usr/libexec/hepta-shadow-host-installer",
            "usr/libexec/hepta-p1-watch-profile-deployer",
            "usr/libexec/hepta-p1-watch-activation-transaction",
            "usr/libexec/hepta-p1-shadow-host-controller",
            "usr/libexec/hepta-p1-load-probe-validator",
            "usr/libexec/build-hepta-p1-observation-policy",
            "usr/libexec/hepta-p1-shadow-observer-controller",
            "usr/libexec/hepta-p1-shadow-admission-launcher",
            "usr/libexec/hepta-bounded-shadow-closure-verifier",
            "usr/libexec/hepta-official-source-capture",
            "usr/libexec/hepta_bounded_shadow_observer.py",
            "usr/libexec/hepta_market_context_builder.py",
            "usr/libexec/hepta_market_evidence_normalizer.py",
            "usr/libexec/hepta_market_official_source_extractor.py",
            "usr/libexec/hepta_eurusd_confirmed_momentum_strategy.py",
            "usr/libexec/hepta_shadow_market_history.py",
            "usr/libexec/hepta_strategy_shadow_runner.py",
            "usr/libexec/hepta_strategy_contracts.py",
            "usr/libexec/validate_hepta_strategy_decision_receipt.py",
            "usr/share/heptatrader/strategies/"
            "eurusd-confirmed-momentum-shadow-v2.json",
            "usr/lib/systemd/system/hepta-tool-gateway@.service",
            "usr/lib/systemd/system/hepta-tool-gateway@.socket",
            "usr/lib/systemd/system/"
            "hepta-tool-session-supervisor@.socket",
            "usr/lib/systemd/system/hepta-broker-egress-policy.service",
            "usr/lib/systemd/system/hepta-p1-watch-activation.service",
            "usr/lib/systemd/system/"
            "hepta-p1-watch-activation-reconcile.service",
            "usr/lib/systemd/system/"
            "hepta-p1-watch-activation-reconcile.timer",
            "usr/lib/systemd/system/"
            "hepta-shadow-watch-collector@.service",
            "usr/lib/systemd/system/"
            "hepta-shadow-watch-collector@.timer",
            "usr/lib/systemd/system/"
            "hepta-shadow-watch-export@.service",
            "usr/lib/systemd/system/"
            "hepta-shadow-watch-custodian@.service",
            "usr/lib/systemd/system/"
            "hepta-shadow-watch-custodian-reconcile@.service",
            "usr/lib/systemd/system/"
            "hepta-shadow-watch-custodian-reconcile@.timer",
            "usr/share/doc/heptatrader/examples/"
            "hepta-tool-gateway-domain.env.example",
            "usr/share/doc/heptatrader/examples/"
            "hepta-shadow-watch-domain.env.example",
            "usr/share/heptatrader/hepta-broker-network-policy-v1.json",
            "etc/heptatrader/"
            "hepta-agent-trust-domain-paper-identities-v1.json",
        }
        self.assertEqual(
            set(CHECKER.REVIEWED_SHADOW_CLOSURE_SHA256), expected_exact)
        for relative in sorted(expected_exact):
            with self.subTest(relative=relative), provisioned_fixture() as (
                    root, _owners, provider, _sockets):
                path = root / relative
                mode = path.stat().st_mode & 0o7777
                path.write_bytes(path.read_bytes() + b"\n# same-mode tamper\n")
                path.chmod(mode)
                with self.assertRaisesRegex(
                        CHECKER.ContractFailure,
                        "reviewed SHADOW closure digest mismatch"):
                    CHECKER.validate(root, provider)

    def test_gateway_cannot_own_agent_token(self) -> None:
        with provisioned_fixture() as (root, owners, provider, _sockets):
            owners["run/hepta-agent/session.token"] = (2001, 2001)
            with self.assertRaisesRegex(
                    CHECKER.ContractFailure, "metadata mismatch"):
                CHECKER.validate(root, provider)

    def test_all_four_service_identities_are_required_and_unique(self) -> None:
        with provisioned_fixture() as (root, _owners, provider, _sockets):
            passwd = root / "etc/passwd"
            passwd.write_text(
                passwd.read_text(encoding="utf-8").replace(
                    "hepta-ib-exec:x:2003:2003:",
                    "hepta-ib-exec:x:2002:2003:"),
                encoding="utf-8")
            with self.assertRaisesRegex(
                    CHECKER.ContractFailure,
                    "mismatch|collides"):
                CHECKER.validate(root, provider)


if __name__ == "__main__":
    unittest.main(verbosity=2)
