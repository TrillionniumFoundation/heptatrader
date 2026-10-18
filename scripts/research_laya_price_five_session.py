#!/usr/bin/env python3
"""Pocket4-only frozen price observer; file source only, no broker client."""
from __future__ import annotations
import argparse, fcntl, json, os, signal, socket, sys, time
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"research/neuron_learning"))
from laya_cell.integrity import ContractError,read_json,sha_file,digest
from laya_cell.resident_store import write_once,atomic
from laya_cell.frozen_price_numpy import FrozenPriceModel
from laya_cell.original_price_source import read_original_price_window
from laya_cell.source_readiness import assess,RULE
from laya_cell.market_session import session_clock
from laya_cell.price_original_live_features import original_feature_snapshot
from laya_cell import price_five_session_observer as c

ACTIVE_OUTPUT=None

def clock():return time.time_ns()//1000000

def verified_assets(path):
    cfg=read_json(path)
    if cfg.get("schema")!="heptatrader.frozen-price-observer-config.v1":
        raise ContractError("OBSERVER_CONFIG_SCHEMA")
    for pin in cfg["file_pins"]:
        if sha_file(Path(pin["path"]))!=pin["sha256"]:
            raise ContractError("OBSERVER_PIN_CHANGED:"+Path(pin["path"]).name)
    preparation=read_json(Path(cfg["preparation_path"]))
    selection=read_json(Path(preparation["train_only_selection"]["path"]))
    source=read_json(Path(cfg["source_config_path"]))
    if (source.get("automatic_trading") is not False or source.get("canonical_owner_endpoint") is not None
        or source.get("completed_history_sources") or preparation["normalizer"]!=selection["normalizer"]
        or preparation["prior"]!=selection["frozen_prior"]
        or preparation["candidate"]["sha256"]!=selection["training"]["tiny_mlp"]["weights_sha256"]
        or selection["selected_before_test"]!="tiny_mlp" or selection["test_used_in_selection"] is not False
        or sha_file(Path(source["period_path"]))!=source["period_sha256"]):
        raise ContractError("OBSERVER_FROZEN_ASSET_IDENTITY")
    required={str(Path(cfg["preparation_path"])),str(Path(cfg["source_config_path"])),
        preparation["candidate"]["path"],preparation["train_only_selection"]["path"],
        preparation["dataset_contract"]["path"],preparation["training_result"]["path"],
        str(Path(preparation["train_only_selection"]["path"]).parent/"protocol.json"),
        str(Path(source["period_path"]))}
    required.update(str(ROOT/p) for p in (
        "scripts/research_laya_price_five_session.py",
        "research/neuron_learning/laya_cell/frozen_price_numpy.py",
        "research/neuron_learning/laya_cell/original_price_source.py",
        "research/neuron_learning/laya_cell/price_five_session_observer.py",
        "research/neuron_learning/laya_cell/price_original_live_features.py",
        "research/neuron_learning/laya_cell/price_year_gpu.py",
        "research/neuron_learning/laya_cell/shadow_source.py",
        "research/neuron_learning/laya_cell/source_archive.py",
        "research/neuron_learning/laya_cell/resident_recent_source.py",
        "research/neuron_learning/laya_cell/source_readiness.py",
        "research/neuron_learning/laya_cell/resident_data.py",
        "research/neuron_learning/laya_cell/market_session.py",
        "research/neuron_learning/laya_cell/integrity.py",
        "research/neuron_learning/laya_cell/resident_store.py",
        "research/neuron_learning/laya_cell/dataset.py",
        "research/neuron_learning/data.py"))
    if not required.issubset({p["path"] for p in cfg["file_pins"]}):
        raise ContractError("OBSERVER_REQUIRED_FILE_PIN_MISSING")
    if sha_file(ROOT/"research/neuron_learning/laya_cell/price_year_gpu.py")!=preparation["feature_source_sha256"]:
        raise ContractError("OBSERVER_TRAIN_FEATURE_CONTRACT_CHANGED")
    model=FrozenPriceModel(Path(preparation["candidate"]["path"]),
        preparation["candidate"]["sha256"],preparation["normalizer"])
    return cfg,preparation,source,model

