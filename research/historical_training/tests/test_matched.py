import copy,unittest
import numpy as np
from historical_training.common import EvidenceError,digest
from historical_training.matched_data import SIZES,select,text
from historical_training.matched_fit import probability,fit_value,value
from historical_training.calibration_model import fit_logistic
from historical_training.calibration_development import PROFILE


def fixture():
    start=1775779200
    def rows(n,offset):
        out=[]
        for i in range(n):
            t=start+offset+i*300
            out.append({'time':t,'history_start':t-3600,'entry_at':t+300,'exit_at':t+1200,'outcome_end':t+1500,
                'features':[.1]*11+[.2]*12+[0.,1.],'class':i%3,'target_bps':i%3-1,
                'long_after_spread_bps':.4,'short_after_spread_bps':-.6,'real_execution_reward':False})
        return out
    data={'profile':PROFILE,'groups':{'train':rows(4000,0),'validation':rows(3000,5000*300)},
        'source_test_rows_exported':0,'actual_execution_rewards':0,'trade_authority':False}
    data['fingerprint']=digest(data);return data

class MatchedTests(unittest.TestCase):
    def test_exact_same_role_sizes(self):
        groups,_=select(fixture());self.assertEqual({k:len(v) for k,v in groups.items()},SIZES)
    def test_selection_does_not_depend_on_labels(self):
        data=fixture();a,_=select(data)
        for rows in data['groups'].values():
            for r in rows:r['class']=0;r['target_bps']=999
        data['fingerprint']=digest({k:v for k,v in data.items() if k!='fingerprint'});b,_=select(data)
        self.assertEqual({k:[r['time'] for r in v] for k,v in a.items()},{k:[r['time'] for r in v] for k,v in b.items()})
    def test_full_role_spans_do_not_overlap(self):
        groups,_=select(fixture());values=list(groups.values())
        self.assertTrue(all(max(r['outcome_end'] for r in a)<min(r['history_start'] for r in b) for a,b in zip(values,values[1:])))
    def test_test_population_is_denied(self):
        d=fixture();d['groups']['test']=[]
        with self.assertRaises(EvidenceError):select(d)
    def test_text_excludes_future_targets_and_fees(self):
        r=fixture()['groups']['train'][0];before=text(r);r['target_bps']=87654;r['long_after_spread_bps']=55555
        self.assertEqual(before,text(r));self.assertNotIn('87654',text(r));self.assertIn('five-minute decision delay',text(r))
    def test_bad_feature_width(self):
        r=fixture()['groups']['train'][0];r['features'].pop()
        with self.assertRaises(EvidenceError):text(r)
    def test_nonfinite_text_rejected(self):
        r=fixture()['groups']['train'][0];r['features'][0]=float('nan')
        with self.assertRaises(EvidenceError):text(r)
    def test_common_softmax_for_any_feature_arm(self):
        rng=np.random.default_rng(1);x=rng.normal(size=(120,8));y=np.arange(120)%3;m=fit_logistic(x,y,.1);p=probability(m,x)
        self.assertEqual(p.shape,(120,3));np.testing.assert_allclose(p.sum(1),1)
    def test_value_is_modelled_not_execution_credit(self):
        rows=fixture()['groups']['train'][:120];x=np.ones((120,8));m=fit_value(x,rows,.1)
        self.assertEqual(m['actual_execution_rewards_used'],0);self.assertEqual(value(m,x).shape,(120,2))
        np.testing.assert_allclose(value(m,x)[0],[-.5,-1.5],atol=1e-9)
    def test_bad_value_targets_rejected(self):
        rows=fixture()['groups']['train'][:120];rows[0]['long_after_spread_bps']=float('nan')
        with self.assertRaises(EvidenceError):fit_value(np.ones((120,3)),rows,.1)

if __name__=='__main__':unittest.main()
