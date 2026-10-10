"""Offline contracts for economics, true quote provenance and OOS windows."""
from __future__ import annotations
import json
import math
import tempfile
import time
import unittest
from pathlib import Path
from laya_cell.shadow_source import canonical,digest
from historical_training.ib_execution_cost import calibrate
from historical_training.eurusd_walkforward_cost import features,simulate,run,verify_inputs

class CostTests(unittest.TestCase):
    def setUp(self):
        obj=tempfile.TemporaryDirectory();self.addCleanup(obj.cleanup)
        self.root=Path(obj.name)
        self.feed=self.root/'input';self.feed.mkdir(mode=0o700)
        self.period=self.root/'period.json';self.period.write_text('{}');self.period.chmod(0o600)
        from laya_cell.integrity import sha_file
        self.c={'period_path':str(self.period),'period_sha256':sha_file(self.period),
                'feed_directory':str(self.feed),'completed_history_sources':[]}
        now=int(time.time()*1000)
        self.timestamp=now-900
        source=self.feed/'segment-0';source.mkdir(mode=0o700)
        quote={'instrument':'EUR.USD','source':'IB','authoritative':True,'stale':False,
               'bid':'1.12500','ask':'1.12510','observed_at_ms':self.timestamp,
               'stale_after_ms':self.timestamp+5000,'subscription_id':'test',
               'subscription_state':'active'}
        header={'source':'IB_TOOL_GATEWAY','instrument':'EUR.USD',
                'execution_domain':'PAPER','period_sha256':self.c['period_sha256'],
                'write_operations_allowed':[],'broker_orders_sent':0}
        previous='0'*64;rows=[]
        for i,(kind,data,at) in enumerate([
            ('HEADER',header,self.timestamp-100),
            ('QUOTE',quote,self.timestamp+100)]):
            body={'schema':'heptatrader.laya-shadow-quotes.v1','sequence':i,
                  'previous':previous,'at_ms':at,'kind':kind,'data':data}
            entry={**body,'sha256':digest(body)};previous=entry['sha256']
            rows.append(canonical(entry))
        f=source/'quotes.jsonl';f.write_bytes(b'\n'.join(rows)+b'\n');f.chmod(0o600)
        self.oms=self.root/'oms.jsonl'
        self.execution={'event':'broker_execution','execution_domain':'PAPER',
                        'broker_execution_id':'abc','ts_ms':self.timestamp+500,
                        'instrument':'EUR.USD','side':'BOT','qty':50000.,
                        'price':1.12510}
        self.commission={'event':'broker_commission','execution_domain':'PAPER',
                         'broker_execution_id':'abc','broker_econ_commission':2.0,
                         'broker_econ_commission_currency':'USD','broker_econ_valid':1,
                         'broker_econ_version':1}
        self.write([self.execution,self.commission,self.execution,self.commission])

    def write(self,events):
        self.oms.write_text(''.join(json.dumps(r,separators=(',',':'))+'\n' for r in events))

    def test_deduplicated_commission_and_preexisting_live_quote(self):
        r=calibrate(self.c,self.oms)
        self.assertEqual(r['fx_unique_executions'],1)
        self.assertEqual(r['fx_fee_complete_unique_executions'],1)
        self.assertEqual(r['fx_commission_total_usd'],2.0)
        self.assertEqual(r['causally_preceding_quote_matches'],1)
        self.assertFalse(r['actual_slippage_verified'])
        self.assertEqual(r['orders_sent'],0)

    def test_replayed_broker_commission_conflicts_fail_closed(self):
        different={**self.commission,'broker_econ_commission':200.}
        self.write([self.execution,self.commission,different])
        with self.assertRaisesRegex(ValueError,'CONFLICTING_DUPLICATE'):
            calibrate(self.c,self.oms)

    def test_missing_broker_commission_is_not_zero_fee(self):
        self.write([self.execution])
        r=calibrate(self.c,self.oms)
        self.assertEqual(r['fx_fee_complete_unique_executions'],0)
        self.assertEqual(r['fx_fee_missing_or_unproved_executions'],1)


class WalkForwardTests(unittest.TestCase):
    @staticmethod
    def synthetic():
        start=1773187200
        bars=[]
        for i in range(210*288):
            m=1.12+0.0007*math.sin(i/73.)+0.0002*math.sin(i/11.)
            bars.append({'time':start+i*300,'midpoint':{'open':str(m),'close':str(m+1e-5*math.sin(i/17.))},
                         'bid':{'open':str(m),'close':str(m)},'ask':{'open':str(m),'close':str(m)}})
        coverage={'bar_count':len(bars),'scheduled_missing_count':0,
                  'full_requested_coverage_proven':True,
                  'calendar_response_interval_complete':True,
                  'revised_historical_data_not_contemporaneous':True,
                  'counts_by_kind':{k:len(bars) for k in ('MIDPOINT','BID','ASK')}}
        cost={'schema':'heptatrader.ib-cost-evidence.offline.v1','trade_authority':False,'orders_sent':0,
              'fx_fee_complete_unique_executions':100,'fx_fee_missing_or_unproved_executions':0,
              'actual_slippage_verified':False,'quote_spread_full_bps':{'p90':.5,'p99':3.},
              'fx_commission_one_way_bps':{'p90':.7}}
        return {'schema':'heptatrader.historical-paired-bars.v1','bars':bars},coverage,cost

    def test_cost_sources_cannot_claim_attested_slippage(self):
        bars,coverage,cost=self.synthetic()
        cost['actual_slippage_verified']=True
        with self.assertRaisesRegex(ValueError,'COST_EVIDENCE_INVALID'):
            verify_inputs(bars,coverage,cost)

    def test_historical_gap_excludes_cross_session_feature(self):
        b,_,_=self.synthetic()
        base=features(b['bars'])
        b['bars'][199]['time']+=30
        new=features(b['bars'])
        self.assertLess(len(new['at']),len(base['at']))

    def test_15minute_nonoverlap_and_negative_cost(self):
        at=[1,301,601,901,1201];forecast=[10]*5;returns=[5]*5
        r=simulate(at,forecast,returns,0,3)
        self.assertEqual(r['trades'],2)
        self.assertEqual(r['net_usd'],40.)

    def test_real_purged_walkforward_no_actor_promotion(self):
        b,coverage,cost=self.synthetic()
        r=run(b,coverage,cost)
        self.assertGreaterEqual(r['tested_folds'],2)
        self.assertFalse(r['promotion_allowed'])
        self.assertEqual(r['orders_sent'],0)
        for fold in r['folds']:
            self.assertTrue(fold['train_label_matured_before_cutoff'])
            self.assertTrue(fold['test_rows_never_reused'])
            self.assertGreater(fold['training_rows'],5000)
            self.assertGreater(fold['test_rows'],300)

if __name__=='__main__':unittest.main()
