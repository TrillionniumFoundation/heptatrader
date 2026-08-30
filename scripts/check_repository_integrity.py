#!/usr/bin/env python3
"""Bounded repository-truth checks used by local development and CI."""

from __future__ import annotations

import json
import ast
import hashlib
from pathlib import Path, PureWindowsPath
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
CPP_SUFFIXES = frozenset({".c", ".cc", ".cpp", ".h", ".hpp"})
BUILD_SUFFIXES = frozenset({".cmake", ".in"})
DOCUMENT_METADATA_FIELDS = ("Status:", "Applies to:", "Verification:")

# Workflow files are part of the product's authority boundary.  A read-only
# CI gate may inspect source, build artifacts and test results, but it must
# never be able to mutate the repository, a pull request, or its own closure
# state.  Keep these checks deliberately command-oriented: prose/comments in
# the workflow can document a prohibited operation without becoming an
# executable capability.
# GitHub's permission scopes.  Limiting the regex to actual scopes avoids
# rejecting an unrelated job input such as ``mode: write`` while still
# covering both workflow-level and job-level permission maps.
WORKFLOW_PERMISSION_SCOPES = (
    "actions", "attestations", "checks", "contents", "deployments",
    "discussions", "id-token", "issues", "packages", "pages",
    "pull-requests", "repository-projects", "security-events", "statuses",
)
WORKFLOW_MUTATION_PERMISSION_RE = re.compile(
    rf"\b(?:{'|'.join(re.escape(scope) for scope in WORKFLOW_PERMISSION_SCOPES)})"
    r"\s*:\s*write(?:-all)?\b",
    re.IGNORECASE,
)
WORKFLOW_WRITE_ALL_RE = re.compile(
    r"\bpermissions\s*:\s*write-all\b", re.IGNORECASE)

