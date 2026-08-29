#!/usr/bin/env python3
"""Enforce no-growth budgets for Native Agent OS source structure."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import subprocess
import tarfile
from typing import Any


POLICY_SCHEMA = "hepta.code-quality-policy.v1"
REPORT_SCHEMA = "hepta.code-quality-report.v1"
CPP_REFERENCE = re.compile(r"[A-Za-z0-9_./${}-]+\.cpp")
INCLUDE_DIRECTIVE = re.compile(
    r'^\s*#\s*include\s*"(?P<path>[^"\r\n]+)"', re.MULTILINE)
CONTROL_NAME = {"if", "for", "while", "switch", "catch"}
BRANCH_KEYWORD = re.compile(r"\b(?:if|for|while|case|catch)\b")
COGNITIVE_TOKEN = re.compile(
    r"\b(?:if|for|while|switch|case|catch)\b|&&|\|\||[{}]")
FUNCTION_NAME = re.compile(
    r"(?P<name>(?:[A-Za-z_]\w*::)*(?:~?[A-Za-z_]\w*|operator\s*[^\s(]+))\s*\(")
MAX_CI_ARCHIVE_INPUT_BYTES = 16 * 1024 * 1024


class CodeQualityError(RuntimeError):
    pass


def _coverage_toolchain_policy(value: Any) -> dict[str, Any]:
    expected_distributions = (
        ("colorlog", "6.12.0"),
        ("gcovr", "7.2"),
        ("Jinja2", "3.1.6"),
        ("lxml", "6.1.1"),
        ("MarkupSafe", "3.0.3"),
        ("Pygments", "2.20.0"),
    )
    fields = {
        "schema", "version", "provisioned", "runner_labels",
        "python", "gcov", "immutable_tool_root_receipt",
        "distributions",
    }
    executable_fields = {
        "configured_path", "realpath", "sha256", "size", "mode",
    }
    distribution_fields = {
        "name", "version", "root", "file_count", "files_sha256",
    }
    if (not isinstance(value, dict) or set(value) != fields or
            value["schema"] != "hepta.coverage-toolchain.v1" or
            value["version"] != 1 or
            type(value["provisioned"]) is not bool or
            value["runner_labels"] != [
                "self-hosted", "linux", "x64",
                "heptatrader-coverage-v1",
            ] or
            any(not isinstance(value[label], dict) or
                set(value[label]) != executable_fields
                for label in (
                    "python", "gcov",
                    "immutable_tool_root_receipt")) or
            not isinstance(value["distributions"], list) or
            len(value["distributions"]) != len(expected_distributions)):
        raise CodeQualityError("coverage toolchain policy is invalid")
    for label in ("python", "gcov", "immutable_tool_root_receipt"):
        configured = value[label]["configured_path"]
        if (not isinstance(configured, str) or not configured or
                "\0" in configured or not Path(configured).is_absolute()):
            raise CodeQualityError(
                f"coverage {label} policy path is invalid")
    for record, (name, version) in zip(
            value["distributions"], expected_distributions, strict=True):
        if (not isinstance(record, dict) or
                set(record) != distribution_fields or
                record["name"] != name or record["version"] != version):
            raise CodeQualityError(
                "coverage distribution policy is invalid")
    if not value["provisioned"]:
        if (any(value[label][field] is not None
                for label in (
                    "python", "gcov",
                    "immutable_tool_root_receipt")
                for field in ("realpath", "sha256", "size", "mode")) or
                any(record[field] is not None
                    for record in value["distributions"]
                    for field in ("root", "file_count", "files_sha256"))):
            raise CodeQualityError(
                "unprovisioned coverage toolchain claims an identity")
        return value
    for label in ("python", "gcov"):
        record = value[label]
        if (not isinstance(record["realpath"], str) or
                not Path(record["realpath"]).is_absolute() or
                not isinstance(record["sha256"], str) or
                re.fullmatch(r"[0-9a-f]{64}", record["sha256"]) is None or
                type(record["size"]) is not int or record["size"] <= 0 or
                not isinstance(record["mode"], str) or
                re.fullmatch(r"0[0-7]{3}", record["mode"]) is None or
                int(record["mode"], 8) & 0o022 or
                int(record["mode"], 8) & 0o7000 or
                not int(record["mode"], 8) & 0o111):
            raise CodeQualityError(
                f"coverage {label} identity is invalid")
    receipt = value["immutable_tool_root_receipt"]
    if (not isinstance(receipt["realpath"], str) or
            not Path(receipt["realpath"]).is_absolute() or
            not isinstance(receipt["sha256"], str) or
            re.fullmatch(r"[0-9a-f]{64}", receipt["sha256"]) is None or
            type(receipt["size"]) is not int or receipt["size"] <= 0 or
            not isinstance(receipt["mode"], str) or
            re.fullmatch(r"0[0-7]{3}", receipt["mode"]) is None or
            int(receipt["mode"], 8) & 0o022 or
            int(receipt["mode"], 8) & 0o7000):
        raise CodeQualityError(
            "coverage immutable tool-root receipt identity is invalid")
    for record in value["distributions"]:
        if (not isinstance(record["root"], str) or
                not Path(record["root"]).is_absolute() or
                type(record["file_count"]) is not int or
                record["file_count"] <= 0 or
                not isinstance(record["files_sha256"], str) or
                re.fullmatch(
                    r"[0-9a-f]{64}", record["files_sha256"]) is None):
            raise CodeQualityError(
                "coverage distribution identity is invalid")
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _protected_bytes(
        path: Path, label: str, *, allow_group_write: bool = False) -> bytes:
    try:
        before = path.lstat()
    except OSError as error:
        raise CodeQualityError(f"{label} is unavailable") from error
    forbidden_write_bits = 0o002 if allow_group_write else 0o022
    if (stat.S_ISLNK(before.st_mode) or
            not stat.S_ISREG(before.st_mode) or
            before.st_nlink != 1 or before.st_mode & forbidden_write_bits):
        raise CodeQualityError(f"{label} must be a protected regular file")
    data = path.read_bytes()
    after = path.lstat()
    identity = lambda value: (
        value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
        value.st_uid, value.st_gid, value.st_size, value.st_mtime_ns,
        value.st_ctime_ns,
    )
    if identity(before) != identity(after):
        raise CodeQualityError(f"{label} changed while reading")
    return data


def _policy(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(
            _protected_bytes(path, "quality policy").decode(
                "utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise CodeQualityError(
            "quality policy is not strict UTF-8 JSON") from error
    if not isinstance(document, dict) or set(document) != {
            "schema", "version", "native_line_budgets",
            "native_structure_budgets", "include_graph", "cmake",
            "coverage", "ci_archive"}:
        raise CodeQualityError(
            "quality policy fields do not exactly match schema")
    if document["schema"] != POLICY_SCHEMA or document["version"] != 1:
        raise CodeQualityError("unsupported quality policy")
    budgets = document["native_line_budgets"]
    if (not isinstance(budgets, dict) or not budgets or
            any(not isinstance(path, str) or
                not isinstance(limit, int) or limit < 1
                for path, limit in budgets.items())):
        raise CodeQualityError("native line budgets are invalid")
    structure = document["native_structure_budgets"]
    structure_fields = {
        "maximum_function_lines",
        "maximum_cyclomatic_complexity",
        "maximum_cognitive_complexity",
    }
    if (not isinstance(structure, dict) or not structure or
            set(structure) != set(budgets)):
        raise CodeQualityError(
            "native structure budgets must match native line budget paths")
    for relative, limits in structure.items():
        if (not isinstance(relative, str) or
                not isinstance(limits, dict) or
                set(limits) != structure_fields or
                any(not isinstance(limits[field], int) or limits[field] < 1
                    for field in structure_fields)):
            raise CodeQualityError(
                f"native structure budget is invalid: {relative}")
    include_graph = document["include_graph"]
    if (not isinstance(include_graph, dict) or set(include_graph) != {
            "maximum_edges", "maximum_local_fan_in",
            "maximum_local_fan_out"} or
            any(not isinstance(include_graph[field], int) or
                include_graph[field] < 1
                for field in include_graph)):
        raise CodeQualityError("include graph policy is invalid")
    cmake = document["cmake"]
    if (not isinstance(cmake, dict) or set(cmake) != {
            "files", "forbidden_source_discovery",
            "maximum_cpp_references"} or
            not isinstance(cmake["files"], list) or
            not isinstance(cmake["forbidden_source_discovery"], list) or
            not isinstance(cmake["maximum_cpp_references"], int)):
        raise CodeQualityError("CMake quality policy is invalid")
    coverage = document["coverage"]
    if (not isinstance(coverage, dict) or set(coverage) != {
            "line_minimum_percent", "toolchain"} or
            not isinstance(coverage["line_minimum_percent"], int) or
            not 0 <= coverage["line_minimum_percent"] <= 100):
        raise CodeQualityError("coverage policy is invalid")
    _coverage_toolchain_policy(coverage["toolchain"])
    ci_archive = document["ci_archive"]
    if (not isinstance(ci_archive, dict) or set(ci_archive) != {
            "actionlint_config", "canonical_forbidden_fragments",
            "checkout_contract",
            "require_action_sha_pinning",
            "revision", "workflows",
            "supplemental_workflows",
            "supplemental_workflow_sha256",
            "member_manifests", "path_prefixes",
            "root_executable_suffixes"} or
            ci_archive["require_action_sha_pinning"] is not True or
            ci_archive["actionlint_config"] !=
            ".github/actionlint.yaml" or
            ci_archive["revision"] != "HEAD" or
            not isinstance(ci_archive["workflows"], list) or
            not ci_archive["workflows"] or
            not isinstance(ci_archive["supplemental_workflows"], list) or
            not ci_archive["supplemental_workflows"] or
            not isinstance(
                ci_archive["supplemental_workflow_sha256"], dict) or
            set(ci_archive["supplemental_workflow_sha256"]) !=
            set(ci_archive["supplemental_workflows"]) or
            any(re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in
                ci_archive["supplemental_workflow_sha256"].values()) or
            not isinstance(ci_archive["member_manifests"], list) or
            not ci_archive["member_manifests"] or
            not isinstance(ci_archive["path_prefixes"], list) or
            not ci_archive["path_prefixes"] or
            not isinstance(ci_archive["root_executable_suffixes"], list) or
            not ci_archive["root_executable_suffixes"] or
            not isinstance(
                ci_archive["canonical_forbidden_fragments"], list) or
            not ci_archive["canonical_forbidden_fragments"] or
            any(not isinstance(value, str) or not value
                for field in (
                    "workflows", "supplemental_workflows",
                    "member_manifests", "path_prefixes",
                    "root_executable_suffixes",
                    "canonical_forbidden_fragments")
                for value in ci_archive[field])):
        raise CodeQualityError("CI archive policy is invalid")
    checkout = ci_archive["checkout_contract"]
    if (not isinstance(checkout, dict) or set(checkout) != {
            "action", "expected_count", "fetch_depth", "ref"} or
            checkout["action"] !=
                "actions/checkout@11d5960a326750d5838078e36cf38b85af677262" or
            type(checkout["expected_count"]) is not int or
            checkout["expected_count"] < 1 or
            checkout["fetch_depth"] != 2 or
            checkout["ref"] !=
            "${{ github.event.pull_request.head.sha || github.sha }}"):
        raise CodeQualityError("CI checkout contract is invalid")
    return document


def _line_count(data: bytes, label: str) -> int:
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise CodeQualityError(f"{label} is not UTF-8") from error
    return len(text.splitlines())


def _cpp_without_comments_or_literals(text: str) -> str:
    """Replace comments and literals with spaces while preserving newlines."""
    output: list[str] = []
    index = 0
    state = "normal"
    quote = ""
    while index < len(text):
        current = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if state == "normal":
            if current == "/" and following == "/":
                output.extend((" ", " "))
                index += 2
                state = "line-comment"
                continue
            if current == "/" and following == "*":
                output.extend((" ", " "))
                index += 2
                state = "block-comment"
                continue
            if current in {'"', "'"}:
                output.append(" ")
                quote = current
                state = "literal"
            else:
                output.append(current)
        elif state == "line-comment":
            if current == "\n":
                output.append("\n")
                state = "normal"
            else:
                output.append(" ")
        elif state == "block-comment":
            if current == "*" and following == "/":
                output.extend((" ", " "))
                index += 2
                state = "normal"
                continue
            output.append("\n" if current == "\n" else " ")
        else:
            if current == "\\" and following:
                output.append(" ")
                output.append("\n" if following == "\n" else " ")
                index += 2
                continue
            if current == quote:
                output.append(" ")
                state = "normal"
            else:
                output.append("\n" if current == "\n" else " ")
        index += 1
    return "".join(output)


def _cpp_without_preprocessor_directives(text: str) -> str:
    """Replace preprocessor directives with spaces, preserving line numbers.

    The lexical function scanner must not interpret a function-like macro (or
    ``defined(...)`` in an ``#if`` expression) as a C++ function definition.
    Keep every newline and blank continued directive lines so the reported
    source locations remain stable.  This is deliberately a lexical filter;
    it does not attempt to evaluate conditional-compilation expressions.
    """
    output: list[str] = []
    continued = False
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        directive = continued or re.match(r"^[ \t]*#", line) is not None
        if directive:
            output.append("".join(
                "\n" if character == "\n" else
                "\r" if character == "\r" else " "
                for character in line))
            continued = body.rstrip(" \t").endswith("\\")
        else:
            output.append(line)
            continued = False
    return "".join(output)


def _brace_pairs(text: str) -> dict[int, int]:
    stack: list[int] = []
    pairs: dict[int, int] = {}
    for index, character in enumerate(text):
        if character == "{":
            stack.append(index)
        elif character == "}" and stack:
            pairs[stack.pop()] = index
    return pairs


def _function_regions(text: str) -> list[dict[str, Any]]:
    """Return deterministic, conservative C++ function approximations.

    This is intentionally a lexical no-growth gate, not a C++ parser. It only
    recognizes brace-delimited definitions with a function-like header and
    ignores control blocks and lambdas.
    """
    clean = _cpp_without_preprocessor_directives(
        _cpp_without_comments_or_literals(text))
    pairs = _brace_pairs(clean)
    regions: list[dict[str, Any]] = []
    delimiters = ";{}"
    for opening, closing in sorted(pairs.items()):
        start = max(clean.rfind(delimiter, 0, opening)
                    for delimiter in delimiters) + 1
        header = clean[start:opening].strip()
        if not header or "(" not in header or ")" not in header:
            continue
        matches = list(FUNCTION_NAME.finditer(header))
        if not matches:
            continue
        match = matches[0]
        name = match.group("name").split("::")[-1].strip()
        if name in CONTROL_NAME:
            continue
        before_parameters = header[:match.start()]
        if "[" in before_parameters or "=" in before_parameters:
            continue
        start_line = clean.count("\n", 0, start) + 1
        while (start_line < clean.count("\n", 0, opening) + 1 and
               not clean.splitlines()[start_line - 1].strip()):
            start_line += 1
        end_line = clean.count("\n", 0, closing) + 1
        body = clean[opening:closing + 1]
        cyclomatic = (
            1 + len(BRANCH_KEYWORD.findall(body)) +
            body.count("&&") + body.count("||") + body.count("?"))
        cognitive = 0
        depth = 0
        for token in COGNITIVE_TOKEN.finditer(body):
            value = token.group(0)
            if value == "{":
                depth += 1
            elif value == "}":
                depth = max(0, depth - 1)
            elif value in {"&&", "||", "case"}:
                cognitive += 1
            else:
                cognitive += 1 + max(0, depth - 1)
        regions.append({
            "name": name,
            "start_line": start_line,
            "end_line": end_line,
            "lines": end_line - start_line + 1,
            "cyclomatic_complexity": cyclomatic,
            "cognitive_complexity": cognitive,
        })
    return regions


def _resolve_local_include(
        root: Path, source: Path, include: str) -> str | None:
    candidates = (source.parent / include, root / include)
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
            relative = resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        if resolved.is_file():
            return relative.as_posix()
    return None


def _structural_metrics(
        root: Path, paths: list[str]) -> tuple[
            dict[str, dict[str, Any]], dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    edges: set[tuple[str, str]] = set()
    fan_in: dict[str, int] = {}
    for relative in sorted(paths):
        data = _protected_bytes(
            root / relative, relative, allow_group_write=True)
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise CodeQualityError(f"{relative} is not UTF-8") from error
        functions = _function_regions(text)
        maximum_lines = max(
            functions, key=lambda item: item["lines"], default=None)
        maximum_cyclomatic = max(
            functions,
            key=lambda item: item["cyclomatic_complexity"], default=None)
        maximum_cognitive = max(
            functions,
            key=lambda item: item["cognitive_complexity"], default=None)
        local_targets: set[str] = set()
        for match in INCLUDE_DIRECTIVE.finditer(text):
            target = _resolve_local_include(
                root, root / relative, match.group("path"))
            if target is not None:
                local_targets.add(target)
                edges.add((relative, target))
        for target in local_targets:
            fan_in[target] = fan_in.get(target, 0) + 1
        metrics[relative] = {
            "function_count": len(functions),
            "maximum_function": maximum_lines,
            "maximum_cyclomatic_function": maximum_cyclomatic,
            "maximum_cognitive_function": maximum_cognitive,
            "local_include_fan_out": len(local_targets),
        }
    fan_out = {
        relative: metrics[relative]["local_include_fan_out"]
        for relative in sorted(metrics)
    }
    graph = {
        "edge_count": len(edges),
        "maximum_local_fan_out": max(fan_out.values(), default=0),
        "maximum_local_fan_in": max(fan_in.values(), default=0),
        "fan_out": fan_out,
        "fan_in": dict(sorted(fan_in.items())),
    }
    return metrics, graph


def _ci_local_references(
        workflow_text: str, path_prefixes: list[str],
        root_suffixes: list[str]) -> list[str]:
    prefix_expression = "|".join(
        re.escape(prefix.rstrip("/")) for prefix in path_prefixes)
    suffix_expression = "|".join(
        re.escape(suffix.lstrip(".")) for suffix in root_suffixes)
    shell_variable_prefix = (
        r"(?:\$(?:[A-Za-z_][A-Za-z0-9_]*|"
        r"\{[A-Za-z_][A-Za-z0-9_]*\})/)?")
    prefixed = re.compile(
        rf"(?<![A-Za-z0-9_./\\-]){shell_variable_prefix}(?:\./)?"
        rf"(?P<path>(?:{prefix_expression})/"
        rf"[-A-Za-z0-9_./${{}}]+\.(?:py|ps1|sh|json|yml|yaml))"
        rf"(?![A-Za-z0-9_.-])", re.IGNORECASE)
    root_executable = re.compile(
        rf"(?<![A-Za-z0-9_./\\-])(?:\./)?"
        rf"(?P<path>[A-Za-z0-9_.-]+\.(?:{suffix_expression}))"
        rf"(?![A-Za-z0-9_.-])", re.IGNORECASE)
    sources = {
        workflow_text,
        workflow_text.replace("\\", "/"),
    }
    references = {
        match.group("path").removeprefix("./")
        for source in sources
        for pattern in (prefixed, root_executable)
        for match in pattern.finditer(source)
    }
    return sorted(references)


def _yaml_code_without_comment(line: str) -> str:
    quote = ""
    index = 0
    while index < len(line):
        character = line[index]
        if quote == "'":
            if character == "'" and index + 1 < len(line):
                if line[index + 1] == "'":
                    index += 2
                    continue
            if character == "'":
                quote = ""
        elif quote == '"':
            if character == "\\":
                index += 2
                continue
            if character == '"':
                quote = ""
        elif character in {"'", '"'}:
            quote = character
        elif (character == "#" and
              (index == 0 or line[index - 1].isspace())):
            return line[:index].rstrip()
        index += 1
    return line.rstrip()


def _yaml_mapping_keys(code: str) -> list[str]:
    key_pattern = re.compile(
        r"(?:^|[{,])\s*(?:-\s+|\?\s+)?"
        r"(?P<key>\"(?:\\.|[^\"\\])*\"|'(?:''|[^'])*'|"
        r"[A-Za-z_][A-Za-z0-9_-]*)\s*:")
    keys: list[str] = []
    for match in key_pattern.finditer(code):
        token = match.group("key")
        if token.startswith('"'):
            try:
                key = json.loads(token)
            except (json.JSONDecodeError, ValueError):
                continue
        elif token.startswith("'"):
            key = token[1:-1].replace("''", "'")
        else:
            key = token
        if isinstance(key, str):
            keys.append(key)
    explicit = re.fullmatch(
        r"\s*\?\s*(?P<key>\"(?:\\.|[^\"\\])*\"|'(?:''|[^'])*'|"
        r"[A-Za-z_][A-Za-z0-9_-]*)\s*", code)
    if explicit is not None:
        token = explicit.group("key")
        if token.startswith('"'):
            try:
                key = json.loads(token)
            except (json.JSONDecodeError, ValueError):
                key = None
        elif token.startswith("'"):
            key = token[1:-1].replace("''", "'")
        else:
            key = token
        if isinstance(key, str):
            keys.append(key)
    return keys


def _yaml_outside_strict_mapping_subset(code: str) -> bool:
    structural = code.lstrip(" ")
    if structural.startswith("- "):
        structural = structural[2:].lstrip(" ")
    without_expressions = structural
    while (start := without_expressions.find("${{")) != -1:
        end = without_expressions.find("}}", start + 3)
        if end == -1:
            return True
        without_expressions = (
            without_expressions[:start] +
            without_expressions[end + 2:])
    return (
        without_expressions.startswith(
            ("?", '"', "'", "!", "&", "*", "{", "<<:")) or
        "{" in without_expressions or "}" in without_expressions)


def _ci_workflow_uses(workflow_text: str) -> dict[str, Any]:
    action_reference = (
        r"(?:\./[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*|"
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
        r"(?:/[A-Za-z0-9_.-]+)*@[A-Za-z0-9_./-]+)")
    canonical = re.compile(
        rf"^(?P<indent> +)uses: (?P<action>{action_reference})$")
    step_name = re.compile(
        r"^(?P<indent> *)- name: (?P<name>\S(?:.*\S)?)$")
    block_scalar = re.compile(
        r".*:\s*[|>](?:[1-9][+-]?|[+-][1-9]?)?\s*$")
    lines = workflow_text.splitlines()
    records: list[dict[str, Any]] = []
    violations: list[str] = []
    active_steps: dict[int, dict[str, Any]] = {}
    scalar_indentation: int | None = None
    for index, line in enumerate(lines):
        indentation = len(line) - len(line.lstrip(" "))
        if scalar_indentation is not None:
            if not line.strip():
                continue
            if indentation > scalar_indentation:
                continue
            scalar_indentation = None
        code = _yaml_code_without_comment(line)
        if not code.strip():
            continue
        indentation = len(code) - len(code.lstrip(" "))
        if _yaml_outside_strict_mapping_subset(code):
            violations.append(
                f"line {index + 1}: workflow mapping syntax is outside "
                "the strict subset")
        for key_indentation in list(active_steps):
            if indentation <= key_indentation - 2:
                del active_steps[key_indentation]
        if (match := step_name.fullmatch(code)) is not None:
            key_indentation = len(match.group("indent")) + 2
            active_steps[key_indentation] = {
                "line": index + 1,
                "uses_seen": False,
            }
        match = canonical.fullmatch(code)
        uses_like = "uses" in _yaml_mapping_keys(code)
        if match is not None:
            action = match.group("action")
            action_parts = action.removeprefix("./").split("/")
            step = active_steps.get(len(match.group("indent")))
            if (action.startswith("./") and
                    any(part in {"", ".", ".."} for part in action_parts)):
                violations.append(
                    f"line {index + 1}: local uses path is not canonical")
            elif step is None:
                violations.append(
                    f"line {index + 1}: uses is not in a named step")
            elif step["uses_seen"]:
                violations.append(
                    f"line {index + 1}: named step has duplicate uses")
            else:
                step["uses_seen"] = True
                records.append({
                    "action": action,
                    "indent": len(match.group("indent")),
                    "line": index + 1,
                    "line_index": index,
                })
        elif uses_like:
            violations.append(
                f"line {index + 1}: uses mapping is not canonical")
        if block_scalar.fullmatch(code) is not None:
            scalar_indentation = indentation
    return {
        "records": records,
        "violations": violations,
    }


def _ci_checkout_options(
        lines: list[str], record: dict[str, Any],
        contract: dict[str, Any]) -> dict[str, Any]:
    key_indentation = record["indent"]
    end = record["line_index"] + 1
    while end < len(lines):
        code = _yaml_code_without_comment(lines[end])
        if code.strip():
            indentation = len(code) - len(code.lstrip(" "))
            if indentation <= key_indentation - 2:
                break
        end += 1
    with_indexes = [
        index for index in range(record["line_index"] + 1, end)
        if _yaml_code_without_comment(lines[index]) ==
        " " * key_indentation + "with:"
    ]
    exact = {
        "fetch_depth": (
            " " * (key_indentation + 2) +
            f"fetch-depth: {contract['fetch_depth']}"),
        "ref": (
            " " * (key_indentation + 2) +
            f"ref: {contract['ref']}"),
        "persist_credentials": (
            " " * (key_indentation + 2) +
            "persist-credentials: false"),
    }
    counts = {key: 0 for key in exact}
    violations: list[str] = []
    protected_keys = {
        "fetch-depth", "ref", "persist-credentials",
    }
    for with_index in with_indexes:
        index = with_index + 1
        while index < end:
            code = _yaml_code_without_comment(lines[index])
            if code.strip():
                indentation = len(code) - len(code.lstrip(" "))
                if indentation <= key_indentation:
                    break
                if indentation == key_indentation + 2:
                    mapping_keys = set(_yaml_mapping_keys(code))
                    observed_keys = protected_keys.intersection(mapping_keys)
                    for key, expected_line in exact.items():
                        if code == expected_line:
                            counts[key] += 1
                    if mapping_keys.difference(protected_keys):
                        violations.append(
                            f"line {index + 1}: checkout option is outside "
                            "the exact contract")
                    if (observed_keys and
                            code not in exact.values()):
                        violations.append(
                            f"line {index + 1}: checkout option is not "
                            "canonical")
            index += 1
    return {
        "with_count": len(with_indexes),
        "fetch_depth_count": counts["fetch_depth"],
        "ref_count": counts["ref"],
        "persist_credentials_count": counts["persist_credentials"],
        "violations": violations,
        "passed": (
            len(with_indexes) == 1 and
            all(count == 1 for count in counts.values()) and
            not violations),
    }


def _ci_checkout_contract(
        workflow_text: str, contract: dict[str, Any]) -> dict[str, Any]:
    action = contract["action"]
    parsed = _ci_workflow_uses(workflow_text)
    lines = workflow_text.splitlines()
    matches = [
        record for record in parsed["records"]
        if record["action"] == action
    ]
    options = [
        _ci_checkout_options(lines, record, contract)
        for record in matches
    ]
    expected = contract["expected_count"]
    fetch_depth_count = sum(
        item["fetch_depth_count"] for item in options)
    pull_request_head_ref_count = sum(
        item["ref_count"] for item in options)
    persist_credentials_false_count = sum(
        item["persist_credentials_count"] for item in options)
    return {
        "action": action,
        "expected_count": expected,
        "checkout_count": len(matches),
        "with_count": sum(item["with_count"] for item in options),
        "fetch_depth": contract["fetch_depth"],
        "fetch_depth_count": fetch_depth_count,
        "ref": contract["ref"],
        "pull_request_head_ref_count": pull_request_head_ref_count,
        "persist_credentials": False,
        "persist_credentials_false_count":
            persist_credentials_false_count,
        "uses_violations": parsed["violations"],
        "option_violations": [
            violation for item in options
            for violation in item["violations"]
        ],
        "passed": (
            not parsed["violations"] and
            len(matches) == expected and
            len(options) == expected and
            all(item["passed"] for item in options) and
            fetch_depth_count == expected and
            pull_request_head_ref_count == expected and
            persist_credentials_false_count == expected),
    }


def _ci_action_pinning(workflow_text: str) -> dict[str, Any]:
    parsed = _ci_workflow_uses(workflow_text)
    sha_pinned_pattern = re.compile(
        r"^[^/@\s]+/[^/@\s]+(?:/[^/@\s]+)*@[0-9a-fA-F]{40}$")
    actions = [
        record["action"] for record in parsed["records"]
        if not record["action"].startswith("./")
    ]
    local_actions = [
        record["action"] for record in parsed["records"]
        if record["action"].startswith("./")
    ]
    unpinned = sorted({
        action for action in actions
        if sha_pinned_pattern.fullmatch(action) is None
    })
    return {
        "external_action_count": len(actions),
        "local_action_count": len(local_actions),
        "unbound_local_uses": sorted(set(local_actions)),
        "unpinned_actions": unpinned,
        "uses_violations": parsed["violations"],
        "passed": (
            not unpinned and not local_actions and
            not parsed["violations"]),
    }


def _ci_product_boundary(
        workflow_text: str, forbidden_fragments: list[str]) -> dict[str, Any]:
    normalize = lambda value: re.sub(
        r"[\\/]+", "/", value.casefold())
    normalized_workflow = normalize(workflow_text)
    observed = sorted({
        fragment for fragment in forbidden_fragments
        if normalize(fragment) in normalized_workflow
    })
    return {
        "forbidden_fragments": observed,
        "passed": not observed,
    }


def _ci_manual_quarantine(
        workflow_text: str, expected_sha256: str) -> dict[str, Any]:
    lines = workflow_text.splitlines()
    event_syntax_violations: list[str] = []
    try:
        start = lines.index("on:") + 1
    except ValueError:
        events: list[str] = []
        event_syntax_violations.append(
            "top-level on mapping is absent or noncanonical")
    else:
        end = start
        while end < len(lines):
            line = lines[end]
            if line and not line.startswith((" ", "\t")):
                break
            end += 1
        observed_events: set[str] = set()
        for index in range(start, end):
            code = _yaml_code_without_comment(lines[index])
            if not code.strip():
                continue
            indentation = len(code) - len(code.lstrip(" "))
            if indentation != 2:
                continue
            keys = _yaml_mapping_keys(code)
            observed_events.update(keys)
            canonical = (
                len(keys) == 1 and
                re.fullmatch(
                    r"[A-Za-z_][A-Za-z0-9_-]*", keys[0]) is not None and
                code == f"  {keys[0]}:")
            if (_yaml_outside_strict_mapping_subset(code) or
                    not canonical):
                event_syntax_violations.append(
                    f"line {index + 1}: workflow event mapping is "
                    "noncanonical")
        events = sorted(observed_events)
    required_markers = {
        "release_eligible=false",
        "host_execution_authorized=false",
        "broker_connection_authorized=false",
        "paper_authorized=false",
        "live_authorized=false",
    }
    missing_markers = sorted(
        marker for marker in required_markers
        if marker not in workflow_text)
    normalize = lambda value: re.sub(
        r"[\\/]+", "/", value.casefold())
    normalized_workflow = normalize(workflow_text)
    execution_fragments = sorted({
        fragment for fragment in (
            "./scripts/ci_gate",
            "& scripts/ci_gate",
            "-File scripts/ci_gate",
        )
        if normalize(fragment) in normalized_workflow
    })
    run_input_interpolations: list[str] = []
    for index, line in enumerate(lines):
        match = re.fullmatch(r"(?P<indent> +)run:\s*[|>][-+]?\s*", line)
        if match is None:
            continue
        indentation = len(match.group("indent"))
        end = index + 1
        while end < len(lines):
            following = lines[end]
            following_indentation = (
                len(following) - len(following.lstrip(" ")))
            if following.strip() and following_indentation <= indentation:
                break
            end += 1
        block = "\n".join(lines[index + 1:end])
        if "${{ inputs." in block:
            run_input_interpolations.append(f"line {index + 1}")
    observed_sha256 = hashlib.sha256(
        workflow_text.encode("utf-8")).hexdigest()
    return {
        "expected_sha256": expected_sha256,
        "observed_sha256": observed_sha256,
        "events": events,
        "event_syntax_violations": event_syntax_violations,
        "missing_quarantine_markers": missing_markers,
        "legacy_execution_fragments": execution_fragments,
        "run_input_interpolations": run_input_interpolations,
        "passed": (
            events == ["workflow_dispatch"] and
            not event_syntax_violations and
            observed_sha256 == expected_sha256 and
            not missing_markers and
            not execution_fragments and
            not run_input_interpolations),
    }


def _git_archive_entries(
        root: Path, revision: str) -> dict[str, dict[str, Any]]:
    try:
        result = subprocess.run(
            [
                "git", "-C", str(root), "-c", "tar.umask=0022",
                "archive", "--format=tar", revision,
            ],
            check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as error:
        raise CodeQualityError("git archive is unavailable") from error
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise CodeQualityError(f"git archive failed: {detail}")
    try:
        with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
            entries: dict[str, dict[str, Any]] = {}
            for member in archive.getmembers():
                if member.isdir():
                    continue
                name = member.name.removeprefix("./").rstrip("/")
                if (not name or name in entries or
                        Path(name).is_absolute() or "\\" in name or
                        any(part in {"", ".", ".."}
                            for part in name.split("/"))):
                    raise CodeQualityError(
                        "git archive contains an unsafe or duplicate member")
                kind = (
                    "regular" if member.isfile() else
                    "symlink" if member.issym() else
                    "other")
                data = None
                if (kind == "regular" and
                        member.size <= MAX_CI_ARCHIVE_INPUT_BYTES):
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise CodeQualityError(
                            f"git archive cannot read regular member: {name}")
                    data = stream.read(MAX_CI_ARCHIVE_INPUT_BYTES + 1)
                    if len(data) != member.size:
                        raise CodeQualityError(
                            f"git archive member size drift: {name}")
                entries[name] = {
                    "kind": kind,
                    "mode": f"{member.mode & 0o7777:04o}",
                    "size": member.size,
                    "data": data,
                }
            return entries
    except tarfile.TarError as error:
        raise CodeQualityError("git archive output is invalid") from error


def _archive_input_problem(
        root: Path, entries: dict[str, dict[str, Any]],
        relative: str) -> str | None:
    entry = entries.get(relative)
    if entry is None:
        return "absent from git archive"
    if entry["kind"] != "regular":
        return f"git archive member type is {entry['kind']}"
    if entry["mode"] not in {"0644", "0755"}:
        return f"git archive mode is {entry['mode']}"
    if entry["data"] is None:
        return "git archive input exceeds the bounded size"
    path = root / relative
    try:
        before = path.lstat()
        current = _protected_bytes(
            path, relative, allow_group_write=True)
        after = path.lstat()
    except (OSError, CodeQualityError) as error:
        return f"worktree input is unsafe: {error}"
    identity = lambda value: (
        value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
        value.st_uid, value.st_gid, value.st_size, value.st_mtime_ns,
        value.st_ctime_ns,
    )
    if identity(before) != identity(after):
        return "worktree input changed while binding archive content"
    if current != entry["data"]:
        return "worktree bytes differ from git archive HEAD"
    return None


def _archive_manifest_references(root: Path, relative: str) -> set[str]:
    try:
        document = json.loads(
            _protected_bytes(
                root / relative, relative,
                allow_group_write=True).decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise CodeQualityError(
            f"{relative} is not strict UTF-8 JSON") from error
    if not isinstance(document, dict):
        raise CodeQualityError(f"{relative} is not a JSON object")
    references = {relative}
    for field in ("include_files", "required_files"):
        values = document.get(field)
        if (not isinstance(values, list) or
                any(not isinstance(value, str) or not value
                    for value in values)):
            raise CodeQualityError(
                f"{relative} does not expose a valid {field} list")
        for value in values:
            normalized = Path(value)
            if (normalized.is_absolute() or "\\" in value or
                    value.startswith("./") or
                    any(part in {"", ".", ".."}
                        for part in value.split("/"))):
                raise CodeQualityError(
                    f"{relative} contains an unsafe archive member: {value}")
            references.add(value)
    return references


def audit_ci_archive_references(
        root: Path, policy_path: Path) -> dict[str, Any]:
    policy = _policy(policy_path)
    config = policy["ci_archive"]
    entries = _git_archive_entries(root, config["revision"])
    references: set[str] = {config["actionlint_config"]}
    missing: set[str] = set()
    unsafe: dict[str, str] = {}
    checkout_reports: list[dict[str, Any]] = []
    action_pinning_reports: list[dict[str, Any]] = []
    product_boundary_reports: list[dict[str, Any]] = []
    manual_quarantine_reports: list[dict[str, Any]] = []
    actionlint_expected = (
        "self-hosted-runner:\n"
        "  labels:\n"
        "    - heptatrader-coverage-v1\n"
        "    - heptatrader-ib-sdk-v1\n"
        "\n"
        "config-variables: null\n"
        "\n"
        "paths:\n"
    ).encode("utf-8")
    actionlint_data = _protected_bytes(
        root / config["actionlint_config"],
        config["actionlint_config"], allow_group_write=True)
    actionlint_config_report = {
        "path": config["actionlint_config"],
        "sha256": hashlib.sha256(actionlint_data).hexdigest(),
        "passed": actionlint_data == actionlint_expected,
    }
    workflow_kinds = [
        (workflow, True) for workflow in config["workflows"]
    ] + [
        (workflow, False)
        for workflow in config["supplemental_workflows"]
    ]
    for workflow, canonical in workflow_kinds:
        references.add(workflow)
        data = _protected_bytes(
            root / workflow, workflow, allow_group_write=True)
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise CodeQualityError(f"{workflow} is not UTF-8") from error
        local = _ci_local_references(
            text, config["path_prefixes"],
            config["root_executable_suffixes"])
        references.update(local)
        action_pinning_reports.append(_ci_action_pinning(text))
        if canonical:
            checkout_reports.append(
                _ci_checkout_contract(
                    text, config["checkout_contract"]))
            product_boundary_reports.append(
                _ci_product_boundary(
                    text, config["canonical_forbidden_fragments"]))
        else:
            manual_quarantine_reports.append(
                _ci_manual_quarantine(
                    text,
                    config["supplemental_workflow_sha256"][workflow]))
    for manifest in config["member_manifests"]:
        local = _archive_manifest_references(root, manifest)
        references.update(local)
    for relative in sorted(references):
        problem = _archive_input_problem(root, entries, relative)
        if problem is None:
            continue
        if problem == "absent from git archive":
            missing.add(relative)
        else:
            unsafe[relative] = problem
    checkout_passed = (
        len(checkout_reports) == 1 and
        checkout_reports[0]["passed"])
    action_pinning_passed = (
        not config["require_action_sha_pinning"] or
        (len(action_pinning_reports) == len(workflow_kinds) and
         all(item["passed"] for item in action_pinning_reports)))
    product_boundary_passed = (
        len(product_boundary_reports) == len(config["workflows"]) and
        all(item["passed"] for item in product_boundary_reports))
    manual_quarantine_passed = (
        len(manual_quarantine_reports) ==
        len(config["supplemental_workflows"]) and
        all(item["passed"] for item in manual_quarantine_reports))
    return {
        "revision": config["revision"],
        "workflow_count": len(workflow_kinds),
        "reference_count": len(references),
        "references": sorted(references),
        "missing_from_archive": sorted(missing),
        "unsafe_archive_inputs": unsafe,
        "checkout_contract": checkout_reports,
        "action_pinning": action_pinning_reports,
        "product_boundary": product_boundary_reports,
        "manual_quarantine": manual_quarantine_reports,
        "actionlint_config": actionlint_config_report,
        "passed": (
            not missing and not unsafe and
            checkout_passed and action_pinning_passed and
            product_boundary_passed and manual_quarantine_passed and
            actionlint_config_report["passed"]),
    }


def audit(root: Path, policy_path: Path) -> dict[str, Any]:
    policy = _policy(policy_path)
    violations: list[str] = []
    line_counts: dict[str, int] = {}
    for relative, limit in sorted(
            policy["native_line_budgets"].items()):
        path = root / relative
        lines = _line_count(
            _protected_bytes(
                path, relative, allow_group_write=True), relative)
        line_counts[relative] = lines
        if lines > limit:
            violations.append(
                f"{relative} has {lines} lines; budget is {limit}")

    structure, include_graph = _structural_metrics(
        root, list(policy["native_structure_budgets"]))
    for relative, limits in sorted(
            policy["native_structure_budgets"].items()):
        values = structure[relative]
        maximum_function = values["maximum_function"]
        maximum_cyclomatic = values["maximum_cyclomatic_function"]
        maximum_cognitive = values["maximum_cognitive_function"]
        if maximum_function is None:
            violations.append(f"{relative} has no recognized functions")
            continue
        comparisons = (
            ("function lines", maximum_function["lines"],
             limits["maximum_function_lines"]),
            ("cyclomatic complexity",
             maximum_cyclomatic["cyclomatic_complexity"],
             limits["maximum_cyclomatic_complexity"]),
            ("cognitive complexity",
             maximum_cognitive["cognitive_complexity"],
             limits["maximum_cognitive_complexity"]),
        )
        for label, actual, limit in comparisons:
            if actual > limit:
                violations.append(
                    f"{relative} maximum {label} {actual} exceeds {limit}")
    graph_policy = policy["include_graph"]
    for label, report_field, policy_field in (
            ("edge count", "edge_count", "maximum_edges"),
            ("local fan-out", "maximum_local_fan_out",
             "maximum_local_fan_out"),
            ("local fan-in", "maximum_local_fan_in",
             "maximum_local_fan_in")):
        if include_graph[report_field] > graph_policy[policy_field]:
            violations.append(
                f"include graph {label} {include_graph[report_field]} "
                f"exceeds {graph_policy[policy_field]}")

    cmake_text: dict[str, str] = {}
    references = 0
    for relative in policy["cmake"]["files"]:
        data = _protected_bytes(
            root / relative, relative, allow_group_write=True)
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise CodeQualityError(f"{relative} is not UTF-8") from error
        cmake_text[relative] = text
        references += len(CPP_REFERENCE.findall(text))
        for pattern in policy["cmake"]["forbidden_source_discovery"]:
            if pattern in text:
                violations.append(
                    f"{relative} uses forbidden source discovery: {pattern}")
    if references > policy["cmake"]["maximum_cpp_references"]:
        violations.append(
            "CMake C++ reference count "
            f"{references} exceeds {policy['cmake']['maximum_cpp_references']}")

    return {
        "schema": REPORT_SCHEMA,
        "version": 1,
        "passed": not violations,
        "native_line_counts": line_counts,
        "native_structure_metrics": structure,
        "include_graph": include_graph,
        "cmake_cpp_references": references,
        "coverage_line_minimum_percent":
            policy["coverage"]["line_minimum_percent"],
        "coverage_toolchain_provisioned":
            policy["coverage"]["toolchain"]["provisioned"],
        "violations": violations,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path,
        default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--policy", type=Path,
        default=Path("policies/heptatrader-code-quality-v1.json"))
    parser.add_argument(
        "--check-ci-archive", action="store_true",
        help="also require CI local inputs to exist in git archive HEAD")
    parser.add_argument("--strict", action="store_true")
    arguments = parser.parse_args()
    root = arguments.root.resolve(strict=True)
    policy = arguments.policy
    if not policy.is_absolute():
        policy = root / policy
    policy = policy.resolve(strict=True)
    report = audit(root, policy)
    if arguments.check_ci_archive:
        ci_archive = audit_ci_archive_references(root, policy)
        report["ci_archive"] = ci_archive
        if not ci_archive["passed"]:
            report["violations"].extend(
                f"CI local input is absent from git archive HEAD: {path}"
                for path in ci_archive["missing_from_archive"])
            report["violations"].extend(
                f"CI archive input is unsafe or drifted: {path}: {problem}"
                for path, problem in
                ci_archive["unsafe_archive_inputs"].items())
            report["violations"].extend(
                "CI checkout contract drifted: "
                f"checkout={item['checkout_count']}/"
                f"{item['expected_count']} "
                f"fetch-depth={item['fetch_depth_count']}/"
                f"{item['expected_count']} "
                f"head-ref={item['pull_request_head_ref_count']}/"
                f"{item['expected_count']} "
                "persist-credentials=false="
                f"{item['persist_credentials_false_count']}/"
                f"{item['expected_count']} "
                f"uses-syntax={len(item['uses_violations'])} "
                f"option-syntax={len(item['option_violations'])}"
                for item in ci_archive["checkout_contract"]
                if not item["passed"])
            report["violations"].extend(
                f"CI action is not pinned to a full commit SHA: {action}"
                for item in ci_archive["action_pinning"]
                for action in item["unpinned_actions"])
            report["violations"].extend(
                f"CI action declaration is not canonical: {violation}"
                for item in ci_archive["action_pinning"]
                for violation in item["uses_violations"])
            report["violations"].extend(
                f"CI local action is not archive-bound: {action}"
                for item in ci_archive["action_pinning"]
                for action in item["unbound_local_uses"])
            report["violations"].extend(
                f"canonical CI references legacy/host-bound input: {fragment}"
                for item in ci_archive["product_boundary"]
                for fragment in item["forbidden_fragments"])
            report["violations"].extend(
                "supplemental CI is not a manual non-executing quarantine"
                for item in ci_archive["manual_quarantine"]
                if not item["passed"])
            report["passed"] = False
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    return 1 if arguments.strict and not report["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
