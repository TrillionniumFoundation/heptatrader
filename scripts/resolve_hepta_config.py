#!/usr/bin/env python3
"""Resolve one canonical HeptaTrader runtime configuration.

Production profiles never use implicit paths or template files.  The module is
importable so the same conflict and profile rules are exercised by unit tests
and command-line callers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


VALID_PROFILES = frozenset({"sim", "paper"})
PRODUCTION_PROFILES = frozenset({"paper"})
CONFIG_ENV_KEYS = ("HEPTA_CONFIG_PATH", "HEPTA_TRADER_CONFIG_PATH")


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
        raise ConfigError(
            f"invalid {source}={normalized}; allowed: sim/paper")
    return normalized


def _canonical_path(value: str | None, project_root: Path) -> Path | None:
    if value is None or not value.strip():
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _detect_profile(root: ET.Element) -> str | None:
    """Return only an explicitly configured profile.

    Broker mode and account identifiers are deliberately not profile
    selectors.  In particular, a non-DU account must never silently turn a
    config into an unsupported LIVE profile.
    """
    runtime = root.find("Runtime")
    if runtime is not None:
        configured = _profile(
            runtime.attrib.get("Profile") or runtime.findtext("Profile"))
        if configured:
            return _validated_profile(configured, "config profile")
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _select_config(
    project_root: Path,
    explicit_config: str | None,
) -> tuple[Path, str]:
    configured = [
        ("arg", _canonical_path(explicit_config, project_root)),
        ("HEPTA_CONFIG_PATH", _canonical_path(
            os.getenv("HEPTA_CONFIG_PATH"), project_root)),
        ("HEPTA_TRADER_CONFIG_PATH", _canonical_path(
            os.getenv("HEPTA_TRADER_CONFIG_PATH"), project_root)),
    ]
    present = [(source, path) for source, path in configured if path is not None]
    if len({path for _, path in present}) > 1:
        values = ", ".join(f"{source}={path}" for source, path in present)
        raise ConfigError(
            "conflicting config sources: " + values +
            "; keep one source or make all paths identical")

    for source, path in configured:
        if path is not None:
            return path, source

    # Implicit resolution is intentionally development-only.  Do not scan
    # build trees, Tools/, user workspaces or PAPER examples.
    candidates = (
        project_root / "HeptaTrade" / "HeptaTraderConfig.xml",
        project_root / "HeptaTrade" / "HeptaTraderConfig.xml.example",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve(), "auto"
    return candidates[0].resolve(), "auto"


def resolve(
    project_root: Path,
    explicit_config: str | None = None,
    explicit_profile: str | None = None,
) -> dict[str, object]:
    project_root = project_root.expanduser().resolve()
    config_path, config_source = _select_config(project_root, explicit_config)
    if not config_path.is_file():
        raise ConfigError(f"config file not found: {config_path}")

    try:
        root = ET.parse(config_path).getroot()
    except (ET.ParseError, OSError) as error:
        raise ConfigError(
            f"config XML parse failed: {config_path}: {error}") from error

    configured_profile = _detect_profile(root)
    env_profile = _validated_profile(os.getenv("HEPTA_PROFILE"), "HEPTA_PROFILE")
    arg_profile = _validated_profile(explicit_profile, "--profile")

    if env_profile and arg_profile and env_profile != arg_profile:
        raise ConfigError(
            f"conflicting profiles: HEPTA_PROFILE={env_profile}, "
            f"--profile={arg_profile}")

    requested_profile = arg_profile or env_profile
    if configured_profile is not None and requested_profile and \
            requested_profile != configured_profile:
        raise ConfigError(
            f"profile lock mismatch: requested={requested_profile}, "
            f"config={configured_profile}")
    locked_profile = requested_profile or configured_profile or "sim"

    is_template = config_path.name.lower().endswith(".example")
    if locked_profile in PRODUCTION_PROFILES:
        if config_source == "auto":
            raise ConfigError(
                f"production profile={locked_profile} requires an explicit "
                "--config or HEPTA_CONFIG_PATH")
        if is_template:
            raise ConfigError(
                f"production profile={locked_profile} cannot use template "
                f"config: {config_path}")

    ib = root.find("IBServer")
    ib_mode = ib.attrib.get("Mode", "").strip().upper() if ib is not None else ""
    if locked_profile == "sim" and ib_mode == "IB":
        raise ConfigError("profile=sim conflicts with IBServer.Mode=IB")
    if locked_profile == "paper" and ib_mode != "IB":
        raise ConfigError("profile=paper requires IBServer.Mode=IB")

    return {
        "config_path": str(config_path),
        "profile": locked_profile,
        "sha256": _sha256(config_path),
        "sources": {
            "config": config_source,
            "profile": (
                "arg" if arg_profile else
                "HEPTA_PROFILE" if env_profile else
                "config" if configured_profile else
                "default"
            ),
        },
        "is_example": is_template,
    }


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Resolve canonical HeptaTrader config and profile lock")
    parser.add_argument("--project-root", type=Path,
                        default=_default_project_root())
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
        print(f"HEPTA_CONFIG_SHA256={result['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
