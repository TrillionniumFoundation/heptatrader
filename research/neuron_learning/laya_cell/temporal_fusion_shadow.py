"""Prospective A/B for frozen Laya, numeric temporal neuron, and fusion.

Prediction and outcome paths share no optimizer.  This service has no trading
authority and never invokes the remote model or broker.
"""
from __future__ import annotations
import argparse,fcntl,json,math,os,signal,time
from bisect import bisect_left,bisect_right
from math import comb
from pathlib import Path
import torch
from .integrity import ContractError,read_json,digest,sha_file
from .resident_store import write_once,atomic
from .resident_data import read_quotes,forecast_input
from .dataset import encode_rows,BIN_EDGES_BPS
from .model import load_verified_laya
from .resident_model import ResidentDecisionCell
from .temporal_fusion_model import PROFILE,TemporalLayaFusion,padded_sequences,output_contract

RULE={"schema":"heptatrader.temporal-fusion-shadow-rule.v1","slots":12,"slot_spacing_ms":360000,
 "horizon_ms":300000,"input_grace_ms":60000,"minimum_observed":10,"maximum_censored":2,
 "minimum_target_classes":2,"minimum_mean_brier_improvement":1e-5,
 "one_sided_sign_alpha_each_baseline":0.025,"automatic_selection":False,"trade_permission":False}

def validate_plan(plan):
 if plan.get("rule")!=RULE or plan.get("plan_id")!=digest({k:v for k,v in plan.items() if k!="plan_id"}):raise ContractError("FUSION_PLAN_CHANGED")
 if not plan["registered_at_ms"]<plan["start_ms"]<plan["end_ms"]:raise ContractError("FUSION_PLAN_CLOCK")
 return plan

class Predictor:
 def __init__(self,c):
  self.c=c;self.root=Path(c["shadow_root"]);self.plan=validate_plan(read_json(self.root/"plan.json"));self.stop=False
  candidate_dir=Path(c["fusion_candidate_directory"]);self.candidate=read_json(candidate_dir/"candidate.json")
  if self.candidate["candidate_id"]!=self.plan["candidate_id"] or sha_file(candidate_dir/"fusion.safetensors")!=self.candidate["fusion_sha256"]:raise ContractError("FUSION_CANDIDATE_CHANGED")
  laya_dir=Path(c["laya_candidate_directory"]);self.laya=read_json(laya_dir/"candidate.json")
  if self.laya["candidate_id"]!=self.plan["laya_candidate_id"] or sha_file(laya_dir/"adapter.safetensors")!=self.laya["adapter_sha256"]:raise ContractError("FUSION_LAYA_CHANGED")
  from safetensors.torch import load_file
  raw,tok,base_id=load_verified_laya(Path(c["base_directory"]),read_json(Path(c["base_directory"])/"base-manifest.json"))
  if base_id!=self.candidate["laya_base_id"]:raise ContractError("FUSION_BASE_CHANGED")
  self.tok=tok;self.cell=ResidentDecisionCell(raw,base_id);self.cell.restore_trainable(load_file(str(laya_dir/"adapter.safetensors")));self.cell.eval()
  self.model=TemporalLayaFusion().to("cuda");self.model.load_state_dict(load_file(str(candidate_dir/"fusion.safetensors")),strict=True);self.model.eval()
 def one(self,slot,now):
  start=self.plan["start_ms"]+slot*RULE["slot_spacing_ms"];late=start+RULE["input_grace_ms"]
  path=self.root/f"prediction-{slot:03}.json"
  if path.exists() or now<start:return False
  if now>late:
   write_once(self.root/f"failure-{slot:03}.json",{"slot":slot,"reason":"NO_FRESH_INPUT_IN_FIXED_SLOT","at_ms":now});return False
  quotes,prefixes=read_quotes(self.c,now)
  try:sample,row=forecast_input(self.c,quotes,now)
  except ContractError:return False
  if sample["anchor_ms"]<start:return False
  if self.candidate["created_at_ms"]>=sample["anchor_ms"] or self.candidate["trained_until_ms"]>sample["anchor_ms"]:raise ContractError("FUSION_CANDIDATE_LOOKAHEAD")
  if self.laya["trained_until_ms"]>sample["anchor_ms"]:raise ContractError("FUSION_LAYA_LOOKAHEAD")
  batch={k:v.to("cuda") for k,v in encode_rows(self.tok,[row]).items()}
  seq,length=padded_sequences([sample],torch.device("cuda"))
  with torch.no_grad():
   logits,_=self.cell(**batch);lp=logits.softmax(-1);out=self.model(seq,length,lp)
  nodes=output_contract(out,0);nodes["laya"]["probs"]=[float(x) for x in lp[0].cpu()]
  evidence={"sample_id":sample["sample_id"],"input_id":digest(row["state"]),"source_prefixes":prefixes,
   "laya_candidate_id":self.laya["candidate_id"],"fusion_candidate_id":self.candidate["candidate_id"],
   "numeric_profile_id":self.candidate["profile_id"],"effect_authority":False}
  for node in nodes.values():node["evidence"]=evidence
  made=int(time.time()*1000)
  if made>=sample["anchor_ms"]+RULE["horizon_ms"]:raise ContractError("FUSION_PREDICTION_AFTER_OUTCOME")
  result={"schema":"heptatrader.temporal-laya-fusion-prediction.v1","plan_id":self.plan["plan_id"],"candidate_id":self.candidate["candidate_id"],
   "slot":slot,"created_at_ms":made,"anchor_ms":sample["anchor_ms"],"trained_until_ms":self.candidate["trained_until_ms"],
   "input":row["state"],"input_id":digest(row["state"]),"sample_id":sample["sample_id"],"reference_mid":sample["reference_mid"],
   "node_outputs":nodes,"horizon_sec":300,"broker_orders_sent":0,"automatic_trading":False}
  write_once(path,result);return True
 def step(self):
  now=int(time.time()*1000);new=0
  if sha_file(Path(self.c["period_path"]))!=self.c["period_sha256"]:raise ContractError("FUSION_PERIOD_CHANGED")
  for slot in range(RULE["slots"]):new+=int(self.one(slot,now))
  status={"at_ms":now,"new_predictions":new,"sealed_predictions":len(list(self.root.glob("prediction-*.json"))),
   "failures":len(list(self.root.glob("failure-*.json"))),"gpu_arch":str(torch.cuda.get_device_properties(0).gcnArchName),
   "broker_orders_sent":0,"actor_updated":False}
  atomic(self.root/"predictor-status.json",status);return status

