"""Exact and guarded Laya archive-aware research-service activation.

Never acts before the independent fixed-slot FX cache canary completes.
Only creates two exact Trainer/Evaluator user drop-ins, preserves originals,
and uses the existing systemd user manager. No PAPER/broker/producer changes.
"""
from __future__ import annotations
import argparse
import fcntl
import stat
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from historical_training.common import EvidenceError, read, sha
from historical_training.laya_guard_archive_cutover import validate_old
from historical_training.laya_guard_archive_service_plan import render_dropin

STAGED = Path.home()/".local/state/heptatrader/operator-evidence/train-v2-laya-guard-cutover-20261013"
CANARY = Path.home()/".local/state/heptatrader/operator-evidence/fx-cache-fixedslot-1005-20261013-v1"
OUTPUT = Path.home()/".local/state/heptatrader/operator-evidence/train-v2-laya-activation-20261013"
SOURCE_PLAN_SHA="10fea7101627dde050a0b6f6ec1cfa93d7dc746ad0fc57c13b4b7224f7659943"
EXPECTED={
  "trainer":"31218b5e40f3b9a4e7589be87e499faebd389b3c2ad24ebbbe87c96949d9542b",
  "evaluator":"8bea5b65740b99be4f33ed624b5f8a5336b3e5db3b4998cc8aeced037725f5d1",
}
ROLE_UNIT={r:f"heptatrader-laya-{r}.service" for r in EXPECTED}
CONF="zzzz-archive-guard-pin-20261013.conf"
CANARY_END_MS=1791857140000   # 10:05:40 UTC+8: 24 x 15s from 09:59:40
MAX_CONTROL_AGE_MS=300000

def demand(ok,reason):
    if not ok:raise EvidenceError(reason)

def systemctl(*args):
    p=subprocess.run(["systemctl","--user",*args],capture_output=True,
                     text=True,timeout=12,check=False)
    demand(p.returncode==0,"SYSTEMD_COMMAND_REJECTED:"+":".join(args))
    return p.stdout

def private_existing(file):
    file=Path(file)
    demand(file.is_absolute() and not file.is_symlink() and file.is_file(),
           "SOURCE_FILE_NOT_VERIFIABLE")
    st=file.stat()
    demand(st.st_uid==os.getuid() and st.st_mode&0o077==0,
           "SOURCE_NOT_PRIVATE")
    return file

def write_once(path,payload):
    path=Path(path)
    demand(not path.exists() and not path.is_symlink(),"OUTPUT_ALREADY_EXISTS")
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(payload);f.flush();os.fsync(f.fileno())
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)

