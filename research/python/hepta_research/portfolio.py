"""Causal, single-currency portfolio consumer of the existing research ledger.

Offline only. No broker, Gateway, execution-state or legacy SPI dependency.
See research/PORTFOLIO.md for the explicit bar-clock and valuation assumptions.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal, localcontext
import hashlib
import heapq
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

from .model import HypotheticalFill, ReplayBar, ResearchLedger, number
from .pipeline import IDENTITY, MAX_ROWS, MovingAverageTarget, read_bars, write_report

MAX_INSTRUMENTS = 64
MAX_SOURCES = 256
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_TIME = 2**63 - 1
CURRENCY = re.compile(r"[A-Z]{3}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _bound(value: object, low: int, high: int, label: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(label)
    return value


def _identity(value: object) -> str:
    if not isinstance(value, str) or not IDENTITY.fullmatch(value):
        raise ValueError("invalid instrument/source identity")
    return value


def _currency(value: object) -> str:
    if not isinstance(value, str) or not CURRENCY.fullmatch(value):
        raise ValueError("explicit three-letter accounting currency required")
    return value


@dataclass(frozen=True)
class InstrumentModel:
    """Research assumptions, not a broker contract or a risk authorization."""
    currency: str
    max_abs_target: object
    multiplier: object = 1
    lot: object = 1
    slippage: object = 0
    fee_per_unit: object = 0
    long_only: bool = False

    def checked(self) -> InstrumentModel:
        cap = number(self.max_abs_target, positive=True)
        multiplier, lot = number(self.multiplier, positive=True), number(self.lot, positive=True)
        slip, fee = number(self.slippage), number(self.fee_per_unit)
        if slip < 0 or fee < 0 or type(self.long_only) is not bool:
            raise ValueError("execution costs/long-only flag")
        with localcontext() as ctx:
            ctx.prec = 128
            if cap % lot:
                raise ValueError("target bound must be an exact lot multiple")
        return InstrumentModel(_currency(self.currency), cap, multiplier, lot,
                               slip, fee, self.long_only)


def _bars(values: object, remaining: int) -> list[ReplayBar]:
    # Bounded lists make validation finite and reject the entire input before
    # any user-supplied pure target function is invoked.
    if not isinstance(values, list) or not 1 <= len(values) <= remaining:
        raise ValueError("nonempty bounded per-instrument bar list required")
    result: list[ReplayBar] = []
    for bar in values:
        if not isinstance(bar, ReplayBar) or type(bar.complete) is not bool:
            raise ValueError("ReplayBar/completeness type")
        begin = _bound(bar.begin_us, 0, MAX_TIME, "bar begin time")
        end = _bound(bar.end_us, 1, MAX_TIME, "bar end time")
        if end <= begin or (result and (not result[-1].complete or begin < result[-1].end_us)):
            raise ValueError("overlap/regression or data after incomplete final bar")
        result.append(ReplayBar(begin, end, number(bar.open), number(bar.close), bar.complete))
    return result


def portfolio_replay(streams: Mapping[str, list[ReplayBar]],
                     models: Mapping[str, InstrumentModel],
                     targets: Mapping[str, Callable[[ReplayBar], object]], *,
                     capital: object, currency: str, max_mark_age_us: int,
                     max_total_bars: int = 100000) -> dict:
    """Merge OPEN/CLOSE events, never whole bars sorted by their start time.

    CLOSE(t) precedes OPEN(t), allowing a completed bar to trade the next
    contiguous bar's opening under the SAME next-bar assumption as replay().
    Independent per-instrument targets never observe another stream's future.
    Incomplete closes have no observation time and never enter the event clock.
    """
    if not all(isinstance(value, Mapping) for value in (streams, models, targets)):
        raise ValueError("stream/model/target mappings required")
    if not 1 <= len(streams) <= MAX_INSTRUMENTS or set(streams) != set(models) or set(streams) != set(targets):
        raise ValueError("nonempty matching bounded instrument sets required")
    names = sorted(_identity(name) for name in streams)
    currency, initial = _currency(currency), number(capital, positive=True)
    age = _bound(max_mark_age_us, 0, MAX_TIME, "explicit mark-age bound required")
    remaining = _bound(max_total_bars, 1, MAX_ROWS, "total bar bound")
    bars, policies, functions = {}, {}, {}
    for name in names:
        if not isinstance(models[name], InstrumentModel) or not callable(targets[name]):
            raise ValueError("instrument model/pure target required")
        policies[name] = models[name].checked()
        if policies[name].currency != currency:
            raise ValueError("mixed currencies require an explicit FX model; unsupported")
        functions[name] = targets[name]
        bars[name] = _bars(streams[name], remaining)
        remaining -= len(bars[name])

    # Reuse, do not duplicate, fill accounting. Each component ledger has the
    # same computational basis; subtract EVERY basis before adding capital ONCE.
    books = {name: ResearchLedger(initial, policies[name].multiplier) for name in names}
    pending: dict[str, Decimal | None] = {name: None for name in names}
    marks: dict[str, tuple[Decimal, int, str]] = {}
    fills, observations = [], []
    heap = [(bars[name][0].begin_us, 1, name, 0) for name in names]
    heapq.heapify(heap)
    peak, drawdown = initial, Decimal(0)
    valuation_gaps = 0
    with localcontext() as ctx:
        ctx.prec = 128
        while heap:
            timestamp = heap[0][0]
            events = []
            while heap and heap[0][0] == timestamp:
                _, phase, name, index = heapq.heappop(heap)
                bar, model, book = bars[name][index], policies[name], books[name]
                if phase == 0:
                    marks[name] = (bar.close, timestamp, "complete_close")
                    target = number(functions[name](bar))
                    if target.copy_abs() > model.max_abs_target or target % model.lot or (model.long_only and target < 0):
                        raise ValueError("target exceeds instrument/lot/long-only bounds")
                    pending[name] = target
                    if index + 1 < len(bars[name]):
                        heapq.heappush(heap, (bars[name][index+1].begin_us, 1, name, index+1))
                else:
                    marks[name] = (bar.open, timestamp, "bar_open")
                    if pending[name] is not None:
                        delta = number(pending[name] - book.quantity)
                        if delta:
                            fill = HypotheticalFill(timestamp, delta,
                                number(bar.open + (model.slippage if delta > 0 else -model.slippage)),
                                number(delta.copy_abs() * model.fee_per_unit))
                            book.fill(fill)
                            fills.append({"instrument": name, **asdict(fill)})
                        pending[name] = None
                    if bar.complete:
                        heapq.heappush(heap, (bar.end_us, 0, name, index))
                events.append({"instrument": name, "phase": "close" if phase == 0 else "open"})

            cash = number(initial + sum((books[name].cash-initial for name in names), Decimal(0)))
            fees = number(sum((books[name].fees for name in names), Decimal(0)))
            stale = [name for name in names if books[name].quantity and
                     (name not in marks or timestamp-marks[name][1] > age)]
            value, gross = None, None
            if not stale:
                notionals = [books[name].quantity*marks[name][0]*policies[name].multiplier
                             for name in names if books[name].quantity]
                value = number(cash+sum(notionals, Decimal(0)))
                gross = number(sum((v.copy_abs() for v in notionals), Decimal(0)))
                peak = max(peak, value)
                drawdown = max(drawdown, (peak-value)/peak)
            else:
                valuation_gaps += 1
            observations.append({"timestamp_us": timestamp, "events": events,
                "cash": cash, "fees": fees, "equity": value, "gross_notional": gross,
                "valuation_complete": not stale, "stale_instruments": stale})

        final = observations[-1]
        return {"schema": "hepta.research.portfolio-report.v1", "mode": "OFFLINE_HYPOTHETICAL",
            "fill_model": "next_bar_open_fixed_cost",
            "accounting_model": "shared_cash_plus_marked_inventory_v1",
            "assumptions": {"capital": initial, "currency": currency, "max_mark_age_us": age,
                "clock": "complete_close_then_bar_open", "automatic_funding": False,
                "margin_model": False, "exchange_settlement": False, "fx_conversion": False,
                "forced_final_liquidation": False, "broker_authorized": False},
            "models": {name: asdict(policies[name]) for name in names},
            "fills": fills, "equity": observations,
            "positions": {name: books[name].quantity for name in names},
            "fees_by_instrument": {name: books[name].fees for name in names},
            "pending_targets": pending, "cash": final["cash"], "fees": final["fees"],
            "final_equity": final["equity"],
            "marks": {name: {"price": mark[0], "timestamp_us": mark[1], "kind": mark[2]}
                      for name, mark in sorted(marks.items())},
            "untimed_incomplete_closes": {name: bars[name][-1].close for name in names
                                          if not bars[name][-1].complete},
            "metrics": {"total_return": final["equity"]/initial-1 if final["valuation_complete"] else None,
                "max_drawdown": drawdown if not valuation_gaps else None,
                "valuation_gap_count": valuation_gaps,
                "annualized": None, "annualization_reason": "irregular_event_clock"}}


def _capture(path: Path, limit: int) -> bytes:
    """Capture bounded regular-file bytes, not a FIFO/device or final symlink."""
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError("bounded regular source required")
        chunks, length = [], 0
        while True:
            chunk = os.read(fd, min(65536, limit+1-length))
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
            if length > limit:
                raise ValueError("source byte bound exceeded")
        after = os.fstat(fd)
        signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if signature(before) != signature(after) or length != before.st_size:
            raise ValueError("source changed during capture")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _object(value: object, keys: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("unsupported/missing manifest fields")
    return value


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError("nonfinite JSON number: "+value)


def run_portfolio_report(manifest: Path, sources: Mapping[str, Path], *,
                         max_total_bars: int = 100000, max_input_bytes: int = MAX_INPUT_BYTES) -> dict:
    """Explicit local bindings feed the SAME normalized-bar parser and targets.

    Source references in the manifest are inert. Capture once, verify digests,
    and parse only those bytes. Multiple files for an instrument form one stream.
    """
    remaining = _bound(max_total_bars, 1, MAX_ROWS, "total bar bound")
    budget = _bound(max_input_bytes, 1, MAX_INPUT_BYTES, "total input byte bound")
    if not isinstance(sources, Mapping) or not 1 <= len(sources) <= MAX_SOURCES:
        raise ValueError("bounded explicit source bindings required")
    bindings = {_identity(ref): Path(path) for ref, path in sources.items()}
    captured = _capture(Path(manifest), MAX_MANIFEST_BYTES)
    try:
        document = json.loads(captured.decode("utf-8"), object_pairs_hook=_pairs,
                              parse_float=Decimal, parse_constant=_constant)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("invalid/bounded UTF-8 JSON manifest") from exc
    _object(document, {"schema", "currency", "capital", "max_mark_age_us", "instruments"})
    if document["schema"] != "hepta.research.portfolio-input.v1":
        raise ValueError("unsupported portfolio input schema")
    instruments = document["instruments"]
    if not isinstance(instruments, list) or not 1 <= len(instruments) <= MAX_INSTRUMENTS:
        raise ValueError("bounded nonempty instrument list required")
    # Validate all declarations/bindings before touching any bar source.
    declarations, used = {}, set()
    policies, targets = {}, {}
    for item in instruments:
        _object(item, {"instrument", "currency", "tick_size", "quantity", "multiplier", "lot",
                       "slippage", "fee_per_unit", "fast", "slow", "long_only", "sources"})
        name = _identity(item["instrument"])
        if name in declarations:
            raise ValueError("duplicate instrument")
        model = InstrumentModel(item["currency"], item["quantity"], item["multiplier"],
                                item["lot"], item["slippage"], item["fee_per_unit"], item["long_only"]).checked()
        if model.currency != _currency(document["currency"]):
            raise ValueError("mixed currencies unsupported")
        policies[name] = model
        targets[name] = MovingAverageTarget(item["fast"], item["slow"], model.max_abs_target, model.long_only)
        number(item["tick_size"], positive=True)
        if not isinstance(item["sources"], list) or not 1 <= len(item["sources"]) <= MAX_SOURCES:
            raise ValueError("bounded ordered sources required")
        for source in item["sources"]:
            _object(source, {"ref", "sha256"})
            ref = _identity(source["ref"])
            if ref in used or not isinstance(source["sha256"], str) or not SHA256.fullmatch(source["sha256"]):
                raise ValueError("duplicate source reference/invalid SHA-256")
            used.add(ref)
        declarations[name] = item
    if used != set(bindings) or len(used) > MAX_SOURCES:
        raise ValueError("source bindings must match declared references exactly")
    number(document["capital"], positive=True)
    _bound(document["max_mark_age_us"], 0, MAX_TIME, "explicit mark-age bound required")
    streams, provenance = {}, {}
    with tempfile.TemporaryDirectory(prefix="hepta-portfolio-") as temporary:
        snapshot = Path(temporary)/"captured-bars.csv"
        for name, item in sorted(declarations.items()):
            stream, inputs, previous_day = [], [], ""
            for source in item["sources"]:
                data = _capture(bindings[source["ref"]], budget)
                budget -= len(data)
                if hashlib.sha256(data).hexdigest() != source["sha256"]:
                    raise ValueError("source SHA-256 mismatch")
                snapshot.write_bytes(data)
                parsed, metadata = read_bars(snapshot, item["tick_size"], remaining)
                if metadata["instrument"] != name:
                    raise ValueError("bar identity differs from declared instrument")
                # read_bars validates every row/date internally; preserve that
                # ordering across file boundaries without adding a new parser.
                rows = data.decode("ascii").splitlines()
                first_day, last_day = rows[1].split(",")[1], rows[-1].split(",")[1]
                if first_day < previous_day:
                    raise ValueError("cross-file trading-day regression")
                previous_day = last_day
                remaining -= len(parsed)
                stream.extend(parsed)
                inputs.append({"ref": source["ref"], **metadata})
            streams[name] = stream
            provenance[name] = inputs
    result = portfolio_replay(streams, policies, targets, capital=document["capital"],
                              currency=document["currency"], max_mark_age_us=document["max_mark_age_us"],
                              max_total_bars=max_total_bars)
    result["input"] = {"manifest_sha256": hashlib.sha256(captured).hexdigest(),
                       "instruments": provenance}
    result["strategy"] = {name: {"name": "reference_moving_average", "fast": targets[name].fast,
                        "slow": targets[name].slow, "quantity": targets[name].quantity,
                        "long_only": targets[name].long_only} for name in sorted(targets)}
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline causal portfolio replay; no broker or order route")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source", action="append", default=[], metavar="REF=PATH")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-total-bars", type=int, default=100000)
    parser.add_argument("--max-input-bytes", type=int, default=MAX_INPUT_BYTES)
    args = parser.parse_args(argv)
    try:
        sources = {}
        for binding in args.source:
            ref, sep, path = binding.partition("=")
            if not sep or not path or _identity(ref) in sources:
                raise ValueError("unique REF=PATH binding required")
            sources[ref] = Path(path)
        for path in [args.manifest, *sources.values()]:
            if path.resolve() == args.output.resolve() or (args.output.exists() and os.path.samefile(path, args.output)):
                raise ValueError("output must not replace an input")
        report = run_portfolio_report(args.manifest, sources,
                    max_total_bars=args.max_total_bars, max_input_bytes=args.max_input_bytes)
        write_report(args.output, report)
    except (ValueError, OSError, ArithmeticError, TypeError) as exc:
        print("research portfolio rejected: "+str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
