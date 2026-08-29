#!/usr/bin/env python3
"""Derive a deterministic Agent-OS-only source bundle."""

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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict-source-tar", type=Path, required=True)
    parser.add_argument("--strict-source-manifest", type=Path, required=True)
    parser.add_argument(
        "--policy", type=Path,
        default=ROOT / "policies/heptatrader-agent-os-source-v2.json")
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    arguments = parser.parse_args()

    strict_verifier.verify_bundle(
        arguments.strict_source_tar,
        arguments.strict_source_manifest)
    strict_manifest_bytes = arguments.strict_source_manifest.read_bytes()
    strict_manifest = agent_os_source.strict_json(
        strict_manifest_bytes, "strict source manifest")
    if strict_manifest.get("version") != arguments.version:
        raise agent_os_source.AgentOsSourceError(
            "release version does not match strict source")
    policy = agent_os_source.load_policy(arguments.policy.resolve(strict=True))
    records = agent_os_source.selected_records(strict_manifest, policy)
    files = agent_os_source.extract_selected(
        arguments.strict_source_tar, strict_manifest, records)
    document = agent_os_source.manifest_document(
        arguments.version,
        strict_manifest,
        hashlib.sha256(arguments.strict_source_tar.read_bytes()).hexdigest(),
        hashlib.sha256(strict_manifest_bytes).hexdigest(),
        policy,
        records)
    bundle = agent_os_source.build_tar(document, files)
    agent_os_source.publish_new(arguments.output, bundle)
    agent_os_source.publish_new(
        arguments.manifest, agent_os_source.canonical_json(document))
    print(f"BUNDLE={arguments.output}")
    print(f"MANIFEST={arguments.manifest}")
    print(f"FILES={document['file_count']}")
    print(f"SHA256={hashlib.sha256(bundle).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