# Commands/actions that can commit, push, change PR state, or mutate GitHub
# resources.  The patterns are applied after YAML/shell comments are removed.
# They intentionally permit ordinary read-only commands such as
# ``git diff``, ``git fetch``, ``gh pr view`` and ``gh api`` GET calls.
WORKFLOW_MUTATION_PATTERNS = (
    (re.compile(
        r"\bgit\s+(?:(?:-{1,2}[^\s;&|]+)"
        r"(?:\s+(?!(?:commit|push)\b)[^\s;&|]+)?\s+)*(?:commit|push)\b",
        re.IGNORECASE),
     "git commit/push"),
    (re.compile(
        r"\bgh\s+pr\s+(?:create|merge|close|comment|edit|review|reopen|lock|unlock|ready|"
        r"convert-draft)\b",
        re.IGNORECASE), "gh pr mutation"),
    (re.compile(
        r"\bgh\s+issue\s+(?:create|close|comment|edit|reopen|lock|unlock|delete)\b",
        re.IGNORECASE), "gh issue mutation"),
    (re.compile(
        r"\bgh\s+repo\s+(?:create|delete|edit|rename|archive|transfer|sync)\b",
        re.IGNORECASE), "gh repo mutation"),
    (re.compile(
        r"\bgh\s+release\s+(?:create|delete|edit|upload|delete-asset)\b",
        re.IGNORECASE), "gh release mutation"),
    (re.compile(
        r"\bgh\s+workflow\s+(?:run|enable|disable)\b", re.IGNORECASE),
     "gh workflow mutation"),
    (re.compile(
        r"\bgh\s+api\b[^\n]*(?:--?method|--request|-X)\s*(?:=\s*)?(?:POST|PATCH|PUT|DELETE)\b",
        re.IGNORECASE), "gh api mutation"),
    (re.compile(
        r"\bgh\s+api\b[^\n]*(?:--field|--raw-field|--input|-F|-f)\b",
        re.IGNORECASE), "gh api mutation"),
    (re.compile(
        r"\b(?:curl|wget)\b[^\n]*(?:--?request|--?method|-X)\s*(?:=\s*)?(?:POST|PATCH|PUT|DELETE)\b",
        re.IGNORECASE), "HTTP mutation"),
    (re.compile(
        r"\b(?:curl|wget)\b[^\n]*(?:--data(?:-[A-Za-z0-9-]+)?|--upload-file|"
        r"--post-data|--body-data|-d)\b",
        re.IGNORECASE), "HTTP mutation"),
    (re.compile(
        r"\b(?:github|context)\.rest\.[A-Za-z0-9_]+\."
        r"(?:create|update|delete|merge|close|remove|add|assign|lock|unlock|"
        r"cancel|request|set[A-Za-z0-9_]*)\b",
        re.IGNORECASE), "GitHub API mutation"),
    (re.compile(
        r"\b(?:github|context|octokit)\.request\b[\s\S]{0,512}\b(?:POST|PATCH|PUT|DELETE)\b",
        re.IGNORECASE), "GitHub API mutation"),
    (re.compile(
        r"\b(?:github|context|octokit)\.graphql\b[\s\S]{0,512}\bmutation\s*\{",
        re.IGNORECASE), "GitHub API mutation"),
    (re.compile(
        r"\b(?:git\s+)?rm\b[^\n]*\.github[\\/]workflows\b", re.IGNORECASE),
     "workflow self-delete"),
    (re.compile(
        r"(?=[^\n]*\.github[\\/]workflows\b)(?=[^\n]*\brm\b)[^\n]*",
        re.IGNORECASE), "workflow self-delete"),
    (re.compile(
        r"\bfind\b[^\n]*\.github[\\/]workflows\b[^\n]*\s-delete\b",
        re.IGNORECASE), "workflow self-delete"),
    (re.compile(
        r"\bgit\s+clean\b[^\n]*\.github[\\/]workflows\b",
        re.IGNORECASE), "workflow self-delete"),
    (re.compile(
        r"(?=[^\n]*\.github[\\/]workflows\b)"
        r"(?=[^\n]*\b(?:unlink|rmtree|remove|shutil\.rmtree)\b)[^\n]*",
        re.IGNORECASE), "workflow self-delete"),
    (re.compile(
        r"\b(?:tee|cp|mv|install|sed\s+(?:-i|--in-place)|perl\s+-i)\b"
        r"[^\n]*(?:docs[\\/]development|PLAN\.md|EXACT-HEAD|"
        r"(?:final|closure|evidence|receipt)[-_A-Za-z0-9./]*)",
        re.IGNORECASE), "evidence/plan mutation"),
    (re.compile(
        r"(?:>|>>)\s*[^\n]*(?:docs[\\/]development|PLAN\.md|EXACT-HEAD|"
        r"(?:final|closure|evidence|receipt)[-_A-Za-z0-9./]*)",
        re.IGNORECASE), "evidence/plan mutation"),
    (re.compile(
        r"(?=[^\n]*(?:docs[\\/]development|PLAN\.md|EXACT-HEAD|"
        r"(?:final|closure|evidence|receipt)[-_A-Za-z0-9./]*))"
        r"(?=[^\n]*\b(?:open|write_text|File\.write|writeFile|writeFileSync)\b)[^\n]*",
        re.IGNORECASE), "evidence/plan mutation"),
    (re.compile(
        r"(?:^|[^A-Za-z0-9])(?:finaliz(?:e|er|ation)?|close[-_]?gap|"
        r"self[-_]?merge)(?:[^A-Za-z0-9]|$)",
        re.IGNORECASE), "closure/finalizer command"),
)

WORKFLOW_MUTATING_ACTION_RE = re.compile(
    r"(?:create[-_]pull[-_]request|create[-_]release|auto[-_]?merge|"
    r"auto[-_]?approve|automerge|release[-_]please|release[-_]drafter|"
    r"gh[-_]release|semantic[-_]release)",
    re.IGNORECASE,
)

WORKFLOW_PERMISSION_READ_VALUES = frozenset({"read", "none"})


