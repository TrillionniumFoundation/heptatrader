"""Original closed-source parent replay: fixed 8 timestamps/day × 3 UTC days.

Observe only, never repair or reclassify original training labels.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import time

from scripts.research_laya_action_v2_gpu_isolated import (
    GUARD_CONFIG,source_bytes,strict_pairs,partition,write_once,deadline
)


def pick_eight(group):
    if len(group)<8:raise RuntimeError("INSUFFICIENT_PER_DAY")
    return [group[(i*(len(group)-1))//7] for i in range(8)]


def main():
    import torch
    from safetensors.torch import load_file
    from historical_training.resident_resource_guard import Gate,verify_config
    from laya_cell.integrity import read_json
    from laya_cell.model import load_verified_laya
    from laya_cell.resident_model import ResidentDecisionCell
    from laya_cell.resident_train import validate_parent
    from laya_cell.dataset import encode_rows

    out=Path("/home/alex/.local/state/heptatrader/operator-evidence/action-v2-original-parent-cohort-parity-20261016-a1")
    if out.exists():raise RuntimeError("EXISTING_EVIDENCE_DIR")
    os.umask(0o077);out.mkdir(mode=0o700)
    raw=source_bytes();artifact=json.loads(raw,object_pairs_hook=strict_pairs)
    m,train,hold=partition(artifact)
    days={}
    for r in train+hold:
        days.setdefault(r["anchor_ms"]//86400000,[]).append(r)
    if len(days)!=3:raise RuntimeError("EXPECTED_THREE_CLOSED_UTC_DAYS")
    for rows in days.values():rows.sort(key=lambda r:(r["anchor_ms"],r["row_id"]))
    selected=[(day,r) for day,rows in sorted(days.items()) for r in pick_eight(rows)]
    c=read_json(GUARD_CONFIG);original=verify_config(c);gate=Gate(c,"trainer")
    if not gate.acquire({"mode":"original_closed_parent_cohort_parity","orders":0}):
        write_once(out/"denied.json",{"status":"NO_GPU_ADMISSION",
                   "reasons":gate.last_admission.get("reasons",[]),
                   "training_steps":0,"broker_orders_sent":0})
        print("NO_GPU_ADMISSION",gate.last_admission.get("reasons",[]),flush=True)
        return 3
    start=time.monotonic()
    report={"schema":"heptatrader.action-v2-original-parent-24-replay.v1",
            "source_sha256":hashlib.sha256(raw).hexdigest(),
            "frozen_parent_id":m["frozen_parent_id"],"sample_rule":"FIXED_EVEN_EIGHT_BY_UTC_DAY_V1",
            "training_steps":0,"broker_orders_sent":0,"no_model_promotion":True,
            "day_results":{},"status":"INCOMPLETE"}
    try:
        base=Path(original["base_directory"])
        par=Path(original["initial_parent_directory"])
        pm=validate_parent(par)
        if pm["candidate_id"]!=m["frozen_parent_id"]:
            raise RuntimeError("PARENT_ID_CHANGED")
        torch.manual_seed(20261007)
        model,tok,base_id=load_verified_laya(base,read_json(base/"base-manifest.json"))
        if base_id!=pm["base_id"]:raise RuntimeError("BASE_ID_MISMATCH")
        cell=ResidentDecisionCell(model,base_id)
        cell.restore_parent(load_file(str(par/"adapter.safetensors")),allow_scorer_only=True)
        cell.eval()
        for day,row in selected:
            deadline(start)
            batch={k:v.to("cuda:0") for k,v in encode_rows(tok,[row]).items()}
            with torch.no_grad():p,a=cell(**batch)
            actual=p.softmax(-1)[0].detach().cpu().tolist()
            expected=row["frozen_parent_probs"]
            if any(not math.isfinite(v) for v in actual):
                raise RuntimeError("NONFINITE_REPLAY")
            err=max(abs(x-y) for x,y in zip(actual,expected))
            truth=row["target_class"]
            old_hit=max(range(7),key=lambda i:expected[i])==truth
            now_hit=max(range(7),key=lambda i:actual[i])==truth
            if old_hit!=row["parent_correct"]:raise RuntimeError("ORIGINAL_LABEL_CONFLICT")
            k=str(day);g=report["day_results"].setdefault(k,{"n":0,"max_error":0.0,"sum_error":0.0,
                    "original_hits":0,"replay_hits":0,"flipped_parent_correctness":0,
                    "predicted_class_changed":0,"parity_failures_over_5e-5":0})
            g["n"]+=1;g["max_error"]=max(err,g["max_error"]);g["sum_error"]+=err
            g["original_hits"]+=int(old_hit);g["replay_hits"]+=int(now_hit)
            g["flipped_parent_correctness"]+=int(old_hit!=now_hit)
            g["predicted_class_changed"]+=int(
                max(range(7),key=lambda i:expected[i])!=max(range(7),key=lambda i:actual[i]))
            g["parity_failures_over_5e-5"]+=int(err>5e-5)
            print("PARENT_REPLAY_DAY",day,g["n"],"max_error",round(g["max_error"],6),
                  "elapsed",round(time.monotonic()-start,2),flush=True)
        cell.assert_frozen()
        report["total_cases"]=sum(g["n"] for g in report["day_results"].values())
        report["total_parity_failures"]=sum(
            g["parity_failures_over_5e-5"] for g in report["day_results"].values())
        report["total_correctness_flips"]=sum(
            g["flipped_parent_correctness"] for g in report["day_results"].values())
        report["status"]="FAIL_ORIGINAL_SEALED_MODEL_REPLAY" if report["total_parity_failures"] else "PASS_PARITY_ONLY"
        report["elapsed_seconds"]=time.monotonic()-start
        report["script_sha256"]=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        digest=write_once(out/"cohort-parity.json",report)
        print("OUTCOME",report["status"],"TOTAL",report["total_cases"],
              "FAILED_PARITY",report["total_parity_failures"],
              "CORRECTNESS_FLIPS",report["total_correctness_flips"],
              "EVIDENCE_SHA",digest,flush=True)
        return 0 if report["total_cases"]==24 else 2
    except Exception as e:
        report["status"]="PROBE_ABORTED";report["error_type"]=type(e).__name__
        report["error_code"]=str(e)[:120]
        write_once(out/"failure.json",report);print("ABORTED",report["error_code"],flush=True)
        return 2
    finally:gate.release()


if __name__=="__main__":
    raise SystemExit(main())
