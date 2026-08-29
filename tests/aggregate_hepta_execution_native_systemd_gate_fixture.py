#!/usr/bin/env python3

"""Offline contract tests for native systemd variant aggregation."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any, cast
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))
sys.path.insert(0, str(REPOSITORY))
import aggregate_hepta_execution_native_systemd_gate as aggregate  # noqa: E402
import run_hepta_execution_native_systemd_gate as native  # noqa: E402
from tests.run_hepta_execution_native_systemd_gate_fixture import (  # noqa: E402
    agent_runtime_result,
    inner_result,
)


TRUSTED_TEST_OWNER_PAIRS = frozenset({
    (0, 0),
    (os.geteuid(), os.getegid()),
})


def protected_test_temp_parent() -> Path:
    candidates: list[Path] = []
    runtime_directory = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_directory:
        candidates.append(Path(runtime_directory))
    candidates.extend((
        Path("/run/user") / str(os.geteuid()),
        Path.home(),
        REPOSITORY,
    ))
    seen: set[Path] = set()
    for candidate in candidates:
        absolute = Path(os.path.abspath(candidate))
        if absolute in seen:
            continue
        seen.add(absolute)
        try:
            canonical = absolute.resolve(strict=True)
            if canonical != absolute:
                continue
            chain = tuple(reversed((canonical, *canonical.parents)))
            for component in chain:
                metadata = os.lstat(component)
                mode = stat.S_IMODE(metadata.st_mode)
                if (not stat.S_ISDIR(metadata.st_mode) or
                        (metadata.st_uid, metadata.st_gid) not in
                        TRUSTED_TEST_OWNER_PAIRS or
                        mode & 0o022 or mode & 0o7000):
                    break
            else:
                if os.access(canonical, os.W_OK | os.X_OK):
                    return canonical
        except (OSError, RuntimeError, ValueError):
            continue
    raise RuntimeError(
        "no writable protected parent is available for native aggregate "
        "file-backed fixtures")


PROTECTED_TEST_TEMP_PARENT = protected_test_temp_parent()


def instance_identity(variant: str, ordinal: int) -> dict[str, object]:
    boot_id = f"11111111-2222-4333-8{ordinal:03d}-55555555555{ordinal}"
    run_id = format(ordinal + 1, "032x")
    challenge = format(ordinal + 101, "064x")
    statement = {
        "schema": native.INSTANCE_STATEMENT_SCHEMA,
        "challenge": challenge,
        "instance_uuid":
            f"00000000-0000-4000-8000-{ordinal + 1:012d}",
        "instance_state": "running",
        "provisioner_id": "fixture-provisioner",
        "hypervisor_id": "fixture-hypervisor",
        "variant": variant,
        "vm_type": "kvm",
        "boot_id": boot_id,
        "run_id": run_id,
        "vm_image_manifest_sha256": format(ordinal + 21, "064x"),
        "provisioning_manifest_sha256": format(ordinal + 31, "064x"),
        "source_lineage": {
            "bundle_sha256": "6" * 64,
            "manifest_sha256": "7" * 64,
            "files_sha256": "8" * 64,
        },
        "issued_at_ms": 1_000_000 + ordinal,
        "expires_at_ms": 2_000_000 + ordinal,
    }
    def reference(path: str, marker: str, mode: str) -> dict[str, object]:
        return {
            "path": path,
            "file_sha256": hashlib.sha256(
                (marker + ":file").encode()).hexdigest(),
            "body_sha256": hashlib.sha256(
                (marker + ":body").encode()).hexdigest(),
            "size": 100, "mode": mode, "device": 7, "inode": 100 + ordinal,
        }
    return {
        "schema": native.INSTANCE_VERIFICATION_SCHEMA,
        "verified": True,
        "verified_at_ms": 1_500_000 + ordinal,
        "statement": statement,
        "receipt": reference(
            f"/var/lib/hepta/execution-native-systemd-{variant}-"
            "instance-receipt.json", f"{variant}:receipt", "0400"),
        "trust_policy": reference(
            str(native.INSTANCE_TRUST_POLICY), "trust", "0400"),
        "verification_key": reference(
            "/etc/heptatrader/fixture-native-instance.pub", "key", "0444"),
        "signature_verifier": reference(
            str(native.INSTANCE_OPENSSL), "openssl", "0755"),
        "key_id": "sha256/" + "a" * 64,
    }


def variant_report(variant: str, ordinal: int) -> dict[str, object]:
    identity = instance_identity(variant, ordinal)
    statement = cast(dict[str, Any], identity["statement"])
    runtime_inner = agent_runtime_result()
    runtime_inputs = [{
        "path": str(native.AGENT_OS_RUNTIME_INNER),
        "sha256": "a" * 64,
        "size": 100,
        "device": ordinal + 1,
        "inode": ordinal + 101,
        "mode": "0755",
    }]
    runtime_result_sha256 = hashlib.sha256(
        native.canonical_json(runtime_inner)).hexdigest()
    runtime_lifecycle_sha256 = hashlib.sha256(
        native.canonical_json(runtime_inner["lifecycle"])).hexdigest()
    return {
        "schema": native.SCHEMA,
        "passed": True,
        "certification_level":
            aggregate.VARIANT_CERTIFICATION_LEVEL,
        "variant": variant,
        "host": {
            "vm_type": "kvm",
            "systemd_pid1": True,
            "cgroup_v2_root": True,
            "docker_socket_absent": True,
            "kernel_release": "7.0.0-fixture",
        },
        "instance_identity": identity,
        "disposable_sentinel": {
            "contract": native.SENTINEL_HEADER,
            "root_owned": True,
            "mode": "0400",
            "single_link": True,
            "machine_id_bound": True,
            "boot_id_bound": True,
            "machine_id_sha256": format(ordinal + 1, "064x"),
            "boot_id_sha256": native.sha256_text(statement["boot_id"]),
            "vm_image_manifest_sha256": format(ordinal + 21, "064x"),
            "provisioning_manifest_sha256": format(ordinal + 31, "064x"),
            "platform_policy_sha256": "9" * 64,
            "clean_source_bundle_sha256": "6" * 64,
            "clean_source_manifest_sha256": "7" * 64,
            "clean_source_files_sha256": "8" * 64,
            "run_id_bound": True,
            "run_id_sha256": native.sha256_text(statement["run_id"]),
            "instance_challenge_bound": True,
            "instance_challenge_sha256":
                native.sha256_text(statement["challenge"]),
        },
        "network_isolation": {
            "loopback_present": True,
            "non_loopback_addresses": 0,
            "non_loopback_links_up": 0,
            "default_routes": 0,
            "non_loopback_routes": 0,
        },
        "agent_os": {
            "installation_manifest_sha256": "4" * 64,
            "installation_file_count": 24,
            "gateway_sha256": "5" * 64,
            "sessionctl_sha256": "6" * 64,
            "mcp_server_sha256": "7" * 64,
            "installation_preflight": True,
            "runtime_preflight_executed": True,
            "runtime_preflight_required": True,
            "runtime_gate_inputs_staged": True,
            "runtime_input_manifest_sha256": "b" * 64,
            "runtime_input_file_count": len(runtime_inputs),
            "runtime_artifacts_staged": False,
        },
        "agent_os_runtime": {
            "source": "real-native-vm-rootful-inner-process",
            "result_schema": native.AGENT_OS_RUNTIME_RESULT_SCHEMA,
            "result_parse_verified": True,
            "runtime_preflight_executed": True,
            "runtime_preflight_required": True,
            "runtime_input_manifest_sha256": "b" * 64,
            "runtime_input_records_sha256":
                native.input_manifest_sha256(runtime_inputs),
            "runtime_input_content_sha256":
                native.input_content_manifest_sha256(runtime_inputs),
            "runtime_inner_gate_sha256": "a" * 64,
            "runtime_result_sha256": runtime_result_sha256,
            "runtime_lifecycle_sha256": runtime_lifecycle_sha256,
            "identities": runtime_inner["identities"],
            "watch_tools": list(native.AGENT_OS_WATCH_TOOLS),
            "read_probes": list(native.AGENT_OS_READ_PROBES),
            "lifecycle": runtime_inner["lifecycle"],
            "checks": runtime_inner["checks"],
            "watch_session_revoked": True,
            "runtime_cleanup_complete": True,
            "ib_adapter_visible_during_runtime": False,
            "paper_authorized": False,
            "live_authorized": False,
            "real_broker_connections": 0,
            "paper_orders": 0,
            "inner": runtime_inner,
        },
        "runtime_inputs": runtime_inputs,
        "runtime_input_stability": True,
        "inputs": [{
            "path": "/usr/local/libexec/native-fixture",
            "sha256": "c" * 64,
            "size": 100,
            "device": ordinal + 1,
            "inode": ordinal + 201,
            "mode": "0755",
        }],
        "input_stability": True,
        "inner": inner_result(variant),
        "boundary": {
            "real_ibapi_elf_executed": False,
            "real_broker_connections": 0,
            "paper_orders": 0,
            "live_enabled": False,
            "paper_authorized": False,
            "agent_os_installation_preflight": True,
            "agent_os_runtime_preflight_executed": True,
            "agent_os_runtime_preflight_required": True,
            "agent_os_runtime_evidence_fabricated": False,
            "final_native_gate":
                "four_uid_watch_runtime_variant_requires_three_distinct_"
                "native_vm_runtime_aggregation",
        },
    }


def reports() -> dict[str, object]:
    return {
        variant: variant_report(variant, ordinal)
        for ordinal, variant in enumerate(aggregate.VARIANTS)
    }


def write_raw_reports(
        root: Path, evidence: dict[str, object],
        ) -> list[dict[str, object]]:
    bindings: list[dict[str, object]] = []
    for variant in aggregate.VARIANTS:
        path = root / f"execution-native-systemd-{variant}.json"
        payload = (
            json.dumps(
                evidence[variant], ensure_ascii=True,
                indent=2, sort_keys=True) + "\n").encode("ascii")
        path.write_bytes(payload)
        path.chmod(0o600)
        bindings.append({
            "variant": variant,
            "path": str(path.resolve(strict=True)),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
            "mode": "0600",
        })
    return bindings


def trusted_test_owners() -> mock._patch:
    return mock.patch.object(
        aggregate,
        "TRUSTED_REPORT_OWNER_PAIRS",
        TRUSTED_TEST_OWNER_PAIRS,
    )


class NativeSystemdAggregateFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        def reverify(path: Path, **_kwargs: object) -> dict[str, object]:
            name = Path(path).name
            for ordinal, variant in enumerate(aggregate.VARIANTS):
                if name == (
                        f"execution-native-systemd-{variant}-"
                        "instance-receipt.json"):
                    return instance_identity(variant, ordinal)
            raise native.NativeGateError("unknown fixture instance receipt")
        self.instance_reverify = mock.patch.object(
            aggregate.native, "verify_instance_receipt", side_effect=reverify)
        self.instance_reverify.start()

    def tearDown(self) -> None:
        self.instance_reverify.stop()

    def test_three_distinct_variants_aggregate(self) -> None:
        result = aggregate.aggregate_reports(reports())
        self.assertTrue(result["passed"])
        self.assertEqual(
            result["certification_level"],
            aggregate.RUNTIME_CERTIFICATION_LEVEL)
        self.assertEqual(result["common_closure"]["distinct_native_vms"], 3)
        self.assertEqual(result["aggregation_inputs"], [])
        self.assertTrue(
            result["boundary"][
                "native_agent_os_installation_gate_satisfied"])
        self.assertTrue(
            result["boundary"]["agent_os_runtime_preflight_executed"])
        self.assertFalse(result["boundary"]["paper_authorized"])

    def test_duplicate_vm_identity_fails_closed(self) -> None:
        evidence = reports()
        stub = cast(dict[str, Any], evidence["stub"])
        real = cast(dict[str, Any], evidence["real"])
        stub_sentinel = cast(dict[str, Any], stub["disposable_sentinel"])
        real_sentinel = cast(dict[str, Any], real["disposable_sentinel"])
        stub_sentinel["machine_id_sha256"] = real_sentinel[
            "machine_id_sha256"]
        with self.assertRaisesRegex(aggregate.AggregateError, "distinct"):
            aggregate.aggregate_reports(evidence)

    def test_distinct_guest_ids_cannot_hide_duplicate_external_instance(self) -> None:
        evidence = reports()
        duplicate_uuid = cast(
            dict[str, Any], evidence["real"])["instance_identity"][  # type: ignore[index]
                "statement"]["instance_uuid"]
        for variant in ("sandbox", "stub"):
            evidence[variant]["instance_identity"][  # type: ignore[index]
                "statement"]["instance_uuid"] = duplicate_uuid

        def reverify(path: Path, **_kwargs: object) -> dict[str, Any]:
            name = Path(path).name
            variant = next(
                item for item in aggregate.VARIANTS
                if name.startswith(f"execution-native-systemd-{item}-"))
            return deepcopy(cast(
                dict[str, Any], evidence[variant])["instance_identity"])

        with mock.patch.object(
                aggregate.native, "verify_instance_receipt",
                side_effect=reverify):
            with self.assertRaisesRegex(
                    aggregate.AggregateError, "external instances"):
                aggregate.aggregate_reports(evidence)

    def test_replayed_external_instance_challenge_fails_closed(self) -> None:
        evidence = reports()
        real = cast(dict[str, Any], evidence["real"])
        sandbox = cast(dict[str, Any], evidence["sandbox"])
        challenge = real["instance_identity"]["statement"]["challenge"]
        sandbox["instance_identity"]["statement"]["challenge"] = challenge
        sandbox["disposable_sentinel"]["instance_challenge_sha256"] = \
            native.sha256_text(challenge)

        def reverify(path: Path, **_kwargs: object) -> dict[str, Any]:
            name = Path(path).name
            variant = next(
                item for item in aggregate.VARIANTS
                if name.startswith(f"execution-native-systemd-{item}-"))
            return deepcopy(cast(
                dict[str, Any], evidence[variant])["instance_identity"])

        with mock.patch.object(
                aggregate.native, "verify_instance_receipt",
                side_effect=reverify):
            with self.assertRaisesRegex(
                    aggregate.AggregateError, "reuse.*challenge"):
                aggregate.aggregate_reports(evidence)

    def test_platform_policy_drift_fails_closed(self) -> None:
        evidence = reports()
        evidence["sandbox"]["disposable_sentinel"][  # type: ignore[index]
            "platform_policy_sha256"] = "8" * 64
        with self.assertRaisesRegex(
                aggregate.AggregateError, "disagree|lineage mismatch"):
            aggregate.aggregate_reports(evidence)

    def test_clean_source_drift_fails_closed(self) -> None:
        evidence = reports()
        evidence["sandbox"]["disposable_sentinel"][  # type: ignore[index]
            "clean_source_bundle_sha256"] = "5" * 64
        with self.assertRaisesRegex(
                aggregate.AggregateError, "disagree|lineage mismatch"):
            aggregate.aggregate_reports(evidence)

    def test_formal_binary_drift_fails_closed(self) -> None:
        evidence = reports()
        evidence["stub"]["inner"]["metrics"][  # type: ignore[index]
            "formal_ibapi_sha256"] = "1" * 64
        with self.assertRaisesRegex(aggregate.AggregateError, "disagree"):
            aggregate.aggregate_reports(evidence)

    def test_sandbox_must_execute_distinct_broker_free_probe(self) -> None:
        evidence = reports()
        evidence["sandbox"]["inner"]["metrics"][  # type: ignore[index]
            "executed_ib_path_sha256"] = "e" * 64
        with self.assertRaises(aggregate.AggregateError):
            aggregate.aggregate_reports(evidence)

    def test_variant_boundary_drift_fails_closed(self) -> None:
        evidence = deepcopy(reports())
        evidence["real"]["boundary"]["paper_orders"] = 1  # type: ignore[index]
        with self.assertRaisesRegex(aggregate.AggregateError, "boundary"):
            aggregate.aggregate_reports(evidence)

    def test_agent_os_installation_drift_fails_closed(self) -> None:
        evidence = deepcopy(reports())
        evidence["sandbox"]["agent_os"][  # type: ignore[index]
            "gateway_sha256"] = "1" * 64
        with self.assertRaisesRegex(aggregate.AggregateError, "Agent OS"):
            aggregate.aggregate_reports(evidence)

    def test_runtime_preflight_cannot_be_forged(self) -> None:
        evidence = deepcopy(reports())
        evidence["real"]["agent_os"][  # type: ignore[index]
            "runtime_preflight_executed"] = False
        with self.assertRaisesRegex(aggregate.AggregateError, "Agent OS"):
            aggregate.aggregate_reports(evidence)

    def test_runtime_result_identity_and_tool_surface_forgery_fail_closed(
            self) -> None:
        cases = []
        identity = deepcopy(reports())
        identity["real"]["agent_os_runtime"]["inner"]["identities"][  # type: ignore[index]
            "agent_uid"] = 0
        cases.append(identity)
        tool_result = deepcopy(reports())
        tool_result["real"]["agent_os_runtime"]["inner"]["checks"][  # type: ignore[index]
            "uid_2004_exact_watch_tool_list"] = False
        cases.append(tool_result)
        advertised = deepcopy(reports())
        advertised["real"]["agent_os_runtime"]["watch_tools"][0] = (  # type: ignore[index]
            "trade.place_order")
        cases.append(advertised)
        for evidence in cases:
            with self.subTest(evidence=evidence):
                with self.assertRaises(aggregate.AggregateError):
                    aggregate.aggregate_reports(evidence)

    def test_runtime_input_identity_and_digest_drift_fail_closed(self) -> None:
        evidence = deepcopy(reports())
        evidence["real"]["runtime_inputs"][0]["inode"] += 1  # type: ignore[index]
        with self.assertRaisesRegex(aggregate.AggregateError, "input digest"):
            aggregate.aggregate_reports(evidence)

    def test_runtime_aggregate_parser_rejects_install_only_and_accepts_v6(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-aggregate-",
                dir=PROTECTED_TEST_TEMP_PARENT) as temporary:
            root = Path(temporary)
            evidence = reports()
            aggregation_inputs = write_raw_reports(root, evidence)
            result = aggregate.aggregate_reports(
                evidence, aggregation_inputs)
            self.assertIs(aggregate.parse_runtime_aggregate(result), result)
            old = deepcopy(result)
            old["schema"] = "hepta.execution-native-systemd-aggregate.v5"
            old["certification_level"] = (
                "native-disposable-vm-agent-os-installation-rootful-systemd")
            with self.assertRaisesRegex(aggregate.AggregateError, "top-level"):
                aggregate.parse_runtime_aggregate(old)

    def test_file_backed_verifier_rebuilds_all_raw_variant_semantics(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-aggregate-",
                dir=PROTECTED_TEST_TEMP_PARENT) as temporary:
            root = Path(temporary)
            evidence = reports()
            bindings = write_raw_reports(root, evidence)
            result = aggregate.aggregate_reports(evidence, bindings)
            with trusted_test_owners():
                self.assertIs(
                    aggregate.verify_runtime_aggregate(result), result)

                hostile_cases = []
                host = deepcopy(result)
                host["variants"]["sandbox"]["vm_type"] = "xen"
                hostile_cases.append(host)
                kernel = deepcopy(result)
                kernel["variants"]["sandbox"]["kernel_release"] = "different"
                hostile_cases.append(kernel)
                kind = deepcopy(result)
                for variant in aggregate.VARIANTS:
                    kind["variants"][variant]["executed_kind"] = "invented"
                hostile_cases.append(kind)
                binary = deepcopy(result)
                binary["variants"]["stub"][
                    "executed_ib_path_sha256"] = "1" * 64
                hostile_cases.append(binary)
                runtime = deepcopy(result)
                runtime["variants"]["real"][
                    "agent_os_runtime_result_sha256"] = "2" * 64
                hostile_cases.append(runtime)
                lifecycle = deepcopy(result)
                lifecycle["variants"]["real"][
                    "agent_os_runtime_lifecycle_sha256"] = "3" * 64
                hostile_cases.append(lifecycle)
                for hostile in hostile_cases:
                    with self.subTest(hostile=hostile):
                        with self.assertRaises(aggregate.AggregateError):
                            aggregate.verify_runtime_aggregate(hostile)

    def test_file_backed_verifier_rejects_raw_report_mutation(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-aggregate-",
                dir=PROTECTED_TEST_TEMP_PARENT) as temporary:
            root = Path(temporary)
            evidence = reports()
            bindings = write_raw_reports(root, evidence)
            result = aggregate.aggregate_reports(evidence, bindings)
            raw = root / "execution-native-systemd-real.json"
            changed = deepcopy(evidence["real"])
            changed["host"]["kernel_release"] = "changed"  # type: ignore[index]
            raw.write_text(
                json.dumps(changed, indent=2, sort_keys=True) + "\n",
                encoding="ascii")
            raw.chmod(0o600)
            with trusted_test_owners():
                with self.assertRaisesRegex(
                        aggregate.AggregateError, "binding drift"):
                    aggregate.verify_runtime_aggregate(result)

    def test_file_backed_verifier_rejects_zero_counter_type_confusion(
            self) -> None:
        mutations = (
            ("network-boolean", lambda value: value["real"][
                "network_isolation"].__setitem__(
                    "non_loopback_addresses", False)),
            ("runtime-float", lambda value: value["real"][
                "agent_os_runtime"].__setitem__(
                    "real_broker_connections", 0.0)),
            ("boundary-negative-float", lambda value: value["real"][
                "boundary"].__setitem__("paper_orders", -0.0)),
        )
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-aggregate-",
                dir=PROTECTED_TEST_TEMP_PARENT) as temporary:
            parent = Path(temporary)
            for name, mutate in mutations:
                with self.subTest(name=name):
                    root = parent / name
                    root.mkdir(mode=0o700)
                    legitimate = reports()
                    bindings = write_raw_reports(root, legitimate)
                    result = aggregate.aggregate_reports(
                        legitimate, bindings)
                    hostile = deepcopy(legitimate)
                    mutate(hostile)  # type: ignore[arg-type]
                    hostile_bindings = write_raw_reports(root, hostile)
                    forged = deepcopy(result)
                    forged["aggregation_inputs"] = hostile_bindings
                    with trusted_test_owners():
                        with self.assertRaises(aggregate.AggregateError):
                            aggregate.verify_runtime_aggregate(forged)

            root = parent / "runtime-inner-float"
            root.mkdir(mode=0o700)
            legitimate = reports()
            bindings = write_raw_reports(root, legitimate)
            result = aggregate.aggregate_reports(legitimate, bindings)
            hostile = deepcopy(legitimate)
            runtime = hostile["real"]["agent_os_runtime"]  # type: ignore[index]
            runtime_inner = runtime["inner"]  # type: ignore[index]
            runtime_inner["boundary"][  # type: ignore[index]
                "real_broker_connections"] = 0.0
            runtime["runtime_result_sha256"] = hashlib.sha256(  # type: ignore[index]
                native.canonical_json(runtime_inner)).hexdigest()
            forged = deepcopy(result)
            forged["aggregation_inputs"] = write_raw_reports(root, hostile)
            with trusted_test_owners():
                with self.assertRaises(aggregate.AggregateError):
                    aggregate.verify_runtime_aggregate(forged)

    def test_raw_variant_files_reject_links_modes_and_writable_ancestor(
            self) -> None:
        attacks = ("symlink", "hardlink", "wrong-mode", "writable-ancestor")
        for attack in attacks:
            with self.subTest(attack=attack), tempfile.TemporaryDirectory(
                    prefix="hepta-native-aggregate-",
                    dir=PROTECTED_TEST_TEMP_PARENT) as temporary:
                root = Path(temporary)
                evidence = reports()
                bindings = write_raw_reports(root, evidence)
                result = aggregate.aggregate_reports(evidence, bindings)
                raw = root / "execution-native-systemd-real.json"
                if attack in {"symlink", "hardlink"}:
                    alternate = root / "alternate-real.json"
                    alternate.write_bytes(raw.read_bytes())
                    alternate.chmod(0o600)
                    raw.unlink()
                    if attack == "symlink":
                        raw.symlink_to(alternate)
                    else:
                        os.link(alternate, raw)
                elif attack == "wrong-mode":
                    raw.chmod(0o640)
                else:
                    root.chmod(0o777)
                try:
                    with trusted_test_owners():
                        with self.assertRaises(aggregate.AggregateError):
                            aggregate.verify_runtime_aggregate(result)
                finally:
                    if attack == "writable-ancestor":
                        root.chmod(0o700)

    def test_raw_variant_bindings_fail_closed_on_path_and_metadata_drift(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-aggregate-",
                dir=PROTECTED_TEST_TEMP_PARENT) as temporary:
            root = Path(temporary)
            evidence = reports()
            bindings = write_raw_reports(root, evidence)
            result = aggregate.aggregate_reports(evidence, bindings)
            hostile_cases = []
            relative = deepcopy(result)
            relative["aggregation_inputs"][0]["path"] = (
                "execution-native-systemd-real.json")
            hostile_cases.append(relative)
            digest = deepcopy(result)
            digest["aggregation_inputs"][0]["sha256"] = "0" * 64
            hostile_cases.append(digest)
            size = deepcopy(result)
            size["aggregation_inputs"][0]["size"] += 1
            hostile_cases.append(size)
            mode = deepcopy(result)
            mode["aggregation_inputs"][0]["mode"] = "0644"
            hostile_cases.append(mode)
            wrong_name = deepcopy(result)
            wrong_name["aggregation_inputs"][0]["path"] = str(
                root / "execution-native-systemd-invented.json")
            hostile_cases.append(wrong_name)
            swapped = deepcopy(result)
            swapped["aggregation_inputs"][0]["path"], \
                swapped["aggregation_inputs"][1]["path"] = (
                    swapped["aggregation_inputs"][1]["path"],
                    swapped["aggregation_inputs"][0]["path"],
                )
            hostile_cases.append(swapped)
            with trusted_test_owners():
                for hostile in hostile_cases:
                    with self.subTest(hostile=hostile):
                        with self.assertRaises(aggregate.AggregateError):
                            aggregate.verify_runtime_aggregate(hostile)

    def test_cli_output_self_parses_and_rebuilds_raw_reports(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-aggregate-",
                dir=PROTECTED_TEST_TEMP_PARENT) as temporary:
            root = Path(temporary)
            evidence = reports()
            write_raw_reports(root, evidence)
            output = root / "execution-native-systemd-aggregate.json"
            arguments = []
            for variant in aggregate.VARIANTS:
                arguments.extend([
                    f"--{variant}-report",
                    str(root / f"execution-native-systemd-{variant}.json"),
                ])
            arguments.extend(["--report", str(output)])
            with trusted_test_owners():
                self.assertEqual(aggregate.main(arguments), 0)
                document = json.loads(output.read_bytes())
                self.assertIs(
                    aggregate.verify_runtime_aggregate(document), document)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(
                [item["variant"]
                 for item in document["aggregation_inputs"]],
                list(aggregate.VARIANTS))


if __name__ == "__main__":
    unittest.main(verbosity=2)
