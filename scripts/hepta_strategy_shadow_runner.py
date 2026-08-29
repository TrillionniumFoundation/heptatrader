#!/usr/bin/env python3

"""Run one deterministic, zero-authority EUR.USD SHADOW strategy iteration."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Iterator

import hepta_eurusd_confirmed_momentum_strategy as strategy_evaluator
import hepta_market_context_builder as context_builder
from hepta_strategy_contracts import (
    ContractError,
    atomic_write_json,
    canonical_bytes,
    digest_bytes,
    digest_document,
    digest_file,
    load_document,
    require_bool,
    require_digest,
    require_exact_fields,
    require_int,
    require_text,
)
import validate_hepta_strategy_decision_receipt as receipt_validator


POLICY_SCHEMA = "hepta.strategy-shadow-observation-policy.v1"
CAMPAIGN_BINDING_SCHEMA = "hepta.strategy-shadow-observation-campaign.v1"
STATE_SCHEMA = "hepta.strategy-shadow-state.v2"
SLOT_INTERVAL_MS = 2 * 60 * 1000
MAXIMUM_POLICY_ITERATIONS = 10000
POLICY_FIELDS = frozenset({
    "schema", "version", "campaign_id", "campaign_sha256",
    "strategy_id", "strategy_version", "strategy_sha256",
    "valid_after_ms", "expires_at_ms", "slot_interval_ms",
    "maximum_iterations", "maximum_lateness_ms", "shadow_only",
    "paper_authorized", "live_authorized", "mutation_attempted",
    "direct_broker_access", "body_sha256",
})
STATE_FIELDS = frozenset({
    "schema", "campaign_id", "campaign_sha256", "policy_sha256",
    "policy_body_sha256", "strategy_id", "strategy_version",
    "strategy_sha256", "valid_after_ms", "expires_at_ms",
    "slot_interval_ms", "maximum_iterations", "maximum_lateness_ms",
    "status", "completed_iterations", "last_scheduled_at_ms",
    "last_evaluated_at_ms", "last_decision_id", "last_outcome",
    "last_information_packet_sha256", "last_receipt_sha256",
    "paper_authorized", "live_authorized", "mutation_attempted",
    "direct_broker_access",
})
INPUT_LABELS = (
    "policy", "strategy", "snapshot", "quote_history", "bar_history",
    "calendar", "information",
)


def _stable_input_digests(paths: dict[str, Path]) -> dict[str, str]:
    return {
        label: digest_file(paths[label])
        for label in INPUT_LABELS
    }


def _campaign_binding(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": CAMPAIGN_BINDING_SCHEMA,
        "campaign_id": policy["campaign_id"],
        "valid_after_ms": policy["valid_after_ms"],
        "expires_at_ms": policy["expires_at_ms"],
        "slot_interval_ms": policy["slot_interval_ms"],
        "maximum_iterations": policy["maximum_iterations"],
        "maximum_lateness_ms": policy["maximum_lateness_ms"],
        "shadow_only": True,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }


def load_observation_policy(
    path: Path,
    *,
    campaign_id: str,
) -> tuple[dict[str, Any], str]:
    policy = require_exact_fields(
        load_document(path, "SHADOW_POLICY", maximum_bytes=65536),
        POLICY_FIELDS,
        "SHADOW_POLICY_FIELDS_INVALID",
    )
    try:
        contents = path.read_bytes()
    except OSError as error:
        raise ContractError("SHADOW_POLICY_READ_FAILED") from error
    if contents != canonical_bytes(policy):
        raise ContractError("SHADOW_POLICY_CANONICAL_INVALID")
    policy_sha256 = digest_bytes(contents)
    if policy["schema"] != POLICY_SCHEMA:
        raise ContractError("SHADOW_POLICY_SCHEMA_INVALID")
    require_int(
        policy["version"],
        "SHADOW_POLICY_SCHEMA_INVALID",
        minimum=1,
        maximum=1,
    )
    require_text(
        policy["campaign_id"],
        "SHADOW_POLICY_CAMPAIGN_ID_INVALID",
        identifier=True,
    )
    if policy["campaign_id"] != campaign_id:
        raise ContractError("SHADOW_POLICY_CAMPAIGN_BINDING_INVALID")
    require_digest(
        policy["campaign_sha256"],
        "SHADOW_POLICY_CAMPAIGN_DIGEST_INVALID",
    )
    require_text(
        policy["strategy_id"],
        "SHADOW_POLICY_STRATEGY_ID_INVALID",
        identifier=True,
    )
    require_text(
        policy["strategy_version"],
        "SHADOW_POLICY_STRATEGY_VERSION_INVALID",
        identifier=True,
    )
    require_digest(
        policy["strategy_sha256"],
        "SHADOW_POLICY_STRATEGY_DIGEST_INVALID",
    )
    valid_after_ms = require_int(
        policy["valid_after_ms"],
        "SHADOW_POLICY_TIME_INVALID",
        minimum=0,
    )
    expires_at_ms = require_int(
        policy["expires_at_ms"],
        "SHADOW_POLICY_TIME_INVALID",
        minimum=valid_after_ms + 1,
    )
    require_int(
        policy["slot_interval_ms"],
        "SHADOW_POLICY_CADENCE_INVALID",
        minimum=SLOT_INTERVAL_MS,
        maximum=SLOT_INTERVAL_MS,
    )
    maximum_iterations = require_int(
        policy["maximum_iterations"],
        "SHADOW_POLICY_ITERATIONS_INVALID",
        minimum=1,
        maximum=MAXIMUM_POLICY_ITERATIONS,
    )
    maximum_lateness_ms = require_int(
        policy["maximum_lateness_ms"],
        "SHADOW_POLICY_LATENESS_INVALID",
        minimum=0,
        maximum=SLOT_INTERVAL_MS - 1,
    )
    require_bool(
        policy["shadow_only"], True,
        "SHADOW_POLICY_SHADOW_BOUNDARY_INVALID",
    )
    require_bool(
        policy["paper_authorized"], False,
        "SHADOW_POLICY_PAPER_BOUNDARY_INVALID",
    )
    require_bool(
        policy["live_authorized"], False,
        "SHADOW_POLICY_LIVE_BOUNDARY_INVALID",
    )
    require_bool(
        policy["mutation_attempted"], False,
        "SHADOW_POLICY_MUTATION_BOUNDARY_INVALID",
    )
    require_bool(
        policy["direct_broker_access"], False,
        "SHADOW_POLICY_BROKER_BOUNDARY_INVALID",
    )
    final_scheduled_at_ms = (
        valid_after_ms +
        (maximum_iterations - 1) * SLOT_INTERVAL_MS
    )
    if final_scheduled_at_ms + maximum_lateness_ms >= expires_at_ms:
        raise ContractError("SHADOW_POLICY_WINDOW_INVALID")
    expected_campaign_sha256 = digest_document(_campaign_binding(policy))
    if policy["campaign_sha256"] != expected_campaign_sha256:
        raise ContractError("SHADOW_POLICY_CAMPAIGN_DIGEST_MISMATCH")
    body_sha256 = require_digest(
        policy["body_sha256"],
        "SHADOW_POLICY_BODY_DIGEST_INVALID",
    )
    body = {
        key: value
        for key, value in policy.items()
        if key != "body_sha256"
    }
    if body_sha256 != digest_document(body):
        raise ContractError("SHADOW_POLICY_BODY_DIGEST_MISMATCH")
    return policy, policy_sha256


def validate_policy_strategy_binding(
    policy: dict[str, Any],
    strategy_path: Path,
) -> tuple[dict[str, Any], str]:
    config = strategy_evaluator.load_strategy(strategy_path)
    strategy_sha256 = strategy_evaluator.strategy_package_digest(strategy_path)
    if (
            config["strategy_id"] != policy["strategy_id"] or
            config["strategy_version"] != policy["strategy_version"] or
            strategy_sha256 != policy["strategy_sha256"]):
        raise ContractError("SHADOW_POLICY_STRATEGY_BINDING_INVALID")
    return config, strategy_sha256


def _scheduled_at_ms(policy: dict[str, Any], iteration: int) -> int:
    return (
        policy["valid_after_ms"] +
        (iteration - 1) * policy["slot_interval_ms"]
    )


def _validate_slot(
    policy: dict[str, Any],
    *,
    iteration: int,
    evaluated_at_ms: int,
) -> int:
    if iteration > policy["maximum_iterations"]:
        raise ContractError("SHADOW_ITERATION_OVER_MAXIMUM")
    if evaluated_at_ms < policy["valid_after_ms"]:
        raise ContractError("SHADOW_SLOT_EARLY")
    if evaluated_at_ms >= policy["expires_at_ms"]:
        raise ContractError("SHADOW_POLICY_EXPIRED")
    scheduled_at_ms = _scheduled_at_ms(policy, iteration)
    if evaluated_at_ms < scheduled_at_ms:
        raise ContractError("SHADOW_SLOT_EARLY")
    if (
            evaluated_at_ms >
            scheduled_at_ms + policy["maximum_lateness_ms"]):
        raise ContractError("SHADOW_SLOT_LATE")
    return scheduled_at_ms


def _initial_state(
    policy: dict[str, Any],
    policy_sha256: str,
) -> dict[str, Any]:
    return {
        "schema": STATE_SCHEMA,
        "campaign_id": policy["campaign_id"],
        "campaign_sha256": policy["campaign_sha256"],
        "policy_sha256": policy_sha256,
        "policy_body_sha256": policy["body_sha256"],
        "strategy_id": policy["strategy_id"],
        "strategy_version": policy["strategy_version"],
        "strategy_sha256": policy["strategy_sha256"],
        "valid_after_ms": policy["valid_after_ms"],
        "expires_at_ms": policy["expires_at_ms"],
        "slot_interval_ms": policy["slot_interval_ms"],
        "maximum_iterations": policy["maximum_iterations"],
        "maximum_lateness_ms": policy["maximum_lateness_ms"],
        "status": "RUNNING",
        "completed_iterations": 0,
        "last_scheduled_at_ms": None,
        "last_evaluated_at_ms": None,
        "last_decision_id": None,
        "last_outcome": None,
        "last_information_packet_sha256": None,
        "last_receipt_sha256": None,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }


def _load_state(
    path: Path,
    *,
    policy: dict[str, Any],
    policy_sha256: str,
) -> dict[str, Any]:
    if not path.exists():
        return _initial_state(policy, policy_sha256)
    state = require_exact_fields(
        load_document(path, "SHADOW_STATE"),
        STATE_FIELDS,
        "SHADOW_STATE_FIELDS_INVALID",
    )
    if (
            state["schema"] != STATE_SCHEMA or
            state["campaign_id"] != policy["campaign_id"] or
            state["campaign_sha256"] != policy["campaign_sha256"] or
            state["policy_sha256"] != policy_sha256 or
            state["policy_body_sha256"] != policy["body_sha256"] or
            state["strategy_id"] != policy["strategy_id"] or
            state["strategy_version"] != policy["strategy_version"] or
            state["strategy_sha256"] != policy["strategy_sha256"] or
            state["valid_after_ms"] != policy["valid_after_ms"] or
            state["expires_at_ms"] != policy["expires_at_ms"] or
            state["slot_interval_ms"] != policy["slot_interval_ms"] or
            state["maximum_iterations"] != policy["maximum_iterations"] or
            state["maximum_lateness_ms"] !=
            policy["maximum_lateness_ms"] or
            state["status"] != "RUNNING"):
        raise ContractError("SHADOW_STATE_BINDING_INVALID")
    for field in (
            "campaign_sha256", "policy_sha256", "policy_body_sha256",
            "strategy_sha256"):
        require_digest(
            state[field],
            f"SHADOW_STATE_{field.upper()}_INVALID",
        )
    require_int(
        state["valid_after_ms"],
        "SHADOW_STATE_TIME_INVALID",
        minimum=0,
    )
    require_int(
        state["expires_at_ms"],
        "SHADOW_STATE_TIME_INVALID",
        minimum=policy["valid_after_ms"] + 1,
    )
    require_int(
        state["slot_interval_ms"],
        "SHADOW_STATE_CADENCE_INVALID",
        minimum=SLOT_INTERVAL_MS,
        maximum=SLOT_INTERVAL_MS,
    )
    require_int(
        state["maximum_iterations"],
        "SHADOW_STATE_MAXIMUM_ITERATIONS_INVALID",
        minimum=1,
        maximum=MAXIMUM_POLICY_ITERATIONS,
    )
    require_int(
        state["maximum_lateness_ms"],
        "SHADOW_STATE_LATENESS_INVALID",
        minimum=0,
        maximum=SLOT_INTERVAL_MS - 1,
    )
    require_bool(
        state["paper_authorized"], False,
        "SHADOW_STATE_PAPER_BOUNDARY_INVALID")
    require_bool(
        state["live_authorized"], False,
        "SHADOW_STATE_LIVE_BOUNDARY_INVALID")
    require_bool(
        state["mutation_attempted"], False,
        "SHADOW_STATE_MUTATION_BOUNDARY_INVALID")
    require_bool(
        state["direct_broker_access"], False,
        "SHADOW_STATE_BROKER_BOUNDARY_INVALID")
    completed = require_int(
        state["completed_iterations"],
        "SHADOW_STATE_ITERATION_INVALID",
        minimum=0,
        maximum=policy["maximum_iterations"],
    )
    if completed == 0:
        if any(
                state[field] is not None
                for field in (
                    "last_scheduled_at_ms", "last_evaluated_at_ms",
                    "last_decision_id", "last_outcome",
                    "last_information_packet_sha256",
                    "last_receipt_sha256",
                )):
            raise ContractError("SHADOW_STATE_EMPTY_HISTORY_INVALID")
    else:
        expected_scheduled_at_ms = _scheduled_at_ms(policy, completed)
        last_scheduled_at_ms = require_int(
            state["last_scheduled_at_ms"],
            "SHADOW_STATE_SCHEDULE_INVALID",
            minimum=0,
        )
        if last_scheduled_at_ms != expected_scheduled_at_ms:
            raise ContractError("SHADOW_STATE_SCHEDULE_INVALID")
        last_evaluated_at_ms = require_int(
            state["last_evaluated_at_ms"],
            "SHADOW_STATE_EVALUATED_TIME_INVALID",
            minimum=expected_scheduled_at_ms,
            maximum=(
                expected_scheduled_at_ms +
                policy["maximum_lateness_ms"]
            ),
        )
        if last_evaluated_at_ms >= policy["expires_at_ms"]:
            raise ContractError("SHADOW_STATE_EVALUATED_TIME_INVALID")
        require_text(
            state["last_decision_id"],
            "SHADOW_STATE_DECISION_ID_INVALID",
            identifier=True,
        )
        if state["last_outcome"] not in {"NO_TRADE", "SHADOW_TRADE"}:
            raise ContractError("SHADOW_STATE_OUTCOME_INVALID")
        require_digest(
            state["last_information_packet_sha256"],
            "SHADOW_STATE_PACKET_DIGEST_INVALID",
        )
        require_digest(
            state["last_receipt_sha256"],
            "SHADOW_STATE_RECEIPT_DIGEST_INVALID",
        )
    return state


@contextmanager
def _state_lock(state_path: Path) -> Iterator[None]:
    state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = state_path.with_name(f".{state_path.name}.lock")
    flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ContractError("SHADOW_STATE_LOCKED") from error
        yield
    finally:
        os.close(descriptor)


def _validate_policy_bound_receipt(
    receipt: dict[str, Any],
    *,
    policy_sha256: str,
    campaign_sha256: str,
) -> None:
    receipt_validator.validate_observation_policy_binding(
        receipt,
        policy_sha256=policy_sha256,
        campaign_sha256=campaign_sha256,
    )


def _publish_new_receipt(
    path: Path,
    receipt: dict[str, Any],
    *,
    policy_sha256: str,
    campaign_sha256: str,
) -> None:
    _validate_policy_bound_receipt(
        receipt,
        policy_sha256=policy_sha256,
        campaign_sha256=campaign_sha256,
    )
    contents = canonical_bytes(receipt)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False) as output:
            temporary_path = Path(output.name)
            os.fchmod(output.fileno(), 0o600)
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())
        validated = load_document(
            temporary_path, "SHADOW_RECEIPT", maximum_bytes=262144)
        _validate_policy_bound_receipt(
            validated,
            policy_sha256=policy_sha256,
            campaign_sha256=campaign_sha256,
        )
        os.link(temporary_path, path)
        temporary_path.unlink()
        temporary_path = None
        directory = os.open(
            path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except FileExistsError as error:
        raise ContractError("SHADOW_RECEIPT_ALREADY_EXISTS") from error
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _existing_receipt_matches(
    path: Path,
    receipt: dict[str, Any],
    *,
    policy_sha256: str,
    campaign_sha256: str,
) -> bool:
    if not path.exists():
        return False
    existing = load_document(
        path, "SHADOW_RECEIPT", maximum_bytes=262144)
    _validate_policy_bound_receipt(
        existing,
        policy_sha256=policy_sha256,
        campaign_sha256=campaign_sha256,
    )
    return (
        existing == receipt and
        path.read_bytes() == canonical_bytes(receipt)
    )


def _materialize_receipt(
    packet: dict[str, Any],
    decision: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    policy_sha256: str,
    campaign_sha256: str,
) -> dict[str, Any]:
    strategy = packet["strategy"]
    descriptor_map = snapshot.get("descriptor_sha256")
    if not isinstance(descriptor_map, dict) or not descriptor_map:
        raise ContractError("SHADOW_DESCRIPTOR_MAP_INVALID")
    if (
            snapshot.get("catalog_sha256") !=
            packet["source_snapshot"].get("catalog_sha256")):
        raise ContractError("SHADOW_CATALOG_BINDING_INVALID")
    trade_intent = decision["trade_intent"]
    evidence_refs = list(dict.fromkeys([
        policy_sha256,
        campaign_sha256,
        *decision["evidence_refs"],
    ]))
    receipt = {
        "schema": "hepta.autonomous-paper-decision-receipt.v1",
        "campaign_id": packet["campaign_id"],
        "strategy_id": strategy["strategy_id"],
        "strategy_version": strategy["strategy_version"],
        "strategy_sha256": strategy["pinned_sha256"],
        "decision_id": decision["decision_id"],
        "cycle_id": decision["cycle_id"],
        "started_at_ms": decision["started_at_ms"],
        "finished_at_ms": decision["finished_at_ms"],
        "paper_only": True,
        "live_authorized": False,
        "shadow_only": True,
        "information_packet_sha256": digest_document(packet),
        "catalog_sha256": snapshot["catalog_sha256"],
        "descriptor_sha256": digest_document(descriptor_map),
        "preflight_sha256": None,
        "regime": decision["regime"],
        "setup_gates": list(decision["setup_gates"]),
        "risk_challenges": list(decision["risk_challenges"]),
        "evidence_refs": evidence_refs,
        "conflicts": list(decision["conflicts"]),
        "decision": decision["decision"],
        "reason_codes": list(decision["reason_codes"]),
        "trade_intent": trade_intent,
        "trade_intent_sha256": (
            None if trade_intent is None else digest_document(trade_intent)),
        "campaign_open_request_id": None,
        "campaign_close_request_id": None,
        "mutation_attempted": False,
        "direct_broker_access": False,
        "final_outcome": decision["final_outcome"],
    }
    _validate_policy_bound_receipt(
        receipt,
        policy_sha256=policy_sha256,
        campaign_sha256=campaign_sha256,
    )
    return receipt


def run_shadow_iteration(
    *,
    campaign_id: str,
    iteration: int,
    evaluated_at_ms: int,
    policy_path: Path,
    strategy_path: Path,
    snapshot_path: Path,
    quote_history_path: Path,
    bar_history_path: Path,
    calendar_path: Path,
    information_path: Path,
    receipt_path: Path,
    state_path: Path,
) -> dict[str, Any]:
    require_text(
        campaign_id, "SHADOW_CAMPAIGN_ID_INVALID", identifier=True)
    require_int(iteration, "SHADOW_ITERATION_INVALID", minimum=1)
    require_int(
        evaluated_at_ms, "SHADOW_EVALUATED_TIME_INVALID", minimum=0)
    policy, policy_sha256 = load_observation_policy(
        policy_path,
        campaign_id=campaign_id,
    )
    scheduled_at_ms = _validate_slot(
        policy,
        iteration=iteration,
        evaluated_at_ms=evaluated_at_ms,
    )
    input_paths = {
        "policy": policy_path,
        "strategy": strategy_path,
        "snapshot": snapshot_path,
        "quote_history": quote_history_path,
        "bar_history": bar_history_path,
        "calendar": calendar_path,
        "information": information_path,
    }
    output_paths = {
        receipt_path.resolve(strict=False),
        state_path.resolve(strict=False),
    }
    if (
            len(output_paths) != 2 or
            any(
                path.resolve(strict=False) in output_paths
                for path in input_paths.values())):
        raise ContractError("SHADOW_INPUT_OUTPUT_PATH_COLLISION")

    before = _stable_input_digests(input_paths)
    if before["policy"] != policy_sha256:
        raise ContractError("SHADOW_POLICY_DRIFT")
    config, strategy_sha256 = validate_policy_strategy_binding(
        policy,
        strategy_path,
    )
    packet = context_builder.build_packet(
        campaign_id=campaign_id,
        iteration=iteration,
        evaluated_at_ms=evaluated_at_ms,
        strategy_path=strategy_path,
        snapshot_path=snapshot_path,
        quote_history_path=quote_history_path,
        bar_history_path=bar_history_path,
        calendar_path=calendar_path,
        information_path=information_path,
    )
    decision = strategy_evaluator.evaluate(
        packet,
        config,
        strategy_sha256,
        started_at_ms=evaluated_at_ms,
        finished_at_ms=evaluated_at_ms,
    )
    snapshot = load_document(snapshot_path, "SHADOW_SNAPSHOT")
    after = _stable_input_digests(input_paths)
    if (
            before["policy"] != after["policy"] or
            after["policy"] != policy_sha256):
        raise ContractError("SHADOW_POLICY_DRIFT")
    if before != after:
        raise ContractError("SHADOW_INPUT_DRIFT")
    if (
            packet["source_snapshot"]["file_sha256"] != after["snapshot"] or
            packet["strategy"]["config_sha256"] != after["strategy"]):
        raise ContractError("SHADOW_PACKET_INPUT_BINDING_INVALID")
    receipt = _materialize_receipt(
        packet,
        decision,
        snapshot,
        policy_sha256=policy_sha256,
        campaign_sha256=policy["campaign_sha256"],
    )
    receipt_sha256 = digest_bytes(canonical_bytes(receipt))

    with _state_lock(state_path):
        state = _load_state(
            state_path,
            policy=policy,
            policy_sha256=policy_sha256,
        )
        completed = state["completed_iterations"]
        if completed == iteration:
            if (
                    state["last_decision_id"] != receipt["decision_id"] or
                    state["last_outcome"] != receipt["final_outcome"] or
                    state["last_information_packet_sha256"] !=
                    receipt["information_packet_sha256"] or
                    state["last_receipt_sha256"] != receipt_sha256 or
                    not _existing_receipt_matches(
                        receipt_path,
                        receipt,
                        policy_sha256=policy_sha256,
                        campaign_sha256=policy["campaign_sha256"],
                    )):
                raise ContractError("SHADOW_IDEMPOTENT_REPLAY_MISMATCH")
            return {
                "packet": packet,
                "decision": decision,
                "receipt": receipt,
                "state": state,
                "idempotent": True,
            }
        if completed != iteration - 1:
            raise ContractError("SHADOW_ITERATION_SEQUENCE_INVALID")
        if receipt_path.exists():
            if not _existing_receipt_matches(
                    receipt_path,
                    receipt,
                    policy_sha256=policy_sha256,
                    campaign_sha256=policy["campaign_sha256"]):
                raise ContractError("SHADOW_RECEIPT_ALREADY_EXISTS")
        else:
            _publish_new_receipt(
                receipt_path,
                receipt,
                policy_sha256=policy_sha256,
                campaign_sha256=policy["campaign_sha256"],
            )
        published = load_document(
            receipt_path, "SHADOW_RECEIPT", maximum_bytes=262144)
        _validate_policy_bound_receipt(
            published,
            policy_sha256=policy_sha256,
            campaign_sha256=policy["campaign_sha256"],
        )
        if published != receipt:
            raise ContractError("SHADOW_RECEIPT_PUBLICATION_DRIFT")
        next_state = {
            **state,
            "completed_iterations": iteration,
            "last_scheduled_at_ms": scheduled_at_ms,
            "last_evaluated_at_ms": evaluated_at_ms,
            "last_decision_id": receipt["decision_id"],
            "last_outcome": receipt["final_outcome"],
            "last_information_packet_sha256":
                receipt["information_packet_sha256"],
            "last_receipt_sha256": receipt_sha256,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_attempted": False,
            "direct_broker_access": False,
        }
        atomic_write_json(state_path, next_state, mode=0o600)
    return {
        "packet": packet,
        "decision": decision,
        "receipt": receipt,
        "state": next_state,
        "idempotent": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--iteration", type=int, required=True)
    parser.add_argument("--evaluated-at-ms", type=int, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--strategy", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--quote-history", type=Path, required=True)
    parser.add_argument("--bar-history", type=Path, required=True)
    parser.add_argument("--calendar", type=Path, required=True)
    parser.add_argument("--information", type=Path, required=True)
    parser.add_argument("--receipt-output", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        result = run_shadow_iteration(
            campaign_id=arguments.campaign_id,
            iteration=arguments.iteration,
            evaluated_at_ms=arguments.evaluated_at_ms,
            policy_path=arguments.policy,
            strategy_path=arguments.strategy,
            snapshot_path=arguments.snapshot,
            quote_history_path=arguments.quote_history,
            bar_history_path=arguments.bar_history,
            calendar_path=arguments.calendar,
            information_path=arguments.information,
            receipt_path=arguments.receipt_output,
            state_path=arguments.state,
        )
    except (ContractError, OSError, ValueError) as error:
        print(
            "hepta_strategy_shadow_runner: FAIL " + str(error),
            file=sys.stderr,
        )
        return 78
    print(
        "hepta_strategy_shadow_runner: PASS "
        f"decision={result['receipt']['decision']} "
        f"iteration={result['state']['completed_iterations']} "
        f"idempotent={str(result['idempotent']).lower()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
