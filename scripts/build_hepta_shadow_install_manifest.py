#!/usr/bin/env python3

"""Build the external manifest for a passive SHADOW runtime archive."""

from __future__ import annotations

import argparse
from pathlib import Path
import stat
import sys
import tarfile

import hepta_shadow_host_installer as installer


def build(
    archive: Path,
    source_baseline_sha256: str,
    installer_path: Path,
) -> dict[str, object]:
    if (
            type(source_baseline_sha256) is not str or
            installer.SHA256_IDENTITY.fullmatch(source_baseline_sha256) is None):
        raise installer.InstallError("INSTALL_BASELINE_DIGEST_INVALID")
    records, _directories = installer.archive_records(archive)
    files: list[dict[str, object]] = []
    with tarfile.open(archive, "r:gz") as handle:
        for relative in sorted(records):
            member = records[relative]
            stream = handle.extractfile(member)
            if stream is None:
                raise installer.InstallError(
                    "INSTALL_ARCHIVE_MEMBER_READ_FAILED")
            payload = stream.read(installer.MAX_ARCHIVE_BYTES + 1)
            if len(payload) != member.size:
                raise installer.InstallError(
                    "INSTALL_ARCHIVE_MEMBER_READ_FAILED")
            files.append({
                "path": relative,
                "mode": f"{stat.S_IMODE(member.mode):04o}",
                "size": len(payload),
                "sha256": installer.digest_bytes(payload),
            })
    document = {
        "schema": installer.MANIFEST_SCHEMA,
        "version": installer.MANIFEST_VERSION,
        "archive_sha256": installer.digest_file(archive),
        "source_baseline_sha256": source_baseline_sha256,
        "installer_sha256": installer.digest_file(installer_path),
        "files": files,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    return installer.validate_manifest_document(document)


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--source-baseline-sha256", required=True)
    parser.add_argument("--installer", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        if arguments.output.exists():
            raise installer.InstallError("INSTALL_MANIFEST_OUTPUT_EXISTS")
        document = build(
            arguments.archive.resolve(strict=True),
            arguments.source_baseline_sha256,
            arguments.installer.resolve(strict=True),
        )
        arguments.output.write_bytes(installer.canonical_bytes(document))
        arguments.output.chmod(0o644)
    except (installer.InstallError, OSError, tarfile.TarError) as error:
        print(
            f"build-hepta-shadow-install-manifest: FAIL: {error}",
            file=sys.stderr,
        )
        return 2
    print(
        "build-hepta-shadow-install-manifest: PASS "
        f"files={len(document['files'])} "
        f"archive={document['archive_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
