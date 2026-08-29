#!/usr/bin/env python3

from __future__ import annotations

from copy import deepcopy
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
VERSION = "0.1.0-beta.1-round122"
BASELINE = REPOSITORY / "source-baseline.json"
sys.path.insert(0, str(REPOSITORY / "scripts"))

import verify_heptatrader_source_baseline as verifier  # noqa: E402
import hepta_release_check as release_check  # noqa: E402


class SourceBaselineTests(unittest.TestCase):
    @staticmethod
    def baseline() -> dict[str, object]:
        return json.loads(BASELINE.read_text(encoding="utf-8"))

    def test_current_source_baseline_round_trip(self) -> None:
        report = verifier.verify(REPOSITORY, BASELINE, VERSION)
        self.assertEqual(report["version"], VERSION)
        self.assertTrue(report["clean_checkout_certified"])
        self.assertFalse(report["release_authorized"])
        self.assertIsNone(report["blocked_reason"])

    def test_exact_schema_and_release_identity_are_required(self) -> None:
        mutations = {
            "unexpected": "value",
            "version": "WRONG-ROUND",
            "source_baseline_frozen": False,
            "clean_checkout_certified": False,
            "release_authorized": True,
            "blocked_reason": "WRONG",
        }
        for field, value in mutations.items():
            document = deepcopy(self.baseline())
            document[field] = value
            with self.subTest(field=field):
                with self.assertRaises(verifier.SourceBaselineError):
                    verifier.validate_baseline_document(document, VERSION)

    def test_version_type_and_generated_at_are_strict(self) -> None:
        for field, value in (
                ("version", True),
                ("version", 1.0),
                ("generated_at", "2026-07-24T00:00:00"),
                ("generated_at", 1)):
            document = deepcopy(self.baseline())
            document[field] = value
            with self.subTest(field=field, value=value):
                with self.assertRaises(verifier.SourceBaselineError):
                    verifier.validate_baseline_document(document, VERSION)

    def test_git_identity_is_bound_to_the_current_source(self) -> None:
        with self.assertRaisesRegex(
                verifier.SourceBaselineError, "Git identity drift"):
            verifier._verify_git_identity(
                REPOSITORY, BASELINE, "0" * 40, VERSION)

    def test_single_baseline_manifest_commit_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-baseline-git-") as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.name", "Hepta Test"],
                cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "hepta@example.invalid"],
                cwd=root, check=True)
            (root / "source.txt").write_text("source\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "source"], cwd=root, check=True)
            source_head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                text=True, capture_output=True).stdout.strip()
            baseline = root / "release-manifests/v/manifest.json"
            baseline.parent.mkdir(parents=True)
            baseline.write_text("{}\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "baseline"], cwd=root, check=True)
            verifier._verify_git_identity(
                root, baseline, source_head, "fixture")

            baseline.write_text('{"dirty":true}\n', encoding="utf-8")
            with self.assertRaisesRegex(
                    verifier.SourceBaselineError, "Git identity drift"):
                verifier._verify_git_identity(
                    root, baseline, source_head, "fixture")

    def test_contiguous_baseline_seal_commits_are_accepted(self) -> None:
        """A refresh may take more than one metadata-only commit."""
        with tempfile.TemporaryDirectory(
                prefix="hepta-baseline-seal-chain-") as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.name", "Hepta Test"],
                cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "hepta@example.invalid"],
                cwd=root, check=True)
            (root / "source.txt").write_text("source\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "source"], cwd=root, check=True)
            source_head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                text=True, capture_output=True).stdout.strip()
            baseline = root / "release-manifests/v/manifest.json"
            baseline.parent.mkdir(parents=True)
            baseline.write_text("{\"seal\":1}\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "baseline seal 1"],
                cwd=root, check=True)
            baseline.write_text("{\"seal\":2}\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "baseline seal 2"],
                cwd=root, check=True)
            verifier._verify_git_identity(
                root, baseline, source_head, "fixture")

    def test_non_baseline_commit_breaks_seal_chain(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-baseline-seal-drift-") as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.name", "Hepta Test"],
                cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "hepta@example.invalid"],
                cwd=root, check=True)
            (root / "source.txt").write_text("source\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "source"], cwd=root, check=True)
            source_head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                text=True, capture_output=True).stdout.strip()
            baseline = root / "release-manifests/v/manifest.json"
            baseline.parent.mkdir(parents=True)
            baseline.write_text("{}\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "baseline seal"],
                cwd=root, check=True)
            # A source/code change between seals is never part of the
            # continuity exception, even if a later baseline commit exists.
            (root / "source.txt").write_text("changed\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "source change"],
                cwd=root, check=True)
            baseline.write_text("{\"seal\":2}\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "baseline seal 2"],
                cwd=root, check=True)
            with self.assertRaisesRegex(
                    verifier.SourceBaselineError, "non-baseline commit"):
                verifier._verify_git_identity(
                    root, baseline, source_head, "fixture")

    def test_unrelated_release_manifest_breaks_seal_chain(self) -> None:
        """A different release's manifest cannot extend this identity chain."""
        with tempfile.TemporaryDirectory(
                prefix="hepta-baseline-unrelated-manifest-") as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.name", "Hepta Test"],
                cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "hepta@example.invalid"],
                cwd=root, check=True)
            (root / "source.txt").write_text("source\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "source"], cwd=root, check=True)
            source_head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                text=True, capture_output=True).stdout.strip()
            baseline = root / "release-manifests/v/manifest.json"
            baseline.parent.mkdir(parents=True)
            baseline.write_text("{\"seal\":1}\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "baseline seal 1"],
                cwd=root, check=True)
            unrelated = root / "release-manifests/other/manifest.json"
            unrelated.parent.mkdir(parents=True)
            unrelated.write_text("{\"unrelated\":true}\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "unrelated manifest"],
                cwd=root, check=True)
            baseline.write_text("{\"seal\":2}\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "baseline seal 2"],
                cwd=root, check=True)
            with self.assertRaisesRegex(
                    verifier.SourceBaselineError, "non-baseline commit"):
                verifier._verify_git_identity(
                    root, baseline, source_head, "fixture")

    def test_agent_source_marker_carries_baseline_identity(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-baseline-agent-source-") as temporary:
            root = Path(temporary)
            baseline = root / "release-manifests/v/manifest.json"
            baseline.parent.mkdir(parents=True)
            baseline.write_text("{}\n", encoding="utf-8")
            marker = root / ".hepta/agent-os-source-manifest.json"
            marker.parent.mkdir()
            marker.write_text(json.dumps({
                "schema": "hepta.agent-os-source-bundle.v1",
                "release_version": "fixture",
                "parent_strict_source": {
                    "schema": "hepta.clean-source-bundle.v2",
                    "git_head": "2" * 40,
                },
            }) + "\n", encoding="utf-8")
            marker.chmod(0o644)
            verifier._verify_git_identity(
                root, baseline, "2" * 40, "fixture")


class ReleaseReceiptContractTests(unittest.TestCase):
    @staticmethod
    def _sealed(body: dict[str, object]) -> dict[str, object]:
        result = dict(body)
        result["body_sha256"] = (
            "sha256:" + hashlib.sha256(
                release_check._canonical_json(body)).hexdigest())
        return result

    def test_receipt_reader_rejects_symlinks_and_oversize_documents(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-release-receipt-") as temporary:
            root = Path(temporary)
            receipt = root / "receipt.json"
            receipt.write_text("{}\n", encoding="utf-8")
            self.assertEqual(release_check._read_receipt_bytes(receipt), b"{}\n")

            alias = root / "alias.json"
            alias.symlink_to(receipt)
            with self.assertRaises((OSError, ValueError)):
                release_check._read_receipt_bytes(alias)
            payload, error = release_check._read_soak_json(alias)
            self.assertIsNone(payload)
            self.assertIsNotNone(error)

            oversized = root / "oversized.json"
            oversized.write_bytes(b"x" * (release_check.MAX_RECEIPT_BYTES + 1))
            with self.assertRaises((OSError, ValueError)):
                release_check._read_receipt_bytes(oversized)

    def test_summary_writer_is_anchored_and_atomic(self) -> None:
        """A report race cannot truncate or redirect an unrelated file."""
        with tempfile.TemporaryDirectory(prefix="hepta-release-summary-") as temporary:
            root = Path(temporary)
            private = root / "private"
            private.mkdir(mode=0o700)
            outside = root / "outside"
            outside.mkdir(mode=0o700)
            sentinel = outside / "sentinel.json"
            sentinel.write_text("do-not-overwrite\n", encoding="utf-8")
            sentinel.chmod(0o600)

            report = private / "report.json"
            release_check._safe_write_json(report, {"value": 1})
            self.assertEqual(report.stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                report.read_bytes(), release_check._canonical_json({"value": 1}))
            # Re-publication replaces the prior safe leaf atomically.
            release_check._safe_write_json(report, {"value": 2})
            self.assertEqual(
                report.read_bytes(), release_check._canonical_json({"value": 2}))

            symlink = private / "symlink.json"
            symlink.symlink_to(sentinel)
            with self.assertRaises((OSError, ValueError)):
                release_check._safe_write_json(symlink, {"forged": True})
            self.assertEqual(sentinel.read_text(encoding="utf-8"),
                             "do-not-overwrite\n")

            hardlink = private / "hardlink.json"
            hardlink.hardlink_to(sentinel)
            with self.assertRaises((OSError, ValueError)):
                release_check._safe_write_json(hardlink, {"forged": True})
            self.assertEqual(sentinel.read_text(encoding="utf-8"),
                             "do-not-overwrite\n")

            parent_alias = root / "parent-alias"
            parent_alias.symlink_to(outside, target_is_directory=True)
            with self.assertRaises((OSError, ValueError)):
                release_check._safe_write_json(
                    parent_alias / "escaped.json", {"forged": True})
            self.assertFalse((outside / "escaped.json").exists())

            nested = root / "new-parent" / "nested" / "report.json"
            release_check._safe_write_json(nested, {"created": True})
            self.assertEqual(
                nested.read_bytes(), release_check._canonical_json({"created": True}))
            self.assertEqual(nested.stat().st_mode & 0o777, 0o600)

            # A restrictive umask must not make a successful atomic publish
            # look like a post-publication identity failure.
            umask_before = os.umask(0o777)
            try:
                restrictive = private / "restrictive-umask.json"
                release_check._safe_write_json(restrictive, {"ok": True})
            finally:
                os.umask(umask_before)
            self.assertEqual(
                restrictive.read_bytes(), release_check._canonical_json({"ok": True}))
            self.assertEqual(restrictive.stat().st_mode & 0o777, 0o600)

            failed = private / "failed.json"
            with mock.patch.object(
                    release_check.os, "replace",
                    side_effect=OSError("forced publication failure")):
                with self.assertRaisesRegex(OSError, "forced publication failure"):
                    release_check._safe_write_json(failed, {"private": True})
            self.assertFalse(failed.exists())
            orphans = list(private.glob(".failed.json.tmp-*"))
            self.assertEqual(len(orphans), 1)
            self.assertEqual(orphans[0].stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                orphans[0].read_bytes(),
                release_check._canonical_json({"private": True}))

    def test_candidate_marker_is_rejected_at_any_receipt_depth(self) -> None:
        self.assertFalse(release_check._candidate_free({
            "nested": {"paper_test_admission_candidate": True}}))
        self.assertFalse(release_check._candidate_free([
            {"nested": [{"paper_test_admission_candidate": True}]}]))
        self.assertTrue(release_check._candidate_free({
            "nested": {"paper_test_admission_candidate": False}}))

    def test_receipt_schema_is_exact_and_candidate_remains_authority_free(self) -> None:
        rootful = {
            "schema": release_check.ROOTFUL_SCHEMA,
            "passed": True,
            "certification_level": "containerized-effective-systemd-rehearsal",
            "boundary": dict(release_check.ROOTFUL_BOUNDARY),
        }
        with tempfile.TemporaryDirectory(prefix="hepta-release-schema-") as temporary:
            path = Path(temporary) / "rootful.json"
            path.write_text(json.dumps(rootful), encoding="utf-8")
            path.chmod(0o600)
            accepted = release_check._receipt(
                path, "ROOTFUL", release_check.ROOTFUL_SCHEMA,
                required_fields=release_check.ROOTFUL_REQUIRED_FIELDS,
                validator=release_check._validate_rootful_receipt)
            # The former Docker/rootful rehearsal is diagnostic only.  PAPER
            # admission now accepts only the native three-VM aggregate, which
            # must be reconstructed from its raw variant receipts.
            self.assertFalse(accepted["pass"])
            rootful["schema"] = "hepta.execution-rootful-systemd-gate.v99"
            path.write_text(json.dumps(rootful), encoding="utf-8")
            rejected = release_check._receipt(
                path, "ROOTFUL", release_check.ROOTFUL_SCHEMA,
                required_fields=release_check.ROOTFUL_REQUIRED_FIELDS,
                validator=release_check._validate_rootful_receipt)
            self.assertFalse(rejected["pass"])

            candidate_body = {
                "schema": release_check.PAPER_AUTHORITY_SCHEMA,
                "version": 1,
                "status": "GO",
                "paper_test_admission_candidate": True,
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_authorized": False,
                "direct_broker_access": False,
                "order_submission_authorized": False,
                "authorization_effect": "NONE_READ_ONLY_CANDIDATE_ONLY",
                "findings": [],
            }
            path.write_text(
                json.dumps(self._sealed(candidate_body)), encoding="utf-8")
            path.chmod(0o600)
            accepted = release_check._receipt(
                path, "PAPER", release_check.PAPER_AUTHORITY_SCHEMA,
                required_fields=release_check.PAPER_AUTHORITY_REQUIRED_FIELDS,
                validator=release_check._validate_paper_authority_receipt)
            self.assertTrue(accepted["pass"])
            candidate_body["paper_authorized"] = True
            path.write_text(
                json.dumps(self._sealed(candidate_body)), encoding="utf-8")
            path.chmod(0o600)
            rejected = release_check._receipt(
                path, "PAPER", release_check.PAPER_AUTHORITY_SCHEMA,
                required_fields=release_check.PAPER_AUTHORITY_REQUIRED_FIELDS,
                validator=release_check._validate_paper_authority_receipt)
            self.assertFalse(rejected["pass"])

            p1_body = {
                "schema": release_check.P1_SCHEMA,
                "run_id": "p1-test",
                "decision": "GO",
                "passed": True,
                "rehearsal_passed": True,
                "certification_ready": True,
                "certification_blockers": [],
                "scope":
                    "p1-campaign-coordinator-rootful-liveness-prerequisite-only",
                "production_mode":
                    "PRODUCTION_REVIEWED_ROOTFUL_CERTIFICATION",
                "paper_test_admission_candidate": False,
                "paper_admission_authorized": False,
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_authorized": False,
                "direct_broker_access": False,
                "order_submission_authorized": False,
                "boundary": dict(release_check.P1_BOUNDARY),
            }
            path.write_text(
                json.dumps(self._sealed(p1_body)), encoding="utf-8")
            path.chmod(0o600)
            accepted = release_check._receipt(
                path, "P1", release_check.P1_SCHEMA,
                required_fields=release_check.P1_REQUIRED_FIELDS,
                validator=release_check._validate_p1_receipt)
            self.assertTrue(accepted["pass"])
            p1_body["paper_test_admission_candidate"] = True
            path.write_text(
                json.dumps(self._sealed(p1_body)), encoding="utf-8")
            path.chmod(0o600)
            rejected = release_check._receipt(
                path, "P1", release_check.P1_SCHEMA,
                required_fields=release_check.P1_REQUIRED_FIELDS,
                validator=release_check._validate_p1_receipt)
            self.assertFalse(rejected["pass"])

    def test_optional_rc_summary_is_bound_to_current_config_and_soak(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-rc-summary-") as temporary:
            root = Path(temporary)
            build = root / "build"
            build.mkdir()
            soak = build / "execution-gateway-short-soak.json"
            soak.write_text("{\"soak\":\"fixture\"}\n", encoding="utf-8")
            soak.chmod(0o600)
            soak_digest = release_check._protected_file_sha256(soak)
            config_digest = "a" * 64
            profile = release_check.SoakProfile.resolve("release")
            summary = {
                "schema": release_check.RELEASE_CHECK_SCHEMA,
                "phase": "rc",
                "overall": "PASS",
                "profile": "paper",
                "config_sha256": config_digest,
                "soak_profile": profile.name,
                "requested_rounds": profile.rounds,
                "soak_report": str(soak),
                "soak_report_sha256": soak_digest,
                "authority_granted": False,
                "paper_test_admission_candidate": False,
                "paper_admission_authorized": False,
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_authorized": False,
                "direct_broker_access": False,
                "order_submission_authorized": False,
                "checks": [
                    {"name": name, "pass": True,
                     "artifacts": ([str(soak)]
                                   if name == "EXECUTION_GATEWAY_SOAK"
                                   else []),
                     **({"soak_report_sha256": soak_digest}
                        if name == "EXECUTION_GATEWAY_SOAK" else {})}
                    for name in release_check.RC_SUMMARY_REQUIRED_CHECKS
                ],
            }
            rc = root / "rc-summary.json"
            rc.write_text(json.dumps(summary), encoding="utf-8")
            rc.chmod(0o600)
            accepted = release_check._validate_rc_summary(
                rc,
                expected_config_sha256=config_digest,
                expected_profile="paper",
                expected_soak_profile=profile,
                expected_soak_report=soak,
                expected_soak_report_sha256=soak_digest,
            )
            self.assertTrue(accepted["pass"], accepted)

            for field, value in (
                    ("phase", "paper"),
                    ("overall", "FAIL"),
                    ("config_sha256", "b" * 64),
                    ("soak_report_sha256", "sha256:" + "b" * 64),
                    ("paper_authorized", True)):
                mutated = dict(summary)
                mutated[field] = value
                rc.write_text(json.dumps(mutated), encoding="utf-8")
                rc.chmod(0o600)
                rejected = release_check._validate_rc_summary(
                    rc,
                    expected_config_sha256=config_digest,
                    expected_profile="paper",
                    expected_soak_profile=profile,
                    expected_soak_report=soak,
                    expected_soak_report_sha256=soak_digest,
                )
                self.assertFalse(rejected["pass"], (field, rejected))


if __name__ == "__main__":
    unittest.main(verbosity=2)
