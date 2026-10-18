#!/usr/bin/env python3
"""Two isolated GPU readout controls on the already frozen 32 training rows."""
from __future__ import annotations
import argparse
import copy
import json
import math
import os
from pathlib import Path
import time
from research_laya_readout_diagnostics import Admission, ROOT, SUBSET, SUBSET_SHA, GUARD, write_once, sha

DIAG = ROOT / "diagnosis-v1"
TRACE_SHA = "a7ef5fed549c3a70843a27cd4464b0be93bd6e1d3cd9894661a0a00f7772059c"
READOUT_SHA = "cf9126b10d708697d40c309343a5f91caf8d003a1cc644a8d19a02412737ac77"
MAX_SECONDS = 120


class ControlAdmission(Admission):
    def observe(self):
        admitted = super().observe()
        self.report["scope"] = "FROZEN_REPRESENTATION_READOUT_CONTROL_ONLY"
        self.report["maximum_seconds"] = MAX_SECONDS
        if sha(DIAG/"activation-trace.safetensors") != TRACE_SHA or sha(DIAG/"frozen-readout.safetensors") != READOUT_SHA:
            self.report["reasons"].append("FROZEN_READOUT_TRACE_CHANGED")
            admitted = False
        self.report["admit_new_worker"] = admitted
        return admitted


