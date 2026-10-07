"""Independent resident future evaluator and conditional promotion assessment.

The evaluator has no optimizer, no write path into model tensors and no execution
client. QUALIFIED is still not a canonical-owner admission or a trading cutover.
"""
from __future__ import annotations
import argparse,fcntl,json,math,os,signal,subprocess,sys,time
from bisect import bisect_left,bisect_right
from pathlib import Path
from .integrity import ContractError,read_json,digest,sha_file
from .resident_store import write_once,atomic
from .resident_data import collect
from .dataset import BIN_EDGES_BPS
from .promotion import RULE,assess


def measure(plan,slot,prediction,quotes,now_ms):
 if prediction.get('candidate_id')!=plan['candidate_id'] or prediction.get('plan_id')!=plan['plan_id'] or prediction.get('slot')!=slot:
  raise ContractError('PREDICTION_IDENTITY')
 if prediction.get('input_id')!=digest(prediction.get('input')):raise ContractError('PREDICTION_INPUT_HASH')
 anchor=prediction['anchor_ms'];due=anchor+RULE['horizon_ms'];times=[q['t'] for q in quotes]
 a=bisect_left(times,anchor);b=bisect_left(times,due)
 if b==len(times) and now_ms<=due+15000:return None
 reason=None
 if prediction['created_at_ms']>=due or prediction['trained_until_ms']>anchor:reason='LOOKAHEAD'
 elif a>=len(times) or times[a]!=anchor or quotes[a]['mid']!=prediction['reference_mid']:reason='ANCHOR_MISSING_OR_CHANGED'
 elif b>=len(times):reason='OUTCOME_MISSING'
 elif times[b]>due+5000:reason='ENDPOINT_GAP'
 elif any(y-x>30000 for x,y in zip(times[a:b],times[a+1:b+1])):reason='PATH_GAP'
 base={'candidate_id':plan['candidate_id'],'plan_id':plan['plan_id'],'slot':slot,'anchor_ms':anchor,
       'prediction_available_ms':prediction['created_at_ms'],'trained_until_ms':prediction['trained_until_ms'],
       'evaluated_at_ms':now_ms,'broker_orders_sent':0,'metric_scope':'PREDICTION_NOT_EXECUTION_PNL'}
 if reason:return {**base,'status':'CENSORED','reason':reason}
 value=(quotes[b]['mid']/quotes[a]['mid']-1)*10000;target=bisect_right(BIN_EDGES_BPS,value)
 def score(probs):
  if len(probs)!=7 or any(type(x) not in (int,float) or not math.isfinite(x) or not 0<=x<=1 for x in probs) or abs(sum(probs)-1)>1e-5:
   raise ContractError('PROBABILITY_VECTOR')
  return sum((p-(i==target))**2 for i,p in enumerate(probs))
 p=prediction['candidate_probs'];parent=prediction['parent_probs'];act=prediction['candidate_act_escalate_probs']
 if len(act)!=2 or any(not math.isfinite(x) for x in act):raise ContractError('ACT_ESCALATE_VECTOR')
 parent_correct=max(range(7),key=lambda i:parent[i])==target
 action_target=0 if parent_correct else 1
 return {**base,'status':'OBSERVED','path_gap':False,'reason':None,'return_bps':value,'target_class':target,
  'candidate_brier':score(p),'parent_brier':score(parent),'candidate_hit':max(range(7),key=lambda i:p[i])==target,
  'bootstrap_hit':bisect_right(BIN_EDGES_BPS,prediction['bootstrap_point_bps'])==target,
  'action_head_brier':sum((v-(i==action_target))**2 for i,v in enumerate(act)),
  'outcome_receipt':quotes[b]['receipt'],'outcome_ms':times[b]}


