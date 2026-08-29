#!/usr/bin/env python3

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))

import build_heptatrader_delivery_closure as closure_builder  # noqa: E402
import run_execution_gateway_soak as soak_runner  # noqa: E402
import verify_heptatrader_delivery_closure as closure_verifier  # noqa: E402


ROUND = 35
RELEASE_VERSION = "heptatrader-v1-round35"
GENERATED_AT = "2026-07-24T00:00:00Z"
GIT_HEAD = "a" * 40


class DeliveryClosureFixture:
    """Small, internally consistent seven-artifact delivery lineage."""

    def __init__(
            self, root: Path, *, round_number: int = ROUND,
            release_version: str = RELEASE_VERSION) -> None:
        self.root = root
        self.round = round_number
        self.release_version = release_version
        self.artifact_root = root / "artifacts"
        artifact_directory = self.artifact_root / "sealed"
        artifact_directory.mkdir(parents=True)
        self.artifact_root.chmod(0o700)
        artifact_directory.chmod(0o700)
        self.paths = {
            role: (
                f"sealed/{index:02d}-{role}.tar"
                if role == "strict-source-bundle"
                else f"sealed/{index:02d}-{role}.json"
            )
            for index, role in enumerate(
                closure_builder.REQUIRED_ARTIFACT_ROLES)
        }

        runner_bytes = b"fixture soak runner\n"
        runner = {
            "mode": "0644",
            "path": "scripts/run_execution_gateway_soak.py",
            "sha256": "sha256:" + hashlib.sha256(
                runner_bytes).hexdigest(),
            "size": len(runner_bytes),
        }
        source_records = [runner]
        self.source_manifest = {
            "file_count": len(source_records),
            "files": source_records,
            "sha256": "sha256:" + hashlib.sha256(
                closure_builder.canonical_json(source_records)).hexdigest(),
        }
        baseline = {
            "blocked_reason": "VERSION_CONTROL_COMMIT_REQUIRED",
            "clean_checkout_certified": False,
            "excluded_unsafe_tree": "compat/unsafe-direct-broker",
            "generated_at": GENERATED_AT,
            "git_head": GIT_HEAD,
            "live_authorized": False,
            "paper_authorized": False,
            "release_authorized": False,
            "schema": closure_builder.BASELINE_SCHEMA,
            "source_baseline_frozen": True,
            "source_manifest": self.source_manifest,
            "version": self.release_version,
            "worktree_status_entry_count": 1,
        }
        self.write_role("source-baseline-manifest", baseline)

        baseline_path = self.path_for("source-baseline-manifest")
        baseline_bytes = baseline_path.read_bytes()
        bundled_baseline_path = (
            "release-manifests/heptatrader-agent-os-v" +
            self.release_version + "/manifest.json")
        bundle_files = [{
            "mode": "0644",
            "path": bundled_baseline_path,
            "sha256": hashlib.sha256(baseline_bytes).hexdigest(),
            "size": len(baseline_bytes),
        }]
        bundle_manifest = {
            "file_count": len(bundle_files),
            "files": bundle_files,
            "files_sha256": "sha256:" + hashlib.sha256(
                closure_builder.canonical_json(bundle_files)).hexdigest(),
            "git_head": GIT_HEAD,
            "live_authorized": False,
            "paper_authorized": False,
            "schema": closure_builder.CLEAN_SOURCE_SCHEMA,
            "security_manifest_file_count":
                self.source_manifest["file_count"],
            "security_manifest_sha256":
                self.source_manifest["sha256"],
            "version": self.release_version,
        }
        self.write_role("strict-source-bundle-manifest", bundle_manifest)
        bundle = self.path_for("strict-source-bundle")
        bundle.write_bytes(b"fixture strict-source tar payload\n")
        bundle.chmod(0o600)

        for role, ibapi_enabled, source_bundle_present in (
                ("worktree-soak-ibapi-off", False, False),
                ("worktree-soak-ibapi-on", True, False),
                ("no-git-soak-ibapi-off", False, True),
                ("no-git-soak-ibapi-on", True, True)):
            self.write_role(
                role,
                self.make_soak(
                    ibapi_enabled=ibapi_enabled,
                    source_bundle_present=source_bundle_present,
                    runner=runner,
                    bundle_manifest=bundle_manifest,
                ),
            )

        self.closure = self.build()
        self.closure_path = root / "delivery-closure.json"
        closure_builder.write_closure(
            self.closure_path, self.closure)

    def path_for(self, role: str) -> Path:
        return self.artifact_root / self.paths[role]

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.write_bytes(closure_builder.canonical_json(value) + b"\n")
        path.chmod(0o600)

    def write_role(self, role: str, value: object) -> None:
        self._write_json(self.path_for(role), value)

    def read_role(self, role: str) -> dict[str, object]:
        value = json.loads(
            self.path_for(role).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise AssertionError("fixture JSON role is not an object")
        return value

    def make_soak(
            self, *, ibapi_enabled: bool, source_bundle_present: bool,
            runner: dict[str, object],
            bundle_manifest: dict[str, object]) -> dict[str, object]:
        build_dir = (
            f"build-{'nogit' if source_bundle_present else 'worktree'}-"
            f"{'on' if ibapi_enabled else 'off'}")
        binary_inputs = {
            name: {
                "mode": "0755",
                "path": f"{build_dir}/tests/{name}",
                "sha256": "sha256:" + hashlib.sha256(
                    f"{build_dir}:{name}".encode("ascii")).hexdigest(),
                "size": 1024 + index,
            }
            for index, name in enumerate(soak_runner.SOAK_BINARY_NAMES)
        }
        contracts = deepcopy(list(soak_runner.SOAK_EVIDENCE_CONTRACTS))
        checks = []
        for name, contract in zip(
                soak_runner.SOAK_BINARY_NAMES, contracts, strict=True):
            pinned = binary_inputs[name]
            output = f"{contract['prefix']} fixture\n".encode("utf-8")
            checks.append({
                "binary": pinned["path"],
                "duration_ms": 1,
                "evidence_contract_satisfied": True,
                "evidence_fields": deepcopy(contract["fields"]),
                "evidence_line_count": 1,
                "evidence_observed": True,
                "evidence_parse_error": "",
                "evidence_prefix": contract["prefix"],
                "exit_code": 0,
                "expected_evidence_fields": deepcopy(contract["fields"]),
                "high_water": {
                    "fds": 1,
                    "processes":
                        soak_runner.SOAK_MINIMUM_OBSERVED_PROCESSES[name],
                    "rss_kb": 1,
                    "threads": 1,
                },
                "mismatched_evidence_fields": {},
                "missing_evidence_fields": [],
                "output_limit_exceeded": False,
                "output_sha256": "sha256:" + hashlib.sha256(output).hexdigest(),
                "output_size_bytes": len(output),
                "output_tail_redacted": output.decode("utf-8"),
                "passed": True,
                "pinned_binary": deepcopy(pinned),
                "post_cleanup_process_group_members": [],
                "process_group_cleanup_succeeded": True,
                "process_resources_within_limit": True,
                "remaining_process_group_members": [],
                "timed_out": False,
                "unexpected_evidence_fields": [],
            })
        rounds = [{
            "checks": deepcopy(checks),
            "no_orphan_descendants": True,
            "passed": True,
            "process_tree_observed": True,
            "resource_growth_within_limit": True,
            "round": index,
            "runner_growth": {"fds": 0, "rss_kb": 0, "threads": 0},
        } for index in range(1, closure_builder.EXPECTED_SOAK_ROUNDS + 1)]
        build_configuration = {
            "build_type": "Release",
            "cmake_cache": {
                "mode": "0644",
                "path": f"{build_dir}/CMakeCache.txt",
                "sha256": "sha256:" + hashlib.sha256(
                    f"{build_dir}:cache".encode("ascii")).hexdigest(),
                "size": 1024,
            },
            "compile_commands": "not-enabled",
            "cxx_compiler_name": "c++",
            "generator": "Ninja",
            "ibapi_enabled": ibapi_enabled,
            "legacy_0dte_bridge_enabled": False,
            "legacy_monolith_built": False,
            "legacy_simulator_built": False,
        }
        if source_bundle_present:
            manifest_path = self.path_for(
                "strict-source-bundle-manifest")
            manifest_bytes = manifest_path.read_bytes()
            source_bundle: dict[str, object] | None = {
                "file_count": bundle_manifest["file_count"],
                "files_sha256": bundle_manifest["files_sha256"],
                "git_head": GIT_HEAD,
                "manifest": {
                    "mode": "0644",
                    "path": ".hepta/source-bundle-manifest.json",
                    "sha256": "sha256:" + hashlib.sha256(
                        manifest_bytes).hexdigest(),
                    "size": len(manifest_bytes),
                },
            }
            tracked_diff = closure_builder.EMPTY_SHA256
            tracked_status = closure_builder.EMPTY_SHA256
        else:
            source_bundle = None
            tracked_diff = "sha256:" + hashlib.sha256(
                b"fixture dirty diff").hexdigest()
            tracked_status = "sha256:" + hashlib.sha256(
                b"fixture dirty status").hexdigest()
        input_provenance = {
            "build_configuration": build_configuration,
            "runner": deepcopy(runner),
            "source_bundle": source_bundle,
            "source_manifest": deepcopy(self.source_manifest),
            "tracked_diff_sha256": tracked_diff,
            "tracked_worktree_status_sha256": tracked_status,
        }
        snapshot = {
            "binaries": deepcopy(binary_inputs),
            "git_head": GIT_HEAD,
            "provenance": input_provenance,
        }
        return {
            "all_invariants_certified": True,
            "binary_inputs": binary_inputs,
            "build_dir": build_dir,
            "completed_rounds": closure_builder.EXPECTED_SOAK_ROUNDS,
            "evidence_contracts": contracts,
            "expected_invariants_per_round": dict(
                soak_runner.SOAK_EXPECTED_INVARIANTS),
            "generated_at_unix_ms": 1784822400000,
            "git_head": GIT_HEAD,
            "limits": dict(soak_runner.SOAK_DEFAULT_LIMITS),
            "minimum_observed_processes": dict(
                soak_runner.SOAK_MINIMUM_OBSERVED_PROCESSES),
            "passed": True,
            "provenance": {
                "inputs_stable": True,
                "post_run": deepcopy(snapshot),
                "post_snapshot_error": "",
                "pre_run": snapshot,
                "source_binary_binding":
                    soak_runner.SOAK_SOURCE_BINARY_BINDING,
            },
            "requested_rounds": closure_builder.EXPECTED_SOAK_ROUNDS,
            "rounds": rounds,
            "schema": soak_runner.SOAK_SCHEMA,
            "soak_profile": "release",
        }

    def clean_source_result(self) -> dict[str, object]:
        bundle = self.path_for("strict-source-bundle").read_bytes()
        manifest = self.path_for(
            "strict-source-bundle-manifest").read_bytes()
        return {
            "bundle_sha256": hashlib.sha256(bundle).hexdigest(),
            "git_head": GIT_HEAD,
            "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
            "version": self.release_version,
        }

    def build(
            self, paths: dict[str, str] | None = None,
    ) -> dict[str, object]:
        with mock.patch.object(
                closure_builder.clean_source_verifier,
                "verify_bundle",
                return_value=self.clean_source_result()):
            return closure_builder.build_closure(
                self.artifact_root,
                paths or self.paths,
                round_number=self.round,
                release_version=self.release_version,
                generated_at=GENERATED_AT,
            )

    def verify(self, path: Path | None = None) -> dict[str, object]:
        with mock.patch.object(
                closure_builder.clean_source_verifier,
                "verify_bundle",
                return_value=self.clean_source_result()):
            return closure_verifier.verify(
                path or self.closure_path, self.artifact_root)


class DeliveryClosureTests(unittest.TestCase):
    def test_clean_committed_source_baseline_is_valid(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-clean-baseline-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            baseline = json.loads(
                fixture.path_for(
                    "source-baseline-manifest").read_text(
                        encoding="utf-8"))
            baseline["clean_checkout_certified"] = True
            baseline["worktree_status_entry_count"] = 0
            baseline["blocked_reason"] = None
            validated = closure_builder._validate_baseline(
                baseline,
                round_number=ROUND,
                release_version=RELEASE_VERSION)
            self.assertTrue(validated["clean_checkout_certified"])
            self.assertIsNone(validated["blocked_reason"])
            self.assertFalse(validated["release_authorized"])

    def test_build_publish_and_verify_semantic_round_trip(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-closure-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            report = fixture.verify()

            self.assertEqual(
                fixture.closure["artifact_roles"],
                list(closure_builder.REQUIRED_ARTIFACT_ROLES))
            self.assertEqual(
                [item["role"] for item in fixture.closure["artifacts"]],
                list(closure_builder.REQUIRED_ARTIFACT_ROLES))
            self.assertIs(fixture.closure["passed"], True)
            self.assertEqual(
                fixture.closure["passed_scope"],
                closure_builder.LOCAL_OFFLINE_SCOPE)
            self.assertTrue(all(
                value is False for value in
                fixture.closure["safety_boundaries"].values()))
            self.assertEqual(
                fixture.closure["production_trust"],
                closure_builder.PRODUCTION_TRUST_BOUNDARY)

            closure_bytes = fixture.closure_path.read_bytes()
            self.assertEqual(
                closure_bytes,
                closure_builder.canonical_json(fixture.closure) + b"\n")
            self.assertEqual(
                stat.S_IMODE(fixture.closure_path.stat().st_mode), 0o600)
            self.assertEqual(fixture.closure_path.stat().st_nlink, 1)

            self.assertEqual(
                report["schema"], closure_verifier.VERIFICATION_SCHEMA)
            self.assertEqual(report["status"], "verified")
            self.assertEqual(report["round"], ROUND)
            self.assertEqual(report["release_version"], RELEASE_VERSION)
            self.assertEqual(report["git_head"], GIT_HEAD)
            self.assertEqual(
                report["source_manifest_sha256"],
                fixture.source_manifest["sha256"])
            self.assertEqual(
                report["artifact_count"],
                len(closure_builder.REQUIRED_ARTIFACT_ROLES))
            self.assertEqual(
                report["closure_sha256"],
                hashlib.sha256(closure_bytes).hexdigest())
            self.assertEqual(
                report["production_trust_status"], "pending-external")
            self.assertEqual(report["production_trust_key_count"], 0)
            for field in (
                    "broker_connection_performed",
                    "order_placement_performed",
                    "paper_authorized",
                    "live_authorized",
                    "source_files_deleted",
                    "source_removal_authorized",
                    "real_systemd_certified",
                    "real_ib_certified",
                    "object_store_ingestion_receipt_certified",
                    "retention_enforcement_certified",
                    "release_authorized",
                    "clean_checkout_certified"):
                self.assertIs(report[field], False)
            self.assertEqual(
                report["blocked_reason"],
                "VERSION_CONTROL_COMMIT_REQUIRED")

    def test_arbitrary_seven_files_cannot_form_a_delivery_closure(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-arbitrary-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            for role in closure_builder.REQUIRED_ARTIFACT_ROLES:
                path = fixture.path_for(role)
                path.write_bytes(
                    b"arbitrary tar bytes"
                    if role == "strict-source-bundle"
                    else b"{}\n")
                path.chmod(0o600)
            with self.assertRaisesRegex(
                    closure_builder.DeliveryClosureError,
                    "baseline fields"):
                fixture.build()

    def test_ibapi_on_off_role_swap_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-role-swap-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            paths = dict(fixture.paths)
            off = "worktree-soak-ibapi-off"
            on = "worktree-soak-ibapi-on"
            paths[off], paths[on] = paths[on], paths[off]
            with self.assertRaisesRegex(
                    closure_builder.DeliveryClosureError,
                    "build profile drift"):
                fixture.build(paths)

    def test_worktree_and_no_git_provenance_cannot_be_cross_wired(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-provenance-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            worktree = fixture.read_role("worktree-soak-ibapi-off")
            no_git = fixture.read_role("no-git-soak-ibapi-off")
            worktree["provenance"] = deepcopy(no_git["provenance"])
            fixture.write_role("worktree-soak-ibapi-off", worktree)
            with self.assertRaisesRegex(
                    closure_builder.DeliveryClosureError,
                    "path drift|unexpectedly claims a source bundle"):
                fixture.build()

    def test_soak_nested_contract_fail_open_mutations_are_rejected(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-soak-contract-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            role = "worktree-soak-ibapi-off"
            original = fixture.read_role(role)
            first_binary = soak_runner.SOAK_BINARY_NAMES[0]

            def remove_binary_digest(report: dict[str, object]) -> None:
                report["binary_inputs"][first_binary].pop("sha256")
                for snapshot_name in ("pre_run", "post_run"):
                    report["provenance"][snapshot_name][
                        "binaries"][first_binary].pop("sha256")

            def remove_contract_binding_evidence(
                    report: dict[str, object]) -> None:
                prefix = "ib_authoritative_fault_matrix_evidence:"
                for contract in report["evidence_contracts"]:
                    if contract["prefix"] == prefix:
                        contract["fields"].pop(
                            "contract_binding_fail_closed")
                for round_result in report["rounds"]:
                    for check in round_result["checks"]:
                        if check["evidence_prefix"] == prefix:
                            check["expected_evidence_fields"].pop(
                                "contract_binding_fail_closed")
                            check["evidence_fields"].pop(
                                "contract_binding_fail_closed")

            mutations = (
                (
                    "v10 schema replay",
                    lambda report: report.__setitem__(
                        "schema", "hepta.execution-gateway-soak.v10"),
                ),
                (
                    "source binary binding",
                    lambda report: report["provenance"].__setitem__(
                        "source_binary_binding", "FORGED"),
                ),
                (
                    "limits type",
                    lambda report: report.__setitem__("limits", "FORGED"),
                ),
                (
                    "timestamp boolean",
                    lambda report: report.__setitem__(
                        "generated_at_unix_ms", True),
                ),
                (
                    "minimum process count",
                    lambda report: report[
                        "minimum_observed_processes"].__setitem__(
                            first_binary, -999),
                ),
                (
                    "omitted v11 invariant",
                    lambda report: report[
                        "expected_invariants_per_round"].pop(
                            "ib_paper_contract_binding_fails_closed"),
                ),
                ("binary record digest", remove_binary_digest),
                (
                    "omitted v11 contract-binding evidence",
                    remove_contract_binding_evidence,
                ),
                (
                    "evidence contract shape",
                    lambda report: report["evidence_contracts"][0].__setitem__(
                        "fields", ["FORGED"]),
                ),
                (
                    "round fields",
                    lambda report: report["rounds"][0].pop("runner_growth"),
                ),
                (
                    "process resource result",
                    lambda report: report["rounds"][0]["checks"][0].__setitem__(
                        "process_resources_within_limit", False),
                ),
                (
                    "high water limit",
                    lambda report: report["rounds"][0]["checks"][0][
                        "high_water"].__setitem__(
                            "fds",
                            soak_runner.SOAK_DEFAULT_LIMITS[
                                "max_process_tree_fds"] + 1),
                ),
            )
            for label, mutate in mutations:
                with self.subTest(label=label):
                    forged = deepcopy(original)
                    mutate(forged)
                    fixture.write_role(role, forged)
                    with self.assertRaises(
                            closure_builder.DeliveryClosureError):
                        fixture.build()
            fixture.write_role(role, original)

    def test_baseline_and_bundle_manifest_digests_cannot_be_cross_wired(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-digest-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            manifest = fixture.read_role(
                "strict-source-bundle-manifest")
            manifest["security_manifest_sha256"] = (
                manifest["files_sha256"])
            fixture.write_role(
                "strict-source-bundle-manifest", manifest)
            with self.assertRaisesRegex(
                    closure_builder.DeliveryClosureError,
                    "bundle/baseline lineage drift"):
                fixture.build()

        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-digest-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            manifest = fixture.read_role(
                "strict-source-bundle-manifest")
            manifest["files"][0]["sha256"] = hashlib.sha256(
                fixture.path_for(
                    "strict-source-bundle-manifest").read_bytes()
            ).hexdigest()
            fixture.write_role(
                "strict-source-bundle-manifest", manifest)
            with self.assertRaisesRegex(
                    closure_builder.DeliveryClosureError,
                    "external and bundled source baselines differ"):
                fixture.build()

    def test_exact_schema_rejects_field_and_type_drift(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-schema-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            cases: list[tuple[str, dict[str, object]]] = []

            extra = deepcopy(fixture.closure)
            extra["unexpected"] = False
            cases.append(("extra top-level field", extra))

            missing = deepcopy(fixture.closure)
            del missing["generated_at"]
            cases.append(("missing top-level field", missing))

            boolean_version = deepcopy(fixture.closure)
            boolean_version["version"] = True
            cases.append(("boolean version", boolean_version))

            failed = deepcopy(fixture.closure)
            failed["passed"] = False
            cases.append(("failed closure", failed))

            scope_drift = deepcopy(fixture.closure)
            scope_drift["passed_scope"] = "production"
            cases.append(("scope drift", scope_drift))

            artifact_extra = deepcopy(fixture.closure)
            artifact_extra["artifacts"][0]["unexpected"] = False
            cases.append(("extra artifact field", artifact_extra))

            for label, forged in cases:
                with self.subTest(label=label):
                    with self.assertRaises(
                            closure_builder.DeliveryClosureError):
                        closure_builder.validate_contract_structure(forged)

    def test_fixed_roles_are_complete_unique_and_canonical(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-roles-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))

            drifted_roles = deepcopy(fixture.closure)
            drifted_roles["artifact_roles"] = list(reversed(
                drifted_roles["artifact_roles"]))

            drifted_bindings = deepcopy(fixture.closure)
            drifted_bindings["artifacts"] = list(reversed(
                drifted_bindings["artifacts"]))

            renamed_role = deepcopy(fixture.closure)
            renamed_role["artifacts"][0]["role"] = "custom-role"

            duplicate_path = deepcopy(fixture.closure)
            duplicate_path["artifacts"][1]["path"] = (
                duplicate_path["artifacts"][0]["path"])

            for label, forged in (
                    ("role declaration order", drifted_roles),
                    ("binding order", drifted_bindings),
                    ("renamed role", renamed_role),
                    ("duplicate path", duplicate_path)):
                with self.subTest(label=label):
                    with self.assertRaises(
                            closure_builder.DeliveryClosureError):
                        closure_builder.validate_contract_structure(forged)

            missing = dict(fixture.paths)
            missing.pop(closure_builder.REQUIRED_ARTIFACT_ROLES[0])
            with self.assertRaisesRegex(
                    closure_builder.DeliveryClosureError,
                    "artifact roles must be fixed"):
                closure_builder._artifact_mapping(missing)

            extra = dict(fixture.paths)
            extra["unexpected-role"] = "sealed/unexpected.json"
            with self.assertRaisesRegex(
                    closure_builder.DeliveryClosureError,
                    "artifact roles must be fixed"):
                closure_builder._artifact_mapping(extra)

    def test_safety_boundaries_cannot_be_elevated(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-safety-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            for field in closure_builder.SAFETY_BOUNDARIES:
                forged = deepcopy(fixture.closure)
                forged["safety_boundaries"][field] = True
                with self.subTest(field=field):
                    with self.assertRaisesRegex(
                            closure_builder.DeliveryClosureError,
                            "safety boundary"):
                        closure_builder.validate_contract_structure(forged)

    def test_production_trust_stays_pending_external_with_zero_keys(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-trust-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            mutations = (
                ("status", "configured-external"),
                ("key_count", 1),
                ("key_count", True),
                ("object_store_ingestion_receipt_certified", True),
                ("retention_enforcement_certified", True),
                ("source_removal_authorized", True),
            )
            for field, value in mutations:
                forged = deepcopy(fixture.closure)
                forged["production_trust"][field] = value
                with self.subTest(field=field, value=value):
                    with self.assertRaises(
                            closure_builder.DeliveryClosureError):
                        closure_builder.validate_contract_structure(forged)

    def test_artifact_paths_must_be_normalized_relative_posix_paths(
            self) -> None:
        self.assertEqual(
            closure_builder.normalized_relative_path(
                "nested/artifact.json"),
            "nested/artifact.json")
        self.assertEqual(
            closure_builder.normalized_relative_path(
                Path("nested/artifact.json")),
            "nested/artifact.json")

        for value in (
                "", ".", "..", "../escape", "/absolute",
                "nested/../escape", "./artifact.json",
                "double//separator", "nested/./artifact.json",
                "windows\\artifact.json", "nul\0artifact.json",
                "control\nartifact.json",
                "non-ascii-\N{LATIN SMALL LETTER E WITH ACUTE}.json", 1):
            with self.subTest(value=value):
                with self.assertRaises(
                        closure_builder.DeliveryClosureError):
                    closure_builder.normalized_relative_path(value)

        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-path-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            paths = dict(fixture.paths)
            paths[closure_builder.REQUIRED_ARTIFACT_ROLES[0]] = "../escape"
            with self.assertRaises(
                    closure_builder.DeliveryClosureError):
                fixture.build(paths)

    def test_symlink_artifacts_and_closures_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-symlink-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            first, second = closure_builder.REQUIRED_ARTIFACT_ROLES[:2]
            artifact = fixture.path_for(first)
            artifact.unlink()
            artifact.symlink_to(Path(fixture.paths[second]).name)
            with self.assertRaisesRegex(
                    closure_builder.DeliveryClosureError,
                    "no-follow regular file|unsafe"):
                fixture.build()

        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-symlink-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            alias = fixture.root / "delivery-closure-link.json"
            alias.symlink_to(fixture.closure_path.name)
            with self.assertRaisesRegex(
                    closure_verifier.DeliveryClosureVerificationError,
                    "no-follow regular file|unsafe"):
                fixture.verify(alias)

    def test_group_or_world_writable_inputs_are_rejected(self) -> None:
        for mode in (0o620, 0o602):
            with self.subTest(kind="artifact", mode=oct(mode)):
                with tempfile.TemporaryDirectory(
                        prefix="hepta-delivery-writable-") as temporary:
                    fixture = DeliveryClosureFixture(Path(temporary))
                    role = closure_builder.REQUIRED_ARTIFACT_ROLES[0]
                    fixture.path_for(role).chmod(mode)
                    with self.assertRaisesRegex(
                            closure_builder.DeliveryClosureError,
                            "group- or world-writable"):
                        fixture.build()

            with self.subTest(kind="closure", mode=oct(mode)):
                with tempfile.TemporaryDirectory(
                        prefix="hepta-delivery-writable-") as temporary:
                    fixture = DeliveryClosureFixture(Path(temporary))
                    fixture.closure_path.chmod(mode)
                    with self.assertRaisesRegex(
                            closure_verifier.
                            DeliveryClosureVerificationError,
                            "group- or world-writable"):
                        fixture.verify()

    def test_world_writable_security_boundary_directories_are_rejected(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-writable-dir-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            fixture.artifact_root.chmod(0o755)
            (fixture.artifact_root / "sealed").chmod(0o777)
            with self.assertRaisesRegex(
                    closure_builder.DeliveryClosureError,
                    "trusted non-writable directory"):
                fixture.build()

        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-writable-dir-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            output_directory = fixture.root / "world-output"
            output_directory.mkdir()
            output_directory.chmod(0o777)
            output = output_directory / "closure.json"
            with self.assertRaisesRegex(
                    closure_builder.DeliveryClosureError,
                    "output directory"):
                closure_builder.write_closure(output, fixture.closure)
            self.assertFalse(output.exists())

        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-writable-dir-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            closure_directory = fixture.root / "world-closure"
            closure_directory.mkdir()
            closure_directory.chmod(0o777)
            closure = closure_directory / "closure.json"
            fixture.closure_path.rename(closure)
            with self.assertRaisesRegex(
                    closure_verifier.DeliveryClosureVerificationError,
                    "file parent"):
                fixture.verify(closure)

    def test_hardlinked_artifacts_and_closures_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-hardlink-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            role = closure_builder.REQUIRED_ARTIFACT_ROLES[0]
            artifact = fixture.path_for(role)
            os.link(artifact, fixture.root / "artifact-hardlink.json")
            with self.assertRaisesRegex(
                    closure_builder.DeliveryClosureError,
                    "exactly one hard link"):
                fixture.build()

        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-hardlink-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            os.link(
                fixture.closure_path,
                fixture.root / "delivery-closure-hardlink.json")
            with self.assertRaisesRegex(
                    closure_verifier.DeliveryClosureVerificationError,
                    "exactly one hard link"):
                fixture.verify()

    def test_duplicate_json_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-duplicate-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            schema = (
                b'"schema":"heptatrader.delivery-closure.v1"')
            payload = closure_builder.canonical_json(fixture.closure)
            self.assertEqual(payload.count(schema), 1)
            payload = payload.replace(
                schema, schema + b"," + schema, 1) + b"\n"
            duplicate = fixture.root / "duplicate-closure.json"
            duplicate.write_bytes(payload)
            duplicate.chmod(0o600)

            with self.assertRaisesRegex(
                    closure_verifier.DeliveryClosureVerificationError,
                    "duplicate JSON key"):
                fixture.verify(duplicate)

    def test_noncanonical_closure_json_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-noncanonical-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            noncanonical = fixture.root / "pretty-closure.json"
            noncanonical.write_text(
                json.dumps(
                    fixture.closure, indent=2, sort_keys=False) + "\n",
                encoding="ascii")
            noncanonical.chmod(0o600)
            with self.assertRaisesRegex(
                    closure_verifier.DeliveryClosureVerificationError,
                    "not canonical JSON"):
                fixture.verify(noncanonical)

    def test_in_read_mutation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-toctou-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            role = closure_builder.REQUIRED_ARTIFACT_ROLES[0]
            relative = fixture.paths[role]
            artifact = fixture.path_for(role)
            original = artifact.read_bytes()
            real_read = os.read
            mutated = False

            def read_then_mutate(
                    descriptor: int, count: int) -> bytes:
                nonlocal mutated
                chunk = real_read(descriptor, count)
                if chunk and not mutated:
                    mutated = True
                    artifact.write_bytes(b"X" * len(original))
                    artifact.chmod(0o600)
                return chunk

            with mock.patch.object(
                    closure_builder.os, "read",
                    side_effect=read_then_mutate):
                with self.assertRaisesRegex(
                        closure_builder.DeliveryClosureError,
                        "changed during read"):
                    closure_builder.stable_artifact(
                        fixture.artifact_root, relative)
            self.assertTrue(mutated)

    def test_artifact_replacement_during_semantic_validation_is_rejected(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-replace-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            first_role = closure_builder.REQUIRED_ARTIFACT_ROLES[0]
            artifact = fixture.path_for(first_role)
            real_validate = closure_builder.validate_delivery_evidence
            replaced = False

            def validate_then_replace(*args, **kwargs):
                nonlocal replaced
                result = real_validate(*args, **kwargs)
                replacement = artifact.with_name(
                    artifact.name + ".replacement")
                replacement.write_bytes(artifact.read_bytes())
                replacement.chmod(0o600)
                os.replace(replacement, artifact)
                replaced = True
                return result

            with mock.patch.object(
                    closure_builder, "validate_delivery_evidence",
                    side_effect=validate_then_replace):
                with self.assertRaisesRegex(
                        closure_builder.DeliveryClosureError,
                        "changed during evidence validation"):
                    fixture.build()
            self.assertTrue(replaced)

    def test_closure_replacement_during_verification_is_rejected(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-verify-replace-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            real_validate = closure_builder.validate_delivery_evidence
            replaced = False

            def validate_then_replace(*args, **kwargs):
                nonlocal replaced
                result = real_validate(*args, **kwargs)
                replacement = (
                    fixture.root / "replacement-closure.json")
                replacement.write_bytes(
                    fixture.closure_path.read_bytes())
                replacement.chmod(0o600)
                os.replace(replacement, fixture.closure_path)
                replaced = True
                return result

            with mock.patch.object(
                    closure_builder, "validate_delivery_evidence",
                    side_effect=validate_then_replace):
                with self.assertRaisesRegex(
                        closure_verifier.
                        DeliveryClosureVerificationError,
                        "closure changed across verification"):
                    fixture.verify()
            self.assertTrue(replaced)

    def test_publication_is_private_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-publish-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            output = fixture.root / "published-closure.json"
            returned = closure_builder.write_closure(
                output, fixture.closure)
            before = output.read_bytes()

            self.assertEqual(returned, output)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(output.stat().st_nlink, 1)
            self.assertEqual(
                list(fixture.root.glob(
                    ".hepta-delivery-closure-*.tmp")),
                [])

            with self.assertRaisesRegex(
                    closure_builder.DeliveryClosureError,
                    "refusing to overwrite"):
                closure_builder.write_closure(output, fixture.closure)
            self.assertEqual(output.read_bytes(), before)

            target = fixture.root / "outside-target.json"
            target.write_bytes(b"do-not-overwrite\n")
            alias = fixture.root / "existing-symlink.json"
            alias.symlink_to(target.name)
            with self.assertRaisesRegex(
                    closure_builder.DeliveryClosureError,
                    "refusing to overwrite"):
                closure_builder.write_closure(alias, fixture.closure)
            self.assertTrue(alias.is_symlink())
            self.assertEqual(target.read_bytes(), b"do-not-overwrite\n")

    def test_directory_fsync_failure_removes_published_output(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-fsync-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            output = fixture.root / "fsync-failure.json"
            real_fsync = os.fsync

            def reject_directory_fsync(descriptor: int) -> None:
                if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                    raise OSError("fixture directory fsync failure")
                real_fsync(descriptor)

            with mock.patch.object(
                    closure_builder.os, "fsync",
                    side_effect=reject_directory_fsync):
                with self.assertRaisesRegex(
                        closure_builder.DeliveryClosureError,
                        "atomic closure publication failed"):
                    closure_builder.write_closure(
                        output, fixture.closure)
            self.assertFalse(output.exists())
            self.assertEqual(
                list(fixture.root.glob(
                    ".hepta-delivery-closure-*.tmp")),
                [])

    def test_final_path_replacement_during_directory_fsync_is_rejected(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-final-race-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            output = fixture.root / "fsync-race.json"
            replacement = fixture.root / "attacker-replacement.json"
            replacement.write_bytes(b"attacker replacement\n")
            replacement.chmod(0o600)
            real_fsync = os.fsync
            replaced = False

            def replace_final_during_fsync(descriptor: int) -> None:
                nonlocal replaced
                if (stat.S_ISDIR(os.fstat(descriptor).st_mode) and
                        not replaced):
                    os.replace(replacement, output)
                    replaced = True
                real_fsync(descriptor)

            with mock.patch.object(
                    closure_builder.os, "fsync",
                    side_effect=replace_final_during_fsync):
                with self.assertRaisesRegex(
                        closure_builder.DeliveryClosureError,
                        "changed during directory fsync"):
                    closure_builder.write_closure(
                        output, fixture.closure)
            self.assertTrue(replaced)
            self.assertEqual(output.read_bytes(), b"attacker replacement\n")
            self.assertEqual(
                list(fixture.root.glob(
                    ".hepta-delivery-closure-*.tmp")),
                [])

    def test_temporary_name_collision_preserves_existing_file(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-temp-collision-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            token = "a" * 32
            existing = (
                fixture.root /
                f".hepta-delivery-closure-{token}.tmp")
            existing.write_bytes(b"pre-existing\n")
            existing.chmod(0o600)
            output = fixture.root / "collision-output.json"

            with mock.patch.object(
                    closure_builder.secrets, "token_hex",
                    return_value=token):
                with self.assertRaisesRegex(
                        closure_builder.DeliveryClosureError,
                        "atomic closure publication failed"):
                    closure_builder.write_closure(
                        output, fixture.closure)
            self.assertEqual(existing.read_bytes(), b"pre-existing\n")
            self.assertFalse(output.exists())

    def test_anchored_directory_does_not_reclassify_body_oserror(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-anchor-body-") as temporary:
            with self.assertRaisesRegex(
                    OSError, "fixture body failure"):
                with closure_builder._anchored_directory(temporary):
                    raise OSError("fixture body failure")

    def test_output_ancestor_replacement_during_fsync_cleans_both_paths(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-delivery-ancestor-") as temporary:
            fixture = DeliveryClosureFixture(Path(temporary))
            output_directory = fixture.root / "publish"
            output_directory.mkdir(mode=0o700)
            moved_directory = fixture.root / "publish-moved"
            output = output_directory / "delivery-closure.json"
            real_fsync = os.fsync
            replaced = False

            def replace_ancestor_during_fsync(
                    descriptor: int) -> None:
                nonlocal replaced
                if (stat.S_ISDIR(os.fstat(descriptor).st_mode) and
                        not replaced):
                    output_directory.rename(moved_directory)
                    output_directory.mkdir(mode=0o700)
                    replaced = True
                real_fsync(descriptor)

            with mock.patch.object(
                    closure_builder.os, "fsync",
                    side_effect=replace_ancestor_during_fsync):
                with self.assertRaisesRegex(
                        closure_builder.DeliveryClosureError,
                        "directory changed while anchored"):
                    closure_builder.write_closure(
                        output, fixture.closure)
            self.assertTrue(replaced)
            self.assertFalse(output.exists())
            self.assertFalse(
                (moved_directory / output.name).exists())
            self.assertEqual(
                list(output_directory.glob(
                    ".hepta-delivery-closure-*.tmp")),
                [])
            self.assertEqual(
                list(moved_directory.glob(
                    ".hepta-delivery-closure-*.tmp")),
                [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
