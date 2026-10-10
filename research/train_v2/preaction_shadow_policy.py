"""Read-only original IB PAPER source -> genuine *shadow-only* action sampler.

Its conditional action probability is exact because this new research policy
owns a closed signal grammar and an explicit unbiased two-bit selection map.
This is NEVER the original model/trader behavior policy. It has no broker,
order, model, production signer, native LedgerWriter or promotion API.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import secrets
import time

from .adapters import make_spec
from .causal_ttl import sealed_original_broker_expiry
from .original_policy_audit import write_once

SCHEMA = "heptatrader.train-v2-shadow-preaction-policy.v1"
POLICY_ID = "SHADOW_RESEARCH_PREACTION_TWO_BIT_NO_ORDER_V1"
CANDIDATES = ("NO_TRADE", "UP_SIGNAL", "DOWN_SIGNAL")
DRAW_MAPPING = ("NO_TRADE", "NO_TRADE", "UP_SIGNAL", "DOWN_SIGNAL")
DENOMINATOR = 4
MAX_SPREAD_BPS = Decimal("3")
MAX_SOURCE_AGE_MS = 1500
MAX_HISTORY_GAP_MS = 30000
REQUIRED_HISTORY = 6
HORIZON_MS = 300000


def _clock_ms() -> int:
    return time.time_ns() // 1000000


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value) -> bytes:
    return json.dumps(value, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode()


def _hold(reason: str, at: int, source_path: Path) -> dict:
    return {
        "schema": SCHEMA,
        "status": "HOLD_NO_ELIGIBLE_FRESH_SOURCE",
        "reason": reason,
        "attempted_at_ms": at,
        "source_path_sha256": _sha(str(source_path).encode()),
        "candidate_decision_created": False,
        "local_prospective_plan_presealed": False,
        "behavior_propensity_attested": False,
        "product_native_writer_acked": False,
        "independent_product_signer_admitted": False,
        "broker_queries_sent": 0,
        "broker_orders_sent": 0,
        "trading_authority": False,
    }


def _check_directory_scope(config: dict, path: Path) -> bool:
    """Accept only configured resident PAPER feed segment paths."""
    try:
        if (path.is_symlink() or path.parent.is_symlink()
                or path.parent.parent.is_symlink()
                or path.name != "quotes.jsonl"
                or not path.parent.name.startswith("segment-")
                or path.parent.parent != Path(config["feed_directory"])):
            return False
        return path.is_absolute() and path.is_file()
    except (KeyError, ValueError, OSError):
        return False


def _policy_selection(source: dict, *, draw) -> dict:
    bid, ask = Decimal(source["bid"]), Decimal(source["ask"])
    spread = (ask - bid) * Decimal(20000) / (ask + bid)
    if not spread.is_finite() or spread < 0:
        raise ValueError("INVALID_SOURCE_SPREAD")
    if spread > MAX_SPREAD_BPS:
        legal = ("NO_TRADE",)
        weights = {"NO_TRADE": DENOMINATOR}
        selected = "NO_TRADE"
        word = None
    else:
        legal = CANDIDATES
        weights = {"NO_TRADE": 2, "UP_SIGNAL": 1, "DOWN_SIGNAL": 1}
        word = draw()
        if type(word) is not int or not 0 <= word < DENOMINATOR:
            raise ValueError("SAMPLER_NOT_TWO_BIT_UNBIASED")
        selected = DRAW_MAPPING[word]
    if sum(weights.values()) != DENOMINATOR or selected not in legal:
        raise ValueError("INVALID_POLICY_SUPPORT")
    return {
        "candidates": list(legal),
        "selected": selected,
        "selection_word_two_bit": word,
        "selection_weights": weights,
        "probability_numerator": weights[selected],
        "probability_denominator": DENOMINATOR,
        # Exact Q32, since each policy probability is an integral quarter.
        "selected_propensity_q32_raw": weights[selected] * (2**32 // DENOMINATOR),
        "omitted_count_bound": 0,
        "complete_for_shadow_generator": True,
        "hard_filter_spread_bps": format(spread, "f"),
        "policy_mechanism": "CSPRNG_TWO_BIT_MAPPED_TO_CLOSED_SHADOW_SIGNAL_GRAMMAR",
    }


def decide_from_pinned_source(config_path: Path, source_path: Path,
                              *, registry_root: Path | None = None,
                              plan_path: Path | None = None,
                              claims_root: Path | None = None,
                              clock=_clock_ms, draw=lambda: secrets.randbits(2)) -> dict:
    """No broker access; reverify source on each call; never backdate in CLI."""
    from hepta_decision_attribution import read_private, strict

    at = clock()
    if type(at) is not int or at <= 0:
        raise ValueError("LOCAL_CLOCK_INVALID")
    config_path = Path(config_path)
    source_path = Path(source_path)
    if not config_path.is_absolute() or config_path.is_symlink():
        return _hold("CONFIG_NOT_PINNED", at, source_path)
    try:
        config = strict(read_private(config_path, 65536))
        if (config.get("schema") != "heptatrader.laya-resident-config.v1"
                or config.get("automatic_trading") is not False
                or config.get("period_sha256") is None
                or not _check_directory_scope(config, source_path)):
            return _hold("SOURCE_NOT_CONFIGURED_FEED", at, source_path)
        period_path = Path(config["period_path"])
        if period_path.is_symlink() or not period_path.is_absolute():
            return _hold("PERIOD_UNTRUSTED", at, source_path)
        period_bytes = read_private(period_path, 65536)
        period = strict(period_bytes)
        if (period.get("execution_domain") != "PAPER"
                or _sha(period_bytes) != config["period_sha256"]):
            return _hold("ORIGINAL_PERIOD_CHANGED", at, source_path)
        spec = make_spec(config["period_sha256"], POLICY_ID, broker_reported_expiry=True)
        observations, proof = sealed_original_broker_expiry(source_path, spec)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # No data interpolation, no reliance on plain trust/error messages.
        return _hold("SOURCE_FAILED_CANONICAL_READ:" + type(exc).__name__,
                     at, source_path)

    # Interpret decision time AFTER the original hash-chain snapshot is read:
    # never backdate a decision before the data it may have consumed.
    at = clock()
    if type(at) is not int or at <= 0:
        raise ValueError("LOCAL_CLOCK_INVALID_AFTER_SOURCE_READ")
    if len(observations) < REQUIRED_HISTORY:
        return _hold("INSUFFICIENT_ORIGINAL_QUOTE_HISTORY", at, source_path)
    latest = observations[-1]
    if (not latest["event_at_ms"] <= latest["available_at_ms"] <= at
            or not 0 <= at - latest["available_at_ms"] <= MAX_SOURCE_AGE_MS
            or at > latest["valid_until_ms"]):
        return _hold("ORIGINAL_SOURCE_EXPIRED", at, source_path)
    history = observations[-REQUIRED_HISTORY:]
    if (any(row["available_at_ms"] > at for row in history)
            or any(b["event_at_ms"] - a["event_at_ms"] > MAX_HISTORY_GAP_MS
                   for a, b in zip(history, history[1:]))
            or len({row["source_id"] for row in history}) != REQUIRED_HISTORY):
        return _hold("SOURCE_HISTORY_NOT_CAUSAL", at, source_path)

    # A future source is insufficient: the exact immutable local plan must
    # have been DURABLY preregistered at least 30s before this decision.
    # This is still local research trust, not signed product/owner admission.
    if registry_root is None or plan_path is None:
        return _hold("PREACTION_PLAN_NOT_REGISTERED", at, source_path)
    try:
        from historical_training.common import read
        from .registry import verify_registration, MIN_LEAD_MS
        plan = read(Path(plan_path), 16384)
        receipt = verify_registration(Path(registry_root), plan, spec, source_path)
        if (plan["behavior_policy_id"] != POLICY_ID
                or receipt["policy_id"] != POLICY_ID
                or receipt["period_sha256"] != config["period_sha256"]
                or receipt["instrument"] != "EUR.USD"
                or type(receipt["sealed_at_ms"]) is not int
                or type(receipt["first_decision_ms"]) is not int
                or type(receipt["last_decision_ms"]) is not int
                or receipt["first_decision_ms"] > at
                or receipt["last_decision_ms"] < at
                or receipt["sealed_at_ms"] + MIN_LEAD_MS > at
                or receipt["independent_owner_signature"] is not False
                or receipt["canonical_agentd_owner_enrolled"] is not False):
            return _hold("PREACTION_PLAN_WINDOW_OR_AUTHORITY_MISMATCH",
                         at, source_path)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        return _hold("PREACTION_PLAN_REVALIDATION_FAILED:" + type(exc).__name__,
                     at, source_path)

    # In the standalone CLI, one immutable local research claim is fsynced
    # BEFORE RNG selection. A crash after this point is indeterminate and
    # cannot silently reroll the same (plan, source, policy) episode.
    episode_id = _sha(_canonical({
        "policy_id": POLICY_ID, "plan_id": plan["plan_id"],
        "source_id": latest["source_id"],
        "period_sha256": config["period_sha256"],
    }))
    claim_digest = None
    if claims_root is not None:
        claims_root = Path(claims_root)
        if claims_root != Path(registry_root) / "shadow-claims":
            raise ValueError("EPISODE_CLAIM_ROOT_UNBOUND")
        claim = {
            "schema": "heptatrader.shadow-research-preselection-claim.v1",
            "episode_id": episode_id,
            "policy_id": POLICY_ID,
            "plan_id": plan["plan_id"],
            "source_quote_receipt": latest["source_id"],
            "source_prefix_sha256": proof["prefix_sha256"],
            "claimed_before_rng_at_ms": at,
            "trade_authority": False,
            "native_product_witness": False,
        }
        try:
            claim_digest = write_once(claims_root / (episode_id + ".json"), claim)
        except FileExistsError:
            return _hold("PREACTION_EPISODE_ALREADY_CLAIMED_INDETERMINATE",
                         at, source_path)

    selection = _policy_selection(latest, draw=draw)
    # A source that expired while policy code ran may not be admitted.
    committed_at = clock()
    if (type(committed_at) is not int or committed_at < at
            or committed_at > latest["valid_until_ms"]
            or committed_at - latest["available_at_ms"] > MAX_SOURCE_AGE_MS
            or committed_at < receipt["first_decision_ms"]
            or committed_at > receipt["last_decision_ms"]):
        return _hold("SOURCE_EXPIRED_DURING_SELECTION", committed_at, source_path)

    code_digest = _sha(Path(__file__).read_bytes())
    candidate_receipt = {
        "policy_id": POLICY_ID,
        "generator_code_sha256": code_digest,
        "grammar": list(CANDIDATES),
        "hard_filter_max_spread_bps": str(MAX_SPREAD_BPS),
        "canonical_order": selection["candidates"],
        "candidate_count": len(selection["candidates"]),
        "omitted_count_bound": 0,
        "complete_for_shadow_generator": True,
        "source_quote_receipt": latest["source_id"],
    }
    candidate_receipt["candidate_set_digest"] = _sha(_canonical(candidate_receipt))
    decision = {
        "schema": SCHEMA,
        "status": "RESEARCH_SHADOW_PREACTION_ONLY",
        "policy_id": POLICY_ID,
        "source_kind": "ORIGINAL_PAPER_LOCAL_SHA256_CHAIN_NOT_BROKER_SIGNED",
        "source_quote_receipt": latest["source_id"],
        "original_source_prefix_sha256": proof["prefix_sha256"],
        "source_at_ms": latest["event_at_ms"],
        "source_available_at_ms": latest["available_at_ms"],
        "source_expires_at_ms": latest["valid_until_ms"],
        "source_snapshot_read_finished_at_ms": at,
        "decision_at_ms": committed_at,
        "pre_outcome_horizon_ms": HORIZON_MS,
        "outcome_not_available_before_ms": committed_at + HORIZON_MS,
        "source_history_quote_receipts": [q["source_id"] for q in history],
        "local_prospective_plan_presealed": True,
        "local_prospective_plan_id": receipt["plan_id"],
        "local_plan_seal_digest": _sha(_canonical(receipt)),
        "local_plan_sealed_at_ms": receipt["sealed_at_ms"],
        "independent_plan_registration_authority": False,
        "shadow_episode_id": episode_id,
        "local_episode_claimed_before_rng": claim_digest is not None,
        "local_episode_preselection_claim_digest": claim_digest,
        "product_acknowledgement_unknown_not_retriable": True,
        "candidate_set_receipt": candidate_receipt,
        **selection,
        "genuine_behavior_propensity_only_for_new_shadow_policy": True,
        "original_opaque_llm_trading_policy_propensity_qualified": False,
        "not_an_original_broker_PREPARED_command": True,
        "product_native_ledger_writer_acked": False,
        "production_generator_root_trust_signed": False,
        "independent_observer_outcome_signed": False,
        "dataset_snapshot_v3_frozen": False,
        "after_cost_execution_pnl_observed": False,
        "broker_requests_or_orders": 0,
        "model_inference_or_weights_changed": False,
        "trade_authority": False,
    }
    decision["decision_digest"] = _sha(_canonical(decision))
    return decision


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--source-file", type=Path, required=True)
    parser.add_argument("--registry-root", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    record = decide_from_pinned_source(
        args.config, args.source_file,
        registry_root=args.registry_root, plan_path=args.plan,
        claims_root=(args.registry_root / "shadow-claims"
                     if args.registry_root is not None else None))
    digest = write_once(args.output, record)
    print(json.dumps({
        "status": record["status"],
        "reason": record.get("reason"),
        "decision_created": record["status"] == "RESEARCH_SHADOW_PREACTION_ONLY",
        "original_trading_propensity_attested": False,
        "record_sha256": digest,
        "broker_orders_sent": 0,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
