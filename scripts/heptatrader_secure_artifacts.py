#!/usr/bin/env python3
"""Small shared primitives for fail-closed HeptaTrader artifacts.

The module deliberately keeps policy at call sites.  It centralizes only the
byte representation and the descriptor-anchored stable read contract so
builders, verifiers, and rootful gates cannot drift into weaker helpers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import build_heptatrader_delivery_closure as closure


class SecureArtifactError(RuntimeError):
    pass


def canonical_json(
    value: Any, *, pretty: bool = False, trailing_newline: bool = False,
) -> bytes:
    """Encode deterministic ASCII JSON and reject non-finite values."""
    try:
        if pretty:
            text = json.dumps(
                value, ensure_ascii=True, indent=2, sort_keys=True,
                allow_nan=False)
        else:
            text = json.dumps(
                value, ensure_ascii=True, separators=(",", ":"),
                sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise SecureArtifactError(
            "value cannot be represented as canonical JSON") from error
    if trailing_newline:
        text += "\n"
    return text.encode("ascii")


def stable_bytes(
    path: Path, *, label: str, limit: int,
    require_trusted_parent: bool = False,
) -> tuple[bytes, closure.StableRead]:
    """Capture a regular file through the reviewed no-follow fd walk."""
    try:
        snapshot = closure.stable_read(
            path, limit=limit, capture=True,
            require_trusted_parent=require_trusted_parent)
    except (OSError, closure.DeliveryClosureError) as error:
        raise SecureArtifactError(f"{label}: {error}") from error
    if snapshot.data is None:
        raise SecureArtifactError(f"{label}: stable read did not capture bytes")
    return snapshot.data, snapshot
