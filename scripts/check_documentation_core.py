#!/usr/bin/env python3
"""Validate the canonical HeptaTrader module and capability documentation."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import stat
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_STATUSES = {
    "CURRENT",
    "QUALIFICATION_REQUIRED",
    "EXPERIMENTAL",
    "LEGACY",
    "PROPOSAL",
    "UNAVAILABLE",
}
ALLOWED_MUTATION_CLASSES = {
    "NONE",
    "FORWARD_ONLY",
    "SOLE_AUTHORITY",
    "POLICY_GATE",
    "LOCAL_ONLY",
    "PAPER_GATED",
    "EXTERNAL_QUALIFIER_ONLY",
}
MODULE_KEYS = {
    "id",
    "status",
    "document",
    "implementation",
    "tests",
    "broker_mutation",
    "production_authorized",
}
CAPABILITY_KEYS_V1 = {
    "id",
    "status",
    "order_transport",
    "requires_external_qualification",
    "advertise_as_real_venue",
}
CAPABILITY_KEYS_V2 = {
    "id",
    "status",
    "order_transport",
    "requires_external_qualification",
    "transport_implemented",
    "advertisable",
    "authorized",
}
# Public compatibility alias for tooling that imported the old constant.
CAPABILITY_KEYS = CAPABILITY_KEYS_V2
CANONICAL_FILES = (
    Path("README.md"),
    Path("docs/index.md"),
    Path("docs/DOCUMENTATION-POLICY.md"),
)
REQUIRED_WORKFLOWS = (
    Path(".github/workflows/documentation-control-plane.yml"),
    Path(".github/workflows/core-ci.yml"),
    Path(".github/workflows/canonical-full-suite.yml"),
    Path(".github/workflows/release.yml"),
)
ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
USER_ABSOLUTE_RE = re.compile(
    r"(?:[A-Za-z]:[\\/](?:Users|home)[\\/]|/home/[^/]+/|/Users/[^/]+/)"
)


class DuplicateKeyError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _load_json(path: Path, errors: list[str]) -> Any:
    try:
        info = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            errors.append(f"{path}: must be a regular single-link file")
            return None
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        errors.append(f"{path}: invalid JSON: {error}")
        return None


def _canonical_relative(value: Any, label: str, errors: list[str]) -> Path | None:
    if not isinstance(value, str) or not value or "\\" in value:
        errors.append(f"{label}: expected a non-empty repository-relative POSIX path")
        return None
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        errors.append(f"{label}: path is not canonical: {value!r}")
        return None
    return path


def _existing_path(root: Path, value: Any, label: str, errors: list[str]) -> Path | None:
    relative = _canonical_relative(value, label, errors)
    if relative is None:
        return None
    path = root / relative
    try:
        info = path.lstat()
    except OSError as error:
        errors.append(f"{label}: missing path {relative}: {error}")
        return None
    if path.is_symlink():
        errors.append(f"{label}: symlink is not accepted: {relative}")
        return None
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        errors.append(f"{label}: unsupported file type: {relative}")
        return None
    return path


def _read_text(path: Path, label: str, errors: list[str]) -> str:
    try:
        info = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            errors.append(f"{label}: must be a regular single-link file")
            return ""
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        errors.append(f"{label}: unreadable: {error}")
        return ""


def _validate_doc_metadata(
    root: Path,
    relative: Path,
    module: dict[str, Any],
    errors: list[str],
) -> None:
    text = _read_text(root / relative, relative.as_posix(), errors)
    if not text:
        return
    expected = (
        f"Status: {module['status']}",
        "Applies to:",
        "Implementation:",
        "Tests:",
    )
    for token in expected:
        if token not in text:
            errors.append(f"{relative}: missing required metadata token: {token}")
    if not text.lstrip().startswith("# "):
        errors.append(f"{relative}: first heading must be level one")
    if "## " not in text:
        errors.append(f"{relative}: module document requires technical sections")
    if USER_ABSOLUTE_RE.search(text):
        errors.append(f"{relative}: developer-specific absolute path is forbidden")


def _validate_catalog(root: Path, errors: list[str]) -> dict[str, dict[str, Any]]:
    path = root / "docs/module-catalog.json"
    value = _load_json(path, errors)
    if not isinstance(value, dict) or value.get("schema") != "heptatrader.module-catalog.v1":
        errors.append("docs/module-catalog.json: unsupported schema")
        return {}
    modules = value.get("modules")
    if not isinstance(modules, list) or not modules:
        errors.append("docs/module-catalog.json: modules must be a non-empty array")
        return {}

    output: dict[str, dict[str, Any]] = {}
    documented_paths: set[Path] = set()
    for index, module in enumerate(modules):
        label = f"module[{index}]"
        if not isinstance(module, dict) or set(module) != MODULE_KEYS:
            errors.append(f"{label}: fields must be exactly {sorted(MODULE_KEYS)}")
            continue
        module_id = module["id"]
        if not isinstance(module_id, str) or ID_RE.fullmatch(module_id) is None:
            errors.append(f"{label}: invalid module id")
            continue
        if module_id in output:
            errors.append(f"{label}: duplicate module id: {module_id}")
            continue
        output[module_id] = module

        status = module["status"]
        if status not in ALLOWED_STATUSES:
            errors.append(f"{label}: invalid status: {status!r}")
        if not isinstance(module["production_authorized"], bool):
            errors.append(f"{label}: production_authorized must be boolean")
        if module["production_authorized"] and status != "CURRENT":
            errors.append(f"{label}: only CURRENT modules may be production-authorized")
        mutation = module["broker_mutation"]
        if mutation not in ALLOWED_MUTATION_CLASSES:
            errors.append(f"{label}: invalid broker_mutation: {mutation!r}")
        if status in {"EXPERIMENTAL", "PROPOSAL", "LEGACY", "UNAVAILABLE"} and mutation not in {"NONE", "LOCAL_ONLY"}:
            errors.append(f"{label}: non-current module cannot expose broker mutation")

        document = _canonical_relative(module["document"], f"{label}.document", errors)
        if document is not None:
            if document.parent != Path("docs/modules") or document.suffix != ".md":
                errors.append(f"{label}.document: must be docs/modules/<name>.md")
            elif _existing_path(root, document.as_posix(), f"{label}.document", errors):
                documented_paths.add(document)
                _validate_doc_metadata(root, document, module, errors)

        implementations = module["implementation"]
        if not isinstance(implementations, list) or not implementations:
            errors.append(f"{label}.implementation: non-empty array required")
        else:
            for item_index, item in enumerate(implementations):
                _existing_path(root, item, f"{label}.implementation[{item_index}]", errors)

        tests = module["tests"]
        if not isinstance(tests, list):
            errors.append(f"{label}.tests: array required")
        elif status in {"CURRENT", "QUALIFICATION_REQUIRED"} and not tests:
            errors.append(f"{label}.tests: maintained modules require tests")
        else:
            for item_index, item in enumerate(tests):
                _existing_path(root, item, f"{label}.tests[{item_index}]", errors)

    module_directory = root / "docs/modules"
    try:
        actual = {
            path.relative_to(root)
            for path in module_directory.glob("*.md")
            if path.is_file() and not path.is_symlink()
        }
    except OSError as error:
        errors.append(f"docs/modules: cannot enumerate: {error}")
        actual = set()
    unregistered = sorted(actual - documented_paths)
    if unregistered:
        errors.append(
            "docs/modules: unregistered module documents: "
            + ", ".join(path.as_posix() for path in unregistered)
        )
    return output


def _validate_capabilities(
    root: Path,
    modules: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    value = _load_json(root / "docs/capabilities.json", errors)
    schema = value.get("schema") if isinstance(value, dict) else None
    if schema not in {"heptatrader.capabilities.v1", "heptatrader.capabilities.v2"}:
        errors.append("docs/capabilities.json: unsupported schema")
        return
    if schema == "heptatrader.capabilities.v1" and root.resolve() == ROOT.resolve():
        errors.append("docs/capabilities.json: checked-in capability data must use schema v2")
        return
    if value.get("live_trading_authorized") is not False:
        errors.append("docs/capabilities.json: LIVE must remain unauthorized")
    items = value.get("capabilities")
    if not isinstance(items, list) or not items:
        errors.append("docs/capabilities.json: capabilities must be a non-empty array")
        return
    capabilities: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items):
        label = f"capability[{index}]"
        expected_keys = CAPABILITY_KEYS_V2 if schema.endswith(".v2") else CAPABILITY_KEYS_V1
        if not isinstance(item, dict) or set(item) != expected_keys:
            errors.append(f"{label}: fields must be exactly {sorted(expected_keys)}")
            continue
        capability_id = item["id"]
        if not isinstance(capability_id, str) or ID_RE.fullmatch(capability_id) is None:
            errors.append(f"{label}: invalid capability id")
            continue
        if capability_id in capabilities:
            errors.append(f"{label}: duplicate capability id: {capability_id}")
            continue
        capabilities[capability_id] = item
        if item["status"] not in ALLOWED_STATUSES:
            errors.append(f"{label}: invalid status")
        if not isinstance(item["order_transport"], str) or not item["order_transport"]:
            errors.append(f"{label}: order_transport must be a non-empty string")
        if not isinstance(item["requires_external_qualification"], bool):
            errors.append(f"{label}: requires_external_qualification must be boolean")
        if schema.endswith(".v2"):
            for field in ("transport_implemented", "advertisable", "authorized"):
                if not isinstance(item[field], bool):
                    errors.append(f"{label}: {field} must be boolean")
            # Keep the three decisions independent and fail closed. A caller
            # must never infer real venue authority from transport presence.
            if item["advertisable"] and not item["transport_implemented"]:
                errors.append(f"{label}: advertisable requires implemented transport")
            if item["advertisable"] and not item["authorized"]:
                errors.append(f"{label}: advertisable requires authorization")
            if item["requires_external_qualification"] and item["authorized"]:
                errors.append(f"{label}: externally qualified capability cannot be source-authorized")
            if item["status"] != "CURRENT" and item["authorized"]:
                errors.append(f"{label}: only CURRENT capabilities may be authorized")
            if item["status"] in {"EXPERIMENTAL", "PROPOSAL", "UNAVAILABLE", "QUALIFICATION_REQUIRED"}:
                if item["advertisable"] or item["authorized"]:
                    errors.append(f"{label}: non-current capability must not be advertisable or authorized")
        else:
            if not isinstance(item["advertise_as_real_venue"], bool):
                errors.append(f"{label}: advertise_as_real_venue must be boolean")
        if item["status"] in {"EXPERIMENTAL", "PROPOSAL", "UNAVAILABLE"}:
            advertised = item.get("advertisable", item.get("advertise_as_real_venue"))
            if item["order_transport"] != "NONE" or advertised:
                errors.append(f"{label}: experimental/unavailable capability must fail closed")

    required = {
        "deterministic-simulator",
        "ib-paper",
        "ctp",
        "xt-qmt",
        "live",
    }
    missing = sorted(required - set(capabilities))
    if missing:
        errors.append("docs/capabilities.json: missing capabilities: " + ", ".join(missing))
    if capabilities.get("live", {}).get("status") != "UNAVAILABLE":
        errors.append("docs/capabilities.json: live capability must be UNAVAILABLE")
    if capabilities.get("ctp", {}).get("status") != modules.get("ctp-adapter", {}).get("status"):
        errors.append("CTP capability/module status mismatch")
    if capabilities.get("xt-qmt", {}).get("status") != modules.get("xt-adapter", {}).get("status"):
        errors.append("XT capability/module status mismatch")
    if capabilities.get("ib-paper", {}).get("status") != modules.get("ib-paper", {}).get("status"):
        errors.append("IB PAPER capability/module status mismatch")


def _markdown_files(root: Path, modules: dict[str, dict[str, Any]]) -> list[Path]:
    files = [root / relative for relative in CANONICAL_FILES]
    for module in modules.values():
        document = _canonical_relative(module.get("document"), "module.document", [])
        if document is not None:
            files.append(root / document)
    files.extend(sorted((root / "docs/operations").glob("*.md")))
    return files


def _validate_links(root: Path, files: list[Path], errors: list[str]) -> None:
    seen: set[Path] = set()
    for path in files:
        path = path.resolve()
        if path in seen:
            continue
        seen.add(path)
        try:
            relative = path.relative_to(root)
        except ValueError:
            errors.append(f"documentation file escaped repository root: {path}")
            continue
        text = _read_text(path, relative.as_posix(), errors)
        if USER_ABSOLUTE_RE.search(text):
            errors.append(f"{relative}: developer-specific absolute path is forbidden")
        for raw_target in LINK_RE.findall(text):
            target = raw_target.split("#", 1)[0].strip()
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            decoded = target.replace("%20", " ")
            resolved = (path.parent / decoded).resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                errors.append(f"{relative}: link escapes repository: {raw_target}")
                continue
            if not resolved.exists():
                errors.append(f"{relative}: broken link: {raw_target}")


def validate(root: Path | str = ROOT) -> list[str]:
    root = Path(root).resolve()
    errors: list[str] = []
    readme = _read_text(root / "README.md", "README.md", errors)
    if len(readme.strip()) < 200:
        errors.append("README.md: project entry point is missing or too small")
    for workflow in REQUIRED_WORKFLOWS:
        _existing_path(root, workflow.as_posix(), workflow.as_posix(), errors)
    modules = _validate_catalog(root, errors)
    _validate_capabilities(root, modules, errors)
    _validate_links(root, _markdown_files(root, modules), errors)
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    errors = validate(args.root)
    for error in errors:
        print(f"[DOCUMENTATION] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[DOCUMENTATION] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
