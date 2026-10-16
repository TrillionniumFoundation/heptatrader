"""Single bounded Pocket4 Action V2 adapter experiment on genuine original-source rows.

Research-only: original frozen parent, sealed historical closed outcomes, immutable
Oct7-8 training / Oct9 holdout. A single shared original heavy-worker guard
covers all real ROCm operations. No execution, orders, auto-promotion or live
service mutation. Last-layer Action LoRA learns on *genuine parent hidden
activations* (not newly synthesized feature vectors).
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
import time

from .integrity import ContractError, digest, read_json, sha_file
from .resident_action_v2_closed_source import check_train_source
from .action_v2_temporal_pairing import time_matched_action_pairs
from .resident_action_v2_research import (
    decide_train_only_action, balanced_action_loss, v2_action_inference,
)

SOURCE_SHA = "3d701e3795cb438b5ba38f3bb7c44bed83003e040108d1378d1084635157e4a9"
PARENT_ID = "76d6b1bf3d1b00505277c88a6d93159f3f17fe2511a65f21fc99647cf56b8ff6"
TRAIN_END = 1791504000000
HOLD_END = 1791590400000
MAX_WALL_SECONDS = 505
EPOCHS = 2
LEARNING_RATE = 0.001
MAX_PROB_DIFF = 0.0002
SCHEMA = "heptatrader.laya-action-v2-pocket4-genuine-adapter-fit.v1"


def split_artifact(artifact):
    manifest = check_train_source(artifact)
    if (manifest["frozen_parent_id"] != PARENT_ID or
        manifest["training_rows"] != 298 or
        manifest["frozen_parent_correct_ACT"] != 14 or
        manifest["frozen_parent_wrong_ESCALATE"] != 284 or
        manifest["training_cutoff_ms"] < HOLD_END):
        raise ContractError("V2_EXACT_ORIGINAL_SOURCE_MISMATCH")
    train, hold = [], []
    for row in artifact["rows"]:
        at = row["anchor_ms"]
        available = row["future_outcome_available_ms"]
        if at < TRAIN_END and available <= TRAIN_END:
            train.append(row)
        elif at < TRAIN_END:  # old but late label is never used
            continue
        elif at < HOLD_END:
            hold.append(row)
        else:
            raise ContractError("V2_OUT_OF_DECLARED_CALENDAR")
    if (len(train), sum(r["parent_correct"] for r in train),
        len(hold), sum(r["parent_correct"] for r in hold)) != (178, 9, 120, 5):
        raise ContractError("V2_HISTORICAL_TIME_COHORT_CHANGED")
    return manifest, train, hold


def _read_source(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if (not stat.S_ISREG(st.st_mode) or
            not 0 < st.st_size <= 4 * 1024 * 1024):
            raise ContractError("V2_ORIGINAL_PRIVATE_SOURCE_SCOPE")
        raw = f.read(4 * 1024 * 1024 + 1)
    if len(raw) != st.st_size or hashlib.sha256(raw).hexdigest() != SOURCE_SHA:
        raise ContractError("V2_ORIGINAL_PRIVATE_SOURCE_HASH_CHANGED")
    def pairs(kvs):
        ret = {}
        for k, v in kvs:
            if k in ret:
                raise ContractError("V2_DUPLICATE_SOURCE_JSON_KEY")
            ret[k] = v
        return ret
    return json.loads(raw, object_pairs_hook=pairs)


def parent_probability_drift(historical, recomputed):
    # Numeric parity is mandatory *before* any Action V2 optimizer step;
    # a matching candidate ID and file hashes are not enough.
    if (type(historical) not in (list,tuple) or
        type(recomputed) not in (list,tuple) or
        len(historical)!=7 or len(recomputed)!=7 or
        any(type(p) not in (int,float) or not math.isfinite(p) or not 0<=p<=1
            for p in list(historical)+list(recomputed)) or
        abs(sum(historical)-1)>1e-5 or abs(sum(recomputed)-1)>1e-5):
        raise ContractError("V2_PARENT_PARITY_PROBABILITY_CONTRACT")
    return max(abs(x-y) for x,y in zip(historical,recomputed))


def binary_scores(probabilities, labels):
    if len(probabilities) != len(labels) or not labels:
        raise ContractError("V2_BINARY_OUTCOME_COUNT")
    if any(p is None or type(p) not in (int, float)
           or not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities):
        raise ContractError("V2_BINARY_OOD_OR_BAD_PROB")
    n = len(labels)
    brier = sum(2 * (p-y)**2 for p, y in zip(probabilities, labels)) / n
    nll = -sum(y*math.log(max(p, 1e-12))
               + (1-y)*math.log(max(1-p, 1e-12))
               for p, y in zip(probabilities, labels)) / n
    return {"denominator": n, "real_correct_ACT": sum(labels),
            "binary_brier": brier, "binary_nll": nll,
            "action_proposals_at_5_of_6": sum(p >= 5/6 for p in probabilities)}


def write_immutable(directory, name, obj):
    p = directory / name
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    data = json.dumps(obj, sort_keys=True, separators=(",",":"),allow_nan=False).encode() + b"\n"
    with os.fdopen(fd, "wb") as f:
        f.write(data); f.flush(); os.fsync(f.fileno())
    return hashlib.sha256(data).hexdigest()


def real_fit(args, started):
    import torch
    from torch.nn import functional as F
    from safetensors.torch import load_file, save_file
    from .resident_train import validate_parent
    from .dataset import encode_rows
    from .model import load_verified_laya
    from .resident_model import ResidentDecisionCell

    torch.set_num_threads(2)
    torch.manual_seed(20261016)
    original = _read_source(args.source)
    manifest, train, hold = split_artifact(original)
    initial = read_json(args.parent / "candidate.json")
    verified = validate_parent(args.parent)
    if verified["candidate_id"] != PARENT_ID or initial["base_id"] != verified["base_id"]:
        raise ContractError("V2_FROZEN_PARENT_ID_CHANGED")
    base_manifest = read_json(args.base / "base-manifest.json")
    if args.base.resolve() == args.parent.resolve():
        raise ContractError("V2_BASE_PARENT_PATH_COLLISION")

    def deadline():
        if time.monotonic()-started > MAX_WALL_SECONDS:
            raise ContractError("V2_RESEARCH_WORKER_TIME_BUDGET")
    deadline()
    print(json.dumps({"phase":"LOAD_VERIFIED_FROZEN_LAYA","rows":len(train)+len(hold),
                      "research_only":True,"broker_orders_sent":0}), flush=True)
    pretrained, tokenizer, base_id = load_verified_laya(args.base,base_manifest)
    if base_id != verified["base_id"]:
        raise ContractError("V2_BASE_ID_MISMATCH")
    cell = ResidentDecisionCell(pretrained,base_id)
    cell.restore_parent(load_file(str(args.parent/"adapter.safetensors")),
                        allow_scorer_only=True)
    cell.eval()
    layer=cell.model.get_submodule("act_head.2")
    original_a=layer.A.detach().clone()
    original_b=layer.B.detach().clone()
    cache=[]
    def capture(module, tensorargs):
        v=tensorargs[0]
        if tuple(v.shape) != (1, 256):
            raise ContractError("V2_ACT_HIDDEN_SHAPE_CHANGED")
        cache.append(v.detach().cpu().reshape(256))
    handle=layer.register_forward_pre_hook(capture)
    logits, action_logits, hidden, price_ce=[],[],[],[]
    maximum_probability_drift=0.
    target_rows=train+hold
    with torch.no_grad():
        for index,row in enumerate(target_rows):
            deadline()
            assert not cache
            batch={k:v.to("cuda:0") for k,v in encode_rows(tokenizer,[row]).items()}
            p,a=cell(**batch)
            if (tuple(p.shape),tuple(a.shape),len(cache)) != ((1,7),(1,2),1):
                raise ContractError("V2_PARENT_FORWARD_OR_HOOK_SHAPE")
            prob=torch.softmax(p,dim=-1)[0].detach().cpu().tolist()
            drift=parent_probability_drift(row["frozen_parent_probs"],prob)
            maximum_probability_drift=max(maximum_probability_drift,drift)
            if drift > MAX_PROB_DIFF:
                # Independent, same-process SAME tensor replay. Never turn a
                # mismatch into training. Capture GPU repeat instability before
                # attributing it to source/weights/kernel without evidence.
                same_input_replays=[prob]
                for _replay in range(3):
                    cache.clear()
                    pp,aa=cell(**batch)
                    if tuple(pp.shape)!=(1,7) or tuple(aa.shape)!=(1,2):
                        raise ContractError("V2_DIAGNOSTIC_REPLAY_SHAPE")
                    same_input_replays.append(torch.softmax(pp,dim=-1)[0].detach().cpu().tolist())
                    cache.clear()
                repeat_max=max(abs(x-y) for trial in same_input_replays[1:]
                               for x,y in zip(prob,trial))
                evidence={
                    "schema":"heptatrader.laya-v2-real-parent-parity-negative.v1",
                    "phase":"BEFORE_ANY_OPTIMIZER",
                    "source_sha256":SOURCE_SHA,
                    "original_parent_manifest_id":manifest["manifest_id"],
                    "parent_candidate_id":verified["candidate_id"],
                    "base_id":base_id,
                    "row_index":index,
                    "row_identity_digest":digest([row["plan_id"],row["slot"],row["row_id"]]),
                    "historically_sealed_parent_probs":list(row["frozen_parent_probs"]),
                    "genuine_recomputed_parent_probs":prob,
                    "same_input_gpu_replay_probs":same_input_replays,
                    "same_input_gpu_replay_max_abs_difference":repeat_max,
                    "probabilities_max_abs_difference":drift,
                    "reported_original_target_class":row["target_class"],
                    "original_parent_correct":row["parent_correct"],
                    "training_steps":0,
                    "broker_orders_sent":0,
                    "model_promotion":False
                }
                evidence["observation_id"]=digest(evidence)
                sha=write_immutable(args.output_dir,"parent-parity-mismatch-v1.json",evidence)
                print(json.dumps({"phase":"FAIL_ORIGINAL_PARENT_PARITY",
                                  "max_abs_diff":drift,
                                  "historical_argmax":max(range(7),key=row["frozen_parent_probs"].__getitem__),
                                  "recomputed_argmax":max(range(7),key=prob.__getitem__),
                                  "evidence_sha256":sha,
                                  "training_steps":0}),flush=True)
                raise ContractError("V2_FROZEN_PARENT_PRICE_PARITY_FAILED")
            if (max(range(7),key=prob.__getitem__)==row["target_class"]) is not row["parent_correct"]:
                raise ContractError("V2_FROZEN_PARENT_LABEL_IDENTITY_FAILED")
            logits.append(p[0].detach().cpu())
            action_logits.append(a[0].detach().cpu())
            hidden.append(cache.pop())
            price_ce.append(float(F.cross_entropy(p,torch.tensor([row["target_class"]],device=p.device))))
            if index == 0 or (index+1)%32==0 or index+1 == len(target_rows):
                print(json.dumps({"phase":"REAL_FROZEN_PARENT_FORWARD",
                                  "done":index+1,"total":len(target_rows),
                                  "max_probability_abs_diff":round(maximum_probability_drift,9)}), flush=True)
    handle.remove()
    cell.assert_frozen()
    deadline()
    original_action=torch.stack(action_logits).float().to("cuda:0")
    source_hidden=torch.stack(hidden).float().to("cuda:0")
    source_price_ce=torch.tensor(price_ce,dtype=torch.float32,device="cuda:0")
    profile=decide_train_only_action(
        train,cutoff_ms=TRAIN_END,
        parent_action_logits=original_action[:len(train)])
    matched=time_matched_action_pairs(train,profile)
    if (not matched["eligible"] or len(matched["pairs"])!=8
            or matched["skipped_act_rows"]!=1):
        raise ContractError("V2_REAL_CAUSAL_ACTION_PAIRING_CHANGED")
    original_train_logits=original_action[:len(train)].clone()
    original_hold_logits=original_action[len(train):].clone()
    # Cache only hidden data that was physically computed by the frozen parent.
    # Construct the exact pre-adapter base logits from actual upstream outputs.
    original_delta=(source_hidden[:,:64]@original_a.T)@original_b.T
    base_logits=original_action-original_delta
    optimizer=torch.optim.AdamW([layer.A,layer.B],lr=LEARNING_RATE,weight_decay=0.)
    steps=[]
    print(json.dumps({"phase":"ACTION_ADAPTER_FIT","pairs":len(matched["pairs"]),
                      "true_unique_action_rows":16,
                      "training_epochs":EPOCHS,"learning_rate":LEARNING_RATE,
                      "research_only":True}),flush=True)
    for epoch in range(EPOCHS):
        for ai,ni in matched["pairs"]:
            deadline()
            ix=torch.tensor([ai,ni],device="cuda:0",dtype=torch.long)
            feats=source_hidden.index_select(0,ix)
            fixed=base_logits.index_select(0,ix)
            correctness=torch.tensor([train[ai]["parent_correct"],train[ni]["parent_correct"]],
                                     device="cuda:0",dtype=torch.bool)
            if int(correctness.sum()) != 1:
                raise ContractError("V2_ACT_BALANCED_PAIR_CHANGED")
            optimizer.zero_grad(set_to_none=True)
            modified=fixed+(feats[:,:64]@layer.A.T)@layer.B.T
            price_loss=source_price_ce.index_select(0,ix).mean()
            weighted=balanced_action_loss(modified,correctness,price_loss,profile)
            weighted.backward()
            gradnorm=float(torch.nn.utils.clip_grad_norm_([layer.A,layer.B],1.,error_if_nonfinite=True))
            if not math.isfinite(gradnorm):
                raise ContractError("V2_NONFINITE_ACTION_GRADIENT")
            optimizer.step()
            steps.append({"epoch":epoch+1,"pair":len(steps)%len(matched["pairs"])+1,
                          "weighted_loss":float(weighted.detach()),
                          "gradient_norm_preclip":gradnorm})
    cell.eval()
    changed_a=int((layer.A.detach()!=original_a).sum())
    changed_b=int((layer.B.detach()!=original_b).sum())
    if changed_a+changed_b==0:
        raise ContractError("V2_ZERO_ACTUAL_ADAPTER_OPTIMIZER_UPDATE")
    norm=cell.norm_report()
    if not norm["within_profile"]:
        raise ContractError("V2_ACTION_ADAPTER_NORM_PROFILE_REFUSED")
    with torch.no_grad():
        updated=base_logits+(source_hidden[:,:64]@layer.A.T)@layer.B.T
        # A full model forward must reproduce the exact cached-activation path.
        last=target_rows[-1]
        cache.clear()
        check={k:v.to("cuda:0") for k,v in encode_rows(tokenizer,[last]).items()}
        new_p,new_a=cell(**check)
        validated=float((updated[-1]-new_a[0]).abs().max())
        if validated > 0.0002:
            raise ContractError("V2_CACHE_TO_REAL_FORWARD_MISMATCH")
        if max(abs(float(x)-float(y)) for x,y in zip(
             torch.softmax(new_p,dim=-1)[0].cpu().tolist(),last["frozen_parent_probs"]))>MAX_PROB_DIFF:
            raise ContractError("V2_ACTION_FIT_CHANGED_FROZEN_PRICE")
        before=v2_action_inference(original_hold_logits,profile)
        after=v2_action_inference(updated[len(train):],profile)
        labels=[int(r["parent_correct"]) for r in hold]
        prior=(sum(int(r["parent_correct"]) for r in train)+.5)/(len(train)+1)
        result={
            "schema":SCHEMA,"source_sha256":SOURCE_SHA,
            "source_manifest_id":manifest["manifest_id"],
            "frozen_parent_id":PARENT_ID,
            "base_id":base_id,
            "base_manifest_sha256":sha_file(args.base/"base-manifest.json"),
            "frozen_parent_manifest_sha256":sha_file(args.parent/"candidate.json"),
            "frozen_parent_adapter_sha256":sha_file(args.parent/"adapter.safetensors"),
            "program_sha256":sha_file(Path(__file__)),
            "train_cutoff_ms":TRAIN_END,
            "holdout_end_ms":HOLD_END,
            "train_rows":len(train),"train_real_correct":sum(r["parent_correct"] for r in train),
            "holdout_rows":len(hold),"holdout_real_correct":sum(labels),
            "real_model_forward_rows":len(target_rows),
            "max_frozen_parent_price_prob_abs_drift":maximum_probability_drift,
            "first_and_last_model_parent_input_verified":True,
            "original_action_logit_training_temperature":profile["action_temperature_training_only"],
            "action_training_profile_id":profile["profile_id"],
            "causal_pair_count":len(matched["pairs"]),
            "unique_real_action_training_rows":16,
            "excluded_true_ACT_training_rows":matched["skipped_act_rows"],
            "max_match_gap_ms":matched["max_pair_gap_ms"],
            "fit_optimizer":"AdamW","fit_epochs":EPOCHS,
            "fit_gradient_steps":len(steps),
            "fit_distinct_rows_not_steps":16,
            "changed_action_A_parameters":changed_a,
            "changed_action_B_parameters":changed_b,
            "full_forward_cached_activation_max_abs_difference":validated,
            "within_original_relative_norm_profile":bool(norm["within_profile"]),
            "scores":{
                "train_prior":binary_scores([prior]*len(hold),labels),
                "frozen_action_v2_prior_corrected":binary_scores(before["action_correct_probs"],labels),
                "trained_action_v2_prior_corrected":binary_scores(after["action_correct_probs"],labels)},
            "before_out_of_distribution_abstentions":before.get("ood_abstentions",0),
            "after_out_of_distribution_abstentions":after.get("ood_abstentions",0),
            "all_scored_original_price_logits_unchanged":True,
            "known_historical_not_prospective":True,
            "full_end_to_end_price_training":False,
            "no_synthetic_or_future_training_labels":True,
            "worker_holds_existing_shared_lease":True,
            "no_orders_or_broker_queries":True,
            "no_model_promotion":True,
            "no_live_model_cutover":True,
            "trade_authority":False,
            "device":str(torch.cuda.get_device_properties(0).gcnArchName),
        }
        result["report_id"]=digest(result)
    model_path=args.output_dir/"action-only-standalone-v2.safetensors"
    if model_path.exists():
        raise ContractError("V2_OUTPUT_MODEL_ALREADY_EXISTS")
    # Store only 66 genuinely trained LoRA-factor coordinates, never the private
    # original data or the full 644MB pretrained base.
    save_file({"action.A":layer.A.detach().cpu().contiguous(),
               "action.B":layer.B.detach().cpu().contiguous()},str(model_path))
    model_path.chmod(0o400)
    result["research_adapter_sha256"]=sha_file(model_path)
    result["receipt_id"]=digest(result)
    receiptsha=write_immutable(args.output_dir,"observed-results-v1.json",result)
    dirfd=os.open(args.output_dir,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(dirfd)
    finally:os.close(dirfd)
    print(json.dumps({"state":"COMPLETED_RESEARCH_ONLY","receipt_sha256":receiptsha,
                      "report_id":result["report_id"],
                      "scores":result["scores"],
                      "gradient_steps":len(steps),
                      "research_only":True,"broker_orders_sent":0}),flush=True)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source",type=Path,required=True)
    p.add_argument("--base",type=Path,required=True)
    p.add_argument("--parent",type=Path,required=True)
    p.add_argument("--guard-config",type=Path,required=True)
    p.add_argument("--guard-module-dir",type=Path,required=True)
    p.add_argument("--output-dir",type=Path,required=True)
    args=p.parse_args()
    for x in (args.source,args.base,args.parent,args.guard_config,
              args.guard_module_dir,args.output_dir):
        if not x.is_absolute() or x.is_symlink():
            raise ContractError("V2_RESEARCH_ABSOLUTE_NON_SYMLINK_PATHS")
    if (not args.output_dir.is_dir() or args.output_dir.stat().st_mode & 0o077
            or list(args.output_dir.iterdir())):
        raise ContractError("V2_NEW_PRIVATE_EMPTY_OUTPUT_DIRECTORY_REQUIRED")
    started=time.monotonic()
    # Import REAL installed, SHA-pinned guard before allocating GPU memory.
    sys.path.insert(0,str(args.guard_module_dir))
    from historical_training.resident_resource_guard import Gate,verify_config,read
    c=read(args.guard_config)
    original=verify_config(c)
    if (original["automatic_trading"] is not False
            or original["base_directory"]!=str(args.base)
            or original["initial_parent_directory"]!=str(args.parent)):
        raise ContractError("V2_GUARD_ORIGINAL_SCOPE_CHANGED")
    g=Gate(c,"trainer")
    if not g.acquire({"role":"v2_original_action_fit", "source_sha256":SOURCE_SHA}):
        x=g.last_admission or {}
        print(json.dumps({"state":"DENIED_BY_ORIGINAL_SHARED_GUARD",
                          "reasons":x.get("reasons",[]),
                          "new_training_started":False,
                          "broker_orders_sent":0}),flush=True)
        raise SystemExit(3)
    try:
        return real_fit(args,started)
    finally:
        g.release()


if __name__=="__main__":
    main()