def verify_pins(cfg):
    for pin in cfg["file_pins"]:
        if sha_file(Path(pin["path"]))!=pin["sha256"]:
            raise ContractError("OBSERVER_PIN_CHANGED:"+Path(pin["path"]).name)


def persisted_clock_floor(root):
    keys=("at_ms","created_at_ms","verified_at_ms","ready_at_ms",
          "prediction_durable_at_ms","prediction_and_seal_durable_at_ms",
          "assessment_durable_at_ms","prior_max_observed_at_ms")
    floor=0
    for path in Path(root).glob("*.json"):
        data=read_json(path,8*1024**2)
        floor=max(floor,path.stat().st_mtime_ns//1000000,
            *(data[k] for k in keys if type(data.get(k)) is int))
    return floor

def verify_admission_evidence(root,registration,source):
    readyfile=Path(root)/"first-ready-original-source.json";ready=read_json(readyfile)
    started=ready.get("source_read_started_at_ms");done=ready.get("verified_at_ms")
    proof=ready.get("source_proof",{});feature=ready.get("feature",{});health=ready.get("readiness",{})
    if (ready.get("registration_id")!=registration["registration_id"]
        or ready.get("config_sha256")!=registration["config_sha256"]
        or ready.get("source_config_digest")!=digest(source)
        or type(started) is not int or type(done) is not int
        or not registration["created_at_ms"]<=started<=done<=registration["admission_deadline_ms"]
        or done-started>c.INPUT_DEADLINE_MS
        or proof.get("asof_ms")!=started or proof.get("period_sha256")!=source["period_sha256"]
        or proof.get("source_identity")!="ORIGINAL_VERIFIED_PAPER_QUOTES"
        or health.get("period_sha256")!=source["period_sha256"]
        or health.get("state")!="READY_FOR_REVIEW_NOT_AUTHORIZATION"
        or health.get("rule")!=RULE or health.get("usable",0)<RULE["minimum_usable"]
        or health.get("at_ms")!=started
        or not 0<=done-health.get("latest_source_quote_ms",0)<=5000
        or feature.get("anchor_ms")!=(started//c.BAR_MS)*c.BAR_MS
        or feature.get("asof_ms")!=feature.get("anchor_ms")
        or feature.get("quote_prefixes_digest")!=digest(proof)
        or feature.get("source_kind")!="ORIGINAL_VERIFIED_PAPER_QUOTES"):
        raise ContractError("OBSERVER_READY_EVIDENCE_CHANGED")
    return ready

def recover_committed_plan(root,registration,source):
    root=Path(root);planpath=root/"plan.json";readyfile=root/"first-ready-original-source.json"
    if not planpath.exists() and not readyfile.exists():return None
    ready=verify_admission_evidence(root,registration,source)
    if planpath.exists():
        plan=c.check_plan(read_json(planpath),registration)
        if (sha_file(readyfile)!=plan["ready_evidence_sha256"]
            or ready["verified_at_ms"]!=plan["ready_at_ms"]):
            raise ContractError("OBSERVER_READY_EVIDENCE_CHANGED")
        return plan
    candidate=c.make_plan(registration,ready["verified_at_ms"],sha_file(readyfile))
    write_once(planpath,candidate)
    return candidate

def main():
    global ACTIVE_OUTPUT
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config",type=Path,required=True)
    ap.add_argument("--output",type=Path,required=True)
    ap.add_argument("--max-cycles",type=int,default=0,
        help="Bounded operational canary; resume retains the same registration.")
    args=ap.parse_args()
    if not 0<=args.max_cycles<=120:ap.error("max-cycles 0..120 required")
    if socket.gethostname()!="alex-G1628-04":raise ContractError("POCKET4_ONLY")
    if not args.output.is_absolute() or args.output.is_symlink():raise ContractError("OBSERVER_PRIVATE_PATH")
    os.umask(0o077);args.output.mkdir(parents=True,mode=0o700,exist_ok=True)
    if args.output.stat().st_mode&0o077:raise ContractError("OBSERVER_PRIVATE_DIRECTORY")
    lock=os.open(args.output/"observer.lock",os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    ACTIVE_OUTPUT=args.output
    previous_floor=persisted_clock_floor(args.output)
    boot=clock()
    write_once(args.output/("boot-clock-%d-%d.json"%(boot,os.getpid())),
        {"at_ms":boot,"prior_max_observed_at_ms":previous_floor})
    atomic(args.output/"status.json",{"schema":c.SCHEMA+".status",
        "at_ms":boot,"pid":os.getpid(),"state":"BOOTSTRAP_VERIFYING","orders_sent":0})
    if boot<previous_floor:raise ContractError("OBSERVER_CLOCK_REGRESSED_ACROSS_RESTART")
    cfg,prep,source,model=verified_assets(args.config)
    regpath=args.output/"registration.json"
    if regpath.exists():
        reg=c.verify_stamp(read_json(regpath),"registration_id")
        if reg!=c.register(reg["created_at_ms"],sha_file(args.config)):
            raise ContractError("OBSERVER_REGISTRATION_CHANGED")
    else:
        reg=c.register(clock(),sha_file(args.config));write_once(regpath,reg)
    boot=clock()
    write_once(args.output/("run-start-%d-%d.json"%(boot,os.getpid())),
        {"at_ms":boot,"pid":os.getpid(),"registration_id":reg["registration_id"],
         "config_sha256":sha_file(args.config),"file_pins":cfg["file_pins"],
         "cpu_only":True,"torch_imported":"torch" in sys.modules,"orders_sent":0})
    stop=False
    def finish(*_):
        nonlocal stop
        stop=True
    signal.signal(signal.SIGTERM,finish);signal.signal(signal.SIGINT,finish)
    planpath=args.output/"plan.json"
    plan=recover_committed_plan(args.output,reg,source)
    prior=prep["prior"];model_sha=prep["candidate"]["sha256"]
    outcomes={};predicted=set();last_errors={};cycles=0
    checks=sorted(args.output.glob("admission-check-*.json"))
    checked={int(p.stem.rsplit("-",1)[-1]) for p in checks}
    last_admission=None;last_pin_check=0;previous_clock=boot
    if checks:last_admission=read_json(checks[-1])
    def save_outcome(value):
        slot=value["slot"]
        if value["status"]=="OBSERVED":
            predpath=args.output/("prediction-%04d.json"%slot)
            value["prediction_sha256"]=sha_file(predpath)
            assessed=args.output/("assessment-%04d.json"%slot)
            write_once(assessed,value)
            durable=clock()
            due=plan["anchors"][slot]["anchor_ms"]+c.BAR_MS
            if durable>due+c.OUTCOME_GRACE_MS:
                value=c.censor(plan,slot,durable,"OUTCOME_ASSESSMENT_FSYNC_LATE")
            else:
                value={**value,"assessment_sha256":sha_file(assessed),
                    "assessment_durable_at_ms":durable,"at_ms":durable}
        c.verify_outcome(args.output,plan,value,model_sha,prior)
        write_once(args.output/("outcome-%04d.json"%slot),value)
        outcomes[slot]=value
    if plan:
        for p in sorted(args.output.glob("outcome-*.json")):
            value=read_json(p);c.verify_outcome(args.output,plan,value,model_sha,prior)
            outcomes[value["slot"]]=value
        for row in plan["anchors"]:
            i=row["slot"]
            if i in outcomes:
                if (args.output/("commit-%04d.json"%i)).exists():
                    try:
                        c.verified_prediction(args.output,plan,i,model_sha,prior)
                        predicted.add(i)
                    except (ContractError,OSError,ValueError):
                        if outcomes[i]["status"]=="OBSERVED":raise
                continue
            if (args.output/("assessment-%04d.json"%i)).exists():
                save_outcome(c.censor(plan,i,clock(),"RESTART_ORPHAN_ASSESSMENT_WITHOUT_FINAL_RECEIPT"))
                continue
            committed=args.output/("commit-%04d.json"%i)
            started=any((args.output/(k+"-%04d.json"%i)).exists() for k in ("input","prediction","seal","commit"))
            if committed.exists():
                c.verified_prediction(args.output,plan,i,model_sha,prior);predicted.add(i)
            elif started:
                save_outcome(c.censor(plan,i,clock(),"RESTART_ORPHAN_WITHOUT_DURABLE_COMMIT"))
    def status(state,reason=None):
        at=clock();data={"schema":c.SCHEMA+".status","at_ms":at,"pid":os.getpid(),
            "state":state,"reason":reason,"registration_id":reg["registration_id"],
            "model_sha256":model_sha,"cpu_only":True,"torch_imported":"torch" in sys.modules,
            "admission_deadline_ms":reg["admission_deadline_ms"],
            "hard_end_ms":reg["hard_end_ms"],"source_admission_checks":len(checked),
            "elapsed_wait_minutes":(min(at,plan["ready_at_ms"] if plan else at)-reg["created_at_ms"])/60000,
            "admission_check_slots_missed":max(0,((min(at,plan["ready_at_ms"] if plan else at)-reg["created_at_ms"])//60000)+1-len(checked)),
            "last_admission_check":last_admission,
            "orders_sent":0,"model_promotion":False,"formal_laya_trial":False,
            "actual_forward_predictions":len(predicted),"statistical_or_economic_qualification":False}
        if plan:data.update(plan_id=plan["plan_id"],schedule=plan["sessions"],**c.summarize(plan,list(outcomes.values())))
        else:data.update(eligible_opportunities=None,observed=0,coverage=None)
        atomic(args.output/"status.json",data)
        print(json.dumps({k:v for k,v in data.items() if k not in ("last_admission_check","schedule","sessions")}),flush=True)
    status("WAITING_FOR_ORIGINAL_SOURCE" if plan is None else "OBSERVING_FIXED_SCHEDULE")
    try:
        while not stop:
            cycles+=1
            if args.max_cycles and cycles>args.max_cycles:
                status("CANARY_COMPLETE_RESUMABLE");return
            now=clock()
            if now<previous_clock:raise ContractError("OBSERVER_CLOCK_REGRESSED")
            previous_clock=now
            if now-last_pin_check>=60000:
                verify_pins(cfg);last_pin_check=now
            if now>reg["hard_end_ms"]:
                if plan:
                    for row in plan["anchors"]:
                        if row["slot"] not in outcomes:
                            save_outcome(c.censor(plan,row["slot"],now,"HARD_RUNTIME_END_MISSED"))
                status("HARD_END");return
            if plan is None:
                plan=recover_committed_plan(args.output,reg,source)
                if plan is not None:
                    status("FIXED_SCHEDULE_COMMITTED");continue
                if now>reg["admission_deadline_ms"]:
                    status("START_FAILED_NO_ORIGINAL_SOURCE");return
                check=(now-reg["created_at_ms"])//60000
                if check not in checked:
                    cal=session_clock(datetime.fromtimestamp(now/1000,timezone.utc))
                    attempt={"at_ms":now,"index":check,"calendar":cal,"state":"WAIT",
                        "reason":"OUTSIDE_REGULAR_CALENDAR_WINDOW","source_quote_count":0}
                    if cal["calendar_regular_session_open"]:
                        try:
                            quotes,proof=read_original_price_window(source,now)
                            ready=assess(source,now,quote_loader=lambda *_:(quotes,proof["archive_pages"]+proof["selected_original_prefixes"]))
                            anchor=(now//c.BAR_MS)*c.BAR_MS
                            feature=original_feature_snapshot(quotes,proof,anchor,require_fresh=False)
                            if feature["anchor_ms"]!=anchor:
                                raise ContractError("ADMISSION_CLOSED_BAR_ANCHOR_UNAVAILABLE")
                            if ready["state"]!="READY_FOR_REVIEW_NOT_AUTHORIZATION":
                                raise ContractError("ADMISSION_"+",".join(ready["reasons"]))
                            if not quotes or quotes[-1]["known_at_ms"]<reg["created_at_ms"]:
                                raise ContractError("ADMISSION_NO_NEW_ORIGINAL_SOURCE")
                            done=clock()
                            if done-now>c.INPUT_DEADLINE_MS or done-quotes[-1]["t"]>5000:
                                raise ContractError("ADMISSION_SOURCE_EXPIRED_DURING_READ")
                            if done>reg["admission_deadline_ms"]:
                                raise ContractError("FIVE_SESSION_ADMISSION_EXPIRED")
                            readyfile=args.output/"first-ready-original-source.json"
                            write_once(readyfile,{"registration_id":reg["registration_id"],
                                "config_sha256":reg["config_sha256"],"source_config_digest":digest(source),
                                "source_read_started_at_ms":now,"verified_at_ms":done,
                                "source_proof":proof,"feature":feature,"readiness":ready})
                            verify_admission_evidence(args.output,reg,source)
                            candidate_plan=c.make_plan(reg,done,sha_file(readyfile))
                            write_once(planpath,candidate_plan)
                            plan=candidate_plan
                            attempt.update(state="READY_COMMITTED",reason=None,
                                source_quote_count=len(quotes),ready_evidence_sha256=sha_file(readyfile))
                        except (ContractError,OSError,ValueError,KeyError) as exc:
                            attempt["reason"]=type(exc).__name__+":"+str(exc)[:180]
                    write_once(args.output/("admission-check-%06d.json"%check),attempt)
                    checked.add(check);last_admission=attempt
                    status("WAITING_FOR_ORIGINAL_SOURCE" if plan is None else "FIXED_SCHEDULE_COMMITTED")
                time.sleep(.5);continue
            work=c.pending_actions(plan,now,predicted,set(outcomes))
            changed=False
            for slot,action in work:
                anchor=plan["anchors"][slot]["anchor_ms"]
                try:
                    if action=="CENSOR_INPUT":
                        save_outcome(c.censor(plan,slot,clock(),"INPUT_UNAVAILABLE_OR_LATE:"+last_errors.get(slot,"NO_PRECOMMIT")))
                    elif action=="CENSOR_OUTCOME":
                        save_outcome(c.censor(plan,slot,clock(),"OUTCOME_DEADLINE_MISSED:"+last_errors.get(slot,"NO_COMPLETE_TARGET")))
                    elif action=="PREDICT":
                        quotes,proof=read_original_price_window(source,anchor)
                        snap=c.input_snapshot(quotes,proof,anchor)
                        probs,clips=model.predict(snap["feature"]["features"])
                        c.persist_prediction(args.output,plan,slot,snap,probs[0].tolist(),prior,model_sha,int(clips[0]),clock)
                        predicted.add(slot)
                    else:
                        pred=c.verified_prediction(args.output,plan,slot,model_sha,prior)
                        quotes,proof=read_original_price_window(source,anchor+c.BAR_MS)
                        result=c.grade(plan,slot,pred,quotes,proof,clock())
                        if result is None:continue
                        save_outcome(result)
                    changed=True
                except (ContractError,OSError,ValueError,KeyError) as exc:
                    last_errors[slot]=type(exc).__name__+":"+str(exc)[:160]
                    if action=="PREDICT" and any((args.output/(k+"-%04d.json"%slot)).exists() for k in ("input","prediction","seal","commit")):
                        save_outcome(c.censor(plan,slot,clock(),"PARTIAL_OR_LATE_PRECOMMIT:"+last_errors[slot]))
                        changed=True
            if changed:status("OBSERVING_FIXED_SCHEDULE")
            if len(outcomes)==len(plan["anchors"]):
                status("COMPLETED_ORIGINAL_OBSERVATION_RESEARCH_ONLY");return
            if not changed and now%60000<1000:status("OBSERVING_FIXED_SCHEDULE")
            time.sleep(.2)
        status("STOPPED_RESUMABLE")
    except Exception as exc:
        status("FAILED_INTEGRITY_OR_RUNTIME",type(exc).__name__+":"+str(exc)[:180])
        raise
    finally:os.close(lock)

if __name__=="__main__":
    try:main()
    except Exception as exc:
        if ACTIVE_OUTPUT is not None:
            atomic(ACTIVE_OUTPUT/"status.json",{"schema":c.SCHEMA+".status",
                "at_ms":clock(),"pid":os.getpid(),"state":"FAILED_INTEGRITY_OR_RUNTIME",
                "reason":type(exc).__name__+":"+str(exc)[:180],"orders_sent":0})
        raise
