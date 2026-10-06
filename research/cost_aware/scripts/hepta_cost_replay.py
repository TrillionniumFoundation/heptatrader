#!/usr/bin/env python3
"""Causal three-path replay and offline, past-only calibration tooling.

No broker or model invocation. Every strategy maintains its own position, cash,
fees, holding clock and reentry state. Inputs must contain position-neutral
candidates, not factual trades to prune or forecasts conditioned on one position.
Results are execution-model simulations, not PAPER or live performance evidence.
"""
from __future__ import annotations
import argparse
from bisect import bisect_left
from dataclasses import replace, asdict
from decimal import Decimal
import json
from pathlib import Path
import sys
from hepta_cost_policy import (D, BPS, CostError, DEFAULT_POLICY, FORECAST_VERSION, MODEL_ID,
    canonical, digest, dec, quote_values, validate_forecast, commission, entry_admission,
    risk_exit, discretionary_exit, build_calibration, strict_loads)
from hepta_cost_capture import read_capture

NAMES = ("DIRECTION_BASELINE", "COST_GATE", "COST_GATE_BAND")


def validate_tape(tape):
    if (not isinstance(tape, dict) or tape.get("schema") != "heptatrader.cost-tape.v1" or
            tape.get("instrument") not in ("BTC.USD", "EUR.USD") or
            tape.get("source_kind") not in ("IB_CAPTURE", "SYNTHETIC") or
            not isinstance(tape.get("rows"), list) or not 1 <= len(tape["rows"]) <= 100000):
        raise CostError("TAPE_SCHEMA_INVALID")
    last = 0
    for row in tape["rows"]:
        if not isinstance(row, dict):
            raise CostError("TAPE_ROW_SCHEMA_INVALID")
        at = row.get("at_ms")
        if type(at) is not int or at <= 0 or at < last or not isinstance(row.get("quote"), dict):
            raise CostError("TAPE_TIME_OR_QUOTE_INVALID")
        last = at
        forecast = row.get("forecast")
        if forecast is not None:
            validate_forecast(forecast)
            available = row.get("forecast_available_at_ms", at)
            if (forecast["instrument"] != tape["instrument"] or type(available) is not int or
                    not forecast["issued_at_ms"] <= available <= at):
                raise CostError("TAPE_FORECAST_CAUSALITY_INVALID")
    return tape


def capture_to_tape(capture):
    # Forecast completion and evaluation are different clocks. A completed
    # forecast waits for a fresh OBSERVED quote, never a future price backdated
    # to model completion. Keep the original model-availability/target identity.
    rows, quote, pending = [], None, []
    seen = set()
    instrument = capture["events"][0]["data"]["instrument"]
    def fresh(at):
        try:
            quote_values(quote, instrument, at)
            return True
        except CostError:
            return False
    def evaluate(forecast, available, at):
        rows.append({"at_ms": at, "quote": quote, "forecast": forecast,
                     "forecast_available_at_ms": available})
    for event in capture["events"]:
        at = event["at_ms"]
        if event["event"] == "QUOTE":
            quote = event["data"]
            rows.append({"at_ms": at, "quote": quote, "forecast": None})
            if pending and fresh(at):
                for forecast, available in pending:
                    evaluate(forecast, available, at)
                pending.clear()
        elif event["event"] == "CANDIDATE":
            forecast = event["data"]["forecast"]
            validate_forecast(forecast)
            identity = digest(forecast)
            if identity in seen:
                raise CostError("DUPLICATE_CANDIDATE_IN_CAPTURE")
            seen.add(identity)
            if quote is None:
                raise CostError("CANDIDATE_WITHOUT_CAUSAL_QUOTE")
            if fresh(at):
                evaluate(forecast, at, at)
            else:
                pending.append((forecast, at))
    # Preserve a candidate with no subsequent fresh quote as a rejected/unknown
    # observation; never drop it or manufacture a quote at the end of the tape.
    if pending:
        at = capture["events"][-1]["at_ms"]
        for forecast, available in pending:
            evaluate(forecast, available, at)
    if not rows:
        raise CostError("NO_QUOTE_TAPE_AVAILABLE")
    return validate_tape({"schema": "heptatrader.cost-tape.v1", "instrument": instrument,
                          "source_kind": capture["source_kind"], "input_sha256": capture["input_sha256"],
                          "evaluation_alignment": "NEXT_FRESH_OBSERVED_QUOTE_NO_BACKDATING", "rows": rows})


