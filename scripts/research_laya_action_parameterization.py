#!/usr/bin/env python3
"""Pocket4 cached ACT parameterization and AdamW interaction diagnostic."""
from __future__ import annotations
import argparse
import copy
import json
import math
import os
from pathlib import Path
import time
from research_laya_readout_diagnostics import Admission,SUBSET,SUBSET_SHA,GUARD,write_once,sha

ROOT=Path("/home/alex/.local/state/heptatrader/operator-evidence/train-price-readout-20261018")
OLD=Path("/home/alex/.local/state/heptatrader/operator-evidence/train-readout-diagnostics-20261018")
DIAG=OLD/"diagnosis-v1"
PRIOR=OLD/"prior-control-v1"
TRACE_SHA="a7ef5fed549c3a70843a27cd4464b0be93bd6e1d3cd9894661a0a00f7772059c"
READOUT_SHA="cf9126b10d708697d40c309343a5f91caf8d003a1cc644a8d19a02412737ac77"
MAX_SECONDS=120


class ActionAdmission(Admission):
    def observe(self):
        admitted=super().observe()
        self.report.update(scope="SEALED_TRAIN_CACHED_ACT_PARAMETERIZATION_ONLY",maximum_seconds=MAX_SECONDS)
        if sha(DIAG/"activation-trace.safetensors")!=TRACE_SHA or sha(DIAG/"frozen-readout.safetensors")!=READOUT_SHA:
            self.report["reasons"].append("FROZEN_ACTION_CACHE_CHANGED");admitted=False
        self.report["admit_new_worker"]=admitted
        return admitted


