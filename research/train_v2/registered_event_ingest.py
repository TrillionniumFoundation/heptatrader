"""One matching, genuinely new LOCAL event for a pre-sealed research trial."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path

from historical_training.common import read
from .registry import ingest_registered_original_event


def main():
    parser = argparse.ArgumentParser()
    for name in ("registry-root", "plan", "dataset", "event", "original-segment"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    root = args.registry_root
    status = ingest_registered_original_event(
        root, root / "registered-events.sqlite",
        read(args.plan, 16384), read(args.dataset, 32 * 1024 * 1024),
        read(args.event, 16384), args.original_segment)
    print(json.dumps(status, sort_keys=True))


if __name__ == "__main__":
    main()
