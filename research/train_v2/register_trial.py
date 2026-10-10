"""Pre-register a NEW LOCAL research window before its first decision.

No event watcher, broker connection, GPU worker, model or owner signing.
"""
from __future__ import annotations
import argparse
import json
import os
import time
from pathlib import Path

from historical_training.common import read
from .orchestrator import register_plan
from .registry import register_before_window


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature-spec", type=Path, required=True)
    parser.add_argument("--registry-root", type=Path, required=True)
    parser.add_argument("--original-source-root", type=Path, required=True)
    parser.add_argument("--first-decision-ms", type=int, required=True)
    parser.add_argument("--last-decision-ms", type=int, required=True)
    parser.add_argument("--min-mature-rows", type=int, default=16)
    args = parser.parse_args()
    os.umask(0o077)
    spec = read(args.feature_spec, 16384)
    plan = register_plan(
        spec=spec, registered_at_ms=time.time_ns() // 1_000_000,
        first_decision_ms=args.first_decision_ms,
        last_decision_ms=args.last_decision_ms,
        minimum_mature_rows=args.min_mature_rows)
    receipt = register_before_window(
        args.registry_root, plan, spec, args.original_source_root)
    print(json.dumps({
        "plan_id": receipt["plan_id"],
        "sealed_at_ms": receipt["sealed_at_ms"],
        "first_decision_ms": receipt["first_decision_ms"],
        "last_decision_ms": receipt["last_decision_ms"],
        "local_registry_only": True,
        "independent_owner_signature": False,
        "automatic_training": False,
        "broker_orders_sent": 0,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
