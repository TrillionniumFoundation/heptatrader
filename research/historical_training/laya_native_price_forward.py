"""One true NEW future frozen Native+Numeric PRICE forecast; research shadow only.

This uses the original native NDU feature bridge and frozen Numeric readout
from the existing Laya bootstrap evaluation. It is NOT an Laya7 GPU
prediction, a trade-action head, a signed Agentd decision, or actual PnL.
"""
from __future__ import annotations
import argparse
import fcntl
import json
import math
import os
import time
from pathlib import Path
import numpy as np

from historical_training.common import read, sha, write_once
from laya_cell.integrity import ContractError, digest, sha_file
from laya_cell.native import NativePorts
from laya_cell.dataset import verify_source_prefixes
from laya_cell.resident_data import forecast_input, read_quotes
from laya_cell.resident_recent_source import read_recent_original_quotes
from .laya_fast_future_canary import _config, _directory, _sync

SCHEMA="heptatrader.native-price-future-shadow-plan.v1"
INPUT_SCHEMA="heptatrader.native-price-future-input.v1"
PREDICTION_SCHEMA="heptatrader.native-price-future-prediction.v1"
COMMIT_SCHEMA="heptatrader.native-price-future-commit.v1"
WINDOW_MS=16000
MIN_LEAD_MS=60000
MAX_LEAD_MS=600000
HORIZON_SEC=300
BASE=Path(__file__).resolve().parents[1]
FILE_MAP=(
 "historical_training/laya_native_price_forward.py",
 "historical_training/laya_fast_future_canary.py",
 "neuron_learning/laya_cell/resident_recent_source.py",
 "neuron_learning/laya_cell/resident_data.py",
 "neuron_learning/laya_cell/native.py",
 "neuron_learning/laya_cell/resident_predict.py",
 "neuron_learning/laya_cell/source_archive.py",
 "neuron_learning/laya_cell/shadow_source.py",
 "neuron_learning/data.py",
)
def now_ms():
 return time.time_ns()//1000000
def inventory():
 return {name:sha_file(BASE/name) for name in FILE_MAP}
def _model(c,expected=None):
 root=Path(c["baseline_directory"])
 if root.is_symlink() or not root.is_absolute():
  raise ContractError("NATIVE_FUTURE_BASELINE_ROOT_UNSAFE")
 manifest=read(root/"freeze-manifest.json",16*1024*1024)
 model_path=root/"model.json"
 model=read(model_path,1024*1024)
 if sha(model_path)!=manifest.get("inventory_sha256",{}).get("model.json"):
  raise ContractError("NATIVE_FUTURE_BASELINE_FREEZE_CHANGED")
 if (model.get("effect_authority") is not False
     or type(model.get("trained_until_ms")) is not int
     or not isinstance(model.get("model_id"),str)
     or len(model.get("model_id"))!=64
     or not isinstance(model.get("weights"),list)
     or len(model["weights"])!=562):
  raise ContractError("NATIVE_FUTURE_BASELINE_PROFILE_INVALID")
 if expected is not None and (
    sha(model_path)!=expected["baseline_model_sha256"]
    or sha(root/"freeze-manifest.json")!=expected["baseline_freeze_sha256"]
    or model["model_id"]!=expected["baseline_model_id"]):
  raise ContractError("NATIVE_FUTURE_BASELINE_CHANGED_AFTER_REGISTER")
 return model
