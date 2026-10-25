"""Causal input continuity, nonblocking scheduling and finance/credit separation."""
import copy,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from laya_cell.integrity import ContractError,digest
from laya_cell.resident_data import contiguous_history,forecast_input
from laya_cell.resident_rewards import financial_episodes
from laya_cell.resident_evaluate import measure,Evaluator
from laya_cell.resident_store import database
from laya_cell.promotion import RULE

T=1791362000000

def quotes(times):return [{'t':t,'known_at_ms':t,'mid':1.1,'bid':'1.09999','ask':'1.10001','receipt':str(t)} for t in times]

def report():
 return {'fees_complete':True,'settlement_pending_command_ids':[],
  'executions':[{'execution_id':'e1','command_id':'open1','side':'BUY','quantity':'1','price':'100','commission':'1','commission_currency':'USD'},
  {'execution_id':'e2','command_id':'close1','side':'SELL','quantity':'1','price':'105','commission':'1','commission_currency':'USD'}]}

class ContinuityTests(unittest.TestCase):
 def test_never_cross_old_gap_but_recent_contiguous_history_is_usable(self):
  q=quotes([T+i*10000 for i in range(10)]+[T+500000+i*10000 for i in range(7)])
  h,reset=contiguous_history(q,T+560001)
  self.assertTrue(reset);self.assertEqual(len(h),7);self.assertEqual(h[0]['t'],T+500000)
  s,_=forecast_input({'period_sha256':'1'*64},q,T+560001)
  self.assertFalse(s['input_gap']);self.assertTrue(s['contiguous_history_reset_at_gap'])
 def test_short_suffix_must_wait_not_borrow_across_gap(self):
  q=quotes([T+i*10000 for i in range(24)]+[T+600000])
  with self.assertRaisesRegex(ContractError,'INSUFFICIENT_CONTIGUOUS'):forecast_input({'period_sha256':'x'},q,T+600001)
 def test_stale_latest_quote_is_still_rejected(self):
  q=quotes([T+i*10000 for i in range(6)])
  with self.assertRaisesRegex(ContractError,'NOT_FRESH'):forecast_input({'period_sha256':'x'},q,T+56000)
 def test_future_arrivals_never_enter_input(self):
  q=quotes([T+i*10000 for i in range(7)]);q[-1]['known_at_ms']=T+90000
  h,_=contiguous_history(q,T+60000);self.assertEqual(h[-1]['t'],T+50000)
 def test_original_timestamps_preserved(self):
  q=quotes([T+i*10000 for i in range(10)]);raw=copy.deepcopy(q);contiguous_history(q,T+90000);self.assertEqual(q,raw)
 def test_boundary_30seconds_allowed_31_rejected(self):
  q=quotes([T,T+10000,T+20000,T+30000,T+40000,T+70000]);self.assertEqual(len(contiguous_history(q,T+70000)[0]),6)
  q[-1]['t']+=1000;q[-1]['known_at_ms']+=1000;self.assertEqual(len(contiguous_history(q,T+71000)[0]),1)