def prob_score(probs,target):
 if len(probs)!=7 or any(type(x) not in (int,float) or not math.isfinite(x) or not 0<=x<=1 for x in probs) or abs(sum(probs)-1)>1e-5:raise ContractError("FUSION_PROB_VECTOR")
 return sum((p-(i==target))**2 for i,p in enumerate(probs))
def sign_p(wins,n):
 if n<=0:return 1.
 return sum(comb(n,k) for k in range(wins,n+1))/(2**n)
def assess(plan,results,now):
 obs=[r for r in results if r["status"]=="OBSERVED"];censored=[r for r in results if r["status"]=="CENSORED"]
 reasons=[];targets={r["target_class"] for r in obs};comparisons={}
 for baseline,key in (("laya","laya_brier"),("numeric","numeric_brier")):
  diffs=[r[key]-r["fusion_brier"] for r in obs];wins=sum(d>0 for d in diffs);ties=sum(d==0 for d in diffs);n=len(diffs)-ties
  gain=sum(diffs)/len(diffs) if diffs else None;p=sign_p(wins,n)
  comparisons[baseline]={"mean_brier_improvement":gain,"wins":wins,"losses":n-wins,"ties":ties,"one_sided_sign_p":p}
  if gain is None or gain<=RULE["minimum_mean_brier_improvement"]:reasons.append("NO_MEAN_BRIER_GAIN:"+baseline)
  if p>RULE["one_sided_sign_alpha_each_baseline"]:reasons.append("SIGN_EVIDENCE_INSUFFICIENT:"+baseline)
 if len(obs)<RULE["minimum_observed"]:reasons.append("INSUFFICIENT_OBSERVED")
 if len(censored)>RULE["maximum_censored"]:reasons.append("TOO_MANY_CENSORED")
 if len(targets)<RULE["minimum_target_classes"]:reasons.append("INSUFFICIENT_TARGET_CLASSES")
 complete=len(results)>=RULE["slots"] or now>=plan["end_ms"]
 if not complete:reasons.append("FIXED_WINDOW_NOT_COMPLETE")
 return {"schema":"heptatrader.temporal-laya-fusion-assessment.v1","at_ms":now,"state":("EXPERIMENTAL_FUSION_WIN_SHADOW_ONLY" if not reasons else ("REJECTED" if complete else "PENDING")),
  "observed":len(obs),"censored":len(censored),"target_classes":sorted(targets),"comparisons":comparisons,"reasons":reasons,
  "automatic_selection":False,"trading_authority":False,"broker_orders_sent":0}

