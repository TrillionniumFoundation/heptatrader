"""Read-only PAPER round-trip cost decomposition over the canonical OMS ledger.

This is accounting research, not a signal, execution permission, or account NAV.
The only event authority is the existing hepta_pnl_accounting.replay implementation.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal, localcontext
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time

ACCOUNTING_DIR = Path(os.environ.get("HEPTA_PNL_ACCOUNTING_DIR", str(Path(__file__).resolve().parents[1] / "scripts")))
if not (ACCOUNTING_DIR / "hepta_pnl_accounting.py").is_file():
    raise ImportError("PINNED_ACCOUNTING_SOURCE_MISSING")
sys.path.insert(0, str(ACCOUNTING_DIR))
from hepta_pnl_accounting import Book, replay, validate_period  # noqa: E402

ZERO = Decimal("0")


def decimal(value):
    return Decimal(str(value))


def text(value):
    return format(value, "f") if isinstance(value, Decimal) else value


def attribution(executions, original_sent_at, opening_quantity="0"):
    """Compute position-flat episodes from validated, ordered replay executions.

    A cross-zero flip is deliberately excluded instead of silently fabricating a
    flat trade; original send timestamps describe intent timing, not fill timing.
    """
    if decimal(opening_quantity) != ZERO:
        raise ValueError("NON_FLAT_PERIOD_OPENING_NOT_SUPPORTED")
    book = Book()
    episodes = []
    current = None
    missing_times = 0
    for execution in executions:
        side = execution["side"]
        if side not in ("BUY", "SELL"):
            raise ValueError("UNKNOWN_EXECUTION_SIDE")
        q = decimal(execution["quantity"])
        price = decimal(execution["price"])
        commission = execution.get("commission")
        if q <= 0 or price <= 0 or commission is None:
            raise ValueError("INCOMPLETE_EXECUTION_ECONOMICS")
        fee = decimal(commission)
        if not fee.is_finite():
            raise ValueError("NONFINITE_COMMISSION")
        direction = 1 if side == "BUY" else -1
        delta = direction * q
        if book.quantity != ZERO and book.quantity * delta < 0 and abs(delta) > abs(book.quantity):
            raise ValueError("CROSS_ZERO_FLIP_REQUIRES_SEPARATE_ATTRIBUTION")
        sent_at = original_sent_at.get(execution["command_id"])
        if sent_at is None:
            missing_times += 1
        if current is None:
            if book.quantity != ZERO:
                raise AssertionError("EPISODE_NOT_STARTED_FLAT")
            current = {"start_command": execution["command_id"],
                       "first_sent_ms": sent_at,
                       "last_sent_ms": sent_at,
                       "opening_side": side,
                       "gross_before": book.realized,
                       "fee_before": book.paid_fees,
                       "execution_count": 0,
                       "entry_quantity": ZERO}
        if book.quantity == ZERO or book.quantity * delta > 0:
            current["entry_quantity"] += q
        current["execution_count"] += 1
        if sent_at is not None:
            current["last_sent_ms"] = sent_at
        book.apply(direction, q, price, fee)
        if book.quantity == ZERO:
            gross = book.realized - current["gross_before"]
            charged = book.paid_fees - current["fee_before"]
            net = gross - charged
            begin, end = current["first_sent_ms"], current["last_sent_ms"]
            duration = (end - begin) / 1000 if isinstance(begin, int) and isinstance(end, int) else None
            if duration is not None and duration < 0:
                raise ValueError("ORIGINAL_SEND_TIME_REVERSED")
            episodes.append({"gross_usd": text(gross), "commissions_usd": text(charged),
                             "net_usd": text(net),
                             "first_original_send_ms": begin,
                             "last_original_send_ms": end,
                             "send_to_send_seconds": duration,
                             "execution_count": current["execution_count"],
                             "opening_side": current["opening_side"],
                             "start_command": current["start_command"],
                             "end_command": execution["command_id"],
                             "entry_quantity": text(current["entry_quantity"])})
            current = None
    return episodes, current, book, missing_times


def summary(episodes):
    n = len(episodes)
    gross = [decimal(e["gross_usd"]) for e in episodes]
    fees = [decimal(e["commissions_usd"]) for e in episodes]
    net = [decimal(e["net_usd"]) for e in episodes]
    durations = [e["send_to_send_seconds"] for e in episodes if e["send_to_send_seconds"] is not None]
    def metric(values):
        return {"sum": text(sum(values, ZERO)), "mean": text(sum(values, ZERO) / len(values)) if values else None,
                "median": text(statistics.median(values)) if values else None}
    running = ZERO
    peak = ZERO
    drawdown = ZERO
    for value in net:
        running += value
        peak = max(peak, running)
        drawdown = max(drawdown, peak - running)
    return {"closed_roundtrips": n,
            "gross_usd": metric(gross), "commissions_usd": metric(fees), "net_usd": metric(net),
            "gross_positive_roundtrips": sum(x > 0 for x in gross),
            "net_positive_roundtrips": sum(x > 0 for x in net),
            "gross_positive_but_net_nonpositive": sum(g > 0 and v <= 0 for g, v in zip(gross, net)),
            "gross_nonpositive_roundtrips": sum(x <= 0 for x in gross),
            "max_closed_episode_net_drawdown_usd": text(drawdown),
            "original_send_to_send_duration_sec_median": statistics.median(durations) if durations else None,
            "original_send_to_send_duration_sec_max": max(durations) if durations else None,
            "timed_episode_count": len(durations)}


def duration_side_breakdown(episodes):
    """Descriptive CLOSED-episode stratification; holding time is known only after exit.

    Original-send to original-send duration is not execution latency or a
    prospective trading feature. A bucket's post-hoc PnL never licenses a
    new entry policy, model gradient, or selective backtest admission.
    """
    import math
    bands = ("LT_5MIN", "5_TO_15MIN", "15_TO_60MIN", "GE_60MIN", "UNKNOWN")
    groups = defaultdict(list)
    for episode in episodes:
        side = episode.get("opening_side")
        if side not in ("BUY", "SELL"):
            raise ValueError("EPISODE_UNKNOWN_OPENING_SIDE")
        duration = episode.get("send_to_send_seconds")
        if duration is None:
            band = "UNKNOWN"
        elif type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
            raise ValueError("EPISODE_INVALID_ORIGINAL_SEND_DURATION")
        elif duration < 300:
            band = "LT_5MIN"
        elif duration < 900:
            band = "5_TO_15MIN"
        elif duration < 3600:
            band = "15_TO_60MIN"
        else:
            band = "GE_60MIN"
        gross = decimal(episode["gross_usd"])
        fees = decimal(episode["commissions_usd"])
        net = decimal(episode["net_usd"])
        if (not gross.is_finite() or not fees.is_finite() or
                not net.is_finite() or fees < 0 or gross - fees != net):
            raise ValueError("EPISODE_UNRECONCILED_NET")
        groups[(side, band)].append((gross, fees, net))
    output = {}
    for (side, band), rows in sorted(groups.items()):
        sums = [sum((r[k] for r in rows), ZERO) for k in range(3)]
        key = side + ":" + band
        output[key] = {
            "closed_roundtrips": len(rows),
            "gross_usd": text(sums[0]),
            "reported_commissions_usd": text(sums[1]),
            "net_usd": text(sums[2]),
            "net_positive_roundtrips": sum(r[2] > ZERO for r in rows),
            "gross_positive_but_net_nonpositive": sum(
                r[0] > ZERO and r[2] <= ZERO for r in rows),
        }
    if (sum((x["closed_roundtrips"] for x in output.values()), 0) != len(episodes) or
            any(sum((decimal(v[k]) for v in output.values()), ZERO) !=
                sum((decimal(e[src]) for e in episodes), ZERO)
                for k, src in (("gross_usd", "gross_usd"),
                               ("reported_commissions_usd", "commissions_usd"),
                               ("net_usd", "net_usd")))):
        raise ValueError("EPISODE_BUCKET_RECONCILIATION_FAILED")
    return {
        "classification": "EX_POST_ONLY_NO_CAUSAL_POLICY_OR_PROSPECTIVE_ALPHA",
        "duration_basis": "FIRST_TO_LAST_ORIGINAL_SEND_NOT_FILL_TIME",
        "bands": list(bands),
        "groups": output,
        "reconciled_to_closed_episodes": True,
    }



def close_reason_coverage(episodes, executions, receipts, instrument):
    """Correlate DRIVER-REPORTED future CLOSE reasons with exact OMS fills.

    No exit reason is derived from holding time, a mutable 'last_exit_reason',
    or an adjacent model decision. This is not broker-native stop evidence.
    """
    if instrument not in ("EUR.USD", "BTC.USD") or not isinstance(receipts, list) or len(receipts) > 64:
        raise ValueError("CLOSE_REASON_SOURCE_SCOPE")
    by_command = defaultdict(list)
    for fill in executions:
        key, execution_id = fill.get("command_id"), fill.get("execution_id")
        if not isinstance(key, str) or not isinstance(execution_id, str) or not execution_id:
            raise ValueError("CLOSE_REASON_UNIDENTIFIABLE_EXECUTION")
        by_command[key].append(fill)
    record_by_command = {}
    for receipt in receipts:
        if not isinstance(receipt, dict) or receipt.get("schema") != "heptatrader.driver-close-command-attribution.v1":
            raise ValueError("CLOSE_REASON_MALFORMED_RECORD")
        key = receipt.get("original_command_id")
        if not isinstance(key, str) or not key:
            raise ValueError("CLOSE_REASON_MISSING_COMMAND")
        if key in record_by_command:
            raise ValueError("CLOSE_REASON_DUPLICATE_COMMAND")
        record_by_command[key] = receipt
    buckets = defaultdict(lambda: {"roundtrips": 0, "net_usd": ZERO})
    blocked = defaultdict(int)
    matched = 0
    for episode in episodes:
        key = episode["end_command"]
        rec = record_by_command.get(key)
        if rec is None:
            blocked["NO_EXACT_CLOSE_RECEIPT"] += 1
            continue
        trigger = rec.get("exit_trigger")
        if (rec.get("instrument") != instrument or
                not isinstance(trigger, dict) or
                trigger.get("schema") != "heptatrader.driver-close-trigger.v1" or
                trigger.get("original_command_id") != key):
            blocked["RECEIPT_COMMAND_OR_INSTRUMENT_CONFLICT"] += 1
            continue
        if (rec.get("terminal_partial_exit") is not False or
                rec.get("broker_terminal_observed") is not True or
                decimal(rec.get("position_after_terminal_fill", "NaN")) != ZERO):
            blocked["NOT_FLAT_TERMINAL_FILL"] += 1
            continue
        observed = by_command.get(key, [])
        ids = [x["execution_id"] for x in observed]
        claimed = rec.get("broker_execution_ids")
        if (not ids or len(ids) != len(set(ids)) or not isinstance(claimed, list) or
                not claimed or len(claimed) != len(set(claimed)) or
                set(ids) != set(claimed) or
                rec.get("broker_execution_id") not in ids or
                any(x.get("order_id") != rec.get("broker_order_id") for x in observed)):
            blocked["ORIGINAL_EXECUTION_IDENTITY_NOT_PROVEN"] += 1
            continue
        try:
            summed_quantity = sum((decimal(x["quantity"]) for x in observed), ZERO)
            reported_quantity = decimal(rec["broker_fill_quantity"])
            weighted_price = (sum((decimal(x["quantity"]) * decimal(x["price"])
                                   for x in observed), ZERO) / summed_quantity)
            reported_price = decimal(rec["broker_average_fill_price"])
            tolerance_qty = max(Decimal("0.0000001"),
                                summed_quantity * Decimal("0.000000001"))
            tolerance_price = (Decimal("0.000005") if instrument == "EUR.USD"
                               else Decimal("0.05"))
            economics_match = (all(v.is_finite() and v > ZERO for v in (
                summed_quantity, reported_quantity, weighted_price, reported_price))
                and abs(summed_quantity - reported_quantity) <= tolerance_qty
                and abs(weighted_price - reported_price) <= tolerance_price)
        except (KeyError, TypeError, ValueError, ArithmeticError):
            economics_match = False
        if not economics_match:
            blocked["CLOSE_FILL_QUANTITY_OR_PRICE_UNRECONCILED"] += 1
            continue
        trigger_at, recorded_at, sent_at = (trigger.get("trigger_at_ms"),
                                           rec.get("recorded_at_ms"),
                                           episode.get("last_original_send_ms"))
        if (type(trigger_at) is not int or type(recorded_at) is not int or
                type(sent_at) is not int or
                not 0 < trigger_at <= sent_at <= recorded_at or
                (trigger.get("quote_observed_at_ms") is not None and
                 (type(trigger["quote_observed_at_ms"]) is not int or
                  not 0 < trigger["quote_observed_at_ms"] <= trigger_at))):
            blocked["CAUSAL_TRIGGER_TIMESTAMP_NOT_PROVEN"] += 1
            continue
        reason = trigger.get("reason_code")
        authority = trigger.get("reason_source")
        if not ((reason in {"MAX_ADVERSE_MOVE", "MODEL_REVERSAL", "HALT_REDUCE_ONLY"}
                 and authority == "DRIVER_EVALUATED") or
                (reason in {"OWNER_RECOVERY_REDUCE_ONLY", "CRYPTO_CANARY_EXIT"}
                 and authority == "DRIVER_RECOVERY_OR_CANARY")):
            blocked["MISSING_OR_UNSUPPORTED_ORIGINAL_REASON"] += 1
            continue
        # Exact recorded execution IDs + original driver's pre-send reason,
        # NOT proof that the exchange executed a native stop trigger.
        buckets[reason]["roundtrips"] += 1
        buckets[reason]["net_usd"] += decimal(episode["net_usd"])
        matched += 1
    if matched + sum(blocked.values()) != len(episodes):
        raise ValueError("CLOSE_REASON_DENOMINATOR")
    return {
        "scope": "EXACT_DRIVER_INTENT_AND_OMS_EXECUTION_IDS_NOT_BROKER_CAUSAL_PROOF",
        "historical_unattributed_not_repaired": True,
        "closed_episodes": len(episodes),
        "exact_original_reason_bound": matched,
        "unknown_or_unverified": len(episodes) - matched,
        "reason_buckets": {code: {"roundtrips": value["roundtrips"],
                                 "net_usd": text(value["net_usd"])}
                           for code, value in sorted(buckets.items())},
        "nonqualifying_reasons": dict(sorted(blocked.items())),
        "unmatched_retained_recent_receipts": len(set(record_by_command) -
                                                  {x["end_command"] for x in episodes}),
        "never_trade_authority": True,
    }


def read_bounded_driver_receipts(path, instrument):
    """One local driver snapshot read: optional historical evidence, never order input."""
    p = Path(path)
    if p.is_symlink() or not p.is_file():
        raise ValueError("CLOSE_REASON_UNSAFE_STATE_PATH")
    data = p.read_bytes()
    if not data or len(data) > 1024 * 1024:
        raise ValueError("CLOSE_REASON_STATE_SIZE")
    document = json.loads(data)
    if document.get("instrument") != instrument or not isinstance(document.get("close_command_attribution", []), list):
        raise ValueError("CLOSE_REASON_STATE_WRONG_INSTRUMENT")
    return document.get("close_command_attribution", []), hashlib.sha256(data).hexdigest()


def read_prefix(path):
    """Freeze a newline-complete prefix; never infer a transaction from a tail."""
    payload = Path(path).read_bytes()
    full = payload.rfind(b"\n") + 1
    if not full:
        raise ValueError("JOURNAL_NO_COMPLETE_RECORD")
    prefix = payload[:full]
    events = [json.loads(line) for line in prefix.splitlines() if line]
    return events, hashlib.sha256(prefix).hexdigest(), len(prefix), len(payload) - full


def inspect(journal, period_path, snapshot_path=None, driver_states=None):
    period_bytes = Path(period_path).read_bytes()
    period = json.loads(period_bytes)
    validate_period(period)
    events, journal_sha, journal_bytes, trailing_bytes = read_prefix(journal)
    started = int(time.time() * 1000)
    seen = defaultdict(set)
    sent_at = {}
    for e in events:
        if e.get("event") in ("place_sent", "flatten_sent") and e.get("execution_domain") == "PAPER":
            cmd = e.get("req_id")
            if isinstance(cmd, str) and isinstance(e.get("ts_ms"), int):
                seen[cmd].add(e["ts_ms"])
                sent_at[cmd] = min(sent_at.get(cmd, e["ts_ms"]), e["ts_ms"])
    output = {"schema": "heptatrader.pnl-cost-attribution.v1",
              "source": {"accounting_period_id": period["period_id"],
                         "period_sha256": hashlib.sha256(period_bytes).hexdigest(),
                         "journal_prefix_sha256": journal_sha,
                         "journal_prefix_bytes": journal_bytes,
                         "trailing_uncommitted_bytes_ignored": trailing_bytes,
                         "journal_events": len(events),
                         "accounting_module_sha256": hashlib.sha256((ACCOUNTING_DIR / "hepta_pnl_accounting.py").read_bytes()).hexdigest(),
                         "original_send_timestamp_disagreements": sum(len(v) > 1 for v in seen.values())},
              "scope": "PAPER_STRATEGY_PERIOD_COMMISSION_NET_NOT_ACCOUNT_NAV",
              "time_basis": "ORIGINAL_SEND_NOT_BROKER_FILL_OR_INFERRED_PRICE",
              "orders_sent_by_this_audit": 0,
              "automatic_trading_or_model_selection": False,
              "instruments": {}}
    for instrument, ins in sorted(period["instruments"].items()):
        ledger = replay(events, period, instrument, started)
        if not ledger["fees_complete"] or ledger["fee_missing_execution_ids"] or ledger["fee_conflicting_execution_ids"]:
            raise ValueError("BROKER_FEE_RECONCILIATION_INCOMPLETE_" + instrument)
        rounds, open_episode, book, missing_t = attribution(
            ledger["executions"], sent_at, ins["opening_quantity"])
        expected = ledger["book"]
        if (book.quantity != expected.quantity or book.realized != expected.realized or
                book.paid_fees != expected.paid_fees):
            raise ValueError("REPLAY_ACCOUNTING_PARITY_FAILED_" + instrument)
        all_fees = sum((decimal(e["commissions_usd"]) for e in rounds), ZERO)
        stats = summary(rounds)
        if not open_episode and all_fees != book.paid_fees:
            raise ValueError("EPISODE_FEES_NOT_RECONCILED_" + instrument)
        output["instruments"][instrument] = {
            "ledger_execution_count": ledger["execution_count"],
            "ledger_command_count": ledger["command_count"],
            "ledger_position": text(book.quantity),
            "ledger_realized_gross_usd": text(book.realized),
            "ledger_paid_execution_fees_usd": text(book.paid_fees),
            "ledger_commissions_complete": True,
            "unsettled_original_commands": len(ledger["settlement_pending_command_ids"]),
            "original_sent_time_missing_for_fills": missing_t,
            "open_episode": bool(open_episode),
            "closed": stats, "duration_side": duration_side_breakdown(rounds)}
        if driver_states and instrument in driver_states:
            receipts, driver_state_sha = read_bounded_driver_receipts(driver_states[instrument], instrument)
            output["instruments"][instrument]["close_reason"] = close_reason_coverage(
                rounds, ledger["executions"], receipts, instrument)
            output["instruments"][instrument]["close_reason"]["driver_state_sha256"] = driver_state_sha
    if snapshot_path:
        s = json.loads(Path(snapshot_path).read_text())
        same_period = s.get("period_id") == period["period_id"]
        same_journal = s.get("journal_sha256") == journal_sha
        if same_period and same_journal:
            for component in s.get("components", []):
                item = output["instruments"].get(component.get("instrument"))
                if not item or any((
                    decimal(item["ledger_paid_execution_fees_usd"]) != decimal(component["fees_paid"]),
                    decimal(item["ledger_realized_gross_usd"]) != decimal(component["realized_gross_pnl"]),
                    decimal(item["ledger_position"]) != decimal(component["position"]),
                    item["ledger_execution_count"] != component["execution_count"],
                )):
                    raise ValueError("CONTEMPORANEOUS_PNL_OBSERVER_PARITY_FAILED")
        output["snapshot_crosscheck"] = {
            "same_period": same_period,
            "same_journal_digest": same_journal,
            "execution_fees_position_and_realized_parity": bool(same_period and same_journal),
            "snapshot_observed_ms": s.get("observed_at_ms"),
            "snapshot_net_usd": s.get("totals", {}).get("net_total_pnl"),
            "not_a_same_time_claim_when_digest_mismatches": True}
    output["finished_at_ms"] = int(time.time() * 1000)
    return output


def main():
    if not os.environ.get("HEPTA_PNL_ACCOUNTING_DIR"):
        raise SystemExit("SET_EXPLICIT_ACTIVE_HEPTA_PNL_ACCOUNTING_DIR")
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--period", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--fx-driver-state", type=Path)
    parser.add_argument("--btc-driver-state", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # Match production build_report's 40-digit Decimal replay context exactly.
    with localcontext(prec=40):
        sources = {k: v for k, v in (("EUR.USD", args.fx_driver_state),
                                     ("BTC.USD", args.btc_driver_state)) if v is not None}
        result = inspect(args.journal, args.period, args.snapshot, sources)
    args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    print(json.dumps({k: v["closed"] for k, v in result["instruments"].items()}))


if __name__ == "__main__":
    main()