def candidate_outcomes(tape, *, cutoff_ms=None, max_gap_ms=30000):
    """Keep ALL candidate outcomes, including refused/HOLD and unobservable ones.

    Labels come from first usable source quote at/after target time within TTL.
    Calibration excludes windows with data gaps and labels not known by cutoff.
    """
    validate_tape(tape)
    rows = tape["rows"]
    # Source quote timestamps, not the time a repeated quote was reread.
    unique = {}
    for row in rows:
        try:
            _, _, mid = quote_values(row["quote"], tape["instrument"], row["at_ms"])
        except CostError:
            continue
        at = row["quote"]["observed_at_ms"]
        old = unique.get(at)
        value = (mid, row["at_ms"])
        if old and old[0] != mid:
            raise CostError("SAME_SOURCE_TIMESTAMP_DIFFERENT_PRICE")
        if old is None or value[1] < old[1]:
            unique[at] = value
    times = sorted(unique)
    results = []
    seen = set()
    for row in rows:
        forecast = row.get("forecast")
        if not forecast:
            continue
        key = digest(forecast)
        if key in seen:
            raise CostError("DUPLICATE_FORECAST_ON_TAPE")
        seen.add(key)
        due = forecast["issued_at_ms"] + forecast["horizon_sec"] * 1000
        item = {"candidate_id": key, "forecast": forecast, "target_at_ms": due,
                "source_kind": tape["source_kind"], "forecast_available_at_ms": row.get("forecast_available_at_ms", row["at_ms"]), "realized_underlying_move_bps": None,
                "label_observed_at_ms": None, "label_available_at_ms": None, "reason": "OUTCOME_NOT_YET_OBSERVED"}
        start = bisect_left(times, forecast["issued_at_ms"])
        end = bisect_left(times, due)
        if end < len(times) and times[end] <= due + DEFAULT_POLICY.quote_ttl_ms:
            middle = times[start:end + 1]
            if not middle or middle[0] > forecast["issued_at_ms"] + DEFAULT_POLICY.quote_ttl_ms:
                item["reason"] = "MISSING_FORECAST_ANCHOR_QUOTE"
            elif any(b - a > max_gap_ms for a, b in zip(middle, middle[1:])):
                item["reason"] = "DATA_GAP_IN_FORECAST_HORIZON"
            elif cutoff_ms is not None and unique[times[end]][1] >= cutoff_ms:
                item["reason"] = "OUTCOME_NOT_KNOWN_AT_CALIBRATION_CUTOFF"
            else:
                anchor = dec(forecast["reference_mid"])
                anchor_value = unique[middle[0]][0]
                if middle[0] != forecast["issued_at_ms"] or anchor_value != anchor:
                    item["reason"] = "FORECAST_ANCHOR_MISMATCH"
                else:
                    item.update(realized_underlying_move_bps=str((unique[times[end]][0] / anchor - 1) * BPS),
                                label_observed_at_ms=times[end], label_available_at_ms=unique[times[end]][1], reason="OBSERVED")
        knowledge_end = min(rows[-1]['at_ms'], cutoff_ms) if cutoff_ms is not None else rows[-1]['at_ms']
        if item['reason'] == 'OUTCOME_NOT_YET_OBSERVED' and knowledge_end > due + DEFAULT_POLICY.quote_ttl_ms:
            item['reason'] = 'OUTCOME_WINDOW_MISSING_QUOTE'
        results.append(item)
    return results


def fit_from_tape(tape, cutoff_ms):
    outcomes = candidate_outcomes(tape, cutoff_ms=cutoff_ms)
    usable = [item for item in outcomes if item["reason"] == "OBSERVED"]
    return {"artifact": build_calibration(usable, cutoff_ms),
            "candidate_outcomes": outcomes, "activation_performed": False,
            "input_sha256": digest(tape), "sample_rows_usable_before_purging": len(usable)}


