"""Separate seven-bin prospective price screen on future raw bar acquisitions.
Shares ONLY original raw bars and custody with the existing 3-bin campaign;
never reuses its probabilities, labels, Brier or qualification. No orders, no
new broker query, no optimizer, no live-model handoff. CPU inference is bounded
by the existing heavy-worker lease. Fixed misses cannot be replayed.
"""
from __future__ import annotations
import argparse,gc,hashlib,json,math,os,time
from bisect import bisect_right
from datetime import datetime,timezone
from pathlib import Path
from .common import EvidenceError,read,sha,digest,write_once,atomic
EDGES=(-10.,-3.,-1.,1.,3.,10.)
BASELINES=('prior','numeric','equal_mix','graph')
RHO_GRID=(0.,.1,.25,.5,1.)
FAMILY='NEURON256_PRIOR_RESIDUAL_LEAD1_HOLD3_SEVEN_BIN_PROSPECTIVE'


def valid_probs(values):
 if not isinstance(values,(list,tuple)) or len(values)!=7 or any(type(x) not in (int,float) or not math.isfinite(x) or not 0<=x<=1 for x in values) or abs(sum(values)-1)>1e-5:
  raise EvidenceError('SEVEN_CLASS_PROBABILITY_VECTOR')
 return values


def choose_rho(prior,graph,targets):
 if not (len(prior)==len(graph)==len(targets)>0):raise EvidenceError('DEVELOPMENT_ROLE_ALIGNMENT')
 scores=[]
 for rho in RHO_GRID:
  losses=[]
  for p,g,y in zip(prior,graph,targets):
   valid_probs(p);valid_probs(g)
   if type(y)is not int or not 0<=y<7:raise EvidenceError('SEVEN_CLASS_TARGET')
   q=[(1-rho)*a+rho*b for a,b in zip(p,g)];losses.append(sum((v-(k==y))**2 for k,v in enumerate(q)))
  scores.append({'rho':rho,'development_brier':sum(losses)/len(losses)})
 return min(scores,key=lambda x:(x['development_brier'],x['rho'])),scores


def validate_candidate(c):
 if c.get('schema')!='heptatrader.neuron256-residual-frozen.v1' or c.get('candidate_id')!=digest({k:v for k,v in c.items() if k!='candidate_id'}):raise EvidenceError('RESIDUAL_CANDIDATE_ID')
 if type(c.get('rho')) not in (int,float) or c.get('rho') not in RHO_GRID or c.get('edges_bps')!=list(EDGES):raise EvidenceError('RESIDUAL_RULE')
 if c.get('trade_authority') is not False or c.get('retraining_steps')!=0 or c.get('selection_data_already_seen') is not True:raise EvidenceError('RESIDUAL_SCOPE')
 for p,h in c['pinned_files'].items():
  if sha(p)!=h:raise EvidenceError('FROZEN_RESIDUAL_DEPENDENCY_CHANGED')
 valid_probs(c['prior']);return c


def validate_plan(p):
 if p.get('schema')!='heptatrader.neuron256-seven-bin-future-plan.v1' or p.get('plan_id')!=digest({k:v for k,v in p.items() if k!='plan_id'}):raise EvidenceError('SEVEN_BIN_PLAN_ID')
 if p.get('family')!=FAMILY or (type(p.get('trial'))is not int or p.get('trial')!=1) or p.get('alpha')!=.025 or p.get('baselines')!=list(BASELINES):raise EvidenceError('SEVEN_BIN_FAMILY_RULE')
 from .neuron256_cpu_custody import public_key,BACKEND
 try:public_key(p.get('cpu_public_key_hex'))
 except EvidenceError as exc:raise EvidenceError('FUTURE_CANONICAL_CPU_KEY_REQUIRED') from exc
 if (p.get('cpu_backend')!=BACKEND or type(p.get('cpu_emitter_sha256'))is not str
     or len(p['cpu_emitter_sha256'])!=64
     or any(c not in '0123456789abcdef' for c in p['cpu_emitter_sha256'])):
  raise EvidenceError('FUTURE_PINNED_CPU_EMITTER_REQUIRED')
 stamps=p['anchors']
 if len(stamps)!=18 or stamps!=sorted(set(stamps)) or any(type(t)is not int or t%300 for t in stamps):raise EvidenceError('FIXED_EIGHTEEN_FUTURE_ANCHORS')
 if p['registered_at_ms']>=stamps[0]*1000 or p['laya29_end_ms']>=stamps[0]*1000:raise EvidenceError('FUTURE_AND_AFTER_LAYA29_REQUIRED')
 if any(a+1500>=b-3600 for a,b in zip(stamps,stamps[1:])):raise EvidenceError('FULL_INPUT_OUTCOME_OVERLAP')
 if p.get('min_observed')!=16 or p.get('max_censored')!=2 or p.get('min_days')!=4 or p.get('min_mean_brier_gain')!=.001:raise EvidenceError('SEVEN_BIN_SCREEN_THRESHOLDS')
 if p.get('trade_authority') is not False or p.get('automatic_selection') is not False or p.get('replay_missed_windows') is not False:raise EvidenceError('NO_TRADE_NO_REPLAY')
 if 'research/historical_training/neuron256_cpu_custody.py' not in p.get('runtime_sha256',{}):
  raise EvidenceError('CPU_CUSTODY_RUNTIME_NOT_PINNED')
 return p


