#!/usr/bin/env python3
"""Generate deterministic release evidence from two identical install trees.

This tool never publishes an artifact and never grants runtime capability. It
validates two independently built, IB-disabled install trees, requires exact
byte/mode identity, and emits an immutable manifest, checksums, SPDX 2.3 SBOM
and provenance bound to one source revision and recorded toolchain.
"""
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
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "heptatrader.release-evidence.v1"
TOOL_NAME = "heptatrader-generate-release-evidence/1"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
MAX_TOOLCHAIN_DEPTH = 4
MAX_TOOLCHAIN_ITEMS = 128
MAX_TOOLCHAIN_STRING = 4096
FORBIDDEN_METADATA = re.compile(
    r"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\bgh[pousr]_[A-Za-z0-9]{20,}\b|"
    r"\b(?:password|passwd|secret|token|credential)\s*[:=]\s*\S+)",
    re.IGNORECASE,
)


class EvidenceError(ValueError):
    pass


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8-sig"),
            object_pairs_hook=_strict_pairs,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"cannot read JSON {path}: {exc}") from exc


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_hash(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    try:
        with path.open("rb") as handle:
            while True:
                block = handle.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
    except OSError as exc:
        raise EvidenceError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def _validate_toolchain_value(
    value: Any,
    label: str,
    depth: int,
    counter: list[int],
) -> Any:
    if depth > MAX_TOOLCHAIN_DEPTH:
        raise EvidenceError(f"toolchain metadata is too deeply nested at {label}")
    counter[0] += 1
    if counter[0] > MAX_TOOLCHAIN_ITEMS:
        raise EvidenceError("toolchain metadata has too many values")

    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        if not value or len(value) > MAX_TOOLCHAIN_STRING or "\x00" in value:
            raise EvidenceError(f"invalid toolchain string at {label}")
        if FORBIDDEN_METADATA.search(value):
            raise EvidenceError(f"secret-like toolchain metadata at {label}")
        return value
    if isinstance(value, list):
        return [
            _validate_toolchain_value(item, f"{label}[{index}]", depth + 1, counter)
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key in sorted(value):
            if not isinstance(key, str) or not SAFE_KEY_RE.fullmatch(key):
                raise EvidenceError(f"invalid toolchain key at {label}: {key!r}")
            normalized[key] = _validate_toolchain_value(
                value[key], f"{label}.{key}", depth + 1, counter
            )
        return normalized
    raise EvidenceError(f"unsupported toolchain value at {label}: {type(value).__name__}")


def _load_toolchain(path: Path) -> dict[str, Any]:
    value = _load_json(path)
    if not isinstance(value, dict) or not value:
        raise EvidenceError("toolchain observation must be a non-empty object")
    normalized = _validate_toolchain_value(value, "toolchain", 0, [0])
    assert isinstance(normalized, dict)
    required = {
        "cmake",
        "compiler",
        "ninja",
        "openssl",
        "python",
        "runner_image",
        "source_date_epoch",
    }
    missing = sorted(required - set(normalized))
    if missing:
        raise EvidenceError(
            "toolchain observation lacks required fields: " + ", ".join(missing)
        )
    epoch = normalized["source_date_epoch"]
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch <= 0:
        raise EvidenceError("toolchain.source_date_epoch must be a positive integer")
    return normalized


def _validate_directory(path: Path, label: str) -> Path:
    try:
        raw = path.absolute()
        info = raw.lstat()
    except OSError as exc:
        raise EvidenceError(f"{label} is unavailable: {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise EvidenceError(f"{label} must be a real directory: {path}")
    if info.st_mode & stat.S_IWOTH:
        raise EvidenceError(f"{label} is world-writable: {path}")
    return raw.resolve(strict=True)


def _entry_mode(info: os.stat_result) -> str:
    return f"{stat.S_IMODE(info.st_mode):04o}"


def _scan_tree(root: Path, label: str) -> list[dict[str, Any]]:
    root = _validate_directory(root, label)
    entries: list[dict[str, Any]] = []
    for directory, names, filenames in os.walk(root, topdown=True, followlinks=False):
        directory_path = Path(directory)
        names.sort()
        filenames.sort()

        for name in list(names):
            path = directory_path / name
            relative = path.relative_to(root).as_posix()
            try:
                info = path.lstat()
            except OSError as exc:
                raise EvidenceError(f"cannot inspect {label}/{relative}: {exc}") from exc
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise EvidenceError(
                    f"{label}/{relative}: directory entry is a symlink or special file"
                )
            if info.st_mode & stat.S_IWOTH:
                raise EvidenceError(f"{label}/{relative}: directory is world-writable")
            if info.st_mode & (stat.S_ISUID | stat.S_ISGID):
                raise EvidenceError(f"{label}/{relative}: set-id directory is forbidden")

        for name in filenames:
            path = directory_path / name
            relative = path.relative_to(root).as_posix()
            if not relative or relative.startswith("/") or ".." in Path(relative).parts:
                raise EvidenceError(f"{label}: unsafe relative path: {relative!r}")
            try:
                before = path.lstat()
            except OSError as exc:
                raise EvidenceError(f"cannot inspect {label}/{relative}: {exc}") from exc
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
                raise EvidenceError(
                    f"{label}/{relative}: only regular non-symlink files are allowed"
                )
            if before.st_nlink != 1:
                raise EvidenceError(f"{label}/{relative}: hard-linked file is forbidden")
            if before.st_mode & stat.S_IWOTH:
                raise EvidenceError(f"{label}/{relative}: world-writable file is forbidden")
            if before.st_mode & (stat.S_ISUID | stat.S_ISGID):
                raise EvidenceError(f"{label}/{relative}: set-id file is forbidden")

            sha256 = _file_hash(path, "sha256")
            sha1 = _file_hash(path, "sha1")
            try:
                after = path.lstat()
            except OSError as exc:
                raise EvidenceError(f"cannot re-inspect {label}/{relative}: {exc}") from exc
            identity_before = (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_nlink,
                before.st_size,
                before.st_mtime_ns,
            )
            identity_after = (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_nlink,
                after.st_size,
                after.st_mtime_ns,
            )
            if identity_before != identity_after:
                raise EvidenceError(f"{label}/{relative}: file changed while hashing")
            entries.append(
                {
                    "path": relative,
                    "mode": _entry_mode(before),
                    "size": before.st_size,
                    "sha1": sha1,
                    "sha256": sha256,
                }
            )
    if not entries:
        raise EvidenceError(f"{label} contains no files")
    return entries


def _comparison_view(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "path": item["path"],
            "mode": item["mode"],
            "size": item["size"],
            "sha256": item["sha256"],
        }
        for item in entries
    ]


def _compare_trees(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
) -> None:
    left_map = {item["path"]: item for item in _comparison_view(left)}
    right_map = {item["path"]: item for item in _comparison_view(right)}
    missing = sorted(set(left_map) - set(right_map))
    extra = sorted(set(right_map) - set(left_map))
    changed = sorted(
        path
        for path in set(left_map) & set(right_map)
        if left_map[path] != right_map[path]
    )
    if missing or extra or changed:
        fragments: list[str] = []
        if missing:
            fragments.append("missing from build B: " + ", ".join(missing[:20]))
        if extra:
            fragments.append("extra in build B: " + ", ".join(extra[:20]))
        if changed:
            fragments.append("content/mode mismatch: " + ", ".join(changed[:20]))
        raise EvidenceError("install trees are not reproducible: " + "; ".join(fragments))


def _manifest(git_sha: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    files = _comparison_view(entries)
    digest = _sha256_bytes(_canonical_json(files))
    return {
        "schema": "heptatrader.install-manifest.v1",
        "git_sha": git_sha,
        "reproducible_builds": 2,
        "file_count": len(files),
        "tree_sha256": digest,
        "files": files,
    }


def _spdx(
    git_sha: str,
    version: str,
    entries: list[dict[str, Any]],
    source_date_epoch: int,
    tree_digest: str,
) -> dict[str, Any]:
    created = datetime.fromtimestamp(source_date_epoch, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    files: list[dict[str, Any]] = []
    relationships: list[dict[str, str]] = []
    for index, item in enumerate(entries, start=1):
        spdx_id = f"SPDXRef-File-{index}"
        files.append(
            {
                "SPDXID": spdx_id,
                "fileName": "./" + item["path"],
                "checksums": [
                    {"algorithm": "SHA1", "checksumValue": item["sha1"]},
                    {"algorithm": "SHA256", "checksumValue": item["sha256"]},
                ],
                "licenseConcluded": "NOASSERTION",
                "copyrightText": "NOASSERTION",
            }
        )
        relationships.append(
            {
                "spdxElementId": "SPDXRef-Package-HeptaTrader",
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": spdx_id,
            }
        )
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"HeptaTrader-runtime-{git_sha[:12]}",
        "documentNamespace": (
            "https://heptatrader.local/spdx/"
            + git_sha
            + "/"
            + tree_digest
        ),
        "creationInfo": {
            "created": created,
            "creators": [f"Tool: {TOOL_NAME}"],
        },
        "documentDescribes": ["SPDXRef-Package-HeptaTrader"],
        "packages": [
            {
                "name": "HeptaTrader runtime",
                "SPDXID": "SPDXRef-Package-HeptaTrader",
                "versionInfo": version,
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": True,
                "licenseConcluded": "Apache-2.0",
                "licenseDeclared": "Apache-2.0",
                "copyrightText": "Copyright 2026 Trillionnium Foundation",
                "externalRefs": [
                    {
                        "referenceCategory": "OTHER",
                        "referenceType": "heptatrader-git-sha",
                        "referenceLocator": git_sha,
                    }
                ],
            }
        ],
        "files": files,
        "relationships": relationships,
    }


def _read_version() -> str:
    path = ROOT / "VERSION"
    try:
        version = path.read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeError) as exc:
        raise EvidenceError(f"cannot read VERSION: {exc}") from exc
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", version):
        raise EvidenceError(f"invalid VERSION: {version!r}")
    return version


def _write(path: Path, content: bytes) -> str:
    path.write_bytes(content)
    path.chmod(0o644)
    return _sha256_bytes(content)


def generate(
    install_a: Path,
    install_b: Path,
    output_dir: Path,
    git_sha: str,
    toolchain_path: Path,
) -> dict[str, Any]:
    if not SHA_RE.fullmatch(git_sha):
        raise EvidenceError("expected git SHA must be 40 lowercase hexadecimal characters")
    if output_dir.exists():
        raise EvidenceError(f"output directory already exists: {output_dir}")
    parent = output_dir.parent.absolute()
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink():
        raise EvidenceError(f"output parent must not be a symlink: {parent}")

    toolchain = _load_toolchain(toolchain_path)
    entries_a = _scan_tree(install_a, "install A")
    entries_b = _scan_tree(install_b, "install B")
    _compare_trees(entries_a, entries_b)

    manifest = _manifest(git_sha, entries_a)
    manifest_bytes = _canonical_json(manifest)
    checksums_bytes = "".join(
        f"{item['sha256']}  {item['path']}\n" for item in entries_a
    ).encode("utf-8")
    sbom = _spdx(
        git_sha,
        _read_version(),
        entries_a,
        int(toolchain["source_date_epoch"]),
        manifest["tree_sha256"],
    )
    sbom_bytes = _canonical_json(sbom)

    temporary = Path(tempfile.mkdtemp(prefix=".heptatrader-release.", dir=parent))
    try:
        manifest_sha = _write(temporary / "install-manifest-v1.json", manifest_bytes)
        checksums_sha = _write(temporary / "SHA256SUMS", checksums_bytes)
        sbom_sha = _write(temporary / "sbom.spdx.json", sbom_bytes)
        provenance = {
            "schema": SCHEMA,
            "git_sha": git_sha,
            "version": _read_version(),
            "build_profile": "release-ib-disabled-core",
            "build_count": 2,
            "reproducible": True,
            "tree_sha256": manifest["tree_sha256"],
            "file_count": manifest["file_count"],
            "toolchain": toolchain,
            "subjects": [
                {
                    "name": "install-manifest-v1.json",
                    "sha256": manifest_sha,
                },
                {"name": "SHA256SUMS", "sha256": checksums_sha},
                {"name": "sbom.spdx.json", "sha256": sbom_sha},
            ],
            "capability_ceiling": {
                "ib_paper": "not-qualified-by-release-evidence",
                "live": "forbidden",
                "vendor_sdks_included": False,
            },
        }
        provenance_bytes = _canonical_json(provenance)
        provenance_sha = _write(
            temporary / "provenance-v1.json", provenance_bytes
        )
        index = {
            "schema": "heptatrader.release-evidence-index.v1",
            "git_sha": git_sha,
            "files": [
                {"path": "install-manifest-v1.json", "sha256": manifest_sha},
                {"path": "SHA256SUMS", "sha256": checksums_sha},
                {"path": "sbom.spdx.json", "sha256": sbom_sha},
                {"path": "provenance-v1.json", "sha256": provenance_sha},
            ],
        }
        index_sha = _write(temporary / "evidence-index-v1.json", _canonical_json(index))
        os.replace(temporary, output_dir)
        return {
            "git_sha": git_sha,
            "tree_sha256": manifest["tree_sha256"],
            "file_count": manifest["file_count"],
            "evidence_index_sha256": index_sha,
        }
    except BaseException:
        for path in sorted(temporary.rglob("*"), reverse=True):
            try:
                if path.is_file() or path.is_symlink():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            except OSError:
                pass
        try:
            temporary.rmdir()
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-a", required=True, type=Path)
    parser.add_argument("--install-b", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--expected-git-sha", required=True)
    parser.add_argument("--toolchain-json", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = generate(
            args.install_a,
            args.install_b,
            args.output_dir,
            args.expected_git_sha,
            args.toolchain_json,
        )
    except (EvidenceError, OSError) as exc:
        print(f"[RELEASE-EVIDENCE] {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
