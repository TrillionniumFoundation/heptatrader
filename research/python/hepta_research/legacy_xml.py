"""Bounded, inert legacy XML -> explicit source bindings -> one research replay.

The legacy reader is a format reference, not an executable configuration source.
No embedded path is followed. The caller binds every file explicitly; vendor
SDKs, credentials, cache/output settings and simulator threads are never loaded.
See XML-IMPORT.md for the supported profile and deliberately changed semantics.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from xml.parsers import expat
from zoneinfo import ZoneInfoNotFoundError

from .legacy import _day, _decimal, _source, _unsigned
from .legacy_ticks import (LAYOUTS, MAX_SOURCE_BYTES, MAX_TICKS, NormalizedTicks,
                           TICK_HEADER, normalize_ticks, normalized_report)
from .model import number
from .pipeline import write_report

MAX_XML_BYTES = 4 * 1024 * 1024
MAX_XML_ELEMENTS = 20001
MAX_FILES = 128
XML_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}\Z")
IDENTITY = re.compile(r"[A-Za-z0-9_.:-]{1,81}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
BINARY_LAYOUT = "hepta-depth82-le-a8-v1"
DATE_FIELDS = {"CreateDate", "OpenDate", "ExpireDate", "StartDelivDate", "EndDelivDate"}
INT_FIELDS = {"DeliveryYear", "DeliveryMonth", "MaxMarketOrderVolume", "MinMarketOrderVolume",
              "MaxLimitOrderVolume", "MinLimitOrderVolume", "IsTrading", "VolumeMultiple"}
DECIMAL_FIELDS = {"UnderlyingMultiple", "PriceTick", "StrikePrice"}
CHAR_FIELDS = {"ProductClass", "Currency", "OptionsType", "PositionType"}
TEXT_FIELDS = {"InstrumentID", "InstrumentName", "ExchangeID", "ProductID", "UnderlyingInstrID"}
INSTRUMENT_FIELDS = DATE_FIELDS | INT_FIELDS | DECIMAL_FIELDS | CHAR_FIELDS | TEXT_FIELDS


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    children: list[_Node] = field(default_factory=list)


def _identity(stat_result) -> tuple:
    return (stat_result.st_dev, stat_result.st_ino, stat_result.st_size,
            stat_result.st_mtime_ns, stat_result.st_ctime_ns)


def _capture(path: Path, maximum: int) -> tuple[bytes, str]:
    """Capture/hash the same bounded bytes; reject observed changes and special files."""
    if type(maximum) is not int or maximum < 1:
        raise ValueError("source byte budget exhausted")
    with _source(path) as (source, digest):
        before = os.fstat(source.fileno())
        if not 0 < before.st_size <= maximum:
            raise ValueError("source byte bound")
        data = source.read(maximum + 1)
        after = os.fstat(source.fileno())
        if (len(data) != before.st_size or len(data) > maximum or
                _identity(before) != _identity(after)):
            raise ValueError("source changed during capture")
        digest.update(data)
        return data, digest.hexdigest()


def _xml(path: Path, encoding: str) -> tuple[_Node, str]:
    if encoding not in ("utf-8", "gb18030"):
        raise ValueError("explicit UTF-8 or GB18030 encoding required")
    raw, digest = _capture(path, MAX_XML_BYTES)
    text = raw.decode(encoding)
    if text.startswith("\ufeff") and encoding == "utf-8":
        text = text[1:]
    # Decode explicitly before parsing. No encoding guessing/fallback. Expat
    # still validates the declaration; its declared encoding must match ours.
    parser = expat.ParserCreate()
    stack: list[_Node] = []
    root: list[_Node] = []
    count = 0

    def reject(*_args):
        raise ValueError("DTD, entities, processing instructions and CDATA are unsupported")

    def declaration(version, declared, standalone):
        if version != "1.0" or (declared is not None and declared.lower() != encoding):
            raise ValueError("XML declaration/explicit encoding mismatch")

    def start(tag, attrs):
        nonlocal count
        count += 1
        if (count > MAX_XML_ELEMENTS or len(stack) >= 4 or not XML_NAME.fullmatch(tag)
                or len(attrs) > 40 or any(not XML_NAME.fullmatch(k) or len(v) > 4096
                                         for k, v in attrs.items())):
            raise ValueError("XML shape/resource bound or namespace")
        if any(any(ord(c) < 32 for c in value) for value in attrs.values()):
            raise ValueError("control character in XML attribute")
        node = _Node(tag, dict(attrs))
        if stack:
            stack[-1].children.append(node)
        else:
            root.append(node)
        stack.append(node)

    def end(_tag):
        stack.pop()

    def data(value):
        if value.strip():
            raise ValueError("only attribute-based legacy XML is supported")

    parser.XmlDeclHandler = declaration
    parser.StartElementHandler, parser.EndElementHandler = start, end
    parser.CharacterDataHandler = data
    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    parser.SkippedEntityHandler = reject
    parser.ProcessingInstructionHandler = reject
    parser.StartCdataSectionHandler = reject
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    try:
        parser.Parse(text, True)
    except expat.ExpatError as exc:
        # Do not echo potentially sensitive malformed attribute values.
        raise ValueError("malformed XML") from exc
    if len(root) != 1 or stack:
        raise ValueError("one XML root required")
    return root[0], digest


def _shape(node: _Node, attrs: set[str], children: set[str] = frozenset()) -> None:
    if set(node.attrs) - attrs or any(child.tag not in children for child in node.children):
        raise ValueError("unsupported XML attribute/child")


def _one(node: _Node, tag: str, *, required: bool = False) -> _Node | None:
    found = [child for child in node.children if child.tag == tag]
    if len(found) > 1 or (required and not found):
        raise ValueError("missing/duplicate singleton XML element")
    return found[0] if found else None


def _text(value: object) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= 4096 or value != value.strip()
            or any(ord(c) < 32 for c in value)):
        raise ValueError("nonempty unpadded text required")
    return value


def _symbol(value: object) -> str:
    if not isinstance(value, str) or not IDENTITY.fullmatch(value):
        raise ValueError("instrument/product/exchange identity")
    return value


def _boolean(value: str) -> bool:
    if value not in ("true", "false", "1", "0"):
        raise ValueError("explicit XML boolean required")
    return value in ("true", "1")


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_instruments(path: Path, *, encoding: str = "utf-8") -> tuple[dict[str, dict], str]:
    root, digest = _xml(path, encoding)
    _shape(root, set(), {"Instrument"})
    instruments: dict[str, dict] = {}
    for child in root.children:
        _shape(child, INSTRUMENT_FIELDS)
        raw = child.attrs
        required = {"InstrumentID", "ExchangeID", "ProductID", "ProductClass", "PriceTick", "VolumeMultiple"}
        if not required <= set(raw):
            raise ValueError("missing required instrument metadata")
        values: dict = {}
        for name, value in raw.items():
            if name in DATE_FIELDS:
                if value:
                    _day(value)
                values[name] = value or None
            elif name in INT_FIELDS:
                values[name] = _unsigned(value, 2**31-1)
            elif name in DECIMAL_FIELDS:
                values[name] = _decimal(value)
            elif name in CHAR_FIELDS:
                if len(value) != 1 or not 33 <= ord(value) <= 126:
                    raise ValueError("one-character legacy enum required")
                values[name] = value
            else:
                values[name] = value
        name = _symbol(values["InstrumentID"])
        _symbol(values["ExchangeID"])
        _symbol(values["ProductID"])
        if raw.get("UnderlyingInstrID"):
            _symbol(raw["UnderlyingInstrID"])
        if name in instruments:
            raise ValueError("duplicate instrument identity; no last-write-wins")
        number(values["PriceTick"], positive=True)
        number(values["VolumeMultiple"], positive=True)
        if values.get("IsTrading", 0) not in (0, 1):
            raise ValueError("IsTrading range")
        if not 0 <= values.get("DeliveryMonth", 0) <= 12:
            raise ValueError("DeliveryMonth range")
        for prefix in ("Market", "Limit"):
            minimum, maximum = values.get("Min"+prefix+"OrderVolume"), values.get("Max"+prefix+"OrderVolume")
            if minimum is not None and maximum is not None and minimum > maximum:
                raise ValueError("inverted instrument volume limits")
        for begin, end in (("OpenDate", "ExpireDate"), ("StartDelivDate", "EndDelivDate")):
            if values.get(begin) and values.get(end) and values[begin] > values[end]:
                raise ValueError("inverted instrument date interval")
        instruments[name] = values
    if not instruments:
        raise ValueError("empty instrument catalog")
    return instruments, digest


def read_config(path: Path, *, encoding: str = "utf-8") -> tuple[dict, str]:
    root, digest = _xml(path, encoding)
    _shape(root, set(), {"User", "Subscription", "Result", "System"})
    user = _one(root, "User", required=True)
    _shape(user, {"type"}, {"SimulatorServer", "Account"})
    if "type" not in user.attrs:
        raise ValueError("simulator type must be explicit")
    kind = _unsigned(user.attrs["type"], 6)
    server = _one(user, "SimulatorServer", required=True)
    _shape(server, {"Front", "Instrument", "Interval"})
    front, instrument_ref = (_text(server.attrs.get(key)) for key in ("Front", "Instrument"))
    interval = _unsigned(server.attrs["Interval"], 2**31-1) if "Interval" in server.attrs else None
    account = _one(user, "Account")
    pre_balance = None
    if account is not None:
        _shape(account, {"PreBalance"})
        if "PreBalance" in account.attrs:
            pre_balance = _decimal(account.attrs["PreBalance"])
            if pre_balance < 0:
                raise ValueError("negative legacy initial balance")
    subscription = _one(root, "Subscription")
    instruments = None
    if subscription is not None:
        _shape(subscription, set(), {"Instrument"})
        instruments = []
        for child in subscription.children:
            _shape(child, {"ID"})
            name = _symbol(child.attrs.get("ID"))
            if name in instruments:
                raise ValueError("duplicate subscription")
            instruments.append(name)
        if not instruments:
            raise ValueError("empty explicit subscription")
    results = []
    result = _one(root, "Result")
    if result is not None:
        _shape(result, set(), {"TotalResult", "InsResult"})
        _one(result, "TotalResult")
        seen = set()
        for child in result.children:
            _shape(child, {"bSave", "Interval"} | ({"ID"} if child.tag == "InsResult" else set()))
            item = {"kind": child.tag}
            if "bSave" in child.attrs:
                item["save"] = _boolean(child.attrs["bSave"])
            if "Interval" in child.attrs:
                item["interval"] = _unsigned(child.attrs["Interval"], 2**31-1)
            if child.tag == "InsResult":
                item["instrument"] = _symbol(child.attrs.get("ID"))
                if item["instrument"] in seen:
                    raise ValueError("duplicate instrument result")
                seen.add(item["instrument"])
            results.append(item)
    cache = None
    system = _one(root, "System")
    if system is not None:
        _shape(system, set(), {"Cache"})
        child = _one(system, "Cache")
        if child is not None:
            _shape(child, {"Need", "Path", "Instrument"})
            cache = {}
            if "Need" in child.attrs:
                cache["need"] = _boolean(child.attrs["Need"])
            if "Path" in child.attrs:
                cache["path_reference_sha256"] = _sha_text(child.attrs["Path"])
            if "Instrument" in child.attrs:
                cache["instruments"] = [_symbol(v) for v in child.attrs["Instrument"].split(",")]
                if len(set(cache["instruments"])) != len(cache["instruments"]):
                    raise ValueError("duplicate cache instrument")
    return {"type": kind, "front_reference": front, "instrument_reference": instrument_ref,
            "subscriptions": instruments, "ignored_runtime_settings": {
                "interval": interval, "pre_balance": pre_balance, "results": results, "cache": cache}}, digest


def read_file_list(path: Path, *, encoding: str = "utf-8") -> tuple[list[tuple[int, str]], str]:
    root, digest = _xml(path, encoding)
    _shape(root, set(), {"MDFile"})
    records = {}
    for child in root.children:
        _shape(child, {"DateIndexId", "FilePath"})
        if set(child.attrs) != {"DateIndexId", "FilePath"} or len(records) >= MAX_FILES:
            raise ValueError("file list required attributes/count")
        index = _unsigned(child.attrs["DateIndexId"], 2**31-1)
        if index in records:
            raise ValueError("duplicate file index")
        records[index] = _text(child.attrs["FilePath"])
    if not records:
        raise ValueError("empty file list")
    # This is the old map ordering, NOT inference of an ActionDay from index.
    return sorted(records.items()), digest


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON binding key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("nonfinite JSON")


def read_bindings(path: Path) -> tuple[dict, str]:
    raw, digest = _capture(path, MAX_XML_BYTES)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid binding JSON") from exc
    if (not isinstance(value, dict) or set(value) != {"schema", "instrument_reference", "front_reference", "sources"}
            or value["schema"] != "hepta.research.xml-bindings.v1"):
        raise ValueError("explicit XML binding schema required")
    _text(value["instrument_reference"])
    _text(value["front_reference"])
    sources = value["sources"]
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_FILES:
        raise ValueError("binding source count")
    seen = set()
    required = {"index", "reference", "path", "sha256", "layout"}
    optional = {"action_day", "action_days_path", "action_days_sha256", "has_header"}
    for source in sources:
        if not isinstance(source, dict) or not required <= set(source) or set(source) - required - optional:
            raise ValueError("binding source fields")
        index = source["index"]
        if type(index) is not int or not 0 <= index <= 2**31-1 or index in seen:
            raise ValueError("binding source index")
        seen.add(index)
        _text(source["reference"])
        for key in ("path", "action_days_path"):
            if key in source and not Path(_text(source[key])).is_absolute():
                raise ValueError("bound local file paths must be absolute")
        for key in ("sha256", "action_days_sha256"):
            if key in source and (not isinstance(source[key], str) or not SHA256.fullmatch(source[key])):
                raise ValueError("lowercase SHA256 required")
        if ("action_days_path" in source) != ("action_days_sha256" in source):
            raise ValueError("action-date file and digest must be bound together")
        if "action_day" in source:
            if not isinstance(source["action_day"], str):
                raise ValueError("action date string required")
            _day(source["action_day"])
            if "action_days_path" in source:
                raise ValueError("one external date source per file")
        if not isinstance(source["layout"], str) or source["layout"] not in (*LAYOUTS, BINARY_LAYOUT):
            raise ValueError("unsupported source layout")
        if "has_header" in source and type(source["has_header"]) is not bool:
            raise ValueError("header flag must be boolean")
        if source["layout"] == BINARY_LAYOUT and source.get("has_header", False):
            raise ValueError("binary cache has no CSV header")
    return value, digest


def normalize_xml(config_path: Path, instruments_path: Path, bindings_path: Path,
                  sessions_path: Path, *, instrument: str, clock_zone: str,
                  file_list_path: Path | None = None, encoding: str = "utf-8",
                  max_ticks: int = MAX_TICKS) -> NormalizedTicks:
    if type(max_ticks) is not int or not 1 <= max_ticks <= MAX_TICKS:
        raise ValueError("total Tick count bound")
    config, config_digest = read_config(config_path, encoding=encoding)
    if config["type"] not in (0, 1, 2, 3):
        raise ValueError("database, network and custom-callback simulator modes are not offline file inputs")
    instruments, instrument_digest = read_instruments(instruments_path, encoding=encoding)
    _symbol(instrument)
    if instrument not in instruments or instruments[instrument]["ProductClass"] != "1":
        raise ValueError("selected catalog instrument must be an explicit futures record")
    if config["subscriptions"] is not None and instrument not in config["subscriptions"]:
        raise ValueError("selected instrument is not subscribed")
    bindings, binding_digest = read_bindings(bindings_path)
    if any(bindings[key] != config[key] for key in ("instrument_reference", "front_reference")):
        raise ValueError("binding references do not match the captured XML")
    list_digest = None
    if config["type"] in (2, 3):
        if file_list_path is None:
            raise ValueError("list mode requires an explicitly supplied file list")
        entries, list_digest = read_file_list(file_list_path, encoding=encoding)
    else:
        if file_list_path is not None:
            raise ValueError("single-file mode must not receive a file list")
        entries = [(1, config["front_reference"])]
    by_index = {item["index"]: item for item in bindings["sources"]}
    if set(by_index) != {index for index, _ in entries}:
        raise ValueError("all and only XML data references must be explicitly bound")
    is_binary = config["type"] in (1, 3)
    for index, reference in entries:
        item = by_index[index]
        if item["reference"] != reference or (item["layout"] == BINARY_LAYOUT) != is_binary:
            raise ValueError("source reference/type differs from XML")
    selected = instruments[instrument]
    rows, sources = [TICK_HEADER], []
    previous_stamp, previous_day, previous_volume, ordinal = -1, "", 0, 0
    sessions_csv = None
    budget = MAX_SOURCE_BYTES
    with tempfile.TemporaryDirectory(prefix="hepta-xml-capture-") as temporary:
        directory = Path(temporary)
        session_data, session_digest = _capture(sessions_path, min(MAX_XML_BYTES, budget))
        budget -= len(session_data)
        staged_sessions = directory/"sessions.csv"
        staged_sessions.write_bytes(session_data)
        for index, reference in entries:
            item = by_index[index]
            raw, digest = _capture(Path(item["path"]), budget)
            budget -= len(raw)
            if digest != item["sha256"]:
                raise ValueError("bound source digest mismatch")
            staged = directory/"source"
            staged.write_bytes(raw)
            del raw
            dates = None
            if "action_days_path" in item:
                date_data, date_digest = _capture(Path(item["action_days_path"]), min(MAX_XML_BYTES, budget))
                budget -= len(date_data)
                if date_digest != item["action_days_sha256"]:
                    raise ValueError("bound action-date digest mismatch")
                dates = directory/"dates.csv"
                dates.write_bytes(date_data)
            options = dict(layout=item["layout"], instrument=instrument, clock_zone=clock_zone,
                           tick_size=selected["PriceTick"], action_day=item.get("action_day"),
                           action_days_path=dates, max_ticks=max_ticks-ordinal)
            if is_binary:
                from .legacy_binary import normalize_binary
                normalized = normalize_binary(staged, staged_sessions, **options)
            else:
                normalized = normalize_ticks(staged, staged_sessions,
                                             has_header=item.get("has_header", False), **options)
            if normalized.metadata["source_sha256"] != digest:
                raise ValueError("captured/normalized source digest disagreement")
            if sessions_csv is not None and normalized.sessions_csv != sessions_csv:
                raise ValueError("source session disagreement")
            sessions_csv = normalized.sessions_csv
            first = ordinal + 1
            for row in normalized.ticks_csv.splitlines()[1:]:
                fields = row.split(",")
                stamp, volume, day = int(fields[2]), int(fields[5]), fields[1]
                if (stamp < previous_stamp or day < previous_day or
                        (day == previous_day and volume < previous_volume)):
                    raise ValueError("cross-file time/day regression or cumulative volume reset")
                ordinal += 1
                if ordinal > max_ticks:
                    raise ValueError("total Tick count bound exceeded")
                fields[3] = str(ordinal)
                rows.append(",".join(fields)+"\n")
                previous_stamp, previous_day, previous_volume = stamp, day, volume
            sources.append({"index": index, "reference_sha256": _sha_text(reference),
                            "global_first_sequence": first, "global_last_sequence": ordinal,
                            "input": normalized.metadata})
    ticks_csv = "".join(rows)
    metadata = {"source_format": "hepta_xml_file_bundle_v1", "tick_count": ordinal,
                "tick_size": selected["PriceTick"], "instrument_metadata": selected,
                "config_sha256": config_digest, "instruments_sha256": instrument_digest,
                "bindings_sha256": binding_digest, "file_list_sha256": list_digest,
                "sessions_sha256": session_digest, "xml_encoding": encoding,
                "simulator_type": config["type"], "clock_zone": clock_zone,
                "front_reference_sha256": _sha_text(config["front_reference"]),
                "instrument_reference_sha256": _sha_text(config["instrument_reference"]),
                "subscriptions": config["subscriptions"], "sources": sources,
                "ignored_runtime_settings": config["ignored_runtime_settings"],
                "source_order": "ascending_DateIndexId_not_a_calendar_date",
                "sequence_semantics": "one_based_concatenated_row_not_venue_identity",
                "duplicate_policy": "preserve_rows_no_invented_venue_deduplication",
                "file_boundary_policy": "one_continuous_bar_strategy_and_ledger_state",
                "instrument_semantics": "historical_research_metadata_not_broker_authority",
                "normalized_ticks_sha256": _sha_text(ticks_csv),
                "normalized_sessions_sha256": _sha_text(sessions_csv)}
    return NormalizedTicks(ticks_csv, sessions_csv, metadata)


def xml_report(config_path: Path, instruments_path: Path, bindings_path: Path,
               sessions_path: Path, *, instrument: str, clock_zone: str,
               bars_executable: Path, period_us: int, first_volume: str,
               file_list_path: Path | None = None, encoding: str = "utf-8",
               max_ticks: int = MAX_TICKS, timeout_seconds: int = 120, **evaluation) -> dict:
    normalized = normalize_xml(config_path, instruments_path, bindings_path, sessions_path,
                               instrument=instrument, clock_zone=clock_zone,
                               file_list_path=file_list_path, encoding=encoding, max_ticks=max_ticks)
    if "multiplier" in evaluation:
        raise ValueError("XML profile takes its multiplier from the selected catalog record")
    selected = normalized.metadata["instrument_metadata"]
    return normalized_report(normalized, bars_executable=bars_executable, period_us=period_us,
                             first_volume=first_volume, tick_size=selected["PriceTick"],
                             max_ticks=max_ticks, timeout_seconds=timeout_seconds,
                             multiplier=selected["VolumeMultiple"], **evaluation)


def main(argv: list[str] | None = None, *, default_bars: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inert legacy XML to offline research; no broker/order route")
    for name in ("config", "instruments", "bindings", "sessions", "output"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--file-list", dest="file_list_path", type=Path)
    parser.add_argument("--encoding", choices=("utf-8", "gb18030"), default="utf-8")
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--clock-zone", required=True)
    parser.add_argument("--max-ticks", type=int, default=MAX_TICKS)
    parser.add_argument("--period-us", type=int, required=True)
    parser.add_argument("--first-volume", choices=("baseline", "include"), required=True)
    parser.add_argument("--bars-executable", type=Path, default=default_bars or os.environ.get("HEPTA_RESEARCH_BARS"))
    parser.add_argument("--capital", required=True)
    parser.add_argument("--quantity", required=True)
    parser.add_argument("--fast", type=int, default=5)
    parser.add_argument("--slow", type=int, default=20)
    parser.add_argument("--long-only", action="store_true")
    parser.add_argument("--slippage", default="0")
    parser.add_argument("--fee-per-unit", default="0")
    parser.add_argument("--periods-per-year", type=int)
    args = vars(parser.parse_args(argv))
    config, instruments, bindings, sessions, output = (args.pop(key) for key in
        ("config", "instruments", "bindings", "sessions", "output"))
    try:
        if args["bars_executable"] is None:
            raise ValueError("--bars-executable or installed launcher required")
        bound, binding_digest = read_bindings(bindings)
        inputs = [config, instruments, bindings, sessions, args["file_list_path"], args["bars_executable"]]
        inputs += [Path(item[key]) for item in bound["sources"] for key in ("path", "action_days_path") if key in item]
        for source in inputs:
            if source is not None and (output.resolve() == source.resolve() or
                    (output.exists() and os.path.samefile(output, source))):
                raise ValueError("output must not replace any input or executable")
        report = xml_report(config, instruments, bindings, sessions, **args)
        if report["input"]["legacy_ticks"]["bindings_sha256"] != binding_digest:
            raise ValueError("binding file changed after output-alias validation")
        write_report(output, report)
    except (ValueError, OSError, ArithmeticError, ZoneInfoNotFoundError, subprocess.TimeoutExpired) as exc:
        print("legacy XML import rejected: "+str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
