#!/usr/bin/env python3
"""Bounded legacy file adapters to the ONE installed HeptaResearch Data SDK.

Ported profile parsers and exact-grid decoder from public PR #106,
acffe4ae84a8e4377fd92b0ac536a865c6f6b6f5. No private legacy source or SDK
is imported. XML is inert metadata. All sources of a bundle are fed to ONE
existing LegacyTickCsvReader, so its clock/session/cumulative-volume state
survives file and layout boundaries. There is no Python bar, matching,
accounting, strategy, execution client or broker implementation here.

The output is an atomically published ZIP of ticks.csv, sessions.csv and
manifest.json. Optional replay calls the existing native executable. It is
never an authorization artifact. See LEGACY-BUNDLE.md for limitations.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
import tempfile
from xml.parsers import expat
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import zipfile

MAX_TICKS = 100000
MAX_SOURCE_BYTES = 64 * 1024 * 1024
I64_MAX = 2**63 - 1
U64_MAX = 2**64 - 1
UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
RECORD_BYTES = 424
LAYOUT = "hepta-depth82-le-a8-v1"
def number(value: object, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean is not a quantity")
    if len(str(value)) > 128:
        raise ValueError("decimal length")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid decimal") from exc
    if not result.is_finite() or result.copy_abs() > Decimal("1e18") or result.as_tuple().exponent < -18:
        raise ValueError("nonfinite or excessive decimal")
    if positive and result <= 0:
        raise ValueError("positive decimal required")
    return result


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


def _day(text: str) -> None:
    if not re.fullmatch(r"[0-9]{8}", text) or int(text[:4]) < 1600:
        raise ValueError("trading day syntax/range")
    date(int(text[:4]), int(text[4:6]), int(text[6:]))


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


def _decimal(text: str) -> Decimal:
    if len(text) > 128 or not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", text):
        raise ValueError("decimal syntax")
    return number(text)


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


# Positional marshaling profiles only. Numeric/time/session/counter validation
# ultimately runs in the installed C++ Data SDK, not a copied Tick/Bar model.
LAYOUTS = {
    "hepta32": dict(count=32, instrument=0, day=1, action=None, time=2,
                    fraction=3, fraction_scale=1000, price=4, volume=5, turnover=7, interest=29),
    "immsg34": dict(count=34, instrument=2, day=3, action=None, time=4,
                    fraction=5, fraction_scale=1000, price=6, volume=7, turnover=9, interest=31),
    "immsg35": dict(count=35, instrument=2, day=3, action=4, time=5,
                    fraction=6, fraction_scale=1000, price=7, volume=8, turnover=10, interest=32),
    "zs58": dict(count=58, instrument=3, day=0, action=None, time=1,
                 fraction=2, fraction_scale=1, price=37, volume=38, turnover=46, interest=39),
}


class CaptureBudget:
    """Capture once and parse captured bytes only; data/date budget is shared."""
    def __init__(self, maximum: int = MAX_SOURCE_BYTES):
        if type(maximum) is not int or not 1 <= maximum <= MAX_SOURCE_BYTES:
            raise ValueError("invalid capture budget")
        self.remaining = maximum
        self.paths: list[Path] = []

    def read(self, path: Path, expected: str | None = None,
             maximum: int = MAX_SOURCE_BYTES) -> tuple[bytes, str]:
        raw, digest = _capture(path, min(self.remaining, maximum))
        self.remaining -= len(raw)
        self.paths.append(path)
        if expected is not None and digest != expected:
            raise ValueError("bound source digest mismatch")
        return raw, digest


def _rows(raw: bytes):
    # bytes.splitlines() accepts extra control characters; CSV permits LF/CRLF
    # only, so remove one terminal LF and split explicitly.
    text = raw.decode("ascii")
    if text.endswith("\n"):
        text = text[:-1]
    for line in text.split("\n"):
        if line.endswith("\r"):
            line = line[:-1]
        if not line or len(line) > 4096 or '"' in line:
            raise ValueError("invalid bounded positional CSV row")
        fields = line.split(",")
        if any(not f or len(f) > 128 or f != f.strip() or
               any(ord(c) < 32 or ord(c) > 126 for c in f) for f in fields):
            raise ValueError("invalid positional CSV field")
        yield fields


def _action_dates(raw: bytes) -> list[str]:
    rows = iter(_rows(raw))
    if next(rows) != ["row", "action_day"]:
        raise ValueError("action date header must be row,action_day")
    dates = []
    for row in rows:
        if len(row) != 2 or _unsigned(row[0]) != len(dates) + 1 or len(dates) >= MAX_TICKS:
            raise ValueError("action dates must cover consecutive source rows")
        _day(row[1])
        dates.append(row[1])
    if not dates:
        raise ValueError("empty action-date source")
    return dates


def _header(fields: list[str], layout: str, spec: dict):
    names = {spec['instrument']: 'instrumentid', spec['day']: 'tradingday',
             spec['time']: 'updatetime', spec['fraction']: 'updatemicrosec' if layout == 'zs58' else 'updatemillisec',
             spec['price']: 'lastprice', spec['volume']: 'volume',
             spec['turnover']: 'turnover', spec['interest']: 'openinterest'}
    if spec['action'] is not None:
        names[spec['action']] = 'actionday'
    if layout.startswith('immsg'):
        names.update({0: 'localtime', 1: 'msgtype'})
    if len(fields) != spec['count'] or any(fields[i].lower() != name for i, name in names.items()):
        raise ValueError('selected positional CSV header mismatch')


def _csv_records(raw: bytes, layout: str, has_header: bool):
    spec = LAYOUTS[layout]
    rows = iter(_rows(raw))
    if has_header:
        _header(next(rows), layout, spec)
    for fields in rows:
        if len(fields) != spec['count']:
            raise ValueError('positional CSV field count mismatch')
        if layout.startswith('immsg') and fields[1] != 'IMMSG':
            raise ValueError('IMMSG discriminator required')
        clock = fields[spec['time']]
        if layout == 'zs58':
            if not re.fullmatch(r'[0-9]{6}', clock):
                raise ValueError('compact clock syntax')
            clock = ':'.join((clock[:2], clock[2:4], clock[4:]))
        fraction = _unsigned(fields[spec['fraction']], 999999 // spec['fraction_scale'])
        yield dict(instrument=fields[spec['instrument']], day=fields[spec['day']],
                   recorded_day=fields[spec['action']] if spec['action'] is not None else '',
                   clock=clock, microseconds=fraction * spec['fraction_scale'],
                   price=fields[spec['price']], volume=fields[spec['volume']],
                   turnover=fields[spec['turnover']], interest=fields[spec['interest']],
                   provenance={'raw_fields': fields})


def _binary_records(raw: bytes, scale: Decimal):
    if not raw or len(raw) % RECORD_BYTES:
        raise ValueError('binary file size/partial record')
    for offset in range(0, len(raw), RECORD_BYTES):
        record = raw[offset:offset + RECORD_BYTES]
        fraction = struct.unpack_from('<I', record, 40)[0]
        volume = struct.unpack_from('<q', record, 328)[0]
        if fraction > 999 or volume < 0:
            raise ValueError('binary milliseconds/cumulative volume range')
        stored = struct.unpack_from('<d', record, 288)[0]
        price, ticks = _price(stored, scale)
        turnover = struct.unpack_from('<d', record, 336)[0]
        interest = struct.unpack_from('<d', record, 344)[0]
        yield dict(instrument=_cstring(record, 44, 82), day=_cstring(record, 11, 9),
                   recorded_day=_cstring(record, 20, 9, optional=True),
                   clock=_cstring(record, 29, 9), microseconds=fraction * 1000,
                   price=price, volume=str(volume),
                   turnover=format(number(repr(turnover)), 'f'),
                   interest=format(number(repr(interest)), 'f'),
                   provenance={'record_sha256': hashlib.sha256(record).hexdigest(),
                               'unqualified_exchange_id': _cstring(record, 0, 11, optional=True),
                               'last_price_binary64_hex': stored.hex(), 'price_ticks': ticks,
                               'turnover_binary64_hex': turnover.hex(),
                               'open_interest_binary64_hex': interest.hex()})


def _native_symbol(instrument: str) -> str:
    if not isinstance(instrument, str) or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,64}', instrument):
        raise ValueError('instrument outside canonical Data SDK identity contract')
    return instrument


def _selected(records, *, instrument: str, scale: Decimal, zone: ZoneInfo,
              action_day: str | None, dates: list[str] | None, maximum: int):
    """Marshal into Zs58 to preserve microseconds; placeholders are not depth."""
    count = 0
    for record in records:
        count += 1
        if count > maximum or record['instrument'] != instrument:
            raise ValueError('foreign instrument or total Tick bound exceeded')
        if dates is not None and count > len(dates):
            raise ValueError('missing row in action-date map')
        supplied = dates[count - 1] if dates is not None else action_day
        actual = record['recorded_day'] or supplied
        if record['recorded_day'] and supplied is not None and supplied != actual:
            raise ValueError('external date conflicts with recorded ActionDay')
        if actual is None:
            raise ValueError('missing ActionDay requires an explicit civil date')
        _day(actual)
        _day(record['day'])
        if not re.fullmatch(r'[0-9]{2}:[0-9]{2}:[0-9]{2}', record['clock']):
            raise ValueError('source clock syntax')
        local = datetime.strptime(actual + ' ' + record['clock'], '%Y%m%d %H:%M:%S')
        local = local.replace(microsecond=record['microseconds'])
        stamp, offset = _utc(local, zone)
        if offset % 60 or not -840 <= offset // 60 <= 840:
            raise ValueError('clock offset outside canonical integer-minute contract')
        # PR106 used integer grids. Preserve that contract at the adapter boundary;
        # canonical market values remain doubles, never silently rounded to ticks.
        price = _decimal(record['price'])
        with localcontext() as ctx:
            ctx.prec = 128
            units = price / scale
            if units != units.to_integral_value() or not -2**63 <= units < 2**63:
                raise ValueError('price not exactly on declared grid')
        if price <= 0:
            raise ValueError('signed-price profile retained in PR106; canonical Data SDK requires positive prices')
        _, roundtrip_units = _price(float(price), scale)
        if roundtrip_units != int(units):
            raise ValueError('price loses declared grid identity in canonical binary64')
        cells = ['0'] * 58
        cells[0:4] = [record['day'], record['clock'].replace(':', ''),
                      str(record['microseconds']), instrument]
        cells[37], cells[38], cells[46], cells[39] = (format(price, 'f'), record['volume'],
                                                     record['turnover'], record['interest'])
        metadata = dict(record['provenance'], source_row=count, action_day=actual,
                        recorded_action_day=record['recorded_day'] or None,
                        utc_offset_minutes=offset // 60, timestamp_us=stamp)
        yield ','.join(cells) + '\n', (record['day'], actual, offset // 60), metadata
    if count == 0 or (dates is not None and len(dates) != count):
        raise ValueError('empty source or unused action-date rows')


def _sessions_bytes(raw: bytes) -> bytes:
    # #106 used begin/end; #107 uses open/close. Only the declared header is
    # translated, never timestamps, day labels, sorting or session coverage.
    first, sep, rest = raw.partition(b'\n')
    first = first.rstrip(b'\r')
    if first not in (b'open_us,close_us,trading_day', b'begin_us,end_us,trading_day') or not sep:
        raise ValueError('explicit UTC session CSV header required')
    return b'open_us,close_us,trading_day\n' + rest


def _json_default(value):
    if isinstance(value, Decimal):
        return format(value, 'f')
    raise TypeError('unsupported report type')


def _alias(output: Path, inputs: list[Path]):
    if output.is_symlink():
        raise ValueError('output symlink is not supported')
    if output.exists() and not output.is_file():
        raise ValueError('output must be a regular file')
    for path in inputs:
        if output.absolute() == path.absolute() or (output.exists() and os.path.samefile(output, path)):
            raise ValueError('output aliases an input or native executable')


def _publish(output: Path, members: dict[str, bytes], inputs: list[Path]):
    _alias(output, inputs)
    temporary = None
    fd, name = tempfile.mkstemp(prefix='.hepta-import-', suffix='.zip', dir=output.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w+b') as stream:
            with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for name in sorted(members):
                    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o100600 << 16
                    archive.writestr(info, members[name])
            stream.flush()
            os.fsync(stream.fileno())
        _alias(output, inputs)
        os.replace(temporary, output)
        temporary = None
        directory = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _native(command: list[str], output: Path, *, timeout: int):
    # Native output is private until the entire run succeeds. The executable
    # rejects late input/stream errors. No partial stdout becomes a valid bundle.
    with output.open('wb') as stream, tempfile.TemporaryFile() as error:
        result = subprocess.run(command, stdout=stream, stderr=error, timeout=timeout, check=False)
        if result.returncode != 0:
            raise ValueError('canonical native data/replay process failed; no bundle published')
    if output.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError('native output exceeded bounded bundle size')


def import_bundle(args) -> dict:
    instrument = _native_symbol(args.instrument)
    if not 1 <= args.max_ticks <= MAX_TICKS or not 1 <= args.timeout <= 3600:
        raise ValueError('invalid row/timeout bound')
    executable = Path(args.native_executable).absolute()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError('explicit installed native executable required')
    # The caller chooses trusted code; XML/bindings never choose executables.
    native_bytes, native_digest = _capture(executable, 64 * 1024 * 1024)
    del native_bytes
    zone = ZoneInfo(args.clock_zone)
    budget = CaptureBudget()
    session_raw, session_digest = budget.read(args.sessions, maximum=MAX_XML_BYTES)
    session_bytes = _sessions_bytes(session_raw)
    metadata_inputs: list[Path] = []
    meta = {'schema': 'hepta.research.import-bundle.v1', 'instrument': instrument,
            'clock_zone': args.clock_zone, 'first_volume': args.first_volume,
            'source_reference_pr106': 'acffe4ae84a8e4377fd92b0ac536a865c6f6b6f5',
            'native_executable_sha256': native_digest, 'source_sessions_sha256': session_digest,
            'authority': 'none', 'broker_authorized': False,
            'sequence_semantics': 'one_based_concatenated_row_not_venue_identity',
            'depth_semantics': 'source_metadata_only_not_used_for_matching',
            'adapter_policy': 'selected_fields_into_one_existing_LegacyTickCsvReader',
            'binary_padding': 'hash_only_never_exported', 'sources': []}
    if args.mode == 'binary':
        if args.layout != BINARY_LAYOUT or (args.action_day and args.action_days):
            raise ValueError('explicit binary layout and at most one action-date source required')
        scale = number(args.tick_size, positive=True)
        entries = [dict(index=1, path=str(args.ticks), layout=args.layout,
                        action_day=args.action_day, action_days_path=str(args.action_days) if args.action_days else None)]
        meta['source_format'] = BINARY_LAYOUT
    else:
        metadata_inputs = [args.config, args.instruments, args.bindings]
        config, config_digest = read_config(args.config, encoding=args.encoding)
        if config['type'] not in (0, 1, 2, 3):
            raise ValueError('database, network and custom callbacks are not offline sources')
        instruments, instrument_digest = read_instruments(args.instruments, encoding=args.encoding)
        if instrument not in instruments or instruments[instrument]['ProductClass'] != '1':
            raise ValueError('explicit futures catalog record required')
        if config['subscriptions'] is not None and instrument not in config['subscriptions']:
            raise ValueError('selected instrument is not subscribed')
        bindings, bindings_digest = read_bindings(args.bindings)
        if any(bindings[key] != config[key] for key in ('instrument_reference', 'front_reference')):
            raise ValueError('binding reference differs from XML')
        list_digest = None
        if config['type'] in (2, 3):
            if args.file_list is None:
                raise ValueError('explicit file list required')
            metadata_inputs.append(args.file_list)
            ordered, list_digest = read_file_list(args.file_list, encoding=args.encoding)
        else:
            if args.file_list is not None:
                raise ValueError('single file mode cannot take a file list')
            ordered = [(1, config['front_reference'])]
        by_index = {item['index']: item for item in bindings['sources']}
        if set(by_index) != {index for index, _ in ordered}:
            raise ValueError('all and only XML references must be bound')
        entries = []
        for index, reference in ordered:
            item = by_index[index]
            if item['reference'] != reference or (item['layout'] == BINARY_LAYOUT) != (config['type'] in (1, 3)):
                raise ValueError('bound reference/layout differs from XML mode')
            entries.append(item)
        selected = instruments[instrument]
        scale = number(selected['PriceTick'], positive=True)
        meta.update(source_format='hepta_xml_file_bundle_v1', simulator_type=config['type'],
                    config_sha256=config_digest, instruments_sha256=instrument_digest,
                    bindings_sha256=bindings_digest, file_list_sha256=list_digest,
                    xml_encoding=args.encoding, instrument_metadata=selected,
                    ignored_runtime_settings=config['ignored_runtime_settings'],
                    source_order='ascending_DateIndexId_not_a_date')
    if args.replay is None and any(v is not None for v in (args.initial_equity, args.fee_per_unit, args.multiplier)):
        raise ValueError('accounting options require replay; no silently ignored settings')
    meta['tick_size'] = scale
    total = 0
    with tempfile.TemporaryDirectory(prefix='hepta canonical import ') as directory:
        root = Path(directory)
        raw_path, evidence, sessions, ticks = [root / p for p in ('selected.csv', 'clocks.csv', 'sessions.csv', 'ticks.csv')]
        sessions.write_bytes(session_bytes)
        with raw_path.open('w', encoding='ascii', newline='') as raw_stream, evidence.open('w', encoding='ascii', newline='') as clocks:
            clocks.write('row,instrument,trading_day,action_day,utc_offset_minutes\n')
            for item in entries:
                raw, digest = budget.read(Path(item['path']), item.get('sha256'))
                dates, date_digest = None, None
                if item.get('action_days_path'):
                    data, date_digest = budget.read(Path(item['action_days_path']), item.get('action_days_sha256'), MAX_XML_BYTES)
                    dates = _action_dates(data)
                record_iter = (_binary_records(raw, scale) if item['layout'] == BINARY_LAYOUT
                               else _csv_records(raw, item['layout'], item.get('has_header', False)))
                details = []
                first = total + 1
                for row, clock, detail in _selected(record_iter, instrument=instrument, scale=scale,
                        zone=zone, action_day=item.get('action_day'), dates=dates, maximum=args.max_ticks - total):
                    total += 1
                    raw_stream.write(row)
                    clocks.write(f'{total},{instrument},{clock[0]},{clock[1]},{clock[2]}\n')
                    details.append(detail)
                meta['sources'].append(dict(index=item['index'], layout=item['layout'], sha256=digest,
                    reference_sha256=_sha_text(item['reference']) if 'reference' in item else None,
                    action_days_sha256=date_digest, source_bytes=len(raw),
                    global_first_sequence=first, global_last_sequence=total, records=details))
        inputs = [executable] + budget.paths + metadata_inputs
        _alias(args.output, inputs)
        _native([str(executable), '--import-legacy-ticks', 'Zs58', str(raw_path), str(evidence),
                 str(sessions), instrument, args.first_volume, 'headerless', str(args.max_ticks)],
                ticks, timeout=args.timeout)
        members = {'ticks.csv': ticks.read_bytes(), 'sessions.csv': session_bytes}
        if args.replay is not None:
            period, fast, slow, units, basis = args.replay
            if (not all(re.fullmatch(r'[0-9]{1,19}', s) for s in (period, fast, slow, units)) or
                    basis not in ('average', 'fifo')):
                raise ValueError('explicit replay period/windows/units/cost basis required')
            if args.initial_equity is None or args.fee_per_unit is None:
                raise ValueError('replay requires explicit initial equity and fee per unit')
            capital = number(args.initial_equity, positive=True)
            fee = number(args.fee_per_unit)
            if fee < 0:
                raise ValueError('negative replay fee')
            if args.mode == 'xml':
                if args.multiplier is not None:
                    raise ValueError('XML multiplier comes from the selected catalog, not an override')
                multiplier = number(meta['instrument_metadata']['VolumeMultiple'], positive=True)
            else:
                if args.multiplier is None:
                    raise ValueError('binary replay requires an explicit multiplier')
                multiplier = number(args.multiplier, positive=True)
            replay_options = ['--initial-equity', str(capital), '--multiplier', str(multiplier),
                              '--fee-per-unit', str(fee)]
            replay = root / 'replay.csv'
            _native([str(executable), str(ticks), str(sessions), instrument, period, fast, slow, units, basis] + replay_options,
                    replay, timeout=args.timeout)
            members['replay.csv'] = replay.read_bytes()
            meta['replay_model'] = 'offline-last-trade-liquidity-v1'
            meta['replay_arguments'] = args.replay + replay_options
        meta['tick_count'] = total
        meta['members'] = {name: hashlib.sha256(data).hexdigest() for name, data in members.items()}
        members['manifest.json'] = (json.dumps(meta, ensure_ascii=True, sort_keys=True,
            separators=(',', ':'), allow_nan=False, default=_json_default) + '\n').encode('utf-8')
        _publish(args.output, members, inputs)
    return {'output_format': 'hepta.research.import-bundle.v1', 'tick_count': total,
            'source_count': len(entries), 'broker_authorized': False}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    modes = result.add_subparsers(dest='mode', required=True)
    for mode in ('binary', 'xml'):
        p = modes.add_parser(mode)
        p.add_argument('--sessions', type=Path, required=True)
        p.add_argument('--instrument', required=True)
        p.add_argument('--clock-zone', required=True)
        p.add_argument('--first-volume', choices=('baseline', 'day-start'), required=True)
        p.add_argument('--output', type=Path, required=True)
        p.add_argument('--initial-equity')
        p.add_argument('--fee-per-unit')
        p.add_argument('--multiplier')
        p.add_argument('--max-ticks', type=int, default=MAX_TICKS)
        p.add_argument('--timeout', type=int, default=120)
        p.add_argument('--native-executable', type=Path,
                       default=Path(__file__).absolute().with_name('hepta-research-replay'))
        p.add_argument('--replay', nargs=5, metavar=('PERIOD_US', 'FAST', 'SLOW', 'UNITS', 'BASIS'))
        if mode == 'binary':
            p.add_argument('--layout', required=True, choices=(BINARY_LAYOUT,))
            p.add_argument('--ticks', type=Path, required=True)
            p.add_argument('--tick-size', required=True)
            p.add_argument('--action-day')
            p.add_argument('--action-days', type=Path)
        else:
            p.add_argument('--config', type=Path, required=True)
            p.add_argument('--instruments', type=Path, required=True)
            p.add_argument('--bindings', type=Path, required=True)
            p.add_argument('--file-list', type=Path)
            p.add_argument('--encoding', choices=('utf-8', 'gb18030'), default='utf-8')
    return result


def main(argv=None) -> int:
    try:
        report = import_bundle(parser().parse_args(argv))
        print(json.dumps(report, sort_keys=True), flush=True)
        return 0
    except (OSError, ValueError, TypeError, OverflowError, subprocess.SubprocessError, ZoneInfoNotFoundError) as exc:
        # No raw XML values, full paths, credentials or source bytes in errors.
        print('RESEARCH_IMPORT_FAILED: ' + type(exc).__name__ + ': ' +
              (str(exc) if isinstance(exc, ValueError) else 'input/process/output failure'), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
