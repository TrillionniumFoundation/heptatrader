"""SDK-free normalized-bar -> causal strategy -> offline report consumer.

This module has no Gateway import or order route. The reference moving-average
strategy is a new example, not a claimed reproduction of a legacy strategy.
"""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict
from datetime import date
from decimal import Decimal, localcontext
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from .model import ReplayBar, number, performance, replay

HEADER = "instrument,trading_day,begin_us,end_us,open,high,low,close,volume,ticks,complete"
IDENTITY = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
INTEGER = re.compile(r"-?[0-9]{1,20}\Z")
MAX_ROWS = 1000000


def _integer(text: str, low: int, high: int) -> int:
    if not INTEGER.fullmatch(text) or (low >= 0 and text.startswith("-")):
        raise ValueError("integer syntax")
    value = int(text)
    if not low <= value <= high:
        raise ValueError("integer range")
    return value


def read_bars(path: Path, tick_size: object, max_bars: int = 100000) -> tuple[list[ReplayBar], dict]:
    """Read once, hash those same bytes, and reject the entire malformed stream."""
    if type(max_bars) is not int or not 1 <= max_bars <= MAX_ROWS:
        raise ValueError("bar count bound")
    scale = number(tick_size, positive=True)
    digest = hashlib.sha256()
    bars: list[ReplayBar] = []
    instrument = previous_day = ""
    with Path(path).open("rb") as source:
        def line() -> str | None:
            raw = source.readline(4098)
            if not raw:
                return None
            digest.update(raw)
            text = raw.rstrip(b"\n")
            if text.endswith(b"\r"):
                text = text[:-1]
            if len(text) > 4096 or b"\r" in text or b"\n" in text:
                raise ValueError("bar row length/line ending")
            return text.decode("ascii")
        if line() != HEADER:
            raise ValueError("normalized bar header required")
        while (text := line()) is not None:
            if len(bars) >= max_bars:
                raise ValueError("bar count bound exceeded")
            fields = text.split(",")
            if len(fields) != 11 or any(not item for item in fields):
                raise ValueError("bar field count/empty field")
            name, day = fields[:2]
            if not IDENTITY.fullmatch(name) or (instrument and name != instrument):
                raise ValueError("foreign/invalid instrument")
            if not re.fullmatch(r"[0-9]{8}", day) or int(day[:4]) < 1600:
                raise ValueError("trading day format/range")
            date(int(day[:4]), int(day[4:6]), int(day[6:]))
            if day < previous_day:
                raise ValueError("trading day regression")
            begin, end = (_integer(v, 0, 2**63-1) for v in fields[2:4])
            opening, high, low, close = (_integer(v, -2**63, 2**63-1) for v in fields[4:8])
            _integer(fields[8], 0, 2**64-1)
            _integer(fields[9], 1, 2**64-1)
            if fields[10] not in ("0", "1") or end <= begin:
                raise ValueError("bar interval/completeness")
            if low > min(opening, close) or high < max(opening, close):
                raise ValueError("inconsistent OHLC")
            if bars and (not bars[-1].complete or begin < bars[-1].end_us):
                raise ValueError("overlap/order or data after incomplete bar")
            with localcontext() as ctx:
                ctx.prec = 128
                prices = [number(Decimal(v)*scale) for v in (opening, high, low, close)]
            bars.append(ReplayBar(begin, end, prices[0], prices[3], fields[10] == "1"))
            instrument, previous_day = name, day
    if not bars:
        raise ValueError("empty bar stream")
    return bars, {"bars_sha256": digest.hexdigest(), "instrument": instrument,
                  "bar_count": len(bars), "tick_size": scale}


class MovingAverageTarget:
    """Bounded close-only reference signal; incomplete bars never advance state."""
    def __init__(self, fast: int, slow: int, quantity: object, long_only: bool = False) -> None:
        if type(fast) is not int or type(slow) is not int or not 1 <= fast < slow <= 100000:
            raise ValueError("require 1 <= fast < slow <= 100000")
        if type(long_only) is not bool:
            raise ValueError("long_only must be boolean")
        self.fast, self.slow = fast, slow
        self.quantity, self.long_only = number(quantity, positive=True), long_only
        self.fast_values: deque[Decimal] = deque()
        self.slow_values: deque[Decimal] = deque()
        self.fast_sum = self.slow_sum = Decimal(0)
        self.last_end = -1

    def __call__(self, bar: ReplayBar) -> Decimal:
        if (bar.complete is not True or type(bar.begin_us) is not int or type(bar.end_us) is not int
                or bar.begin_us < 0 or bar.begin_us < self.last_end or bar.end_us <= bar.begin_us):
            raise ValueError("closed ordered bar required")
        close = number(bar.close)
        with localcontext() as ctx:
            ctx.prec = 128
            self.fast_values.append(close)
            self.slow_values.append(close)
            self.fast_sum += close
            self.slow_sum += close
            if len(self.fast_values) > self.fast:
                self.fast_sum -= self.fast_values.popleft()
            if len(self.slow_values) > self.slow:
                self.slow_sum -= self.slow_values.popleft()
            self.last_end = bar.end_us
            if len(self.slow_values) < self.slow:
                return Decimal(0)
            comparison = self.fast_sum*self.slow - self.slow_sum*self.fast
            if comparison > 0:
                return self.quantity
            return self.quantity.copy_negate() if comparison < 0 and not self.long_only else Decimal(0)


