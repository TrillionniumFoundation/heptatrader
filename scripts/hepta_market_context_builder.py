#!/usr/bin/env python3

"""Build a provenance-bound, read-only market information packet."""

from __future__ import annotations

import argparse
from bisect import bisect_right
from datetime import datetime, timezone
import math
from pathlib import Path
import statistics
import sys
from typing import Any
from urllib.parse import urlsplit

import hepta_market_evidence_normalizer as evidence_normalizer
from hepta_eurusd_confirmed_momentum_strategy import (
    load_strategy,
    strategy_package_digest,
)
from hepta_strategy_contracts import (
    ContractError,
    atomic_write_json,
    canonical_bytes,
    digest_bytes,
    digest_file,
    load_document,
    require_digest,
    require_exact_fields,
    require_int,
    require_number,
    require_text,
)


SNAPSHOT_V1_FIELDS = frozenset({
    "schema", "version", "domain_id", "agent_uid", "generated_at_ms",
    "instrument", "catalog_sha256", "descriptor_sha256", "reads",
    "paper_authorized", "live_authorized", "mutation_attempted",
    "direct_broker_access", "body_sha256",
})
SNAPSHOT_V2_FIELDS = SNAPSHOT_V1_FIELDS | frozenset({
    "collection_started_at_ms", "collection_finished_at_ms",
    "read_finished_at_ms",
})
QUOTE_HISTORY_V1_FIELDS = frozenset({
    "schema", "instrument", "provider", "source_ref",
    "observed_at_ms", "quotes",
})
QUOTE_HISTORY_V2_FIELDS = QUOTE_HISTORY_V1_FIELDS | frozenset({
    "version", "window_started_at_ms", "window_finished_at_ms",
    "cadence_ms", "maximum_gap_ms", "complete", "body_sha256",
})
QUOTE_HISTORY_V3_FIELDS = QUOTE_HISTORY_V2_FIELDS | frozenset({
    "source_window_truncated",
})
QUOTE_V1_FIELDS = frozenset({
    "bid", "ask", "observed_at_ms", "stale_after_ms",
    "authoritative", "stale",
})
QUOTE_V2_FIELDS = QUOTE_V1_FIELDS | frozenset({
    "source_snapshot_body_sha256", "catalog_sha256", "descriptor_sha256",
    "execution_service_epoch", "execution_service_fencing_generation",
    "watch_generation",
})
QUOTE_V3_FIELDS = QUOTE_V2_FIELDS | frozenset({
    "captured_at_ms", "quote_changed",
})
BAR_HISTORY_V1_FIELDS = frozenset({
    "schema", "instrument", "provider", "source_ref",
    "observed_at_ms", "bars",
})
BAR_HISTORY_V2_FIELDS = BAR_HISTORY_V1_FIELDS | frozenset({
    "version", "source_content_sha256", "interval_ms",
    "window_started_at_ms", "window_finished_at_ms", "expected_bar_count",
    "complete", "body_sha256",
})
BAR_V1_FIELDS = frozenset({
    "started_at_ms", "finished_at_ms", "open", "high", "low", "close",
})
BAR_V2_FIELDS = BAR_V1_FIELDS | frozenset({
    "sample_count", "complete", "source_content_sha256",
})
CALENDAR_V1_FIELDS = frozenset({
    "schema", "provider", "source_ref", "observed_at_ms", "events",
})
CALENDAR_V2_FIELDS = frozenset({
    "schema", "provider", "source_ref", "observed_at_ms", "sources", "events",
    "body_sha256",
})
CALENDAR_V3_FIELDS = CALENDAR_V2_FIELDS | frozenset({"attestation"})
EVENT_V1_FIELDS = frozenset({
    "event_id", "currencies", "importance", "scheduled_at_ms",
    "title_sha256",
})
EVENT_V2_FIELDS = EVENT_V1_FIELDS | frozenset({"source_content_sha256"})
INFORMATION_V1_FIELDS = frozenset({
    "schema", "provider", "source_ref", "observed_at_ms", "items",
})
INFORMATION_V2_FIELDS = frozenset({
    "schema", "provider", "source_ref", "observed_at_ms", "sources", "items",
    "body_sha256",
})
INFORMATION_V3_FIELDS = INFORMATION_V2_FIELDS | frozenset({"attestation"})
ITEM_V1_FIELDS = frozenset({
    "item_id", "published_at_ms", "observed_at_ms", "content_sha256",
    "confidence", "currencies", "conflict_group",
})
ITEM_V2_FIELDS = ITEM_V1_FIELDS | frozenset({"source_content_sha256"})
SOURCE_FIELDS = frozenset({
    "provider", "source_ref", "retrieved_at_ms", "published_at_ms",
    "revision", "content_sha256", "coverage_start_ms", "coverage_end_ms",
    "currencies",
})
PROVIDER_HOSTS = {
    "FEDERAL_RESERVE": frozenset({
        "federalreserve.gov", "www.federalreserve.gov",
    }),
    "BLS": frozenset({"bls.gov", "www.bls.gov", "blsmon1.bls.gov"}),
    "BEA": frozenset({"bea.gov", "www.bea.gov"}),
    "ECB": frozenset({"ecb.europa.eu", "www.ecb.europa.eu"}),
    "EUROSTAT": frozenset({"ec.europa.eu"}),
}
PROVIDER_CURRENCIES = {
    "FEDERAL_RESERVE": frozenset({"USD"}),
    "BLS": frozenset({"USD"}),
    "BEA": frozenset({"USD"}),
    "ECB": frozenset({"EUR"}),
    "EUROSTAT": frozenset({"EUR"}),
}


def _validate_snapshot(snapshot: dict[str, Any]) -> None:
    schema = snapshot.get("schema")
    version = snapshot.get("version")
    if schema == "hepta.shadow-watch-snapshot.v1" and version == 1:
        require_exact_fields(
            snapshot, SNAPSHOT_V1_FIELDS, "CONTEXT_SNAPSHOT_FIELDS_INVALID")
    elif schema == "hepta.shadow-watch-snapshot.v2" and version == 2:
        require_exact_fields(
            snapshot, SNAPSHOT_V2_FIELDS, "CONTEXT_SNAPSHOT_FIELDS_INVALID")
        started = require_int(
            snapshot["collection_started_at_ms"],
            "CONTEXT_COLLECTION_TIME_INVALID", minimum=0)
        finished = require_int(
            snapshot["collection_finished_at_ms"],
            "CONTEXT_COLLECTION_TIME_INVALID", minimum=started)
        if finished > snapshot["generated_at_ms"]:
            raise ContractError("CONTEXT_COLLECTION_TIME_INVALID")
        read_times = snapshot["read_finished_at_ms"]
        if (
                not isinstance(read_times, dict) or
                set(read_times) != set(snapshot["reads"])):
            raise ContractError("CONTEXT_READ_TIMES_INVALID")
        previous = started
        for tool in (
                "account.get_summary", "portfolio.list_positions",
                "orders.list", "risk.get_limits", "market.get_quote",
                "system.get_health"):
            observed = require_int(
                read_times.get(tool), "CONTEXT_READ_TIMES_INVALID",
                minimum=previous, maximum=finished)
            previous = observed
    else:
        raise ContractError("CONTEXT_SNAPSHOT_SCHEMA_INVALID")
    if (
            snapshot.get("instrument") != "EUR.USD" or
            snapshot.get("paper_authorized") is not False or
            snapshot.get("live_authorized") is not False or
            snapshot.get("mutation_attempted") is not False or
            snapshot.get("direct_broker_access") is not False):
        raise ContractError("CONTEXT_SNAPSHOT_BOUNDARY_INVALID")
    require_digest(
        snapshot.get("catalog_sha256"), "CONTEXT_CATALOG_DIGEST_INVALID")
    descriptor_map = snapshot.get("descriptor_sha256")
    if not isinstance(descriptor_map, dict) or not descriptor_map:
        raise ContractError("CONTEXT_DESCRIPTOR_DIGEST_INVALID")
    for digest in descriptor_map.values():
        require_digest(digest, "CONTEXT_DESCRIPTOR_DIGEST_INVALID")
    require_digest(
        descriptor_map.get("market.get_quote"),
        "CONTEXT_DESCRIPTOR_DIGEST_INVALID",
    )
    body = dict(snapshot)
    expected_digest = body.pop("body_sha256", None)
    if expected_digest != digest_bytes(canonical_bytes(body)):
        raise ContractError("CONTEXT_SNAPSHOT_DIGEST_INVALID")


