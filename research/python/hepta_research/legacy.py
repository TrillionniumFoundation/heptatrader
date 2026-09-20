"""Explicit legacy Hepta one-minute K-line CSV -> existing offline consumer.

The reviewed legacy fields are a data-format reference, not copied runtime code.
No vendor library, original thread/OMS, broker credential or Gateway is imported.
See LEGACY-IMPORT.md for clock, volume, completeness and unsupported formats.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
import hashlib
import os
from pathlib import Path
import re
import stat
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .model import ReplayBar, number
from .pipeline import evaluate_bars, write_report

LEGACY_EPOCH = datetime(1601, 1, 1)
UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
MINUTE_US = 60000000
MAX_ROWS = 1000000
I64_MAX = 2**63-1
U64_MAX = 2**64-1
HEADER = ("TimeStamp", "DateTime", "Open", "High", "Low", "Close", "TotalVolume",
          "LastVolume", "TotalTurnOver", "LastTurnOver", "OpenInterest")


def _unsigned(text: str, maximum: int = U64_MAX) -> int:
    if not re.fullmatch(r"[0-9]{1,20}", text):
        raise ValueError("unsigned integer syntax")
    value = int(text)
    if value > maximum:
        raise ValueError("integer range")
    return value


@contextmanager
def _source(path: Path):
    # Reject FIFOs/devices without blocking and close every rejected descriptor.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("regular source file required")
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            yield stream, hashlib.sha256()
    finally:
        if fd >= 0:
            os.close(fd)


def _line(stream, digest) -> list[str] | None:
    raw = stream.readline(4098)
    if not raw:
        return None
    digest.update(raw)
    text = raw[:-1] if raw.endswith(b"\n") else raw
    if text.endswith(b"\r"):
        text = text[:-1]
    if len(text) > 4096 or any(c in text for c in (b"\r", b"\n", b'"', b"\0")):
        raise ValueError("CSV row length/quoting/control character")
    fields = text.decode("ascii").split(",")
    if any(not field or field != field.strip() for field in fields):
        raise ValueError("empty or padded CSV field")
    return fields


def _day(text: str) -> None:
    if not re.fullmatch(r"[0-9]{8}", text) or int(text[:4]) < 1600:
        raise ValueError("trading day syntax/range")
    date(int(text[:4]), int(text[4:6]), int(text[6:]))


def _sessions(path: Path) -> tuple[list[tuple[int, int, str]], str]:
    result: list[tuple[int, int, str]] = []
    with _source(path) as (source, digest):
        if _line(source, digest) != ["begin_us", "end_us", "trading_day"]:
            raise ValueError("explicit UTC sessions CSV required")
        while (fields := _line(source, digest)) is not None:
            if len(fields) != 3 or len(result) >= MAX_ROWS:
                raise ValueError("session row/count")
            begin, end = (_unsigned(v, I64_MAX) for v in fields[:2])
            day = fields[2]
            _day(day)
            if end <= begin or (result and (begin < result[-1][1] or day < result[-1][2])):
                raise ValueError("session overlap/order/day regression")
            result.append((begin, end, day))
        if not result:
            raise ValueError("empty sessions")
        return result, digest.hexdigest()


def _label(text: str) -> datetime:
    if re.fullmatch(r"[0-9]{8}_[0-9]{6}", text):
        return datetime.strptime(text, "%Y%m%d_%H%M%S")
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}", text):
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    raise ValueError("legacy date-time label syntax")


def _utc(local: datetime, zone: ZoneInfo) -> tuple[int, int]:
    candidates: dict[int, int] = {}
    for fold in (0, 1):
        aware = local.replace(tzinfo=zone, fold=fold)
        utc = aware.astimezone(timezone.utc)
        if utc.astimezone(zone).replace(tzinfo=None) != local:
            continue
        delta = utc-UNIX_EPOCH
        value = delta.days*86400000000 + delta.seconds*1000000 + delta.microseconds
        candidates[value] = int(aware.utcoffset().total_seconds())
    if len(candidates) != 1:
        raise ValueError("ambiguous/nonexistent local clock time; no DST guessing")
    value, offset = next(iter(candidates.items()))
    if not 0 <= value <= I64_MAX:
        raise ValueError("timestamp outside supported Unix range")
    return value, offset


def _legacy_clock(text: str) -> datetime | None:
    value = _unsigned(text)
    return LEGACY_EPOCH + timedelta(microseconds=value) if value else None


def _decimal(text: str) -> Decimal:
    if len(text) > 128 or not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", text):
        raise ValueError("decimal syntax")
    return number(text)


def _price_ticks(text: str, scale: Decimal) -> tuple[Decimal, int]:
    value = _decimal(text)
    with localcontext() as ctx:
        ctx.prec = 128
        ticks = value/scale
        if ticks != ticks.to_integral_value() or not -2**63 <= ticks <= I64_MAX:
            raise ValueError("price not exactly representable in signed integer ticks")
    return value, int(ticks)


def read_legacy_bars(path: Path, sessions_path: Path, *, instrument: str,
                     clock_zone: str, volume_field: str, complete_through_us: int,
                     columns: int = 11, tick_size: object,
                     max_bars: int = 100000) -> tuple[list[ReplayBar], dict]:
    """Read a bounded 11/13-field positional format and preserve source fields.

    Numeric timestamps are microseconds from 1601, interpreted in the explicitly
    supplied clock zone. Nonzero numeric time must agree with the second-precision
    label. Zero uses the label. A source-owned watermark, NOT EOF, controls the
    final bar's completeness. Session input owns trading-day attribution.
    """
    if not isinstance(instrument, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", instrument):
        raise ValueError("instrument identity")
    if type(columns) is not int or columns not in (11, 13):
        raise ValueError("qualified layouts have exactly 11 or 13 fields")
    if volume_field not in ("LastVolume", "TotalVolume"):
        raise ValueError("explicit legacy volume-field choice required")
    if type(complete_through_us) is not int or not 0 <= complete_through_us <= I64_MAX:
        raise ValueError("explicit source completeness watermark required")
    if type(max_bars) is not int or not 1 <= max_bars <= MAX_ROWS:
        raise ValueError("bar count bound")
    if not isinstance(clock_zone, str) or not 1 <= len(clock_zone) <= 128:
        raise ValueError("explicit clock zone required")
    try:
        zone = ZoneInfo(clock_zone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("unknown clock zone") from exc
    scale = number(tick_size, positive=True)
    sessions, session_digest = _sessions(sessions_path)
    beginnings = [item[0] for item in sessions]
    bars: list[ReplayBar] = []
    attributes: list[dict] = []
    first = True
    header = None
    with _source(path) as (source, digest):
        while (fields := _line(source, digest)) is not None:
            if len(fields) != columns:
                raise ValueError("legacy field count differs from selected layout")
            if first and fields[0].casefold() == "timestamp":
                expected = list(HEADER) + (["HighTime", "LowTime"] if columns == 13 else [])
                # The label column has several names in historical exports;
                # every other named position must match, never auto-reorder.
                if (fields[1] not in ("DateTime", "Time", "StartTime", "szStartTime") or
                        any(a.casefold() != b.casefold() for i, (a, b) in enumerate(zip(fields, expected)) if i != 1)):
                    raise ValueError("legacy header names/order not qualified")
                header, first = fields, False
                continue
            first = False
            if len(bars) >= max_bars:
                raise ValueError("legacy bar count bound exceeded")
            label = _label(fields[1])
            numeric = _legacy_clock(fields[0])
            if numeric is not None and numeric.replace(microsecond=0) != label:
                raise ValueError("numeric timestamp and date-time label disagree")
            begin, offset = _utc(numeric if numeric is not None else label, zone)
            end = begin + MINUTE_US
            session_index = bisect_right(beginnings, begin)-1
            if (session_index < 0 or end > I64_MAX or end > sessions[session_index][1]):
                raise ValueError("bar outside/straddling explicit session")
            trading_day = sessions[session_index][2]
            if bars and (begin < bars[-1].end_us or not bars[-1].complete):
                raise ValueError("bar overlap/order or data after incomplete bar")
            prices, ticks = zip(*(_price_ticks(v, scale) for v in fields[2:6]))
            opening, high, low, close = prices
            if low > min(opening, close) or high < max(opening, close):
                raise ValueError("legacy inconsistent OHLC")
            total, last = (_unsigned(v) for v in fields[6:8])
            turnover, last_turnover, interest = (_decimal(v) for v in fields[8:11])
            if interest < 0:
                raise ValueError("negative open interest")
            extrema = []
            for value in fields[11:]:
                local = _legacy_clock(value)
                stamp = _utc(local, zone)[0] if local is not None else None
                if stamp is not None and not begin <= stamp < end:
                    raise ValueError("extremum timestamp outside bar")
                extrema.append(stamp)
            complete = end <= complete_through_us
            bars.append(ReplayBar(begin, end, opening, close, complete))
            attributes.append({"trading_day": trading_day, "begin_us": begin, "end_us": end,
                               "open_ticks": ticks[0], "high_ticks": ticks[1],
                               "low_ticks": ticks[2], "close_ticks": ticks[3],
                               "volume": last if volume_field == "LastVolume" else total,
                               "total_volume": total, "last_volume": last,
                               "total_turnover": turnover, "last_turnover": last_turnover,
                               "open_interest": interest, "tick_count": None,
                               "high_time_us": extrema[0] if extrema else None,
                               "low_time_us": extrema[1] if extrema else None,
                               "utc_offset_seconds": offset, "complete": complete})
        if not bars:
            raise ValueError("empty legacy bar stream")
        metadata = {"bars_sha256": digest.hexdigest(), "sessions_sha256": session_digest,
                    "instrument": instrument, "bar_count": len(bars), "tick_size": scale,
                    "source_format": f"hepta_future_kindle_csv_{columns}", "header": header,
                    "clock_zone": clock_zone, "timestamp_epoch": "1601-01-01",
                    "timestamp_unit": "microseconds", "period_us": MINUTE_US,
                    "volume_field": volume_field, "complete_through_us": complete_through_us,
                    "source_fields": attributes}
        return bars, metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Legacy K-line import and offline report; no order route")
    parser.add_argument("--bars", type=Path, required=True)
    parser.add_argument("--sessions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--clock-zone", required=True)
    parser.add_argument("--volume-field", choices=("LastVolume", "TotalVolume"), required=True)
    parser.add_argument("--complete-through-us", type=int, required=True)
    parser.add_argument("--columns", type=int, choices=(11, 13), default=11)
    parser.add_argument("--tick-size", required=True)
    parser.add_argument("--max-bars", type=int, default=100000)
    parser.add_argument("--capital", required=True)
    parser.add_argument("--quantity", required=True)
    parser.add_argument("--fast", type=int, default=5)
    parser.add_argument("--slow", type=int, default=20)
    parser.add_argument("--long-only", action="store_true")
    parser.add_argument("--multiplier", default="1")
    parser.add_argument("--slippage", default="0")
    parser.add_argument("--fee-per-unit", default="0")
    parser.add_argument("--periods-per-year", type=int)
    args = vars(parser.parse_args(argv))
    source, sessions, output = args.pop("bars"), args.pop("sessions"), args.pop("output")
    import_options = {key: args.pop(key) for key in ("instrument", "clock_zone", "volume_field",
                      "complete_through_us", "columns", "tick_size", "max_bars")}
    try:
        for input_path in (source, sessions):
            if output.resolve() == input_path.resolve() or (output.exists() and os.path.samefile(output, input_path)):
                raise ValueError("output must not replace either input")
        bars, metadata = read_legacy_bars(source, sessions, **import_options)
        write_report(output, evaluate_bars(bars, metadata, **args))
    except (ValueError, OSError, ArithmeticError) as exc:
        print("legacy research import rejected: "+str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
