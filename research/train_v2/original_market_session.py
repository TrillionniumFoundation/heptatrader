"""Read-only IB EUR/USD weekly-session boundary and real quote-chain audit.

Never grants a brokerage market-data entitlement, canonical product identity,
automated watcher, prediction, trade, or financial efficacy. The published
IDEALPRO regular FX session is Sunday 17:15 to Friday 17:00 New York time.
Known weekly/daily closure may explain a missing quote; OPEN is only a
necessary clock condition, never a sufficient quote/authority condition.
"""
from __future__ import annotations

import argparse
from datetime import datetime, time as wall_time, timedelta, timezone
import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from .original_policy_audit import write_once

SCHEMA = "heptatrader.train-v2-local-fx-session-source-audit.v1"
PROFILE = "IBKR_IDEALPRO_REGULAR_HOURS_SUN_1715_FRI_1700_NY_V1"
OFFICIAL_HOURS_URL = "https://www.interactivebrokers.co.uk/en/trading/productsprint.php"
NEW_YORK = ZoneInfo("America/New_York")
MAX_SEGMENTS = 200
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_SOURCE_AGE_MS = 1500
MAX_SPACING_MS = 30000
MIN_HISTORY = 6


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def iso_millis(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat(
        timespec="milliseconds")


def _until_next_regular_open(ny: datetime) -> datetime | None:
    """Compute next regular *calendar* opening only when currently closed."""
    day = ny.weekday()
    t = ny.time().replace(tzinfo=None)
    if day == 5:  # Saturday
        return datetime.combine((ny + timedelta(days=1)).date(),
                                wall_time(17, 15), NEW_YORK)
    if day == 6 and t < wall_time(17, 15):  # Sunday
        return datetime.combine(ny.date(), wall_time(17, 15), NEW_YORK)
    if day == 4 and t >= wall_time(17):  # Friday after 17:00
        return datetime.combine((ny + timedelta(days=2)).date(),
                                wall_time(17, 15), NEW_YORK)
    if day in (0, 1, 2, 3) and wall_time(17) <= t < wall_time(17, 15):
        return datetime.combine(ny.date(), wall_time(17, 15), NEW_YORK)
    return None


def session_clock(when: datetime) -> dict:
    """DST-aware weekly clock profile; never infers a real quote."""
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("TIMEZONE_AWARE_WALL_CLOCK_REQUIRED")
    utc = when.astimezone(timezone.utc)
    ny = utc.astimezone(NEW_YORK)
    next_open = _until_next_regular_open(ny)
    if next_open is None:
        classification = "WITHIN_REGULAR_CALENDAR_WINDOW_NOT_MARKET_AUTHORITY"
    elif ny.weekday() in (5, 6, 4):
        classification = "OUTSIDE_REGULAR_FX_WEEKLY_SESSION"
    else:
        classification = "FX_DAILY_ROLLOVER_GAP"
    return {
        "session_profile": PROFILE,
        "official_hours_url": OFFICIAL_HOURS_URL,
        "checked_at_utc": utc.isoformat(timespec="milliseconds"),
        "checked_at_new_york": ny.isoformat(timespec="milliseconds"),
        "calendar_regular_session_open": next_open is None,
        "classification": classification,
        "next_regular_calendar_open_utc":
            next_open.astimezone(timezone.utc).isoformat(timespec="milliseconds")
            if next_open else None,
        "next_regular_calendar_open_new_york":
            next_open.isoformat(timespec="milliseconds") if next_open else None,
        "calendar_holidays_and_scheduled_outages_not_proven": True,
        "realtime_quote_subscription_admitted_by_calendar": False,
        "trade_authority": False,
    }


def _config(config_path: Path) -> dict:
    from hepta_decision_attribution import read_private, strict
    cfg = Path(config_path)
    if not cfg.is_absolute() or cfg.is_symlink():
        raise ValueError("LOCAL_PAPER_CONFIG_PATH_UNSAFE")
    doc = strict(read_private(cfg, 65536))
    period = Path(doc.get("period_path", ""))
    root = Path(doc.get("feed_directory", ""))
    if (doc.get("schema") != "heptatrader.laya-resident-config.v1"
            or doc.get("automatic_trading") is not False
            or not period.is_absolute() or period.is_symlink()
            or not root.is_absolute() or root.is_symlink()
            or not root.is_dir()):
        raise ValueError("LOCAL_PAPER_FEED_NOT_AUTHORIZED")
    raw_period = read_private(period, 65536)
    if (strict(raw_period).get("execution_domain") != "PAPER"
            or sha(raw_period) != doc.get("period_sha256")):
        raise ValueError("ORIGINAL_PAPER_PERIOD_CHANGED")
    return doc


def _segment_list(feed: Path) -> tuple[list[Path], int]:
    paths = list(feed.glob("segment-*/quotes.jsonl"))
    if not paths:
        raise ValueError("BOUNDED_FEED_SEGMENTS_REQUIRED")
    # An active 30-minute feed can exceed MAX_SEGMENTS before the next
    # Sunday opening. Audit the RECENT bounded window without implying the
    # omitted older archive was scanned. This is diagnostics, not release.
    paths.sort(key=lambda p: (p.stat().st_mtime_ns, str(p)))
    omitted_older = max(0, len(paths) - MAX_SEGMENTS)
    paths = paths[-MAX_SEGMENTS:]
    total = 0
    for p in paths:
        if (not p.is_file() or p.is_symlink()
                or p.parent.is_symlink() or p.parent.parent != feed):
            raise ValueError("SOURCE_PATH_ESCAPE_OR_SYMLINK")
        total += p.stat().st_size
    if total > MAX_TOTAL_BYTES:
        raise ValueError("FULL_SOURCE_BYTES_TOO_LARGE")
    return paths, omitted_older


def _first_failure_after_verified_last_quote(
    file: Path, prefix: dict, last_known_at: int,
) -> dict | None:
    """Reopen and bind actual failure row to independently verified prefix.

    A concurrently growing source may be re-read only at its exact earlier
    verified byte cutoff; mutation to that prefix rejects the entire audit.
    """
    from hepta_decision_attribution import read_private
    from laya_cell.shadow_source import strict

    raw = read_private(file)
    complete = raw[:raw.rfind(b"\n") + 1]
    n = prefix["bytes"]
    if (n > len(raw) or sha(raw[:n]) != prefix["sha256"]
            or (n and not raw[:n].endswith(b"\n"))
            or n > len(complete)):
        raise ValueError("READONLY_VERIFIED_ORIGINAL_PREFIX_CHANGED")
    for line in raw[:n].splitlines():
        row = strict(line)
        if row["kind"] == "FAILURE" and row["at_ms"] > last_known_at:
            return {
                "at_utc": iso_millis(row["at_ms"]),
                "reason_code": row["data"].get("reason"),
                "local_chain_entry_sha256": row["sha256"],
            }
    return None


def inspect(config_path: Path, *, now: datetime | None = None) -> dict:
    """Verified real locally owned quote-chain readback; no extra IB request."""
    from laya_cell.shadow_source import read_source
    current = datetime.now(timezone.utc) if now is None else now
    clock = session_clock(current)
    now_ms = int(current.astimezone(timezone.utc).timestamp() * 1000)
    cfg = _config(config_path)
    feed = Path(cfg["feed_directory"])
    paths, older_segments_omitted = _segment_list(feed)
    latest: dict | None = None
    sources = []
    all_quotes = []
    exact_quote_by_timestamp = {}
    for file in paths:
        header, quotes, prefix = read_source(file)
        if (header.get("period_sha256") != cfg["period_sha256"]
                or header.get("instrument") != "EUR.USD"
                or header.get("source") != "IB_TOOL_GATEWAY"
                or header.get("write_operations_allowed") != []
                or header.get("broker_orders_sent") != 0):
            raise ValueError("CROSS_PERIOD_OR_UNTRUSTED_ORIGINAL_FEED")
        sources.append({
            "source_path_sha256": sha(str(file).encode()),
            "source_prefix_sha256": prefix["sha256"],
            "source_bytes": prefix["bytes"],
            "verified_quote_count": len(quotes),
            "sealed": prefix["ended"],
            "uncommitted_tail_bytes": prefix["uncommitted_tail_bytes"],
        })
        all_quotes.extend(quotes)
        # Source may contain repeated original timestamps across archived
        # segments. Enforce exactly the same cross-source price conflict rule
        # as the unchanged production Laya read_quotes source loader.
        for quote in quotes:
            existing = exact_quote_by_timestamp.get(quote["t"])
            if (existing is not None and
                    (Decimal(str(existing["bid"])) != Decimal(str(quote["bid"]))
                     or Decimal(str(existing["ask"])) != Decimal(str(quote["ask"])))):
                raise ValueError("CROSS_SEGMENT_ORIGINAL_QUOTE_PRICE_CONFLICT")
            if (existing is None
                    or quote["known_at_ms"] < existing["known_at_ms"]):
                exact_quote_by_timestamp[quote["t"]] = quote
        if quotes:
            last = max(quotes, key=lambda v: (v["known_at_ms"], v["receipt"]))
            if latest is None or (last["known_at_ms"], last["receipt"]) > (
                latest["quote"]["known_at_ms"], latest["quote"]["receipt"]
            ):
                latest = {"quote": last, "source": file, "prefix": prefix}

    all_quotes.sort(key=lambda q: (q["t"], q["known_at_ms"], q["receipt"]))
    unique_quotes = [exact_quote_by_timestamp[t]
                     for t in sorted(exact_quote_by_timestamp)]
    count = len(all_quotes)
    status = "HOLD_ORIGINAL_SOURCE_NOT_PROVEN"
    freshest = None
    near_weekly_close = False
    first_failure = None
    if latest:
        quote = latest["quote"]
        age = now_ms - quote["known_at_ms"]
        freshest = {
            "original_observed_at_utc": iso_millis(quote["t"]),
            "original_locally_available_at_utc": iso_millis(quote["known_at_ms"]),
            "local_receipt_sha256": quote["receipt"],
            "source_path_sha256": sha(str(latest["source"]).encode()),
            "prefix_sha256": latest["prefix"]["sha256"],
            "quote_age_ms": age,
        }
        first_failure = _first_failure_after_verified_last_quote(
            latest["source"], latest["prefix"], quote["known_at_ms"])
        observed_et = datetime.fromtimestamp(
            quote["t"] / 1000, timezone.utc).astimezone(NEW_YORK)
        regular_close = datetime.combine(observed_et.date(),
                                         wall_time(17), NEW_YORK)
        close_delta_ms = int(round(
            (observed_et - regular_close).total_seconds() * 1000))
        freshest["new_york_local_time"] = observed_et.isoformat(
            timespec="milliseconds")
        freshest["last_quote_offset_from_1700_friday_close_ms"] = (
            close_delta_ms if observed_et.weekday() == 4 else None)
        if first_failure:
            fail_et = datetime.fromisoformat(
                first_failure["at_utc"]).astimezone(NEW_YORK)
            first_failure["new_york_local_time"] = fail_et.isoformat(
                timespec="milliseconds")
            failure_delta = int(round(
                (fail_et - regular_close).total_seconds() * 1000))
            first_failure["offset_from_1700_friday_close_ms"] = (
                failure_delta if observed_et.weekday() == 4 else None)
            near_weekly_close = (observed_et.weekday() == 4
                                 and abs(close_delta_ms) <= 10000
                                 and abs(failure_delta) <= 10000
                                 and first_failure["reason_code"] ==
                                     "AUTHORITATIVE_QUOTE_UNREADY")

        if (clock["calendar_regular_session_open"] is False):
            status = "HOLD_FX_REGULAR_SESSION_CLOSED"
        elif not 0 <= age <= MAX_SOURCE_AGE_MS:
            status = "HOLD_AUTHORITATIVE_QUOTE_NOT_FRESH"
        else:
            # A quoted price alone is never sufficient for candidacy.
            fresh = [q for q in unique_quotes if q["known_at_ms"] <= now_ms]
            recent = fresh[-MIN_HISTORY:]
            if (len(recent) < MIN_HISTORY
                    or any(b["t"] - a["t"] > MAX_SPACING_MS
                           for a, b in zip(recent, recent[1:]))):
                status = "HOLD_INSUFFICIENT_CONTIGUOUS_MARKET_HISTORY"
            else:
                status = "SOURCE_TIMING_REVIEW_ONLY_NOT_PRODUCT_ACCEPTANCE"
    else:
        status = ("HOLD_FX_REGULAR_SESSION_CLOSED" if not
                  clock["calendar_regular_session_open"]
                  else "HOLD_NO_AUTHENTIC_ORIGINAL_QUOTES")

    return {
        "schema": SCHEMA,
        "original_data_origin": "LOCAL_HASHED_IB_PAPER_SHADOW_FEED",
        "calendar": clock,
        "original_feed_source_count": len(paths),
        "older_segments_outside_bounded_recent_window":
            older_segments_omitted,
        "all_configured_feed_history_verified_in_this_audit":
            older_segments_omitted == 0,
        "source_scope": "RECENT_BOUND_ONLY_WHEN_OLDER_FILES_EXCLUDED",
        "verified_local_quote_count_in_all_segments": count,
        "unique_verified_original_quote_timestamps": len(unique_quotes),
        "cross_segment_quote_price_conflicts": 0,
        "source_witnesses": sources,
        "latest_original_quote": freshest,
        "first_verified_failure_after_last_quote": first_failure,
        "regular_weekly_close_explains_transition_consistently":
            near_weekly_close,
        "status": status,
        "local_source_hash_chain_verified": True,
        "market_data_entitlement_independently_verified": False,
        "no_broker_market_query_added": True,
        "no_broker_order_or_model_inference": True,
        "complete_original_trade_behavior_policy_observed": False,
        "research_new_shadow_decision_emitted": False,
        "source_time_readback_grants_no_product_trust": True,
        "production_generator_and_observer_enrolled": False,
        "native_product_ledger_writer_acked": False,
        "dataset_snapshot_v3_accepted": False,
        "after_cost_forward_efficacy_accepted": False,
        "trade_authority": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    body = inspect(args.config)
    receipt_sha = write_once(args.output, body)
    print(json.dumps({
        "status": body["status"],
        "calendar_classification": body["calendar"]["classification"],
        "original_quotes_verified": body["verified_local_quote_count_in_all_segments"],
        "next_calendar_open_utc": body["calendar"][
            "next_regular_calendar_open_utc"],
        "last_quote_utc": (body["latest_original_quote"] or {}).get(
            "original_observed_at_utc"),
        "quote_to_weekend_failure_match":
            body["regular_weekly_close_explains_transition_consistently"],
        "receipt_sha256": receipt_sha,
        "trading_authority": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
