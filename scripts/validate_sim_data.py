#!/usr/bin/env python3
"""Validate portable HeptaSimulator market-data CSV datasets."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
import math
import os
from pathlib import Path
import stat
import sys
import xml.etree.ElementTree as ET

REQUIRED_COLUMNS = (
    "TradingDay",
    "UpdateTime",
    "InstrumentID",
    "LastPrice",
    "Volume",
)
OPTIONAL_NUMERIC_COLUMNS = (
    "UpdateMillisec",
    "Turnover",
    "OpenInterest",
    "BidPrice1",
    "BidVolume1",
    "AskPrice1",
    "AskVolume1",
)
MAX_INDEX_BYTES = 1_048_576
MAX_LINE_BYTES = 1_048_576
DEFAULT_MAX_FILE_BYTES = 4 * 1024 * 1024 * 1024


def regular_file(path: Path, max_bytes: int) -> os.stat_result:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"not a regular non-symlink file: {path}")
    if metadata.st_size > max_bytes:
        raise ValueError(f"file exceeds size bound: {path}")
    return metadata


def finite_number(value: str, field: str, line: int) -> float:
    try:
        number = float(value)
    except ValueError as error:
        raise ValueError(f"line {line}: {field} is not numeric") from error
    if not math.isfinite(number):
        raise ValueError(f"line {line}: {field} is not finite")
    return number


def timestamp_key(row: dict[str, str], line: int) -> tuple[str, str, int]:
    trading_day = row.get("TradingDay", "")
    update_time = row.get("UpdateTime", "")
    try:
        datetime.strptime(trading_day + update_time, "%Y%m%d%H:%M:%S")
    except ValueError as error:
        raise ValueError(f"line {line}: invalid TradingDay/UpdateTime") from error
    millis_text = row.get("UpdateMillisec", "") or "0"
    if not millis_text.isascii() or not millis_text.isdecimal():
        raise ValueError(f"line {line}: UpdateMillisec is invalid")
    millis = int(millis_text, 10)
    if millis < 0 or millis > 999:
        raise ValueError(f"line {line}: UpdateMillisec is out of range")
    return trading_day, update_time, millis


def validate_csv(path: Path, max_bytes: int) -> dict[str, object]:
    regular_file(path, max_bytes)
    rows = 0
    instruments: set[str] = set()
    last_timestamp: dict[str, tuple[str, str, int]] = {}
    with path.open("r", encoding="utf-8", errors="strict", newline="") as source:
        reader = csv.DictReader(source)
        columns = reader.fieldnames or []
        if len(columns) != len(set(columns)):
            raise ValueError("CSV header contains duplicate columns")
        missing = [column for column in REQUIRED_COLUMNS if column not in columns]
        if missing:
            raise ValueError("CSV header misses required columns: " + ",".join(missing))
        for line, row in enumerate(reader, start=2):
            if None in row:
                raise ValueError(f"line {line}: row contains extra columns")
            if any(len((value or "").encode("utf-8")) > MAX_LINE_BYTES for value in row.values()):
                raise ValueError(f"line {line}: field exceeds size bound")
            instrument = (row.get("InstrumentID") or "").strip()
            if not instrument or len(instrument.encode("utf-8")) > 128:
                raise ValueError(f"line {line}: InstrumentID is invalid")
            key = timestamp_key(row, line)
            previous = last_timestamp.get(instrument)
            if previous is not None and key < previous:
                raise ValueError(f"line {line}: timestamp moves backwards for {instrument}")
            last_timestamp[instrument] = key

            last_price = finite_number(row.get("LastPrice", ""), "LastPrice", line)
            volume = finite_number(row.get("Volume", ""), "Volume", line)
            if last_price <= 0.0:
                raise ValueError(f"line {line}: LastPrice must be positive")
            if volume < 0.0:
                raise ValueError(f"line {line}: Volume must be nonnegative")
            for field in OPTIONAL_NUMERIC_COLUMNS:
                value = row.get(field, "")
                if value == "" or field == "UpdateMillisec":
                    continue
                number = finite_number(value, field, line)
                if field.endswith("Price1") and number < 0.0:
                    raise ValueError(f"line {line}: {field} must be nonnegative")
                if field.endswith("Volume1") and number < 0.0:
                    raise ValueError(f"line {line}: {field} must be nonnegative")
            rows += 1
            instruments.add(instrument)
    if rows == 0:
        raise ValueError("CSV contains no data rows")
    return {
        "path": str(path),
        "rows": rows,
        "instruments": len(instruments),
        "status": "PASS",
    }


def index_files(index: Path, dataset_root: Path) -> list[Path]:
    regular_file(index, MAX_INDEX_BYTES)
    root = ET.parse(index).getroot()
    if root.tag != "HisMDFiles":
        raise ValueError("index root must be HisMDFiles")
    files: list[Path] = []
    root_resolved = dataset_root.resolve()
    for node in root.findall("MDFile"):
        raw = node.attrib.get("FilePath", "").strip()
        if not raw:
            raise ValueError("MDFile FilePath is required")
        candidate = Path(raw)
        if candidate.is_absolute():
            raise ValueError("absolute FilePath is forbidden; use --dataset-root")
        resolved = (root_resolved / candidate).resolve()
        try:
            resolved.relative_to(root_resolved)
        except ValueError as error:
            raise ValueError("FilePath escapes dataset root") from error
        files.append(resolved)
    if not files:
        raise ValueError("index contains no MDFile entries")
    if len(files) != len(set(files)):
        raise ValueError("index contains duplicate files")
    return files


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, action="append", dest="inputs")
    source.add_argument("--index", type=Path)
    parser.add_argument("--dataset-root", type=Path)
    parser.add_argument("--max-file-bytes", type=int, default=DEFAULT_MAX_FILE_BYTES)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    if args.max_file_bytes < 1:
        print("max-file-bytes must be positive", file=sys.stderr)
        return 2
    try:
        if args.index:
            dataset_root = args.dataset_root or args.index.parent
            paths = index_files(args.index, dataset_root)
        else:
            paths = [path.resolve() for path in (args.inputs or [])]
        results = [validate_csv(path, args.max_file_bytes) for path in paths]
        report = {
            "schema_version": 1,
            "status": "PASS",
            "files": results,
            "total_rows": sum(int(item["rows"]) for item in results),
        }
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        print(json.dumps(report, sort_keys=True))
        return 0
    except (OSError, UnicodeError, ET.ParseError, ValueError) as error:
        print(f"validate-sim-data: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