def run(out,admission,proof,plan):
    import torch
    from torch import nn
    from torch.nn import functional as F
    from safetensors.torch import load_file,save_file
    from laya_cell.model import require_pocket4_gpu,tensor_hash
    from laya_cell.resident_model import MaskedActionLinear
    from laya_cell.resident_readout_diagnostics import normalize_cls_input,apply_margin_offset
    from laya_cell.resident_price_readout_diagnostics import option_metrics,action_gradient_parts
    from laya_cell.integrity import digest
    started=time.monotonic()
    report={"schema":"heptatrader.cached-action-parameterization.v1","status":"IN_PROGRESS",
        "at_ms":int(time.time()*1000),"plan":plan,"admission":admission.report,"sandbox":proof,
        "subset_sha256":SUBSET_SHA,"activation_trace_sha256":TRACE_SHA,"frozen_readout_sha256":READOUT_SHA,
        "old_holdout_forwards":0,"encoder_forwards":0,"price_parameters_updated":False,
        "financial_reward_used":False,"model_promotion":False,"automatic_resident_selection":False,
        "orders_sent":0,"arms":[]}
    try:
        device=require_pocket4_gpu()
        torch.set_num_threads(2);torch.manual_seed(20261007);torch.cuda.reset_peak_memory_stats()
        trace=load_file(str(DIAG/"activation-trace.safetensors"))
        weights=load_file(str(DIAG/"frozen-readout.safetensors"))
        rows_data=json.loads(SUBSET.read_text());rows=rows_data["rows"]
        if len(rows)!=32 or sum(r["parent_correct"] for r in rows)!=2:
            raise RuntimeError("FIXED_ACTION_LABELS_CHANGED")
        if [[r["row_id"] for r in rows[i:i+2]] for i in range(0,32,2)]!=rows_data["batch_order"]:
            raise RuntimeError("FIXED_ACTION_BATCH_ORDER_CHANGED")
        if not torch.equal(weights["action_B"],torch.zeros_like(weights["action_B"])):
            raise RuntimeError("DIRECT_MARGIN_EQUIVALENCE_REQUIRES_ZERO_PARENT_B")
        if bool((weights["action_B"]@weights["action_A"]).count_nonzero()):
            raise RuntimeError("NONZERO_PARENT_EFFECTIVE_ACTION_DELTA")
        target=torch.tensor([0 if r["parent_correct"] else 1 for r in rows],device=device)
        is_act=target==0
        old_logits=json.loads((PRIOR/"prior_anchored_cls-logits.json").read_text())
        if old_logits["row_ids"]!=[r["row_id"] for r in rows] or old_logits["ACT_targets"]!=is_act.cpu().tolist():
            raise RuntimeError("PRIOR_ACTION_ROW_ALIGNMENT_CHANGED")
        old_before=torch.tensor(old_logits["before"],dtype=torch.float32,device=device)
        old_after=torch.tensor(old_logits["after"],dtype=torch.float32,device=device)
        old_adapter=load_file(str(PRIOR/"prior_anchored_cls-adapter.safetensors"))
        old_profile=json.loads((PRIOR/"prior-intercept-profile.json").read_text())
        offset=old_profile["offset"]
        if old_profile["subset_sha256"]!=SUBSET_SHA or old_profile["initialization_sha256"]!=plan["action_initialization_sha256"]:
            raise RuntimeError("FROZEN_ACTION_PRIOR_PROFILE_CHANGED")
        x=trace["action_first_input"].to(device)
        values=normalize_cls_input(x)
        if not torch.equal(x[:,768:],values[:,768:]):
            raise RuntimeError("FROZEN_PRICE_FEATURES_CHANGED")
        report["fixed_ACT_margin_offset"]=offset
        report["ACT_training_pool_prior"]=old_profile["frozen_training_prior"]
        report["selected_ACT_frequency"]=float(is_act.double().mean())
        report["actual_parent_B_zero"]=True
        report["actual_parent_effective_action_delta_zero"]=True
        report["original_action_weight_cap"]=.005*math.sqrt(plan["parent_base_squared"]["act_head.2"])
        denominator=sum(plan["parent_base_squared"].values())
        def deadline():
            if time.monotonic()-started>MAX_SECONDS:raise RuntimeError("ACTION_PARAMETERIZATION_DEADLINE")
            if os.path.lexists(admission.c["manual_hold_path"]):raise RuntimeError("MANUAL_HOLD_APPEARED")
        def make_head():
            first=nn.Linear(772,256,device=device);lastbase=nn.Linear(256,2,device=device)
            with torch.no_grad():
                first.weight.copy_(weights["action_first_weight"].to(device))
                first.bias.copy_(weights["action_first_bias"].to(device))
                lastbase.weight.copy_(weights["action_final_weight"].to(device))
                lastbase.bias.copy_(weights["action_final_bias"].to(device))
            first.requires_grad_(False);lastbase.requires_grad_(False)
            head=nn.Sequential(first,nn.GELU(),MaskedActionLinear(lastbase)).eval()
            with torch.no_grad():
                head[2].A.copy_(weights["action_A"].to(device));head[2].B.copy_(weights["action_B"].to(device))
            return head
        def cosine(a,b):
            d=float(a.double().norm()*b.double().norm())
            return float((a.double().flatten()@b.double().flatten())/d) if d>0 else None
        def theta(last):
            return ((last.B[0]-last.B[1])@last.A).detach().double().flatten()
        for name in plan["arms"]:
            deadline();torch.manual_seed(20261007)
            head=make_head();last=head[2]
            parent_reference=last.full_effective_weight().detach().clone()
            initial_id=digest({k:tensor_hash(getattr(last,k).detach()) for k in ("A","B")})
            if initial_id!=plan["action_initialization_sha256"]:raise RuntimeError("PARENT_ACTION_FACTORS_CHANGED")
            base_squared=float(parent_reference.double().square().sum())
            if abs(base_squared-plan["parent_base_squared"]["act_head.2"])>1e-12:
                raise RuntimeError("ORIGINAL_ACTION_NORM_DENOMINATOR_CHANGED")
            if name=="direct_theta":
                with torch.no_grad():
                    last.A.zero_();last.B.copy_(torch.tensor([[.5],[-.5]],device=device))
                last.B.requires_grad_(False)
                parameters=[last.A]
                parameter_names=["theta"]
            else:
                parameters=[last.A,last.B];parameter_names=["A","B"]
            frozen={n:tensor_hash(p) for n,p in head.named_parameters() if not p.requires_grad}
            def predict(v):return apply_margin_offset(head(v),offset)
            with torch.no_grad():
                hidden=head[1](head[0](values))
                before=predict(values)
            step0_max=float((before-old_before).abs().max())
            if step0_max>1e-6:raise RuntimeError("ACTION_STEP_ZERO_REPLAY_PARITY")
            before_metrics=option_metrics(before,target)
            hidden64=hidden[:,:64].double()
            parts=action_gradient_parts(hidden64,before,is_act)
            residual=before.double().softmax(-1)[:,0]-is_act.double()
            complete_gradient=.01*(residual[:,None]*hidden64).mean(0)
            hmean=hidden64.mean(0)
            label_pair_gradient=((is_act.double()-is_act.double().mean())[:,None]*(hidden64-hmean)).mean(0)
            initial_A=weights["action_A"].to(device).double().flatten()
            direction_cos=cosine(initial_A,complete_gradient)
            initial_geometry={"full32_unweighted_theta_gradient":parts,
                "weighted_full32_theta_gradient_l2":float(complete_gradient.norm()),
                "full32_gradient_projection_on_original_A_norm_fraction":abs(direction_cos) if direction_cos is not None else None,
                "pairing_gain_theta_gradient_l2":float(label_pair_gradient.norm()),
                "initial_A_l2":float(initial_A.norm())}
            optimizer=torch.optim.AdamW(parameters,lr=.0001,betas=(.9,.999),eps=1e-8,weight_decay=0.)
            steps=[]
            for index in range(0,32,2):
                deadline()
                saved_parameters=[p.detach().clone() for p in parameters]
                saved_optimizer=copy.deepcopy(optimizer.state_dict())
                before_effective=last.full_effective_weight().detach().clone()
                before_theta=theta(last)
                optimizer.zero_grad(set_to_none=True)
                logits=predict(values[index:index+2])
                loss=F.cross_entropy(logits,target[index:index+2])
                (.01*loss).backward()
                with torch.no_grad():
                    h2=head[1](head[0](values[index:index+2]))[:,:64].double()
                    r2=logits.detach().double().softmax(-1)[:,0]-is_act[index:index+2].double()
                    full_batch_theta_gradient=.01*(r2[:,None]*h2).mean(0)
                grad_stats={n:{"l2":float(p.grad.detach().double().norm()),
                               "nonzero_coordinates":int(p.grad.count_nonzero())}
                            for n,p in zip(parameter_names,parameters)}
                norm=torch.nn.utils.clip_grad_norm_(parameters,1.,error_if_nonfinite=True)
                optimizer.step()
                attempt_effective=last.full_effective_weight().detach().clone()
                attempt_theta=theta(last)
                epsilon={}
                for n,p,saved in zip(parameter_names,parameters,saved_parameters):
                    state=optimizer.state[p];step=int(state["step"].item())
                    vhat=state["exp_avg_sq"].detach().double()/(1.-.999**step)
                    active=vhat>0;fraction=1e-8/(vhat.sqrt()+1e-8)
                    parameter_change=(p.detach()-saved).double()
                    epsilon[n]={"state_step":step,"zero_state_coordinates":int((~active).sum()),
                        "nonzero_state_coordinates":int(active.sum()),
                        "active_state_epsilon_fraction_mean":float(fraction[active].mean()) if bool(active.any()) else None,
                        "active_state_epsilon_fraction_median":float(fraction[active].median()) if bool(active.any()) else None,
                        "active_state_epsilon_fraction_at_least_half":int((active & (fraction>=.5)).sum()),
                        "actual_parameter_change_l2":float(parameter_change.norm()),
                        "actual_changed_coordinates":int(parameter_change.count_nonzero())}
                delta2=float((attempt_effective.double()-parent_reference.double()).square().sum())
                relative=math.sqrt(delta2/base_squared);global_relative=math.sqrt(delta2/denominator)
                applied=relative<=.005 and global_relative<=.0025
                if not applied:
                    with torch.no_grad():
                        for p,saved in zip(parameters,saved_parameters):p.copy_(saved)
                    optimizer.load_state_dict(saved_optimizer)
                record={"step":index//2+1,"row_ids":[r["row_id"] for r in rows[index:index+2]],
                    "unweighted_action_nll":float(loss.detach()),"weighted_factor_gradients":grad_stats,
                    "gradient_l2_before_clip":float(norm),"clipped":float(norm)>1.,
                    "Adam_epsilon":epsilon,"attempt_action_relative_l2":relative,
                    "attempt_global_relative_l2":global_relative,"applied":applied,
                    "attempt_effective_step_l2":float((attempt_effective.double()-before_effective.double()).norm()),
                    "attempt_theta_step_l2":float((attempt_theta-before_theta).norm()),
                    "attempt_theta_step_cosine_with_negative_full_batch_gradient":cosine(attempt_theta-before_theta,-full_batch_theta_gradient),
                    "full_batch_weighted_theta_gradient_l2":float(full_batch_theta_gradient.norm())}
                if index==0:
                    record["full_batch_gradient_projection_on_original_A_norm_fraction"]=abs(cosine(initial_A,full_batch_theta_gradient)) if cosine(initial_A,full_batch_theta_gradient) is not None else None
                    record["original_A_gradient_zero"]=grad_stats.get("A",{}).get("nonzero_coordinates")==0 if name=="original_lora" else None
                steps.append(record)
            with torch.no_grad():after=predict(values)
            if frozen!={n:tensor_hash(p) for n,p in head.named_parameters() if not p.requires_grad}:
                raise RuntimeError("ACTION_CONTROL_CHANGED_FROZEN_PARAMETERS")
            after_metrics=option_metrics(after,target)
            change=(after[:,0].double()-after[:,1].double())-(before[:,0].double()-before[:,1].double())
            mean_change=float(change.mean())
            counter=before.double()+torch.tensor([mean_change/2,-mean_change/2],device=device)
            counter_metrics=option_metrics(counter,target)
            final_effective=last.full_effective_weight().detach().double()
            delta2=float((final_effective-parent_reference.double()).square().sum())
            common_vector=((last.B[0]+last.B[1])*.5)@last.A
            actual_theta=theta(last)
            result={"name":name,"same_initial_probability_function":True,
                "parent_action_factor_initialization_sha256":initial_id,
                "trainable_parameters":sum(p.numel() for p in parameters),
                "nominal_learning_rate_not_equal_effective_learning_rate":True,
                "step_zero_raw_logit_max_abs_vs_original":step0_max,
                "initial_geometry":initial_geometry,"before":before_metrics,"after":after_metrics,
                "steps":steps,"attempted_steps":16,"applied_steps":sum(s["applied"] for s in steps),
                "rollback_steps":sum(not s["applied"] for s in steps),"clipped_steps":sum(s["clipped"] for s in steps),
                "effective_delta_l2":math.sqrt(delta2),"action_relative_l2":math.sqrt(delta2/base_squared),
                "global_relative_l2":math.sqrt(delta2/denominator),"theta_l2":float(actual_theta.norm()),
                "common_logit_weight_vector_l2":float(common_vector.detach().double().norm()),
                "margin_change":{"mean":mean_change,"l2":float(change.norm()),
                    "row_centered_l2":float((change-change.mean()).norm()),
                    "min":float(change.min()),"max":float(change.max())},
                "NLL_improvement":before_metrics["nll"]-after_metrics["nll"],
                "conditional_pairing_gain_change":after_metrics["conditional_pairing_gain"]-before_metrics["conditional_pairing_gain"],
                "mean_margin_only_counterfactual_metrics":counter_metrics,
                "mean_margin_only_NLL_improvement":before_metrics["nll"]-counter_metrics["nll"],
                "additional_row_varying_NLL_improvement":counter_metrics["nll"]-after_metrics["nll"],
                "counterfactual_uses_observed_mean_update_no_intercept_refit":True}
            if name=="original_lora":
                replay={"final_logit_max_abs":float((after-old_after).abs().max()),
                    "adapter_max_abs":{k:float((getattr(last,k).detach().cpu()-old_adapter[k]).abs().max()) for k in ("A","B")},
                    "old_result_sha256":plan["prior_control_result_sha256"]}
                if replay["final_logit_max_abs"]>1e-6 or any(v>1e-7 for v in replay["adapter_max_abs"].values()):
                    raise RuntimeError("ORIGINAL_LORA_16_STEP_REPLAY_PARITY")
                result["original_16_step_replay"]=replay
            state={"A":last.A.detach().cpu().contiguous().clone(),"B":last.B.detach().cpu().contiguous().clone()}
            artifact=out/(name+"-action-adapter.safetensors")
            save_file(state,str(artifact));artifact.chmod(0o400)
            reload_head=make_head();loaded=load_file(str(artifact))
            with torch.no_grad():
                reload_head[2].A.copy_(loaded["A"].to(device));reload_head[2].B.copy_(loaded["B"].to(device))
                replay_logits=apply_margin_offset(reload_head(values),offset)
            reread_max=float((after-replay_logits).abs().max())
            reread_delta2=float((reload_head[2].full_effective_weight().detach().double()-parent_reference.double()).square().sum())
            if reread_max>1e-6 or reread_delta2!=delta2:
                raise RuntimeError("SERIALIZED_ACTION_MODULE_PARITY")
            result["serialized_parity"]={"raw_logit_max_abs":reread_max,"effective_delta_squared_equal":True}
            result["adapter_sha256"]=sha(artifact)
            logits_sha=write_once(out/(name+"-logits.json"),{
                "row_ids":[r["row_id"] for r in rows],"ACT_targets":is_act.cpu().tolist(),
                "before":before.detach().cpu().tolist(),"after":after.detach().cpu().tolist(),
                "mean_margin_only_counterfactual":counter.cpu().tolist()})
            recipe={"schema":"heptatrader.research-action-parameterization-recipe.v1","name":name,
                "parent_id":rows_data["frozen_parent_id"],"base_id":plan["base_id"],
                "training_cutoff_ms":rows_data["training_cutoff_ms"],"training_subset_sha256":SUBSET_SHA,
                "frozen_readout_path":str(DIAG/"frozen-readout.safetensors"),"frozen_readout_sha256":READOUT_SHA,
                "activation_trace_path":str(DIAG/"activation-trace.safetensors"),"activation_trace_sha256":TRACE_SHA,
                "CLS_normalization":{"dimensions":768,"affine":False,"eps":1e-5},
                "price_features":"FOUR_ORIGINAL_FROZEN_PARENT_FEATURES_UNCHANGED",
                "prior_profile_path":str(PRIOR/"prior-intercept-profile.json"),
                "prior_profile_sha256":plan["prior_profile_sha256"],"fixed_margin_offset":offset,
                "ACT_index":0,"ESCALATE_index":1,"adapter_path":str(artifact),"adapter_sha256":sha(artifact),
                "parameterization":"A_THETA_TRAINABLE_B_PLUS_MINUS_HALF_FIXED" if name=="direct_theta" else "ORIGINAL_A_B_LORA",
                "optimizer":plan["optimizer"],"source_inventory":plan["source_inventory"],
                "norms":{"effective_delta_l2":result["effective_delta_l2"],"action_relative_l2":result["action_relative_l2"],
                    "global_relative_l2":result["global_relative_l2"]},
                "normalization_and_offset_outside_legacy_adapter_profile":True,
                "automatic_resident_selection":False,"trade_authority":False,
                "requires_new_prospective_validation":True,"orders_sent":0}
            result["recipe_sha256"]=write_once(out/(name+"-research-recipe.json"),recipe)
            result["logits_sha256"]=logits_sha
            write_once(out/(name+"-result.json"),result)
            report["arms"].append(result)
            print("ACTION_PARAMETERIZATION_ARM",name,json.dumps({
                "before_nll":before_metrics["nll"],"after_nll":after_metrics["nll"],
                "delta_W":result["effective_delta_l2"],"delta_G":result["conditional_pairing_gain_change"],
                "mean_margin_NLL_improvement":result["mean_margin_only_NLL_improvement"],
                "additional_row_varying_NLL_improvement":result["additional_row_varying_NLL_improvement"],
                "applied":result["applied_steps"],"rollback":result["rollback_steps"]}),flush=True)
            del head,reload_head,optimizer
        torch.cuda.synchronize()
        report.update(status="COMPLETED",elapsed_seconds=time.monotonic()-started,
            peak_gpu_allocated_bytes=torch.cuda.max_memory_allocated(),torch_version=torch.__version__,
            rocm_version=torch.version.hip,device=str(torch.cuda.get_device_properties(0).gcnArchName),
            frozen_price_features_unchanged=True,no_new_intercept_fit=True,
            research_recipe_required_not_resident_artifact=True,
            artifacts={p.name:sha(p) for p in sorted(out.iterdir()) if p.is_file()})
        write_once(out/"result.json",report)
        print("ACTION_PARAMETERIZATION_COMPLETE",round(report["elapsed_seconds"],3),flush=True)
        return 0
    except Exception as exc:
        report.update(status="FAILED",error_type=type(exc).__name__,error=str(exc)[:500],
                      elapsed_seconds=time.monotonic()-started)
        write_once(out/"failure.json",report)
        print("ACTION_PARAMETERIZATION_FAILED",type(exc).__name__,str(exc),flush=True)
        return 2


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-directory",type=Path,required=True);p.add_argument("--plan-sha256",required=True)
    args=p.parse_args();out=args.output_directory;plan_path=ROOT/"action-parameterization-plan.json"
    if out.parent!=ROOT/"candidates" or out.exists() or ROOT.resolve()!=ROOT:
        p.error("new isolated Pocket4 action candidate directory required")
    if sha(plan_path)!=args.plan_sha256:p.error("frozen action parameterization plan changed")
    plan=json.loads(plan_path.read_text())
    if (plan.get("schema")!="heptatrader.action-parameterization-plan.v1" or plan.get("training_rows")!=32 or
        plan.get("old_holdout_forwards")!=0 or plan.get("trade_authority") is not False or
        plan.get("arms")!=["original_lora","direct_theta"]):
        p.error("action parameterization scope changed")
    repo=Path(__file__).resolve().parents[1]
    for relative,expected in plan["source_inventory"].items():
        if sha(repo/relative)!=expected:p.error("action source changed: "+relative)
    required={"prior-intercept-profile.json":"prior_profile_sha256",
              "prior_anchored_cls-logits.json":"prior_logits_sha256",
              "prior_anchored_cls-adapter.safetensors":"prior_adapter_sha256",
              "result.json":"prior_control_result_sha256"}
    for name,key in required.items():
        if sha(PRIOR/name)!=plan[key]:p.error("prior action evidence changed: "+name)
    os.umask(0o077);out.parent.mkdir(mode=0o700,exist_ok=True)
    if out.parent.resolve()!=out.parent:p.error("action output parent symlink")
    out.mkdir(mode=0o700);admission=ActionAdmission()
    if not admission.acquire():
        write_once(out/"denied.json",admission.report)
        print("ACTION_PARAMETERIZATION_NOT_ADMITTED",json.dumps(admission.report),flush=True);return 3
    try:
        from sandbox import enter
        proof=enter({"output_dir":str(out),"period_path":admission.original["period_path"],
            "legacy_capture":str(SUBSET),"oms_path":str(SUBSET),"export_dir":str(SUBSET),
            "read_only_paths":[str(repo/"scripts"),str(repo/"research/neuron_learning"),
                "/home/alex/.local/share/heptatrader/laya-runtime",
                str(DIAG),str(PRIOR),str(plan_path),str(GUARD),"/proc/pressure/memory"]})
        write_once(out/"sandbox.json",proof)
        return run(out,admission,proof,plan)
    finally:admission.lease.release()


if __name__=="__main__":
    raise SystemExit(main())
