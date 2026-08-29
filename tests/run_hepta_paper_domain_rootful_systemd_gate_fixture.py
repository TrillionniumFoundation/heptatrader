#!/usr/bin/env python3

"""Rootless contract tests for the PAPER-domain effective-systemd runner."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve(strict=True).parents[1]
MODULE_PATH = (
    ROOT / "scripts/run_hepta_paper_domain_rootful_systemd_gate.py")
SPEC = importlib.util.spec_from_file_location(
    "run_hepta_paper_domain_rootful_systemd_gate_under_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot import PAPER-domain rootful runner")
RUNNER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUNNER
SPEC.loader.exec_module(RUNNER)


def pinned() -> str:
    return "registry.example/hepta/systemd@sha256:" + "a" * 64


RUN_ID = "c" * 32


def valid_inner(run_id: str = RUN_ID) -> dict[str, object]:
    return {
        "schema": RUNNER.INNER_SCHEMA,
        "passed": True,
        "run_id": run_id,
        "checks": {name: True for name in RUNNER.EXPECTED_CHECKS},
        "versions": {
            "systemd": "systemd 252",
            "nft": "nftables v1.0.6",
            "kernel": "6.8.0",
            "architecture": "x86_64",
            "cgroup": "v2-private",
            "immutable_file_count": "1234",
            "immutable_file_inventory_sha256": "a" * 64,
            "package_count": "100",
            "package_inventory_sha256": "b" * 64,
        },
        "boot": {
            "boot_id": "01234567-89ab-cdef-0123-456789abcdef",
            "pid1_cgroup": "0::/",
        },
        "boundary": copy.deepcopy(RUNNER.EXPECTED_BOUNDARY),
    }


def reseal(value: dict[str, object]) -> dict[str, object]:
    result = copy.deepcopy(value)
    result.pop("body_sha256", None)
    result["body_sha256"] = RUNNER.canonical_sha256(result)
    return result


def valid_rehearsal_report() -> dict[str, object]:
    inner = valid_inner()
    inputs = {
        name: {"sha256": (format(index + 1, "064x")),
               "size": 1, "mode": format(mode, "04o")}
        for index, (name, (_destination, mode)) in enumerate(
            RUNNER.SOURCE_FILES.items())
    }
    now = int(time.time() * 1000)
    report = {
        "schema": RUNNER.SCHEMA,
        "run_id": RUN_ID,
        "decision": "REHEARSAL_ONLY",
        "passed": False,
        "rehearsal_passed": True,
        "certification_ready": False,
        "certification_blockers": list(RUNNER.CERTIFICATION_BLOCKERS),
        "scope": "broker-free-paper-domain-rootful-prerequisite-only",
        "started_at_ms": now,
        "completed_at_ms": now + 10,
        "expires_at_ms": now + 1000,
        "duration_ms": 10,
        "paper_test_admission_candidate": False,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
        "order_submission_authorized": False,
        "lineage": {
            "source_commit": "d" * 40,
            "expected_source_commit": "d" * 40,
            "source_tree_clean": False,
            "all_inputs_versioned": False,
            "inputs_stable": True,
            "final_lineage": False,
            "input_manifest_sha256": RUNNER.canonical_sha256(inputs),
            "expected_input_manifest_sha256": None,
            "runner_sha256": "sha256:" + inputs[
                "scripts/run_hepta_paper_domain_rootful_systemd_gate.py"
            ]["sha256"],
            "expected_runner_sha256": None,
        },
        "inputs": inputs,
        "generated_input_sha256": {"fixture": "e" * 64},
        "platform": {
            "host_kernel": "6.8", "host_architecture": "x86_64",
            "docker_client": "Docker 29", "docker_server_version": "29.0.0",
            "docker_server_api_version": "1.50", "docker_server_os": "Linux",
            "docker_server_architecture": "amd64",
            "docker_cgroup_driver": "systemd", "docker_cgroup_version": "2",
            "docker_default_runtime": "runc",
            "docker_security_options": ["name=apparmor"],
            "base_image_reference": pinned(),
            "base_image_id": "sha256:" + "f" * 64,
            "base_image_os": "linux", "base_image_architecture": "amd64",
            "systemd": "systemd 252", "nft": "nft 1.0",
            "container_kernel": "6.8", "container_architecture": "x86_64",
            "container_cgroup": "v2-private",
            "container_boot_id": inner["boot"]["boot_id"],
            "container_pid1_cgroup": "0::/",
            "immutable_file_count": "100",
            "immutable_file_inventory_sha256": "a" * 64,
            "package_count": "10", "package_inventory_sha256": "b" * 64,
        },
        "container": {
            "image_id": "sha256:" + "1" * 64, "network_mode": "none",
            "read_only_rootfs": True, "private_cgroup_namespace": True,
            "privileged": False, "bind_mounts": 0, "published_ports": 0,
            "devices": 0, "device_requests": 0, "links": 0,
            "tmpfs_allowlist": copy.deepcopy(RUNNER.RUNTIME_TMPFS),
            "apparmor_profile": RUNNER.APPARMOR_PROFILE,
            "capabilities": list(RUNNER.RUNTIME_CAPABILITIES),
        },
        "disposable_cleanup": {
            "container_absent": True, "image_tag_absent": True,
            "image_id_absent": True,
        },
        "certification": {
            "requested": False, "eligible": False, "provenance": None,
            "provenance_reopened_equal": False, "reviewed_base": None,
            "reviewed_buildkit": None, "buildx_toolchain": None,
            "isolated_builder": None, "isolated_builder_cleanup": None,
            "docker_socket_before": None, "docker_socket_after": None,
            "docker_socket_records_equal": False,
            "apparmor_before": None, "apparmor_after": None,
            "apparmor_records_equal": False,
            "docker_namespace_before": None, "docker_namespace_after": None,
            "docker_namespace_records_equal": False,
        },
        "environment_review_closure": None,
        "inner": inner,
        "boundary": {
            "host_root_sentinel_required": False,
            "host_systemd_units_touched": 0, "host_nft_tables_touched": 0,
            "real_broker_connections": 0, "broker_protocol_messages": 0,
            "real_credentials": 0, "paper_orders": 0,
            "paper_units_instantiated": 8, "inert_stub_only": True,
            "fixture_local_authority_only": True,
            "paper_test_admission_candidate": False,
            "paper_authorized": False, "live_authorized": False,
            "mutation_authorized": False, "direct_broker_access": False,
            "order_submission_authorized": False,
        },
    }
    return RUNNER.seal_report(report)


def provenance_body(kind: str, now: int | None = None) -> dict[str, object]:
    now = int(time.time() * 1000) if now is None else now
    common = {
        "decision": "GO", "issued_at_ms": now - 1000,
        "expires_at_ms": now + 60_000,
    }
    if kind == "base":
        return {**common, "schema": RUNNER.REVIEWED_BASE_PROVENANCE_SCHEMA,
                "image_id": "sha256:" + "1" * 64,
                "repo_digest": pinned(), "labels_sha256": "sha256:" + "2" * 64}
    if kind == "builder":
        return {
            **common, "schema": RUNNER.REVIEWED_BUILDER_PROVENANCE_SCHEMA,
            "image_id": "sha256:" + "3" * 64,
            "repo_digest": "registry.example/buildkit@sha256:" + "4" * 64,
            "config_sha256": "sha256:" + "5" * 64,
            "buildkit_version": "v0.20.0", "buildx_version": "0.30.0",
            "buildx_binary_sha256": "sha256:" + "6" * 64,
            "docker_server_version": "29.0.0",
            "docker_server_api_version": "1.50",
            "docker_server_git_commit": "abcdef1",
        }
    if kind == "apparmor":
        return {
            **common, "schema": RUNNER.REVIEWED_APPARMOR_PROVENANCE_SCHEMA,
            "profile": RUNNER.APPARMOR_PROFILE,
            "policy_source_sha256": "sha256:" + "7" * 64,
            "profile_sha256": "sha256:" + "8" * 64,
            "raw_sha256": "sha256:" + "9" * 64, "raw_abi": "v8",
        }
    if kind == "docker_namespace":
        return {
            **common,
            "schema": RUNNER.REVIEWED_DOCKER_NAMESPACE_PROVENANCE_SCHEMA,
            "docker_daemon_id": "daemon-id", "docker_daemon_pid": 123,
            "docker_daemon_start_time_ticks": 456,
            "host_boot_id": "01234567-89ab-cdef-0123-456789abcdef",
            "host_namespace_name": "root", "host_namespace_level": 0,
            "host_namespace_stacked": False,
            "daemon_namespace_name": "root", "daemon_namespace_level": 0,
            "daemon_namespace_stacked": False,
        }
    raise AssertionError(kind)


def valid_runtime_inspect() -> dict[str, object]:
    image_id = "sha256:" + "b" * 64
    name = "paper-fixture"
    env = [
        "HEPTA_PAPER_DOMAIN_SYSTEMD_DISPOSABLE=1",
        "HEPTA_PAPER_DOMAIN_SYSTEMD_RUN_ID=" + RUN_ID,
    ]
    return {
        "Id": "a" * 64, "Name": "/" + name, "Image": image_id,
        "AppArmorProfile": RUNNER.APPARMOR_PROFILE,
        "Config": {
            "Image": image_id, "Hostname": "hepta-paper-domain-systemd",
            "User": "0:0", "WorkingDir": "/",
            "Entrypoint": [
                "/usr/local/libexec/hepta-paper-domain-systemd-entrypoint"],
            "Cmd": None, "ExposedPorts": None, "Volumes": None,
            "StopSignal": "SIGRTMIN+3", "Env": env,
            "Labels": {"io.hepta.purpose": RUNNER.PURPOSE,
                       RUNNER.RUN_LABEL_KEY: RUN_ID},
        },
        "HostConfig": {
            "Privileged": False, "ReadonlyRootfs": True,
            "NetworkMode": "none", "CgroupnsMode": "private",
            "IpcMode": "private", "SecurityOpt": [
                "no-new-privileges", "apparmor=" + RUNNER.APPARMOR_PROFILE],
            "PidsLimit": 512, "Memory": 1024 * 1024 * 1024,
            "NanoCpus": 2_000_000_000, "PublishAllPorts": False,
            "PortBindings": {}, "Binds": [], "Devices": [],
            "DeviceRequests": [], "DeviceCgroupRules": [], "Links": [],
            "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
            "Tmpfs": copy.deepcopy(RUNNER.RUNTIME_TMPFS),
            "CapDrop": ["ALL"],
            "CapAdd": ["CAP_" + item for item in RUNNER.RUNTIME_CAPABILITIES],
        },
        "Mounts": [
            {"Type": "tmpfs", "Destination": path}
            for path in RUNNER.RUNTIME_TMPFS],
    }


class PaperDomainRootfulRunnerFixture(unittest.TestCase):
    def test_default_is_disabled_without_docker_or_report(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-paper-disabled-") as directory:
            report = Path(directory) / "receipt.json"
            argv = [
                "gate", "--base-image", pinned(),
                "--expected-source-commit", "d" * 40,
                "--report", str(report),
            ]
            with (
                    mock.patch.object(sys, "argv", argv),
                    mock.patch.object(
                        RUNNER, "execute",
                        side_effect=AssertionError("execute must remain disabled"))):
                self.assertEqual(RUNNER.main(), 78)
            self.assertFalse(report.exists())

    def test_restart_fault_injection_remains_bounded_for_slow_stop_post(self):
        inner = (
            ROOT / "tests/paper_domain_rootful_systemd/"
            "hepta_paper_domain_rootful_inner_gate.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "def wait_restarted(unit: str, previous_pid: int, "
            "timeout: float = 45.0)",
            inner,
        )
        self.assertNotIn("timeout: float = 60.0", inner)

    def test_paper_opt_in_is_fixture_local_and_shipped_unit_is_deny_all(
            self) -> None:
        inner = (
            ROOT / "tests/paper_domain_rootful_systemd/"
            "hepta_paper_domain_rootful_inner_gate.py"
        ).read_text(encoding="utf-8", errors="strict")
        unit = (ROOT / "systemd/hepta-broker-egress-policy.service").read_text(
            encoding="utf-8", errors="strict")
        self.assertIn("BROKER_PAPER_OPT_IN_DROPIN", inner)
        self.assertIn(
            "90-explicit-paper-opt-in-gate.conf", inner)
        self.assertIn("--supervise --paper-identities", inner)
        self.assertIn("ExecStart=/usr/bin/python3.12 -I -S ", inner)
        self.assertIn("ExecStopPost=/usr/bin/python3.12 -I -S ", inner)
        self.assertIn("--supervise-deny-all --paper-identities", unit)
        self.assertNotIn(" --supervise --paper-identities ", unit)
        for source in (inner, unit):
            self.assertIn(
                "${CREDENTIALS_DIRECTORY}/hepta-broker-egress-policy.py",
                source)

    def test_base_reference_is_digest_pinned(self) -> None:
        self.assertEqual(RUNNER.require_pinned_image(pinned()), pinned())
        for value in (
                "debian:bookworm",
                "debian@sha256:" + "a" * 63,
                "debian@sha256:" + "A" * 64):
            with self.assertRaises(RUNNER.GateError):
                RUNNER.require_pinned_image(value)
        self.assertTrue(RUNNER.architecture_matches("amd64", "x86_64"))
        self.assertTrue(RUNNER.architecture_matches("arm64", "aarch64"))
        self.assertFalse(RUNNER.architecture_matches("arm64", "x86_64"))

    def test_context_is_exact_and_broker_free(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-paper-systemd-fixture-") as directory:
            context = Path(directory)
            records, generated = RUNNER.stage_context(ROOT, context)
            self.assertEqual(set(records), set(RUNNER.SOURCE_FILES))
            self.assertIn("network-a.json", generated)
            self.assertIn("authority-b.json", generated)
            self.assertIn("service-start-limit-dropin.conf", generated)
            self.assertEqual(
                (context / "install-root/usr/libexec/"
                 "hepta-ib-executiond").read_bytes(),
                (ROOT / "tests/paper_domain_rootful_systemd/"
                 "hepta_paper_inert_execution_stub.py").read_bytes())
        joined = "\n".join(RUNNER.SOURCE_FILES)
        for forbidden in (
                "Interface/IBApi", "ib_gateway_adapter",
                "hepta-ib-paper-authorization"):
            self.assertNotIn(forbidden, joined)

    def test_container_boundary_is_default_off_and_exact(self) -> None:
        build = RUNNER.build_arguments(
            pinned(), "hepta:test", Path("/context"), Path("/context/iid"),
            "c" * 32)
        self.assertIn("--network=none", build)
        self.assertIn("--no-cache", build)
        self.assertIn(
            f"{RUNNER.RUN_LABEL_KEY}=" + "c" * 32, build)
        create = RUNNER.create_arguments(
            "sha256:" + "b" * 64, "hepta-test", "c" * 32)
        self.assertEqual(create[0], "create")
        self.assertEqual(create[create.index("--network") + 1], "none")
        self.assertIn("--read-only", create)
        self.assertEqual(create[create.index("--cap-drop") + 1], "ALL")
        self.assertIn(
            f"{RUNNER.RUN_LABEL_KEY}=" + "c" * 32, create)
        self.assertNotIn("--privileged", create)
        self.assertIn(
            "apparmor=" + RUNNER.APPARMOR_PROFILE, create)
        self.assertNotIn("apparmor=unconfined", create)
        for forbidden in (
                "--mount", "--volume", "-v", "--publish", "-p",
                "/run/docker.sock", "/var/run/docker.sock"):
            self.assertNotIn(forbidden, create)

    def test_dirty_lineage_is_default_denied(self) -> None:
        RUNNER.require_source_lineage(True, False)
        RUNNER.require_source_lineage(False, True)
        with self.assertRaises(RUNNER.GateError):
            RUNNER.require_source_lineage(False, False)

    def test_cleanup_ownership_rejects_foreign_objects(self) -> None:
        run_id = "c" * 32
        owned = {
            "Id": "sha256:" + "d" * 64,
            "Config": {"Labels": {
                "io.hepta.purpose": RUNNER.PURPOSE,
                RUNNER.RUN_LABEL_KEY: run_id,
            }},
        }
        self.assertTrue(RUNNER.object_owned(owned, run_id))
        self.assertTrue(RUNNER.object_owned(
            owned, run_id, expected_image_id=owned["Id"]))
        foreign = copy.deepcopy(owned)
        foreign["Config"]["Labels"][RUNNER.RUN_LABEL_KEY] = "e" * 32
        self.assertFalse(RUNNER.object_owned(foreign, run_id))
        self.assertFalse(RUNNER.object_owned(
            owned, run_id, expected_image_id="sha256:" + "f" * 64))

    def test_inner_result_is_exact(self) -> None:
        value = valid_inner()
        framed = (
            RUNNER.INNER_MARKER +
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
        self.assertEqual(
            RUNNER.validate_inner(framed, expected_run_id=RUN_ID), value)
        mutation = copy.deepcopy(value)
        mutation["boundary"]["paper_orders"] = 1
        with self.assertRaises(RUNNER.GateError):
            RUNNER.validate_inner(
                RUNNER.INNER_MARKER + json.dumps(mutation),
                expected_run_id=RUN_ID)
        mutation = copy.deepcopy(value)
        mutation["checks"].pop(next(iter(RUNNER.EXPECTED_CHECKS)))
        with self.assertRaises(RUNNER.GateError):
            RUNNER.validate_inner(
                RUNNER.INNER_MARKER + json.dumps(mutation),
                expected_run_id=RUN_ID)

    def test_inert_fixture_contains_no_broker_protocol(self) -> None:
        text = (
            ROOT / "tests/paper_domain_rootful_systemd/"
            "hepta_paper_inert_execution_stub.py").read_text(
                encoding="utf-8", errors="strict")
        for forbidden in (
                "import ibapi", "EClientSocket", "placeOrder(",
                "trade.place_order", "reqIds("):
            self.assertNotIn(forbidden, text)

    def test_v2_rehearsal_is_strict_and_legacy_v1_cannot_certify(self) -> None:
        report = valid_rehearsal_report()
        self.assertEqual(RUNNER.validate_report(report), report)
        legacy = copy.deepcopy(report)
        legacy["schema"] = RUNNER.LEGACY_SCHEMA
        legacy = reseal(legacy)
        with self.assertRaises(RUNNER.GateError):
            RUNNER.validate_report(legacy)

    def test_fake_promotion_and_authority_mutations_fail_closed(self) -> None:
        base = valid_rehearsal_report()
        for mutate in (
                lambda item: item.update({
                    "decision": "GO", "passed": True,
                    "certification_ready": True, "certification_blockers": []}),
                lambda item: item.update({"paper_authorized": True}),
                lambda item: item.update({"order_submission_authorized": True}),
                lambda item: item["inner"]["boundary"].update(
                    {"paper_orders": 1})):
            changed = copy.deepcopy(base)
            mutate(changed)
            with self.assertRaises(RUNNER.GateError):
                RUNNER.validate_report(reseal(changed))

    def test_run_id_replay_and_cleanup_residue_fail_closed(self) -> None:
        base = valid_rehearsal_report()
        replay = copy.deepcopy(base)
        replay["inner"]["run_id"] = "e" * 32
        residue = copy.deepcopy(base)
        residue["disposable_cleanup"]["container_absent"] = False
        for changed in (replay, residue):
            with self.assertRaises(RUNNER.GateError):
                RUNNER.validate_report(reseal(changed))

    def test_runtime_inspect_rejects_unconfined_network_and_bind_drift(self) -> None:
        base = valid_runtime_inspect()
        RUNNER.validate_container_inspect_record(
            base, container_id="a" * 64,
            image_id="sha256:" + "b" * 64,
            name="paper-fixture", run_id=RUN_ID)
        mutations = []
        item = copy.deepcopy(base)
        item["AppArmorProfile"] = "unconfined"
        mutations.append(item)
        item = copy.deepcopy(base)
        item["HostConfig"]["SecurityOpt"] = [
            "no-new-privileges", "apparmor=unconfined"]
        mutations.append(item)
        item = copy.deepcopy(base)
        item["HostConfig"]["NetworkMode"] = "bridge"
        mutations.append(item)
        item = copy.deepcopy(base)
        item["HostConfig"]["Binds"] = ["/host:/container:ro"]
        mutations.append(item)
        for changed in mutations:
            with self.assertRaises(RUNNER.GateError):
                RUNNER.validate_container_inspect_record(
                    changed, container_id="a" * 64,
                    image_id="sha256:" + "b" * 64,
                    name="paper-fixture", run_id=RUN_ID)

    def test_provenance_exact_fields_freshness_and_pin(self) -> None:
        now = int(time.time() * 1000)
        body = provenance_body("base", now)
        raw = RUNNER.canonical_json(body)
        digest = "sha256:" + __import__("hashlib").sha256(raw).hexdigest()
        metadata = (1, 2, 0o100400, 1, 0, 0, len(raw), 3, 4)
        with mock.patch.object(
                RUNNER, "read_anchored_root_provenance",
                return_value=(raw, metadata, "0400")):
            document = RUNNER.read_root_canonical_provenance(
                Path("/root/base.json"), digest, kind="base",
                expected_schema=RUNNER.REVIEWED_BASE_PROVENANCE_SCHEMA,
                expected_keys=RUNNER.REVIEWED_BASE_KEYS, now_ms=now)
            self.assertEqual(document.body, body)
            with self.assertRaises(RUNNER.GateError):
                RUNNER.read_root_canonical_provenance(
                    Path("/root/base.json"), "sha256:" + "f" * 64,
                    kind="base",
                    expected_schema=RUNNER.REVIEWED_BASE_PROVENANCE_SCHEMA,
                    expected_keys=RUNNER.REVIEWED_BASE_KEYS, now_ms=now)
        for mutation in ("expired", "extra", "noncanonical"):
            changed = copy.deepcopy(body)
            if mutation == "expired":
                changed["expires_at_ms"] = now
            if mutation == "extra":
                changed["extra"] = True
            changed_raw = RUNNER.canonical_json(changed)
            if mutation == "noncanonical":
                changed_raw = b" " + changed_raw
            changed_digest = "sha256:" + __import__("hashlib").sha256(
                changed_raw).hexdigest()
            with mock.patch.object(
                    RUNNER, "read_anchored_root_provenance",
                    return_value=(changed_raw, metadata, "0400")):
                with self.assertRaises(RUNNER.GateError):
                    RUNNER.read_root_canonical_provenance(
                        Path("/root/base.json"), changed_digest, kind="base",
                        expected_schema=RUNNER.REVIEWED_BASE_PROVENANCE_SCHEMA,
                        expected_keys=RUNNER.REVIEWED_BASE_KEYS, now_ms=now)

    def test_provenance_missing_symlink_and_noncanonical_paths_fail(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-paper-prov-") as directory:
            root = Path(directory)
            regular = root / "regular.json"
            regular.write_text("{}\n", encoding="ascii")
            final_link = root / "final.json"
            final_link.symlink_to(regular.name)
            ancestor_link = root / "ancestor"
            ancestor_link.symlink_to(root, target_is_directory=True)
            for path in (
                    root / "missing.json", final_link,
                    ancestor_link / "regular.json",
                    Path(os.fspath(root) + "/../" + root.name + "/regular.json")):
                with self.assertRaises(RUNNER.GateError):
                    RUNNER.read_anchored_root_provenance(path, kind="test")

    def test_provenance_path_sha_and_inode_reuse_fail_closed(self) -> None:
        request = RUNNER.CertificationRequest(
            buildkit_image=str(provenance_body("builder")["repo_digest"]),
            buildx_binary_sha256=str(
                provenance_body("builder")["buildx_binary_sha256"]),
            reviewed_base_path=Path("/root/a.json"),
            reviewed_base_sha256="sha256:" + "a" * 64,
            reviewed_builder_path=Path("/root/b.json"),
            reviewed_builder_sha256="sha256:" + "a" * 64,
            reviewed_apparmor_path=Path("/root/c.json"),
            reviewed_apparmor_sha256="sha256:" + "c" * 64,
            reviewed_docker_namespace_path=Path("/root/d.json"),
            reviewed_docker_namespace_sha256="sha256:" + "d" * 64,
            expected_input_manifest_sha256="sha256:" + "e" * 64,
            expected_runner_sha256="sha256:" + "f" * 64)
        with self.assertRaises(RUNNER.GateError):
            RUNNER.load_certification_provenance(
                request, now_ms=int(time.time() * 1000))
        request = RUNNER.CertificationRequest(
            **{**request.__dict__,
               "reviewed_builder_sha256": "sha256:" + "b" * 64})
        bodies = {kind: provenance_body(kind) for kind in (
            "base", "builder", "apparmor", "docker_namespace")}
        counter = iter(bodies)

        def fake_read(*_args, **_kwargs):
            kind = next(counter)
            return RUNNER.RootProvenanceDocument(
                kind=kind, path=Path("/root/" + kind),
                document_sha256="sha256:" + kind[0] * 64,
                body=bodies[kind], metadata=(1, 99, 0, 0, 0, 0, 0, 0, 0),
                mode="0400")

        with mock.patch.object(
                RUNNER, "read_root_canonical_provenance",
                side_effect=fake_read):
            with self.assertRaises(RUNNER.GateError):
                RUNNER.load_certification_provenance(
                    request, now_ms=int(time.time() * 1000))

    def test_certification_arguments_require_root_all_pins_and_clean_mode(self):
        with mock.patch.object(RUNNER.os, "geteuid", return_value=1000):
            with self.assertRaises(RUNNER.GateError):
                RUNNER.certification_request_from_values(
                    certify=True, buildkit_image=None,
                    buildx_binary_sha256=None, reviewed_base_path=None,
                    reviewed_base_sha256=None, reviewed_builder_path=None,
                    reviewed_builder_sha256=None, reviewed_apparmor_path=None,
                    reviewed_apparmor_sha256=None,
                    reviewed_docker_namespace_path=None,
                    reviewed_docker_namespace_sha256=None,
                    expected_input_manifest_sha256=None,
                    expected_runner_sha256=None)
        with self.assertRaises(RUNNER.GateError):
            RUNNER.require_source_lineage(False, False)

    def test_docker_daemon_or_boot_drift_fails_namespace_binding(self) -> None:
        body = provenance_body("docker_namespace")
        document = RUNNER.RootProvenanceDocument(
            kind="docker_namespace", path=Path("/root/docker.json"),
            document_sha256="sha256:" + "a" * 64, body=body,
            metadata=(1,) * 9, mode="0400")
        process_a = {"pid": 123, "start_time_ticks": 456,
                     "comm": "dockerd", "process_inode": 10}
        process_b = {**process_a, "start_time_ticks": 457}
        with (
                mock.patch.object(
                    RUNNER, "docker_daemon_process_record",
                    side_effect=[process_a, process_b]),
                mock.patch.object(
                    RUNNER, "current_host_boot_id",
                    return_value=body["host_boot_id"]),
                mock.patch.object(
                    RUNNER, "docker_daemon_id",
                    return_value=body["docker_daemon_id"])):
            with self.assertRaises(RUNNER.GateError):
                RUNNER.validate_docker_namespace_binding(
                    document,
                    {"namespace": {
                        "name": "root", "level": 0, "stacked": False}})

    def test_builder_network_bind_and_external_pin_drift_fail_closed(self):
        names = RUNNER.isolated_builder_names(RUN_ID)
        image_id = "sha256:" + "3" * 64
        buildkit = {"id": image_id, "bare_id": "3" * 64,
                    "config_labels": {}}
        labels = RUNNER.builder_labels(
            RUN_ID, names["builder"], image_id, RUNNER.BUILDER_DAEMON_ROLE)
        record = {
            "Id": "4" * 64, "Name": "/" + names["container"],
            "Image": image_id,
            "Config": {"Image": "3" * 64, "Labels": labels},
            "State": {"Running": True},
            "HostConfig": {
                "NetworkMode": "none", "Privileged": True, "Init": True,
                "AutoRemove": False, "ReadonlyRootfs": False,
                "RestartPolicy": {"Name": "no"}, "Binds": [], "Tmpfs": {},
                "VolumesFrom": [], "Devices": [], "DeviceRequests": [],
                "PortBindings": {}, "PublishAllPorts": False,
            },
            "Mounts": [{
                "Type": "volume", "Name": names["volume"],
                "Destination": RUNNER.BUILDKIT_STATE_DIRECTORY,
                "Driver": "local", "RW": True,
            }],
        }
        RUNNER.validate_builder_container_record(
            record, names, RUN_ID, buildkit, running=True)
        for field, value in (("NetworkMode", "bridge"),
                             ("Binds", ["/host:/builder:ro"])):
            changed = copy.deepcopy(record)
            changed["HostConfig"][field] = value
            with self.assertRaises(RUNNER.GateError):
                RUNNER.validate_builder_container_record(
                    changed, names, RUN_ID, buildkit, running=True)
        request = RUNNER.CertificationRequest(
            buildkit_image="registry.example/buildkit@sha256:" + "4" * 64,
            buildx_binary_sha256="sha256:" + "6" * 64,
            reviewed_base_path=Path("/root/a"),
            reviewed_base_sha256="sha256:" + "a" * 64,
            reviewed_builder_path=Path("/root/b"),
            reviewed_builder_sha256="sha256:" + "b" * 64,
            reviewed_apparmor_path=Path("/root/c"),
            reviewed_apparmor_sha256="sha256:" + "c" * 64,
            reviewed_docker_namespace_path=Path("/root/d"),
            reviewed_docker_namespace_sha256="sha256:" + "d" * 64,
            expected_input_manifest_sha256="sha256:" + "e" * 64,
            expected_runner_sha256="sha256:" + "f" * 64)
        RUNNER.require_external_input_pins(
            "sha256:" + "e" * 64, "sha256:" + "f" * 64, request)
        with self.assertRaises(RUNNER.GateError):
            RUNNER.require_external_input_pins(
                "sha256:" + "0" * 64, "sha256:" + "f" * 64, request)

    def test_report_publication_is_noreplace(self) -> None:
        report = valid_rehearsal_report()
        with tempfile.TemporaryDirectory(
                prefix="hepta-paper-report-") as directory:
            path = Path(directory) / "receipt.json"
            RUNNER.atomic_report(path, report)
            with self.assertRaises(RUNNER.GateError):
                RUNNER.safe_report_path(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
