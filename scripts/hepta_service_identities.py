#!/usr/bin/env python3

"""Strict parser for the versioned HeptaTrader service identity contract."""

from __future__ import annotations

import json
from typing import Any


SCHEMA = "hepta.service-identities.v1"
EXPECTED_IDENTITIES = {
    "hepta-agent": {"uid": 2004, "gid": 2004, "role": "agent-tool-client"},
    "hepta-exec": {
        "uid": 2002,
        "gid": 2002,
        "role": "simulator-execution-authority",
    },
    "hepta-gateway": {"uid": 2001, "gid": 2001, "role": "tool-gateway"},
    "hepta-ib-exec": {
        "uid": 2003,
        "gid": 2003,
        "role": "ib-paper-execution-authority",
    },
}


def parse_identity_manifest(contents: bytes) -> dict[str, Any]:
    try:
        manifest = json.loads(contents.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("identity manifest is not valid UTF-8 JSON") from error
    if not isinstance(manifest, dict) or set(manifest) != {"schema", "identities"}:
        raise ValueError("identity manifest key contract mismatch")
    if manifest.get("schema") != SCHEMA:
        raise ValueError("identity manifest schema mismatch")
    if manifest.get("identities") != EXPECTED_IDENTITIES:
        raise ValueError("identity manifest fixed identity matrix mismatch")
    uids = [record["uid"] for record in EXPECTED_IDENTITIES.values()]
    gids = [record["gid"] for record in EXPECTED_IDENTITIES.values()]
    if len(set(uids)) != len(uids) or len(set(gids)) != len(gids):
        raise ValueError("identity manifest IDs are not mutually distinct")
    return manifest
