"""One bounded Train V2 provenance smoke; source reads only, no model/broker."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from historical_training.common import sha

from .adapters import make_spec, sealed_live_segment, sealed_revised_ib_bars
from .contracts import ContractError, compile_example, digest


def run(period_file, live_segment, bars, coverage, source_root):
    period_sha = sha(period_file)
    policy = "RESEARCH_ONLY_EXACT_PAPER_BEHAVIOR_IDENTITY_NOT_ATTESTED"
    live_spec = make_spec(period_sha, policy)
    history_spec = make_spec(period_sha, policy, historical=True)
    historic, history_receipt = sealed_revised_ib_bars(
        bars, coverage, source_root, history_spec, last_rows=128)
    hist_example = None
    for item in historic[5:-1]:
        try:
            hist_example = compile_example(
                history_spec, historic, item["event_at_ms"],
                history_receipt["conservative_available_at_ms"])
            break
        except ContractError:
            continue
    if hist_example is None or hist_example["candidate_training_eligible"] or hist_example["locally_point_in_time"]:
        raise ContractError("SMOKE_HISTORICAL_CANNOT_UPGRADE_SOURCE")
    live, live_receipt = sealed_live_segment(live_segment, live_spec)
    causal_example = None
    for item in live[5:128]:
        try:
            causal_example = compile_example(
                live_spec, live, item["available_at_ms"],
                item["available_at_ms"])
            break
        except ContractError:
            continue
    if causal_example is None or not causal_example["locally_point_in_time"]:
        raise ContractError("SMOKE_NO_VALID_LOCAL_CAUSAL_INPUT")
    if causal_example["label_status"] != "PENDING_OUTCOME":
        raise ContractError("SMOKE_NOT_AN_EARLY_DECISION")
    return {
        "schema": "heptatrader.train-v2-read-only-smoke.v1",
        "period_sha256": period_sha,
        "historical": {
            "feature_id": hist_example["feature_id"],
            "label_status": hist_example["label_status"],
            "revised_historical": hist_example["revised_historical"],
            "locally_point_in_time": hist_example["locally_point_in_time"],
            "candidate_training_eligible": hist_example["candidate_training_eligible"],
            "broker_receipts_verified": history_receipt["broker_receipts_verified"],
            "source_file_sha256": history_receipt["source_file_sha256"],
            "observed_count": len(historic),
        },
        "live": {
            "feature_id": causal_example["feature_id"],
            "label_status": causal_example["label_status"],
            "locally_point_in_time": causal_example["locally_point_in_time"],
            "candidate_training_eligible": causal_example["candidate_training_eligible"],
            "original_hash_chain_locally_verified": live_receipt["locally_verified_not_broker_signed"],
            "source_chain_head": live_receipt["chain_head"],
            "observed_count": len(live),
        },
        "source_code_sha256": {
            name: sha(Path(__file__).with_name(name)) for name in
            ("contracts.py", "adapters.py", "orchestrator.py", "event_ingest.py", "smoke.py")
        },
        "backdated_predictions_created": False,
        "prospective_signatures_created": False,
        "canonical_owner_admitted": False,
        "model_trained": False,
        "automatic_training": False,
        "production_mutation": False,
        "broker_queries_sent": 0,
        "broker_orders_sent": 0,
    }


def main():
    cli = argparse.ArgumentParser()
    for name in ("period", "live-segment", "bars", "coverage", "source-root", "output"):
        cli.add_argument("--" + name, required=True, type=Path)
    args = cli.parse_args()
    result = run(args.period, args.live_segment, args.bars, args.coverage, args.source_root)
    dest = args.output
    if dest.is_symlink() or dest.exists():
        raise ContractError("SMOKE_NEW_IMMUTABLE_OUTPUT_REQUIRED")
    dest.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    raw = json.dumps(result, sort_keys=True, indent=2) + "\n"
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    dirfd = os.open(dest.parent, os.O_DIRECTORY | os.O_RDONLY)
    try:
        os.fsync(dirfd)
    finally:
        os.close(dirfd)
    print(json.dumps({
        "schema": result["schema"],
        "historical_role": "REVISED_RESEARCH_ONLY",
        "historical_broker_receipts": result["historical"]["broker_receipts_verified"],
        "live_role": "LOCAL_CAUSAL_FEATURE_ONLY",
        "early_live_label": result["live"]["label_status"],
        "model_trained": False,
        "broker_orders_sent": 0,
        "result_sha256": hashlib.sha256(raw.encode()).hexdigest(),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
