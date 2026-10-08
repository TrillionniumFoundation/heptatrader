"""Independent read-only audit of the fixed 24-slot paired MID experiment.

Does not recompute, append or overwrite experiment predictions/outcomes.
The registered sign test is an exploratory screen, not canonical owner proof.
"""
from __future__ import annotations
import argparse,hashlib,json,math,os,time
from pathlib import Path
from bisect import bisect_left,bisect_right
from .common import RULE,ARMS,check_candidate,check_plan,require,utc_ms
from .evaluate import score,sign_p
from laya_cell.integrity import ContractError,read_json,sha_file,digest
from laya_cell.resident_data import read_quotes
from laya_cell.dataset import BIN_EDGES_BPS

def verify_prefixes(pred):
 for v in pred['source_prefixes']:
  p=Path(v['path'])
  if p.is_symlink() or not p.is_file() or type(v.get('bytes'))is not int or v['bytes']>64*1024*1024:
   raise ContractError('SOURCE_PREFIX_SCOPE')
  with p.open('rb') as f: raw=f.read(v['bytes'])
  require(len(raw)==v['bytes'] and hashlib.sha256(raw).hexdigest()==v['sha256'],'SOURCE_PREFIX_REWRITTEN')
  require(not raw or raw.endswith(b'\n'),'SOURCE_PREFIX_UNCOMMITTED')

