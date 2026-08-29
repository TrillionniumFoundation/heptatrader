#!/usr/bin/env python3

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any


BLS = b"""BEGIN:VCALENDAR
PRODID:-//Department of Labor//Bureau of Labor Statistics//EN
VERSION:2.0
CALSCALE:GREGORIAN
METHOD:PUBLISH
SUMMARY:BLS.gov Economic News Release Schedule
X-WR-CALNAME:BLS.gov Economic News Release Schedule
X-WR-TIMEZONE:US-Eastern
BEGIN:VTIMEZONE
TZID:US-Eastern
BEGIN:DAYLIGHT
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
DTSTART:20070311T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
TZNAME:EDT
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
DTSTART:20071104T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
TZNAME:EST
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
SEQUENCE:1
CLASS:PUBLIC
UID:270ff9a3-e8c2-46e8-ba33-d8a3f51a32e5
DTSTART;TZID=US-Eastern:20260721T100000
DURATION:PT0M
SUMMARY:Job Openings and Labor Turnover Survey
LOCATION:Washington\\, DC
TRANSP:TRANSPARENT
CATEGORIES:IMPORTANT, BLS
END:VEVENT
BEGIN:VEVENT
SEQUENCE:1
CLASS:PUBLIC
UID:172bca9a-2f58-485b-a3e8-f882cf2c68cb
DTSTART;TZID=US-Eastern:20260729T100000
DURATION:PT0M
SUMMARY:State Employment and Unemployment (Monthly)
LOCATION:Washington\\, DC
TRANSP:TRANSPARENT
CATEGORIES:IMPORTANT, BLS
END:VEVENT
BEGIN:VEVENT
SEQUENCE:1
CLASS:PUBLIC
UID:4a8c96ea-6a24-463b-a19e-082ef5bf7cb5
DTSTART;TZID=US-Eastern:20260807T083000
DURATION:PT0M
SUMMARY:Employment Situation
LOCATION:Washington\\, DC
TRANSP:TRANSPARENT
CATEGORIES:IMPORTANT, BLS
END:VEVENT
END:VCALENDAR
"""

FED_ROWS = """
<div class="row fomc-meeting" ">
<div class="fomc-meeting__month col-xs-5 col-sm-3 col-md-2"><strong>{month}</strong></div>
<div class="fomc-meeting__date col-xs-4 col-sm-9 col-md-10 col-lg-1">{days}</div>
</div>
"""
FED_CALENDAR = (
    """<!DOCTYPE html><html><body>
<h3>Meeting calendars, statements, and minutes (2026-2026)</h3>
<p>The FOMC holds eight regularly scheduled meetings during the year and other meetings as needed.</p>
<div class="panel panel-default"><div class="panel-heading"><h4><a id="42828">2026 FOMC Meetings</a></h4></div>
"""
    + "".join(
        FED_ROWS.format(month=month, days=days)
        for month, days in (
            ("January", "27-28"),
            ("March", "17-18*"),
            ("April", "28-29"),
            ("June", "16-17*"),
            ("July", "28-29"),
            ("September", "15-16*"),
            ("October", "27-28"),
            ("December", "8-9*"),
        )
    )
    + """</div>
<div class='lastUpdate' id="lastUpdate">Last Update:
July 08, 2026
</div>
</body></html>
"""
).encode("utf-8")

ECB_CALENDAR = b"""<!DOCTYPE html>
<html lang="en"><head>
<meta property="article:published_time"  content="2026-07-27">
<link rel="canonical" href="https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html">
</head><body>
<h1>Schedules for the meetings of the Governing Council and General Council of the ECB and related press conferences</h1>
<div class="definition-list -zebra"><dl>
<dt>09/09/2026</dt>
<dd>Governing Council of the ECB: monetary policy meeting hosted by the Deutsche Bundesbank in Berlin, Germany (Day 1)<br></dd>
<dt>10/09/2026</dt>
<dd>Governing Council of the ECB: monetary policy meeting hosted by the Deutsche Bundesbank in Berlin, Germany (Day 2), followed by press conference<br></dd>
<dt>30/09/2026</dt>
<dd>Governing Council of the ECB: non-monetary policy meeting (virtual)<br></dd>
</dl></div>
</body></html>
"""