def _plan(root):
 root=_directory(root)
 p=read(root/"plan.json",65536)
 if (p.get("schema")!=SCHEMA
     or p.get("plan_id")!=digest({k:v for k,v in p.items() if k!="plan_id"})
     or p.get("target_horizon_sec")!=HORIZON_SEC
     or p.get("window_end_ms")!=p["slot_start_ms"]+WINDOW_MS
     or p["registered_at_ms"]+MIN_LEAD_MS>p["slot_start_ms"]
     or p.get("source_profile")!="ARCHIVE_VERIFIED_RECENT_ORIGINAL_V2"
     or p.get("forecast_profile")!="FROZEN_BOOTSTRAP_NATIVE_NUMERIC_300S_V1"
     or p.get("automatic_model_promotion") is not False
     or p.get("trade_authority") is not False
     or p.get("broker_orders_sent")!=0
     or p.get("canonical_agentd_owner_enrolled") is not False
     or p.get("model_inference_allowed") is not True):
  raise ContractError("NATIVE_FUTURE_FROZEN_PLAN_INVALID")
 if p.get("code_sha256")!=inventory():
  raise ContractError("NATIVE_FUTURE_PINNED_CODE_CHANGED")
 return p

def register(root,config_file,slot_start_ms):
 if type(slot_start_ms) is not int:
  raise ContractError("NATIVE_FUTURE_SLOT_INTEGER_REQUIRED")
 c=_config(config_file)
 at=now_ms()
 if not MIN_LEAD_MS<=slot_start_ms-at<=MAX_LEAD_MS:
  raise ContractError("NATIVE_FUTURE_NOT_REGISTERED_BEFORE_SLOT")
 model=_model(c)
 if model["trained_until_ms"]>=at:
  raise ContractError("NATIVE_FUTURE_MODEL_NOT_FROZEN_BEFORE_REGISTER")
 if sha(c["native_binary"])!=c["native_binary_sha256"]:
  raise ContractError("NATIVE_FUTURE_NATIVE_BINARY_CHANGED")
 root=_directory(root,create=True)
 body={
  "schema":SCHEMA,
  "registered_at_ms":at,
  "slot_start_ms":slot_start_ms,
  "window_end_ms":slot_start_ms+WINDOW_MS,
  "target_horizon_sec":HORIZON_SEC,
  "original_config_path":str(config_file),
  "original_config_sha256":sha(config_file),
  "original_period_sha256":c["period_sha256"],
  "original_feed_directory":c["feed_directory"],
  "source_profile":"ARCHIVE_VERIFIED_RECENT_ORIGINAL_V2",
  "forecast_profile":"FROZEN_BOOTSTRAP_NATIVE_NUMERIC_300S_V1",
  "baseline_model_id":model["model_id"],
  "baseline_model_sha256":sha(Path(c["baseline_directory"])/"model.json"),
  "baseline_freeze_sha256":sha(Path(c["baseline_directory"])/"freeze-manifest.json"),
  "baseline_trained_until_ms":model["trained_until_ms"],
  "original_native_binary_sha256":c["native_binary_sha256"],
  "code_sha256":inventory(),
  "model_inference_allowed":True,
  "full_original_asof_replay_required":True,
  "no_trade_policy":"NO_ACTION_NO_BROKER_ORDER",
  "actual_broker_execution_economics":False,
  "independent_model_outcome_attested":False,
  "canonical_agentd_owner_enrolled":False,
  "automatic_model_promotion":False,
  "trade_authority":False,
  "broker_orders_sent":0,
 }
 body["plan_id"]=digest(body)
 write_once(root/"plan.json",body)
 _sync(root)
 return body

def predict_point(c,plan,sample):
 """Same frozen original native/Numeric readout path as resident_predict.py."""
 model=_model(c,plan)
 if model["trained_until_ms"]>sample["anchor_ms"]:
  raise ContractError("NATIVE_FUTURE_MODEL_WAS_NOT_FROZEN_BEFORE_QUOTE")
 port=NativePorts(Path(c["native_binary"]),plan["original_native_binary_sha256"])
 out=port.call({"op":"neuron","episodes":[sample["native_episode"]]})
 activation=out.get("episodes")
 if not isinstance(activation,list) or len(activation)!=1:
  raise ContractError("NATIVE_FUTURE_NUMERIC_ACTIVATION_SHAPE")
 x=np.asarray(sample["raw_features"]+activation[0]["activation"],dtype=np.float64)
 w=np.asarray(model["weights"],dtype=np.float64)
 if (x.shape!=(32,) or w.shape!=(562,)
     or not np.isfinite(x).all() or not np.isfinite(w).all()):
  raise ContractError("NATIVE_FUTURE_NUMERIC_MODEL_SHAPE")
 z=np.tanh(x@w[:512].reshape(32,16)+w[512:528])
 prediction=(z@w[528:560].reshape(16,2)+w[560:562])[0]*100.0
 if not math.isfinite(float(prediction)) or abs(float(prediction))>1000000:
  raise ContractError("NATIVE_FUTURE_PREDICTION_NOT_FINITE_OR_BOUND")
 return float(prediction),out["native_source_sha"]