class Evaluator:
 def __init__(self,c):self.c=c;self.root=Path(c["shadow_root"]);self.plan=validate_plan(read_json(self.root/"plan.json"));self.stop=False
 def step(self):
  now=int(time.time()*1000);quotes,_=read_quotes(self.c,now);times=[q["t"] for q in quotes]
  for slot in range(RULE["slots"]):
   out=self.root/f"outcome-{slot:03}.json";pred_path=self.root/f"prediction-{slot:03}.json";fail=self.root/f"failure-{slot:03}.json"
   if out.exists():continue
   if fail.exists():write_once(out,{"status":"CENSORED","slot":slot,"reason":read_json(fail)["reason"],"evaluated_at_ms":now});continue
   if not pred_path.exists():
    if now>self.plan["start_ms"]+slot*RULE["slot_spacing_ms"]+RULE["input_grace_ms"]:write_once(out,{"status":"CENSORED","slot":slot,"reason":"PREDICTION_MISSING","evaluated_at_ms":now})
    continue
   p=read_json(pred_path);anchor=p["anchor_ms"];due=anchor+RULE["horizon_ms"]
   if now<=due+5000:continue
   a=bisect_left(times,anchor);b=bisect_left(times,due);reason=None
   if p["input_id"]!=digest(p["input"]):reason="INPUT_HASH_CHANGED"
   elif p["created_at_ms"]>=due:reason="LOOKAHEAD"
   elif a>=len(times) or times[a]!=anchor or abs(quotes[a]["mid"]-p["reference_mid"])>1e-12:reason="ANCHOR_CHANGED"
   elif b>=len(times):reason="OUTCOME_MISSING"
   elif times[b]>due+5000:reason="ENDPOINT_GAP"
   elif any(y-x>30000 for x,y in zip(times[a:b],times[a+1:b+1])):reason="PATH_GAP"
   if reason:write_once(out,{"status":"CENSORED","slot":slot,"reason":reason,"evaluated_at_ms":now});continue
   move=(quotes[b]["mid"]/quotes[a]["mid"]-1)*10000;target=bisect_right(BIN_EDGES_BPS,move)
   nodes=p["node_outputs"];result={"status":"OBSERVED","slot":slot,"reason":None,"anchor_ms":anchor,"outcome_ms":times[b],
    "return_bps":move,"target_class":target,"fusion_brier":prob_score(nodes["fusion"]["probs"],target),
    "laya_brier":prob_score(nodes["laya"]["probs"],target),"numeric_brier":prob_score(nodes["numeric"]["probs"],target),
    "gate_numeric_weight":nodes["fusion"]["gate_numeric_weight"],"fusion_uncertainty":nodes["fusion"]["uncertainty"],
    "outcome_receipt":quotes[b]["receipt"],"broker_orders_sent":0}
   write_once(out,result)
  results=[read_json(p) for p in sorted(self.root.glob("outcome-*.json"))];assessment=assess(self.plan,results,now)
  atomic(self.root/"assessment.json",assessment);return assessment

def main():
 p=argparse.ArgumentParser();p.add_argument("--config",type=Path,required=True);p.add_argument("--mode",choices=("predictor","evaluator"),required=True);p.add_argument("--once",action="store_true");a=p.parse_args();os.umask(0o077)
 c=read_json(a.config);service=Predictor(c) if a.mode=="predictor" else Evaluator(c);root=Path(c["shadow_root"])
 lock=os.open(root/(a.mode+".lock"),os.O_WRONLY|os.O_CREAT|os.O_CLOEXEC,0o600);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 def stop(*_):service.stop=True
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 while not service.stop:
  try:service.step()
  except Exception as exc:atomic(root/(a.mode+"-status.json"),{"at_ms":int(time.time()*1000),"state":"UNAVAILABLE_NO_TRADE_EFFECT","reason":str(exc)[:240]})
  if a.once:break
  time.sleep(1 if a.mode=="predictor" else 10)
 os.close(lock)
if __name__=="__main__":main()
