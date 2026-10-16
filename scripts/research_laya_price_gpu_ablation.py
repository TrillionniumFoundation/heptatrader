#!/usr/bin/env python3
"""Pocket4-only research ablation; no formal trial, execution or promotion.

Frozen parent scorer + 16-step original action-weight training vs price-only.
Identical time-purged, hash-bound original PAPER samples in both arms.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"research/neuron_learning"))
sys.path.insert(0,str(ROOT/"research"))
from laya_cell.price_gpu_ablation import (cohort,independent_count,score,
    frequency_prior,choose_validation,promotion_screen,ARMS)
from laya_cell.integrity import ContractError,canonical,digest,read_json,sha_file
from laya_cell.resident_store import write_once
from laya_cell.dataset import rows_for_training,verify_source_prefixes


def verified_inputs(original_job, stage):
    config=read_json(original_job)
    c=read_json(Path(stage["config"]))
    if (config["schema"] != "heptatrader.laya-resident-job.v1" or
            config["parent_directory"] != c["initial_parent_directory"] or
            config["period_sha256"] != c["period_sha256"]):
        raise ContractError("PRICE_ABLATION_FROZEN_JOB_BINDING")
    import laya_cell
    for relative, pinned in c["runtime_sha256"].items():
        if sha_file(Path(c["runtime_directory"])/relative) != pinned:
            raise ContractError("PRICE_ABLATION_INSTALLED_RUNTIME_CHANGED")
    critical=("dataset.py","model.py","resident_model.py","resident_train.py",
              "train.py","resident_row_accounting.py","integrity.py")
    for filename in critical:
        rel="learn/laya_cell/"+filename
        if (rel not in c["runtime_sha256"] or
                sha_file(ROOT/"research/neuron_learning/laya_cell"/filename)!=c["runtime_sha256"][rel]):
            raise ContractError("PRICE_ABLATION_WORKTREE_RUNTIME_MISMATCH:"+filename)
    source=Path(config["dataset"])
    if sha_file(source)!=config["dataset_sha256"]:
        raise ContractError("PRICE_ABLATION_DATASET_CHANGED")
    dataset=read_json(source,64*1024**2)
    verify_source_prefixes(dataset)
    rows,_=rows_for_training(dataset,config["window"])
    selected=cohort(rows,dataset["samples"])
    parent = read_json(Path(config["parent_directory"])/"candidate.json")
    frozen_until = parent.get("trained_until_ms",parent["window"]["training_end_ms"])
    if min(r["anchor_ms"] for r in selected["training"]) <= frozen_until:
        raise ContractError("PRICE_ABLATION_PARENT_NOT_FROZEN_BEFORE_TRAIN")
    return c,config,selected


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-job",required=True,type=Path)
    parser.add_argument("--stage",required=True,type=Path)
    parser.add_argument("--evidence",required=True,type=Path)
    args=parser.parse_args()
    os.umask(0o077)
    if args.evidence.exists() or args.evidence.is_symlink() or not args.evidence.is_absolute():
        parser.error("new absolute research evidence directory required")
    args.evidence.mkdir(mode=0o700,parents=True)
    root=args.evidence
    started=time.monotonic()
    report={"schema":"heptatrader.laya-price-gpu-ablation.v1",
            "created_at_ms":int(time.time()*1000),
            "host":"pocket4","autonomous_trading":False,
            "formal_trial_registered":False,"trained_candidate_promoted":False,
            "original_trial_rewritten":False,
            "intended_comparison":["frozen_parent","train_frequency_prior",*ARMS],
            "frozen_original_training_objective_modified":False}
    gate=None
    try:
        stage=read_json(args.stage)
        c,job,selected=verified_inputs(args.original_job,stage)
        from historical_training.resident_resource_guard import verify_config,Gate,Lease
        g=read_json(Path(stage["guard"]))
        original_lock=Path(g["state_root"])/"heavy-worker.lock"
        g["state_root"]=str(root/"admission")
        verify_config(g)
        gate=Gate(g,"trainer")
        gate.lease=Lease(original_lock)
        config_summary={
            "schema":"heptatrader.laya-price-gpu-cohort-receipt.v1",
            "job_file_sha256":sha_file(args.original_job),
            "data_file_sha256":job["dataset_sha256"],
            "parent_id":job["parent_id"],
            "parent_manifest_sha256":sha_file(Path(job["parent_directory"])/"candidate.json"),
            "parent_adapter_sha256":sha_file(Path(job["parent_directory"])/"adapter.safetensors"),
            "pretrained_base_manifest_sha256":sha_file(Path(job["base_directory"])/"base-manifest.json"),
            "source_original_period_sha256":job["period_sha256"],
            "source_code_sha256":{p:sha_file(ROOT/p) for p in (
                "scripts/research_laya_price_gpu_ablation.py",
                "research/neuron_learning/laya_cell/price_gpu_ablation.py")},
            "split_offsets_minutes":{"train_until":35,"validation_start":45,
                 "validation_end":60,"test_start":70},
            "training_count":len(selected["training"]),
            "validation_count":len(selected["validation"]),
            "test_count":len(selected["test"]),
            "time_boundaries":selected["anchor_bounds"],
            "rows_identity":selected["row_identity"],
            "training_label_cutoff_ms":selected["training_label_cutoff_ms"],
            "no_optimizer_test_labels":True, "trade_authority":False}
        config_summary["cohort_id"]=digest(config_summary)
        write_once(root/"cohort.json",config_summary)
        report["cohort_id"]=config_summary["cohort_id"]
        print("FROZEN_COHORT",json.dumps({"cohort_id":config_summary["cohort_id"],
               "anchor_bounds":config_summary["time_boundaries"]}),flush=True)
        acquired=False
        for attempt in range(16):
            if gate.acquire({"purpose":"POCKET4_PRICE_GPU_RESEARCH","attempt":attempt}):
                acquired=True
                break
            print("TRAIN_RESOURCE_WAIT",gate.last_admission.get("reasons"),flush=True)
            time.sleep(3)
        if not acquired:
            report.update(state="NOT_STARTED_RESOURCE_OR_VALUE_PRIORITY",admission=gate.last_admission)
            return

        import torch
        from torch.nn import functional as F
        from safetensors.torch import load_file,save_file
        from laya_cell.model import require_pocket4_gpu,load_verified_laya
        from laya_cell.resident_model import ResidentDecisionCell,PROFILE,answer_targets,action_loss
        from laya_cell.resident_train import validate_parent,OBJECTIVE
        from laya_cell.train import supervised_loss
        from laya_cell.dataset import encode_rows

        if job["profile"] != PROFILE or job["objective"] != OBJECTIVE:
            raise ContractError("PRICE_ABLATION_ORIGINAL_OBJECTIVE_OR_PROFILE_CHANGED")
        torch.manual_seed(20261007)
        torch.set_num_threads(2)
        device=require_pocket4_gpu()
        torch.cuda.reset_peak_memory_stats()
        parent=validate_parent(Path(job["parent_directory"]))
        raw,tok,base_id=load_verified_laya(Path(job["base_directory"]),
                        read_json(Path(job["base_directory"])/"base-manifest.json"))
        if parent["base_id"] != base_id:
            raise ContractError("PRICE_ABLATION_PARENT_BASE_CHANGED")
        cell=ResidentDecisionCell(raw,base_id)
        cell.restore_parent(load_file(str(Path(job["parent_directory"])/"adapter.safetensors")),
                            allow_scorer_only=True)
        frozen_state=cell.trainable_state()
        frozen_parent_sha=digest({k:hashlib.sha256(v.detach().cpu().numpy().tobytes()).hexdigest()
                                  for k,v in frozen_state.items()})
        train_rows=selected["training"]
        valid_rows=selected["validation"]
        test_rows=selected["test"]
        prior=frequency_prior(train_rows)
        report.update(training_class_counts={str(i):sum(r["target_class"]==i for r in train_rows)
                                               for i in range(7)},
                      training_span_independent=independent_count(train_rows),
                      validation_span_independent=independent_count(valid_rows),
                      test_span_independent=independent_count(test_rows),
                      frozen_parent_tensor_digest=frozen_parent_sha,
                      architecture=str(torch.cuda.get_device_properties(0).gcnArchName))
        def eval_probs(rows):
            out=[]
            cell.eval()
            with torch.no_grad():
                for item in rows:
                    inp={k:v.to(device) for k,v in encode_rows(tok,[item]).items()}
                    p,_=cell(**inp)
                    out.append([float(x) for x in p.softmax(-1)[0].detach().cpu().tolist()])
            return out
        def metrics(rows,probabilities):
            return score(probabilities,[r["target_class"] for r in rows])
        # Frozen model and static class counts never depend on held-out labels.
        vmetrics={"train_frequency_prior":metrics(valid_rows,[prior]*len(valid_rows)),
                  "frozen_parent":metrics(valid_rows,eval_probs(valid_rows))}
        arm_receipts={}
        for arm in ARMS:
            cell.restore_trainable(frozen_state)
            if digest({k:hashlib.sha256(v.detach().cpu().numpy().tobytes()).hexdigest()
                        for k,v in cell.trainable_state().items()}) != frozen_parent_sha:
                raise ContractError("PRICE_ABLATION_ARMS_DO_NOT_SHARE_FROZEN_PARENT")
            cell.train(True)
            opt=torch.optim.AdamW([p for p in cell.parameters() if p.requires_grad],
                                  lr=OBJECTIVE["learning_rate"],weight_decay=0.)
            steps=[]
            for offset in range(0,len(train_rows),2):
                chosen=train_rows[offset:offset+2]
                data={k:v.to(device) for k,v in encode_rows(tok,chosen).items()}
                truth=torch.tensor([r["target_class"] for r in chosen],device=device)
                tmask=torch.tensor([r["teacher_probs"] is not None for r in chosen],device=device)
                teachers=torch.tensor([r["teacher_probs"] or [0.]*7 for r in chosen],device=device)
                snapshot=cell.trainable_state()
                optimizer_before=copy.deepcopy(opt.state_dict())
                opt.zero_grad(set_to_none=True)
                p,a=cell(**data)
                ploss=supervised_loss(p,truth,teachers,tmask)
                alabels=answer_targets(p.detach(),truth)
                aloss=action_loss(a,alabels)
                loss=ploss+(0.01*aloss if arm==ARMS[0] else 0.0*aloss)
                loss.backward()
                for name,param in cell.named_parameters():
                    if not param.requires_grad and param.grad is not None:
                        raise ContractError("PRICE_ABLATION_FROZEN_PARAM_HAS_GRADIENT")
                gradnorm=float(torch.nn.utils.clip_grad_norm_(
                    [p for p in cell.parameters() if p.requires_grad],1.,error_if_nonfinite=True))
                opt.step()
                norm=cell.norm_report()
                applied=norm["within_profile"]
                if not applied:
                    cell.restore_trainable(snapshot)
                    opt.load_state_dict(optimizer_before)
                steps.append({"sample_ids":[item["sample_id"] for item in chosen],
                              "applied":applied,"gradient_norm_preclip":gradnorm,
                              "price_ce":float(ploss.detach()),
                              "weighted_action_ce":float((0.01 if arm==ARMS[0] else 0.)*aloss.detach()),
                              "effective_weight_global_norm":norm["global_relative_l2"]})
                if offset%8==0:
                    print("ARM_STEP",arm,len(steps),"PRICE_CE",round(float(ploss.detach()),5),
                          "APPLIED",applied,flush=True)
            cell.eval()
            cell.assert_frozen()
            states={name:t.detach().cpu().contiguous() for name,t in cell.trainable_state().items()}
            action_changed=sum(int((t != frozen_state[name].detach().cpu()).sum())
                               for name,t in states.items() if ".act_head." in name)
            if arm==ARMS[1] and action_changed:
                raise ContractError("PRICE_ONLY_SHOULD_NOT_UPDATE_ACTION_HEAD")
            location=root/arm
            location.mkdir(mode=0o700)
            save_file(states,str(location/"adapter.safetensors"))
            (location/"adapter.safetensors").chmod(0o400)
            validated_score=metrics(valid_rows,eval_probs(valid_rows))
            vmetrics[arm]=validated_score
            arm_receipts[arm]={"steps":len(steps),
                "applied_steps":sum(s["applied"] for s in steps),
                "rolled_back_steps":sum(not s["applied"] for s in steps),
                "train_sample_count":len(train_rows),
                "action_parameter_coordinates_changed":action_changed,
                "adapter_sha256":sha_file(location/"adapter.safetensors"),
                "validation":validated_score,"steps_diagnostics":steps,
                "trade_authority":False,"candidate_promoted":False}
            print("VALIDATION_ARM",arm,validated_score["brier"],validated_score["nll"],flush=True)
        selected_model=choose_validation(vmetrics)
        # This is the first use of the test population. All families are fixed.
        validation_freeze={"schema":"heptatrader.laya-price-validation-selection.v1",
                           "cohort_id":config_summary["cohort_id"],"selection":selected_model,
                           "validation_scores":vmetrics,"family":list(vmetrics),
                           "test_scores_used_for_selection":False,
                           "trade_authority":False}
        write_once(root/"validation-selection.json",validation_freeze)
        test_scores={"train_frequency_prior":metrics(test_rows,[prior]*len(test_rows)),
                     "frozen_parent":None}
        cell.restore_trainable(frozen_state)
        test_scores["frozen_parent"]=metrics(test_rows,eval_probs(test_rows))
        for arm in ARMS:
            weights=load_file(str(root/arm/"adapter.safetensors"))
            cell.restore_trainable(weights)
            test_scores[arm]=metrics(test_rows,eval_probs(test_rows))
        cell.restore_trainable(frozen_state)
        cell.assert_frozen()
        result=promotion_screen(selected_model,test_scores,selected)
        report.update(state="COMPLETED_GPU_ABLATION_NO_PRODUCTION_SELECTION",
                      validation_selected=selected_model, validation_scores=vmetrics,
                      test_scores=test_scores, arms=arm_receipts,screen=result,
                      static_prior=prior,
                      gpu_peak_memory_bytes=torch.cuda.max_memory_allocated(),
                      frozen_parent_adapter_unchanged=sha_file(Path(job["parent_directory"])/"adapter.safetensors")==config_summary["parent_adapter_sha256"])
    except Exception as exc:
        report.update(state="FAILED_OR_BLOCKED_GPU_ABLATION",error_type=type(exc).__name__,
                      error=str(exc)[:300])
    finally:
        if gate is not None:
            gate.release()
        report["wall_seconds"]=round(time.monotonic()-started,3)
        report["ended_at_ms"]=int(time.time()*1000)
        write_once(root/"result.json",report)
        print("ABLATED_GPU_RESULT",json.dumps({key:report.get(key) for key in
              ("state","error_type","error","wall_seconds","validation_selected","test_scores","screen")},
              sort_keys=True),flush=True)


if __name__=="__main__":
    main()
