"""Independent host-local complete numeric-segment earliest-quote review.

No trading, broker API, feature training, original-window mutation or model
promotion. The source *inventory* is local filesystem evidence, not a broker-
signed guarantee that a privileged host could not have removed other segments.
"""
from __future__ import annotations
import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

from .fx_ridge_campaign_independent import (
    AuditReject, SCHEMA, canonical, digest, read_private_root,
    require, run_audit, sha, strict,
)

NUMERIC = re.compile(r"^segment-(\d{16,20})-([a-f0-9]{8})$")
MAX_SEGMENTS = 512
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_SEGMENT_BYTES = 4 * 1024 * 1024


def strict_line(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "GLOBAL_ORIGINAL_DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    return json.loads(
        raw, object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(
            AuditReject("GLOBAL_ORIGINAL_NONFINITE_JSON")))


def numeric_source_inventory(root, maturity_at_ms):
    """Read all original numeric writer segments through an as-of cutoff.

    A numeric segment created later than the frozen maturity attempt is not
    eligible to change an earlier result. Failed/unknown/recovery segments
    do not silently become substitute market data.
    """
    root = read_private_root(root)
    require(type(maturity_at_ms) is int and maturity_at_ms > 0,
            "GLOBAL_ORIGINAL_MATURITY_CLOCK")
    dirs = []
    for p in root.iterdir():
        if not p.is_dir() and not p.is_symlink():
            continue
        if p.name == "segment-recovery-live":
            continue
        if p.name.startswith("segment-"):
            m = NUMERIC.fullmatch(p.name)
            require(m is not None, "GLOBAL_ORIGINAL_UNRECOGNIZED_NUMERIC_SEGMENT")
            start_ns = int(m.group(1))
            if start_ns // 1000000 > maturity_at_ms:
                continue
            require(not p.is_symlink(), "GLOBAL_ORIGINAL_SEGMENT_SYMLINK")
            read_private_root(p)
            dirs.append((start_ns, p))
    require(0 < len(dirs) <= MAX_SEGMENTS,
            "GLOBAL_ORIGINAL_SEGMENT_COUNT_OR_SOURCE_MISSING")
    require(len({n for n, _ in dirs}) == len(dirs),
            "GLOBAL_ORIGINAL_DUPLICATE_NUMERIC_SEGMENT")
    return [p for _, p in sorted(dirs)]


