#!/usr/bin/env python3
"""Resolve one canonical HeptaTrader runtime configuration.

Production profiles never use implicit paths or template files. The module is
importable so the same file-integrity, conflict and profile rules are exercised
by unit tests and command-line callers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import xml.etree.ElementTree as ET
from typing import Any


VALID_PROFILES = frozenset({"sim", "paper"})
PRODUCTION_PROFILES = frozenset({"paper"})
CONFIG_ENV_KEYS = ("HEPTA_CONFIG_PATH", "HEPTA_TRADER_CONFIG_PATH")
MAX_XML_DEPTH = 32
MAX_XML_ELEMENTS = 4096
MAX_XML_ATTRIBUTES = 64
MAX_XML_VALUE = 4096


class ConfigError(RuntimeError):
    """Stable fail-fast configuration error."""


def _profile(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


def _validated_profile(value: str | None, source: str) -> str | None:
    normalized = _profile(value)
    if normalized is not None and normalized not in VALID_PROFILES:
        raise ConfigError(f"invalid {source}={normalized}; allowed: sim/paper")
    return normalized


def _canonical_path(value: str | None, project_root: Path) -> Path | None:
    if value is None or not value.strip():
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return Path(os.path.abspath(os.fspath(path)))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ConfigError(f"config read failed: {path}: {error}") from error
    return digest.hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _safe_xml_value(value: str, label: str) -> str:
    if "\x00" in value or len(value) > MAX_XML_VALUE:
        raise ConfigError(f"config XML value is invalid at {label}")
    return value


def _canonical_xml_element(
    element: ET.Element,
    depth: int,
    counter: list[int],
) -> dict[str, Any]:
    if depth > MAX_XML_DEPTH:
        raise ConfigError("config XML exceeds maximum nesting depth")
    counter[0] += 1
    if counter[0] > MAX_XML_ELEMENTS:
        raise ConfigError("config XML has too many elements")
    if not isinstance(element.tag, str) or not element.tag:
        raise ConfigError("config XML contains a non-element node")
    if len(element.attrib) > MAX_XML_ATTRIBUTES:
        raise ConfigError(f"config XML element {element.tag} has too many attributes")

    attributes: dict[str, str] = {}
    for key in sorted(element.attrib):
        if not isinstance(key, str) or not key:
            raise ConfigError(f"config XML element {element.tag} has invalid attribute")
        attributes[_safe_xml_value(key, f"{element.tag}.attribute")] = _safe_xml_value(
            element.attrib[key], f"{element.tag}.{key}"
        )

    text = (element.text or "").strip()
    tail = (element.tail or "").strip()
    if tail:
        raise ConfigError(f"config XML mixed-content tail is forbidden after {element.tag}")
    children = [
        _canonical_xml_element(child, depth + 1, counter)
        for child in list(element)
    ]
    return {
        "tag": _safe_xml_value(element.tag, "tag"),
        "attributes": attributes,
        "text": _safe_xml_value(text, f"{element.tag}.text"),
        "children": children,
    }


def _canonical_xml_digest(root: ET.Element) -> str:
    canonical = _canonical_xml_element(root, 0, [0])
    return hashlib.sha256(_canonical_json(canonical)).hexdigest()


def _single_child(root: ET.Element, tag: str) -> ET.Element | None:
    matches = root.findall(tag)
    if len(matches) > 1:
        raise ConfigError(f"config contains duplicate authoritative {tag} elements")
    return matches[0] if matches else None


def _detect_profile(runtime: ET.Element | None) -> str | None:
    """Return only an explicitly configured profile.

    Broker mode and account identifiers are deliberately not profile selectors.
    A non-DU account must never silently turn a config into a LIVE profile.
    """
    if runtime is None:
        return None
    profile_nodes = runtime.findall("Profile")
    if len(profile_nodes) > 1:
        raise ConfigError("Runtime contains duplicate Profile elements")
    attribute = runtime.attrib.get("Profile")
    child = profile_nodes[0].text if profile_nodes else None
    attribute_profile = _profile(attribute)
    child_profile = _profile(child)
    if attribute_profile and child_profile and attribute_profile != child_profile:
        raise ConfigError(
            "Runtime.Profile attribute and element conflict: "
            f"{attribute_profile} != {child_profile}"
        )
    configured = attribute_profile or child_profile
    return _validated_profile(configured, "config profile")


def _validate_config_file(path: Path) -> Path:
    try:
        direct = path.lstat()
    except OSError as error:
        raise ConfigError(f"config file not found: {path}") from error
    if stat.S_ISLNK(direct.st_mode):
        raise ConfigError(f"config file must not be a symlink: {path}")
    if not stat.S_ISREG(direct.st_mode):
        raise ConfigError(f"config path must be a regular file: {path}")
    if direct.st_nlink != 1:
        raise ConfigError(f"config file must have one hard-link: {path}")
    if direct.st_mode & stat.S_IWOTH:
        raise ConfigError(f"config file must not be world-writable: {path}")
    if direct.st_mode & (stat.S_ISUID | stat.S_ISGID):
        raise ConfigError(f"config file must not carry set-id bits: {path}")
    try:
        return path.resolve(strict=True)
    except OSError as error:
        raise ConfigError(f"config path resolution failed: {path}: {error}") from error


def _select_config(
    project_root: Path,
    explicit_config: str | None,
) -> tuple[Path, str]:
    configured = [
        ("arg", _canonical_path(explicit_config, project_root)),
        (
            "HEPTA_CONFIG_PATH",
            _canonical_path(os.getenv("HEPTA_CONFIG_PATH"), project_root),
        ),
        (
            "HEPTA_TRADER_CONFIG_PATH",
            _canonical_path(os.getenv("HEPTA_TRADER_CONFIG_PATH"), project_root),
        ),
    ]
    present = [(source, path) for source, path in configured if path is not None]
    resolved_identities: dict[Path, list[tuple[str, Path]]] = {}
    for source, path in present:
        assert path is not None
        try:
            identity = path.resolve(strict=False)
        except OSError as error:
            raise ConfigError(f"config path resolution failed: {path}: {error}") from error
        resolved_identities.setdefault(identity, []).append((source, path))
    if len(resolved_identities) > 1:
        values = ", ".join(f"{source}={path}" for source, path in present)
        raise ConfigError(
            "conflicting config sources: "
            + values
            + "; keep one source or make all paths identical"
        )

    for source, path in configured:
        if path is not None:
            return path, source

    # Implicit resolution is intentionally development-only. Do not scan build
    # trees, Tools/, user workspaces or PAPER examples.
    candidates = (
        project_root / "HeptaTrade" / "HeptaTraderConfig.xml",
        project_root / "HeptaTrade" / "HeptaTraderConfig.xml.example",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate.absolute(), "auto"
    return candidates[0].absolute(), "auto"


def resolve(
    project_root: Path,
    explicit_config: str | None = None,
    explicit_profile: str | None = None,
) -> dict[str, object]:
    project_root = project_root.expanduser().resolve()
    selected_path, config_source = _select_config(project_root, explicit_config)
    config_path = _validate_config_file(selected_path)

    try:
        tree = ET.parse(config_path)
        root = tree.getroot()
    except (ET.ParseError, OSError) as error:
        raise ConfigError(
            f"config XML parse failed: {config_path}: {error}"
        ) from error
    if root.tag != "Config":
        raise ConfigError(f"config XML root must be Config, got {root.tag!r}")

    runtime = _single_child(root, "Runtime")
    ib = _single_child(root, "IBServer")
    configured_profile = _detect_profile(runtime)
    env_profile = _validated_profile(os.getenv("HEPTA_PROFILE"), "HEPTA_PROFILE")
    arg_profile = _validated_profile(explicit_profile, "--profile")

    if env_profile and arg_profile and env_profile != arg_profile:
        raise ConfigError(
            f"conflicting profiles: HEPTA_PROFILE={env_profile}, "
            f"--profile={arg_profile}"
        )

    requested_profile = arg_profile or env_profile
    if configured_profile is not None and requested_profile and \
            requested_profile != configured_profile:
        raise ConfigError(
            f"profile lock mismatch: requested={requested_profile}, "
            f"config={configured_profile}"
        )
    locked_profile = requested_profile or configured_profile or "sim"

    is_template = config_path.name.lower().endswith(".example")
    if locked_profile in PRODUCTION_PROFILES:
        if config_source == "auto":
            raise ConfigError(
                f"production profile={locked_profile} requires an explicit "
                "--config or HEPTA_CONFIG_PATH"
            )
        if is_template:
            raise ConfigError(
                f"production profile={locked_profile} cannot use template "
                f"config: {config_path}"
            )

    ib_mode = ib.attrib.get("Mode", "").strip().upper() if ib is not None else ""
    if locked_profile == "sim" and ib_mode == "IB":
        raise ConfigError("profile=sim conflicts with IBServer.Mode=IB")
    if locked_profile == "paper" and ib_mode != "IB":
        raise ConfigError("profile=paper requires IBServer.Mode=IB")

    source_digest = _sha256(config_path)
    canonical_digest = _canonical_xml_digest(root)
    return {
        "schema": "heptatrader.runtime-config-resolution.v1",
        "config_path": str(config_path),
        "profile": locked_profile,
        # Preserve the historical key as the canonical semantic identity.
        "sha256": canonical_digest,
        "canonical_sha256": canonical_digest,
        "source_sha256": source_digest,
        "sources": {
            "config": config_source,
            "profile": (
                "arg"
                if arg_profile
                else "HEPTA_PROFILE"
                if env_profile
                else "config"
                if configured_profile
                else "default"
            ),
        },
        "is_example": is_template,
        "authority": {
            "runtime_profile": "Runtime.Profile/--profile/HEPTA_PROFILE",
            "venue_mode": "IBServer.Mode",
            "account_identity_exported": False,
        },
    }


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Resolve canonical HeptaTrader config and profile lock"
    )
    parser.add_argument("--project-root", type=Path, default=_default_project_root())
    parser.add_argument("--config")
    parser.add_argument("--profile", choices=sorted(VALID_PROFILES))
    parser.add_argument("--format", choices=("json", "env"), default="json")
    args = parser.parse_args(argv)

    try:
        result = resolve(args.project_root, args.config, args.profile)
    except ConfigError as error:
        print(f"[CONFIG-ERROR] {error}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(f"HEPTA_CONFIG_PATH={result['config_path']}")
        print(f"HEPTA_PROFILE={result['profile']}")
        print(f"HEPTA_CONFIG_SHA256={result['canonical_sha256']}")
        print(f"HEPTA_CONFIG_SOURCE_SHA256={result['source_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
