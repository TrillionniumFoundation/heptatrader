"""One event per invocation: no timer, model dispatch, or broker operation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from historical_training.common import read

from .orchestrator import ingest


def main():
    ap = argparse.ArgumentParser()
    for name in ("plan", "dataset", "event", "ledger"):
        ap.add_argument("--" + name, type=Path, required=True)
    args = ap.parse_args()
    os.umask(0o077)
    # Registered trials must use registered_event_ingest; older research-only
    # CLIs cannot accidentally bypass their preregistration/source-root fence.
    if args.ledger.name == "registered-events.sqlite":
        ap.error("REGISTERED_LEDGER_REQUIRES_PRESEALED_PLAN_AND_ORIGINAL_SOURCE")
    plan = read(args.plan, 1024 * 1024)
    dataset = read(args.dataset, 32 * 1024 * 1024)
    event = read(args.event, 1024 * 1024)
    result = ingest(args.ledger, plan, dataset, event)
    # No raw account identifiers, market observations or model weights emitted.
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
