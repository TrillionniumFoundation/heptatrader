#!/usr/bin/env python3
"""Pocket4 price geometry, fixed option-order and effective-weight controls."""
from __future__ import annotations
import argparse
import copy
import json
import math
import os
from pathlib import Path
import time
from research_laya_readout_diagnostics import Admission, SUBSET, SUBSET_SHA, GUARD, write_once, sha

ROOT=Path("/home/alex/.local/state/heptatrader/operator-evidence/train-price-readout-20261018")
OLD=Path("/home/alex/.local/state/heptatrader/operator-evidence/train-readout-diagnostics-20261018")
DIAG=OLD/"diagnosis-v1"
PRIOR=Path("/home/alex/.local/state/heptatrader/operator-evidence/train-action-ablation-20261017/candidates/three-arm-v4")
TRACE_SHA="a7ef5fed549c3a70843a27cd4464b0be93bd6e1d3cd9894661a0a00f7772059c"
MAX_SECONDS=360
ORDERS={"canonical":list(range(7)),"rotate_one":[1,2,3,4,5,6,0],"reverse":[6,5,4,3,2,1,0]}


class PriceAdmission(Admission):
    def observe(self):
        admitted=super().observe()
        self.report.update(scope="SEALED_TRAIN_PRICE_GEOMETRY_AND_FIXED_OPTION_ORDERS",
                           maximum_seconds=MAX_SECONDS)
        if sha(DIAG/"activation-trace.safetensors")!=TRACE_SHA:
            self.report["reasons"].append("ORIGINAL_TRACE_CHANGED")
            admitted=False
        self.report["admit_new_worker"]=admitted
        return admitted


