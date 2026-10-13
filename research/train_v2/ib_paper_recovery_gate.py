"""Read-only PAPER recovery gate: no broker requests, orders or restarts.

A local source-health snapshot and an ACTIVE systemd unit are not proof of a
working IB quote callback, an OOS profitable model, or complete account NAV.
This audit records authoritative local failure *without* changing the broker
session, sources, drivers, services, trades, positions, permissions or ledger.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

SCHEMA="heptatrader.ib-paper-observed-recovery-gate.v1"
MIN_STABILITY_MS=5*60*1000
MAX_SOURCE_STATUS_AGE_MS=30000
MAX_SOURCE_QUOTE_AGE_MS=5000
SAFE_EXEC_FAILURES=(
    "IB_PAPER_QUOTE_ADMISSION_CALLBACK_UNSAFE",
    "IB_PAPER_AUTHORITATIVE_SNAPSHOTS_TIMEOUT",
    "IB_PAPER_ADAPTER_CONNECT_FAILED",
)
# Source-reported IB 10197 is historical, NOT proof of the current root cause.
IB_10197="IB_10197_SOURCE_RECORDED_COMPETING_SESSION"
STATUS_UNVERIFIED="NO_AUTHORITATIVE_SNAPSHOT"
SOURCE_RULE={"schema":"heptatrader.quote-readiness.v1",
    "input_probes":24,"spacing_ms":60000,"horizon_ms":300000,
    "input_grace_ms":60000,"endpoint_tolerance_ms":5000,
    "path_gap_max_ms":30000,"minimum_usable":20,"maximum_missing":4,
    "source_ttl_ms":5000,
    "statistics_scope":"OVERLAPPING_OPERATIONAL_COVERAGE_NOT_PERFORMANCE"}

class GateHold(ValueError):
    pass

def require(cond, code):
    if not cond: raise GateHold(code)

def canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(",",":"),
        allow_nan=False).encode()

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def strict(raw):
    def pairs(items):
        out={}
        for k,v in items:
            require(k not in out,"DUPLICATE_JSON_KEY")
            out[k]=v
        return out
    return json.loads(raw,object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(GateHold("NONFINITE_JSON")))

def safe_file(path,*,max_size=12*1024*1024):
    """A missing/replaced file is a HOLD, never permission to synthesize it."""
    p=Path(path)
    require(p.is_absolute() and not p.is_symlink(),"SOURCE_PATH_INVALID")
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        st=os.fstat(fd)
        require(stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid()
                and st.st_nlink==1 and stat.S_IMODE(st.st_mode)&0o077==0
                and 0<st.st_size<=max_size,"SOURCE_FILE_NOT_PRIVATE")
        buf=bytearray()
        while len(buf)<st.st_size:
            piece=os.read(fd,min(1024*1024,st.st_size-len(buf)))
            require(bool(piece),"SOURCE_SHORT_READ")
            buf.extend(piece)
        after=os.fstat(fd)
        require((st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns)==(
                after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns),
                "SOURCE_CHANGED_DURING_READ")
        raw=bytes(buf)
        return strict(raw),digest(raw)
    finally:
        os.close(fd)

def parse_systemd(output):
    props={}
    for line in output.splitlines():
        if "=" not in line: continue
        k,v=line.split("=",1)
        require(k not in props,"DUPLICATED_SERVICE_PROPERTY")
        props[k]=v
    require(props.get("ActiveState") in
            ("active","activating","inactive","failed","deactivating"),
            "SERVICE_STATE_MISSING")
    require(props.get("SubState") is not None,
            "SERVICE_SUBSTATE_MISSING")
    pid=int(props.get("MainPID") or "0")
    entered=props.get("ActiveEnterTimestampMonotonic")
    return {"active_state":props["ActiveState"],
            "sub_state":props["SubState"],
            "restart_count":int(props.get("NRestarts") or "0"),
            "main_pid_present":pid>0,
            "main_pid":pid,
            "invocation_id":props.get("InvocationID") or None,
            "active_enter_monotonic_usec":(
                int(entered) if entered and entered.isdecimal() else None),
            "result":props.get("Result")}

def count_incidents(json_lines):
    counts=Counter()
    for line in json_lines.splitlines():
        if not line.strip():continue
        try:
            obj=strict(line.encode())
            message=obj.get("MESSAGE","")
        except (ValueError,GateHold,json.JSONDecodeError):
            continue
        if type(message) is not str:continue
        for code in SAFE_EXEC_FAILURES:
            if code in message and "runtime startup rejected" in message:
                counts[code]+=1
        if "10197" in message and ("error" in message.lower()
                                 or "fatal" in message.lower()):
            counts[IB_10197]+=1
    return dict(sorted(counts.items()))

def assess(now_ms,execution,other_services,pnl,health,
           period_sha256,prior=None,journal_failures=None):
    """Never returns permission to trade or production promotion."""
    require(type(now_ms) is int and now_ms>0,"CLOCK_INVALID")
    require(type(execution) is dict and type(other_services) is dict,
            "SERVICE_SHAPE")
    reasons=[]
    if (execution.get("active_state")!="active"
            or execution.get("sub_state")!="running"
            or execution.get("main_pid_present") is not True):
        reasons.append("EXECUTION_NOT_RUNNING")
    restart=execution.get("restart_count")
    if type(restart) is not int or restart<0:
        reasons.append("EXECUTION_RESTART_COUNTER_UNTRUSTED")
    # NRestarts alone is insufficient: explicit stop/start and host reboot
    # may reset that counter even when the broker session was replaced.
    pid=execution.get("main_pid")
    invocation=execution.get("invocation_id")
    active_since=execution.get("active_enter_monotonic_usec")
    execution_identity_valid=(
        type(pid) is int and pid>0
        and type(invocation) is str and len(invocation)==32
        and all(c in "0123456789abcdef" for c in invocation)
        and type(active_since) is int and active_since>0)
    if not execution_identity_valid:
        reasons.append("EXECUTION_PROCESS_IDENTITY_NOT_ATTESTED")
    if prior is None:
        reasons.append("NO_COMPLETED_FIVE_MIN_STABILITY_WITNESS")
    else:
        sample=prior.get("observed_at_ms")
        previous=prior.get("execution")
        if (type(sample) is not int or sample>=now_ms
                or now_ms-sample<MIN_STABILITY_MS
                or type(previous) is not dict):
            reasons.append("INSUFFICIENT_STABILITY_OBSERVATION")
        elif (type(restart) is not int or
              previous.get("restart_count")!=restart or
              previous.get("active_state")!="active"):
            reasons.append("EXECUTION_RESTARTED_SINCE_PRIOR_SAMPLE")
        elif (not execution_identity_valid or
              previous.get("main_pid")!=pid or
              previous.get("invocation_id")!=invocation or
              previous.get("active_enter_monotonic_usec")!=active_since):
            reasons.append("EXECUTION_PROCESS_CHANGED_SINCE_PRIOR_SAMPLE")
    for unit,state in other_services.items():
        if state.get("active_state")!="active":
            reasons.append("DEPENDENT_SERVICE_NOT_ACTIVE:"+unit)
    if pnl is None:
        reasons.append("MISSING_CURRENT_PNL_SNAPSHOT")
    else:
        if pnl.get("schema")!="heptatrader.pnl.v1":
            reasons.append("UNRECOGNIZED_PNL_SCHEMA")
        if (type(pnl.get("observed_at_ms")) is not int
            or not 0<=now_ms-pnl["observed_at_ms"]<=MAX_SOURCE_STATUS_AGE_MS):
            reasons.append("PNL_SNAPSHOT_STALE")
        if (pnl.get("complete") is not True
            or (pnl.get("totals") or {}).get("net_total_pnl") is None):
            reasons.append("STRATEGY_PNL_MARK_INCOMPLETE")
        if pnl.get("account_complete") is not True:
            reasons.append("ACCOUNT_NAV_NOT_RECONCILED")
    status_summary=None
    if health is None:
        reasons.append("MISSING_ORIGINAL_SOURCE_STATUS")
    else:
        status_summary={
            "state":health.get("state"),"at_ms":health.get("at_ms"),
            "usable":health.get("usable"),"unusable":health.get("unusable"),
            "latest_quote_age_at_source_ms":health.get("latest_quote_age_ms")}
        if health.get("schema")!="heptatrader.quote-readiness-status.v1":
            reasons.append("SOURCE_HEALTH_SCHEMA_UNTRUSTED")
        if health.get("rule")!=SOURCE_RULE:
            reasons.append("SOURCE_HEALTH_RULE_CHANGED")
        probes=health.get("probes")
        counts=health.get("counts")
        if (type(probes) is not list or len(probes)!=24
                or not all(type(x) is dict and type(x.get("status")) is str
                           for x in probes)
                or type(counts) is not dict
                or dict(Counter(p["status"] for p in probes))!=counts
                or counts.get("USABLE",0)!=health.get("usable")
                or 24-counts.get("USABLE",0)!=health.get("unusable")):
            reasons.append("SOURCE_HEALTH_PROBES_OR_COUNTS_INVALID")
        prefixes=health.get("source_prefixes")
        if (type(prefixes) is not list or
                health.get("source_prefix_digest")!=digest(canonical(prefixes))):
            reasons.append("SOURCE_HEALTH_PREFIX_CHAIN_UNATTESTED")
        if health.get("trading_authority") is not False:
            reasons.append("SOURCE_HEALTH_UNEXPECTED_AUTHORITY")
        if health.get("period_sha256")!=period_sha256:
            reasons.append("SOURCE_PERIOD_ID_MISMATCH")
        at=health.get("at_ms")
        src=health.get("latest_source_quote_ms")
        if type(at) is not int or not 0<=now_ms-at<=MAX_SOURCE_STATUS_AGE_MS:
            reasons.append("SOURCE_STATUS_NOT_CONTEMPORANEOUS")
        if type(src) is not int or not 0<=now_ms-src<=MAX_SOURCE_QUOTE_AGE_MS:
            reasons.append("ORIGINAL_IB_QUOTE_UNAVAILABLE_OR_EXPIRED")
        if (health.get("state")!="READY_FOR_REVIEW_NOT_AUTHORIZATION"
                or type(health.get("usable")) is not int
                or health.get("usable")<20
                or type(health.get("unusable")) is not int
                or health.get("unusable")>4
                or health.get("automatic_trial_registration") is not False):
            reasons.append("SOURCE_READINESS_HOLD")
    journal_failures=journal_failures or {}
    if any(journal_failures.get(code,0) for code in SAFE_EXEC_FAILURES):
        reasons.append("RECENT_EXECUTION_QUOTE_ADMISSION_FAILURES")
    # The research-only verdict never claims root/signer/Owner/Reward.
    reasons.append("MISSING_NATIVE_OWNER_WITNESS_AND_ACCOUNT_ECONOMICS")
    return {
        "schema":SCHEMA,
        "observed_at_ms":now_ms,
        "state":"PAPER_RESTORATION_AND_PRODUCTION_RELEASE_HOLD",
        "hold_reasons":sorted(set(reasons)),
        "execution":execution,
        "dependencies":other_services,
        "source_health":status_summary,
        "strategy_pnl_complete":(pnl or {}).get("complete") is True,
        "account_pnl_complete":(pnl or {}).get("account_complete") is True,
        "journal_failure_reason_counts":journal_failures,
        "historical_ib_10197_is_not_present_session_root_proof":True,
        "independent_broker_quote_signed":False,
        "model_prospective_efficacy_proven":False,
        "paper_order_submission_authorized_by_this_audit":False,
        "production_root_or_owner_witness_admitted":False,
        "model_promotion_allowed":False,
        "broker_queries_or_orders_sent_by_auditor":0,
        "driver_or_service_mutated":False,
    }

def command(argv,max_bytes=512000):
    result=subprocess.run(argv,text=True,capture_output=True,timeout=12,
                          check=False)
    require(result.returncode==0 and len(result.stdout.encode())<=max_bytes,
            "LOCAL_READONLY_AUDIT_COMMAND_FAILED")
    return result.stdout

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--output",required=True,type=Path)
    p.add_argument("--prior",type=Path)
    p.add_argument("--journal-since",default="2026-10-13 19:50:00")
    args=p.parse_args()
    now=time.time_ns()//1000000
    home=Path.home()
    period_path=home/".config/heptatrader/pnl-period.json"
    # Period bytes are scope identity only; output never exposes account ids.
    period,_=safe_file(period_path,max_size=65536)
    period_sha=digest(period_path.read_bytes())
    require(period.get("execution_domain")=="PAPER",
            "ORIGINAL_EXECUTION_DOMAIN_NOT_PAPER")
    statuses={}
    for unit in ("heptatrader-execution","heptatrader-paper-driver",
                 "heptatrader-crypto-driver","heptatrader-pnl",
                 "heptatrader-account-nav","heptatrader-learning-source-health"):
        raw=command(["systemctl","--user","show",unit,
                     "-p","ActiveState","-p","SubState",
                     "-p","MainPID","-p","NRestarts","-p","Result",
                     "-p","InvocationID",
                     "-p","ActiveEnterTimestampMonotonic"],6000)
        statuses[unit]=parse_systemd(raw)
    pnl_path=home/".local/state/heptatrader/pnl/snapshot.json"
    health_path=(home/".local/state/heptatrader/neuron-learning/source-readiness"
                 /period_sha/"status.json")
    inputs={}
    for label,path in (("pnl",pnl_path),("health",health_path)):
        try: inputs[label]=safe_file(path)
        except (OSError,GateHold,ValueError,json.JSONDecodeError):
            inputs[label]=(None,None)
    previous=None
    if args.prior:
        previous,_=safe_file(args.prior,max_size=65536)
        require(previous.get("schema")==SCHEMA,
                "PRIOR_RECEIPT_NOT_SAME_AUDIT")
    journal=command(["journalctl","--user","-u",
        "heptatrader-execution.service","--since",args.journal_since,
        "-n","256","--no-pager","-o","json"],max_bytes=3*1024*1024)
    failures=count_incidents(journal)
    report=assess(now,statuses.pop("heptatrader-execution"),
                  statuses,inputs["pnl"][0],inputs["health"][0],
                  period_sha,previous,failures)
    report["source_file_sha256"]={
        "original_period":period_sha,"pnl":inputs["pnl"][1],
        "source_health":inputs["health"][1]}
    report["audit_code_sha256"]=digest(Path(__file__).read_bytes())
    report["journal_scan_bounded_last_records"]=256
    report["journal_since_argument"]=args.journal_since
    report["observed_at_utc"]=datetime.fromtimestamp(
        now/1000,timezone.utc).isoformat()
    raw=canonical(report)+b"\n"
    path=args.output
    require(path.is_absolute() and not path.exists() and not path.is_symlink(),
            "WRITE_ONCE_NEW_RECEIPT_REQUIRED")
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    st=path.parent.stat()
    require(stat.S_IMODE(st.st_mode)==0o700 and st.st_uid==os.geteuid(),
            "RECEIPT_DIRECTORY_NOT_PRIVATE")
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    dfd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(dfd)
    finally:os.close(dfd)
    print(json.dumps({
        "state":report["state"],
        "hold_reasons":report["hold_reasons"],
        "execution_restart_count":report["execution"]["restart_count"],
        "source_health":report["source_health"],
        "journal_failure_reason_counts":failures,
        "new_broker_queries_or_orders":0},sort_keys=True))
if __name__=="__main__":
    main()