def original_input(parent_root,parent_plan,anchor,now_ms):
 from .dataset import checked_bar
 from .bar_forward_smoke import bar_input,WindowGuard
 if anchor not in parent_plan['anchors']:raise EvidenceError('FOREIGN_RAW_SOURCE_ANCHOR')
 root=Path(parent_root)/'slots'/str(anchor)/'proof';child=read(root/'plan.json')
 if child.get('anchor_seconds')!=anchor or child.get('candidate_id')!=parent_plan['candidate_id'] or child.get('candidate_sha256')!=parent_plan['candidate_sha256']:
  raise EvidenceError('PARENT_RAW_CUSTODY_MISMATCH')
 if not parent_plan['registered_at_ms']<=child['registered_at_ms']<anchor*1000:raise EvidenceError('PARENT_RAW_TIME')
 series={};pins={}
 for rid,kind in [(9101,'MIDPOINT'),(9102,'BID'),(9103,'ASK')]:
  path=root/(str(rid)+'.json');r=read(path)
  if r['request_id']!=rid or not r.get('end') or r.get('errors') or not anchor*1000<=r['received_at_ms']<=min(now_ms,(anchor+240)*1000):raise EvidenceError('FUTURE_INPUT_RAW_TIMING_OR_COMPLETENESS')
  fields=r['fields']
  if fields!=WindowGuard(anchor).expected(rid):raise EvidenceError('RAW_INPUT_WIRE_SCOPE')
  bars={}
  for item in r['rows']:
   t,bar=checked_bar(item)
   if t+300>anchor:continue
   if t in bars:raise EvidenceError('DUPLICATE_INPUT_BAR')
   bars[t]=bar
  series[kind]=bars;pins[str(path)]=sha(path)
 row=bar_input(series,anchor)
 if row!=read(root/'input.json'):raise EvidenceError('RECONSTRUCTED_INPUT_DIFFERS')
 pins[str(root/'input.json')]=sha(root/'input.json');pins[str(root/'plan.json')]=sha(root/'plan.json')
 return row,pins


def outcome(parent_root,anchor,prediction):
 from .dataset import checked_bar
 root=Path(parent_root)/'slots'/str(anchor)/'proof';path=root/'9104.json';raw=read(path)
 if raw.get('request_id')!=9104 or raw.get('errors') or not raw.get('end') or raw['received_at_ms']<(anchor+1500)*1000:raise EvidenceError('RAW_OUTCOME_INCOMPLETE_OR_EARLY')
 from .bar_forward_smoke import WindowGuard
 if raw['fields']!=WindowGuard(anchor).expected(9104):raise EvidenceError('RAW_OUTCOME_SCOPE')
 bars={}
 for item in raw['rows']:
  t,value=checked_bar(item)
  if t in bars:raise EvidenceError('DUPLICATE_OUTCOME_BAR')
  bars[t]=value
 if any(t not in bars for t in range(anchor+300,anchor+1500,300)):raise EvidenceError('RAW_OUTCOME_PATH_MISSING')
 change=(float(bars[anchor+1200]['open'])/float(bars[anchor+300]['open'])-1)*10000;target=bisect_right(EDGES,change)
 scores={k:sum((v-(i==target))**2 for i,v in enumerate(valid_probs(prediction['probabilities'][k]))) for k in ('candidate',)+BASELINES}
 return {'target_class':target,'return_bps':change,'brier':scores,'raw_outcome_sha256':sha(path),'raw_received_at_ms':raw['received_at_ms']}