def artifact_for(forecast, artifacts):
    # Select only artifacts already completed when the forecast was issued.
    candidates = [artifact for artifact in artifacts if type(artifact.get("completed_at_ms")) is int and
                  artifact["completed_at_ms"] < forecast["issued_at_ms"]]
    return max(candidates, key=lambda item: item["completed_at_ms"]) if candidates else None


class PathState:
    def __init__(self, name, instrument, size, capital, policy):
        self.name, self.instrument, self.size, self.initial_cash = name, instrument, dec(size), dec(capital)
        self.policy = policy
        self.cash, self.quantity, self.entry_price = self.initial_cash, D(0), None
        self.entry_at = self.last_exit = 0
        self.forecast = None
        self.entry_fee = self.fees = self.carry = self.turnover = D(0)
        self.exposure_integral = D(0)
        self.exposure_ms = 0
        self.high_water = self.initial_cash
        self.max_drawdown = self.max_exposure = D(0)
        self.trades, self.decisions, self.marks = [], [], []
        self.missing_mark_count = 0
        self.last_at, self.last_mid = None, None
        self.path_complete = True
        self.last_equity = self.initial_cash

    def price(self, quote, side):
        slip = self.policy.slip(self.instrument) / BPS
        return dec(quote["ask"]) * (1 + slip) if side == "BUY" else dec(quote["bid"]) * (1 - slip)

    def open(self, forecast, quote, at):
        side = forecast["action"]
        price = self.price(quote, side)
        quantity = self.size / price if self.instrument == "BTC.USD" else self.size
        sign = D(1 if side == "BUY" else -1)
        self.quantity = sign * quantity
        amount = self.size if self.instrument == "BTC.USD" else quantity * price
        fee = commission(self.instrument, amount, fee_stress=self.policy.fee_stress)
        self.cash -= sign * amount + fee
        self.fees += fee
        self.turnover += amount
        self.entry_fee, self.entry_price, self.entry_at = fee, price, at
        self.forecast = forecast
        self.trades.append({"event": "OPEN", "at_ms": at, "candidate_id": digest(forecast), "order_type": "MKT",
                            "quantity": str(self.quantity), "price": str(price), "commission_usd": str(fee)})

    def close(self, quote, at, reason):
        price = self.price(quote, "SELL" if self.quantity > 0 else "BUY")
        amount = abs(self.quantity) * price
        fee = commission(self.instrument, amount, fee_stress=self.policy.fee_stress)
        self.cash += self.quantity * price - fee
        self.fees += fee
        self.turnover += amount
        gross = (price - self.entry_price) * self.quantity
        self.trades.append({"event": "CLOSE", "at_ms": at, "reason": reason, "order_type": "MKT",
                            "quantity": str(self.quantity), "price": str(price), "commission_usd": str(fee),
                            "holding_ms": at - self.entry_at, "execution_gross_usd": str(gross),
                            "execution_net_before_carry_usd": str(gross - fee - self.entry_fee)})
        self.quantity, self.entry_price, self.forecast, self.last_exit = D(0), None, None, at

    def step(self, row, artifacts, synthetic_ok):
        at, quote, forecast = row["at_ms"], row["quote"], row.get("forecast")
        if self.last_at is not None and self.quantity and self.last_mid:
            elapsed = at - self.last_at
            exposure = abs(self.quantity) * self.last_mid
            self.exposure_integral += exposure * elapsed
            self.exposure_ms += elapsed
            self.carry += exposure * dec(self.policy.holding_bps_per_hour) / BPS * D(elapsed) / D(3600000)
            if elapsed > 30000:
                self.path_complete = False
        self.last_at = at
        try:
            _, _, mid = quote_values(quote, self.instrument, at, self.policy)
            self.last_mid = mid
        except CostError as exc:
            if self.quantity:
                self.path_complete = False
            self.missing_mark_count += 1
            if forecast:
                self.decisions.append({"candidate_id": digest(forecast), "at_ms": at, "action": "REJECT", "reason": str(exc)})
            return
        closed = False
        # Common risk rules across all paths isolate entry-cost/band effects.
        why = risk_exit(self.quantity, self.entry_price, quote, at, entry_forecast=self.forecast)
        if why:
            self.close(quote, at, why)
            closed = True
        if forecast:
            identity = digest(forecast)
            decision = {"candidate_id": identity, "at_ms": at, "position_before": str(self.quantity)}
            artifact = artifact_for(forecast, artifacts)
            if closed:
                decision.update(action="NO_SAME_TICK_REENTRY", reason="RISK_EXIT_HAS_PRIORITY")
            elif at - forecast["issued_at_ms"] > self.policy.forecast_entry_ttl_ms:
                # The old resident also discarded results older than 60 seconds;
                # stale forecasts must not be accepted by the baseline alone.
                decision.update(action="REJECT", reason="FORECAST_STALE")
            elif self.quantity:
                opposite = "SELL" if self.quantity > 0 else "BUY"
                if forecast["action"] == opposite and dec(forecast["confidence"]) >= D(".62"):
                    if self.name == "COST_GATE_BAND":
                        take_exit, reason = discretionary_exit(forecast, quote, self.quantity, at, artifact,
                                                              policy=self.policy, synthetic_ok=synthetic_ok)
                    else:
                        take_exit, reason = True, "DIRECTION_REVERSAL"
                    if take_exit:
                        self.close(quote, at, reason)
                    decision.update(action="CLOSE" if take_exit else "HOLD", reason=reason)
                else:
                    decision.update(action="HOLD", reason="NO_REVERSAL")
            elif forecast["action"] == "HOLD" or dec(forecast["confidence"]) < D(".62"):
                decision.update(action="REJECT", reason="DIRECTION_OR_CONFIDENCE")
            elif self.instrument == "BTC.USD" and forecast["action"] == "SELL":
                decision.update(action="REJECT", reason="CRYPTO_SHORT_UNSUPPORTED")
            else:
                if self.name == "DIRECTION_BASELINE":
                    allowed, evidence = True, {"allowed": True, "reason": "BASELINE_COST_UNAWARE"}
                else:
                    evidence = entry_admission(forecast, quote, self.size, at, artifact, last_exit_ms=self.last_exit,
                                               no_trade_band=self.name == "COST_GATE_BAND", policy=self.policy, synthetic_ok=synthetic_ok)
                    allowed = evidence["allowed"]
                if allowed:
                    self.open(forecast, quote, at)
                decision.update(action="OPEN" if allowed else "REJECT", evidence=evidence,
                                reason="ACCEPTED" if allowed else ",".join(evidence["reasons"]))
            decision["position_after"] = str(self.quantity)
            self.decisions.append(decision)
        self.max_exposure = max(self.max_exposure, abs(self.quantity) * mid)
        equity = self.cash - self.carry
        if self.quantity:
            exit_price = self.price(quote, "SELL" if self.quantity > 0 else "BUY")
            equity += self.quantity * exit_price - commission(self.instrument, abs(self.quantity) * exit_price, fee_stress=self.policy.fee_stress)
        self.high_water = max(self.high_water, equity)
        self.max_drawdown = max(self.max_drawdown, self.high_water - equity)
        self.last_equity = equity
        self.marks.append({"at_ms": at, "net_liquidation_equity_usd": str(equity), "quantity": str(self.quantity)})

    def summary(self, duration_ms):
        exits = [trade for trade in self.trades if trade["event"] == "CLOSE"]
        reasons = {}
        for decision in self.decisions:
            if decision["action"] in ("REJECT", "HOLD", "NO_SAME_TICK_REENTRY"):
                reasons[decision["reason"]] = reasons.get(decision["reason"], 0) + 1
        return {
            "strategy": self.name, "complete_observed_path": self.path_complete,
            "net_liquidation_pnl_usd": str(self.last_equity - self.initial_cash) if self.path_complete else None,
            "partial_observed_net_liquidation_pnl_usd": str(self.last_equity - self.initial_cash),
            "commission_paid_usd": str(self.fees), "holding_haircut_usd": str(self.carry),
            "turnover_usd": str(self.turnover), "max_drawdown_usd": str(self.max_drawdown) if self.path_complete else None,
            "max_exposure_usd": str(self.max_exposure),
            "average_exposure_usd": str(self.exposure_integral / max(1, duration_ms)),
            "time_exposed_fraction": str(D(self.exposure_ms) / max(1, duration_ms)),
            "completed_round_trips": len(exits), "open_events": sum(trade["event"] == "OPEN" for trade in self.trades),
            "average_holding_ms_closed": sum(trade["holding_ms"] for trade in exits) / len(exits) if exits else None,
            "remaining_simulated_quantity": str(self.quantity), "missing_marks": self.missing_mark_count,
            "rejection_or_hold_reasons": reasons, "decisions": self.decisions, "trades": self.trades, "marks": self.marks,
            "net_basis": "SIMULATED_BID_ASK_LIQUIDATION_WITH_EXIT_FEE_AND_PROVISIONAL_CARRY",
            "actual_account_pnl": None, "realized_live_profit_proven": False,
        }


