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
    "cmake/HeptaInstall.cmake",
    "cmake/HeptaProjectOptions.cmake",
    "cmake/HeptaTargetHardening.cmake",
    "docs/CAPABILITY-MATRIX.md",
    "docs/IB-PAPER-QUALIFICATION.md",
    "docs/RELEASE-PROCESS.md",
    "docs/RUNBOOK-STARTUP.md",
    "docs/PROD-GO-LIVE-CHECKLIST.md",
    "docs/SUPPLY-CHAIN.md",
    "scripts/build_release_archive.py",
    "scripts/verify_install_tree.py",
    "scripts/verify_release_ci.py",
    "scripts/verify_ib_paper_qualification.py",
    "scripts/generate_sbom.py",
    "scripts/hepta_observability.py",
    "scripts/validate_sim_data.py",
    "tests/assertions_enabled_tests.cpp",
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
TEXT_SUFFIXES = {".md", ".py", ".sh", ".cmake", ".txt", ".yml", ".yaml", ".json"}
PORTABILITY_ROOTS = (".github", "cmake", "docs", "scripts", "systemd", "plugins")
# The negative look-behind includes '/' so an installed path such as
# lib/systemd/system/foo.service is not mistaken for a repository path rooted
# at systemd/. Backslashes are normalized before matching.
REPOSITORY_PATH_REFERENCE = re.compile(
    r"(?<![-A-Za-z0-9_./])((?:scripts|docs|systemd|strategies)/[-A-Za-z0-9_./]+\.(?:py|sh|ps1|md|service|socket|timer|example|json|xml))"
)
WORKFLOW_ACTION_USE = re.compile(
    r"^\s*(?:-\s*)?uses:\s*([^\s#]+)", re.MULTILINE
)
FULL_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
CONTAINER_DIGEST = re.compile(r"^docker://[^@\s]+@sha256:[0-9a-fA-F]{64}$")

# Runtime names can differ from CMake target names through OUTPUT_NAME or
# install(PROGRAMS ... RENAME ...). Each entry must have at least one token in
# the canonical install graph.
EXECUTABLE_INSTALL_TOKENS = {
    "hepta-tool-gatewayd": ("hepta_tool_gatewayd",),
    "hepta-executiond": ("hepta_executiond",),
    "hepta-ib-executiond": ("hepta_ib_executiond",),
    "hepta-broker-egress-policy": ("hepta_broker_egress_policy.py",),
    "hepta-observability": ("hepta_observability.py",),
}

DYNAMIC_ENV_EXAMPLES = {
    "trust-domains/%i.env": "systemd/hepta-tool-gateway-domain.env.example",
    "trust-domains/%i.execution.env": "systemd/hepta-execution-simulator.env.example",
}


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


def iter_portability_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "SECURITY-HARDENING.md", ROOT / "CMakeLists.txt"]
    for name in PORTABILITY_ROOTS:
        root = ROOT / name
        if not root.exists():
            continue
        files.extend(
            path for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES
        )
    return files


def check_portability(errors: list[str]) -> None:
    for path in iter_portability_files():
        text = read_text(path)
        for pattern in FORBIDDEN_WORKSPACE_PATTERNS:
            match = pattern.search(text)
            if match:
                errors.append(
                    f"developer-specific absolute path in {relative(path)}: {match.group(0)!r}")
                break
        normalized_text = text.replace("\\", "/")
        for raw in REPOSITORY_PATH_REFERENCE.findall(normalized_text):
            target = ROOT / raw
            if not target.is_file():
                errors.append(
                    f"stale repository path in {relative(path)}: {raw}")


def action_use_is_immutable(specification: str) -> bool:
    if specification.startswith("./"):
        return True
    if specification.startswith("docker://"):
        return CONTAINER_DIGEST.fullmatch(specification) is not None
    if "@" not in specification:
        return False
    action, revision = specification.rsplit("@", 1)
    return bool(action) and FULL_COMMIT_SHA.fullmatch(revision) is not None


def check_workflow_action_pins(errors: list[str]) -> None:
    workflow_root = ROOT / ".github/workflows"
    if not workflow_root.is_dir():
        return
    workflows = sorted(workflow_root.glob("*.yml")) + sorted(
        workflow_root.glob("*.yaml")
    )
    for path in workflows:
        for specification in WORKFLOW_ACTION_USE.findall(read_text(path)):
            if not action_use_is_immutable(specification):
                errors.append(
                    "workflow action is not pinned to an immutable digest in "
                    f"{relative(path)}: {specification}"
                )


