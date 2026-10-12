"""Isolated EUR/USD ORIGINAL-PAPER pre-action fee hurdle, ZERO-RETURN control.

No API/network/broker queries; no trained model; never makes orders or modifies
the trading policy. Produces a genuinely prospective locally pre-registered
NO_TRADE control. This is not an independent signer or product LedgerWriter.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import stat
import time

from hepta_decision_attribution import read_private, strict
from .adapters import make_spec
from .causal_ttl import sealed_original_broker_expiry
from .contracts import digest
from .economics import verify_cost_receipt
from .orchestrator import register_plan
from .original_policy_audit import write_once
from .registry import register_before_window, verify_registration, MIN_LEAD_MS

SCHEMA = "heptatrader.fx-original-cost-shadow.v1"
MANIFEST_SCHEMA = "heptatrader.fx-cost-control-manifest.v1"
ZERO_POLICY = "ZERO_RETURN_NO_ALPHA_CLAIM_V1"
MIN_EDGE_BPS = Decimal("2")
SLIPPAGE_BPS_PER_SIDE = Decimal("2")
MAX_LAST_QUOTE_AGE_MS = 1500
MAX_HISTORY_GAP_MS = 30000
LOOKBACK = 6
HORIZON_MS = 300000
CODE_SHA = lambda: hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class CostShadowError(ValueError):
    pass


def reject(condition, reason):
    if not condition:
        raise CostShadowError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def numeric(v):
    reject(not isinstance(v, bool) and type(v) in (str, int, float),
           "NONNUMERIC_COST")
    try:
        x = Decimal(str(v))
    except InvalidOperation as exc:
        raise CostShadowError("INVALID_COST") from exc
    reject(x.is_finite() and x >= 0 and x <= Decimal("100000"),
           "UNBOUNDED_COST")
    return x


def _load_private(path: Path, limit=65536):
    reject(path.is_absolute() and not path.is_symlink() and ".." not in path.parts,
           "UNTRUSTED_PRIVATE_PATH")
    raw = read_private(path, limit)
    return strict(raw), raw


def _scope(config_path, cost_path):
    config, raw_config = _load_private(Path(config_path))
    period_path = Path(config.get("period_path", ""))
    period, period_raw = _load_private(period_path)
    cost, cost_raw = _load_private(Path(cost_path))
    reject(config.get("schema") == "heptatrader.laya-resident-config.v1"
           and config.get("automatic_trading") is False
           and config.get("period_sha256") == sha(period_raw)
           and period.get("execution_domain") == "PAPER"
           and period.get("started_at_ms", 0) > 0
           and config.get("feed_directory") is not None,
           "ORIGINAL_PAPER_CONFIG_SCOPE")
    verify_cost_receipt(cost, sha(period_raw))
    root = Path(config["feed_directory"])
    reject(root.is_absolute() and not root.is_symlink() and root.is_dir()
           and stat.S_IMODE(root.stat().st_mode) == 0o700,
           "ORIGINAL_FEED_ROOT_NOT_PRIVATE")
    return config, sha(period_raw), cost, sha(cost_raw)


def _manifest(period_sha, cost_sha):
    material = {
        "schema": MANIFEST_SCHEMA,
        "original_period_sha256": period_sha,
        "historical_cost_receipt_sha256": cost_sha,
        "zero_return_control": ZERO_POLICY,
        "zero_return_forecast_mid_bps": "0",
        "minimum_modeled_net_edge_bps": str(MIN_EDGE_BPS),
        "slippage_stress_bps_per_side": str(SLIPPAGE_BPS_PER_SIDE),
        "comparison_cost_quantile": "P90_BROKER_CALLBACK_FEES",
        "spread_method": "MAX_CURRENT_ORIGINAL_QUOTE_AND_HISTORICAL_P99",
        "source_kind": "ORIGINAL_LOCAL_PAPER_HASH_CHAIN",
        "prediction_model_fitted": False,
        "original_trader_behavior_propensity_known": False,
        "trade_authority": False,
        "actor_update": False,
        "promotion_allowed": False,
        "model_weights_changed": False,
        "broker_requests_sent": 0,
        "broker_orders_sent": 0,
        "implementation_sha256": CODE_SHA(),
    }
    return {**material,
            "behavior_policy_id": "FX_COST_ZERO_" + digest(material)[:20]}


def cost_frontier(*, bid, ask, cost):
    """Current causally known full spread counted exactly once.

    This ZERO-RETURN baseline cannot select a market direction; no gross edge
    is manufactured from confidence, historical labels or a future bar.
    """
    bid, ask = numeric(bid), numeric(ask)
    reject(bid > 0 and ask >= bid, "INVALID_ORIGINAL_BID_ASK")
    mid = (bid + ask)/2
    live = (ask - bid)/mid * 10000
    h_p99 = numeric(cost["quote_spread_full_bps"]["p99"])
    fee_p90 = numeric(cost["fx_commission_one_way_bps"]["p90"])
    reject(h_p99 > 0 and fee_p90 > 0, "COST_ESTIMATE_UNAVAILABLE")
    stressed_spread = max(live, h_p99)
    fees = fee_p90*2
    slip = SLIPPAGE_BPS_PER_SIDE*2
    hurdle = stressed_spread + fees + slip + MIN_EDGE_BPS
    forecast = Decimal(0)
    return {
        "original_quote_full_spread_bps": str(live),
        "historical_p99_stress_spread_bps": str(h_p99),
        "precommitted_stress_spread_bps": str(stressed_spread),
        "commission_p90_roundtrip_bps": str(fees),
        "hypothetical_slippage_roundtrip_bps": str(slip),
        "minimum_net_edge_reserve_bps": str(MIN_EDGE_BPS),
        "required_predicted_abs_mid_move_bps": str(hurdle),
        "forecast_zero_control_bps": str(forecast),
        "cost_vs_prediction_net_bps": str(forecast - hurdle),
        "choice": "NO_TRADE",
        "expected_market_direction": None,
        "actual_execution_profit_attested": False,
        "actual_slippage_verified": False,
    }


def preseal(*, config_path, cost_path, registry_root, root, now=None):
    config, period_sha, _cost, cost_sha = _scope(config_path, cost_path)
    manifest = _manifest(period_sha, cost_sha)
    policy = manifest["behavior_policy_id"]
    spec = make_spec(period_sha, policy, lookback=LOOKBACK,
                     horizon_ms=HORIZON_MS, broker_reported_expiry=True)
    clock = time.time_ns()//1000000 if now is None else now
    reject(type(clock) is int and clock > 0, "CLOCK_INVALID")
    plan = register_plan(spec=spec, registered_at_ms=clock,
                         first_decision_ms=clock + 45000,
                         last_decision_ms=clock + 165000,
                         minimum_mature_rows=16)
    root = Path(root)
    registry_root = Path(registry_root)
    reject(root.is_absolute() and not root.is_symlink()
           and registry_root.is_absolute() and not registry_root.is_symlink(),
           "OUTPUT_SCOPE")
    # Any partial preseal is unambiguously an orphan; no implicit repair.
    write_once(root/"manifest.json", manifest)
    write_once(root/"feature-spec.json", spec)
    write_once(root/"plan.json", plan)
    receipt = register_before_window(registry_root, plan, spec,
                                     Path(config["feed_directory"]))
    write_once(root/"local-registration.json", receipt)
    return {
        "state": "PRESEALED_LOCAL_RESEARCH_ONLY",
        "plan_id": plan["plan_id"],
        "first_decision_ms": plan["first_decision_ms"],
        "last_decision_ms": plan["last_decision_ms"],
        "registered_at_ms": receipt["sealed_at_ms"],
        "period_sha256": period_sha,
        "trade_authority": False,
        "broker_queries_sent": 0,
        "broker_orders_sent": 0,
    }


def _source_file_scope(config, source):
    source = Path(source)
    root = Path(config["feed_directory"])
    reject(source.is_absolute() and ".." not in source.parts
           and source.name == "quotes.jsonl"
           and source.parent.name.startswith("segment-")
           and source.parent.parent == root
           and not source.is_symlink() and not source.parent.is_symlink(),
           "SOURCE_FILE_OUTSIDE_REGISTERED_ORIGINAL_FEED")


def run(*, config_path, cost_path, registry_root, root, source_file,
        at_ms=None):
    config, period_sha, cost, cost_sha = _scope(config_path, cost_path)
    root, registry_root = Path(root), Path(registry_root)
    manifest, manifest_raw = _load_private(root/"manifest.json")
    spec, _ = _load_private(root/"feature-spec.json")
    plan, _ = _load_private(root/"plan.json")
    registration, _ = _load_private(root/"local-registration.json")
    reject(manifest == _manifest(period_sha, cost_sha)
           and spec == make_spec(period_sha, manifest["behavior_policy_id"],
                                 lookback=LOOKBACK,horizon_ms=HORIZON_MS,
                                 broker_reported_expiry=True)
           and plan.get("behavior_policy_id") == manifest["behavior_policy_id"]
           and plan.get("period_sha256") == period_sha
           and registration.get("plan_id") == plan.get("plan_id"),
           "PRESEALED_SOURCE_OR_COST_OR_CODE_CHANGED")
    _source_file_scope(config, source_file)
    receipt = verify_registration(registry_root, plan, spec, source_file)
    reject(receipt == registration, "PRECOMMIT_RECEIPT_CHANGED")
    rows, provenance = sealed_original_broker_expiry(source_file, spec)
    # Check time AFTER reading and authenticating all source bytes.
    now = time.time_ns()//1000000 if at_ms is None else at_ms
    reject(type(now) is int and plan["first_decision_ms"] <= now
           <= plan["last_decision_ms"]
           and now >= receipt["sealed_at_ms"] + MIN_LEAD_MS,
           "DECISION_NOT_IN_PRECOMMITTED_WINDOW")
    reject(len(rows) >= LOOKBACK, "NO_ORIGINAL_SIX_QUOTE_HISTORY")
    history = rows[-LOOKBACK:]
    latest = history[-1]
    reject(all(type(q["event_at_ms"]) is int
               and q["available_at_ms"] <= now
               and q["event_at_ms"] <= q["available_at_ms"]
               for q in history), "NONCAUSAL_INPUT_QUOTE")
    reject(len({q["source_id"] for q in history}) == LOOKBACK
           and all(b["event_at_ms"]-a["event_at_ms"] <= MAX_HISTORY_GAP_MS
                   for a,b in zip(history,history[1:])),
           "STALE_OR_NONCONTIGUOUS_HISTORY")
    reject(0 <= now-latest["available_at_ms"] <= MAX_LAST_QUOTE_AGE_MS
           and now <= latest["valid_until_ms"],
           "LATEST_BROKER_ORIGINAL_QUOTE_EXPIRED")
    economics = cost_frontier(bid=latest["bid"],ask=latest["ask"],
                              cost=cost)
    episode_id = digest({"policy_id":manifest["behavior_policy_id"],
                         "plan_id":plan["plan_id"],
                         "latest_source_id":latest["source_id"],
                         "cost_sha256":cost_sha})
    claim = {
        "schema":"heptatrader.fx-cost-shadow-predecision-claim.v1",
        "episode_id":episode_id,
        "plan_id":plan["plan_id"],
        "latest_original_quote":latest["source_id"],
        "source_prefix_sha256":provenance["prefix_sha256"],
        "historical_cost_receipt_sha256":cost_sha,
        "claimed_at_ms":now,
        "behavior_policy_id":manifest["behavior_policy_id"],
        "trade_authority":False,
        "broker_orders_sent":0,
    }
    claim_sha=write_once(registry_root/"shadow-claims"/(episode_id+".json"),claim)
    # No RNG, trading command, downstream trained forecast or outcome lookup.
    decision_time = time.time_ns()//1000000 if at_ms is None else at_ms
    reject(decision_time >= now
           and decision_time <= latest["valid_until_ms"]
           and decision_time <= plan["last_decision_ms"]
           and decision_time-latest["available_at_ms"] <= MAX_LAST_QUOTE_AGE_MS,
           "SOURCE_EXPIRED_BEFORE_DECISION_DURABLE_COMMIT")
    decision = {
        "schema":SCHEMA,
        "state":"FROZEN_ORIGINAL_PAPER_COST_SHADOW_NO_TRADE",
        "research_eligibility":"ZERO_RETURN_CONTROL_ONLY_NOT_A_TRAINED_STRATEGY",
        "behavior_policy_id":manifest["behavior_policy_id"],
        "period_sha256":period_sha,
        "plan_id":plan["plan_id"],
        "original_quote_source_id":latest["source_id"],
        "input_quote_source_ids":[q["source_id"] for q in history],
        "source_prefix_sha256":provenance["prefix_sha256"],
        "source_quote_available_at_ms":latest["available_at_ms"],
        "source_quote_expiry_ms":latest["valid_until_ms"],
        "decision_at_ms":decision_time,
        "outcome_not_available_before_ms":decision_time+HORIZON_MS,
        "claim_sha256":claim_sha,
        "episode_id":episode_id,
        "cost_receipt_sha256":cost_sha,
        "manifest_sha256":sha(manifest_raw),
        "economic_cost_hurdle":economics,
        "candidate_set":["NO_TRADE"],
        "selected_action":"NO_TRADE",
        "new_market_order_prepared":False,
        "broker_query_or_order_by_trial":0,
        "trained_model_applied":False,
        "test_or_validation_reselected":False,
        "new_execution_pnl_observed":False,
        "original_trader_policy_propensity_known":False,
        "canonical_product_native_ledger_acked":False,
        "production_owner_enrolled":False,
        "automatic_training":False,
        "model_promotion_authorized":False,
        "trade_authority":False,
    }
    output_sha=write_once(root/"frozen-decision.json",decision)
    durable_at=time.time_ns()//1000000 if at_ms is None else at_ms
    result={
        "schema":"heptatrader.fx-cost-shadow-local-qualification.v1",
        "decision_sha256":output_sha,
        "durable_at_ms":durable_at,
        "broker_reported_original_quote_expiry_ms":latest["valid_until_ms"],
        "decision_durable_before_expiry":durable_at<=latest["valid_until_ms"],
        "state":("RESEARCH_PREACTION_NO_TRADE_DURABLE_BEFORE_ORIGINAL_TTL"
                 if durable_at<=latest["valid_until_ms"] else
                 "HOLD_COST_SHADOW_DECISION_DURABLE_AFTER_TTL"),
        "model_promotion_authorized":False,"trade_authority":False,
        "broker_queries_sent":0,"broker_orders_sent":0,
    }
    write_once(root/"qualification.json",result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode",required=True,choices=("preseal","decide"))
    for name in ("config","cost","registry-root","root"):
        parser.add_argument("--"+name,required=True,type=Path)
    parser.add_argument("--source-file",type=Path)
    args=parser.parse_args()
    os.umask(0o077)
    common={"config_path":args.config,"cost_path":args.cost,
            "registry_root":args.registry_root,"root":args.root}
    if args.mode=="preseal":
        result=preseal(**common)
    else:
        reject(args.source_file is not None, "EXPLICIT_ORIGINAL_SOURCE_REQUIRED")
        result=run(**common,source_file=args.source_file)
    print(json.dumps(result,sort_keys=True))
    if result.get("state","").startswith("HOLD_"):
        raise SystemExit(2)


if __name__=="__main__":
    main()
