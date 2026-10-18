import math
import tempfile
import unittest
import copy
import importlib.util
import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'research/neuron_learning'))
from laya_cell import price_five_session_observer as c
from laya_cell.integrity import ContractError, sha_file, read_json
from laya_cell.resident_store import write_once

def ms(s):
    return int(datetime.fromisoformat(s).timestamp() * 1000)

def setup_plan():
    reg = c.register(ms('2026-10-18T05:00:00+00:00'), 'a' * 64)
    plan = c.make_plan(reg, ms('2026-10-18T23:00:00+00:00'), 'b' * 64)
    return (reg, plan)

def source(anchor):
    quotes = []
    for t in range(anchor - 13 * c.BAR_MS + 1000, anchor, 5000):
        quotes.append({'t': t, 'known_at_ms': t + 1, 'bid': 1.0999, 'ask': 1.1001, 'mid': 1.1, 'receipt': 'a' * 64})
    return (quotes, {'asof_ms': anchor})

def sealed(root, plan):
    anchor = plan['anchors'][0]['anchor_ms']
    quotes, proof = source(anchor)
    snap = c.input_snapshot(quotes, proof, anchor)
    pred = c.persist_prediction(root, plan, 0, snap, [1 / 7] * 7, [1 / 7] * 7, 'c' * 64, 0, lambda: anchor + 1000)
    for p in root.glob('*.json'):
        os.utime(p, ns=((anchor + 1000) * 1000000,) * 2)
    return pred