def check_release_authority_contract(errors: list[str]) -> None:
    path = ROOT / ".github/workflows/release.yml"
    if not path.is_file():
        return
    text = read_text(path)
    required_tokens = (
        "scripts/verify_release_ci.py",
        "environment: heptatrader-release",
        "HEPTA_RELEASE_APPROVED_SHA",
        "needs: build-candidate",
        "actions/download-artifact@",
        "actions/attest-build-provenance@",
        "gh release create",
        "SOURCE_DATE_EPOCH",
        "build-a",
        "build-b",
        "scripts/build_release_archive.py",
        "Prove and seal byte-for-byte reproducibility",
    )
    for token in required_tokens:
        if token not in text:
            errors.append(f"release workflow lacks required authority boundary: {token}")

    publish = text.find("\n  publish:")
    environment = text.find("environment: heptatrader-release")
    approval = text.find("HEPTA_RELEASE_APPROVED_SHA")
    release = text.find("gh release create")
    attestation = text.find("actions/attest-build-provenance@")
    if publish < 0 or not (publish < environment < approval < attestation < release):
        errors.append(
            "release publication must occur only after protected environment approval"
        )
    if publish >= 0 and re.search(
        r"^\s+contents:\s*write\s*$", text[:publish], re.MULTILINE
    ):
        errors.append("release candidate build holds contents:write before publication")
    if text.count("cmp \"") < 3:
        errors.append("release workflow does not compare all deterministic evidence outputs")


def check_ci_reproducibility_contract(errors: list[str]) -> None:
    path = ROOT / ".github/workflows/ci.yml"
    if not path.is_file():
        return
    text = read_text(path)
    required_tokens = (
        "SOURCE_DATE_EPOCH",
        "build-a",
        "build-b",
        "scripts/build_release_archive.py",
        "Prove byte-for-byte reproducibility",
        "install-manifest.json",
        "heptatrader.spdx.json",
    )
    for token in required_tokens:
        if token not in text:
            errors.append(f"CI lacks reproducible-package boundary: {token}")
    if text.count("cmp \"") < 3:
        errors.append("CI does not compare manifest, SBOM, and archive byte-for-byte")


def check_ib_qualification_contract(errors: list[str]) -> None:
    workflow_path = ROOT / ".github/workflows/ib-paper-qualification.yml"
    wrapper_path = ROOT / "scripts/run_ib_paper_qualification.sh"
    if not workflow_path.is_file() or not wrapper_path.is_file():
        return
    workflow = read_text(workflow_path)
    wrapper = read_text(wrapper_path)
    workflow_tokens = (
        "environment: ib-paper",
        "mutation_mode",
        "MUTATION_MODE",
        "HEPTA_QUALIFICATION_MUTATIONS: '1'",
        "scripts/verify_ib_paper_qualification.py",
        "Re-verify committed qualification artifact",
        "persist-credentials: false",
    )
    for token in workflow_tokens:
        if token not in workflow:
            errors.append(f"IB PAPER workflow lacks required qualification boundary: {token}")
    if "inputs.mutation_mode && '1' || '0'" in workflow:
        errors.append("IB PAPER qualification must not expose a green read-only fallback")

    wrapper_tokens = (
        'HEPTA_QUALIFICATION_MUTATIONS:-0',
        '"$MUTATIONS" != "1"',
        "qualification-result.json",
        "qualification-verification.json",
        "verify_ib_paper_qualification.py",
        "--mode bounded-mutations",
        "mv -T",
    )
    for token in wrapper_tokens:
        if token not in wrapper:
            errors.append(f"IB PAPER wrapper lacks required qualification boundary: {token}")


def install_declares(executable: str, install_manifest: str) -> bool:
    tokens = EXECUTABLE_INSTALL_TOKENS.get(executable)
    return tokens is not None and any(token in install_manifest for token in tokens)


def environment_example(environment_file: str) -> Path:
    dynamic = DYNAMIC_ENV_EXAMPLES.get(environment_file)
    if dynamic:
        return ROOT / dynamic
    return ROOT / "systemd" / f"{environment_file}.example"


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
            r"^(?:ExecStart|ExecStop|LoadCredential)=.*?/usr/libexec/([A-Za-z0-9._-]+)",
            text,
            re.MULTILINE,
        ):
            executable = match.group(1)
            if executable not in EXECUTABLE_INSTALL_TOKENS:
                errors.append(
                    f"{relative(unit)} references an unknown packaged executable: {executable}")
            elif not install_declares(executable, install_manifest):
                errors.append(
                    f"{relative(unit)} executable is not declared by install graph: {executable}")

        for match in re.finditer(
            r"^EnvironmentFile=-?/etc/heptatrader/([^\s]+)$", text, re.MULTILINE
        ):
            environment_file = match.group(1)
            example = environment_example(environment_file)
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
    check_portability(errors)
    check_workflow_action_pins(errors)
    check_release_authority_contract(errors)
    check_ci_reproducibility_contract(errors)
    check_ib_qualification_contract(errors)
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