class Evaluator:
 def __init__(self,config_path):
  self.path=Path(config_path);self.c=read_json(self.path);self.root=Path(self.c['evaluation_directory'])
  self.root.mkdir(mode=0o700,parents=True,exist_ok=True);self.stop=False;self.child=None;self.last_log_ms=0
 def signal(self,*_):
  self.stop=True
  if self.child and self.child.poll() is None:self.child.terminate()
 def step(self):
  now=int(time.time()*1000)
  if sha_file(Path(self.c['period_path']))!=self.c['period_sha256']:raise ContractError('EVAL_PERIOD_CHANGED')
  _,_,quality,quotes=collect(self.c,now)
  plans=sorted(self.root.glob('*/plan.json'))
  if len(plans)>512:raise ContractError('EVALUATION_CAPACITY_NO_PRUNING')
  counts={};new_predictions=0
  for path in plans:
   plan=read_json(path)
   if plan['plan_id']!=digest({k:v for k,v in plan.items() if k!='plan_id'}) or plan['rule']!=RULE:
    raise ContractError('REGISTERED_PLAN_CHANGED')
   pins=plan.get('evaluation_runtime_sha256')
   runtime=Path(self.c.get('runtime_directory',''))
   source_ok=isinstance(pins,dict) and bool(pins) and all(expected and sha_file(runtime/rel)==expected for rel,expected in pins.items())
   if not source_ok:
    # Old pre-deployment experiments stay recorded; do not silently assess them
    # with a changed evaluator and do not rewrite their reserved window.
    decision={'state':'REJECTED','candidate_id':plan['candidate_id'],'plan_id':plan['plan_id'],
      'reasons':['EVALUATOR_SOURCE_UNPINNED_OR_CHANGED'],'promotion_performed':False,
      'canonical_owner_admitted':False,'automatic_execution_selection':False}
    if not (path.parent/'qualification.json').exists():write_once(path.parent/'qualification.json',decision)
    counts['REJECTED']=counts.get('REJECTED',0)+1
    continue
   if (path.parent/'qualification.json').exists():
    q=read_json(path.parent/'qualification.json');counts[q['state']]=counts.get(q['state'],0)+1;continue
   for slot in range(RULE['slots']):
    output=path.parent/f'outcome-{slot:03}.json';prediction=path.parent/f'prediction-{slot:03}.json'
    if output.exists():continue
    start=plan['start_ms']+slot*RULE['slot_spacing_ms'];end=start+RULE['input_grace_ms']
    if not prediction.exists():
     if now>end:
      write_once(output,{'candidate_id':plan['candidate_id'],'plan_id':plan['plan_id'],'slot':slot,'status':'CENSORED',
        'reason':'NO_PREDICTION_IN_FIXED_SLOT','evaluated_at_ms':now});continue
     if now<start:continue
     with (path.parent/f'prediction-{slot:03}.log').open('a') as log:
      self.child=subprocess.Popen([self.c['python'],'-B','-m','laya_cell.resident_launch','predict','--config',str(self.path),'--plan',str(path),'--slot',str(slot)],stdout=log,stderr=log)
      try:rc=self.child.wait(timeout=150)
      except subprocess.TimeoutExpired:self.child.terminate();self.child.wait(timeout=15);rc=-1
      self.child=None
     now=int(time.time()*1000)
     if not prediction.exists():
      write_once(output,{'candidate_id':plan['candidate_id'],'plan_id':plan['plan_id'],'slot':slot,'status':'CENSORED','reason':'PREDICTION_PROCESS_FAILED','exit_code':rc,'evaluated_at_ms':now});continue
     new_predictions+=1
    pred=read_json(prediction)
    if now<pred['anchor_ms']+RULE['horizon_ms']:continue
    result=measure(plan,slot,pred,quotes,now)
    if result is not None:write_once(output,result)
   results=[read_json(p) for p in sorted(path.parent.glob('outcome-*.json'))]
   assessment=assess(plan,results,now)
   if assessment['state']!='PENDING':write_once(path.parent/'qualification.json',assessment)
   else:atomic(path.parent/'progress.json',assessment)
   counts[assessment['state']]=counts.get(assessment['state'],0)+1
  status={'schema':'heptatrader.laya-resident-evaluation-status.v1','at_ms':now,'plans':len(plans),'counts':counts,
   'new_forward_predictions':new_predictions,'latest_quote_ms':quality['latest_quote_ms'],
   'statistical_rule':RULE,'automatic_trading':False,'canonical_owner_connected':False,
   'promotion_performed':False,'broker_orders_sent':0}
  atomic(self.root/'status.json',status)
  if now-self.last_log_ms>=30000 or new_predictions:
   print(json.dumps(status),flush=True);self.last_log_ms=now
  return status

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--once',action='store_true');a=p.parse_args();os.umask(0o077)
 service=Evaluator(a.config);lock=os.open(service.root/'evaluator.lock',os.O_WRONLY|os.O_CREAT|os.O_CLOEXEC,0o600);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 signal.signal(signal.SIGTERM,service.signal);signal.signal(signal.SIGINT,service.signal)
 while not service.stop:
  try:service.step()
  except Exception as exc:atomic(service.root/'status.json',{'state':'INDEPENDENT_EVALUATION_UNAVAILABLE','reason':str(exc),'promotion_performed':False});print(type(exc).__name__,str(exc),flush=True)
  if a.once:break
  for _ in range(2):
   if service.stop:break
   time.sleep(1)
 os.close(lock)
if __name__=='__main__':main()
