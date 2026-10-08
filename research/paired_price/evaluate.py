"""Independent future-MID evaluator: no encoder, optimizer, model selection or orders."""
from __future__ import annotations
import argparse,fcntl,math,os,signal,time
from bisect import bisect_left,bisect_right
from math import comb
from pathlib import Path
from laya_cell.integrity import ContractError,digest,read_json,sha_file
from laya_cell.resident_store import write_once,atomic
from laya_cell.resident_data import read_quotes
from laya_cell.dataset import BIN_EDGES_BPS
from .common import RULE,ARMS,require,check_plan,check_candidate,utc_ms

def score(p,target):
 require(len(p)==7 and all(type(x)in(int,float) and math.isfinite(x) and 0<=x<=1 for x in p) and abs(sum(p)-1)<1e-5,'BAD_FROZEN_PROBABILITY')
 return sum((x-(i==target))**2 for i,x in enumerate(p))
def sign_p(wins,n):
 if n==0:return 1.
 return sum(comb(n,i) for i in range(wins,n+1))/2**n

def summarize(plan,outcomes,now):
 observed=[x for x in outcomes if x['status']=='OBSERVED'];censored=[x for x in outcomes if x['status']=='CENSORED']
 times=sorted(x['anchor_ms'] for x in observed);half=plan['start_ms']+((RULE['slots']-1)*RULE['slot_spacing_ms'])//2
 classes={x['target_class'] for x in observed};reasons=[];comparisons={}
 for baseline in RULE['comparators']:
  differences=[x['brier'][baseline]-x['brier']['gate'] for x in observed]
  wins=sum(x>0 for x in differences);ties=sum(x==0 for x in differences);n=len(differences)-ties
  gain=sum(differences)/len(differences) if differences else None
  p=sign_p(wins,n)
  comparisons[baseline]={'mean_brier_gain':gain,'wins':wins,'losses':n-wins,'ties':ties,
   'one_sided_sign_screen_p':p}
  if gain is None or gain<=RULE['min_mean_brier_gain']:reasons.append('NO_BRIER_GAIN:'+baseline)
  if p>RULE['alpha_each']:reasons.append('SIGN_SCREEN_NOT_PASSED:'+baseline)
 if len(observed)<RULE['min_observed']:reasons.append('INSUFFICIENT_REAL_OBSERVED')
 if len(censored)>RULE['max_censored']:reasons.append('TOO_MANY_CENSORED')
 if len(classes)<RULE['min_class_count']:reasons.append('TARGET_CLASS_COLLAPSE')
 if min(sum(t<half for t in times),sum(t>=half for t in times))<RULE['min_half_observed']:reasons.append('HALF_SUPPORT')
 complete=now>=plan['end_ms'] and len(outcomes)==RULE['slots']
 if not complete:reasons.append('FIXED_WINDOW_NOT_COMPLETE')
 state=('EXPERIMENTAL_GAIN_SCREENED_NO_SELECTION' if not reasons else ('REJECTED' if now>=plan['end_ms'] else 'PENDING'))
 return {'schema':'heptatrader.paired-price-assessment.v1','state':state,'plan_id':plan['plan_id'],
  'candidate_id':plan['candidate_id'],'observed':len(observed),'censored':len(censored),
  'registered_slots':RULE['slots'],'classes':sorted(classes),'comparisons':comparisons,'reasons':reasons,
  'at_ms':now,'screen_not_time_dependence_robust_owner_qualification':True,
  'canonical_owner_accepted':False,'automatic_selection':False,'trading_authority':False,'broker_orders_sent':0}

