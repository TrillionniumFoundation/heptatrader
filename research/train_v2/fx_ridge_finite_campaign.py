"""Finite research-only EUR/USD Ridge campaign over original IB PAPER quote receipts.

Four fixed, non-overlapping future grid anchors are preregistered before any
prediction. No broker calls, orders, model training, retrospective registration,
risk/driver mutations, retry-on-miss, or promotion. Local hash chaining is NOT
an independent production signature or evidence of an executable edge.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import time
from datetime import datetime, timezone
from pathlib import Path

from historical_training.common import read, sha, digest
from .adapters import make_spec
from .contracts import feature_spec_id
from .fx_ridge_original import (
    HORIZON_MS, LOOKBACK, MAX_DECISION_LATENCY_MS, CODE_SHA, ResearchHold,
    decide, recover_frozen, require
)
from .fx_ridge_outcome import mature
from .orchestrator import register_plan, verify_plan
from .original_policy_audit import write_once
from .registry import register_before_window, MIN_LEAD_MS

SCHEMA = "heptatrader.fx-ridge-finite-prospective-campaign.v1"
N_WINDOWS = 4
INTERVAL_MS = 20 * 60 * 1000
GRID_MS = 5 * 60 * 1000
SCHEDULE_LEAD_MS = 10 * 60 * 1000
MATURE_WAIT_MS = 6500
MAX_ATTEMPT_REPORT_BYTES = 4096

def now_ms():
    return time.time_ns() // 1_000_000

def campaign_source_sha():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

def grid(now: int):
    require(type(now) is int and now > 0, "CAMPAIGN_CLOCK_INVALID")
    first = ((now + SCHEDULE_LEAD_MS + GRID_MS-1) // GRID_MS) * GRID_MS
    return [first + i * INTERVAL_MS for i in range(N_WINDOWS)]

def require_private_root(root: Path, create=False):
    require(root.is_absolute() and not root.is_symlink()
            and ".." not in root.parts, "CAMPAIGN_ROOT_PATH_INVALID")
    if create:
        require(not root.exists(), "CAMPAIGN_ROOT_ALREADY_EXISTS")
        root.mkdir(mode=0o700)
    st = root.stat()
    require(stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid()
            and stat.S_IMODE(st.st_mode) == 0o700, "CAMPAIGN_ROOT_NOT_PRIVATE")
    return root

def preseal(*, root, source_root, config_path, cost_path, original_registry):
    root, source_root = Path(root), Path(source_root)
    config_path, cost_path, original_registry = map(
        Path, (config_path, cost_path, original_registry))
    require_private_root(source_root)
    config, period_sha, cost, model, original_manifest, old_plan, old_spec, old_receipt = (
        recover_frozen(source_root, config_path, cost_path))
    require(old_receipt["plan_id"] == old_plan["plan_id"]
            and old_receipt["trade_authority"] is False
            and old_receipt["sealed_at_ms"] + MIN_LEAD_MS <= old_plan["first_decision_ms"],
            "SOURCE_MODEL_ORIGINAL_PRECOMMIT_UNPROVEN")
    require(model["trade_authority"] is False
            and model["promotion_allowed"] is False
            and model["historical_validation_selected_action"] == "NO_TRADE"
            and model["candidate_has_positive_validation_edge"] is False
            and model["trained_rows"] >= 40000,
            "OLD_VALIDATION_NOT_QUALIFIED_FOR_PRICE_RESEARCH")
    # Verify the original preregistration against the actual unchanged source root.
    from .registry import verify_registration
    origin_source = Path(original_manifest["source_root"])
    segments = sorted(origin_source.glob("segment-*/quotes.jsonl"))
    require(bool(segments), "ORIGINAL_FEED_MISSING")
    require(verify_registration(original_registry, old_plan, old_spec,
                                segments[-1]) == old_receipt,
            "OLD_ORIGINAL_REGISTRY_IDENTITY_CHANGED")
    stamp = now_ms()
    require(model["source_max_bar_end_ms"] < stamp - 86400000,
            "HISTORICAL_SOURCE_NOT_SEPARATE_FROM_NEW_WINDOWS")
    anchors = grid(stamp)
    require(all(b-a > HORIZON_MS + MATURE_WAIT_MS
                for a,b in zip(anchors, anchors[1:])),
            "CAMPAIGN_WINDOWS_OVERLAP")
    require_private_root(root, create=True)
    intent = {
        "schema": SCHEMA,
        "registered_operator_start_ms": stamp,
        "anchors_ms": anchors,
        "outcome_wait_ms": MATURE_WAIT_MS,
        "fixed_count": N_WINDOWS,
        "interval_ms": INTERVAL_MS,
        "historical_model_sha256": digest(model),
        "original_model_file_sha256": sha(source_root / "model.json"),
        "original_model_root": str(source_root),
        "original_source_code_sha256": CODE_SHA(),
        "campaign_source_code_sha256": campaign_source_sha(),
        "config_path": str(config_path),
        "config_sha256": sha(config_path),
        "cost_path": str(cost_path),
        "cost_sha256": sha(cost_path),
        "original_period_sha256": period_sha,
        "original_validation_action": "NO_TRADE",
        "execution_domain": "PAPER",
        "shadow_only": True, "no_historical_test_reselection": True,
        "no_prior_price_result_used_for_this_plan": True,
        "independent_broker_signed_source": False,
        "trader_behavior_propensities_attested": False,
        "canonical_owner_or_witness_enrolled": False,
        "production_model_qualification": False,
        "trade_authority": False, "broker_requests_sent": 0,
        "broker_orders_sent": 0, "automatic_training": False,
        "model_promotion_allowed": False,
    }
    write_once(root / "campaign-intent.json", intent)
    receipts = []
    for index, anchor in enumerate(anchors):
        cell = root / ("window-%02d" % index)
        cell.mkdir(mode=0o700)
        # Exactly the same historical model and model-code digest; no fitting
        # or resampling after selecting prospective windows.
        manifest = dict(original_manifest)
        manifest["anchor_ms"] = anchor
        manifest["outcome_due_ms"] = anchor + HORIZON_MS
        spec = make_spec(period_sha, original_manifest["policy_id"],
                         horizon_ms=HORIZON_MS, lookback=LOOKBACK,
                         broker_reported_expiry=True)
        registered_at = now_ms()
        plan = register_plan(
            spec=spec, registered_at_ms=registered_at,
            first_decision_ms=anchor + 1000,
            last_decision_ms=anchor + MAX_DECISION_LATENCY_MS,
            minimum_mature_rows=16,
        )
        require(plan["first_decision_ms"] > registered_at + MIN_LEAD_MS,
                "CAMPAIGN_REGISTRATION_TOO_LATE")
        write_once(cell / "model.json", model)
        write_once(cell / "manifest.json", manifest)
        write_once(cell / "feature-spec.json", spec)
        write_once(cell / "plan.json", plan)
        receipt = register_before_window(root / "registry", plan, spec,
                                         origin_source)
        write_once(cell / "local-registration.json", receipt)
        receipts.append({
            "index": index, "anchor_ms": anchor,
            "plan_id": plan["plan_id"],
            "registration_sha256": sha(cell / "local-registration.json"),
            "model_sha256": sha(cell / "model.json"),
            "manifest_sha256": sha(cell / "manifest.json"),
            "plan_sha256": sha(cell / "plan.json"),
            "spec_sha256": sha(cell / "feature-spec.json"),
            "first_decision_ms": plan["first_decision_ms"],
            "last_decision_ms": plan["last_decision_ms"],
            "outcome_due_ms": anchor + HORIZON_MS,
            "receipt_sealed_at_ms": receipt["sealed_at_ms"],
            "trade_authority": False
        })
    finish = {
        "schema": SCHEMA + ".sealed.v1",
        "intent_sha256": sha(root / "campaign-intent.json"),
        "sealed_at_ms": now_ms(),
        "windows": receipts,
        "all_registered_before_first_decision": all(
            x["receipt_sealed_at_ms"] + MIN_LEAD_MS <= x["first_decision_ms"]
            for x in receipts),
        "fixed_model_across_all_windows": len(
            {x["model_sha256"] for x in receipts}) == 1,
        "outcomes_known_at_registration": False,
        "trading_or_promotion_authority": False
    }
    require(finish["all_registered_before_first_decision"]
            and finish["fixed_model_across_all_windows"]
            and finish["sealed_at_ms"] < anchors[0], "CAMPAIGN_SEAL_INVALID")
    write_once(root / "campaign-sealed.json", finish)
    return {"state": "FOUR_PROSPECTIVE_WINDOWS_PRESEALED_NO_TRADE",
            "count": N_WINDOWS,
            "anchors_utc": [
                datetime.fromtimestamp(x/1000, timezone.utc).isoformat()
                for x in anchors],
            "first_decision_at_ms": anchors[0] + 1000,
            "seal_sha256": sha(root / "campaign-sealed.json"),
            "trade_authority": False, "broker_orders_sent": 0}

def verify_sealed(root):
    root = require_private_root(Path(root))
    intent = read(root / "campaign-intent.json", 8192)
    sealed = read(root / "campaign-sealed.json", 8192)
    require(intent["schema"] == SCHEMA
            and sealed["schema"] == SCHEMA + ".sealed.v1"
            and sealed["intent_sha256"] == sha(root / "campaign-intent.json")
            and intent["campaign_source_code_sha256"] == campaign_source_sha()
            and intent["original_source_code_sha256"] == CODE_SHA()
            and intent["shadow_only"] is True
            and intent["automatic_training"] is False
            and intent["model_promotion_allowed"] is False
            and intent["trade_authority"] is False
            and sealed["all_registered_before_first_decision"] is True
            and sealed["fixed_model_across_all_windows"] is True
            and len(sealed["windows"]) == len(intent["anchors_ms"]) == N_WINDOWS,
            "CAMPAIGN_SEAL_NOT_ELIGIBLE")
    require(intent["anchors_ms"] == grid(
                intent["registered_operator_start_ms"])
            and all(x["anchor_ms"] == intent["anchors_ms"][n]
                    for n,x in enumerate(sealed["windows"])),
            "CAMPAIGN_ANCHORS_CHANGED")
    require(sha(Path(intent["config_path"])) == intent["config_sha256"]
            and sha(Path(intent["cost_path"])) == intent["cost_sha256"],
            "PINNED_CONFIG_OR_COST_CHANGED")
    for x in sealed["windows"]:
        cell = root / ("window-%02d" % x["index"])
        require_private_root(cell)
        for key, filename in [
            ("registration_sha256", "local-registration.json"),
            ("model_sha256", "model.json"),
            ("manifest_sha256", "manifest.json"),
            ("plan_sha256", "plan.json"),
            ("spec_sha256", "feature-spec.json"),
        ]:
            require(sha(cell / filename) == x[key],
                    "PRECOMMITTED_WINDOW_BYTES_CHANGED")
        plan = read(cell / "plan.json")
        verify_plan(plan)
        require(plan["plan_id"] == x["plan_id"]
                and x["receipt_sealed_at_ms"]+MIN_LEAD_MS<=plan["first_decision_ms"]
                and x["anchor_ms"] + HORIZON_MS == x["outcome_due_ms"],
                "PER_WINDOW_PLAN_MISMATCH")
    return intent, sealed

def note_once(cell, filename, stage, state, reason, *, now=None):
    stamp = now_ms() if now is None else now
    record = {
        "schema": SCHEMA + ".attempt.v1",
        "stage": stage, "state": state, "reason_code": reason,
        "attempt_recorded_at_ms": stamp, "nonretryable": True,
        "prospective_research_only": True,
        "no_historical_replay_or_sample_backfill": True,
        "trade_authority": False, "broker_requests_sent": 0,
        "broker_orders_sent": 0, "model_promotion_allowed": False,
    }
    write_once(cell / filename, record)
    return record

def wait_until_ms(target):
    # Wake with a little headroom; cannot change the frozen target.
    while True:
        difference = target - now_ms()
        if difference <= 0: return
        if difference > 1000:
            time.sleep(min(5., (difference - 100)/1000))
        else:
            time.sleep(min(.05, difference/1000))

def perform_event(root, intent, cell_record, stage, *, at=None,
                  decision_fn=decide, maturity_fn=mature):
    cell = root / ("window-%02d" % cell_record["index"])
    stamp = now_ms() if at is None else at
    filename = "decision-attempt.json" if stage == "decision" else "maturity-attempt.json"
    require(not (cell / filename).exists(), "EVENT_ALREADY_ATTEMPTED_NO_RETRY")
    assert stage in ("decision", "maturity")
    start = cell_record["first_decision_ms"]
    end = cell_record["last_decision_ms"]
    if stage == "decision":
        if not start <= stamp <= end:
            return note_once(cell, filename, stage, "CENSORED",
                             "MISSED_FIXED_DECISION_WINDOW", now=stamp)
    else:
        if stamp < cell_record["outcome_due_ms"] + 6000:
            return note_once(cell, filename, stage, "CENSORED",
                             "OUTCOME_TOO_EARLY_NO_BACKFILL", now=stamp)
        if not (cell / "qualification.json").is_file():
            return note_once(cell, filename, stage, "CENSORED",
                             "NO_PREMATURE_VALID_FROZEN_DECISION", now=stamp)
        qualified = read(cell / "qualification.json")
        if qualified.get("state") != "PRICE_ONLY_PREDICTION_FROZEN_NO_TRADE":
            return note_once(cell, filename, stage, "CENSORED",
                             "PREDECISION_NOT_QUALIFIED", now=stamp)
    common = {
        "config_path": Path(intent["config_path"]),
        "cost_path": Path(intent["cost_path"]),
        "registry_root": root / "registry",
        "root": cell,
    }
    try:
        result = (decision_fn(**common) if stage == "decision"
                  else maturity_fn(**common))
        state = ("FROZEN_PRICE_ONLY" if stage == "decision"
                 else "MATURE_PRICE_ONLY")
        if stage == "decision":
            require(result["state"] == "PRICE_ONLY_PREDICTION_FROZEN_NO_TRADE"
                    and result["model_promotion_allowed"] is False,
                    "DECISION_NOT_IN_FROZEN_NO_TRADE_STATE")
        else:
            require(result["state"] == "PRICE_ONLY_REAL_ORIGINAL_OUTCOME_MATURED_NOT_MODEL_EFFICACY",
                    "OUTCOME_NOT_PRICE_ONLY")
        return note_once(cell, filename, stage, state, "NONE", now=now_ms())
    except Exception as error:
        # Immutable primary evidence or a partial predecision stays untouched.
        # No retry; even a later successful quote cannot replace this sample.
        message = str(error)
        reason = ("EXCEPTION_" + type(error).__name__ + "_" + message)[:160]
        return note_once(cell, filename, stage, "CENSORED", reason, now=now_ms())

def run(root):
    os.umask(0o077)
    root = Path(root)
    intent, sealed = verify_sealed(root)
    require(not (root / "campaign-worker-start.json").exists(),
            "CAMPAIGN_WORKER_ALREADY_STARTED_NO_DUPLICATE")
    require(now_ms() < sealed["windows"][0]["first_decision_ms"],
            "CAMPAIGN_ALREADY_STARTED_OR_TOO_LATE")
    write_once(root / "campaign-worker-start.json", {
        "schema": SCHEMA + ".worker-start.v1",
        "started_at_ms": now_ms(),
        "sealed_sha256": sha(root / "campaign-sealed.json"),
        "trade_authority": False,
        "no_order_api_or_model_promotion": True,
        "automatic_retries": False,
    })
    print(json.dumps({"state":"SCHEDULED_FINITE_ORIGINAL_RESEARCH",
                      "windows":N_WINDOWS,"broker_orders_sent":0}), flush=True)
    summary=[]
    for x in sealed["windows"]:
        cell = root / ("window-%02d" % x["index"])
        wait_until_ms(x["first_decision_ms"])
        first = perform_event(root, intent, x, "decision")
        print(json.dumps({"window":x["index"],"stage":"decision",
                          "state":first["state"],
                          "reason":first["reason_code"]}),flush=True)
        wait_until_ms(x["outcome_due_ms"] + MATURE_WAIT_MS)
        mature_record=perform_event(root,intent,x,"maturity")
        print(json.dumps({"window":x["index"],"stage":"maturity",
                          "state":mature_record["state"],
                          "reason":mature_record["reason_code"]}),flush=True)
        summary.append({"window":x["index"],"plan_id":x["plan_id"],
                        "decision_attempt_sha256":sha(cell/"decision-attempt.json"),
                        "maturity_attempt_sha256":sha(cell/"maturity-attempt.json"),
                        "decision_state":first["state"],
                        "maturity_state":mature_record["state"]})
    write_once(root/"campaign-final.json",{
        "schema":SCHEMA+".final.v1","completed_at_ms":now_ms(),
        "intent_sha256":sha(root/"campaign-intent.json"),
        "sealed_sha256":sha(root/"campaign-sealed.json"),
        "window_results":summary,
        "window_count":len(summary),
        "successful_matured_count":sum(
            r["maturity_state"]=="MATURE_PRICE_ONLY" for r in summary),
        "censored_count":sum(
            r["maturity_state"]!="MATURE_PRICE_ONLY" for r in summary),
        "automatic_promotion":False,
        "model_economic_efficacy_proven":False,
        "broker_orders_sent":0,"trade_authority":False})
    return {"state":"FINITE_RESEARCH_CAMPAIGN_ENDED",
            "window_count":len(summary),"broker_orders_sent":0,
            "model_efficacy_proven":False}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--mode",required=True,choices=("preseal","run","verify"))
    p.add_argument("--root",type=Path,required=True)
    for name in ("source-root","config","cost","original-registry"):
        p.add_argument("--"+name,type=Path)
    a=p.parse_args()
    os.umask(0o077)
    if a.mode == "preseal":
        require(all((a.source_root,a.config,a.cost,a.original_registry)),
                "PRESEAL_INPUT_REQUIRED")
        r=preseal(root=a.root,source_root=a.source_root,
                  config_path=a.config,cost_path=a.cost,
                  original_registry=a.original_registry)
    elif a.mode=="run":
        r=run(a.root)
    else:
        _,s=verify_sealed(a.root)
        r={"state":"ALL_ORIGINAL_FUTURE_WINDOWS_VERIFIED",
           "windows":len(s["windows"]),"trade_authority":False,
           "broker_orders_sent":0}
    print(json.dumps(r,sort_keys=True),flush=True)
if __name__=="__main__":
    main()
