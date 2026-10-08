"""Adversarial tests for offline-only evidence and temporal boundaries."""
import copy,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from historical_training.common import EvidenceError,digest,read,write_once,atomic
from historical_training.current100k import to_row
from historical_training.dataset import checked_bar,examples,split,parse_session
from historical_training.ib_history import plans,ALLOWED
from datetime import datetime,timezone

class EvidenceTests(unittest.TestCase):
    def test_immutable_replay(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'a.json';write_once(p,{'n':1});write_once(p,{'n':1})
            with self.assertRaises(EvidenceError):write_once(p,{'n':2})
    def test_duplicate_keys_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'a.json';p.write_text('{"n":1,"n":2}')
            with self.assertRaises(EvidenceError):read(p)
    def test_nan_rejected(self):
        with self.assertRaises(ValueError):digest({'n':float('nan')})
    def test_symlink_output_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'a';q=Path(d)/'b';p.write_text('unchanged');q.symlink_to(p)
            with self.assertRaises(EvidenceError):atomic(q,{})
            self.assertEqual(p.read_text(),'unchanged')
    def test_fingerprint_key_order(self):self.assertEqual(digest({'a':1,'b':2}),digest({'b':2,'a':1}))
    def test_history_no_order_codes(self):
        self.assertTrue(ALLOWED.isdisjoint({3,4,5,15,16,21,58,61,62,76}))
    def test_bounded_plan(self):
        p=plans({'chunks':6},datetime(2026,10,6,23,59,59,tzinfo=timezone.utc))
        self.assertEqual(len(p),27)
        self.assertEqual(sum(r['kind']=='bars' for r in p),18)
        self.assertTrue(all(r.get('bar_size')=='5 mins' for r in p if r['kind']=='bars'))

class TemporalTests(unittest.TestCase):
    def bars(self,n=500):
        start=1790899200
        return [{'time':start+i*300,**{k:{'open':str(1.1+i*.00001+off),
            'high':str(1.101+i*.00001+off),'low':str(1.099+i*.00001+off),
            'close':str(1.1001+i*.00001+off)} for k,off in [('midpoint',0),('bid',-.00001),('ask',.00001)]}} for i in range(n)]
    def test_bar_valid(self):
        r={'time':'1790899200','open':'1','low':'.9','high':'1.1','close':'1.05'}
        self.assertEqual(checked_bar(r)[0],1790899200)
    def test_bar_invalid_ohlc(self):
        with self.assertRaises(EvidenceError):checked_bar({'time':'1790899200','open':'1','low':'2','high':'3','close':'2'})
    def test_bar_unaligned(self):
        with self.assertRaises(EvidenceError):checked_bar({'time':'1790899201','open':'1','low':'1','high':'1','close':'1'})
    def test_bar_nonfinite(self):
        with self.assertRaises(EvidenceError):checked_bar({'time':'1790899200','open':'NaN','low':'1','high':'1','close':'1'})
    def test_full_span_purge(self):
        rows,_=examples(self.bars());g,b=split(rows)
        self.assertLess(max(x['outcome_end'] for x in g['train']),min(x['history_start'] for x in g['validation']))
        self.assertLess(max(x['outcome_end'] for x in g['validation']),min(x['history_start'] for x in g['test']))
        self.assertGreater(b['purged_rows'],0)
    def test_missing_path_never_imputed(self):
        bars=self.bars();del bars[30];rows,e=examples(bars)
        self.assertGreater(e['INPUT_OR_OUTCOME_GAP'],0)
        self.assertFalse(any(r['history_start']<1790899200+30*300<r['outcome_end'] for r in rows))
    def test_future_change_does_not_change_features(self):
        a=self.bars();before,_=examples(a);a[15]['midpoint']['open']='1.8';after,_=examples(a)
        self.assertEqual(before[0]['features'],after[0]['features'])
        self.assertEqual(before[0]['feature_text'],after[0]['feature_text'])
        self.assertNotEqual(before[0]['target_bps'],after[0]['target_bps'])
    def test_simulated_labels_not_real(self):
        rows,_=examples(self.bars());self.assertTrue(all(r['real_execution_reward'] is False for r in rows))
    def test_insufficient_rows(self):
        with self.assertRaises(EvidenceError):split([{'time':1}])
    def test_session_dst(self):
        self.assertEqual(parse_session('20261006-17:00:00','US/Eastern'),1791320400)

class Current100kTests(unittest.TestCase):
    def fixture(self):
        req={'period_sha256':'p'*64,'instrument':'EUR.USD','position_at_request':0,
            'prompt':'original request','prompt_sha256':__import__('hashlib').sha256(b'original request').hexdigest(),
            'request_id':'request1','model_id':'model1','started_at_ms':100}
        item={'request':req,'request_sha256':digest(req),'producer_manifest_sha256':'m'*64,
              'source_record_sha256':'r'*64,'source_prefix':{'path':'original','bytes':1,'sha256':'x'*64}}
        cmd={'command_id':'open1','kind':'OPEN','instrument':'EUR.USD','position_before_send':0,
             'quantity_mode':'BASE_NOTIONAL_TARGET','target_notional_usd':100000,'side':'SELL','order_quantity':89388}
        binding={'command':cmd,'request_id':'request1','request_sha256':digest(req),'decision_sha256':'forecast'}
        reward={'learning_eligible':True,'exact_input_bound':True,'fees_complete':True,'settlement_complete':True,
            'instrument':'EUR.USD','currency':'USD','policy_gradient_allowed':False,'laya_on_policy':False,
            'learning_exclusion_reasons':[],'input_attribution':{'producer_manifest_sha256':'m'*64},
            'behavior_model_id':'model1','open_command_id':'open1','input_id':digest(req),'forecast_id':'forecast',
            'episode_id':'episode1','realized_gross_usd':'4.46940','actual_commissions_usd':'4.000039','realized_net_usd':'0.469361',
            'executions':[{'execution_id':'e1','command_id':'open1','quantity':'89388','price':'1.11874','side':'SELL','commission':'2.000039','commission_currency':'USD'},
                          {'execution_id':'e2','command_id':'close1','quantity':'89388','price':'1.11869','side':'BUY','commission':'2','commission_currency':'USD'}]}
        reward['reward_id']=digest(reward)
        return req,item,binding,reward
    def row(self,mutation=None,reseal=True):
        req,item,binding,reward=self.fixture()
        if mutation:mutation(reward,item,binding)
        if reseal:reward['reward_id']=digest({k:v for k,v in reward.items() if k!='reward_id'})
        with patch('laya_cell.financial_value_data.exact_request',return_value=req):
            return to_row(reward,item,binding,'m'*64,'p'*64)
    def test_variable_quantity_current100k_accepted(self):self.assertEqual(self.row()['net_usd'],'0.469361')
    def test_replay_stable(self):self.assertEqual(self.row()['row_id'],self.row()['row_id'])
    def test_features_exclude_outcome(self):
        r=self.row();self.assertNotIn('0.469361',r['feature_text']);self.assertNotIn('89388',r['feature_text'])
    def test_old_quantity_profile_refused(self):
        with self.assertRaises(EvidenceError):self.row(lambda r,i,b:b['command'].update(target_notional_usd=25000))
    def test_wrong_manifest_refused(self):
        with self.assertRaises(EvidenceError):self.row(lambda r,i,b:r['input_attribution'].update(producer_manifest_sha256='x'*64))
    def test_unsettled_refused(self):
        with self.assertRaises(EvidenceError):self.row(lambda r,i,b:r.update(settlement_complete=False))
    def test_missing_fee_refused(self):
        with self.assertRaises(EvidenceError):self.row(lambda r,i,b:r.update(fees_complete=False))
    def test_economic_tamper_refused(self):
        with self.assertRaises(EvidenceError):self.row(lambda r,i,b:r.update(realized_net_usd='400'))
    def test_unsealed_tamper_refused(self):
        with self.assertRaises(EvidenceError):self.row(lambda r,i,b:r.update(realized_net_usd='400'),reseal=False)
    def test_duplicate_execution_refused(self):
        with self.assertRaises(EvidenceError):self.row(lambda r,i,b:r['executions'][1].update(execution_id='e1'))
    def test_hindsight_on_policy_refused(self):
        with self.assertRaises(EvidenceError):self.row(lambda r,i,b:r.update(laya_on_policy=True))
    def test_forecast_identity_refused(self):
        with self.assertRaises(EvidenceError):self.row(lambda r,i,b:b.update(decision_sha256='different'))
if __name__=='__main__':unittest.main()
