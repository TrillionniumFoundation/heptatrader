"""Adversarial synthetic tests; no live services, broker, GPU, or orders."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from historical_training.common import EvidenceError, canonical, digest, sha
from historical_training.laya_frozen_admission_audit import (
    audit, write_audit_once, PREWARM_MS,
)
from laya_cell.promotion import rule_for_trial
from laya_cell.promotion_power import PROTOCOL


class OriginalGuardAttributionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / 'evaluation' / 'trial37'
        self.folder.mkdir(parents=True)
        self.denials = self.root / 'guard-denials'
        self.denials.mkdir()
        start = 1_800_000_000_000
        rule = rule_for_trial(37, PROTOCOL)
        self.plan = {
            'schema': 'heptatrader.laya-resident-future-plan.v1',
            'created_at_ms': start - 240_000,
            'start_ms': start,
            'outcome_end_ms': (start + (rule['slots']-1)*rule['slot_spacing_ms']
                               + rule['input_grace_ms'] + rule['horizon_ms'] + 5000),
            'rule': rule,
            'candidate_id': 'c' * 64,
            'trial': 37,
            'future_source_read_profile': 'ARCHIVE_VERIFIED_RECENT_ORIGINAL_V2',
            'broker_orders_sent': 0,
        }
        self.plan['plan_id'] = digest(self.plan)
        self.path = self.folder / 'plan.json'
        self.path.write_bytes(canonical(self.plan) + b'\n')
        self.now = start + 40 * rule['slot_spacing_ms']

    def make_outcome(self, slot, *, status='CENSORED', reason='NO_PREDICTION_IN_FIXED_SLOT',
                     replacement=None):
        start = self.plan['start_ms'] + slot * self.plan['rule']['slot_spacing_ms']
        row = dict(candidate_id=self.plan['candidate_id'], plan_id=self.plan['plan_id'],
                   slot=slot, evaluated_at_ms=start + 62000, status=status,
                   reason=reason)
        if replacement:
            row.update(replacement)
        dest = self.folder / f'outcome-{slot:03d}.json'
        dest.write_bytes(canonical(row) + b'\n')
        return dest

    def _guard(self, slot, reasons=None, *, delay_ms=-PREWARM_MS + 155,
               replacement=None, subject_override=None):
        if reasons is None:
            reasons = ['VALUE_PRIORITY_DEFER_NEW_LONG_EVALUATION']
        subject = subject_override or {'plan': str(self.path), 'slot': slot}
        at = self.plan['start_ms'] + slot * self.plan['rule']['slot_spacing_ms'] + delay_ms
        decision = dict(role='evaluator', admit_new_worker=False, reasons=reasons,
                        at_ms=at)
        doc = dict(subject=subject, decision=decision, orders_sent=0)
        if replacement:
            doc.update(replacement)
        dest = self.denials / (digest(['evaluator', subject, reasons]) + '.json')
        dest.write_bytes(canonical(doc) + b'\n')
        return dest

    def test_original_plan_and_terminal_receipts_preserved(self):
        originals = [self.make_outcome(i) for i in range(4)]
        before = [(p, sha(p)) for p in [self.path] + originals]
        self._guard(0)
        self._guard(0, ['VALUE_PRIORITY_FRESH_FLAT_ORIGINAL_REQUEST'],
                    delay_ms=-PREWARM_MS+30100)
        r = audit(self.path, self.denials, now_ms=self.now)
        self.assertEqual(r['observed'], 0)
        self.assertEqual(r['censored'], 4)
        self.assertEqual(r['terminal_outcome_count'], 4)
        self.assertEqual(r['slot_denial_proof_count'], 1)
        self.assertEqual(r['distinct_original_guard_denial_files'], 2)
        self.assertTrue(r['mathematically_impossible_to_qualify'])
        self.assertTrue(r['slots'][0]['guard_denial_observed'])
        self.assertEqual(r['slots'][3]['cause_without_exact_guard_receipt'],
                         'UNPROVEN')
        self.assertEqual(r['future_slots_preceded_by_irreversible_censoring'],
                         self.plan['rule']['slots'] - 3)
        self.assertFalse(r['source_release_hashes_rechecked_on_host'])
        self.assertFalse(r['automatic_model_promotion'])
        for p, original_sha in before:
            self.assertEqual(sha(p), original_sha)

    def test_no_denial_receipt_never_attributed_to_guard(self):
        self.make_outcome(0)
        r = audit(self.path, self.denials, now_ms=self.now)
        self.assertEqual(r['slot_denial_proof_count'], 0)
        self.assertFalse(r['slots'][0]['guard_denial_observed'])
        self.assertEqual(r['slots'][0]['cause_without_exact_guard_receipt'],
                         'UNPROVEN')

    def test_other_owners_denials_never_count_as_laya(self):
        self.make_outcome(0)
        self._guard(0, subject_override={'role': 'trainer'})
        r = audit(self.path, self.denials, now_ms=self.now)
        self.assertEqual(r['unrelated_denial_files_ignored'], 1)
        self.assertEqual(r['slot_denial_proof_count'], 0)

    def test_forged_denial_filename_refused(self):
        self.make_outcome(0)
        file = self._guard(0)
        file.rename(file.with_name('a' * 64 + '.json'))
        with self.assertRaisesRegex(EvidenceError, 'DENIAL_KEY_CHANGED'):
            audit(self.path, self.denials, now_ms=self.now)

    def test_denial_without_negative_admission_refused(self):
        self.make_outcome(0)
        file = self._guard(0)
        d = json.loads(file.read_text())
        d['decision']['admit_new_worker'] = True
        file.write_bytes(canonical(d))
        with self.assertRaisesRegex(EvidenceError, 'NOT_FAIL_CLOSED'):
            audit(self.path, self.denials, now_ms=self.now)

    def test_denial_outside_exact_fixed_slot_not_attributed(self):
        self.make_outcome(0)
        self._guard(0, delay_ms=-PREWARM_MS-1)
        with self.assertRaisesRegex(EvidenceError, 'DENIAL_OUTSIDE_SLOT'):
            audit(self.path, self.denials, now_ms=self.now)

    def test_denial_cannot_contain_broker_order(self):
        self.make_outcome(0)
        self._guard(0, replacement={'orders_sent': 1})
        with self.assertRaisesRegex(EvidenceError, 'NOT_FAIL_CLOSED'):
            audit(self.path, self.denials, now_ms=self.now)

    def test_modified_original_plan_refused(self):
        self.make_outcome(0)
        d = json.loads(self.path.read_text())
        d['start_ms'] += 1
        self.path.write_bytes(canonical(d))
        with self.assertRaisesRegex(EvidenceError, 'PLAN_MUTATED'):
            audit(self.path, self.denials, now_ms=self.now)

    def test_outcome_candidate_spoof_refused(self):
        self.make_outcome(0, replacement={'candidate_id': 'f' * 64})
        with self.assertRaisesRegex(EvidenceError, 'OUTCOME_IDENTITY'):
            audit(self.path, self.denials, now_ms=self.now)

    def test_outcome_slot_overflow_refused(self):
        self.make_outcome(999)
        with self.assertRaisesRegex(EvidenceError, 'DUPLICATE_OR_RANGE'):
            audit(self.path, self.denials, now_ms=self.now)

    def test_missing_prediction_censor_cannot_precede_original_deadline(self):
        self.make_outcome(0, replacement={'evaluated_at_ms': self.plan['start_ms']+59999})
        with self.assertRaisesRegex(EvidenceError, 'MISSED_SLOT_NOT_EXPIRED'):
            audit(self.path, self.denials, now_ms=self.now)

    def test_boolean_order_count_refused(self):
        self.make_outcome(0)
        self._guard(0, replacement={'orders_sent': False})
        with self.assertRaisesRegex(EvidenceError, 'NOT_FAIL_CLOSED'):
            audit(self.path, self.denials, now_ms=self.now)

    def test_future_outcome_timestamp_refused(self):
        self.make_outcome(0, replacement={'evaluated_at_ms': self.now+1})
        with self.assertRaisesRegex(EvidenceError, 'OUTCOME_FROM_FUTURE'):
            audit(self.path, self.denials, now_ms=self.now)

    def test_duplicate_json_outcome_refused(self):
        path = self.make_outcome(0)
        row = json.loads(path.read_text())
        raw = canonical(row).decode().replace('"slot":0', '"slot":0,"slot":0')
        path.write_text(raw)
        with self.assertRaisesRegex(EvidenceError, 'DUPLICATE_JSON_KEY'):
            audit(self.path, self.denials, now_ms=self.now)

    def test_symlink_denial_refused(self):
        self.make_outcome(0)
        file = self._guard(0)
        original = self.root / 'original.json'
        file.rename(original)
        file.symlink_to(original)
        with self.assertRaisesRegex(EvidenceError, 'PATH_IDENTITY'):
            audit(self.path, self.denials, now_ms=self.now)

    def test_actual_qualification_bound_but_never_rewritten(self):
        self.make_outcome(0)
        q = {'candidate_id': self.plan['candidate_id'],
             'plan_id': self.plan['plan_id'], 'state': 'REJECTED',
             'canonical_owner_admitted': False, 'promotion_performed': False}
        path = self.folder / 'qualification.json'
        path.write_bytes(canonical(q))
        snapshot = sha(path)
        result = audit(self.path, self.denials, now_ms=self.now)
        self.assertEqual(result['original_qualification_sha256'], snapshot)
        self.assertEqual(result['original_qualification_state'], 'REJECTED')
        self.assertEqual(sha(path), snapshot)

    def test_publish_is_create_only(self):
        self.make_outcome(0)
        result = audit(self.path, self.denials, now_ms=self.now)
        dest = self.root / 'receipts' / 'report.json'
        expected_sha = write_audit_once(dest, result)
        self.assertEqual(sha(dest), expected_sha)
        self.assertEqual(json.loads(dest.read_bytes())['audit_id'],
                         result['audit_id'])
        with self.assertRaisesRegex(EvidenceError, 'OUTPUT_ALREADY_EXISTS'):
            write_audit_once(dest, result)


if __name__ == '__main__':
    unittest.main()
