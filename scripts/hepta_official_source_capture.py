#!/usr/bin/env python3

"""Root-owned HTTPS capture seam for the five pinned EUR/USD sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import ssl
import stat
import sys
import tempfile
import time
from typing import Any
import urllib.error
import urllib.request

import hepta_market_evidence_normalizer as normalizer
import hepta_market_official_source_extractor as extractor
from hepta_strategy_contracts import ContractError, canonical_bytes, digest_bytes


CAPTURE_RECEIPT_SCHEMA = "hepta.official-source-root-capture-receipt.v1"
USER_AGENT = "HeptaTrader-SHADOW-Evidence/1.0"
TIMEOUT_SECONDS = 25
MAX_RESPONSE_BYTES = extractor.MAX_PAYLOAD_BYTES
SOURCE_ORDER = (
    ("BLS", extractor.BLS_URL, "bls-calendar.ics", "text/calendar"),
    ("FEDERAL_RESERVE", extractor.FED_CALENDAR_URL,
     "fed-fomc-calendar.html", "text/html"),
    ("ECB", extractor.ECB_CALENDAR_URL,
     "ecb-governing-council-calendar.html", "text/html"),
    ("FEDERAL_RESERVE", extractor.FED_PRESS_URL,
     "fed-press.xml", "application/rss+xml"),
    ("ECB", extractor.ECB_PRESS_URL,
     "ecb-press.xml", "application/rss+xml"),
)


class CaptureError(RuntimeError):
    """Fail-closed official transport or publication error."""


class RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        raise CaptureError("OFFICIAL_CAPTURE_REDIRECT_REJECTED")


def _digest_file(path: Path) -> str:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise CaptureError("OFFICIAL_CAPTURE_FILE_INVALID")
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _opener() -> urllib.request.OpenerDirector:
    context = ssl.create_default_context()
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        RejectRedirect(),
        urllib.request.HTTPSHandler(context=context),
    )


def fetch(
    opener: urllib.request.OpenerDirector,
    provider: str,
    url: str,
    expected_content_type: str,
) -> dict[str, Any]:
    started_at_ms = int(time.time() * 1000)
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": expected_content_type,
            "Accept-Encoding": "identity",
            "Cache-Control": "no-cache",
        },
    )
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            status = response.getcode()
            final_url = response.geturl()
            content_type = response.headers.get_content_type()
            content_encoding = response.headers.get("Content-Encoding", "identity")
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except CaptureError:
        raise
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as error:
        raise CaptureError("OFFICIAL_CAPTURE_HTTPS_FAILED") from error
    fetched_at_ms = int(time.time() * 1000)
    rule = extractor.SOURCE_RULES.get((provider, url))
    if rule is None:
        raise CaptureError("OFFICIAL_CAPTURE_SOURCE_NOT_PINNED")
    if (status != 200 or final_url != url or
            content_type not in rule["content_types"] or
            content_encoding.lower() not in {"", "identity"} or
            not payload or len(payload) > MAX_RESPONSE_BYTES):
        raise CaptureError("OFFICIAL_CAPTURE_RESPONSE_INVALID")
    return {
        "provider": provider,
        "requested_url": url,
        "final_url": final_url,
        "http_status": status,
        "content_type": content_type,
        "fetch_started_at_ms": started_at_ms,
        "fetched_at_ms": fetched_at_ms,
        "payload": payload,
    }


def _secure_directory(path: Path, gid: int) -> None:
    if os.path.lexists(path):
        metadata = path.lstat()
        if (not stat.S_ISDIR(metadata.st_mode) or
                stat.S_ISLNK(metadata.st_mode) or metadata.st_uid != 0 or
                metadata.st_mode & 0o022):
            raise CaptureError("OFFICIAL_CAPTURE_DIRECTORY_INVALID")
        os.chown(path, 0, gid)
        os.chmod(path, 0o750)
        metadata = path.lstat()
        if (metadata.st_uid != 0 or metadata.st_gid != gid or
                stat.S_IMODE(metadata.st_mode) != 0o750):
            raise CaptureError("OFFICIAL_CAPTURE_DIRECTORY_INVALID")
        return
    path.mkdir(parents=True, mode=0o750)
    os.chown(path, 0, gid)
    os.chmod(path, 0o750)


def _atomic_write(path: Path, payload: bytes, gid: int, mode: int = 0o440) -> None:
    if os.path.lexists(path):
        raise CaptureError("OFFICIAL_CAPTURE_OUTPUT_EXISTS")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        os.fchown(descriptor, 0, gid)
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.link(temporary, path, follow_symlinks=False)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _atomic_replace_export(
        path: Path, payload: bytes, gid: int, mode: int = 0o440) -> None:
    owner_uid = os.geteuid()
    if os.path.lexists(path):
        metadata = path.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or
                stat.S_ISLNK(metadata.st_mode) or
                metadata.st_nlink != 1 or metadata.st_uid != owner_uid or
                metadata.st_gid != gid or
                stat.S_IMODE(metadata.st_mode) != mode):
            raise CaptureError("OFFICIAL_CAPTURE_EXPORT_REPLACE_UNSAFE")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        os.fchown(descriptor, owner_uid, gid)
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        metadata = path.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or
                stat.S_ISLNK(metadata.st_mode) or
                metadata.st_nlink != 1 or metadata.st_uid != owner_uid or
                metadata.st_gid != gid or
                stat.S_IMODE(metadata.st_mode) != mode or
                path.read_bytes() != payload):
            raise CaptureError("OFFICIAL_CAPTURE_EXPORT_REPLACE_INVALID")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def capture(
    evidence_root: Path,
    export_root: Path,
    receipt_output: Path,
    reader_uid: int,
    reader_gid: int,
    capture_helper_sha256: str,
) -> dict[str, Any]:
    if os.geteuid() != 0:
        raise CaptureError("OFFICIAL_CAPTURE_ROOT_REQUIRED")
    if (evidence_root != Path("/var/lib/hepta/market-evidence") or
            export_root != Path("/var/lib/hepta/shadow-observation") or
            receipt_output.parent != evidence_root or
            reader_uid != 1000 or reader_gid != 1000):
        raise CaptureError("OFFICIAL_CAPTURE_PATH_OR_READER_INVALID")
    actual_helper_sha256 = digest_bytes(Path(__file__).read_bytes())
    if capture_helper_sha256 != actual_helper_sha256:
        raise CaptureError("OFFICIAL_CAPTURE_HELPER_DIGEST_MISMATCH")
    _secure_directory(evidence_root, reader_gid)
    _secure_directory(export_root, reader_gid)
    opener = _opener()
    responses = [
        fetch(opener, provider, url, content_type)
        for provider, url, _name, content_type in SOURCE_ORDER
    ]
    observed_at_ms = max(
        int(time.time() * 1000),
        max(response["fetched_at_ms"] for response in responses),
    )
    transaction = evidence_root / f"capture-{observed_at_ms}"
    _secure_directory(transaction, reader_gid)
    sources: list[dict[str, Any]] = []
    transport_sources: list[dict[str, Any]] = []
    for response, (_provider, _url, filename, _type) in zip(
            responses, SOURCE_ORDER, strict=True):
        payload_path = transaction / filename
        _atomic_write(payload_path, response["payload"], reader_gid)
        relative = payload_path.relative_to(transaction).as_posix()
        source = {
            key: response[key]
            for key in (
                "provider", "requested_url", "final_url", "http_status",
                "content_type", "fetch_started_at_ms", "fetched_at_ms",
            )
        }
        source["payload_path"] = relative
        sources.append(source)
        transport_sources.append({
            **source,
            "payload_sha256": digest_bytes(response["payload"]),
            "payload_bytes": len(response["payload"]),
        })
    manifest = {
        "schema": extractor.CAPTURE_SCHEMA,
        "version": 1,
        "observed_at_ms": observed_at_ms,
        "sources": sources,
        "mutation_attempted": False,
        "direct_broker_access": False,
        "paper_authorized": False,
        "live_authorized": False,
    }
    manifest_path = transaction / "capture-manifest.json"
    _atomic_write(manifest_path, canonical_bytes(manifest), reader_gid)
    extraction_receipt = transaction / "extraction-receipt.json"
    private_bundle = transaction / "source-bundle.json"
    receipt, bundle = extractor.produce(
        manifest_path,
        evidence_root,
        extraction_receipt,
        private_bundle,
    )
    for path in (extraction_receipt, private_bundle):
        os.chown(path, 0, reader_gid)
        os.chmod(path, 0o440)
    calendar, information = normalizer.normalize(bundle)
    exported_bundle = export_root / "official-source-bundle.json"
    exported_calendar = export_root / "economic-calendar.json"
    exported_information = export_root / "market-information.json"
    _atomic_replace_export(
        exported_bundle, canonical_bytes(bundle), reader_gid)
    _atomic_replace_export(
        exported_calendar, canonical_bytes(calendar), reader_gid)
    _atomic_replace_export(
        exported_information, canonical_bytes(information), reader_gid)
    body = {
        "schema": CAPTURE_RECEIPT_SCHEMA,
        "version": 1,
        "observed_at_ms": observed_at_ms,
        "transport": {
            "tls_verified": True,
            "proxy_inherited": False,
            "redirects_allowed": False,
            "credentials_used": False,
            "sources": transport_sources,
        },
        "capture_manifest_path": str(manifest_path),
        "capture_manifest_sha256": _digest_file(manifest_path),
        "extraction_receipt_path": str(extraction_receipt),
        "extraction_receipt_sha256": _digest_file(extraction_receipt),
        "extractor_sha256": digest_bytes(Path(extractor.__file__).read_bytes()),
        "capture_helper_sha256": actual_helper_sha256,
        "semantic_output_sha256": receipt["semantic_output_sha256"],
        "exported_bundle_path": str(exported_bundle),
        "exported_bundle_sha256": _digest_file(exported_bundle),
        "calendar_event_count": len(calendar["events"]),
        "information_item_count": len(information["items"]),
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
        "status": "OFFICIAL_CAPTURE_COMPLETE",
    }
    final_receipt = {
        **body,
        "body_sha256": digest_bytes(canonical_bytes(body)),
    }
    _atomic_write(receipt_output, canonical_bytes(final_receipt), reader_gid)
    return final_receipt


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--export-root", required=True, type=Path)
    parser.add_argument("--receipt-output", required=True, type=Path)
    parser.add_argument("--reader-uid", required=True, type=int)
    parser.add_argument("--reader-gid", required=True, type=int)
    parser.add_argument("--capture-helper-sha256", required=True)
    arguments = parser.parse_args()
    try:
        receipt = capture(
            arguments.evidence_root,
            arguments.export_root,
            arguments.receipt_output,
            arguments.reader_uid,
            arguments.reader_gid,
            arguments.capture_helper_sha256,
        )
    except (
            CaptureError, ContractError, extractor.ExtractionError,
            OSError, ValueError,
            urllib.error.URLError) as error:
        print(f"hepta-official-source-capture: FAIL: {error}", file=sys.stderr)
        return 2
    print(
        "hepta-official-source-capture: PASS "
        f"events={receipt['calendar_event_count']} "
        f"items={receipt['information_item_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