def _workflow_permission_errors(code: str) -> list[str]:
    """Return diagnostics for missing/ambiguous workflow permissions.

    GitHub's repository default token policy is mutable outside the checkout;
    a permanent CI workflow therefore has to declare its read-only permission
    map explicitly.  This deliberately parses only the small YAML shape used
    by ``permissions`` (inline map, block map, ``read-all`` or ``{}``) and
    rejects aliases/expressions/unknown values rather than guessing their
    effective scope.
    """

    lines = code.splitlines()
    blocks: list[tuple[int, str, int]] = []
    top_level_count = 0
    for index, line in enumerate(lines):
        match = re.match(r"^(?P<indent>\s*)permissions\s*:\s*(?P<value>.*)$", line,
                         re.IGNORECASE)
        if not match:
            continue
        indent = len(match.group("indent"))
        if indent == 0:
            top_level_count += 1
        blocks.append((indent, match.group("value").strip(), index))

    errors: list[str] = []
    if top_level_count == 0:
        errors.append("workflow lacks an explicit top-level read-only permissions map")
    elif top_level_count > 1:
        errors.append("workflow has duplicate top-level permissions maps")

    for base_indent, inline_value, index in blocks:
        value = inline_value.strip()
        entries: list[tuple[str, str]] = []
        if not value:
            cursor = index + 1
            while cursor < len(lines):
                candidate = lines[cursor]
                if not candidate.strip():
                    cursor += 1
                    continue
                candidate_indent = len(candidate) - len(candidate.lstrip())
                if candidate_indent <= base_indent:
                    break
                entry = re.match(
                    r"^\s*(?P<scope>[A-Za-z0-9-]+)\s*:\s*(?P<grant>[^\s#]+)\s*$",
                    candidate,
                )
                if entry is None:
                    errors.append("workflow permissions map is ambiguous")
                    break
                entries.append((entry.group("scope"), entry.group("grant")))
                cursor += 1
            if not entries:
                errors.append("workflow permissions map is empty or ambiguous")
                continue
        elif value.lower() in {"{}", "read-all"}:
            continue
        elif value.lower() in {"write-all", "write"}:
            errors.append("workflow permissions grant write-all access")
            continue
        elif value.startswith("{") and value.endswith("}"):
            inner = value[1:-1].strip()
            if not inner:
                continue
            for raw_entry in inner.split(","):
                entry = re.match(
                    r"^\s*(?P<scope>[A-Za-z0-9-]+)\s*:\s*(?P<grant>[^\s]+)\s*$",
                    raw_entry,
                )
                if entry is None:
                    errors.append("workflow inline permissions map is ambiguous")
                    entries = []
                    break
                entries.append((entry.group("scope"), entry.group("grant")))
            if not entries:
                continue
        else:
            errors.append("workflow permissions value is ambiguous")
            continue

        for scope, grant in entries:
            normalized_grant = grant.strip("'\"").lower()
            if normalized_grant not in WORKFLOW_PERMISSION_READ_VALUES:
                errors.append(
                    f"workflow permissions scope {scope} is not read-only"
                )
    return errors

