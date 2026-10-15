"""Frozen-campaign-safe, reversible Pocket4 monitor-only source-health release.

Reuse the already tested 2026-10-13 cutover primitives. The 2026-10-15
verified-cache code is staged already. Source merge and file staging do NOT
permit activation before the original 24 frozen windows close naturally.
No order, strategy, operator kill-switch or model-promotion interface.
"""
from __future__ import annotations
import argparse, collections, hashlib, json, os, stat, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

BASE_SHA="63bcef1a6d8bc22e3c2fb10d1dcebc1457559538e6a959004e61eeb88c09c320"
NEW_SHA="8cbedb6a5f792891f0141f7b2ae0da91a7738080cd14c45995436ed55473c309"
CACHE_SHA="a125a88765f3b212b709ee817fce07d8dded504d93139b0ab6da4db3833f0bc7"
BASE_RELEASE="8dd7ddd-proof-c17f338"
NEW_RELEASE="fx-cache-suffix-a125a887-20261015"
OVERRIDE_NAME="zzzz-health-monitor-verified-cache-20261015.conf"
FINAL_UTC_MS=int(datetime(2026,10,15,17,38,tzinfo=timezone.utc).timestamp()*1000)
TRADING_UNITS=("heptatrader-execution.service","heptatrader-paper-driver.service",
               "heptatrader-crypto-driver.service","heptatrader-laya-trainer.service")
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
    need(show["CPUQuotaPerSecUSec"]==quota and
         show["MemoryMax"]=="402653184",
         "UNEXPECTED_MONITOR_RESOURCE_BUDGET")
    need(show["RestrictAddressFamilies"].strip()=="AF_UNIX"
         and show["NoNewPrivileges"]=="yes"
         and show["Restart"]=="on-failure",
         "MONITOR_SANDBOX_CHANGED")
    need("laya_cell.source_readiness" in show["ExecStart"]
         and ("--refresh-seconds 15" in show["ExecStart"])==candidate
         and ("--verified-sealed-cache" in show["ExecStart"])==candidate,
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
        "frozen":h/".local/state/heptatrader/neuron-learning/frozen-price-campaign-f7f18c-20261008",
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
            "--output "+str(p["health"])+" --sandbox --verified-sealed-cache --refresh-seconds 15\n"
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

def candidate_inventory(p):
    """Allow precisely the reviewed module replacement plus the verified cache."""
    def inventory(root):
        need(root.is_dir() and not root.is_symlink(), "RELEASE_DIRECTORY_INVALID")
        results={}
        for entry in root.rglob("*"):
            need(not entry.is_symlink(), "RELEASE_SYMLINK_REFUSED")
            if entry.is_file():
                need(entry.stat().st_uid==os.geteuid(), "RELEASE_FILE_OWNER_CHANGED")
                results[str(entry.relative_to(root))]=sha(entry)
        return results
    before=inventory(p["base"]);after=inventory(p["candidate"])
    source="learn/laya_cell/source_readiness.py"
    cache="learn/laya_cell/verified_sealed_readiness_cache.py"
    need(set(after)-set(before)=={cache}
         and set(before)-set(after)==set()
         and {k for k in before if before[k]!=after[k]}=={source}
         and before[source]==BASE_SHA and after[source]==NEW_SHA
         and after[cache]==CACHE_SHA,
         "STAGED_RELEASE_FOOTPRINT_CHANGED")
    return hashlib.sha256(json.dumps(after,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def frozen_terminal(p,now_ms=None):
    """No change to any original slot; never infer terminality from timer state."""
    now_ms=time.time_ns()//1000000 if now_ms is None else now_ms
    need(now_ms>=FINAL_UTC_MS,"ORIGINAL_CAMPAIGN_FUTURE_WINDOW_PENDING")
    root=p["frozen"];done=root/"complete.json";status=root/"status.json";plan=root/"plan.json"
    need(all(x.is_file() and not x.is_symlink() for x in (done,status,plan)),
         "ORIGINAL_CAMPAIGN_NOT_TERMINAL")
    a=json.loads(done.read_bytes());b=json.loads(status.read_bytes());c=json.loads(plan.read_bytes())
    slots=b.get("slots",[])
    need(type(slots) is list and len(slots)==24 and
         all(type(x) is dict and x.get("state") in ("OBSERVED","CENSORED") for x in slots) and
         b.get("planned")==24 and b.get("finalized")==24 and
         type(b.get("observed")) is int and type(b.get("censored")) is int and
         b["observed"]+b["censored"]==24 and
         b.get("phase")=="COMPLETE_PENDING_REVIEW" and
         b.get("automatic_promotion") is False and
         b.get("trade_authority") is False and
         a.get("campaign_id")==b.get("campaign_id")==c.get("campaign_id"),
         "ORIGINAL_CAMPAIGN_TERMINAL_EVIDENCE_INVALID")
    return {"campaign_id":b["campaign_id"],"complete_sha256":sha(done),
            "status_sha256":sha(status),"plan_sha256":sha(plan),
            "observed":b["observed"],"censored":b["censored"]}


def trading_pids():
    out={}
    for name in TRADING_UNITS:
        line=cmd("systemctl","--user","show",name,"-p","MainPID","-p","ActiveState")
        values=dict(x.split("=",1) for x in line.splitlines() if "=" in x)
        need(values.get("ActiveState")=="active" and int(values.get("MainPID","0"))>0,
             "PROTECTED_SERVICE_UNHEALTHY:"+name)
        out[name]=values["MainPID"]
    return out

def stage(root,src=None):
    p=paths();root=Path(root)
    need(root.is_absolute() and not root.exists(),"EVIDENCE_ROOT_REUSED")
    need(p["dropin"].parent.is_dir() and not p["dropin"].exists(),
         "EXISTING_MONITOR_OVERRIDE")
    if src is not None:need(Path(src).resolve()==module(p["candidate"]).resolve(),
                            "CANDIDATE_SOURCE_PATH_MISMATCH")
    inventory_hash=candidate_inventory(p)
    s=frozen_prereqs(p);status_assert(s,candidate=False)
    protected=trading_pids()
    # Staging is harmless before the original experiment ends: it creates
    # evidence only, never a systemd drop-in or restart.
    root.mkdir(mode=0o700,parents=True)
    old=p["health"].read_bytes()
    fd=os.open(root/"status-before.json",os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:f.write(old);f.flush();os.fsync(f.fileno())
    write_once(root/"stage.json",{
        "schema":"heptatrader.fx-health-monitor-reversible-cutover.v2",
        "staged_at_ms":time.time_ns()//1000000,
        "original_service_show":s,
        "original_service_cat_sha256":hashlib.sha256(cmd("systemctl","--user","cat",SERVICE).encode()).hexdigest(),
        "status_before_sha256":hashlib.sha256(old).hexdigest(),
        "original_campaign_plan_sha256":sha(p["frozen"]/"plan.json"),
        "original_release_sha256":BASE_SHA,
        "candidate_module_sha256":NEW_SHA,
        "candidate_cache_sha256":CACHE_SHA,
        "candidate_inventory_sha256":inventory_hash,
        "candidate_release":str(p["candidate"]),
        "override_sha256":hashlib.sha256(override(p).encode()).hexdigest(),
        "protected_pids_before":protected,
        "original_campaign_terminal_required":True,
        "readiness_rule_unchanged":True,
        "original_paper_drivers_restart_allowed":False,
        "production_trading_authority":False,
        "model_promotion_authorized":False,
        "rollback_ready":True,
    })
    return {"state":"STAGED_ONLY_NO_SERVICE_CHANGE",
            "candidate_sha":NEW_SHA,"cache_sha":CACHE_SHA,
            "original_campaign_terminal":(p["frozen"]/"complete.json").exists(),
            "trade_authority":False}

def activate(root):
    p=paths();root=Path(root)
    need((root/"stage.json").is_file(),"CANARY_STAGE_MISSING")
    original=json.loads((root/"stage.json").read_bytes())
    # This MUST precede any drop-in or daemon-reload, even on forceful retries.
    terminal=frozen_terminal(p)
    need(terminal["plan_sha256"]==original["original_campaign_plan_sha256"],
         "ORIGINAL_CAMPAIGN_PLAN_CHANGED")
    need(not p["dropin"].exists() and
         candidate_inventory(p)==original["candidate_inventory_sha256"] and
         hashlib.sha256(override(p).encode()).hexdigest()==original["override_sha256"],
         "MONITOR_CANDIDATE_NOT_EXACTLY_STAGED")
    s=frozen_prereqs(p);status_assert(s,candidate=False)
    # Do not replace another operator's unit or new effective drop-in.
    need(s["WorkingDirectory"]==original["original_service_show"]["WorkingDirectory"]
         and s["Environment"]==original["original_service_show"]["Environment"]
         and hashlib.sha256(cmd("systemctl","--user","cat",SERVICE).encode()).hexdigest()==original["original_service_cat_sha256"],
         "PRODUCTION_MONITOR_CHANGED_SINCE_STAGING")
    protected=trading_pids()
    need(not (root/"activation.json").exists(),"CUTOVER_ALREADY_ACTIVATED")
    fd=os.open(p["dropin"],os.O_EXCL|os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"w") as f:
        f.write(override(p));f.flush();os.fsync(f.fileno())
    try:
        cmd("systemctl","--user","daemon-reload")
        cmd("systemctl","--user","restart",SERVICE)
        s2=service_show()
        status_assert(s2,candidate=True)
        need(s2["MainPID"]!=s["MainPID"],"MONITOR_RESTART_IDENTITY_UNCHANGED")
        need(trading_pids()==protected,"TRADING_OR_LAYA_PID_CHANGED_DURING_MONITOR_CUTOVER")
    except BaseException:
        # Roll back only OUR exact bytes. Never delete a concurrent override.
        if p["dropin"].is_file() and sha(p["dropin"])==original["override_sha256"]:
            p["dropin"].unlink()
            try:
                cmd("systemctl","--user","daemon-reload")
                cmd("systemctl","--user","restart",SERVICE)
            except BaseException:pass
        raise
    write_once(root/"activation.json",{
        "schema":"heptatrader.fx-health-monitor-reversible-cutover.v2.activation",
        "activated_at_ms":time.time_ns()//1000000,
        "override_sha256":sha(p["dropin"]),
        "new_service_show":s2,
        "original_terminal":terminal,
        "protected_pids":protected,
        "production_trading_authority":False,
        "model_promotion_authorized":False,
    })
    return {"state":"CONTROLLED_SOURCE_MONITOR_ONLY_ACTIVATED",
            "main_pid":s2["MainPID"],"safety":"ROLLBACK_RETAINED"}


def audit_live(root,minimum_cycles=24):
    p=paths();root=Path(root)
    need((root/"activation.json").is_file(),"MONITOR_ACTIVATION_RECEIPT_MISSING")
    activated=json.loads((root/"activation.json").read_bytes())
    need(p["dropin"].is_file() and sha(p["dropin"])==activated["override_sha256"],
         "MONITOR_EFFECTIVE_OVERRIDE_CHANGED")
    show=service_show();status_assert(show,candidate=True)
    need(show["MainPID"]==activated["new_service_show"]["MainPID"],
         "CANDIDATE_SERVICE_RESTARTED")
    need(trading_pids()==activated["protected_pids"],
         "TRADING_OR_LAYA_PID_CHANGED")
    text=cmd("journalctl","--user","-u",SERVICE,"--since",
             "@"+str(activated["activated_at_ms"]//1000),"-o","cat","--no-pager")
    seq=[]
    for line in text.splitlines():
        if not line.startswith("{"):continue
        try:row=json.loads(line)
        except ValueError:continue
        if row.get("schema")=="heptatrader.source-readiness-cadence-lab.v1":seq.append(row)
    need(len(seq)>=minimum_cycles,"MONITOR_CADENCE_NOT_YET_PROVEN")
    seq=seq[:minimum_cycles]
    need([x.get("cycle") for x in seq]==list(range(1,minimum_cycles+1)),
         "SOURCE_CYCLE_IDENTITY_GAP")
    for x in seq:
        need(x.get("trade_authority") is False and x.get("orders_sent")==0
             and x.get("state") in ("READY_FOR_REVIEW_NOT_AUTHORIZATION","HOLD_NEW_FUSION_WINDOW")
             and type(x.get("source_asof_ms")) is int
             and type(x.get("published_ms")) is int
             and 0 <= x["published_ms"]-x["source_asof_ms"] < 30000,
             "MONITOR_SOURCE_AT_PUBLICATION_STALE_OR_UNTRUSTED")
        need(x.get("published_source_age_ms")==x["published_ms"]-x["source_asof_ms"],
             "MONITOR_AGE_DIAGNOSTIC_MISMATCH")
        if x["state"]=="READY_FOR_REVIEW_NOT_AUTHORIZATION":
            need(type(x.get("usable")) is int and x["usable"]>=20,
                 "MONITOR_FALSE_READY_AT_PUBLICATION")
    gaps=[b["published_ms"]-a["source_asof_ms"] for a,b in zip(seq,seq[1:])]
    need(all(0<g<30000 for g in gaps),"MONITOR_SLA_BREACHED_BETWEEN_PUBLICATIONS")
    status=json.loads(p["health"].read_bytes())
    need(status.get("schema")=="heptatrader.quote-readiness-status.v1"
         and status.get("period_sha256")==PERIOD_SHA
         and status.get("trading_authority") is False
         and status.get("automatic_trial_registration") is False
         and status.get("orders_sent")==0
         and type(status.get("probes")) is list and len(status["probes"])==24,
         "MONITOR_OUTPUT_NOT_ORIGINAL_READONLY_SCHEMA")
    counts=dict(collections.Counter(x["status"] for x in status["probes"]))
    need(status.get("counts")==counts and
         status.get("usable")==counts.get("USABLE",0),
         "MONITOR_OUTPUT_PROBE_COUNT_MISMATCH")
    if status.get("state")=="READY_FOR_REVIEW_NOT_AUTHORIZATION":
        need(status["usable"]>=20 and type(status.get("latest_quote_age_ms")) is int
             and 0<=status["latest_quote_age_ms"]<=5000 and not status.get("reasons"),
             "FALSE_SOURCE_READY_REJECTED")
    return {"schema":"heptatrader.fx-health-monitor-release-audit.v1",
            "at_ms":time.time_ns()//1000000,"cycles":len(seq),
            "max_publish_source_age_ms":max(x["published_source_age_ms"] for x in seq),
            "max_before_next_publish_ms":max(gaps),
            "original_source_HOLD_preserved":status.get("state")=="HOLD_NEW_FUSION_WINDOW",
            "source_health_status_sha256":sha(p["health"]),
            "protected_pids_unchanged":True,"orders_sent":0,"trade_authority":False}


def cutover(root,*,minimum_cycles=24,timeout_seconds=520):
    activate(root)
    try:
        deadline=time.monotonic()+timeout_seconds
        while True:
            try:
                evidence=audit_live(root,minimum_cycles=minimum_cycles)
                write_once(Path(root)/"acceptance.json",evidence)
                return {"state":"MONITOR_ONLY_ROLLOUT_ACCEPTED",
                        "receipt_sha256":sha(Path(root)/"acceptance.json"),
                        "cycles":evidence["cycles"],"orders_sent":0,"trade_authority":False}
            except Stop as exc:
                # Only incomplete cadence may mature; a genuine unsafe
                # observation fails immediately and triggers rollback.
                if str(exc)!="MONITOR_CADENCE_NOT_YET_PROVEN" or time.monotonic()>deadline:
                    raise
                time.sleep(3)
    except BaseException:
        restore(root)
        raise

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
    parser.add_argument("--mode",choices=["stage","cutover","restore","inspect"],required=True)
    parser.add_argument("--evidence-root",type=Path,required=True)
    parser.add_argument("--new-source",type=Path)
    a=parser.parse_args()
    try:
        if a.mode=="stage":
            result=stage(a.evidence_root,a.new_source)
        elif a.mode=="cutover":result=cutover(a.evidence_root)
        elif a.mode=="restore":result=restore(a.evidence_root)
        else:
            p=paths();s=service_show()
            result={"state":"CURRENT_SOURCE_MONITOR_READONLY",
                    "active":s["ActiveState"],"main_pid":s["MainPID"],
                    "working_directory":s["WorkingDirectory"],
                    "candidate_code_present":p["candidate"].is_dir(),
                    "candidate_override_present":p["dropin"].exists(),
                    "original_module_sha256":sha(module(p["base"])),
                    "candidate_inventory_sha256":candidate_inventory(p),
                    "original_campaign_complete":(p["frozen"]/"complete.json").exists(),
                    "trade_authority":False}
        print(json.dumps(result,sort_keys=True))
    except (Stop,subprocess.CalledProcessError,OSError) as exc:
        print(json.dumps({"state":"HOLD_CANARY_PRECONDITION","reason":str(exc),
                          "trade_authority":False}),file=sys.stderr)
        raise SystemExit(2) from exc
if __name__=="__main__":main()
