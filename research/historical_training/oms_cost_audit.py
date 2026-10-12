"""Reconcile **all** validated OMS fills to an immutable PAPER PnL snapshot.

Research-only. Never contacts the broker, replays commands, selects trades,
changes execution policy, or promotes models. The public PnL snapshot displays
only 128 fills for each instrument; cost attribution MUST replay its exact
journal prefix instead of analyzing that truncated display tail.

Usage: PYTHONPATH=scripts:research python -m historical_training.oms_cost_audit
  --snapshot PRIVATE/pnl/snapshot.json --period PRIVATE/pnl-period.json
  --journal PRIVATE/execution/oms-journal.jsonl
  --installed-accounting PRIVATE/observer-releases/HASH/hepta_pnl_accounting.py
  --output PRIVATE/operator-evidence/RESEARCH/oms-cost-audit.json
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import inspect
import json
import os
from pathlib import Path
import stat
import sys

from hepta_pnl_accounting import INSTRUMENTS, replay, validate_period

MAX_JOURNAL_BYTES = 64 * 1024 * 1024
MAX_JOURNAL_RECORDS = 65536

class CostEvidenceError(Exception):
    pass


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise CostEvidenceError(reason)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def strict(raw: bytes):
    def pairs_unique(pairs):
        obj = {}
        for key, value in pairs:
            require(key not in obj, "DUPLICATE_JSON_KEY")
            obj[key] = value
        return obj

    def no_constant(value):
        raise CostEvidenceError("NONFINITE_JSON_NUMBER")

    return json.loads(raw, object_pairs_hook=pairs_unique,
                      parse_constant=no_constant)


def read_private(path: Path, limit: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        require(stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid()
                and st.st_nlink == 1 and not st.st_mode & 0o077
                and 0 < st.st_size <= limit, "PRIVATE_SOURCE_INVALID")
        chunks = []
        remaining = st.st_size
        while remaining:
            raw = os.read(fd, min(1024 * 1024, remaining))
            require(bool(raw), "PRIVATE_SOURCE_SHORT_READ")
            chunks.append(raw)
            remaining -= len(raw)
        after = os.fstat(fd)
        require((st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
                == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
                "PRIVATE_SOURCE_MUTATED_DURING_READ")
        return b"".join(chunks)
    finally:
        os.close(fd)


def read_journal_prefix(path: Path, fingerprint: list, expected_sha: str) -> bytes:
    """Bind to the same inode and content used by the original PnL observer.

    Appends after its snapshot are allowed. Truncation, rewrite, replacement,
    symlinks and mismatched prefixes are not.
    """
    require(isinstance(fingerprint, list) and len(fingerprint) == 4
            and all(type(v) is int and v >= 0 for v in fingerprint),
            "INVALID_JOURNAL_FINGERPRINT")
    size = fingerprint[2]
    require(0 < size <= MAX_JOURNAL_BYTES, "JOURNAL_PREFIX_BOUND")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        require(stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid()
                and st.st_nlink == 1 and not st.st_mode & 0o077,
                "JOURNAL_PRIVATE_FILE_REQUIRED")
        require((st.st_dev, st.st_ino) == tuple(fingerprint[:2])
                and st.st_size >= size, "JOURNAL_REPLACED_OR_TRUNCATED")
        parts, offset = [], 0
        while offset < size:
            part = os.pread(fd, min(1024 * 1024, size - offset), offset)
            require(bool(part), "JOURNAL_PREFIX_SHORT_READ")
            parts.append(part)
            offset += len(part)
        after = os.fstat(fd)
        require((after.st_dev, after.st_ino) == tuple(fingerprint[:2])
                and after.st_size >= size, "JOURNAL_MUTATED_DURING_READ")
        data = b"".join(parts)
        require(data.endswith(b"\n") and sha(data) == expected_sha,
                "JOURNAL_PREFIX_SHA256_MISMATCH")
        return data
    finally:
        os.close(fd)


def as_decimal(value) -> Decimal:
    require(type(value) is str, "DECIMAL_STRING_REQUIRED")
    try:
        x = Decimal(value)
    except Exception as exc:
        raise CostEvidenceError("DECIMAL_INVALID") from exc
    require(x.is_finite(), "NONFINITE_DECIMAL")
    return x


def dec_text(value: Decimal) -> str:
    return format(value, "f")


def percentile(values: list[Decimal], p: int) -> str | None:
    require(0 <= p <= 100, "PERCENTILE_INVALID")
    if not values:
        return None
    seq = sorted(values)
    index = (len(seq)-1) * Decimal(p)/100
    lo = int(index)
    hi = min(lo + 1, len(seq)-1)
    part = index - lo
    return dec_text(seq[lo] + part * (seq[hi] - seq[lo]))


def summarize_validated_ledger(ledger: dict, component: dict) -> dict:
    """Exactly validated *full* replayed fills; never the display tail."""
    require(ledger.get("fees_complete") is True
            and component.get("fees_complete") is True
            and component.get("ledger_complete") is True,
            "FEE_OR_LEDGER_NOT_COMPLETE")
    fills = ledger["executions"]
    require(len(fills) == ledger["execution_count"]
            == component["execution_count"]
            == component["execution_details_total"], "EXECUTION_COUNT_MISMATCH")
    require(ledger["command_count"] == component["command_count"],
            "COMMAND_COUNT_MISMATCH")
    require(component.get("fee_missing_execution_ids_total") == 0
            and component.get("fee_conflicting_execution_ids_total") == 0,
            "COMMISSION_IDENTITY_UNRESOLVED")
    require(as_decimal(component["fees_paid"]) == ledger["book"].paid_fees,
            "COMMISSION_BOOK_TOTAL_MISMATCH")
    require(as_decimal(component["realized_gross_pnl"]) == ledger["book"].realized,
            "BROKER_REALIZED_GROSS_MISMATCH")
    require(as_decimal(component["position"]) == ledger["book"].quantity,
            "BROKER_POSITION_MISMATCH")
    seen = set()
    sides = Counter()
    side_notional = {"BUY": Decimal(0), "SELL": Decimal(0)}
    side_fees = {"BUY": Decimal(0), "SELL": Decimal(0)}
    effective_fee_bps = []
    notionals = []
    fees = []
    small = {"below_1000": {"count": 0, "fees_usd": Decimal(0)},
             "below_10000": {"count": 0, "fees_usd": Decimal(0)},
             "below_50000": {"count": 0, "fees_usd": Decimal(0)},
             "at_least_50000": {"count": 0, "fees_usd": Decimal(0)}}
    last_side = None
    flips = 0
    cmd_ids = set()
    for row in fills:
        eid, cid, side = row["execution_id"], row["command_id"], row["side"]
        require(isinstance(eid, str) and eid and eid not in seen,
                "DUPLICATE_VALIDATED_EXECUTION_ID")
        seen.add(eid)
        require(isinstance(cid, str) and cid and side in sides.keys() | {"BUY","SELL"},
                "VALIDATED_FILL_MISSING_ORDER_SIDE")
        qty = as_decimal(row["quantity"])
        price = as_decimal(row["price"])
        fee = as_decimal(row["commission"])
        require(qty > 0 and price > 0 and fee >= 0
                and row["commission_currency"] == "USD",
                "EXECUTION_ECONOMICS_INVALID")
        notional = qty * price
        notionals.append(notional)
        fees.append(fee)
        effective_fee_bps.append(fee / notional * 10000)
        sides[side] += 1
        side_notional[side] += notional
        side_fees[side] += fee
        if last_side and side != last_side:
            flips += 1
        last_side = side
        cmd_ids.add(cid)
        bucket = ("below_1000" if notional < 1000 else
                  "below_10000" if notional < 10000 else
                  "below_50000" if notional < 50000 else "at_least_50000")
        small[bucket]["count"] += 1
        small[bucket]["fees_usd"] += fee
    require(sum(fees, Decimal(0)) == as_decimal(component["fees_paid"]),
            "FULL_FILL_COMMISSION_SUM_MISMATCH")
    notional_total = sum(notionals, Decimal(0))
    total_fees = sum(fees, Decimal(0))
    require(notional_total > 0, "NO_VALIDATED_TRADING_TURNOVER")
    gross = as_decimal(component["gross_total_pnl"])
    net = as_decimal(component["net_total_pnl"])
    require(gross - total_fees == net, "NET_PNL_NOT_GROSS_MINUS_FEES")
    require(len(cmd_ids) <= ledger["command_count"], "FILLED_COMMANDS_EXCEED_SENT")
    buckets = {k: {"execution_count": v["count"], "fees_usd": dec_text(v["fees_usd"])}
               for k, v in small.items()}
    return {
        "instrument": component["instrument"],
        "unique_executions": len(seen),
        "validated_order_commands": ledger["command_count"],
        "commands_with_fills": len(cmd_ids),
        "original_sent_without_fill": ledger["command_count"] - len(cmd_ids),
        "side_counts": dict(sides),
        "side_change_count_observational_only": flips,
        "cumulative_executed_notional_usd": dec_text(notional_total),
        "buy_notional_usd": dec_text(side_notional["BUY"]),
        "sell_notional_usd": dec_text(side_notional["SELL"]),
        "commission_usd": dec_text(total_fees),
        "buy_commission_usd": dec_text(side_fees["BUY"]),
        "sell_commission_usd": dec_text(side_fees["SELL"]),
        "execution_oneway_fee_bps": {
            "p50": percentile(effective_fee_bps, 50),
            "p90": percentile(effective_fee_bps, 90),
            "p99": percentile(effective_fee_bps, 99),
            "turnover_weighted": dec_text(total_fees / notional_total * 10000)
        },
        "execution_notional_usd": {
            "p50": percentile(notionals, 50), "p90": percentile(notionals, 90)
        },
        "fee_by_execution_notional": buckets,
        "gross_total_pnl_usd": dec_text(gross),
        "net_total_pnl_usd": dec_text(net),
        "gross_pnl_already_includes_real_fill_price_effects": True,
        "entry_exit_spread_and_slippage_not_decomposed": True,
        "broker_statement_expenses_not_proven": True,
        "truncated_pnl_display_tail_not_used": True
    }


def audit(snapshot_path: Path, period_path: Path, journal_path: Path,
          installed_accounting: Path) -> dict:
    raw_snapshot = read_private(snapshot_path, 1024*1024)
    raw_period = read_private(period_path, 1024*1024)
    snapshot = strict(raw_snapshot)
    period = strict(raw_period)
    validate_period(period)
    require(snapshot.get("schema") == "heptatrader.pnl.v1"
            and snapshot.get("scope") == "STRATEGY_PERIOD_NOT_ACCOUNT_NAV"
            and snapshot.get("complete") is True
            and snapshot.get("mutation_authorized") is False,
            "PNL_SNAPSHOT_NOT_ELIGIBLE")
    require(period.get("execution_domain") == "PAPER"
            and snapshot.get("period_id") == period.get("period_id")
            and snapshot.get("period_sha256") == sha(raw_period),
            "PNL_PERIOD_OR_DOMAIN_MISMATCH")
    source = Path(inspect.getfile(replay))
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    require(source_sha == hashlib.sha256(installed_accounting.read_bytes()).hexdigest(),
            "ACCOUNTING_SOURCE_DIFFERS_FROM_INSTALLED_OBSERVER")
    journal = read_journal_prefix(journal_path, snapshot["journal_fingerprint"],
                                  snapshot["journal_sha256"])
    lines = journal.splitlines(keepends=True)
    require(len(lines) == snapshot["journal_records"] <= MAX_JOURNAL_RECORDS,
            "JOURNAL_RECORD_COUNT_MISMATCH")
    events = [strict(line) for line in lines]
    components = {r["instrument"]: r for r in snapshot["components"]}
    require(set(components) == set(INSTRUMENTS), "INSTRUMENT_SET_CHANGED")
    with localcontext() as context:
        context.prec = 40
        summaries = []
        for instrument in sorted(INSTRUMENTS):
            ledger = replay(events, period, instrument, snapshot["observed_at_ms"])
            summaries.append(summarize_validated_ledger(ledger, components[instrument]))
    require(len(set(ex["execution_id"] for instrument in INSTRUMENTS
                    for ex in replay(events, period, instrument,
                                     snapshot["observed_at_ms"])["executions"]))
            == sum(x["unique_executions"] for x in summaries),
            "EXECUTION_COLLISION_ACROSS_INSTRUMENTS")
    # The totals already come from a full replay. Require their consistency
    # while making no inference about unavailable NAV/Flex/financing.
    for key in ("gross_total_pnl", "net_total_pnl", "fees_paid"):
        actual = sum((as_decimal(row[key + "_usd"]) for row in summaries), Decimal(0)
                     ) if key in ("gross_total_pnl", "net_total_pnl") else sum(
                     (as_decimal(row["commission_usd"]) for row in summaries), Decimal(0))
        require(actual == as_decimal(snapshot["totals"][key]),
                "STRATEGY_TOTAL_RECONCILIATION_FAILED:" + key)
    return {
        "schema": "heptatrader.oms-period-execution-cost-readonly.v1",
        "read_only": True, "broker_queries_sent": 0, "broker_orders_sent": 0,
        "period_id": period["period_id"],
        "snapshot_at_ms": snapshot["observed_at_ms"],
        "snapshot_sha256": sha(raw_snapshot),
        "period_sha256": sha(raw_period),
        "journal_prefix_sha256": sha(journal),
        "journal_prefix_bytes": len(journal),
        "journal_records": len(lines),
        "accounting_source_sha256": source_sha,
        "component_summary": summaries,
        "account_net_pnl_unproven": snapshot.get("account_complete") is not True,
        "limitations": [
            "PAPER strategy period, not account total net return",
            "Commissions are IB callback records, not a Flex statement",
            "Unreported financing, taxes and other charges are excluded",
            "Effective per-execution fee is not an ex-ante executable quote",
            "Side changes do not imply completed round trips",
            "Historical/offline audit has no model promotion or live trade authority"
        ]
    }


def write_exclusive(path: Path, report: dict) -> None:
    os.umask(0o077)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = path.parent.stat()
    require(st.st_uid == os.geteuid() and stat.S_IMODE(st.st_mode) == 0o700,
            "EVIDENCE_DIR_NOT_PRIVATE")
    raw = (json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--period", required=True, type=Path)
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument("--installed-accounting", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    r = audit(args.snapshot, args.period, args.journal, args.installed_accounting)
    write_exclusive(args.output, r)
    print(json.dumps({
        "state": "PASS_FULL_VALIDATED_JOURNAL_COST_AUDIT",
        "execution_counts": {s["instrument"]: s["unique_executions"] for s in r["component_summary"]},
        "no_broker_queries_or_orders": True, "output": str(args.output)
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
