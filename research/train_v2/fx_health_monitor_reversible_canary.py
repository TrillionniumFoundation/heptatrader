"""Reversible, monitor-only source-health canary; no trading/permissions changes.

Intentionally narrow to the pre-inspected pocket4 user service, immutable
releases and fixed 2026-10-13 evidence. It refuses unfamiliar installations.
"""
from __future__ import annotations
import argparse, hashlib, json, os, shutil, stat, subprocess, sys, time
from pathlib import Path

BASE_SHA="63bcef1a6d8bc22e3c2fb10d1dcebc1457559538e6a959004e61eeb88c09c320"
NEW_SHA="0172c3470e7e0b821c39aac00eb077d18d436b1711c32d46f4a99bfd32199f12"
BASE_RELEASE="8dd7ddd-proof-c17f338"
NEW_RELEASE="health-monitor-15s-canary-20261013"
OVERRIDE_NAME="zzzz-health-monitor-canary-20261013.conf"
SERVICE="heptatrader-learning-source-health.service"
PERIOD_SHA="61995fcedc2993d7ac33d2ed86c9613068c5a3ecd0d1c377e612639cd5a0958f"

class Stop(Exception): pass
def need(x,why):
    if not x: raise Stop(why)
def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""):h.update(b)
    return h.hexdigest()
def cmd(*args):
    r=subprocess.run(args,check=True,capture_output=True,text=True,timeout=30)
    return r.stdout
def service_show():
    text=cmd("systemctl","--user","show",SERVICE,
             "-p","LoadState","-p","ActiveState","-p","SubState",
             "-p","MainPID","-p","CPUQuotaPerSecUSec","-p","MemoryMax",
             "-p","WorkingDirectory","-p","Environment",
             "-p","RestrictAddressFamilies","-p","NoNewPrivileges",
             "-p","Restart","-p","ExecStart")
    return dict(x.split("=",1) for x in text.splitlines() if "=" in x)
def status_assert(show,*,candidate):
    expected_release=NEW_RELEASE if candidate else BASE_RELEASE
    quota="500ms" if candidate else "250ms"
    need(show["ActiveState"]=="active"
         and show["SubState"]=="running" and int(show["MainPID"])>0,
         "SOURCE_MONITOR_NOT_ACTIVE")
    need(expected_release in show["WorkingDirectory"]
         and expected_release in show["Environment"],
         "MONITOR_RELEASE_SCOPE_MISMATCH")
    need(show["CPUQuotaPerSecUSec"]==quota,
         "UNEXPECTED_MONITOR_CPU_BUDGET")
    need(show["RestrictAddressFamilies"].strip()=="AF_UNIX"
         and show["NoNewPrivileges"]=="yes"
         and show["Restart"]=="on-failure",
         "MONITOR_SANDBOX_CHANGED")
    need("laya_cell.source_readiness" in show["ExecStart"]
         and ("--refresh-seconds 15" in show["ExecStart"])==candidate,
         "MONITOR_ARGUMENTS_CHANGED")
def paths():
    h=Path.home()
    releases=h/".local/share/heptatrader/laya-resident/releases"
    return {
        "base":releases/BASE_RELEASE,
        "candidate":releases/NEW_RELEASE,
        "dropin":h/".config/systemd/user/heptatrader-learning-source-health.service.d"/OVERRIDE_NAME,
        "config":h/".config/heptatrader-laya-resident/e96766cd75e3e5953cc775d97aa75a26a7446424da8a0807cd7b69825a705900.json",
        "period":h/".config/heptatrader/pnl-period.json",
        "health":h/".local/state/heptatrader/neuron-learning/source-readiness/61995fcedc2993d7ac33d2ed86c9613068c5a3ecd0d1c377e612639cd5a0958f/status.json",
    }
def module(root):
    return root/"learn/laya_cell/source_readiness.py"
def config_guard(p):
    c=json.loads(p["config"].read_text())
    need(c.get("automatic_trading") is False
         and c.get("period_sha256")==PERIOD_SHA
         and c.get("period_path")==str(p["period"])
         and sha(p["period"])==PERIOD_SHA,
         "PAPER_PERIOD_OR_AUTOTRADING_CHANGED")