def assess(plan,items,now_ms):
 validate_plan(plan)
 if len(items)!=len({x['anchor'] for x in items}) or any(x['anchor'] not in plan['anchors'] for x in items):raise EvidenceError('DUPLICATE_OR_FOREIGN_PROSPECTIVE_SUBJECT')
 complete=now_ms>=plan['anchors'][-1]*1000+1560000 and len(items)==len(plan['anchors']) and all(x['status'] in ('OBSERVED','CENSORED') for x in items)
 observed=[x for x in items if x['status']=='OBSERVED'];censored=sum(x['status']=='CENSORED' for x in items);reasons=[]
 if not complete:reasons.append('FIXED_FUTURE_WINDOW_INCOMPLETE')
 if len(observed)<plan['min_observed']:reasons.append('INSUFFICIENT_OBSERVATIONS')
 if censored>plan['max_censored']:reasons.append('TOO_MANY_CENSORED')
 days={datetime.fromtimestamp(x['anchor'],timezone.utc).date().isoformat() for x in observed}
 if len(days)<plan['min_days']:reasons.append('INSUFFICIENT_DAYS')
 if len({x['target_class'] for x in observed})<2:reasons.append('INSUFFICIENT_TARGET_VARIATION')
 comparisons={}
 for name in BASELINES:
  diffs=[x['brier'][name]-x['brier']['candidate'] for x in observed]
  if any(not math.isfinite(x) for x in diffs):raise EvidenceError('NONFINITE_SCORES')
  vals=[x for x in diffs if abs(x)>1e-12];n=len(vals);wins=sum(x>0 for x in vals);p=sum(math.comb(n,k) for k in range(wins,n+1))/2**n if n else 1.
  mean=sum(diffs)/len(diffs) if diffs else None
  comparisons[name]={'mean_brier_gain':mean,'wins':wins,'non_ties':n,'descriptive_sign_p':p,'alpha':plan['alpha']/len(BASELINES)}
  if mean is None or mean<=plan['min_mean_brier_gain']:reasons.append('NO_MATERIAL_GAIN:'+name)
  if p>plan['alpha']/len(BASELINES):reasons.append('CONDITIONAL_SIGN_EVIDENCE_INSUFFICIENT:'+name)
 return {'plan_id':plan['plan_id'],'state':'PENDING' if not complete else ('REJECTED' if reasons else 'PRICE_SCREEN_PASSED_RESEARCH_ONLY'),
  'observed':len(observed),'censored':censored,'planned':len(plan['anchors']),'days':len(days),'comparisons':comparisons,'reasons':reasons,
  'conditional_sign_null':'P_NON_TIE_WIN_CONDITIONAL_ON_PRIOR_HISTORY_AT_MOST_0_5','independence_proven':False,'small_sample_power_limited':True,
  'global_familywise_claim_across_other_research':False,'economic_qualification':False,'automatic_selection':False,'trade_authority':False}


def validate_runtime(c):
 p=read(Path(c['root'])/'plan.json');validate_plan(p)
 from laya_cell.financial_forward_witness import verify_record
 seal=read(Path(c['root'])/'registration.json');verify_record(c,'registrar',seal)
 if seal['plan_id']!=p['plan_id'] or seal['plan_sha256']!=sha(Path(c['root'])/'plan.json') or seal['role_witness']['signed_at_ms']>=p['anchors'][0]*1000:raise EvidenceError('FROZEN_STUDY_REGISTRATION')
 candidate=validate_candidate(read(Path(c['root'])/'candidate.json'))
 if sha(Path(c['root'])/'candidate.json')!=p['candidate_sha256'] or candidate['candidate_id']!=p['candidate_id']:raise EvidenceError('STUDY_CANDIDATE_SUBSTITUTION')
 if sha(Path(c['parent_root'])/'plan.json')!=p['parent_plan_sha256']:raise EvidenceError('ORIGINAL_RAW_SOURCE_PLAN_CHANGED')
 if sha(c['period_path'])!=c['period_sha256']:raise EvidenceError('STUDY_ACCOUNTING_PERIOD_CHANGED')
 cpu_root=Path(c.get('canonical_cpu_root',''))
 if not cpu_root.is_absolute() or not cpu_root.resolve().is_relative_to(root.resolve()):
  raise EvidenceError('CANONICAL_CPU_LOCAL_INBOX_SCOPE')
 for relative,want in p['runtime_sha256'].items():
  name=Path(relative)
  if name.is_absolute() or '..' in name.parts or sha(Path(c['source_root'])/name)!=want:raise EvidenceError('SEVEN_BIN_RUNTIME_CHANGED')
 return p,candidate


