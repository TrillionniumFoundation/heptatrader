"""Install two shadow-only temporal/Laya fusion services.

No trading, existing learner, evaluator, Execution or Gateway unit is restarted.
"""
from __future__ import annotations
import argparse,hashlib,json,os,subprocess,time
from pathlib import Path
from .integrity import read_json,digest,canonical,sha_file,ContractError
from .temporal_fusion_model import PROFILE
from .temporal_fusion_shadow import RULE

def write_once(path,raw,mode=0o400):
 path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 if path.exists():
  if path.read_bytes()!=raw:raise ContractError("IMMUTABLE_INSTALL_CONFLICT:"+str(path))
  return
 with path.open("xb") as f:f.write(raw);f.flush();os.fsync(f.fileno())
 path.chmod(mode)

def show(name):
 raw=subprocess.check_output(["systemctl","--user","show",name,"-p","MainPID","-p","ActiveState","-p","NRestarts"],text=True)
 return dict(x.split("=",1) for x in raw.splitlines() if "=" in x)

def install(laya_config_path,fusion_candidate,start=False,update_owned=False):
 h=Path.home();src=Path(__file__).resolve().parent;learn=src.parent
 c=read_json(laya_config_path);candidate=read_json(fusion_candidate/"candidate.json")
 if candidate["profile"]!=PROFILE or sha_file(fusion_candidate/"fusion.safetensors")!=candidate["fusion_sha256"]:raise ContractError("FUSION_CANDIDATE_CHANGED")
 if candidate["period_sha256"]!=c["period_sha256"] or sha_file(Path(c["period_path"]))!=c["period_sha256"]:raise ContractError("FUSION_PERIOD_SCOPE")
 # Resolve and freeze the exact Laya candidate named by the fusion artifact.
 root=Path(c["candidate_directory"]);laya_dir=None
 for p in root.glob("*/artifact/candidate.json"):
  value=read_json(p)
  if value.get("candidate_id")==candidate["laya_candidate_id"]:laya_dir=p.parent;break
 if laya_dir is None:raise ContractError("FUSION_LAYA_CANDIDATE_NOT_FOUND")
 peers={name:show(name) for name in ("heptatrader-paper-driver.service","heptatrader-crypto-driver.service","heptatrader-execution.service",
   "heptatrader-gateway.service","heptatrader-laya-trainer.service","heptatrader-laya-evaluator.service","heptatrader-laya-financial-value.service")}
 files={}
 for p in sorted(src.glob("*.py")):files["learn/laya_cell/"+p.name]=p
 for name in ("data.py","sandbox.py"):files["learn/"+name]=learn/name
 hashes={name:sha_file(path) for name,path in files.items()};release_id=digest(hashes)
 release=h/".local/share/heptatrader/temporal-fusion/releases"/release_id;release.mkdir(mode=0o700,parents=True,exist_ok=True)
 for name,path in files.items():write_once(release/name,path.read_bytes())
 shadow=h/".local/state/heptatrader/neuron-learning/temporal-fusion"/c["period_sha256"]/candidate["candidate_id"]/release_id
 shadow.mkdir(mode=0o700,parents=True,exist_ok=True)
 plan_path=shadow/"plan.json"
 if plan_path.exists():
  plan=read_json(plan_path)
  if (plan.get("candidate_id")!=candidate["candidate_id"] or plan.get("laya_candidate_id")!=candidate["laya_candidate_id"] or
      plan.get("period_sha256")!=c["period_sha256"] or plan.get("rule")!=RULE):
   raise ContractError("EXISTING_FUSION_PLAN_SCOPE")
 else:
  registered=int(time.time()*1000);start_ms=registered+60000
  end_ms=start_ms+(RULE["slots"]-1)*RULE["slot_spacing_ms"]+RULE["input_grace_ms"]+RULE["horizon_ms"]+15000
  plan={"schema":"heptatrader.temporal-laya-fusion-plan.v1","registered_at_ms":registered,"start_ms":start_ms,"end_ms":end_ms,
   "candidate_id":candidate["candidate_id"],"laya_candidate_id":candidate["laya_candidate_id"],"period_sha256":c["period_sha256"],
   "rule":RULE,"runtime_sha256":hashes,"training_row_ids":candidate["train_row_ids"],"internal_holdout_row_ids":candidate["internal_holdout_row_ids"],
   "automatic_selection":False,"trade_permission":False}
  plan["plan_id"]=digest(plan);write_once(plan_path,canonical(plan)+b"
")
 config={**c,"profile":PROFILE,"fusion_candidate_directory":str(fusion_candidate),"laya_candidate_directory":str(laya_dir),
  "shadow_root":str(shadow),"runtime_directory":str(release),"runtime_sha256":hashes,"plan_id":plan["plan_id"]}
 cfgdir=h/".config/heptatrader-temporal-fusion";cfgdir.mkdir(mode=0o700,parents=True,exist_ok=True)
 cfg=cfgdir/(digest(config)+".json");write_once(cfg,canonical(config)+b"\n")
 units={}
 for mode in ("predictor","evaluator"):
  name="heptatrader-temporal-fusion-"+mode+".service";unit=h/".config/systemd/user"/name
  py=c["python"]
  text=f"""[Unit]
Description=HeptaTrader temporal+Laya fusion {mode} shadow only

[Service]
Type=simple
ExecStart={py} -B -m laya_cell.temporal_fusion_launch --config {cfg} --mode {mode}
WorkingDirectory={release}
Environment=PYTHONPATH={release}/learn
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=OMP_NUM_THREADS=2
Environment=OPENBLAS_NUM_THREADS=1
Environment=HF_HUB_OFFLINE=1
Environment=TRANSFORMERS_OFFLINE=1
Environment=USE_TF=0
UMask=0077
Nice=15
CPUQuota={"150%" if mode=="predictor" else "50%"}
CPUWeight=10
MemoryMax={"4G" if mode=="predictor" else "1G"}
TasksMax=96
NoNewPrivileges=yes
RestrictSUIDSGID=yes
RestrictAddressFamilies=AF_UNIX
LockPersonality=yes
Restart=on-failure
RestartSec=30
KillMode=control-group
TimeoutStopSec=20

[Install]
WantedBy=default.target
"""
  if unit.exists() and unit.read_text()!=text:
   if not update_owned or "temporal+Laya fusion" not in unit.read_text():raise ContractError("EXISTING_FUSION_UNIT_DIFFERS")
   subprocess.run(["systemctl","--user","stop",name],check=True)
   backup=cfgdir/(name+"."+sha_file(unit)+".before");write_once(backup,unit.read_bytes())
   tmp=unit.with_suffix(".next");tmp.write_text(text);tmp.chmod(0o600);os.replace(tmp,unit)
  elif not unit.exists():write_once(unit,text.encode(),0o600)
  units[mode]=name
 subprocess.run(["systemctl","--user","daemon-reload"],check=True)
 if start:subprocess.run(["systemctl","--user","enable","--now",*units.values()],check=True)
 after={name:show(name) for name in peers}
 if any(after[name]["MainPID"]!=value["MainPID"] for name,value in peers.items()):raise ContractError("EXISTING_SERVICE_PID_CHANGED")
 receipt={"schema":"heptatrader.temporal-fusion-install.v1","installed_at_ms":int(time.time()*1000),"release":str(release),
  "runtime_sha256":hashes,"config":str(cfg),"shadow_root":str(shadow),"plan":plan,"services":units,
  "candidate_id":candidate["candidate_id"],"laya_candidate_id":candidate["laya_candidate_id"],
  "existing_service_pids_unchanged":True,"broker_orders_sent":0,"automatic_trading":False}
 print(json.dumps(receipt,indent=2));return receipt

def main():
 p=argparse.ArgumentParser();p.add_argument("--laya-config",type=Path,required=True);p.add_argument("--fusion-candidate",type=Path,required=True);p.add_argument("--start",action="store_true");p.add_argument("--update-owned",action="store_true");a=p.parse_args();os.umask(0o077);install(a.laya_config,a.fusion_candidate,a.start,a.update_owned)
if __name__=="__main__":main()
