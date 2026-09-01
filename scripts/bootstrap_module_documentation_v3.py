#!/usr/bin/env python3
"""One-shot ModuleManifest V3 and generated technical-guide migration."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
OLD_SCHEMA_PATH = DOCS / "modules/module-manifest-schema-v3.json"
NEW_SCHEMA_PATH = DOCS / "modules/module-manifest-schema-v3.json"
PROFILE_PATH = DOCS / "modules/module-documentation-profiles-v1.json"
REGISTRY_PATH = DOCS / "modules/module-registry-v2.json"
DOCUMENT_REGISTRY_PATH = DOCS / "document-registry-v2.json"
TEST_MATRIX_PATH = DOCS / "verification/test-matrix-v2.json"
GAP_REGISTRY_PATH = DOCS / "program/gap-registry-v2.json"
MILESTONE_REGISTRY_PATH = DOCS / "program/milestone-registry-v1.json"

OLD_SCHEMA_ID = "heptatrader.module-manifest.v3"
NEW_SCHEMA_ID = "heptatrader.module-manifest.v3"
CHECK_ID = "module-documentation-coverage"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def replace_current_references() -> None:
    replacements = (
        ("module-manifest-schema-v3.json", "module-manifest-schema-v3.json"),
        (OLD_SCHEMA_ID, NEW_SCHEMA_ID),
        ("ModuleManifest V3", "ModuleManifest V3"),
    )
    allowed = {".py", ".md", ".json", ".yml", ".yaml"}
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in allowed:
            continue
        relative = path.relative_to(ROOT)
        if ".git" in relative.parts or "build" in relative.parts or path == OLD_SCHEMA_PATH:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeError:
            continue
        updated = text
        for old, new in replacements:
            updated = updated.replace(old, new)
        if updated != text:
            path.write_text(updated, encoding="utf-8")


def migrate_schema(required_topics: list[str]) -> None:
    schema = load(OLD_SCHEMA_PATH)
    schema["$id"] = "https://heptatrader.local/schemas/module-manifest-v3.json"
    schema["title"] = "Hepta ModuleManifest V3"
    required = schema["required"]
    if "documentation" not in required:
        required.append("documentation")
    schema["properties"]["schema"] = {"const": NEW_SCHEMA_ID}
    schema["properties"]["documentation"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["technical_guide", "coverage_topics"],
        "properties": {
            "technical_guide": {
                "type": "string",
                "pattern": r"^modules/technical/[a-z0-9-]+\.md$",
                "maxLength": 256,
            },
            "coverage_topics": {
                "type": "array",
                "minItems": len(required_topics),
                "maxItems": len(required_topics),
                "uniqueItems": True,
                "items": {"enum": required_topics},
            },
        },
    }
    write(NEW_SCHEMA_PATH, schema)
    OLD_SCHEMA_PATH.unlink()


def migrate_manifests(required_topics: list[str]) -> list[dict[str, Any]]:
    registry = load(REGISTRY_PATH)
    registry["manifest_schema"] = "modules/module-manifest-schema-v3.json"
    manifests: list[dict[str, Any]] = []
    for relative in registry["manifest_paths"]:
        path = DOCS / relative
        manifest = load(path)
        manifest["schema"] = NEW_SCHEMA_ID
        guide = "modules/technical/" + path.stem + ".md"
        manifest["documentation"] = {
            "technical_guide": guide,
            "coverage_topics": required_topics,
        }
        verification = manifest.setdefault("verification", [])
        if CHECK_ID not in verification:
            verification.append(CHECK_ID)
        write(path, manifest)
        manifests.append(manifest)
    write(REGISTRY_PATH, registry)
    return manifests


def migrate_document_registry(manifests: list[dict[str, Any]]) -> None:
    registry = load(DOCUMENT_REGISTRY_PATH)
    remove_paths = {
        "modules/module-manifest-schema-v3.json",
        "modules/module-manifest-schema-v3.json",
        "modules/module-documentation-profiles-v1.json",
    }
    remove_paths.update(
        item["documentation"]["technical_guide"] for item in manifests
    )
    documents = [
        item for item in registry["documents"]
        if item.get("path") not in remove_paths
    ]
    documents.extend([
        {
            "path": "modules/module-manifest-schema-v3.json",
            "class": "machine-registry",
            "owner": "@hepta/platform",
        },
        {
            "path": "modules/module-documentation-profiles-v1.json",
            "class": "machine-registry",
            "owner": "@hepta/platform",
        },
    ])
    for manifest in manifests:
        documents.append({
            "path": manifest["documentation"]["technical_guide"],
            "class": "generated-view",
            "owner": manifest["owners"]["dri"],
        })
    registry["documents"] = sorted(documents, key=lambda item: item["path"])
    write(DOCUMENT_REGISTRY_PATH, registry)


def migrate_test_matrix() -> None:
    document = load(TEST_MATRIX_PATH)
    checks = document["checks"]
    replacement = {
        "id": CHECK_ID,
        "lane": "A-module-fast",
        "state": "implemented",
        "evidence": (
            "ModuleManifest V3 documentation contract, deterministic 22-guide "
            "generation, registry cross-reference checks and negative schema tests"
        ),
    }
    for position, check in enumerate(checks):
        if check.get("id") == CHECK_ID:
            checks[position] = replacement
            break
    else:
        checks.insert(4, replacement)
    write(TEST_MATRIX_PATH, document)


def reopen_gap() -> None:
    document = load(GAP_REGISTRY_PATH)
    for gap in document["gaps"]:
        if gap.get("id") == "G-DOC-003":
            gap["title"] = (
                "Every registered module must have a complete, generated and "
                "machine-verified technical development guide"
            )
            gap["state"] = "in-progress"
            gap["evidence"] = [
                "docs-generated",
                "docs-control",
                "module-manifest-schema",
                CHECK_ID,
                "physical-source-ownership",
                "module-registry",
            ]
            break
    else:
        raise RuntimeError("G-DOC-003 is missing")
    write(GAP_REGISTRY_PATH, document)

    milestones = load(MILESTONE_REGISTRY_PATH)
    for milestone in milestones["milestones"]:
        if milestone.get("id") == "M1":
            milestone["state"] = "in-progress"
            break
    else:
        raise RuntimeError("M1 is missing")
    write(MILESTONE_REGISTRY_PATH, milestones)


def main() -> int:
    profile = load(PROFILE_PATH)
    if profile.get("schema") != "heptatrader.module-documentation-profiles.v1":
        raise RuntimeError("profile registry schema mismatch")
    required_topics = profile.get("required_topics")
    if (
        not isinstance(required_topics, list)
        or not required_topics
        or len(required_topics) != len(set(required_topics))
        or any(not isinstance(item, str) or not item for item in required_topics)
    ):
        raise RuntimeError("invalid required documentation topics")
    migrate_schema(required_topics)
    manifests = migrate_manifests(required_topics)
    if len(manifests) != 22:
        raise RuntimeError(f"expected 22 module manifests, found {len(manifests)}")
    migrate_document_registry(manifests)
    migrate_test_matrix()
    reopen_gap()
    replace_current_references()
    print("[MODULE-DOCS-MIGRATION] migrated 22 ModuleManifest V3 documents")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
