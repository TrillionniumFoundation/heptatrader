#!/usr/bin/env python3
"""Run the canonical offline Tool Gateway execution regression."""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
from datetime import datetime, timezone


TEST_REGEX = "^hepta_(tool_gateway_runtime_composition|execution_service_process_e2e)_tests$"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline Agent OS regression through Tool Gateway and fake venue"
    )
    parser.add_argument("--project-root", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parents[1])
    parser.add_argument("--build-dir", type=pathlib.Path, required=True)
    parser.add_argument("--report", type=pathlib.Path)
    args = parser.parse_args()

    project_root = args.project_root.resolve(strict=True)
    build_dir = args.build_dir
    if not build_dir.is_absolute():
        build_dir = project_root / build_dir
    build_dir = build_dir.resolve(strict=True)
    report = args.report or project_root / "runtime-logs" / "strategy-iterate" / "latest_summary.json"
    if not report.is_absolute():
        report = project_root / report

    command = [
        "ctest",
        "--test-dir",
        str(build_dir),
        "--output-on-failure",
        "--tests-regex",
        TEST_REGEX,
    ]
    completed = subprocess.run(command, cwd=project_root, text=True, capture_output=True, check=False)
    payload = {
        "schema": "hepta.offline-tool-gateway-regression.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "offline-simulator-fake-venue",
        "paper_authorized": False,
        "live_authorized": False,
        "broker_connection_attempted": False,
        "command": command,
        "exit_code": completed.returncode,
        "overall": "PASS" if completed.returncode == 0 else "FAIL",
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=__import__("sys").stderr)
    print(f"OVERALL={payload['overall']}")
    print(f"SUMMARY={report}")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
