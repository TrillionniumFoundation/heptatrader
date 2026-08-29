#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any
from unittest import mock


def canonical(document: Any) -> bytes:
    return (
        json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")


def write_document(path: Path, document: Any) -> None:
    path.write_bytes(canonical(document))


def digest(contents: bytes) -> str:
    return "sha256:" + hashlib.sha256(contents).hexdigest()


def reseal(document: dict[str, Any]) -> dict[str, Any]:
    body = dict(document)
    body.pop("body_sha256", None)
    return {**body, "body_sha256": digest(canonical(body))}


def replay_policy(
    *,
    campaign_id: str,
    strategy_id: str,
    strategy_version: str,
    strategy_sha256: str,
    valid_after_ms: int,
    maximum_iterations: int,
) -> dict[str, Any]:
    body = {
        "schema": "hepta.strategy-shadow-observation-policy.v1",
        "version": 1,
        "campaign_id": campaign_id,
        "campaign_sha256": "sha256:" + "0" * 64,
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "strategy_sha256": strategy_sha256,
        "valid_after_ms": valid_after_ms,
        "expires_at_ms":
            valid_after_ms + maximum_iterations * 120_000 + 60_000,
        "slot_interval_ms": 120_000,
        "maximum_iterations": maximum_iterations,
        "maximum_lateness_ms": 2_000,
        "shadow_only": True,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    campaign = {
        "schema": "hepta.strategy-shadow-observation-campaign.v1",
        "campaign_id": campaign_id,
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
    body["campaign_sha256"] = digest(canonical(campaign))
    return {**body, "body_sha256": digest(canonical(body))}


def replay_receipt(
    *,
    decision: dict[str, Any],
    packet: dict[str, Any],
    source_snapshot: dict[str, Any],
    policy: dict[str, Any],
    policy_sha256: str,
) -> dict[str, Any]:
    intent = decision["trade_intent"]
    evidence_refs = list(dict.fromkeys([
        policy_sha256,
        policy["campaign_sha256"],
        *decision["evidence_refs"],
    ]))
    return {
        "schema": "hepta.autonomous-paper-decision-receipt.v1",
        "campaign_id": policy["campaign_id"],
        "strategy_id": policy["strategy_id"],
        "strategy_version": policy["strategy_version"],
        "strategy_sha256": policy["strategy_sha256"],
        "decision_id": decision["decision_id"],
        "cycle_id": decision["cycle_id"],
        "started_at_ms": decision["started_at_ms"],
        "finished_at_ms": decision["finished_at_ms"],
        "paper_only": True,
        "live_authorized": False,
        "shadow_only": True,
        "information_packet_sha256": digest(canonical(packet)),
        "catalog_sha256": source_snapshot["catalog_sha256"],
        "descriptor_sha256":
            digest(canonical(source_snapshot["descriptor_sha256"])),
        "preflight_sha256": None,
        "regime": decision["regime"],
        "setup_gates": list(decision["setup_gates"]),
        "risk_challenges": list(decision["risk_challenges"]),
        "evidence_refs": evidence_refs,
        "conflicts": list(decision["conflicts"]),
        "decision": decision["decision"],
        "reason_codes": list(decision["reason_codes"]),
        "trade_intent": intent,
        "trade_intent_sha256": (
            None if intent is None else digest(canonical(intent))),
        "campaign_open_request_id": None,
        "campaign_close_request_id": None,
        "mutation_attempted": False,
        "direct_broker_access": False,
        "final_outcome": decision["final_outcome"],
    }


def snapshot(
    evaluated_at_ms: int,
    bid: float,
    ask: float,
    *,
    version: int = 2,
    active_order_ids: list[int] | None = None,
) -> dict[str, Any]:
    reads = {
        "account.get_summary": {
            "authoritative": True,
            "account_complete": True,
        },
        "portfolio.list_positions": {
            "authoritative": True,
            "positions": [],
        },
        "orders.list": {
            "authoritative": True,
            "active_order_ids": (
                [] if active_order_ids is None else active_order_ids),
        },
        "risk.get_limits": {
            "authoritative": True,
            "gross_absolute_position": 0,
        },
        "market.get_quote": {
            "source": "HEPTA_IB_PAPER",
            "authoritative": True,
            "stale": False,
            "instrument": "EUR.USD",
            "bid": bid,
            "ask": ask,
            "observed_at_ms": evaluated_at_ms - 1000,
            "stale_after_ms": evaluated_at_ms + 5000,
        },
        "system.get_health": {
            "gateway_ready": True,
            "remote_execution": True,
            "remote_execution_configured": True,
            "remote_execution_ready": True,
            "execution_mode": "SIMULATOR",
            "execution_service_epoch": "epoch-replay-1",
            "execution_service_fencing_generation": 7,
            "remote_execution_reason": "",
        },
    }
    body: dict[str, Any] = {
        "schema": f"hepta.shadow-watch-snapshot.v{version}",
        "version": version,
        "domain_id": "alpha",
        "agent_uid": 2104,
        "generated_at_ms": evaluated_at_ms - 500,
        "instrument": "EUR.USD",
        "catalog_sha256": "sha256:" + "1" * 64,
        "descriptor_sha256": {
            tool: "sha256:" + format(index + 2, "064x")
            for index, tool in enumerate(reads)
        },
        "reads": reads,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    if version == 2:
        read_order = (
            "account.get_summary",
            "portfolio.list_positions",
            "orders.list",
            "risk.get_limits",
            "market.get_quote",
            "system.get_health",
        )
        body.update({
            "collection_started_at_ms": evaluated_at_ms - 900,
            "collection_finished_at_ms": evaluated_at_ms - 550,
            "read_finished_at_ms": {
                tool: evaluated_at_ms - 850 + index * 50
                for index, tool in enumerate(read_order)
            },
        })
    return {
        **body,
        "body_sha256": digest(canonical(body)),
    }


def quote_history(
    evaluated_at_ms: int,
    mids: list[float],
    source_snapshot: dict[str, Any],
) -> dict[str, Any]:
    latest_at_ms = evaluated_at_ms - 1000
    start = latest_at_ms - 5_400_000
    interval = 10_000
    sample_count = 5_400_000 // interval + 1
    quotes = []
    for index in range(sample_count):
        position = index * (len(mids) - 1) / (sample_count - 1)
        left = int(position)
        right = min(left + 1, len(mids) - 1)
        fraction = position - left
        mid = mids[left] + (mids[right] - mids[left]) * fraction
        observed_at_ms = start + interval * index
        quotes.append({
            "bid": round(mid - 0.00002, 8),
            "ask": round(mid + 0.00002, 8),
            "observed_at_ms": observed_at_ms,
            "stale_after_ms": observed_at_ms + 5000,
            "authoritative": True,
            "stale": False,
            "source_snapshot_body_sha256": (
                source_snapshot["body_sha256"]
                if index == sample_count - 1 else
                "sha256:" + format(index + 1000, "064x")
            ),
            "catalog_sha256": source_snapshot["catalog_sha256"],
            "descriptor_sha256":
                source_snapshot["descriptor_sha256"]["market.get_quote"],
            "execution_service_epoch": "epoch-replay-1",
            "execution_service_fencing_generation": 7,
            "watch_generation": 1 + index // 300,
        })
    body = {
        "schema": "hepta.authoritative-quote-history.v2",
        "version": 2,
        "instrument": "EUR.USD",
        "provider": "HEPTA_IB_PAPER",
        "source_ref": "fixture:quotes",
        "observed_at_ms": latest_at_ms,
        "window_started_at_ms": start,
        "window_finished_at_ms": latest_at_ms,
        "cadence_ms": interval,
        "maximum_gap_ms": 15_000,
        "complete": True,
        "quotes": quotes,
    }
    return {**body, "body_sha256": digest(canonical(body))}


def bar_history(
    evaluated_at_ms: int,
    *,
    ascending: bool,
) -> dict[str, Any]:
    bars = []
    base = evaluated_at_ms - 60 * 300_000
    start_price = 1.092 if ascending else 1.108
    direction = 1.0 if ascending else -1.0
    for index in range(60):
        started_at_ms = base + index * 300_000
        open_price = start_price + direction * index * 0.00015
        close_price = open_price + direction * 0.00010
        bars.append({
            "started_at_ms": started_at_ms,
            "finished_at_ms": started_at_ms + 299_999,
            "open": round(open_price, 8),
            "high": round(max(open_price, close_price) + 0.00010, 8),
            "low": round(min(open_price, close_price) - 0.00010, 8),
            "close": round(close_price, 8),
        })
    source_content_sha256 = digest(b"fixture-bar-source-v2")
    for bar in bars:
        bar.update({
            "sample_count": 30,
            "complete": True,
            "source_content_sha256": source_content_sha256,
        })
    body = {
        "schema": "hepta.authoritative-bar-history.v2",
        "version": 2,
        "instrument": "EUR.USD",
        "provider": "HEPTA_REPLAY",
        "source_ref": "fixture:bars",
        "source_content_sha256": source_content_sha256,
        "observed_at_ms": evaluated_at_ms - 1,
        "interval_ms": 300_000,
        "window_started_at_ms": bars[0]["started_at_ms"],
        "window_finished_at_ms": bars[-1]["finished_at_ms"],
        "expected_bar_count": len(bars),
        "complete": True,
        "bars": bars,
    }
    return {**body, "body_sha256": digest(canonical(body))}


def calendar(
    evaluated_at_ms: int,
    *,
    event_window: bool,
    version: int = 2,
    event_deltas_ms: list[int] | None = None,
) -> dict[str, Any]:
    source_content_sha256 = digest(b"fixture-calendar-usd-source-v2")
    eur_source_sha256 = digest(b"fixture-calendar-eur-source-v2")
    deltas = (
        event_deltas_ms
        if event_deltas_ms is not None else
        ([600_000] if event_window else [])
    )
    events = []
    for index, delta_ms in enumerate(deltas, start=1):
        events.append({
            "event_id": f"usd-high-impact-{index}",
            "currencies": ["USD"],
            "importance": "high",
            "scheduled_at_ms": evaluated_at_ms + delta_ms,
            "title_sha256": digest(
                f"USD high impact fixture {index}".encode("ascii")),
            **({
                "source_content_sha256": source_content_sha256,
            } if version == 2 else {}),
        })
    if version == 1:
        return {
            "schema": "hepta.economic-calendar.v1",
            "provider": "FIXTURE_CALENDAR",
            "source_ref": "fixture:calendar",
            "observed_at_ms": evaluated_at_ms - 500,
            "events": events,
        }
    body = {
        "schema": "hepta.economic-calendar.v2",
        "provider": "FIXTURE_CALENDAR",
        "source_ref": "fixture:calendar",
        "observed_at_ms": evaluated_at_ms - 500,
        "sources": [
            {
                "provider": "FEDERAL_RESERVE",
                "source_ref":
                    "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
                "retrieved_at_ms": evaluated_at_ms - 500,
                "published_at_ms": evaluated_at_ms - 1000,
                "revision": "revision-1",
                "content_sha256": source_content_sha256,
                "coverage_start_ms": evaluated_at_ms - 86_400_000,
                "coverage_end_ms": evaluated_at_ms + 86_400_000,
                "currencies": ["USD"],
            },
            {
                "provider": "ECB",
                "source_ref":
                    "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html",
                "retrieved_at_ms": evaluated_at_ms - 500,
                "published_at_ms": evaluated_at_ms - 1000,
                "revision": "revision-1",
                "content_sha256": eur_source_sha256,
                "coverage_start_ms": evaluated_at_ms - 86_400_000,
                "coverage_end_ms": evaluated_at_ms + 86_400_000,
                "currencies": ["EUR"],
            },
        ],
        "events": events,
    }
    return {**body, "body_sha256": digest(canonical(body))}


def information(
    evaluated_at_ms: int,
    *,
    version: int = 2,
) -> dict[str, Any]:
    if version == 1:
        return {
            "schema": "hepta.market-information-items.v1",
            "provider": "FIXTURE_INFORMATION",
            "source_ref": "fixture:information",
            "observed_at_ms": evaluated_at_ms - 500,
            "items": [],
        }
    source_content_sha256 = digest(b"fixture-information-usd-source-v2")
    eur_source_sha256 = digest(b"fixture-information-eur-source-v2")
    body = {
        "schema": "hepta.market-information-items.v2",
        "provider": "FIXTURE_INFORMATION",
        "source_ref": "fixture:information",
        "observed_at_ms": evaluated_at_ms - 500,
        "sources": [
            {
                "provider": "FEDERAL_RESERVE",
                "source_ref":
                    "https://www.federalreserve.gov/newsevents.htm",
                "retrieved_at_ms": evaluated_at_ms - 500,
                "published_at_ms": None,
                "revision": "revision-1",
                "content_sha256": source_content_sha256,
                "coverage_start_ms": evaluated_at_ms - 900_000,
                "coverage_end_ms": evaluated_at_ms + 900_000,
                "currencies": ["USD"],
            },
            {
                "provider": "ECB",
                "source_ref": "https://www.ecb.europa.eu/press/html/index.en.html",
                "retrieved_at_ms": evaluated_at_ms - 500,
                "published_at_ms": None,
                "revision": "revision-1",
                "content_sha256": eur_source_sha256,
                "coverage_start_ms": evaluated_at_ms - 900_000,
                "coverage_end_ms": evaluated_at_ms + 900_000,
                "currencies": ["EUR"],
            },
        ],
        "items": [],
    }
    return {**body, "body_sha256": digest(canonical(body))}


def attested_evidence(
    directory: Path,
    normalizer: Any,
    *,
    name: str,
    evaluated_at_ms: int,
    event_window: bool,
    event_deltas_ms: list[int] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    receipt_directory = directory / f"{name}-trusted-evidence"
    receipt_directory.mkdir(mode=0o700, exist_ok=True)
    fed_payload = receipt_directory / "fed.html"
    ecb_payload = receipt_directory / "ecb.html"
    for payload in (fed_payload, ecb_payload):
        if payload.exists():
            payload.chmod(0o600)
    fed_payload.write_bytes(
        f"<html>official FED fixture {name}</html>".encode("ascii"))
    ecb_payload.write_bytes(
        f"<html>official ECB fixture {name}</html>".encode("ascii"))
    fed_payload.chmod(0o400)
    ecb_payload.chmod(0o400)
    fed_digest = digest(fed_payload.read_bytes())
    ecb_digest = digest(ecb_payload.read_bytes())
    deltas = (
        event_deltas_ms
        if event_deltas_ms is not None else
        ([600_000] if event_window else [])
    )
    events: list[dict[str, Any]] = []
    for index, delta_ms in enumerate(deltas, start=1):
        events.append({
            "event_id": f"usd-high-impact-{index}",
            "currencies": ["USD"],
            "importance": "high",
            "scheduled_at_ms": evaluated_at_ms + delta_ms,
            "title_sha256": digest(
                f"USD high impact fixture {index}".encode("ascii")),
            "source_content_sha256": fed_digest,
        })
    items: list[dict[str, Any]] = []
    extractor_code_sha256 = digest(b"fixture deterministic extractor")
    receipt_body = {
        "schema": "hepta.market-source-extraction-receipt.v1",
        "version": 1,
        "observed_at_ms": evaluated_at_ms - 500,
        "extractor": {
            "extractor_id": "HEPTA_TEST_OFFICIAL_EXTRACTOR",
            "extractor_version": "1.0.0",
            "extractor_code_sha256": extractor_code_sha256,
            "deterministic": True,
        },
        "sources": [
            {
                "provider": "FEDERAL_RESERVE",
                "requested_url":
                    "https://www.federalreserve.gov/newsevents.htm",
                "final_url":
                    "https://www.federalreserve.gov/newsevents.htm",
                "http_status": 200,
                "content_type": "text/html",
                "fetch_started_at_ms": evaluated_at_ms - 700,
                "fetched_at_ms": evaluated_at_ms - 500,
                "published_at_ms": None,
                "revision": "fixture-fed-1",
                "payload_path": fed_payload.name,
                "content_sha256": fed_digest,
            },
            {
                "provider": "ECB",
                "requested_url":
                    "https://www.ecb.europa.eu/press/html/index.en.html",
                "final_url":
                    "https://www.ecb.europa.eu/press/html/index.en.html",
                "http_status": 200,
                "content_type": "text/html",
                "fetch_started_at_ms": evaluated_at_ms - 700,
                "fetched_at_ms": evaluated_at_ms - 500,
                "published_at_ms": None,
                "revision": "fixture-ecb-1",
                "payload_path": ecb_payload.name,
                "content_sha256": ecb_digest,
            },
        ],
        "completeness": [
            {
                "source_content_sha256": fed_digest,
                "coverage_start_ms": evaluated_at_ms - 86_400_000,
                "coverage_end_ms": evaluated_at_ms + 86_400_000,
                "currencies": ["USD"],
                "complete": True,
                "derived_by_extractor": True,
                "rule_id": "fed-calendar-and-information",
                "rule_version": "1.0.0",
            },
            {
                "source_content_sha256": ecb_digest,
                "coverage_start_ms": evaluated_at_ms - 86_400_000,
                "coverage_end_ms": evaluated_at_ms + 86_400_000,
                "currencies": ["EUR"],
                "complete": True,
                "derived_by_extractor": True,
                "rule_id": "ecb-calendar-and-information",
                "rule_version": "1.0.0",
            },
        ],
        "events": events,
        "items": items,
        "semantic_output_sha256": digest(canonical({
            "events": events,
            "items": items,
        })),
        "mutation_attempted": False,
        "direct_broker_access": False,
        "paper_authorized": False,
        "live_authorized": False,
    }
    receipt = {
        **receipt_body,
        "body_sha256": digest(canonical(receipt_body)),
    }
    receipt_path = receipt_directory / "receipt.json"
    if receipt_path.exists():
        receipt_path.chmod(0o600)
    write_document(receipt_path, receipt)
    receipt_path.chmod(0o400)
    bundle = {
        "schema": "hepta.market-source-bundle.v2",
        "observed_at_ms": evaluated_at_ms - 500,
        "extraction_receipt_path": receipt_path.relative_to(
            directory).as_posix(),
        "extraction_receipt_sha256": digest(receipt_path.read_bytes()),
        "mutation_attempted": False,
        "direct_broker_access": False,
        "paper_authorized": False,
        "live_authorized": False,
    }
    normalizer.TRUSTED_EVIDENCE_ROOTS = (directory.resolve(),)
    normalizer.TRUSTED_ATTESTATION_UID = os.geteuid()
    normalizer.PINNED_EXTRACTORS = {
        (
            "HEPTA_TEST_OFFICIAL_EXTRACTOR",
            "1.0.0",
        ): extractor_code_sha256,
    }
    return normalizer.normalize(bundle)


def build_case(
    directory: Path,
    builder: Any,
    evaluator: Any,
    strategy_path: Path,
    *,
    name: str,
    mids: list[float],
    ascending: bool,
    event_window: bool = False,
    snapshot_version: int = 2,
    active_order_ids: list[int] | None = None,
    calendar_version: int = 3,
    information_version: int = 3,
    event_deltas_ms: list[int] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    evaluated_at_ms = 1_800_000_000_000
    paths = {
        label: directory / f"{name}-{label}.json"
        for label in ("snapshot", "quotes", "bars", "calendar", "information")
    }
    latest = mids[-1]
    source_snapshot = snapshot(
        evaluated_at_ms,
        round(latest - 0.00002, 8),
        round(latest + 0.00002, 8),
        version=snapshot_version,
        active_order_ids=active_order_ids,
    )
    write_document(paths["snapshot"], source_snapshot)
    write_document(
        paths["quotes"],
        quote_history(evaluated_at_ms, mids, source_snapshot),
    )
    write_document(
        paths["bars"], bar_history(evaluated_at_ms, ascending=ascending))
    if calendar_version == 3 and information_version == 3:
        calendar_document, information_document = attested_evidence(
            directory,
            builder.evidence_normalizer,
            name=name,
            evaluated_at_ms=evaluated_at_ms,
            event_window=event_window,
            event_deltas_ms=event_deltas_ms,
        )
    else:
        calendar_document = calendar(
            evaluated_at_ms,
            event_window=event_window,
            version=calendar_version,
            event_deltas_ms=event_deltas_ms,
        )
        information_document = information(
            evaluated_at_ms, version=information_version)
    write_document(paths["calendar"], calendar_document)
    write_document(paths["information"], information_document)
    packet = builder.build_packet(
        campaign_id="strategy-pipeline-test",
        iteration=1,
        evaluated_at_ms=evaluated_at_ms,
        strategy_path=strategy_path,
        snapshot_path=paths["snapshot"],
        quote_history_path=paths["quotes"],
        bar_history_path=paths["bars"],
        calendar_path=paths["calendar"],
        information_path=paths["information"],
    )
    config = evaluator.load_strategy(strategy_path)
    decision = evaluator.evaluate(
        packet,
        config,
        evaluator.strategy_package_digest(strategy_path),
    )
    return packet, decision


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    scripts = root / "scripts"
    sys.path.insert(0, str(scripts))
    import hepta_eurusd_confirmed_momentum_strategy as evaluator
    import hepta_market_context_builder as builder
    import hepta_strategy_replay_evaluator as replay
    sys.path.insert(0, str(root / "tests"))
    import hepta_strategy_replay_evaluator_tests as replay_support

    strategy_path = (
        root / "strategies/eurusd-confirmed-momentum-shadow-v2.json")
    with tempfile.TemporaryDirectory(
            prefix="hepta-strategy-pipeline-") as temporary:
        directory = Path(temporary)
        rising_mids = [
            1.10000, 1.10015, 1.10020, 1.10042,
            1.10050, 1.10062, 1.10080, 1.10100,
        ]
        buy_packet, buy_decision = build_case(
            directory,
            builder,
            evaluator,
            strategy_path,
            name="buy",
            mids=rising_mids,
            ascending=True,
        )
        assert buy_decision["decision"] == "TRADE", buy_decision
        assert buy_decision["trade_intent"]["side"] == "BUY"
        assert buy_decision["risk_challenges"] == []
        assert buy_packet["freshness"]["portfolio_freshness_provable"] is True
        assert buy_packet["authority"]["live_authorized"] is False
        assert buy_packet["history"]["raw_quote_observations"] == 541
        assert buy_packet["history"]["resampled_quote_observations"] == 7
        assert buy_packet["history"]["span_seconds"] == 5400.0
        assert buy_packet["features"]["quote_resample_seconds"] == 900
        packet_body = dict(buy_packet)
        packet_sha256 = packet_body.pop("body_sha256")
        assert packet_sha256 == digest(canonical(packet_body))

        expected_package = {
            "schema": "hepta.strategy-package-binding.v3",
            "strategy_id": "eurusd-confirmed-momentum-shadow",
            "strategy_version": "2.0.0",
            "config_sha256": digest(strategy_path.read_bytes()),
            "evaluator_sha256": digest(
                (scripts / "hepta_eurusd_confirmed_momentum_strategy.py").
                read_bytes()),
            "builder_sha256": digest(
                (scripts / "hepta_market_context_builder.py").read_bytes()),
            "normalizer_sha256": digest(
                (scripts / "hepta_market_evidence_normalizer.py").read_bytes()),
            "contracts_sha256": digest(
                (scripts / "hepta_strategy_contracts.py").read_bytes()),
        }
        assert (
            evaluator.strategy_package_digest(strategy_path) ==
            digest(canonical(expected_package))
        )

        strategy_config = evaluator.load_strategy(strategy_path)
        evaluated_at_ms = 1_800_000_000_000
        exclusion_after_ms = (
            strategy_config["setup"]["event_exclusion_after_seconds"] * 1000)
        exclusion_before_ms = (
            strategy_config["setup"]["event_exclusion_before_seconds"] * 1000)
        boundary_cases = (
            ("after-inclusive", -exclusion_after_ms, True),
            ("after-outside", -exclusion_after_ms - 1, False),
            ("before-inclusive", exclusion_before_ms, True),
            ("before-outside", exclusion_before_ms + 1, False),
        )
        for name, event_delta_ms, expected_active in boundary_cases:
            calendar_document = calendar(
                evaluated_at_ms,
                event_window=False,
                event_deltas_ms=[event_delta_ms],
            )
            normalized_calendar = builder._calendar(
                calendar_document,
                digest(canonical(calendar_document)),
                strategy_config,
                evaluated_at_ms,
            )
            assert (
                normalized_calendar["high_impact_event_window_active"] is
                expected_active
            ), name
            assert (
                normalized_calendar["nearest_high_impact_delta_seconds"] ==
                event_delta_ms / 1000.0
            ), name

        multi_event_deltas_ms = [
            -exclusion_after_ms - 60_000,
            exclusion_before_ms - 60_000,
        ]
        multi_event_calendar = calendar(
            evaluated_at_ms,
            event_window=False,
            event_deltas_ms=multi_event_deltas_ms,
        )
        normalized_multi_event_calendar = builder._calendar(
            multi_event_calendar,
            digest(canonical(multi_event_calendar)),
            strategy_config,
            evaluated_at_ms,
        )
        assert (
            normalized_multi_event_calendar[
                "nearest_high_impact_delta_seconds"] ==
            multi_event_deltas_ms[0] / 1000.0
        )
        assert (
            normalized_multi_event_calendar[
                "high_impact_event_window_active"] is True
        )

        tampered_packet = json.loads(json.dumps(buy_packet))
        tampered_packet["features"]["window_return_bps"] += 100.0
        try:
            evaluator.evaluate(
                tampered_packet,
                evaluator.load_strategy(strategy_path),
                evaluator.strategy_package_digest(strategy_path),
            )
        except evaluator.ContractError as error:
            assert str(error) == "STRATEGY_PACKET_DIGEST_INVALID"
        else:
            raise AssertionError("tampered strategy packet was accepted")

        def rebuild(
            quote_path: Path,
            bar_path: Path,
            *,
            iteration: int,
        ) -> dict[str, Any]:
            return builder.build_packet(
                campaign_id="strategy-pipeline-negative",
                iteration=iteration,
                evaluated_at_ms=1_800_000_000_000,
                strategy_path=strategy_path,
                snapshot_path=directory / "buy-snapshot.json",
                quote_history_path=quote_path,
                bar_history_path=bar_path,
                calendar_path=directory / "buy-calendar.json",
                information_path=directory / "buy-information.json",
            )

        def expect_builder_error(
            reason: str,
            quote_path: Path,
            bar_path: Path,
            *,
            iteration: int,
        ) -> None:
            try:
                rebuild(quote_path, bar_path, iteration=iteration)
            except builder.ContractError as error:
                assert str(error) == reason, error
            else:
                raise AssertionError(f"{reason} input was accepted")

        valid_quotes = json.loads(
            (directory / "buy-quotes.json").read_text(encoding="ascii"))
        digest_tampered_quotes = json.loads(json.dumps(valid_quotes))
        digest_tampered_quotes["quotes"][0]["bid"] -= 0.00001
        digest_tampered_path = directory / "digest-tampered-quotes.json"
        write_document(digest_tampered_path, digest_tampered_quotes)
        expect_builder_error(
            "CONTEXT_QUOTE_HISTORY_DIGEST_INVALID",
            digest_tampered_path,
            directory / "buy-bars.json",
            iteration=10,
        )

        gap_quotes = json.loads(json.dumps(valid_quotes))
        del gap_quotes["quotes"][len(gap_quotes["quotes"]) // 2]
        gap_quotes = reseal(gap_quotes)
        gap_quote_path = directory / "gap-quotes.json"
        write_document(gap_quote_path, gap_quotes)
        expect_builder_error(
            "CONTEXT_QUOTE_GAP_INVALID",
            gap_quote_path,
            directory / "buy-bars.json",
            iteration=11,
        )

        stale_quotes = json.loads(json.dumps(valid_quotes))
        stale_quotes["quotes"][-1]["stale"] = True
        stale_quotes = reseal(stale_quotes)
        stale_quote_path = directory / "stale-quotes.json"
        write_document(stale_quote_path, stale_quotes)
        expect_builder_error(
            "CONTEXT_QUOTE_VALUE_INVALID",
            stale_quote_path,
            directory / "buy-bars.json",
            iteration=12,
        )

        provenance_quotes = json.loads(json.dumps(valid_quotes))
        provenance_quotes["quotes"][-1][
            "source_snapshot_body_sha256"] = "sha256:" + "f" * 64
        provenance_quotes = reseal(provenance_quotes)
        provenance_quote_path = directory / "provenance-quotes.json"
        write_document(provenance_quote_path, provenance_quotes)
        expect_builder_error(
            "CONTEXT_LATEST_QUOTE_PROVENANCE_MISMATCH",
            provenance_quote_path,
            directory / "buy-bars.json",
            iteration=13,
        )

        valid_bars = json.loads(
            (directory / "buy-bars.json").read_text(encoding="ascii"))
        gap_bars = json.loads(json.dumps(valid_bars))
        del gap_bars["bars"][10]
        gap_bars["expected_bar_count"] = len(gap_bars["bars"])
        gap_bars = reseal(gap_bars)
        gap_bar_path = directory / "gap-bars.json"
        write_document(gap_bar_path, gap_bars)
        expect_builder_error(
            "CONTEXT_BAR_CADENCE_INVALID",
            directory / "buy-quotes.json",
            gap_bar_path,
            iteration=14,
        )

        stale_bars = json.loads(json.dumps(valid_bars))
        stale_shift_ms = 1_200_000
        for bar in stale_bars["bars"]:
            bar["started_at_ms"] -= stale_shift_ms
            bar["finished_at_ms"] -= stale_shift_ms
        stale_bars["window_started_at_ms"] -= stale_shift_ms
        stale_bars["window_finished_at_ms"] -= stale_shift_ms
        stale_bars = reseal(stale_bars)
        stale_bar_path = directory / "stale-bars.json"
        write_document(stale_bar_path, stale_bars)
        stale_bar_packet = rebuild(
            directory / "buy-quotes.json", stale_bar_path, iteration=15)
        stale_bar_decision = evaluator.evaluate(
            stale_bar_packet,
            evaluator.load_strategy(strategy_path),
            evaluator.strategy_package_digest(strategy_path),
        )
        assert stale_bar_decision["decision"] == "NO_TRADE"
        assert "STALE_BAR_HISTORY" in stale_bar_decision["reason_codes"]

        falling_mids = [
            1.10100, 1.10085, 1.10080, 1.10058,
            1.10050, 1.10038, 1.10020, 1.10000,
        ]
        _, sell_decision = build_case(
            directory,
            builder,
            evaluator,
            strategy_path,
            name="sell",
            mids=falling_mids,
            ascending=False,
        )
        assert sell_decision["decision"] == "TRADE", sell_decision
        assert sell_decision["trade_intent"]["side"] == "SELL"

        event_packet, event_decision = build_case(
            directory,
            builder,
            evaluator,
            strategy_path,
            name="event",
            mids=rising_mids,
            ascending=True,
            event_deltas_ms=multi_event_deltas_ms,
        )
        assert (
            event_packet["economic_calendar"][
                "nearest_high_impact_delta_seconds"] ==
            multi_event_deltas_ms[0] / 1000.0
        )
        assert (
            event_packet["economic_calendar"][
                "high_impact_event_window_active"] is True
        )
        assert event_decision["decision"] == "NO_TRADE"
        assert "HIGH_IMPACT_EVENT_WINDOW" in event_decision["reason_codes"]

        _, legacy_decision = build_case(
            directory,
            builder,
            evaluator,
            strategy_path,
            name="legacy",
            mids=rising_mids,
            ascending=True,
            snapshot_version=1,
        )
        assert legacy_decision["decision"] == "NO_TRADE"
        assert (
            "UNPROVABLE_PORTFOLIO_FRESHNESS" in
            legacy_decision["reason_codes"])

        _, active_order_decision = build_case(
            directory,
            builder,
            evaluator,
            strategy_path,
            name="active-order",
            mids=rising_mids,
            ascending=True,
            active_order_ids=[42],
        )
        assert active_order_decision["decision"] == "NO_TRADE"
        assert "ACTIVE_ORDER_PRESENT" in active_order_decision["reason_codes"]

        _, legacy_provenance_decision = build_case(
            directory,
            builder,
            evaluator,
            strategy_path,
            name="legacy-provenance",
            mids=rising_mids,
            ascending=True,
            calendar_version=1,
            information_version=1,
        )
        assert legacy_provenance_decision["decision"] == "NO_TRADE"
        assert (
            "MISSING_INFORMATION_PROVENANCE" in
            legacy_provenance_decision["reason_codes"])
        assert (
            "UNPROVABLE_ECONOMIC_CALENDAR" in
            legacy_provenance_decision["reason_codes"])

        strategy_sha256 = evaluator.strategy_package_digest(strategy_path)
        event_for_replay = {
            **event_decision,
            "decision_id": "strategy-pipeline-test-decision-0002",
            "started_at_ms": event_decision["started_at_ms"] + 120_000,
            "finished_at_ms": event_decision["finished_at_ms"] + 120_000,
        }
        fabricated_decision_set = {
            "schema": "hepta.strategy-decision-set.v1",
            "campaign_id": "strategy-pipeline-test",
            "strategy_id": strategy_config["strategy_id"],
            "strategy_version": strategy_config["strategy_version"],
            "strategy_sha256": strategy_sha256,
            "generated_at_ms": 1_800_001_000_000,
            "decisions": [buy_decision, event_for_replay],
            "mutation_attempted": False,
            "live_authorized": False,
        }
        entry_at_ms = buy_decision["trade_intent"]["observed_at_ms"]
        fabricated_marks = {
            "schema": "hepta.authoritative-replay-marks.v1",
            "instrument": "EUR.USD",
            "provider": "HEPTA_REPLAY",
            "source_ref": "fixture:marks",
            "observed_at_ms": entry_at_ms + 901_000,
            "marks": [{
                "bid": 1.10198,
                "ask": 1.10202,
                "observed_at_ms": entry_at_ms + 900_000,
            }],
        }
        try:
            replay.evaluate_replay(
                fabricated_decision_set,
                fabricated_marks,
                horizon_seconds=900,
                round_trip_cost_bps=0.8,
            )
        except replay.ContractError as error:
            assert str(error) == "REPLAY_DECISION_SET_FIELDS_INVALID"
        else:
            raise AssertionError(
                "unsealed fabricated replay evidence was accepted")

        fabricated_decision_path = directory / "fabricated-decisions.json"
        fabricated_marks_path = directory / "fabricated-marks.json"
        fabricated_output_path = directory / "fabricated-report.json"
        write_document(fabricated_decision_path, fabricated_decision_set)
        write_document(fabricated_marks_path, fabricated_marks)
        completed = subprocess.run(
            [
                sys.executable,
                str(scripts / "hepta_strategy_replay_evaluator.py"),
                "--decisions", str(fabricated_decision_path),
                "--marks", str(fabricated_marks_path),
                "--horizon-seconds", "900",
                "--round-trip-cost-bps", "0.8",
                "--output", str(fabricated_output_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        assert completed.returncode == 78, completed.stderr
        assert (
            "REPLAY_DECISION_SET_FIELDS_INVALID" in completed.stderr)
        assert not fabricated_output_path.exists()

        policy = replay_policy(
            campaign_id="strategy-pipeline-test",
            strategy_id=strategy_config["strategy_id"],
            strategy_version=strategy_config["strategy_version"],
            strategy_sha256=strategy_sha256,
            valid_after_ms=buy_decision["started_at_ms"],
            maximum_iterations=2,
        )
        policy_path = directory / "replay-policy.json"
        write_document(policy_path, policy)
        policy_sha256 = digest(policy_path.read_bytes())
        buy_snapshot = json.loads(
            (directory / "buy-snapshot.json").read_text(encoding="ascii"))
        event_snapshot = json.loads(
            (directory / "event-snapshot.json").read_text(encoding="ascii"))
        receipts = [
            replay_receipt(
                decision=buy_decision,
                packet=buy_packet,
                source_snapshot=buy_snapshot,
                policy=policy,
                policy_sha256=policy_sha256,
            ),
            replay_receipt(
                decision=event_for_replay,
                packet=event_packet,
                source_snapshot=event_snapshot,
                policy=policy,
                policy_sha256=policy_sha256,
            ),
        ]
        receipt_paths: list[Path] = []
        for index, receipt in enumerate(receipts, start=1):
            receipt_path = (
                directory / f"replay-receipt-{index:04d}.json")
            write_document(receipt_path, receipt)
            receipt_paths.append(receipt_path)

        intent_limit = float(buy_decision["trade_intent"]["limit_price"])

        def promotion_price(offset_ms: int) -> tuple[float, float]:
            if offset_ms >= 900_000:
                return 1.10198, 1.10202
            return intent_limit - 0.00007, intent_limit - 0.00004

        records = replay_support.make_records(price=promotion_price)
        segment_audit = replay_support.audit_for(records)
        final_audit = replay_support.make_final_audit(
            policy, policy_sha256, segment_audit)
        final_audit_path = directory / "replay-final-audit.json"
        write_document(final_audit_path, final_audit)
        history_directory = directory / "replay-history"
        history_directory.mkdir()
        sealed_decisions = replay.seal_decision_set(
            policy_path, final_audit_path, receipt_paths)
        with (
                mock.patch.object(
                    replay.market_history,
                    "audit_history",
                    return_value=segment_audit),
                mock.patch.object(
                    replay.market_history,
                    "load_history",
                    return_value=records)):
            sealed_marks = replay.seal_mark_set(
                policy_path,
                final_audit_path,
                [history_directory],
                cadence_ms=replay_support.CADENCE_MS,
                maximum_jitter_ms=replay_support.JITTER_MS,
            )
        report = replay.evaluate_replay(
            sealed_decisions,
            sealed_marks,
            horizon_seconds=900,
            round_trip_cost_bps=0.8,
        )
        assert report["schema"] == "hepta.strategy-replay-report.v3"
        assert report["decision_count"] == 2
        assert report["trade_candidate_count"] == 1
        assert report["no_trade_count"] == 1
        assert report["filled_count"] == 1
        assert report["resolved_count"] == 1
        assert report["cost_adjusted_hit_rate"] == 1.0
        assert report["mutation_attempted"] is False
        assert report["live_authorized"] is False
        assert report["results"][0]["entry_at_ms"] > entry_at_ms
        assert report["results"][0]["entry_price"] <= intent_limit
        assert report["results"][0]["exit_reason"] == "HORIZON"
        report_body = dict(report)
        expected_digest = report_body.pop("body_sha256")
        assert expected_digest == digest(canonical(report_body))

        decision_path = directory / "sealed-decisions.json"
        marks_path = directory / "sealed-marks.json"
        output_path = directory / "report.json"
        write_document(decision_path, sealed_decisions)
        write_document(marks_path, sealed_marks)
        completed = subprocess.run(
            [
                sys.executable,
                str(scripts / "hepta_strategy_replay_evaluator.py"),
                "--decisions", str(decision_path),
                "--marks", str(marks_path),
                "--horizon-seconds", "900",
                "--round-trip-cost-bps", "0.8",
                "--output", str(output_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        assert completed.returncode == 0, completed.stderr
        assert "resolved=1 candidates=1" in completed.stdout
        assert json.loads(
            output_path.read_text(encoding="ascii")) == report

    print("hepta_strategy_pipeline_tests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
