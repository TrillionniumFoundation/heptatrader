"""Offline FIFO attribution over the existing cash/inventory ResearchLedger.

Net positions only: no hedge buckets, exchange settlement, margin approval or
broker state. A basis rebase changes attribution, never cash or total equity.
"""
from __future__ import annotations

from collections import deque
from copy import copy
from dataclasses import dataclass
from decimal import Decimal, localcontext

from .model import HypotheticalFill, ResearchLedger, number


def checked(value: Decimal) -> Decimal:
    """Remove insignificant zeros; never round a nonrepresentable amount."""
    with localcontext() as context:
        context.prec = 128
        return number(value.normalize())


@dataclass(frozen=True)
class Lot:
    quantity: int
    basis: Decimal


class FifoAccount:
    """One existing ledger, plus immutable lot detail for P&L attribution.

    A failed fill/rebase leaves all prior state unchanged. Integer units and
    explicit multipliers mirror the selected legacy net-position responsibility,
    not its raw structs, process controls or complete exchange semantics.
    """
    def __init__(self, capital: object, multiplier: object = 1, *, max_lots: int = 10000) -> None:
        if type(max_lots) is not int or not 1 <= max_lots <= 100000:
            raise ValueError("FIFO lot bound")
        self.initial = number(capital, positive=True)
        self._ledger = ResearchLedger(self.initial, multiplier)
        self.max_lots = max_lots
        self.lots: tuple[Lot, ...] = ()
        self.realized = Decimal(0)

    @property
    def quantity(self) -> Decimal:
        return self._ledger.quantity

    @property
    def cash(self) -> Decimal:
        return self._ledger.cash

    @property
    def fees(self) -> Decimal:
        return self._ledger.fees

    @property
    def multiplier(self) -> Decimal:
        return self._ledger.multiplier

    def fill(self, fill: HypotheticalFill) -> None:
        if not isinstance(fill, HypotheticalFill):
            raise ValueError("hypothetical fill required")
        delta = number(fill.delta)
        if not delta or delta != delta.to_integral_value() or delta.copy_abs() > 10**12:
            raise ValueError("nonzero bounded integer fill units required")
        price, fee = number(fill.price), number(fill.fee)
        # This copy is the only cash/inventory mutation. Commit it last.
        ledger = copy(self._ledger)
        ledger.fill(HypotheticalFill(fill.timestamp_us, delta, price, fee))
        lots = deque(self.lots)
        remaining, realized = int(delta), self.realized
        with localcontext() as context:
            context.prec = 128
            while remaining and lots and (remaining > 0) != (lots[0].quantity > 0):
                first = lots.popleft()
                sign = 1 if first.quantity > 0 else -1
                closed = min(abs(remaining), abs(first.quantity))
                realized = checked(realized + (price-first.basis)*sign*closed*self.multiplier)
                residual = first.quantity-sign*closed
                remaining += sign*closed
                if residual:
                    lots.appendleft(Lot(residual, first.basis))
            if remaining:
                if lots and lots[-1].basis == price:
                    last = lots.pop()
                    lots.append(Lot(last.quantity+remaining, price))
                else:
                    lots.append(Lot(remaining, price))
            if len(lots) > self.max_lots:
                raise ValueError("FIFO lot bound exceeded")
            # Independent identity at mark zero catches sign/reversal errors
            # without importing an observation or inventing a market mark.
            unmarked = checked(sum((-lot.basis*lot.quantity*self.multiplier for lot in lots), Decimal(0)))
            if sum(lot.quantity for lot in lots) != ledger.quantity:
                raise ArithmeticError("FIFO inventory identity")
            if checked(self.initial+realized+unmarked-ledger.fees) != ledger.cash:
                raise ArithmeticError("FIFO cash attribution identity")
        self._ledger, self.lots, self.realized = ledger, tuple(lots), realized

    def attribution(self, mark: object) -> dict:
        mark = number(mark)
        with localcontext() as context:
            context.prec = 128
            unrealized = checked(sum(((mark-lot.basis)*lot.quantity*self.multiplier
                                     for lot in self.lots), Decimal(0)))
            equity = self._ledger.equity(mark)
            if checked(self.initial+self.realized+unrealized-self.fees) != equity:
                raise ArithmeticError("FIFO marked equity identity")
        return {"quantity": self.quantity, "realized_pnl": self.realized,
                "unrealized_pnl": unrealized, "fees": self.fees, "equity": equity}

    def rebase(self, mark: object) -> Decimal:
        """Explicit research basis rebase; NOT a cash-settlement instruction."""
        mark = number(mark)
        detail = self.attribution(mark)
        with localcontext() as context:
            context.prec = 128
            realized = checked(self.realized+detail["unrealized_pnl"])
        lots = (Lot(int(self.quantity), mark),) if self.quantity else ()
        self.lots, self.realized = lots, realized
        return detail["unrealized_pnl"]