# A workflow can still mutate its own definition (or a sibling workflow)
# while keeping the GitHub token read-only.  Keep this separate from the
# repository-evidence guards above: build/test commands may legitimately
# write under ``build/``, but no active workflow should write under
# ``.github/workflows``.  The compact form is checked as well as line-oriented
# commands so a heredoc or a line-wrapped Python/Node helper cannot bypass the
# detector by putting the path and write call on different lines.
WORKFLOW_FILE_MUTATION_RE = re.compile(
    r"(?ix)"
    r"(?:"
    r"\btee\b[^\n;&|]{0,512}\.github[\\/]workflows"
    # For copy/move/install, the workflow path must be the destination (or a
    # later destination token), not merely a source being inspected/copied out
    # of the tree.  This keeps read-only artifact collection such as
    # ``cp .github/workflows/foo.yml "$RUNNER_TEMP"`` legal.
    r"|\b(?:cp|mv|install)\b(?:[ \t]+--?[^ \t\r\n;&|]+)*"
       r"[ \t]+[^ \t\r\n;&|]+(?:[ \t]+[^ \t\r\n;&|]+)*[ \t]+\.github[\\/]workflows"
    r"|\b(?:cp|mv|install)\b[^\n;&|]*"
       r"(?:--target-directory|-t)(?:[ \t]*=[ \t]*|[ \t]+)\.github[\\/]workflows"
    r"|\b(?:sed|perl)\b[^\n;&|]{0,512}(?:-i|--in-place)[^\n;&|]{0,512}\.github[\\/]workflows"
    r"|(?:>>?|<<)[ \t]*[^\n;&|]{0,512}\.github[\\/]workflows"
    r"|\.github[\\/]workflows[\s\S]{0,512}"
       r"\b(?:write_text|write_bytes|writeFile(?:Sync)?|appendFile(?:Sync)?|"
       r"fs\.writeFile(?:Sync)?|File\.write)\s*\("
    r"|\b(?:write_text|write_bytes|writeFile(?:Sync)?|appendFile(?:Sync)?|"
       r"fs\.writeFile(?:Sync)?|File\.write)\s*\([\s\S]{0,512}"
       r"\.github[\\/]workflows"
    r"|\.github[\\/]workflows[\s\S]{0,512}\bopen\s*\([\s\S]{0,512}"
       r"(?:['\"][wax][bt+]*['\"]|mode\s*=\s*['\"][wax][bt+]*['\"])"
    r"|\bopen\s*\([\s\S]{0,512}\.github[\\/]workflows[\s\S]{0,512}"
       r"(?:['\"][wax][bt+]*['\"]|mode\s*=\s*['\"][wax][bt+]*['\"])"
    r")"
)


def _workflow_code(contents: str) -> str:
    """Return workflow text with YAML/shell comments removed.

    We do not need a complete YAML parser for this guard.  A comment begins at
    ``#`` when it is the first non-whitespace character or follows whitespace;
    ``#`` inside URLs/identifiers is retained.  Joining the resulting lines
    with newlines keeps command-boundary checks from spanning unrelated YAML
    fields while still allowing a line-wrapped ``git``/``push`` command to be
    recognized.
    """

    cleaned: list[str] = []
    for line in contents.splitlines():
        line = re.sub(r"(^|\s)#.*$", r"\1", line)
        cleaned.append(line)
    # Shell line continuations are one command.  Collapse them before the
    # command regexes so a line-wrapped ``git``/``push`` cannot bypass the write guard.
    return re.sub(r"\\\s*\n", " ", "\n".join(cleaned))


def validate_workflows(root: Path = ROOT) -> list[str]:
    """Validate that active workflow files are read-only and non-finalizing.

    This helper is intentionally independently callable so Python contract
    tests can exercise malicious fixture workflows without copying the whole
    repository or mutating the process-wide ``ROOT`` constant.
    """

    root = Path(root).resolve()
    workflows = root / ".github" / "workflows"
    if not workflows.exists():
        return []

    errors: list[str] = []
    for path in sorted(workflows.glob("*.y*ml")):
        relative = path.relative_to(root).as_posix()
        # A finalizer/self-merger is disallowed even if it happens to avoid a
        # direct write permission by delegating to another command/action.
        if re.search(
                r"(?:^|[-_])(finaliz(?:e|er|ation)?|close[-_]?gap|self[-_]?merge)(?:[-_.]|$)",
                path.name,
                re.IGNORECASE):
            errors.append(f"{relative}: finalizer/self-merge workflow is present")

        try:
            contents = _text(path)
        except (OSError, UnicodeError) as error:
            errors.append(f"{relative}: workflow is unreadable: {error}")
            continue

        code = _workflow_code(contents)
        for permission_error in _workflow_permission_errors(code):
            errors.append(f"{relative}: {permission_error}")
        for match in WORKFLOW_MUTATION_PERMISSION_RE.finditer(code):
            errors.append(
                f"{relative}: workflow has forbidden mutation: "
                f"{match.group(0).strip()}"
            )
        if WORKFLOW_WRITE_ALL_RE.search(code):
            errors.append(
                f"{relative}: workflow has forbidden mutation: "
                "permissions: write-all"
            )

        if WORKFLOW_FILE_MUTATION_RE.search(code):
            errors.append(
                f"{relative}: workflow has forbidden mutation: "
                "workflow file write"
            )

        for pattern, description in WORKFLOW_MUTATION_PATTERNS:
            if pattern.search(code):
                errors.append(
                    f"{relative}: workflow has forbidden mutation: {description}")

        # ``uses:`` actions execute third-party code with the workflow token.
        # A small deny-list catches common PR/release mutators while leaving
        # checkout/setup/upload actions available to read-only CI.
        for line in code.splitlines():
            uses_match = re.search(r"['\"]?uses['\"]?\s*:\s*", line, re.IGNORECASE)
            if uses_match:
                action = line[uses_match.end():].strip()
                if WORKFLOW_MUTATING_ACTION_RE.search(action):
                    errors.append(
                        f"{relative}: workflow uses a mutating action: {action}")

    return errors

