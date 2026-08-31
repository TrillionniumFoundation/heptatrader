#!/usr/bin/env python3
"""Repository-level safety, documentation, and release-contract checks."""

from __future__ import annotations

from pathlib import Path
import re
import sys
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_PATHS = (
    "README.md",
    "VERSION",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    ".github/CODEOWNERS",
    ".github/workflows/ci.yml",
    ".github/workflows/nightly-sanitizers.yml",
    ".github/workflows/ib-paper-qualification.yml",
    ".github/workflows/release.yml",
    "docs/CAPABILITY-MATRIX.md",
    "docs/RELEASE-PROCESS.md",
    "docs/RUNBOOK-STARTUP.md",
    "docs/PROD-GO-LIVE-CHECKLIST.md",
    "docs/SUPPLY-CHAIN.md",
    "scripts/verify_install_tree.py",
    "scripts/generate_sbom.py",
    "scripts/hepta_observability.py",
)

SOURCE_SIZE_LIMIT = 100_000
SOURCE_SIZE_ALLOWLIST = {
    "HeptaTrade/HeptaDemoStrategyTrader.cpp": 310_000,
    "HeptaTrade/ib_fx_multi_strategy.cpp": 180_000,
    "HeptaTrade/tool_host/session_supervisor_lease_store.cpp": 132_000,
    "HeptaTrade/tool_host/unix_session_supervisor_server.cpp": 135_000,
    "tests/unix_session_supervisor_server_tests.cpp": 165_000,
    "scripts/hepta_shadow_market_history.py": 106_000,
}

MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
FORBIDDEN_WORKSPACE_PATTERNS = (
    re.compile(r"[A-Za-z]:[\\/]Users[\\/]", re.IGNORECASE),
    re.compile(r"[A-Za-z]:[\\/]quant[\\/]", re.IGNORECASE),
    re.compile(r"/home/(?!hepta(?:/|$))[A-Za-z0-9._-]+/"),
)
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def check_required_paths(errors: list[str]) -> None:
    for item in REQUIRED_PATHS:
        if not (ROOT / item).is_file():
            errors.append(f"required file is missing: {item}")


def check_version(errors: list[str]) -> None:
    version_path = ROOT / "VERSION"
    header_path = ROOT / "Interface/include/heptaVersion.h"
    if not version_path.is_file() or not header_path.is_file():
        return
    version = read_text(version_path).strip()
    if not SEMVER.fullmatch(version):
        errors.append(f"VERSION is not a supported semantic version: {version!r}")
    header = read_text(header_path)
    if f'HEPTA_TRADER_VERSION "{version}"' not in header:
        errors.append("Interface/include/heptaVersion.h does not match VERSION")
    if "inline const char* GetHeptaTraderVersion()" not in header:
        errors.append("GetHeptaTraderVersion must be inline to avoid header ODR violations")


def iter_documentation_files() -> list[Path]:
    paths = [ROOT / "README.md", ROOT / "SECURITY-HARDENING.md"]
    for directory in (ROOT / "docs", ROOT / "scripts", ROOT / "plugins"):
        if not directory.exists():
            continue
        paths.extend(path for path in directory.rglob("*.md") if path.is_file())
    return paths


def check_documentation(errors: list[str]) -> None:
    readme = ROOT / "README.md"
    if readme.is_file() and len(read_text(readme).strip()) < 800:
        errors.append("README.md is too small to describe the supported runtime safely")

    for path in iter_documentation_files():
        text = read_text(path)
        for pattern in FORBIDDEN_WORKSPACE_PATTERNS:
            match = pattern.search(text)
            if match:
                errors.append(
                    f"developer-specific absolute path in {relative(path)}: {match.group(0)!r}")

        for raw_target in MARKDOWN_LINK.findall(text):
            target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
            if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            target = unquote(target.split("#", 1)[0])
            if not target:
                continue
            resolved = (path.parent / target).resolve()
            try:
                resolved.relative_to(ROOT.resolve())
            except ValueError:
                errors.append(
                    f"markdown link escapes repository in {relative(path)}: {raw_target}")
                continue
            if not resolved.exists():
                errors.append(
                    f"broken markdown link in {relative(path)}: {raw_target}")


def check_systemd_contracts(errors: list[str]) -> None:
    install_manifest_path = ROOT / "cmake/HeptaInstall.cmake"
    install_manifest = (
        read_text(install_manifest_path) if install_manifest_path.is_file() else ""
    )
    for unit in sorted((ROOT / "systemd").glob("*.service")):
        text = read_text(unit)
        for match in re.finditer(
            r"Documentation=file:/usr/share/doc/heptatrader/([^\s]+)", text
        ):
            doc = ROOT / "docs" / match.group(1)
            if not doc.is_file():
                errors.append(
                    f"{relative(unit)} references missing documentation {relative(doc)}")
        for match in re.finditer(
            r"^(?:ExecStart|ExecStop)=.*?/usr/libexec/([A-Za-z0-9._-]+)",
            text,
            re.MULTILINE,
        ):
            executable = match.group(1)
            if executable not in install_manifest:
                errors.append(
                    f"{relative(unit)} executable is not declared by install graph: {executable}")
        for match in re.finditer(
            r"^EnvironmentFile=-?/etc/heptatrader/([^\s]+)$", text, re.MULTILINE
        ):
            example = ROOT / "systemd" / f"{match.group(1)}.example"
            if not example.is_file():
                errors.append(
                    f"{relative(unit)} has no checked-in environment example: {relative(example)}")


def check_unsupported_venues(errors: list[str]) -> None:
    ctp = ROOT / "HeptaTrade/adapter_ctp/ctp_gateway_adapter.cpp"
    xt = ROOT / "HeptaTrade/adapter_xt/xt_gateway_adapter.cpp"
    if ctp.is_file():
        text = read_text(ctp)
        connect = text.find("HeptaCTPGatewayAdapter::Connect")
        if connect >= 0 and "return true" in text[connect : connect + 300]:
            errors.append("CTP scaffold reports a successful connection")
    if xt.is_file():
        text = read_text(xt)
        for forbidden in ("accepted_scaffold", "place_order_scaffold", "cancel_sent_scaffold"):
            if forbidden in text:
                errors.append(f"XT scaffold emits a synthetic broker success: {forbidden}")
        if "XT_TRANSPORT_UNAVAILABLE" not in text:
            errors.append("XT scaffold lacks a stable fail-closed reason code")


def check_source_size_budget(errors: list[str]) -> None:
    roots = (ROOT / "HeptaTrade", ROOT / "tests", ROOT / "scripts")
    suffixes = {".cpp", ".cc", ".cxx", ".h", ".hpp", ".py"}
    for source_root in roots:
        if not source_root.exists():
            continue
        for path in source_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in suffixes:
                continue
            rel = relative(path)
            limit = SOURCE_SIZE_ALLOWLIST.get(rel, SOURCE_SIZE_LIMIT)
            size = path.stat().st_size
            if size > limit:
                errors.append(f"source-size budget exceeded: {rel} ({size} > {limit})")


def main() -> int:
    errors: list[str] = []
    check_required_paths(errors)
    check_version(errors)
    check_documentation(errors)
    check_systemd_contracts(errors)
    check_unsupported_venues(errors)
    check_source_size_budget(errors)

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"repository contract check failed with {len(errors)} error(s)", file=sys.stderr)
        return 1
    print("repository contract check PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