def inspect(config,now=None):
 now=utc_ms() if now is None else now;root=Path(config['shadow_root'])
 plan=check_plan(read_json(root/'plan.json'),now)
 candidate=check_candidate(config['candidate_directory'])
 require(candidate['candidate_id']==plan['candidate_id'],'AUDIT_CANDIDATE_ID')
 require(candidate['trained_model_sha256']==plan['candidate_model_sha256'],'AUDIT_MODEL_BYTES')
 require(plan['candidate_created_at_ms']==candidate['created_at_ms'],'AUDIT_MODEL_CREATED')
 require(plan['period_sha256']==config['period_sha256'],'AUDIT_PERIOD')
 require(sha_file(Path(config['period_path']))==config['period_sha256'],'AUDIT_PERIOD_CHANGED')
 for name,original_sha in plan['runtime_sha256'].items():
  require(sha_file(Path(config['runtime_directory'])/name)==original_sha,'PINNED_EXPERIMENT_SOURCE_CHANGED')
 quotes,prefixes=read_quotes(config,now);times=[q['t'] for q in quotes];records=[];issues=[]
 for slot in range(RULE['slots']):
  original_start=plan['start_ms']+slot*RULE['slot_spacing_ms']
  prediction=root/f'prediction-{slot:03}.json';failure=root/f'failure-{slot:03}.json';outcome=root/f'outcome-{slot:03}.json'
  item={'slot':slot,'expected_start_ms':original_start,'prediction_sha256':None,
   'outcome_sha256':sha_file(outcome) if outcome.exists() else None,
   'status':'FUTURE_INPUT_NOT_YET_DUE' if now<original_start else 'UNKNOWN'}
  if not prediction.exists():
   if failure.exists():
    f=read_json(failure)
    item.update(status='REGISTERED_FAILURE',reason=f.get('reason'),failure_sha256=sha_file(failure))
   elif now>=original_start+RULE['input_grace_ms']+30000:
    item.update(status='MISSING_FIXED_SLOT',reason='NO_FROZEN_PREDICTION')
   elif now>=original_start:item.update(status='INPUT_GRACE_OPEN')
   records.append(item);continue
  if failure.exists():
   raise ContractError('PREDICTION_AND_FAILURE_COEXIST')
  p=read_json(prediction);item['prediction_sha256']=sha_file(prediction)
  if (p.get('plan_id')!=plan['plan_id'] or p.get('candidate_id')!=plan['candidate_id']
      or p.get('slot')!=slot or p.get('effect_authority') is not False
      or p.get('broker_orders_sent')!=0 or p.get('horizon_sec')!=300
      or p.get('train_label_cutoff_ms')!=candidate['trained_until_ms']
      or set(p.get('arms',{}))!=set(ARMS)
      or p.get('input_id')!=digest(p.get('input_state'))):
   raise ContractError('INDEPENDENT_PREDICTION_BINDING_CHANGED')
  verify_prefixes(p)
  for values in p['arms'].values():score(values,3)
  anchor=p['anchor_ms'];due=anchor+RULE['horizon_ms']
  require(original_start<=anchor<=original_start+RULE['input_grace_ms'],'ORIGINAL_SLOT_ANCHOR')
  require(anchor<=p['created_at_ms']<=original_start+RULE['input_grace_ms'],'PREDICTION_NOT_COMMITTED_IN_TIME')
  require(p['created_at_ms']<due and candidate['created_at_ms']<anchor and candidate['trained_until_ms']<anchor,'TRAINING_IN_FUTURE')
  item.update(status='PREDICTION_SEALED',anchor_ms=anchor,created_at_ms=p['created_at_ms'],arms=sorted(p['arms']))
  if now<due+RULE['endpoint_tolerance_ms']+60000:
   item['status']='WAITING_FOR_ACTUAL_FUTURE_OUTCOME';records.append(item);continue
  a=bisect_left(times,anchor);b=bisect_left(times,due);reason=None
  if a>=len(times) or times[a]!=anchor or abs(quotes[a]['mid']-p['reference_mid'])>1e-12:reason='ANCHOR_CHANGED'
  elif b>=len(times):reason='OUTCOME_MISSING'
  elif times[b]>due+RULE['endpoint_tolerance_ms']:reason='ENDPOINT_GAP'
  elif any(y-x>RULE['path_gap_max_ms'] for x,y in zip(times[a:b],times[a+1:b+1])):reason='PATH_GAP'
  if not outcome.exists():
   item.update(status='EVALUATOR_RECORD_PENDING',reason=reason or 'NOT_YET_WRITTEN')
   records.append(item);continue
  o=read_json(outcome)
  if reason:
   require(o.get('status')=='CENSORED' and o.get('reason')==reason,'CENSOR_REASON_MISMATCH')
   item.update(status='CENSORED',reason=reason)
  else:
   target=bisect_right(BIN_EDGES_BPS,(quotes[b]['mid']/quotes[a]['mid']-1)*10000)
   brier={name:score(p['arms'][name],target) for name in ARMS}
   require(o.get('status')=='OBSERVED' and o.get('slot')==slot and
    o.get('plan_id')==plan['plan_id'] and o.get('candidate_id')==candidate['candidate_id'] and
    o.get('target_class')==target and o.get('prediction_sha256')==sha_file(prediction),'OBSERVATION_IDENTITY')
   require(set(o['brier'])==set(ARMS) and all(abs(o['brier'][k]-v)<1e-8 for k,v in brier.items()),
     'INDEPENDENT_BRIER_DISAGREES_WITH_EVALUATOR')
   require(o.get('endpoint_receipt')==quotes[b]['receipt'],'ENDPOINT_QUOTE_WITNESS')
   item.update(status='OBSERVED',target_class=target,return_bps=(quotes[b]['mid']/quotes[a]['mid']-1)*10000,brier=brier,
     actual_endpoint_ms=times[b],endpoint_receipt_digest=digest(quotes[b]['receipt']))
  records.append(item)
 # Assessment is consulted only AFTER independent reconstruction.
 observed=[r for r in records if r['status']=='OBSERVED']
 censored=[r for r in records if r['status'] in ('CENSORED','REGISTERED_FAILURE','MISSING_FIXED_SLOT')]
 comparisons={}
 for baseline in RULE['comparators']:
  diffs=[r['brier'][baseline]-r['brier']['gate'] for r in observed]
  wins=sum(x>0 for x in diffs);ties=sum(x==0 for x in diffs);n=len(diffs)-ties
  comparisons[baseline]={'observed_pairs':len(diffs),'mean_brier_gain':sum(diffs)/len(diffs) if diffs else None,
   'wins':wins,'losses':n-wins,'ties':ties,'unadjusted_sign_screen_p':sign_p(wins,n),
   'time_dependence_robust':False}
 finished=now>=plan['end_ms'] and len([r for r in records if r['status'] in ('OBSERVED','CENSORED','REGISTERED_FAILURE','MISSING_FIXED_SLOT')])==RULE['slots']
 audit={'schema':'heptatrader.paired-price-independent-replay.v1','at_ms':now,
  'plan_id':plan['plan_id'],'candidate_id':candidate['candidate_id'],'plan_sha256':sha_file(root/'plan.json'),
  'candidate_sha256':sha_file(Path(config['candidate_directory'])/'candidate.json'),
  'rule_sha256':digest(RULE),'source_prefixes_current':prefixes,
  'slot_records':records,'outcome_status_counts':{s:sum(x['status']==s for x in records) for s in sorted(set(x['status'] for x in records))},
  'observed':len(observed),'censored_or_missing':len(censored),'comparisons':comparisons,
  'state':'COMPLETE_INDEPENDENT_REPLAY_REQUIRES_SCOPE_REVIEW' if finished else 'WAIT_FIXED_OUTCOMES_NO_ADVANTAGE_CLAIM',
  'old_failed_window_rewritten':False,'canonical_owner_admitted':False,
  'model_selected':False,'broker_orders_sent':0}
 return audit

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
 a=p.parse_args();os.umask(0o077);config=read_json(a.config);result=inspect(config)
 a.output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 # Each observer invocation saves a new immutable timestamp-specific review, no
 # rewriting the running experiment's own files.
 with a.output.open('xb') as f:raw=(json.dumps(result,sort_keys=True,separators=(',',':'))+'\n').encode();f.write(raw);f.flush();os.fsync(f.fileno())
 print(json.dumps({'state':result['state'],'observed':result['observed'],'censored_or_missing':result['censored_or_missing'],
  'outcome_status_counts':result['outcome_status_counts'],'comparisons':result['comparisons'],
  'audit_sha256':hashlib.sha256(raw).hexdigest(),'written_to':str(a.output)},indent=2))
if __name__=='__main__':main()
