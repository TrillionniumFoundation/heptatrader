"""Explicit legacy depth-cache ABI -> existing Tick/bar/replay pipeline.

Only the reviewed 82-character instrument, five-level, little-endian,
8-byte-aligned, 424-byte layout is accepted. No ctypes, pickle, vendor SDK,
native object deserialization or ABI auto-detection is used by this reader.
See BINARY-IMPORT.md for provenance, price policy and unsupported layouts.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from fractions import Fraction
import hashlib
import math
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfoNotFoundError

from .legacy import _day, _source
from .legacy_ticks import (MAX_TICKS, MAX_SOURCE_BYTES, NormalizedTicks, _dates,
                           normalize_ticks, tick_report)
from .model import number
from .pipeline import write_report

LAYOUT = "hepta-depth82-le-a8-v1"
RECORD_BYTES = 424


@dataclass(frozen=True)
class DecodedBinary:
    # A private intermediate adapter, NOT an original IMMSG source document.
    intermediate_csv: str
    metadata: dict


def _cstring(record: bytes, offset: int, size: int, *, optional: bool = False) -> str:
    field = record[offset:offset+size]
    end = field.find(b"\0")
    if end < 0:
        raise ValueError("unterminated binary string")
    value = field[:end].decode("ascii")
    if ((not value and not optional) or value != value.strip() or
            any(ord(c) < 32 or ord(c) > 126 or c in ',"' for c in value)):
        raise ValueError("invalid binary string")
    # Bytes after the first NUL and native padding are neither text nor data
    # authority. Do not expose stale process-memory padding in public reports.
    return value


def _price(value: float, scale: Decimal) -> tuple[str, int]:
    """Unique decimal-grid point that round-trips to the stored binary64.

    No epsilon or price rounding tolerance. Reject adjacent ticks that alias
    to one binary64, off-grid arithmetic noise, sentinels and signed overflow.
    The original binary64 hex value is retained separately in provenance.
    """
    if not math.isfinite(value) or abs(value) > 1e18:
        raise ValueError("nonfinite/excessive binary price")
    candidate = round(Fraction.from_float(value) / Fraction(scale))
    if not -2**63 <= candidate < 2**63:
        raise ValueError("binary price tick range")
    with localcontext() as ctx:
        ctx.prec = 128
        exact = Decimal(candidate) * scale
        if float(exact) != value:
            raise ValueError("binary price does not round-trip on the supplied tick grid")
        for adjacent in (candidate-1, candidate+1):
            if float(Decimal(adjacent)*scale) == value:
                raise ValueError("binary64 cannot distinguish adjacent price ticks")
        return format(number(exact), "f"), candidate


def decode_binary(path: Path, *, layout: str, instrument: str, tick_size: object,
                  action_day: str | None = None, action_days_path: Path | None = None,
                  max_ticks: int = MAX_TICKS) -> DecodedBinary:
    """Decode one bounded regular file; dates/sessions are never guessed.

    Present ActionDay cannot be overridden. External dates can fill missing
    ActionDay and must agree with every present date. TradingDay is never an
    ActionDay fallback. Session/UTC validation is subsequently done by the
    same normalizer used for CSV inputs.
    """
    if layout != LAYOUT:
        raise ValueError("explicit supported binary ABI layout required")
    if not isinstance(instrument, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,81}", instrument):
        raise ValueError("binary instrument identity")
    if type(max_ticks) is not int or not 1 <= max_ticks <= MAX_TICKS:
        raise ValueError("binary Tick count bound")
    if action_day is not None and action_days_path is not None:
        raise ValueError("only one external action-date source is allowed")
    scale = number(tick_size, positive=True)
    dates, dates_digest = None, None
    if action_day is not None:
        _day(action_day)
    if action_days_path is not None:
        dates, dates_digest = _dates(action_days_path)
    rows, attributes = [], []
    with _source(path) as (source, digest):
        before = os.fstat(source.fileno())
        if (before.st_size <= 0 or before.st_size > MAX_SOURCE_BYTES or
                before.st_size % RECORD_BYTES or before.st_size//RECORD_BYTES > max_ticks):
            raise ValueError("binary file size/record count/truncation")
        while (record := source.read(RECORD_BYTES)):
            if len(record) != RECORD_BYTES or len(rows) >= max_ticks:
                raise ValueError("binary partial record/count bound")
            digest.update(record)
            ordinal = len(rows)+1
            name = _cstring(record, 44, 82)
            exchange = _cstring(record, 0, 11, optional=True)
            day = _cstring(record, 11, 9)
            recorded_day = _cstring(record, 20, 9, optional=True)
            clock = _cstring(record, 29, 9)
            if name != instrument:
                raise ValueError("foreign binary instrument")
            _day(day)
            if dates is not None and ordinal > len(dates):
                raise ValueError("missing action-date map record")
            supplied_day = dates[ordinal-1] if dates is not None else action_day
            if recorded_day:
                _day(recorded_day)
                if supplied_day is not None and recorded_day != supplied_day:
                    raise ValueError("external date conflicts with recorded ActionDay")
            actual_day = recorded_day or supplied_day
            if actual_day is None:
                raise ValueError("missing ActionDay requires an external actual date")
            _day(actual_day)
            if not re.fullmatch(r"[0-9]{2}:[0-9]{2}:[0-9]{2}", clock):
                raise ValueError("binary clock syntax")
            datetime.strptime(actual_day+" "+clock, "%Y%m%d %H:%M:%S")
            fraction = struct.unpack_from("<I", record, 40)[0]
            volume = struct.unpack_from("<q", record, 328)[0]
            if fraction > 999 or volume < 0:
                raise ValueError("binary milliseconds/cumulative volume range")
            price = struct.unpack_from("<d", record, 288)[0]
            price_text, price_ticks = _price(price, scale)
            turnover = struct.unpack_from("<d", record, 336)[0]
            interest = struct.unpack_from("<d", record, 344)[0]
            turnover_text = format(number(repr(turnover)), "f")
            interest_value = number(repr(interest))
            if interest_value < 0:
                raise ValueError("negative binary open interest")
            fields = ["0"] * 35
            fields[1:9] = ["IMMSG", name, day, actual_day, clock,
                           str(fraction), price_text, str(volume)]
            fields[10], fields[32] = turnover_text, format(interest_value, "f")
            rows.append(",".join(fields)+"\n")
            attributes.append({"row": ordinal, "record_sha256": hashlib.sha256(record).hexdigest(),
                               "trading_day": day, "update_time": clock, "milliseconds": fraction,
                               "cumulative_volume": volume,
                               "recorded_action_day": recorded_day or None,
                               "action_day_source": "record" if recorded_day else "external",
                               "unqualified_exchange_id": exchange,
                               "last_price_binary64_hex": price.hex(), "price_ticks": price_ticks,
                               "turnover_binary64_hex": turnover.hex(),
                               "open_interest_binary64_hex": interest.hex()})
        after = os.fstat(source.fileno())
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if identity(before) != identity(after) or len(rows)*RECORD_BYTES != before.st_size:
            raise ValueError("binary source changed during read")
        if dates is not None and len(dates) != len(rows):
            raise ValueError("unused action-date map records")
        return DecodedBinary("".join(rows), {
            "source_format": LAYOUT, "source_sha256": digest.hexdigest(),
            "source_bytes": before.st_size, "record_bytes": RECORD_BYTES,
            "record_count": len(rows), "action_days_sha256": dates_digest,
            "action_day": action_day, "binary_records": attributes,
            "price_policy": "unique_decimal_grid_point_binary64_round_trip_no_epsilon",
            "auxiliary_decimal_policy": "shortest_round_trip_decimal_bounded_to_18_places",
            "padding_policy": "hash_only_not_exported_or_interpreted",
            "depth_semantics": "not_imported_not_qualified_for_execution_or_matching"})


@contextmanager
def _staged(decoded: DecodedBinary):
    with tempfile.TemporaryDirectory(prefix="hepta-bin-import-") as temporary:
        path = Path(temporary)/"selected-fields.csv"
        path.write_text(decoded.intermediate_csv, encoding="ascii")
        yield path


def _provenance(metadata: dict, decoded: DecodedBinary) -> dict:
    # The normalizer sees a generated IMMSG-shaped adapter with placeholders.
    # Never relabel those placeholders as original depth/source observations.
    result = dict(metadata)
    result["intermediate_selected_fields_sha256"] = result.pop("source_sha256")
    if len(result["source_fields"]) != len(decoded.metadata["binary_records"]):
        raise ValueError("binary/normalized record count mismatch")
    source_fields = []
    for attribute, binary in zip(result["source_fields"], decoded.metadata["binary_records"]):
        item = {key: value for key, value in attribute.items() if key != "raw_fields"}
        item.update(binary)
        source_fields.append(item)
    result.update({key: value for key, value in decoded.metadata.items() if key != "binary_records"})
    result["source_fields"] = source_fields
    result["header"] = None
    result["intermediate_semantics"] = "generated_selected_fields_not_original_IMMSG_or_depth"
    return result


def normalize_binary(path: Path, sessions_path: Path, *, layout: str, instrument: str,
                     clock_zone: str, tick_size: object, action_day: str | None = None,
                     action_days_path: Path | None = None,
                     max_ticks: int = MAX_TICKS) -> NormalizedTicks:
    decoded = decode_binary(path, layout=layout, instrument=instrument, tick_size=tick_size,
                            action_day=action_day, action_days_path=action_days_path,
                            max_ticks=max_ticks)
    with _staged(decoded) as staged:
        normalized = normalize_ticks(staged, sessions_path, layout="immsg35", instrument=instrument,
                                     clock_zone=clock_zone, tick_size=tick_size, max_ticks=max_ticks)
    return NormalizedTicks(normalized.ticks_csv, normalized.sessions_csv,
                           _provenance(normalized.metadata, decoded))


def binary_report(path: Path, sessions_path: Path, *, layout: str, instrument: str,
                  clock_zone: str, tick_size: object, action_day: str | None = None,
                  action_days_path: Path | None = None,
                  max_ticks: int = MAX_TICKS, **evaluation) -> dict:
    decoded = decode_binary(path, layout=layout, instrument=instrument, tick_size=tick_size,
                            action_day=action_day, action_days_path=action_days_path,
                            max_ticks=max_ticks)
    with _staged(decoded) as staged:
        report = tick_report(staged, sessions_path, layout="immsg35", instrument=instrument,
                             clock_zone=clock_zone, tick_size=tick_size, max_ticks=max_ticks,
                             **evaluation)
    report["input"]["legacy_ticks"] = _provenance(report["input"]["legacy_ticks"], decoded)
    return report


def main(argv: list[str] | None = None, *, default_bars: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explicit binary Tick cache to offline replay; no order route")
    parser.add_argument("--ticks", type=Path, required=True)
    parser.add_argument("--sessions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--layout", choices=(LAYOUT,), required=True)
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--clock-zone", required=True)
    parser.add_argument("--tick-size", required=True)
    parser.add_argument("--action-day")
    parser.add_argument("--action-days", dest="action_days_path", type=Path)
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
        write_report(output, binary_report(path, sessions, **args))
    except (ValueError, OSError, ArithmeticError, ZoneInfoNotFoundError, subprocess.TimeoutExpired) as exc:
        print("legacy binary import rejected: "+str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
