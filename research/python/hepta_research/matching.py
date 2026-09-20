"""Deterministic OFFLINE order-flow replay; never an Execution Service adapter.

Input is explicit external/research order arrival and cancellation, not a depth
snapshot or a guessed tick-to-queue conversion. Prices are exact integer ticks.
"""
from __future__ import annotations

from copy import copy
from dataclasses import asdict, dataclass, replace
from decimal import Decimal, localcontext
from typing import Mapping

from .fifo import FifoAccount, checked
from .model import HypotheticalFill, number
from .pipeline import IDENTITY

MAX_TIME = 2**63-1
MAX_QUANTITY = 10**12
ACTORS = {"RESEARCH", "EXTERNAL"}
SIDES = {"BUY", "SELL"}


def bound(value: object, low: int, high: int, name: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(name)
    return value


def identity(value: object) -> str:
    if not isinstance(value, str) or not IDENTITY.fullmatch(value):
        raise ValueError("invalid replay identity")
    return value


def fields(value: object, names: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != names:
        raise ValueError("unknown/missing event fields")
    return value


@dataclass(frozen=True)
class InstrumentSpec:
    tick_size: object
    multiplier: object = 1
    lot: int = 1
    fee_per_unit: object = 0
    fee_rate: object = 0

    def checked(self) -> InstrumentSpec:
        tick, multiplier = number(self.tick_size, positive=True), number(self.multiplier, positive=True)
        lot = bound(self.lot, 1, MAX_QUANTITY, "integer lot")
        fee, rate = number(self.fee_per_unit), number(self.fee_rate)
        if fee < 0 or not 0 <= rate <= 1:
            raise ValueError("nonnegative fee and 0..1 notional fee rate required")
        return InstrumentSpec(tick, multiplier, lot, fee, rate)

    def price(self, ticks: object) -> Decimal:
        ticks = bound(ticks, -2**63, MAX_TIME, "integer price ticks")
        with localcontext() as context:
            context.prec = 128
            return checked(Decimal(ticks)*self.tick_size)


@dataclass(frozen=True)
class BookOrder:
    order_id: str
    instrument: str
    actor: str
    side: str
    quantity: int
    remaining: int
    filled: int
    canceled: int
    limit_ticks: int | None
    time_in_force: str
    arrival_seq: int
    arrival_us: int
    reason: str = ""

    def record(self) -> dict:
        status = ("PARTIAL" if self.filled else "OPEN") if self.remaining else (
            "FILLED" if self.filled == self.quantity else "CANCELED")
        return {**asdict(self), "status": status}


class OrderFlowReplay:
    """Bounded single-writer replay with preflight/commit event transactions.

    Research-vs-research crossing cancels the aggressor remainder (no wash
    fills). FOK preflights the complete walk before ANY liquidity/account change.
    External orders share the same queues, so explicit queue-ahead volume is
    consumed exactly once. No snapshot refresh silently replenishes liquidity.
    """
    def __init__(self, instruments: Mapping[str, InstrumentSpec], *, capital: object,
                 currency: str, max_mark_age_us: int, max_events: int = 100000,
                 max_trades: int = 100000, max_active_orders: int = 4096) -> None:
        if not isinstance(instruments, Mapping) or not 1 <= len(instruments) <= 64:
            raise ValueError("1..64 instrument models required")
        if not isinstance(currency, str) or len(currency) != 3 or not currency.isascii() or not currency.isupper() or not currency.isalpha():
            raise ValueError("explicit three-letter accounting currency required")
        self.models = {}
        for name, spec in instruments.items():
            if not isinstance(spec, InstrumentSpec):
                raise ValueError("instrument model required")
            self.models[identity(name)] = spec.checked()
        self.capital, self.currency = number(capital, positive=True), currency
        self.max_age = bound(max_mark_age_us, 0, MAX_TIME, "mark age")
        self.max_events = bound(max_events, 1, 1000000, "event bound")
        self.max_trades = bound(max_trades, 1, 1000000, "trade bound")
        self.max_active = bound(max_active_orders, 1, 100000, "active order bound")
        self._accounts = {name: FifoAccount(self.capital, spec.multiplier)
                          for name, spec in self.models.items()}
        self._active: dict[str, dict[str, BookOrder]] = {name: {} for name in self.models}
        self._orders: dict[str, BookOrder] = {}
        self._marks: dict[str, tuple[Decimal, int]] = {}
        self._trades: list[dict] = []
        self._fills: list[dict] = []
        self._events: list[dict] = []
        self._last_seq = self._last_us = -1
        self._active_count = 0

    def _snapshot(self, timestamp: int, accounts: dict, marks: dict) -> dict:
        with localcontext() as context:
            context.prec = 128
            # Same single-capital convention as portfolio_replay. Component
            # initial bases are computational only, never additional funding.
            cash = checked(self.capital + sum((a.cash-self.capital for a in accounts.values()), Decimal(0)))
            fees = checked(sum((a.fees for a in accounts.values()), Decimal(0)))
            realized = checked(sum((a.realized for a in accounts.values()), Decimal(0)))
            details, stale = {}, []
            gross = unrealized = Decimal(0)
            for name, account in sorted(accounts.items()):
                missing = bool(account.quantity) and (name not in marks or timestamp-marks[name][1] > self.max_age)
                if missing:
                    stale.append(name)
                    detail = {"quantity": account.quantity, "realized_pnl": account.realized,
                              "unrealized_pnl": None, "fees": account.fees, "equity": None}
                else:
                    mark = marks[name][0] if name in marks else Decimal(0)
                    detail = account.attribution(mark)
                    unrealized = checked(unrealized+detail["unrealized_pnl"])
                    gross = checked(gross+abs(account.quantity*mark*account.multiplier))
                # Do not expose per-instrument equity with a repeated capital.
                detail.pop("equity")
                details[name] = detail
            equity = None if stale else checked(self.capital+realized+unrealized-fees)
        return {"timestamp_us": timestamp, "cash_inventory_balance": cash, "equity": equity,
                "fees": fees, "realized_pnl": realized,
                "unrealized_pnl": None if stale else unrealized,
                "gross_notional": None if stale else gross,
                "valuation_complete": not stale, "stale_instruments": stale,
                "instruments": details}

    def _new_order(self, event: dict, name: str, seq: int, time: int) -> BookOrder:
        fields(event, {"kind", "seq", "timestamp_us", "instrument", "order_id", "actor",
                       "side", "quantity", "limit_ticks", "time_in_force"})
        oid = identity(event["order_id"])
        actor, side, tif = event["actor"], event["side"], event["time_in_force"]
        if not isinstance(actor, str) or actor not in ACTORS or not isinstance(side, str) or side not in SIDES:
            raise ValueError("order actor/side")
        if not isinstance(tif, str) or tif not in {"GTC", "DAY", "IOC", "FAK", "FOK"}:
            raise ValueError("time in force")
        if oid in self._orders:
            raise ValueError("order identity must never be reused")
        qty = bound(event["quantity"], 1, MAX_QUANTITY, "order units")
        if qty % self.models[name].lot:
            raise ValueError("off-lot order")
        ticks = event["limit_ticks"]
        if ticks is None:
            if tif not in {"IOC", "FAK", "FOK"}:
                raise ValueError("unpriced order cannot rest")
        else:
            self.models[name].price(ticks)
        return BookOrder(oid, name, actor, side, qty, qty, 0, 0, ticks,
                         "IOC" if tif == "FAK" else tif, seq, time)

    def _plan(self, incoming: BookOrder, active: dict) -> tuple[list[tuple[BookOrder, int]], bool]:
        makers = [o for o in active.values() if o.side != incoming.side]
        makers.sort(key=lambda o: (o.limit_ticks if incoming.side == "BUY" else -o.limit_ticks, o.arrival_seq))
        needed, trades, self_cross = incoming.quantity, [], False
        for maker in makers:
            if incoming.limit_ticks is not None and (
                maker.limit_ticks > incoming.limit_ticks if incoming.side == "BUY" else maker.limit_ticks < incoming.limit_ticks
            ):
                break
            if maker.actor == incoming.actor == "RESEARCH":
                self_cross = True
                break
            taken = min(needed, maker.remaining)
            trades.append((maker, taken))
            needed -= taken
            if not needed:
                break
        return trades, self_cross

    def apply(self, event: dict) -> dict:
        """Apply one complete event or raise with the entire prior state intact."""
        if not isinstance(event, dict) or not {"kind", "seq", "timestamp_us", "instrument"} <= set(event):
            raise ValueError("event envelope")
        if len(self._events) >= self.max_events:
            raise ValueError("event bound exceeded")
        seq = bound(event["seq"], 0, MAX_TIME, "event sequence")
        timestamp = bound(event["timestamp_us"], 0, MAX_TIME, "event timestamp")
        if seq <= self._last_seq or timestamp < self._last_us:
            raise ValueError("event sequence/time regression")
        name = identity(event["instrument"])
        if name not in self.models:
            raise ValueError("undeclared instrument")
        kind = event["kind"]
        if not isinstance(kind, str):
            raise ValueError("event kind")
        active = dict(self._active[name])
        updates, trades, fills = {}, [], []
        accounts, marks = dict(self._accounts), dict(self._marks)
        account = copy(accounts[name])
        accounts[name] = account
        receipt = {"kind": kind, "seq": seq, "timestamp_us": timestamp, "instrument": name}
        if kind == "order":
            incoming = self._new_order(event, name, seq, timestamp)
            plan, self_cross = self._plan(incoming, active)
            executed = sum(qty for _, qty in plan)
            if incoming.time_in_force == "FOK" and executed != incoming.quantity:
                incoming = replace(incoming, remaining=0, canceled=incoming.quantity,
                                   reason="FOK_SELF_TRADE" if self_cross else "FOK_INSUFFICIENT_LIQUIDITY")
                plan = []
            else:
                if len(self._trades)+len(plan) > self.max_trades:
                    raise ValueError("trade bound exceeded")
                with localcontext() as context:
                    context.prec = 128
                    for index, (maker, qty) in enumerate(plan):
                        price = self.models[name].price(maker.limit_ticks)
                        trade = {"event_seq": seq, "match_index": index, "timestamp_us": timestamp,
                                 "instrument": name, "maker_id": maker.order_id, "taker_id": incoming.order_id,
                                 "price_ticks": maker.limit_ticks, "price": price, "quantity": qty}
                        trades.append(trade)
                        own = incoming if incoming.actor == "RESEARCH" else maker if maker.actor == "RESEARCH" else None
                        if own is not None:
                            spec = self.models[name]
                            fee = checked(Decimal(qty)*(spec.fee_per_unit+abs(price)*spec.multiplier*spec.fee_rate))
                            fill = HypotheticalFill(timestamp, Decimal(qty if own.side == "BUY" else -qty), price, fee)
                            account.fill(fill)
                            fills.append({**trade, "order_id": own.order_id, "side": own.side,
                                          "liquidity": "TAKER" if own is incoming else "MAKER",
                                          "delta": fill.delta, "fee": fee})
                        updated = replace(maker, remaining=maker.remaining-qty, filled=maker.filled+qty)
                        updates[maker.order_id] = updated
                        if updated.remaining:
                            active[maker.order_id] = updated
                        else:
                            del active[maker.order_id]
                incoming = replace(incoming, remaining=incoming.quantity-executed, filled=executed)
                if incoming.remaining and (incoming.time_in_force == "IOC" or self_cross):
                    incoming = replace(incoming, remaining=0, canceled=incoming.remaining,
                                       reason="SELF_TRADE_PREVENTED" if self_cross else "IOC_REMAINDER")
                if incoming.remaining:
                    active[incoming.order_id] = incoming
            updates[incoming.order_id] = incoming
            receipt["order"] = incoming.record()
        elif kind == "cancel":
            fields(event, {"kind", "seq", "timestamp_us", "instrument", "order_id", "actor", "quantity"})
            oid = identity(event["order_id"])
            order = self._orders.get(oid)
            if order is None or order.instrument != name or order.actor != event["actor"]:
                raise ValueError("cancel identity/instrument/actor")
            requested = bound(event["quantity"], 0, MAX_QUANTITY, "cancel units; zero means all remaining")
            qty = requested or order.remaining
            if qty > order.remaining or qty % self.models[name].lot:
                raise ValueError("over-cancel/off-lot cancel")
            updated = replace(order, remaining=order.remaining-qty, canceled=order.canceled+qty,
                              reason="USER_CANCEL" if qty else order.reason)
            updates[oid] = updated
            if updated.remaining:
                active[oid] = updated
            else:
                active.pop(oid, None)
            receipt["order"] = updated.record()
            receipt["already_terminal"] = not qty
        elif kind == "session_end":
            fields(event, {"kind", "seq", "timestamp_us", "instrument"})
            expired = []
            for oid, order in list(active.items()):
                if order.time_in_force == "DAY":
                    updates[oid] = replace(order, remaining=0, canceled=order.canceled+order.remaining,
                                           reason="SESSION_ENDED")
                    del active[oid]
                    expired.append(oid)
            receipt["expired_order_ids"] = sorted(expired)
        elif kind in {"mark", "basis_rebase"}:
            fields(event, {"kind", "seq", "timestamp_us", "instrument", "price_ticks"})
            price = self.models[name].price(event["price_ticks"])
            marks[name] = (price, timestamp)
            if kind == "basis_rebase":
                receipt["attribution_transfer"] = account.rebase(price)
                receipt["cash_transfer"] = Decimal(0)
        else:
            raise ValueError("unsupported event kind")
        active_count = self._active_count-len(self._active[name])+len(active)
        if active_count > self.max_active:
            raise ValueError("active order bound exceeded")
        # Preflight aggregate arithmetic as well as each instrument's ledger.
        # Stale/missing marks are an explicit null valuation, not an exception.
        snapshot = self._snapshot(timestamp, accounts, marks)
        for order in updates.values():
            if order.quantity != order.remaining+order.filled+order.canceled:
                raise ArithmeticError("order conservation invariant")
        self._active[name], self._active_count = active, active_count
        self._accounts, self._marks = accounts, marks
        self._orders.update(updates)
        self._trades.extend(trades)
        self._fills.extend(fills)
        self._last_seq, self._last_us = seq, timestamp
        self._events.append(receipt)
        # Never return a mutable alias into retained replay state.
        from copy import deepcopy
        return deepcopy({"event": receipt, "trades": trades, "fills": fills, "snapshot": snapshot})

    def report(self) -> dict:
        from copy import deepcopy
        snapshot = self._snapshot(max(0, self._last_us), self._accounts, self._marks)
        return deepcopy({"schema": "hepta.research.order-flow-report.v1",
            "mode": "OFFLINE_HYPOTHETICAL", "matching_model": "explicit_price_time_v1",
            "accounting_model": "existing_cash_inventory_ledger_with_fifo_attribution",
            "assumptions": {"capital": self.capital, "currency": self.currency,
                "max_mark_age_us": self.max_age, "price_rule": "resting_limit_price",
                "priority": "price_then_arrival_sequence", "self_trade": "cancel_aggressor_remainder",
                "liquidity": "explicit_order_flow_only", "automatic_funding": False,
                "margin_model": False, "exchange_settlement": False, "fx_conversion": False,
                "broker_authorized": False, "forced_final_liquidation": False},
            "bounds": {"max_events": self.max_events, "max_trades": self.max_trades,
                       "max_active_orders": self.max_active},
            "models": {name: asdict(spec) for name, spec in sorted(self.models.items())},
            "events": self._events, "trades": self._trades, "fills": self._fills,
            "orders": [order.record() for order in sorted(self._orders.values(), key=lambda o: o.arrival_seq)],
            "active_order_count": self._active_count, "final": snapshot,
            "marks": {name: {"price": mark, "timestamp_us": time} for name, (mark, time) in sorted(self._marks.items())},
            "metrics": {"annualized": None, "reason": "irregular_explicit_event_clock"}})
