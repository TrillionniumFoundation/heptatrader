#!/usr/bin/env python3

from __future__ import annotations

import base64
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))
sys.path.insert(0, str(REPOSITORY / "scripts"))

import build_heptatrader_evidence_index as index_builder  # noqa: E402
import build_heptatrader_evidence_ingestion_request as request_builder  # noqa: E402
import verify_heptatrader_evidence_index as index_verifier  # noqa: E402
import verify_heptatrader_evidence_ingestion_receipt as receipt_verifier  # noqa: E402
from tests.heptatrader_evidence_set_tests import (  # noqa: E402
    EvidenceSetFixture,
    ReleaseValidationEvidenceFixture,
)


POLICY = REPOSITORY / "policies/heptatrader-evidence-retention-v1.json"
PRODUCTION_TRUST = (
    REPOSITORY / "policies/heptatrader-evidence-receipt-trust-v1.json")
OPENSSL = "/usr/bin/openssl"


class ReceiptFixture:
    def __init__(
            self, root: Path, *, finite: bool = False,
            certified: bool = False,
            release_certified: bool = False) -> None:
        if finite and (certified or release_certified) or (
                certified and release_certified):
            raise ValueError("finite legacy and certified fixtures are exclusive")
        self.root = root
        self.manifest_path: Path | None = None
        self._delivery_verification = None
        if certified or release_certified:
            evidence_set = (
                ReleaseValidationEvidenceFixture(root)
                if release_certified else EvidenceSetFixture(root))
            self._delivery_verification = (
                evidence_set.mock_release_index_roles
                if release_certified else
                evidence_set.mock_full_delivery_verification)
            self.evidence = evidence_set.evidence
            self.index_path = evidence_set.index_path
            self.manifest_path = (
                self.evidence /
                f"heptatrader-round{evidence_set.round}-"
                "evidence-set-manifest-v2.json")
            self.manifest_path.write_bytes(
                evidence_set.manifest_path.read_bytes())
            self.manifest_path.chmod(0o600)
        else:
            self.evidence = root / "runtime-logs"
            self.evidence.mkdir()
            name = (
                "ci-execution-gateway-soak.json" if finite else
                "heptatrader-round99-closure-v1.json")
            payload = self.evidence / name
            payload.write_bytes(b"{\"passed\":true}\n")
            payload.chmod(0o600)
            self.index_path = root / "index.json"
            index = index_builder.build_index(
                self.evidence, POLICY, [name],
                "2026-01-01T00:00:00+00:00")
            self.index_path.write_text(
                json.dumps(index, indent=2, sort_keys=True) + "\n",
                encoding="utf-8")
            self.index_path.chmod(0o600)
        with self.mock_full_delivery_verification():
            self.request = request_builder.build_request(
                self.index_path, self.evidence, POLICY,
                request_nonce="11" * 32,
                created_at="2026-01-01T00:00:01+00:00",
                evidence_set_manifest_path=self.manifest_path,
            )
        self.request_path = root / "request.json"
        self.request_path.write_bytes(
            request_builder.canonical_json(self.request) + b"\n")
        self.request_path.chmod(0o600)

        self.private_key = root / "fixture-private.pem"
        self.public_key = root / "fixture-public.pem"
        self._openssl([
            "genpkey", "-algorithm", "Ed25519",
            "-out", str(self.private_key),
        ])
        self._openssl([
            "pkey", "-in", str(self.private_key), "-pubout",
            "-out", str(self.public_key),
        ])
        self.private_key.chmod(0o600)
        self.public_key.chmod(0o644)
        public_bytes = self.public_key.read_bytes()
        self.key_digest = receipt_verifier.public_key_spki_sha256(public_bytes)
        self.key_id = "sha256/" + self.key_digest
        self.trust_path = root / "trust.json"
        self.trust = {
            "schema": receipt_verifier.TRUST_SCHEMA,
            "version": 1,
            "project_id": request_builder.PROJECT_ID,
            "signature_domain": receipt_verifier.SIGNATURE_DOMAIN,
            "production_receipt_status": "configured-external",
            "allowed_retention_policy_sha256": [
                self.request["index"]["policy_sha256"],
            ],
            "legal_hold_attestation_max_age_seconds": 3600,
            "keys": [{
                "key_id": self.key_id,
                "algorithm": "ed25519",
                "public_key_path": self.public_key.name,
                "public_key_spki_sha256": self.key_digest,
                "valid_from": "2025-01-01T00:00:00+00:00",
                "valid_until": "2027-01-01T00:00:00+00:00",
                "revoked": False,
                "allowed_store_ids": ["fixture-immutable-store"],
                "allowed_retention_policy_sha256": [
                    self.request["index"]["policy_sha256"],
                ],
            }],
        }
        self._write_json(self.trust_path, self.trust)
        self.receipt_path = root / "receipt.json"
        self.receipt = self.make_receipt()
        self._write_json(self.receipt_path, self.receipt)
        self.verification_time = datetime(
            2026, 1, 1, 0, 2, 0, tzinfo=timezone.utc)

    @staticmethod
    def _openssl(arguments: list[str]) -> None:
        result = subprocess.run(
            [OPENSSL, *arguments], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode(errors="replace"))

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        path.chmod(0o600)

    def _sign(self, statement: dict[str, object]) -> str:
        statement_path = self.root / "signed-statement.bin"
        signature_path = self.root / "signature.bin"
        statement_path.write_bytes(
            receipt_verifier.SIGNATURE_DOMAIN.encode("ascii") + b"\0" +
            request_builder.canonical_json(statement))
        statement_path.chmod(0o600)
        if signature_path.exists():
            signature_path.unlink()
        self._openssl([
            "pkeyutl", "-sign", "-inkey", str(self.private_key), "-rawin",
            "-in", str(statement_path), "-out", str(signature_path),
        ])
        signature = signature_path.read_bytes()
        self.assert_signature_size(signature)
        return base64.b64encode(signature).decode("ascii")

    @staticmethod
    def assert_signature_size(signature: bytes) -> None:
        if len(signature) != 64:
            raise AssertionError("fixture Ed25519 signature is not 64 bytes")

    def make_receipt(self) -> dict[str, object]:
        ingested = datetime(
            2026, 1, 1, 0, 1, 0, tzinfo=timezone.utc)
        verified = ingested + timedelta(seconds=5)
        signed = verified + timedelta(seconds=1)
        objects = []
        for item in self.request["objects"]:
            requirement = item["required_retention"]
            if requirement["kind"] == "indefinite":
                retention = {
                    "kind": "indefinite",
                    "days": None,
                    "anchor_at": ingested.isoformat(),
                    "retain_until": None,
                    "object_lock_mode": "legal-hold",
                    "legal_hold": True,
                }
            else:
                retention = {
                    "kind": "finite-days",
                    "days": requirement["days"],
                    "anchor_at": ingested.isoformat(),
                    "retain_until": (
                        verified + timedelta(
                            days=requirement["days"] + 1)).isoformat(),
                    "object_lock_mode": "compliance",
                    "legal_hold": False,
                }
            objects.append({
                "sha256": item["sha256"],
                "size": item["size"],
                "object_key": item["object_key"],
                "version_id": "version-fixture-001",
                "provider_checksum_sha256": item["sha256"],
                "readback_sha256": item["sha256"],
                "retention": retention,
            })
        request_bytes = self.request_path.read_bytes()
        statement = {
            "project_id": request_builder.PROJECT_ID,
            "request_sha256": hashlib.sha256(request_bytes).hexdigest(),
            "request_nonce": self.request["request_nonce"],
            "index_sha256": self.request["index"]["sha256"],
            "policy_sha256": self.request["index"]["policy_sha256"],
            "records_sha256": self.request["index"]["records_sha256"],
            "store_id": "fixture-immutable-store",
            "provider": "fixture-provider",
            "receipt_serial": "fixture-receipt-0001",
            "ingested_at": ingested.isoformat(),
            "verified_at": verified.isoformat(),
            "signed_at": signed.isoformat(),
            "verification_method": "full-object-readback-sha256",
            "source_files_deleted": False,
            "source_removal_authorized": False,
            "paper_authorized": False,
            "live_authorized": False,
            "objects": objects,
        }
        if self.request["schema"] == request_builder.REQUEST_SCHEMA:
            statement["evidence_set"] = self.request["evidence_set"]
        statement_bytes = request_builder.canonical_json(statement)
        return {
            "schema": (
                receipt_verifier.RECEIPT_SCHEMA
                if self.request["schema"] == request_builder.REQUEST_SCHEMA
                else receipt_verifier.LEGACY_RECEIPT_SCHEMA),
            "version": (
                2 if self.request["schema"] == request_builder.REQUEST_SCHEMA
                else 1),
            "statement": statement,
            "statement_sha256": hashlib.sha256(statement_bytes).hexdigest(),
            "signature": {
                "algorithm": "ed25519",
                "key_id": self.key_id,
                "value_base64": self._sign(statement),
            },
        }

    def resign(self, receipt: dict[str, object]) -> None:
        statement = receipt["statement"]
        receipt["statement_sha256"] = hashlib.sha256(
            request_builder.canonical_json(statement)).hexdigest()
        receipt["signature"]["value_base64"] = self._sign(statement)

    def verify(
            self, receipt_path: Path | None = None, *,
            verification_time: datetime | None = None) -> dict[str, object]:
        with self.mock_full_delivery_verification():
            return receipt_verifier.verify_receipt(
                receipt_path or self.receipt_path,
                self.request_path,
                self.trust_path,
                self.index_path,
                self.evidence,
                POLICY,
                self.manifest_path,
                require_system_trust=False,
                verification_time=(
                    self.verification_time
                    if verification_time is None else verification_time),
            )

    def mock_full_delivery_verification(self):
        if self._delivery_verification is None:
            return nullcontext()
        return self._delivery_verification()