def collect_canary(now_ms):
    demand(now_ms>CANARY_END_MS,"FIXED_CANARY_NOT_COMPLETED")
    plan=read(private_existing(CANARY/"plan.json"))
    demand(sha(CANARY/"plan.json")==SOURCE_PLAN_SHA,
           "FIXED_CANARY_PRECOMMIT_CHANGED")
    demand(plan["capture_exact_target_ms"]==1791857102000 and
           plan["fixed_anchor_ms"]==1791857100000 and
           plan["capture_permitted_window_ms"]==[1791857101000,1791857104500] and
           plan["model_promotion_allowed"] is False and
           plan["broker_orders_sent"]==0,"FIXED_CANARY_SCOPE_CHANGED")
    receipt=read(private_existing(CANARY/"comparison.json"))
    demand(receipt.get("state")=="TWO_READONLY_SOURCE_HEALTH_SNAPSHOTS_PRESERVED"
           and receipt.get("canary_precommit_sha256")==SOURCE_PLAN_SHA
           and receipt.get("trade_authority") is False
           and receipt.get("original_trade_entry_eligibility_not_reclassified") is True
           and receipt.get("broker_orders_sent")==0,
           "FIXED_CANARY_NOT_TERMINALLY_PRESERVED")
    captured=receipt.get("attempt_at_ms")
    durable=receipt.get("comparison_durable_at_ms")
    demand(type(captured)is int and 1791857101000<=captured<=1791857104500
           and type(durable)is int and durable<=1791857104500,
           "FIXED_CANARY_CAPTURE_TIME_NOT_QUALIFIED")
    for name in ("baseline","canary"):
        entry=receipt.get(name)
        demand(isinstance(entry,dict) and
               isinstance(entry.get("captured_sha256"),str) and
               len(entry["captured_sha256"])==64,
               "FIXED_CANARY_RAW_SNAPSHOT_MISSING:"+name)
        path=private_existing(CANARY/(name+"-original-status.json"))
        demand(sha(path)==entry["captured_sha256"],
               "FIXED_CANARY_SNAPSHOT_CHANGED:"+name)
    for role in ("canary","snapshot"):
        unit=f"heptatrader-fx-cache-{role}-1005-20261013.service"
        probe=subprocess.run(
            ["systemctl","--user","show",unit,"-p","ActiveState"],
            capture_output=True,text=True,timeout=8,check=False)
        # Transient systemd services are removed after completion. That is
        # acceptable ONLY after immutable terminal capture and exact deadline.
        if probe.returncode != 0:
            demand("not found" in probe.stderr.lower()
                   or "could not be found" in probe.stderr.lower(),
                   "FIXED_CANARY_SYSTEMD_STATE_UNKNOWN:"+role)
        else:
            demand("ActiveState=active" not in probe.stdout
                   and "ActiveState=activating" not in probe.stdout,
                   "FIXED_CANARY_WORKER_STILL_RUNNING")
    p=subprocess.run(["ps","-eo","args="],capture_output=True,text=True,
                     timeout=6,check=True)
    demand(not any("laya_cell.source_readiness" in x
                   and str(CANARY/"cache-status.json") in x
                   for x in p.stdout.splitlines()),
           "FIXED_CANARY_CACHE_MONITOR_STILL_ACTIVE")
    return {"precommit_sha256":SOURCE_PLAN_SHA,
            "comparison_sha256":sha(CANARY/"comparison.json"),
            "comparison_state":receipt["state"],
            "baseline_sha256":receipt["baseline"]["captured_sha256"],
            "canary_sha256":receipt["canary"]["captured_sha256"],
            "capture_at_ms":captured,"comparison_durable_at_ms":durable}