def predict_one(c,plan,candidate,anchor):
 from historical_training.resident_resource_guard import Gate,verify_config
 from laya_cell.financial_forward_witness import sign_record
 from .neuron256_cpu_custody import verify_feature
 now=int(time.time()*1000);folder=Path(c['root'])/'slots'/str(anchor)
 if not anchor*1000<=now<(anchor+240)*1000:raise EvidenceError('FIXED_INPUT_DEADLINE')
 if (folder/'prediction.json').exists():return
 parent=read(Path(c['parent_root'])/'plan.json');row,pins=original_input(c['parent_root'],parent,anchor,now)
 cpu_dir=Path(c['canonical_cpu_root'])/str(anchor)
 if not (cpu_dir/'raw.safetensors').exists() or not (cpu_dir/'receipt.json').exists():
  # No feature and no claim: do not load a different local CPU backend.
  return
 original_input_file=Path(c['parent_root'])/'slots'/str(anchor)/'proof'/'input.json'
 raw,receipt=verify_feature(cpu_dir,plan,candidate,row,anchor,now,source_input_file=original_input_file)
 cpu_pins={'cpu_raw_sha256':sha(cpu_dir/'raw.safetensors'),
           'cpu_receipt_sha256':sha(cpu_dir/'receipt.json')}
 guard=read(c['guard_config']);verify_config(guard);gate=Gate(guard,'evaluator')
 if not gate.acquire({'study':plan['plan_id'],'anchor':anchor}):return
 try:
  write_once(folder/'claim.json',{'plan_id':plan['plan_id'],'anchor':anchor,'claimed_at_ms':int(time.time()*1000),'replay_allowed':False})
  from sandbox import enter
  proof=enter({'output_dir':str(folder),'period_path':c['period_path'],'legacy_capture':str(Path(c['root'])/'candidate.json'),
   'oms_path':str(Path(c['root'])/'plan.json'),'export_dir':c['parent_root'],
   'read_only_paths':[c['source_root'],c['root'],c['base_directory'],c['role_key_path'],
       c['witness_trust_path'],str(Path(candidate['checkpoint_path']).parent),__import__('sys').prefix]})
  write_once(folder/'sandbox.json',proof)
  from .neuron256_frozen_readout import from_vectors
  raw,verified=verify_feature(cpu_dir,plan,candidate,row,anchor,int(time.time()*1000),source_input_file=original_input_file)
  if cpu_pins!={'cpu_raw_sha256':sha(cpu_dir/'raw.safetensors'),
                 'cpu_receipt_sha256':sha(cpu_dir/'receipt.json')}:
   raise EvidenceError('CANONICAL_CPU_CHANGED_AFTER_ADMISSION')
  import torch
  if not torch.version.hip or not str(torch.cuda.get_device_properties(0).gcnArchName).startswith('gfx1150'):raise EvidenceError('ORIGINAL_GFX1150_ROCM_REQUIRED')
  torch.cuda.set_per_process_memory_fraction(min(1.,1024**3/torch.cuda.get_device_properties(0).total_memory))
  predictions=from_vectors(candidate['checkpoint_path'],[row],raw,candidate['prior'],candidate['rho'],device='cuda')
  now=int(time.time()*1000)
  if now>=(anchor+300)*1000:raise EvidenceError('SEVEN_BIN_PREDICTION_LATE')
  record={'plan_id':plan['plan_id'],'candidate_id':candidate['candidate_id'],'anchor':anchor,'input':row,'input_sha256':digest(row),'raw_input_pins':pins,
   'encoder_text_sha256':receipt['source_text_sha256'],
   'raw_vector_sha256':hashlib.sha256(raw.tobytes()).hexdigest(),
   'canonical_cpu_backend':receipt['torch_version'],
   'cpu_feature_pins':cpu_pins,
   'probabilities':{k:v[0] for k,v in predictions.items()},'computed_at_ms':now,'encoder_cpu_readout_original_rocm':True,'new_training_steps':0,'new_broker_queries':0,'trade_authority':False}
  write_once(folder/'prediction.json',record)
  commit=sign_record(c,'predictor',{'plan_id':plan['plan_id'],'anchor':anchor,'prediction_sha256':sha(folder/'prediction.json'),'committed_at_ms':int(time.time()*1000)})
  write_once(folder/'commit.json',commit)
 finally:
  gc.collect()
  if 'torch' in locals():torch.cuda.synchronize();torch.cuda.empty_cache()
  gate.release()