def write_once(path,obj):
    raw=(json.dumps(obj,sort_keys=True,indent=2,allow_nan=False)+"\n").encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    d=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(d)
    finally:os.close(d)
def override(p):
    root=p["candidate"]
    return ("[Service]\n"
            "ExecStart=\n"
            "ExecStart=/usr/bin/python3 -B -m laya_cell.source_readiness "
            "--config "+str(p["config"])+" "
            "--output "+str(p["health"])+" --sandbox --refresh-seconds 15\n"
            "Environment=PYTHONPATH="+str(root/"learn")+":"+str(root/"scripts")+"\n"
            "WorkingDirectory="+str(root)+"\n"
            "CPUQuota=50%\n")
def frozen_prereqs(p):
    need(p["base"].is_dir()
         and sha(module(p["base"]))==BASE_SHA,
         "FROZEN_BASE_RELEASE_CHANGED")
    config_guard(p)
    s=service_show()
    need(str(p["health"]) in s["ExecStart"]
         and str(p["config"]) in s["ExecStart"],
         "SOURCE_MONITOR_CURRENT_INPUT_OUTPUT_PATH_MISMATCH")
    need(p["health"].is_file() and not p["health"].is_symlink()
         and stat.S_IMODE(p["health"].stat().st_mode)==0o600,
         "SOURCE_MONITOR_STATUS_NOT_PRIVATE_OR_MISSING")
    return s

def stage(root,src):
    p=paths(); root=Path(root)
    need(root.is_absolute() and not root.exists(),"EVIDENCE_ROOT_REUSED")
    need(p["dropin"].parent.is_dir()
         and not p["dropin"].exists()
         and not p["candidate"].exists(),"EXISTING_DROPIN_OR_RELEASE")
    need(sha(src)==NEW_SHA,"CANDIDATE_SOURCE_NOT_PINNED")
    s=frozen_prereqs(p);status_assert(s,candidate=False)
    root.mkdir(mode=0o700,parents=True)
    old=p["health"].read_bytes()
    fd=os.open(root/"status-before.json",os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,"wb") as f:f.write(old);f.flush();os.fsync(f.fileno())
    # Copy into a new private directory, never mutate the immutable source.
    shutil.copytree(p["base"],p["candidate"])
    dest=module(p["candidate"])
    dest.parent.chmod(0o755)
    dest.chmod(0o644)
    shutil.copyfile(src,dest)
    dest.chmod(0o444)
    dest.parent.chmod(0o555)
    need(sha(dest)==NEW_SHA and sha(module(p["base"]))==BASE_SHA,
         "STAGED_SOURCE_IDENTITY_MISMATCH")
    # Keep immutable application footprint except a single known module.
    write_once(root/"stage.json",{
        "schema":"heptatrader.fx-health-monitor-reversible-cutover.v1",
        "staged_at_ms":time.time_ns()//1000000,
        "original_service_show":s,
        "original_service_cat_sha256":hashlib.sha256(cmd("systemctl","--user","cat",SERVICE).encode()).hexdigest(),
        "status_before_sha256":hashlib.sha256(old).hexdigest(),
        "original_release_sha256":BASE_SHA,
        "candidate_module_sha256":NEW_SHA,
        "candidate_release":str(p["candidate"]),
        "override_sha256":hashlib.sha256(override(p).encode()).hexdigest(),
        "readiness_rule_unchanged":True,
        "original_paper_drivers_restart_allowed":False,
        "production_trading_authority":False,
        "model_promotion_authorized":False,
        "rollback_ready":True,
    })
    return {"state":"NEW_IMMUTABLE_MONITOR_CANDIDATE_STAGED","candidate_sha":NEW_SHA}
