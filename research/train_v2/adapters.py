"""Read-only adapters; never upgrade revised history to a causal quote."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .contracts import (
    CAUSAL, EXPLORATORY, LIVE, OBS_SCHEMA, REVISED, SPEC_SCHEMA,
    BROKER_EXPIRY_POLICY, BROKER_EXPIRY_PROFILE,
    ContractError, digest, is_digest, validate_spec,
)


def make_spec(period_sha256, behavior_policy_id, *, historical=False,
              horizon_ms=300000, lookback=6, broker_reported_expiry=False):
    if broker_reported_expiry and historical:
        raise ContractError("BROKER_EXPIRY_NOT_FOR_REVISED_HISTORY")
    spec = {
        "schema": SPEC_SCHEMA,
        "instrument": "EUR.USD",
        "period_sha256": period_sha256,
        "behavior_policy_id": behavior_policy_id,
        "feature_profile": (BROKER_EXPIRY_PROFILE if broker_reported_expiry
                            else "EURUSD_BID_ASK_MID_RETURNS_V1"),
        "fields": ["mid", "spread_bps", "return_1_bps", "return_window_bps"],
        "source_kind": REVISED if historical else LIVE,
        "mode": EXPLORATORY if historical else CAUSAL,
        "lookback": lookback,
        "max_gap_ms": 300000 if historical else 30000,
        "max_age_ms": 300000 if historical else 5000,
        "horizon_ms": horizon_ms,
        "label_tolerance_ms": 300000 if historical else 5000,
        "trade_authority": False,
        "promotion_allowed": False,
        "actor_update": False,
    }
    if broker_reported_expiry:
        spec["quote_expiry_policy"] = BROKER_EXPIRY_POLICY
    return validate_spec(spec)


def sealed_live_segment(path, spec):
    """Consume prevalidated PAPER source-chain records, not a broker connection.

    The old reader verifies source ownership, quote expiry at collection time,
    monotonic hash-chain, period identity and no trading operations. It does
    not return the broker's original expiry, so the new adapter conservatively
    allows the quote only at its already witnessed receipt instant.
    """
    validate_spec(spec)
    if spec["source_kind"] != LIVE or spec.get("quote_expiry_policy") is not None:
        raise ContractError("LIVE_ADAPTER_SCOPE")
    from laya_cell.shadow_source import read_source
    header, quotes, prefix = read_source(Path(path))
    if (header.get("period_sha256") != spec["period_sha256"]
            or header.get("instrument") != spec["instrument"]
            or header.get("broker_orders_sent") != 0
            or header.get("write_operations_allowed") != []):
        raise ContractError("LIVE_ADAPTER_HEADER")
    if prefix["uncommitted_tail_bytes"]:
        raise ContractError("LIVE_ADAPTER_UNCOMMITTED_SUFFIX")
    if not is_digest(prefix["sha256"]) or len(quotes) > 100000:
        raise ContractError("LIVE_ADAPTER_PREFIX")
    records = []
    for quote in quotes:
        t, known = quote["t"], quote["known_at_ms"]
        records.append({
            "schema": OBS_SCHEMA,
            "instrument": spec["instrument"],
            "period_sha256": spec["period_sha256"],
            "source_kind": LIVE,
            "source_id": quote["receipt"],
            # A source-available event is witnessed by its own causal chain
            # receipt, not by a segment hash that might include future rows.
            "source_sha256": quote["receipt"],
            "event_at_ms": t,
            "available_at_ms": known,
            "valid_until_ms": known,
            "bid": str(quote["bid"]),
            "ask": str(quote["ask"]),
        })
    return records, {
        "source_profile": LIVE,
        "prefix_sha256": prefix["sha256"],
        "chain_head": prefix["chain_head"],
        "complete_prefix_bytes": prefix["bytes"],
        "locally_verified_not_broker_signed": True,
        "broker_queries_sent": 0,
        "broker_orders_sent": 0,
    }


def sealed_revised_ib_bars(dataset_path, coverage_path, source_root, spec, *, last_rows=256):
    """Only an IB-revised-history research adapter, never historical live facts.

    Acquisition availability comes from verified immutable broker receipt
    timestamps, not a caller-chosen early timestamp or the bar date.
    """
    validate_spec(spec)
    if spec["source_kind"] != REVISED or type(last_rows) is not int or not 1 <= last_rows <= 512:
        raise ContractError("HISTORICAL_ADAPTER_SCOPE")
    from historical_training.common import read, sha
    dataset_path, coverage_path, source_root = Path(dataset_path), Path(coverage_path), Path(source_root)
    for path in (dataset_path, coverage_path, source_root):
        if path.is_symlink():
            raise ContractError("HISTORICAL_SYMLINK")
    data = read(dataset_path)
    coverage = read(coverage_path)
    if (data.get("schema") != "heptatrader.historical-paired-bars.v1"
            or coverage.get("schema") != "heptatrader.historical-coverage.v1"
            or not coverage.get("full_requested_coverage_proven")
            or not coverage.get("revised_historical_data_not_contemporaneous")
            or coverage.get("scheduled_missing_count") != 0
            or coverage.get("bar_count") != len(data.get("bars", []))
            or data.get("coverage") != coverage
            or data.get("source_plan_sha256") != sha(source_root / "plan.json")):
        raise ContractError("HISTORICAL_DATASET_NOT_VERIFIED")
    if data.get("trade_authority") is not False or data.get("actual_execution_rewards") != 0:
        raise ContractError("HISTORICAL_DATASET_EFFECT_FORBIDDEN")
    acquired = []
    sources = data.get("sources", [])
    if not isinstance(sources, list) or len(sources) < 3 or len(sources) > 36:
        raise ContractError("HISTORICAL_SOURCES_MISSING")
    for witness in sources:
        key = witness.get("request_key")
        if not is_digest(key):
            raise ContractError("HISTORICAL_SOURCE_ID")
        filename = source_root / "chunks" / key / "receipt.json"
        if filename.is_symlink() or not filename.is_file() or sha(filename) != witness.get("receipt_sha256"):
            raise ContractError("HISTORICAL_SOURCE_CHANGED")
        receipt = read(filename, 1024 * 1024)
        if receipt.get("state") != "RECEIVED" or receipt.get("response_sha256") != witness.get("response_sha256"):
            raise ContractError("HISTORICAL_RESPONSE_NOT_RECEIVED")
        at = receipt.get("received_at_ms")
        if type(at) is not int or at <= 0:
            raise ContractError("HISTORICAL_RECEIPT_TIME")
        acquired.append(at)
    last_received = max(acquired)
    bars = data["bars"][-last_rows:]
    source_sha = sha(dataset_path)
    output = []
    for row in bars:
        begin = row.get("time")
        if type(begin) is not int or begin < 1 or begin % 300 != 0:
            raise ContractError("HISTORICAL_BAR_TIME")
        close = begin * 1000 + 300000
        if close > last_received:
            raise ContractError("HISTORICAL_ACQUISITION_PRECEDES_BAR")
        bid = row["bid"]["close"]
        ask = row["ask"]["close"]
        output.append({
            "schema": OBS_SCHEMA,
            "instrument": spec["instrument"],
            "period_sha256": spec["period_sha256"],
            "source_kind": REVISED,
            "source_id": digest({"source_sha256": source_sha, "bar": row}),
            "source_sha256": source_sha,
            "event_at_ms": close,
            "available_at_ms": last_received,
            "valid_until_ms": last_received,
            "bid": str(bid),
            "ask": str(ask),
        })
    return output, {
        "source_profile": REVISED,
        "source_file_sha256": source_sha,
        "coverage_sha256": sha(coverage_path),
        "broker_receipts_verified": len(acquired),
        "conservative_available_at_ms": last_received,
        "revised_not_contemporaneous": True,
        "past_live_claim": False,
        "broker_queries_sent": 0,
        "broker_orders_sent": 0,
    }