def original_quotes(root, *, due_at_ms, maturity_at_ms, period_sha):
    require(type(due_at_ms) is int and 0 < due_at_ms < maturity_at_ms,
            "GLOBAL_ORIGINAL_DUE_OR_MATURITY_CLOCK")
    require(isinstance(period_sha, str) and len(period_sha) == 64,
            "GLOBAL_ORIGINAL_PERIOD_NOT_SPECIFIED")
    segments = numeric_source_inventory(root, maturity_at_ms)
    found = {}
    inventory = []
    total = 0
    for segment in segments:
        p = segment / "quotes.jsonl"
        require(not p.is_symlink() and p.is_file(),
                "GLOBAL_ORIGINAL_SEGMENT_MISSING_QUOTE_FILE")
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            st = os.fstat(fd)
            require(
                stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid()
                and stat.S_IMODE(st.st_mode) == 0o600
                and 0 < st.st_size <= MAX_SEGMENT_BYTES,
                "GLOBAL_ORIGINAL_QUOTE_CUSTODY_OR_BOUND",
            )
            total += st.st_size
            require(total <= MAX_TOTAL_BYTES,
                    "GLOBAL_ORIGINAL_TOTAL_BYTES_BOUND")
            raw = bytearray()
            while len(raw) < st.st_size:
                chunk = os.read(fd, min(1024 * 1024, st.st_size - len(raw)))
                require(chunk, "GLOBAL_ORIGINAL_QUOTE_SHORT_READ")
                raw.extend(chunk)
        finally:
            os.close(fd)
        # Current appended segment may have a partial trailing row. Future
        # content cannot be used as an already mature source.
        complete = raw[:raw.rfind(b"\n")+1]
        require(complete and complete.endswith(b"\n"),
                "GLOBAL_ORIGINAL_NO_COMPLETE_PREFIX")
        inventory.append({
            "path": str(p),
            "complete_prefix_bytes": len(complete),
            "complete_prefix_sha256": hashlib.sha256(complete).hexdigest(),
        })
        predecessor = "0" * 64
        previous_at = 0
        header = None
        for i, line in enumerate(complete.splitlines()):
            require(len(line) < 65536, "GLOBAL_ORIGINAL_OVERSIZED_JSON_RECORD")
            row = strict_line(line)
            require(
                row.get("schema") == "heptatrader.laya-shadow-quotes.v1"
                and row.get("sequence") == i
                and row.get("previous") == predecessor
                and row.get("sha256") ==
                    digest({k:v for k,v in row.items() if k != "sha256"}),
                "GLOBAL_ORIGINAL_CHAIN_INVALID",
            )
            predecessor = row["sha256"]
            at = row.get("at_ms")
            require(type(at) is int and previous_at <= at,
                    "GLOBAL_ORIGINAL_CLOCK_REGRESSED")
            previous_at = at
            if i == 0:
                header = row.get("data")
                require(
                    row.get("kind") == "HEADER"
                    and isinstance(header, dict)
                    and header.get("execution_domain") == "PAPER"
                    and header.get("instrument") == "EUR.USD"
                    and header.get("period_sha256") == period_sha
                    and header.get("write_operations_allowed") == []
                    and header.get("broker_orders_sent") == 0,
                    "GLOBAL_ORIGINAL_SOURCE_SCOPE",
                )
                continue
            if row.get("kind") != "QUOTE":
                require(row.get("kind") in ("FAILURE", "END"),
                        "GLOBAL_ORIGINAL_UNEXPECTED_RECORD_TYPE")
                continue
            q = row["data"]
            event = q.get("observed_at_ms")
            expiry = q.get("stale_after_ms")
            require(
                type(event) is int and type(expiry) is int
                and 0 < event <= at <= min(event+5000, expiry)
                and q.get("instrument") == "EUR.USD"
                and q.get("source") == "IB"
                and q.get("authoritative") is True
                and q.get("stale") is False,
                "GLOBAL_ORIGINAL_NOT_AUTHORITY_QUALIFIED",
            )
            try:
                bid = Decimal(str(q["bid"]))
                ask = Decimal(str(q["ask"]))
                require(
                    bid.is_finite() and ask.is_finite()
                    and 0 < bid <= ask < Decimal("1e12"),
                    "GLOBAL_ORIGINAL_PRICE_INVALID",
                )
            except (InvalidOperation, ValueError, TypeError, KeyError) as exc:
                raise AuditReject("GLOBAL_ORIGINAL_PRICE_INVALID") from exc
            if due_at_ms <= event <= due_at_ms + 5000 and due_at_ms <= at <= maturity_at_ms:
                old = found.get(event)
                if old is not None:
                    require(
                        old["bid"] == str(bid) and old["ask"] == str(ask),
                        "GLOBAL_ORIGINAL_SAME_TIMESTAMP_PRICE_CONFLICT",
                    )
                candidate = {
                    "event_at_ms": event,
                    "available_at_ms": at,
                    "quote_receipt": row["sha256"],
                    "bid": str(bid), "ask": str(ask),
                }
                if old is None or (at, row["sha256"]) < (
                        old["available_at_ms"], old["quote_receipt"]):
                    found[event] = candidate
    first = min(found.values(), key=lambda q: (
        q["event_at_ms"], q["available_at_ms"], q["quote_receipt"]), default=None)
    return first, {
        "numeric_writer_segments_audited": len(segments),
        "total_original_bytes_read": total,
        "asof_original_inventory_sha256": digest(inventory),
        "filesystem_inventory_not_independent_broker_signature": True,
        "deleted_prior_segments_not_detectable_without_remote_anchor": True,
        "broker_requests_sent": 0,
        "broker_orders_sent": 0,
    }


def require_original_first_maturity(frozen, earliest):
    require(earliest is not None, "GLOBAL_ORIGINAL_NO_MATURE_PRICE_PRESENT")
    require(
        frozen.get("price_event_ms") == earliest["event_at_ms"]
        and frozen.get("price_available_ms") == earliest["available_at_ms"]
        and frozen.get("outcome_quote_sha256") == earliest["quote_receipt"],
        "GLOBAL_ORIGINAL_OMITTED_EARLIER_ELIGIBLE_QUOTE",
    )


