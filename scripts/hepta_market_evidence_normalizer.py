#!/usr/bin/env python3

"""Normalize provenance-bound official market evidence without network access."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any
from urllib.parse import urlsplit

import hepta_market_official_source_extractor as official_extractor
from hepta_strategy_contracts import (
    ContractError,
    atomic_write_json,
    canonical_bytes,
    digest_bytes,
    load_document,
    require_digest,
    require_exact_fields,
    require_int,
    require_number,
    require_text,
)


MAX_SOURCE_AGE_MS = 15 * 60 * 1000
BUNDLE_V1_FIELDS = frozenset({
    "schema", "observed_at_ms", "sources", "events", "items",
    "mutation_attempted", "direct_broker_access", "paper_authorized",
    "live_authorized",
})
BUNDLE_V2_FIELDS = frozenset({
    "schema", "observed_at_ms", "extraction_receipt_path",
    "extraction_receipt_sha256", "mutation_attempted",
    "direct_broker_access", "paper_authorized", "live_authorized",
})
SOURCE_V1_FIELDS = frozenset({
    "provider", "source_ref", "retrieved_at_ms", "published_at_ms",
    "revision", "content_sha256", "coverage_start_ms", "coverage_end_ms",
    "currencies",
})
EXTRACTION_RECEIPT_FIELDS = frozenset({
    "schema", "version", "observed_at_ms", "extractor", "sources",
    "completeness", "events", "items", "semantic_output_sha256",
    "mutation_attempted", "direct_broker_access", "paper_authorized",
    "live_authorized", "body_sha256",
})
EXTRACTOR_FIELDS = frozenset({
    "extractor_id", "extractor_version", "extractor_code_sha256",
    "deterministic",
})
ATTESTED_SOURCE_FIELDS = frozenset({
    "provider", "requested_url", "final_url", "http_status",
    "content_type", "fetch_started_at_ms", "fetched_at_ms",
    "published_at_ms", "revision", "payload_path", "content_sha256",
})
COMPLETENESS_FIELDS = frozenset({
    "source_content_sha256", "coverage_start_ms", "coverage_end_ms",
    "currencies", "complete", "derived_by_extractor", "rule_id",
    "rule_version",
})
ATTESTATION_FIELDS = frozenset({
    "schema", "receipt_path", "receipt_file_sha256",
    "receipt_body_sha256", "extractor_id", "extractor_version",
    "extractor_code_sha256", "semantic_output_sha256",
    "completeness_sha256", "raw_payloads_verified",
    "fetch_metadata_verified", "semantic_derivation_attested",
    "completeness_attested",
})
EVENT_FIELDS = frozenset({
    "event_id", "currencies", "importance", "scheduled_at_ms",
    "title_sha256", "source_content_sha256",
})
ITEM_FIELDS = frozenset({
    "item_id", "published_at_ms", "observed_at_ms", "content_sha256",
    "confidence", "currencies", "conflict_group",
    "source_content_sha256",
})
PROVIDER_HOSTS = {
    "FEDERAL_RESERVE": frozenset({
        "federalreserve.gov", "www.federalreserve.gov",
    }),
    "BLS": frozenset({
        "bls.gov", "www.bls.gov", "blsmon1.bls.gov",
    }),
    "BEA": frozenset({
        "bea.gov", "www.bea.gov",
    }),
    "ECB": frozenset({
        "ecb.europa.eu", "www.ecb.europa.eu",
    }),
    "EUROSTAT": frozenset({
        "ec.europa.eu",
    }),
}
PROVIDER_CURRENCIES = {
    "FEDERAL_RESERVE": frozenset({"USD"}),
    "BLS": frozenset({"USD"}),
    "BEA": frozenset({"USD"}),
    "ECB": frozenset({"EUR"}),
    "EUROSTAT": frozenset({"EUR"}),
}
ALLOWED_CONTENT_TYPES = frozenset({
    "application/json",
    "application/rss+xml",
    "application/xml",
    "text/csv",
    "text/calendar",
    "text/html",
    "text/plain",
    "text/xml",
})
MAX_RAW_PAYLOAD_BYTES = 16 * 1024 * 1024
MAX_TOTAL_RAW_PAYLOAD_BYTES = 64 * 1024 * 1024
TRUSTED_ATTESTATION_UID = 0
TRUSTED_EVIDENCE_ROOTS = (
    Path("/run/hepta/market-evidence"),
    Path("/var/lib/hepta/market-evidence"),
)

# The normalizer hashes the installed pure extractor and replays it over every
# retained payload.  This mapping cannot be extended by an environment or
# receipt field. The value below is the reviewed source digest exercised by the
# official-format and adversarial replay tests.
PINNED_EXTRACTORS: dict[tuple[str, str], str] = {
    (
        official_extractor.EXTRACTOR_ID,
        official_extractor.EXTRACTOR_VERSION,
    ): "sha256:fea8620b44ddc53a5729a3d99cc12967d885ffccf32b04019cfcea59b8122d7d",
}


def _source_url(provider: str, value: Any) -> str:
    source_ref = require_text(
        value, "EVIDENCE_SOURCE_REF_INVALID", maximum=2048)
    parsed = urlsplit(source_ref)
    if (
            parsed.scheme != "https" or
            parsed.hostname not in PROVIDER_HOSTS[provider] or
            parsed.username is not None or
            parsed.password is not None or
            parsed.fragment or
            not parsed.path.startswith("/")):
        raise ContractError("EVIDENCE_SOURCE_REF_INVALID")
    return source_ref


def _trusted_root_for(path: Path) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise ContractError("EVIDENCE_ATTESTATION_PATH_INVALID")
    for configured in TRUSTED_EVIDENCE_ROOTS:
        try:
            root = configured.resolve(strict=True)
            if root != configured:
                continue
            candidate = path.resolve(strict=True)
            candidate.relative_to(root)
        except (OSError, ValueError):
            continue
        if candidate != path:
            raise ContractError("EVIDENCE_ATTESTATION_PATH_INVALID")
        return root
    raise ContractError("EVIDENCE_ATTESTATION_PATH_UNTRUSTED")


def _resolve_trusted_reference(value: Any) -> tuple[Path, str]:
    reference = require_text(
        value, "EVIDENCE_ATTESTATION_PATH_INVALID", maximum=4096)
    relative = Path(reference)
    if relative.is_absolute() or relative == Path(".") or ".." in relative.parts:
        raise ContractError("EVIDENCE_ATTESTATION_PATH_INVALID")
    candidates: list[Path] = []
    for configured in TRUSTED_EVIDENCE_ROOTS:
        candidate = configured / relative
        try:
            _trusted_root_for(candidate)
        except ContractError:
            continue
        candidates.append(candidate)
    if len(candidates) != 1:
        raise ContractError("EVIDENCE_ATTESTATION_PATH_UNTRUSTED")
    return candidates[0], relative.as_posix()


def _trusted_file_bytes(
    path: Path,
    *,
    maximum_bytes: int,
    label: str,
) -> bytes:
    root = _trusted_root_for(path)
    try:
        root_metadata = root.lstat()
    except OSError as error:
        raise ContractError(f"{label}_READ_FAILED") from error
    if (
            not stat.S_ISDIR(root_metadata.st_mode) or
            stat.S_ISLNK(root_metadata.st_mode) or
            root_metadata.st_uid != TRUSTED_ATTESTATION_UID or
            root_metadata.st_mode & 0o022):
        raise ContractError(f"{label}_OWNERSHIP_INVALID")
    current = root
    components = path.relative_to(root).parts
    for component in components[:-1]:
        current = current / component
        try:
            metadata = current.lstat()
        except OSError as error:
            raise ContractError(f"{label}_READ_FAILED") from error
        if (
                not stat.S_ISDIR(metadata.st_mode) or
                stat.S_ISLNK(metadata.st_mode) or
                metadata.st_uid != TRUSTED_ATTESTATION_UID or
                metadata.st_mode & 0o022):
            raise ContractError(f"{label}_OWNERSHIP_INVALID")
    try:
        path_metadata = path.lstat()
    except OSError as error:
        raise ContractError(f"{label}_READ_FAILED") from error
    if (
            not stat.S_ISREG(path_metadata.st_mode) or
            stat.S_ISLNK(path_metadata.st_mode) or
            path_metadata.st_uid != TRUSTED_ATTESTATION_UID or
            path_metadata.st_nlink != 1 or
            path_metadata.st_mode & 0o222 or
            path_metadata.st_size > maximum_bytes):
        raise ContractError(f"{label}_OWNERSHIP_INVALID")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) |
            getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as error:
        raise ContractError(f"{label}_READ_FAILED") from error
    try:
        metadata = os.fstat(descriptor)
        if (
                not stat.S_ISREG(metadata.st_mode) or
                metadata.st_uid != TRUSTED_ATTESTATION_UID or
                metadata.st_nlink != 1 or
                metadata.st_mode & 0o222 or
                metadata.st_size > maximum_bytes or
                metadata.st_dev != path_metadata.st_dev or
                metadata.st_ino != path_metadata.st_ino):
            raise ContractError(f"{label}_OWNERSHIP_INVALID")
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(1 << 20, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        contents = b"".join(chunks)
        if len(contents) > maximum_bytes:
            raise ContractError(f"{label}_TOO_LARGE")
        after = os.fstat(descriptor)
        try:
            current_metadata = path.lstat()
        except OSError as error:
            raise ContractError(f"{label}_CHANGED_DURING_READ") from error
        if (
                after.st_dev != metadata.st_dev or
                after.st_ino != metadata.st_ino or
                after.st_size != metadata.st_size or
                after.st_mtime_ns != metadata.st_mtime_ns or
                after.st_mode != metadata.st_mode or
                after.st_uid != metadata.st_uid or
                after.st_nlink != 1 or
                current_metadata.st_dev != metadata.st_dev or
                current_metadata.st_ino != metadata.st_ino or
                current_metadata.st_size != metadata.st_size or
                current_metadata.st_mtime_ns != metadata.st_mtime_ns or
                current_metadata.st_mode != metadata.st_mode or
                current_metadata.st_uid != metadata.st_uid or
                current_metadata.st_nlink != 1 or
                len(contents) != metadata.st_size):
            raise ContractError(f"{label}_CHANGED_DURING_READ")
        return contents
    finally:
        os.close(descriptor)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ContractError("EVIDENCE_RECEIPT_DUPLICATE_KEY")
        value[key] = item
    return value


def _trusted_document(
    path: Path,
    *,
    maximum_bytes: int,
    label: str,
) -> tuple[dict[str, Any], bytes]:
    contents = _trusted_file_bytes(
        path, maximum_bytes=maximum_bytes, label=label)
    try:
        document = json.loads(
            contents.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ContractError("EVIDENCE_RECEIPT_NON_FINITE")),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ContractError(f"{label}_JSON_INVALID") from error
    if not isinstance(document, dict):
        raise ContractError(f"{label}_ROOT_INVALID")
    if canonical_bytes(document) != contents:
        raise ContractError(f"{label}_NOT_CANONICAL")
    return document, contents


def _currencies(value: Any, *, allow_empty: bool) -> list[str]:
    if (
            not isinstance(value, list) or
            (not allow_empty and not value) or
            len(value) > 2 or
            any(currency not in {"EUR", "USD"} for currency in value) or
            len(set(value)) != len(value)):
        raise ContractError("EVIDENCE_CURRENCIES_INVALID")
    return list(value)


def _normalize_sources(
    values: Any,
    observed_at_ms: int,
) -> tuple[list[dict[str, Any]], set[str]]:
    if not isinstance(values, list) or not values or len(values) > 32:
        raise ContractError("EVIDENCE_SOURCES_INVALID")
    normalized: list[dict[str, Any]] = []
    digests: set[str] = set()
    identities: set[tuple[str, str]] = set()
    for value in values:
        source = require_exact_fields(
            value, SOURCE_V1_FIELDS, "EVIDENCE_SOURCE_FIELDS_INVALID")
        provider = require_text(
            source["provider"], "EVIDENCE_PROVIDER_INVALID",
            identifier=True)
        if provider not in PROVIDER_HOSTS:
            raise ContractError("EVIDENCE_PROVIDER_NOT_ALLOWED")
        source_ref = _source_url(provider, source["source_ref"])
        retrieved_at_ms = require_int(
            source["retrieved_at_ms"], "EVIDENCE_RETRIEVED_TIME_INVALID",
            minimum=0, maximum=observed_at_ms)
        if observed_at_ms - retrieved_at_ms > MAX_SOURCE_AGE_MS:
            raise ContractError("EVIDENCE_SOURCE_STALE")
        published_at_ms = source["published_at_ms"]
        if published_at_ms is not None:
            published_at_ms = require_int(
                published_at_ms, "EVIDENCE_PUBLISHED_TIME_INVALID",
                minimum=0, maximum=retrieved_at_ms)
        revision = require_text(
            source["revision"], "EVIDENCE_REVISION_INVALID",
            maximum=256)
        content_sha256 = require_digest(
            source["content_sha256"], "EVIDENCE_SOURCE_DIGEST_INVALID")
        coverage_start_ms = require_int(
            source["coverage_start_ms"], "EVIDENCE_COVERAGE_INVALID",
            minimum=0)
        coverage_end_ms = require_int(
            source["coverage_end_ms"], "EVIDENCE_COVERAGE_INVALID",
            minimum=coverage_start_ms)
        currencies = _currencies(
            source["currencies"], allow_empty=False)
        if not set(currencies).issubset(PROVIDER_CURRENCIES[provider]):
            raise ContractError("EVIDENCE_COVERAGE_CURRENCY_INVALID")
        identity = (provider, source_ref)
        if identity in identities or content_sha256 in digests:
            raise ContractError("EVIDENCE_SOURCE_DUPLICATE")
        identities.add(identity)
        digests.add(content_sha256)
        normalized.append({
            "provider": provider,
            "source_ref": source_ref,
            "retrieved_at_ms": retrieved_at_ms,
            "published_at_ms": published_at_ms,
            "revision": revision,
            "content_sha256": content_sha256,
            "coverage_start_ms": coverage_start_ms,
            "coverage_end_ms": coverage_end_ms,
            "currencies": currencies,
        })
    normalized.sort(
        key=lambda source: (source["provider"], source["source_ref"]))
    return normalized, digests


def _normalize_events(
    values: Any,
    source_digests: set[str],
) -> list[dict[str, Any]]:
    if not isinstance(values, list) or len(values) > 2048:
        raise ContractError("EVIDENCE_EVENTS_INVALID")
    normalized: list[dict[str, Any]] = []
    event_ids: set[str] = set()
    for value in values:
        event = require_exact_fields(
            value, EVENT_FIELDS, "EVIDENCE_EVENT_FIELDS_INVALID")
        event_id = require_text(
            event["event_id"], "EVIDENCE_EVENT_ID_INVALID",
            identifier=True)
        if event_id in event_ids:
            raise ContractError("EVIDENCE_EVENT_DUPLICATE")
        event_ids.add(event_id)
        currencies = _currencies(event["currencies"], allow_empty=False)
        importance = event["importance"]
        if importance not in {"low", "medium", "high"}:
            raise ContractError("EVIDENCE_EVENT_IMPORTANCE_INVALID")
        scheduled_at_ms = require_int(
            event["scheduled_at_ms"], "EVIDENCE_EVENT_TIME_INVALID",
            minimum=0)
        title_sha256 = require_digest(
            event["title_sha256"], "EVIDENCE_EVENT_TITLE_DIGEST_INVALID")
        source_content_sha256 = require_digest(
            event["source_content_sha256"],
            "EVIDENCE_EVENT_SOURCE_DIGEST_INVALID")
        if source_content_sha256 not in source_digests:
            raise ContractError("EVIDENCE_EVENT_SOURCE_UNBOUND")
        normalized.append({
            "event_id": event_id,
            "currencies": currencies,
            "importance": importance,
            "scheduled_at_ms": scheduled_at_ms,
            "title_sha256": title_sha256,
            "source_content_sha256": source_content_sha256,
        })
    normalized.sort(
        key=lambda event: (event["scheduled_at_ms"], event["event_id"]))
    return normalized


def _normalize_items(
    values: Any,
    source_digests: set[str],
    observed_at_ms: int,
) -> list[dict[str, Any]]:
    if not isinstance(values, list) or len(values) > 2048:
        raise ContractError("EVIDENCE_ITEMS_INVALID")
    normalized: list[dict[str, Any]] = []
    item_ids: set[str] = set()
    for value in values:
        item = require_exact_fields(
            value, ITEM_FIELDS, "EVIDENCE_ITEM_FIELDS_INVALID")
        item_id = require_text(
            item["item_id"], "EVIDENCE_ITEM_ID_INVALID", identifier=True)
        if item_id in item_ids:
            raise ContractError("EVIDENCE_ITEM_DUPLICATE")
        item_ids.add(item_id)
        published_at_ms = require_int(
            item["published_at_ms"], "EVIDENCE_ITEM_PUBLISHED_INVALID",
            minimum=0, maximum=observed_at_ms)
        item_observed_at_ms = require_int(
            item["observed_at_ms"], "EVIDENCE_ITEM_OBSERVED_INVALID",
            minimum=published_at_ms, maximum=observed_at_ms)
        content_sha256 = require_digest(
            item["content_sha256"], "EVIDENCE_ITEM_DIGEST_INVALID")
        confidence = require_number(
            item["confidence"], "EVIDENCE_ITEM_CONFIDENCE_INVALID",
            minimum=0.0, maximum=1.0)
        currencies = _currencies(item["currencies"], allow_empty=True)
        conflict_group = item["conflict_group"]
        if conflict_group is not None:
            conflict_group = require_text(
                conflict_group, "EVIDENCE_ITEM_CONFLICT_INVALID",
                identifier=True)
        source_content_sha256 = require_digest(
            item["source_content_sha256"],
            "EVIDENCE_ITEM_SOURCE_DIGEST_INVALID")
        if source_content_sha256 not in source_digests:
            raise ContractError("EVIDENCE_ITEM_SOURCE_UNBOUND")
        normalized.append({
            "item_id": item_id,
            "published_at_ms": published_at_ms,
            "observed_at_ms": item_observed_at_ms,
            "content_sha256": content_sha256,
            "confidence": confidence,
            "currencies": currencies,
            "conflict_group": conflict_group,
            "source_content_sha256": source_content_sha256,
        })
    normalized.sort(
        key=lambda item: (item["published_at_ms"], item["item_id"]))
    return normalized


def _normalize_attested_sources(
    values: Any,
    completeness_values: Any,
    observed_at_ms: int,
    receipt_path: Path,
) -> tuple[
        list[dict[str, Any]],
        set[str],
        str,
        dict[str, bytes],
]:
    if not isinstance(values, list) or not values or len(values) > 32:
        raise ContractError("EVIDENCE_ATTESTED_SOURCES_INVALID")
    raw_sources: dict[str, dict[str, Any]] = {}
    identities: set[tuple[str, str]] = set()
    payload_paths: set[str] = set()
    payloads_by_path: dict[str, bytes] = {}
    total_payload_bytes = 0
    for value in values:
        source = require_exact_fields(
            value, ATTESTED_SOURCE_FIELDS,
            "EVIDENCE_ATTESTED_SOURCE_FIELDS_INVALID")
        provider = require_text(
            source["provider"], "EVIDENCE_PROVIDER_INVALID",
            identifier=True)
        if provider not in PROVIDER_HOSTS:
            raise ContractError("EVIDENCE_PROVIDER_NOT_ALLOWED")
        requested_url = _source_url(provider, source["requested_url"])
        final_url = _source_url(provider, source["final_url"])
        if source["http_status"] != 200:
            raise ContractError("EVIDENCE_FETCH_STATUS_INVALID")
        content_type = require_text(
            source["content_type"], "EVIDENCE_CONTENT_TYPE_INVALID",
            maximum=128)
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise ContractError("EVIDENCE_CONTENT_TYPE_INVALID")
        fetch_started_at_ms = require_int(
            source["fetch_started_at_ms"], "EVIDENCE_FETCH_TIME_INVALID",
            minimum=0, maximum=observed_at_ms)
        fetched_at_ms = require_int(
            source["fetched_at_ms"], "EVIDENCE_FETCH_TIME_INVALID",
            minimum=fetch_started_at_ms, maximum=observed_at_ms)
        if observed_at_ms - fetched_at_ms > MAX_SOURCE_AGE_MS:
            raise ContractError("EVIDENCE_SOURCE_STALE")
        published_at_ms = source["published_at_ms"]
        if published_at_ms is not None:
            published_at_ms = require_int(
                published_at_ms, "EVIDENCE_PUBLISHED_TIME_INVALID",
                minimum=0, maximum=fetched_at_ms)
        revision = require_text(
            source["revision"], "EVIDENCE_REVISION_INVALID", maximum=256)
        payload_value = require_text(
            source["payload_path"], "EVIDENCE_PAYLOAD_PATH_INVALID",
            maximum=512)
        payload_relative = Path(payload_value)
        if (
                payload_relative.is_absolute() or
                payload_relative == Path(".") or
                ".." in payload_relative.parts):
            raise ContractError("EVIDENCE_PAYLOAD_PATH_INVALID")
        payload_path = receipt_path.parent / payload_relative
        payload_identity = str(payload_relative)
        if payload_identity in payload_paths:
            raise ContractError("EVIDENCE_PAYLOAD_DUPLICATE")
        payload_paths.add(payload_identity)
        payload = _trusted_file_bytes(
            payload_path,
            maximum_bytes=MAX_RAW_PAYLOAD_BYTES,
            label="EVIDENCE_RAW_PAYLOAD",
        )
        payloads_by_path[payload_identity] = payload
        total_payload_bytes += len(payload)
        if total_payload_bytes > MAX_TOTAL_RAW_PAYLOAD_BYTES:
            raise ContractError("EVIDENCE_RAW_PAYLOAD_TOTAL_TOO_LARGE")
        content_sha256 = require_digest(
            source["content_sha256"], "EVIDENCE_SOURCE_DIGEST_INVALID")
        if digest_bytes(payload) != content_sha256:
            raise ContractError("EVIDENCE_RAW_PAYLOAD_DIGEST_MISMATCH")
        identity = (provider, final_url)
        if identity in identities or content_sha256 in raw_sources:
            raise ContractError("EVIDENCE_SOURCE_DUPLICATE")
        identities.add(identity)
        raw_sources[content_sha256] = {
            "provider": provider,
            "requested_url": requested_url,
            "final_url": final_url,
            "http_status": 200,
            "content_type": content_type,
            "fetch_started_at_ms": fetch_started_at_ms,
            "fetched_at_ms": fetched_at_ms,
            "published_at_ms": published_at_ms,
            "revision": revision,
            "payload_path": payload_identity,
            "content_sha256": content_sha256,
        }

    if (
            not isinstance(completeness_values, list) or
            len(completeness_values) != len(raw_sources)):
        raise ContractError("EVIDENCE_COMPLETENESS_INVALID")
    completeness_by_digest: dict[str, dict[str, Any]] = {}
    for value in completeness_values:
        completeness = require_exact_fields(
            value, COMPLETENESS_FIELDS, "EVIDENCE_COMPLETENESS_FIELDS_INVALID")
        source_digest = require_digest(
            completeness["source_content_sha256"],
            "EVIDENCE_COMPLETENESS_SOURCE_INVALID")
        if (
                source_digest not in raw_sources or
                source_digest in completeness_by_digest):
            raise ContractError("EVIDENCE_COMPLETENESS_SOURCE_INVALID")
        coverage_start_ms = require_int(
            completeness["coverage_start_ms"],
            "EVIDENCE_COVERAGE_INVALID",
            minimum=0, maximum=observed_at_ms)
        coverage_end_ms = require_int(
            completeness["coverage_end_ms"],
            "EVIDENCE_COVERAGE_INVALID",
            minimum=raw_sources[source_digest]["fetched_at_ms"])
        currencies = _currencies(
            completeness["currencies"], allow_empty=False)
        provider = raw_sources[source_digest]["provider"]
        if not set(currencies).issubset(PROVIDER_CURRENCIES[provider]):
            raise ContractError("EVIDENCE_COVERAGE_CURRENCY_INVALID")
        if (
                completeness["complete"] is not True or
                completeness["derived_by_extractor"] is not True):
            raise ContractError("EVIDENCE_COMPLETENESS_NOT_ATTESTED")
        rule_id = require_text(
            completeness["rule_id"], "EVIDENCE_COMPLETENESS_RULE_INVALID",
            identifier=True)
        rule_version = require_text(
            completeness["rule_version"],
            "EVIDENCE_COMPLETENESS_RULE_INVALID",
            identifier=True)
        completeness_by_digest[source_digest] = {
            "source_content_sha256": source_digest,
            "coverage_start_ms": coverage_start_ms,
            "coverage_end_ms": coverage_end_ms,
            "currencies": currencies,
            "complete": True,
            "derived_by_extractor": True,
            "rule_id": rule_id,
            "rule_version": rule_version,
        }
    if set(completeness_by_digest) != set(raw_sources):
        raise ContractError("EVIDENCE_COMPLETENESS_INVALID")
    covered_currencies = {
        currency
        for completeness in completeness_by_digest.values()
        for currency in completeness["currencies"]
    }
    if covered_currencies != {"EUR", "USD"}:
        raise ContractError("EVIDENCE_COMPLETENESS_CURRENCY_INVALID")

    normalized: list[dict[str, Any]] = []
    for source_digest, source in raw_sources.items():
        completeness = completeness_by_digest[source_digest]
        normalized.append({
            "provider": source["provider"],
            "source_ref": source["final_url"],
            "retrieved_at_ms": source["fetched_at_ms"],
            "published_at_ms": source["published_at_ms"],
            "revision": source["revision"],
            "content_sha256": source_digest,
            "coverage_start_ms": completeness["coverage_start_ms"],
            "coverage_end_ms": completeness["coverage_end_ms"],
            "currencies": completeness["currencies"],
        })
    normalized.sort(
        key=lambda source: (source["provider"], source["source_ref"]))
    normalized_completeness = sorted(
        completeness_by_digest.values(),
        key=lambda value: value["source_content_sha256"],
    )
    return (
        normalized,
        set(raw_sources),
        digest_bytes(canonical_bytes(normalized_completeness)),
        payloads_by_path,
    )


def _replay_official_extractor(
    receipt: dict[str, Any],
    payloads_by_path: dict[str, bytes],
    observed_at_ms: int,
    extractor_id: str,
    extractor_version: str,
    extractor_code_sha256: str,
) -> dict[str, Any] | None:
    identity = (extractor_id, extractor_version)
    official_identity = (
        official_extractor.EXTRACTOR_ID,
        official_extractor.EXTRACTOR_VERSION,
    )
    if identity != official_identity:
        # Unit tests may replace PINNED_EXTRACTORS with a local fixture
        # identity. Production contains only official_identity.
        return None
    try:
        runtime_path = Path(official_extractor.__file__).resolve(strict=True)
        runtime_digest = digest_bytes(runtime_path.read_bytes())
    except OSError as error:
        raise ContractError("EVIDENCE_EXTRACTOR_RUNTIME_READ_FAILED") from error
    if runtime_digest != extractor_code_sha256:
        raise ContractError("EVIDENCE_EXTRACTOR_RUNTIME_DIGEST_MISMATCH")
    try:
        replayed = official_extractor.derive(
            receipt["sources"], payloads_by_path, observed_at_ms)
    except official_extractor.ExtractionError as error:
        raise ContractError("EVIDENCE_EXTRACTOR_REPLAY_FAILED") from error
    for field in ("sources", "completeness", "events", "items"):
        if canonical_bytes(replayed[field]) != canonical_bytes(receipt[field]):
            raise ContractError("EVIDENCE_EXTRACTOR_REPLAY_MISMATCH")
    return replayed


def _validate_extraction_receipt(
    receipt_path: Path,
    receipt_reference: str,
    expected_file_sha256: str,
    observed_at_ms: int,
) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        dict[str, Any],
]:
    receipt, receipt_bytes = _trusted_document(
        receipt_path,
        maximum_bytes=4 * 1024 * 1024,
        label="EVIDENCE_EXTRACTION_RECEIPT",
    )
    if digest_bytes(receipt_bytes) != expected_file_sha256:
        raise ContractError("EVIDENCE_EXTRACTION_RECEIPT_DIGEST_MISMATCH")
    require_exact_fields(
        receipt, EXTRACTION_RECEIPT_FIELDS,
        "EVIDENCE_EXTRACTION_RECEIPT_FIELDS_INVALID")
    if (
            receipt["schema"] !=
            "hepta.market-source-extraction-receipt.v1" or
            receipt["version"] != 1):
        raise ContractError("EVIDENCE_EXTRACTION_RECEIPT_SCHEMA_INVALID")
    for field in (
            "mutation_attempted", "direct_broker_access",
            "paper_authorized", "live_authorized"):
        if receipt[field] is not False:
            raise ContractError("EVIDENCE_EXTRACTION_RECEIPT_BOUNDARY_INVALID")
    if require_int(
            receipt["observed_at_ms"],
            "EVIDENCE_EXTRACTION_RECEIPT_TIME_INVALID",
            minimum=0) != observed_at_ms:
        raise ContractError("EVIDENCE_EXTRACTION_RECEIPT_TIME_INVALID")
    expected_body_sha256 = require_digest(
        receipt["body_sha256"],
        "EVIDENCE_EXTRACTION_RECEIPT_BODY_INVALID")
    receipt_body = dict(receipt)
    receipt_body.pop("body_sha256")
    if digest_bytes(canonical_bytes(receipt_body)) != expected_body_sha256:
        raise ContractError("EVIDENCE_EXTRACTION_RECEIPT_BODY_INVALID")
    extractor = require_exact_fields(
        receipt["extractor"], EXTRACTOR_FIELDS,
        "EVIDENCE_EXTRACTOR_FIELDS_INVALID")
    extractor_id = require_text(
        extractor["extractor_id"], "EVIDENCE_EXTRACTOR_ID_INVALID",
        identifier=True)
    extractor_version = require_text(
        extractor["extractor_version"],
        "EVIDENCE_EXTRACTOR_VERSION_INVALID",
        identifier=True)
    extractor_code_sha256 = require_digest(
        extractor["extractor_code_sha256"],
        "EVIDENCE_EXTRACTOR_DIGEST_INVALID")
    if extractor["deterministic"] is not True:
        raise ContractError("EVIDENCE_EXTRACTOR_NOT_DETERMINISTIC")
    if PINNED_EXTRACTORS.get(
            (extractor_id, extractor_version)) != extractor_code_sha256:
        raise ContractError("EVIDENCE_EXTRACTOR_NOT_PINNED")
    (
        sources,
        source_digests,
        completeness_sha256,
        payloads_by_path,
    ) = (
        _normalize_attested_sources(
            receipt["sources"],
            receipt["completeness"],
            observed_at_ms,
            receipt_path,
        )
    )
    replayed = _replay_official_extractor(
        receipt,
        payloads_by_path,
        observed_at_ms,
        extractor_id,
        extractor_version,
        extractor_code_sha256,
    )
    semantic_events = (
        receipt["events"] if replayed is None else replayed["events"])
    semantic_items = (
        receipt["items"] if replayed is None else replayed["items"])
    events = _normalize_events(semantic_events, source_digests)
    items = _normalize_items(
        semantic_items, source_digests, observed_at_ms)
    semantic_output_sha256 = require_digest(
        receipt["semantic_output_sha256"],
        "EVIDENCE_SEMANTIC_OUTPUT_DIGEST_INVALID")
    semantic_output = {"events": events, "items": items}
    if digest_bytes(canonical_bytes(
            semantic_output)) != semantic_output_sha256:
        raise ContractError("EVIDENCE_SEMANTIC_OUTPUT_DIGEST_MISMATCH")
    attestation = {
        "schema": "hepta.market-source-attestation.v1",
        "receipt_path": receipt_reference,
        "receipt_file_sha256": expected_file_sha256,
        "receipt_body_sha256": expected_body_sha256,
        "extractor_id": extractor_id,
        "extractor_version": extractor_version,
        "extractor_code_sha256": extractor_code_sha256,
        "semantic_output_sha256": semantic_output_sha256,
        "completeness_sha256": completeness_sha256,
        "raw_payloads_verified": True,
        "fetch_metadata_verified": True,
        "semantic_derivation_attested": True,
        "completeness_attested": True,
    }
    return sources, events, items, attestation


def _legacy_normalize(
    bundle: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    require_exact_fields(
        bundle, BUNDLE_V1_FIELDS, "EVIDENCE_BUNDLE_FIELDS_INVALID")
    for field in (
            "mutation_attempted", "direct_broker_access",
            "paper_authorized", "live_authorized"):
        if bundle[field] is not False:
            raise ContractError("EVIDENCE_BUNDLE_BOUNDARY_INVALID")
    observed_at_ms = require_int(
        bundle["observed_at_ms"], "EVIDENCE_OBSERVED_TIME_INVALID",
        minimum=0)
    sources, source_digests = _normalize_sources(
        bundle["sources"], observed_at_ms)
    events = _normalize_events(bundle["events"], source_digests)
    items = _normalize_items(
        bundle["items"], source_digests, observed_at_ms)
    bundle_sha256 = digest_bytes(canonical_bytes(bundle))
    common = {
        "provider": "HEPTA_OFFICIAL_SOURCE_BUNDLE",
        "source_ref": "bundle:" + bundle_sha256,
        "observed_at_ms": observed_at_ms,
        "sources": sources,
    }
    calendar_body = {
        "schema": "hepta.economic-calendar.v2",
        **common,
        "events": events,
    }
    information_body = {
        "schema": "hepta.market-information-items.v2",
        **common,
        "items": items,
    }
    return (
        {
            **calendar_body,
            "body_sha256": digest_bytes(canonical_bytes(calendar_body)),
        },
        {
            **information_body,
            "body_sha256": digest_bytes(canonical_bytes(information_body)),
        },
    )


def _attested_normalize(
    bundle: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    require_exact_fields(
        bundle, BUNDLE_V2_FIELDS, "EVIDENCE_BUNDLE_FIELDS_INVALID")
    for field in (
            "mutation_attempted", "direct_broker_access",
            "paper_authorized", "live_authorized"):
        if bundle[field] is not False:
            raise ContractError("EVIDENCE_BUNDLE_BOUNDARY_INVALID")
    observed_at_ms = require_int(
        bundle["observed_at_ms"], "EVIDENCE_OBSERVED_TIME_INVALID",
        minimum=0)
    receipt_path, receipt_reference = _resolve_trusted_reference(
        bundle["extraction_receipt_path"])
    receipt_file_sha256 = require_digest(
        bundle["extraction_receipt_sha256"],
        "EVIDENCE_EXTRACTION_RECEIPT_DIGEST_INVALID")
    sources, events, items, attestation = _validate_extraction_receipt(
        receipt_path, receipt_reference, receipt_file_sha256, observed_at_ms)
    bundle_sha256 = digest_bytes(canonical_bytes(bundle))
    common = {
        "provider": "HEPTA_ATTESTED_OFFICIAL_SOURCE_BUNDLE",
        "source_ref": "bundle:" + bundle_sha256,
        "observed_at_ms": observed_at_ms,
        "sources": sources,
        "attestation": attestation,
    }
    calendar_body = {
        "schema": "hepta.economic-calendar.v3",
        **common,
        "events": events,
    }
    information_body = {
        "schema": "hepta.market-information-items.v3",
        **common,
        "items": items,
    }
    return (
        {
            **calendar_body,
            "body_sha256": digest_bytes(canonical_bytes(calendar_body)),
        },
        {
            **information_body,
            "body_sha256": digest_bytes(canonical_bytes(information_body)),
        },
    )


def normalize(bundle: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    schema = bundle.get("schema")
    if schema == "hepta.market-source-bundle.v1":
        return _legacy_normalize(bundle)
    if schema == "hepta.market-source-bundle.v2":
        return _attested_normalize(bundle)
    raise ContractError("EVIDENCE_BUNDLE_SCHEMA_INVALID")


def validate_output_attestation(
    document: dict[str, Any],
    *,
    semantic_field: str,
    evaluated_at_ms: int,
) -> None:
    if semantic_field not in {"events", "items"}:
        raise ContractError("EVIDENCE_OUTPUT_KIND_INVALID")
    attestation = require_exact_fields(
        document.get("attestation"), ATTESTATION_FIELDS,
        "EVIDENCE_OUTPUT_ATTESTATION_FIELDS_INVALID")
    receipt_path, receipt_reference = _resolve_trusted_reference(
        attestation["receipt_path"])
    receipt_file_sha256 = require_digest(
        attestation["receipt_file_sha256"],
        "EVIDENCE_EXTRACTION_RECEIPT_DIGEST_INVALID")
    sources, events, items, expected_attestation = (
        _validate_extraction_receipt(
            receipt_path,
            receipt_reference,
            receipt_file_sha256,
            require_int(
                document.get("observed_at_ms"),
                "EVIDENCE_OBSERVED_TIME_INVALID",
                minimum=0,
                maximum=evaluated_at_ms,
            ),
        )
    )
    if attestation != expected_attestation:
        raise ContractError("EVIDENCE_OUTPUT_ATTESTATION_MISMATCH")
    expected_semantics = events if semantic_field == "events" else items
    if (
            document.get("sources") != sources or
            document.get(semantic_field) != expected_semantics):
        raise ContractError("EVIDENCE_OUTPUT_SEMANTICS_MISMATCH")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-bundle", type=Path, required=True)
    parser.add_argument("--calendar-output", type=Path, required=True)
    parser.add_argument("--information-output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        bundle = load_document(arguments.source_bundle, "EVIDENCE_BUNDLE")
        calendar, information = normalize(bundle)
        atomic_write_json(arguments.calendar_output, calendar)
        atomic_write_json(arguments.information_output, information)
    except (ContractError, OSError, ValueError) as error:
        print(
            "hepta_market_evidence_normalizer: FAIL: " + str(error),
            file=sys.stderr,
        )
        return 78
    print(
        "hepta_market_evidence_normalizer: PASS "
        f"{len(calendar['events'])} events "
        f"{len(information['items'])} items"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
