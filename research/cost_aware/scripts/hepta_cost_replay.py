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
from collections import Counter
import copy
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


def fit_from_tape(tape, cutoff_ms, *, policy=DEFAULT_POLICY):
    outcomes = candidate_outcomes(tape, cutoff_ms=cutoff_ms)
    usable, excluded = [], []
    for item in outcomes:
        forecast = item["forecast"]
        reason = item["reason"]
        if reason == "OBSERVED":
            if forecast["action"] == "HOLD":
                reason = "HOLD_NOT_A_DIRECTIONAL_CALIBRATION_SAMPLE"
            elif item["forecast_available_at_ms"] - forecast["issued_at_ms"] > policy.forecast_entry_ttl_ms:
                reason = "FORECAST_TOO_LATE_FOR_CALIBRATION"
            elif item["forecast_available_at_ms"] >= item["label_observed_at_ms"]:
                reason = "FORECAST_KNOWN_AFTER_LABEL"
        if reason == "OBSERVED":
            usable.append(item)
        else:
            # Keep its realized outcome intact for audit, but one late inference
            # must not abort fitting every timely candidate in the same capture.
            excluded.append({"candidate_id": item["candidate_id"], "reason": reason})
    return {"artifact": build_calibration(usable, cutoff_ms, policy=policy),
            "candidate_outcomes": outcomes, "calibration_sample_exclusions": excluded,
            "activation_performed": False, "input_sha256": digest(tape),
            "sample_rows_usable_before_purging": len(usable)}


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