def runner():
    spec = importlib.util.spec_from_file_location('five_session_test_runner', ROOT / 'scripts/research_laya_price_five_session.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class PriceFiveSessionTests(unittest.TestCase):

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp_path = Path(directory.name)

    def test_calendar_full_five_sessions_warmup_and_last_target(self):
        reg, plan = setup_plan()
        assert plan['sessions'][0]['open_new_york'].startswith('2026-10-19T17:15')
        assert len(plan['sessions']) == 5 and len(plan['anchors']) == 1360
        for session in plan['sessions']:
            anchors = [a for a in plan['anchors'] if a['session'] == session['session']]
            assert anchors[0]['anchor_ms'] == session['open_ms'] + 13 * c.BAR_MS
            assert anchors[-1]['anchor_ms'] + c.BAR_MS == session['close_ms']
        assert c.check_plan(plan, reg) == plan

    def test_latest_admission_gets_five_full_sessions_before_hard_end(self):
        reg, _ = setup_plan()
        plan = c.make_plan(reg, reg['admission_deadline_ms'], 'b' * 64)
        assert plan['last_outcome_deadline_ms'] <= reg['hard_end_ms']
        with self.assertRaises(ContractError):
            c.make_plan(reg, reg['admission_deadline_ms'] + 1, 'b' * 64)
        bad = copy.deepcopy(plan)
        bad['anchors'].pop()
        with self.assertRaises(ContractError):
            c.check_plan(bad, reg)

    def test_late_input_quote_cannot_revise_features(self):
        _, plan = setup_plan()
        anchor = plan['anchors'][0]['anchor_ms']
        q, p = source(anchor)
        base = c.input_snapshot(q, p, anchor)
        q.append({'t': anchor - 999, 'known_at_ms': anchor + 1, 'bid': 98.0, 'ask': 100.0, 'mid': 99.0, 'receipt': 'e' * 64})
        changed = c.input_snapshot(q, p, anchor)
        assert changed['feature']['features'] == base['feature']['features']
        bad = copy.deepcopy(p)
        bad['asof_ms'] += 1
        with self.assertRaises(ContractError):
            c.input_snapshot(q, bad, anchor)

    def test_gap_censors_first_eligible_without_moving_schedule(self):
        _, plan = setup_plan()
        anchor = plan['anchors'][0]['anchor_ms']
        q, p = source(anchor)
        q = [x for x in q if not anchor - 100000 < x['t'] < anchor - 50000]
        with self.assertRaises(ContractError):
            c.input_snapshot(q, p, anchor)
        assert c.pending_actions(plan, anchor + 5001, set(), set())[0] == (0, 'CENSOR_INPUT')
        assert plan['anchors'][0]['anchor_ms'] == anchor

    def test_prediction_and_input_survive_strict_restart(self):
        tmp_path = self.tmp_path
        _, plan = setup_plan()
        sealed(tmp_path, plan)
        pred = c.verified_prediction(tmp_path, plan, 0, 'c' * 64, [1 / 7] * 7)
        assert pred['source_asof_ms'] == plan['anchors'][0]['anchor_ms']
        p = tmp_path / 'input-0000.json'
        p.write_text(p.read_text().replace('"base_mid":1.1', '"base_mid":1.2'))
        with self.assertRaises(ContractError):
            c.verified_prediction(tmp_path, plan, 0, 'c' * 64, [1 / 7] * 7)

    def test_fsync_crossing_deadline_preserves_unqualified_prediction(self):
        tmp_path = self.tmp_path
        _, plan = setup_plan()
        anchor = plan['anchors'][0]['anchor_ms']
        q, p = source(anchor)
        ticks = iter([anchor + 1, anchor + 2, anchor + 5001])
        with self.assertRaisesRegex(ContractError, 'PREDICTION_FSYNC_LATE'):
            c.persist_prediction(tmp_path, plan, 0, c.input_snapshot(q, p, anchor), [1 / 7] * 7, [1 / 7] * 7, 'c' * 64, 0, lambda: next(ticks))
        assert (tmp_path / 'prediction-0000.json').exists()
        assert not (tmp_path / 'commit-0000.json').exists()

    def test_missing_commit_never_reconstructed_as_valid(self):
        tmp_path = self.tmp_path
        _, plan = setup_plan()
        sealed(tmp_path, plan)
        (tmp_path / 'commit-0000.json').unlink()
        with self.assertRaises(OSError):
            c.verified_prediction(tmp_path, plan, 0, 'c' * 64, [1 / 7] * 7)

    def test_new_prediction_priority_and_expired_outcome_remains_censored(self):
        _, plan = setup_plan()
        anchor = plan['anchors'][1]['anchor_ms']
        actions = c.pending_actions(plan, anchor + 1, {0}, set())
        assert actions[:2] == [(1, 'PREDICT'), (0, 'GRADE')]
        expired = c.grade(plan, 0, {}, [], {}, anchor + c.OUTCOME_GRACE_MS + 1)
        assert expired['status'] == 'CENSORED'

    def test_outcome_fixed_receipt_cutoff_and_assessment_binding(self):
        tmp_path = self.tmp_path
        _, plan = setup_plan()
        pred = sealed(tmp_path, plan)
        anchor = plan['anchors'][0]['anchor_ms']
        due = anchor + c.BAR_MS
        q, p = source(due)
        raw = c.grade(plan, 0, pred, q, p, due + 10)
        q.append({'t': due - 999, 'known_at_ms': due + 1, 'bid': 98.0, 'ask': 100.0, 'mid': 99.0, 'receipt': 'f' * 64})
        assert c.grade(plan, 0, pred, q, p, due + 20)['outcome_close'] == raw['outcome_close']
        raw['prediction_sha256'] = sha_file(tmp_path / 'prediction-0000.json')
        path = tmp_path / 'assessment-0000.json'
        write_once(path, raw)
        os.utime(path, ns=((due + 10) * 1000000,) * 2)
        result = {**raw, 'at_ms': due + 20, 'assessment_durable_at_ms': due + 20, 'assessment_sha256': sha_file(path)}
        assert c.verify_outcome(tmp_path, plan, result, 'c' * 64, [1 / 7] * 7) == result
        bad = copy.deepcopy(result)
        bad['outcome_close'] = 1.2
        bp = (1.2 / pred['base_mid'] - 1) * 10000
        bad['return_bps'] = bp
        bad['actual_class'] = 6
        bad['model']['hit'] = False
        bad['prior']['hit'] = False
        with self.assertRaises(ContractError):
            c.verify_outcome(tmp_path, plan, bad, 'c' * 64, [1 / 7] * 7)

    def test_first_ready_recovery_keeps_original_schedule_and_write_failure_has_no_plan(self):
        tmp_path = self.tmp_path
        m = runner()
        reg, _ = setup_plan()
        at = ms('2026-10-18T23:00:00+00:00')
        src = {'period_sha256': 'f' * 64}
        proof = {'asof_ms': at, 'period_sha256': 'f' * 64, 'source_identity': 'ORIGINAL_VERIFIED_PAPER_QUOTES'}
        ready = {'registration_id': reg['registration_id'], 'verified_at_ms': at, 'config_sha256': reg['config_sha256'], 'source_config_digest': m.digest(src), 'source_read_started_at_ms': at, 'source_proof': proof, 'feature': {'anchor_ms': at, 'asof_ms': at, 'quote_prefixes_digest': m.digest(proof), 'source_kind': 'ORIGINAL_VERIFIED_PAPER_QUOTES'}, 'readiness': {'period_sha256': 'f' * 64, 'state': 'READY_FOR_REVIEW_NOT_AUTHORIZATION', 'rule': m.RULE, 'usable': 24, 'at_ms': at, 'latest_source_quote_ms': at}}
        write_once(tmp_path / 'first-ready-original-source.json', ready)
        with patch.object(m, 'write_once', side_effect=OSError('disk')):
            with self.assertRaises(OSError):
                m.recover_committed_plan(tmp_path, reg, src)
        assert not (tmp_path / 'plan.json').exists()
        plan = m.recover_committed_plan(tmp_path, reg, src)
        assert plan['sessions'][0]['open_new_york'].startswith('2026-10-19T17:15')
        assert m.recover_committed_plan(tmp_path, reg, src) == plan

    def test_restart_clock_floor_ignores_future_scheduled_anchors_but_keeps_actual_receipts(self):
        tmp_path = self.tmp_path
        m = runner()
        reg, plan = setup_plan()
        write_once(tmp_path / 'plan.json', plan)
        write_once(tmp_path / 'status.json', {'at_ms': plan['ready_at_ms'] + 12345})
        for p in tmp_path.glob('*.json'):
            os.utime(p, ns=(plan['ready_at_ms'] * 1000000,) * 2)
        assert m.persisted_clock_floor(tmp_path) == plan['ready_at_ms'] + 12345
if __name__ == '__main__':
    unittest.main()
