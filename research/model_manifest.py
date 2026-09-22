#!/usr/bin/env python3
"""Digest-bound JSON/JSONL adapters to the installed canonical native models.

No Python matching, accounting, strategy, client transport or broker engine.
Order-flow input uses the public #106 v1 schema in the bounded common domain;
output explicitly uses the native v1 contract, NOT its Decimal/null report ABI.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import importlib.machinery
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def _boundary():
    """Share the existing capture/JSON/alias boundary, also after relocation."""
    directory = Path(__file__).absolute().parent
    source = directory / "import_legacy.py"
    if not source.is_file():
        source = directory / "hepta-research-import"
    loader = importlib.machinery.SourceFileLoader("_hepta_research_capture", str(source))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


B = _boundary()
MAX_BYTES = 64 * 1024 * 1024
I64_MAX = 2**63 - 1
GRID_MAX = 2**40


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("integer field outside declared native domain")
    return value


def _identity(value, bound=96):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1," + str(bound) + "}", value):
        raise ValueError("bounded ASCII identity required")
    return value


def _fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise ValueError("unknown or missing manifest/event fields")
    return value


def _json(data, *, native=False):
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=B._unique_object,
                          parse_constant=B._reject_constant,
                          parse_float=float if native else Decimal)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("bounded UTF-8 JSON required") from exc


def _real(value, *, positive=False):
    decimal = B.number(value, positive=positive)
    result = float(decimal)
    if not math.isfinite(result) or Decimal(str(result)) != decimal:
        raise ValueError("decimal configuration does not round-trip through native binary64")
    return format(result, ".17g")


def _choice(value, choices):
    if not isinstance(value, str) or value not in choices:
        raise ValueError("unknown event choice")
    return value


def _flow(event):
    if not isinstance(event, dict):
        raise ValueError("event object required")
    kind = event.get("kind")
    common = "kind seq timestamp_us instrument"
    shapes = {"order": common + " order_id actor side quantity limit_ticks time_in_force",
              "cancel": common + " order_id actor quantity", "session_end": common,
              "mark": common + " price_ticks", "basis_rebase": common + " price_ticks"}
    _choice(kind, shapes)
    _fields(event, shapes[kind])
    # #106 starts at sequence zero; native receipt IDs require positive values.
    # The constant offset preserves ordering and retries, never repairs a stream.
    seq = _integer(event["seq"], 0, I64_MAX) + 1
    time = _integer(event["timestamp_us"], 0, I64_MAX)
    name = _identity(event["instrument"], 64)
    row = [seq, time, name]
    if kind == "order":
        limit = event["limit_ticks"]
        return ["A", *row, _identity(event["order_id"]),
                _choice(event["actor"], ("EXTERNAL", "RESEARCH")),
                _choice(event["side"], ("BUY", "SELL")),
                _integer(event["quantity"], 1, 10**12),
                "none" if limit is None else _integer(limit, -GRID_MAX, GRID_MAX),
                _choice(event["time_in_force"], ("GTC", "DAY", "IOC", "FAK", "FOK"))]
    if kind == "cancel":
        return ["C", *row, _identity(event["order_id"]),
                _choice(event["actor"], ("EXTERNAL", "RESEARCH")),
                _integer(event["quantity"], 0, 10**12)]
    if kind == "session_end":
        return ["E", *row]
    return ["M" if kind == "mark" else "B", *row,
            _integer(event["price_ticks"], -GRID_MAX, GRID_MAX)]


def _next(event):
    if not isinstance(event, dict):
        raise ValueError("event object required")
    kind = _choice(event.get("kind"), ("target", "open"))
    if kind == "open":
        _fields(event, "kind instrument timestamp_us sequence price_ticks volume")
        return ["O", _identity(event["instrument"], 64), _integer(event["timestamp_us"], 0, I64_MAX),
                _integer(event["sequence"], 1, 2**64-1),
                _integer(event["price_ticks"], -GRID_MAX, GRID_MAX),
                _integer(event["volume"], 0, 2**64-1)]
    _fields(event, "kind target_id instrument trading_day begin_us end_us observed_at_us target_quantity "
            "open_ticks high_ticks low_ticks close_ticks volume tick_count complete")
    if event["complete"] is not True or not isinstance(event["trading_day"], str):
        raise ValueError("target requires an explicitly complete bar and trading-day string")
    B._day(event["trading_day"])
    return ["T", _identity(event["target_id"]), _identity(event["instrument"], 64), event["trading_day"],
            *[_integer(event[k], 0, I64_MAX) for k in ("begin_us", "end_us", "observed_at_us")],
            _integer(event["target_quantity"], -10**12, 10**12),
            *[_integer(event[k], -GRID_MAX, GRID_MAX) for k in ("open_ticks", "high_ticks", "low_ticks", "close_ticks")],
            _integer(event["volume"], 0, 2**64-1), _integer(event["tick_count"], 1, 2**64-1)]


def _publish(output, report, inputs):
    """JSON-specific publication; reuse the existing alias boundary."""
    B._alias(output, inputs)
    data = (json.dumps(report, sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n").encode()
    if len(data) > MAX_BYTES:
        raise ValueError("report byte bound")
    fd, name = tempfile.mkstemp(prefix=".hepta-model-", suffix=".json", dir=output.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        B._alias(output, inputs)
        os.replace(temporary, output)
        directory = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def replay_manifest(args):
    _integer(args.max_events, 1, 1000000)
    _integer(args.max_active_orders, 1, 100000)
    _integer(args.max_input_bytes, 1, MAX_BYTES)
    _integer(args.timeout, 1, 3600)
    native = args.native_executable.absolute()
    if not native.is_file():
        raise ValueError("canonical native executable is missing")
    bindings = {}
    for binding in args.source:
        ref, sep, path = binding.partition("=")
        if not sep or not path or _identity(ref) in bindings:
            raise ValueError("unique REF=PATH source bindings required")
        bindings[ref] = Path(path)
    if not 1 <= len(bindings) <= 256:
        raise ValueError("1..256 source bindings required")
    inputs = [args.manifest, native, Path(__file__).absolute(), Path(B.__file__).absolute(), *bindings.values()]
    B._alias(args.output, inputs)
    manifest_bytes, manifest_digest = B._capture(args.manifest, 1024 * 1024)
    document = _json(manifest_bytes)
    flow = args.mode == "order-flow"
    _fields(document, "schema capital currency max_mark_age_us instruments sources" +
            ("" if flow else " slippage_ticks"))
    expected_schema = "hepta.research.order-flow.v1" if flow else "hepta.research.next-open-input.v1"
    if document["schema"] != expected_schema:
        raise ValueError("explicit mode/schema mismatch")
    currency = document["currency"]
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("explicit three-letter accounting currency required")
    age = _integer(document["max_mark_age_us"], 0, I64_MAX)
    slip = 0 if flow else _integer(document["slippage_ticks"], 0, 1000000)
    declarations, entries = document["instruments"], document["sources"]
    if not isinstance(declarations, list) or not 1 <= len(declarations) <= 64:
        raise ValueError("1..64 instruments required")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 256:
        raise ValueError("1..256 ordered sources required")
    refs = set()
    for item in entries:
        _fields(item, "ref sha256")
        ref = _identity(item["ref"])
        if ref in refs or not isinstance(item["sha256"], str) or not B.SHA256.fullmatch(item["sha256"]):
            raise ValueError("duplicate source or invalid digest")
        refs.add(ref)
    if refs != set(bindings):
        raise ValueError("source bindings must match exactly")
    rows = [["HMR1", "FLOW" if flow else "NEXT", _real(document["capital"], positive=True),
             currency, args.max_events, age, slip, args.max_active_orders]]
    names = set()
    for item in declarations:
        _fields(item, "instrument tick_size multiplier lot fee_per_unit fee_rate")
        name = _identity(item["instrument"], 64)
        if name in names:
            raise ValueError("duplicate instrument")
        names.add(name)
        fee, rate = B.number(item["fee_per_unit"]), B.number(item["fee_rate"])
        if fee < 0 or not 0 <= rate <= 1:
            raise ValueError("nonnegative fees and rate in [0,1] required")
        rows.append(["I", name, _real(item["tick_size"], positive=True),
                     _real(item["multiplier"], positive=True), _integer(item["lot"], 1, 10**12),
                     _real(fee), _real(rate), "fifo", "signed"])
    rows.append(["BEGIN"])
    count = 0; budget = args.max_input_bytes; encoded_bytes = 0; provenance = []
    with tempfile.TemporaryDirectory(prefix="hepta-model-input-") as directory:
        root = Path(directory); protocol = root / "model.txt"; result_file = root / "result.json"
        with protocol.open("wb") as stream:
            def emit(row):
                nonlocal encoded_bytes
                encoded = (",".join(map(str, row)) + "\n").encode("ascii")
                encoded_bytes += len(encoded)
                if encoded_bytes > MAX_BYTES:
                    raise ValueError("normalized protocol byte bound")
                stream.write(encoded)
            for row in rows:
                emit(row)
            for item in entries:
                raw, digest = B._capture(bindings[item["ref"]], budget)
                budget -= len(raw)
                if digest != item["sha256"]:
                    raise ValueError("source digest mismatch")
                lines = raw.split(b"\n")
                if lines[-1] == b"":
                    lines.pop()
                for line in lines:
                    if line.endswith(b"\r"):
                        line = line[:-1]
                    if not line or len(line) > 8192 or b"\r" in line:
                        raise ValueError("event line bound or line ending")
                    count += 1
                    if count > args.max_events:
                        raise ValueError("event row bound")
                    event = _json(line)
                    row = _flow(event) if flow else _next(event)
                    if event["instrument"] not in names:
                        raise ValueError("undeclared event instrument")
                    emit(row)
                provenance.append({"ref": item["ref"], "sha256": digest, "event_count": len(lines)})
        with protocol.open("rb") as source, result_file.open("wb") as output, tempfile.TemporaryFile() as error:
            result = subprocess.run([str(native), "--model-stream"], stdin=source, stdout=output,
                                    stderr=error, timeout=args.timeout, check=False)
        if result.returncode:
            raise ValueError("canonical model rejected the stream or complete final valuation; no report published")
        captured, _ = B._capture(result_file, MAX_BYTES)
        report = _json(captured, native=True)
        expected_model = "explicit-price-time-flow-v1" if flow else "observed-next-distinct-open-v1"
        if (not isinstance(report, dict) or report.get("schema") != "hepta.research.native-model-report.v1"
                or report.get("model") != expected_model or report.get("broker_authorized") is not False
                or type(report.get("input_rows")) is not int or report["input_rows"] != count):
            raise ValueError("native output contract mismatch")
        report["input"] = {"manifest_sha256": manifest_digest, "sources": provenance,
                           "source_sequence_offset": 1 if flow else 0}
        _publish(args.output, report, inputs)
    return {"schema": report["schema"], "model": expected_model, "input_rows": count, "broker_authorized": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("order-flow", "next-open"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source", action="append", default=[], metavar="REF=PATH")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-events", type=int, default=100000)
    parser.add_argument("--max-active-orders", type=int, default=4096)
    parser.add_argument("--max-input-bytes", type=int, default=MAX_BYTES)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--native-executable", type=Path,
                        default=Path(__file__).absolute().with_name("hepta-research-replay"))
    args = parser.parse_args(argv)
    try:
        print(json.dumps(replay_manifest(args), sort_keys=True), flush=True)
    except (OSError, ValueError, TypeError, ArithmeticError, subprocess.SubprocessError) as exc:
        print("RESEARCH_MODEL_IMPORT_FAILED: " +
              (str(exc) if isinstance(exc, ValueError) else "input/process/output failure"), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
