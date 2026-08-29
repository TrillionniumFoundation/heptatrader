#!/usr/bin/env python3
"""Bounded repository-truth checks used by local development and CI."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = (
    "README.md",
    "docs/README.md",
    "docs/development/PLAN.md",
    "docs/development/AGENT-INTENT-CONTRACT.md",
    "docs/development/TEST-STRATEGY.md",
    "docs/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md",
    "docs/CAPABILITY-MATRIX.md",
    "docs/SECURITY.md",
    "docs/OBSERVABILITY.md",
    "docs/RISK-MODEL.md",
    "docs/OMS-EVENT-SCHEMA.md",
    "docs/RECONCILE-RULES.md",
    "docs/CONFIGURATION.md",
    "docs/DEPLOYMENT.md",
    "docs/ITERATION.md",
    "docs/STRATEGY-VALIDATION-PLAN.md",
    "research/README.md",
    "research/manifest-v1.json",
    "legacy/README.md",
)

REMOVED_ACTIVE_PATHS = (
    "HeptaSimulator",
    "HeptaStrategy",
    "Interface",
    "Tools",
    "doc",
    "HeptaTrader.sln",
    "HeptaTrader_Linux.sln",
    "HeptaTrade/HeptaDemoStrategyTrader.cpp",
    "HeptaTrade/HeptaTrader.vcxproj",
    "HeptaTrade/HeptaTrader_Linux.vcxproj",
    "HeptaTrade/ib_fx_multi_strategy.cpp",
    "HeptaTrade/ib_fx_multi_strategy.h",
    "HeptaTrade/openclaw_0dte_bridge.cpp",
    "HeptaTrade/openclaw_0dte_bridge.h",
    "HeptaTrade/order_watchdog.cpp",
    "HeptaTrade/order_watchdog.h",
    "HeptaTrade/risk/pre_trade_risk_engine.cpp",
    "HeptaTrade/risk/pre_trade_risk_engine.h",
)

STALE_BUILD_TOKENS = (
    "HEPTA_BUILD_LEGACY_MONOLITH",
    "HEPTA_BUILD_LEGACY_SIMULATOR",
    "HEPTA_ENABLE_LEGACY_0DTE_BRIDGE",
)

LINK_DOCS = (
    "README.md",
    "docs/README.md",
    "research/README.md",
)

TEXT_SUFFIXES = frozenset({
    ".c", ".cc", ".cpp", ".h", ".hpp", ".py", ".cmake", ".json",
    ".yml", ".yaml", ".service", ".socket", ".in", ".conf",
})


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _check_markdown_links(relative: str, errors: list[str]) -> None:
    source = ROOT / relative
    pattern = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
    for raw_target in pattern.findall(_text(source)):
        target = raw_target.strip().split(" ", 1)[0].strip("<>")
        if not target or target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        target = unquote(target.split("#", 1)[0])
        resolved = (source.parent / target).resolve()
        try:
            resolved.relative_to(ROOT)
        except ValueError:
            errors.append(f"{relative}: link escapes repository: {raw_target}")
            continue
        if not resolved.exists():
            errors.append(f"{relative}: missing local link target: {raw_target}")


def _active_text_files() -> list[Path]:
    result: list[Path] = [ROOT / "CMakeLists.txt", ROOT / "CMakePresets.json"]
    for directory in ("HeptaTrade", "adapters", "cmake", "systemd", "plugins"):
        root = ROOT / directory
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.name == "CMakeLists.txt" or path.suffix in TEXT_SUFFIXES:
                result.append(path)
    return result


def validate() -> list[str]:
    errors: list[str] = []

    for relative in REQUIRED_FILES:
        if not (ROOT / relative).is_file():
            errors.append(f"required file is missing: {relative}")

    for relative in REMOVED_ACTIVE_PATHS:
        if (ROOT / relative).exists():
            errors.append(f"inactive monolith surface remains active: {relative}")

    for relative in LINK_DOCS:
        if (ROOT / relative).is_file():
            _check_markdown_links(relative, errors)

    for relative in ("CMakeLists.txt", "CMakePresets.json", "scripts/dev_core.sh"):
        path = ROOT / relative
        if not path.is_file():
            continue
        contents = _text(path)
        for token in STALE_BUILD_TOKENS:
            if token in contents:
                errors.append(f"{relative}: stale legacy build token: {token}")

    for path in _active_text_files():
        if "legacy/" in _text(path).replace("\\", "/"):
            errors.append(
                f"{path.relative_to(ROOT)}: active runtime depends on legacy/")

    workflows = ROOT / ".github" / "workflows"
    if (workflows.exists()):
        if (workflows / "finalize-remaining-gaps.yml").exists():
            errors.append("self-merging finalizer workflow is present")
        for path in workflows.glob("*.y*ml"):
            contents = _text(path)
            for forbidden in (
                "contents: write",
                "pull-requests: write",
                "gh pr merge",
                "/pulls/$PR_NUMBER/merge",
            ):
                if forbidden in contents:
                    errors.append(
                        f"{path.relative_to(ROOT)}: workflow has forbidden mutation: {forbidden}")

    manifest_path = ROOT / "research" / "manifest-v1.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(_text(manifest_path))
        except json.JSONDecodeError as error:
            errors.append(f"research manifest is invalid JSON: {error}")
        else:
            if manifest.get("schema") != "heptatrader.research-manifest.v1":
                errors.append("research manifest schema is not v1")
            if manifest.get("mode") != "shadow":
                errors.append("research manifest must remain SHADOW-only")
            capability = manifest.get("capability")
            if not isinstance(capability, dict) or any(capability.values()):
                errors.append("research manifest grants runtime capability")
            strategy = manifest.get("strategy") or {}
            for field in ("definition", "implementation", "context_builder", "replay_evaluator"):
                value = strategy.get(field)
                if not isinstance(value, str) or not (ROOT / value).is_file():
                    errors.append(f"research manifest has missing strategy.{field}: {value}")

    ctp = ROOT / "HeptaTrade" / "adapter_ctp" / "ctp_gateway_adapter.cpp"
    if ctp.is_file():
        contents = _text(ctp)
        if "VENUE_NOT_IMPLEMENTED" not in contents:
            errors.append("CTP scaffold lacks a typed unsupported reason")
        if "return true" in contents:
            errors.append("CTP scaffold can report synthetic success")

    xt = ROOT / "HeptaTrade" / "adapter_xt" / "xt_gateway_adapter.cpp"
    if xt.is_file():
        contents = _text(xt)
        if not any(reason in contents for reason in (
                "VENUE_NOT_IMPLEMENTED", "XT_TRANSPORT_NOT_BUILT")):
            errors.append("XT scaffold lacks a typed unsupported reason")
        for synthetic in ("accepted_scaffold", "place_order_scaffold", "cancel_sent_scaffold"):
            if synthetic in contents:
                errors.append(f"XT scaffold contains synthetic success: {synthetic}")

    capability_pattern = re.compile(
        r"(?:^|[,\s])(trade\.place|operator\.trade\.place)(?:[,\s]|$)")
    for pattern in ("*agent*env.example", "*gateway*env.example"):
        for path in (ROOT / "systemd").glob(pattern):
            if "operator" in path.name:
                continue
            if capability_pattern.search(_text(path)):
                errors.append(
                    f"ordinary Agent/Gateway example exposes raw place authority: {path.name}")

    return errors


def main() -> int:
    errors = validate()
    if errors:
        for error in errors:
            print(f"[REPOSITORY-INTEGRITY] {error}", file=sys.stderr)
        return 1
    print("[REPOSITORY-INTEGRITY] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