def compare(tape, artifacts=(), *, size="25000", capital="100000", policy=DEFAULT_POLICY):
    validate_tape(tape)
    policy.validate()
    if dec(size) <= 0 or dec(size) > 25000 or dec(capital) <= 0:
        raise CostError("REPLAY_SIZE_OR_CAPITAL_INVALID")
    states = [PathState(name, tape["instrument"], size, capital, policy) for name in NAMES]
    synthetic_ok = tape["source_kind"] == "SYNTHETIC"
    for row in tape["rows"]:
        for state in states:
            state.step(row, artifacts, synthetic_ok)
    duration = tape["rows"][-1]["at_ms"] - tape["rows"][0]["at_ms"]
    return {
        "mode": "THREE_INDEPENDENT_COUNTERFACTUAL_PATHS", "source_kind": tape["source_kind"],
        "input_sha256": digest(tape), "policy": asdict(policy), "instrument": tape["instrument"],
        "size": str(size), "initial_simulation_capital_usd": str(capital),
        "rows": len(tape["rows"]), "duration_ms": duration,
        "baseline_scope": "DIRECTION_RULE_ON_SHARED_POSITION_NEUTRAL_CANDIDATES_WITH_COMMON_RISK_EXITS_NOT_OLD_TRADE_RECONSTRUCTION",
        "live_profitability_proven": False, "broker_orders_sent": 0,
        "results": [state.summary(duration) for state in states], "candidate_outcomes": candidate_outcomes(tape),
    }


