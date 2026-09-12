#!/usr/bin/env python3
"""Finalize existing SHADOW evidence without sampling, evaluating or trading.

The caller must stop collection first. Cooperative history writers are fenced
by shared directory locks while the final audit is assembled and published.
The runner state lock prevents concurrent iteration commits. A retry with the
same inputs/time returns the same immutable receipt; changed inputs fail.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import os
from pathlib import Path
import sys
from typing import Iterable

import hepta_shadow_market_history as history
import hepta_strategy_replay_evaluator as replay
import hepta_strategy_shadow_runner as runner
from hepta_strategy_contracts import (ContractError, canonical_bytes, digest_bytes,
                                      digest_document, load_document, require_int)


def finalize_campaign(*, policy_path: Path, state_path: Path,
                      receipt_paths: Iterable[Path], history_directories: Iterable[Path],
                      output_path: Path, finalized_at_ms: int, cadence_ms: int,
                      maximum_jitter_ms: int = 0) -> dict:
    receipts = list(receipt_paths)
    directories = list(history_directories)
    if not receipts or not directories or len(directories) > replay.MAXIMUM_REPLAY_RECORDS:
        raise ContractError("SHADOW_FINAL_INPUT_SET_EMPTY_OR_TOO_LARGE")
    inputs = [policy_path, state_path, *receipts]
    resolved = [path.resolve() for path in inputs]
    output = output_path.resolve()
    roots = [path.resolve() for path in directories]
    if (len(set(resolved)) != len(resolved) or len(set(roots)) != len(roots) or
            output in resolved or any(output == root or root in output.parents for root in roots)):
        raise ContractError("SHADOW_FINAL_PATH_COLLISION")
    require_int(finalized_at_ms, "SHADOW_FINAL_TIME_INVALID", minimum=0)
    before = {str(path): digest_bytes(canonical_bytes(load_document(path, "SHADOW_FINAL_INPUT")))
              for path in inputs}
    policy_value = load_document(policy_path, "SHADOW_POLICY", maximum_bytes=65536)
    policy, policy_digest = runner.load_observation_policy(
        policy_path, campaign_id=policy_value.get("campaign_id"))
    if len(receipts) != policy["maximum_iterations"]:
        raise ContractError("SHADOW_FINAL_RECEIPT_SET_INCOMPLETE")
    with ExitStack() as stack:
        stack.enter_context(runner._state_lock(state_path))
        for directory in sorted(directories):
            history._directory_metadata(directory, create=False)
            stack.enter_context(history._history_lock(directory, exclusive=False))
        state = runner._load_state(state_path, policy=policy, policy_sha256=policy_digest)
        if state["completed_iterations"] != policy["maximum_iterations"]:
            raise ContractError("SHADOW_FINAL_STATE_INCOMPLETE")
        envelopes = []
        payload_bytes = len(canonical_bytes(policy)) + len(canonical_bytes(state))
        for path in receipts:
            receipt, contents = replay._canonical_document(path, "SHADOW_FINAL_RECEIPT", maximum_bytes=262144)
            envelopes.append({"receipt": receipt, "receipt_sha256": digest_document(receipt),
                              "receipt_file_sha256": digest_bytes(contents)})
            payload_bytes += len(contents)
        envelopes.sort(key=lambda value: (value["receipt"]["started_at_ms"], value["receipt"]["decision_id"]))
        last = envelopes[-1]
        if (state["last_receipt_sha256"] != last["receipt_file_sha256"] or
                state["last_decision_id"] != last["receipt"]["decision_id"] or
                state["last_information_packet_sha256"] != last["receipt"]["information_packet_sha256"] or
                state["last_outcome"] != last["receipt"]["final_outcome"]):
            raise ContractError("SHADOW_FINAL_STATE_RECEIPT_MISMATCH")
        segments = []
        previous = None
        latest_sample = 0
        for index, directory in enumerate(directories, 1):
            audited = history.audit_history(directory, cadence_ms=cadence_ms,
                                             maximum_jitter_ms=maximum_jitter_ms)
            records = history.load_history(directory, cadence_ms=cadence_ms,
                                             maximum_jitter_ms=maximum_jitter_ms)
            if previous is not None:
                history._validate_segment_transition(previous, records[0])
            # Rotation and segment transitions have looser history continuity
            # rules. Zero missed samples needs an independent cadence proof.
            adjacent = ([previous] if previous is not None else []) + records
            for left, right in zip(adjacent, adjacent[1:]):
                if abs(right["collection_started_at_ms"] - left["collection_started_at_ms"] - cadence_ms) > maximum_jitter_ms:
                    raise ContractError("SHADOW_FINAL_SAMPLE_CADENCE_GAP")
            previous = records[-1]
            latest_sample = max(latest_sample, previous["generated_at_ms"])
            if sum(segment["record_count"] for segment in segments) + len(records) > replay.MAXIMUM_REPLAY_RECORDS:
                raise ContractError("SHADOW_FINAL_RECORD_LIMIT_EXCEEDED")
            segments.append({"segment_index": index,
                             **{key: audited[key] for key in replay.FINAL_AUDIT_SEGMENT_FIELDS
                                if key not in {"segment_index", "audit_sha256"}},
                             "audit_sha256": digest_document(audited)})
            payload_bytes += audited["history_storage_bytes"]
        if finalized_at_ms < max(latest_sample, last["receipt"]["finished_at_ms"]):
            raise ContractError("SHADOW_FINAL_TIME_PRECEDES_EVIDENCE")
        accumulator = {"schema": "hepta.shadow-finalization-inputs.v1", "inputs": before,
                       "segments": segments}
        body = {
            "schema": "hepta.bounded-shadow-final-audit-receipt.v2", "version": 2,
            **{key: policy[key] for key in ("campaign_id", "campaign_sha256", "strategy_id",
                                            "strategy_version", "strategy_sha256", "maximum_iterations")},
            "policy_sha256": policy_digest, "completed_iterations": len(receipts),
            "finalized_at_ms": finalized_at_ms, "segment_count": len(segments), "segments": segments,
            "sample_count": sum(segment["record_count"] for segment in segments),
            "missed_sample_count": 0, "missed_decision_count": 0,
            "payload_bytes_before_final_receipt": payload_bytes,
            "payload_files_before_final_receipt": 2 + len(receipts) + sum(s["record_count"] + 1 for s in segments),
            "payload_accumulator_before_final_receipt": digest_document(accumulator),
            "paper_authorized": False, "live_authorized": False,
            "mutation_attempted": False, "direct_broker_access": False,
        }
        audit = {**body, "body_sha256": digest_document(body)}
        replay._validate_final_audit(audit, policy=policy, policy_sha256=policy_digest,
                                     expected_file_sha256=digest_bytes(canonical_bytes(audit)))
        replay._validate_receipt_envelopes(envelopes, policy=policy, audit=audit,
                                           policy_sha256=policy_digest)
        after = {str(path): digest_bytes(canonical_bytes(load_document(path, "SHADOW_FINAL_INPUT")))
                 for path in inputs}
        if before != after:
            raise ContractError("SHADOW_FINAL_INPUT_DRIFT")
        if os.path.lexists(output_path):
            existing, contents = replay._canonical_document(output_path, "SHADOW_FINAL_EXISTING", maximum_bytes=4 << 20)
            if existing != audit or contents != canonical_bytes(audit):
                raise ContractError("SHADOW_FINAL_EXISTING_MISMATCH")
            return existing
        output_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        history._atomic_publish(output_path, canonical_bytes(audit), mode=0o600)
        return audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--receipt", required=True, action="append", type=Path)
    parser.add_argument("--history", required=True, action="append", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--finalized-at-ms", required=True, type=int)
    parser.add_argument("--cadence-ms", required=True, type=int)
    parser.add_argument("--maximum-jitter-ms", default=0, type=int)
    args = parser.parse_args(argv)
    try:
        audit = finalize_campaign(policy_path=args.policy, state_path=args.state,
                                  receipt_paths=args.receipt, history_directories=args.history,
                                  output_path=args.output, finalized_at_ms=args.finalized_at_ms,
                                  cadence_ms=args.cadence_ms, maximum_jitter_ms=args.maximum_jitter_ms)
        print(canonical_bytes(audit).decode(), end="")
        return 0
    except (ContractError, history.HistoryError, OSError, ValueError, KeyError, TypeError) as error:
        print(f"SHADOW finalization failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