def run(out,admission,proof,plan):
    import torch
    from torch import nn
    from torch.nn import functional as F
    from safetensors.torch import load_file,save_file
    from laya.common import build_sequence,collate_items
    from laya_cell.integrity import digest,read_json
    from laya_cell.model import load_verified_laya,tensor_hash
    from laya_cell.resident_model import ResidentDecisionCell
    from laya_cell.resident_train import validate_parent
    from laya_cell.original_input_order import original_forward_row,encode_original_forward_rows
    from laya_cell.resident_price_readout_diagnostics import (
        option_metrics,frozen_option_offset,unpermute_slots,solve_shared_readout_ball,joint_price_gap_bounds)

    start=time.monotonic()
    report={"schema":"heptatrader.price-readout-research.v1","status":"IN_PROGRESS",
            "at_ms":int(time.time()*1000),"plan":plan,"admission":admission.report,"sandbox":proof,
            "subset_sha256":SUBSET_SHA,"trace_sha256":TRACE_SHA,"old_holdout_forwards":0,
            "encoder_forward_calls":0,"row_forward_evaluations":0,"model_promotion":False,
            "orders_sent":0,"automatic_resident_selection":False}
    handles=[]
    try:
        def deadline():
            if time.monotonic()-start>MAX_SECONDS:raise RuntimeError("PRICE_DIAGNOSTIC_DEADLINE")
            if os.path.lexists(admission.c["manual_hold_path"]):raise RuntimeError("MANUAL_HOLD_APPEARED")
        data=read_json(SUBSET,4*1024**2);rows=data["rows"]
        if len(rows)!=32 or sum(r["parent_correct"] for r in rows)!=2:
            raise RuntimeError("FIXED_TRAIN_ROWS_CHANGED")
        if [[r["row_id"] for r in rows[i:i+2]] for i in range(0,32,2)]!=data["batch_order"]:
            raise RuntimeError("FIXED_TRAIN_BATCH_ORDER")
        if any(r["anchor_ms"]>=data["training_cutoff_ms"] or r["future_outcome_available_ms"]>data["training_cutoff_ms"] for r in rows):
            raise RuntimeError("TRAIN_CUTOFF")
        parent_dir=Path(admission.original["initial_parent_directory"])
        parent=validate_parent(parent_dir)
        if parent["candidate_id"]!=data["frozen_parent_id"]:raise RuntimeError("PARENT_CHANGED")
        torch.set_num_threads(2);torch.manual_seed(20261007)
        base=Path(admission.original["base_directory"])
        raw,tok,base_id=load_verified_laya(base,read_json(base/"base-manifest.json"))
        if base_id!=parent["base_id"]:raise RuntimeError("BASE_CHANGED")
        cell=ResidentDecisionCell(raw,base_id)
        cell.restore_parent(load_file(str(parent_dir/"adapter.safetensors")),allow_scorer_only=True)
        initial=cell.trainable_state()
        initial_id=digest({n:tensor_hash(t) for n,t in initial.items()})
        if initial_id!=data["initialization_sha256"]:raise RuntimeError("INITIALIZATION_CHANGED")
        cell.eval();device=next(cell.parameters()).device
        trace=load_file(str(DIAG/"activation-trace.safetensors"))
        target=torch.tensor([r["target_class"] for r in rows],device=device)
        cached_x=trace["price_layernorm_input"].to(device)
        original_scorer=raw.scorer
        torch.cuda.reset_peak_memory_stats()
        report.update(base_id=base_id,parent_id=parent["candidate_id"],initialization_sha256=initial_id,
                      torch_version=torch.__version__,rocm_version=torch.version.hip,
                      device=str(torch.cuda.get_device_properties(0).gcnArchName),
                      model_dtype="float32",LayerNorm_eps=original_scorer[0].eps)
        print("PRICE_PARENT_LOADED",initial_id,flush=True)

        # Independent original B2 forward versus a full scorer clone, cached before LN.
        captured={}
        def capture(module,args):
            captured["input"]=args[0].detach().clone()
        handle=original_scorer.register_forward_pre_hook(capture);handles.append(handle)
        b2={k:v.to(device) for k,v in encode_original_forward_rows(tok,rows[:2]).items()}
        full_p,_=cell(**b2);report["encoder_forward_calls"]+=1;report["row_forward_evaluations"]+=2
        handle.remove();handles.remove(handle)
        clone=copy.deepcopy(original_scorer)
        cloned_p=clone(captured["input"]).squeeze(-1)
        fp=[(n,p) for n,p in original_scorer.named_parameters() if p.requires_grad]
        cp=[(n,p) for n,p in clone.named_parameters() if p.requires_grad]
        if [n for n,p in fp]!=[n for n,p in cp]:raise RuntimeError("CLONED_PRICE_PARAMETER_ORDER")
        fg=torch.autograd.grad(F.cross_entropy(full_p,target[:2]),[p for n,p in fp])
        cg=torch.autograd.grad(F.cross_entropy(cloned_p,target[:2]),[p for n,p in cp])
        gradients=[]
        for (name,_),g1,g2 in zip(fp,fg,cg):
            error=float((g1-g2).abs().max())
            gradients.append({"name":name,"max_abs":error,"reference_l2":float(g1.double().norm()),
                              "cached_l2":float(g2.double().norm())})
            if not torch.allclose(g1,g2,rtol=1e-5,atol=1e-7):
                raise RuntimeError("CACHED_PRICE_GRADIENT_PARITY:"+name)
        report["B2_scorer_parity"]={
            "cache_cut":"BEFORE_FROZEN_LAYER_NORM_AND_ALL_TRAINABLE_PRICE_OPERATORS",
            "raw_logit_max_abs":float((full_p-cloned_p).abs().max()),
            "probability_max_abs":float((full_p.softmax(-1)-cloned_p.softmax(-1)).abs().max()),
            "gradients":gradients,"optimizer_steps":0}
        if report["B2_scorer_parity"]["raw_logit_max_abs"]>1e-4:raise RuntimeError("CACHED_PRICE_B2_FORWARD_PARITY")
        del full_p,cloned_p,fg,cg,clone,captured,b2
        print("PRICE_B2_FORWARD_GRADIENT_PARITY",json.dumps(report["B2_scorer_parity"]),flush=True)

        def encode(row,order):
            ordered=original_forward_row(row)
            ids,markers,stats,trunc=build_sequence(tok,ordered["state"],ordered["question"],
                max_len=1024,head_max_len=256,option_order=order,
                return_stats=True,return_truncation_stats=True)
            if len(markers)!=7 or stats["options_distinct"]!=7 or trunc["truncated"]:
                raise RuntimeError("PERMUTED_OPTIONS_OR_STATE_TRUNCATED")
            batch=collate_items([[{"ids":ids,"markers":markers,"qtype":0}]],tok.pad_token_id)
            batch={k:batch[k] for k in ("input_ids","attention_mask","marker_pos","marker_mask","qtype")}
            if order==ORDERS["canonical"]:
                original=encode_original_forward_rows(tok,[row])
                if any(not torch.equal(batch[k],original[k]) for k in batch):
                    raise RuntimeError("CANONICAL_TOKEN_REPLAY_CHANGED")
            meta={"input_id":row["input_id"],"row_id":row["row_id"],
                  "encoded_tensors_sha256":digest({k:tensor_hash(v) for k,v in batch.items()}),
                  "marker_positions":markers,"marker_token_ids":[ids[m] for m in markers],
                  "sequence_length":len(ids),"truncation":trunc}
            return {k:v.to(device) for k,v in batch.items()},meta
        ordered_logits={};ordered_meta={}
        observations=out/"forward-observations.jsonl"
        with observations.open("xb"):pass
        with torch.no_grad():
            for name,order in ORDERS.items():
                logits=[];metadata=[]
                for i,row in enumerate(rows):
                    deadline();batch,meta=encode(row,order)
                    p,_=cell(**batch)
                    report["encoder_forward_calls"]+=1;report["row_forward_evaluations"]+=1
                    if not bool(torch.isfinite(p).all()):raise RuntimeError("NONFINITE_PRICE_FORWARD")
                    logits.append(p.detach().cpu().clone());metadata.append(meta)
                    observation={"order":name,"slot_to_canonical":order,"index":i,
                                 "metadata":meta,"slot_logits":p.detach().cpu().tolist()}
                    with observations.open("ab") as stream:
                        stream.write((json.dumps(observation,sort_keys=True,separators=(",",":"),allow_nan=False)+"\n").encode())
                        stream.flush();os.fsync(stream.fileno())
                    if (i+1)%8==0:print("PRICE_ORDER_PROGRESS",name,i+1,round(time.monotonic()-start,2),flush=True)
                ordered_logits[name]=torch.cat(logits)
                ordered_meta[name]=metadata
        observations.chmod(0o400)
        if set(ordered_logits)!=set(ORDERS) or any(v.shape!=(32,7) for v in ordered_logits.values()):
            raise RuntimeError("INCOMPLETE_FIXED_OPTION_FORWARD")
        report["complete_order_rows"]={n:int(v.shape[0]) for n,v in ordered_logits.items()}
        canonical=ordered_logits["canonical"]
        old_z=trace["price_logits"]
        replay={"raw_logit_max_abs":float((canonical-old_z).abs().max()),
                "probability_max_abs":float((canonical.softmax(-1)-old_z.softmax(-1)).abs().max())}
        if replay["raw_logit_max_abs"]>1e-4 or replay["probability_max_abs"]>5e-5:
            raise RuntimeError("FROZEN_CANONICAL_PRICE_REPLAY")
        report["canonical_replay"]=replay
        for name in ORDERS:
            if [v["input_id"] for v in ordered_meta[name]]!=[r["input_id"] for r in rows]:
                raise RuntimeError("PERMUTATION_SOURCE_ID_CHANGED")
            if [v["truncation"] for v in ordered_meta[name]]!=[v["truncation"] for v in ordered_meta["canonical"]]:
                raise RuntimeError("PERMUTATION_STATE_TOKEN_RETENTION_CHANGED")
        views={};canonical_prob=canonical.double().softmax(-1)
        reference=canonical.double()-canonical.double().mean(-1,keepdim=True)
        canon_y=target.cpu()
        for name,order in ORDERS.items():
            slots=ordered_logits[name]
            semantic=unpermute_slots(slots,order)
            inverse=torch.tensor(order).argsort()
            slot_y=inverse[canon_y]
            slot_c=slots.double()-slots.double().mean(-1,keepdim=True)
            semantic_c=semantic.double()-semantic.double().mean(-1,keepdim=True)
            views[name]={"slot_to_canonical":order,"slot":option_metrics(slots,slot_y),
                         "canonical":option_metrics(semantic,canon_y),
                         "slot_centered_rmse_vs_canonical":float((slot_c-reference).square().mean().sqrt()),
                         "semantic_centered_rmse_vs_canonical":float((semantic_c-reference).square().mean().sqrt()),
                         "semantic_probability_mean_abs_change":float((semantic.double().softmax(-1)-canonical_prob).abs().mean()),
                         "canonical_winner_agreement":int((semantic.argmax(-1)==canonical.argmax(-1)).sum())}
        mean_probs=torch.stack([unpermute_slots(ordered_logits[n],o).double().softmax(-1) for n,o in ORDERS.items()]).mean(0)
        report["option_orders"]=views
        report["equal_three_order_probability_average_diagnostic"]=option_metrics(mean_probs.log(),canon_y)
        write_once(out/"option-order-logits.json",{"rows":[r["row_id"] for r in rows],
          "slot_logits":{n:v.tolist() for n,v in ordered_logits.items()},"orders":ORDERS,"target":canon_y.tolist()})
        write_once(out/"option-order-input-metadata.json",ordered_meta)
        print("PRICE_OPTION_ORDERS_DONE",json.dumps({n:{"nll":v["canonical"]["nll"],
           "correct":v["canonical"]["correct"],"slot_rmse":v["slot_centered_rmse_vs_canonical"],
           "semantic_rmse":v["semantic_centered_rmse_vs_canonical"]} for n,v in views.items()}),flush=True)

        with torch.no_grad():
            nx=original_scorer[0](cached_x)
            pre=original_scorer[1](nx)
            hidden=original_scorer[2](pre)
            cached_z=original_scorer[3](hidden).squeeze(-1)
        cache_replay={"raw_logit_max_abs":float((cached_z.cpu()-old_z).abs().max()),
                      "probability_max_abs":float((cached_z.cpu().softmax(-1)-old_z.softmax(-1)).abs().max()),
                      "hidden_max_abs":float((hidden.cpu()-trace["price_final_input"]).abs().max())}
        if cache_replay["raw_logit_max_abs"]>1e-4 or cache_replay["probability_max_abs"]>5e-5:
            raise RuntimeError("CACHED_PRICE_ALL_ROW_REPLAY")
        report["cached_price_replay"]=cache_replay
        refs={i:original_scorer[i].full_effective_weight().detach().clone() for i in (1,3)}
        original_norm=cell.norm_report()
        for i in (1,3):
            if abs(float(refs[i].double().square().sum())-plan["parent_base_squared"]["scorer."+str(i)])>1e-7:
                raise RuntimeError("ORIGINAL_NORM_REFERENCE_CHANGED")
        radius1=.005*float(refs[1].double().norm());radius3=.005*float(refs[3].double().norm())
        bound=joint_price_gap_bounds(cached_z,target,nx,hidden,refs[3][0,:4],radius1,radius3)
        report["joint_price_support_bounds"]=bound
        derivative=.5*(1+torch.erf(pre[...,:4].double()/math.sqrt(2)))+pre[...,:4].double()*torch.exp(-.5*pre[...,:4].double().square())/math.sqrt(2*math.pi)
        report["first_four_hidden"]={"preactivation_min":float(pre[...,:4].min()),
            "preactivation_max":float(pre[...,:4].max()),"gelu_derivative_min":float(derivative.min()),
            "gelu_derivative_max":float(derivative.max()),"final_weight_l2":float(refs[3][0,:4].double().norm()),
            "contribution_centered_l2":float(((hidden[...,:4].double()*refs[3][0,:4].double()).sum(-1)-
                  (hidden[...,:4].double()*refs[3][0,:4].double()).sum(-1).mean(-1,keepdim=True)).norm())}
        report["parent_cached_metrics"]=option_metrics(cached_z,target)
        previous=load_file(str(PRIOR/"current_001.safetensors"))
        old_scorer=copy.deepcopy(original_scorer)
        with torch.no_grad():
            for i in (1,3):
                for key in ("A","B"):
                    getattr(old_scorer[i],key).copy_(previous["model.scorer."+str(i)+"."+key].to(device))
            previous_z=old_scorer(cached_x).squeeze(-1)
        change=previous_z.double()-cached_z.double()
        report["previous_16_step_price_adapter_replay"]={
          "adapter_sha256":plan["prior_adapter_sha256"],"training_only":True,
          "metrics":option_metrics(previous_z,target),
          "effective_delta_l2":{str(i):float((old_scorer[i].full_effective_weight().detach().double()-refs[i].double()).norm()) for i in (1,3)},
          "raw_logit_change_l2":float(change.norm()),
          "row_centered_logit_change_l2":float((change-change.mean(-1,keepdim=True)).norm()),
          "row_common_logit_change_l2":float(change.mean(-1).norm())}
        del old_scorer,previous

        counts=plan["training_pool_price_counts"]
        prior=[(n+.5)/(sum(counts)+.5*len(counts)) for n in counts]
        offset,offset_profile=frozen_option_offset(cached_z,prior,split="train")
        offset_profile.update(parent_id=data["frozen_parent_id"],training_cutoff_ms=data["training_cutoff_ms"],
            subset_sha256=SUBSET_SHA,training_pool_counts=counts,training_pool_rows=sum(counts),
            trace_sha256=TRACE_SHA,bin_semantics=plan["price_bins"],
            automatic_resident_selection=False,trade_authority=False)
        offset_sha=write_once(out/"frozen-price-offset.json",offset_profile)
        report["training_pool_price_prior_on_selected_train"]=option_metrics(
            torch.tensor(prior,device=device,dtype=torch.float64).log().repeat(32,1),target)
        frozen_weights={n:v.detach().cpu().contiguous().clone() for n,v in original_scorer.state_dict().items()}
        frozen_path=out/"frozen-price-readout.safetensors"
        save_file(frozen_weights,str(frozen_path));frozen_path.chmod(0o400)
        report["frozen_price_readout_sha256"]=sha(frozen_path)
        denominator=sum(v["base_squared"] for v in original_norm["layers"].values())
        if abs(denominator-sum(plan["parent_base_squared"].values()))>1e-7:
            raise RuntimeError("ORIGINAL_GLOBAL_NORM_DENOMINATOR_CHANGED")
        report["original_norm_reference"]=original_norm
        report["convex_controls"]=[]
        for name,adjustment in (("raw",torch.zeros(7,device=device,dtype=torch.float64)),
                                ("frozen_price_prior_offset",offset)):
            deadline()
            baseline=cached_z.double()+adjustment
            delta,solution=solve_shared_readout_ball(hidden,baseline,target,radius3*.9999,
                maximum_iterations=512,gap_tolerance=1e-7,deadline=deadline)
            centered_h=hidden.double()-hidden.double().mean(1,keepdim=True)
            flat=centered_h.reshape(-1,hidden.shape[-1])
            values=baseline-baseline.mean(-1,keepdim=True)+(flat@delta).reshape(baseline.shape)
            truth=F.one_hot(target,7).to(values)
            gradient=flat.T@(values.softmax(-1)-truth).reshape(-1)/32
            full_gap=max(0.,float(gradient@delta)+radius3*float(gradient.norm()))
            solution.update(original_radius=radius3,packing_radius_fraction=.9999,
                original_radius_gap=full_gap,
                original_radius_nll_lower_bound=solution["nll"]-full_gap,
                original_radius_initial_improvement_upper_bound=radius3*solution["initial_gradient_l2"],
                first_price_layer_frozen=True,all_encoder_features_frozen=True,
                optimizer_is_not_resident_16_step_AdamW=True,
                objective="SELECTED_32_TRAIN_MEAN_PRICE_CROSS_ENTROPY_NO_TEACHER_KL")
            packed=copy.deepcopy(original_scorer)
            with torch.no_grad():
                parent_product=original_scorer[3].B@original_scorer[3].A
                packed[3].A.copy_((parent_product.double()+delta[None,:]).float())
                packed[3].B.fill_(1.)
            candidate_path=out/(name+"-price-adapter.safetensors")
            tensors={"model.scorer."+str(i)+"."+key:getattr(packed[i],key).detach().cpu().contiguous().clone()
                     for i in (1,3) for key in ("A","B")}
            save_file(tensors,str(candidate_path));candidate_path.chmod(0o400)
            # Reload the written artifact through the original FP32 LoRA modules.
            restored=copy.deepcopy(original_scorer)
            saved=load_file(str(candidate_path))
            with torch.no_grad():
                for i in (1,3):
                    for key in ("A","B"):
                        getattr(restored[i],key).copy_(saved["model.scorer."+str(i)+"."+key].to(device))
                packed_z=restored(cached_x).squeeze(-1)
            for key in ("A","B"):
                if not torch.equal(getattr(restored[1],key),getattr(original_scorer[1],key)):
                    raise RuntimeError("FINAL_VECTOR_CONTROL_CHANGED_FIRST_PRICE_ADAPTER")
            norm_layers={}
            total_delta2=0.
            for i in (1,3):
                d2=float((restored[i].full_effective_weight().detach().double()-refs[i].double()).square().sum())
                den=plan["parent_base_squared"]["scorer."+str(i)]
                norm_layers["scorer."+str(i)]={"delta_squared":d2,"base_squared":den,"relative_l2":math.sqrt(d2/den)}
                total_delta2+=d2
            norm_layers["act_head.2"]={"delta_squared":0.,"base_squared":plan["parent_base_squared"]["act_head.2"],"relative_l2":0.}
            candidate_norm={"layers":norm_layers,"global_relative_l2":math.sqrt(total_delta2/denominator)}
            candidate_norm["within_original_norm_profile"]=candidate_norm["global_relative_l2"]<=.0025 and all(
                v["relative_l2"]<=.005 for v in norm_layers.values())
            if not candidate_norm["within_original_norm_profile"]:
                raise RuntimeError("PACKED_FP32_WEIGHT_EXCEEDS_ORIGINAL_NORM_CAP")
            expected=cached_z.double()+(hidden.double().reshape(-1,hidden.shape[-1])@delta).reshape(cached_z.shape)
            centered_error=(packed_z.double()-packed_z.double().mean(-1,keepdim=True))-(expected-expected.mean(-1,keepdim=True))
            parity={"serialized_module_raw_logit_max_abs":float((packed_z.double()-expected).abs().max()),
                    "serialized_module_centered_logit_max_abs":float(centered_error.abs().max()),
                    "serialized_module_probability_max_abs":float((packed_z.double().softmax(-1)-expected.softmax(-1)).abs().max()),
                    "first_price_adapter_unchanged":True,"LoRA_scaling":1.}
            if parity["serialized_module_raw_logit_max_abs"]>1e-4 or parity["serialized_module_probability_max_abs"]>5e-5:
                raise RuntimeError("SERIALIZED_FINAL_VECTOR_FORWARD_PARITY")
            applied=packed_z+adjustment.float()
            before=option_metrics(baseline,target);after=option_metrics(applied,target)
            logits_sha=write_once(out/(name+"-logits.json"),{
                "row_ids":[r["row_id"] for r in rows],"target":target.cpu().tolist(),
                "before":baseline.cpu().tolist(),"solver_after_fp64":(expected+adjustment).cpu().tolist(),
                "serialized_after_fp32":applied.detach().cpu().tolist()})
            delta_path=out/(name+"-effective-vector.safetensors")
            save_file({"delta":delta.detach().cpu().contiguous()},str(delta_path));delta_path.chmod(0o400)
            recipe={"schema":"heptatrader.research-price-readout-recipe.v1","name":name,
                    "base_id":base_id,"parent_id":parent["candidate_id"],
                    "source_inventory":plan["source_inventory"],"training_subset_sha256":SUBSET_SHA,
                    "training_cutoff_ms":data["training_cutoff_ms"],"price_bins":plan["price_bins"],
                    "input_order":"CANONICAL_ORIGINAL_STATE_AND_SEVEN_CANONICAL_PRICE_OPTIONS",
                    "activation_trace_path":str(DIAG/"activation-trace.safetensors"),
                    "activation_trace_sha256":TRACE_SHA,
                    "frozen_price_readout_path":str(frozen_path),"frozen_price_readout_sha256":sha(frozen_path),
                    "price_adapter_path":str(candidate_path),"price_adapter_sha256":sha(candidate_path),
                    "price_offset_profile_path":str(out/"frozen-price-offset.json") if name!="raw" else None,
                    "price_offset_profile_sha256":offset_sha if name!="raw" else None,
                    "logit_adjustment":adjustment.cpu().tolist(),
                    "effective_vector_sha256":sha(delta_path),"solver":{k:v for k,v in solution.items() if k!="history"},
                    "norm":candidate_norm,"serialized_parity":parity,
                    "fixed_offset_outside_legacy_adapter_profile":name!="raw",
                    "price_only_research_control":True,
                    "ACT_calibration_not_reused_for_changed_price_features":True,
                    "automatic_resident_selection":False,"requires_new_prospective_validation":True,
                    "trade_authority":False,"orders_sent":0}
            recipe_sha=write_once(out/(name+"-research-recipe.json"),recipe)
            control={"name":name,"before":before,"after":after,"solver":solution,"norm":candidate_norm,
                     "serialized_parity":parity,"price_adapter_sha256":sha(candidate_path),
                     "effective_vector_sha256":sha(delta_path),"research_recipe_sha256":recipe_sha,
                     "logits_sha256":logits_sha,
                     "price_offset_profile_sha256":offset_sha if name!="raw" else None,
                     "NLL_improvement":before["nll"]-after["nll"],
                     "conditional_pairing_gain_change":after["conditional_pairing_gain"]-before["conditional_pairing_gain"]}
            write_once(out/(name+"-result.json"),control)
            report["convex_controls"].append(control)
            print("PRICE_CONVEX_CONTROL",name,json.dumps({
                "before_nll":before["nll"],"after_nll":after["nll"],
                "before_G":before["conditional_pairing_gain"],"after_G":after["conditional_pairing_gain"],
                "gap":solution["convex_gap"],"original_radius_gap":full_gap,
                "iterations":solution["iterations"],"norm":candidate_norm,"parity":parity}),flush=True)
            del packed,restored,saved
        cell.assert_frozen()
        if digest({n:tensor_hash(v) for n,v in cell.trainable_state().items()})!=initial_id:
            raise RuntimeError("PRICE_RESEARCH_CHANGED_ORIGINAL_CELL")
        if report["encoder_forward_calls"]!=97 or report["row_forward_evaluations"]!=98:
            raise RuntimeError("PRICE_FORWARD_ACCOUNTING")
        torch.cuda.synchronize()
        report.update(status="COMPLETED",elapsed_seconds=time.monotonic()-start,
            peak_gpu_allocated_bytes_since_parent_load=torch.cuda.max_memory_allocated(),
            frozen_base_unchanged=True,original_parent_cell_unchanged=True,
            artifacts={p.name:sha(p) for p in sorted(out.iterdir()) if p.is_file() and p.name!="result.json"})
        write_once(out/"result.json",report)
        print("PRICE_READOUT_COMPLETE",json.dumps({
            "elapsed_seconds":report["elapsed_seconds"],"encoder_forward_calls":report["encoder_forward_calls"],
            "row_forward_evaluations":report["row_forward_evaluations"],
            "wrong_unfixable":bound["wrong_unfixable"],
            "minimum_unfixable_remaining_gap":bound["minimum_unfixable_remaining_gap"]}),flush=True)
        return 0
    except Exception as exc:
        report.update(status="FAILED",error_type=type(exc).__name__,error=str(exc)[:500],
                      elapsed_seconds=time.monotonic()-start)
        write_once(out/"failure.json",report)
        print("PRICE_READOUT_FAILED",type(exc).__name__,str(exc),flush=True)
        return 2
    finally:
        for handle in handles:handle.remove()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory",type=Path,required=True)
    parser.add_argument("--plan-sha256",required=True)
    args=parser.parse_args();out=args.output_directory
    plan_path=ROOT/"price-readout-plan.json"
    if out.parent!=ROOT/"candidates" or out.name in (".","..") or out.exists() or ROOT.resolve()!=ROOT:
        parser.error("new isolated Pocket4 price evidence candidate directory required")
    if sha(plan_path)!=args.plan_sha256:parser.error("frozen price research plan changed")
    plan=json.loads(plan_path.read_text())
    if (plan.get("schema")!="heptatrader.frozen-price-readout-plan.v1" or
        plan.get("training_rows")!=32 or plan.get("old_holdout_forwards")!=0 or
        plan.get("trade_authority") is not False or plan.get("option_orders")!=ORDERS or
        plan.get("controls")!=["raw","frozen_price_prior_offset"]):
        parser.error("price research scope changed")
    repo=Path(__file__).resolve().parents[1]
    for relative,expected in plan["source_inventory"].items():
        if sha(repo/relative)!=expected:parser.error("price source inventory changed: "+relative)
    if sha(PRIOR/"current_001.safetensors")!=plan["prior_adapter_sha256"]:
        parser.error("previous price endpoint changed")
    if sha(DIAG/"result.json")!=plan["diagnosis_result_sha256"]:
        parser.error("original price diagnosis changed")
    os.umask(0o077)
    out.parent.mkdir(mode=0o700,exist_ok=True)
    if out.parent.resolve()!=out.parent:parser.error("price output parent symlink")
    out.mkdir(mode=0o700)
    admission=PriceAdmission()
    if not admission.acquire():
        write_once(out/"denied.json",admission.report)
        print("PRICE_READOUT_NOT_ADMITTED",json.dumps(admission.report),flush=True)
        return 3
    try:
        from sandbox import enter
        proof=enter({"output_dir":str(out),"period_path":admission.original["period_path"],
          "legacy_capture":str(SUBSET),"oms_path":str(SUBSET),"export_dir":str(SUBSET),
          "read_only_paths":[str(repo/"scripts"),str(repo/"research/neuron_learning"),
              "/home/alex/.local/share/heptatrader/laya-runtime",
              admission.original["initial_parent_directory"],admission.original["runtime_directory"],
              str(OLD),str(plan_path),str(PRIOR/"current_001.safetensors"),
              str(GUARD),"/proc/pressure/memory"]})
        write_once(out/"sandbox.json",proof)
        return run(out,admission,proof,plan)
    finally:
        admission.lease.release()


if __name__=="__main__":
    raise SystemExit(main())