def shared_gpu_lease_available(guard_root):
    """Verify the same exclusive GPU lease used by the existing Value worker.

    A one-second Value service can exist without having acquired GPU; do not
    mistake that transient PID for an admitted GPU job. Once its lease is held,
    this independently opened file fails LOCK_EX|LOCK_NB and cutover stops.
    """
    root=Path(guard_root)
    demand(root.is_dir() and not root.is_symlink() and
           root.stat().st_uid==os.getuid(),
           "SHARED_GPU_LEASE_ROOT_UNTRUSTED")
    lock=root/"heavy-worker.lock"
    demand(lock.is_file() and not lock.is_symlink(),
           "SHARED_GPU_LEASE_MISSING")
    fd=os.open(lock,os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        info=os.fstat(fd)
        demand(stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid()
               and info.st_mode&0o077==0,
               "SHARED_GPU_LEASE_OWNER_OR_MODE_CHANGED")
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise EvidenceError("SHARED_GPU_LEASE_HELD") from None
        finally:
            # Immediately release the inspection lease. The existing real
            # Guard remains the only authority to admit a future GPU worker.
            pass
        fcntl.flock(fd,fcntl.LOCK_UN)
    finally:os.close(fd)


def controlled_jobs():
    root=Path.home()/".local/state/heptatrader/neuron-learning/laya-resident-reset-1791349390533/control/status.json"
    doc=read(private_existing(root))
    jobs=doc.get("job_counts",{})
    demand(type(jobs)is dict and jobs.get("RUNNING")==0 and jobs.get("ADMITTED")==0,
           "RESEARCH_CANDIDATE_RUNNING_OR_ADMITTED")
    now=time.time_ns()//1000000
    stamp=doc.get("at_ms")
    demand(type(stamp)is int and 0<=now-stamp<MAX_CONTROL_AGE_MS,
           "RESEARCH_CONTROL_STATE_STALE")
    demand(doc.get("canonical_agentd_owner_connected") is False
           and doc.get("automatic_trading") is False
           and doc.get("broker_orders_sent")==0,
           "RESEARCH_TRADE_OR_OWNER_AUTHORITY_CHANGED")
    # Guard checks still apply; known heavy worker must have exited.
    p=subprocess.run(["ps","-eo","args="],capture_output=True,
                     text=True,timeout=5,check=True)
    prohibited=("laya_cell.resident_train","laya_cell.resident_evaluate",
                "laya_cell.temporal_fusion_train")
    active=[line for line in p.stdout.splitlines()
            if any(word in line for word in prohibited)]
    demand(not active,"RESEARCH_GPU_WORKER_IN_FLIGHT")
    # Value's per-second source-check worker is harmless while outside the
    # shared exclusive lease. Both research configurations MUST agree on the
    # exact same guard root before considering it idle.
    old_guard=read(private_existing(
        Path.home()/".config/heptatrader-resident-resource-guard/training-repair-d20801cce510-namespace.json"))
    value_guard=read(private_existing(
        Path.home()/".config/heptatrader-resident-resource-guard/value-role-08a5f87c8e8c.json"))
    demand(old_guard.get("state_root")==value_guard.get("state_root") and
           old_guard.get("max_heavy_workers")==value_guard.get("max_heavy_workers")==1,
           "SHARED_GPU_GUARD_CONFIG_DIFFERENT")
    shared_gpu_lease_available(old_guard["state_root"])
    return jobs

def staged_artifacts():
    proof=read(private_existing(STAGED/"receipt.json"))
    stage_guard=read(private_existing(STAGED/"guard.json"))
    candidate=read(private_existing(STAGED/"resident.json"))
    demand(proof.get("schema")=="heptatrader.laya-archive-runtime-cutover.v1"
           and proof.get("systemd_unit_modified") is False
           and proof.get("services_restarted") is False
           and proof.get("trade_authority") is False
           and proof.get("automatic_promotion_enabled") is False
           and proof.get("canonical_owner_enrolled") is False,
           "ARCHIVE_STAGED_RECEIPT_AUTHORITY_DRIFT")
    demand(sha(STAGED/"guard.json")==proof["new_guard_sha256"] and
           sha(STAGED/"resident.json")==proof["new_config_sha256"] and
           stage_guard.get("original_config")==str(STAGED/"resident.json") and
           stage_guard.get("original_config_sha256")==proof["new_config_sha256"],
           "ARCHIVE_STAGED_CONFIG_BYTES_CHANGED")
    old_guard=Path.home()/".config/heptatrader-resident-resource-guard/training-repair-d20801cce510-namespace.json"
    original=Path(read(private_existing(old_guard))["original_config"])
    old_g,old_config,old_runtime=validate_old(old_guard,original)
    demand(sha(old_guard)==proof["old_guard_sha256"] and
           sha(original)==proof["old_config_sha256"],
           "ARCHIVE_ORIGINAL_GUARD_REPLACED")
    demand(candidate.get("runtime_directory")==proof["new_runtime"]
           and candidate.get("automatic_trading") is False and
           candidate.get("canonical_owner_endpoint") is None
           and stage_guard.get("trade_authority") is False,
           "ARCHIVE_CANDIDATE_AUTHORITY_CHANGED")
    pins=candidate.get("runtime_sha256",{})
    demand(len(pins)==54 and
           set(pins)==set(old_config["runtime_sha256"])|{"learn/laya_cell/source_archive.py"},
           "ARCHIVE_UNEXPECTED_PIN_SET")
    target=Path(proof["new_runtime"])
    demand(target.is_dir() and not target.is_symlink(),
           "ARCHIVE_RELEASE_DIRECTORY_DRIFT")
    for name,expected in pins.items():
        file=target/name
        demand(file.is_file() and not file.is_symlink() and
               sha(file)==expected,"ARCHIVE_SOURCE_HASH_DRIFT:"+name)
    plan=read(private_existing(STAGED/"unit-plan/unit-plan.json"))
    demand(plan.get("schema")=="heptatrader.laya-archive-guard-systemd-plan.v1"
           and plan.get("guard_sha256")==proof["new_guard_sha256"]
           and plan.get("systemd_deployed") is False
           and plan.get("systemd_restarted") is False
           and plan.get("canonical_owner_granted") is False,
           "ARCHIVE_UNIT_PLAN_IDENTITY_DRIFT")
    check={}
    for role,unit in ROLE_UNIT.items():
        entry=plan["service_dropins"][role]
        staged=STAGED/"unit-plan"/f"zzzz-archive-guard-pin-20261013-{role}.conf"
        body=render_dropin(role,STAGED/"guard.json",target).encode()
        demand(str(staged)==entry["staged_path"] and
               sha(staged)==entry["sha256"]==EXPECTED[role] and
               staged.read_bytes()==body,"ARCHIVE_DROPIN_HASH_OR_BODY_DRIFT:"+role)
        previous=private_existing(STAGED/f"{role}-effective-old-unit.txt").read_bytes()
        demand(systemctl("cat",unit).encode()==previous,
               "ARCHIVE_PREVIOUS_EFFECTIVE_UNIT_CHANGED:"+role)
        dest=Path.home()/".config/systemd/user"/(unit+".d")/CONF
        demand(not dest.exists() and not dest.is_symlink(),
               "ARCHIVE_ACTIVATION_ALREADY_ATTEMPTED:"+role)
        check[role]={"old_effective_sha256":hashlib.sha256(previous).hexdigest(),
                     "new_sha256":EXPECTED[role],"destination":str(dest),
                     "old_active_state":systemctl("show",unit,"-p","ActiveState").strip()}
        demand(check[role]["old_active_state"]=="ActiveState=active",
               "ARCHIVE_PREVIOUS_SERVICE_NOT_RUNNING:"+role)
    return proof,check


def isolated_guard_check():
    from historical_training.laya_guard_archive_cutover import SOURCE_PIN
    # Strict pinned source and unchanged runtime manifest have already passed.
    # The original guard module's validation uses the new immutable config.
    script=Path.home()/".local/share/heptatrader/laya-runtime/venv/bin/python"
    root=Path.home()/".local/share/heptatrader/resident-resource-guard/training-repair-d20801cce510-namespace"
    newroot=read(private_existing(STAGED/"resident.json"))["runtime_directory"]
    env=dict(os.environ)
    env["PYTHONPATH"]=f"{root}:{newroot}/learn:{newroot}/scripts"
    checks={}
    for role in ROLE_UNIT:
        argv=[str(script),"-B","-m","historical_training.resident_resource_guard",
              "--config",str(STAGED/"guard.json"),
              "--role",role,"--check-only"]
        p=subprocess.run(argv,capture_output=True,text=True,timeout=18,
                         env=env,check=False)
        demand(p.returncode==0,
               "ARCHIVE_NEW_GUARD_REJECTED:"+role)
        checks[role]="PASSED"
    return checks


def preview(*,now_ms=None):
    now=time.time_ns()//1000000 if now_ms is None else now_ms
    canary=collect_canary(now)
    proof,units=staged_artifacts()
    jobs=controlled_jobs()
    guards=isolated_guard_check()
    return {"schema":"heptatrader.laya-archive-guard-activation-preflight.v1",
            "at_ms":now,"protected_cache_canary":canary,"source_pin":proof["source_commit"],
            "candidate_guard_sha256":proof["new_guard_sha256"],
            "units":units,"job_counts":jobs,"new_guard_checks":guards,
            "canonical_owner_granted":False,
            "automatic_trading":False,"broker_orders_sent":0}

def activate():
    preflight=preview()
    demand(not OUTPUT.exists() and not OUTPUT.is_symlink(),
           "ACTIVATION_OUTPUT_ALREADY_EXISTS")
    OUTPUT.mkdir(mode=0o700,parents=True)
    demand(OUTPUT.stat().st_uid==os.getuid(),"ACTIVATION_PRIVATE_SCOPE")
    write_once(OUTPUT/"preflight.json",
               json.dumps(preflight,sort_keys=True,separators=(",",":")).encode()+b"\n")
    installed=[]
    try:
        # Last-minute frontiers before touching the original user units.
        controlled_jobs()
        collect_canary(time.time_ns()//1000000)
        for role,unit in ROLE_UNIT.items():
            dest=Path(preflight["units"][role]["destination"])
            demand(dest.parent.is_dir() and not dest.parent.is_symlink(),
                   "ARCHIVE_UNIT_DIRECTORY_CHANGED:"+role)
            source=STAGED/"unit-plan"/f"zzzz-archive-guard-pin-20261013-{role}.conf"
            demand(sha(source)==EXPECTED[role],
                   "ARCHIVE_DROPIN_CHANGED_DURING_INSTALL")
            write_once(dest,source.read_bytes())
            installed.append((role,dest))
        # Original user root units and their isolation controls are unchanged.
        systemctl("daemon-reload")
        for role in ("evaluator","trainer"):
            systemctl("restart",ROLE_UNIT[role])
        time.sleep(3)
        for role,unit in ROLE_UNIT.items():
            show=systemctl("show",unit,"-p","ActiveState","-p","ExecStart","-p","DropInPaths")
            demand("ActiveState=active" in show and
                   str(STAGED/"guard.json") in show and
                   CONF in show,
                   "ARCHIVE_ACTIVATED_UNIT_NOT_EFFECTIVE:"+role)
            # This tests a real systemd cgroup and unit resolution, not only
            # a preparatory source import or an active-only proxy.
        outcome={
            "schema":"heptatrader.laya-archive-guard-activation-result.v1",
            "state":"GUARDED_RESEARCH_SERVICE_CUTOVER_APPLIED",
            "original_canary_sha256":preflight["protected_cache_canary"]["comparison_sha256"],
            "preflight_sha256":sha(OUTPUT/"preflight.json"),
            "new_config_sha256":preflight["candidate_guard_sha256"],
            "installed_dropin_sha256":{r:sha(p) for r,p in installed},
            "roles_restarted":list(ROLE_UNIT),
            "original_units_not_overwritten":True,
            "original_quote_sources_not_modified":True,
            "full_training_qualification_claimed":False,
            "canonical_owner_granted":False,
            "trading_services_restarted":False,
            "broker_orders_sent":0,
        }
        write_once(OUTPUT/"result.json",
                   json.dumps(outcome,sort_keys=True,separators=(",",":")).encode()+b"\n")
        return outcome
    except BaseException as exc:
        rollback={"schema":"heptatrader.laya-archive-guard-rollback.v1",
                  "reason_type":type(exc).__name__,
                  "reason":str(exc)[:180],
                  "installed_paths_removed":[],"old_units_restarted":[],
                  "state":"ROLLBACK_ATTEMPTED_NO_PROMOTION",
                  "trade_authority":False,"broker_orders_sent":0}
        try:
            # Only delete the exact files just created by this invocation.
            for role,path in reversed(installed):
                if path.exists() and not path.is_symlink() and sha(path)==EXPECTED[role]:
                    path.unlink()
                    rollback["installed_paths_removed"].append(role)
            systemctl("daemon-reload")
            for role in ("evaluator","trainer"):
                systemctl("restart",ROLE_UNIT[role])
                rollback["old_units_restarted"].append(role)
        except BaseException as rollback_exc:
            rollback["state"]="ROLLBACK_UNCERTAIN_MANUAL_OPERATOR_REQUIRED"
            rollback["rollback_error"]=type(rollback_exc).__name__
        try:
            write_once(OUTPUT/"rollback.json",
                       json.dumps(rollback,sort_keys=True,separators=(",",":")).encode()+b"\n")
        except BaseException:
            pass
        raise


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--mode",choices=("preflight","activate"),required=True)
    opts=p.parse_args()
    os.umask(0o077)
    if opts.mode=="preflight":
        result=preview()
    else:
        result=activate()
    print(json.dumps(result,sort_keys=True),flush=True)

if __name__=="__main__":
    main()
