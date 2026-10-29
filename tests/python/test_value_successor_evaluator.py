"""Successor cadence tests: retain evidence, skip unchanged work, honor changes."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from historical_training import value_successor_evaluator as successor


class SuccessorCadenceTests(unittest.TestCase):
    def setUp(self):
        # run() sets a private CLI mask; keep that side effect inside this
        # fixture even when a test fails before its temporary tree is removed.
        original_umask = os.umask(0o077)
        self.addCleanup(os.umask, original_umask)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.original = self.root / "original"
        self.target = self.root / "successor"
        self.source = self.original / "plans" / "plan" / "plan.json"
        self.source.parent.mkdir(parents=True)
        (self.source.parent / "predictions").mkdir()
        (self.target / "plans" / "plan").mkdir(parents=True)
        self.journal = self.root / "journal"
        self.journal.mkdir()
        self.manifest = self.root / "manifest.json"
        self.write(self.manifest, {"journal_directory": str(self.journal)})
        self.plan = {"plan_id": "plan", "input_start_ms": 1000000,
                     "input_end_ms": 3000000, "outcome_end_ms": 4000000,
                     "producer_registration": {"manifest": str(self.manifest)}}
        self.write(self.source, self.plan)
        self.write(self.source.parent / "registration.json", {})
        self.write(self.target / "plans" / "plan" / "plan.json", self.plan)
        self.c = {"forward_root": str(self.original),
                  "oms_path": str(self.root / "oms.jsonl"),
                  "period_path": str(self.root / "period.json"),
                  "witness_trust_path": str(self.root / "trust.json"),
                  "role_key_path": str(self.root / "evaluator.key"),
                  "attribution_directory": str(self.journal),
                  "decision_attribution_registrations": {},
                  "export_directories": {}}
        for key in ("oms_path", "period_path", "witness_trust_path", "role_key_path"):
            self.write(Path(self.c[key]), {})
        self.now = 2000000
        self.calls = 0
        self.during_evaluate = None
        self.patch(successor, "_prepare", return_value=(self.c, self.target, self.plan, self.source))
        self.patch(successor, "verify_record", return_value=True)
        self.patch(successor, "_evaluate", side_effect=self.evaluate)
        self.patch(successor.time, "time", side_effect=lambda: self.now / 1000)

    def patch(self, obj, name, **kwargs):
        patcher = mock.patch.object(obj, name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value) + "\n")
        path.chmod(0o600)

    def evaluate(self, c, target, plan, source):
        self.calls += 1
        if self.during_evaluate:
            self.during_evaluate()
        value = {"as_of_ms": self.now,
                 "state": "REJECTED" if self.now >= plan["outcome_end_ms"] else "PENDING",
                 "current_ledger_prefix": {"sha256": "a" * 64, "bytes": 100},
                 "financial_candidate_selected": False,
                 "observed_trades": 1, "total_original_trades": 2,
                 "unresolved_or_censored": 1}
        folder = target / "plans" / plan["plan_id"]
        successor.atomic(folder / "assessment.json", value)
        successor.atomic(folder / "assessment-signed.json",
                         {**value, "role_witness": {"signed_at_ms": self.now},
                          "signature": str(self.now)})

    def run_successor(self):
        return successor.run(self.root / "config.json", self.target, "b" * 64)

    def receipts(self):
        return list((self.target / "successor-receipts").glob("*.json"))

    def test_identical_inputs_only_update_heartbeat(self):
        first = self.run_successor()
        self.now += 1000
        second = self.run_successor()
        self.assertEqual(self.calls, 1)
        self.assertTrue(second["evaluation_skipped"])
        self.assertEqual(first["receipt_id"], second["receipt_id"])
        self.assertEqual(second["assessment_as_of_ms"], first["assessment_as_of_ms"])
        self.assertGreater(second["at_ms"], first["at_ms"])
        self.assertEqual(len(self.receipts()), 1)

    def test_health_heartbeat_is_ignored_but_loss_triggers_evaluation(self):
        health = self.journal / "producer.health.json"
        body = {"producer_id": "producer", "written_records": 4,
                "dropped": 0, "error": "", "at_ms": self.now}
        self.write(health, body)
        self.run_successor()
        self.now += 1000
        fresh = self.journal / "producer.next"
        self.write(fresh, {**body, "at_ms": self.now})
        os.replace(fresh, health)
        self.assertTrue(self.run_successor()["evaluation_skipped"])
        self.assertEqual(self.calls, 1)
        self.write(health, {**body, "dropped": 1, "at_ms": self.now})
        self.assertFalse(self.run_successor()["evaluation_skipped"])
        self.assertEqual(self.calls, 2)

    def test_ledger_append_and_original_prediction_commit_trigger_work(self):
        self.run_successor()
        with Path(self.c["oms_path"]).open("a") as handle:
            handle.write("{}\n")
        self.now += 1000
        self.assertFalse(self.run_successor()["evaluation_skipped"])
        prediction = self.source.parent / "predictions" / "request" / "commit.json"
        self.write(prediction, {"committed": True})
        self.now += 1000
        self.assertFalse(self.run_successor()["evaluation_skipped"])
        self.assertEqual(self.calls, 3)

    def test_outcome_deadline_recomputes_without_file_change(self):
        first = self.run_successor()
        self.now = self.plan["outcome_end_ms"]
        final = self.run_successor()
        self.assertEqual(self.calls, 2)
        self.assertFalse(final["evaluation_skipped"])
        self.assertEqual(final["state"], "REJECTED")
        self.assertNotEqual(first["receipt_id"], final["receipt_id"])
        self.assertEqual(len(self.receipts()), 2)

    def test_same_semantics_after_revalidation_do_not_create_receipt(self):
        first = self.run_successor()
        self.write(Path(self.c["oms_path"]), {})
        self.now += 1000
        second = self.run_successor()
        self.assertEqual(self.calls, 2)
        self.assertNotEqual(first["result_sha256"], second["result_sha256"])
        self.assertEqual(first["receipt_id"], second["receipt_id"])
        self.assertFalse(second["receipt_created"])
        self.assertEqual(len(self.receipts()), 1)

    def test_input_race_does_not_create_reusable_checkpoint(self):
        self.during_evaluate = lambda: self.write(self.journal / "new.jsonl", {})
        first = self.run_successor()
        self.assertFalse(first["input_checkpoint_reusable"])
        self.assertFalse((self.target / "successor-input-checkpoint.json").exists())
        self.during_evaluate = None
        self.now += 1000
        self.run_successor()
        self.now += 1000
        self.assertTrue(self.run_successor()["evaluation_skipped"])
        self.assertEqual(self.calls, 2)

    def test_output_tampering_requires_full_evaluation(self):
        self.run_successor()
        self.write(self.target / "plans" / "plan" / "assessment.json", {"tampered": True})
        self.now += 1000
        self.assertFalse(self.run_successor()["evaluation_skipped"])
        self.assertEqual(self.calls, 2)

    def test_clock_regression_never_reuses_future_assessment(self):
        self.run_successor()
        self.now -= 1
        with self.assertRaisesRegex(ValueError, "CLOCK_REGRESSION"):
            self.run_successor()
        self.assertEqual(self.calls, 1)

    def test_finite_reverification_deadline(self):
        plan = {"input_start_ms": 1, "input_end_ms": 2, "outcome_end_ms": 3}
        self.assertEqual(successor.next_due_ms(plan, 10), 10 + successor.MAX_REUSE_MS)
        self.assertEqual(successor.next_due_ms(self.plan, self.now), self.plan["input_end_ms"])

    def test_runner_change_invalidates_checkpoint(self):
        self.run_successor()
        original_sha = successor.sha_file
        runner = Path(successor.__file__)
        with mock.patch.object(successor, "sha_file", side_effect=lambda path:
                               "c" * 64 if Path(path) == runner else original_sha(path)):
            self.now += 1000
            self.assertFalse(self.run_successor()["evaluation_skipped"])
        self.assertEqual(self.calls, 2)

    def test_invalid_checkpoint_does_not_delay_outcome(self):
        for invalid in ([], {"schema": successor.CACHE_SCHEMA, "next_due_ms": 10**20}):
            with self.subTest(invalid=invalid):
                self.run_successor()
                self.write(self.target / "successor-input-checkpoint.json", invalid)
                self.now += 1000
                before = self.calls
                self.assertFalse(self.run_successor()["evaluation_skipped"])
                self.assertEqual(self.calls, before + 1)

    def test_future_evidence_deadline_forces_evaluation(self):
        base_evaluate = successor._evaluate.side_effect
        deadline = self.now + 5000
        def evaluate(*args):
            base_evaluate(*args)
            return deadline if self.now < deadline else None
        with mock.patch.object(successor, "_evaluate", side_effect=evaluate):
            self.assertEqual(self.run_successor()["next_evaluation_due_ms"], deadline)
            self.now += 1000
            self.assertTrue(self.run_successor()["evaluation_skipped"])
            self.now = deadline
            self.assertFalse(self.run_successor()["evaluation_skipped"])
        self.assertEqual(self.calls, 2)

    def test_unavailable_source_is_retried_without_input_change(self):
        base_evaluate = successor._evaluate.side_effect
        def evaluate(c, target, plan, source):
            result = base_evaluate(c, target, plan, source)
            for name in ("assessment.json", "assessment-signed.json"):
                path = target / "plans" / plan["plan_id"] / name
                value = successor.read_json(path)
                value["source_issues"] = [{"reason": "PROVENANCE_UNAVAILABLE:ATTRIBUTION_CLOCK"}]
                successor.atomic(path, value)
            return result
        with mock.patch.object(successor, "_evaluate", side_effect=evaluate):
            self.assertFalse(self.run_successor()["input_checkpoint_reusable"])
            self.now += 1000
            self.assertFalse(self.run_successor()["evaluation_skipped"])
        self.assertEqual(self.calls, 2)




class SuccessorFutureEvidenceTest(unittest.TestCase):
    def test_stream_reports_future_nonexecution_evidence_in_one_scan(self):
        import hashlib, json, os, tempfile
        from pathlib import Path
        from unittest import mock
        from historical_training import value_successor_evaluator as successor
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); ledger = root / "ledger.jsonl"
            rows = [
                {"event": "broker_execution", "broker_execution_id": "past", "ts_ms": 999000},
                {"event": "broker_commission", "ts_ms": 1007000},
                {"event": "broker_order_status", "ts_ms": 1003000},
            ]
            raw = b"".join((json.dumps(row) + "\n").encode() for row in rows)
            ledger.write_bytes(raw); os.chmod(ledger, 0o600)
            prefix = {"path": str(ledger), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
            indexes = []; previous = successor.ff.execution_index
            original_stream = successor.stream_execution_index
            with mock.patch.object(successor.ff, "Evaluator") as evaluator, \
                    mock.patch.object(successor, "_assessment"), \
                    mock.patch.object(successor.time, "time", return_value=1000), \
                    mock.patch.object(successor, "stream_execution_index", wraps=original_stream) as stream:
                evaluator.return_value.step.side_effect = lambda: indexes.append(successor.ff.execution_index(prefix))
                due = successor._evaluate({"forward_root": str(root / "original")}, root / "target", {}, None)
                stream.assert_called_once()
            self.assertEqual(due, 1003000)
            self.assertEqual(indexes, [({"past": 999000}, {}, {})])
            self.assertIs(successor.ff.execution_index, previous)

if __name__ == "__main__":
    unittest.main()