def stress_compare(tape, artifacts=(), **kwargs):
    policy = kwargs.pop("policy", DEFAULT_POLICY)
    cases = (("BASE", "1", "1"), ("SLIPPAGE_2X", "1", "2"), ("SLIPPAGE_5X", "1", "5"),
             ("FEES_125PCT", "1.25", "1"), ("FEES_150PCT_SLIPPAGE_5X", "1.5", "5"))
    return {name: compare(tape, artifacts, policy=replace(policy, fee_stress=fee, slippage_stress=slip), **kwargs)
            for name, fee, slip in cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tape", type=Path)
    group.add_argument("--capture", type=Path)
    parser.add_argument("--calibration", type=Path, action="append", default=[])
    parser.add_argument("--fit-cutoff-ms", type=int)
    parser.add_argument("--stress", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; never overwrite source/evidence")
    if args.capture:
        tape = capture_to_tape(read_capture(args.capture))
    else:
        if args.tape.stat().st_size > 64 * 1024 * 1024:
            raise CostError("TAPE_TOO_LARGE")
        tape = validate_tape(strict_loads(args.tape.read_text()))
    artifacts = [strict_loads(path.read_text()) for path in args.calibration]
    if args.fit_cutoff_ms is not None:
        report = fit_from_tape(tape, args.fit_cutoff_ms)
    else:
        report = stress_compare(tape, artifacts) if args.stress else compare(tape, artifacts)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    args.output.chmod(0o600)
    print(json.dumps({"output": str(args.output), "broker_orders_sent": 0, "live_profitability_proven": False}))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CostError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc), "broker_orders_sent": 0}), file=sys.stderr)
        raise SystemExit(2)
