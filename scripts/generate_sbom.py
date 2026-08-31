#!/usr/bin/env python3
"""Generate a deterministic SPDX 2.3 JSON SBOM for a staged install tree."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import stat
import sys
import uuid


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def spdx_file_id(relative: str) -> str:
    token = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:24]
    return f"SPDXRef-File-{token}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--version-file", type=Path, required=True)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = args.root.resolve()
    version = args.version_file.read_text(encoding="utf-8").strip()
    if not root.is_dir() or not version:
        print("invalid SBOM input", file=sys.stderr)
        return 2

    files = []
    relationships = []
    package_id = "SPDXRef-Package-HeptaTrader"
    for path in sorted(root.rglob("*")):
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

    namespace_seed = f"heptatrader:{version}:{args.git_sha}:" + ",".join(
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
            "created": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
                "+00:00", "Z"
            ),
            "creators": ["Tool: heptatrader-generate-sbom/1"],
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
                        "referenceLocator": args.git_sha,
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
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"SBOM generated: {args.output} ({len(files)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
