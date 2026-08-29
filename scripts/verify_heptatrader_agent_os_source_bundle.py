#!/usr/bin/env python3
"""Verify an Agent-OS-only source bundle and strict-source lineage."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(1, str(ROOT / "scripts"))

from hepta_ops import agent_os_source  # noqa: E402
import verify_heptatrader_clean_source_bundle as strict_verifier  # noqa: E402


def verify(
        bundle: Path,
        manifest_path: Path,
        strict_bundle: Path,
        strict_manifest_path: Path,
        policy_path: Path) -> dict:
    strict_verifier.verify_bundle(strict_bundle, strict_manifest_path)
    strict_manifest_bytes = strict_manifest_path.read_bytes()
    strict_manifest = agent_os_source.strict_json(
        strict_manifest_bytes, "strict source manifest")
    policy = agent_os_source.load_policy(policy_path.resolve(strict=True))
    records = agent_os_source.selected_records(strict_manifest, policy)
    files = agent_os_source.extract_selected(
        strict_bundle, strict_manifest, records)
    expected = agent_os_source.manifest_document(
        strict_manifest["version"],
        strict_manifest,
        hashlib.sha256(strict_bundle.read_bytes()).hexdigest(),
        hashlib.sha256(strict_manifest_bytes).hexdigest(),
        policy,
        records)
    observed = agent_os_source.strict_json(
        manifest_path.read_bytes(), "Agent OS source manifest")
    if observed != expected:
        raise agent_os_source.AgentOsSourceError(
            "Agent OS source manifest does not match strict-source lineage")
    agent_os_source.verify_tar(bundle, observed, files)
    return {
        "passed": True,
        "file_count": observed["file_count"],
        "files_sha256": observed["files_sha256"],
        "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--strict-source-tar", type=Path, required=True)
    parser.add_argument("--strict-source-manifest", type=Path, required=True)
    parser.add_argument(
        "--policy", type=Path,
        default=ROOT / "policies/heptatrader-agent-os-source-v2.json")
    arguments = parser.parse_args()
    result = verify(
        arguments.bundle,
        arguments.manifest,
        arguments.strict_source_tar,
        arguments.strict_source_manifest,
        arguments.policy)
    print(
        "PASS: Agent OS source bundle "
        f"files={result['file_count']} sha256={result['bundle_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