class Evaluator:
 def __init__(self,c):
  self.c=c;self.root=Path(c['shadow_root']);self.plan=check_plan(read_json(self.root/'plan.json'),utc_ms());self.stop=False
  self.candidate=check_candidate(c['candidate_directory'])
  candidate=self.candidate
  require(candidate['candidate_id']==self.plan['candidate_id'] and
          candidate['trained_model_sha256']==self.plan['candidate_model_sha256'],
          'INDEPENDENT_EVALUATOR_CANDIDATE_CHANGED')
 def step(self):
  now=utc_ms();require(sha_file(Path(self.c['period_path']))==self.c['period_sha256'],'EVALUATION_PERIOD_CHANGED')
  for name,expected in self.plan['runtime_sha256'].items():
   require(sha_file(Path(self.c['runtime_directory'])/name)==expected,'INDEPENDENT_EVALUATOR_RUNTIME_CHANGED')
  quotes,_=read_quotes(self.c,now);times=[x['t'] for x in quotes]
  for slot in range(RULE['slots']):
   path=self.root/f'outcome-{slot:03}.json';pred=self.root/f'prediction-{slot:03}.json';fail=self.root/f'failure-{slot:03}.json'
   if path.exists():continue
   start=self.plan['start_ms']+slot*RULE['slot_spacing_ms']
   if fail.exists():
    write_once(path,{'status':'CENSORED','slot':slot,'reason':read_json(fail)['reason'],'observed_at_ms':now});continue
   if not pred.exists():
    if now>start+RULE['input_grace_ms']+30000:
     write_once(path,{'status':'CENSORED','slot':slot,'reason':'REGISTERED_PREDICTION_ABSENT','observed_at_ms':now})
    continue
   p=read_json(pred)
   anchor=p['anchor_ms'];due=anchor+RULE['horizon_ms']
   if now<due+RULE['endpoint_tolerance_ms']+60000:continue
   why=None
   if p['plan_id']!=self.plan['plan_id'] or p['candidate_id']!=self.plan['candidate_id'] or p['slot']!=slot:why='PREDICTION_IDENTITY'
   elif p['input_id']!=digest(p['input_state']):why='INPUT_HASH_CHANGED'
   elif not start<=anchor<=start+RULE['input_grace_ms']:why='INPUT_OUTSIDE_WINDOW'
   elif p['created_at_ms']<anchor or p['created_at_ms']>=due:why='LATE_OR_BACKDATED_PREDICTION'
   else:
    a=bisect_left(times,anchor);b=bisect_left(times,due)
    if a>=len(times) or times[a]!=anchor or abs(quotes[a]['mid']-p['reference_mid'])>1e-12:why='ANCHOR_CHANGED'
    elif b>=len(times):why='OUTCOME_MISSING'
    elif times[b]>due+RULE['endpoint_tolerance_ms']:why='ENDPOINT_GAP'
    elif any(y-x>RULE['path_gap_max_ms'] for x,y in zip(times[a:b],times[a+1:b+1])):why='PATH_GAP'
   if why:
    write_once(path,{'status':'CENSORED','slot':slot,'reason':why,'observed_at_ms':now});continue
   move=(quotes[b]['mid']/quotes[a]['mid']-1)*10000;target=bisect_right(BIN_EDGES_BPS,move)
   try:
    if (type(p.get('arms'))is not dict or set(p['arms'])!=set(ARMS)
        or p.get('effect_authority') is not False
        or p.get('horizon_sec')!=RULE['horizon_ms']//1000
        or p.get('train_label_cutoff_ms')!=self.candidate['trained_until_ms']):
     raise ContractError('FIVE_ARM_PREDICTION_SCOPE')
    briers={name:score(p['arms'][name],target) for name in ARMS}
   except (ContractError,KeyError,TypeError,ValueError) as exc:
    write_once(path,{'status':'CENSORED','slot':slot,'reason':'INVALID_PREDICTION:'+str(exc)[:100],
      'observed_at_ms':now});continue
   result={'schema':'heptatrader.paired-price-outcome.v1','status':'OBSERVED','slot':slot,
    'plan_id':self.plan['plan_id'],'candidate_id':self.plan['candidate_id'],'anchor_ms':anchor,
    'outcome_ms':times[b],'prediction_sha256':sha_file(pred),'endpoint_receipt':quotes[b]['receipt'],
    'move_bps':move,'target_class':target,'brier':briers,'observed_at_ms':now,
    'source_prefix_verified':True,'trading_authority':False}
   write_once(path,result)
  outcomes=[read_json(p) for p in sorted(self.root.glob('outcome-*.json'))]
  result=summarize(self.plan,outcomes,now)
  atomic(self.root/'assessment.json',result)
  return result

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--once',action='store_true');a=p.parse_args()
 os.umask(0o077);c=read_json(a.config);root=Path(c['shadow_root']);root.mkdir(mode=0o700,parents=True,exist_ok=True)
 fd=os.open(root/'evaluator.lock',os.O_WRONLY|os.O_CREAT|os.O_CLOEXEC|os.O_NOFOLLOW,0o600);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
 worker=Evaluator(c)
 def stop(*_):worker.stop=True
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 while not worker.stop:
  try:worker.step()
  except Exception as exc:atomic(root/'evaluator-status.json',{'state':'EVALUATION_UNAVAILABLE','at_ms':utc_ms(),'reason':str(exc)[:150]})
  if a.once:break
  time.sleep(10)
 os.close(fd)
if __name__=='__main__':main()
