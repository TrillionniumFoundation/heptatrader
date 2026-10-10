"""Fresh original-source event ingress. Exactly one event; no periodic worker."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from historical_training.common import read
from .handoff import ingest_original_event


def main():
    ap = argparse.ArgumentParser()
    for name in ("plan", "dataset", "event", "original-segment", "ledger"):
        ap.add_argument("--" + name, type=Path, required=True)
    args = ap.parse_args()
    os.umask(0o077)
    plan = read(args.plan, 1024*1024)
    dataset = read(args.dataset, 32*1024*1024)
    event = read(args.event, 1024*1024)
    # This is local chain verification only, not a canonical owner/trader grant.
    status = ingest_original_event(
        args.ledger, plan, dataset, event, args.original_segment)
    print(json.dumps(status, sort_keys=True))


if __name__ == "__main__":
    main()
