#!/usr/bin/env python3

"""Focused fail-closed tests for the Round36 final certifier."""

from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))
sys.path.insert(0, str(REPOSITORY))

import build_heptatrader_round36_certification as builder  # noqa: E402
import verify_heptatrader_round36_certification as verifier  # noqa: E402
from tests.aggregate_hepta_execution_native_systemd_gate_fixture import (  # noqa: E402
    reports as native_variant_reports,
)


def _write(path: Path, payload: bytes, mode: int = 0o600) -> None:
    path.write_bytes(payload)
    path.chmod(mode)


def _binding(path: Path, role: str) -> dict[str, Any]:
    payload = path.read_bytes()
    return {
        "role": role,
        "path": path.name,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
        "mode": f"{stat.S_IMODE(path.stat().st_mode):04o}",
    }


class CertificationFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.chmod(0o700)
        self.git_head = "1" * 40
        self.source_manifest_sha256 = "sha256:" + "2" * 64
        self.source_files_sha256 = "3" * 64

        self.artifact_root = root / builder.SOURCE_ARTIFACT_ROOT_NAME
        self.artifact_root.mkdir(mode=0o700)
        self.artifacts: list[dict[str, Any]] = []
        for role in builder.closure_contract.REQUIRED_ARTIFACT_ROLES:
            suffix = ".tar" if role == "strict-source-bundle" else ".json"
            path = self.artifact_root / f"{role}{suffix}"
            if role == "strict-source-bundle-manifest":
                payload = builder.canonical_json({
                    "version": builder.SOURCE_RELEASE_VERSION,
                    "git_head": self.git_head,
                    "security_manifest_sha256":
                        self.source_manifest_sha256,
                    "files_sha256": self.source_files_sha256,
                }) + b"\n"
            elif role == "strict-source-bundle":
                payload = b"fixture deterministic source archive\n"
            else:
                payload = (
                    builder.canonical_json(
                        {"fixture_role": role, "passed": True}) + b"\n")
            _write(path, payload)
            self.artifacts.append(_binding(path, role))

        self.closure_document = {
            "schema": builder.closure_contract.CLOSURE_SCHEMA,
            "version": builder.closure_contract.CLOSURE_VERSION,
            "project_id": builder.PROJECT_ID,
            "round": builder.SOURCE_ROUND,
            "release_version": builder.SOURCE_RELEASE_VERSION,
            "generated_at": "2026-07-24T00:00:00Z",
            "passed": True,
            "passed_scope": builder.closure_contract.LOCAL_OFFLINE_SCOPE,
            "artifact_roles":
                list(builder.closure_contract.REQUIRED_ARTIFACT_ROLES),
            "artifacts": self.artifacts,
            "safety_boundaries":
                dict(builder.closure_contract.SAFETY_BOUNDARIES),
            "production_trust":
                dict(builder.closure_contract.PRODUCTION_TRUST_BOUNDARY),
        }
        self.closure_path = root / builder.SOURCE_CLOSURE_NAME
        _write(
            self.closure_path,
            builder.canonical_json(self.closure_document) + b"\n")
        self.closure_report = self._closure_report()

        self.native_variant_root = root / "native-variant-reports"
        self.native_variant_root.mkdir(mode=0o700)
        self.native_reports = native_variant_reports()
        by_role = {item["role"]: item for item in self.artifacts}
        for report in self.native_reports.values():
            sentinel = report["disposable_sentinel"]  # type: ignore[index]
            sentinel["clean_source_bundle_sha256"] = by_role[
                "strict-source-bundle"]["sha256"]
            sentinel["clean_source_manifest_sha256"] = by_role[
                "strict-source-bundle-manifest"]["sha256"]
            sentinel["clean_source_files_sha256"] = self.source_files_sha256
            statement = report["instance_identity"][  # type: ignore[index]
                "statement"]
            statement["source_lineage"] = {
                "bundle_sha256": sentinel["clean_source_bundle_sha256"],
                "manifest_sha256": sentinel["clean_source_manifest_sha256"],
                "files_sha256": sentinel["clean_source_files_sha256"],
            }
        self.native_path = root / "execution-native-systemd-aggregate.json"
        self._rewrite_native_evidence()

        self.receipt_root = root / "receipt-inputs"
        self.receipt_root.mkdir(mode=0o700)
        self.evidence_root = root / "evidence-root"
        self.evidence_root.mkdir(mode=0o700)
        self.receipt_paths = {
            role: self.receipt_root / f"{role}.json"
            for role in builder.RECEIPT_INPUT_ROLES
        }
        for role, path in self.receipt_paths.items():
            if role != "evidence_set_manifest":
                _write(
                    path,
                    builder.canonical_json(
                        {"fixture": role, "production": False}) + b"\n")
        self._write_evidence_set_manifest()
        self.receipt_inputs = builder.ReceiptInputs(
            receipt=self.receipt_paths["receipt"],
            request=self.receipt_paths["request"],
            trust_policy=self.receipt_paths["trust_policy"],
            index=self.receipt_paths["index"],
            evidence_root=self.evidence_root,
            retention_policy=self.receipt_paths["retention_policy"],
            evidence_set_manifest=
                self.receipt_paths["evidence_set_manifest"],
        )
        self.receipt_report = self._receipt_report()

    def _closure_report(self) -> dict[str, Any]:
        by_role = {item["role"]: item for item in self.artifacts}
        return {
            "round": builder.SOURCE_ROUND,
            "release_version": builder.SOURCE_RELEASE_VERSION,
            "passed": True,
            "passed_scope": builder.closure_contract.LOCAL_OFFLINE_SCOPE,
            "closure_sha256":
                hashlib.sha256(self.closure_path.read_bytes()).hexdigest(),
            "artifact_roles":
                list(builder.closure_contract.REQUIRED_ARTIFACT_ROLES),
            "git_head": self.git_head,
            "source_manifest_sha256": self.source_manifest_sha256,
            "source_manifest_file_count": 17,
            "bundle_sha256":
                by_role["strict-source-bundle"]["sha256"],
            "bundle_manifest_sha256":
                by_role["strict-source-bundle-manifest"]["sha256"],
            "broker_connection_performed": False,
            "order_placement_performed": False,
            "paper_authorized": False,
            "live_authorized": False,
            "real_systemd_certified": False,
            "real_ib_certified": False,
            "object_store_ingestion_receipt_certified": False,
            "retention_enforcement_certified": False,
        }

    def _rewrite_native_evidence(self) -> None:
        bindings = []
        for variant in builder.native_aggregate.VARIANTS:
            path = (
                self.native_variant_root /
                f"execution-native-systemd-{variant}.json")
            payload = (
                json.dumps(
                    self.native_reports[variant],
                    ensure_ascii=True, indent=2, sort_keys=True) + "\n"
            ).encode("ascii")
            _write(path, payload)
            bindings.append({
                "variant": variant,
                "path": str(path.resolve(strict=True)),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
                "mode": "0600",
            })
        def verify_instance(path: Path, **_kwargs: Any) -> dict[str, Any]:
            variant = next(
                item for item in builder.native_aggregate.VARIANTS
                if f"-{item}-instance-receipt.json" in Path(path).name)
            return deepcopy(
                self.native_reports[variant]["instance_identity"])

        with mock.patch.object(
                builder.native_aggregate.native, "verify_instance_receipt",
                side_effect=verify_instance):
            self.native_document = (
                builder.native_aggregate.aggregate_reports(
                    self.native_reports, bindings))
        _write(
            self.native_path,
            (json.dumps(
                self.native_document, indent=2, sort_keys=True) + "\n"
             ).encode("ascii"))

    def _write_evidence_set_manifest(self) -> None:
        closure_bytes = self.closure_path.read_bytes()
        artifacts = [{
            "role": "round-closure",
            "path": self.closure_path.name,
            "sha256": hashlib.sha256(closure_bytes).hexdigest(),
            "size": len(closure_bytes),
            "mode": "0600",
            "tier": "certification",
        }]
        artifacts.extend({
            **item,
            "path": f"{builder.SOURCE_ARTIFACT_ROOT_NAME}/{item['path']}",
            "tier": "certification",
        } for item in self.artifacts)
        manifest = {
            "schema": "hepta.evidence-set-manifest.v2",
            "version": 2,
            "round": builder.SOURCE_ROUND,
            "release_version": builder.SOURCE_RELEASE_VERSION,
            "artifacts": artifacts,
        }
        _write(
            self.receipt_paths["evidence_set_manifest"],
            builder.canonical_json(manifest) + b"\n")

    def _receipt_report(self) -> dict[str, Any]:
        manifest_sha256 = hashlib.sha256(
            self.receipt_paths[
                "evidence_set_manifest"].read_bytes()).hexdigest()
        baseline = next(
            item for item in self.artifacts
            if item["role"] == "source-baseline-manifest")
        return {
            "schema": builder.receipt_verifier.VERIFICATION_SCHEMA,
            "version": 2,
            "production_trust": True,
            "trust_scope": "system-production",
            "signature_status": "verified",
            "retention_status": "current-policy-satisfied",
            "current_policy_satisfied_object_count": 1,
            "statement_sha256": "4" * 64,
            "request_sha256": hashlib.sha256(
                self.receipt_paths["request"].read_bytes()).hexdigest(),
            "index_sha256": hashlib.sha256(
                self.receipt_paths["index"].read_bytes()).hexdigest(),
            "evidence_set_manifest_sha256": manifest_sha256,
            "trust_policy_sha256": hashlib.sha256(
                self.receipt_paths["trust_policy"].read_bytes()).hexdigest(),
            "source_files_deleted": False,
            "source_removal_authorized": False,
            "paper_authorized": False,
            "live_authorized": False,
            "evidence_set_bound": True,
            "evidence_set_certified": True,
            "evidence_set": {
                "evidence_set_id": "round36-certification",
                "round": builder.SOURCE_ROUND,
                "release_version": builder.SOURCE_RELEASE_VERSION,
                "manifest_sha256": manifest_sha256,
                "source_baseline": {
                    key: baseline[key]
                    for key in ("path", "sha256", "size", "mode")
                },
            },
            "receipt": {
                "statement": {
                    "policy_sha256": hashlib.sha256(
                        self.receipt_paths[
                            "retention_policy"].read_bytes()).hexdigest(),
                },
            },
            "objects": [{
                "sha256": "5" * 64,
                "status": "fresh-signed-active-attestation",
            }],
        }

    def patched(
        self,
        *,
        receipt_report: dict[str, Any] | None = None,
        receipt_side_effect: Any = None,
        allow_user_native: bool = False,
    ) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(mock.patch.object(
            builder.closure_verifier,
            "verify",
            return_value=deepcopy(self.closure_report),
        ))
        if allow_user_native:
            stack.enter_context(mock.patch.object(
                builder, "_require_native_root_ownership"))
            stack.enter_context(mock.patch.object(
                builder.native_aggregate,
                "TRUSTED_REPORT_OWNER_PAIRS",
                frozenset({
                    (0, 0),
                    (os.geteuid(), os.getegid()),
                }),
            ))
            stack.enter_context(mock.patch.object(
                builder.native_aggregate,
                "_require_trusted_report_directory",
            ))
            def verify_instance(
                    path: Path, **_kwargs: Any) -> dict[str, Any]:
                variant = next(
                    item for item in builder.native_aggregate.VARIANTS
                    if f"-{item}-instance-receipt.json" in Path(path).name)
                return deepcopy(
                    self.native_reports[variant]["instance_identity"])
            stack.enter_context(mock.patch.object(
                builder.native_aggregate.native, "verify_instance_receipt",
                side_effect=verify_instance,
            ))
        if receipt_side_effect is not None:
            stack.enter_context(mock.patch.object(
                builder.receipt_verifier,
                "verify_receipt",
                side_effect=receipt_side_effect,
            ))
        elif receipt_report is not None:
            stack.enter_context(mock.patch.object(
                builder.receipt_verifier,
                "verify_receipt",
                return_value=deepcopy(receipt_report),
            ))
        return stack


