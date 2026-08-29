#!/usr/bin/env python3
"""Verify a content-addressed evidence index and its local payload closure."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

import build_heptatrader_evidence_index as builder


def verify(
        index_path: Path, evidence_root: Path, policy_path: Path,
        verify_files: bool = True) -> dict[str, Any]:
    if not verify_files:
        raise builder.EvidenceIndexError(
            "metadata-only verification is forbidden before external receipt")
    _, data = builder.stable_bytes(index_path, builder.MAX_POLICY_BYTES * 16)
    index = builder.strict_json(data, "index")
    required = {
        "schema", "version", "generated_at", "policy_sha256",
        "evidence_root_label", "selection_mode",
        "excluded_local_only_count", "excluded_local_only_sha256",
        "file_count", "total_bytes", "records_sha256",
        "git_index_eligible", "object_store_upload_status",
        "retention_anchor_status",
        "source_files_deleted", "paper_authorized", "live_authorized", "files",
    }
    if not isinstance(index, dict) or set(index) != required:
        raise builder.EvidenceIndexError("index fields do not exactly match schema")
    if (index["schema"] != builder.INDEX_SCHEMA or
            type(index["version"]) is not int or index["version"] != 2):
        raise builder.EvidenceIndexError("unsupported evidence index")
    try:
        generated = datetime.fromisoformat(
            index["generated_at"].replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise builder.EvidenceIndexError(
            "evidence generated_at is invalid") from error
    if generated.tzinfo is None or generated.utcoffset() is None:
        raise builder.EvidenceIndexError(
            "evidence generated_at lacks timezone")
    policy, policy_sha256 = builder.load_policy(policy_path)
    if (not isinstance(index["policy_sha256"], str) or
            not builder.HEX64.fullmatch(index["policy_sha256"]) or
            index["policy_sha256"] != policy_sha256):
        raise builder.EvidenceIndexError("evidence policy digest mismatch")
    if index["evidence_root_label"] != evidence_root.name:
        raise builder.EvidenceIndexError("evidence root label mismatch")
    if (not isinstance(index["selection_mode"], str) or
            index["selection_mode"] not in {"explicit", "complete-tree"}):
        raise builder.EvidenceIndexError("invalid evidence selection mode")
    if (not isinstance(index["excluded_local_only_count"], int) or
            isinstance(index["excluded_local_only_count"], bool) or
            index["excluded_local_only_count"] < 0 or
            not isinstance(index["excluded_local_only_sha256"], str) or
            not builder.HEX64.fullmatch(
                index["excluded_local_only_sha256"])):
        raise builder.EvidenceIndexError(
            "invalid local-only evidence exclusion closure")
    files = index["files"]
    if (not isinstance(files, list) or not files or
            not isinstance(index["file_count"], int) or
            isinstance(index["file_count"], bool) or
            index["file_count"] != len(files)):
        raise builder.EvidenceIndexError("evidence file count mismatch")
    if (not isinstance(index["records_sha256"], str) or
            not builder.HEX64.fullmatch(index["records_sha256"]) or
            index["records_sha256"] != hashlib.sha256(
                builder.canonical_json(files)).hexdigest()):
        raise builder.EvidenceIndexError("evidence record closure mismatch")
    if (not isinstance(index["total_bytes"], int) or
            isinstance(index["total_bytes"], bool) or
            index["total_bytes"] < 0):
        raise builder.EvidenceIndexError("invalid evidence byte total")
    if (index["git_index_eligible"] is not True or
            index["object_store_upload_status"] != "pending-external" or
            index["retention_anchor_status"] !=
            "pending-external-ingestion-receipt" or
            index["source_files_deleted"] is not False or
            index["paper_authorized"] is not False or
            index["live_authorized"] is not False):
        raise builder.EvidenceIndexError("evidence safety boundary drift")
    seen: set[str] = set()
    previous = ""
    total_bytes = 0
    root = evidence_root.resolve(strict=True)
    for record in files:
        if not isinstance(record, dict) or set(record) != {
                "path", "rule", "tier", "retention_days",
                "git_index_allowed", "size", "mode", "sha256", "object_key"}:
            raise builder.EvidenceIndexError("invalid evidence record")
        relative = record["path"]
        relative_path = Path(relative) if isinstance(relative, str) else Path()
        if (not isinstance(relative, str) or not relative or
                "\0" in relative or "\\" in relative or
                relative_path.is_absolute() or ".." in relative_path.parts or
                relative_path.as_posix() != relative):
            raise builder.EvidenceIndexError("invalid evidence path")
        if relative in seen:
            raise builder.EvidenceIndexError("duplicate evidence path")
        if relative <= previous:
            raise builder.EvidenceIndexError(
                "evidence records are not canonically ordered")
        previous = relative
        seen.add(relative)
        rule, tier = builder.classify(relative, policy)
        if (record["rule"], record["tier"]) != (rule, tier):
            raise builder.EvidenceIndexError("evidence classification drift")
        retention = policy["tiers"][tier]
        if (type(record["retention_days"]) is not
                type(retention["retention_days"]) or
                record["retention_days"] != retention["retention_days"] or
                record["git_index_allowed"] is not True or
                retention["git_index_allowed"] is not True):
            raise builder.EvidenceIndexError("evidence retention drift")
        if (not isinstance(record["size"], int) or
                isinstance(record["size"], bool) or record["size"] < 0 or
                not isinstance(record["mode"], str) or
                re.fullmatch(r"0[0-7]{3}", record["mode"]) is None or
                int(record["mode"], 8) & 0o022 or
                not isinstance(record["sha256"], str) or
                not builder.HEX64.fullmatch(record["sha256"])):
            raise builder.EvidenceIndexError(
                "invalid evidence content metadata")
        expected_key = policy["object_store"]["key_template"].format(
            sha256=record["sha256"])
        if record["object_key"] != expected_key:
            raise builder.EvidenceIndexError("object key is not content addressed")
        total_bytes += record["size"]
        path = root / relative_path
        metadata, size, digest = builder.stable_digest(path)
        if size != record["size"]:
            raise builder.EvidenceIndexError("indexed evidence size drift")
        if digest != record["sha256"]:
            raise builder.EvidenceIndexError("indexed evidence digest drift")
        if format(metadata.st_mode & 0o7777, "04o") != record["mode"]:
            raise builder.EvidenceIndexError("indexed evidence mode drift")
    if total_bytes != index["total_bytes"]:
        raise builder.EvidenceIndexError("indexed evidence byte total drift")
    if index["selection_mode"] == "complete-tree":
        selected, excluded, _ = builder.selected_paths(
            evidence_root, policy, [])
        selected_relatives = {
            Path(os.path.abspath(path)).relative_to(root).as_posix()
            for path in selected
        }
        if selected_relatives != seen:
            raise builder.EvidenceIndexError(
                "complete evidence tree selection drift")
    else:
        excluded = []
    if (len(excluded) != index["excluded_local_only_count"] or
            hashlib.sha256(
                builder.canonical_json(excluded)).hexdigest() !=
            index["excluded_local_only_sha256"]):
        raise builder.EvidenceIndexError(
            "local-only evidence exclusion closure drift")
    return index


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    index = args.index if args.index.is_absolute() else root / args.index
    evidence_root = (
        args.evidence_root or root / "runtime-logs").resolve(strict=True)
    policy = root / "policies" / "heptatrader-evidence-retention-v1.json"
    report = verify(index, evidence_root, policy, True)
    print(
        f"PASS: {report['file_count']} files "
        f"records_sha256={report['records_sha256']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (builder.EvidenceIndexError, OSError) as error:
        print(f"evidence-index: {error}", file=os.sys.stderr)
        raise SystemExit(78)
