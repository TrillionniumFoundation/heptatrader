#!/usr/bin/env python3

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
from unittest import mock
import subprocess
import sys
import tempfile
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import hepta_shadow_market_history as history  # noqa: E402
import hepta_shadow_market_history_tests as history_fixtures  # noqa: E402
import hepta_strategy_replay_evaluator as replay  # noqa: E402


ENTRY_AT_MS = 1_800_000_000_000
CADENCE_MS = 10_000
JITTER_MS = 1_000
DIGEST_1 = "sha256:" + "1" * 64
DIGEST_2 = "sha256:" + "2" * 64


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ) + "\n"
    ).encode("ascii")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def file_digest(contents: bytes) -> str:
    return "sha256:" + hashlib.sha256(contents).hexdigest()


def write(path: Path, value: Any) -> None:
    path.write_bytes(canonical(value))


def make_policy(*, iterations: int) -> dict[str, Any]:
    body = {
        "schema": "hepta.strategy-shadow-observation-policy.v1",
        "version": 1,
        "campaign_id": "replay-promotion-test",
        "campaign_sha256": DIGEST_1,
        "strategy_id": "eurusd-confirmed-momentum-shadow",
        "strategy_version": "2.0.0",
        "strategy_sha256": DIGEST_2,
        "valid_after_ms": ENTRY_AT_MS + 1_000,
        "expires_at_ms": ENTRY_AT_MS + iterations * 120_000 + 60_000,
        "slot_interval_ms": 120_000,
        "maximum_iterations": iterations,
        "maximum_lateness_ms": 2_000,
        "shadow_only": True,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    campaign = {
        "schema": "hepta.strategy-shadow-observation-campaign.v1",
        "campaign_id": body["campaign_id"],
        "valid_after_ms": body["valid_after_ms"],
        "expires_at_ms": body["expires_at_ms"],
        "slot_interval_ms": body["slot_interval_ms"],
        "maximum_iterations": body["maximum_iterations"],
        "maximum_lateness_ms": body["maximum_lateness_ms"],
        "shadow_only": True,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    body["campaign_sha256"] = digest(campaign)
    return {**body, "body_sha256": digest(body)}


def make_intent(
    policy: dict[str, Any],
    *,
    index: int,
    side: str = "BUY",
) -> dict[str, Any]:
    observed_at_ms = ENTRY_AT_MS + index * 120_000
    bid = 1.1000
    ask = 1.1002
    return {
        "schema": "hepta.trade-intent.v1",
        "paper_only": True,
        "strategy_id": policy["strategy_id"],
        "strategy_version": policy["strategy_version"],
        "strategy_sha256": policy["strategy_sha256"],
        "intent_id": f"shadow-intent-replay-test-{index + 1:04d}",
        "instrument": "EUR.USD",
        "symbol": "EUR",
        "currency": "USD",
        "sec_type": "CASH",
        "exchange": "IDEALPRO",
        "side": side,
        "quantity": 1,
        "order_type": "LMT",
        "limit_price": ask if side == "BUY" else bid,
        "tif": "DAY",
        "observed_bid": bid,
        "observed_ask": ask,
        "observed_at_ms": observed_at_ms,
        "expires_at_ms": observed_at_ms + 60_000,
        "entry_thesis": "canonical replay fixture",
        "invalidation_condition":
            "absolute max adverse move is replayed as a stop",
        "max_holding_ms": 1_800_000,
        "max_adverse_move": 0.001,
        "expected_slippage": 0.00002,
        "exit_plan": "stop, horizon, or maximum holding",
    }


def make_receipt(
    policy: dict[str, Any],
    policy_sha256: str,
    *,
    index: int,
    trade: bool,
    side: str = "BUY",
) -> dict[str, Any]:
    started_at_ms = policy["valid_after_ms"] + index * 120_000
    intent = make_intent(policy, index=index, side=side) if trade else None
    reason_codes = [] if trade else ["NO_VALID_SETUP"]
    return {
        "schema": "hepta.autonomous-paper-decision-receipt.v1",
        "campaign_id": policy["campaign_id"],
        "strategy_id": policy["strategy_id"],
        "strategy_version": policy["strategy_version"],
        "strategy_sha256": policy["strategy_sha256"],
        "decision_id": f"replay-test-decision-{index + 1:04d}",
        "cycle_id": (
            f"shadow-cycle-replay-test-{index + 1:04d}"
            if trade else None),
        "started_at_ms": started_at_ms,
        "finished_at_ms": started_at_ms,
        "paper_only": True,
        "live_authorized": False,
        "shadow_only": True,
        "information_packet_sha256":
            "sha256:" + format(index + 10, "064x"),
        "catalog_sha256": "sha256:" + "3" * 64,
        "descriptor_sha256": "sha256:" + "4" * 64,
        "preflight_sha256": None,
        "regime": "trend" if trade else "unknown",
        "setup_gates": ["MOMENTUM_CONFIRMED"] if trade else [],
        "risk_challenges": reason_codes,
        "evidence_refs": [
            policy_sha256,
            policy["campaign_sha256"],
            "sha256:" + format(index + 20, "064x"),
        ],
        "conflicts": [],
        "decision": "TRADE" if trade else "NO_TRADE",
        "reason_codes": reason_codes,
        "trade_intent": intent,
        "trade_intent_sha256": None if intent is None else digest(intent),
        "campaign_open_request_id": None,
        "campaign_close_request_id": None,
        "mutation_attempted": False,
        "direct_broker_access": False,
        "final_outcome": "SHADOW_TRADE" if trade else "NO_TRADE",
    }


def make_record(
    sequence: int,
    previous: str | None,
    *,
    price: Callable[[int], tuple[float, float]],
) -> dict[str, Any]:
    offset = (sequence - 1) * CADENCE_MS
    started = ENTRY_AT_MS + offset
    bid, ask = price(offset)
    descriptor = {
        tool: "sha256:" + format(index + 100, "064x")
        for index, tool in enumerate(history.READ_ORDER)
    }
    body = {
        "schema": "hepta.shadow-market-history-record.v3",
        "version": 3,
        "sequence": sequence,
        "cadence_ms": CADENCE_MS,
        "maximum_jitter_ms": JITTER_MS,
        "previous_record_sha256": previous,
        "snapshot_body_sha256":
            "sha256:" + format(sequence + 1_000, "064x"),
        "snapshot_file_sha256":
            "sha256:" + format(sequence + 2_000, "064x"),
        "domain_id": "paper-trust-domain",
        "agent_uid": 1001,
        "instrument": "EUR.USD",
        "catalog_sha256": "sha256:" + "3" * 64,
        "descriptor_sha256": descriptor,
        "execution_service_epoch": "paper-execution-epoch-1",
        "execution_service_fencing_generation": 7,
        "watch_generation": 1,
        "watch_lease_receipt_body_sha256": "sha256:" + "5" * 64,
        "watch_lease_receipt_file_sha256": "sha256:" + "6" * 64,
        "watch_lease_operation": "PROVISION",
        "watch_lease_previous_generation": None,
        "watch_lease_previous_receipt_body_sha256": None,
        "watch_lease_accepted_at_ms": ENTRY_AT_MS - 1_000,
        "watch_lease_ttl_seconds": 3_600,
        "watch_lease_expires_at_ms": ENTRY_AT_MS + 3_599_000,
        "watch_export_receipt_body_sha256": "sha256:" + "7" * 64,
        "watch_export_receipt_file_sha256": "sha256:" + "8" * 64,
        "watch_exported_at_ms": started + 6,
        "watch_export_reader_uid": 1002,
        "watch_export_reader_gid": 1002,
        "collection_started_at_ms": started,
        "collection_finished_at_ms": started + 5,
        "generated_at_ms": started + 5,
        "quote_read_finished_at_ms": started + 3,
        "quote_changed": True,
        "quote": {
            "source": "SIMULATOR",
            "authoritative": True,
            "stale": False,
            "bid": bid,
            "ask": ask,
            "observed_at_ms": started + 2,
            "stale_after_ms": started + 60_000,
        },
    }
    return {**body, "record_sha256": digest(body)}


def normal_price(offset: int) -> tuple[float, float]:
    if offset == 10_000:
        return 1.0999, 1.1001
    if offset == 20_000:
        return 1.0998, 1.1000
    if offset >= 40_000:
        return 1.1010, 1.1012
    return 1.1000, 1.1002


def no_fill_price(offset: int) -> tuple[float, float]:
    if offset == 0:
        return 1.1000, 1.1002
    return 1.1001, 1.1003


def stop_price(offset: int) -> tuple[float, float]:
    if offset == 10_000:
        return 1.0999, 1.1001
    if offset >= 20_000:
        return 1.0980, 1.0982
    return 1.1000, 1.1002


def make_records(
    *,
    price: Callable[[int], tuple[float, float]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    previous: str | None = None
    for sequence in range(1, 93):
        record = make_record(sequence, previous, price=price)
        records.append(record)
        previous = record["record_sha256"]
    return records


def audit_for(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "valid",
        "record_count": len(records),
        "history_head_sha256": records[-1]["record_sha256"],
        "history_record_bytes": 100_000,
        "history_index_bytes": 1_000,
        "history_storage_bytes": 101_000,
        "source_sha256": replay._history_source_digest(
            [record["record_sha256"] for record in records]),
        "directory_entries_scanned": len(records) + 1,
    }


def make_final_audit(
    policy: dict[str, Any],
    policy_sha256: str,
    segment_audit: dict[str, Any],
) -> dict[str, Any]:
    body = {
        "schema": "hepta.bounded-shadow-final-audit-receipt.v2",
        "version": 2,
        "campaign_id": policy["campaign_id"],
        "campaign_sha256": policy["campaign_sha256"],
        "policy_sha256": policy_sha256,
        "strategy_id": policy["strategy_id"],
        "strategy_version": policy["strategy_version"],
        "strategy_sha256": policy["strategy_sha256"],
        "completed_iterations": policy["maximum_iterations"],
        "maximum_iterations": policy["maximum_iterations"],
        "finalized_at_ms": ENTRY_AT_MS + 920_000,
        "segment_count": 1,
        "segments": [{
            "segment_index": 1,
            "record_count": segment_audit["record_count"],
            "history_head_sha256":
                segment_audit["history_head_sha256"],
            "source_sha256": segment_audit["source_sha256"],
            "history_record_bytes":
                segment_audit["history_record_bytes"],
            "history_index_bytes":
                segment_audit["history_index_bytes"],
            "history_storage_bytes":
                segment_audit["history_storage_bytes"],
            "audit_sha256": digest(segment_audit),
        }],
        "sample_count": segment_audit["record_count"],
        "missed_sample_count": 0,
        "missed_decision_count": 0,
        "payload_bytes_before_final_receipt": 250_000,
        "payload_files_before_final_receipt": 20,
        "payload_accumulator_before_final_receipt":
            "sha256:" + "9" * 64,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    return {**body, "body_sha256": digest(body)}


class Evidence:
    def __init__(
        self,
        root: Path,
        *,
        price: Callable[[int], tuple[float, float]] = normal_price,
        second_trade: bool = False,
    ) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.policy_path = root / "policy.json"
        self.audit_path = root / "final-audit.json"
        self.history_path = root / "history"
        self.history_path.mkdir()
        self.policy = make_policy(iterations=2)
        write(self.policy_path, self.policy)
        self.policy_sha256 = file_digest(self.policy_path.read_bytes())
        self.receipts = [
            make_receipt(
                self.policy, self.policy_sha256, index=0, trade=True),
            make_receipt(
                self.policy,
                self.policy_sha256,
                index=1,
                trade=second_trade,
                side="SELL",
            ),
        ]
        self.receipt_paths: list[Path] = []
        for index, receipt in enumerate(self.receipts, start=1):
            path = root / f"receipt-{index:04d}.json"
            write(path, receipt)
            self.receipt_paths.append(path)
        self.records = make_records(price=price)
        self.segment_audit = audit_for(self.records)
        self.final_audit = make_final_audit(
            self.policy, self.policy_sha256, self.segment_audit)
        write(self.audit_path, self.final_audit)
        self.decisions = replay.seal_decision_set(
            self.policy_path, self.audit_path, self.receipt_paths)
        with (
                mock.patch.object(
                    replay.market_history,
                    "audit_history",
                    return_value=self.segment_audit),
                mock.patch.object(
                    replay.market_history,
                    "load_history",
                    return_value=self.records)):
            self.marks = replay.seal_mark_set(
                self.policy_path,
                self.audit_path,
                [self.history_path],
                cadence_ms=CADENCE_MS,
                maximum_jitter_ms=JITTER_MS,
            )


def expect_failure(
    function: Callable[[], Any],
    reason: str,
) -> None:
    try:
        function()
    except replay.ContractError as error:
        assert str(error) == reason, (str(error), reason)
    else:
        raise AssertionError(f"expected {reason}")


def evaluate(
    evidence: Evidence,
    **overrides: Any,
) -> dict[str, Any]:
    arguments = {
        "horizon_seconds": 30,
        "round_trip_cost_bps": 0.2,
        "maximum_exit_delay_seconds": 10,
        "entry_latency_ms": 1_000,
        "entry_slippage_bps": 0.2,
        "exit_slippage_bps": 0.2,
    }
    arguments.update(overrides)
    return replay.evaluate_replay(
        evidence.decisions, evidence.marks, **arguments)


def build_real_history(directory: Path) -> tuple[Path, dict[str, Any]]:
    history_directory = directory / "history"
    snapshot_path = directory / "snapshot.json"
    lease_path = directory / "lease.json"
    export_path = directory / "export.json"
    lease = history_fixtures.lease_receipt(
        1, ENTRY_AT_MS - 1_000)
    history_fixtures.write_receipt(lease_path, lease)
    for sequence in range(1, 93):
        offset = (sequence - 1) * CADENCE_MS
        started_at_ms = ENTRY_AT_MS + offset
        snapshot = history_fixtures.snapshot(started_at_ms)
        bid, ask = normal_price(offset)
        snapshot["reads"]["market.get_quote"]["bid"] = bid
        snapshot["reads"]["market.get_quote"]["ask"] = ask
        snapshot_body = dict(snapshot)
        snapshot_body.pop("body_sha256")
        snapshot["body_sha256"] = digest(snapshot_body)
        history_fixtures.write_receipt(snapshot_path, snapshot)
        export = history_fixtures.export_receipt(snapshot, lease)
        history_fixtures.write_receipt(export_path, export)
        history.append_snapshot(
            history_directory,
            snapshot_path,
            cadence_ms=CADENCE_MS,
            maximum_jitter_ms=JITTER_MS,
            watch_lease_receipt_path=lease_path,
            watch_export_receipt_path=export_path,
            minimum_free_bytes=0,
        )
    return history_directory, history.audit_history(
        history_directory,
        cadence_ms=CADENCE_MS,
        maximum_jitter_ms=JITTER_MS,
    )


def test_real_history_seal_audit_load_and_replay(root: Path) -> None:
    real_root = root / "real-history-pipeline"
    real_root.mkdir()
    policy_path = real_root / "policy.json"
    audit_path = real_root / "final-audit.json"
    policy = make_policy(iterations=2)
    write(policy_path, policy)
    policy_sha256 = file_digest(policy_path.read_bytes())
    receipts = [
        make_receipt(policy, policy_sha256, index=0, trade=True),
        make_receipt(policy, policy_sha256, index=1, trade=False),
    ]
    receipt_paths: list[Path] = []
    for index, receipt in enumerate(receipts, start=1):
        path = real_root / f"receipt-{index:04d}.json"
        write(path, receipt)
        receipt_paths.append(path)

    original_trust_uid = history.ROOT_TRUST_UID
    history.ROOT_TRUST_UID = os.geteuid()
    try:
        history_directory, segment_audit = build_real_history(real_root)
        final_audit = make_final_audit(
            policy, policy_sha256, segment_audit)
        write(audit_path, final_audit)
        decisions = replay.seal_decision_set(
            policy_path, audit_path, receipt_paths)
        marks = replay.seal_mark_set(
            policy_path,
            audit_path,
            [history_directory],
            cadence_ms=CADENCE_MS,
            maximum_jitter_ms=JITTER_MS,
        )
        assert marks["record_count"] == 92
        assert all(
            mark["record"]["quote"]["source"] == "SIMULATOR"
            for mark in marks["marks"])
        report = replay.evaluate_replay(
            decisions,
            marks,
            horizon_seconds=30,
            round_trip_cost_bps=0.2,
            maximum_exit_delay_seconds=10,
            entry_latency_ms=1_000,
            entry_slippage_bps=0.2,
            exit_slippage_bps=0.2,
        )
        assert report["schema"] == "hepta.strategy-replay-report.v3"
        assert report["filled_count"] == 1

        drift_directory = real_root / "history-source-drift"
        shutil.copytree(history_directory, drift_directory)
        first_path = (
            drift_directory / "record-00000000000000000001.json")
        drifted = json.loads(first_path.read_text(encoding="ascii"))
        drifted["quote"]["source"] = "IB"
        drifted_body = dict(drifted)
        drifted_body.pop("record_sha256")
        drifted["record_sha256"] = digest(drifted_body)
        write(first_path, drifted)
        try:
            replay.seal_mark_set(
                policy_path,
                audit_path,
                [drift_directory],
                cadence_ms=CADENCE_MS,
                maximum_jitter_ms=JITTER_MS,
            )
        except replay.ContractError as error:
            assert str(error) == "REPLAY_MARK_HISTORY_AUDIT_FAILED"
            assert isinstance(error.__cause__, history.HistoryError)
            assert str(error.__cause__) == (
                "MARKET_HISTORY_QUOTE_NOT_AUTHORITATIVE")
        else:
            raise AssertionError("IB source drift was accepted")
    finally:
        history.ROOT_TRUST_UID = original_trust_uid


def test_resolved_conservative_fill(root: Path) -> Evidence:
    evidence = Evidence(root / "resolved")
    report = evaluate(evidence)
    result = report["results"][0]
    assert report["schema"] == "hepta.strategy-replay-report.v3"
    assert result["status"] == "CLOSED"
    assert result["entry_at_ms"] == ENTRY_AT_MS + 10_002
    assert abs(result["entry_price"] - 1.100122002) < 1e-12
    assert result["entry_price"] <= result["limit_price"]
    assert result["exit_at_ms"] == ENTRY_AT_MS + 40_002
    assert result["exit_price"] < result["exit_touch_price"]
    assert result["exit_reason"] == "HORIZON"
    assert result["mae_bps"] < 0.0
    assert result["mfe_bps"] > 0.0
    assert report["filled_count"] == 1
    assert report["resolved_count"] == 1
    assert report["unresolved_count"] == 0
    assert (
        report["execution_model"]["entry_model"] ==
        "FIRST_POST_LATENCY_EXECUTABLE_LIMIT_WITH_ADVERSE_SLIPPAGE")
    return evidence


def test_non_fill_and_stop_and_maximum_holding(root: Path) -> None:
    non_fill = Evidence(root / "non-fill", price=no_fill_price)
    non_fill_report = evaluate(non_fill)
    result = non_fill_report["results"][0]
    assert result["status"] == "NOT_FILLED_EXPIRED"
    assert result["filled"] is False
    assert result["evidence_complete"] is True
    assert non_fill_report["expired_unfilled_count"] == 1

    stopped = Evidence(root / "stop", price=stop_price)
    stopped_result = evaluate(stopped)["results"][0]
    assert stopped_result["exit_reason"] == "STOP_INVALIDATION"
    assert stopped_result["exit_at_ms"] == ENTRY_AT_MS + 20_002
    assert stopped_result["net_return_bps"] < 0.0

    held = Evidence(root / "holding")
    held_result = evaluate(
        held,
        horizon_seconds=300,
        maximum_holding_seconds=15,
    )["results"][0]
    assert held_result["exit_reason"] == "MAXIMUM_HOLDING"
    assert held_result["exit_at_ms"] <= held_result["maximum_holding_at_ms"]
    assert held_result["holding_ms"] <= 15_000


def test_overlap_policy(root: Path) -> None:
    evidence = Evidence(root / "overlap", second_trade=True)
    expect_failure(
        lambda: evaluate(evidence),
        "REPLAY_OVERLAPPING_INTENTS_REJECTED:"
        "replay-test-decision-0001:replay-test-decision-0002",
    )
    report = evaluate(evidence, allow_overlapping_intents=True)
    assert (
        report["execution_model"]["allow_overlapping_intents"] is True)
    assert report["trade_candidate_count"] == 2


def test_fail_closed_seals(root: Path, evidence: Evidence) -> None:
    legacy_decisions = {
        "schema": "hepta.strategy-decision-set.v1",
    }
    expect_failure(
        lambda: replay.evaluate_replay(
            legacy_decisions,
            evidence.marks,
            horizon_seconds=30,
            round_trip_cost_bps=0.2,
        ),
        "REPLAY_DECISION_SET_FIELDS_INVALID",
    )

    for label, field, value, reason in (
            (
                "missed-sample",
                "missed_sample_count",
                1,
                "REPLAY_FINAL_AUDIT_MISSED_COUNT_NONZERO",
            ),
            (
                "sample-drift",
                "sample_count",
                evidence.final_audit["sample_count"] + 1,
                "REPLAY_FINAL_AUDIT_SAMPLE_COUNT_DRIFT",
            )):
        changed_audit = deepcopy(evidence.final_audit)
        changed_audit[field] = value
        changed_body = dict(changed_audit)
        changed_body.pop("body_sha256")
        changed_audit["body_sha256"] = digest(changed_body)
        changed_path = root / f"{label}-final-audit.json"
        write(changed_path, changed_audit)
        expect_failure(
            lambda changed_path=changed_path:
                replay.seal_decision_set(
                    evidence.policy_path,
                    changed_path,
                    evidence.receipt_paths,
                ),
            reason,
        )

    changed = deepcopy(evidence.decisions)
    changed["receipts"][0]["receipt"]["decision_id"] = "forged-decision"
    body = dict(changed)
    body.pop("body_sha256")
    changed["body_sha256"] = digest(body)
    expect_failure(
        lambda: evaluate(
            EvidenceProxy(changed, evidence.marks)),
        "REPLAY_RECEIPT_DIGEST_MISMATCH",
    )

    changed_marks = deepcopy(evidence.marks)
    changed_marks["marks"][1]["ask"] = 1.0
    top = dict(changed_marks)
    top.pop("body_sha256")
    changed_marks["body_sha256"] = digest(top)
    expect_failure(
        lambda: evaluate(
            EvidenceProxy(evidence.decisions, changed_marks)),
        "REPLAY_MARK_BODY_DIGEST_MISMATCH",
    )

    changed_marks = deepcopy(evidence.marks)
    changed_marks["marks"][1]["ask"] = 1.0
    mark_body = dict(changed_marks["marks"][1])
    mark_body.pop("mark_body_sha256")
    changed_marks["marks"][1]["mark_body_sha256"] = digest(mark_body)
    top = dict(changed_marks)
    top.pop("body_sha256")
    changed_marks["body_sha256"] = digest(top)
    expect_failure(
        lambda: evaluate(
            EvidenceProxy(evidence.decisions, changed_marks)),
        "REPLAY_MARK_RECORD_BINDING_INVALID",
    )

    changed_marks = deepcopy(evidence.marks)
    first_mark = changed_marks["marks"][0]
    first_record = first_mark["record"]
    first_record["quote_changed"] = False
    record_body = dict(first_record)
    record_body.pop("record_sha256")
    first_record["record_sha256"] = digest(record_body)
    first_mark["quote_changed"] = False
    first_mark["record_body_sha256"] = first_record["record_sha256"]
    mark_body = dict(first_mark)
    mark_body.pop("mark_body_sha256")
    first_mark["mark_body_sha256"] = digest(mark_body)
    top = dict(changed_marks)
    top.pop("body_sha256")
    changed_marks["body_sha256"] = digest(top)
    expect_failure(
        lambda: evaluate(
            EvidenceProxy(evidence.decisions, changed_marks)),
        "REPLAY_MARK_QUOTE_CHANGE_FLAG_INVALID",
    )

    with (
            mock.patch.object(
                replay.market_history,
                "audit_history",
                return_value={
                    **evidence.segment_audit, "record_count": 91}),
            mock.patch.object(
                replay.market_history,
                "load_history",
                return_value=evidence.records)):
        expect_failure(
            lambda: replay.seal_mark_set(
                evidence.policy_path,
                evidence.audit_path,
                [evidence.history_path],
                cadence_ms=CADENCE_MS,
                maximum_jitter_ms=JITTER_MS,
            ),
            "REPLAY_MARK_HISTORY_AUDIT_BINDING_INVALID",
        )

    legacy_records = deepcopy(evidence.records)
    legacy_body = dict(legacy_records[0])
    legacy_body.pop("record_sha256")
    legacy_body.pop("quote_changed")
    legacy_body["schema"] = "hepta.shadow-market-history-record.v2"
    legacy_body["version"] = 2
    legacy_records[0] = {
        **legacy_body, "record_sha256": digest(legacy_body)}
    with (
            mock.patch.object(
                replay.market_history,
                "audit_history",
                return_value=evidence.segment_audit),
            mock.patch.object(
                replay.market_history,
                "load_history",
                return_value=legacy_records)):
        expect_failure(
            lambda: replay.seal_mark_set(
                evidence.policy_path,
                evidence.audit_path,
                [evidence.history_path],
                cadence_ms=CADENCE_MS,
                maximum_jitter_ms=JITTER_MS,
            ),
            "REPLAY_MARK_RECORD_FIELDS_INVALID",
        )

    noncanonical = root / "noncanonical-receipt.json"
    noncanonical.write_text(
        json.dumps(evidence.receipts[0], indent=2) + "\n",
        encoding="ascii",
    )
    expect_failure(
        lambda: replay.seal_decision_set(
            evidence.policy_path,
            evidence.audit_path,
            [noncanonical, evidence.receipt_paths[1]],
        ),
        "REPLAY_DECISION_RECEIPT_CANONICAL_INVALID",
    )


class EvidenceProxy:
    def __init__(
        self,
        decisions: dict[str, Any],
        marks: dict[str, Any],
    ) -> None:
        self.decisions = decisions
        self.marks = marks


def test_cli(root: Path, evidence: Evidence) -> None:
    decisions_path = root / "sealed-decisions.json"
    marks_path = root / "sealed-marks.json"
    report_path = root / "report.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/hepta_strategy_replay_evaluator.py"),
            "seal-decisions",
            "--policy", str(evidence.policy_path),
            "--final-audit", str(evidence.audit_path),
            "--receipt", str(evidence.receipt_paths[1]),
            "--receipt", str(evidence.receipt_paths[0]),
            "--output", str(decisions_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(decisions_path.read_text()) == evidence.decisions
    write(marks_path, evidence.marks)
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/hepta_strategy_replay_evaluator.py"),
            "--decisions", str(decisions_path),
            "--marks", str(marks_path),
            "--horizon-seconds", "30",
            "--round-trip-cost-bps", "0.2",
            "--maximum-exit-delay-seconds", "10",
            "--entry-latency-ms", "1000",
            "--entry-slippage-bps", "0.2",
            "--exit-slippage-bps", "0.2",
            "--output", str(report_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(report_path.read_text()) == evaluate(evidence)


def main() -> int:
    with tempfile.TemporaryDirectory(
            prefix="hepta-strategy-replay-evaluator-") as temporary:
        root = Path(temporary)
        evidence = test_resolved_conservative_fill(root)
        test_non_fill_and_stop_and_maximum_holding(root)
        test_overlap_policy(root)
        test_fail_closed_seals(root, evidence)
        test_real_history_seal_audit_load_and_replay(root)
        test_cli(root, evidence)
    print("hepta_strategy_replay_evaluator_tests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
