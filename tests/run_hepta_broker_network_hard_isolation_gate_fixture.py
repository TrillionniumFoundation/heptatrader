#!/usr/bin/env python3

"""Rootless fake-command contracts for the broker netns hard gate."""

from __future__ import annotations

import copy
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve(strict=True).parents[1]
MODULE_PATH = (
    ROOT / "scripts/run_hepta_broker_network_hard_isolation_gate.py")
SPEC = importlib.util.spec_from_file_location(
    "run_hepta_broker_network_hard_isolation_gate_under_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot import hard-isolation gate")
RUNNER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUNNER
SPEC.loader.exec_module(RUNNER)


RUN_ID = "a1b2c3d4e5f60123456789abcdef0000"
COMMIT = "c" * 40
BOOT_ID = "01234567-89ab-cdef-8123-456789abcdef"
HOST_ID = "disposable-native-vm-fixture"
FIREWALL_JSON = '{"nftables":[]}\n'
FIREWALL_SHA = RUNNER.nft_semantic_sha256(FIREWALL_JSON)


def evidence() -> RUNNER.EvidenceBundle:
    records = {
        kind: {
            "path": f"/fixture/{kind}.json",
            "file_sha256": (str(index) * 64)[:64],
            "body_sha256": "sha256:" + (str(index + 4) * 64)[:64],
            "issued_at_ms": 1_700_000_000_000,
            "expires_at_ms": 2_000_000_000_000,
            "size": 100 + index,
            "device": 1,
            "inode": 1000 + index,
            "mode": "0600",
            "nlink": 1,
            "uid": 0,
            "gid": 0,
            "mtime_ns": 100_000 + index,
            "ctime_ns": 200_000 + index,
            "parent_identity": {
                "st_dev": 1,
                "st_ino": 10,
                "st_uid": 0,
                "st_gid": 0,
                "st_mode": stat.S_IFDIR | 0o700,
                "st_nlink": 2,
            },
        }
        for index, kind in enumerate(("host", "source", "base", "tooling"), 1)
    }
    return RUNNER.EvidenceBundle(
        documents={kind: {} for kind in records},
        records=records,
        source_commit=COMMIT,
        source_manifest_sha256="f" * 64,
        runner_sha256="e" * 64,
        host_id=HOST_ID,
        boot_id=BOOT_ID,
        virtualization="kvm",
        listener_allowlist=(),
        netns_allowlist=(),
        firewall_semantic_sha256=FIREWALL_SHA,
        firewall_reload_unit="nftables.service",
        expires_at_ms=2_000_000_000_000,
        revalidate_paths=False,
        provenance_owner_uid=os.geteuid(),
    )


