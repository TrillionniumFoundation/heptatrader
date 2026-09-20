"""Legacy Tick CSV -> canonical C++ BarBuilder -> existing offline replay.

Only the reviewed positional layouts are accepted. Missing ActionDay requires
external data, not TradingDay substitution. No vendor SDK or execution client
is loaded. Full raw columns are retained as unqualified source observations.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .legacy import (_day, _decimal, _line, _price_ticks, _sessions, _source,
                     _unsigned, _utc, I64_MAX)
from .model import number
from .pipeline import evaluate_bars, read_bars, write_report

MAX_TICKS = 100000
MAX_SOURCE_BYTES = 64 * 1024 * 1024
TICK_HEADER = "instrument,trading_day,timestamp_us,sequence,price_ticks,cumulative_volume\n"
# Indices are from ParseheptaTickDataRow / ParseZS_CZCE_TickDataRow at the
# pinned legacy baseline; they are not a native struct/ABI interpretation.
LAYOUTS = {
    "hepta32": dict(count=32, instrument=0, day=1, action=None, time=2,
                    fraction=3, fraction_scale=1000, price=4, volume=5, turnover=7, interest=29),
    "immsg35": dict(count=35, instrument=2, day=3, action=4, time=5,
                    fraction=6, fraction_scale=1000, price=7, volume=8, turnover=10, interest=32),
    "immsg34": dict(count=34, instrument=2, day=3, action=None, time=4,
                    fraction=5, fraction_scale=1000, price=6, volume=7, turnover=9, interest=31),
    "zs58": dict(count=58, instrument=3, day=0, action=None, time=1,
                 fraction=2, fraction_scale=1, price=37, volume=38, turnover=46, interest=39),
}


@dataclass(frozen=True)
class NormalizedTicks:
    ticks_csv: str
    sessions_csv: str
    metadata: dict


def _dates(path: Path) -> tuple[list[str], str]:
    values = []
    with _source(path) as (source, digest):
        if _line(source, digest) != ["row", "action_day"]:
            raise ValueError("action dates header must be row,action_day")
        while (fields := _line(source, digest)) is not None:
            if (source.tell() > MAX_SOURCE_BYTES or len(values) >= MAX_TICKS or
                    len(fields) != 2 or _unsigned(fields[0]) != len(values)+1):
                raise ValueError("action dates must cover consecutive one-based data rows")
            _day(fields[1])
            values.append(fields[1])
        if not values:
            raise ValueError("empty action dates")
        return values, digest.hexdigest()


def _check_header(fields: list[str], layout: str, spec: dict) -> None:
    names = {spec["instrument"]: "instrumentid", spec["day"]: "tradingday",
             spec["time"]: "updatetime", spec["price"]: "lastprice",
             spec["volume"]: "volume", spec["turnover"]: "turnover",
             spec["interest"]: "openinterest",
             spec["fraction"]: "updatemicrosec" if layout == "zs58" else "updatemillisec"}
    if spec["action"] is not None:
        names[spec["action"]] = "actionday"
    if layout.startswith("immsg"):
        names[0], names[1] = "localtime", "msgtype"
    if any(fields[i].casefold() != name for i, name in names.items()):
        raise ValueError("Tick header disagrees with selected positional layout")


def normalize_ticks(path: Path, sessions_path: Path, *, layout: str,
                    instrument: str, clock_zone: str, tick_size: object,
                    action_day: str | None = None, action_days_path: Path | None = None,
                    has_header: bool = False, max_ticks: int = MAX_TICKS) -> NormalizedTicks:
    if layout not in LAYOUTS:
        raise ValueError("unsupported legacy Tick layout")
    if not isinstance(instrument, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", instrument):
        raise ValueError("instrument identity")
    if type(max_ticks) is not int or not 1 <= max_ticks <= MAX_TICKS or type(has_header) is not bool:
        raise ValueError("Tick count/header bound")
    if not isinstance(clock_zone, str) or not 1 <= len(clock_zone) <= 128:
        raise ValueError("explicit clock zone required")
    spec, zone, scale = LAYOUTS[layout], ZoneInfo(clock_zone), number(tick_size, positive=True)
    external_dates, dates_digest = None, None
    if spec["action"] is not None:
        if action_day is not None or action_days_path is not None:
            raise ValueError("explicit source ActionDay must not be overridden")
    else:
        if (action_day is None) == (action_days_path is None):
            raise ValueError("missing ActionDay requires exactly one external date source")
        if action_day is not None:
            _day(action_day)
        else:
            external_dates, dates_digest = _dates(action_days_path)
    sessions, session_digest = _sessions(sessions_path)
    beginnings = [s[0] for s in sessions]
    output, attributes, header = [TICK_HEADER], [], None
    previous_stamp, previous_day, previous_volume = -1, "", 0
    with _source(path) as (source, digest):
        while (fields := _line(source, digest)) is not None:
            if source.tell() > MAX_SOURCE_BYTES or len(fields) != spec["count"]:
                raise ValueError("Tick source byte/field bound")
            if any(len(value) > 128 for value in fields):
                raise ValueError("Tick field length")
            if has_header and header is None:
                _check_header(fields, layout, spec)
                header = fields
                continue
            ordinal = len(attributes)+1
            if ordinal > max_ticks:
                raise ValueError("Tick count bound exceeded")
            if fields[spec["instrument"]] != instrument:
                raise ValueError("foreign instrument")
            if layout.startswith("immsg") and fields[1] != "IMMSG":
                raise ValueError("IMMSG discriminator required")
            day = fields[spec["day"]]
            _day(day)
            if spec["action"] is not None:
                actual_day = fields[spec["action"]]
            elif external_dates is not None:
                if ordinal > len(external_dates):
                    raise ValueError("missing row in action dates")
                actual_day = external_dates[ordinal-1]
            else:
                actual_day = action_day
            _day(actual_day)
            clock = fields[spec["time"]]
            compact = layout == "zs58"
            if not re.fullmatch(r"[0-9]{6}" if compact else r"[0-9]{2}:[0-9]{2}:[0-9]{2}", clock):
                raise ValueError("Tick time syntax")
            local = datetime.strptime(actual_day+" "+clock, "%Y%m%d %H%M%S" if compact else "%Y%m%d %H:%M:%S")
            fraction = _unsigned(fields[spec["fraction"]], 999999//spec["fraction_scale"])
            local = local.replace(microsecond=fraction*spec["fraction_scale"])
            stamp, offset = _utc(local, zone)
            index = bisect_right(beginnings, stamp)-1
            if index < 0 or stamp >= sessions[index][1] or day != sessions[index][2]:
                raise ValueError("Tick outside explicit session or wrong trading day")
            _, price = _price_ticks(fields[spec["price"]], scale)
            volume = _unsigned(fields[spec["volume"]])
            turnover, interest = (_decimal(fields[spec[key]]) for key in ("turnover", "interest"))
            if interest < 0:
                raise ValueError("negative open interest")
            if (stamp < previous_stamp or day < previous_day or
                    (day == previous_day and volume < previous_volume)):
                raise ValueError("Tick time/day regression or intraday cumulative volume reset")
            output.append(f"{instrument},{day},{stamp},{ordinal},{price},{volume}\n")
            attributes.append({"row": ordinal, "action_day": actual_day, "timestamp_us": stamp,
                               "utc_offset_seconds": offset, "turnover": turnover,
                               "open_interest": interest, "raw_fields": fields})
            previous_stamp, previous_day, previous_volume = stamp, day, volume
        if not attributes:
            raise ValueError("empty Tick source")
        if external_dates is not None and len(external_dates) != len(attributes):
            raise ValueError("unused rows in action dates")
        ticks_csv = "".join(output)
        sessions_csv = "begin_us,end_us,trading_day\n" + "".join(f"{a},{b},{d}\n" for a,b,d in sessions)
        metadata = {"source_format": layout, "source_sha256": digest.hexdigest(),
                    "sessions_sha256": session_digest, "action_days_sha256": dates_digest,
                    "action_day": action_day, "clock_zone": clock_zone,
                    "tick_size": scale, "tick_count": len(attributes), "header": header,
                    "sequence_semantics": "one_based_source_row_not_venue_identity",
                    "duplicate_policy": "preserve_rows_no_invented_venue_deduplication",
                    "depth_semantics": "raw_unqualified_not_used_for_execution_or_matching",
                    "normalized_ticks_sha256": hashlib.sha256(ticks_csv.encode("ascii")).hexdigest(),
                    "normalized_sessions_sha256": hashlib.sha256(sessions_csv.encode("ascii")).hexdigest(),
                    "source_fields": attributes}
        return NormalizedTicks(ticks_csv, sessions_csv, metadata)


def _builder_executable(bars_executable: Path, period_us: int, first_volume: str,
                        timeout_seconds: int) -> Path:
    if type(period_us) is not int or not 0 <= period_us <= I64_MAX:
        raise ValueError("period_us must be a nonnegative signed 64-bit integer")
    if first_volume not in ("baseline", "include"):
        raise ValueError("explicit first-volume policy required")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 600:
        raise ValueError("bounded executable timeout required")
    executable = Path(bars_executable)
    if not executable.is_absolute() or not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError("absolute compiled hepta-research-bars executable required")
    return executable


def tick_report(path: Path, sessions_path: Path, *, bars_executable: Path,
                period_us: int, first_volume: str, layout: str, instrument: str,
                clock_zone: str, tick_size: object, action_day: str | None = None,
                action_days_path: Path | None = None, has_header: bool = False,
                max_ticks: int = MAX_TICKS, timeout_seconds: int = 120, **evaluation) -> dict:
    """Stage captured inputs and call the SAME compiled bar engine as normalized CSV.

    File EOF never completes the final bar. All parsing/building/evaluation must
    succeed before the caller publishes a report. No fallback fill/bar engine.
    """
    _builder_executable(bars_executable, period_us, first_volume, timeout_seconds)
    normalized = normalize_ticks(path, sessions_path, layout=layout, instrument=instrument,
        clock_zone=clock_zone, tick_size=tick_size, action_day=action_day,
        action_days_path=action_days_path, has_header=has_header, max_ticks=max_ticks)
    return normalized_report(normalized, bars_executable=bars_executable,
        period_us=period_us, first_volume=first_volume, tick_size=tick_size,
        max_ticks=max_ticks, timeout_seconds=timeout_seconds, **evaluation)


def normalized_report(normalized: NormalizedTicks, *, bars_executable: Path,
                      period_us: int, first_volume: str, tick_size: object,
                      max_ticks: int = MAX_TICKS, timeout_seconds: int = 120,
                      **evaluation) -> dict:
    """One compiled builder/evaluator for single files and captured XML bundles."""
    executable = _builder_executable(bars_executable, period_us, first_volume, timeout_seconds)
    if not isinstance(normalized, NormalizedTicks) or type(max_ticks) is not int or not 1 <= max_ticks <= MAX_TICKS:
        raise ValueError("bounded normalized Tick input required")
    with tempfile.TemporaryDirectory(prefix="hepta-tick-import-") as temporary:
        directory = Path(temporary)
        ticks, sessions, bars = (directory/name for name in ("ticks.csv", "sessions.csv", "bars.csv"))
        ticks.write_text(normalized.ticks_csv, encoding="ascii")
        sessions.write_text(normalized.sessions_csv, encoding="ascii")
        with bars.open("wb") as output:
            completed = subprocess.run([str(executable), str(ticks), str(sessions), str(period_us), first_volume],
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.PIPE, timeout=timeout_seconds, check=False)
        if completed.returncode != 0:
            raise ValueError("canonical bar builder rejected staged input (exit "+str(completed.returncode)+")")
        bar_values, metadata = read_bars(bars, tick_size, max_ticks)
        metadata.update({"legacy_ticks": normalized.metadata, "period_us": period_us,
                         "first_volume": first_volume, "final_bar_completion": "not_inferred_from_eof"})
        return evaluate_bars(bar_values, metadata, **evaluation)


def main(argv: list[str] | None = None, *, default_bars: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explicit legacy Tick CSV to offline replay; no order route")
    parser.add_argument("--ticks", type=Path, required=True)
    parser.add_argument("--sessions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--layout", choices=tuple(LAYOUTS), required=True)
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--clock-zone", required=True)
    parser.add_argument("--tick-size", required=True)
    parser.add_argument("--action-day")
    parser.add_argument("--action-days", dest="action_days_path", type=Path)
    parser.add_argument("--has-header", action="store_true")
    parser.add_argument("--max-ticks", type=int, default=MAX_TICKS)
    parser.add_argument("--period-us", type=int, required=True)
    parser.add_argument("--first-volume", choices=("baseline", "include"), required=True)
    parser.add_argument("--bars-executable", type=Path, default=default_bars or os.environ.get("HEPTA_RESEARCH_BARS"))
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
    path, sessions, output = args.pop("ticks"), args.pop("sessions"), args.pop("output")
    try:
        if args["bars_executable"] is None:
            raise ValueError("--bars-executable or installed launcher required")
        for source in (path, sessions, args["action_days_path"], args["bars_executable"]):
            if source is not None and (output.resolve() == source.resolve() or
                    (output.exists() and os.path.samefile(output, source))):
                raise ValueError("output must not replace inputs or executable")
        write_report(output, tick_report(path, sessions, **args))
    except (ValueError, OSError, ArithmeticError, ZoneInfoNotFoundError, subprocess.TimeoutExpired) as exc:
        print("legacy Tick import rejected: "+str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