class FinancialSeparationTests(unittest.TestCase):
 def test_actual_positive_reward_kept_without_faking_input(self):
  rs,_=financial_episodes(report(),{},'period','EUR.USD');r=rs[0]
  self.assertEqual(r['realized_net_usd'],'3');self.assertIsNone(r['input_id']);self.assertFalse(r['learning_eligible'])
 def test_loss_not_filtered(self):
  x=report();x['executions'][1]['price']='95';rs,_=financial_episodes(x,{},'period','BTC.USD');self.assertEqual(rs[0]['realized_net_usd'],'-7')
 def test_different_instrument_rewards_never_alias(self):
  a=financial_episodes(report(),{},'period','EUR.USD')[0][0];b=financial_episodes(report(),{},'period','BTC.USD')[0][0]
  self.assertNotEqual(a['reward_id'],b['reward_id'])
 def test_unknown_commission_stays_null_not_zero(self):
  x=report();x['executions'][0]['commission']=None;r=financial_episodes(x,{},'period','BTC.USD')[0][0]
  self.assertIsNone(r['realized_net_usd']);self.assertFalse(r['fees_complete'])
 def test_missing_one_command_status_does_not_hide_other_instrument_profit(self):
  x=report();x['settlement_pending_command_ids']=['unrelated-command']
  b={'open1':{'input_id':'i','forecast_id':'f','model_id':'remote'}}
  self.assertTrue(financial_episodes(x,b,'period','EUR.USD')[0][0]['learning_eligible'])
 def test_own_missing_terminal_status_keeps_financial_fact_but_blocks_credit(self):
  x=report();x['settlement_pending_command_ids']=['open1'];r=financial_episodes(x,{},'period','BTC.USD')[0][0]
  self.assertEqual(r['realized_net_usd'],'3');self.assertFalse(r['settlement_complete']);self.assertFalse(r['learning_eligible'])
 def test_complete_binding_gives_value_target_never_onpolicy_actor_reward(self):
  b={'open1':{'input_id':'i','forecast_id':'f','model_id':'remote'}};r=financial_episodes(report(),b,'period','EUR.USD')[0][0]
  self.assertTrue(r['learning_eligible']);self.assertFalse(r['policy_gradient_allowed']);self.assertFalse(r['laya_on_policy'])
 def test_duplicate_execution_raises(self):
  x=report();x['executions']*=2
  with self.assertRaisesRegex(ContractError,'DUPLICATE'):financial_episodes(x,{},'p','BTC.USD')
 def test_residual_position_never_roundtrip_reward(self):
  x=report();x['executions']=x['executions'][:1];r,b=financial_episodes(x,{},'p','BTC.USD');self.assertEqual(r,[]);self.assertEqual(b[0]['reason'],'OPEN_RESIDUAL_POSITION')
 def test_correction_creates_new_version_not_double_active_credit(self):
  x=report();r=financial_episodes(x,{},'p','EUR.USD')[0][0];x['executions'][1]['commission']='2';s=financial_episodes(x,{},'p','EUR.USD')[0][0]
  self.assertEqual(r['episode_id'],s['episode_id']);self.assertNotEqual(r['reward_id'],s['reward_id'])
 def test_ledger_has_separate_financial_table(self):
  with tempfile.TemporaryDirectory() as td:
   d=database(Path(td)/'scheduler.sqlite');tables={r[0] for r in d.execute("select name from sqlite_master where type='table'")}
   self.assertTrue({'rewards','execution_rewards'}<=tables);d.close()

class EndpointTests(unittest.TestCase):
 def fixture(self):
  p={'plan_id':'p','candidate_id':'c'};state={'instrument':'EUR.USD'}
  pred={'candidate_id':'c','plan_id':'p','slot':0,'input':state,'input_id':digest(state),'anchor_ms':T,'created_at_ms':T+1000,
   'trained_until_ms':T-1,'reference_mid':1.1,'candidate_probs':[1/7]*7,'parent_probs':[1/7]*7,
   'candidate_act_escalate_probs':[.5,.5],'bootstrap_point_bps':0}
  q=quotes([T+i*10000 for i in range(32)]);return p,pred,q
 def test_late_source_read_must_use_current_prefix(self):
  p,r,q=self.fixture();self.assertEqual(measure(p,0,r,q,T+320000)['status'],'OBSERVED')
  self.assertEqual(measure(p,0,r,q[:10],T+320000)['reason'],'OUTCOME_MISSING')
 def test_no_endpoint_interpolation(self):
  p,r,q=self.fixture();q=[x for x in q if x['t']!=T+300000]
  self.assertEqual(measure(p,0,r,q,T+320000)['reason'],'ENDPOINT_GAP')
 def test_missing_path_cannot_pass_with_valid_endpoint(self):
  p,r,q=self.fixture();q=[x for x in q if not T+100000<x['t']<T+150000]
  self.assertEqual(measure(p,0,r,q,T+320000)['reason'],'PATH_GAP')
 def test_current_clock_does_not_mature_future_early(self):
  p,r,q=self.fixture();self.assertIsNone(measure(p,0,r,q[:20],T+250000))
 def test_new_plan_requires_actual_durable_publication_before_outcome(self):
  from laya_cell.resident_evaluate import PUBLICATION_PROFILE
  p,r,q=self.fixture();p.update(prediction_publication_profile=PUBLICATION_PROFILE,start_ms=T,rule=RULE)
  r['prediction_publication_profile']=PUBLICATION_PROFILE
  publication={'profile':PUBLICATION_PROFILE,'plan_id':'p','slot':0,
   'prediction_digest':digest(r),'durably_published_at_ms':T+250000}
  result=measure(p,0,r,q,T+320000,publication_record=publication)
  self.assertEqual(result['status'],'OBSERVED');self.assertEqual(result['prediction_available_ms'],T+250000)
  publication['durably_published_at_ms']=T+300000
  self.assertEqual(measure(p,0,r,q,T+320000,publication_record=publication)['reason'],'LOOKAHEAD')
  self.assertEqual(measure(p,0,r,q,T+320000)['reason'],'PREDICTION_PUBLICATION_TIME_UNAVAILABLE')
  self.assertIsNone(measure(p,0,r,q,T+300001))
  publication['prediction_digest']='wrong'
  with self.assertRaisesRegex(ContractError,'PUBLICATION_RECEIPT_CHANGED'):
   measure(p,0,r,q,T+320000,publication_record=publication)

