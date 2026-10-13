"""Seal live monitor-only cutover evidence before reverting to old source service."""
from __future__ import annotations
import collections, hashlib, json, os, stat, subprocess, sys, time
from pathlib import Path

EXPECTED={
 "heptatrader-execution.service":"4111860",
 "heptatrader-paper-driver.service":"724712",
 "heptatrader-crypto-driver.service":"724713",
 "heptatrader-laya-trainer.service":"3755315",
}
SCHEMA="heptatrader.source-readiness-cadence-lab.v1"
def cmd(*argv):
    return subprocess.check_output(argv,text=True,timeout=12)
def hash_(blob):return hashlib.sha256(blob).hexdigest()
def save(path,raw):
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    d=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(d)
    finally:os.close(d)
def require(cond,msg):
    if not cond:raise ValueError(msg)
def main():
    root=Path(sys.argv[1]);path=Path(sys.argv[2])
    require(root.is_dir() and stat.S_IMODE(root.stat().st_mode)==0o700,"PRIVATE_EVIDENCE_REQUIRED")
    s=json.loads((root/"activation.json").read_bytes())
    require(s["schema"]=="heptatrader.fx-health-monitor-reversible-cutover.v1.activation.v1","ACTIVATION_MISSING")
    lines=cmd("journalctl","--user","-u","heptatrader-learning-source-health.service",
              "--since","2026-10-13 16:30:00","-o","cat","--no-pager").splitlines()
    seq=[]
    for line in lines:
        if not line.startswith("{"):continue
        try:item=json.loads(line)
        except json.JSONDecodeError:continue
        if item.get("schema")==SCHEMA:
            seq.append(item)
    require(len(seq)>=4 and [x["cycle"] for x in seq]==list(range(1,len(seq)+1)),
            "MIN_FOUR_MONOTONIC_REAL_SERVICE_CYCLES")
    require(all(x["state"] in ("READY_FOR_REVIEW_NOT_AUTHORIZATION","HOLD_NEW_FUSION_WINDOW")
              and x["trade_authority"] is False and x["orders_sent"]==0
              and x["published_ms"]-x["source_asof_ms"]==x["published_source_age_ms"]
              and 0<=x["published_source_age_ms"]<30000 for x in seq),
              "REAL_MONITOR_SOURCE_AGE_FAIL")
    pairs=[seq[i+1]["published_ms"]-seq[i]["source_asof_ms"] for i in range(len(seq)-1)]
    require(max(pairs)<30000,"REAL_MONITOR_BETWEEN_PUBLICATIONS_STALE")
    st=path.stat()
    require(st.st_uid==os.geteuid() and st.st_mode&0o077==0,"STATUS_NOT_PRIVATE")
    raw=path.read_bytes()
    health=json.loads(raw)
    original_health=dict(collections.Counter(x["status"] for x in health["probes"]))
    require(health["schema"]=="heptatrader.quote-readiness-status.v1"
            and len(health["probes"])==24 and original_health==health["counts"]
            and health["period_sha256"]=="61995fcedc2993d7ac33d2ed86c9613068c5a3ecd0d1c377e612639cd5a0958f"
            and health["automatic_trial_registration"] is False
            and health["trading_authority"] is False and health["orders_sent"]==0,
            "SOURCE_HEALTH_CONTRACT_CHANGED")
    require(health["source_prefix_digest"]==hash_(json.dumps(
        health["source_prefixes"],sort_keys=True,separators=(",",":"),allow_nan=False).encode()),
        "ORIGINAL_SOURCE_PREFIX_DIGEST_CHANGED")
    observed_pids={}
    for service,pid in EXPECTED.items():
        actual=cmd("systemctl","--user","show",service,"-p","MainPID").strip().split("=",1)[-1]
        observed_pids[service]=actual
        require(actual==pid,"TRADING_OR_LAYA_MAIN_PROCESS_RESTARTED_"+service)
    save(root/"live-status-candidate.json",raw)
    report={
       "schema":"heptatrader.fx-health-monitor-controlled-canary-live.v1",
       "at_ms":time.time_ns()//1000000,
       "original_source_health_sha256":hash_(raw),
       "original_source_health_counts":original_health,
       "original_source_health_state":health["state"],
       "candidate_live_monitor_pid":s["new_service_show"]["MainPID"],
       "cycles":seq,
       "completed_actual_cycles":len(seq),
       "max_source_published_age_ms":max(x["published_source_age_ms"] for x in seq),
       "max_source_age_before_next_publication_ms":max(pairs),
       "original_paper_trading_and_trainer_pids_unchanged":True,
       "process_identities_after":observed_pids,
       "post_activation_old_timers_untouched":True,
       "model_promotion_authorized":False,
       "execution_or_order_authority":False,
       "cutover_qualified_for_future_permanent_deployment":False,
    }
    save(root/"live-canary-audit.json",json.dumps(
        report,sort_keys=True,indent=2,allow_nan=False).encode()+b"\n")
    print(json.dumps({"cycle_count":len(seq),
          "worst_publish_age":report["max_source_published_age_ms"],
          "worst_between_publishes":report["max_source_age_before_next_publication_ms"],
          "healthy":health["state"],"original_service_pids_unchanged":True,
          "receipt_sha256":hash_((root/"live-canary-audit.json").read_bytes())},
          sort_keys=True))
if __name__=="__main__":main()
