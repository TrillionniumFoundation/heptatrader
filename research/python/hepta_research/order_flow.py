"""Digest-bound, multi-file consumer of the offline price/time replay profile."""
from __future__ import annotations

import argparse
from collections.abc import Mapping
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys

from .matching import InstrumentSpec, OrderFlowReplay, bound, fields, identity
from .pipeline import write_report
from .portfolio import (MAX_INPUT_BYTES, MAX_MANIFEST_BYTES, MAX_SOURCES, SHA256,
                        _capture, _constant, _pairs)


def _json(data: bytes) -> object:
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_float=Decimal, parse_constant=_constant)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("bounded UTF-8 JSON required") from exc


def run_order_flow(manifest: Path, sources: Mapping[str, Path], *,
                   max_events: int = 100000, max_trades: int = 100000,
                   max_active_orders: int = 4096,
                   max_input_bytes: int = MAX_INPUT_BYTES) -> dict:
    """Capture once; filenames in the manifest are inert source references.

    State, queue priority, command identities and accounting persist across ALL
    source boundaries. The list is ordered; no input sorting repairs regressions.
    This accepts explicit order-flow events, not ambiguous depth snapshots.
    """
    budget = bound(max_input_bytes, 1, MAX_INPUT_BYTES, "input byte budget")
    if not isinstance(sources, Mapping) or not 1 <= len(sources) <= MAX_SOURCES:
        raise ValueError("bounded explicit source bindings required")
    bindings = {identity(ref): Path(path) for ref, path in sources.items()}
    captured = _capture(Path(manifest), MAX_MANIFEST_BYTES)
    document = fields(_json(captured), {"schema", "capital", "currency", "max_mark_age_us", "instruments", "sources"})
    if document["schema"] != "hepta.research.order-flow.v1":
        raise ValueError("unsupported order-flow schema")
    declarations = document["instruments"]
    if not isinstance(declarations, list) or not 1 <= len(declarations) <= 64:
        raise ValueError("1..64 instrument declarations required")
    models = {}
    for item in declarations:
        fields(item, {"instrument", "tick_size", "multiplier", "lot", "fee_per_unit", "fee_rate"})
        name = identity(item["instrument"])
        if name in models:
            raise ValueError("duplicate instrument")
        models[name] = InstrumentSpec(item["tick_size"], item["multiplier"], item["lot"],
                                      item["fee_per_unit"], item["fee_rate"]).checked()
    entries = document["sources"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_SOURCES:
        raise ValueError("bounded ordered source declarations required")
    refs = set()
    for item in entries:
        fields(item, {"ref", "sha256"})
        ref = identity(item["ref"])
        if ref in refs or not isinstance(item["sha256"], str) or not SHA256.fullmatch(item["sha256"]):
            raise ValueError("duplicate source/invalid digest")
        refs.add(ref)
    if refs != set(bindings):
        raise ValueError("source bindings must match declarations exactly")
    engine = OrderFlowReplay(models, capital=document["capital"], currency=document["currency"],
                             max_mark_age_us=document["max_mark_age_us"], max_events=max_events,
                             max_trades=max_trades, max_active_orders=max_active_orders)
    provenance = []
    for item in entries:
        data = _capture(bindings[item["ref"]], budget)
        budget -= len(data)
        digest = hashlib.sha256(data).hexdigest()
        if digest != item["sha256"]:
            raise ValueError("source SHA-256 mismatch")
        lines = data.split(b"\n")
        if lines[-1] == b"":
            lines.pop()
        if not lines:
            raise ValueError("empty event source")
        for raw in lines:
            line = raw[:-1] if raw.endswith(b"\r") else raw
            if not line or len(line) > 8192 or b"\r" in line:
                raise ValueError("event row length/line ending")
            engine.apply(_json(line))
        provenance.append({"ref": item["ref"], "sha256": digest, "event_count": len(lines)})
    report = engine.report()
    report["input"] = {"manifest_sha256": hashlib.sha256(captured).hexdigest(), "sources": provenance}
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline explicit order-flow replay; no broker/Execution route")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source", action="append", default=[], metavar="REF=PATH")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-events", type=int, default=100000)
    parser.add_argument("--max-trades", type=int, default=100000)
    parser.add_argument("--max-active-orders", type=int, default=4096)
    parser.add_argument("--max-input-bytes", type=int, default=MAX_INPUT_BYTES)
    args = parser.parse_args(argv)
    try:
        sources = {}
        for value in args.source:
            ref, sep, path = value.partition("=")
            if not sep or not path or identity(ref) in sources:
                raise ValueError("unique REF=PATH binding required")
            sources[ref] = Path(path)
        for path in [args.manifest, *sources.values()]:
            if path.resolve() == args.output.resolve() or (args.output.exists() and os.path.samefile(path, args.output)):
                raise ValueError("output must not replace an input")
        report = run_order_flow(args.manifest, sources, max_events=args.max_events,
                                max_trades=args.max_trades, max_active_orders=args.max_active_orders,
                                max_input_bytes=args.max_input_bytes)
        write_report(args.output, report)
    except (ValueError, OSError, ArithmeticError, TypeError) as exc:
        print("research order-flow rejected: "+str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