class AsyncSchedulingTests(unittest.TestCase):
 def setup_case(self,start):
  from laya_cell.integrity import sha_file,canonical
  from unittest.mock import MagicMock
  td=tempfile.TemporaryDirectory();self.addCleanup(td.cleanup);root=Path(td.name)
  period=root/'period.json';period.write_text('{}');pin=root/'pinned.py';pin.write_text('fixed')
  config={'period_path':str(period),'period_sha256':sha_file(period),'evaluation_directory':str(root/'evaluation'),
          'runtime_directory':str(root),'python':'unused-python'}
  cfg=root/'config.json';cfg.write_bytes(canonical(config));engine=Evaluator(cfg)
  folder=root/'evaluation/plan';folder.mkdir()
  plan={'candidate_id':'c','rule':RULE,'scope':config['period_sha256'],'start_ms':start,'outcome_end_ms':T+99999999,
        'trial':1,'evaluation_runtime_sha256':{'pinned.py':sha_file(pin)}}
  plan['plan_id']=digest(plan);path=folder/'plan.json';path.write_bytes(canonical(plan))
  proc=MagicMock();proc.poll.return_value=None;proc.wait.side_effect=AssertionError('Must not block on GPU worker')
  self.addCleanup(lambda:[v[1].close() for v in engine.tasks.values()])
  return engine,plan,path,proc
 def test_model_prewarm_starts_before_input_slot_without_blocking(self):
  engine,plan,path,proc=self.setup_case(T+60000)
  with patch('laya_cell.resident_evaluate.time.time',return_value=T/1000),patch('laya_cell.resident_evaluate.read_quotes',return_value=([],[])) as rd,patch('laya_cell.resident_evaluate.subprocess.Popen',return_value=proc):
   first=engine.step();second=engine.step()
  self.assertEqual(first['active_prediction_workers'],1);self.assertEqual(second['active_prediction_workers'],1)
  rd.assert_not_called();proc.wait.assert_not_called()
  self.assertFalse(first['source_scan_performed']);self.assertFalse(second['source_scan_performed'])
  self.assertEqual(list(path.parent.glob('outcome-*.json')),[])
 def test_outcome_refresh_does_not_use_snapshot_from_before_worker(self):
  from laya_cell.integrity import canonical
  engine,plan,path,proc=self.setup_case(T)
  _,pred,qs=EndpointTests().fixture();pred['plan_id']=plan['plan_id']
  (path.parent/'prediction-000.json').write_bytes(canonical(pred))
  with patch('laya_cell.resident_evaluate.time.time',return_value=(T+250000)/1000) as clock,patch('laya_cell.resident_evaluate.read_quotes',return_value=(qs,[])) as rd,patch('laya_cell.resident_evaluate.subprocess.Popen',return_value=proc):
   first=engine.step()
   rd.assert_not_called();self.assertFalse(first['source_scan_performed'])
   self.assertFalse((path.parent/'outcome-000.json').exists())
   # The step starts mature, then evidence loading happens at a later time.
   # Preserve the fresh causal as-of check as well as cross-step laziness.
   clock.return_value=(T+310000)/1000
   from laya_cell.resident_evaluate import read_json as actual_read_json
   def advance_after_prediction(file):
    value=actual_read_json(file)
    if Path(file)==path.parent/'prediction-000.json':
     clock.return_value=(T+320000)/1000
    return value
   with patch('laya_cell.resident_evaluate.read_json',side_effect=advance_after_prediction):
    second=engine.step()
  result=json.loads((path.parent/'outcome-000.json').read_bytes())
  self.assertEqual(result['status'],'OBSERVED')
  self.assertEqual(result['slot'],0);self.assertEqual(result['plan_id'],plan['plan_id'])
  self.assertEqual(len(list(path.parent.glob('outcome-*.json'))),1)
  rd.assert_called_once_with(engine.c,T+320000)
  self.assertTrue(second['source_scan_performed']);proc.wait.assert_not_called()
 def test_failed_worker_retains_reason_and_phase(self):
  engine,plan,path,proc=self.setup_case(T+60000)
  with patch('laya_cell.resident_evaluate.time.time',return_value=T/1000),patch('laya_cell.resident_evaluate.read_quotes',return_value=([],[])),patch('laya_cell.resident_evaluate.subprocess.Popen',return_value=proc):
   engine.step();proc.poll.return_value=2;proc.wait.side_effect=None;proc.wait.return_value=2;engine.step()
  out=json.loads((path.parent/'outcome-000.json').read_bytes())
  self.assertEqual(out['reason'],'PREDICTION_PROCESS_FAILED');self.assertEqual(out['phase']['phase'],'STARTUP')
  self.assertEqual(out['detail'],'NO_PROCESS_DIAGNOSTIC')
 def test_due_prewarm_precedes_slow_mature_outcome_scan_in_same_plan(self):
  from laya_cell.integrity import canonical
  engine,plan,path,proc=self.setup_case(T)
  _,pred,qs=EndpointTests().fixture();pred['plan_id']=plan['plan_id']
  (path.parent/'prediction-000.json').write_bytes(canonical(pred))
  clock=[T+310000];events=[]
  def launch(*args,**kwargs):events.append(('launch',clock[0]));return proc
  def slow_source(*_):
   events.append(('source',clock[0]));clock[0]=T+480000
   return qs,[]
  with patch('laya_cell.resident_evaluate.time.time',side_effect=lambda:clock[0]/1000), \
       patch('laya_cell.resident_evaluate.read_quotes',side_effect=slow_source), \
       patch('laya_cell.resident_evaluate.subprocess.Popen',side_effect=launch):
   engine.step()
  self.assertEqual(events,[('launch',T+310000),('source',T+310000)])
  self.assertIn((str(path),1),engine.tasks)
  self.assertEqual(json.loads((path.parent/'outcome-000.json').read_bytes())['status'],'OBSERVED')
  self.assertEqual(json.loads(path.read_bytes()),plan)
 def test_due_plan_is_dispatched_before_older_plan_cpu_closeout(self):
  from laya_cell.integrity import canonical
  engine,plan,path,proc=self.setup_case(T-3600000)
  _,pred,qs=EndpointTests().fixture();pred['plan_id']=plan['plan_id']
  for key in ('anchor_ms','created_at_ms','trained_until_ms'):pred[key]-=3600000
  for quote in qs:quote['t']-=3600000;quote['known_at_ms']-=3600000
  # All other historical slots are already immutable; this old source read
  # must not consume the new plan's fixed input budget.
  for slot in range(1,RULE['slots']):
   (path.parent/f'outcome-{slot:03}.json').write_bytes(canonical({'candidate_id':'c',
    'plan_id':plan['plan_id'],'slot':slot,'status':'CENSORED'}))
  (path.parent/'prediction-000.json').write_bytes(canonical(pred))
  current=copy.deepcopy(plan);current.update(candidate_id='new',start_ms=T+370000)
  current.pop('plan_id');current['plan_id']=digest(current)
  folder=path.parent.parent/'z-current';folder.mkdir();current_path=folder/'plan.json'
  current_path.write_bytes(canonical(current))
  clock=[T+310000];events=[]
  def launch(*args,**kwargs):events.append(('launch',clock[0],args[0]));return proc
  def slow_source(*_):events.append(('source',clock[0],None));clock[0]=T+480000;return qs,[]
  with patch('laya_cell.resident_evaluate.time.time',side_effect=lambda:clock[0]/1000), \
       patch('laya_cell.resident_evaluate.read_quotes',side_effect=slow_source), \
       patch('laya_cell.resident_evaluate.subprocess.Popen',side_effect=launch):
   engine.step()
  self.assertEqual([event[0] for event in events],['launch','source'])
  self.assertIn(str(current_path),events[0][2]);self.assertEqual(events[0][1],T+310000)
  self.assertIn((str(current_path),0),engine.tasks)
 def test_actual_dispatch_after_expensive_preflight_cannot_launch_expired_slot(self):
  engine,plan,path,proc=self.setup_case(T)
  with patch('laya_cell.resident_evaluate.time.time',return_value=(T+RULE['input_grace_ms']+1)/1000), \
       patch('laya_cell.resident_evaluate.subprocess.Popen',side_effect=AssertionError('expired slot')):
   self.assertFalse(engine.launch(path,0))
  self.assertFalse(engine.tasks)
  denial=json.loads((path.parent/'admission-denial-000.json').read_bytes())
  self.assertEqual(denial['reason_codes'],['INPUT_SLOT_EXPIRED_BEFORE_WORKER_DISPATCH'])
  self.assertGreater(denial['at_ms'],T+RULE['input_grace_ms'])
 def test_futile_same_runtime_plan_cannot_block_viable_dispatch_with_source_scan(self):
  from laya_cell.integrity import canonical
  engine,plan,path,proc=self.setup_case(T-4*RULE['slot_spacing_ms'])
  _,pred,qs=EndpointTests().fixture();pred['plan_id']=plan['plan_id']
  shift=4*RULE['slot_spacing_ms']
  for key in ('anchor_ms','created_at_ms','trained_until_ms'):pred[key]-=shift
  for quote in qs:quote['t']-=shift;quote['known_at_ms']-=shift
  (path.parent/'prediction-000.json').write_bytes(canonical(pred))
  originals={}
  for slot in (1,2,3):
   target=path.parent/f'outcome-{slot:03}.json'
   target.write_bytes(canonical({'candidate_id':'c','plan_id':plan['plan_id'],
    'slot':slot,'status':'CENSORED','evaluated_at_ms':T-100000}))
   originals[target]=target.read_bytes()
  current=copy.deepcopy(plan);current.update(candidate_id='viable',start_ms=T+10000)
  current.pop('plan_id');current['plan_id']=digest(current)
  folder=path.parent.parent/'z-viable';folder.mkdir();current_path=folder/'plan.json'
  current_path.write_bytes(canonical(current))
  clock=[T-60000];events=[]
  def launch(*args,**kwargs):events.append(('launch',clock[0],args[0]));return proc
  def slow_source(*_):events.append(('source',clock[0],None));clock[0]+=300000;return qs,[]
  with patch('laya_cell.resident_evaluate.time.time',side_effect=lambda:clock[0]/1000), \
       patch('laya_cell.resident_evaluate.read_quotes',side_effect=slow_source), \
       patch('laya_cell.resident_evaluate.subprocess.Popen',side_effect=launch):
   engine.step()
  self.assertEqual([event[0] for event in events],['launch','source'])
  self.assertIn(str(current_path),events[0][2]);self.assertEqual(events[0][1],T-60000)
  self.assertNotIn((str(path),4),engine.tasks)
  for target,raw in originals.items():self.assertEqual(target.read_bytes(),raw)

if __name__=='__main__':unittest.main(verbosity=2)
