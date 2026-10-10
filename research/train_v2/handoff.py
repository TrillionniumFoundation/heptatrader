"""Evidence-bound LOCAL handoff: fail-closed on backdated source or label.

This does NOT provide a canonical broker/Agentd signature. It independently
recompiles the claimed dataset from an original locally chained PAPER segment,
then permits only freshly *submitted* research events on a new registered plan.
"""
from __future__ import annotations

import time
from pathlib import Path

from .adapters import sealed_live_segment
from .contracts import (
    CAUSAL, LIVE, ContractError, compile_example, digest, feature_spec_id,
)
from .orchestrator import (
    SOURCE_EVENT, OUTCOME_EVENT, verify_plan, verify_event,
    _validate_dataset, ingest,
)

MAX_EVENT_HANDOFF_LAG_MS = 3000


def verify_original_paper_handoff(plan, dataset, event, original_segment,
                                  now_ms=None):
    verify_plan(plan)
    verify_event(event)
    if event["kind"] not in (SOURCE_EVENT, OUTCOME_EVENT):
        raise ContractError("ORIGINAL_CORRECTION_REQUIRES_SEPARATE_OWNER")
    if event["plan_id"] != plan["plan_id"]:
        raise ContractError("ORIGINAL_EVENT_CROSS_PLAN")
    now = time.time_ns() // 1000000 if now_ms is None else now_ms
    if type(now) is not int or not 0 <= now - event["at_ms"] <= MAX_EVENT_HANDOFF_LAG_MS:
        raise ContractError("ORIGINAL_EVENT_NOT_FRESH_NO_BACKDATING")
    if (not isinstance(dataset, dict) or dataset.get("dataset_role") != CAUSAL
            or not isinstance(dataset.get("feature_spec"), dict)):
        raise ContractError("ORIGINAL_PAPER_DATASET_REQUIRED")
    spec = dataset["feature_spec"]
    if spec.get("source_kind") != LIVE or feature_spec_id(spec) != plan["feature_spec_id"]:
        raise ContractError("ORIGINAL_PAPER_PROFILE_MISMATCH")
    example = _validate_dataset(plan, dataset, event)
    # Source reader checks the complete original hash chain, custody, PAPER
    # scope, source time and no mutation authority. It cannot broker-sign data.
    original, provenance = sealed_live_segment(Path(original_segment), spec)
    decision = example["features"]["decision_at_ms"]
    recomputed = compile_example(spec, original, decision, event["at_ms"])
    if (recomputed["feature_id"] != example["feature_id"]
            or recomputed["features"] != example["features"]
            or recomputed["label_due_ms"] != example["label_due_ms"]):
        raise ContractError("ORIGINAL_SOURCE_FEATURE_NOT_REPRODUCIBLE")
    if event["kind"] == SOURCE_EVENT:
        if (example.get("label_status") != "PENDING_OUTCOME"
                or example.get("label_receipt") is not None
                or example.get("label_return_bps") is not None
                or example.get("candidate_training_eligible") is not False):
            raise ContractError("ORIGINAL_PREDECISION_LABEL_MUST_BE_UNOBSERVED")
        if event["at_ms"] > decision or event["at_ms"] < max(
                q["available_at_ms"] for q in recomputed["features"]["input_receipts"]):
            raise ContractError("ORIGINAL_SOURCE_LATE_OR_PREDATED")
        if (event["evidence_sha256"]
                != recomputed["features"]["input_receipts"][-1]["source_sha256"]):
            raise ContractError("ORIGINAL_SOURCE_WITNESS_MISMATCH")
    else:
        if (recomputed["label_status"] != "OBSERVED"
                or example["label_status"] != "OBSERVED"
                or recomputed["label_return_bps"] != example["label_return_bps"]
                or recomputed["label_receipt"] != example["label_receipt"]
                or event["evidence_sha256"]
                != recomputed["label_receipt"]["source_sha256"]):
            raise ContractError("ORIGINAL_OUTCOME_SOURCE_OR_LABEL_MISMATCH")
    return {
        "schema": "heptatrader.train-v2-local-source-handoff.v1",
        "plan_id": plan["plan_id"],
        "event_id": event["event_id"],
        "feature_id": example["feature_id"],
        "original_chain_head": provenance["chain_head"],
        "original_prefix_sha256": provenance["prefix_sha256"],
        "quote_count": len(original),
        "verified_asof_ms": event["at_ms"],
        "original_source_chain_locally_verified": True,
        "canonical_agentd_owner_enrolled": False,
        "prospective_registration_attested_by_independent_owner": False,
        "actual_broker_signature_present": False,
        "financial_model_trained": False,
        "trade_authority": False,
        "broker_queries_sent": 0,
        "broker_orders_sent": 0,
    }


def ingest_original_event(db_file, plan, dataset, event, original_segment):
    proof = verify_original_paper_handoff(
        plan, dataset, event, original_segment, now_ms=None)
    result = ingest(db_file, plan, dataset, event)
    return {
        "schema": "heptatrader.train-v2-bound-research-ingest.v1",
        "event_id": event["event_id"],
        "local_proof_digest": digest(proof),
        "result": result["state"],
        "automatic_training": False,
        "canonical_agentd_owner_enrolled": False,
        "model_selected": False,
        "broker_orders_sent": 0,
    }
