"""Pure target planning and offline accounting extracted from legacy responsibilities.

This is a new interface, NOT an ABI-compatible copy of HeptaDLL. See
research/README.md for changed semantics and retained source provenance.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
import math
import statistics
from typing import Callable, Iterable


def number(value: object, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean is not a quantity")
    if len(str(value)) > 128:
        raise ValueError("decimal length")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid decimal") from exc
    if not result.is_finite() or result.copy_abs() > Decimal("1e18") or result.as_tuple().exponent < -18:
        raise ValueError("nonfinite or excessive decimal")
    if positive and result <= 0:
        raise ValueError("positive decimal required")
    return result


@dataclass(frozen=True)
class PositionObservation:
    """A client projection, not a statement of authoritative broker state."""
    quantity: Decimal
    observed_at_ms: int
    complete: bool
    has_active_orders: bool


@dataclass(frozen=True)
class TargetPolicy:
    max_abs_target: Decimal
    max_order: Decimal
    lot: Decimal
    max_age_ms: int = 1000
    long_only: bool = False

    def delta(self, target: object, snapshot: PositionObservation, now_ms: int) -> Decimal:
        maximum = number(self.max_abs_target, positive=True)
        cap, lot = number(self.max_order, positive=True), number(self.lot, positive=True)
        target, current = number(target), number(snapshot.quantity)
        if not isinstance(self.max_age_ms, int) or isinstance(self.max_age_ms, bool) or self.max_age_ms <= 0:
            raise ValueError("invalid age bound")
        if not isinstance(now_ms, int) or isinstance(now_ms, bool) or now_ms <= 0:
            raise ValueError("invalid current time")
        if not isinstance(snapshot.observed_at_ms, int) or isinstance(snapshot.observed_at_ms, bool):
            raise ValueError("invalid observation time")
        if snapshot.complete is not True or snapshot.has_active_orders is not False:
            raise ValueError("incomplete position or active orders: reconcile first")
        if not 0 < snapshot.observed_at_ms <= now_ms or now_ms-snapshot.observed_at_ms > self.max_age_ms:
            raise ValueError("stale/future observation")
        if target.copy_abs() > maximum or (self.long_only and target < 0):
            raise ValueError("target outside strategy bounds")
        with localcontext() as ctx:
            ctx.prec = 128
            if target % lot or current % lot or cap % lot:
                raise ValueError("fractional lot")
            # Close first; a NEW authoritative observation must precede the
            # opposite opening leg. Do not infer a fill from this proposal.
            reversing = (current < 0 < target) or (target < 0 < current)
            effective_target = Decimal(0) if reversing else target
            change = effective_target-current
            return max(-cap, min(cap, change))


@dataclass(frozen=True)
class ReplayBar:
    begin_us: int
    end_us: int
    open: Decimal
    close: Decimal
    complete: bool = True


@dataclass(frozen=True)
class HypotheticalFill:
    timestamp_us: int
    delta: Decimal
    price: Decimal
    fee: Decimal


class ResearchLedger:
    """Offline cash + marked inventory only; no automatic funding/margin model.

    Quantity is signed; prices may be negative for historical futures data.
    Events are hypothetical or explicitly imported. They are NEVER fed back
    into Gateway as authoritative positions, fills, prices, or risk approval.
    """
    def __init__(self, capital: object, multiplier: object = 1) -> None:
        self.cash = number(capital, positive=True)
        self.multiplier = number(multiplier, positive=True)
        self.quantity = Decimal(0)
        self.fees = Decimal(0)
        self.last_fill_us = -1

    def fill(self, fill: HypotheticalFill) -> None:
        delta, price, fee = number(fill.delta), number(fill.price), number(fill.fee)
        if type(fill.timestamp_us) is not int or fill.timestamp_us < 0 or fill.timestamp_us < self.last_fill_us or fee < 0:
            raise ValueError("fill time/fee")
        with localcontext() as ctx:
            ctx.prec = 128
            cash = number(self.cash - delta*price*self.multiplier-fee)
            quantity, fees = number(self.quantity+delta), number(self.fees+fee)
        self.cash, self.quantity, self.fees = cash, quantity, fees
        self.last_fill_us = fill.timestamp_us

    def equity(self, mark: object) -> Decimal:
        with localcontext() as ctx:
            ctx.prec = 128
            return number(self.cash+self.quantity*number(mark)*self.multiplier)


def replay(bars: Iterable[ReplayBar], target: Callable[[ReplayBar], object], *,
           capital: object, max_abs_target: object, multiplier: object = 1,
           slippage: object = 0, fee_per_unit: object = 0) -> dict:
    """A closed bar's target can fill only at a LATER bar's open.

    Deliberately simple explicit fill assumption, not Pegasus queue-matching
    equivalence. No same-bar hindsight fills and no fill for a final target.
    A final incomplete bar is marked but never used to generate a signal.
    """
    ledger = ResearchLedger(capital, multiplier)
    slip, fee, cap = number(slippage), number(fee_per_unit), number(max_abs_target, positive=True)
    if slip < 0 or fee < 0:
        raise ValueError("negative execution cost")
    pending: Decimal | None = None
    previous_end = -1
    fills: list[HypotheticalFill] = []
    equities: list[Decimal] = []
    incomplete_seen = False
    for bar in bars:
        if type(bar.begin_us) is not int or type(bar.end_us) is not int or type(bar.complete) is not bool:
            raise ValueError("bar types")
        if bar.begin_us < 0 or bar.begin_us < previous_end or bar.end_us <= bar.begin_us or incomplete_seen:
            raise ValueError("invalid/overlapping bars or data after incomplete final bar")
        opening, close = number(bar.open), number(bar.close)
        if pending is not None:
            with localcontext() as ctx:
                ctx.prec = 128
                delta = pending-ledger.quantity
                if delta:
                    fill = HypotheticalFill(bar.begin_us, delta, opening+(slip if delta>0 else -slip), abs(delta)*fee)
                    ledger.fill(fill)
                    fills.append(fill)
        equities.append(ledger.equity(close))
        pending = number(target(bar)) if bar.complete else None
        if pending is not None and pending.copy_abs()>cap:
            raise ValueError("research target exceeds explicit bound")
        incomplete_seen = not bar.complete
        previous_end = bar.end_us
    return {"mode": "OFFLINE_HYPOTHETICAL", "fill_model": "next_bar_open_fixed_cost",
            "fills": fills, "equity": equities, "pending_target": pending,
            "position": ledger.quantity, "fees": ledger.fees}


def performance(equities: Iterable[object], periods_per_year: int, risk_free_per_period: object = 0) -> dict:
    """Explicit equally-spaced evaluation periods. Undefined ratios are None.

    No implicit 16:00 close, 252-day calendar, or artificial capital injection.
    Cash flows must be removed/unitized by the caller before this function.
    """
    if type(periods_per_year) is not int or periods_per_year <= 0 or periods_per_year > 1000000:
        raise ValueError("annualization frequency")
    values = [number(v, positive=True) for v in equities]
    if not values:
        raise ValueError("empty equity series")
    rf = float(number(risk_free_per_period))
    with localcontext() as ctx:
        ctx.prec = 128
        returns = [float(b/a-1) for a,b in zip(values, values[1:])]
        total_return = float(values[-1]/values[0]-1)
        peak = values[0]
        drawdowns = []
        for v in values:
            peak = max(peak,v)
            drawdowns.append(float((peak-v)/peak))
    vol = statistics.stdev(returns)*math.sqrt(periods_per_year) if len(returns)>1 else None
    sharpe = ((statistics.mean(returns)-rf)*periods_per_year/vol) if vol else None
    downside = math.sqrt(sum(min(r-rf,0)**2 for r in returns)/len(returns)*periods_per_year) if returns else None
    sortino = ((statistics.mean(returns)-rf)*periods_per_year/downside) if downside else None
    return {"periods": len(returns), "total_return": total_return,
            "max_drawdown": max(drawdowns), "annual_volatility": vol,
            "sharpe": sharpe, "sortino": sortino}
