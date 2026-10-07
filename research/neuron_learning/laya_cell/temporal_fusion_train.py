"""One bounded GPU fit for numeric temporal + frozen-Laya gated fusion.

The chronological internal tail is diagnostic only.  Real acceptance is a new
prospective window after this candidate is frozen.
"""
from __future__ import annotations
import argparse,hashlib,json,os,time
from pathlib import Path
import torch
from torch.nn import functional as F
from .integrity import ContractError,read_json,digest,sha_file,canonical
from .model import load_verified_laya,require_pocket4_gpu,tensor_hash
from .resident_model import ResidentDecisionCell
from .resident_data import collect
from .dataset import encode_rows
from .temporal_fusion_model import PROFILE,TemporalLayaFusion,padded_sequences,training_loss,brier

def immutable(path,value):
 with path.open("xb") as f:f.write(canonical(value)+b"\n");f.flush();os.fsync(f.fileno())
 path.chmod(0o400)

def load_laya_candidate(path):
 c=read_json(path/"candidate.json")
 if c.get("candidate_id")!=digest({k:v for k,v in c.items() if k!="candidate_id"}):raise ContractError("FUSION_LAYA_CANDIDATE_HASH")
 if sha_file(path/"adapter.safetensors")!=c.get("adapter_sha256"):raise ContractError("FUSION_LAYA_ADAPTER_HASH")
 return c

def laya_probs(cell,tok,rows,device):
 result=[]
 cell.eval()
 with torch.no_grad():
  for i in range(0,len(rows),16):
   batch={k:v.to(device) for k,v in encode_rows(tok,rows[i:i+16]).items()}
   logits,_=cell(**batch);result.append(logits.softmax(-1).detach().cpu())
 return torch.cat(result,0)

def diagnostics(model,x,lengths,laya,target):
 model.eval()
 with torch.no_grad():
  out=model(x,lengths,laya);one=F.one_hot(target,7).float()
  def stats(p):
   return {"brier":float((p-one).square().sum(-1).mean()),
           "ce":float(F.nll_loss(p.clamp_min(1e-8).log(),target)),
           "hits":int((p.argmax(-1)==target).sum())}
  return {"laya":stats(laya),"numeric":stats(out["numeric_probs"]),"fusion":stats(out["fusion_probs"]),
          "gate_mean":float(out["gate_numeric_weight"].mean()),
          "gate_min":float(out["gate_numeric_weight"].min()),"gate_max":float(out["gate_numeric_weight"].max())}

