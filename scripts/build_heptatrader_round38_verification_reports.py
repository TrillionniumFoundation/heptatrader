#!/usr/bin/env python3
"""Build the fixed Round38 matrix/sanitizer/coverage/runner report set."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys


SCRIPT_DIRECTORY = Path(__file__).resolve(strict=True).parent
sys.path.insert(0, str(SCRIPT_DIRECTORY))

import build_heptatrader_delivery_closure as common  # noqa: E402
import build_heptatrader_verification_evidence as evidence  # noqa: E402


OUTPUTS = {
    "matrix": "round38-test-matrix-report.json",
    "sanitizer": "round38-sanitizer-report.json",
    "coverage": "round38-coverage-report.json",
    "runner": "round38-runner-identity-report.json",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--matrix-sidecar", action="append", default=[])
    parser.add_argument("--sanitizer-sidecar", action="append", default=[])
    parser.add_argument("--coverage-sidecar", required=True)
    parser.add_argument("--minimum-line-rate", type=float, default=0.70)
    parser.add_argument("--runner-cache", action="append", default=[])
    parser.add_argument("--runner-source-manifest", action="append", default=[])
    parser.add_argument("--generated-at")
    arguments = parser.parse_args()
    root = evidence._protected_root(arguments.artifact_root)
    generated_at = arguments.generated_at or datetime.now(
        timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    reports = {
        "matrix": evidence.build_ctest(
            "matrix", root, arguments.matrix_sidecar, generated_at),
        "sanitizer": evidence.build_ctest(
            "sanitizer", root, arguments.sanitizer_sidecar, generated_at),
        "coverage": evidence.build_coverage(
            root, arguments.coverage_sidecar,
            arguments.minimum_line_rate, generated_at),
        "runner": evidence.build_runner(
            root, arguments.runner_cache, generated_at,
            arguments.runner_source_manifest),
    }
    for kind, name in OUTPUTS.items():
        evidence._write_private(
            root, Path(name),
            common.canonical_json(reports[kind]) + b"\n")
    print(
        "PASS: heptatrader.round38-verification-report-set.v1 "
        "reports=4 production_passed=false release_authorized=false")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except evidence.EvidenceError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
