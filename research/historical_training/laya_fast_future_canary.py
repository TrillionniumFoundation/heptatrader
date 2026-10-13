"""Finite, preregistered FUTURE original Laya source canary; NO model or orders.

Exactly one original PAPER input and an independent full-source verification.
This is not a Laya model prediction, output label, or signed product authority.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import time
from pathlib import Path

from historical_training.common import read, sha, write_once, EvidenceError
from laya_cell.integrity import ContractError, digest, sha_file
from laya_cell.resident_data import forecast_input, read_quotes
from laya_cell.resident_recent_source import read_recent_original_quotes

SCHEMA="heptatrader.laya-future-recent-source-canary.v1"
INPUT_SCHEMA="heptatrader.laya-future-source-input-only.v1"
ACK_SCHEMA="heptatrader.laya-future-source-ack.v1"
WINDOW_MS=16000
MIN_LEAD_MS=60000
MAX_LEAD_MS=600000
FILE_MAP=(
 "historical_training/laya_fast_future_canary.py",
 "neuron_learning/laya_cell/resident_recent_source.py",
 "neuron_learning/laya_cell/resident_data.py",
 "neuron_learning/laya_cell/source_archive.py",
 "neuron_learning/laya_cell/shadow_source.py",
 "neuron_learning/laya_cell/integrity.py",
)
BASE=Path(__file__).resolve().parents[1]

def now_ms():
 return time.time_ns()//1000000

def file_inventory():
 return {name:sha_file(BASE/name) for name in FILE_MAP}

def _config(config_file,plan=None):
 f=Path(config_file)
 if f.is_symlink() or not f.is_absolute() or not f.is_file():
  raise ContractError("CANARY_CONFIG_FILE_NOT_PINNED")
 c=read(f,1024*1024)
 period=Path(c.get("period_path",""))
 feed=Path(c.get("feed_directory",""))
 if (not period.is_absolute() or period.is_symlink() or not period.is_file()
     or not feed.is_absolute() or feed.is_symlink() or not feed.is_dir()
     or not isinstance(c.get("period_sha256"),str)
     or sha(period)!=c["period_sha256"]
     or c.get("completed_history_sources")):
  raise ContractError("CANARY_REAL_PAPER_SOURCE_REQUIRED")
 if plan is not None:
  if (str(f)!=plan["original_config_path"]
      or sha(f)!=plan["original_config_sha256"]
      or str(feed)!=plan["original_feed_directory"]
      or c["period_sha256"]!=plan["original_period_sha256"]):
   raise ContractError("CANARY_SOURCE_SCOPE_CHANGED")
 return c

def _directory(root,create=False):
 root=Path(root)
 if root.is_symlink() or not root.is_absolute():
  raise ContractError("CANARY_UNSAFE_OUTPUT_DIRECTORY")
 if create:
  root.mkdir(parents=True,mode=0o700,exist_ok=False)
 if not root.is_dir() or root.stat().st_mode&0o077 or root.stat().st_uid!=os.getuid():
  raise ContractError("CANARY_OUTPUT_NOT_PRIVATE")
 return root

def _plan(root):
 root=_directory(root)
 plan=read(root/"plan.json",65536)
 if (plan.get("schema")!=SCHEMA or plan.get("plan_id")!=
     digest({k:v for k,v in plan.items() if k!="plan_id"})):
  raise ContractError("CANARY_FROZEN_PLAN_CHANGED")
 if (plan.get("window_end_ms")!=plan["slot_start_ms"]+WINDOW_MS
     or plan["registered_at_ms"]+MIN_LEAD_MS>plan["slot_start_ms"]
     or plan.get("versioned_source_profile")!="ARCHIVE_VERIFIED_RECENT_ORIGINAL_V2"
     or plan.get("model_inference_allowed") is not False
     or plan.get("broker_orders_sent")!=0
     or plan.get("full_source_parity_mandatory") is not True):
  raise ContractError("CANARY_FROZEN_PLAN_INVALID")
 if plan.get("code_sha256")!=file_inventory():
  raise ContractError("CANARY_PINNED_CODE_CHANGED")
 return plan

def register(root,config_file,slot_start_ms):
 # Library calls may share an existing evaluator process: never mutate its
 # process-wide umask. File creation already uses explicit private modes.
 if type(slot_start_ms) is not int:
  raise ContractError("CANARY_SLOT_INVALID")
 c=_config(config_file)
 current=now_ms()
 if not MIN_LEAD_MS<=slot_start_ms-current<=MAX_LEAD_MS:
  raise ContractError("CANARY_MUST_REGISTER_BEFORE_FUTURE")
 plan={
  "schema":SCHEMA,
  "original_config_path":str(Path(config_file)),
  "original_config_sha256":sha(config_file),
  "original_period_sha256":c["period_sha256"],
  "original_feed_directory":str(Path(c["feed_directory"])),
  "registered_at_ms":current,
  "slot_start_ms":slot_start_ms,
  "window_end_ms":slot_start_ms+WINDOW_MS,
  "versioned_source_profile":"ARCHIVE_VERIFIED_RECENT_ORIGINAL_V2",
  "code_sha256":file_inventory(),
  "full_source_parity_mandatory":True,
  "original_account_paper_only":True,
  "model_inference_allowed":False,
  "automatic_model_promotion":False,
  "broker_orders_sent":0,
  "canonical_owner_enrolled":False,
 }
 plan["plan_id"]=digest(plan)
 root=_directory(root,create=True)
 write_once(root/"plan.json",plan)
 _sync(root)
 return plan

def _sync(root):
 fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try: os.fsync(fd)
 finally: os.close(fd)

def capture(root,config_file,*,wait=False):
 root=_directory(root);plan=_plan(root);c=_config(config_file,plan)
 if (root/"input.json").exists() or (root/"commit.json").exists():
  raise ContractError("CANARY_CAPTURE_ALREADY_ATTEMPTED")
 current=now_ms()
 if current<plan["slot_start_ms"]:
  if not wait:
   raise ContractError("CANARY_FUTURE_SLOT_NOT_DUE")
  if plan["slot_start_ms"]-current>MAX_LEAD_MS:
   raise ContractError("CANARY_WAIT_UNBOUNDED")
  while now_ms()<plan["slot_start_ms"]:
   time.sleep(min(.25,max(.01,(plan["slot_start_ms"]-now_ms())/1000)))
 latest_error=None
 attempts=0
 while now_ms()<plan["window_end_ms"]-1200:
  at=now_ms()
  if at<plan["slot_start_ms"]:
   continue
  attempts+=1
  try:
   tick=time.monotonic()
   original,prefixes=read_recent_original_quotes(c,at)
   end=now_ms()
   if end>=plan["window_end_ms"]-800:
    raise ContractError("CANARY_SOURCE_READ_FINISHED_TOO_LATE")
   sample,row=forecast_input(c,original,end)
   if (sample["anchor_ms"]<plan["slot_start_ms"]
       or sample["anchor_ms"]>at
       or sample.get("history_selection_version")!="CONTIGUOUS_SELECTED_SUFFIX_V2"):
    raise ContractError("CANARY_QUOTE_OUTSIDE_FROZEN_SLOT")
   chosen=[q for q in original if q["t"]==sample["anchor_ms"]]
   if len(chosen)!=1 or not plan["registered_at_ms"]<chosen[0]["known_at_ms"]<=at:
    raise ContractError("CANARY_NO_GENUINELY_NEW_ORIGINAL_QUOTE")
   receipt={
    "schema":INPUT_SCHEMA,"plan_id":plan["plan_id"],
    "original_config_sha256":plan["original_config_sha256"],
    "registered_at_ms":plan["registered_at_ms"],
    "slot_start_ms":plan["slot_start_ms"],"window_end_ms":plan["window_end_ms"],
    "source_asof_ms":at,"feature_decision_ms":end,
    "read_duration_ms":round((time.monotonic()-tick)*1000,3),
    "attempt_count":attempts,
    "latest_source_event_at_ms":sample["anchor_ms"],
    "latest_source_receipt":chosen[0]["receipt"],
    "latest_source_known_at_ms":chosen[0]["known_at_ms"],
    "source_original_prefixes_digest":digest(prefixes),
    "sample_id":sample["sample_id"],"sample_sha256":digest(sample),
    "price_state_sha256":digest(row["state"]),
    "source_read_profile":"ARCHIVE_VERIFIED_RECENT_ORIGINAL_V2",
    "original_price_only":True,"prediction_produced":False,
    "model_loaded":False,"model_selection_allowed":False,
    "orders_sent":0,"broker_requests_sent":0,
   }
   write_once(root/"input.json",receipt)
   _sync(root)
   committed=now_ms()
   ack={"schema":ACK_SCHEMA,"plan_id":plan["plan_id"],
        "input_sha256":sha(root/"input.json"),
        "post_fsync_at_ms":committed,
        "source_input_sealed_before_deadline":committed<=plan["window_end_ms"],
        "broker_orders_sent":0,"model_loaded":False}
   write_once(root/"commit.json",ack)
   _sync(root)
   return ack
  except ContractError as exc:
   latest_error=str(exc)
   if (root/"input.json").exists():
    raise
   time.sleep(.25)
 raise ContractError("CANARY_MISSED_ORIGINAL_SLOT:"+str(latest_error or "SOURCE_UNAVAILABLE"))

def verify(root,config_file):
 root=_directory(root);plan=_plan(root);c=_config(config_file,plan)
 item=read(root/"input.json",65536)
 ack=read(root/"commit.json",65536)
 if (item.get("schema")!=INPUT_SCHEMA or ack.get("schema")!=ACK_SCHEMA
     or item.get("plan_id")!=plan["plan_id"] or ack.get("plan_id")!=plan["plan_id"]
     or ack.get("input_sha256")!=sha(root/"input.json")
     or ack.get("source_input_sealed_before_deadline") is not True
     or ack.get("model_loaded") is not False
     or item.get("prediction_produced") is not False
     or ack.get("post_fsync_at_ms",0)>plan["window_end_ms"]):
  raise ContractError("CANARY_CAPTURE_ACK_OR_IDENTITY_NOT_VALID")
 asof=item["source_asof_ms"];decision=item["feature_decision_ms"]
 if (type(asof) is not int or type(decision) is not int
     or not plan["slot_start_ms"]<=asof<=decision<=ack["post_fsync_at_ms"]
     or asof>plan["window_end_ms"]):
  raise ContractError("CANARY_CAPTURE_CLOCK_NOT_FROZEN")
 full,prefixes=read_quotes(c,asof)
 sample,row=forecast_input(c,full,decision)
 if (sample["sample_id"]!=item["sample_id"]
     or digest(sample)!=item["sample_sha256"]
     or digest(row["state"])!=item["price_state_sha256"]
     or sample["anchor_ms"]!=item["latest_source_event_at_ms"]):
  raise ContractError("CANARY_INDEPENDENT_FULL_SOURCE_PARITY_FAILURE")
 same=[q for q in full if q["t"]==sample["anchor_ms"]]
 if (len(same)!=1 or same[0]["receipt"]!=item["latest_source_receipt"]
     or same[0]["known_at_ms"]!=item["latest_source_known_at_ms"]):
  raise ContractError("CANARY_ORIGINAL_RECEIPT_CHANGED")
 return {"schema":"heptatrader.laya-future-source-verified.v1",
         "plan_id":plan["plan_id"],"original_prefix_asof_ms":asof,
         "complete_original_asof_feature_parity":True,
         "quote_source_original_receipt_verified":True,
         "sample_id":sample["sample_id"],
         "source_decision_population_count":1,
         "model_predictions":0,"actual_trade_rewards":0,
         "canonical_owner_enrolled":False,
         "broker_queries_sent":0,"broker_orders_sent":0}

def main():
 cli=argparse.ArgumentParser()
 cli.add_argument("--mode",choices=("register","capture","verify"),required=True)
 cli.add_argument("--root",type=Path,required=True)
 cli.add_argument("--config",type=Path,required=True)
 cli.add_argument("--slot-ms",type=int)
 cli.add_argument("--wait",action="store_true")
 args=cli.parse_args()
 # Only this short-lived CLI process owns the process umask.
 os.umask(0o077)
 if args.mode=="register":
  result=register(args.root,args.config,args.slot_ms)
 elif args.mode=="capture":
  result=capture(args.root,args.config,wait=args.wait)
 else:
  result=verify(args.root,args.config)
 print(json.dumps(result,sort_keys=True))

if __name__=="__main__":
 main()
