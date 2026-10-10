"""Synthetic adversarial tests for read-only original policy provenance audit."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research"))
from train_v2 import original_policy_audit as a


class OriginalPolicyAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.release_root = self.root / "releases"
        self.release = self.release_root / "immutable-sha" / "EUR.USD"
        self.release.mkdir(parents=True)
        self.period = self.root / "period.json"
        self.period.write_bytes(b'{"execution_domain":"PAPER"}\n')
        self.period.chmod(0o600)
        self.runtime = self.release / "heptatrader_paper_driver.py"
        self.runtime.write_bytes(b"# fixture original pinned runtime\n")
        self.runtime.chmod(0o600)
        self.m = {
            "schema": "heptatrader.attribution-producer.v1",
            "execution_domain": "PAPER",
            "instrument": "EUR.USD",
            "period_path": str(self.period),
            "period_sha256": a.sha256(self.period.read_bytes()),
            "runtime_sha256": {"heptatrader_paper_driver.py": a.sha256(self.runtime.read_bytes())},
            "trading_behavior_changed": False,
            "journal_directory": str(self.root / "journal"),
        }
        self.manifest = self.release / "manifest.json"
        self.seal()

    def seal(self):
        self.manifest.write_text(json.dumps(self.m, sort_keys=True))
        self.manifest.chmod(0o600)

    def fake_original_binding(self, reg, instrument, period):
        self.assertEqual(instrument, "EUR.USD")
        self.assertEqual(period, self.m["period_sha256"])
        return {"original-command": {
            "input_id": "a" * 64,
            "forecast_id": "b" * 64,
            "request_id": "original-request",
            "prompt_sha256": "c" * 64,
            "producer_manifest_sha256": reg["sha256"],
            "source_prefixes": [{"path": "/original/journal",
                                 "bytes": 123, "sha256": "e" * 64}],
            "binding_basis": "IDENTICAL_RETURNED_OBJECT_AT_ORIGINAL_DURABLE_PREPARED",
            "instrument": "EUR.USD",
            "side": "BUY",
            "model_confidence": 0.99,
        }}, []

    def verified(self, binding=None):
        return a.verify_release(self.manifest,
                                binder=binding or self.fake_original_binding)

    def test_real_file_bytes_are_verified_before_a_binding_is_counted(self):
        report = self.verified()
        self.assertEqual(report["exact_original_command_binding_count"], 1)
        self.assertEqual(report["runtime_file_count_verified"], 1)
        self.assertEqual(report["bound_command_side_counts"], {"BUY": 1})
        self.assertFalse(report["canonical_v2_product_admission"])

    def test_confidence_cannot_satisfy_behavior_propensity(self):
        report = self.verified()
        self.assertEqual(report["original_logged_behavior_propensities_attested"], 0)
        self.assertEqual(report["original_complete_candidate_sets_attested"], 0)

    def test_wrong_behavior_flag_rejected(self):
        self.m["trading_behavior_changed"] = "changed"
        self.seal()
        with self.assertRaisesRegex(ValueError, "ORIGINAL_PAPER_SCOPE"):
            self.verified()

    def test_changed_original_policy_is_verified_but_separate_cohort(self):
        self.m["trading_behavior_changed"] = True
        self.seal()
        report = self.verified()
        self.assertTrue(report["declared_trading_behavior_changed"])
        aggregate = a.audit_releases(self.release_root, verifier=lambda _: self.verified())
        self.assertEqual(aggregate["declared_behavior_changed_command_bindings"], 1)
        self.assertEqual(aggregate["unchanged_policy_command_bindings"], 0)
        self.assertEqual(aggregate["assessment"],
                         "HOLD_POLICY_COHORT_MISMATCH_AND_MISSING_PROPENSITY")
        self.assertEqual(aggregate["original_logged_behavior_propensities_attested"], 0)

    def test_period_mutation_rejected(self):
        self.period.write_text("changed")
        with self.assertRaisesRegex(ValueError, "ORIGINAL_PERIOD_CHANGED"):
            self.verified()

    def test_runtime_mutation_rejected(self):
        self.runtime.write_text("# altered")
        with self.assertRaisesRegex(ValueError, "ORIGINAL_RUNTIME_SHA_CHANGED"):
            self.verified()

    def test_unsafe_runtime_basename_rejected(self):
        self.m["runtime_sha256"] = {"../driver.py": "a" * 64}
        self.seal()
        with self.assertRaisesRegex(ValueError, "ORIGINAL_RUNTIME_PATH_INVALID"):
            self.verified()

    def test_unreadable_manifest_scope_rejected(self):
        self.manifest.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "ATTRIBUTION_FILE_SCOPE"):
            self.verified()

    def test_symlink_manifest_rejected(self):
        self.manifest.rename(self.release / "old-manifest")
        self.manifest.symlink_to(self.release / "old-manifest")
        with self.assertRaisesRegex(ValueError, "MANIFEST_OR_RELEASE_SYMLINK"):
            self.verified()

    def test_parent_release_symlink_rejected(self):
        self.release.parent.rename(self.release_root / "renamed")
        self.release.parent.symlink_to(self.release_root / "renamed", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "MANIFEST_OR_RELEASE_SYMLINK"):
            self.verified()

    def test_nonidentical_prepared_basis_rejected(self):
        def invalid(reg, instrument, period):
            result, issues = self.fake_original_binding(reg, instrument, period)
            result["original-command"]["binding_basis"] = "CLOSE_TIMESTAMP"
            return result, issues
        with self.assertRaisesRegex(ValueError, "NONEXACT_ORIGINAL_COMMAND_BINDING"):
            self.verified(invalid)

    def test_different_registered_manifest_rejected(self):
        def invalid(reg, instrument, period):
            result, issues = self.fake_original_binding(reg, instrument, period)
            result["original-command"]["producer_manifest_sha256"] = "f" * 64
            return result, issues
        with self.assertRaisesRegex(ValueError, "NONEXACT_ORIGINAL_COMMAND_BINDING"):
            self.verified(invalid)

    def test_missing_ledger_prefix_rejected(self):
        def invalid(reg, instrument, period):
            result, issues = self.fake_original_binding(reg, instrument, period)
            result["original-command"]["source_prefixes"] = []
            return result, issues
        with self.assertRaisesRegex(ValueError, "NONEXACT_ORIGINAL_COMMAND_BINDING"):
            self.verified(invalid)

    def test_no_original_records_never_turns_into_pass(self):
        report = a.audit_releases(self.release_root, verifier=lambda _: {
            **self.verified(), "exact_original_command_binding_count": 0,
            "bound_command_side_counts": {}})
        self.assertEqual(report["assessment"], "HOLD_ORIGINAL_BINDINGS_UNVERIFIED")
        self.assertEqual(report["original_logged_behavior_propensities_attested"], 0)
        self.assertFalse(report["original_generator_root_trust_activated_by_audit"])
        self.assertEqual(report["broker_queries_or_orders_performed_by_audit"], 0)

    def test_unsafe_manifest_poison_does_not_get_dropped(self):
        self.m["execution_domain"] = "LIVE"
        self.seal()
        report = a.audit_releases(self.release_root, verifier=self.verified_by_path)
        self.assertEqual(report["unverified_release_count"], 1)
        self.assertEqual(report["verified_original_command_bindings"], 0)
        self.assertEqual(report["assessment"], "HOLD_ORIGINAL_BINDINGS_UNVERIFIED")

    def verified_by_path(self, manifest):
        return self.verified()

    def test_output_written_once_and_fsynced(self):
        report = a.audit_releases(self.release_root, verifier=lambda _: self.verified())
        root = self.root / "evidence"
        output = root / "receipt.json"
        os.chmod(self.root, 0o700)
        digest = a.write_once(output, report)
        self.assertEqual(digest, hashlib.sha256(output.read_bytes()).hexdigest())
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        before = output.read_bytes()
        with self.assertRaises(FileExistsError):
            a.write_once(output, report)
        self.assertEqual(output.read_bytes(), before)

    def test_group_readable_destination_rejected(self):
        root = self.root / "public"
        root.mkdir(mode=0o750)
        with self.assertRaisesRegex(ValueError, "PRIVATE_OUTPUT_DIRECTORY_REQUIRED"):
            a.write_once(root / "nope.json", {"assessment": "HOLD"})
        self.assertFalse((root / "nope.json").exists())

    def test_too_many_releases_rejected(self):
        for i in range(a.MAX_RELEASES + 1):
            d = self.release_root / ("extra-%d" % i) / "EUR.USD"
            d.mkdir(parents=True)
            (d / "manifest.json").write_text("bad")
        with self.assertRaisesRegex(ValueError, "RELEASE_COUNT_LIMIT"):
            a.audit_releases(self.release_root)

    def test_duplicate_original_command_across_releases_holds(self):
        second = self.release_root / "other" / "EUR.USD"
        second.mkdir(parents=True)
        (second / "manifest.json").write_text("{}")
        report = a.audit_releases(self.release_root, verifier=lambda _: self.verified())
        self.assertEqual(report["verified_original_command_bindings"], 2)
        self.assertEqual(report["unique_instrument_command_identities"], 1)
        self.assertEqual(report["cross_release_duplicate_command_ids"], 1)
        self.assertEqual(report["assessment"], "HOLD_ORIGINAL_BINDINGS_UNVERIFIED")

    def test_empty_root_has_no_fabricated_evidence(self):
        empty = self.root / "empty"
        empty.mkdir()
        result = a.audit_releases(empty)
        self.assertEqual(result["original_release_count"], 0)
        self.assertEqual(result["verified_original_command_bindings"], 0)
        self.assertFalse(result["canonical_dataset_snapshot_v3_frozen_by_audit"])


if __name__ == "__main__":
    unittest.main()
