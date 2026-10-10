"""Receive an externally signed, research-only Train V2 record.

No private keys are accessed. Signature issuer is deliberately out-of-process.
This does not configure a canonical Agentd owner, start a model or trade.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from historical_training.common import read
from .signed_handoff import (
    register_externally_witnessed, ingest_signed_original,
)


def main():
    p = argparse.ArgumentParser()
    commands = p.add_subparsers(dest="mode", required=True)
    for name in ("register", "event"):
        op = commands.add_parser(name)
        for flag in ("registry-root", "trust-snapshot", "pinned-trust-sha256",
                     "plan", "witness"):
            op.add_argument("--" + flag,
                            type=Path if flag != "pinned-trust-sha256" else str,
                            required=True)
        op.add_argument("--min-trust-generation", type=int, default=1)
        if name == "register":
            op.add_argument("--feature-spec", type=Path, required=True)
            op.add_argument("--original-source-root", type=Path, required=True)
        else:
            op.add_argument("--dataset", type=Path, required=True)
            op.add_argument("--event", type=Path, required=True)
            op.add_argument("--original-segment", type=Path, required=True)
    args = p.parse_args()
    os.umask(0o077)
    plan = read(args.plan, 16384)
    witness = read(args.witness, 16384)
    if args.mode == "register":
        status = register_externally_witnessed(
            args.registry_root, plan, read(args.feature_spec, 16384),
            args.original_source_root, args.trust_snapshot,
            args.pinned_trust_sha256, witness,
            min_generation=args.min_trust_generation)
    else:
        status = ingest_signed_original(
            args.registry_root, plan, read(args.dataset, 32 * 1024 * 1024),
            read(args.event, 16384), args.original_segment,
            args.trust_snapshot, args.pinned_trust_sha256, witness,
            min_generation=args.min_trust_generation)
    print(json.dumps(status, sort_keys=True))


if __name__ == "__main__":
    main()
