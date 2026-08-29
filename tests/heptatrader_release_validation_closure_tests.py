#!/usr/bin/env python3

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))

import build_heptatrader_release_validation_closure as builder  # noqa: E402
import verify_heptatrader_evidence_ingestion_receipt as receipt_verifier  # noqa: E402
import verify_heptatrader_evidence_set as set_verifier  # noqa: E402
import verify_heptatrader_release_validation_closure as verifier  # noqa: E402


ROUND = 95
RELEASE = "0.1.0-beta.1-round95"
NOW = datetime(2026, 8, 3, 4, 0, tzinfo=timezone.utc)


def utc(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


class ReleaseValidationTests(unittest.TestCase):
    def _input_manifest(self) -> dict[str, object]:
        directory = "heptatrader-round95-engineering-artifacts-v1"
        roots = {
            "delivery-artifact-root": f"{directory}/delivery",
            "verification-artifact-root": f"{directory}/verification",
        }
        components = {
            role: f"{directory}/components/{role}.json"
            for role in builder.COMPONENT_ROLES
        }
        return builder.build_input_manifest(
            round_number=ROUND,
            release_version=RELEASE,
            generated_at=utc(NOW),
            roots=roots,
            components=components)

    def _lane_documents(self):
        matrix_cases = []
        runner_cases = []
        matrix_inputs: dict[str, dict[str, object]] = {}
        runner_inputs: dict[str, dict[str, object]] = {}
        agent = {"sha256": "a" * 64, "size": 10, "mode": "0600"}
        strict = {"sha256": "b" * 64, "size": 20, "mode": "0600"}
        for label in sorted(builder.verification.MATRIX_LABELS):
            matrix_cases.append({
                "name": label,
                "passed": True,
                "returncode": 0,
                "selection": [],
                "expected": 17,
                "observed": 17,
            })
            cache = {
                "name": f"{label}.cmake-cache",
                "path": f"raw/{label}.cache",
                "sha256": "c" * 64,
                "size": 30,
                "mode": "0600",
            }
            matrix_inputs[cache["name"]] = deepcopy(cache)
            runner_inputs[cache["name"]] = deepcopy(cache)
            if label in builder.verification.NO_GIT_LABELS:
                source = {
                    "name": f"{label}.source-manifest",
                    "path": f"raw/{label}.source.json",
                    **agent,
                }
                matrix_inputs[source["name"]] = deepcopy(source)
                runner_inputs[source["name"]] = deepcopy(source)
            runner_cases.append({
                "name": label,
                "passed": True,
                "cmake": {
                    "build_type": "Release",
                    "policy": {
                        "build_testing": True,
                        "ibapi_enabled":
                            label in builder.verification.IBAPI_ON_LABELS,
                        "legacy_enabled": False,
                        "build_type": "Release",
                        "sanitizer": None,
                        "coverage": False,
                    },
                },
            })
        for label in sorted(
                builder.verification.RUNNER_LABELS -
                builder.verification.MATRIX_LABELS):
            runner_cases.append({"name": label, "passed": True, "cmake": {}})
            runner_inputs[f"{label}.cmake-cache"] = {
                "name": f"{label}.cmake-cache",
                "path": f"raw/{label}.cache",
                "sha256": "d" * 64,
                "size": 40,
                "mode": "0600",
            }
        for label in builder.verification.STRICT_SOURCE_LABELS:
            runner_inputs[f"{label}.source-manifest"] = {
                "name": f"{label}.source-manifest",
                "path": f"raw/{label}.source.json",
                **strict,
            }
        return (
            {"cases": matrix_cases}, {"cases": runner_cases},
            matrix_inputs, runner_inputs, agent, strict)

    @staticmethod
    def _write_private(path: Path, value: object) -> None:
        path.write_bytes(builder.canonical_json(value) + b"\n")
        path.chmod(0o600)

    def _closure_fixture(self, root: Path):
        evidence = root / "evidence"
        evidence.mkdir(mode=0o700)
        directory = evidence / (
            "heptatrader-round95-engineering-artifacts-v1")
        directory.mkdir(mode=0o700)
        input_path = directory / builder.INPUT_MANIFEST_NAME
        self._write_private(input_path, self._input_manifest())
        receipt_paths = {}
        for role in (
                "receipt", "request", "trust_policy", "index",
                "evidence_set_manifest", "retention_policy"):
            path = root / f"{role}.json"
            self._write_private(path, {"role": role})
            receipt_paths[role] = path
        captures = {
            role: builder._capture_file(
                path, role, require_trusted_parent=False)
            for role, path in receipt_paths.items()
        }
        input_capture = builder._capture_file(input_path, "input")
        baseline = {
            "path": "delivery/source-baseline-manifest.json",
            "sha256": "1" * 64,
            "size": 100,
            "mode": "0600",
        }
        local = {
            "profile": builder.PROFILE,
            "round": ROUND,
            "release_version": RELEASE,
            "artifact_directory": directory.name,
            "input_manifest_sha256": input_capture.snapshot.sha256,
            "source_baseline": baseline,
            "source_lineage": {"git_head": "a" * 40},
            "verification": {
                "matrix_generated_at": utc(NOW - timedelta(hours=1)),
                "runner_generated_at": utc(NOW - timedelta(hours=1)),
                "fresh_until": utc(NOW + timedelta(hours=23)),
                "maximum_age_seconds":
                    builder.MAX_VERIFICATION_AGE_SECONDS,
                "lanes": [],
            },
            "delivery": {"four_soaks_eight_rounds_verified": True},
            "native": {
                "schema": "hepta.execution-native-systemd-aggregate.v6",
                "certification_level":
                    "native-disposable-vm-agent-os-watch-runtime-rootful-systemd",
                "distinct_native_vms": 3,
                "distinct_provisioner_attested_instances": 3,
                "external_instance_receipts_verified": True,
                "runtime_contract_verified": True,
            },
            "critical_files": [{
                "role": "release-input-manifest",
                "path": input_path.relative_to(evidence).as_posix(),
                "sha256": input_capture.snapshot.sha256,
                "size": input_capture.snapshot.size,
                "mode": input_capture.snapshot.mode,
            }],
            "safety_boundaries": deepcopy(builder.SAFETY_BOUNDARIES),
        }
        retention = {
            "schema": receipt_verifier.VERIFICATION_SCHEMA,
            "trust_scope": "system-production",
            "signature_status": "verified",
            "retention_status": "current-policy-satisfied",
            "current_policy_satisfied_object_count": 1,
            "statement_sha256": "2" * 64,
            "request_sha256": "3" * 64,
            "index_sha256": "4" * 64,
            "evidence_set_manifest_sha256": "5" * 64,
            "trust_policy_sha256": "6" * 64,
            "evidence_set_id": "round95-certification",
            "profile": builder.PROFILE,
            "role_count": 1,
            "production_contract_verified": True,
        }
        inputs = builder.ReceiptInputs(
            receipt=receipt_paths["receipt"],
            request=receipt_paths["request"],
            trust_policy=receipt_paths["trust_policy"],
            index=receipt_paths["index"],
            evidence_set_manifest=receipt_paths["evidence_set_manifest"],
            retention_policy=receipt_paths["retention_policy"])
        return evidence, input_path, inputs, local, retention, captures

    def test_round95_input_manifest_is_generic_and_non_authorizing(self) -> None:
        manifest = self._input_manifest()
        self.assertEqual(manifest["round"], 95)
        self.assertEqual(manifest["release_version"], RELEASE)
        self.assertEqual(
            manifest["safety_boundaries"], builder.SAFETY_BOUNDARIES)
        self.assertTrue(all(
            value is False for value in
            manifest["safety_boundaries"].values()))

    def test_input_manifest_rejects_cross_round_and_missing_roles(self) -> None:
        manifest = self._input_manifest()
        manifest["round"] = 94
        manifest["release_version"] = "0.1.0-beta.1-round94"
        with self.assertRaisesRegex(
                builder.ReleaseValidationError, "directory round drift"):
            builder.validate_input_manifest(manifest)
        manifest = self._input_manifest()
        del manifest["components"]["runtime-package-manifest"]
        with self.assertRaisesRegex(
                builder.ReleaseValidationError, "roles are incomplete"):
            builder.validate_input_manifest(manifest)

    def test_release_ctest_freshness_rejects_expired_and_future(self) -> None:
        with self.assertRaisesRegex(
                builder.ReleaseValidationError, "expired"):
            builder._verification_freshness(
                utc(NOW), utc(NOW - timedelta(days=2)),
                utc(NOW - timedelta(days=2)), NOW)
        with self.assertRaisesRegex(
                builder.ReleaseValidationError, "future-dated"):
            builder._verification_freshness(
                utc(NOW), utc(NOW + timedelta(hours=1)),
                utc(NOW), NOW)

    def test_release_lanes_bind_full_ctest_ibapi_off_on_and_sources(self) -> None:
        matrix, runner, matrix_inputs, runner_inputs, agent, strict = (
            self._lane_documents())
        summary = builder._lane_summary(
            matrix, runner, matrix_inputs, runner_inputs, agent, strict)
        self.assertEqual(
            {record["name"] for record in summary},
            builder.verification.MATRIX_LABELS)
        self.assertTrue(all(
            record["build_type"] == "Release" and
            record["build_testing"] is True and
            record["expected_tests"] == record["observed_tests"]
            for record in summary))
        forged = deepcopy(runner)
        first = next(
            case for case in forged["cases"]
            if case["name"] in builder.verification.MATRIX_LABELS)
        first["cmake"]["build_type"] = "Debug"
        with self.assertRaisesRegex(
                builder.ReleaseValidationError, "lane contract drift"):
            builder._lane_summary(
                matrix, forged, matrix_inputs, runner_inputs, agent, strict)
        drifted = deepcopy(matrix_inputs)
        label = "agent-no-git-ibapi-off.source-manifest"
        drifted[label]["sha256"] = "f" * 64
        with self.assertRaisesRegex(
                builder.ReleaseValidationError, "source lineage drift"):
            builder._lane_summary(
                matrix, runner, drifted, runner_inputs, agent, strict)

    def test_native_instance_receipt_original_is_critical_evidence(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-release-native-receipt-") as temporary:
            root = Path(temporary)
            evidence = root / "evidence"
            artifact = evidence / (
                "heptatrader-round95-engineering-artifacts-v1")
            artifact.mkdir(parents=True, mode=0o700)
            receipt_path = artifact / (
                "execution-native-systemd-real-instance-receipt.json")
            body_sha256 = "a" * 64
            self._write_private(
                receipt_path, {"body_sha256": body_sha256})
            receipt = builder._capture_file(
                receipt_path, "native receipt")
            metadata = receipt_path.stat()
            receipt_reference = {
                "path": str(receipt_path.resolve(strict=True)),
                "file_sha256": receipt.snapshot.sha256,
                "body_sha256": body_sha256,
                "size": receipt.snapshot.size,
                "mode": receipt.snapshot.mode,
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
            }
            raw_path = artifact / "execution-native-systemd-real.json"
            self._write_private(raw_path, {
                "instance_identity": {"receipt": receipt_reference},
            })
            raw = builder._capture_file(raw_path, "native raw report")
            parsed = {"variants": {"real": {
                "instance_receipt_file_sha256": receipt.snapshot.sha256,
                "instance_receipt_body_sha256": body_sha256,
            }}}
            captured = builder._capture_native_instance_receipt(
                raw, parsed, "real")
            self.assertEqual(captured, receipt)
            self.assertEqual(
                builder._critical_record(
                    "native-instance-receipt-real", captured, evidence)["path"],
                receipt_path.relative_to(evidence).as_posix())
            self.assertIn(
                "native-instance-receipt-real",
                builder.CORE_EVIDENCE_ROLES)

            crossed = deepcopy(parsed)
            crossed["variants"]["real"][
                "instance_receipt_file_sha256"] = "f" * 64
            with self.assertRaisesRegex(
                    builder.ReleaseValidationError, "binding drift"):
                builder._capture_native_instance_receipt(
                    raw, crossed, "real")

    def test_production_receipt_lineage_cannot_cross_round(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-release-receipt-") as temporary:
            root = Path(temporary)
            evidence, _input, inputs, local, _retention, _captures = (
                self._closure_fixture(root))
            manifest_digest = hashlib.sha256(
                inputs.evidence_set_manifest.read_bytes()).hexdigest()
            report = {
                "schema": receipt_verifier.VERIFICATION_SCHEMA,
                "version": 2,
                "production_trust": True,
                "trust_scope": "system-production",
                "signature_status": "verified",
                "retention_status": "current-policy-satisfied",
                "evidence_set_bound": True,
                "evidence_set_certified": True,
                "source_files_deleted": False,
                "source_removal_authorized": False,
                "paper_authorized": False,
                "live_authorized": False,
                "current_policy_satisfied_object_count": 1,
                "objects": [{"status": "active-at-verification-time"}],
                "statement_sha256": "1" * 64,
                "request_sha256": "2" * 64,
                "index_sha256": "3" * 64,
                "evidence_set_manifest_sha256": manifest_digest,
                "trust_policy_sha256": "4" * 64,
                "evidence_set": {
                    "profile": builder.PROFILE,
                    "round": 94,
                    "release_version": "0.1.0-beta.1-round94",
                    "source_baseline": local["source_baseline"],
                    "manifest_sha256": manifest_digest,
                    "evidence_set_id": "round94-certification",
                },
            }
            with mock.patch.object(
                    receipt_verifier, "verify_receipt", return_value=report):
                with self.assertRaisesRegex(
                        builder.ReleaseValidationError,
                        "receipt release/evidence-set lineage drift"):
                    builder._receipt_summary(inputs, evidence, local)

    def test_closure_is_go_candidate_but_all_authorities_remain_false(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-release-closure-") as temporary:
            root = Path(temporary)
            evidence, input_path, inputs, local, retention, captures = (
                self._closure_fixture(root))
            with (mock.patch.object(
                    builder, "verify_local_input_manifest",
                    return_value=local),
                  mock.patch.object(
                    builder, "_receipt_summary",
                    return_value=(retention, captures))):
                closure = builder.build_closure(
                    input_path, evidence, inputs, evaluated_at=NOW)
            self.assertEqual(closure["decision"], "GO")
            self.assertEqual(
                closure["candidate_scope"], builder.CANDIDATE_SCOPE)
            self.assertTrue(all(
                value is False
                for value in closure["safety_boundaries"].values()))

    def test_verifier_rejects_authority_tamper_expiry_and_drift(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-release-verify-") as temporary:
            root = Path(temporary)
            evidence, input_path, inputs, local, retention, captures = (
                self._closure_fixture(root))
            with (mock.patch.object(
                    builder, "verify_local_input_manifest",
                    return_value=local),
                  mock.patch.object(
                    builder, "_receipt_summary",
                    return_value=(retention, captures))):
                closure = builder.build_closure(
                    input_path, evidence, inputs, evaluated_at=NOW)
            path = root / "closure.json"
            self._write_private(path, closure)
            with mock.patch.object(
                    verifier.builder, "build_closure", return_value=closure):
                report = verifier.verify(
                    path, verification_time=NOW,
                    _allow_test_time=True)
            self.assertEqual(report["decision"], "GO")
            self.assertFalse(report["paper_authorized"])

            forged = deepcopy(closure)
            forged["safety_boundaries"]["paper_authorized"] = True
            self._write_private(path, forged)
            with self.assertRaisesRegex(
                    verifier.ReleaseValidationVerificationError,
                    "boundary drift"):
                verifier.verify(
                    path, verification_time=NOW,
                    _allow_test_time=True)

            self._write_private(path, closure)
            with self.assertRaisesRegex(
                    verifier.ReleaseValidationVerificationError, "expired"):
                verifier.verify(
                    path,
                    verification_time=NOW + timedelta(days=2),
                    _allow_test_time=True)

            different = deepcopy(closure)
            different["local_evidence"]["source_lineage"] = {
                "git_head": "f" * 40}
            with (mock.patch.object(
                    verifier.builder, "build_closure", return_value=different),
                  self.assertRaisesRegex(
                    verifier.ReleaseValidationVerificationError,
                    "differs from causal reconstruction")):
                verifier.verify(
                    path, verification_time=NOW,
                    _allow_test_time=True)

    def test_input_manifest_publication_is_canonical_and_no_replace(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-release-publish-") as temporary:
            root = Path(temporary)
            parent = root / (
                "heptatrader-round95-engineering-artifacts-v1")
            parent.mkdir(mode=0o700)
            output = parent / builder.INPUT_MANIFEST_NAME
            builder.publish_input_manifest(output, self._input_manifest())
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                output.read_bytes(),
                builder.canonical_json(self._input_manifest()) + b"\n")
            with self.assertRaisesRegex(
                    builder.ReleaseValidationError, "refusing to replace"):
                builder.publish_input_manifest(output, self._input_manifest())


if __name__ == "__main__":
    unittest.main(verbosity=2)
