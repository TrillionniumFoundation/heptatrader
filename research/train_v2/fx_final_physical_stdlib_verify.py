"""Independent fixed 2026-10-13 EUR.USD 4x4 PAPER no-trade custody check.

Python 3.9 stdlib only; runs OFFLINE on Mac/X230/qian-qi using the same
bounded ZIP archive. It does not import original HeptaTrader readers.
A pass means integrity of retained bytes, NOT independently broker-signed
quotes, hardware-backed witness, execution PnL, or model efficacy.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import socket
import stat
import zipfile

SCHEMA = "heptatrader.fx-paper-four-window-twohost-stdlib-audit.v1"
ORIGINAL = "heptatrader.fx-original-future-price-cost-calendar.v1"
CUSTODY = "heptatrader.fx-forward-original-health-custody.v1"
PINS = {
    "plan": "02aa4a2662b743203cc925d09d3250bcc0e02155cf5d00bb319b0526448aaa04",
    "seal": "1f13ed5c7a8fcdc29cc0881bf5c7854c263876f5f89bb6bc87633d5b2b33860b",
    "final": "857914d7222cc6e85058d9a19c3bf8765690e172a91a4de9cd2d22ebecc3890a",
    "witness_plan": "9bdb268359c2befeecb66ff11cddf45161f72543678875ed86428cb3a6420737",
    "witness_final": "6b2c9b6a2c1931a26c415801495329591db99f56a1b19560191f3591470fb184",
}
ANCHORS = (1791822000000, 1791833700000, 1791845400000, 1791857100000)
HORIZONS = (15,30,60,120)
SOURCE_RULE = {
 "schema":"heptatrader.quote-readiness.v1",
 "input_probes":24,"spacing_ms":60000,"horizon_ms":300000,
 "input_grace_ms":60000,"endpoint_tolerance_ms":5000,
 "path_gap_max_ms":30000,"minimum_usable":20,"maximum_missing":4,
 "source_ttl_ms":5000,
 "statistics_scope":"OVERLAPPING_OPERATIONAL_COVERAGE_NOT_PERFORMANCE",
}
MAX_FILE = 131072
MAX_ARCHIVE = 1024*1024
class AuditHold(ValueError):
    pass

def need(cond, reason):
    if not cond: raise AuditHold(reason)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()

def strict(raw):
    def unique(pairs):
        d={}
        for k,v in pairs:
            need(k not in d,"DUPLICATED_JSON_KEY")
            d[k]=v
        return d
    try:
        return json.loads(raw,object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(AuditHold("NONFINITE_JSON")))
    except (UnicodeError,json.JSONDecodeError) as exc:
        raise AuditHold("INVALID_JSON") from exc

def expected_names():
    files = {
        "precommit/plan.json", "precommit/sealed.json",
        "precommit/crosshost-readback.json",
        "original/plan.json", "original/sealed.json",
        "original/worker-start.json", "original/final.json",
        "custody/witness-plan.json", "custody/witness-worker-start.json",
        "custody/witness-final.json",
    }
    for i in range(4):
        files.add("original/window-%02d/decision-attempt.json"%i)
        for horizon in HORIZONS:
            files.add("original/window-%02d/maturity-%03d-attempt.json"%(i,horizon))
    for i in (1,2,3):
        files.add("custody/window-%02d-health-attempt.json"%i)
        files.add("custody/window-%02d-health-original.json"%i)
    need(len(files)==36,"BAD_EXPECTED_BUNDLE_SCHEMA")
    return files

def load_bundle(path):
    p=Path(path)
    need(p.is_file() and not p.is_symlink()
         and p.stat().st_uid==os.geteuid()
         and not p.stat().st_mode&0o077
         and 0 < p.stat().st_size <= MAX_ARCHIVE, "ZIP_NOT_BOUNDED_PRIVATE_FILE")
    files={}
    try:
        with zipfile.ZipFile(p,"r") as z:
            names=z.infolist()
            need(len(names)==len(expected_names()),"ZIP_FILE_COUNT")
            for member in names:
                name=member.filename
                pure=PurePosixPath(name)
                need(name in expected_names() and len(pure.parts)>=2
                     and all(x not in (".","..","") for x in pure.parts)
                     and not name.startswith("/") and not name.endswith("/")
                     and name not in files, "ZIP_UNEXPECTED_DUPLICATE_OR_TRAVERSAL")
                kind=member.external_attr>>16
                need(stat.S_ISREG(kind) and stat.S_IMODE(kind)&0o077==0,
                     "ZIP_FILE_NOT_PRIVATE_REGULAR")
                need(0<member.file_size<=MAX_FILE and
                     member.compress_size<=MAX_ARCHIVE,"ZIP_MEMBER_OVERSIZED")
                raw=z.read(member)
                need(len(raw)==member.file_size,"ZIP_TRUNCATED_MEMBER")
                files[name]=raw
    except (zipfile.BadZipFile,RuntimeError) as exc:
        raise AuditHold("ZIP_INTEGRITY_FAILED") from exc
    need(set(files)==expected_names(),"ZIP_MISSING_OR_EXTRA_SOURCE")
    return files

def document(files,name):
    need(name in files,"ORIGINAL_FILE_MISSING")
    result=strict(files[name])
    need(type(result) is dict,"DOCUMENT_MUST_BE_JSON_OBJECT")
    return result

def validate_attempt(row, anchor, stage, *, h=None):
    due=anchor
    code="EXCEPTION_Hold_ORIGINAL_READINESS_HOLD_OR_STALE"
    if h is not None:
        due=anchor+h*60000
        code="NO_ELIGIBLE_ORIGINAL_PREACTION"
    need(row.get("schema")==ORIGINAL+".attempt.v1"
         and row.get("stage")==stage
         and row.get("state")=="CENSORED"
         and row.get("reason_code")==code
         and row.get("automatic_retry_allowed") is False
         and row.get("historical_price_or_future_quote_backfilled") is False
         and row.get("model_promotion_allowed") is False
         and row.get("trade_authority") is False
         and type(row.get("broker_orders_sent")) is int
         and row.get("broker_orders_sent")==0
         and type(row.get("broker_queries_sent")) is int
         and row.get("broker_queries_sent")==0,
         "ORIGINAL_CENSORED_ATTEMPT_IDENTITY_INVALID")
    ts=row.get("attempt_recorded_at_ms")
    lower,upper=(due+1000,due+4500) if h is None else (due+6500,due+15000)
    need(type(ts) is int and lower<=ts<=upper,
         "ORIGINAL_ATTEMPT_OUTSIDE_PREREGISTERED_CLOCK")
    return ts

def validate_health_record(original, receipt, anchor):
    need(original.get("schema")=="heptatrader.quote-readiness-status.v1"
         and original.get("period_sha256")==
             "61995fcedc2993d7ac33d2ed86c9613068c5a3ecd0d1c377e612639cd5a0958f"
         and original.get("rule")==SOURCE_RULE
         and original.get("automatic_trial_registration") is False
         and original.get("trading_authority") is False,
         "HEALTH_SOURCE_SCOPE_INVALID")
    probes=original.get("probes")
    need(type(probes) is list and len(probes)==24
         and all(type(p) is dict and type(p.get("status")) is str for p in probes),
         "HEALTH_24_PROBES_MISSING")
    counts=dict(Counter(p["status"] for p in probes))
    need(counts==original.get("counts")
         and counts.get("USABLE")==24
         and original.get("usable")==24
         and original.get("unusable")==0
         and original.get("state")=="READY_FOR_REVIEW_NOT_AUTHORIZATION"
         and original.get("reasons")==[],
         "HEALTH_NOT_ORIGINAL_24_24_READY")
    prefixes=original.get("source_prefixes")
    need(type(prefixes) is list and sha(canonical(prefixes))==
         original.get("source_prefix_digest"),"ORIGINAL_SOURCE_PREFIX_DIGEST_CHANGED")
    at,source,age=(original.get(k) for k in
                   ("at_ms","latest_source_quote_ms","latest_quote_age_ms"))
    need(all(type(x) is int for x in (at,source,age))
         and at-source==age and 0<=age<=5000,"ORIGINAL_LAST_QUOTE_HEALTH_INVALID")
    started=receipt.get("attempt_started_at_ms")
    durable=receipt.get("durable_at_ms")
    need(type(started) is int and type(durable) is int
         and anchor+1000<=started<=durable<=anchor+4500,
         "HEALTH_SNAPSHOT_CAPTURE_CLOCK_INVALID")
    delta=started-at
    need(delta>30000 and delta<10*60000,
         "ORIGINAL_HEALTH_SNAPSHOT_WAS_NOT_STALE")
    need(receipt.get("state")=="HOLD"
         and receipt.get("reasons")==["READINESS_SNAPSHOT_STALE_OR_FUTURE"]
         and receipt.get("recomputed_source_usable_probes")==24
         and receipt.get("readiness_health_at_ms")==at
         and receipt.get("original_study_entry_readiness_proven") is False
         and receipt.get("no_retroactive_relabel_of_original_trial") is True
         and receipt.get("trade_authority") is False
         and receipt.get("broker_orders_sent")==0,
         "HEALTH_CUSTODY_DID_NOT_HOLD_ORIGINAL_STALE")
    return delta

def validate(files):
    plan,seal,final=(document(files,x) for x in
             ("original/plan.json","original/sealed.json","original/final.json"))
    witness,witness_final=(document(files,x) for x in
             ("custody/witness-plan.json","custody/witness-final.json"))
    root_hashes={
        "plan":sha(files["original/plan.json"]),
        "seal":sha(files["original/sealed.json"]),
        "final":sha(files["original/final.json"]),
        "witness_plan":sha(files["custody/witness-plan.json"]),
        "witness_final":sha(files["custody/witness-final.json"]),
    }
    need(root_hashes==PINS,"PUBLISHED_ORIGINAL_ROOT_HASH_CHANGED")
    need(files["original/plan.json"]==files["precommit/plan.json"]
         and files["original/sealed.json"]==files["precommit/sealed.json"],
         "PRETRIAL_PHYSICAL_CUSTODY_BYTES_NOT_SAME")
    precopy=document(files,"precommit/crosshost-readback.json")
    need(precopy.get("schema")=="heptatrader.fx-research-crosshost-preseal-copy.v1"
         and precopy.get("plan_sha256")==PINS["plan"]
         and precopy.get("seal_sha256")==PINS["seal"]
         and precopy.get("all_windows_future_at_copy") is True
         and precopy.get("trade_authority") is False
         and precopy.get("broker_orders_sent")==0
         and datetime.fromisoformat(precopy["checked_at_utc"]).timestamp()*1000 <
             ANCHORS[0], "EARLY_PHYSICAL_PRETRIAL_COPY_UNPROVEN")
    need(plan.get("schema")==ORIGINAL
         and plan.get("anchors_ms")==list(ANCHORS)
         and plan.get("independent_horizons_ms")==[h*60000 for h in HORIZONS]
         and plan.get("fixed_windows")==4
         and plan.get("behavior_policy")=="NO_TRADE"
         and plan.get("trade_authority") is False
         and plan.get("automatic_training") is False
         and plan.get("model_promotion_allowed") is False
         and plan.get("not_native_train_v2_120min_spec") is True
         and plan.get("broker_orders_sent")==0,
         "ORIGINAL_RESEARCH_PLAN_SCOPE_CHANGED")
    need(seal.get("schema")==ORIGINAL+".sealed.v1"
         and seal.get("plan_sha256")==PINS["plan"]
         and seal.get("research_code_sha256")==plan.get("research_code_sha256")
         and len(seal.get("windows",[]))==4,
         "ORIGINAL_FROZEN_CALENDAR_CHANGED")
    for i,w in enumerate(seal["windows"]):
        need(w.get("index")==i and w.get("anchor_ms")==ANCHORS[i]
             and w.get("entry_first_ms")==ANCHORS[i]+1000
             and w.get("entry_last_ms")==ANCHORS[i]+4500
             and w.get("mature_at_ms")==[ANCHORS[i]+h*60000
                                        for h in HORIZONS],
             "ORIGINAL_WINDOW_TIMELINE_CHANGED")
    worker=document(files,"original/worker-start.json")
    need(worker.get("sealed_sha256")==PINS["seal"]
         and worker.get("no_restart_no_retry") is True
         and worker.get("no_trading_or_model_promotion") is True
         and worker.get("broker_orders_sent")==0
         and plan["registered_at_ms"]<=worker.get("started_at_ms",0)<ANCHORS[0],
         "ORIGINAL_WORKER_START_MISSING_OR_REPLAYED")
    need(final.get("schema")==ORIGINAL+".final.v1"
         and final.get("sealed_sha256")==PINS["seal"]
         and final.get("window_count")==4
         and final.get("original_preaction_frozen_count")==0
         and final.get("mature_quote_only_samples")==0
         and final.get("all_historical_test_and_loss_facts_unchanged") is True
         and final.get("no_model_predictions_or_production_admission") is True
         and final.get("trading_strategy_changed") is False
         and final.get("broker_orders_sent")==0
         and final.get("actual_execution_rewards")==0
         and final.get("model_promotion_allowed") is False
         and len(final.get("windows",[]))==4,
         "ORIGINAL_FINAL_ABSTENTION_CONCLUSION_CHANGED")
    last=ANCHORS[-1]+HORIZONS[-1]*60000
    need(type(final.get("completed_at_ms")) is int
         and last+6500<=final["completed_at_ms"]<=last+60000,
         "PRIMARY_FINAL_BEFORE_LAST_MATURITY")
    decisions=[]
    maturities=[]
    for i,w in enumerate(final["windows"]):
        need(w.get("index")==i and w.get("decision_state")=="CENSORED",
             "ORIGINAL_FINAL_ORDER_OR_DECISION_CHANGED")
        base="original/window-%02d/"%i
        name=base+"decision-attempt.json"
        doc=document(files,name)
        validate_attempt(doc,ANCHORS[i],"decision")
        need(sha(files[name])==w.get("decision_attempt_sha256"),
             "ORIGINAL_DECISION_HASH_CHANGED_FROM_FINAL")
        decisions.append(sha(files[name]))
        rows=w.get("outcomes")
        need(type(rows) is list and len(rows)==4,
             "ORIGINAL_FINAL_MATURITY_SET_MISSING")
        for j,h in enumerate(HORIZONS):
            label="maturity-%03d"%h
            mname=base+label+"-attempt.json"
            md=document(files,mname)
            validate_attempt(md,ANCHORS[i],label,h=h)
            need(rows[j].get("horizon_minutes")==h
                 and rows[j].get("state")=="CENSORED"
                 and rows[j].get("attempt_sha256")==sha(files[mname]),
                 "ORIGINAL_MATURITY_HASH_CHANGED_FROM_FINAL")
            maturities.append(sha(files[mname]))
    need(len(set(decisions))==4 and len(set(maturities))==16,
         "DUPLICATED_ORIGINAL_PRICE_ATTEMPTS")
    need(witness.get("schema")==CUSTODY+".plan.v1"
         and witness.get("original_plan_sha256")==PINS["plan"]
         and witness.get("original_seal_sha256")==PINS["seal"]
         and witness.get("source_monitor_fixed_rule")==SOURCE_RULE
         and witness.get("period_sha256")==plan.get("period_sha256")
         and witness.get("trade_authority") is False
         and witness.get("broker_orders_sent")==0
         and witness.get("past_window_00_missed_and_cannot_be_reconstructed") is True
         and witness.get("registered_at_ms") < ANCHORS[1]-5*60000,
         "FUTURE_READINESS_WITNESS_PRECOMMIT_CHANGED")
    need(witness_final.get("schema")==CUSTODY+".final.v1"
         and witness_final.get("plan_sha256")==PINS["witness_plan"]
         and witness_final.get("original_study_amended") is False
         and witness_final.get("production_root_or_witness_authority") is False
         and witness_final.get("broker_orders_sent")==0,
         "HEALTH_WITNESS_FINAL_SCOPE_CHANGED")
    need(len(witness.get("planned_capture_windows",[]))==3
         and len(witness_final.get("windows",[]))==3,
         "HEALTH_WITNESS_FUTURE_POPULATION_INCOMPLETE")
    snapshot_ages={}
    for i in (1,2,3):
        entry=witness["planned_capture_windows"][i-1]
        need(entry.get("index")==i and entry.get("anchor_ms")==ANCHORS[i]
             and entry.get("capture_first_ms")==ANCHORS[i]+1000
             and entry.get("capture_last_ms")==ANCHORS[i]+4500,
             "WITNESS_SCHEDULE_WAS_RETROACTIVELY_SHIFTED")
        attempt_name="custody/window-%02d-health-attempt.json"%i
        data_name="custody/window-%02d-health-original.json"%i
        a=document(files,attempt_name)
        health=document(files,data_name)
        need(a.get("source_health_status_sha256")==sha(files[data_name])
             and a.get("source_health_status_bytes")==len(files[data_name])
             and a.get("schema")==CUSTODY+".snapshot-attempt.v1"
             and a.get("window")==i and a.get("anchor_ms")==ANCHORS[i],
             "ORIGINAL_HEALTH_BYTES_OR_ATTEMPT_TAMPERED")
        need(witness_final["windows"][i-1].get("window")==i
             and witness_final["windows"][i-1].get("state")=="HOLD"
             and witness_final["windows"][i-1].get("witness_receipt_sha256")==
                 sha(files[attempt_name]),
             "WITNESS_FINAL_RAW_HASH_BINDING_CHANGED")
        snapshot_ages[str(i)]=validate_health_record(health,a,ANCHORS[i])
    return {
        "schema": SCHEMA,
        "verified_at_utc":datetime.now(timezone.utc).isoformat(),
        "verifier_host":socket.gethostname(),
        "frozen_root_hashes":root_hashes,
        "pretrial_external_custody_before_first_window":True,
        "all_original_entry_attempt_sha256s":decisions,
        "all_original_maturity_attempt_sha256s":maturities,
        "original_decisions_censored":len(decisions),
        "original_mature_price_attempts_censored":len(maturities),
        "original_valid_price_labels":0,
        "remaining_source_health_stale_age_ms_by_window":snapshot_ages,
        "first_window_full_health_not_saved":True,
        "original_service_natural_exit_requires_separate_host_journal":True,
        "source_health_producer_locally_hashed_not_ib_signed":True,
        "same_paper_source_used_no_second_broker_subscription":True,
        "production_root_owner_witness_enrolled":False,
        "training_fitting_or_policy_promotion_allowed":False,
        "actual_broker_execution_net_pnl_proven":False,
        "trade_authority":False,
        "broker_queries_or_orders_sent":0,
        "result":"PASS_FROZEN_4X4_CENSORED_CUSTODY_NOT_ALPHA",
    }

def write_once_private(output,result):
    p=Path(output)
    need(p.is_absolute() and not p.exists() and not p.is_symlink(),
         "REPORT_OUTPUT_MUST_BE_NEW")
    p.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    mode=p.parent.stat()
    need(stat.S_IMODE(mode.st_mode)==0o700 and mode.st_uid==os.geteuid(),
         "REPORT_OUTPUT_PARENT_NOT_PRIVATE")
    b=canonical(result)+b"\n"
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(b);f.flush();os.fsync(f.fileno())
    d=os.open(p.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(d)
    finally:os.close(d)
    return sha(b)

def pack(root,output):
    root=Path(root)
    need(root.is_absolute() and root.is_dir(),"ARCHIVE_SOURCE_NOT_ABSOLUTE")
    output=Path(output)
    need(not output.exists() and not output.is_symlink(),
         "ZIP_ALREADY_EXISTS")
    need(output.parent.stat().st_uid==os.geteuid()
         and stat.S_IMODE(output.parent.stat().st_mode)==0o700,
         "ZIP_OUTPUT_PARENT_NOT_PRIVATE")
    os.umask(0o077)
    fd=os.open(output,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,"w+b") as dest:
            with zipfile.ZipFile(dest,"w",compression=zipfile.ZIP_DEFLATED,
                                 compresslevel=6,allowZip64=False) as z:
                for name in sorted(expected_names()):
                    p=root/name
                    need(not p.is_symlink() and p.is_file(),"ZIP_SOURCE_NOT_REGULAR")
                    st=p.stat()
                    need(st.st_uid==os.geteuid() and st.st_nlink==1
                         and not st.st_mode&0o077 and 0<st.st_size<=MAX_FILE,
                         "ZIP_SOURCE_NOT_PRIVATE")
                    member=zipfile.ZipInfo(name, date_time=(2026,10,13,0,0,0))
                    member.compress_type=zipfile.ZIP_DEFLATED
                    member.external_attr=(stat.S_IFREG|0o600)<<16
                    z.writestr(member,p.read_bytes())
            dest.flush();os.fsync(dest.fileno())
    except BaseException:
        raise
    result=validate(load_bundle(output))
    return {"archive_sha256":sha(output.read_bytes()),
            "files":len(expected_names()),"local_verify_result":result["result"]}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--mode",choices=("pack","verify"),required=True)
    ap.add_argument("--source",type=Path)
    ap.add_argument("--bundle",type=Path,required=True)
    ap.add_argument("--output",type=Path)
    a=ap.parse_args()
    if a.mode=="pack":
        need(a.source is not None and a.output is None,"PACK_ARGS")
        response=pack(a.source,a.bundle)
    else:
        need(a.source is None and a.output is not None,"VERIFY_ARGS")
        raw=load_bundle(a.bundle)
        response=validate(raw)
        response["archive_sha256"]=sha(a.bundle.read_bytes())
        response["report_sha256"]=write_once_private(a.output,response)
    print(json.dumps({k:v for k,v in response.items() if k not in (
          "all_original_entry_attempt_sha256s",
          "all_original_maturity_attempt_sha256s")},sort_keys=True),flush=True)

if __name__=="__main__":main()
