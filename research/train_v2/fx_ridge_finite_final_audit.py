"""Immutable final audit of the four 2026-10-12 prospective EUR/USD Ridge windows.

Strictly read-only against original PAPER campaign evidence; requires separate
per-window original-prefix/forecast/maturity rechecks. This audits neither
broker statement PnL nor independently broker-signed source timestamps.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
import stat

CAMPAIGN = "heptatrader.fx-ridge-finite-prospective-campaign.v1"
INDEPENDENT = "heptatrader.fx-ridge-independent-original-check.v1"
WINDOWS = 4
INTERVAL = 1200000
HORIZON = 900000
LEAD_MS = 30000
ZERO = Decimal(0)

class CohortAuditHold(ValueError):
    pass

def require(ok, code):
    if not ok:
        raise CohortAuditHold(code)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def decimal(value):
    require(type(value) in (str, int, float) and type(value) is not bool,
            "NUMERIC_TYPE_INVALID")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise CohortAuditHold("NUMERIC_PARSE_INVALID") from None
    require(result.is_finite(), "NUMERIC_NONFINITE")
    return result

def read_private(path, limit=1024*1024):
    path = Path(path)
    require(path.is_absolute() and not path.is_symlink(), "SOURCE_PATH_INVALID")
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        a = os.fstat(fd)
        require(stat.S_ISREG(a.st_mode) and a.st_uid == os.geteuid() and
                a.st_nlink == 1 and not a.st_mode & 0o077 and
                0 < a.st_size <= limit, "SOURCE_NOT_PRIVATE_OR_OVERSIZED")
        pieces, remaining = [], a.st_size
        while remaining:
            block = os.read(fd, min(1048576, remaining))
            require(bool(block), "SOURCE_TRUNCATED")
            pieces.append(block)
            remaining -= len(block)
        b = os.fstat(fd)
        require((a.st_dev, a.st_ino, a.st_size, a.st_mtime_ns) ==
                (b.st_dev, b.st_ino, b.st_size, b.st_mtime_ns),
                "SOURCE_CHANGED_DURING_READ")
        raw = b"".join(pieces)
        def pairs_unique(pairs):
            out = {}
            for k, v in pairs:
                require(k not in out, "DUPLICATE_JSON_FIELD")
                out[k] = v
            return out
        def bad_const(_):
            raise CohortAuditHold("JSON_NONFINITE_CONSTANT")
        result = json.loads(raw, object_pairs_hook=pairs_unique,
                            parse_constant=bad_const)
        require(type(result) is dict, "JSON_OBJECT_REQUIRED")
        return result, sha(raw)
    finally:
        os.close(fd)

def private_dir(p):
    p = Path(p)
    require(p.is_absolute() and not p.is_symlink(), "EVIDENCE_DIR_INVALID")
    st = p.stat()
    require(stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid()
            and stat.S_IMODE(st.st_mode) == 0o700, "EVIDENCE_DIR_NOT_PRIVATE")
    return p

def require_nontrading(doc, kind):
    require(doc.get("trade_authority") is False and
            doc.get("broker_orders_sent") == 0, kind+"_NOT_RESEARCH_ONLY")
    if "model_promotion_allowed" in doc:
        require(doc["model_promotion_allowed"] is False,
                kind+"_MODEL_PROMOTION_ENABLED")

def check_pins(root, independent_root):
    root = private_dir(root)
    independent_root = private_dir(independent_root)
    intent, intent_sha = read_private(root/"campaign-intent.json", 8192)
    sealed, sealed_sha = read_private(root/"campaign-sealed.json", 8192)
    final, final_sha = read_private(root/"campaign-final.json", 8192)
    require(intent.get("schema") == CAMPAIGN and
            sealed.get("schema") == CAMPAIGN+".sealed.v1" and
            final.get("schema") == CAMPAIGN+".final.v1",
            "CAMPAIGN_SCHEMA_CHANGED")
    require(sealed["intent_sha256"] == intent_sha
            and final["intent_sha256"] == intent_sha
            and final["sealed_sha256"] == sealed_sha,
            "CAMPAIGN_IMMUTABILITY_CHAIN_CHANGED")
    for name, value in (("intent", intent), ("final", final)):
        if name == "intent":
            require(value["trade_authority"] is False and
                    value["broker_orders_sent"] == 0 and
                    value["broker_requests_sent"] == 0 and
                    value["automatic_training"] is False and
                    value["model_promotion_allowed"] is False and
                    value["shadow_only"] is True and
                    value["no_historical_test_reselection"] is True and
                    value["original_validation_action"] == "NO_TRADE",
                    "CAMPAIGN_INTENT_CHANGED")
        else:
            require(value["trade_authority"] is False and
                    value["broker_orders_sent"] == 0 and
                    value["automatic_promotion"] is False and
                    value["model_economic_efficacy_proven"] is False,
                    "CAMPAIGN_FINAL_CHANGED")
    require(intent["fixed_count"] == final["window_count"] == WINDOWS and
            len(intent["anchors_ms"]) == len(sealed["windows"]) ==
            len(final["window_results"]) == WINDOWS and
            intent["interval_ms"] == INTERVAL and
            sealed["fixed_model_across_all_windows"] is True and
            sealed["all_registered_before_first_decision"] is True,
            "CAMPAIGN_WINDOW_POPULATION_CHANGED")
    anchors = intent["anchors_ms"]
    require(all(type(a) is int and a % 300000 == 0 for a in anchors) and
            all(b-a == INTERVAL for a,b in zip(anchors,anchors[1:])),
            "CAMPAIGN_ANCHOR_GRID_CHANGED")
    require(intent["registered_operator_start_ms"] < sealed["sealed_at_ms"] <
            anchors[0]+1000, "CAMPAIGN_REGISTRATION_TOO_LATE")
    require(final["completed_at_ms"] >= anchors[-1]+HORIZON+6000,
            "CAMPAIGN_PREMATURE_COMPLETION")
    require(final["completed_at_ms"] <= anchors[-1]+HORIZON+60000,
            "CAMPAIGN_LATE_COMPLETION_NOT_PREDECLARED")
    require(final["successful_matured_count"] + final["censored_count"] ==
            WINDOWS, "FINAL_COUNTS_INCONSISTENT")
    return root, independent_root, intent, sealed, final, sealed_sha, final_sha

def check_window(root, independent_root, anchor, s, f):
    n = s["index"]
    require(type(n) is int and n in range(WINDOWS) and
            f["window"] == n and
            s["anchor_ms"] == anchor and
            s["outcome_due_ms"] == anchor+HORIZON and
            s["plan_id"] == f["plan_id"] and
            s["receipt_sealed_at_ms"]+LEAD_MS <= s["first_decision_ms"] and
            s["first_decision_ms"] <= s["last_decision_ms"] and
            s["last_decision_ms"] <= anchor+4500,
            "WINDOW_PLAN_OR_PRECOMMIT_CHANGED")
    cell = private_dir(root/("window-%02d" % n))
    for key, filename in [
        ("model_sha256", "model.json"),
        ("manifest_sha256", "manifest.json"),
        ("registration_sha256", "local-registration.json"),
        ("plan_sha256", "plan.json"),
        ("spec_sha256", "feature-spec.json"),
    ]:
        _, digest = read_private(cell/filename)
        require(digest == s[key], "REGISTERED_FILE_HASH_CHANGED:"+filename)
    plan, _ = read_private(cell/"plan.json")
    registration, _ = read_private(cell/"local-registration.json")
    manifest, _ = read_private(cell/"manifest.json")
    model, _ = read_private(cell/"model.json")
    require(plan["plan_id"] == registration["plan_id"] == s["plan_id"]
            and registration["sealed_at_ms"] == s["receipt_sealed_at_ms"] and
            registration["plan"] == plan and
            plan["first_decision_ms"] == s["first_decision_ms"] and
            plan["last_decision_ms"] == s["last_decision_ms"] and
            manifest["anchor_ms"] == anchor and
            manifest["outcome_due_ms"] == anchor+HORIZON and
            manifest["historical_model_sha256"] ==
                hashlib.sha256(json.dumps(model,sort_keys=True,
                    separators=(",",":")).encode()).hexdigest() and
            model["historical_validation_selected_action"] == "NO_TRADE" and
            model["candidate_has_positive_validation_edge"] is False and
            model["historical_holdout_previously_seen"] is True and
            registration["trade_authority"] is False and
            registration["broker_market_data_signed"] is False,
            "MODEL_OR_REGISTRATION_CHANGED")
    decision_attempt, decision_attempt_sha = read_private(
        cell/"decision-attempt.json", 4096)
    maturity_attempt, maturity_attempt_sha = read_private(
        cell/"maturity-attempt.json", 4096)
    require(decision_attempt_sha == f["decision_attempt_sha256"] and
            maturity_attempt_sha == f["maturity_attempt_sha256"],
            "ATTEMPT_SHA256_CHANGED")
    for stage, row, expected in (
        ("decision", decision_attempt, f["decision_state"]),
        ("maturity", maturity_attempt, f["maturity_state"]),
    ):
        require(row["schema"] == CAMPAIGN+".attempt.v1" and
                row["stage"] == stage and row["state"] == expected and
                row["nonretryable"] is True and
                row["no_historical_replay_or_sample_backfill"] is True and
                row["prospective_research_only"] is True,
                "ATTEMPT_STATUS_OR_BACKFILL_CHANGED")
        require_nontrading(row, stage)
    if f["decision_state"] == "CENSORED":
        require(f["maturity_state"] == "CENSORED" and
                "LAST_ORIGINAL_QUOTE_TOO_OLD" in decision_attempt["reason_code"] and
                maturity_attempt["reason_code"] == "NO_PREMATURE_VALID_FROZEN_DECISION",
                "CENSORED_ORIGINAL_REASONS_CHANGED")
        require(all(not (cell/name).exists() for name in
                    ("preoutcome-decision.json","qualification.json","mature-outcome.json")),
                "CENSORED_WINDOW_WAS_RETROFILLED")
        return {"window":n, "state":"CENSORED",
                "reason":"LAST_ORIGINAL_QUOTE_TOO_OLD",
                "outcome_scored":False}
    require(f["decision_state"] == "FROZEN_PRICE_ONLY"
            and f["maturity_state"] == "MATURE_PRICE_ONLY" and
            decision_attempt["reason_code"] ==
            maturity_attempt["reason_code"] == "NONE", "UNEXPECTED_SAMPLE_STATE")
    decision, decision_sha = read_private(cell/"preoutcome-decision.json", 65536)
    qualify, _ = read_private(cell/"qualification.json", 4096)
    outcome, _ = read_private(cell/"mature-outcome.json", 65536)
    independent, independent_sha = read_private(
        independent_root/("window-%02d-independent-original-v1.json" % n),
        16384)
    require(independent["schema"] == INDEPENDENT and
            independent["research_only"] is True and
            independent["broker_queries_sent"] == 0 and
            independent["broker_orders_sent"] == 0 and
            independent["model_promotion_allowed"] is False and
            independent["native_ledger_witness_admitted"] is False and
            independent["outcome"]["state"] ==
            "MATURE_ORIGINAL_PRICE_INDEPENDENTLY_RECOMPUTED" and
            independent["source_prefix_count"] > 0,
            "INDEPENDENT_ORIGINAL_SOURCE_REPLAY_MISSING")
    require(decision["plan_id"] == plan["plan_id"] and
            decision["selected_trading_action"] == "NO_TRADE" and
            decision["forecast_magnitude_clears_cost"] is False and
            decision["original_trader_behavior_propensity_attested"] is False and
            decision["native_owner_current"] is False and
            decision["actual_execution_reward"] is False and
            qualify["state"] == "PRICE_ONLY_PREDICTION_FROZEN_NO_TRADE" and
            qualify["decision_sha256"] == decision_sha and
            qualify["sealed_before_broker_reported_quote_expiry"] is True and
            qualify["selected_trading_action"] == "NO_TRADE" and
            s["first_decision_ms"]<=decision["decision_at_ms"]<=
                s["last_decision_ms"] and
            decision["decision_at_ms"]<=qualify["seal_at_ms"]<
                qualify["last_original_quote_expiry_ms"] and
            decision_attempt["attempt_recorded_at_ms"]>=qualify["seal_at_ms"],
            "NONCAUSAL_OR_EDITED_PREDECISION")
    require(outcome["predecision_sha256"] == decision_sha and
            outcome["outcome_due_ms"] == anchor + HORIZON and
            anchor+HORIZON <= outcome["price_event_ms"] <=
                anchor+HORIZON+5000 and
            outcome["price_available_ms"]>=anchor+HORIZON and
            maturity_attempt["attempt_recorded_at_ms"]>=
                anchor+HORIZON+6000 and
            outcome["selected_trading_action"] == "NO_TRADE" and
            outcome["model_efficacy_proven"] is False and
            outcome["model_promotion_allowed"] is False and
            outcome["observed_trading_pnl_attested"] is False,
            "NONCAUSAL_OR_PROMOTED_OUTCOME")
    for name, row in (("decision",decision),("qualification",qualify),
                      ("outcome",outcome)):
        require_nontrading(row,name)
    predicted = decimal(decision["forecast_midpoint_bps"])
    observed = decimal(outcome["observed_original_midpoint_return_bps"])
    signed_error = decimal(outcome["forecast_error_bps"])
    require(abs(predicted-observed-signed_error)<Decimal("1e-12")
            and abs(predicted-decimal(outcome["forecast_midpoint_bps"]))<
                Decimal("1e-12") and
            abs(predicted-decimal(independent["preaction"]["independent_forecast_bps"]))<
                Decimal("1e-9") and
            abs(observed-decimal(independent["outcome"]["observed_midpoint_return_bps"]))<
                Decimal("1e-12") and
            abs(signed_error-decimal(independent["outcome"]["forecast_error_bps"]))<
                Decimal("1e-12") and
            outcome["outcome_quote_sha256"] ==
                independent["outcome"]["outcome_quote_sha256"],
            "INDEPENDENT_PREDICTION_OR_MATURITY_DIFFERS")
    require(independent["preaction"]["outcome_due_ms"] == anchor+HORIZON
            and abs(predicted) < decimal(decision["cost_hurdle"]["required_absolute_prediction_bps"]),
            "INDEPENDENT_COST_OR_TIMELINE_CONFLICT")
    expected_correct = (predicted>0 and observed>0) or (predicted<0 and observed<0)
    require(outcome["research_direction_correct"] is expected_correct
            and independent["outcome"]["direction_correct_research_only"] is expected_correct,
            "DIRECTION_LABEL_MUTATED")
    return {
        "window": n, "state": "MATURE_PRICE_ONLY", "outcome_scored": True,
        "forecast_midpoint_bps": str(predicted),
        "observed_midpoint_return_bps": str(observed),
        "absolute_error_bps": str(abs(signed_error)),
        "zero_change_baseline_absolute_error_bps": str(abs(observed)),
        "direction_correct": expected_correct,
        "cost_required_absolute_prediction_bps":
            str(decimal(decision["cost_hurdle"]["required_absolute_prediction_bps"])),
        "independent_replay_sha256": independent_sha,
        "original_decision_sha256": decision_sha,
        "actual_execution_pnl_claimed": False,
    }

def audit(root, independent_root):
    with localcontext() as ctx:
        ctx.prec=42
        root, ind, intent, sealed, final, seal_sha, final_sha = check_pins(
            root, independent_root)
        outcomes=[]
        model_sha=set()
        for i in range(WINDOWS):
            s,f=sealed["windows"][i],final["window_results"][i]
            outcomes.append(check_window(root, ind,intent["anchors_ms"][i],s,f))
            model_sha.add(s["model_sha256"])
        require(len(model_sha)==1,"CAMPAIGN_USED_DIFFERENT_MODELS")
        mature=[r for r in outcomes if r["outcome_scored"]]
        missed=WINDOWS-len(mature)
        require(len(mature)==final["successful_matured_count"] and
                missed==final["censored_count"],"POSTHOC_SAMPLE_SET_CHANGED")
        correct=sum(r["direction_correct"] for r in mature)
        ridge=sum((decimal(r["absolute_error_bps"]) for r in mature), ZERO)
        baseline=sum((decimal(r["zero_change_baseline_absolute_error_bps"])
                      for r in mature), ZERO)
        require(mature and correct>=0, "NO_QUALIFIED_MATURE_PRICE_ROWS")
        return {
            "schema":"heptatrader.fx-ridge-finite-independent-cohort-final.v1",
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_campaign_final_sha256":final_sha,
            "registered_campaign_seal_sha256":seal_sha,
            "population_windows":WINDOWS,"mature_price_only":len(mature),
            "censored":missed,"correct_direction_count":correct,
            "signed_direction_accuracy_denominator":len(mature),
            "ridge_absolute_error_sum_bps":str(ridge),
            "zero_change_absolute_error_sum_bps":str(baseline),
            "ridge_mae_bps":str(ridge/len(mature)),
            "zero_change_mae_bps":str(baseline/len(mature)),
            "ridge_beats_zero_baseline_on_this_cohort":ridge < baseline,
            "windows":outcomes,
            "evidence_source":"LOCAL_SHA_CHAIN_AND_INDEPENDENT_READONLY_NUMERIC_REPLAY",
            "original_broker_market_data_digitally_signed":False,
            "original_trader_behavior_propensity_attested":False,
            "production_native_owner_ledger_witness_admitted":False,
            "prospective_economic_alpha_proven":False,
            "automatic_training":False,"model_promotion_allowed":False,
            "broker_queries_sent_by_audit":0,"broker_orders_sent_by_audit":0,
            "trade_authority":False,"research_only":True,
            "no_historical_replay_or_sample_backfill":True,
        }

def write_once(path, report):
    path=Path(path)
    private_dir(path.parent)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(report,f,sort_keys=True,indent=2,allow_nan=False)
            f.write("\n");f.flush();os.fsync(f.fileno())
        dfd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    except BaseException:
        path.unlink(missing_ok=True)
        raise

def main():
    p=argparse.ArgumentParser()
    for field in ("root","independent-root","output"):
        p.add_argument("--"+field,type=Path,required=True)
    a=p.parse_args()
    report=audit(a.root,a.independent_root)
    write_once(a.output,report)
    print(json.dumps({
        "state":"COMPLETE_PRICE_ONLY_RESEARCH_HOLD",
        "mature":report["mature_price_only"],"censored":report["censored"],
        "correct":report["correct_direction_count"],
        "ridge_mae_bps":report["ridge_mae_bps"],
        "baseline_mae_bps":report["zero_change_mae_bps"],
        "broker_orders_sent":0,"promotion_allowed":False,
    },sort_keys=True))

if __name__=="__main__":
    main()
