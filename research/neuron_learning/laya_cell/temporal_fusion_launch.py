"""Fail-closed launcher for shadow-only temporal/Laya fusion roles."""
from __future__ import annotations
import argparse,importlib,os,sys
from pathlib import Path
from .integrity import read_json,sha_file,ContractError
from .resident_store import atomic

def main():
 p=argparse.ArgumentParser();p.add_argument("--config",type=Path,required=True);p.add_argument("--mode",choices=("predictor","evaluator"),required=True);a=p.parse_args();os.umask(0o077)
 c=read_json(a.config);root=Path(c["shadow_root"]);root.mkdir(mode=0o700,parents=True,exist_ok=True)
 if sha_file(Path(c["period_path"]))!=c["period_sha256"]:raise ContractError("FUSION_PERIOD_CHANGED")
 for name,expected in c["runtime_sha256"].items():
  path=Path(c["runtime_directory"])/name
  if sha_file(path)!=expected:raise ContractError("FUSION_RUNTIME_CHANGED")
 reads=[c["runtime_directory"],c["base_directory"],c["laya_candidate_directory"],c["fusion_candidate_directory"],
        c["feed_directory"],c["period_path"],c["seed_dataset"],str(Path(c["python"]).parent.parent),str(a.config)]
 from sandbox import enter
 receipt=enter({"output_dir":str(root),"period_path":c["period_path"],"legacy_capture":c["seed_dataset"],
  "oms_path":c["oms_path"],"export_dir":c["export_directory"],"read_only_paths":reads})
 receipt.update({"mode":a.mode,"trade_permission":False,"remote_model_calls":0})
 atomic(root/("sandbox-"+a.mode+".json"),receipt)
 sys.argv=["temporal_fusion_shadow","--config",str(a.config),"--mode",a.mode]
 importlib.import_module(".temporal_fusion_shadow","laya_cell").main()
if __name__=="__main__":main()
