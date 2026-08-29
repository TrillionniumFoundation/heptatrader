#!/usr/bin/env python3

"""Pure offline extraction of retained official EUR/USD source responses.

This module never fetches data.  A separate root-owned capture seam retains the
exact response bodies and supplies transport metadata.  The extractor parses
those bytes, derives the semantic receipt, and emits only SHADOW artifacts.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
from html import unescape
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any
from urllib.parse import urlsplit
import xml.etree.ElementTree as ElementTree

from hepta_strategy_contracts import (
    ContractError,
    canonical_bytes,
    digest_bytes,
    require_exact_fields,
    require_int,
    require_text,
)


EXTRACTOR_ID = "HEPTA_OFFICIAL_EURUSD_SOURCE_EXTRACTOR"
EXTRACTOR_VERSION = "1.0.1"
CAPTURE_SCHEMA = "hepta.official-source-capture-manifest.v1"
RECEIPT_SCHEMA = "hepta.market-source-extraction-receipt.v1"
BUNDLE_SCHEMA = "hepta.market-source-bundle.v2"
MAX_PAYLOAD_BYTES = 16 * 1024 * 1024
MAX_TOTAL_PAYLOAD_BYTES = 64 * 1024 * 1024
MAX_EVENTS = 2048
MAX_ITEMS = 2048
EVENT_LOOKBEHIND = timedelta(days=2)
EVENT_LOOKAHEAD = timedelta(days=14)
ALL_DAY_GUARD = timedelta(hours=12)
SENTINEL_STEP = timedelta(minutes=45)
SENTINEL_LEAD = timedelta(minutes=30)

CAPTURE_FIELDS = frozenset({
    "schema", "version", "observed_at_ms", "sources",
    "mutation_attempted", "direct_broker_access", "paper_authorized",
    "live_authorized",
})
CAPTURE_SOURCE_FIELDS = frozenset({
    "provider", "requested_url", "final_url", "http_status", "content_type",
    "fetch_started_at_ms", "fetched_at_ms", "payload_path",
})
RECEIPT_SOURCE_FIELDS = CAPTURE_SOURCE_FIELDS | frozenset({
    "published_at_ms", "revision", "content_sha256",
})
BOUNDARY_FIELDS = (
    "mutation_attempted",
    "direct_broker_access",
    "paper_authorized",
    "live_authorized",
)

BLS_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
FED_CALENDAR_URL = (
    "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
)
FED_PRESS_URL = "https://www.federalreserve.gov/feeds/press_all.xml"
ECB_CALENDAR_URL = (
    "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html"
)
ECB_PRESS_URL = "https://www.ecb.europa.eu/rss/press.html"

SOURCE_RULES = {
    ("BLS", BLS_URL): {
        "role": "bls-calendar",
        "content_types": frozenset({"text/calendar", "text/plain"}),
        "currency": "USD",
    },
    ("FEDERAL_RESERVE", FED_CALENDAR_URL): {
        "role": "fed-calendar",
        "content_types": frozenset({"text/html"}),
        "currency": "USD",
    },
    ("FEDERAL_RESERVE", FED_PRESS_URL): {
        "role": "fed-press",
        "content_types": frozenset({
            "application/rss+xml", "application/xml", "text/xml",
        }),
        "currency": "USD",
    },
    ("ECB", ECB_CALENDAR_URL): {
        "role": "ecb-calendar",
        "content_types": frozenset({"text/html"}),
        "currency": "EUR",
    },
    ("ECB", ECB_PRESS_URL): {
        "role": "ecb-press",
        "content_types": frozenset({
            "application/rss+xml", "application/xml", "text/xml",
        }),
        "currency": "EUR",
    },
}
REQUIRED_ROLES = frozenset(rule["role"] for rule in SOURCE_RULES.values())
MONTHS = {
    "January": 1,
    "February": 2,
    "March": 3,
    "April": 4,
    "May": 5,
    "June": 6,
    "July": 7,
    "August": 8,
    "September": 9,
    "October": 10,
    "November": 11,
    "December": 12,
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Sept": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}
UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}",
)
DATE_RANGE_PATTERN = re.compile(r"([0-9]{1,2})(?:-([0-9]{1,2}))?\*?")
FOMC_SECTION_PATTERN = re.compile(
    r'<div class="panel panel-default"><div class="panel-heading">'
    r'<h4><a id="[0-9]+">([0-9]{4}) FOMC Meetings</a></h4></div>'
    r"(?P<body>.*?)(?=<div class=\"panel panel-default\">|"
    r"<div class=\"row\">|<div class='lastUpdate')",
    re.DOTALL,
)
FOMC_ROW_PATTERN = re.compile(
    r'<div class="(?:fomc-meeting--shaded )?row fomc-meeting" ">'
    r".*?"
    r'<div class="(?:fomc-meeting--shaded )?fomc-meeting__month '
    r'[^"]+"><strong>(?P<month>[^<]+)</strong></div>'
    r".*?"
    r'<div class="fomc-meeting__date [^"]+">(?P<days>[^<]+)</div>',
    re.DOTALL,
)
ECB_ENTRY_PATTERN = re.compile(
    r"<dt>\s*(?P<date>[0-9]{2}/[0-9]{2}/[0-9]{4})\s*</dt>\s*"
    r"<dd>\s*(?P<title>.*?)<br>\s*</dd>",
    re.DOTALL,
)
WHITESPACE_PATTERN = re.compile(r"\s+")


class ExtractionError(RuntimeError):
    """Fail-closed official-format or capture-contract error."""


def _fail(reason: str) -> None:
    raise ExtractionError(reason)


def _milliseconds(value: datetime) -> int:
    if value.tzinfo is None:
        _fail("OFFICIAL_SOURCE_TIMEZONE_MISSING")
    return int(value.timestamp() * 1000)


def _utc_datetime(value_ms: int) -> datetime:
    return datetime.fromtimestamp(value_ms / 1000.0, tz=timezone.utc)


def _normalized_text(value: str, reason: str, maximum: int = 4096) -> str:
    normalized = WHITESPACE_PATTERN.sub(" ", unescape(value)).strip()
    if not normalized or len(normalized) > maximum:
        _fail(reason)
    return normalized


def _text_digest(value: str) -> str:
    return digest_bytes(value.encode("utf-8"))


def _identifier(prefix: str, identity: str) -> str:
    suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:40]
    return f"{prefix}-{suffix}"


def _source_url(provider: str, value: Any) -> str:
    try:
        url = require_text(value, "OFFICIAL_SOURCE_URL_INVALID", maximum=2048)
    except ContractError as error:
        raise ExtractionError(str(error)) from error
    parsed = urlsplit(url)
    if (
            parsed.scheme != "https" or parsed.username is not None or
            parsed.password is not None or parsed.fragment or
            not parsed.path.startswith("/")):
        _fail("OFFICIAL_SOURCE_URL_INVALID")
    allowed = {
        candidate_url
        for candidate_provider, candidate_url in SOURCE_RULES
        if candidate_provider == provider
    }
    if url not in allowed:
        _fail("OFFICIAL_SOURCE_URL_NOT_PINNED")
    return url


def _capture_source(value: Any, observed_at_ms: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("OFFICIAL_SOURCE_CAPTURE_SOURCE_INVALID")
    fields = set(value)
    if (
            fields != set(CAPTURE_SOURCE_FIELDS) and
            fields != set(RECEIPT_SOURCE_FIELDS)):
        _fail("OFFICIAL_SOURCE_CAPTURE_SOURCE_FIELDS_INVALID")
    provider = value.get("provider")
    if provider not in {"BLS", "FEDERAL_RESERVE", "ECB"}:
        _fail("OFFICIAL_SOURCE_PROVIDER_INVALID")
    requested_url = _source_url(provider, value.get("requested_url"))
    final_url = _source_url(provider, value.get("final_url"))
    if requested_url != final_url:
        _fail("OFFICIAL_SOURCE_REDIRECT_NOT_ALLOWED")
    rule = SOURCE_RULES.get((provider, final_url))
    if rule is None:
        _fail("OFFICIAL_SOURCE_ROLE_NOT_PINNED")
    if value.get("http_status") != 200:
        _fail("OFFICIAL_SOURCE_HTTP_STATUS_INVALID")
    content_type = value.get("content_type")
    if content_type not in rule["content_types"]:
        _fail("OFFICIAL_SOURCE_CONTENT_TYPE_INVALID")
    try:
        fetch_started_at_ms = require_int(
            value.get("fetch_started_at_ms"),
            "OFFICIAL_SOURCE_FETCH_TIME_INVALID",
            minimum=0,
            maximum=observed_at_ms,
        )
        fetched_at_ms = require_int(
            value.get("fetched_at_ms"),
            "OFFICIAL_SOURCE_FETCH_TIME_INVALID",
            minimum=fetch_started_at_ms,
            maximum=observed_at_ms,
        )
        payload_path = require_text(
            value.get("payload_path"),
            "OFFICIAL_SOURCE_PAYLOAD_PATH_INVALID",
            maximum=512,
        )
    except ContractError as error:
        raise ExtractionError(str(error)) from error
    relative = Path(payload_path)
    if relative.is_absolute() or relative == Path(".") or ".." in relative.parts:
        _fail("OFFICIAL_SOURCE_PAYLOAD_PATH_INVALID")
    return {
        "provider": provider,
        "requested_url": requested_url,
        "final_url": final_url,
        "http_status": 200,
        "content_type": content_type,
        "fetch_started_at_ms": fetch_started_at_ms,
        "fetched_at_ms": fetched_at_ms,
        "payload_path": relative.as_posix(),
        "role": rule["role"],
        "currency": rule["currency"],
    }


def _event_window(observed_at_ms: int) -> tuple[datetime, datetime]:
    observed = _utc_datetime(observed_at_ms)
    return observed - EVENT_LOOKBEHIND, observed + EVENT_LOOKAHEAD


def _in_event_window(
    scheduled: datetime,
    observed_at_ms: int,
) -> bool:
    start, end = _event_window(observed_at_ms)
    return start <= scheduled <= end


def _nth_weekday(
    year: int,
    month: int,
    weekday: int,
    occurrence: int,
) -> date:
    first = date(year, month, 1)
    delta = (weekday - first.weekday()) % 7
    return first + timedelta(days=delta + 7 * (occurrence - 1))


def _us_eastern_to_utc(local: datetime) -> datetime:
    if local.tzinfo is not None or not (2007 <= local.year <= 2100):
        _fail("BLS_ICS_TIME_INVALID")
    daylight_start = datetime.combine(
        _nth_weekday(local.year, 3, 6, 2), time(2, 0))
    standard_start = datetime.combine(
        _nth_weekday(local.year, 11, 6, 1), time(2, 0))
    offset = timedelta(hours=4 if daylight_start <= local < standard_start
                       else 5)
    return (local + offset).replace(tzinfo=timezone.utc)


def _unfold_icalendar(payload: bytes) -> list[str]:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ExtractionError("BLS_ICS_UTF8_INVALID") from error
    if "\x00" in text or "\r" in text.replace("\r\n", ""):
        _fail("BLS_ICS_LINE_ENDING_INVALID")
    physical = text.replace("\r\n", "\n").split("\n")
    logical: list[str] = []
    for line in physical:
        if len(line.encode("utf-8")) > 4096:
            _fail("BLS_ICS_LINE_TOO_LONG")
        if line.startswith((" ", "\t")):
            if not logical:
                _fail("BLS_ICS_FOLD_INVALID")
            logical[-1] += line[1:]
        else:
            logical.append(line)
    return [line for line in logical if line]


def _parse_ical_properties(
    lines: list[str],
) -> tuple[dict[str, str], list[dict[str, str]]]:
    if not lines or lines[0] != "BEGIN:VCALENDAR" or lines[-1] != "END:VCALENDAR":
        _fail("BLS_ICS_CONTAINER_INVALID")
    calendar: dict[str, str] = {}
    events: list[dict[str, str]] = []
    stack = ["VCALENDAR"]
    current_event: dict[str, str] | None = None
    timezone_lines: list[str] = []
    for line in lines[1:-1]:
        if line.startswith("BEGIN:"):
            component = line[6:]
            if component == "VTIMEZONE" and stack == ["VCALENDAR"]:
                stack.append(component)
                timezone_lines.append(line)
                continue
            if component in {"DAYLIGHT", "STANDARD"} and stack[-1] == "VTIMEZONE":
                stack.append(component)
                timezone_lines.append(line)
                continue
            if component == "VEVENT" and stack == ["VCALENDAR"]:
                stack.append(component)
                current_event = {}
                continue
            _fail("BLS_ICS_COMPONENT_INVALID")
        if line.startswith("END:"):
            component = line[4:]
            if not stack or component != stack[-1]:
                _fail("BLS_ICS_COMPONENT_INVALID")
            if component in {"DAYLIGHT", "STANDARD", "VTIMEZONE"}:
                timezone_lines.append(line)
                stack.pop()
                continue
            if component == "VEVENT":
                assert current_event is not None
                events.append(current_event)
                current_event = None
                stack.pop()
                continue
            _fail("BLS_ICS_COMPONENT_INVALID")
        if stack[-1] in {"VTIMEZONE", "DAYLIGHT", "STANDARD"}:
            timezone_lines.append(line)
            continue
        if ":" not in line:
            _fail("BLS_ICS_PROPERTY_INVALID")
        name, content = line.split(":", 1)
        target = current_event if stack[-1] == "VEVENT" else calendar
        assert target is not None
        if name in target:
            _fail("BLS_ICS_PROPERTY_DUPLICATE")
        target[name] = content
    if stack != ["VCALENDAR"] or current_event is not None:
        _fail("BLS_ICS_COMPONENT_INVALID")
    expected_timezone = [
        "BEGIN:VTIMEZONE",
        "TZID:US-Eastern",
        "BEGIN:DAYLIGHT",
        "TZOFFSETFROM:-0500",
        "TZOFFSETTO:-0400",
        "DTSTART:20070311T020000",
        "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU",
        "TZNAME:EDT",
        "END:DAYLIGHT",
        "BEGIN:STANDARD",
        "TZOFFSETFROM:-0400",
        "TZOFFSETTO:-0500",
        "DTSTART:20071104T020000",
        "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU",
        "TZNAME:EST",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]
    if timezone_lines != expected_timezone:
        _fail("BLS_ICS_TIMEZONE_CONTRACT_DRIFT")
    return calendar, events


def _parse_bls(
    payload: bytes,
    source_digest: str,
    observed_at_ms: int,
) -> dict[str, Any]:
    calendar, raw_events = _parse_ical_properties(_unfold_icalendar(payload))
    expected_calendar = {
        "PRODID": "-//Department of Labor//Bureau of Labor Statistics//EN",
        "VERSION": "2.0",
        "CALSCALE": "GREGORIAN",
        "METHOD": "PUBLISH",
        "SUMMARY": "BLS.gov Economic News Release Schedule",
        "X-WR-CALNAME": "BLS.gov Economic News Release Schedule",
        "X-WR-TIMEZONE": "US-Eastern",
    }
    if calendar != expected_calendar or not (2 <= len(raw_events) <= 1024):
        _fail("BLS_ICS_CALENDAR_CONTRACT_DRIFT")
    expected_event_names = {
        "SEQUENCE", "CLASS", "UID", "DTSTART;TZID=US-Eastern", "DURATION",
        "SUMMARY", "LOCATION", "TRANSP", "CATEGORIES",
    }
    derived_events: list[dict[str, Any]] = []
    all_times: list[datetime] = []
    identities: set[str] = set()
    for raw in raw_events:
        if set(raw) != expected_event_names:
            _fail("BLS_ICS_EVENT_CONTRACT_DRIFT")
        if (
                raw["SEQUENCE"] != "1" or raw["CLASS"] != "PUBLIC" or
                raw["DURATION"] != "PT0M" or
                raw["LOCATION"] != r"Washington\, DC" or
                raw["TRANSP"] != "TRANSPARENT" or
                raw["CATEGORIES"] != "IMPORTANT, BLS" or
                UUID_PATTERN.fullmatch(raw["UID"]) is None):
            _fail("BLS_ICS_EVENT_CONTRACT_DRIFT")
        if raw["UID"] in identities:
            _fail("BLS_ICS_EVENT_DUPLICATE")
        identities.add(raw["UID"])
        try:
            local = datetime.strptime(
                raw["DTSTART;TZID=US-Eastern"], "%Y%m%dT%H%M%S")
        except ValueError as error:
            raise ExtractionError("BLS_ICS_TIME_INVALID") from error
        scheduled = _us_eastern_to_utc(local)
        all_times.append(scheduled)
        title = _normalized_text(raw["SUMMARY"], "BLS_ICS_TITLE_INVALID")
        if _in_event_window(scheduled, observed_at_ms):
            derived_events.append({
                "event_id": "bls-" + raw["UID"],
                "currencies": ["USD"],
                "importance": "high",
                "scheduled_at_ms": _milliseconds(scheduled),
                "title_sha256": _text_digest(title),
                "source_content_sha256": source_digest,
            })
    if all_times != sorted(all_times):
        _fail("BLS_ICS_EVENT_ORDER_INVALID")
    observed = _utc_datetime(observed_at_ms)
    if not (all_times[0] <= observed <= all_times[-1]):
        _fail("BLS_ICS_OBSERVATION_OUTSIDE_COVERAGE")
    return {
        "events": derived_events,
        "items": [],
        "published_at_ms": None,
        "revision": (
            f"bls-{all_times[0].date().isoformat()}-"
            f"{all_times[-1].date().isoformat()}-{len(all_times)}"
        ),
        "completeness": {
            "source_content_sha256": source_digest,
            "coverage_start_ms": _milliseconds(all_times[0]),
            "coverage_end_ms": _milliseconds(all_times[-1]),
            "currencies": ["USD"],
            "complete": True,
            "derived_by_extractor": True,
            "rule_id": "bls-rfc5545-all-vevents",
            "rule_version": EXTRACTOR_VERSION,
        },
    }


def _meeting_dates(year: int, month_label: str, days_label: str) -> list[date]:
    months = [
        MONTHS.get(part)
        for part in _normalized_text(
            month_label, "OFFICIAL_CALENDAR_MONTH_INVALID").split("/")
    ]
    if any(month is None for month in months) or len(months) not in {1, 2}:
        _fail("OFFICIAL_CALENDAR_MONTH_INVALID")
    match = DATE_RANGE_PATTERN.fullmatch(
        _normalized_text(days_label, "OFFICIAL_CALENDAR_DAYS_INVALID"))
    if match is None:
        _fail("OFFICIAL_CALENDAR_DAYS_INVALID")
    start_day = int(match.group(1))
    end_day = int(match.group(2) or match.group(1))
    start_month = int(months[0])
    end_month = int(months[-1])
    end_year = year + (1 if end_month < start_month else 0)
    try:
        start = date(year, start_month, start_day)
        end = date(end_year, end_month, end_day)
    except ValueError as error:
        raise ExtractionError("OFFICIAL_CALENDAR_DATE_INVALID") from error
    if end < start or end - start > timedelta(days=3):
        _fail("OFFICIAL_CALENDAR_DATE_RANGE_INVALID")
    values: list[date] = []
    current = start
    while current <= end:
        values.append(current)
        current += timedelta(days=1)
    return values


def _all_day_events(
    *,
    provider_prefix: str,
    event_date: date,
    title: str,
    currency: str,
    source_digest: str,
    observed_at_ms: int,
) -> list[dict[str, Any]]:
    day_start = datetime.combine(
        event_date, time(0, 0), tzinfo=timezone.utc) - ALL_DAY_GUARD
    day_end = (
        datetime.combine(event_date + timedelta(days=1), time(0, 0),
                         tzinfo=timezone.utc)
        + ALL_DAY_GUARD
    )
    window_start, window_end = _event_window(observed_at_ms)
    if day_end < window_start or day_start > window_end:
        return []
    title_sha256 = _text_digest(title)
    description_suffix = hashlib.sha256(
        title.encode("utf-8")).hexdigest()[:12]
    values: list[dict[str, Any]] = []
    sentinel = day_start + SENTINEL_LEAD
    index = 0
    while sentinel < day_end:
        values.append({
            "event_id": (
                f"{provider_prefix}-{event_date.strftime('%Y%m%d')}-"
                f"{description_suffix}-s{index:02d}"
            ),
            "currencies": [currency],
            "importance": "high",
            "scheduled_at_ms": _milliseconds(sentinel),
            "title_sha256": title_sha256,
            "source_content_sha256": source_digest,
        })
        sentinel += SENTINEL_STEP
        index += 1
    if index != 64:
        _fail("OFFICIAL_CALENDAR_SENTINEL_INVARIANT_FAILED")
    return values


def _parse_fed_calendar(
    payload: bytes,
    source_digest: str,
    observed_at_ms: int,
) -> dict[str, Any]:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ExtractionError("FED_CALENDAR_UTF8_INVALID") from error
    if (
            "<h3>Meeting calendars, statements, and minutes (" not in text or
            "The FOMC holds eight regularly scheduled meetings during the year"
            not in text):
        _fail("FED_CALENDAR_PAGE_CONTRACT_DRIFT")
    sections = list(FOMC_SECTION_PATTERN.finditer(text))
    if not sections:
        _fail("FED_CALENDAR_SECTIONS_MISSING")
    meetings: list[tuple[date, str]] = []
    years: set[int] = set()
    for section in sections:
        year = int(section.group(1))
        if year in years:
            _fail("FED_CALENDAR_YEAR_DUPLICATE")
        years.add(year)
        rows = list(FOMC_ROW_PATTERN.finditer(section.group("body")))
        if len(rows) != 8:
            _fail("FED_CALENDAR_YEAR_INCOMPLETE")
        for row in rows:
            title = (
                f"Federal Open Market Committee meeting "
                f"{_normalized_text(row.group('month'), 'FED_CALENDAR_INVALID')} "
                f"{_normalized_text(row.group('days'), 'FED_CALENDAR_INVALID')}, "
                f"{year}"
            )
            for event_date in _meeting_dates(
                    year, row.group("month"), row.group("days")):
                meetings.append((event_date, title))
    if len(years) < 1 or max(years) - min(years) + 1 != len(years):
        _fail("FED_CALENDAR_YEAR_RANGE_INVALID")
    last_update_match = re.search(
        r'<div class=[\'"]lastUpdate[\'"] id="lastUpdate">Last Update:\s*'
        r"([A-Za-z]+ [0-9]{2}, [0-9]{4})\s*</div>",
        text,
        re.DOTALL,
    )
    if last_update_match is None:
        _fail("FED_CALENDAR_REVISION_MISSING")
    try:
        revision_date = datetime.strptime(
            last_update_match.group(1), "%B %d, %Y").date()
    except ValueError as error:
        raise ExtractionError("FED_CALENDAR_REVISION_INVALID") from error
    meetings.sort(key=lambda value: (value[0], value[1]))
    if len({(value[0], value[1]) for value in meetings}) != len(meetings):
        _fail("FED_CALENDAR_MEETING_DUPLICATE")
    coverage_start = datetime.combine(
        meetings[0][0], time(0, 0), tzinfo=timezone.utc) - ALL_DAY_GUARD
    coverage_end = (
        datetime.combine(meetings[-1][0] + timedelta(days=1), time(0, 0),
                         tzinfo=timezone.utc)
        + ALL_DAY_GUARD
    )
    observed = _utc_datetime(observed_at_ms)
    if not (coverage_start <= observed <= coverage_end):
        _fail("FED_CALENDAR_OBSERVATION_OUTSIDE_COVERAGE")
    events: list[dict[str, Any]] = []
    for event_date, title in meetings:
        events.extend(_all_day_events(
            provider_prefix="fed-fomc",
            event_date=event_date,
            title=title,
            currency="USD",
            source_digest=source_digest,
            observed_at_ms=observed_at_ms,
        ))
    return {
        "events": events,
        "items": [],
        "published_at_ms": _milliseconds(datetime.combine(
            revision_date, time(0, 0), tzinfo=timezone.utc)),
        "revision": "fed-fomc-" + revision_date.isoformat(),
        "completeness": {
            "source_content_sha256": source_digest,
            "coverage_start_ms": _milliseconds(coverage_start),
            "coverage_end_ms": _milliseconds(coverage_end),
            "currencies": ["USD"],
            "complete": True,
            "derived_by_extractor": True,
            "rule_id": "fed-fomc-calendar-all-meetings",
            "rule_version": EXTRACTOR_VERSION,
        },
    }


def _parse_ecb_calendar(
    payload: bytes,
    source_digest: str,
    observed_at_ms: int,
) -> dict[str, Any]:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ExtractionError("ECB_CALENDAR_UTF8_INVALID") from error
    required = (
        '<link rel="canonical" href="' + ECB_CALENDAR_URL + '">',
        "<h1>Schedules for the meetings of the Governing Council and "
        "General Council of the ECB and related press conferences</h1>",
        '<div class="definition-list -zebra">',
    )
    if any(marker not in text for marker in required):
        _fail("ECB_CALENDAR_PAGE_CONTRACT_DRIFT")
    published_match = re.search(
        r'<meta property="article:published_time"\s+content="'
        r'([0-9]{4}-[0-9]{2}-[0-9]{2})">',
        text,
    )
    if published_match is None:
        _fail("ECB_CALENDAR_PUBLICATION_MISSING")
    try:
        published_date = date.fromisoformat(published_match.group(1))
    except ValueError as error:
        raise ExtractionError("ECB_CALENDAR_PUBLICATION_INVALID") from error
    entries = list(ECB_ENTRY_PATTERN.finditer(text))
    if not (2 <= len(entries) <= 256):
        _fail("ECB_CALENDAR_ENTRIES_INVALID")
    parsed: list[tuple[date, str]] = []
    for entry in entries:
        try:
            event_date = datetime.strptime(
                entry.group("date"), "%d/%m/%Y").date()
        except ValueError as error:
            raise ExtractionError("ECB_CALENDAR_DATE_INVALID") from error
        title = _normalized_text(
            entry.group("title"), "ECB_CALENDAR_TITLE_INVALID")
        if not (
                title.startswith("Governing Council of the ECB:") or
                title.startswith("General Council meeting of the ECB")):
            _fail("ECB_CALENDAR_TITLE_CONTRACT_DRIFT")
        parsed.append((event_date, title))
    if parsed != sorted(parsed) or len(set(parsed)) != len(parsed):
        _fail("ECB_CALENDAR_ENTRY_ORDER_INVALID")
    coverage_start = datetime.combine(
        published_date, time(0, 0), tzinfo=timezone.utc)
    coverage_end = (
        datetime.combine(parsed[-1][0] + timedelta(days=1), time(0, 0),
                         tzinfo=timezone.utc)
        + ALL_DAY_GUARD
    )
    observed = _utc_datetime(observed_at_ms)
    if not (coverage_start <= observed <= coverage_end):
        _fail("ECB_CALENDAR_OBSERVATION_OUTSIDE_COVERAGE")
    events: list[dict[str, Any]] = []
    for event_date, title in parsed:
        events.extend(_all_day_events(
            provider_prefix="ecb-gc",
            event_date=event_date,
            title=title,
            currency="EUR",
            source_digest=source_digest,
            observed_at_ms=observed_at_ms,
        ))
    return {
        "events": events,
        "items": [],
        "published_at_ms": _milliseconds(coverage_start),
        "revision": "ecb-gc-" + published_date.isoformat(),
        "completeness": {
            "source_content_sha256": source_digest,
            "coverage_start_ms": _milliseconds(coverage_start),
            "coverage_end_ms": _milliseconds(coverage_end),
            "currencies": ["EUR"],
            "complete": True,
            "derived_by_extractor": True,
            "rule_id": "ecb-gc-calendar-all-entries",
            "rule_version": EXTRACTOR_VERSION,
        },
    }


def _xml_text(element: ElementTree.Element, name: str) -> str:
    matches = [child for child in element if child.tag == name]
    if len(matches) != 1 or list(matches[0]):
        _fail("OFFICIAL_RSS_FIELD_INVALID")
    return _normalized_text(
        matches[0].text or "", "OFFICIAL_RSS_FIELD_INVALID", maximum=8192)


def _rss_time(value: str) -> datetime:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError) as error:
        raise ExtractionError("OFFICIAL_RSS_TIME_INVALID") from error
    if parsed.tzinfo is None:
        _fail("OFFICIAL_RSS_TIME_INVALID")
    return parsed.astimezone(timezone.utc)


def _parse_rss(
    payload: bytes,
    *,
    role: str,
    source_digest: str,
    fetched_at_ms: int,
) -> dict[str, Any]:
    stripped = payload.lstrip(b"\xef\xbb\xbf")
    upper = stripped.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        _fail("OFFICIAL_RSS_DTD_NOT_ALLOWED")
    try:
        root = ElementTree.fromstring(stripped)
    except ElementTree.ParseError as error:
        raise ExtractionError("OFFICIAL_RSS_XML_INVALID") from error
    if root.tag != "rss" or root.attrib != {"version": "2.0"}:
        _fail("OFFICIAL_RSS_ROOT_CONTRACT_DRIFT")
    channels = [child for child in root if child.tag == "channel"]
    if len(channels) != 1 or len(root) != 1:
        _fail("OFFICIAL_RSS_CHANNEL_INVALID")
    channel = channels[0]
    if role == "fed-press":
        expected_channel_fields = {
            "title", "link", "description", "language", "item",
        }
        expected_item_fields = {
            "title", "link", "guid", "description", "category", "pubDate",
        }
        expected_title = "FRB: Press Release - All Releases"
        expected_description = (
            "All recent press releases from the Federal Reserve Board"
        )
        expected_link = "https://www.federalreserve.gov/feeds/feeds.htm"
        currency = "USD"
        prefix = "fed-rss"
        channel_build: datetime | None = None
    elif role == "ecb-press":
        expected_channel_fields = {
            "title", "link", "description", "language", "copyright",
            "webMaster", "lastBuildDate", "category", "generator", "docs",
            "item",
        }
        expected_item_fields = {"title", "link", "guid", "pubDate"}
        expected_title = "ECB - European Central Bank"
        expected_description = (
            "Latest releases on the ECB website - Press releases, speeches "
            "and interviews, press conferences."
        )
        expected_link = "https://www.ecb.europa.eu/"
        currency = "EUR"
        prefix = "ecb-rss"
        channel_build = _rss_time(_xml_text(channel, "lastBuildDate"))
    else:
        _fail("OFFICIAL_RSS_ROLE_INVALID")
    if {child.tag for child in channel} != expected_channel_fields:
        _fail("OFFICIAL_RSS_CHANNEL_CONTRACT_DRIFT")
    for field in expected_channel_fields - {"item"}:
        if sum(child.tag == field for child in channel) != 1:
            _fail("OFFICIAL_RSS_CHANNEL_CONTRACT_DRIFT")
    if (
            _xml_text(channel, "title") != expected_title or
            _xml_text(channel, "description") != expected_description or
            _xml_text(channel, "link") != expected_link or
            _xml_text(channel, "language") != "en"):
        _fail("OFFICIAL_RSS_IDENTITY_INVALID")
    raw_items = [child for child in channel if child.tag == "item"]
    if not (1 <= len(raw_items) <= 256):
        _fail("OFFICIAL_RSS_ITEMS_INVALID")
    published_values: list[datetime] = []
    items: list[dict[str, Any]] = []
    identities: set[str] = set()
    for element in raw_items:
        if {child.tag for child in element} != expected_item_fields:
            _fail("OFFICIAL_RSS_ITEM_CONTRACT_DRIFT")
        title = _xml_text(element, "title")
        link = _xml_text(element, "link")
        guid = _xml_text(element, "guid")
        published = _rss_time(_xml_text(element, "pubDate"))
        if link != guid or guid in identities:
            _fail("OFFICIAL_RSS_ITEM_IDENTITY_INVALID")
        parsed_link = urlsplit(link)
        expected_hosts = (
            {"federalreserve.gov", "www.federalreserve.gov"}
            if role == "fed-press"
            else {"ecb.europa.eu", "www.ecb.europa.eu"}
        )
        if (
                parsed_link.scheme != "https" or
                parsed_link.hostname not in expected_hosts or
                parsed_link.username is not None or
                parsed_link.password is not None or
                parsed_link.fragment):
            _fail("OFFICIAL_RSS_ITEM_LINK_INVALID")
        identities.add(guid)
        normalized_content: dict[str, Any] = {
            "title": title,
            "link": link,
            "guid": guid,
            "published_at_ms": _milliseconds(published),
        }
        if role == "fed-press":
            normalized_content["description"] = _xml_text(
                element, "description")
            normalized_content["category"] = _xml_text(element, "category")
        items.append({
            "item_id": _identifier(prefix, guid),
            "published_at_ms": _milliseconds(published),
            "observed_at_ms": fetched_at_ms,
            "content_sha256": digest_bytes(canonical_bytes(
                normalized_content)),
            "confidence": 1.0,
            "currencies": [currency],
            "conflict_group": None,
            "source_content_sha256": source_digest,
        })
        published_values.append(published)
    if published_values != sorted(published_values, reverse=True):
        _fail("OFFICIAL_RSS_ITEM_ORDER_INVALID")
    fetched = _utc_datetime(fetched_at_ms)
    if published_values[0] > fetched:
        _fail("OFFICIAL_RSS_ITEM_FROM_FUTURE")
    if channel_build is not None:
        # ECB can prebuild its channel shortly before an item's nominal
        # publication time.  The two RSS timestamps describe different
        # objects and are therefore bounded independently by the root fetch.
        if channel_build > fetched:
            _fail("OFFICIAL_RSS_BUILD_TIME_INVALID")
        published_at = channel_build
    else:
        published_at = published_values[0]
    return {
        "events": [],
        "items": items,
        "published_at_ms": _milliseconds(published_at),
        "revision": f"{prefix}-{published_at.isoformat()}-{len(items)}",
        "completeness": {
            "source_content_sha256": source_digest,
            "coverage_start_ms": _milliseconds(published_values[-1]),
            "coverage_end_ms": fetched_at_ms,
            "currencies": [currency],
            "complete": True,
            "derived_by_extractor": True,
            "rule_id": "official-rss-all-items-at-root-fetch",
            "rule_version": EXTRACTOR_VERSION,
        },
    }


def derive(
    source_values: Any,
    payloads_by_path: dict[str, bytes],
    observed_at_ms: int,
) -> dict[str, Any]:
    """Derive receipt sections solely from pinned formats and retained bytes."""
    if not isinstance(source_values, list) or len(source_values) != 5:
        _fail("OFFICIAL_SOURCE_SET_INVALID")
    if (
            isinstance(observed_at_ms, bool) or
            not isinstance(observed_at_ms, int) or observed_at_ms < 0):
        _fail("OFFICIAL_SOURCE_OBSERVED_TIME_INVALID")
    captures = [
        _capture_source(value, observed_at_ms) for value in source_values
    ]
    roles = [capture["role"] for capture in captures]
    if len(set(roles)) != len(roles) or set(roles) != set(REQUIRED_ROLES):
        _fail("OFFICIAL_SOURCE_SET_INVALID")
    if set(payloads_by_path) != {
            capture["payload_path"] for capture in captures}:
        _fail("OFFICIAL_SOURCE_PAYLOAD_SET_INVALID")
    sources: list[dict[str, Any]] = []
    completeness: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    for capture in sorted(captures, key=lambda value: value["role"]):
        payload = payloads_by_path.get(capture["payload_path"])
        if not isinstance(payload, bytes) or not payload:
            _fail("OFFICIAL_SOURCE_PAYLOAD_INVALID")
        if len(payload) > MAX_PAYLOAD_BYTES:
            _fail("OFFICIAL_SOURCE_PAYLOAD_TOO_LARGE")
        source_digest = digest_bytes(payload)
        role = capture["role"]
        if role == "bls-calendar":
            derived = _parse_bls(payload, source_digest, observed_at_ms)
        elif role == "fed-calendar":
            derived = _parse_fed_calendar(
                payload, source_digest, observed_at_ms)
        elif role == "ecb-calendar":
            derived = _parse_ecb_calendar(
                payload, source_digest, observed_at_ms)
        elif role in {"fed-press", "ecb-press"}:
            derived = _parse_rss(
                payload,
                role=role,
                source_digest=source_digest,
                fetched_at_ms=capture["fetched_at_ms"],
            )
        else:
            _fail("OFFICIAL_SOURCE_ROLE_INVALID")
        source = {
            key: capture[key]
            for key in CAPTURE_SOURCE_FIELDS
        }
        source.update({
            "published_at_ms": derived["published_at_ms"],
            "revision": derived["revision"],
            "content_sha256": source_digest,
        })
        sources.append(source)
        completeness.append(derived["completeness"])
        events.extend(derived["events"])
        items.extend(derived["items"])
    sources.sort(key=lambda value: (value["provider"], value["final_url"]))
    completeness.sort(key=lambda value: value["source_content_sha256"])
    events.sort(key=lambda value: (
        value["scheduled_at_ms"], value["event_id"]))
    items.sort(key=lambda value: (
        value["published_at_ms"], value["item_id"]))
    if len(events) > MAX_EVENTS or len(items) > MAX_ITEMS:
        _fail("OFFICIAL_SOURCE_SEMANTIC_OUTPUT_TOO_LARGE")
    if len({value["event_id"] for value in events}) != len(events):
        _fail("OFFICIAL_SOURCE_EVENT_ID_DUPLICATE")
    if len({value["item_id"] for value in items}) != len(items):
        _fail("OFFICIAL_SOURCE_ITEM_ID_DUPLICATE")
    return {
        "sources": sources,
        "completeness": completeness,
        "events": events,
        "items": items,
    }


def _trusted_payload(path: Path, expected_uid: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(
        os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ExtractionError("OFFICIAL_SOURCE_PAYLOAD_READ_FAILED") from error
    try:
        before = os.fstat(descriptor)
        if (
                not stat.S_ISREG(before.st_mode) or
                before.st_uid != expected_uid or before.st_nlink != 1 or
                before.st_mode & 0o222 or
                not (0 < before.st_size <= MAX_PAYLOAD_BYTES)):
            _fail("OFFICIAL_SOURCE_PAYLOAD_METADATA_INVALID")
        chunks: list[bytes] = []
        remaining = MAX_PAYLOAD_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(1 << 20, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        try:
            current = path.lstat()
        except OSError as error:
            raise ExtractionError(
                "OFFICIAL_SOURCE_PAYLOAD_CHANGED_DURING_READ") from error
        if (
                len(payload) != before.st_size or
                after.st_dev != before.st_dev or
                after.st_ino != before.st_ino or
                after.st_size != before.st_size or
                after.st_mtime_ns != before.st_mtime_ns or
                current.st_dev != before.st_dev or
                current.st_ino != before.st_ino or
                current.st_nlink != 1):
            _fail("OFFICIAL_SOURCE_PAYLOAD_CHANGED_DURING_READ")
        return payload
    finally:
        os.close(descriptor)


def _create_canonical(path: Path, document: Any, mode: int) -> None:
    contents = canonical_bytes(document)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_descriptor: int | None = None
    temporary_path: Path | None = None
    try:
        temporary_descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.")
        temporary_path = Path(temporary_name)
        os.fchmod(temporary_descriptor, mode)
        offset = 0
        while offset < len(contents):
            offset += os.write(temporary_descriptor, contents[offset:])
        os.fsync(temporary_descriptor)
        os.close(temporary_descriptor)
        temporary_descriptor = None
        try:
            os.link(temporary_path, path, follow_symlinks=False)
        except FileExistsError as error:
            raise ExtractionError("OFFICIAL_SOURCE_OUTPUT_EXISTS") from error
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary_descriptor is not None:
            os.close(temporary_descriptor)
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _extractor_digest() -> str:
    return digest_bytes(Path(__file__).read_bytes())


def produce(
    capture_manifest: Path,
    evidence_root: Path,
    receipt_output: Path,
    bundle_output: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        root = evidence_root.resolve(strict=True)
        manifest_path = capture_manifest.resolve(strict=True)
        receipt_parent = receipt_output.parent.resolve(strict=True)
    except OSError as error:
        raise ExtractionError("OFFICIAL_SOURCE_EVIDENCE_ROOT_INVALID") from error
    try:
        manifest_path.relative_to(root)
        receipt_output.relative_to(root)
    except ValueError as error:
        raise ExtractionError("OFFICIAL_SOURCE_PATH_OUTSIDE_ROOT") from error
    if receipt_parent != receipt_output.parent or receipt_output.exists():
        _fail("OFFICIAL_SOURCE_RECEIPT_PATH_INVALID")
    root_metadata = root.lstat()
    manifest_metadata = manifest_path.lstat()
    expected_uid = os.geteuid()
    if (
            not stat.S_ISDIR(root_metadata.st_mode) or
            stat.S_ISLNK(root_metadata.st_mode) or
            root_metadata.st_uid != expected_uid or
            root_metadata.st_mode & 0o022 or
            not stat.S_ISREG(manifest_metadata.st_mode) or
            stat.S_ISLNK(manifest_metadata.st_mode) or
            manifest_metadata.st_uid != expected_uid or
            manifest_metadata.st_nlink != 1 or
            manifest_metadata.st_mode & 0o222):
        _fail("OFFICIAL_SOURCE_CAPTURE_OWNERSHIP_INVALID")
    manifest_bytes = _trusted_payload(manifest_path, expected_uid)
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ExtractionError("OFFICIAL_SOURCE_CAPTURE_JSON_INVALID") from error
    if canonical_bytes(manifest) != manifest_bytes:
        _fail("OFFICIAL_SOURCE_CAPTURE_NOT_CANONICAL")
    try:
        require_exact_fields(
            manifest, CAPTURE_FIELDS, "OFFICIAL_SOURCE_CAPTURE_FIELDS_INVALID")
    except ContractError as error:
        raise ExtractionError(str(error)) from error
    if (
            manifest["schema"] != CAPTURE_SCHEMA or
            manifest["version"] != 1):
        _fail("OFFICIAL_SOURCE_CAPTURE_SCHEMA_INVALID")
    for field in BOUNDARY_FIELDS:
        if manifest[field] is not False:
            _fail("OFFICIAL_SOURCE_CAPTURE_BOUNDARY_INVALID")
    try:
        observed_at_ms = require_int(
            manifest["observed_at_ms"],
            "OFFICIAL_SOURCE_OBSERVED_TIME_INVALID",
            minimum=0,
        )
    except ContractError as error:
        raise ExtractionError(str(error)) from error
    captures = [
        _capture_source(value, observed_at_ms)
        for value in manifest["sources"]
    ] if isinstance(manifest["sources"], list) else []
    if len(captures) != 5:
        _fail("OFFICIAL_SOURCE_SET_INVALID")
    payloads: dict[str, bytes] = {}
    total = 0
    for capture in captures:
        path = manifest_path.parent / capture["payload_path"]
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as error:
            raise ExtractionError(
                "OFFICIAL_SOURCE_PAYLOAD_PATH_INVALID") from error
        if resolved != path:
            _fail("OFFICIAL_SOURCE_PAYLOAD_PATH_INVALID")
        payload = _trusted_payload(path, expected_uid)
        total += len(payload)
        if total > MAX_TOTAL_PAYLOAD_BYTES:
            _fail("OFFICIAL_SOURCE_PAYLOAD_TOTAL_TOO_LARGE")
        payloads[capture["payload_path"]] = payload
    derived = derive(manifest["sources"], payloads, observed_at_ms)
    semantic = {
        "events": derived["events"],
        "items": derived["items"],
    }
    receipt_body = {
        "schema": RECEIPT_SCHEMA,
        "version": 1,
        "observed_at_ms": observed_at_ms,
        "extractor": {
            "extractor_id": EXTRACTOR_ID,
            "extractor_version": EXTRACTOR_VERSION,
            "extractor_code_sha256": _extractor_digest(),
            "deterministic": True,
        },
        "sources": derived["sources"],
        "completeness": derived["completeness"],
        "events": derived["events"],
        "items": derived["items"],
        "semantic_output_sha256": digest_bytes(canonical_bytes(semantic)),
        "mutation_attempted": False,
        "direct_broker_access": False,
        "paper_authorized": False,
        "live_authorized": False,
    }
    receipt = {
        **receipt_body,
        "body_sha256": digest_bytes(canonical_bytes(receipt_body)),
    }
    receipt_reference = receipt_output.relative_to(root).as_posix()
    bundle = {
        "schema": BUNDLE_SCHEMA,
        "observed_at_ms": observed_at_ms,
        "extraction_receipt_path": receipt_reference,
        "extraction_receipt_sha256": digest_bytes(canonical_bytes(receipt)),
        "mutation_attempted": False,
        "direct_broker_access": False,
        "paper_authorized": False,
        "live_authorized": False,
    }
    _create_canonical(receipt_output, receipt, 0o400)
    try:
        _create_canonical(bundle_output, bundle, 0o600)
    except Exception:
        try:
            receipt_output.unlink()
        except OSError:
            pass
        raise
    return receipt, bundle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-manifest", required=True, type=Path)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--receipt-output", required=True, type=Path)
    parser.add_argument("--bundle-output", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        receipt, _bundle = produce(
            arguments.capture_manifest,
            arguments.evidence_root,
            arguments.receipt_output,
            arguments.bundle_output,
        )
    except (ContractError, ExtractionError, OSError) as error:
        print(
            "hepta_market_official_source_extractor: FAIL: " + str(error),
            file=sys.stderr,
        )
        return 2
    print(
        "hepta_market_official_source_extractor: PASS "
        f"events={len(receipt['events'])} items={len(receipt['items'])} "
        f"receipt_sha256={digest_bytes(canonical_bytes(receipt))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