def run_report(bars_path: Path, *, tick_size: object, capital: object, quantity: object,
               fast: int = 5, slow: int = 20, long_only: bool = False,
               multiplier: object = 1, slippage: object = 0, fee_per_unit: object = 0,
               periods_per_year: int | None = None, max_bars: int = 100000) -> dict:
    bars, metadata = read_bars(bars_path, tick_size, max_bars)
    strategy = MovingAverageTarget(fast, slow, quantity, long_only)
    initial = number(capital, positive=True)
    result = replay(bars, strategy, capital=initial, max_abs_target=quantity,
                    multiplier=multiplier, slippage=slippage, fee_per_unit=fee_per_unit)
    values = result["equity"]
    with localcontext() as ctx:
        ctx.prec = 128
        peak, drawdown = initial, Decimal(0)
        for value in values:
            peak = max(peak, value)
            drawdown = max(drawdown, (peak-value)/peak)
        metrics = {"total_return": values[-1]/initial-1, "max_drawdown": drawdown,
                   "annualized": None, "annualization_reason": "not_requested"}
    if periods_per_year is not None:
        if type(periods_per_year) is not int or not 1 <= periods_per_year <= 1000000:
            raise ValueError("annualization frequency")
        # The final incomplete close is NOT an observation at its nominal end.
        # Exclude it and require equally spaced completed observations.
        completed = [(bar.end_us, value) for bar, value in zip(bars, values) if bar.complete]
        spacings = {b[0]-a[0] for a, b in zip(completed, completed[1:])}
        if len(spacings) > 1:
            raise ValueError("annualization requires equally spaced complete bars")
        if len(completed) < 2:
            metrics["annualization_reason"] = "insufficient_complete_observations"
        elif any(value <= 0 for _, value in completed):
            metrics["annualization_reason"] = "nonpositive_equity"
        else:
            metrics["annualized"] = performance([value for _, value in completed], periods_per_year)
            metrics["annualization_reason"] = "explicit_complete_bar_frequency"
    return {"schema": "hepta.research.report.v1", "mode": result["mode"],
            "fill_model": result["fill_model"], "input": metadata,
            "strategy": {"name": "reference_moving_average", "fast": fast, "slow": slow,
                         "quantity": strategy.quantity, "long_only": long_only},
            "assumptions": {"capital": initial, "multiplier": number(multiplier, positive=True),
                            "slippage_price_units": number(slippage), "fee_per_unit": number(fee_per_unit),
                            "periods_per_year": periods_per_year, "automatic_funding": False,
                            "forced_final_liquidation": False, "broker_authorized": False},
            "fills": [asdict(fill) for fill in result["fills"]],
            "equity": [{"bar_begin_us": bar.begin_us, "bar_end_us": bar.end_us,
                        "complete": bar.complete, "value": value} for bar, value in zip(bars, values)],
            "position": result["position"], "pending_target": result["pending_target"],
            "fees": result["fees"], "metrics": metrics}


def _encode(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError("unsupported report type")


def write_report(path: Path, report: dict) -> None:
    """Publish only a fully serialized report; pre-replace failures preserve old output.

    A post-replace directory fsync failure is reported as an error, not claimed
    to roll back the visible file. This output is never a trading permission.
    """
    data = (json.dumps(report, default=_encode, sort_keys=True, indent=2, allow_nan=False)+"\n").encode()
    path = Path(path).absolute()
    dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    temporary = ".hepta-report-"+os.urandom(16).hex()
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=dfd)
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path.name, src_dir_fd=dfd, dst_dir_fd=dfd)
        os.fsync(dfd)
    finally:
        try:
            os.unlink(temporary, dir_fd=dfd)
        except FileNotFoundError:
            pass
        os.close(dfd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline normalized-bar research; no order route")
    parser.add_argument("--bars", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tick-size", required=True)
    parser.add_argument("--capital", required=True)
    parser.add_argument("--quantity", required=True)
    parser.add_argument("--fast", type=int, default=5)
    parser.add_argument("--slow", type=int, default=20)
    parser.add_argument("--long-only", action="store_true")
    parser.add_argument("--multiplier", default="1")
    parser.add_argument("--slippage", default="0")
    parser.add_argument("--fee-per-unit", default="0")
    parser.add_argument("--periods-per-year", type=int)
    parser.add_argument("--max-bars", type=int, default=100000)
    args = vars(parser.parse_args(argv))
    bars, output = args.pop("bars"), args.pop("output")
    try:
        if bars.resolve() == output.resolve() or (output.exists() and os.path.samefile(bars, output)):
            raise ValueError("output must not replace the input data")
        write_report(output, run_report(bars, **args))
    except (ValueError, OSError, ArithmeticError) as exc:
        print("research replay rejected: "+str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
