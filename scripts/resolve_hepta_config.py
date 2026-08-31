#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

VALID_PROFILES = {"sim", "paper", "live"}
PROD_PROFILES = {"paper", "live"}
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def fail(msg: str, code: int = 2):
    print(f"[CONFIG-ERROR] {msg}", file=sys.stderr)
    sys.exit(code)


def norm_profile(v: str | None) -> str | None:
    if v is None:
        return None
    x = v.strip().lower()
    return x or None


def canonical_path_str(v: str | os.PathLike[str] | None) -> str | None:
    if not v:
        return None
    return str(Path(v).expanduser().resolve())


def ensure_valid_profile(v: str | None, source: str) -> str | None:
    if v is None:
        return None
    if v not in VALID_PROFILES:
        fail(f"Invalid {source}={v}; allowed: sim/paper/live")
    return v


def detect_profile_from_xml(root: ET.Element) -> str:
    runtime = root.find("Runtime")
    if runtime is not None:
        p = norm_profile(runtime.attrib.get("Profile") or runtime.findtext("Profile"))
        if p:
            return p

    ib = root.find("IBServer")
    if ib is not None and (ib.attrib.get("Mode", "").upper() == "IB"):
        account = (ib.attrib.get("Account") or "").strip().upper()
        if account.startswith("DU"):
            return "paper"
        return "live"

    return "sim"


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve(project_root: Path, explicit_config: str | None, explicit_profile: str | None) -> dict:
    project_root = project_root.expanduser().resolve()
    env_cfg = os.getenv("HEPTA_CONFIG_PATH")
    legacy_env_cfg = os.getenv("HEPTA_TRADER_CONFIG_PATH")

    cfg_arg = canonical_path_str(explicit_config)
    cfg_env = canonical_path_str(env_cfg)
    cfg_legacy = canonical_path_str(legacy_env_cfg)

    configured = [("arg", cfg_arg), ("HEPTA_CONFIG_PATH", cfg_env), ("HEPTA_TRADER_CONFIG_PATH", cfg_legacy)]
    non_empty = [(k, v) for (k, v) in configured if v]

    if len(non_empty) > 1:
        unique_values = {v for _, v in non_empty}
        if len(unique_values) > 1:
            fail(
                "Conflict between config sources: "
                + ", ".join([f"{k}={v}" for k, v in non_empty])
                + ". Please keep exactly one source or make them identical."
            )

    if cfg_arg:
        chosen_cfg = cfg_arg
        cfg_source = "arg"
    elif cfg_env:
        chosen_cfg = cfg_env
        cfg_source = "HEPTA_CONFIG_PATH"
    elif cfg_legacy:
        chosen_cfg = cfg_legacy
        cfg_source = "HEPTA_TRADER_CONFIG_PATH"
    else:
        candidates = [
            project_root / "x64" / "Debug" / "HeptaTraderConfig.xml",
            project_root / "HeptaTrade" / "HeptaTraderConfig.xml",
            project_root / "HeptaTrade" / "HeptaTraderConfig.paper.xml",
            project_root / "Tools" / "HeptaTraderConfig.xml",
            project_root / "HeptaTrade" / "HeptaTraderConfig.xml.example",
        ]
        selected = next((c for c in candidates if c.exists()), candidates[0])
        chosen_cfg = str(selected)
        cfg_source = "auto"

    cfg_path = Path(chosen_cfg).resolve()
    if not cfg_path.exists():
        fail(f"Config file not found: {cfg_path}")

    try:
        root = ET.parse(cfg_path).getroot()
    except Exception as e:
        fail(f"Config XML parse failed: {cfg_path} :: {e}")

    xml_profile = ensure_valid_profile(detect_profile_from_xml(root), "profile inferred from config")
    env_profile = ensure_valid_profile(norm_profile(os.getenv("HEPTA_PROFILE")), "HEPTA_PROFILE")
    arg_profile = ensure_valid_profile(norm_profile(explicit_profile), "--profile")

    if env_profile and arg_profile and env_profile != arg_profile:
        fail(f"Conflict profile: HEPTA_PROFILE={env_profile} but --profile={arg_profile}")

    locked_profile = arg_profile or env_profile or xml_profile

    if locked_profile != xml_profile and (arg_profile or env_profile):
        fail(
            f"Profile lock mismatch: requested profile={locked_profile}, "
            f"but config infers profile={xml_profile}."
        )

    is_example = cfg_path.name.lower().endswith(".example")
    if is_example and locked_profile in PROD_PROFILES:
        fail(
            f"Production profile={locked_profile} cannot use template config: {cfg_path}. "
            "Render a private non-.example config first."
        )

    if locked_profile == "sim":
        ib = root.find("IBServer")
        if ib is not None and ib.attrib.get("Mode", "").upper() == "IB":
            fail("Profile=sim conflicts with IBServer.Mode=IB in config.")

    sha = file_sha256(cfg_path)
    return {
        "config_path": str(cfg_path),
        "profile": locked_profile,
        "sha256": sha,
        "sources": {
            "config": cfg_source,
            "profile": "arg" if arg_profile else ("HEPTA_PROFILE" if env_profile else "config"),
        },
        "is_example": is_example,
    }


def main():
    ap = argparse.ArgumentParser(description="Resolve canonical Hepta config + profile lock")
    ap.add_argument(
        "--project-root",
        type=Path,
        default=REPOSITORY_ROOT,
        help="repository/project root; defaults to the root containing this script",
    )
    ap.add_argument("--config")
    ap.add_argument("--profile", choices=["sim", "paper", "live"])
    ap.add_argument("--format", choices=["json", "env"], default="json")
    args = ap.parse_args()

    result = resolve(args.project_root, args.config, args.profile)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"HEPTA_CONFIG_PATH={result['config_path']}")
        print(f"HEPTA_PROFILE={result['profile']}")
        print(f"HEPTA_CONFIG_SHA256={result['sha256']}")


if __name__ == "__main__":
    main()
