#!/usr/bin/env python3
"""Pocket4: three fixed historical action-weight arms, with no model promotion."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import time

SOURCE = Path("/home/alex/.local/state/heptatrader/operator-evidence/laya-original-quote-asof-forensics-20261016/action-v2-real-closed-parent298-trainonly.json")
SOURCE_SHA = "3d701e3795cb438b5ba38f3bb7c44bed83003e040108d1378d1084635157e4a9"
GUARD_CONFIG = Path("/home/alex/.local/state/heptatrader/operator-evidence/laya-fx-active-manifest-registration-20261016-r1/trainer-fx-reward-guard.v1.json")
TRAIN_CUTOFF = 1791504000000
HOLDOUT_END = 1791590400000
MAX_SECONDS = 600
SEED = 20261007


def write_once(path, value):
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    with Path(path).open("xb") as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    Path(path).chmod(0o400)
    return hashlib.sha256(raw).hexdigest()


def source_partition():
    from laya_cell.integrity import read_json, sha_file
    if sha_file(SOURCE) != SOURCE_SHA:
        raise RuntimeError("FROZEN_SOURCE_CHANGED")
    source = read_json(SOURCE, 4 * 1024**2)
    train, hold = [], []
    for row in source["rows"]:
        if row["anchor_ms"] < TRAIN_CUTOFF:
            if row["future_outcome_available_ms"] > TRAIN_CUTOFF:
                raise RuntimeError("TRAIN_OUTCOME_AFTER_CUTOFF")
            train.append(row)
        elif row["anchor_ms"] < HOLDOUT_END:
            hold.append(row)
        else:
            raise RuntimeError("UNEXPECTED_FUTURE_ROW")
    train.sort(key=lambda r: (r["anchor_ms"], r["row_id"]))
    hold.sort(key=lambda r: (r["anchor_ms"], r["row_id"]))
    if (len(train), len(hold), sum(r["parent_correct"] for r in train),
        sum(r["parent_correct"] for r in hold)) != (178, 120, 9, 5):
        raise RuntimeError("FROZEN_SPLIT_CHANGED")
    if len({r["row_id"] for r in train + hold}) != 298:
        raise RuntimeError("DUPLICATE_ORIGINAL_ROWS")
    # Train-only proportional stratification keeps the rare ACT class present.
    # Freeze 2 ACT + 30 ESCALATE before any holdout forward, identical in all arms.
    groups = [[r for r in train if r["parent_correct"] is flag] for flag in (True, False)]
    counts = [round(32 * len(groups[0]) / len(train)), 0]
    counts[1] = 32 - counts[0]
    if counts != [2, 30]:
        raise RuntimeError("FROZEN_TRAIN_STRATIFICATION_CHANGED")
    chosen = [rows[round(i * (len(rows)-1)/(count-1))]
              for rows, count in zip(groups, counts) for i in range(count)]
    chosen.sort(key=lambda r: (r["anchor_ms"], r["row_id"]))
    return source, train, hold, [chosen[i:i+2] for i in range(0, 32, 2)]


class OfflineAblationAdmission:
    """A separate user-requested historical scope, reusing the current resource
    thresholds, manual hold, Value priority and original global heavy lease.
    This does not change or grant the production live-source admission."""
    def __init__(self):
        from historical_training.resident_resource_guard import Gate, verify_config
        from laya_cell.integrity import read_json
        self.config = read_json(GUARD_CONFIG)
        self.original = verify_config(self.config)
        self.gate = Gate(self.config, "trainer")
        self.last_admission = None

    def observe(self):
        from historical_training.resident_resource_guard import parse_meminfo, parse_full_psi
        from historical_training.value_priority_admission import decision
        from laya_cell.integrity import sha_file
        c = self.config; rule = c["resource_rule"]; reasons = []
        mem = parse_meminfo(Path("/proc/meminfo").read_text())
        psi = parse_full_psi(Path("/proc/pressure/memory").read_text())
        if mem["MemAvailable"] < rule["min_available_bytes"]: reasons.append("HOST_AVAILABLE_MEMORY_BELOW_8GIB")
        if mem["SwapFree"] < rule["min_swap_free_bytes"]: reasons.append("HOST_SWAP_RESERVE_BELOW_1GIB")
        if psi["avg10"] > rule["max_full_psi_avg10"] or psi["avg60"] > rule["max_full_psi_avg60"]:
            reasons.append("HOST_FULL_MEMORY_PSI_PRESSURE")
        if os.path.lexists(c["manual_hold_path"]): reasons.append("MANUAL_RESEARCH_HOLD")
        if sha_file(Path(c["period_path"])) != c["period_sha256"]:
            reasons.append("PERIOD_CHANGED")
        if sha_file(SOURCE) != SOURCE_SHA: reasons.append("FROZEN_SOURCE_CHANGED")
        held, reason, proof = decision(c["value_priority"], "trainer", int(time.time()*1000))
        if held: reasons.append(reason or "VALUE_PRIORITY_HOLD")
        self.last_admission = {
            "schema": "heptatrader.offline-action-weight-ablation-admission.v1",
            "at_ms": int(time.time()*1000), "admitted": not reasons, "reasons": reasons,
            "mem_available_bytes": mem["MemAvailable"], "swap_free_bytes": mem["SwapFree"],
            "full_psi": psi, "resource_rule": rule,
            "value_priority_held": held, "value_priority_reason": reason,
            "source_sha256": SOURCE_SHA, "training_rows_per_arm": 32,
            "attempted_steps_per_arm": 16, "arms": 3, "maximum_seconds": MAX_SECONDS,
            "scope": "SEALED_HISTORICAL_DIAGNOSTIC_ONLY",
            "production_live_source_qualified": False,
            "model_promotion": False, "trade_authority": False, "orders_sent": 0}
        return not reasons

    def acquire(self):
        if not self.observe(): return False
        if not self.gate.lease.acquire():
            self.last_admission["admitted"] = False
            self.last_admission["reasons"].append("OTHER_RESEARCH_WORKER_ACTIVE")
            return False
        try:
            if not self.observe():
                self.gate.lease.release(); return False
        except Exception:
            self.gate.lease.release(); raise
        self.last_admission["original_heavy_lease_acquired"] = True
        return True

    def release(self):
        self.gate.lease.release()


def run(out, admission, source, train, hold, batches, sandbox_proof):
    import torch
    from torch.nn import functional as F
    from safetensors.torch import load_file, save_file
    from laya_cell.integrity import read_json, digest, sha_file
    from laya_cell.model import load_verified_laya, tensor_hash
    from laya_cell.resident_model import ResidentDecisionCell, action_loss
    from laya_cell.resident_train import validate_parent
    from laya_cell.train import supervised_loss
    from laya_cell.original_input_order import encode_original_forward_rows
    from laya_cell.resident_action_v2_closed_source import check_train_source
    from laya_cell.resident_action_v2_research import decide_train_only_action
    from laya_cell.resident_action_ablation import branch_gradient_diagnostics, freeze_weight

    start = time.monotonic()
    report = {"schema": "heptatrader.historical-action-weight-ablation.v1",
              "status": "IN_PROGRESS", "source_sha256": SOURCE_SHA,
              "program_sha256": sha_file(Path(__file__)), "sandbox": sandbox_proof,
              "admission": admission.last_admission, "broker_orders_sent": 0,
              "model_promoted": False, "historical_holdout_not_prospective": True,
              "holdout_previously_used_for_diagnostics": True, "financial_reward_used": False,
              "train_cutoff_ms": TRAIN_CUTOFF, "holdout_end_ms": HOLDOUT_END,
              "training_pool_rows": 178, "heldout_rows": 120, "rows_per_arm": 32,
              "attempted_steps_per_arm": 16, "seed": SEED,
              "batch_order": [[r["row_id"] for r in b] for b in batches],
              "selected_train_act": sum(r["parent_correct"] for b in batches for r in b),
              "selection": "TRAIN_ONLY_PROPORTIONAL_STRATIFICATION_2_ACT_30_ESCALATE",
              "auxiliary_teacher_labels_available": False,
              "input_order": "ORIGINAL_LIVE_CAUSAL_STATE_INSERTION_ORDER"}
    def deadline():
        if time.monotonic() - start > MAX_SECONDS:
            raise RuntimeError("OFFLINE_ABLATION_DEADLINE")

    try:
        manifest = check_train_source(source)
        original = admission.original
        parent_dir = Path(original["initial_parent_directory"])
        parent = validate_parent(parent_dir)
        if parent["candidate_id"] != manifest["frozen_parent_id"]:
            raise RuntimeError("FROZEN_PARENT_ID_CHANGED")
        cutoff = parent["trained_until_ms"] if "trained_until_ms" in parent else parent["window"]["training_end_ms"]
        if any(r["anchor_ms"] < cutoff for r in train + hold):
            raise RuntimeError("FROZEN_PARENT_AFTER_ANCHOR")
        torch.manual_seed(SEED)
        base = Path(original["base_directory"])
        raw, tok, base_id = load_verified_laya(base, read_json(base/"base-manifest.json"))
        if base_id != parent["base_id"]: raise RuntimeError("PARENT_BASE_ID_CHANGED")
        cell = ResidentDecisionCell(raw, base_id)
        cell.restore_parent(load_file(str(parent_dir/"adapter.safetensors")), allow_scorer_only=True)
        initial = cell.trainable_state()
        initial_id = digest({n: tensor_hash(v) for n, v in initial.items()})
        report.update(parent_id=parent["candidate_id"], base_id=base_id,
                      initialization_sha256=initial_id,
                      trainable_parameters=sum(v.numel() for v in initial.values()))
        trainables = [p for p in cell.parameters() if p.requires_grad]
        torch.cuda.reset_peak_memory_stats()
        cpu_rng = torch.get_rng_state(); gpu_rng = torch.cuda.get_rng_state_all()
        print("PARENT_LOADED", report["trainable_parameters"], flush=True)

        def forward(rows, stage, verify=False):
            prices, actions = [], []
            cell.eval()
            worst = 0.
            with torch.no_grad():
                for i, row in enumerate(rows):
                    deadline()
                    batch = {k: v.to("cuda:0") for k, v in encode_original_forward_rows(tok,[row]).items()}
                    p, a = cell(**batch)
                    pp = p.softmax(-1)[0].detach().cpu().tolist()
                    aa = a[0].detach().double().cpu().tolist()
                    if not all(math.isfinite(x) for x in pp+aa): raise RuntimeError("NONFINITE_FORWARD")
                    if verify:
                        err = max(abs(x-y) for x,y in zip(pp,row["frozen_parent_probs"]))
                        if err > 5e-5: raise RuntimeError("ORIGINAL_PARENT_PRICE_PARITY")
                        if (max(range(7),key=pp.__getitem__)==row["target_class"]) != row["parent_correct"]:
                            raise RuntimeError("ORIGINAL_PARENT_CORRECTNESS_PARITY")
                        worst = max(worst,err)
                    prices.append(pp); actions.append(aa)
                    if (i+1)%40==0 or i+1==len(rows):
                        print("FORWARD",stage,i+1,"seconds",round(time.monotonic()-start,2),flush=True)
            return prices, actions, worst

        batch_parity=[]
        report["calibration_batch_parent_parity"]=batch_parity
        def losses(selected, verify_parent_batch=False):
            batch={k:v.to("cuda:0") for k,v in encode_original_forward_rows(tok,selected).items()}
            truth=torch.tensor([r["target_class"] for r in selected],device="cuda:0")
            targets=torch.tensor([0 if r["parent_correct"] else 1 for r in selected],device="cuda:0")
            p,a=cell(**batch)
            if verify_parent_batch:
                probs=p.softmax(-1).detach().cpu().tolist()
                matches=sum((max(range(7),key=v.__getitem__)==r["target_class"])==r["parent_correct"] for v,r in zip(probs,selected))
                batch_parity.append({"row_ids":[r["row_id"] for r in selected],"correctness_matches":matches,"rows":len(selected),"maximum_probability_error":max(abs(x-y) for v,r in zip(probs,selected) for x,y in zip(v,r["frozen_parent_probs"]))})
                if matches!=len(selected):raise RuntimeError("INITIAL_B2_PARENT_CORRECTNESS_DRIFT")
            teacher=torch.zeros((len(selected),7),device="cuda:0")
            mask=torch.zeros(len(selected),device="cuda:0",dtype=torch.bool)
            return supervised_loss(p,truth,teacher,mask), action_loss(a,targets)

        parent_price_train,parent_action_train,train_parity=forward(train,"parent-train",True)
        temperature_profile=decide_train_only_action(
            train,cutoff_ms=TRAIN_CUTOFF,
            parent_action_logits=torch.tensor(parent_action_train,dtype=torch.float64))
        temperature=float(temperature_profile["action_temperature_training_only"])
        calibration=[]
        cell.train(True)
        for i, selected in enumerate(batches):
            deadline(); pl,al=losses(selected, verify_parent_batch=True)
            calibration.append(branch_gradient_diagnostics(pl,al,cell.named_parameters(),.01))
            print("CALIBRATION",i+1,"price",calibration[-1]["price_l2"],
                  "action",calibration[-1]["action_l2"],flush=True)
        if digest({n:tensor_hash(v) for n,v in cell.trainable_state().items()})!=initial_id:
            raise RuntimeError("CALIBRATION_CHANGED_INITIALIZATION")
        frozen=freeze_weight(calibration,provenance={
            "split":"train", "source_sha256":SOURCE_SHA, "initialization_sha256":initial_id,
            "batch_order_sha256":digest(report["batch_order"]), "train_cutoff_ms":TRAIN_CUTOFF},
            target_ratio=1.)
        profile={"frozen_weight":frozen,"temperature_training_only":temperature,
                 "temperature_profile":temperature_profile,"calibration":calibration,
                 "arms":[{"name":"price_only","weight":0.},
                         {"name":"current_001","weight":.01},
                         {"name":"gradient_equalized","weight":frozen["action_weight"]}]}
        report["profile_sha256"]=write_once(out/"frozen-train-profile.json",profile)
        # The third weight and temperature are now persisted, before any holdout
        # forward scores are computed. The historical labels were already public.
        parent_price_hold,parent_action_hold,hold_parity=forward(hold,"parent-hold",True)
        report["parent_replay_rows"]=298
        report["max_original_parent_probability_error"]=max(train_parity,hold_parity)

        truth=torch.tensor([r["target_class"] for r in hold],dtype=torch.int64)
        target_action=torch.tensor([0 if r["parent_correct"] else 1 for r in hold],dtype=torch.int64)
        labels=F.one_hot(truth,7).double()
        def score(prices,actions):
            p=torch.tensor(prices,dtype=torch.float64)
            a=torch.tensor(actions,dtype=torch.float64)
            raw_logp=a.log_softmax(-1);cal_logp=(a/temperature).log_softmax(-1)
            act_labels=F.one_hot(target_action,2).double()
            return {"rows":len(hold),"price_nll":float(-p.clamp_min(1e-30).log()[range(len(hold)),truth].mean()),
                    "price_brier":float(((p-labels)**2).sum(-1).mean()),
                    "price_accuracy":float((p.argmax(-1)==truth).double().mean()),
                    "raw_action_nll":float(-raw_logp[range(len(hold)),target_action].mean()),
                    "raw_action_brier":float(((raw_logp.exp()-act_labels)**2).sum(-1).mean()),
                    "train_temperature_action_nll":float(-cal_logp[range(len(hold)),target_action].mean()),
                    "train_temperature_action_brier":float(((cal_logp.exp()-act_labels)**2).sum(-1).mean()),
                    "calibrated_act_at_5_of_6":int((cal_logp.exp()[:,0]>=5/6).sum())}
        report["parent_holdout"]=score(parent_price_hold,parent_action_hold)
        counts=[sum(r["target_class"]==i for r in train)+.5 for i in range(7)]
        price_prior=[v/sum(counts) for v in counts]
        action_prior=(9+.5)/(178+1)
        prior_logits=[[math.log(action_prior),math.log(1-action_prior)]]*len(hold)
        prior_p=torch.tensor(price_prior,dtype=torch.float64).repeat(len(hold),1)
        report["train_prior_holdout"]={
            "price_brier":float(((prior_p-labels)**2).sum(-1).mean()),
            "price_nll":float(-prior_p.log()[range(len(hold)),truth].mean()),
            "action_probability":action_prior,
            "action_brier":2*sum((action_prior-int(r["parent_correct"]))**2 for r in hold)/len(hold),
            "action_nll":-sum(math.log(action_prior if r["parent_correct"] else 1-action_prior) for r in hold)/len(hold)}
        report["arms"]=[]
        states={}
        for arm in profile["arms"]:
            cell.restore_trainable(initial);torch.set_rng_state(cpu_rng);torch.cuda.set_rng_state_all(gpu_rng)
            if digest({n:tensor_hash(v) for n,v in cell.trainable_state().items()})!=initial_id:
                raise RuntimeError("ARM_INITIALIZATION_CHANGED")
            optimizer=torch.optim.AdamW(trainables,lr=.0001,weight_decay=0.)
            cell.train(True); steps=[]
            for i,selected in enumerate(batches):
                deadline();saved=cell.trainable_state();opt=copy.deepcopy(optimizer.state_dict())
                optimizer.zero_grad(set_to_none=True)
                pl,al=losses(selected)
                diag=branch_gradient_diagnostics(pl,al,cell.named_parameters(),arm["weight"])
                total=pl+arm["weight"]*al
                total.backward()
                grad_norm=torch.nn.utils.clip_grad_norm_(trainables,1.,error_if_nonfinite=True)
                optimizer.step()
                norm=cell.norm_report();applied=norm["within_profile"]
                if not applied:cell.restore_trainable(saved);optimizer.load_state_dict(opt)
                steps.append({"step":i+1,"applied":applied,"price_loss":float(pl.detach()),
                              "action_loss":float(al.detach()),"total_loss":float(total.detach()),
                              "gradient_norm_before_clip":float(grad_norm),"diagnostics":diag,
                              "within_norm_profile":applied})
                print("ARM_STEP",arm["name"],i+1,"applied",applied,
                      "seconds",round(time.monotonic()-start,2),flush=True)
            cell.eval();cell.assert_frozen()
            prices,actions,_=forward(hold,arm["name"]+"-hold")
            state={n:v.detach().cpu().contiguous() for n,v in cell.trainable_state().items()}
            states[arm["name"]]=state
            changes={}
            for branch,marker in [("price",".scorer."),("action",".act_head.")]:
                delta=[(v-initial[n].cpu()).double() for n,v in state.items() if marker in n]
                changes[branch]={"factor_delta_l2":math.sqrt(sum(float(v.square().sum()) for v in delta)),
                                 "changed_coordinates":sum(int((v!=0).sum()) for v in delta)}
            artifact=out/(arm["name"]+".safetensors");save_file(state,str(artifact));artifact.chmod(0o400)
            result={**arm,"attempted_steps":len(steps),"applied_steps":sum(s["applied"] for s in steps),
                    "rollback_steps":sum(not s["applied"] for s in steps),"parameter_changes":changes,
                    "holdout":score(prices,actions),"steps":steps,
                    "adapter_sha256":sha_file(artifact),"norm":cell.norm_report()}
            write_once(out/(arm["name"]+"-holdout.json"),
                       {"row_ids":[r["row_id"] for r in hold],"prices":prices,"actions":actions})
            report["arms"].append(result)
            write_once(out/(arm["name"]+"-result.json"),result)
        reference=states["price_only"]
        report["price_adapter_difference_from_price_only"]={
            name:math.sqrt(sum(float((v-reference[n]).double().square().sum())
                               for n,v in state.items() if ".scorer." in n))
            for name,state in states.items()}
        report.update(status="COMPLETED",elapsed_seconds=time.monotonic()-start,
                      peak_gpu_allocated_bytes=torch.cuda.max_memory_allocated(),
                      frozen_base_unchanged=True, original_source_unchanged=sha_file(SOURCE)==SOURCE_SHA)
        write_once(out/"result.json",report)
        print("ABLATION_COMPLETE",json.dumps({"elapsed_seconds":report["elapsed_seconds"],
              "weight":frozen["action_weight"],"basis":frozen["basis"],
              "arms":[{"name":a["name"],"applied":a["applied_steps"],"holdout":a["holdout"]} for a in report["arms"]]}),flush=True)
        return 0
    except Exception as exc:
        report.update(status="FAILED",failure_type=type(exc).__name__,failure_code=str(exc)[:240],
                      elapsed_seconds=time.monotonic()-start)
        write_once(out/"failure.json",report)
        print("ABLATION_FAILED",report["failure_code"],flush=True)
        return 2


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory",type=Path,required=True)
    args=parser.parse_args();out=args.output_directory
    evidence=Path("/home/alex/.local/state/heptatrader/operator-evidence")
    if (not out.is_absolute() or not out.is_relative_to(evidence) or out.exists()
        or not out.parent.is_dir() or out.parent.resolve()!=out.parent):
        parser.error("new Pocket4 operator-evidence output directory required")
    os.umask(0o077);out.mkdir(mode=0o700)
    source,train,hold,batches=source_partition()
    admission=OfflineAblationAdmission()
    if not admission.acquire():
        write_once(out/"denied.json",admission.last_admission)
        print("NO_GPU_ADMISSION",json.dumps(admission.last_admission),flush=True)
        return 3
    try:
        # Apply the existing strict sidecar sandbox before importing Torch.
        from sandbox import enter
        original=admission.original
        proof=enter({"output_dir":str(out),"period_path":original["period_path"],
            "legacy_capture":str(SOURCE),"oms_path":str(SOURCE),"export_dir":str(SOURCE),
            "read_only_paths":[str(Path(__file__).resolve().parent),
                str(Path(__file__).resolve().parents[1]/"research/neuron_learning"),
                "/home/alex/.local/share/heptatrader/laya-runtime",
                original["initial_parent_directory"],original["runtime_directory"],
                str(GUARD_CONFIG),str(Path("/proc/pressure/memory"))]})
        write_once(out/"sandbox.json",proof)
        return run(out,admission,source,train,hold,batches,proof)
    finally:
        admission.release()


if __name__=="__main__":
    raise SystemExit(main())