# The compact research protocol is the active path.  Historical strategy
# scripts remain available for archaeology/experimentation, but a static
# manifest must never make them an executable dependency again: those modules
# import campaign/lease/receipt machinery that the current RunManifest path
# deliberately removed.  Keep this guard import-based (rather than a broad
# text grep) so explanatory documentation/comments in the canonical runner do
# not become false positives.
RESEARCH_FORBIDDEN_IMPORTS = frozenset({
    "hepta_strategy_shadow_runner",
    "hepta_shadow_market_history",
    "validate_hepta_strategy_decision_receipt",
    "hepta_market_context_builder",
    "hepta_eurusd_confirmed_momentum_strategy",
})
RESEARCH_SOURCE_PREFIXES = ("research/", "strategies/")
RESEARCH_SUPPORT_PREFIXES = ("research/",)
RESEARCH_FORBIDDEN_KEY_NORMALIZED = frozenset({
    "campaign",
    "campaignid",
    "campaignsha256",
    "campaignopenrequestid",
    "campaigncloserequestid",
    "finalizer",
    "finalizerreceipt",
    "finalaudit",
    "finalauditreceipt",
    "rootcustodian",
    "custodian",
    "lease",
    "leasegeneration",
    "watchlease",
    "watchgeneration",
    "previewpermit",
    "executionpermit",
    "sessiontoken",
    "sessionid",
    "sessioncredential",
    "token",
    "credential",
    "secret",
    "brokercredential",
    "brokercredentials",
    "brokersecret",
    "brokertoken",
    "brokeraccess",
    "directbrokeraccess",
    "papermutation",
    "livemutation",
    "paperauthorized",
    "liveauthorized",
    "paperauthorization",
    "liveauthorization",
    "mutationattempted",
    "promotiongrant",
    "promotionauthorization",
    "mutationcapability",
    "sessionmanagement",
    "capability",
    "campaignrenewrequestid",
    "campaignrepairrequestid",
    "campaignfinalizer",
    "finalauditreceiptsha256",
    "custodianreceipt",
    "renewal",
    "renewer",
    "repair",
    "repairreceipt",
    "closuregrade",
    "certificationreceipt",
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


def _has_active_legacy_dependency(path: Path) -> bool:
    """Detect executable/build references, not prose such as legacy/fake."""
    text = _text(path).replace("\\", "/")
    if path.suffix in CPP_SUFFIXES:
        return any(
            "legacy/" in line
            for line in text.splitlines()
            if re.match(r"^\s*#\s*include\s*[<\"]", line)
        )

    if path.name == "CMakeLists.txt" or path.suffix in BUILD_SUFFIXES:
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            if re.search(r"(?:^|[\s\"'({=;/])(?:\.\./|\./)*legacy/", code):
                return True
        return False

    # Runtime/deployment references are meaningful only as a path token. This
    # avoids treating ordinary prose like "legacy/fake wrappers" as a build
    # dependency while still catching command/config paths.
    for line in text.splitlines():
        code = line.split("#", 1)[0]
        if re.search(r"(?:^|[\s\"'({=])(?:\.\./|\./|/)*legacy/", code):
            return True
    return False


def _document_paths() -> list[Path]:
    """Return current/proposal/index Markdown without vendor payload notes.

    Vendor overlays under ``legacy/vendor`` are historical import notes and are
    deliberately outside the active documentation graph. Every other checked-
    in Markdown file is either a current contract, an index, or a proposal and
    must carry the three-line metadata header used by ``docs/README.md``.
    """
    result: list[Path] = []
    for path in ROOT.rglob("*.md"):
        relative = path.relative_to(ROOT)
        if ".git" in relative.parts or "build" in relative.parts:
            continue
        if relative.parts[:2] == ("legacy", "vendor"):
            continue
        if path.is_file():
            result.append(path)
    return result


def _check_document_metadata(errors: list[str]) -> None:
    for path in _document_paths():
        lines = path.read_text(encoding="utf-8-sig").splitlines()[:12]
        missing = [
            field for field in DOCUMENT_METADATA_FIELDS
            if not any(line.startswith(field) and line[len(field):].strip()
                       for line in lines)
        ]
        if missing:
            relative = path.relative_to(ROOT)
            errors.append(
                f"{relative}: missing document metadata: {', '.join(missing)}")


def _research_imports(path: Path) -> set[str] | None:
    """Return top-level Python imports, or ``None`` for invalid source."""

    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError, RecursionError):
        return None
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
    return imports


def _research_manifest_keys(value: object, path: str = "manifest") -> list[str]:
    """Find forbidden ceremony/capability keys in a static JSON descriptor."""

    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            if normalized in RESEARCH_FORBIDDEN_KEY_NORMALIZED:
                found.append(f"{path}.{key}")
            found.extend(_research_manifest_keys(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_research_manifest_keys(child, f"{path}[{index}]"))
    return found


def _check_research_current_path(manifest: object, errors: list[str]) -> None:
    """Ensure manifest-referenced research assets are capability-free.

    This intentionally checks only assets named by the static contract.  Other
    large scripts under ``scripts/`` are retained as historical inputs and do
    not become current merely by existing in the checkout.
    """

    if not isinstance(manifest, dict):
        return
    strategy = manifest.get("strategy")
    if not isinstance(strategy, dict):
        return
    strategy_digests = manifest.get("strategy_digests")
    expected_digest_fields = {
        "definition", "implementation", "context_builder", "replay_evaluator"
    }
    if (
        not isinstance(strategy_digests, dict)
        or set(strategy_digests) != expected_digest_fields
        or any(
            not isinstance(value, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", value)
            for value in strategy_digests.values()
        )
    ):
        errors.append("research manifest strategy digests are missing or invalid")
        strategy_digests = {}
    for field in ("definition", "implementation", "context_builder", "replay_evaluator"):
        value = strategy.get(field)
        if not isinstance(value, str):
            continue
        windows_path = PureWindowsPath(value)
        relative_parts = re.split(r"[\\/]", value)
        if (
            not value
            or "\x00" in value
            or Path(value).is_absolute()
            or windows_path.is_absolute()
            or bool(windows_path.drive)
            or any(part == ".." for part in relative_parts)
        ):
            errors.append(
                f"research manifest strategy.{field} has unsafe path: {value}"
            )
            continue
        normalized_value = value.replace("\\", "/")
        if not any(normalized_value.startswith(prefix) for prefix in RESEARCH_SOURCE_PREFIXES):
            errors.append(
                f"research manifest strategy.{field} is outside current source roots: {value}"
            )
            continue
        path = (ROOT / value).resolve()
        try:
            path.relative_to(ROOT)
        except ValueError:
            continue
        if not path.is_file():
            continue
        expected_digest = strategy_digests.get(field)
        if expected_digest is not None:
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except (OSError, UnicodeError) as error:
                errors.append(
                    f"research manifest strategy.{field} unreadable: {error}"
                )
            else:
                actual_digest = f"sha256:{digest}"
                if actual_digest != expected_digest:
                    errors.append(
                        f"research manifest strategy.{field} digest mismatch"
                    )
        if path.suffix == ".py":
            imports = _research_imports(path)
            if imports is None:
                errors.append(f"research manifest strategy.{field} is invalid Python: {value}")
                continue
            forbidden = sorted(imports & RESEARCH_FORBIDDEN_IMPORTS)
            if forbidden:
                errors.append(
                    f"research manifest strategy.{field} imports legacy ceremony: "
                    f"{', '.join(forbidden)}"
                )
        elif path.suffix == ".json":
            try:
                descriptor = json.loads(_text(path))
            except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
                errors.append(f"research manifest strategy.{field} invalid JSON: {error}")
                continue
            if not isinstance(descriptor, dict):
                errors.append(
                    f"research manifest strategy.{field} must contain a JSON object"
                )
                continue
            forbidden_keys = _research_manifest_keys(descriptor)
            if forbidden_keys:
                errors.append(
                    f"research manifest strategy.{field} has forbidden fields: "
                    f"{', '.join(forbidden_keys)}"
                )
    support = manifest.get("runner_support")
    if (
        not isinstance(support, dict)
        or set(support) != {"path", "sha256"}
        or not isinstance(support.get("sha256"), str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", support.get("sha256", ""))
    ):
        errors.append("research manifest runner_support is missing or invalid")
        return
    value = support["path"]
    if not isinstance(value, str):
        errors.append("research manifest runner_support.path is not a string")
        return
    windows_path = PureWindowsPath(value)
    relative_parts = re.split(r"[\\/]", value)
    if (
        not value
        or "\x00" in value
        or Path(value).is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or any(part == ".." for part in relative_parts)
    ):
        errors.append(f"research manifest runner_support has unsafe path: {value}")
        return
    normalized_value = value.replace("\\", "/")
    if not any(normalized_value.startswith(prefix) for prefix in RESEARCH_SUPPORT_PREFIXES):
        errors.append(
            f"research manifest runner_support is outside current source roots: {value}"
        )
        return
    path = (ROOT / value).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError:
        errors.append(f"research manifest runner_support escapes repository: {value}")
        return
    if not path.is_file():
        errors.append(f"research manifest has missing runner_support.path: {value}")
        return
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, UnicodeError) as error:
        errors.append(f"research manifest runner_support unreadable: {error}")
    else:
        if f"sha256:{digest}" != support["sha256"]:
            errors.append("research manifest runner_support digest mismatch")
    imports = _research_imports(path)
    if imports is None:
        errors.append(f"research manifest runner_support is invalid Python: {value}")
    else:
        forbidden = sorted(imports & RESEARCH_FORBIDDEN_IMPORTS)
        if forbidden:
            errors.append(
                "research manifest runner_support imports legacy ceremony: "
                f"{', '.join(forbidden)}"
            )


def validate() -> list[str]:
    errors: list[str] = []

    _check_document_metadata(errors)

    for relative in REQUIRED_FILES:
        if not (ROOT / relative).is_file():
            errors.append(f"required file is missing: {relative}")

    for relative in REMOVED_ACTIVE_PATHS:
        if (ROOT / relative).exists():
            errors.append(f"inactive monolith surface remains active: {relative}")

    # Check links in every current/index/proposal document, not only the three
    # top-level indexes.  This keeps a stale local command or contract link
    # from silently becoming a second, contradictory product path.
    for path in _document_paths():
        _check_markdown_links(path.relative_to(ROOT).as_posix(), errors)

    for relative in ("CMakeLists.txt", "CMakePresets.json", "scripts/dev_core.sh"):
        path = ROOT / relative
        if not path.is_file():
            continue
        contents = _text(path)
        for token in STALE_BUILD_TOKENS:
            if token in contents:
                errors.append(f"{relative}: stale legacy build token: {token}")

    for path in _active_text_files():
        if _has_active_legacy_dependency(path):
            errors.append(
                f"{path.relative_to(ROOT)}: active runtime depends on legacy/")

    workflows = ROOT / ".github" / "workflows"
    if workflows.exists():
        # Keep the historical temporary-file check as a distinct diagnostic;
        # ``validate_workflows`` handles all permanent mutation/finalizer
        # capabilities shared by current and fixture trees.
        for path in workflows.glob("dev-*.y*ml"):
            errors.append(
                f"{path.relative_to(ROOT)}: temporary dev workflow remains")
        errors.extend(validate_workflows(ROOT))

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
            _check_research_current_path(manifest, errors)

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
