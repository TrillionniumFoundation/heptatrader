"""Numerical/causal regression tests; entirely synthetic, no broker or GPU."""
import copy,math,unittest
import numpy as np
from historical_training.common import EvidenceError,digest
from historical_training.calibration_model import (features,prior,softmax,brier,fit_logistic,predict,
    logistic_objective,fit_calibration,calibrate_apply,fit_value,predict_value,reliability)
from historical_training.calibration_development import split_purged,validate_development,trade_summary,PROFILE


def rows(n=180):
    start=1775779200
    result=[]
    for i in range(n):
        t=start+i*300;x=(i%3-1)*2.
        result.append({'time':t,'history_start':t-3600,'entry_at':t+300,'exit_at':t+1200,'outcome_end':t+1500,
            'features':[x*(11-j)/11 for j in range(11)]+[.2]*12+[0.,1.],
            'class':i%3,'target_bps':x,'long_after_spread_bps':x-.2,'short_after_spread_bps':-x-.2,'real_execution_reward':False})
    return result

class NumericalTests(unittest.TestCase):
    def test_softmax_extreme_finite(self):
        p=softmax([[1000,0,-1000]]);self.assertEqual(p[0,0],1.);self.assertEqual(float(p.sum()),1.)
    def test_softmax_nan_refused(self):
        with self.assertRaises(EvidenceError):softmax([[1,np.nan,3]])
    def test_probabilities_not_unit_refused(self):
        with self.assertRaises(EvidenceError):brier([[.1,.2,.3]],np.array([1]))
    def test_class_not_truncated(self):
        with self.assertRaises(EvidenceError):prior([.1,1,2])
    def test_missing_training_class_refused(self):
        with self.assertRaises(EvidenceError):prior([0,0,1])
    def test_analytic_logistic_gradient(self):
        a=np.array([[1.,1.],[-1.,1.],[.4,1.]]);y=np.array([0,1,2]);w=np.array([[.1,.2],[.3,.4]])
        _,g,_=logistic_objective(a,y,w,.01)
        for i in range(2):
            for j in range(2):
                u=w.copy();v=w.copy();u[i,j]+=1e-6;v[i,j]-=1e-6
                numeric=(logistic_objective(a,y,u,.01)[0]-logistic_objective(a,y,v,.01)[0])/2e-6
                self.assertAlmostEqual(g[i,j],numeric,places=6)
    def test_real_fit_improves_strong_synthetic_signal(self):
        rs=rows();y=np.array([r['class'] for r in rs]);m=fit_logistic(features(rs),y,.01);m['profile']='compact'
        self.assertLess(brier(predict(m,rs),y).mean(),.5)
        self.assertTrue(m['objective_monotone']);self.assertLess(m['gradient_inf'],1e-6)
    def test_fit_is_deterministic(self):
        rs=rows(90);y=np.array([r['class'] for r in rs]);a=features(rs)
        self.assertEqual(fit_logistic(a,y,.1),fit_logistic(a,y,.1))
    def test_negative_penalty_refused(self):
        with self.assertRaises(EvidenceError):fit_logistic(np.ones((3,2)),np.array([0,1,2]),-1)
    def test_feature_future_poison_does_not_change_input(self):
        rs=rows();before=features(rs);rs[0]['target_bps']=999;rs[0]['class']=2;rs[0]['long_after_spread_bps']=999
        np.testing.assert_array_equal(before,features(rs))
    def test_wrong_feature_width_refused(self):
        rs=rows();rs[0]['features'].append(1)
        with self.assertRaises(EvidenceError):features(rs)
    def test_negative_spread_refused(self):
        rs=rows();rs[0]['features'][11]=-1
        with self.assertRaises(EvidenceError):features(rs)
    def test_temperature_shrinkage_simplex(self):
        p=calibrate_apply([[.99,.005,.005]],{'temperature':2,'prior_weight':.5,'prior':[1/3]*3})
        self.assertAlmostEqual(float(p.sum()),1);self.assertLess(p[0,0],.99)
    def test_calibration_extreme_parameter_refused(self):
        with self.assertRaises(EvidenceError):calibrate_apply([[.3,.3,.4]],{'temperature':0,'prior_weight':0,'prior':[1/3]*3})
    def test_calibrator_uninformative_predictions(self):
        y=np.array([0,1,2]*30);p=np.tile([.99,.005,.005],(90,1));c=fit_calibration(p,y,[1/3]*3)
        self.assertGreater(c['prior_weight'],0)
    def test_value_targets_have_no_execution_reward(self):
        rs=rows();m=fit_value(rs,.1);self.assertEqual(m['actual_execution_rewards_used'],0)
        self.assertEqual(predict_value(m,rs).shape,(180,2))
    def test_reliability_has_every_sample_per_class(self):
        p=np.array([[1,0,0],[0,1,0],[0,0,1]],float);r=reliability(p,np.array([0,1,2]))
        self.assertEqual(r['multiclass_brier'],0)
        self.assertTrue(all(sum(b['count'] for b in c['bins'])==3 for c in r['reliability']))

class CausalContractTests(unittest.TestCase):
    def test_full_span_role_purge(self):
        a,b,info=split_purged(rows(500),.8)
        self.assertLess(max(r['outcome_end'] for r in a),min(r['history_start'] for r in b));self.assertGreater(info['purged'],0)
    def test_duplicate_time_refused(self):
        rs=rows();rs[1]['time']=rs[0]['time']
        with self.assertRaises(EvidenceError):split_purged(rs,.5)
    def test_scope_refuses_test_population(self):
        d={'profile':PROFILE,'groups':{'train':[],'validation':[],'test':[]},'source_test_rows_exported':0,'actual_execution_rewards':0,'trade_authority':False}
        with self.assertRaisesRegex(EvidenceError,'DEVELOPMENT_ONLY_SCOPE'):validate_development(d)
    def test_economic_replay_does_not_overlap(self):
        rs=rows();p=np.tile([3,-3],(len(rs),1));r=trade_summary(rs,p,0)
        self.assertTrue(all(b['time']>=a['time']+1500 for a,b in zip(r['details'],r['details'][1:])))
    def test_extra_cost_keeps_same_trades(self):
        rs=rows();p=np.tile([3,-3],(len(rs),1));a=trade_summary(rs,p,0);b=trade_summary(rs,p,0,1)
        self.assertEqual([(r['time'],r['side']) for r in a['details']],[(r['time'],r['side']) for r in b['details']])
        self.assertAlmostEqual(b['net_usd'],a['net_usd']-10*a['trades'])
    def test_negative_expected_net_abstains(self):
        rs=rows();r=trade_summary(rs,np.tile([-1,-2],(len(rs),1)),0)
        self.assertEqual(r['trades'],0);self.assertEqual(r['net_usd'],0)
    def test_no_policy_uses_realized_reward_for_admission(self):
        rs=rows();p=np.tile([3,-3],(len(rs),1));a=trade_summary(rs,p,0)
        for r in rs:r['long_after_spread_bps']=-999
        b=trade_summary(rs,p,0)
        self.assertEqual([r['time'] for r in a['details']],[r['time'] for r in b['details']])

if __name__=='__main__':unittest.main()