def capture(root,config_file,*,wait=False):
 root=_directory(root)
 plan=_plan(root)
 c=_config(config_file,plan)
 if (sha(c["native_binary"])!=plan["original_native_binary_sha256"]
     or c["baseline_directory"] is None):
  raise ContractError("NATIVE_FUTURE_RUNTIME_CHANGED")
 model=_model(c,plan)
 if any((root/name).exists() or (root/name).is_symlink()
        for name in ("input.json","prediction.json","commit.json","failure.json")):
  raise ContractError("NATIVE_FUTURE_CAPTURE_ALREADY_ATTEMPTED")
 fd=os.open(root/"capture.lock",os.O_CREAT|os.O_NOFOLLOW|os.O_RDWR|os.O_CLOEXEC,0o600)
 try:
  fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
  if any((root/name).exists() or (root/name).is_symlink()
         for name in ("input.json","prediction.json","commit.json","failure.json")):
   raise ContractError("NATIVE_FUTURE_CAPTURE_ALREADY_ATTEMPTED")
  while now_ms()<plan["slot_start_ms"]:
   if not wait:
    raise ContractError("NATIVE_FUTURE_SLOT_NOT_DUE")
   time.sleep(min(.25,max(.01,(plan["slot_start_ms"]-now_ms())/1000)))
  attempts=0
  last_reason=None
  while now_ms()<plan["window_end_ms"]-1000:
   asof=now_ms()
   attempts+=1
   try:
    t=time.monotonic()
    rows,prefixes=read_recent_original_quotes(c,asof)
    at=now_ms()
    if at>=plan["window_end_ms"]-500:
     raise ContractError("NATIVE_FUTURE_SOURCE_READ_FINISHED_AFTER_DEADLINE")
    sample,feature_row=forecast_input(c,rows,at)
    original=[q for q in rows if q["t"]==sample["anchor_ms"]]
    if (len(original)!=1 or sample["anchor_ms"]<plan["slot_start_ms"]
       or not plan["registered_at_ms"]<original[0]["known_at_ms"]<=asof
       or model["trained_until_ms"]>=sample["anchor_ms"]):
     raise ContractError("NATIVE_FUTURE_NO_NEW_PREDECLARED_ORIGINAL_SOURCE")
    score,source_sha=predict_point(c,plan,sample)
    done=now_ms()
    if done>plan["window_end_ms"]-250 or done>=sample["anchor_ms"]+HORIZON_SEC*1000:
     raise ContractError("NATIVE_FUTURE_PREDICTION_FINISHED_TOO_LATE")
    input_item={
     "schema":INPUT_SCHEMA,"plan_id":plan["plan_id"],
     "slot_start_ms":plan["slot_start_ms"],"source_asof_ms":asof,
     "feature_decision_ms":at,"quote_event_at_ms":sample["anchor_ms"],
     "quote_known_at_ms":original[0]["known_at_ms"],
     "quote_receipt":original[0]["receipt"],
     "sample_id":sample["sample_id"],"sample_sha256":digest(sample),
     "feature_state_sha256":digest(feature_row["state"]),
     "original_source_prefixes":prefixes,
     "sample":sample,"feature_state":feature_row["state"],
     "source_read_ms":round((time.monotonic()-t)*1000,3),
     "attempts":attempts,"orders_sent":0
    }
    write_once(root/"input.json",input_item);_sync(root)
    pred_item={
     "schema":PREDICTION_SCHEMA,"plan_id":plan["plan_id"],
     "source_input_sha256":sha(root/"input.json"),
     "baseline_model_id":model["model_id"],
     "baseline_model_sha256":plan["baseline_model_sha256"],
     "native_binary_sha256":plan["original_native_binary_sha256"],
     "native_source_sha":source_sha,
     "sample_id":sample["sample_id"],"anchor_ms":sample["anchor_ms"],
     "target_horizon_sec":HORIZON_SEC,
     "frozen_numeric_model_expected_mid_bps":score,
     "score_semantics":"ORIGINAL_FROZEN_NUMERIC_300S_MID_NOT_TRADING_REWARD",
     "prediction_ready_at_ms":done,"source_decision_at_ms":at,
     "new_prospective_numeric_prediction":True,
     "laya_model_inference":False,
     "action_head_is_trade_permission":False,
     "automatic_trading":False,
     "broker_orders_sent":0,
    }
    write_once(root/"prediction.json",pred_item);_sync(root)
    sealed=now_ms()
    if sealed>plan["window_end_ms"]:
     raise ContractError("NATIVE_FUTURE_PREDICTION_FSYNC_AFTER_DEADLINE")
    ack={
     "schema":COMMIT_SCHEMA,"plan_id":plan["plan_id"],
     "input_sha256":sha(root/"input.json"),
     "prediction_sha256":sha(root/"prediction.json"),
     "post_fsync_at_ms":sealed,
     "prediction_sealed_before_deadline":True,
     "native_numeric_only":True,
     "canonical_owner_enrolled":False,
     "broker_orders_sent":0,
    }
    write_once(root/"commit.json",ack);_sync(root)
    return ack
   except ContractError as exc:
    last_reason=str(exc)
    if (root/"input.json").exists() or (root/"prediction.json").exists():
     raise
    time.sleep(.25)
  failure={
   "schema":"heptatrader.native-price-future-missed-slot.v1",
   "plan_id":plan["plan_id"],"window_end_ms":plan["window_end_ms"],
   "recorded_at_ms":now_ms(),
   "reason":str(last_reason or "NO_GENUINE_NEW_ORIGINAL_SOURCE")[:200],
   "original_sample_fabricated":False,
   "model_prediction_fabricated":False,
   "broker_orders_sent":0,
  }
  write_once(root/"failure.json",failure)
  _sync(root)
  raise ContractError("NATIVE_FUTURE_SLOT_MISSED:"+str(last_reason))
 finally:
  os.close(fd)

