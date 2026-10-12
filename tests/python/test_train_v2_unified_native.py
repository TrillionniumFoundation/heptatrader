"""Adversarial unified native/source cutoff boundary, fixture-only test data."""
import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from train_v2 import full_canonical_workspace as upstream
from train_v2.full_native_source_cutoff import (
    BIN_PIN, CODE_PIN, check_qualification, check_original_test_event,
    run, SCHEMA,
)
from test_train_v2_native_independent import fixture


class UnifiedNativeSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.canonical_source = self.root / "original-full-workspace"
        self.canonical_source.mkdir(mode=0o700)
        self.logs = self.root / "native-logs"
        self.logs.mkdir(mode=0o700)
        self.source_state = {
            "commit": upstream.PIN, "tree": "test-only-tree",
            "tracked_files": 8665, "cargo_lock_sha256": upstream.LOCK,
        }
        self.metadata = {
            "workspace_members": 193, "resolved_packages": 1517,
            "metadata_sha256": "test-only-metadata",
            "offline_locked_full_dependency_graph": True,
        }
        items = []
        for name, (passed, ignored, identity) in upstream.CRATES.items():
            command = [
                "cargo", "test", "--locked", "--offline", "--manifest-path",
                str(self.canonical_source / "codex-rs/Cargo.toml"),
                "-p", name,
            ]
            command += (["--test", "terminal_cell_owner"]
                        if name == "codex-hepta-agentd" else ["--lib"])
            result = {
                "passed": passed, "failed": 0, "ignored": ignored,
                "measured": 0, "filtered": 0,
            }
            log = (
                identity + "\n"
                + f"test result: ok. {passed} passed; 0 failed; "
                + f"{ignored} ignored; 0 measured; 0 filtered out; "
                + "finished in 0.01s\n"
            ).encode()
            (self.logs / (name + ".log")).write_bytes(log)
            items.append({
                "package": name, "command": command, "test_result": result,
                "raw_test_log_sha256": hashlib.sha256(log).hexdigest(),
            })
        self.native = {
            "schema": "heptatrader.train-v2-full-original-canonical-workspace.v1",
            "actual_pinned_git": self.source_state,
            "actual_offline_locked_dependency_resolution": self.metadata,
            "original_package_test_evidence": items,
            "total_native_passed": 212, "total_native_ignored": 3,
            "product_root_signer_enrolled": False,
            "production_activation_authorized": False,
            "actual_trade_request_prepared_bound": False,
            "llm_trade_action_propensity_known": False,
            "signed_production_current_owner": False,
            "original_broker_economic_alpha_proven": False,
            "model_trained_or_promoted": False,
            "trading_authority": False,
            "broker_orders_sent": 0, "broker_requests_sent": 0,
        }
        self.source_dir = self.root / "source"
        self.native_dir = self.root / "native"
        self.source_dir.mkdir(mode=0o700)
        self.native_dir.mkdir(mode=0o700)
        self.source, self.ack, self.precommit, self.independent = fixture()
        self.ack["native_chain_digest"] = "c" * 64
        self.bytes = {
            "ledger": b"L" * 707,
            "witness": b"W" * 160,
            "native-prewarm-public-ack.json":
                json.dumps(self.ack, sort_keys=True).encode() + b"\n",
        }
        self._write(self.source_dir / "source-readiness-outcome.json",
                    json.dumps(self.source, sort_keys=True).encode() + b"\n")
        self._write(self.native_dir / "native-prewarm-public-ack.json",
                    self.bytes["native-prewarm-public-ack.json"])
        self._write(self.native_dir / "native-prewarm-intent.json",
                    json.dumps(self.precommit, sort_keys=True).encode() + b"\n")
        for key in ("ledger", "witness"):
            self._write(self.native_dir / key, self.bytes[key])
        self.readback = {
            "schema": "heptatrader.train-v2-testonly-original-native-independent-readback.v1",
            "test_only": True,
            "source_independent_report_id": self.independent["report_id"],
            "original_source_public_chain_verified": True,
            "copied_corrupted_witness_refused": True,
            "copied_lost_acknowledged_ledger_refused": True,
            "original_typed_native_decision_witness_reopened": True,
            "canonical_product_root_enrolled": False,
            "actual_model_inference": False,
            "original_broker_prepared": False,
            "economic_alpha_observed": False,
            "broker_orders_sent": 0,
            "original_test_recovered_chain_digest": self.ack["native_chain_digest"],
            "native_original_sha256": {
                name: hashlib.sha256(raw).hexdigest()
                for name, raw in self.bytes.items()},
            "rust_native_program_sha256": CODE_PIN,
            "rust_compiled_test_binary_sha256": BIN_PIN,
        }

    @staticmethod
    def _write(path, raw):
        path.write_bytes(raw)
        path.chmod(0o600)

    def native_check(self, native=None):
        with patch.object(upstream, "verify_source", return_value=self.source_state):
            with patch.object(upstream, "resolve_full_graph", return_value=self.metadata):
                return check_qualification(
                    self.native if native is None else native,
                    self.logs, self.canonical_source)

    def event_check(self, readback=None):
        with patch("train_v2.full_native_source_cutoff.verify_original",
                   return_value=self.independent):
            return check_original_test_event(
                self.source_dir, self.native_dir,
                self.readback if readback is None else readback)

    def test_both_independent_proofs_pass_in_fixture(self):
        out = self.native_check()
        self.assertEqual(out["native_test_passed"], 212)
        self.assertEqual(out["native_test_ignored"], 3)
        event = self.event_check()
        self.assertEqual(event["cutoff"]["ack_before_source_decision_cutoff_ms"], 480)
        self.assertFalse(event["original_opaque_policy_evaluated"])

    def test_changed_actual_native_log_fails(self):
        path = self.logs / "codex-hepta-learning-ledger.log"
        path.write_bytes(path.read_bytes() + b"change\n")
        with self.assertRaisesRegex(ValueError, "UNIFIED_NATIVE_TEST_LOG_CHANGED"):
            self.native_check()

    def test_forged_native_test_command_fails(self):
        bad = copy.deepcopy(self.native)
        bad["original_package_test_evidence"][0]["command"][0] = "echo"
        with self.assertRaisesRegex(ValueError, "UNIFIED_NATIVE_TEST_COMMAND_CHANGED"):
            self.native_check(bad)

    def test_native_test_counts_cannot_be_substituted(self):
        bad = copy.deepcopy(self.native)
        bad["total_native_passed"] = 211
        with self.assertRaisesRegex(ValueError, "UNIFIED_NATIVE_PASS_COUNT_INCOMPLETE"):
            self.native_check(bad)

    def test_fake_native_owner_grant_fails(self):
        bad = copy.deepcopy(self.native)
        bad["product_root_signer_enrolled"] = True
        with self.assertRaisesRegex(ValueError, "UNIFIED_NATIVE_PRODUCT_AUTHORITY_FALSIFIED"):
            self.native_check(bad)

    def test_193_member_metadata_cannot_be_forged(self):
        bad = copy.deepcopy(self.native)
        bad["actual_offline_locked_dependency_resolution"]["workspace_members"] = 192
        with self.assertRaisesRegex(ValueError, "UNIFIED_NATIVE_DEPENDENCY_GRAPH_CHANGED"):
            self.native_check(bad)

    def test_source_cutoff_plus_one_is_denied_even_if_before_quote_expiry(self):
        self.ack["native_witness_ack_at_ms"] = (
            self.source["true_new_source_decision_cutoff_ms"] + 1)
        self._write(self.native_dir / "native-prewarm-public-ack.json",
                    json.dumps(self.ack, sort_keys=True).encode() + b"\n")
        with self.assertRaisesRegex(ValueError, "TEST_NATIVE_ORIGINAL_TIMELINE_NOT_CAUSAL"):
            self.event_check()

    def test_recovered_native_source_report_must_identify_same_event(self):
        bad = copy.deepcopy(self.readback)
        bad["source_independent_report_id"] = "f" * 64
        with self.assertRaisesRegex(ValueError,
                                    "UNIFIED_PREVIOUS_RECOVERY_DIFFERENT_SOURCE_EVENT"):
            self.event_check(bad)

    def test_native_original_bytes_tamper_fails(self):
        self._write(self.native_dir / "ledger", b"X" + b"L" * 706)
        with self.assertRaisesRegex(ValueError, "UNIFIED_ORIGINAL_NATIVE_EVIDENCE_CHANGED"):
            self.event_check()

    def test_native_original_ledger_length_fails_before_digests(self):
        self._write(self.native_dir / "ledger", b"L" * 72)
        with self.assertRaisesRegex(ValueError, "UNIFIED_NATIVE_ORIGINAL_TYPE_OR_SIZE_CHANGED"):
            self.event_check()

    def test_wrong_rust_binary_identity_fails(self):
        bad = copy.deepcopy(self.readback)
        bad["rust_compiled_test_binary_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "UNIFIED_NATIVE_RUST_TEST_PROGRAM_DRIFT"):
            self.event_check(bad)

    def test_wrong_fixed_policy_propensity_fails(self):
        self.ack["selected_logged_q32_propensity"] = int(.99 * 2**32)
        self._write(self.native_dir / "native-prewarm-public-ack.json",
                    json.dumps(self.ack, sort_keys=True).encode() + b"\n")
        with self.assertRaisesRegex(ValueError, "ORIGINAL_SOURCE_NATIVE_ACK_IDENTITY_MISMATCH"):
            self.event_check()

    def test_any_product_power_claim_in_old_report_is_rejected(self):
        bad = copy.deepcopy(self.readback)
        bad["canonical_product_root_enrolled"] = True
        with self.assertRaisesRegex(ValueError, "UNIFIED_PREVIOUS_READBACK_NOT_TEST_ONLY"):
            self.event_check(bad)