def _validate_snapshot_reads(snapshot: dict[str, Any]) -> None:
    reads = snapshot.get("reads")
    if not isinstance(reads, dict):
        raise ContractError("CONTEXT_SNAPSHOT_READS_INVALID")
    health = reads.get("system.get_health")
    quote = reads.get("market.get_quote")
    account = reads.get("account.get_summary")
    positions = reads.get("portfolio.list_positions")
    orders = reads.get("orders.list")
    risk = reads.get("risk.get_limits")
    if (
            not isinstance(health, dict) or
            health.get("remote_execution_ready") is not True or
            health.get("execution_mode") != "SIMULATOR"):
        raise ContractError("CONTEXT_HEALTH_INVALID")
    require_text(
        health.get("execution_service_epoch"),
        "CONTEXT_HEALTH_INVALID",
        identifier=True,
    )
    require_int(
        health.get("execution_service_fencing_generation"),
        "CONTEXT_HEALTH_INVALID",
        minimum=0,
    )
    if (
            not isinstance(quote, dict) or
            quote.get("authoritative") is not True or
            quote.get("stale") is not False):
        raise ContractError("CONTEXT_QUOTE_INVALID")
    for value, reason in (
            (account, "CONTEXT_ACCOUNT_INVALID"),
            (positions, "CONTEXT_POSITIONS_INVALID"),
            (orders, "CONTEXT_ORDERS_INVALID"),
            (risk, "CONTEXT_RISK_INVALID")):
        if not isinstance(value, dict) or value.get("authoritative") is not True:
            raise ContractError(reason)
    if (
            not isinstance(positions.get("positions"), list) or
            not isinstance(orders.get("active_order_ids"), list)):
        raise ContractError("CONTEXT_PORTFOLIO_INVALID")


