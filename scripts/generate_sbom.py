#!/usr/bin/env python3
"""Generate a deterministic SPDX 2.3 JSON SBOM for a staged install tree."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid

FULL_SHA256 = re.compile(r"^[0-9a-fA-F]{40}$")
MAX_SOURCE_DATE_EPOCH = 253402300799  # 9999-12-31T23:59:59Z


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def spdx_file_id(relative: str) -> str:
    token = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:24]
    return f"SPDXRef-File-{token}"


def parse_source_date_epoch(value: str | None) -> int:
    raw = value if value is not None else os.environ.get("SOURCE_DATE_EPOCH", "0")
    if not raw or not raw.isascii() or not raw.isdigit():
        raise ValueError("SOURCE_DATE_EPOCH must be a non-negative decimal integer")
    epoch = int(raw, 10)
    if epoch > MAX_SOURCE_DATE_EPOCH:
        raise ValueError("SOURCE_DATE_EPOCH is outside the supported UTC range")
    return epoch


def source_timestamp(epoch: int) -> str:
    return (
        datetime.fromtimestamp(epoch, timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--version-file", type=Path, required=True)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--source-date-epoch")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = Path(os.path.abspath(os.fspath(args.root)))
    version = args.version_file.read_text(encoding="utf-8").strip()
    git_sha = args.git_sha.strip().lower()
    try:
        epoch = parse_source_date_epoch(args.source_date_epoch)
    except ValueError as error:
        print(f"invalid SBOM timestamp: {error}", file=sys.stderr)
        return 2
    if (
        not root.is_dir()
        or root.is_symlink()
        or not version
        or FULL_SHA256.fullmatch(git_sha) is None
    ):
        print("invalid SBOM input", file=sys.stderr)
        return 2

    files = []
    relationships = []
    package_id = "SPDXRef-Package-HeptaTrader"
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            print(f"refusing symlink in SBOM input: {path}", file=sys.stderr)
            return 1
        if not stat.S_ISREG(metadata.st_mode):
            continue
        relative = path.relative_to(root).as_posix()
        file_id = spdx_file_id(relative)
        files.append(
            {
                "SPDXID": file_id,
                "fileName": f"./{relative}",
                "checksums": [
                    {"algorithm": "SHA256", "checksumValue": digest_file(path)}
                ],
                "licenseConcluded": "NOASSERTION",
                "copyrightText": "NOASSERTION",
            }
        )
        relationships.append(
            {
                "spdxElementId": package_id,
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": file_id,
            }
        )

    namespace_seed = f"heptatrader:{version}:{git_sha}:" + ",".join(
        item["fileName"] + item["checksums"][0]["checksumValue"] for item in files
    )
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, namespace_seed)
    payload = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"heptatrader-{version}",
        "documentNamespace": f"https://spdx.org/spdxdocs/heptatrader-{namespace}",
        "creationInfo": {
            "created": source_timestamp(epoch),
            "creators": ["Tool: heptatrader-generate-sbom/2"],
        },
        "packages": [
            {
                "name": "heptatrader",
                "SPDXID": package_id,
                "versionInfo": version,
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": True,
                "licenseConcluded": "LicenseRef-HeptaTrader-All-Rights-Reserved",
                "licenseDeclared": "LicenseRef-HeptaTrader-All-Rights-Reserved",
                "copyrightText": "Copyright 2026 TrillionniumFoundation",
                "externalRefs": [
                    {
                        "referenceCategory": "OTHER",
                        "referenceType": "gitCommit",
                        "referenceLocator": git_sha,
                    }
                ],
            }
        ],
        "hasExtractedLicensingInfos": [
            {
                "licenseId": "LicenseRef-HeptaTrader-All-Rights-Reserved",
                "extractedText": "See the LICENSE file shipped with this artifact.",
            }
        ],
        "files": files,
        "relationships": relationships,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temporary = args.output.with_name(args.output.name + ".tmp")
    temporary.write_text(encoded, encoding="utf-8")
    os.replace(temporary, args.output)
    print(f"SBOM generated: {args.output} ({len(files)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
