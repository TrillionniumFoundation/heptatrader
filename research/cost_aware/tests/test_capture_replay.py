import unittest, tempfile, copy, json, os
from pathlib import Path
from fixtures import *
from hepta_cost_capture import Capture, read_capture
from hepta_cost_replay import compare, stress_compare, candidate_outcomes, fit_from_tape, capture_to_tape


def small_tape(instrument='BTC.USD'):
    rows=[]
    # Same quote/candidate tape; independent strategies produce different paths.
    candidates={0:('BUY','20'),20:('SELL','-20'),40:('BUY','80'),60:('SELL','-20'),
                80:('BUY','80'),100:('SELL','-80'),120:('BUY','80'),140:('HOLD','0'),
                240:('BUY','80'),260:('SELL','-20'),360:('SELL','-80'),500:('BUY','80')}
    for sec in range(0,901,10):
        at=NOW+sec*1000
        m=D('100')*(1+D(sec)/D('10000000')) if instrument=='BTC.USD' else D('1.12')*(1+D(sec)/D('10000000'))
        q=quote(at,instrument,str(m),str(m*D('.00002')))
        f=None
        if sec in candidates:
            side,move=candidates[sec];f=forecast(at,instrument,side,move,str(m))
        rows.append({'at_ms':at,'quote':q,'forecast':f})
    return {'schema':'heptatrader.cost-tape.v1','instrument':instrument,'source_kind':'SYNTHETIC','rows':rows}


def full_artifact(instrument='BTC.USD'):
    return build_calibration(samples(instrument,'BUY')+samples(instrument,'SELL'),NOW-1000)


