"""Regression tests for complete-bar coverage and conflicting revisions."""
import copy,tempfile,unittest
from datetime import datetime,timezone
from pathlib import Path
from historical_training.common import EvidenceError,digest,sha,write_once
from historical_training.dataset import collect,parse_session
from historical_training.offline_compare import select,input_text,SIZES

class CollectionTests(unittest.TestCase):
    def fixture(self,root,partial=False,conflict=False,missing=False):
        start=1791288000
        # 2026-10-06 12:00 UTC: three expected fully closed five-minute bars.
        iso=lambda t:datetime.fromtimestamp(t,timezone.utc).isoformat()
        request=lambda kind,what,end:{'kind':kind,'what':what,'end':str(end),
            'requested_start_utc':iso(start),'requested_end_utc':iso(end),
            'duration':'30 D','bar_size':'1 day' if kind=='schedule' else '5 mins'}
        pairs=[]
        for what,offset in [('MIDPOINT',.00001),('BID',0),('ASK',.00002)]:
            bars=[{'time':str(start+i*300),'open':str(1.1+offset),'low':str(1.1+offset),
                   'high':str(1.1+offset),'close':str(1.1+offset)} for i in range(3)]
            if missing and what=='BID':bars=bars[1:]
            pairs.append((request('bars',what,start+900),{'bars':bars,'head':None,'schedule':None,'errors':[]}))
            if what=='ASK' and (partial or conflict):
                duplicate=copy.deepcopy(bars[-1]);duplicate['close']=duplicate['low']='1.1'
                until=start+899 if partial else start+901
                pairs.append((request('bars',what,until),{'bars':[duplicate],'head':None,'schedule':None,'errors':[]}))
        schedule={'timezone':'UTC','sessions':[{'start':'20261006-12:00:00','end':'20261006-12:15:00','reference_date':'20261006'}]}
        pairs.append((request('schedule','SCHEDULE',start+900),{'bars':[],'head':None,'schedule':schedule,'errors':[]}))
        config={'contract':{'symbol':'EUR'}}
        write_once(root/'plan.json',{'config':config,'requests':[r for r,_ in pairs]})
        for request,response in pairs:
            key=digest({'contract':config['contract'],**request});d=root/'chunks'/key
            write_once(d/'response.json',response)
            write_once(d/'receipt.json',{'request':request,'state':'RECEIVED','response_sha256':sha(d/'response.json')})
    def test_exact_schedule_coverage(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.fixture(root);r=collect(root)
            self.assertEqual(r['coverage']['scheduled_bars'],3)
            self.assertTrue(r['coverage']['full_requested_coverage_proven'])
    def test_incomplete_endpoint_before_duplicate_comparison(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.fixture(root,partial=True);r=collect(root)
            self.assertEqual(r['coverage']['partial_endpoint_bars_excluded'],{'ASK':1})
            self.assertEqual(len(r['bars']),3)
    def test_complete_conflicting_duplicate_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.fixture(root,conflict=True)
            with self.assertRaisesRegex(EvidenceError,'CONFLICTING_DUPLICATE_BAR'):collect(root)
    def test_missing_bid_does_not_fill_or_claim_complete(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.fixture(root,missing=True);r=collect(root)
            self.assertEqual(r['coverage']['scheduled_missing_count'],1)
            self.assertFalse(r['coverage']['full_requested_coverage_proven'])
    def test_tampered_chunk_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.fixture(root);p=next((root/'chunks').glob('*/response.json'))
            p.chmod(0o600);p.write_text('{"bars":[]}')
            with self.assertRaisesRegex(EvidenceError,'SOURCE_CHUNK_HASH_OR_PLAN'):collect(root)
    def test_dst_fold_fails_closed(self):
        with self.assertRaisesRegex(EvidenceError,'AMBIGUOUS_OR_NONEXISTENT'):
            parse_session('20261101-01:30:00','US/Eastern')
    def test_dst_gap_fails_closed(self):
        with self.assertRaisesRegex(EvidenceError,'AMBIGUOUS_OR_NONEXISTENT'):
            parse_session('20260308-02:30:00','US/Eastern')
    def test_historical_sample_selection_ignores_labels(self):
        data={'groups':{k:[{'time':i,'class':i%3} for i in range(v*2)] for k,v in SIZES.items()}}
        before=select(data)
        for rows in data['groups'].values():
            for row in rows:row['class']=0
        after=select(data)
        self.assertEqual({k:[r['time'] for r in v] for k,v in before.items()},
                         {k:[r['time'] for r in v] for k,v in after.items()})
    def test_same_input_text_excludes_hindsight_targets(self):
        row={'features':[.1]*25,'target_bps':5,'net_usd':12345}
        text=input_text(row);row.update(target_bps=-50,net_usd=-12345)
        self.assertEqual(text,input_text(row));self.assertNotIn('12345',text)

if __name__=='__main__':unittest.main()