def run(config):
 started=time.monotonic();torch.manual_seed(20261008);torch.set_num_threads(2);device=require_pocket4_gpu()
 if config.get("profile")!=PROFILE:raise ContractError("TEMPORAL_FUSION_PROFILE_CHANGED")
 laya_config=read_json(Path(config["laya_config"]));now=int(time.time()*1000)
 if sha_file(Path(laya_config["period_path"]))!=laya_config["period_sha256"]:raise ContractError("FUSION_PERIOD_CHANGED")
 candidate_dir=Path(config["laya_candidate_directory"]);laya_candidate=load_laya_candidate(candidate_dir)
 dataset,rows,stats,_=collect(laya_config,now);rows=[r for r in rows if r["horizon_sec"]==300]
 rows=rows[-(PROFILE["max_training_rows"]+PROFILE["internal_holdout_rows"]):]
 if len(rows)<PROFILE["max_training_rows"]+PROFILE["internal_holdout_rows"]:raise ContractError("FUSION_INSUFFICIENT_MATURE_ROWS")
 if any(r["anchor_ms"]<=laya_candidate["trained_until_ms"] for r in rows):raise ContractError("LAYA_NOT_FROZEN_BEFORE_FUSION_ROWS")
 train_rows=rows[:PROFILE["max_training_rows"]];hold_rows=rows[PROFILE["max_training_rows"]:]
 samples={s["sample_id"]:s for s in dataset["samples"]}
 if any(r["sample_id"] not in samples for r in rows):raise ContractError("FUSION_SAMPLE_MISSING")
 raw,tok,base_id=load_verified_laya(Path(laya_config["base_directory"]),read_json(Path(laya_config["base_directory"])/"base-manifest.json"))
 if base_id!=laya_candidate["base_id"]:raise ContractError("FUSION_LAYA_BASE_CHANGED")
 from safetensors.torch import load_file,save_file
 cell=ResidentDecisionCell(raw,base_id);cell.restore_trainable(load_file(str(candidate_dir/"adapter.safetensors")));cell.eval();cell.assert_frozen()
 all_laya=laya_probs(cell,tok,rows,device)
 del cell,raw;torch.cuda.empty_cache()
 all_x,all_lengths=padded_sequences([samples[r["sample_id"]] for r in rows])
 target=torch.tensor([r["target_class"] for r in rows],dtype=torch.long)
 n=PROFILE["max_training_rows"];train_x=all_x[:n].to(device);train_len=all_lengths[:n].to(device);train_laya=all_laya[:n].to(device);train_y=target[:n].to(device)
 hold_x=all_x[n:].to(device);hold_len=all_lengths[n:].to(device);hold_laya=all_laya[n:].to(device);hold_y=target[n:].to(device)
 model=TemporalLayaFusion().to(device);optimizer=torch.optim.AdamW(model.parameters(),lr=PROFILE["learning_rate"],weight_decay=PROFILE["weight_decay"])
 before=diagnostics(model,hold_x,hold_len,hold_laya,hold_y);steps=[]
 for epoch in range(PROFILE["epochs"]):
  model.train();gen=torch.Generator(device="cpu").manual_seed(20261008+epoch);order=torch.randperm(n,generator=gen)
  loss_sum=0.
  for offset in range(0,n,PROFILE["batch_size"]):
   idx=order[offset:offset+PROFILE["batch_size"]].to(device);optimizer.zero_grad(set_to_none=True)
   out=model(train_x[idx],train_len[idx],train_laya[idx]);loss,parts=training_loss(out,train_y[idx]);loss.backward()
   grad=float(torch.nn.utils.clip_grad_norm_(model.parameters(),PROFILE["gradient_norm_cap"],error_if_nonfinite=True))
   optimizer.step();loss_sum+=float(loss.detach())*len(idx)
  if epoch in (0,7,15,31,47):steps.append({"epoch":epoch+1,"mean_loss":loss_sum/n,"gradient_norm_last":grad})
 after=diagnostics(model,hold_x,hold_len,hold_laya,hold_y)
 if any(not torch.isfinite(p).all() for p in model.parameters()):raise ContractError("FUSION_NONFINITE_PARAMETER")
 outdir=Path(config["output_directory"]);outdir.mkdir(mode=0o700,parents=True,exist_ok=False)
 tensors={k:v.detach().cpu().contiguous() for k,v in model.state_dict().items()};save_file(tensors,str(outdir/"fusion.safetensors"));(outdir/"fusion.safetensors").chmod(0o400)
 train_ids=[digest([r["sample_id"],r["horizon_sec"],r["outcome_receipt_digest"]]) for r in train_rows]
 hold_ids=[digest([r["sample_id"],r["horizon_sec"],r["outcome_receipt_digest"]]) for r in hold_rows]
 record={"schema":"heptatrader.temporal-laya-fusion-candidate.v1","created_at_ms":int(time.time()*1000),
  "trained_until_ms":max(r["anchor_ms"] for r in train_rows)+300000,"profile":PROFILE,"profile_id":digest(PROFILE),
  "period_sha256":laya_config["period_sha256"],"laya_candidate_id":laya_candidate["candidate_id"],
  "laya_candidate_created_at_ms":laya_candidate["created_at_ms"],"laya_trained_until_ms":laya_candidate["trained_until_ms"],
  "laya_adapter_sha256":laya_candidate["adapter_sha256"],"laya_base_id":base_id,
  "fusion_sha256":sha_file(outdir/"fusion.safetensors"),"train_rows":len(train_rows),"holdout_rows":len(hold_rows),
  "train_row_ids":train_ids,"internal_holdout_row_ids":hold_ids,"internal_before":before,"internal_after":after,
  "internal_holdout_is_not_independent_qualification":True,"steps":steps,
  "trainable_parameters":sum(p.numel() for p in model.parameters()),"gpu_arch":str(torch.cuda.get_device_properties(0).gcnArchName),
  "torch_version":torch.__version__,"rocm":torch.version.hip,"wall_seconds":time.monotonic()-started,
  "max_gpu_allocated_bytes":torch.cuda.max_memory_allocated(),"source_quote_fingerprint":stats["fingerprint"],
  "node_contract":{"embedding_dim":32,"uncertainty":"NORMALIZED_CATEGORICAL_ENTROPY","evidence":"HASHED_CAUSAL_INPUT_AND_FROZEN_MODEL_IDS"},
  "status":"REQUIRES_NEW_PROSPECTIVE_FUSION_EVALUATION","automatic_trading":False,"actor_updated":False,"broker_orders_sent":0}
 record["candidate_id"]=digest(record);immutable(outdir/"candidate.json",record)
 print(json.dumps(record,indent=2));return record

def main():
 p=argparse.ArgumentParser();p.add_argument("--config",type=Path,required=True);a=p.parse_args();os.umask(0o077);run(read_json(a.config))
if __name__=="__main__":main()