def _quote_document(
    path: Path | None,
    snapshot: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    if path is None:
        quote = snapshot["reads"]["market.get_quote"]
        document = {
            "schema": "hepta.authoritative-quote-history.v1",
            "instrument": "EUR.USD",
            "provider": quote.get("source", "HEPTA"),
            "source_ref": snapshot["body_sha256"],
            "observed_at_ms": quote["observed_at_ms"],
            "quotes": [{
                "bid": quote["bid"],
                "ask": quote["ask"],
                "observed_at_ms": quote["observed_at_ms"],
                "stale_after_ms": quote["stale_after_ms"],
                "authoritative": True,
                "stale": False,
            }],
        }
        return document, digest_bytes(canonical_bytes(document))
    return load_document(path, "CONTEXT_QUOTES"), digest_file(path)


def _validate_quotes(
    document: dict[str, Any],
    config: dict[str, Any],
    evaluated_at_ms: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    schema = document.get("schema")
    is_v2 = schema == "hepta.authoritative-quote-history.v2"
    is_v3 = schema == "hepta.authoritative-quote-history.v3"
    provenance_schema = is_v2 or is_v3
    if provenance_schema:
        require_exact_fields(
            document,
            QUOTE_HISTORY_V3_FIELDS if is_v3 else QUOTE_HISTORY_V2_FIELDS,
            "CONTEXT_QUOTE_HISTORY_FIELDS_INVALID")
        if document.get("version") != (3 if is_v3 else 2):
            raise ContractError("CONTEXT_QUOTE_HISTORY_SCHEMA_INVALID")
        expected_body_sha256 = require_digest(
            document["body_sha256"], "CONTEXT_QUOTE_HISTORY_DIGEST_INVALID")
        body = dict(document)
        body.pop("body_sha256")
        if digest_bytes(canonical_bytes(body)) != expected_body_sha256:
            raise ContractError("CONTEXT_QUOTE_HISTORY_DIGEST_INVALID")
    else:
        require_exact_fields(
            document, QUOTE_HISTORY_V1_FIELDS,
            "CONTEXT_QUOTE_HISTORY_FIELDS_INVALID")
    if (
            schema not in {
                "hepta.authoritative-quote-history.v1",
                "hepta.authoritative-quote-history.v2",
                "hepta.authoritative-quote-history.v3",
            } or
            document["instrument"] != "EUR.USD"):
        raise ContractError("CONTEXT_QUOTE_HISTORY_SCHEMA_INVALID")
    require_text(document["provider"], "CONTEXT_QUOTE_PROVIDER_INVALID")
    require_text(document["source_ref"], "CONTEXT_QUOTE_SOURCE_INVALID")
    require_int(
        document["observed_at_ms"], "CONTEXT_QUOTE_DOCUMENT_TIME_INVALID",
        minimum=0, maximum=evaluated_at_ms)
    quotes = document["quotes"]
    if not isinstance(quotes, list) or not quotes:
        raise ContractError("CONTEXT_QUOTE_HISTORY_EMPTY")
    result: list[dict[str, Any]] = []
    previous_observed = -1
    previous_capture = -1
    for value in quotes:
        quote = require_exact_fields(
            value,
            (
                QUOTE_V3_FIELDS if is_v3 else
                QUOTE_V2_FIELDS if is_v2 else
                QUOTE_V1_FIELDS
            ),
            "CONTEXT_QUOTE_FIELDS_INVALID")
        bid = require_number(
            quote["bid"], "CONTEXT_QUOTE_PRICE_INVALID", positive=True)
        ask = require_number(
            quote["ask"], "CONTEXT_QUOTE_PRICE_INVALID", positive=True)
        observed_at_ms = require_int(
            quote["observed_at_ms"], "CONTEXT_QUOTE_TIME_INVALID",
            minimum=(
                previous_observed if is_v3 else previous_observed + 1
            ),
            maximum=evaluated_at_ms)
        stale_after_ms = require_int(
            quote["stale_after_ms"], "CONTEXT_QUOTE_TIME_INVALID",
            minimum=observed_at_ms + 1)
        if (
                bid > ask or quote["authoritative"] is not True or
                quote["stale"] is not False):
            raise ContractError("CONTEXT_QUOTE_VALUE_INVALID")
        normalized = {
            "bid": bid,
            "ask": ask,
            "mid": (bid + ask) / 2.0,
            "spread_bps": ((ask - bid) / ((ask + bid) / 2.0)) * 10000.0,
            "observed_at_ms": observed_at_ms,
            "stale_after_ms": stale_after_ms,
            "stale": quote["stale"],
        }
        if provenance_schema:
            normalized.update({
                "source_snapshot_body_sha256": require_digest(
                    quote["source_snapshot_body_sha256"],
                    "CONTEXT_QUOTE_PROVENANCE_INVALID"),
                "catalog_sha256": require_digest(
                    quote["catalog_sha256"],
                    "CONTEXT_QUOTE_PROVENANCE_INVALID"),
                "descriptor_sha256": require_digest(
                    quote["descriptor_sha256"],
                    "CONTEXT_QUOTE_PROVENANCE_INVALID"),
                "execution_service_epoch": require_text(
                    quote["execution_service_epoch"],
                    "CONTEXT_QUOTE_PROVENANCE_INVALID", identifier=True),
                "execution_service_fencing_generation": require_int(
                    quote["execution_service_fencing_generation"],
                    "CONTEXT_QUOTE_PROVENANCE_INVALID", minimum=0),
                "watch_generation": require_int(
                    quote["watch_generation"],
                    "CONTEXT_QUOTE_PROVENANCE_INVALID", minimum=1),
            })
        if is_v3:
            captured_at_ms = require_int(
                quote["captured_at_ms"],
                "CONTEXT_QUOTE_CAPTURE_TIME_INVALID",
                minimum=previous_capture + 1,
                maximum=min(evaluated_at_ms, stale_after_ms),
            )
            quote_changed = quote["quote_changed"]
            if not isinstance(quote_changed, bool):
                raise ContractError("CONTEXT_QUOTE_CHANGE_FLAG_INVALID")
            normalized.update({
                "captured_at_ms": captured_at_ms,
                "quote_changed": quote_changed,
            })
            if result:
                previous_quote = result[-1]
                if observed_at_ms == previous_observed:
                    if (
                            quote_changed is not False or
                            any(
                                normalized[field] != previous_quote[field]
                                for field in (
                                    "bid", "ask", "observed_at_ms",
                                    "stale_after_ms", "stale",
                                )
                            )):
                        raise ContractError("CONTEXT_QUOTE_MUTATION")
                elif quote_changed is not True:
                    raise ContractError(
                        "CONTEXT_QUOTE_CHANGE_FLAG_INVALID")
        result.append(normalized)
        previous_observed = observed_at_ms
        if is_v3:
            previous_capture = normalized["captured_at_ms"]
    if not provenance_schema:
        return result, {
            "schema": schema,
            "provenance_provable": False,
            "complete": False,
            "cadence_ms": None,
            "maximum_gap_ms": None,
        }

    cadence_ms = require_int(
        document["cadence_ms"], "CONTEXT_QUOTE_CADENCE_INVALID",
        minimum=5000, maximum=15000)
    configured_gap_ms = (
        config["feature_windows"]["quote_maximum_gap_seconds"] * 1000)
    maximum_gap_ms = require_int(
        document["maximum_gap_ms"], "CONTEXT_QUOTE_CADENCE_INVALID",
        minimum=cadence_ms, maximum=configured_gap_ms)
    if document["complete"] is not True:
        raise ContractError("CONTEXT_QUOTE_HISTORY_INCOMPLETE")
    window_started_at_ms = require_int(
        document["window_started_at_ms"], "CONTEXT_QUOTE_WINDOW_INVALID",
        minimum=0, maximum=evaluated_at_ms)
    window_finished_at_ms = require_int(
        document["window_finished_at_ms"], "CONTEXT_QUOTE_WINDOW_INVALID",
        minimum=window_started_at_ms, maximum=evaluated_at_ms)
    if is_v3:
        source_window_truncated = document["source_window_truncated"]
        if not isinstance(source_window_truncated, bool):
            raise ContractError("CONTEXT_QUOTE_WINDOW_INVALID")
        if (
                result[0]["quote_changed"] is not True and
                source_window_truncated is not True):
            raise ContractError("CONTEXT_QUOTE_CHANGE_FLAG_INVALID")
        if (
                window_started_at_ms != result[0]["captured_at_ms"] or
                window_finished_at_ms != result[-1]["captured_at_ms"] or
                document["observed_at_ms"] !=
                result[-1]["observed_at_ms"]):
            raise ContractError("CONTEXT_QUOTE_WINDOW_INVALID")
    elif (
            window_started_at_ms != result[0]["observed_at_ms"] or
            window_finished_at_ms != result[-1]["observed_at_ms"] or
            document["observed_at_ms"] != window_finished_at_ms):
        raise ContractError("CONTEXT_QUOTE_WINDOW_INVALID")
    for previous_quote, current_quote in zip(result, result[1:]):
        gap_ms = (
            current_quote[
                "captured_at_ms" if is_v3 else "observed_at_ms"] -
            previous_quote[
                "captured_at_ms" if is_v3 else "observed_at_ms"])
        if gap_ms > maximum_gap_ms:
            raise ContractError("CONTEXT_QUOTE_GAP_INVALID")
        if (
                is_v3 and
                current_quote["quote_changed"] is True and
                current_quote["observed_at_ms"] -
                previous_quote["observed_at_ms"] > maximum_gap_ms):
            raise ContractError("CONTEXT_QUOTE_GAP_INVALID")
    independent = (
        [quote for quote in result if quote.get("quote_changed") is True]
        if is_v3 else
        result
    )
    if not independent:
        raise ContractError("CONTEXT_QUOTE_HISTORY_EMPTY")
    required_span_ms = (
        config["feature_windows"]["quote_lookback_seconds"] * 1000)
    if (
            independent[-1]["observed_at_ms"] -
            independent[0]["observed_at_ms"] < required_span_ms):
        raise ContractError("CONTEXT_QUOTE_WINDOW_INSUFFICIENT")
    return result, {
        "schema": schema,
        "provenance_provable": True,
        "complete": True,
        "cadence_ms": cadence_ms,
        "maximum_gap_ms": maximum_gap_ms,
        "independent_quote_count": len(independent),
    }


def _independent_quotes(
    quotes: list[dict[str, Any]],
    quote_metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    if quote_metadata["schema"] != "hepta.authoritative-quote-history.v3":
        return list(quotes)
    independent = [
        quote for quote in quotes if quote["quote_changed"] is True]
    if len(independent) != quote_metadata["independent_quote_count"]:
        raise ContractError("CONTEXT_QUOTE_CHANGE_COUNT_INVALID")
    return independent


def _resample_quotes(
    quotes: list[dict[str, Any]],
    quote_metadata: dict[str, Any],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    if quote_metadata["provenance_provable"] is not True:
        return list(quotes)
    windows = config["feature_windows"]
    lookback_ms = windows["quote_lookback_seconds"] * 1000
    interval_ms = windows["quote_resample_seconds"] * 1000
    maximum_lag_ms = quote_metadata["maximum_gap_ms"]
    anchor_ms = quotes[-1]["observed_at_ms"]
    first_target_ms = anchor_ms - lookback_ms
    quote_times = [quote["observed_at_ms"] for quote in quotes]
    result: list[dict[str, Any]] = []
    target_ms = first_target_ms
    while target_ms <= anchor_ms:
        index = bisect_right(quote_times, target_ms) - 1
        if index < 0 or target_ms - quote_times[index] > maximum_lag_ms:
            raise ContractError("CONTEXT_QUOTE_RESAMPLE_GAP")
        result.append(quotes[index])
        target_ms += interval_ms
    expected = lookback_ms // interval_ms + 1
    if len(result) != expected or result[-1] is not quotes[-1]:
        raise ContractError("CONTEXT_QUOTE_RESAMPLE_INVALID")
    return result


def _bar_document(
    path: Path | None,
) -> tuple[dict[str, Any] | None, str | None]:
    if path is None:
        return None, None
    return load_document(path, "CONTEXT_BARS"), digest_file(path)


def _validate_bars(
    document: dict[str, Any] | None,
    config: dict[str, Any],
    evaluated_at_ms: int,
) -> tuple[list[dict[str, float | int]], dict[str, Any]]:
    if document is None:
        return [], {
            "schema": None,
            "provenance_provable": False,
            "complete": False,
            "interval_ms": None,
            "age_ms": None,
            "fresh": False,
        }
    schema = document.get("schema")
    is_v2 = schema == "hepta.authoritative-bar-history.v2"
    if is_v2:
        require_exact_fields(
            document, BAR_HISTORY_V2_FIELDS,
            "CONTEXT_BAR_HISTORY_FIELDS_INVALID")
        if document.get("version") != 2:
            raise ContractError("CONTEXT_BAR_HISTORY_SCHEMA_INVALID")
        expected_body_sha256 = require_digest(
            document["body_sha256"], "CONTEXT_BAR_HISTORY_DIGEST_INVALID")
        body = dict(document)
        body.pop("body_sha256")
        if digest_bytes(canonical_bytes(body)) != expected_body_sha256:
            raise ContractError("CONTEXT_BAR_HISTORY_DIGEST_INVALID")
    else:
        require_exact_fields(
            document, BAR_HISTORY_V1_FIELDS,
            "CONTEXT_BAR_HISTORY_FIELDS_INVALID")
    if (
            schema not in {
                "hepta.authoritative-bar-history.v1",
                "hepta.authoritative-bar-history.v2",
            } or
            document["instrument"] != "EUR.USD"):
        raise ContractError("CONTEXT_BAR_HISTORY_SCHEMA_INVALID")
    require_text(document["provider"], "CONTEXT_BAR_PROVIDER_INVALID")
    require_text(document["source_ref"], "CONTEXT_BAR_SOURCE_INVALID")
    require_int(
        document["observed_at_ms"], "CONTEXT_BAR_DOCUMENT_TIME_INVALID",
        minimum=0, maximum=evaluated_at_ms)
    bars = document["bars"]
    if not isinstance(bars, list):
        raise ContractError("CONTEXT_BAR_HISTORY_INVALID")
    result: list[dict[str, float | int]] = []
    previous_finished = -1
    for value in bars:
        bar = require_exact_fields(
            value, BAR_V2_FIELDS if is_v2 else BAR_V1_FIELDS,
            "CONTEXT_BAR_FIELDS_INVALID")
        started_at_ms = require_int(
            bar["started_at_ms"], "CONTEXT_BAR_TIME_INVALID",
            minimum=previous_finished + 1, maximum=evaluated_at_ms)
        finished_at_ms = require_int(
            bar["finished_at_ms"], "CONTEXT_BAR_TIME_INVALID",
            minimum=started_at_ms + 1, maximum=evaluated_at_ms)
        open_price = require_number(
            bar["open"], "CONTEXT_BAR_PRICE_INVALID", positive=True)
        high_price = require_number(
            bar["high"], "CONTEXT_BAR_PRICE_INVALID", positive=True)
        low_price = require_number(
            bar["low"], "CONTEXT_BAR_PRICE_INVALID", positive=True)
        close_price = require_number(
            bar["close"], "CONTEXT_BAR_PRICE_INVALID", positive=True)
        if (
                low_price > min(open_price, close_price) or
                high_price < max(open_price, close_price) or
                low_price > high_price):
            raise ContractError("CONTEXT_BAR_OHLC_INVALID")
        normalized: dict[str, float | int] = {
            "started_at_ms": started_at_ms,
            "finished_at_ms": finished_at_ms,
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
        }
        if is_v2:
            sample_count = require_int(
                bar["sample_count"], "CONTEXT_BAR_SAMPLE_COUNT_INVALID",
                minimum=1)
            if bar["complete"] is not True:
                raise ContractError("CONTEXT_BAR_INCOMPLETE")
            source_content_sha256 = require_digest(
                bar["source_content_sha256"],
                "CONTEXT_BAR_PROVENANCE_INVALID")
            normalized.update({
                "sample_count": sample_count,
                "source_content_sha256": source_content_sha256,
            })
        result.append(normalized)
        previous_finished = finished_at_ms
    if not result:
        return result, {
            "schema": schema,
            "provenance_provable": False,
            "complete": False,
            "interval_ms": None,
            "age_ms": None,
            "fresh": False,
        }
    age_ms = evaluated_at_ms - int(result[-1]["finished_at_ms"])
    if not is_v2:
        return result, {
            "schema": schema,
            "provenance_provable": False,
            "complete": False,
            "interval_ms": None,
            "age_ms": age_ms,
            "fresh": False,
        }

    source_content_sha256 = require_digest(
        document["source_content_sha256"],
        "CONTEXT_BAR_PROVENANCE_INVALID")
    interval_ms = require_int(
        document["interval_ms"], "CONTEXT_BAR_INTERVAL_INVALID",
        minimum=1000, maximum=86400000)
    if interval_ms != config["feature_windows"]["bar_interval_seconds"] * 1000:
        raise ContractError("CONTEXT_BAR_INTERVAL_INVALID")
    if document["complete"] is not True:
        raise ContractError("CONTEXT_BAR_HISTORY_INCOMPLETE")
    expected_bar_count = require_int(
        document["expected_bar_count"], "CONTEXT_BAR_COUNT_INVALID",
        minimum=1)
    window_started_at_ms = require_int(
        document["window_started_at_ms"], "CONTEXT_BAR_WINDOW_INVALID",
        minimum=0)
    window_finished_at_ms = require_int(
        document["window_finished_at_ms"], "CONTEXT_BAR_WINDOW_INVALID",
        minimum=window_started_at_ms, maximum=evaluated_at_ms)
    if (
            len(result) != expected_bar_count or
            result[0]["started_at_ms"] != window_started_at_ms or
            result[-1]["finished_at_ms"] != window_finished_at_ms or
            document["observed_at_ms"] < window_finished_at_ms):
        raise ContractError("CONTEXT_BAR_WINDOW_INVALID")
    for index, bar in enumerate(result):
        expected_started_at_ms = window_started_at_ms + index * interval_ms
        if (
                bar["started_at_ms"] != expected_started_at_ms or
                bar["finished_at_ms"] !=
                expected_started_at_ms + interval_ms - 1 or
                bar["source_content_sha256"] != source_content_sha256):
            raise ContractError("CONTEXT_BAR_CADENCE_INVALID")
    fresh = 0 <= age_ms <= config["freshness_limits"]["bar_age_ms"]
    return result, {
        "schema": schema,
        "provenance_provable": True,
        "complete": True,
        "interval_ms": interval_ms,
        "age_ms": age_ms,
        "fresh": fresh,
    }


def _calendar_document(
    path: Path | None,
) -> tuple[dict[str, Any] | None, str | None]:
    if path is None:
        return None, None
    return load_document(path, "CONTEXT_CALENDAR"), digest_file(path)


def _provenance_sources(
    value: Any,
    evaluated_at_ms: int,
    reason: str,
) -> tuple[list[dict[str, Any]], set[str]]:
    if not isinstance(value, list) or not value:
        raise ContractError(reason)
    normalized: list[dict[str, Any]] = []
    content_digests: set[str] = set()
    identities: set[tuple[str, str]] = set()
    for item in value:
        source = require_exact_fields(item, SOURCE_FIELDS, reason)
        provider = require_text(
            source["provider"], reason, identifier=True)
        if provider not in PROVIDER_HOSTS:
            raise ContractError(reason)
        source_ref = require_text(source["source_ref"], reason)
        parsed = urlsplit(source_ref)
        if (
                parsed.scheme != "https" or
                parsed.hostname not in PROVIDER_HOSTS[provider] or
                parsed.username is not None or parsed.password is not None or
                parsed.fragment or not parsed.path.startswith("/")):
            raise ContractError(reason)
        retrieved_at_ms = require_int(
            source["retrieved_at_ms"], reason, minimum=0,
            maximum=evaluated_at_ms)
        published_at_ms = source["published_at_ms"]
        if published_at_ms is not None:
            published_at_ms = require_int(
                published_at_ms, reason, minimum=0,
                maximum=retrieved_at_ms)
        revision = require_text(
            source["revision"], reason, maximum=256)
        content_sha256 = require_digest(source["content_sha256"], reason)
        coverage_start_ms = require_int(
            source["coverage_start_ms"], reason, minimum=0,
            maximum=evaluated_at_ms)
        coverage_end_ms = require_int(
            source["coverage_end_ms"], reason,
            minimum=evaluated_at_ms)
        currencies = source["currencies"]
        if (
                not isinstance(currencies, list) or not currencies or
                len(currencies) != len(set(currencies)) or
                any(currency not in {"EUR", "USD"} for currency in currencies)):
            raise ContractError(reason)
        if not set(currencies).issubset(PROVIDER_CURRENCIES[provider]):
            raise ContractError(reason)
        identity = (provider, source_ref)
        if content_sha256 in content_digests or identity in identities:
            raise ContractError(reason)
        content_digests.add(content_sha256)
        identities.add(identity)
        normalized.append({
            "provider": provider,
            "source_ref": source_ref,
            "retrieved_at_ms": retrieved_at_ms,
            "published_at_ms": published_at_ms,
            "revision": revision,
            "content_sha256": content_sha256,
            "coverage_start_ms": coverage_start_ms,
            "coverage_end_ms": coverage_end_ms,
            "currencies": list(currencies),
        })
    covered_currencies = {
        currency
        for source in normalized
        for currency in source["currencies"]
    }
    if covered_currencies != {"EUR", "USD"}:
        raise ContractError(reason)
    return normalized, content_digests


def _calendar(
    document: dict[str, Any] | None,
    document_digest: str | None,
    config: dict[str, Any],
    evaluated_at_ms: int,
) -> dict[str, Any]:
    if document is None:
        return {
            "schema": None,
            "present": False,
            "provenance_provable": False,
            "provider": None,
            "source_ref": None,
            "file_sha256": None,
            "observed_at_ms": None,
            "sources": [],
            "events": [],
            "high_impact_event_window_active": False,
            "nearest_high_impact_delta_seconds": None,
        }
    schema = document.get("schema")
    is_v2 = schema == "hepta.economic-calendar.v2"
    is_v3 = schema == "hepta.economic-calendar.v3"
    if is_v2 or is_v3:
        require_exact_fields(
            document, CALENDAR_V3_FIELDS if is_v3 else CALENDAR_V2_FIELDS,
            "CONTEXT_CALENDAR_FIELDS_INVALID")
        expected_body_sha256 = require_digest(
            document["body_sha256"], "CONTEXT_CALENDAR_DIGEST_INVALID")
        body = dict(document)
        body.pop("body_sha256")
        if digest_bytes(canonical_bytes(body)) != expected_body_sha256:
            raise ContractError("CONTEXT_CALENDAR_DIGEST_INVALID")
        sources, source_digests = _provenance_sources(
            document["sources"], evaluated_at_ms,
            "CONTEXT_CALENDAR_SOURCES_INVALID")
        if is_v3:
            try:
                evidence_normalizer.validate_output_attestation(
                    document,
                    semantic_field="events",
                    evaluated_at_ms=evaluated_at_ms,
                )
            except ContractError as error:
                raise ContractError(
                    "CONTEXT_CALENDAR_ATTESTATION_INVALID") from error
    else:
        require_exact_fields(
            document, CALENDAR_V1_FIELDS,
            "CONTEXT_CALENDAR_FIELDS_INVALID")
        sources = []
        source_digests = set()
    if schema not in {
            "hepta.economic-calendar.v1",
            "hepta.economic-calendar.v2",
            "hepta.economic-calendar.v3",
    }:
        raise ContractError("CONTEXT_CALENDAR_SCHEMA_INVALID")
    provider = require_text(
        document["provider"], "CONTEXT_CALENDAR_PROVIDER_INVALID")
    source_ref = require_text(
        document["source_ref"], "CONTEXT_CALENDAR_SOURCE_INVALID")
    observed_at_ms = require_int(
        document["observed_at_ms"], "CONTEXT_CALENDAR_TIME_INVALID",
        minimum=0, maximum=evaluated_at_ms)
    if any(
            source["retrieved_at_ms"] > observed_at_ms
            for source in sources):
        raise ContractError("CONTEXT_CALENDAR_SOURCES_INVALID")
    events = document["events"]
    if not isinstance(events, list):
        raise ContractError("CONTEXT_CALENDAR_EVENTS_INVALID")
    normalized: list[dict[str, Any]] = []
    relevant_deltas: list[int] = []
    for value in events:
        event = require_exact_fields(
            value, EVENT_V2_FIELDS if (is_v2 or is_v3) else EVENT_V1_FIELDS,
            "CONTEXT_CALENDAR_EVENT_FIELDS_INVALID")
        event_id = require_text(
            event["event_id"], "CONTEXT_CALENDAR_EVENT_ID_INVALID",
            identifier=True)
        currencies = event["currencies"]
        if (
                not isinstance(currencies, list) or
                not currencies or
                any(currency not in {"EUR", "USD"} for currency in currencies)):
            raise ContractError("CONTEXT_CALENDAR_CURRENCY_INVALID")
        if event["importance"] not in {"low", "medium", "high"}:
            raise ContractError("CONTEXT_CALENDAR_IMPORTANCE_INVALID")
        scheduled_at_ms = require_int(
            event["scheduled_at_ms"], "CONTEXT_CALENDAR_EVENT_TIME_INVALID",
            minimum=0)
        require_digest(
            event["title_sha256"], "CONTEXT_CALENDAR_TITLE_DIGEST_INVALID")
        normalized_event = {
            "event_id": event_id,
            "currencies": list(currencies),
            "importance": event["importance"],
            "scheduled_at_ms": scheduled_at_ms,
            "title_sha256": event["title_sha256"],
        }
        if is_v2 or is_v3:
            source_content_sha256 = require_digest(
                event["source_content_sha256"],
                "CONTEXT_CALENDAR_EVENT_SOURCE_INVALID")
            if source_content_sha256 not in source_digests:
                raise ContractError("CONTEXT_CALENDAR_EVENT_SOURCE_INVALID")
            normalized_event["source_content_sha256"] = source_content_sha256
        normalized.append(normalized_event)
        if event["importance"] == "high":
            relevant_deltas.append(scheduled_at_ms - evaluated_at_ms)
    maximum_age = config["freshness_limits"]["economic_calendar_age_ms"]
    source_fresh = (
        not (is_v2 or is_v3) or
        all(
            evaluated_at_ms - source["retrieved_at_ms"] <= maximum_age
            for source in sources
        )
    )
    present = (
        evaluated_at_ms - observed_at_ms <= maximum_age and source_fresh)
    nearest = (
        min(relevant_deltas, key=lambda value: abs(value))
        if relevant_deltas else None
    )
    setup = config["setup"]
    active = any(
        -setup["event_exclusion_after_seconds"] * 1000 <= delta <=
        setup["event_exclusion_before_seconds"] * 1000
        for delta in relevant_deltas
    )
    return {
        "schema": schema,
        "present": present,
        "provenance_provable": is_v3 and source_fresh,
        "provider": provider,
        "source_ref": source_ref,
        "file_sha256": document_digest,
        "observed_at_ms": observed_at_ms,
        "sources": sources,
        "events": normalized,
        "high_impact_event_window_active": active,
        "nearest_high_impact_delta_seconds": (
            None if nearest is None else nearest / 1000.0),
    }


def _information_document(
    path: Path | None,
) -> tuple[dict[str, Any] | None, str | None]:
    if path is None:
        return None, None
    return load_document(path, "CONTEXT_INFORMATION"), digest_file(path)


def _information(
    document: dict[str, Any] | None,
    document_digest: str | None,
    config: dict[str, Any],
    evaluated_at_ms: int,
) -> dict[str, Any]:
    if document is None:
        return {
            "schema": None,
            "present": False,
            "provenance_provable": False,
            "provider": None,
            "source_ref": None,
            "file_sha256": None,
            "observed_at_ms": None,
            "sources": [],
            "items": [],
            "conflicts": [],
        }
    schema = document.get("schema")
    is_v2 = schema == "hepta.market-information-items.v2"
    is_v3 = schema == "hepta.market-information-items.v3"
    if is_v2 or is_v3:
        require_exact_fields(
            document,
            INFORMATION_V3_FIELDS if is_v3 else INFORMATION_V2_FIELDS,
            "CONTEXT_INFORMATION_FIELDS_INVALID")
        expected_body_sha256 = require_digest(
            document["body_sha256"], "CONTEXT_INFORMATION_DIGEST_INVALID")
        body = dict(document)
        body.pop("body_sha256")
        if digest_bytes(canonical_bytes(body)) != expected_body_sha256:
            raise ContractError("CONTEXT_INFORMATION_DIGEST_INVALID")
        sources, source_digests = _provenance_sources(
            document["sources"], evaluated_at_ms,
            "CONTEXT_INFORMATION_SOURCES_INVALID")
        if is_v3:
            try:
                evidence_normalizer.validate_output_attestation(
                    document,
                    semantic_field="items",
                    evaluated_at_ms=evaluated_at_ms,
                )
            except ContractError as error:
                raise ContractError(
                    "CONTEXT_INFORMATION_ATTESTATION_INVALID") from error
    else:
        require_exact_fields(
            document, INFORMATION_V1_FIELDS,
            "CONTEXT_INFORMATION_FIELDS_INVALID")
        sources = []
        source_digests = set()
    if schema not in {
            "hepta.market-information-items.v1",
            "hepta.market-information-items.v2",
            "hepta.market-information-items.v3",
    }:
        raise ContractError("CONTEXT_INFORMATION_SCHEMA_INVALID")
    provider = require_text(
        document["provider"], "CONTEXT_INFORMATION_PROVIDER_INVALID")
    source_ref = require_text(
        document["source_ref"], "CONTEXT_INFORMATION_SOURCE_INVALID")
    observed_at_ms = require_int(
        document["observed_at_ms"], "CONTEXT_INFORMATION_TIME_INVALID",
        minimum=0, maximum=evaluated_at_ms)
    if any(
            source["retrieved_at_ms"] > observed_at_ms
            for source in sources):
        raise ContractError("CONTEXT_INFORMATION_SOURCES_INVALID")
    items = document["items"]
    if not isinstance(items, list):
        raise ContractError("CONTEXT_INFORMATION_ITEMS_INVALID")
    normalized: list[dict[str, Any]] = []
    conflict_counts: dict[str, int] = {}
    for value in items:
        item = require_exact_fields(
            value, ITEM_V2_FIELDS if (is_v2 or is_v3) else ITEM_V1_FIELDS,
            "CONTEXT_INFORMATION_ITEM_FIELDS_INVALID")
        item_id = require_text(
            item["item_id"], "CONTEXT_INFORMATION_ITEM_ID_INVALID",
            identifier=True)
        published_at_ms = require_int(
            item["published_at_ms"], "CONTEXT_INFORMATION_PUBLISHED_INVALID",
            minimum=0, maximum=evaluated_at_ms)
        item_observed_at_ms = require_int(
            item["observed_at_ms"], "CONTEXT_INFORMATION_OBSERVED_INVALID",
            minimum=published_at_ms, maximum=evaluated_at_ms)
        require_digest(
            item["content_sha256"], "CONTEXT_INFORMATION_DIGEST_INVALID")
        confidence = require_number(
            item["confidence"], "CONTEXT_INFORMATION_CONFIDENCE_INVALID",
            minimum=0.0, maximum=1.0)
        currencies = item["currencies"]
        if (
                not isinstance(currencies, list) or
                any(currency not in {"EUR", "USD"} for currency in currencies)):
            raise ContractError("CONTEXT_INFORMATION_CURRENCY_INVALID")
        conflict_group = item["conflict_group"]
        if conflict_group is not None:
            require_text(
                conflict_group, "CONTEXT_INFORMATION_CONFLICT_INVALID",
                identifier=True)
            conflict_counts[conflict_group] = (
                conflict_counts.get(conflict_group, 0) + 1)
        normalized_item = {
            "item_id": item_id,
            "published_at_ms": published_at_ms,
            "observed_at_ms": item_observed_at_ms,
            "content_sha256": item["content_sha256"],
            "confidence": confidence,
            "currencies": list(currencies),
            "conflict_group": conflict_group,
        }
        if is_v2 or is_v3:
            source_content_sha256 = require_digest(
                item["source_content_sha256"],
                "CONTEXT_INFORMATION_ITEM_SOURCE_INVALID")
            if source_content_sha256 not in source_digests:
                raise ContractError("CONTEXT_INFORMATION_ITEM_SOURCE_INVALID")
            normalized_item["source_content_sha256"] = source_content_sha256
        normalized.append(normalized_item)
    maximum_age = config["freshness_limits"]["information_observation_age_ms"]
    source_fresh = (
        not (is_v2 or is_v3) or
        all(
            evaluated_at_ms - source["retrieved_at_ms"] <= maximum_age
            for source in sources
        )
    )
    present = (
        evaluated_at_ms - observed_at_ms <= maximum_age and source_fresh)
    conflicts = sorted(
        group for group, count in conflict_counts.items() if count > 1)
    return {
        "schema": schema,
        "present": present,
        "provenance_provable": is_v3 and source_fresh,
        "provider": provider,
        "source_ref": source_ref,
        "file_sha256": document_digest,
        "observed_at_ms": observed_at_ms,
        "sources": sources,
        "items": normalized,
        "conflicts": conflicts,
    }


def _ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    series = [values[0]]
    for value in values[1:]:
        series.append(alpha * value + (1.0 - alpha) * series[-1])
    return series


def _features(
    raw_quotes: list[dict[str, Any]],
    resampled_quotes: list[dict[str, Any]],
    bars: list[dict[str, float | int]],
    config: dict[str, Any],
) -> dict[str, float | str]:
    mids = [float(quote["mid"]) for quote in resampled_quotes]
    spreads = [float(quote["spread_bps"]) for quote in raw_quotes]
    latest_mid = mids[-1]
    window_return = (
        0.0 if len(mids) < 2 else
        (latest_mid - mids[0]) / mids[0] * 10000.0
    )
    confirmation_steps = (
        config["feature_windows"]["quote_confirmation_seconds"] //
        config["feature_windows"]["quote_resample_seconds"]
    )
    confirmation_index = max(0, len(mids) - 1 - confirmation_steps)
    confirmation_anchor = mids[confirmation_index]
    confirmation_return = (
        0.0 if confirmation_anchor == 0.0 else
        (latest_mid - confirmation_anchor) / confirmation_anchor * 10000.0
    )
    step_returns = [
        (current - previous) / previous * 10000.0
        for previous, current in zip(mids, mids[1:])
    ]
    step_volatility = (
        statistics.pstdev(step_returns) if len(step_returns) >= 2 else 0.0
    )

    closes = [float(bar["close"]) for bar in bars]
    windows = config["feature_windows"]
    if closes:
        fast_series = _ema_series(closes, windows["fast_ema_bars"])
        slow_series = _ema_series(closes, windows["slow_ema_bars"])
        fast_ema = fast_series[-1]
        slow_ema = slow_series[-1]
        slope_index = max(0, len(fast_series) - 1 - windows["slope_lookback_bars"])
        fast_slope = (
            (fast_ema - fast_series[slope_index]) /
            fast_series[slope_index] * 10000.0
        )
    else:
        fast_ema = latest_mid
        slow_ema = latest_mid
        fast_slope = 0.0
    ema_separation = (fast_ema - slow_ema) / latest_mid * 10000.0

    true_ranges: list[float] = []
    previous_close: float | None = None
    for bar in bars:
        high_price = float(bar["high"])
        low_price = float(bar["low"])
        ranges = [high_price - low_price]
        if previous_close is not None:
            ranges.extend([
                abs(high_price - previous_close),
                abs(low_price - previous_close),
            ])
        true_ranges.append(max(ranges))
        previous_close = float(bar["close"])
    atr_window = true_ranges[-windows["atr_bars"]:]
    atr_price = statistics.fmean(atr_window) if atr_window else 0.0
    atr_bps = atr_price / latest_mid * 10000.0

    breakout_window = bars[-windows["breakout_bars"]:]
    if breakout_window:
        prior_high = max(float(bar["high"]) for bar in breakout_window)
        prior_low = min(float(bar["low"]) for bar in breakout_window)
        breakout_up = (latest_mid - prior_high) / latest_mid * 10000.0
        breakout_down = (prior_low - latest_mid) / latest_mid * 10000.0
    else:
        breakout_up = 0.0
        breakout_down = 0.0
    less_or_equal = sum(spread <= spreads[-1] for spread in spreads)
    spread_percentile = less_or_equal / len(spreads)
    return {
        "calculation_version": config["feature_calculation_version"],
        "quote_lookback_seconds":
            config["feature_windows"]["quote_lookback_seconds"],
        "quote_resample_seconds":
            config["feature_windows"]["quote_resample_seconds"],
        "window_return_bps": window_return,
        "confirmation_return_bps": confirmation_return,
        "step_volatility_bps": step_volatility,
        "ema_fast": fast_ema,
        "ema_slow": slow_ema,
        "ema_separation_bps": ema_separation,
        "ema_fast_slope_bps": fast_slope,
        "atr_bps": atr_bps,
        "breakout_up_bps": breakout_up,
        "breakout_down_bps": breakout_down,
        "spread_percentile": spread_percentile,
    }


def _session(evaluated_at_ms: int) -> str:
    hour = datetime.fromtimestamp(
        evaluated_at_ms / 1000.0, tz=timezone.utc).hour
    if 7 <= hour < 12:
        return "london"
    if 12 <= hour < 16:
        return "london_new_york_overlap"
    if 16 <= hour < 21:
        return "new_york"
    if 21 <= hour < 22:
        return "rollover"
    return "asia"


def _portfolio(snapshot: dict[str, Any]) -> dict[str, float | int]:
    reads = snapshot["reads"]
    positions = reads["portfolio.list_positions"]["positions"]
    position_quantity = 0.0
    for position in positions:
        if not isinstance(position, dict):
            raise ContractError("CONTEXT_POSITION_INVALID")
        position_quantity += require_number(
            position.get("quantity"), "CONTEXT_POSITION_QUANTITY_INVALID")
    active_orders = reads["orders.list"]["active_order_ids"]
    gross_exposure = require_number(
        reads["risk.get_limits"]["gross_absolute_position"],
        "CONTEXT_GROSS_EXPOSURE_INVALID", minimum=0.0)
    return {
        "position_quantity": position_quantity,
        "active_order_count": len(active_orders),
        "gross_exposure": gross_exposure,
    }


def _portfolio_fresh(
    snapshot: dict[str, Any],
    config: dict[str, Any],
    evaluated_at_ms: int,
) -> tuple[bool, int]:
    age_ms = evaluated_at_ms - snapshot["generated_at_ms"]
    if snapshot["schema"] != "hepta.shadow-watch-snapshot.v2":
        return False, age_ms
    oldest_age = evaluated_at_ms - snapshot["collection_started_at_ms"]
    maximum_age = config["freshness_limits"]["portfolio_snapshot_age_ms"]
    return 0 <= oldest_age <= maximum_age, oldest_age


def build_packet(
    *,
    campaign_id: str,
    iteration: int,
    evaluated_at_ms: int,
    strategy_path: Path,
    snapshot_path: Path,
    quote_history_path: Path | None = None,
    bar_history_path: Path | None = None,
    calendar_path: Path | None = None,
    information_path: Path | None = None,
) -> dict[str, Any]:
    require_text(campaign_id, "CONTEXT_CAMPAIGN_ID_INVALID", identifier=True)
    require_int(iteration, "CONTEXT_ITERATION_INVALID", minimum=1)
    require_int(evaluated_at_ms, "CONTEXT_EVALUATED_TIME_INVALID", minimum=0)
    config = load_strategy(strategy_path)
    package_sha256 = strategy_package_digest(strategy_path)
    snapshot = load_document(snapshot_path, "CONTEXT_SNAPSHOT")
    _validate_snapshot(snapshot)
    _validate_snapshot_reads(snapshot)
    if snapshot["generated_at_ms"] > evaluated_at_ms:
        raise ContractError("CONTEXT_SNAPSHOT_FROM_FUTURE")

    quote_document, quote_digest = _quote_document(
        quote_history_path, snapshot)
    quote_timeline, quote_metadata = _validate_quotes(
        quote_document, config, evaluated_at_ms)
    quotes = _independent_quotes(quote_timeline, quote_metadata)
    resampled_quotes = _resample_quotes(quotes, quote_metadata, config)
    snapshot_quote = snapshot["reads"]["market.get_quote"]
    latest_quote = quote_timeline[-1]
    if (
            latest_quote["observed_at_ms"] != snapshot_quote["observed_at_ms"] or
            not math.isclose(
                latest_quote["bid"], float(snapshot_quote["bid"]),
                rel_tol=0.0, abs_tol=1e-12) or
            not math.isclose(
                latest_quote["ask"], float(snapshot_quote["ask"]),
                rel_tol=0.0, abs_tol=1e-12)):
        raise ContractError("CONTEXT_QUOTE_SNAPSHOT_MISMATCH")
    if quote_metadata["provenance_provable"] is True:
        latest_provenance = quote_timeline[-1]
        if (
                latest_provenance["source_snapshot_body_sha256"] !=
                snapshot["body_sha256"] or
                latest_provenance["catalog_sha256"] !=
                snapshot["catalog_sha256"] or
                latest_provenance["descriptor_sha256"] !=
                snapshot["descriptor_sha256"]["market.get_quote"] or
                latest_provenance["execution_service_epoch"] !=
                snapshot["reads"]["system.get_health"][
                    "execution_service_epoch"] or
                latest_provenance[
                    "execution_service_fencing_generation"] !=
                snapshot["reads"]["system.get_health"][
                    "execution_service_fencing_generation"]):
            raise ContractError("CONTEXT_LATEST_QUOTE_PROVENANCE_MISMATCH")

    bar_document, bar_digest = _bar_document(bar_history_path)
    bars, bar_metadata = _validate_bars(
        bar_document, config, evaluated_at_ms)
    calendar_document, calendar_digest = _calendar_document(calendar_path)
    calendar = _calendar(
        calendar_document, calendar_digest, config, evaluated_at_ms)
    information_document, information_digest = _information_document(
        information_path)
    information = _information(
        information_document, information_digest, config, evaluated_at_ms)
    portfolio_fresh, portfolio_age_ms = _portfolio_fresh(
        snapshot, config, evaluated_at_ms)
    quote_age_ms = evaluated_at_ms - latest_quote["observed_at_ms"]
    quote_fresh = (
        latest_quote["stale"] is False and
        quote_age_ms <= config["freshness_limits"]["quote_age_ms"] and
        evaluated_at_ms <= latest_quote["stale_after_ms"]
    )

    script_root = Path(__file__).resolve().parent
    evaluator_path = script_root / "hepta_eurusd_confirmed_momentum_strategy.py"
    normalizer_path = script_root / "hepta_market_evidence_normalizer.py"
    contracts_path = script_root / "hepta_strategy_contracts.py"
    evidence_refs = [
        digest_file(snapshot_path),
        quote_digest,
        digest_file(strategy_path),
        digest_file(Path(__file__).resolve()),
        digest_file(evaluator_path),
        digest_file(normalizer_path),
        digest_file(contracts_path),
    ]
    for digest in (bar_digest, calendar_digest, information_digest):
        if digest is not None:
            evidence_refs.append(digest)
    packet_seed = {
        "campaign_id": campaign_id,
        "iteration": iteration,
        "evaluated_at_ms": evaluated_at_ms,
        "evidence_refs": evidence_refs,
        "strategy_sha256": package_sha256,
    }
    packet_id = "packet-" + digest_bytes(canonical_bytes(packet_seed))[7:39]
    quote_span_seconds = (
        0.0 if len(resampled_quotes) < 2 else
        (
            resampled_quotes[-1]["observed_at_ms"] -
            resampled_quotes[0]["observed_at_ms"]
        ) / 1000.0
    )
    reads = snapshot["reads"]
    features = _features(quotes, resampled_quotes, bars, config)
    packet_body = {
        "schema": "hepta.market-information-packet.v1",
        "packet_id": packet_id,
        "campaign_id": campaign_id,
        "iteration": iteration,
        "mode": "SHADOW",
        "created_at_ms": evaluated_at_ms,
        "evaluated_at_ms": evaluated_at_ms,
        "instrument": "EUR.USD",
        "strategy": {
            "strategy_id": config["strategy_id"],
            "strategy_version": config["strategy_version"],
            "pinned_sha256": package_sha256,
            "config_sha256": digest_file(strategy_path),
            "evaluator_sha256": digest_file(evaluator_path),
            "builder_sha256": digest_file(Path(__file__).resolve()),
            "normalizer_sha256": digest_file(normalizer_path),
            "contracts_sha256": digest_file(contracts_path),
            "sha256_verified": True,
        },
        "context_builder": {
            "schema": "hepta.market-context-builder.v3",
            "builder_sha256": digest_file(Path(__file__).resolve()),
            "normalizer_sha256": digest_file(normalizer_path),
            "contracts_sha256": digest_file(contracts_path),
            "feature_calculation_version":
                config["feature_calculation_version"],
        },
        "source_snapshot": {
            "schema": snapshot["schema"],
            "file_sha256": digest_file(snapshot_path),
            "body_sha256": snapshot["body_sha256"],
            "catalog_sha256": snapshot["catalog_sha256"],
            "generated_at_ms": snapshot["generated_at_ms"],
            "domain_id": snapshot["domain_id"],
            "agent_uid": snapshot["agent_uid"],
            "mutation_attempted": False,
            "direct_broker_access": False,
        },
        "authority": {
            "health_authoritative":
                reads["system.get_health"]["remote_execution_ready"] is True,
            "quote_authoritative":
                reads["market.get_quote"]["authoritative"] is True,
            "account_authoritative":
                reads["account.get_summary"]["authoritative"] is True,
            "positions_authoritative":
                reads["portfolio.list_positions"]["authoritative"] is True,
            "orders_authoritative":
                reads["orders.list"]["authoritative"] is True,
            "risk_authoritative":
                reads["risk.get_limits"]["authoritative"] is True,
            "paper_authorized": False,
            "live_authorized": False,
        },
        "freshness": {
            "quote_observed_at_ms": latest_quote["observed_at_ms"],
            "quote_stale_after_ms": latest_quote["stale_after_ms"],
            "quote_age_ms": quote_age_ms,
            "quote_fresh": quote_fresh,
            "portfolio_snapshot_age_ms": portfolio_age_ms,
            "portfolio_freshness_provable": portfolio_fresh,
            "bar_age_ms": bar_metadata["age_ms"],
            "bar_fresh": bar_metadata["fresh"],
        },
        "provenance": {
            "market_provider": quote_document["provider"],
            "market_source_ref": quote_document["source_ref"],
            "bar_provider": (
                None if bar_document is None else bar_document["provider"]),
            "bar_source_ref": (
                None if bar_document is None else bar_document["source_ref"]),
            "information_provenance_present":
                information["present"] and
                information["provenance_provable"],
            "calendar_provenance_present":
                calendar["present"] and
                calendar["provenance_provable"],
            "descriptor_hashes_present": True,
        },
        "market": {
            "bid": latest_quote["bid"],
            "ask": latest_quote["ask"],
            "mid": latest_quote["mid"],
            "spread_bps": latest_quote["spread_bps"],
        },
        "session": {
            "name": _session(evaluated_at_ms),
            "timezone": "UTC",
        },
        "history": {
            "quote_schema": quote_metadata["schema"],
            "raw_quote_observations": len(quotes),
            "resampled_quote_observations": len(resampled_quotes),
            "quote_provenance_provable":
                quote_metadata["provenance_provable"],
            "quote_complete": quote_metadata["complete"],
            "quote_cadence_ms": quote_metadata["cadence_ms"],
            "quote_maximum_gap_ms": quote_metadata["maximum_gap_ms"],
            "bar_schema": bar_metadata["schema"],
            "bar_observations": len(bars),
            "bar_provenance_provable":
                bar_metadata["provenance_provable"],
            "bar_complete": bar_metadata["complete"],
            "bar_interval_ms": bar_metadata["interval_ms"],
            "span_seconds": quote_span_seconds,
        },
        "features": features,
        "economic_calendar": calendar,
        "information": information,
        "portfolio": _portfolio(snapshot),
        "service": {
            "execution_service_epoch":
                reads["system.get_health"]["execution_service_epoch"],
            "execution_service_fencing_generation":
                reads["system.get_health"][
                    "execution_service_fencing_generation"],
        },
        "privacy": {
            "account_identifiers_included": False,
            "tokens_included": False,
        },
        "evidence_refs": evidence_refs,
    }
    return {
        **packet_body,
        "body_sha256": digest_bytes(canonical_bytes(packet_body)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--iteration", type=int, required=True)
    parser.add_argument("--evaluated-at-ms", type=int, required=True)
    parser.add_argument("--strategy", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--quote-history", type=Path)
    parser.add_argument("--bar-history", type=Path)
    parser.add_argument("--economic-calendar", type=Path)
    parser.add_argument("--information", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        packet = build_packet(
            campaign_id=arguments.campaign_id,
            iteration=arguments.iteration,
            evaluated_at_ms=arguments.evaluated_at_ms,
            strategy_path=arguments.strategy,
            snapshot_path=arguments.snapshot,
            quote_history_path=arguments.quote_history,
            bar_history_path=arguments.bar_history,
            calendar_path=arguments.economic_calendar,
            information_path=arguments.information,
        )
        atomic_write_json(arguments.output, packet)
    except (ContractError, OSError, ValueError) as error:
        print("hepta_market_context_builder: FAIL: " + str(error), file=sys.stderr)
        return 78
    print(
        "hepta_market_context_builder: PASS "
        f"{packet['packet_id']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