class EvidenceIngestionReceiptTests(unittest.TestCase):
    def test_openssl_configuration_cannot_be_redirected_by_caller(self):
        completed = mock.Mock(returncode=0, stdout=b"ok\n", stderr=b"")
        hostile = {
            "OPENSSL_CONF": "/tmp/hostile-openssl.cnf",
            "OPENSSL_MODULES": "/tmp/hostile-providers",
        }
        with (
            mock.patch.dict(os.environ, hostile, clear=True),
            mock.patch.object(
                receipt_verifier.subprocess, "run",
                return_value=completed) as run,
        ):
            self.assertEqual(
                receipt_verifier._run_openssl(["version"]), b"ok\n")
        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment["OPENSSL_CONF"], "/dev/null")
        self.assertEqual(
            environment["OPENSSL_MODULES"],
            "/nonexistent-hepta-openssl-provider-directory")

        with (
            mock.patch.dict(
                os.environ,
                {**hostile, "HEPTA_RELEASE_CAUSAL_ROOTFS": "1"},
                clear=True),
            mock.patch.object(
                receipt_verifier.subprocess, "run",
                return_value=completed) as causal_run,
        ):
            receipt_verifier._run_openssl(["version"])
        self.assertEqual(
            causal_run.call_args.kwargs["env"]["OPENSSL_CONF"],
            str(receipt_verifier.RELEASE_CAUSAL_OPENSSL_CONF))

    def test_signed_receipt_round_trip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(Path(temporary))
            verified = fixture.verify()
            self.assertEqual(
                verified["receipt"]["statement"]["verification_method"],
                "full-object-readback-sha256")
            self.assertEqual(
                verified["retention_status"],
                "test-evaluated-current-policy-satisfied")
            self.assertEqual(
                verified["signature_status"], "test-key-verified")
            self.assertEqual(
                verified["schema"],
                receipt_verifier.TEST_VERIFICATION_SCHEMA)
            self.assertFalse(verified["production_trust"])
            self.assertEqual(verified["trust_scope"], "test-local")
            self.assertEqual(
                verified["current_policy_satisfied_object_count"], 1)
            self.assertEqual(
                verified["objects"][0]["status"],
                "fresh-signed-active-attestation")
            self.assertFalse(verified["source_removal_authorized"])
            self.assertFalse(verified["evidence_set_bound"])
            self.assertFalse(verified["evidence_set_certified"])
            self.assertIsNone(verified["evidence_set"])

    def test_manifest_defined_v2_signed_receipt_round_trip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(Path(temporary), certified=True)
            self.assertTrue(all(
                item["required_retention"] == {
                    "kind": "indefinite",
                    "days": None,
                }
                for item in fixture.request["objects"]))
            verified = fixture.verify()
            self.assertTrue(verified["evidence_set_bound"])
            self.assertFalse(verified["evidence_set_certified"])
            self.assertEqual(
                verified["evidence_set"]["evidence_set_id"],
                "round99-certification")
            self.assertEqual(
                verified["evidence_set"]["manifest_sha256"],
                hashlib.sha256(
                    fixture.manifest_path.read_bytes()).hexdigest())
            self.assertEqual(
                verified["current_policy_satisfied_object_count"], 10)
            self.assertEqual(
                verified["receipt"]["schema"],
                receipt_verifier.RECEIPT_SCHEMA)
            self.assertEqual(verified["receipt"]["version"], 2)

    def test_release_validation_profile_builds_v2_request_and_signed_receipt(
            self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(
                Path(temporary), release_certified=True)
            self.assertEqual(
                fixture.request["schema"], request_builder.REQUEST_SCHEMA)
            self.assertEqual(
                fixture.request["evidence_set"]["profile"],
                "release-validation-p0-v1")
            self.assertEqual(
                fixture.request["evidence_set"]["round"], 95)
            self.assertTrue(all(
                item["required_retention"] == {
                    "kind": "indefinite", "days": None}
                for item in fixture.request["objects"]))
            verified = fixture.verify()
            self.assertTrue(verified["evidence_set_bound"])
            self.assertEqual(
                verified["evidence_set"]["profile"],
                "release-validation-p0-v1")
            self.assertEqual(
                verified["signature_status"], "test-key-verified")
            self.assertEqual(
                verified["retention_status"],
                "test-evaluated-current-policy-satisfied")
            self.assertEqual(
                verified["current_policy_satisfied_object_count"],
                len(fixture.request["objects"]))

    def test_v2_request_binds_raw_manifest_and_requires_it_for_receipt(
            self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(Path(temporary), certified=True)
            with self.assertRaisesRegex(
                    receipt_verifier.IngestionReceiptError,
                    "requires an evidence-set manifest"):
                receipt_verifier.verify_receipt(
                    fixture.receipt_path, fixture.request_path,
                    fixture.trust_path, fixture.index_path,
                    fixture.evidence, POLICY,
                    require_system_trust=False,
                    verification_time=fixture.verification_time)

            original = fixture.request
            document = json.loads(
                fixture.manifest_path.read_text(encoding="utf-8"))
            fixture.manifest_path.write_text(
                json.dumps(document, separators=(",", ":"), sort_keys=True),
                encoding="utf-8")
            fixture.manifest_path.chmod(0o600)
            with fixture.mock_full_delivery_verification():
                rebuilt = request_builder.build_request(
                    fixture.index_path, fixture.evidence, POLICY,
                    request_nonce=original["request_nonce"],
                    created_at=original["created_at"],
                    evidence_set_manifest_path=fixture.manifest_path)
            self.assertNotEqual(
                rebuilt["evidence_set"]["manifest_sha256"],
                original["evidence_set"]["manifest_sha256"])
            self.assertNotEqual(
                rebuilt["objects_sha256"], original["objects_sha256"])

    def test_request_rejects_manifest_a_b_a_verification_swap(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = EvidenceSetFixture(Path(temporary))
            manifest_path = (
                fixture.evidence /
                "heptatrader-round99-evidence-set-manifest-v2.json")
            valid_bytes = fixture.manifest_path.read_bytes()
            invalid = json.loads(valid_bytes)
            invalid["required_roles"] = list(
                reversed(invalid["required_roles"]))
            invalid_bytes = (
                json.dumps(invalid, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8")
            self.assertNotEqual(invalid_bytes, valid_bytes)
            manifest_path.write_bytes(invalid_bytes)
            manifest_path.chmod(0o600)
            real_verify = request_builder.set_verifier.verify

            def verify_b_then_restore_a(*args, **kwargs):
                manifest_path.write_bytes(valid_bytes)
                manifest_path.chmod(0o600)
                try:
                    return real_verify(*args, **kwargs)
                finally:
                    manifest_path.write_bytes(invalid_bytes)
                    manifest_path.chmod(0o600)

            with fixture.mock_full_delivery_verification(), mock.patch.object(
                    request_builder.set_verifier, "verify",
                    side_effect=verify_b_then_restore_a):
                with self.assertRaisesRegex(
                        request_builder.IngestionRequestError,
                        "did not verify the stable manifest bytes"):
                    request_builder.build_request(
                        fixture.index_path, fixture.evidence, POLICY,
                        request_nonce="22" * 32,
                        created_at="2026-01-01T00:00:01+00:00",
                        evidence_set_manifest_path=manifest_path)
            self.assertEqual(manifest_path.read_bytes(), invalid_bytes)

    def test_request_rejects_index_verification_digest_disagreement(
            self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = EvidenceSetFixture(Path(temporary))
            manifest_path = (
                fixture.evidence /
                "heptatrader-round99-evidence-set-manifest-v2.json")
            manifest_path.write_bytes(fixture.manifest_path.read_bytes())
            manifest_path.chmod(0o600)
            real_verify = request_builder.set_verifier.verify

            def report_other_index(*args, **kwargs):
                report = real_verify(*args, **kwargs)
                report["index_sha256"] = "00" * 32
                return report

            with fixture.mock_full_delivery_verification(), mock.patch.object(
                    request_builder.set_verifier, "verify",
                    side_effect=report_other_index):
                with self.assertRaisesRegex(
                        request_builder.IngestionRequestError,
                        "disagrees with its verified index"):
                    request_builder.build_request(
                        fixture.index_path, fixture.evidence, POLICY,
                        request_nonce="33" * 32,
                        created_at="2026-01-01T00:00:01+00:00",
                        evidence_set_manifest_path=manifest_path)

    def test_verification_time_is_captured_after_signature_check(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(Path(temporary))
            events = []
            real_verify = receipt_verifier._verify_ed25519

            def record_signature(*args, **kwargs):
                events.append("signature")
                return real_verify(*args, **kwargs)

            def record_clock(*args, **kwargs):
                events.append("clock")
                return fixture.verification_time

            with mock.patch.object(
                    receipt_verifier, "_verify_ed25519",
                    side_effect=record_signature), mock.patch.object(
                        receipt_verifier, "_capture_verification_time",
                        side_effect=record_clock):
                fixture.verify()
            self.assertEqual(events, ["signature", "clock"])

    def test_trust_revocation_during_signature_check_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(Path(temporary))
            real_verify = receipt_verifier._verify_ed25519

            def verify_then_revoke(*args, **kwargs):
                result = real_verify(*args, **kwargs)
                revoked = deepcopy(fixture.trust)
                revoked["keys"][0]["revoked"] = True
                fixture._write_json(fixture.trust_path, revoked)
                return result

            with mock.patch.object(
                    receipt_verifier, "_verify_ed25519",
                    side_effect=verify_then_revoke):
                with self.assertRaisesRegex(
                        receipt_verifier.IngestionReceiptError,
                        "trust policy changed"):
                    fixture.verify()

    def test_trust_revocation_during_clock_capture_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(Path(temporary))

            def capture_then_revoke(*args, **kwargs):
                revoked = deepcopy(fixture.trust)
                revoked["keys"][0]["revoked"] = True
                fixture._write_json(fixture.trust_path, revoked)
                return fixture.verification_time

            with mock.patch.object(
                    receipt_verifier, "_capture_verification_time",
                    side_effect=capture_then_revoke):
                with self.assertRaisesRegex(
                        receipt_verifier.IngestionReceiptError,
                        "trust policy changed"):
                    fixture.verify()

    def test_evidence_mutation_during_clock_capture_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(Path(temporary))
            relative = fixture.request["objects"][0]["records"][0]["path"]
            evidence_path = fixture.evidence / relative

            def capture_then_mutate(*args, **kwargs):
                evidence_path.write_bytes(b"{\"passed\":false}\n")
                evidence_path.chmod(0o600)
                return fixture.verification_time

            with mock.patch.object(
                    receipt_verifier, "_capture_verification_time",
                    side_effect=capture_then_mutate):
                with self.assertRaisesRegex(
                        receipt_verifier.IngestionReceiptError,
                        "evidence changed during verification"):
                    fixture.verify()

    def test_production_trust_remains_pending_and_contains_no_key(self) -> None:
        trust, keys = receipt_verifier._load_trust_policy(PRODUCTION_TRUST)
        self.assertEqual(
            trust["production_receipt_status"], "pending-external")
        self.assertEqual(keys, {})
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(Path(temporary))
            with self.assertRaisesRegex(
                    receipt_verifier.IngestionReceiptError,
                    "pending external"):
                receipt_verifier.verify_receipt(
                    fixture.receipt_path, fixture.request_path,
                    PRODUCTION_TRUST, fixture.index_path,
                    fixture.evidence, POLICY)

    def test_production_verifier_rejects_legacy_request_schema(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(Path(temporary))
            keys = {
                fixture.key_id: fixture.trust["keys"][0],
            }
            with mock.patch.object(
                    receipt_verifier, "_load_trust_policy",
                    return_value=(b"fixture-trust", fixture.trust, keys)):
                with self.assertRaisesRegex(
                        receipt_verifier.IngestionReceiptError,
                        "manifest-defined v2 evidence set"):
                    receipt_verifier.verify_receipt(
                        fixture.receipt_path, fixture.request_path,
                        fixture.trust_path, fixture.index_path,
                        fixture.evidence, POLICY,
                        require_system_trust=True)

    def test_configured_production_trust_requires_root_owned_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(Path(temporary))
            self.assertEqual(
                fixture.verify()["receipt"]["statement"]["store_id"],
                "fixture-immutable-store")
            with mock.patch.object(
                    receipt_verifier, "SYSTEM_TRUST_POLICY",
                    fixture.trust_path):
                with self.assertRaisesRegex(
                        receipt_verifier.IngestionReceiptError, "root-owned"):
                    receipt_verifier.verify_receipt(
                        fixture.receipt_path,
                        fixture.request_path,
                        fixture.trust_path,
                        fixture.index_path,
                        fixture.evidence,
                        POLICY,
                        require_system_trust=True,
                    )

    def test_configured_production_trust_requires_fixed_system_path(
            self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(Path(temporary))
            fixed = Path(temporary) / "etc/heptatrader/trust.json"
            with mock.patch.object(
                    receipt_verifier, "SYSTEM_TRUST_POLICY", fixed):
                with self.assertRaisesRegex(
                        receipt_verifier.IngestionReceiptError,
                        "fixed system path"):
                    receipt_verifier.verify_receipt(
                        fixture.receipt_path,
                        fixture.request_path,
                        fixture.trust_path,
                        fixture.index_path,
                        fixture.evidence,
                        POLICY,
                        require_system_trust=True,
                    )

    def test_production_trust_prefers_fixed_system_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-trust-path-") as temporary:
            root = Path(temporary)
            repository = root / "repository"
            template = (
                repository /
                "policies/heptatrader-evidence-receipt-trust-v1.json")
            system = root / "etc/heptatrader/trust.json"
            with mock.patch.object(
                    receipt_verifier, "SYSTEM_TRUST_POLICY", system):
                self.assertEqual(
                    receipt_verifier.production_trust_path(repository),
                    template)
                system.parent.mkdir(parents=True)
                system.write_bytes(b"{}\n")
                self.assertEqual(
                    receipt_verifier.production_trust_path(repository),
                    system)
                system.unlink()
                system.symlink_to("missing-trust.json")
                self.assertEqual(
                    receipt_verifier.production_trust_path(repository),
                    system)

    def test_request_is_content_addressed_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-request-") as temporary:
            fixture = ReceiptFixture(Path(temporary))
            output = Path(temporary) / "requests"
            first = request_builder.write_request(output, fixture.request)
            second = request_builder.write_request(output, fixture.request)
            self.assertEqual(first, second)
            self.assertTrue(first.name.startswith("sha256-"))
            self.assertEqual(first.stat().st_mode & 0o777, 0o600)
            outside = Path(temporary) / "outside"
            outside.mkdir()
            parent_link = Path(temporary) / "linked-parent"
            parent_link.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(
                    request_builder.IngestionRequestError, "unsafe component"):
                request_builder.write_request(
                    parent_link / "requests", fixture.request)
            self.assertEqual(list(outside.iterdir()), [])

    def test_stable_file_rejects_hardlinks_and_same_inode_rewrite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-stable-") as temporary:
            root = Path(temporary)
            path = root / "payload.json"
            path.write_bytes(b"{\"trusted\":true}\\n")
            path.chmod(0o600)
            hardlink = root / "hardlink.json"
            os.link(path, hardlink)
            with self.assertRaisesRegex(
                    request_builder.IngestionRequestError, "regular file"):
                request_builder.stable_file(path)
            hardlink.unlink()

            original = path.stat()
            real_read = os.read
            mutated = False

            def read_then_mutate(descriptor: int, size: int) -> bytes:
                nonlocal mutated
                chunk = real_read(descriptor, size)
                if not chunk and not mutated:
                    mutated = True
                    path.write_bytes(b"{\"forged\":true}\\n")
                    os.utime(
                        path,
                        ns=(original.st_atime_ns, original.st_mtime_ns))
                return chunk

            with mock.patch.object(
                    request_builder.os, "read",
                    side_effect=read_then_mutate):
                with self.assertRaisesRegex(
                        request_builder.IngestionRequestError, "changed"):
                    request_builder.stable_file(path)
            self.assertTrue(mutated)

    def test_request_publication_rejects_post_read_same_inode_rewrite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-request-") as temporary:
            fixture = ReceiptFixture(Path(temporary))
            output = Path(temporary) / "requests"
            real_stable_file = request_builder.stable_file
            mutated = False

            def verify_then_mutate(path: Path, limit: int = (
                    request_builder.MAX_JSON_BYTES)):
                nonlocal mutated
                result = real_stable_file(path, limit)
                if path.parent == output and not mutated:
                    mutated = True
                    current = path.read_bytes()
                    path.write_bytes(b"X" * len(current))
                return result

            with mock.patch.object(
                    request_builder, "stable_file",
                    side_effect=verify_then_mutate):
                with self.assertRaisesRegex(
                        request_builder.IngestionRequestError,
                        "identity drift"):
                    request_builder.write_request(output, fixture.request)
            self.assertTrue(mutated)
            self.assertEqual(list(output.iterdir()), [])

    def test_same_digest_uses_strongest_retention(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-request-") as temporary:
            root = Path(temporary)
            evidence = root / "runtime-logs"
            evidence.mkdir()
            for name in (
                    "heptatrader-round99-closure-v1.json",
                    "heptatrader-round99-inventory-v1.json"):
                path = evidence / name
                path.write_bytes(b"same payload\n")
                path.chmod(0o600)
            index_path = root / "index.json"
            index = index_builder.build_index(
                evidence, POLICY, [
                    "heptatrader-round99-closure-v1.json",
                    "heptatrader-round99-inventory-v1.json",
                ], "2026-01-01T00:00:00+00:00")
            index_path.write_text(
                json.dumps(index, indent=2, sort_keys=True) + "\n")
            index_path.chmod(0o600)
            request = request_builder.build_request(
                index_path, evidence, POLICY,
                request_nonce="22" * 32,
                created_at="2026-01-01T00:00:01+00:00")
            self.assertEqual(request["object_count"], 1)
            self.assertEqual(
                request["objects"][0]["required_retention"],
                {"kind": "indefinite", "days": None})
            self.assertEqual(len(request["objects"][0]["records"]), 2)

    def test_request_and_receipt_timestamps_are_causal(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-request-") as temporary:
            fixture = ReceiptFixture(Path(temporary))
            with self.assertRaisesRegex(
                    request_builder.IngestionRequestError, "predate"):
                request_builder.build_request(
                    fixture.index_path, fixture.evidence, POLICY,
                    request_nonce="33" * 32,
                    created_at="2025-12-31T23:59:59+00:00")

            forged = deepcopy(fixture.receipt)
            before_request = datetime(
                2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
            forged["statement"]["ingested_at"] = before_request.isoformat()
            forged["statement"]["verified_at"] = (
                before_request + timedelta(seconds=1)).isoformat()
            forged["statement"]["signed_at"] = (
                before_request + timedelta(seconds=2)).isoformat()
            for item in forged["statement"]["objects"]:
                item["retention"]["anchor_at"] = before_request.isoformat()
            fixture.resign(forged)
            path = Path(temporary) / "pre-request-receipt.json"
            fixture._write_json(path, forged)
            with self.assertRaisesRegex(
                    receipt_verifier.IngestionReceiptError, "predate"):
                fixture.verify(path)

    def test_signature_and_trust_forgery_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(Path(temporary))
            forged = deepcopy(fixture.receipt)
            signature = bytearray(base64.b64decode(
                forged["signature"]["value_base64"]))
            signature[0] ^= 1
            forged["signature"]["value_base64"] = base64.b64encode(
                signature).decode("ascii")
            path = Path(temporary) / "forged.json"
            fixture._write_json(path, forged)
            with self.assertRaisesRegex(
                    receipt_verifier.IngestionReceiptError, "OpenSSL"):
                fixture.verify(path)
            trust = deepcopy(fixture.trust)
            trust["keys"][0]["revoked"] = True
            fixture._write_json(fixture.trust_path, trust)
            with self.assertRaisesRegex(
                    receipt_verifier.IngestionReceiptError, "revoked"):
                fixture.verify()

    def test_non_ed25519_public_keys_cannot_claim_ed25519(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            root = Path(temporary)
            for name, arguments in (
                    ("rsa", [
                        "genpkey", "-algorithm", "RSA",
                        "-pkeyopt", "rsa_keygen_bits:512",
                    ]),
                    ("ed448", ["genpkey", "-algorithm", "Ed448"])):
                private_key = root / f"{name}-private.pem"
                public_key = root / f"{name}-public.pem"
                ReceiptFixture._openssl([
                    *arguments, "-out", str(private_key),
                ])
                ReceiptFixture._openssl([
                    "pkey", "-in", str(private_key), "-pubout",
                    "-out", str(public_key),
                ])
                with self.assertRaisesRegex(
                        receipt_verifier.IngestionReceiptError,
                        "canonical RFC 8410 Ed25519"):
                    receipt_verifier.public_key_spki_sha256(
                        public_key.read_bytes())

    def test_signature_base64_must_be_canonical(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(Path(temporary))
            forged = deepcopy(fixture.receipt)
            encoded = forged["signature"]["value_base64"]
            self.assertTrue(encoded.endswith("=="))
            alphabet = (
                "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                "0123456789+/")
            index = alphabet.index(encoded[-3])
            noncanonical = (
                encoded[:-3] + alphabet[index ^ 1] + encoded[-2:])
            self.assertEqual(
                base64.b64decode(noncanonical, validate=True),
                base64.b64decode(encoded, validate=True))
            self.assertNotEqual(noncanonical, encoded)
            forged["signature"]["value_base64"] = noncanonical
            path = Path(temporary) / "noncanonical-base64.json"
            fixture._write_json(path, forged)
            with self.assertRaisesRegex(
                    receipt_verifier.IngestionReceiptError,
                    "canonical base64"):
                fixture.verify(path)

    def test_retention_policy_key_scope_and_future_receipt_are_rejected(
            self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(Path(temporary))
            untrusted = "00" * 32
            trust = deepcopy(fixture.trust)
            trust["allowed_retention_policy_sha256"].append(untrusted)
            trust["allowed_retention_policy_sha256"].sort()
            trust["keys"][0][
                "allowed_retention_policy_sha256"] = [untrusted]
            fixture._write_json(fixture.trust_path, trust)
            with self.assertRaisesRegex(
                    receipt_verifier.IngestionReceiptError,
                    "signing key is not trusted"):
                fixture.verify()

            fixture._write_json(fixture.trust_path, fixture.trust)
            future = deepcopy(fixture.receipt)
            ingested = datetime.now(timezone.utc) + timedelta(days=1)
            future["statement"]["ingested_at"] = ingested.isoformat()
            future["statement"]["verified_at"] = (
                ingested + timedelta(seconds=1)).isoformat()
            future["statement"]["signed_at"] = (
                ingested + timedelta(seconds=2)).isoformat()
            for item in future["statement"]["objects"]:
                item["retention"]["anchor_at"] = ingested.isoformat()
            fixture.resign(future)
            path = Path(temporary) / "future.json"
            fixture._write_json(path, future)
            with self.assertRaisesRegex(
                    receipt_verifier.IngestionReceiptError, "future"):
                fixture.verify(path)

    def test_future_attestation_or_signature_is_never_current(self) -> None:
        for label, verified_offset, signed_offset, accepted in (
                ("exact-boundary", timedelta(0), timedelta(0), True),
                ("future-attestation", timedelta(microseconds=1),
                 timedelta(microseconds=1), False),
                ("future-signature", timedelta(0),
                 timedelta(microseconds=1), False)):
            with self.subTest(label=label, accepted=accepted):
                with tempfile.TemporaryDirectory(
                        prefix="hepta-receipt-") as temporary:
                    fixture = ReceiptFixture(Path(temporary))
                    future = deepcopy(fixture.receipt)
                    verified = (
                        fixture.verification_time + verified_offset)
                    signed = fixture.verification_time + signed_offset
                    ingested = fixture.verification_time - timedelta(seconds=1)
                    future["statement"]["ingested_at"] = ingested.isoformat()
                    future["statement"]["verified_at"] = verified.isoformat()
                    future["statement"]["signed_at"] = signed.isoformat()
                    for item in future["statement"]["objects"]:
                        item["retention"]["anchor_at"] = ingested.isoformat()
                    fixture.resign(future)
                    path = Path(temporary) / "clock-skew.json"
                    fixture._write_json(path, future)
                    if accepted:
                        result = fixture.verify(path)
                        self.assertEqual(
                            result["objects"][0][
                                "attestation_age_seconds"], 0.0)
                    else:
                        with self.assertRaisesRegex(
                                receipt_verifier.IngestionReceiptError,
                                "future"):
                            fixture.verify(path)

    def test_signing_key_must_remain_valid_at_verification_time(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(Path(temporary))
            trust = deepcopy(fixture.trust)
            trust["keys"][0]["valid_until"] = (
                fixture.verification_time -
                timedelta(seconds=1)).isoformat()
            fixture._write_json(fixture.trust_path, trust)
            with self.assertRaisesRegex(
                    receipt_verifier.IngestionReceiptError,
                    "not valid at verification_time"):
                fixture.verify()

    def test_rfc3339_profile_rejects_noncanonical_iso_forms(self) -> None:
        for value in (
                "2026-01-01 00:00:00+00:00",
                "2026-W01-4T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00:30",
                "2026-01-01T00:00:00",
                "2026-01-01T00:00:00z",
                "2026-01-01T00:00:00-00:00",
                "2026-01-01T00:00:00.0000001Z"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                        request_builder.IngestionRequestError,
                        "RFC3339"):
                    request_builder.require_rfc3339(value, "fixture time")
        self.assertEqual(
            request_builder.require_rfc3339(
                "2026-01-01T00:00:00.000001Z", "fixture time"),
            datetime(
                2026, 1, 1, 0, 0, 0, 1, tzinfo=timezone.utc))

    def test_canonical_clis_reject_policy_and_trust_overrides(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(Path(temporary))
            commands = [
                [
                    str(REPOSITORY /
                        "scripts/build_heptatrader_evidence_index.py"),
                    "--evidence-root", str(fixture.evidence),
                    "--policy", str(POLICY),
                    "--output", "evidence-indexes/forged.json",
                ],
                [
                    str(REPOSITORY /
                        "scripts/build_heptatrader_evidence_ingestion_request.py"),
                    "--index", str(fixture.index_path),
                    "--evidence-root", str(fixture.evidence),
                    "--policy", str(POLICY),
                ],
                [
                    str(REPOSITORY /
                        "scripts/verify_heptatrader_evidence_ingestion_receipt.py"),
                    "--receipt", str(fixture.receipt_path),
                    "--request", str(fixture.request_path),
                    "--index", str(fixture.index_path),
                    "--evidence-root", str(fixture.evidence),
                    "--trust-policy", str(fixture.trust_path),
                ],
            ]
            for arguments in commands:
                result = subprocess.run(
                    [sys.executable, *arguments],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    check=False,
                    env={
                        "PATH": "/usr/bin:/bin",
                        "LANG": "C",
                        "LC_ALL": "C",
                        "PYTHONDONTWRITEBYTECODE": "1",
                    })
                self.assertNotEqual(result.returncode, 0)

            overlap = subprocess.run(
                [
                    sys.executable,
                    str(REPOSITORY /
                        "scripts/build_heptatrader_evidence_ingestion_request.py"),
                    "--index", str(fixture.index_path),
                    "--evidence-root", str(REPOSITORY),
                    "--evidence-set-manifest", str(fixture.receipt_path),
                ],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=False,
                env={
                    "PATH": "/usr/bin:/bin",
                    "LANG": "C",
                    "LC_ALL": "C",
                    "PYTHONDONTWRITEBYTECODE": "1",
                })
            self.assertEqual(overlap.returncode, 78)
            self.assertIn(b"overlaps evidence", overlap.stderr)

    def test_missing_object_and_readback_drift_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(Path(temporary))
            missing = deepcopy(fixture.receipt)
            missing["statement"]["objects"] = []
            path = Path(temporary) / "missing.json"
            fixture._write_json(path, missing)
            with self.assertRaisesRegex(
                    receipt_verifier.IngestionReceiptError, "count"):
                fixture.verify(path)
            drift = deepcopy(fixture.receipt)
            drift["statement"]["objects"][0]["readback_sha256"] = "00" * 32
            path = Path(temporary) / "drift.json"
            fixture._write_json(path, drift)
            with self.assertRaisesRegex(
                    receipt_verifier.IngestionReceiptError, "closure"):
                fixture.verify(path)

    def test_weak_or_short_retention_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(Path(temporary))
            weak = deepcopy(fixture.receipt)
            weak["statement"]["objects"][0]["retention"]["legal_hold"] = False
            path = Path(temporary) / "weak.json"
            fixture._write_json(path, weak)
            with self.assertRaisesRegex(
                    receipt_verifier.IngestionReceiptError, "legal hold"):
                fixture.verify(path)

    def test_extreme_finite_timestamp_fails_with_domain_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(Path(temporary), finite=True)
            forged = deepcopy(fixture.receipt)
            statement = forged["statement"]
            statement["ingested_at"] = "9999-12-31T23:59:55+00:00"
            statement["verified_at"] = "9999-12-31T23:59:56+00:00"
            statement["signed_at"] = "9999-12-31T23:59:57+00:00"
            statement["objects"][0]["retention"]["anchor_at"] = (
                statement["ingested_at"])
            statement["objects"][0]["retention"]["retain_until"] = (
                "9999-12-31T23:59:59+00:00")
            path = Path(temporary) / "extreme-time.json"
            fixture._write_json(path, forged)
            with self.assertRaisesRegex(
                    receipt_verifier.IngestionReceiptError,
                    "retention interval"):
                fixture.verify(path)
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(Path(temporary), finite=True)
            short = deepcopy(fixture.receipt)
            short["statement"]["objects"][0]["retention"]["retain_until"] = (
                short["statement"]["verified_at"])
            path = Path(temporary) / "short.json"
            fixture._write_json(path, short)
            with self.assertRaisesRegex(
                    receipt_verifier.IngestionReceiptError, "too short"):
                fixture.verify(path)
            delayed = deepcopy(fixture.receipt)
            delayed["statement"]["signed_at"] = datetime(
                2026, 1, 2, 0, 1, 6, tzinfo=timezone.utc).isoformat()
            fixture.resign(delayed)
            path = Path(temporary) / "delayed-signing.json"
            fixture._write_json(path, delayed)
            with self.assertRaisesRegex(
                    receipt_verifier.IngestionReceiptError, "too short"):
                fixture.verify(
                    path,
                    verification_time=datetime(
                        2026, 1, 2, 0, 2, 0, tzinfo=timezone.utc))

    def test_retention_must_be_active_at_verification_time(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(Path(temporary), finite=True)
            retain_until = datetime.fromisoformat(
                fixture.receipt["statement"]["objects"][0][
                    "retention"]["retain_until"])
            with self.assertRaisesRegex(
                    receipt_verifier.IngestionReceiptError, "expired"):
                fixture.verify(
                    verification_time=retain_until)
            with self.assertRaisesRegex(
                    receipt_verifier.IngestionReceiptError, "expired"):
                fixture.verify(
                    verification_time=retain_until + timedelta(seconds=1))
            verified = fixture.verify(
                verification_time=retain_until - timedelta(seconds=1))
            self.assertEqual(
                verified["retention_status"],
                "test-evaluated-current-policy-satisfied")
            self.assertEqual(
                verified["objects"][0]["status"],
                "active-at-verification-time")

    def test_legal_hold_requires_fresh_signed_attestation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(Path(temporary))
            verified_at = datetime.fromisoformat(
                fixture.receipt["statement"]["verified_at"])
            maximum = fixture.trust[
                "legal_hold_attestation_max_age_seconds"]
            boundary = verified_at + timedelta(seconds=maximum)
            verified = fixture.verify(verification_time=boundary)
            self.assertEqual(
                verified["objects"][0]["attestation_age_seconds"],
                float(maximum))
            with self.assertRaisesRegex(
                    receipt_verifier.IngestionReceiptError, "stale"):
                fixture.verify(
                    verification_time=boundary + timedelta(microseconds=1))

    def test_system_trust_path_forbids_test_clock_injection(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(Path(temporary))
            with self.assertRaisesRegex(
                    receipt_verifier.IngestionReceiptError,
                    "cannot be supplied"):
                receipt_verifier.verify_receipt(
                    fixture.receipt_path,
                    fixture.request_path,
                    fixture.trust_path,
                    fixture.index_path,
                    fixture.evidence,
                    POLICY,
                    require_system_trust=True,
                    verification_time=fixture.verification_time,
                )

    def test_legal_hold_attestation_max_age_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(Path(temporary))
            for invalid in (
                    0, True,
                    receipt_verifier.
                    MAX_LEGAL_HOLD_ATTESTATION_AGE_SECONDS + 1):
                trust = deepcopy(fixture.trust)
                trust["legal_hold_attestation_max_age_seconds"] = invalid
                fixture._write_json(fixture.trust_path, trust)
                with self.subTest(invalid=invalid):
                    with self.assertRaisesRegex(
                            receipt_verifier.IngestionReceiptError,
                            "maximum age"):
                        fixture.verify()

    def test_duplicate_json_keys_and_symlinks_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(Path(temporary))
            path = Path(temporary) / "duplicate.json"
            original = fixture.receipt_path.read_text(encoding="utf-8")
            path.write_text(
                original.replace(
                    '"schema": "hepta.evidence-ingestion-receipt.v1",',
                    '"schema": "hepta.evidence-ingestion-receipt.v1",'
                    '"schema": "hepta.evidence-ingestion-receipt.v1",',
                    1),
                encoding="utf-8")
            path.chmod(0o600)
            with self.assertRaisesRegex(
                    receipt_verifier.IngestionReceiptError, "duplicate"):
                fixture.verify(path)
            link = Path(temporary) / "receipt-link.json"
            link.symlink_to(fixture.receipt_path.name)
            with self.assertRaisesRegex(
                    receipt_verifier.IngestionReceiptError,
                    "regular file|unstable|unsafe"):
                fixture.verify(link)

    def test_local_status_cannot_masquerade_as_external_receipt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(Path(temporary))
            forged_request = deepcopy(fixture.request)
            forged_request["upload_status"] = "verified-local-only"
            fixture.request_path.write_bytes(
                request_builder.canonical_json(forged_request) + b"\n")
            fixture.request_path.chmod(0o600)
            with self.assertRaisesRegex(
                    receipt_verifier.IngestionReceiptError,
                    "exact closure"):
                fixture.verify()

    def test_receipt_cannot_authorize_trading_or_source_removal(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-receipt-") as temporary:
            fixture = ReceiptFixture(Path(temporary))
            for field in (
                    "paper_authorized", "live_authorized",
                    "source_files_deleted", "source_removal_authorized"):
                forged = deepcopy(fixture.receipt)
                forged["statement"][field] = True
                fixture.resign(forged)
                path = Path(temporary) / f"{field}.json"
                fixture._write_json(path, forged)
                with self.assertRaisesRegex(
                        receipt_verifier.IngestionReceiptError, "safety"):
                    fixture.verify(path)

    def test_policy_index_trust_and_receipt_versions_require_json_integers(
            self) -> None:
        for invalid in (True, 1.0):
            with self.subTest(policy_version=invalid):
                with tempfile.TemporaryDirectory(
                        prefix="hepta-policy-version-") as temporary:
                    policy = json.loads(POLICY.read_text(encoding="utf-8"))
                    policy["version"] = invalid
                    path = Path(temporary) / "policy.json"
                    ReceiptFixture._write_json(path, policy)
                    with self.assertRaisesRegex(
                            index_builder.EvidenceIndexError,
                            "policy version"):
                        index_builder.load_policy(path)

        for invalid in (True, 2.0):
            with self.subTest(index_version=invalid):
                with tempfile.TemporaryDirectory(
                        prefix="hepta-index-version-") as temporary:
                    fixture = ReceiptFixture(Path(temporary))
                    index = json.loads(
                        fixture.index_path.read_text(encoding="utf-8"))
                    index["version"] = invalid
                    fixture._write_json(fixture.index_path, index)
                    with self.assertRaisesRegex(
                            index_builder.EvidenceIndexError,
                            "unsupported evidence index"):
                        index_verifier.verify(
                            fixture.index_path, fixture.evidence, POLICY)

        for field in ("trust", "receipt"):
            for invalid in (True, 1.0):
                with self.subTest(field=field, version=invalid):
                    with tempfile.TemporaryDirectory(
                            prefix="hepta-receipt-version-") as temporary:
                        fixture = ReceiptFixture(Path(temporary))
                        if field == "trust":
                            trust = deepcopy(fixture.trust)
                            trust["version"] = invalid
                            fixture._write_json(fixture.trust_path, trust)
                            expected = "unsupported receipt trust policy"
                        else:
                            receipt = deepcopy(fixture.receipt)
                            receipt["version"] = invalid
                            fixture._write_json(
                                fixture.receipt_path, receipt)
                            expected = "unsupported ingestion receipt"
                        with self.assertRaisesRegex(
                                receipt_verifier.IngestionReceiptError,
                                expected):
                            fixture.verify()

    def test_index_retention_days_require_exact_json_type(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-index-retention-type-") as temporary:
            fixture = ReceiptFixture(Path(temporary), finite=True)
            index = json.loads(
                fixture.index_path.read_text(encoding="utf-8"))
            index["files"][0]["retention_days"] = float(
                index["files"][0]["retention_days"])
            index["records_sha256"] = hashlib.sha256(
                index_builder.canonical_json(index["files"])).hexdigest()
            fixture._write_json(fixture.index_path, index)
            with self.assertRaisesRegex(
                    index_builder.EvidenceIndexError, "retention drift"):
                index_verifier.verify(
                    fixture.index_path, fixture.evidence, POLICY)

    def test_request_exact_closure_is_json_type_strict(self) -> None:
        mutations = (
            ("version_bool", lambda request: request.__setitem__(
                "version", True)),
            ("version_float", lambda request: request.__setitem__(
                "version", 1.0)),
            ("object_count", lambda request: request.__setitem__(
                "object_count", float(request["object_count"]))),
            ("false_flag", lambda request: request.__setitem__(
                "source_files_deleted", 0)),
            ("index_version", lambda request: request["index"].__setitem__(
                "version", float(request["index"]["version"]))),
            ("object_size", lambda request: request["objects"][0].__setitem__(
                "size", float(request["objects"][0]["size"]))),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory(
                        prefix="hepta-request-types-") as temporary:
                    fixture = ReceiptFixture(Path(temporary))
                    request = deepcopy(fixture.request)
                    mutate(request)
                    fixture.request_path.write_bytes(
                        request_builder.canonical_json(request) + b"\n")
                    fixture.request_path.chmod(0o600)
                    receipt = deepcopy(fixture.receipt)
                    receipt["statement"]["request_sha256"] = hashlib.sha256(
                        fixture.request_path.read_bytes()).hexdigest()
                    fixture.resign(receipt)
                    fixture._write_json(fixture.receipt_path, receipt)
                    with self.assertRaisesRegex(
                            receipt_verifier.IngestionReceiptError,
                            "exact closure|unsupported ingestion request schema"):
                        fixture.verify()

    def test_unhashable_json_types_fail_with_domain_errors(self) -> None:
        trust_mutations = (
            ("status", lambda trust: trust.__setitem__(
                "production_receipt_status", [])),
            ("global_policy", lambda trust: trust.__setitem__(
                "allowed_retention_policy_sha256", [[]])),
            ("key_policy", lambda trust: trust["keys"][0].__setitem__(
                "allowed_retention_policy_sha256", [[]])),
        )
        for label, mutate in trust_mutations:
            with self.subTest(source="trust", label=label):
                with tempfile.TemporaryDirectory(
                        prefix="hepta-unhashable-") as temporary:
                    fixture = ReceiptFixture(Path(temporary))
                    trust = deepcopy(fixture.trust)
                    mutate(trust)
                    fixture._write_json(fixture.trust_path, trust)
                    with self.assertRaises(
                            receipt_verifier.IngestionReceiptError):
                        fixture.verify()

        with tempfile.TemporaryDirectory(
                prefix="hepta-unhashable-") as temporary:
            fixture = ReceiptFixture(Path(temporary))
            receipt = deepcopy(fixture.receipt)
            receipt["signature"]["key_id"] = []
            fixture._write_json(fixture.receipt_path, receipt)
            with self.assertRaises(receipt_verifier.IngestionReceiptError):
                fixture.verify()

        with tempfile.TemporaryDirectory(
                prefix="hepta-unhashable-") as temporary:
            fixture = ReceiptFixture(Path(temporary))
            index = json.loads(
                fixture.index_path.read_text(encoding="utf-8"))
            index["selection_mode"] = []
            fixture._write_json(fixture.index_path, index)
            with self.assertRaises(index_builder.EvidenceIndexError):
                index_verifier.verify(
                    fixture.index_path, fixture.evidence, POLICY)

        with tempfile.TemporaryDirectory(
                prefix="hepta-unhashable-") as temporary:
            policy = json.loads(POLICY.read_text(encoding="utf-8"))
            policy["rules"][0]["tier"] = []
            path = Path(temporary) / "policy.json"
            ReceiptFixture._write_json(path, policy)
            with self.assertRaises(index_builder.EvidenceIndexError):
                index_builder.load_policy(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