def chronological_assessment(tape, fit_cutoff_ms, decision_end_ms, *, size="25000",
                            capital="100000", max_drawdown_usd=None, policy=DEFAULT_POLICY):
    """Fixed-prefix training, later decisions, then quote-only outcome tail.

    Both time boundaries must be chosen explicitly, not optimized on results.
    This is one frozen holdout, not repeated fit-on-test, statistical significance,
    an account authorization, or an automatic artifact promotion.
    """
    validate_tape(tape)
    policy.validate()
    first, last = tape["rows"][0]["at_ms"], tape["rows"][-1]["at_ms"]
    if (type(fit_cutoff_ms) is not int or type(decision_end_ms) is not int or
            not first < fit_cutoff_ms < decision_end_ms < last):
        raise CostError("HOLDOUT_REQUIRES_TRAIN_DECISION_AND_OUTCOME_WINDOWS")
    budget = None if max_drawdown_usd is None else dec(max_drawdown_usd)
    if budget is not None and budget <= 0:
        raise CostError("HOLDOUT_DRAWDOWN_BUDGET_INVALID")
    training = {**tape, "rows": copy.deepcopy([row for row in tape["rows"] if row["at_ms"] < fit_cutoff_ms])}
    fitted = fit_from_tape(training, fit_cutoff_ms, policy=policy)
    artifact = fitted["artifact"]
    heldout, selected, purged = [], [], []
    for row in tape["rows"]:
        if row["at_ms"] <= fit_cutoff_ms:
            continue
        output = copy.deepcopy(row)
        forecast = row.get("forecast")
        if forecast:
            issued = forecast["issued_at_ms"]
            known = row.get("forecast_available_at_ms", row["at_ms"])
            reason = None
            if issued <= fit_cutoff_ms:
                reason = "FORECAST_STRADDLES_TRAIN_HOLDOUT_BOUNDARY"
            elif known > decision_end_ms or row["at_ms"] > decision_end_ms:
                reason = "OUTSIDE_PREDECLARED_DECISION_WINDOW"
            if reason:
                purged.append({"candidate_id": digest(forecast), "reason": reason})
                output["forecast"] = None
                output.pop("forecast_available_at_ms", None)
            else:
                selected.append(forecast)
        heldout.append(output)
    evaluation = {**tape, "rows": heldout}
    # One frozen, past-only artifact. Holdout outcomes never go into the fit.
    stress = stress_compare(evaluation, (artifact,), size=size, capital=capital, policy=policy)
    outcomes = stress["BASE"]["candidate_outcomes"]
    from hepta_cost_policy import calibration_key
    cohorts, last_end, exclusions = {}, {}, []
    for item in sorted(outcomes, key=lambda x: x["forecast"]["issued_at_ms"]):
        forecast = item["forecast"]
        reason = item["reason"]
        key = calibration_key(forecast)
        if forecast["action"] == "HOLD":
            reason = "HOLD_NOT_DIRECTIONAL"
        elif reason == "OBSERVED" and item["forecast_available_at_ms"] - forecast["issued_at_ms"] > policy.forecast_entry_ttl_ms:
            reason = "LATE_HOLDOUT_FORECAST"
        elif reason == "OBSERVED" and key not in artifact["cohorts"]:
            reason = "NO_TRAINED_COHORT"
        elif reason == "OBSERVED" and forecast["issued_at_ms"] >= artifact["valid_until_ms"]:
            reason = "FROZEN_CALIBRATION_EXPIRED"
        if reason != "OBSERVED":
            exclusions.append({"candidate_id": item["candidate_id"], "reason": reason})
            continue
        if forecast["issued_at_ms"] <= last_end.get(key, 0):
            exclusions.append({"candidate_id": item["candidate_id"], "reason": "OVERLAPPING_HOLDOUT_HORIZON"})
            continue
        last_end[key] = item["label_observed_at_ms"]
        values = cohorts.setdefault(key, {"count": 0, "utc_days": set()})
        values["count"] += 1
        values["utc_days"].add(forecast["issued_at_ms"] // 86400000)
    coverage = {key: {"nonoverlapping_samples": value["count"], "utc_dates": len(value["utc_days"]),
                      "engineering_minimum_met": value["count"] >= policy.min_calibration_samples and
                          len(value["utc_days"]) >= policy.min_calibration_days}
                for key, value in cohorts.items()}
    reasons = []
    if tape["source_kind"] != "IB_CAPTURE":
        reasons.append("SYNTHETIC_IS_NOT_REAL_HOLDOUT")
    if not artifact["cohorts"]:
        reasons.append("NO_QUALIFIED_TRAINING_COHORT")
    if not selected or not any(x["action"] != "HOLD" for x in selected):
        reasons.append("NO_DIRECTIONAL_HOLDOUT_CANDIDATES")
    if not coverage or not all(value["engineering_minimum_met"] for value in coverage.values()):
        reasons.append("INSUFFICIENT_NONOVERLAPPING_HELDOUT_COVERAGE")
    if any(item["forecast"]["action"] != "HOLD" and item["reason"] != "OBSERVED" for item in outcomes):
        reasons.append("INCOMPLETE_DIRECTIONAL_HOLDOUT_OUTCOMES")
    if budget is None:
        reasons.append("OPERATOR_DRAWDOWN_BUDGET_UNSET")
    # Absence of a trade is not evidence of transaction-cost profitability.
    # Every pressure case reconstructs its own three paths from an initial flat
    # state. Never remove bad baseline trades and reuse later positions.
    case_checks = {}
    for name, report in stress.items():
        baseline = report["results"][0]
        checks = []
        if not baseline["complete_observed_path"]:
            checks.append("BASELINE_PATH_INCOMPLETE")
        for path in report["results"][1:]:
            prefix = path["strategy"] + ":"
            if not path["complete_observed_path"]:
                checks.append(prefix + "PATH_INCOMPLETE")
            if path["completed_round_trips"] < policy.min_calibration_samples:
                checks.append(prefix + "INSUFFICIENT_COMPLETED_ROUNDTRIPS")
            if dec(path["remaining_simulated_quantity"]) != 0:
                checks.append(prefix + "OPEN_POSITION_AT_OUTCOME_END")
            net = path["net_liquidation_pnl_usd"]
            if net is None or dec(net) <= 0:
                checks.append(prefix + "NONPOSITIVE_OR_UNKNOWN_NET_RETURN")
            baseline_net = baseline["net_liquidation_pnl_usd"]
            if net is not None and baseline_net is not None and dec(net) < dec(baseline_net):
                checks.append(prefix + "BELOW_SAME_INPUT_BASELINE")
            drawdown = path["max_drawdown_usd"]
            if budget is not None and (drawdown is None or dec(drawdown) > budget):
                checks.append(prefix + "DRAWDOWN_BUDGET_NOT_MET")
        case_checks[name] = {"passed": not checks, "reasons": checks}
        if checks:
            reasons.append(name + "_COST_PATH_NOT_ACCEPTED")
    return {"schema": "heptatrader.cost-chronological-assessment.v1",
            "source_kind": tape["source_kind"], "input_sha256": digest(tape),
            "fit_cutoff_ms": fit_cutoff_ms, "decision_end_ms": decision_end_ms,
            "outcome_end_ms": last, "training_rows": len(training["rows"]),
            "holdout_rows_including_outcome_tail": len(heldout), "training": fitted,
            "purged_or_outside_window_candidates": purged, "holdout_cohort_coverage": coverage,
            "holdout_sample_exclusions": exclusions, "cost_case_checks": case_checks,
            "stress": stress, "operator_max_drawdown_usd": None if budget is None else str(budget),
            "engineering_minimum_per_cohort": {"nonoverlapping_samples": policy.min_calibration_samples,
                "utc_dates": policy.min_calibration_days, "completed_roundtrips_per_cost_path": policy.min_calibration_samples},
            "research_acceptance_passed": not reasons, "reasons": reasons,
            "acceptance_meaning": "FIXED_HOLDOUT_ENGINEERING_SCREEN_NOT_STATISTICAL_OR_BROKER_QUALIFICATION",
            "authorization_effect": "NONE", "calibration_activated": False,
            "trading_driver_switched": False, "broker_orders_sent": 0, "live_profitability_proven": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tape", type=Path)
    group.add_argument("--capture", type=Path)
    parser.add_argument("--calibration", type=Path, action="append", default=[])
    parser.add_argument("--fit-cutoff-ms", type=int)
    parser.add_argument("--stress", action="store_true")
    parser.add_argument("--holdout-decision-end-ms", type=int)
    parser.add_argument("--max-drawdown-usd")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.holdout_decision_end_ms is not None and (args.fit_cutoff_ms is None or args.calibration or args.stress):
        parser.error("holdout requires --fit-cutoff-ms and its own past-only fit; no supplied calibration/stress switch")
    if args.max_drawdown_usd is not None and args.holdout_decision_end_ms is None:
        parser.error("drawdown budget belongs to an explicit holdout assessment")
    if args.output.exists():
        parser.error("output already exists; never overwrite source/evidence")
    if args.capture:
        tape = capture_to_tape(read_capture(args.capture))
    else:
        if args.tape.stat().st_size > 64 * 1024 * 1024:
            raise CostError("TAPE_TOO_LARGE")
        tape = validate_tape(strict_loads(args.tape.read_text()))
    artifacts = [strict_loads(path.read_text()) for path in args.calibration]
    if args.holdout_decision_end_ms is not None:
        report = chronological_assessment(tape, args.fit_cutoff_ms, args.holdout_decision_end_ms,
                                          max_drawdown_usd=args.max_drawdown_usd)
    elif args.fit_cutoff_ms is not None:
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