class CaptureTests(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'capture.jsonl'
            with Capture(p,'BTC.USD',source_kind='SYNTHETIC',min_free_bytes=0) as c:
                c.append('QUOTE',quote(),at_ms=NOW)
                c.append('CANDIDATE',{'forecast':forecast()},at_ms=NOW)
            data=read_capture(p)
            self.assertEqual(len(data['events']),3)
            self.assertEqual(data['source_kind'],'SYNTHETIC')
            tape=capture_to_tape(data);self.assertEqual(len(tape['rows']),2)
    def test_reopen_preserves_chain(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'capture'
            with Capture(p,'BTC.USD',min_free_bytes=0) as c:c.append('QUOTE',quote(),at_ms=NOW)
            with Capture(p,'BTC.USD',min_free_bytes=0) as c:c.append('QUOTE',quote(NOW+1),at_ms=NOW+1)
            self.assertEqual(len(read_capture(p)['events']),3)
    def test_second_writer_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'capture'
            with Capture(p,'BTC.USD',min_free_bytes=0):
                with self.assertRaises(BlockingIOError):Capture(p,'BTC.USD',min_free_bytes=0)
    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'capture';p.symlink_to(Path(td)/'other')
            with self.assertRaises(OSError):Capture(p,'BTC.USD',min_free_bytes=0)
    def test_truncated_tail_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'capture'
            with Capture(p,'BTC.USD',min_free_bytes=0):pass
            p.write_bytes(p.read_bytes()[:-1])
            with self.assertRaisesRegex(CostError,'TRUNCATED'):read_capture(p)
    def test_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'capture'
            with Capture(p,'BTC.USD',min_free_bytes=0):pass
            p.write_text(p.read_text().replace('BTC.USD','EUR.USD'))
            with self.assertRaisesRegex(CostError,'CHAIN'):read_capture(p)
    def test_path_replacement_poisoned(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'capture'
            with Capture(p,'BTC.USD',min_free_bytes=0) as c:
                p.rename(Path(td)/'old');p.write_text('')
                with self.assertRaisesRegex(CostError,'PATH_REPLACED'):c.append('QUOTE',quote(),at_ms=NOW)
                with self.assertRaisesRegex(CostError,'POISONED'):c.append('QUOTE',quote(),at_ms=NOW)
    def test_clock_regression_refused(self):
        with tempfile.TemporaryDirectory() as td:
            with Capture(Path(td)/'capture','BTC.USD',min_free_bytes=0) as c:
                c.append('QUOTE',quote(),at_ms=NOW)
                with self.assertRaisesRegex(CostError,'CLOCK'):c.append('QUOTE',quote(),at_ms=NOW-1)
    def test_directory_private(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'open';p.mkdir(mode=0o755)
            with self.assertRaisesRegex(CostError,'DIRECTORY'):Capture(p/'capture','BTC.USD',min_free_bytes=0)
    def test_disk_reserve(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(CostError,'DISK_RESERVE'):Capture(Path(td)/'capture','BTC.USD',min_free_bytes=2**63)
    def test_wrong_instrument_reopen(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'capture'
            with Capture(p,'BTC.USD',min_free_bytes=0):pass
            with self.assertRaisesRegex(CostError,'SCOPE'):Capture(p,'EUR.USD',min_free_bytes=0)

class ReplayTests(unittest.TestCase):
    def test_three_independent_paths_not_pruned_trades(self):
        r=compare(small_tape(),[full_artifact()]); a,b,c=r['results']
        self.assertEqual(len(r['results']),3)
        self.assertGreater(a['open_events'],b['open_events'])
        self.assertNotEqual(b['trades'],c['trades'])
        self.assertNotEqual(b['marks'],c['marks'])
        self.assertTrue(all(len(x['decisions'])==len(r['candidate_outcomes']) for x in r['results']))
    def test_no_model_or_broker_calls(self):
        r=compare(small_tape(),[full_artifact()])
        self.assertEqual(r['broker_orders_sent'],0);self.assertFalse(r['live_profitability_proven'])
    def test_refused_candidates_still_have_forward_paths(self):
        r=compare(small_tape(),[full_artifact()]); rejected=r['results'][1]['decisions'][0]
        self.assertEqual(rejected['action'],'REJECT')
        o=[x for x in r['candidate_outcomes'] if x['candidate_id']==rejected['candidate_id']][0]
        self.assertEqual(o['reason'],'OBSERVED');self.assertIsNotNone(o['realized_underlying_move_bps'])
    def test_unmatured_outcomes_remain_unknown(self):
        tape=small_tape();tape['rows']=tape['rows'][:2]
        self.assertEqual(candidate_outcomes(tape)[0]['reason'],'OUTCOME_NOT_YET_OBSERVED')
    def test_source_gap_not_hidden(self):
        tape=small_tape();tape['rows']=[x for x in tape['rows'] if not NOW+10000<=x['at_ms']<=NOW+90000]
        self.assertEqual(candidate_outcomes(tape)[0]['reason'],'DATA_GAP_IN_FORECAST_HORIZON')
        r=compare(tape,[full_artifact()])
        self.assertFalse(r['results'][0]['complete_observed_path'])
        self.assertIsNone(r['results'][0]['net_liquidation_pnl_usd'])
    def test_future_candidate_refused(self):
        t=small_tape();t['rows'][0]['forecast']['issued_at_ms']+=1
        with self.assertRaisesRegex(CostError,'CAUSALITY'):compare(t,[full_artifact()])
    def test_position_conditioned_forecast_refused(self):
        t=small_tape();t['rows'][0]['forecast']['position_neutral']=False
        with self.assertRaises(CostError):compare(t,[full_artifact()])
    def test_stress_runs_rebuild_all_paths(self):
        r=stress_compare(small_tape(),[full_artifact()])
        self.assertEqual(len(r),5)
        self.assertTrue(all(len(x['results'])==3 for x in r.values()))
        self.assertNotEqual(r['BASE']['results'][0]['commission_paid_usd'],r['FEES_125PCT']['results'][0]['commission_paid_usd'])
    def test_no_future_artifact_selection(self):
        a=full_artifact();a['completed_at_ms']=NOW+99999999;reseal(a)
        r=compare(small_tape(),[a])
        self.assertEqual(r['results'][1]['open_events'],0)
    def test_synthetic_artifact_cannot_qualify_real_tape(self):
        t=small_tape();t['source_kind']='IB_CAPTURE'
        r=compare(t,[full_artifact()])
        self.assertEqual(r['results'][1]['open_events'],0)
        self.assertIn('SYNTHETIC_CALIBRATION_NOT_LIVE',r['results'][1]['decisions'][0]['reason'])
    def test_no_calibration_all_cost_entries_rejected(self):
        r=compare(small_tape())
        self.assertGreater(r['results'][0]['open_events'],0)
        self.assertEqual(r['results'][1]['open_events'],0);self.assertEqual(r['results'][2]['open_events'],0)
    def test_each_path_accounts_own_fees(self):
        r=compare(small_tape(),[full_artifact()])
        for path in r['results']:
            self.assertEqual(dec(path['commission_paid_usd']),sum((dec(t['commission_usd']) for t in path['trades']),D(0)))
    def test_fx_short_path_supported(self):
        r=compare(small_tape('EUR.USD'),[full_artifact('EUR.USD')])
        self.assertTrue(any(dec(t['quantity'])<0 for t in r['results'][0]['trades']))
    def test_only_past_known_labels_fit(self):
        r=fit_from_tape(small_tape(),NOW+100000)
        self.assertEqual(r['artifact']['cohorts'],{});self.assertFalse(r['activation_performed'])
    def test_input_not_mutated(self):
        tape=small_tape();a=full_artifact();old=canonical([tape,a]);compare(tape,[a]);self.assertEqual(canonical([tape,a]),old)
    def test_replay_deterministic(self):
        a=compare(small_tape(),[full_artifact()]);b=compare(small_tape(),[full_artifact()])
        self.assertEqual(canonical(a),canonical(b))
    def test_no_forced_fictitious_live_pnl(self):
        r=compare(small_tape(),[full_artifact()])
        self.assertTrue(all(p['actual_account_pnl'] is None for p in r['results']))
        self.assertTrue(all(not p['realized_live_profit_proven'] for p in r['results']))

if __name__=='__main__':unittest.main(verbosity=2)