def verify_prediction(c,plan,anchor):
 from laya_cell.financial_forward_witness import verify_record
 folder=Path(c['root'])/'slots'/str(anchor);commit=read(folder/'commit.json');prediction=read(folder/'prediction.json');verify_record(c,'predictor',commit)
 if commit['plan_id']!=plan['plan_id'] or commit['anchor']!=anchor or commit['prediction_sha256']!=sha(folder/'prediction.json'):raise EvidenceError('SEVEN_BIN_FORECAST_SIGNATURE')
 if not anchor*1000<=prediction['computed_at_ms']<=commit['role_witness']['signed_at_ms']<(anchor+300)*1000:raise EvidenceError('LATE_SEVEN_BIN_FORECAST')
 if (prediction.get('anchor')!=anchor or prediction.get('plan_id')!=plan['plan_id'] or prediction['candidate_id']!=plan['candidate_id']
     or prediction['input_sha256']!=digest(prediction['input']) or set(prediction['probabilities'])!=set(('candidate',)+BASELINES)):
  raise EvidenceError('FROZEN_SUBJECT_CHANGED')
 from .neuron256_cpu_custody import verify_feature
 cpu_dir=Path(c['canonical_cpu_root'])/str(anchor)
 parent_file=Path(c['parent_root'])/'slots'/str(anchor)/'proof'/'input.json'
 raw,receipt=verify_feature(cpu_dir,plan,read(Path(c['root'])/'candidate.json'),
            prediction['input'],anchor,int(time.time()*1000),source_input_file=parent_file)
 if (prediction.get('canonical_cpu_backend')!=receipt['torch_version']
     or prediction.get('cpu_feature_pins')!={'cpu_raw_sha256':sha(cpu_dir/'raw.safetensors'),
                                          'cpu_receipt_sha256':sha(cpu_dir/'receipt.json')}
     or prediction.get('raw_vector_sha256')!=hashlib.sha256(raw.tobytes()).hexdigest()):
  raise EvidenceError('FROZEN_CANONICAL_CPU_PROOF_CHANGED')
 for path,want in prediction['raw_input_pins'].items():
  if sha(path)!=want:raise EvidenceError('ORIGINAL_INPUT_BYTES_CHANGED')
 return prediction


def evaluate(c,plan):
 from laya_cell.financial_forward_witness import verify_record,sign_record
 root=Path(c['root']);now=int(time.time()*1000);items=[]
 for anchor in plan['anchors']:
  folder=root/'slots'/str(anchor);final=folder/'outcome.json'
  if final.exists():
   record=read(final);verify_record(c,'evaluator',record)
   if record.get('plan_id')!=plan['plan_id'] or record.get('candidate_id')!=plan['candidate_id'] or record.get('anchor')!=anchor:raise EvidenceError('CLOSED_SUBJECT_CHANGED')
   if record['status']=='OBSERVED':
    reconstructed=outcome(c['parent_root'],anchor,verify_prediction(c,plan,anchor))
    if any(record.get(k)!=v for k,v in reconstructed.items()):raise EvidenceError('CLOSED_SCORE_OR_SOURCE_CHANGED')
   elif record['status']!='CENSORED':raise EvidenceError('UNKNOWN_CLOSED_STATUS')
   items.append({k:v for k,v in record.items() if k not in ('signature','role_witness')});continue
  if now<(anchor+1560)*1000:continue
  record={'plan_id':plan['plan_id'],'candidate_id':plan['candidate_id'],'anchor':anchor,'status':'CENSORED','reason':None,'at_ms':now}
  try:
   prediction=verify_prediction(c,plan,anchor)
   proof=outcome(c['parent_root'],anchor,prediction);record.update(proof,status='OBSERVED')
  except Exception as exc:record['reason']=str(exc)[:220]
  signed=sign_record(c,'evaluator',record);write_once(final,signed);items.append(record)
 summary=assess(plan,items,now);summary['at_ms']=now;summary['new_broker_queries']=0
 atomic(root/'status.json',summary)
 if summary['state']!='PENDING' and not (root/'complete.json').exists():write_once(root/'complete.json',sign_record(c,'evaluator',summary))
 return summary


def main():
 parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True);parser.add_argument('--mode',choices=['predict','evaluate'],required=True);a=parser.parse_args();os.umask(0o077)
 c=read(a.config);plan,candidate=validate_runtime(c);root=Path(c['root'])
 import fcntl
 fd=os.open(root/(a.mode+'.lock'),os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
 try:
  if a.mode=='evaluate':print(json.dumps(evaluate(c,plan)),flush=True)
  else:
   now=int(time.time()*1000)
   for anchor in plan['anchors']:
    folder=root/'slots'/str(anchor)
    if anchor*1000<=now<(anchor+240)*1000 and not (folder/'claim.json').exists():predict_one(c,plan,candidate,anchor)
 except Exception as exc:
  atomic(root/(a.mode+'-error.json'),{'at_ms':int(time.time()*1000),'state':'UNAVAILABLE_NO_QUALIFICATION','reason':str(exc)[:220],'trade_authority':False})
  raise
 finally:os.close(fd)
if __name__=='__main__':main()