def run(out, admission, proof, plan):
    import torch
    from torch import nn
    from torch.nn import functional as F
    from safetensors.torch import load_file, save_file
    from laya_cell.model import require_pocket4_gpu, tensor_hash
    from laya_cell.resident_model import MaskedActionLinear
    from laya_cell.resident_readout_diagnostics import normalize_cls_input, probability_metrics, tensor_summary, fit_training_prior_offset, apply_margin_offset
    from laya_cell.integrity import digest

    started = time.monotonic()
    report = {"schema": "heptatrader.cached-readout-control.v1", "status": "IN_PROGRESS",
              "at_ms": int(time.time()*1000), "plan": plan, "admission": admission.report,
              "sandbox": proof, "source_sha256": SUBSET_SHA, "activation_trace_sha256": TRACE_SHA,
              "frozen_readout_sha256": READOUT_SHA, "old_holdout_forwards": 0,
              "encoder_forwards": 0, "price_parameters_updated": False, "model_promotion": False,
              "orders_sent": 0, "financial_reward_used": False, "arms": []}
    try:
        device = require_pocket4_gpu()
        torch.set_num_threads(2); torch.manual_seed(20261007)
        trace = load_file(str(DIAG/"activation-trace.safetensors"))
        weights = load_file(str(DIAG/"frozen-readout.safetensors"))
        data = json.loads(SUBSET.read_text()); rows = data["rows"]
        if len(rows) != 32 or sum(r["parent_correct"] for r in rows) != 2:
            raise RuntimeError("TRAIN_SUBSET_LABELS_CHANGED")
        if [[r["row_id"] for r in rows[i:i+2]] for i in range(0,32,2)] != data["batch_order"]:
            raise RuntimeError("ORIGINAL_BATCH_ORDER_CHANGED")
        x = trace["action_first_input"].to(device)
        price = trace["price_logits"]
        yp = [r["target_class"] for r in rows]
        ya = [0 if r["parent_correct"] else 1 for r in rows]
        target = torch.tensor(ya, device=device)
        p_act = (data["training_pool_act"]+.5)/(data["training_pool_rows"]+1)
        prior_action = torch.tensor([[math.log(p_act), math.log(1-p_act)]]*len(rows))
        report["train_pool_action_prior_on_selected_train"] = probability_metrics(price, prior_action, yp, ya)
        report["train_pool_action_prior_probability"] = p_act
        torch.cuda.reset_peak_memory_stats()
        def deadline():
            if time.monotonic()-started > MAX_SECONDS:
                raise RuntimeError("READOUT_CONTROL_DEADLINE")
            if os.path.lexists(admission.c["manual_hold_path"]):
                raise RuntimeError("MANUAL_RESEARCH_HOLD_APPEARED")

        for name in plan["arms"]:
            normalize = name != "raw_cls"
            deadline(); torch.manual_seed(20261007)
            first = nn.Linear(772, 256, device=device)
            final = nn.Linear(256, 2, device=device)
            with torch.no_grad():
                first.weight.copy_(weights["action_first_weight"].to(device))
                first.bias.copy_(weights["action_first_bias"].to(device))
                final.weight.copy_(weights["action_final_weight"].to(device))
                final.bias.copy_(weights["action_final_bias"].to(device))
            first.requires_grad_(False); final.requires_grad_(False)
            head = nn.Sequential(first, nn.GELU(), MaskedActionLinear(final)).eval()
            last = head[2]
            with torch.no_grad():
                last.A.copy_(weights["action_A"].to(device))
                last.B.copy_(weights["action_B"].to(device))
            initial = {"A": last.A.detach().clone(), "B": last.B.detach().clone()}
            initial_id = digest({k:tensor_hash(v) for k,v in initial.items()})
            frozen = {n:tensor_hash(p) for n,p in head.named_parameters() if not p.requires_grad}
            reference = last.full_effective_weight().detach().clone()
            values = normalize_cls_input(x) if normalize else x
            if not torch.equal(values[:, 768:], x[:, 768:]):
                raise RuntimeError("PRICE_FEATURES_CHANGED_BY_CONTROL")
            with torch.no_grad():
                hidden = head[1](head[0](values))
                before = last(hidden).detach().cpu()
            replay_error = float((before-trace["action_logits"]).abs().max()) if not normalize else None
            if replay_error is not None and replay_error > .05:
                raise RuntimeError("CACHED_RAW_READOUT_PARITY")
            prior_profile = None
            offset = 0.
            if name == "prior_anchored_cls":
                reference_path = ROOT/"control-v1/normalized_cls-logits.json"
                if sha(reference_path) != plan["reference_normalized_logits_sha256"]:
                    raise RuntimeError("NORMALIZED_STEP_ZERO_REFERENCE_CHANGED")
                prior_logits = torch.tensor(json.loads(reference_path.read_text())["before"])
                parity = float((before-prior_logits).abs().max())
                if parity > .05:
                    raise RuntimeError("PRIOR_CONTROL_BASE_REPLAY_PARITY")
                unshifted = before.clone()
                prior_profile = fit_training_prior_offset(before[:,0].double()-before[:,1].double(), p_act, split="train")
                offset = prior_profile["offset"]
                prior_profile.update(parent_id=data["frozen_parent_id"], subset_sha256=SUBSET_SHA,
                                     frozen_readout_sha256=READOUT_SHA, initialization_sha256=initial_id,
                                     training_cutoff_ms=data["training_cutoff_ms"],
                                     normalized_step_zero_replay_max_abs=parity,
                                     source_price_features_preserved=True,
                                     ACT_index=0, ESCALATE_index=1,
                                     CLS_normalization={"dimensions":768,"affine":False,"eps":1e-5},
                                     logit_adjustment=[offset/2,-offset/2],
                                     offset_fitted_once_before_optimizer=True,
                                     added_intercept_outside_legacy_adapter_norm_profile=True,
                                     legacy_resident_profile_compatible=False)
                before = apply_margin_offset(before, offset)
                old_pairs = (unshifted[:,0].double()-unshifted[:,1].double())
                new_pairs = (before[:,0].double()-before[:,1].double())
                prior_profile["fp32_pairwise_rank_or_tie_changes"] = int((
                    torch.sign(old_pairs[:,None]-old_pairs[None,:]) !=
                    torch.sign(new_pairs[:,None]-new_pairs[None,:])).sum())
                prior_profile["training_mean_probability_fp32_applied"] = float(before.softmax(-1)[:,0].mean())
                prior_profile["profile_sha256"] = write_once(out/"prior-intercept-profile.json", prior_profile)
            def predict(values):
                return apply_margin_offset(head(values), offset)
            before_metrics = probability_metrics(price, before, yp, ya)
            base_l2 = float(reference.double().norm())
            cap = base_l2 * .005
            hidden64_l2 = hidden[:, :64].detach().double().norm(dim=-1).cpu()
            maximum_margin_correction = math.sqrt(2)*cap*hidden64_l2
            raw_margin = before[:, 0].double()-before[:, 1].double()
            bounds = {"basis": "CAUCHY_SCHWARZ_PER_ROW_UPPER_BOUND_FOR_ANY_ALLOWED_64_COLUMN_DELTA",
                      "base_weight_l2": base_l2, "allowed_effective_delta_l2": cap,
                      "maximum_margin_correction": tensor_summary(maximum_margin_correction),
                      "minimum_remaining_ACT_margin": float((raw_margin-maximum_margin_correction).min()),
                      "all_ACT_argmax_unavoidable_with_current_mask_and_norm_bound": bool((raw_margin>maximum_margin_correction).all())}
            optimizer = torch.optim.AdamW([last.A,last.B], lr=.0001, weight_decay=0.)
            steps = []
            for index in range(0,32,2):
                deadline()
                saved = {"A":last.A.detach().clone(), "B":last.B.detach().clone()}
                saved_optimizer = copy.deepcopy(optimizer.state_dict())
                optimizer.zero_grad(set_to_none=True)
                logits = predict(values[index:index+2])
                loss = F.cross_entropy(logits,target[index:index+2])
                (.01*loss).backward()
                norm = torch.nn.utils.clip_grad_norm_([last.A,last.B],1.,error_if_nonfinite=True)
                optimizer.step()
                delta2 = float((last.full_effective_weight().detach().double()-reference.double()).square().sum())
                relative = math.sqrt(delta2)/base_l2
                global_relative = math.sqrt(delta2/plan["all_parent_effective_base_squared"])
                applied = relative <= .005 and global_relative <= .0025
                if not applied:
                    with torch.no_grad():
                        last.A.copy_(saved["A"]);last.B.copy_(saved["B"])
                    optimizer.load_state_dict(saved_optimizer)
                steps.append({"step":index//2+1, "row_ids":[r["row_id"] for r in rows[index:index+2]],
                              "action_nll_before_step":float(loss.detach()),"weighted_gradient_l2":float(norm),
                              "applied":applied,"action_relative_l2":relative,"global_relative_l2":global_relative})
            with torch.no_grad(): after = predict(values).detach().cpu()
            if frozen != {n:tensor_hash(p) for n,p in head.named_parameters() if not p.requires_grad}:
                raise RuntimeError("CONTROL_CHANGED_FROZEN_READOUT_BASE")
            after_metrics = probability_metrics(price, after, yp, ya)
            state = {"A":last.A.detach().cpu().contiguous(), "B":last.B.detach().cpu().contiguous()}
            artifact = out/(name+"-adapter.safetensors")
            save_file(state,str(artifact));artifact.chmod(0o400)
            result = {"name":name,"CLS_affine_free_LayerNorm":normalize,"LayerNorm_eps":1e-5 if normalize else None,
                      "prior_intercept_profile":prior_profile,"fixed_margin_offset":offset,
                      "initialization_sha256":initial_id,"raw_replay_max_abs":replay_error,
                      "before":before_metrics,"after":after_metrics,"bounds":bounds,"steps":steps,
                      "attempted_steps":16,"applied_steps":sum(s["applied"] for s in steps),
                      "rollback_steps":sum(not s["applied"] for s in steps),
                      "trainable_parameters":last.A.numel()+last.B.numel(),
                      "factor_delta_l2":math.sqrt(sum(float((state[k]-v.cpu()).double().square().sum()) for k,v in initial.items())),
                      "effective_delta_l2":float((last.full_effective_weight().detach().double()-reference.double()).norm()),
                      "adapter_sha256":sha(artifact),"frozen_readout_base_unchanged":True,
                      "beats_train_prior_nll_on_selected_train":after_metrics["action_nll"] < report["train_pool_action_prior_on_selected_train"]["action_nll"]}
            write_once(out/(name+"-result.json"),result)
            write_once(out/(name+"-logits.json"),{"row_ids":[r["row_id"] for r in rows],
                       "before":before.tolist(),"after":after.tolist(),
                       "ACT_targets": [r["parent_correct"] for r in rows],"price_logits":price.tolist()})
            if prior_profile is not None:
                recipe={"schema":"heptatrader.research-prior-anchored-readout-recipe.v1",
                        "frozen_encoder_and_price_parent_id":data["frozen_parent_id"],
                        "frozen_readout_path":str(DIAG/"frozen-readout.safetensors"),
                        "frozen_readout_sha256":READOUT_SHA,
                        "adapter_path":str(artifact),"adapter_sha256":sha(artifact),
                        "prior_intercept_profile_path":str(out/"prior-intercept-profile.json"),
                        "prior_intercept_profile_sha256":prior_profile["profile_sha256"],
                        "action_input":"CLS768_AFTER_ORIGINAL_TRANSFORMER_HEAD_PLUS_FOUR_ORIGINAL_PRICE_FEATURES",
                        "CLS_normalization":{"dimensions":768,"affine":False,"eps":1e-5},
                        "ACT_index":0,"ESCALATE_index":1,"logit_adjustment":[offset/2,-offset/2],
                        "training_subset_sha256":SUBSET_SHA,
                        "automatic_resident_selection":False,"requires_new_prospective_validation":True,
                        "trade_authority":False}
                result["research_recipe_sha256"]=write_once(out/"research-recipe.json",recipe)
            report["arms"].append(result)
            print("READOUT_CONTROL_ARM",name,json.dumps({"before":before_metrics,"after":after_metrics,"bounds":bounds}),flush=True)
        if len({a["initialization_sha256"] for a in report["arms"]}) != 1:
            raise RuntimeError("CONTROL_ARM_INITIALIZATION_CHANGED")
        torch.cuda.synchronize()
        report.update(status="COMPLETED",elapsed_seconds=time.monotonic()-started,
                      peak_gpu_allocated_bytes=torch.cuda.max_memory_allocated(),
                      torch_version=torch.__version__,rocm_version=torch.version.hip,
                      device=str(torch.cuda.get_device_properties(0).gcnArchName),
                      research_recipe_required_not_a_resident_artifact=True)
        write_once(out/"result.json",report)
        return 0
    except Exception as exc:
        report.update(status="FAILED",error_type=type(exc).__name__,error=str(exc)[:300],
                      elapsed_seconds=time.monotonic()-started)
        write_once(out/"failure.json",report)
        print("READOUT_CONTROL_FAILED",str(exc),flush=True)
        return 2


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-directory",type=Path,required=True)
    p.add_argument("--plan-sha256",required=True)
    p.add_argument("--plan-name",choices=["control-plan.json","prior-control-plan.json"],default="control-plan.json")
    a = p.parse_args();out=a.output_directory;plan_path=ROOT/a.plan_name
    if out.parent != ROOT or out.exists() or ROOT.resolve()!=ROOT:
        p.error("new direct Pocket4 readout evidence directory required")
    if sha(plan_path)!=a.plan_sha256:
        p.error("frozen control plan changed")
    plan=json.loads(plan_path.read_text())
    if plan.get("schema")!="heptatrader.frozen-readout-control-plan.v1" or plan.get("training_rows")!=32 or plan.get("old_holdout_forwards")!=0 or plan.get("trade_authority") is not False:
        p.error("research control plan scope")
    if plan.get("arms") not in (["raw_cls","normalized_cls"],["prior_anchored_cls"]):
        p.error("unreviewed control arm set")
    for name,expected in plan["source_inventory"].items():
        if sha(Path(__file__).resolve().parents[1]/name)!=expected:
            p.error("control source inventory changed")
    if sha(DIAG/"result.json")!=plan["diagnosis_result_sha256"]:
        p.error("diagnosis evidence changed")
    os.umask(0o077);out.mkdir(mode=0o700)
    admission=ControlAdmission()
    if not admission.acquire():
        write_once(out/"denied.json",admission.report);print("READOUT_CONTROL_NOT_ADMITTED",json.dumps(admission.report),flush=True);return 3
    try:
        from sandbox import enter
        proof=enter({"output_dir":str(out),"period_path":admission.original["period_path"],
                     "legacy_capture":str(SUBSET),"oms_path":str(SUBSET),"export_dir":str(SUBSET),
                     "read_only_paths":[str(Path(__file__).resolve().parent),
                         str(Path(__file__).resolve().parents[1]/"research/neuron_learning"),
                         "/home/alex/.local/share/heptatrader/laya-runtime",str(DIAG),str(ROOT/"control-v1"),str(plan_path),
                         str(GUARD),"/proc/pressure/memory"]})
        write_once(out/"sandbox.json",proof)
        return run(out,admission,proof,plan)
    finally:
        admission.lease.release()


if __name__=="__main__":
    raise SystemExit(main())
