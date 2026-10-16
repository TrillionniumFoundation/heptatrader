#!/usr/bin/env python3
"""Write-once future v2 TRAIN ONLY population from CLOSED original Laya plans.

The output belongs outside live Laya control, candidate, and evaluation dirs.
No GPU/model training or trading execution is performed here.
"""
from __future__ import annotations
import argparse,json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"research/neuron_learning"))
from laya_cell.integrity import ContractError
from laya_cell.resident_action_v2_closed_source import (
  collect_parent_closed_train,check_train_source)


def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument("--closed-evaluations",required=True,type=Path)
 p.add_argument("--frozen-parent",required=True)
 p.add_argument("--train-cutoff-ms",required=True,type=int)
 p.add_argument("--output",required=True,type=Path)
 a=p.parse_args()
 os.umask(0o077)
 if (not a.output.is_absolute() or a.output.exists() or
         a.output.is_symlink() or not a.output.parent.is_dir() or
         not a.closed_evaluations.is_dir() or
         a.output.parent.resolve()==a.closed_evaluations.resolve()):
  p.error("new absolute training-output outside canonical evaluations is required")
 data=collect_parent_closed_train(
  a.closed_evaluations,a.frozen_parent,a.train_cutoff_ms)
 summary=check_train_source(data)
 if not summary["action_training_two_class_supported"]:
  raise ContractError("V2_CLOSED_TRAIN_GENUINE_ACT_CLASS_UNSUPPORTED")
 fd=os.open(a.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o400)
 with os.fdopen(fd,'w') as f:
  json.dump(data,f,sort_keys=True,allow_nan=False,separators=(",",":"))
  f.write("\n");f.flush();os.fsync(f.fileno())
 print("FROZEN_V2_CLOSED_PARENT_TRAIN_ONLY",json.dumps(summary,sort_keys=True),flush=True)


if __name__=="__main__":
 main()
