"""Opt-in, versioned original IB-reported TTL semantics for FUTURE research.

Old V1 arrival-only feature profile is never upgraded. This independently
revalidates the exact private hash-chain source prefix and copies ONLY expiry
from the already chained original quote, bounded by the existing 5s limit.
No broker/API request, signer key, model dispatch or trade permission.
"""
from __future__ import annotations

import hashlib
import os
import stat
from decimal import Decimal
from pathlib import Path

from .contracts import (
    BROKER_EXPIRY_POLICY, BROKER_EXPIRY_PROFILE, LIVE, OBS_SCHEMA,
    ContractError, validate_spec,
)


def sealed_original_broker_expiry(path, spec):
    validate_spec(spec)
    if (spec["source_kind"] != LIVE
            or spec.get("quote_expiry_policy") != BROKER_EXPIRY_POLICY
            or spec.get("feature_profile") != BROKER_EXPIRY_PROFILE):
        raise ContractError("EXPIRY_ADAPTER_EXPLICIT_NEW_PROFILE_REQUIRED")
    from laya_cell.shadow_source import MAX_BYTES, strict, read_source
    path = Path(path)
    # First parser verifies complete source chain, identity, timing, period,
    # authority, quote economics, local file mode and no trading writes.
    header, quotes, prefix = read_source(path)
    if (header.get("period_sha256") != spec["period_sha256"]
            or header.get("instrument") != spec["instrument"]
            or header.get("broker_orders_sent") != 0
            or header.get("write_operations_allowed") != []
            or prefix.get("uncommitted_tail_bytes") != 0
            or not isinstance(quotes, list) or len(quotes) > 100000):
        raise ContractError("EXPIRY_ORIGINAL_SOURCE_NOT_VALID")
    # Reopen NOFOLLOW, verify the original *same bytes*, and decode the
    # validity field omitted from the legacy read_source return shape.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid()
                or st.st_mode & 0o077 or st.st_size > MAX_BYTES
                or st.st_size != prefix["bytes"]):
            raise ContractError("EXPIRY_SOURCE_CHANGED_BETWEEN_READS")
        raw = bytearray()
        while len(raw) < st.st_size:
            chunk = os.read(fd, min(1024 * 1024, st.st_size - len(raw)))
            if not chunk:
                raise ContractError("EXPIRY_SOURCE_SHORT_READ")
            raw.extend(chunk)
    finally:
        os.close(fd)
    if (hashlib.sha256(raw).hexdigest() != prefix["sha256"]
            or not raw.endswith(b"\n")):
        raise ContractError("EXPIRY_SOURCE_CHANGED_BETWEEN_READS")
    by_receipt = {}
    for line in raw.splitlines():
        row = strict(line)
        if row.get("kind") == "QUOTE":
            by_receipt[row["sha256"]] = row
    normalized = []
    for q in quotes:
        original = by_receipt.get(q["receipt"])
        if not original:
            raise ContractError("EXPIRY_ORIGINAL_QUOTE_WITNESS_MISSING")
        entry = original["data"]
        event, known = q["t"], q["known_at_ms"]
        if (entry.get("observed_at_ms") != event
                or original["at_ms"] != known
                or Decimal(str(entry.get("bid"))) != Decimal(str(q["bid"]))
                or Decimal(str(entry.get("ask"))) != Decimal(str(q["ask"]))):
            raise ContractError("EXPIRY_ORIGINAL_QUOTE_CHANGED")
        broker_expiry = entry.get("stale_after_ms")
        if type(broker_expiry) is not int:
            raise ContractError("EXPIRY_BROKER_BOUND_MISSING")
        valid_until = min(event + 5000, broker_expiry)
        if not event <= known <= valid_until:
            raise ContractError("EXPIRY_ORIGINAL_QUOTE_ALREADY_EXPIRED")
        normalized.append({
            "schema": OBS_SCHEMA,
            "instrument": spec["instrument"],
            "period_sha256": spec["period_sha256"],
            "source_kind": LIVE,
            "source_id": q["receipt"],
            "source_sha256": q["receipt"],
            "event_at_ms": event,
            "available_at_ms": known,
            "valid_until_ms": valid_until,
            "bid": str(q["bid"]),
            "ask": str(q["ask"]),
        })
    return normalized, {
        "schema": "heptatrader.train-v2-original-expiry-provenance.v1",
        "original_quote_count": len(normalized),
        "verified_prefix_sha256": prefix["sha256"],
        "original_chain_head": prefix["chain_head"],
        "prefix_sha256": prefix["sha256"],
        "chain_head": prefix["chain_head"],
        "original_complete_bytes": prefix["bytes"],
        "expiry_provenance": "LOCALLY_CHAINED_IB_REPORTED_NOT_BROKER_SIGNED",
        "expiry_max_ms_after_quote": 5000,
        "original_period_verified": True,
        "historical_revisions_used": False,
        "broker_orders_sent": 0,
        "broker_requests_sent": 0,
        "model_selected": False,
        "canonical_agentd_owner_admitted": False,
    }