class FakeExecutor:
    """Command-level fake.  It never invokes subprocess or a host tool."""

    def __init__(
            self, topology: RUNNER.Topology, *,
            mutate_probe: str | None = None,
            forwarder: bool = False,
            firewall_drift: bool = False,
            cleanup_residue: bool = False,
            fail_action: str | None = None) -> None:
        self.topology = topology
        self.mutate_probe = mutate_probe
        self.forwarder = forwarder
        self.firewall_drift = firewall_drift
        self.cleanup_residue = cleanup_residue
        self.fail_action = fail_action
        self.actions: list[str] = []
        self.probe_counter = 0
        self.anchor_epoch = 0

    def sentinel_sockets(self) -> str:
        lines = []
        for role in RUNNER.CLIENT_ROLES:
            for address in (
                    self.topology.ipv4[role][0],
                    self.topology.ipv6[role][0]):
                for port in RUNNER.PROTECTED_PORTS:
                    lines.append(
                        f"LISTEN 0 16 {address}:{port} 0.0.0.0:*")
        return "\n".join(lines) + "\n"

    def probe(self, spec: RUNNER.CommandSpec) -> str:
        self.probe_counter += 1
        (
            run_id, role, expected_text, uid_text, gid_text,
            _v4, _v6, ports_text,
        ) = spec.argv[-8:]
        expected = expected_text == "success"
        if self.mutate_probe is not None and spec.action == self.mutate_probe:
            observed = not expected
        else:
            observed = expected
        slice_value = next(
            item.split("=", 1)[1] for item in spec.argv
            if item.startswith("--slice="))
        outcomes = [
            {
                "family": family,
                "port": port,
                "connected": observed,
                "payload_valid": observed,
            }
            for family in ("ipv4", "ipv6")
            for port in [int(item) for item in ports_text.split(",")]
        ]
        record = {
            "schema": RUNNER.PROBE_SCHEMA,
            "run_id": run_id,
            "role": role,
            "expected": expected_text,
            "passed": True,
            "uid": int(uid_text),
            "gid": int(gid_text),
            "cgroup": f"0::/{slice_value}/fixture.service",
            "netns_inode": "net:[4026533001]",
            "invocation_id": format(self.probe_counter, "032x"),
            "outcomes": outcomes,
        }
        return (
            RUNNER.PROBE_MARKER +
            json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")

    def run(self, spec: RUNNER.CommandSpec) -> RUNNER.CommandResult:
        self.actions.append(spec.action)
        if spec.action == self.fail_action:
            return RUNNER.CommandResult(97, "injected failure\n", 1)
        output = ""
        if spec.action == "execution-anchor.start":
            self.anchor_epoch = 1
        elif spec.action == "execution-anchor.restart":
            self.anchor_epoch = 2
        if spec.action == "preflight.boot-id":
            output = BOOT_ID + "\n"
        elif spec.action == "preflight.cgroup-fs":
            output = "cgroup2\n"
        elif spec.action == "preflight.virtualization":
            output = "kvm\n"
        elif spec.action in {"preflight.git-head", "cleanup.verify.git-head"}:
            output = COMMIT + "\n"
        elif spec.action in {"preflight.git-clean", "cleanup.verify.git-clean"}:
            output = ""
        elif spec.action in {
                "firewall.snapshot", "firewall.after-flush",
                "firewall.after-reload", "cleanup.firewall-final-inspect",
                "cleanup.firewall-inspect"}:
            if self.firewall_drift and spec.action in {
                    "firewall.after-reload", "cleanup.firewall-final-inspect"}:
                output = '{"nftables":[{"table":{"family":"inet","name":"drift"}}]}\n'
            else:
                output = FIREWALL_JSON
        elif spec.action == "inventory.initial.processes" and self.forwarder:
            output = "123 0 socat socat TCP-LISTEN:7497,fork TCP:127.0.0.1:1\n"
        elif spec.action.startswith("sentinel.status."):
            invocation = "1" * 32
            if ".restart." in spec.action:
                invocation = "2" * 32
            output = (
                "ActiveState=active\nSubState=running\n"
                f"InvocationID={invocation}\n"
                f"ControlGroup=/{self.topology.slices['broker']}/"
                f"{self.topology.units['sentinel']}\n"
                "MainPID=4321\nUser=29001\nGroup=29001\n")
        elif ".inactive." in spec.action:
            output = "ActiveState=inactive\nSubState=dead\nMainPID=0\n"
        elif spec.action.startswith("execution-anchor.status."):
            output = (
                "ActiveState=active\nSubState=running\n"
                f"InvocationID={format(self.anchor_epoch, '032x')}\n"
                f"ControlGroup=/{self.topology.slices['execution']}/"
                f"{self.topology.units['execution_anchor']}\n"
                "MainPID=5432\nUser=29002\nGroup=29002\n")
        elif spec.action.startswith("sentinel.sockets."):
            output = self.sentinel_sockets()
        elif spec.action.startswith("probe."):
            output = self.probe(spec)
        elif spec.action == "cleanup.verify.links" and self.cleanup_residue:
            output = f"99: hb{self.topology.short}1: <BROADCAST>\n"
        return RUNNER.CommandResult(0, output, 1)


def make_gate(
        directory: str, **fake_options: object,
        ) -> tuple[RUNNER.HardIsolationGate, FakeExecutor]:
    topology = RUNNER.make_topology(RUN_ID)
    fake = FakeExecutor(topology, **fake_options)
    gate = RUNNER.HardIsolationGate(
        evidence(), fake, run_id=RUN_ID,
        runtime_parent=Path(directory), now_ms=int(time.time() * 1000))
    return gate, fake


def provenance_document(
        kind: str, *, now_ms: int, runner_sha: str) -> dict[str, object]:
    common: dict[str, object] = {
        "schema": RUNNER.PROVENANCE_SCHEMAS[kind],
        "decision": "GO",
        "reviewed": True,
        "issued_at_ms": now_ms - 1000,
        "expires_at_ms": now_ms + 1000,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
    }
    if kind == "host":
        listeners: list[str] = []
        netns: list[str] = []
        common.update({
            "disposable": True,
            "destructive_network_drills_authorized": True,
            "host_id": HOST_ID,
            "boot_id": BOOT_ID,
            "virtualization": "kvm",
            "console_access": True,
            "expected_euid": 0,
            "host_listener_allowlist": listeners,
            "host_listener_allowlist_sha256": RUNNER.sha256_bytes(
                RUNNER.canonical_json(listeners)),
            "host_netns_allowlist": netns,
            "host_netns_allowlist_sha256": RUNNER.sha256_bytes(
                RUNNER.canonical_json(netns)),
            "host_firewall_semantic_sha256": FIREWALL_SHA,
            "firewall_reload_unit": "nftables.service",
            "reachable_forwarders": 0,
            "ib_binaries": 0,
            "broker_credentials": 0,
        })
    elif kind == "source":
        common.update({
            "clean": True,
            "source_commit": COMMIT,
            "source_manifest_sha256": "f" * 64,
            "runner_sha256": runner_sha,
        })
    elif kind == "base":
        common.update({
            "host_id": HOST_ID,
            "boot_id": BOOT_ID,
            "native_vm_snapshot_sha256": "a" * 64,
            "os_release_sha256": "b" * 64,
            "base_review_sha256": "c" * 64,
            "ib_binaries": 0,
            "broker_credentials": 0,
            "broker_protocol_clients": 0,
        })
    elif kind == "tooling":
        common.update({
            "host_id": HOST_ID,
            "boot_id": BOOT_ID,
            "cgroup_v2": True,
            "nft_socket_cgroupv2": True,
            "netns_supported": True,
            "systemd_network_namespace_path_supported": True,
            "binary_sha256": {
                name: format(index, "064x")
                for index, name in enumerate(RUNNER.TOOL_PATHS, 1)
            },
        })
    else:
        raise AssertionError(kind)
    common["body_sha256"] = RUNNER.body_sha256(common)
    return common


class BrokerNetworkHardIsolationGateFixture(unittest.TestCase):
    def test_default_and_unenabled_production_executors_are_disabled(self) -> None:
        spec = RUNNER.CommandSpec("forbidden", ("/usr/bin/true",))
        with self.assertRaises(RUNNER.GateError):
            RUNNER.DisabledExecutor().run(spec)
        with self.assertRaises(RUNNER.GateError):
            RUNNER.ProductionExecutor().run(spec)

    def test_certification_mode_requires_exact_enabled_production_executor(
            self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-hard-mode-") as directory:
            certifying_evidence = replace(
                evidence(), revalidate_paths=True, provenance_owner_uid=0)
            with mock.patch.object(RUNNER.os, "geteuid", return_value=0):
                native = RUNNER.HardIsolationGate(
                    certifying_evidence,
                    RUNNER.ProductionExecutor(enabled=True), run_id=RUN_ID,
                    runtime_parent=Path(directory),
                    environment_review_session=mock.Mock())
                injected = RUNNER.HardIsolationGate(
                    certifying_evidence,
                    FakeExecutor(RUNNER.make_topology(RUN_ID)), run_id=RUN_ID,
                    runtime_parent=Path(directory))
        self.assertTrue(native.certification_capable)
        self.assertFalse(injected.certification_capable)

    def test_topology_is_unique_bounded_and_has_five_planes(self) -> None:
        topology = RUNNER.make_topology(RUN_ID)
        self.assertEqual(set(topology.namespaces), set(RUNNER.ROLES))
        self.assertEqual(len(set(topology.namespaces.values())), 5)
        self.assertEqual(len(set(topology.slices.values())), 5)
        self.assertTrue(all(
            len(item) <= 15 for item in
            (*topology.client_ifaces.values(), *topology.broker_ifaces.values())))
        with self.assertRaises(RUNNER.GateError):
            RUNNER.make_topology("not-a-run-id")
        with self.assertRaises(RUNNER.GateError):
            RUNNER.validate_reserved_uids_unused(
                "123 29002 python3 /usr/bin/python3 fixture\n")

    def test_nft_contract_requires_exact_execution_uid_and_cgroup_slice(
            self) -> None:
        topology = RUNNER.make_topology(RUN_ID)
        positive = RUNNER.client_nft(
            topology, "execution", execution_forward=True).decode("ascii")
        denied = RUNNER.client_nft(
            topology, "gateway", execution_forward=False).decode("ascii")
        broker = RUNNER.broker_nft(topology, forward=True).decode("ascii")
        self.assertIn("meta skuid 29002", positive)
        self.assertIn("socket cgroupv2 level 1", positive)
        self.assertIn(topology.slices["execution"], positive)
        self.assertNotIn("meta skuid", denied)
        self.assertIn("chain output { type filter hook output priority -200; policy drop;", denied)
        self.assertIn(topology.broker_ifaces["execution"], broker)
        self.assertIn(topology.ipv4["execution"][1], broker)
        self.assertNotIn(
            f"ip saddr {topology.ipv4['execution'][0]}", broker)
        self.assertNotIn(topology.broker_ifaces["gateway"] + " ip", broker)
        properties = RUNNER.systemd_properties(
            topology, "execution", RUNNER.UIDS["execution"])
        self.assertIn("--property=Restart=no", properties)
        self.assertIn("--property=KillMode=control-group", properties)
        self.assertIn(
            "--property=NetworkNamespacePath=/run/netns/" +
            topology.namespaces["execution"], properties)

    def test_fixture_code_has_no_ib_protocol_credential_or_order_surface(
            self) -> None:
        compile(RUNNER.SENTINEL_CODE, "<inert-sentinel>", "exec")
        compile(RUNNER.PROBE_CODE, "<hard-isolation-probe>", "exec")
        source = MODULE_PATH.read_text(encoding="utf-8", errors="strict")
        for forbidden in (
                "import ibapi", "EClientSocket", "placeOrder(", "reqIds(",
                "trade.place_order", "BROKER_PASSWORD", "IB_ACCOUNT"):
            self.assertNotIn(forbidden, source)
        self.assertEqual(source.count("subprocess.Popen("), 1)
        self.assertNotIn("subprocess.run(", source)
        self.assertNotIn("os.system(", source)
        self.assertIn("HEPTA-INERT-SENTINEL-V1", source)
        self.assertEqual(RUNNER.BOUNDARY["broker_protocol_messages"], 0)
        self.assertEqual(RUNNER.BOUNDARY["orders"], 0)

    def test_success_path_exercises_all_required_drills_and_cleans_up(
            self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-hard-gate-fixture-") as directory:
            gate, fake = make_gate(directory)
            with mock.patch.object(RUNNER.os, "geteuid", return_value=0):
                report = gate.run()
        self.assertFalse(report["passed"])
        self.assertFalse(report["certification_ready"])
        self.assertTrue(report["rehearsal_passed"])
        self.assertEqual(report["decision"], "REHEARSAL_ONLY")
        self.assertEqual(report["execution_mode"], "INJECTED_REHEARSAL")
        self.assertTrue(all(report["checks"].values()))
        self.assertTrue(report["cleanup"]["complete"])
        self.assertFalse(report["paper_authorized"])
        self.assertFalse(report["order_submission_authorized"])
        required_actions = {
            "firewall.flush", "firewall.reload",
            "probe.wrong-cgroup", "probe.wrong-uid",
            "execution-anchor.start", "execution-anchor.sigkill",
            "execution-anchor.restart", "sentinel.kill.restart",
            "sentinel.restart.restart",
            "route.revoke.ipv4", "route.regrant.ipv4",
            "interface.revoke", "interface.regrant",
            "policy.execution.outbound-revoked",
            "policy.broker.inbound-revoked",
            "policy.execution.bilateral-revoked",
            "policy.broker.bilateral-revoked",
            "probe.final-deny.execution",
            "cleanup.verify.cgroups", "cleanup.verify.listeners",
        }
        self.assertTrue(required_actions.issubset(fake.actions))
        self.assertLess(fake.actions.index("firewall.flush"),
                        fake.actions.index("firewall.reload"))
        self.assertLess(fake.actions.index("probe.final-deny.execution"),
                        fake.actions.index("cleanup.verify.netns"))

    def test_unexpected_wrong_cgroup_connectivity_is_no_go_and_cleanup_runs(
            self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-hard-gate-fixture-") as directory:
            gate, fake = make_gate(
                directory, mutate_probe="probe.wrong-cgroup")
            with mock.patch.object(RUNNER.os, "geteuid", return_value=0):
                report = gate.run()
        self.assertFalse(report["passed"])
        self.assertEqual(report["decision"], "NO_GO")
        self.assertIn("probe protected-port", report["failure"])
        self.assertTrue(report["cleanup"]["attempted"])
        self.assertIn("cleanup.verify.netns", fake.actions)
        self.assertFalse(report["direct_broker_access"])

    def test_forwarder_inventory_fails_before_namespace_setup(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-hard-gate-fixture-") as directory:
            gate, fake = make_gate(directory, forwarder=True)
            with mock.patch.object(RUNNER.os, "geteuid", return_value=0):
                report = gate.run()
        self.assertFalse(report["passed"])
        self.assertIn("forwarder", report["failure"])
        self.assertNotIn("setup.netns.broker", fake.actions)
        self.assertFalse(any(
            action.startswith("cleanup.unit-stop") for action in fake.actions))
        self.assertFalse(any(
            action.startswith("cleanup.netns") for action in fake.actions))

    def test_firewall_reload_semantic_drift_is_no_go_but_cleanup_restores(
            self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-hard-gate-fixture-") as directory:
            gate, fake = make_gate(directory, firewall_drift=True)
            with mock.patch.object(RUNNER.os, "geteuid", return_value=0):
                report = gate.run()
        self.assertFalse(report["passed"])
        self.assertIn("firewall reload", report["failure"])
        self.assertIn("cleanup.firewall-reload", fake.actions)
        self.assertTrue(report["cleanup"]["firewall_restored"])

    def test_partial_firewall_flush_failure_still_forces_reload_cleanup(
            self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-hard-gate-fixture-") as directory:
            gate, fake = make_gate(directory, fail_action="firewall.flush")
            with mock.patch.object(RUNNER.os, "geteuid", return_value=0):
                report = gate.run()
        self.assertFalse(report["passed"])
        self.assertIn("firewall.flush", report["failure"])
        self.assertIn("cleanup.firewall-reload", fake.actions)
        self.assertTrue(report["cleanup"]["firewall_restored"])

    def test_cleanup_residue_prevents_pass(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-hard-gate-fixture-") as directory:
            gate, _fake = make_gate(directory, cleanup_residue=True)
            with mock.patch.object(RUNNER.os, "geteuid", return_value=0):
                report = gate.run()
        self.assertFalse(report["passed"])
        self.assertFalse(report["cleanup"]["complete"])
        self.assertIn("links-residue", report["cleanup"]["residue"])

    def test_provenance_is_canonical_exact_unexpired_and_independent(self) -> None:
        now_ms = 1_900_000_000_000
        with tempfile.TemporaryDirectory(prefix="hepta-hard-provenance-") as directory:
            root = Path(directory)
            runner = root / "runner.py"
            runner.write_bytes(b"reviewed runner fixture\n")
            runner_sha = RUNNER.sha256_bytes(runner.read_bytes())
            paths = {}
            for kind in ("host", "source", "base", "tooling"):
                document = provenance_document(
                    kind, now_ms=now_ms, runner_sha=runner_sha)
                path = root / f"{kind}.json"
                path.write_bytes(RUNNER.canonical_json(document))
                path.chmod(0o600)
                paths[kind] = path
            bundle = RUNNER.load_evidence(
                host_path=paths["host"], source_path=paths["source"],
                base_path=paths["base"], tooling_path=paths["tooling"],
                now_ms=now_ms, expected_uid=os.geteuid(),
                runner_path=runner, validate_tool_files=False)
            self.assertEqual(bundle.boot_id, BOOT_ID)
            self.assertEqual(len({
                item["file_sha256"] for item in bundle.records.values()}), 4)
            tooling = provenance_document(
                "tooling", now_ms=now_ms, runner_sha=runner_sha)
            tooling["nft_socket_cgroupv2"] = False
            tooling["body_sha256"] = RUNNER.body_sha256(tooling)
            paths["tooling"].write_bytes(RUNNER.canonical_json(tooling))
            paths["tooling"].chmod(0o600)
            with self.assertRaises(RUNNER.GateError):
                RUNNER.load_evidence(
                    host_path=paths["host"], source_path=paths["source"],
                    base_path=paths["base"], tooling_path=paths["tooling"],
                    now_ms=now_ms, expected_uid=os.geteuid(),
                    runner_path=runner, validate_tool_files=False)

    def test_provenance_authority_extra_field_and_noncanonical_bytes_rejected(
            self) -> None:
        now_ms = 1_900_000_000_000
        with tempfile.TemporaryDirectory(prefix="hepta-hard-provenance-") as directory:
            root = Path(directory)
            runner = root / "runner.py"
            runner.write_bytes(b"runner\n")
            runner_sha = RUNNER.sha256_bytes(runner.read_bytes())
            original = provenance_document(
                "host", now_ms=now_ms, runner_sha=runner_sha)
            mutations = []
            authority = copy.deepcopy(original)
            authority["paper_authorized"] = True
            authority["body_sha256"] = RUNNER.body_sha256(authority)
            mutations.append(RUNNER.canonical_json(authority))
            extra = copy.deepcopy(original)
            extra["unexpected"] = True
            extra["body_sha256"] = RUNNER.body_sha256(extra)
            mutations.append(RUNNER.canonical_json(extra))
            mutations.append(
                (json.dumps(original, indent=2, sort_keys=True) + "\n").encode("ascii"))
            for index, raw in enumerate(mutations):
                path = root / f"host-{index}.json"
                path.write_bytes(raw)
                path.chmod(0o600)
                with self.subTest(index=index), self.assertRaises(RUNNER.GateError):
                    RUNNER.read_provenance(
                        "host", path, now_ms=now_ms,
                        expected_uid=os.geteuid())

    def test_directory_anchor_ignores_sibling_timestamp_noise_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-hard-anchor-") as directory:
            root = Path(directory)
            descriptor, before = RUNNER.open_anchored_directory(
                root, expected_uid=os.geteuid())
            try:
                (root / "sibling").write_text("publication\n", encoding="ascii")
                RUNNER.assert_directory_identity(descriptor, before)
                after = RUNNER.directory_identity(os.fstat(descriptor))
            finally:
                os.close(descriptor)
        self.assertEqual(before, after)
        self.assertEqual(
            set(before), {
                "st_dev", "st_ino", "st_uid", "st_gid", "st_mode",
                "st_nlink"})

    def test_atomic_report_is_mode_0600_reopened_and_no_replace(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-hard-report-") as directory:
            gate, _fake = make_gate(directory)
            with mock.patch.object(RUNNER.os, "geteuid", return_value=0):
                report = gate.run()
            path = Path(directory) / "receipt.json"
            RUNNER.write_report_no_replace(path, report)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.read_bytes(), RUNNER.canonical_json(report))
            with self.assertRaises(FileExistsError):
                RUNNER.write_report_no_replace(path, report)

    def test_report_rejects_extra_field_or_authority_flip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-hard-report-") as directory:
            gate, _fake = make_gate(directory)
            with mock.patch.object(RUNNER.os, "geteuid", return_value=0):
                report = gate.run()
        extra = copy.deepcopy(report)
        extra["unexpected"] = True
        with self.assertRaises(RUNNER.GateError):
            RUNNER.validate_report(extra)
        authority = copy.deepcopy(report)
        authority["paper_authorized"] = True
        authority["body_sha256"] = RUNNER.body_sha256(authority)
        with self.assertRaises(RUNNER.GateError):
            RUNNER.validate_report(authority)
        forged = copy.deepcopy(report)
        forged["passed"] = True
        forged["certification_ready"] = True
        forged["decision"] = "GO"
        forged["body_sha256"] = RUNNER.body_sha256(forged)
        with self.assertRaises(RUNNER.GateError):
            RUNNER.validate_report(forged)
        stale = copy.deepcopy(report)
        stale["expires_at_ms"] = stale["completed_at_ms"]
        stale["body_sha256"] = RUNNER.body_sha256(stale)
        with self.assertRaises(RUNNER.GateError):
            RUNNER.validate_report(stale)

    def test_main_refuses_without_run_before_reading_provenance(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-hard-main-") as directory:
            missing = str(Path(directory) / "missing.json")
            result = RUNNER.main([
                "--host-provenance", missing,
                "--source-provenance", missing,
                "--base-provenance", missing,
                "--tooling-provenance", missing,
                "--report", str(Path(directory) / "report.json"),
            ])
        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
