"""Pure synthetic fixtures; no monetary outcome, market order, or A/B claim."""
from __future__ import annotations
import copy,hashlib,json,unittest
import torch
from paired_net_100k_preflight.core import (PROFILE,PreflightError,canonical,digest,
  check_request,feature_text,paired_items,project_fixed_cpu)
H='a'*64
T=1791400000000
def fixture():
 r={'period_sha256':'c'*64,'instrument':'EUR.USD','position_at_request':0,
    'request_id':'synthetic-original-command-input','model_id':'synthetic-remote-policy',
    'prompt':'Observed quotes before model request ONLY','started_at_ms':T,
    'prompt_sha256':hashlib.sha256(b'Observed quotes before model request ONLY').hexdigest()}
 return {'request':r,'request_sha256':digest(r),'producer_manifest_sha256':H,
  'source_record_sha256':'b'*64,
  'source_prefix':{'path':'synthetic-not-an-existing-source','bytes':100,'sha256':'d'*64}}

class IdenticalInputTests(unittest.TestCase):
 def test_same_paired_source_has_both_directions(self):
  obj=paired_items([fixture()],H,'c'*64)
  self.assertEqual([r['side'] for r in obj],['BUY','SELL'])
  self.assertEqual({r['input_sha256'] for r in obj},{fixture()['request_sha256']})
  self.assertEqual(len({r['text_sha256'] for r in obj}),2)
  self.assertTrue(all('100000 USD' in r['text'] for r in obj))
 def test_no_prepared_or_reward_hindsight_added(self):
  obj=paired_items([fixture()],H,'c'*64)
  for field in ('order_quantity=','reference_price=','realized_net_usd=','commission_usd='):
   self.assertFalse(any(field in r['text'] for r in obj))
 def test_original_request_still_verified(self):
  obj=fixture()
  for change in ({'prompt':'mutated'},{'period_sha256':'0'*64},
                 {'position_at_request':89388},{'instrument':'BTC.USD'}):
   with self.subTest(change=change):
    bad=copy.deepcopy(obj);bad['request'].update(change)
    with self.assertRaises(PreflightError):check_request(bad,H,'c'*64)
 def test_producer_binding_cannot_alias_old_25k(self):
  item=fixture()
  with self.assertRaises(PreflightError):paired_items([item],'f'*64,'c'*64)
 def test_no_request_identity_substitution(self):
  item=fixture();item['request_sha256']='z'*64
  with self.assertRaises(PreflightError):paired_items([item],H,'c'*64)
 def test_no_duplicate_source_count_inflation(self):
  with self.assertRaisesRegex(PreflightError,'DUPLICATE'):paired_items([fixture(),fixture()],H,'c'*64)
 def test_no_unknown_conditional_side(self):
  with self.assertRaises(PreflightError):feature_text(fixture()['request'],'HOLD')
 def test_profile_nonexecuting(self):
  self.assertIs(PROFILE['automatic_selection'],False)
  self.assertIs(PROFILE['trade_authority'],False)
  self.assertIs(PROFILE['no_retroactive_prospective_prediction'],True)
 def test_dense_feature_projected_equal_capacity(self):
  a=torch.zeros(2,768);a[0,0]=1.;a[1,1]=1.
  b=torch.zeros(2,1024);b[0,0]=1.;b[1,1]=1.
  x=project_fixed_cpu(a,PROFILE['feature_projection_seed'])
  y=project_fixed_cpu(b,PROFILE['feature_projection_seed'])
  self.assertEqual(x.shape,(2,256));self.assertEqual(y.shape,(2,256))
  torch.testing.assert_close(x,project_fixed_cpu(a,PROFILE['feature_projection_seed']))
  self.assertFalse(x.requires_grad)
 def test_bad_or_nonfinite_features_refused(self):
  with self.assertRaises(PreflightError):project_fixed_cpu(torch.zeros(1,7),3)
  with self.assertRaises(PreflightError):project_fixed_cpu(torch.full((1,768),float('nan')),3)
 def test_no_implied_model_quality_in_equal_width(self):
  self.assertNotEqual(768,1024)
  self.assertEqual(PROFILE['equal_trainable_capacity_experiment'],'FIXED_PROJECTION_TO_256_PLUS_LINEAR_256_TO_1')
 def test_sample_order_is_original_time_not_favorable_score(self):
  first=fixture();second=fixture();second['request']['started_at_ms']=T-1000
  second['request']['request_id']='older-original'
  second['request_sha256']=digest(second['request'])
  v=paired_items([first,second],H,'c'*64)
  self.assertEqual([x['request_id'] for x in v],['older-original']*2+['synthetic-original-command-input']*2)

if __name__=='__main__':unittest.main(verbosity=2)