def activate(root):
    p=paths();root=Path(root)
    need((root/"stage.json").is_file(),"CANARY_STAGE_MISSING")
    original=json.loads((root/"stage.json").read_text())
    need(not p["dropin"].exists() and sha(module(p["candidate"]))==NEW_SHA
         and hashlib.sha256(override(p).encode()).hexdigest()==original["override_sha256"],
         "MONITOR_CANARY_NOT_EXACTLY_STAGED")
    s=frozen_prereqs(p);status_assert(s,candidate=False)
    # Cross-check original process has not been changed by another agent.
    need(s["WorkingDirectory"]==original["original_service_show"]["WorkingDirectory"]
         and s["Environment"]==original["original_service_show"]["Environment"],
         "PRODUCTION_MONITOR_CHANGED_SINCE_STAGING")
    fd=os.open(p["dropin"],os.O_EXCL|os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"w") as f:
        f.write(override(p));f.flush();os.fsync(f.fileno())
    try:
        cmd("systemctl","--user","daemon-reload")
        cmd("systemctl","--user","restart",SERVICE)
        s2=service_show()
        status_assert(s2,candidate=True)
    except BaseException:
        p["dropin"].unlink(missing_ok=True)
        try:
            cmd("systemctl","--user","daemon-reload")
            cmd("systemctl","--user","restart",SERVICE)
        except BaseException: pass
        raise
    write_once(root/"activation.json",{
        "schema":"heptatrader.fx-health-monitor-reversible-cutover.v1.activation.v1",
        "activated_at_ms":time.time_ns()//1000000,
        "override_sha256":sha(p["dropin"]),
        "new_service_show":s2,
        "production_trading_authority":False,
        "model_promotion_authorized":False,
    })
    return {"state":"CONTROLLED_SOURCE_MONITOR_ONLY_ACTIVATED",
            "main_pid":s2["MainPID"],
            "safety":"ROLLBACK_RETAINED"}
def restore(root):
    p=paths();root=Path(root)
    need((root/"stage.json").is_file(),"NOT_ORIGINALLY_STAGED")
    recorded=json.loads((root/"stage.json").read_text())
    need(p["dropin"].is_file()
         and sha(p["dropin"])==recorded["override_sha256"],
         "UNKNOWN_OVERRIDE_CANNOT_REMOVE")
    need(sha(module(p["base"]))==BASE_SHA,"BASE_RELEASE_CHANGED")
    p["dropin"].unlink()
    cmd("systemctl","--user","daemon-reload")
    cmd("systemctl","--user","restart",SERVICE)
    s=service_show()
    status_assert(s,candidate=False)
    write_once(root/"rollback.json",{
        "schema":"heptatrader.fx-health-monitor-reversible-cutover.v1.rollback.v1",
        "restored_at_ms":time.time_ns()//1000000,
        "service_show":s,
        "base_module_sha256":sha(module(p["base"])),
        "canary_override_removed":not p["dropin"].exists(),
        "candidate_retained_for_forensics":p["candidate"].is_dir(),
        "production_trading_authority":False,"model_promotion_authorized":False,
    })
    return {"state":"ORIGINAL_PRODUCTION_MONITOR_RESTORED",
            "pid":s["MainPID"]}
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--mode",choices=["stage","activate","restore","inspect"],required=True)
    parser.add_argument("--evidence-root",type=Path,required=True)
    parser.add_argument("--new-source",type=Path)
    a=parser.parse_args()
    try:
        if a.mode=="stage":
            need(a.new_source is not None,"SOURCE_REQUIRED")
            result=stage(a.evidence_root,a.new_source)
        elif a.mode=="activate":result=activate(a.evidence_root)
        elif a.mode=="restore":result=restore(a.evidence_root)
        else:
            p=paths();s=service_show()
            result={"state":"CURRENT_SOURCE_MONITOR_READONLY",
                    "active":s["ActiveState"],"main_pid":s["MainPID"],
                    "working_directory":s["WorkingDirectory"],
                    "candidate_code_present":p["candidate"].is_dir(),
                    "candidate_override_present":p["dropin"].exists(),
                    "original_module_sha256":sha(module(p["base"])),
                    "trade_authority":False}
        print(json.dumps(result,sort_keys=True))
    except (Stop,subprocess.CalledProcessError,OSError) as exc:
        print(json.dumps({"state":"HOLD_CANARY_PRECONDITION","reason":str(exc),
                          "trade_authority":False}),file=sys.stderr)
        raise SystemExit(2) from exc
if __name__=="__main__":main()