def verify(root,config_file):
 root=_directory(root)
 plan=_plan(root)
 c=_config(config_file,plan)
 input_item=read(root/"input.json",128*1024)
 pred=read(root/"prediction.json",65536)
 ack=read(root/"commit.json",65536)
 if (input_item.get("schema")!=INPUT_SCHEMA
     or pred.get("schema")!=PREDICTION_SCHEMA
     or ack.get("schema")!=COMMIT_SCHEMA
     or any(x.get("plan_id")!=plan["plan_id"] for x in (input_item,pred,ack))
     or pred.get("source_input_sha256")!=sha(root/"input.json")
     or ack.get("input_sha256")!=sha(root/"input.json")
     or ack.get("prediction_sha256")!=sha(root/"prediction.json")
     or ack.get("prediction_sealed_before_deadline") is not True
     or ack.get("post_fsync_at_ms",2**63)>plan["window_end_ms"]
     or pred.get("baseline_model_id")!=plan["baseline_model_id"]
     or pred.get("baseline_model_sha256")!=plan["baseline_model_sha256"]
     or pred.get("native_binary_sha256")!=plan["original_native_binary_sha256"]
     or pred.get("target_horizon_sec")!=HORIZON_SEC
     or pred.get("laya_model_inference") is not False
     or pred.get("automatic_trading") is not False
     or pred.get("action_head_is_trade_permission") is not False
     or ack.get("broker_orders_sent")!=0 or pred.get("broker_orders_sent")!=0):
  raise ContractError("NATIVE_FUTURE_PREDICTION_CHAIN_NOT_VERIFIED")
 asof=input_item["source_asof_ms"]
 decision=input_item["feature_decision_ms"]
 if (not plan["slot_start_ms"]<=asof<=decision<=pred["prediction_ready_at_ms"]
       <=ack["post_fsync_at_ms"]<=plan["window_end_ms"]):
  raise ContractError("NATIVE_FUTURE_SIGNED_TIMELINE_CHANGED")
 # The original at-decision seal must still be an authentic ordered
 # prefix of the current append-only archive, even after later rotations.
 verify_source_prefixes({
  "source_prefixes":input_item["original_source_prefixes"],
  "scope":c["period_sha256"]+":EUR.USD"})
 original,original_prefixes=read_quotes(c,asof)
 sample,row=forecast_input(c,original,decision)
 original_last=[x for x in original if x["t"]==sample["anchor_ms"]]
 if (sample["anchor_ms"]<plan["slot_start_ms"]
     or len(original_last)!=1
     or original_last[0]["receipt"]!=input_item["quote_receipt"]
     or original_last[0]["known_at_ms"]!=input_item["quote_known_at_ms"]
     or sample["sample_id"]!=input_item["sample_id"]
     or digest(sample)!=input_item["sample_sha256"]
     or digest(row["state"])!=input_item["feature_state_sha256"]
     or input_item["sample"]!=sample
     or input_item["feature_state"]!=row["state"]
     or pred["sample_id"]!=sample["sample_id"]
     or pred["anchor_ms"]!=sample["anchor_ms"]):
  raise ContractError("NATIVE_FUTURE_ORIGINAL_SOURCE_OR_FEATURES_CHANGED")
 score,source_sha=predict_point(c,plan,sample)
 if (pred["native_source_sha"]!=source_sha
     or pred["frozen_numeric_model_expected_mid_bps"]!=score):
  raise ContractError("NATIVE_FUTURE_MODEL_OUTPUT_MISMATCH")
 return {
  "schema":"heptatrader.native-price-prospective-verification.v1",
  "plan_id":plan["plan_id"],
  "sample_id":sample["sample_id"],
  "native_model_output_recomputed":True,
  "full_original_source_reverified":True,
  "exact_original_quote_receipt_verified":True,
  "frozen_predecision_model_sha256":plan["baseline_model_sha256"],
  "actual_prediction_sha256":sha(root/"prediction.json"),
  "source_proof_sha256":sha(root/"input.json"),
  "prediction_sealed_at_ms":ack["post_fsync_at_ms"],
  "deadline_ms":plan["window_end_ms"],
  "independent_price_outcome_observed":False,
  "actual_strategy_profit":False,
  "true_model_prospective_observations":1,
  "laya_7bin_gpu_model_executed":False,
  "canonical_owner_enrolled":False,
  "broker_orders_sent":0,
 }

def main():
 p=argparse.ArgumentParser()
 p.add_argument("--mode",choices=("register","capture","verify"),required=True)
 p.add_argument("--root",type=Path,required=True)
 p.add_argument("--config",type=Path,required=True)
 p.add_argument("--slot-ms",type=int)
 p.add_argument("--wait",action="store_true")
 a=p.parse_args()
 os.umask(0o077)
 if a.mode=="register":result=register(a.root,a.config,a.slot_ms)
 elif a.mode=="capture":result=capture(a.root,a.config,wait=a.wait)
 else:result=verify(a.root,a.config)
 print(json.dumps(result,sort_keys=True))
if __name__=="__main__":main()