class Round36CertificationTests(unittest.TestCase):
    def test_missing_both_publishes_only_private_pending_external(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-round36-") as temporary:
            fixture = CertificationFixture(Path(temporary))
            output_root = fixture.root / "output"
            output_root.mkdir(mode=0o700)
            output = output_root / builder.OUTPUT_NAME
            with fixture.patched():
                verification = builder.build_and_publish(
                    fixture.closure_path, fixture.artifact_root, output)
            document = json.loads(output.read_bytes())
            self.assertEqual(verification["status"], "pending-external")
            self.assertFalse(verification["passed"])
            self.assertEqual(
                document["blocked_external_evidence"],
                [builder.NATIVE_BLOCKER, builder.RECEIPT_BLOCKER])
            self.assertTrue(all(
                value is False
                for value in document["certification_flags"].values()))
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(output.stat().st_nlink, 1)
            self.assertEqual(
                output.read_bytes(),
                builder.canonical_json(document) + b"\n")

    def test_native_without_receipt_remains_pending_and_flags_false(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-round36-") as temporary:
            fixture = CertificationFixture(Path(temporary))
            with fixture.patched(allow_user_native=True):
                report = builder.build_certification(
                    fixture.closure_path,
                    fixture.artifact_root,
                    native_aggregate_path=fixture.native_path,
                )
            self.assertEqual(
                report["blocked_external_evidence"],
                [builder.RECEIPT_BLOCKER])
            self.assertIsNotNone(
                report["external_evidence"]["native_systemd"])
            self.assertTrue(all(
                value is False
                for value in report["certification_flags"].values()))

    def test_receipt_without_native_remains_pending_and_uses_system_trust(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-round36-") as temporary:
            fixture = CertificationFixture(Path(temporary))
            calls: list[dict[str, Any]] = []

            def production_receipt(*args: Any, **kwargs: Any) -> dict[str, Any]:
                calls.append(kwargs)
                return deepcopy(fixture.receipt_report)

            with fixture.patched(receipt_side_effect=production_receipt):
                report = builder.build_certification(
                    fixture.closure_path,
                    fixture.artifact_root,
                    receipt_inputs=fixture.receipt_inputs,
                )
            self.assertEqual(calls, [{"require_system_trust": True}])
            self.assertEqual(
                report["blocked_external_evidence"],
                [builder.NATIVE_BLOCKER])
            self.assertIsNotNone(
                report["external_evidence"]["production_receipt"])
            self.assertTrue(all(
                value is False
                for value in report["certification_flags"].values()))

    def test_both_independent_contracts_are_required_for_certified_state(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-round36-") as temporary:
            fixture = CertificationFixture(Path(temporary))
            with fixture.patched(
                    receipt_report=fixture.receipt_report,
                    allow_user_native=True):
                report = builder.build_certification(
                    fixture.closure_path,
                    fixture.artifact_root,
                    native_aggregate_path=fixture.native_path,
                    receipt_inputs=fixture.receipt_inputs,
                )
            self.assertEqual(report["status"], "certified")
            self.assertTrue(report["passed"])
            self.assertTrue(
                report["certification_flags"]["production_certified"])
            self.assertTrue(
                report["certification_flags"]["real_systemd_certified"])
            self.assertTrue(
                report["certification_flags"][
                    "retention_enforcement_certified"])
            for field in builder.ALWAYS_FALSE_FLAG_FIELDS:
                self.assertFalse(report["certification_flags"][field])

    def test_old_installation_container_and_forged_runtime_are_rejected(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-round36-") as temporary:
            fixture = CertificationFixture(Path(temporary))
            cases = []
            old = deepcopy(fixture.native_document)
            old["schema"] = "hepta.execution-native-systemd-aggregate.v5"
            cases.append(old)
            container = {
                "schema": "hepta.execution-rootful-systemd-gate.v4",
                "passed": True,
                "certification_level": "container-rehearsal",
            }
            cases.append(container)
            forged = deepcopy(fixture.native_document)
            forged["boundary"][
                "agent_os_runtime_preflight_executed"] = False
            cases.append(forged)
            shallow = deepcopy(fixture.native_document)
            shallow["variants"]["real"][
                "agent_os_runtime_result_sha256"] = "f" * 64
            cases.append(shallow)
            with fixture.patched(allow_user_native=True):
                for ordinal, document in enumerate(cases):
                    with self.subTest(ordinal=ordinal):
                        _write(
                            fixture.native_path,
                            builder.canonical_json(document) + b"\n")
                        with self.assertRaisesRegex(
                                builder.Round36CertificationError,
                                "raw-report reconstruction"):
                            builder.build_certification(
                                fixture.closure_path,
                                fixture.artifact_root,
                                native_aggregate_path=fixture.native_path,
                            )

    def test_test_local_receipt_contract_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-round36-") as temporary:
            fixture = CertificationFixture(Path(temporary))
            test_local = deepcopy(fixture.receipt_report)
            test_local["schema"] = (
                builder.receipt_verifier.TEST_VERIFICATION_SCHEMA)
            test_local["production_trust"] = False
            test_local["trust_scope"] = "test-local"
            test_local["signature_status"] = "test-key-verified"
            test_local["retention_status"] = (
                "test-evaluated-current-policy-satisfied")
            test_local["evidence_set_certified"] = False
            with fixture.patched(receipt_report=test_local):
                with self.assertRaisesRegex(
                        builder.Round36CertificationError,
                        "production/current"):
                    builder.build_certification(
                        fixture.closure_path,
                        fixture.artifact_root,
                        receipt_inputs=fixture.receipt_inputs,
                    )

    def test_receipt_cross_lineage_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-round36-") as temporary:
            fixture = CertificationFixture(Path(temporary))
            crossed = deepcopy(fixture.receipt_report)
            crossed["evidence_set"]["source_baseline"]["sha256"] = "f" * 64
            with fixture.patched(receipt_report=crossed):
                with self.assertRaisesRegex(
                        builder.Round36CertificationError,
                        "source-baseline lineage"):
                    builder.build_certification(
                        fixture.closure_path,
                        fixture.artifact_root,
                        receipt_inputs=fixture.receipt_inputs,
                    )

    def test_native_cross_lineage_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-round36-") as temporary:
            fixture = CertificationFixture(Path(temporary))
            for report in fixture.native_reports.values():
                report["disposable_sentinel"][  # type: ignore[index]
                    "clean_source_bundle_sha256"] = "f" * 64
                report["instance_identity"]["statement"][  # type: ignore[index]
                    "source_lineage"]["bundle_sha256"] = "f" * 64
            fixture._rewrite_native_evidence()
            with fixture.patched(allow_user_native=True):
                with self.assertRaisesRegex(
                        builder.Round36CertificationError,
                        "crosses the Round36 source lineage"):
                    builder.build_certification(
                        fixture.closure_path,
                        fixture.artifact_root,
                        native_aggregate_path=fixture.native_path,
                    )

    def test_native_raw_report_mutation_during_receipt_verification_fails(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-round36-") as temporary:
            fixture = CertificationFixture(Path(temporary))

            def mutate_variant(
                    *args: Any, **kwargs: Any) -> dict[str, Any]:
                del args, kwargs
                raw = (
                    fixture.native_variant_root /
                    "execution-native-systemd-real.json")
                changed = deepcopy(fixture.native_reports["real"])
                changed["host"]["kernel_release"] = (  # type: ignore[index]
                    "changed-during-receipt")
                _write(
                    raw,
                    (json.dumps(
                        changed, indent=2, sort_keys=True) + "\n"
                     ).encode("ascii"))
                return deepcopy(fixture.receipt_report)

            with fixture.patched(
                    receipt_side_effect=mutate_variant,
                    allow_user_native=True):
                with self.assertRaisesRegex(
                        builder.Round36CertificationError,
                        "native raw reports changed across certification"):
                    builder.build_certification(
                        fixture.closure_path,
                        fixture.artifact_root,
                        native_aggregate_path=fixture.native_path,
                        receipt_inputs=fixture.receipt_inputs,
                    )

    def test_receipt_input_mutation_is_detected_after_verification(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-round36-") as temporary:
            fixture = CertificationFixture(Path(temporary))

            def mutate(*args: Any, **kwargs: Any) -> dict[str, Any]:
                del args, kwargs
                report = deepcopy(fixture.receipt_report)
                _write(
                    fixture.receipt_paths["request"],
                    builder.canonical_json({"mutated": True}) + b"\n")
                return report

            with fixture.patched(receipt_side_effect=mutate):
                with self.assertRaisesRegex(
                        builder.Round36CertificationError,
                        "changed across"):
                    builder.build_certification(
                        fixture.closure_path,
                        fixture.artifact_root,
                        receipt_inputs=fixture.receipt_inputs,
                    )

    def test_noncanonical_report_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-round36-") as temporary:
            fixture = CertificationFixture(Path(temporary))
            with fixture.patched():
                report = builder.build_certification(
                    fixture.closure_path, fixture.artifact_root)
            path = fixture.root / "noncanonical.json"
            _write(
                path,
                (json.dumps(report, indent=2, sort_keys=True) + "\n"
                 ).encode("ascii"))
            with fixture.patched():
                with self.assertRaisesRegex(
                        verifier.Round36CertificationVerificationError,
                        "not canonical"):
                    verifier.verify(path)

    def test_report_cannot_forge_caller_certification_booleans(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-round36-") as temporary:
            fixture = CertificationFixture(Path(temporary))
            with fixture.patched():
                report = builder.build_certification(
                    fixture.closure_path, fixture.artifact_root)
            report["status"] = "certified"
            report["passed"] = True
            report["certification_flags"]["production_certified"] = True
            report["certification_flags"]["real_systemd_certified"] = True
            path = fixture.root / "forged.json"
            _write(path, builder.canonical_json(report) + b"\n")
            with fixture.patched():
                with self.assertRaisesRegex(
                        verifier.Round36CertificationVerificationError,
                        "not fail-closed"):
                    verifier.verify(path)

    def test_partial_receipt_arguments_are_rejected(self) -> None:
        namespace = mock.Mock(
            receipt=Path("/tmp/receipt"),
            request=None,
            trust_policy=None,
            index=None,
            evidence_root=None,
            retention_policy=None,
            evidence_set_manifest=None,
        )
        with self.assertRaisesRegex(
                builder.Round36CertificationError, "all-or-none"):
            builder._receipt_inputs_from_arguments(namespace)


if __name__ == "__main__":
    unittest.main(verbosity=2)