def review_campaign(root):
    """Read-only independent second opinion; all four scheduled slots retained."""
    root = read_private_root(root)
    source = run_audit(root)  # independently verifies each submitted prefix first
    intent = strict(root / "campaign-intent.json")
    windows = source["windows"]
    require(len(windows) == 4 and [w["window_index"] for w in windows] == list(range(4)),
            "GLOBAL_ORIGINAL_REVIEW_MUST_RETAIN_ALL_FOUR")
    results = []
    from decimal import Decimal
    for w in windows:
        item = {
            "window_index": w["window_index"],
            "decision_state": w["decision_state"],
            "maturity_state": w["maturity_state"],
            "terminal": w["maturity_state"] in ("CENSORED", "MATURE_PRICE_ONLY"),
            "no_trade_only": True,
        }
        if w["maturity_state"] == "MATURE_PRICE_ONLY":
            cell = root / ("window-%02d" % w["window_index"])
            attempt = strict(cell / "maturity-attempt.json")
            maturity_at = attempt["attempt_recorded_at_ms"]
            frozen = strict(cell / "mature-outcome.json", maximum=128000)
            decision = strict(cell / "preoutcome-decision.json", maximum=128000)
            manifest = strict(cell / "manifest.json")
            earliest, coverage = original_quotes(
                manifest["source_root"],
                due_at_ms=w["outcome_due_ms"],
                maturity_at_ms=maturity_at,
                period_sha=intent["original_period_sha256"],
            )
            require_original_first_maturity(frozen, earliest)
            original = decision["history_quote_samples"][-1]
            input_mid = (Decimal(str(original["bid"])) +
                         Decimal(str(original["ask"]))) / 2
            future_mid = (Decimal(earliest["bid"]) +
                          Decimal(earliest["ask"])) / 2
            real = (future_mid / input_mid - 1) * Decimal(10000)
            forecast = Decimal(str(decision["forecast_midpoint_bps"]))
            error = forecast - real
            assert real.is_finite() and forecast.is_finite()
            item.update({
                "original_mature_quote_globally_first_in_available_numeric_segments": True,
                "selected_original_mature_quote_receipt": earliest["quote_receipt"],
                "observed_price_return_bps": str(real),
                "forecast_price_return_bps": str(forecast),
                "absolute_price_prediction_error_bps": str(abs(error)),
                "zero_price_prediction_absolute_error_bps": str(abs(real)),
                "original_precommitted_no_trade_hurdle_bps":
                    w["decision_original_recomputed"]["minimum_hypothetical_hurdle_bps"],
                "original_model_chose_no_trade": True,
                "original_outcome_economic_reward_attested": False,
                "full_original_file_inventory": coverage,
            })
        results.append(item)
    matured = [w for w in results if w["maturity_state"] == "MATURE_PRICE_ONLY"]
    terminal = sum(w["terminal"] for w in results)
    # Descriptive price-error statistics cannot be a new efficacy or selection
    # test. No alpha threshold may be tuned on this already-inspected cohort.
    summary = {
        "schema": "heptatrader.fx-ridge-global-earliest-review.v1",
        "independent_prefix_replay_report_id": source["report_id"],
        "source_review_status": source["completed_qualification"],
        "campaign_sealed_sha256": source["campaign_sealed_sha256"],
        "original_cost_receipt_sha256": source["original_cost_receipt_sha256"],
        "window_count_including_censored": 4,
        "terminal_windows": terminal,
        "mature_price_only_windows": len(matured),
        "censored_windows": sum(w["maturity_state"] == "CENSORED" for w in results),
        "unfinished_windows": 4-terminal,
        "matured_forecast_absolute_error_bps_sum":
            str(sum((Decimal(w["absolute_price_prediction_error_bps"])
                     for w in matured), Decimal(0))),
        "matured_zero_price_absolute_error_bps_sum":
            str(sum((Decimal(w["zero_price_prediction_absolute_error_bps"])
                     for w in matured), Decimal(0))),
        "windows": results,
        "all_prospectively_scheduled_samples_retained": True,
        "original_numeric_segments_inventory_not_official_broker_signature": True,
        "statistical_effectiveness_qualified": False,
        "executed_broker_fills_or_financing_qualified": False,
        "no_trade_benchmark_is_zero_hypothetical_usd": True,
        "new_statistical_selection_or_threshold_tuning": False,
        "model_automatic_selection": False,
        "trading_authority": False,
        "broker_requests_sent": 0,
        "broker_orders_sent": 0,
        "status": (
            "COMPLETE_FOUR_WINDOWS_PRICE_ONLY_NO_ECONOMIC_ALPHA"
            if terminal == 4 and source["completed_qualification"] ==
                "COMPLETE_PRICE_ONLY_NO_ALPHA_OR_TRADING"
            else "HOLD_UNFINISHED_OR_CENSORED_ORIGINAL_RESEARCH"),
    }
    return {**summary, "review_id": digest(summary)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    os.umask(0o077)
    report = review_campaign(args.root)
    out = args.output
    require(out.is_absolute() and not out.is_symlink() and not out.exists(),
            "GLOBAL_ORIGINAL_IMMUTABLE_OUTPUT_REQUIRED")
    require(not out.is_relative_to(args.root),
            "GLOBAL_ORIGINAL_MAY_NOT_WRITE_PRODUCER_ROOT")
    out.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    require((out.parent.stat().st_mode & 0o077) == 0,
            "GLOBAL_ORIGINAL_OUTPUT_PRIVATE")
    raw = json.dumps(report, sort_keys=True, indent=2).encode() + b"\n"
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(out.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    print(json.dumps({
        "status": report["status"],
        "terminal": report["terminal_windows"],
        "price_only": report["mature_price_only_windows"],
        "censored": report["censored_windows"],
        "unfinished": report["unfinished_windows"],
        "orders": 0,
        "report_sha256": hashlib.sha256(raw).hexdigest(),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
