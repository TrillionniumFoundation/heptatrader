import copy, hashlib, unittest
from historical_training.value_export_reconcile import digest, reconcile


def seal(data):
 for row in data['rows']:
  reward=row['reward'];reward['reward_id']=digest({k:v for k,v in reward.items() if k!='reward_id'});row['reward_id']=reward['reward_id']
  row['row_id']=digest({k:v for k,v in row.items() if k!='row_id'})
 data['fingerprint']=digest({k:v for k,v in data.items() if k!='fingerprint'});return data

def fixture():
 request={'period_sha256':'p','instrument':'EUR.USD','position_at_request':0,'request_id':'r1','model_id':'model','prompt':'known input','prompt_sha256':hashlib.sha256(b'known input').hexdigest(),'started_at_ms':100}
 rh=digest(request)
 executions=[{'execution_id':'x1','command_id':'open','quantity':'100000','price':'1','commission':'2','commission_currency':'USD','side':'BUY'}, {'execution_id':'x2','command_id':'close','quantity':'100000','price':'1.0001','commission':'2','commission_currency':'USD','side':'SELL'}]
 reward={'instrument':'EUR.USD','currency':'USD','learning_eligible':True,'exact_input_bound':True,'fees_complete':True,'settlement_complete':True,'flat_to_flat_executed_quantity':True,'policy_gradient_allowed':False,'laya_on_policy':False,'effect_authority':False,'learning_exclusion_reasons':[],'unsettled_commands':[],'input_attribution':{'producer_manifest_sha256':'m','request_id':'r1','prompt_sha256':request['prompt_sha256']},'input_id':rh,'behavior_model_id':'model','forecast_id':'decision','open_command_id':'open','episode_id':'ep','executions':executions,'execution_ids':['x1','x2'],'command_ids':['open','close'],'realized_gross_usd':'10','actual_commissions_usd':'4','realized_net_usd':'6'}
 text=request['prompt']+'\nFinancial value context: EUR.USD; requested reference notional budget=100000 USD; selected action=BUY; behavior model=model. Predict eventual roundtrip net USD after reported execution commissions.'
 row={'schema':'heptatrader.current100k-value-row.v1','label_class':'REAL_EXECUTION_BEHAVIOR_BOUND_VALUE_NOT_ON_POLICY','period_sha256':'p','automatic_training':False,'automatic_selection':False,'reward':reward,'source_request':{'request':request,'request_sha256':rh,'producer_manifest_sha256':'m'},'source_binding':{'producer_manifest_sha256':'m','request_sha256':rh,'request_id':'r1','request_started_at_ms':100,'decision_sha256':'decision','command':{'kind':'OPEN','instrument':'EUR.USD','position_before_send':0,'quantity_mode':'BASE_NOTIONAL_TARGET','target_notional_usd':100000,'command_id':'open','side':'BUY','order_quantity':100000}},'behavior_scope':{'producer_manifest_sha256':'m','model_id':'model','reference_notional_usd':100000,'instrument':'EUR.USD'},'input_id':rh,'request_id':'r1','open_command_id':'open','action':'BUY','episode_id':'ep','input_started_at_ms':100,'feature_text':text,'feature_text_sha256':hashlib.sha256(text.encode()).hexdigest(),'actual_commissions_usd':'4','net_usd':'6'}
 return seal({'schema':'heptatrader.current100k-value-dataset.v1','behavior_profile':'EURUSD_ORIGINAL_ENTRY_CONF_0.62_USD100K_REFERENCE_NOTIONAL','synthetic_rewards':0,'old25k_rewards':0,'automatic_selection':False,'automatic_training':False,'trade_authority':False,'period_sha256':'p','producer_manifest_sha256':'m','rows':[row]})

class ExportTest(unittest.TestCase):
 def test_complete(self):
  r=reconcile(fixture());self.assertEqual(r['rows_checked'],1);self.assertEqual(r['totals_exported_subset']['net_usd'],'6.0000')
 def mutate(self, change):
  d=fixture();change(d);seal(d)
  with self.assertRaises(ValueError):reconcile(d)
 def test_economic_change_after_rehash(self):self.mutate(lambda d:d['rows'][0]['reward']['executions'][1].update(price='1.0002'))
 def test_missing_fee(self):self.mutate(lambda d:d['rows'][0]['reward']['executions'][0].update(commission=None))
 def test_duplicate_execution(self):self.mutate(lambda d:d['rows'][0]['reward']['executions'][1].update(execution_id='x1'))
 def test_duplicate_rows(self):self.mutate(lambda d:d['rows'].append(copy.deepcopy(d['rows'][0])))
 def test_old25k_scope(self):self.mutate(lambda d:d['rows'][0]['source_binding']['command'].update(target_notional_usd=25000))
 def test_wrong_decision(self):self.mutate(lambda d:d['rows'][0]['source_binding'].update(decision_sha256='wrong'))
 def test_wrong_model(self):self.mutate(lambda d:d['rows'][0]['behavior_scope'].update(model_id='other'))
 def test_feature_leakage(self):self.mutate(lambda d:d['rows'][0].update(feature_text='future reward 6'))
 def test_unsettled_reward(self):self.mutate(lambda d:d['rows'][0]['reward'].update(settlement_complete=False))
 def test_nonflat(self):self.mutate(lambda d:d['rows'][0]['reward']['executions'][1].update(quantity='99999'))
 def test_authority_escalation(self):self.mutate(lambda d:d.update(trade_authority=True))
 def test_full_payload_fingerprint(self):
  d=fixture();d['fingerprint']=digest([d['rows'][0]['row_id']])
  with self.assertRaisesRegex(ValueError,'DATASET_FINGERPRINT'):reconcile(d)
 def test_hash_failure(self):
  d=fixture();d['rows'][0]['net_usd']='7'
  with self.assertRaisesRegex(ValueError,'ROW_HASH'):reconcile(d)
if __name__=='__main__':unittest.main()
