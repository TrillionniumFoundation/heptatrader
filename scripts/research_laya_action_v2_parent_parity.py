"""Bounded, read-only original-parent numerical parity isolation on Pocket4.

One original frozen base and adapter; compare default ROCm SDPA,
MATH-only ROCm SDPA and CPU math on exactly three SHA-bound inputs.
No model training, promotion, trading operation or live service changes.
"""
from __future__ import annotations
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import time

from scripts.research_laya_action_v2_gpu_isolated import (
    source_bytes, strict_pairs, partition, write_once, GUARD_CONFIG, SCHEMA,
    deadline,
)


def main():
    import torch
    from torch.nn.attention import sdpa_kernel, SDPBackend
    from safetensors.torch import load_file
    from historical_training.resident_resource_guard import Gate,verify_config
    from laya_cell.integrity import read_json
    from laya_cell.model import load_verified_laya
    from laya_cell.resident_model import ResidentDecisionCell
    from laya_cell.dataset import encode_rows
    from laya_cell.resident_train import validate_parent

    outdir=Path("/home/alex/.local/state/heptatrader/operator-evidence/action-v2-original-parent-parity-20261016-a1")
    if outdir.exists():raise RuntimeError("OUTPUT_DIR_ALREADY_EXISTS")
    os.umask(0o077);outdir.mkdir(mode=0o700)
    src=source_bytes();artifact=json.loads(src,object_pairs_hook=strict_pairs)
    m,train,hold=partition(artifact)
    samples=[train[0],train[1],hold[0]]
    config=read_json(GUARD_CONFIG);original=verify_config(config)
    gate=Gate(config,"trainer")
    if not gate.acquire({"mode":"original_gpu_math_cpu_parity","broker_orders_sent":0}):
        write_once(outdir/"denied.json",{"schema":SCHEMA,"status":"NO_GPU_ADMISSION",
                   "reasons":gate.last_admission.get("reasons",[]),
                   "no_model_forward":True,"broker_orders_sent":0})
        print("NO_GPU_ADMISSION",gate.last_admission.get("reasons",[]),flush=True)
        return 3
    start=time.monotonic()
    report={"schema":"heptatrader.action-v2-original-parent-numerical-parity.v1",
            "source_sha256":hashlib.sha256(src).hexdigest(),
            "parent_id":m["frozen_parent_id"],"samples":[],
            "torch_version":torch.__version__,"hip_version":torch.version.hip,
            "broker_orders_sent":0,"optimizer_steps":0,"model_promoted":False}
    try:
        parent=Path(original["initial_parent_directory"]);pm=validate_parent(parent)
        if pm["candidate_id"]!=m["frozen_parent_id"]:
            raise RuntimeError("PARENT_ID_CHANGED")
        base=Path(original["base_directory"])
        raw,tok,bid=load_verified_laya(base,read_json(base/"base-manifest.json"))
        if bid!=pm["base_id"]:raise RuntimeError("BASE_ID_CHANGED")
        cell=ResidentDecisionCell(raw,bid)
        cell.restore_parent(load_file(str(parent/"adapter.safetensors")),allow_scorer_only=True)
        cell.eval()
        print("PARENT_LOADED",round(time.monotonic()-start,3),flush=True)

        def forward(r,device,ctx):
            deadline(start)
            batch={k:v.to(device) for k,v in encode_rows(tok,[r]).items()}
            with torch.no_grad(),ctx:
                p,a=cell(**batch)
                probs=p.softmax(-1)[0].detach().cpu().tolist()
                raw=a.detach().cpu().tolist()[0]
            if not all(__import__("math").isfinite(v) for v in probs+raw):
                raise RuntimeError("NONFINITE_GPU_OR_CPU_LOGIT")
            return {"probs":probs,"gap":raw[0]-raw[1],
                    "delta":max(abs(x-y) for x,y in zip(probs,r["frozen_parent_probs"])),
                    "predicted_bin":max(range(7),key=lambda i:probs[i])}
        for i,r in enumerate(samples):
            expected=r["frozen_parent_probs"]
            obs={"row_index":i,"target_bin":r["target_class"],
                 "expected_predicted_bin":max(range(7),key=lambda j:expected[j]),
                 "expected_probability":expected,
                 "gpu_default":forward(r,"cuda:0",nullcontext()),
                 "gpu_math":forward(r,"cuda:0",sdpa_kernel([SDPBackend.MATH]))}
            report["samples"].append(obs)
            print("GPU_COMPARE",i,"default",obs["gpu_default"]["delta"],
                  "math",obs["gpu_math"]["delta"],flush=True)
        cell.cpu();cell.eval()
        for i,r in enumerate(samples):
            obs=forward(r,"cpu",nullcontext())
            report["samples"][i]["cpu_math"]=obs
            print("CPU_COMPARE",i,obs["delta"],flush=True)
        cell.assert_frozen()
        report["status"]="DIAGNOSIS_RECORDED_NOT_QUALIFIED"
        report["wall_seconds"]=time.monotonic()-start
        report["program_sha256"]=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        sha=write_once(outdir/"parity-diagnostic.json",report)
        print("EVIDENCE_SHA256",sha,"seconds",round(report["wall_seconds"],3),flush=True)
        return 0
    except Exception as exc:
        report["status"]="DIAGNOSTIC_FAILED";report["error_type"]=type(exc).__name__
        report["error_code"]=str(exc)[:140]
        write_once(outdir/"failed.json",report)
        print("FAILED",report["error_code"],flush=True)
        return 2
    finally:gate.release()

if __name__=="__main__":
    raise SystemExit(main())