ECB_PRESS = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>
<title>ECB - European Central Bank</title>
<link>https://www.ecb.europa.eu/</link>
<description>Latest releases on the ECB website - Press releases, speeches and interviews, press conferences.</description>
<language>en</language>
<copyright>Copyright 2026, European Central Bank</copyright>
<webMaster>webmaster@ecb.europa.eu (ECB Webmaster)</webMaster>
<lastBuildDate>Tue, 28 Jul 2026 09:27:19 +0200</lastBuildDate>
<category>Press</category>
<generator>Automatic</generator>
<docs>http://blogs.law.harvard.edu/tech/rss</docs>
<item><title>Philip R. Lane: Outlook for the euro area economy</title><link>https://www.ecb.europa.eu//press/key/date/2026/html/ecb.sp260724~d484b483b9.en.pdf</link><guid>https://www.ecb.europa.eu//press/key/date/2026/html/ecb.sp260724~d484b483b9.en.pdf</guid><pubDate>Fri, 24 Jul 2026 17:30:00 +0200</pubDate></item>
<item><title>Monetary policy decisions</title><link>https://www.ecb.europa.eu//press/pr/date/2026/html/ecb.mp260723~29f24d99bc.en.html</link><guid>https://www.ecb.europa.eu//press/pr/date/2026/html/ecb.mp260723~29f24d99bc.en.html</guid><pubDate>Thu, 23 Jul 2026 14:15:00 +0200</pubDate></item>
</channel></rss>"""

FED_PRESS = b"""\xef\xbb\xbf<?xml version="1.0" encoding="utf-8" ?>
<rss version="2.0"><channel>
<title>FRB: Press Release - All Releases</title>
<link>https://www.federalreserve.gov/feeds/feeds.htm</link>
<description>All recent press releases from the Federal Reserve Board</description>
<language>en</language>
<item>
<title>Agencies issue joint statement on handling of highly sensitive information during bank examinations</title>
<link>https://www.federalreserve.gov/newsevents/pressreleases/bcreg20260716a.htm</link>
<guid>https://www.federalreserve.gov/newsevents/pressreleases/bcreg20260716a.htm</guid>
<description>Agencies issue joint statement on handling of highly sensitive information during bank examinations</description>
<category>Banking and Consumer Regulatory Policy</category>
<pubDate>Thu, 16 Jul 2026 18:00:00 GMT</pubDate>
</item>
<item>
<title>Minutes of the Board's discount rate meetings on June 8 and June 17, 2026</title>
<link>https://www.federalreserve.gov/newsevents/pressreleases/monetary20260714a.htm</link>
<guid>https://www.federalreserve.gov/newsevents/pressreleases/monetary20260714a.htm</guid>
<description>Minutes of the Board's discount rate meetings on June 8 and June 17, 2026</description>
<category>Monetary Policy</category>
<pubDate>Tue, 14 Jul 2026 18:00:00 GMT</pubDate>
</item>
</channel></rss>"""

FIXTURES = {
    "bls.ics": BLS,
    "ecb-calendar.html": ECB_CALENDAR,
    "ecb-press.xml": ECB_PRESS,
    "fed-fomc.html": FED_CALENDAR,
    "fed-press.xml": FED_PRESS,
}
CURRENT_ASSET_SHA256 = {
    "bls.ics":
        "00ac5dde1ff9a9fb8335b0a9918e8093fed98df503f6dbdcd6ceb60a3c595646",
    "ecb-calendar.html":
        "6388f6a294ed1445c2be91d62fa6d20b9030e2e1f8c73dc26e4cf16bd42f7302",
    "ecb-press.xml":
        "3dd76617f54af911196b9cb3909bb51a8533594f922233111bb8b77a819cc027",
    "fed-fomc.html":
        "bf100f2c82fdbcd408691ddf8a5b33ee58a7707503fbe3bf0b79b12251c99cbe",
    "fed-press.xml":
        "dfa77324fc08e3933b1fef0a54f2157440d52d827aa81626a7daed89e74da76a",
}
RETAINED_PREBUILT_CAPTURE = Path(
    "/var/lib/hepta/market-evidence/capture-1785484704868")
RETAINED_PREBUILT_MANIFEST_SHA256 = (
    "86b115120719366241eb0a167ac606d4873a403365ef39e5d4a598d6bcd74295"
)
RETAINED_PREBUILT_ECB_SHA256 = (
    "9e47d73ba2622c224f7d091f0f1abe4430871305386ab4b1d25dc782c6646b75"
)


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")


def observed_ms() -> int:
    # More than eight hours after the ECB channel lastBuildDate. A feed with
    # no new item remains complete at the exact root-attested HTTPS fetch.
    return int(datetime(
        2026, 7, 28, 16, 0, tzinfo=timezone.utc).timestamp() * 1000)


def source_values(extractor: Any, observed: int) -> list[dict[str, Any]]:
    started = observed - 1000
    values = [
        ("BLS", extractor.BLS_URL, "text/calendar", "bls.ics"),
        (
            "ECB", extractor.ECB_CALENDAR_URL, "text/html",
            "ecb-calendar.html",
        ),
        (
            "ECB", extractor.ECB_PRESS_URL, "application/xml",
            "ecb-press.xml",
        ),
        (
            "FEDERAL_RESERVE", extractor.FED_CALENDAR_URL, "text/html",
            "fed-fomc.html",
        ),
        (
            "FEDERAL_RESERVE", extractor.FED_PRESS_URL, "application/xml",
            "fed-press.xml",
        ),
    ]
    return [
        {
            "provider": provider,
            "requested_url": url,
            "final_url": url,
            "http_status": 200,
            "content_type": content_type,
            "fetch_started_at_ms": started,
            "fetched_at_ms": observed,
            "payload_path": name,
        }
        for provider, url, content_type, name in values
    ]


def manifest(extractor: Any, observed: int) -> dict[str, Any]:
    return {
        "schema": extractor.CAPTURE_SCHEMA,
        "version": 1,
        "observed_at_ms": observed,
        "sources": source_values(extractor, observed),
        "mutation_attempted": False,
        "direct_broker_access": False,
        "paper_authorized": False,
        "live_authorized": False,
    }


def expect_extraction_failure(
    extractor: Any,
    sources: list[dict[str, Any]],
    payloads: dict[str, bytes],
    observed: int,
    reason: str,
) -> None:
    try:
        extractor.derive(sources, payloads, observed)
    except extractor.ExtractionError as error:
        assert str(error) == reason, (str(error), reason)
    else:
        raise AssertionError(f"expected {reason}")


def expect_contract_failure(function: Any, reason: str) -> None:
    try:
        function()
    except Exception as error:
        assert str(error) == reason, (str(error), reason)
    else:
        raise AssertionError(f"expected {reason}")


def write_fixture_root(
    root: Path,
    extractor: Any,
    observed: int,
) -> tuple[Path, Path, Path]:
    root.mkdir(mode=0o700)
    for name, payload in FIXTURES.items():
        path = root / name
        path.write_bytes(payload)
        path.chmod(0o400)
    capture = root / "capture.json"
    capture.write_bytes(canonical(manifest(extractor, observed)))
    capture.chmod(0o400)
    return capture, root / "receipt.json", root.parent / "bundle.json"


def reseal_receipt(
    receipt_path: Path,
    bundle_path: Path,
    mutate: Any,
) -> None:
    receipt = json.loads(receipt_path.read_text(encoding="ascii"))
    mutate(receipt)
    receipt["semantic_output_sha256"] = (
        "sha256:" + hashlib.sha256(canonical({
            "events": receipt["events"],
            "items": receipt["items"],
        })).hexdigest()
    )
    body = dict(receipt)
    body.pop("body_sha256")
    receipt["body_sha256"] = (
        "sha256:" + hashlib.sha256(canonical(body)).hexdigest()
    )
    receipt_path.chmod(0o600)
    receipt_path.write_bytes(canonical(receipt))
    receipt_path.chmod(0o400)
    bundle = json.loads(bundle_path.read_text(encoding="ascii"))
    bundle["extraction_receipt_sha256"] = (
        "sha256:" + hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    )
    bundle_path.write_bytes(canonical(bundle))


def verify_current_assets_if_present(
    extractor: Any,
    normalizer: Any,
    observed: int,
) -> bool:
    root = Path("/tmp/hepta-official-sources-20260728")
    if not all((root / name).is_file() for name in CURRENT_ASSET_SHA256):
        return False
    payloads = {
        name: (root / name).read_bytes() for name in CURRENT_ASSET_SHA256
    }
    assert {
        name: hashlib.sha256(payload).hexdigest()
        for name, payload in payloads.items()
    } == CURRENT_ASSET_SHA256
    derived = extractor.derive(
        source_values(extractor, observed), payloads, observed)
    assert len(derived["events"]) == 134
    assert len(derived["items"]) == 35
    assert len(derived["completeness"]) == 5
    with tempfile.TemporaryDirectory(
            prefix="hepta-current-official-assets-") as temporary:
        directory = Path(temporary)
        evidence_root = directory / "evidence"
        evidence_root.mkdir(mode=0o700)
        for name, payload in payloads.items():
            target = evidence_root / name
            target.write_bytes(payload)
            target.chmod(0o400)
        capture = evidence_root / "capture.json"
        capture.write_bytes(canonical(manifest(extractor, observed)))
        capture.chmod(0o400)
        receipt_path = evidence_root / "receipt.json"
        bundle_path = directory / "bundle.json"
        _receipt, bundle = extractor.produce(
            capture, evidence_root, receipt_path, bundle_path)
        normalizer.TRUSTED_EVIDENCE_ROOTS = (evidence_root.resolve(),)
        normalizer.TRUSTED_ATTESTATION_UID = os.geteuid()
        calendar, information = normalizer.normalize(bundle)
        assert len(calendar["events"]) == 134
        assert len(information["items"]) == 35
    return True


def verify_retained_prebuilt_capture_if_present(extractor: Any) -> bool:
    manifest_path = RETAINED_PREBUILT_CAPTURE / "capture-manifest.json"
    ecb_path = RETAINED_PREBUILT_CAPTURE / "ecb-press.xml"
    if not manifest_path.is_file() or not ecb_path.is_file():
        return False
    assert (
        hashlib.sha256(manifest_path.read_bytes()).hexdigest() ==
        RETAINED_PREBUILT_MANIFEST_SHA256
    )
    assert (
        hashlib.sha256(ecb_path.read_bytes()).hexdigest() ==
        RETAINED_PREBUILT_ECB_SHA256
    )
    capture = json.loads(manifest_path.read_text(encoding="ascii"))
    payloads = {
        source["payload_path"]:
            (RETAINED_PREBUILT_CAPTURE / source["payload_path"]).read_bytes()
        for source in capture["sources"]
    }
    derived = extractor.derive(
        capture["sources"], payloads, capture["observed_at_ms"])
    ecb_source = next(
        source for source in derived["sources"]
        if source["requested_url"] == extractor.ECB_PRESS_URL
    )
    ecb_items = [
        item for item in derived["items"]
        if item["source_content_sha256"] == ecb_source["content_sha256"]
    ]
    newest_item = max(item["published_at_ms"] for item in ecb_items)
    assert ecb_source["published_at_ms"] == 1785397513000
    assert newest_item == 1785398400000
    assert ecb_source["published_at_ms"] < newest_item
    return True


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repository / "scripts"))
    import hepta_market_evidence_normalizer as normalizer
    import hepta_market_official_source_extractor as extractor

    observed = observed_ms()
    installed_code_digest = (
        "sha256:" + hashlib.sha256(
            (repository / "scripts" /
             "hepta_market_official_source_extractor.py").read_bytes()
        ).hexdigest()
    )
    assert normalizer.PINNED_EXTRACTORS == {
        (extractor.EXTRACTOR_ID, extractor.EXTRACTOR_VERSION):
            installed_code_digest,
    }
    sources = source_values(extractor, observed)
    payloads = dict(FIXTURES)
    derived = extractor.derive(sources, payloads, observed)
    assert len(derived["events"]) == 130
    assert len(derived["items"]) == 4
    assert len(derived["sources"]) == 5
    assert len(derived["completeness"]) == 5
    # Capture ordering is not a semantic input.
    assert extractor.derive(
        list(reversed(sources)), payloads, observed) == derived
    assert {
        item["currencies"][0] for item in derived["items"]
    } == {"EUR", "USD"}
    assert all(
        value["coverage_start_ms"] <= observed <=
        value["coverage_end_ms"]
        for value in derived["completeness"]
    )

    changed_payloads = dict(payloads)
    changed_payloads["bls.ics"] = BLS.replace(
        b"SUMMARY:Employment Situation",
        b"SUMMARY:Employment Situation revised",
    )
    changed = extractor.derive(sources, changed_payloads, observed)
    assert changed["events"] != derived["events"]

    changed_payloads = dict(payloads)
    changed_payloads["bls.ics"] = BLS.replace(
        b"CATEGORIES:IMPORTANT, BLS\n", b"", 1)
    expect_extraction_failure(
        extractor,
        sources,
        changed_payloads,
        observed,
        "BLS_ICS_EVENT_CONTRACT_DRIFT",
    )

    changed_payloads = dict(payloads)
    changed_payloads["fed-fomc.html"] = FED_CALENDAR.replace(
        b"The FOMC holds eight regularly scheduled meetings during the year",
        b"The schedule is partial",
    )
    expect_extraction_failure(
        extractor,
        sources,
        changed_payloads,
        observed,
        "FED_CALENDAR_PAGE_CONTRACT_DRIFT",
    )

    changed_payloads = dict(payloads)
    changed_payloads["ecb-calendar.html"] = ECB_CALENDAR.replace(
        b"<dt>30/09/2026</dt>", b"<dt>08/09/2026</dt>")
    expect_extraction_failure(
        extractor,
        sources,
        changed_payloads,
        observed,
        "ECB_CALENDAR_ENTRY_ORDER_INVALID",
    )

    changed_payloads = dict(payloads)
    changed_payloads["ecb-press.xml"] = ECB_PRESS.replace(
        b"<generator>Automatic</generator>",
        b"<generator>Automatic</generator><ttl>60</ttl>",
    )
    expect_extraction_failure(
        extractor,
        sources,
        changed_payloads,
        observed,
        "OFFICIAL_RSS_CHANNEL_CONTRACT_DRIFT",
    )

    # ECB can publish a channel build before an item's nominal publication
    # time.  Both values are independently valid once the root fetch observes
    # them, and the channel source time remains the exact lastBuildDate.
    prebuilt_observed = int(datetime(
        2026, 7, 30, 9, 0, tzinfo=timezone.utc).timestamp() * 1000)
    prebuilt_payloads = dict(payloads)
    prebuilt_payloads["ecb-press.xml"] = (
        ECB_PRESS.replace(
            b"Tue, 28 Jul 2026 09:27:19 +0200",
            b"Thu, 30 Jul 2026 09:45:13 +0200",
        ).replace(
            b"Fri, 24 Jul 2026 17:30:00 +0200",
            b"Thu, 30 Jul 2026 10:00:00 +0200",
            1,
        )
    )
    prebuilt = extractor.derive(
        source_values(extractor, prebuilt_observed),
        prebuilt_payloads,
        prebuilt_observed,
    )
    prebuilt_ecb_source = next(
        source for source in prebuilt["sources"]
        if source["requested_url"] == extractor.ECB_PRESS_URL
    )
    prebuilt_ecb_items = [
        item for item in prebuilt["items"]
        if item["source_content_sha256"] ==
        prebuilt_ecb_source["content_sha256"]
    ]
    assert prebuilt_ecb_source["published_at_ms"] == 1785397513000
    assert max(
        item["published_at_ms"] for item in prebuilt_ecb_items
    ) == 1785398400000

    changed_payloads = dict(payloads)
    changed_payloads["ecb-press.xml"] = ECB_PRESS.replace(
        b"Tue, 28 Jul 2026 09:27:19 +0200",
        b"Wed, 29 Jul 2026 18:00:00 +0000",
    )
    expect_extraction_failure(
        extractor,
        sources,
        changed_payloads,
        observed,
        "OFFICIAL_RSS_BUILD_TIME_INVALID",
    )

    changed_payloads = dict(payloads)
    changed_payloads["ecb-press.xml"] = ECB_PRESS.replace(
        b"Fri, 24 Jul 2026 17:30:00 +0200",
        b"Wed, 29 Jul 2026 18:00:00 +0000",
        1,
    )
    expect_extraction_failure(
        extractor,
        sources,
        changed_payloads,
        observed,
        "OFFICIAL_RSS_ITEM_FROM_FUTURE",
    )

    # A multi-hour interval without a new ECB release is not feed staleness:
    # the root HTTPS fetch time is the exact completeness boundary.
    delayed = observed + 8 * 60 * 60 * 1000
    delayed_derived = extractor.derive(
        source_values(extractor, delayed), payloads, delayed)
    ecb_feed = next(
        value for value in delayed_derived["completeness"]
        if value["source_content_sha256"] ==
        "sha256:" + hashlib.sha256(ECB_PRESS).hexdigest()
    )
    assert ecb_feed["coverage_end_ms"] == delayed

    actual_verified = verify_current_assets_if_present(
        extractor, normalizer, observed)
    retained_prebuilt_verified = (
        verify_retained_prebuilt_capture_if_present(extractor))

    with tempfile.TemporaryDirectory(
            prefix="hepta-official-extractor-") as temporary:
        directory = Path(temporary)
        evidence_root = directory / "evidence"
        capture, receipt_path, bundle_path = write_fixture_root(
            evidence_root, extractor, observed)
        receipt, bundle = extractor.produce(
            capture, evidence_root, receipt_path, bundle_path)
        assert receipt_path.stat().st_mode & 0o777 == 0o400
        assert bundle_path.stat().st_mode & 0o777 == 0o600
        assert receipt["events"] == derived["events"]
        assert receipt["items"] == derived["items"]
        assert (
            receipt["extractor"]["extractor_code_sha256"] ==
            "sha256:" + hashlib.sha256(
                (repository / "scripts" /
                 "hepta_market_official_source_extractor.py").read_bytes()
            ).hexdigest()
        )
        assert json.loads(bundle_path.read_text(encoding="ascii")) == bundle

        normalizer.TRUSTED_EVIDENCE_ROOTS = (evidence_root.resolve(),)
        normalizer.TRUSTED_ATTESTATION_UID = os.geteuid()
        code_digest = receipt["extractor"]["extractor_code_sha256"]
        assert code_digest == installed_code_digest
        calendar, information = normalizer.normalize(bundle)
        assert calendar["schema"] == "hepta.economic-calendar.v3"
        assert information["schema"] == "hepta.market-information-items.v3"
        assert len(calendar["events"]) == 130
        assert len(information["items"]) == 4

        reseal_receipt(
            receipt_path,
            bundle_path,
            lambda value: value["events"][0].__setitem__(
                "scheduled_at_ms",
                value["events"][0]["scheduled_at_ms"] + 1,
            ),
        )
        tampered_bundle = json.loads(bundle_path.read_text(encoding="ascii"))
        expect_contract_failure(
            lambda: normalizer.normalize(tampered_bundle),
            "EVIDENCE_EXTRACTOR_REPLAY_MISMATCH",
        )

    with tempfile.TemporaryDirectory(
            prefix="hepta-official-safe-read-") as temporary:
        directory = Path(temporary)
        evidence_root = directory / "evidence"
        capture, receipt_path, bundle_path = write_fixture_root(
            evidence_root, extractor, observed)
        extractor.produce(
            capture, evidence_root, receipt_path, bundle_path)
        normalizer.TRUSTED_EVIDENCE_ROOTS = (evidence_root.resolve(),)
        normalizer.TRUSTED_ATTESTATION_UID = os.geteuid()
        bundle = json.loads(bundle_path.read_text(encoding="ascii"))

        receipt_path.chmod(0o600)
        expect_contract_failure(
            lambda: normalizer.normalize(bundle),
            "EVIDENCE_EXTRACTION_RECEIPT_OWNERSHIP_INVALID",
        )
        receipt_path.chmod(0o400)

        payload = evidence_root / "bls.ics"
        payload.chmod(0o600)
        expect_contract_failure(
            lambda: normalizer.normalize(bundle),
            "EVIDENCE_RAW_PAYLOAD_OWNERSHIP_INVALID",
        )
        payload.chmod(0o400)

        alias = evidence_root / "receipt-hardlink.json"
        os.link(receipt_path, alias)
        expect_contract_failure(
            lambda: normalizer.normalize(bundle),
            "EVIDENCE_EXTRACTION_RECEIPT_OWNERSHIP_INVALID",
        )
        alias.unlink()

        symlink = evidence_root / "receipt-symlink.json"
        symlink.symlink_to(receipt_path.name)
        symlink_bundle = dict(bundle)
        symlink_bundle["extraction_receipt_path"] = symlink.name
        symlink_bundle["extraction_receipt_sha256"] = (
            "sha256:" + hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        )
        expect_contract_failure(
            lambda: normalizer.normalize(symlink_bundle),
            "EVIDENCE_ATTESTATION_PATH_UNTRUSTED",
        )

        original_read = normalizer.os.read
        moved = evidence_root / "bls-original.ics"
        swapped = False

        def swapping_read(descriptor: int, count: int) -> bytes:
            nonlocal swapped
            if not swapped:
                swapped = True
                payload.rename(moved)
                payload.write_bytes(b"replacement")
                payload.chmod(0o400)
            return original_read(descriptor, count)

        normalizer.os.read = swapping_read
        try:
            expect_contract_failure(
                lambda: normalizer._trusted_file_bytes(
                    payload,
                    maximum_bytes=normalizer.MAX_RAW_PAYLOAD_BYTES,
                    label="EVIDENCE_RAW_PAYLOAD",
                ),
                "EVIDENCE_RAW_PAYLOAD_CHANGED_DURING_READ",
            )
        finally:
            normalizer.os.read = original_read
            payload.unlink()
            moved.rename(payload)

    print(
        "hepta_market_official_source_extractor_tests: PASS "
        f"current_assets_verified={str(actual_verified).lower()} "
        "retained_prebuilt_verified="
        f"{str(retained_prebuilt_verified).lower()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
