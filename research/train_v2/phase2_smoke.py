"""Reproduce Phase 2 on existing PRIVATE evidence; never use live broker APIs."""
import argparse
import json
import os
from pathlib import Path

from historical_training.common import read, sha
from .adapters import make_spec, sealed_live_segment
from .parity import compare_numeric_laya_price_inputs
from .economics import modeled_net_edge
from .contracts import ContractError, compile_dataset
from .orchestrator import register_plan, evidence_event, SOURCE_EVENT
from .handoff import verify_original_paper_handoff


def run(period_file, original_segment, cost_file):
    period_sha = sha(period_file)
    spec = make_spec(period_sha, "RESEARCH_REPLAY_NO_CANONICAL_OWNER")
    observations, original = sealed_live_segment(original_segment, spec)
    parity = None
    for item in observations[20: min(len(observations), 384)]:
        try:
            parity = compare_numeric_laya_price_inputs(
                spec, observations, item["available_at_ms"], horizon_sec=300)
            break
        except ContractError:
            continue
    if parity is None:
        raise ContractError("PHASE2_NO_VALID_ORIGINAL_NUMERIC_LAYA_PARITY")
    cost = read(cost_file, 1024*1024)
    weak = modeled_net_edge("0.6", cost, period_sha)
    hypothetical = modeled_net_edge("20", cost, period_sha)
    if weak["assessment"] != "RESEARCH_ABSTAIN_COST_GATE":
        raise ContractError("PHASE2_WEAK_EDGE_NOT_ABSTAIN")
    if hypothetical["assessment"] != "UNQUALIFIED_POSITIVE_MODELED_EDGE":
        raise ContractError("PHASE2_HIGH_EDGE_STRESS_INVALID")
    decision = parity["available_at_ms"]
    plan = register_plan(
        spec=spec, registered_at_ms=decision-5000,
        first_decision_ms=decision, last_decision_ms=decision+60000,
        minimum_mature_rows=16)
    dataset = compile_dataset(spec, observations, [decision], decision)
    sample = dataset["examples"][0]
    event = evidence_event(
        plan, sample, kind=SOURCE_EVENT, at_ms=decision,
        evidence_sha256=sample["features"]["input_receipts"][-1]["source_sha256"])
    try:
        verify_original_paper_handoff(
            plan, dataset, event, original_segment, now_ms=None)
    except ContractError as exc:
        if "ORIGINAL_EVENT_NOT_FRESH" not in str(exc):
            raise
        past_replay_blocked = True
    else:
        raise ContractError("PHASE2_RETROSPECTIVE_SOURCE_FALSIFIED_AS_FRESH")
    return {
        "schema": "heptatrader.train-v2-phase2-readonly-proof.v1",
        "period_sha256": period_sha,
        "original_chain_head": original["chain_head"],
        "original_quote_count": len(observations),
        "source_family": "ORIGINAL_PAPER_LOCAL_CHAIN_NOT_BROKER_SIGNATURE",
        "price_family_parity": {
            "numeric_feature_width": len(parity["numeric16"]),
            "laya_price_class_count": len(parity["laya_price_question"]["crit"]),
            "original_numeric_profile": parity["original_numeric_feature_version"],
            "direct_generic_four_to_numeric16_cast": False,
            "laya_trade_action_head": False,
        },
        "cost": {
            "original_quote_and_commission_callback_receipt_verified": True,
            "reported_fee_execution_count": cost["fx_fee_complete_unique_executions"],
            "weak_expected_edge": weak["assessment"],
            "large_expected_edge": hypothetical["assessment"],
            "stress_hypothetical_cost_bps": weak["scenarios"][-1]["total_hypothetical_cost_bps"],
            "actual_slippage_attested": False,
            "account_pnl_attested": False,
        },
        "old_input_replay_denied_new_fresh_event_status": past_replay_blocked,
        "new_source_event_admitted": False,
        "prospective_owner_registered": False,
        "new_trial_created": False,
        "automatic_model_training": False,
        "broker_queries_sent": 0,
        "broker_orders_sent": 0,
        "trade_authority": False,
    }


def main():
    parser = argparse.ArgumentParser()
    for name in ("period", "original-segment", "cost-receipt", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    result = run(args.period, args.original_segment, args.cost_receipt)
    output = args.output
    if output.exists() or output.is_symlink():
        raise ContractError("PHASE2_IMMUTABLE_OUTPUT_REQUIRED")
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(output, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump(result, file, indent=2, sort_keys=True)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    print(json.dumps({
        "numeric16_verified": True,
        "laya_sevenbin_verified": True,
        "original_ib_fee_evidence": True,
        "modeled_weak_edge": result["cost"]["weak_expected_edge"],
        "old_event_replay_refused": True,
        "new_trial_created": False,
        "broker_orders_sent": 0,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
