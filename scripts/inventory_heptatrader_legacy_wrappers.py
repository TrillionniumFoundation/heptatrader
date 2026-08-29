#!/usr/bin/env python3
"""Build a deterministic, read-only retirement inventory for root wrappers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any


REPOSITORY = Path(__file__).resolve(strict=True).parents[1]
sys.path.insert(0, str(REPOSITORY))

from hepta_ops.registry import load_registry  # noqa: E402


SCHEMA = "hepta.legacy-wrapper-retirement-inventory.v2"
REPORT_VERSION = 2
HOST_RUNTIME_SCHEMA = "hepta.host-script-reference-inventory.v1"
ROOT_WRAPPER = re.compile(
    r"^(?:build|check|install|launch|provision|run|status|stop|verify)_"
    r"[A-Za-z0-9_.-]+\.sh$")
WRAPPER_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"((?:build|check|install|launch|provision|run|status|stop|verify)_"
    r"[A-Za-z0-9_.-]+\.sh)"
    r"(?![A-Za-z0-9_.-])")
PYTHON_TARGET = re.compile(
    r"(?:^|[/\"'\s])scripts/([A-Za-z0-9_.-]+\.py)")
SCRIPT_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_.%/@:+-])"
    r"([/\.A-Za-z0-9_%@:+-]+\.(?:py|sh))"
    r"(?![A-Za-z0-9_.%/@:+-])")
SYSTEMD_EXEC_DIRECTIVE = re.compile(
    r"^(ExecCondition|ExecStart|ExecStartPre|ExecStartPost|ExecReload|"
    r"ExecStop|ExecStopPost)=(.*)$")
SYSTEMD_INSTANCE = re.compile(
    r"^(?P<prefix>[^@]+)@(?P<instance>.+)\."
    r"(?P<suffix>service|socket|timer|target)$")
SYSTEMD_TEMPLATE = re.compile(
    r"^(?P<prefix>[^@]+)@\."
    r"(?P<suffix>service|socket|timer|target)$")
MAX_REFERENCE_FILE_BYTES = 4 * 1024 * 1024
REFERENCE_SUFFIXES = frozenset({
    ".cfg", ".cmake", ".conf", ".ini", ".json", ".md", ".py", ".rst",
    ".service", ".sh", ".socket", ".target", ".timer", ".toml", ".txt",
    ".yaml", ".yml",
})
SYSTEMD_RUNTIME_ROOTS = (
    ("system", Path("/etc/systemd/system")),
    ("system", Path("/usr/local/lib/systemd/system")),
    ("system", Path("/usr/lib/systemd/system")),
    ("system", Path("/lib/systemd/system")),
    ("user", Path.home() / ".config/systemd/user"),
)
SYSTEM_CRON_FILES = (Path("/etc/crontab"),)
SYSTEM_CRON_DIRECTORIES = (Path("/etc/cron.d"),)
SYSTEMD_UNIT_SUFFIXES = frozenset({
    ".service", ".socket", ".timer", ".target",
})
ENABLED_UNIT_FILE_STATES = frozenset({
    "enabled", "enabled-runtime",
})


class InventoryError(RuntimeError):
    """The wrapper inventory could not be established safely."""


def _git_paths(root: Path, *arguments: str) -> set[str]:
    result = subprocess.run(
        ["git", *arguments, "-z"],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise InventoryError("Git wrapper inventory is unavailable")
    return {
        value.decode("utf-8", errors="surrogateescape")
        for value in result.stdout.split(b"\0") if value
    }


def _safe_regular(path: Path) -> tuple[os.stat_result, bytes]:
    before = path.lstat()
    if (stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or
            before.st_nlink != 1):
        raise InventoryError(f"wrapper is not a single regular file: {path}")
    data = path.read_bytes()
    after = path.lstat()
    identity = lambda value: (
        value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
        value.st_uid, value.st_gid, value.st_size, value.st_mtime_ns,
        value.st_ctime_ns,
    )
    if identity(before) != identity(after) or len(data) != before.st_size:
        raise InventoryError(f"wrapper changed while reading: {path}")
    return before, data


def _reference_inventory(
    root: Path, wrappers: set[str], paths: set[str],
) -> tuple[dict[str, list[str]], dict[str, list[str]], list[str]]:
    references = {wrapper: [] for wrapper in wrappers}
    dangling: dict[str, list[str]] = {}
    errors: list[str] = []
    for relative in sorted(paths):
        candidate = root.joinpath(*Path(relative).parts)
        if (candidate.name != "CMakeLists.txt" and
                candidate.suffix.lower() not in REFERENCE_SUFFIXES):
            continue
        try:
            metadata = candidate.lstat()
        except OSError:
            errors.append(f"repository stat failed: {relative}")
            continue
        if (stat.S_ISLNK(metadata.st_mode) or
                not stat.S_ISREG(metadata.st_mode)):
            continue
        if metadata.st_size > MAX_REFERENCE_FILE_BYTES:
            errors.append(f"repository reference file exceeds limit: {relative}")
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            errors.append(f"repository read failed: {relative}")
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            for wrapper in set(WRAPPER_TOKEN.findall(line)):
                location = f"{relative}:{line_number}"
                if wrapper in wrappers:
                    references[wrapper].append(location)
                else:
                    dangling.setdefault(wrapper, []).append(location)
    normalized = {
        wrapper: sorted(set(values))
        for wrapper, values in references.items()
    }
    return normalized, {
        wrapper: sorted(set(values))
        for wrapper, values in sorted(dangling.items())
    }, sorted(set(errors))


def _run_read_only(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _normalize_repository_script(
    token: str,
    root: Path,
    wrappers: set[str],
    *,
    allow_relative: bool = True,
) -> str | None:
    root_text = root.as_posix().rstrip("/")
    if token.startswith(root_text + "/"):
        relative = token[len(root_text) + 1:]
    elif allow_relative and token.startswith("./"):
        relative = token[2:]
    elif allow_relative and token.startswith("scripts/"):
        relative = token
    elif allow_relative and "/" not in token and token in wrappers:
        relative = token
    else:
        return None
    parts = Path(relative).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    return Path(*parts).as_posix()


def _script_metadata(
    relative: str,
    root: Path,
    wrappers: set[str],
    tracked: set[str],
    untracked: set[str],
    *,
    repository_scope: str = "audited-repository",
    execution_root: str | None = None,
) -> dict[str, Any]:
    name = Path(relative).name
    templated = "%" in relative
    if name in wrappers:
        kind = "root-wrapper"
    elif templated and Path(relative).parent == Path("."):
        kind = "template-root-wrapper"
    elif relative.startswith("scripts/"):
        kind = "repository-script"
    else:
        kind = "repository-root-script"
    if repository_scope != "audited-repository":
        git_state = (
            "external-worktree" if
            repository_scope == "external-worktree" else
            "unresolved")
        physical = (
            Path(execution_root) / relative
            if execution_root is not None and not templated else None)
        exists = physical is not None and physical.is_file()
    else:
        if relative in tracked:
            git_state = "tracked"
        elif relative in untracked:
            git_state = "untracked"
        elif templated:
            git_state = "template"
        elif (root / relative).is_file():
            git_state = "ignored"
        else:
            git_state = "missing"
        exists = not templated and (root / relative).is_file()
    normalized_execution_root = execution_root
    if (normalized_execution_root is None and
            repository_scope == "audited-repository"):
        normalized_execution_root = root.as_posix()
    return {
        "script_path": relative,
        "script_kind": kind,
        "git_state": git_state,
        "repository_scope": repository_scope,
        "execution_root": normalized_execution_root,
        "exists": exists,
    }


def _parse_unit_file_states(output: str) -> dict[str, str]:
    states: dict[str, str] = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0].endswith(
                tuple(SYSTEMD_UNIT_SUFFIXES)):
            states[fields[0]] = fields[1]
    return states


def _parse_unit_runtime_states(
    output: str,
) -> dict[str, dict[str, str]]:
    states: dict[str, dict[str, str]] = {}
    for line in output.splitlines():
        fields = line.split()
        if fields and fields[0] in {"●", "*"}:
            fields = fields[1:]
        if len(fields) < 4 or not fields[0].endswith(
                tuple(SYSTEMD_UNIT_SUFFIXES)):
            continue
        states[fields[0]] = {
            "load_state": fields[1],
            "active_state": fields[2],
            "sub_state": fields[3],
        }
    return states


def _systemd_states(
    scope: str, errors: set[str],
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    prefix = ["systemctl"]
    if scope == "user":
        prefix.append("--user")
    suffix = [
        "--type=service", "--type=socket", "--type=timer", "--type=target",
        "--no-legend", "--no-pager", "--plain",
    ]
    try:
        files = _run_read_only(prefix + ["list-unit-files", *suffix])
        runtime = _run_read_only(prefix + ["list-units", "--all", *suffix])
    except OSError:
        errors.add(f"{scope} systemctl is unavailable")
        return {}, {}
    if files.returncode != 0:
        errors.add(
            f"{scope} systemd unit-file inventory failed: "
            f"exit {files.returncode}")
    if runtime.returncode != 0:
        errors.add(
            f"{scope} systemd runtime inventory failed: "
            f"exit {runtime.returncode}")
    return (
        _parse_unit_file_states(files.stdout)
        if files.returncode == 0 else {},
        _parse_unit_runtime_states(runtime.stdout)
        if runtime.returncode == 0 else {},
    )


def _template_name(unit_name: str) -> str | None:
    match = SYSTEMD_INSTANCE.fullmatch(unit_name)
    if match is None:
        return None
    return (
        f"{match.group('prefix')}@.{match.group('suffix')}")


def _template_instance(unit_name: str) -> str | None:
    match = SYSTEMD_INSTANCE.fullmatch(unit_name)
    return match.group("instance") if match is not None else None


def _unescape_systemd_instance(value: str) -> str:
    return re.sub(
        r"\\x([0-9A-Fa-f]{2})",
        lambda match: chr(int(match.group(1), 16)),
        value,
    )


def _expand_systemd_value(value: str, unit_name: str) -> str:
    instance = _template_instance(unit_name)
    expanded = value.replace("%%", "\0")
    expanded = expanded.replace("%n", unit_name)
    expanded = expanded.replace("%N", unit_name.rsplit(".", 1)[0])
    expanded = expanded.replace("%h", Path.home().as_posix())
    if instance is not None:
        expanded = expanded.replace("%i", instance)
        expanded = expanded.replace(
            "%I", _unescape_systemd_instance(instance))
    return expanded.replace("\0", "%")


def _systemd_working_directory(
    text: str, unit_name: str,
) -> str | None:
    value: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("WorkingDirectory="):
            continue
        candidate = _expand_systemd_value(
            stripped.split("=", 1)[1].strip(), unit_name)
        if candidate.startswith("-"):
            candidate = candidate[1:]
        value = candidate or None
    if value is None or not value.startswith("/"):
        return None
    return Path(os.path.normpath(value)).as_posix()


def _normalize_deployment_script(
    token: str,
    root: Path,
    wrappers: set[str],
    working_directory: str | None,
) -> tuple[str, str, str | None] | None:
    root_text = root.as_posix()
    relative = _normalize_repository_script(
        token, root, wrappers, allow_relative=False)
    if relative is not None:
        return relative, "audited-repository", root_text
    if token.startswith("/"):
        if working_directory is None:
            return None
        prefix = working_directory.rstrip("/") + "/"
        if not token.startswith(prefix):
            return None
        relative = token[len(prefix):]
    elif token.startswith("./"):
        relative = token[2:]
    elif not token.startswith("/") and "/" in token:
        relative = token
    elif (working_directory is not None or token in wrappers):
        relative = token
    else:
        return None
    parts = Path(relative).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    normalized = Path(*parts).as_posix()
    if working_directory == root_text:
        return normalized, "audited-repository", root_text
    if working_directory is not None:
        return normalized, "external-worktree", working_directory
    return normalized, "unresolved-relative", None


def _unit_status(
    unit_name: str,
    template_name: str | None,
    unit_file_states: dict[str, str],
    runtime_states: dict[str, dict[str, str]],
) -> dict[str, Any]:
    unit_file_state = unit_file_states.get(unit_name)
    if unit_file_state is None and template_name is not None:
        unit_file_state = unit_file_states.get(template_name)
    runtime = runtime_states.get(unit_name, {})
    active_state = runtime.get("active_state", "not-loaded")
    return {
        "unit_file_state": unit_file_state or "unknown",
        "enabled": unit_file_state in ENABLED_UNIT_FILE_STATES,
        "load_state": runtime.get("load_state", "not-loaded"),
        "active_state": active_state,
        "sub_state": runtime.get("sub_state", "not-loaded"),
        "active": active_state == "active",
    }


def _scan_systemd_unit(
    *,
    text: str,
    root: Path,
    wrappers: set[str],
    tracked: set[str],
    untracked: set[str],
    scope: str,
    unit_name: str,
    template_name: str | None,
    logical: Path,
    unit_file_states: dict[str, str],
    runtime_states: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    status_record = _unit_status(
        unit_name, template_name, unit_file_states, runtime_states)
    working_directory = _systemd_working_directory(text, unit_name)
    for line_number, line in enumerate(text.splitlines(), 1):
        match = SYSTEMD_EXEC_DIRECTIVE.fullmatch(line.strip())
        if match is None:
            continue
        value = _expand_systemd_value(match.group(2), unit_name)
        deployments = {
            deployment
            for token in SCRIPT_TOKEN.findall(value)
            if (deployment := _normalize_deployment_script(
                token, root, wrappers, working_directory)) is not None
        }
        for relative, repository_scope, execution_root in sorted(deployments):
            record = {
                "source": f"systemd-{scope}",
                "source_id": logical.as_posix(),
                "line": line_number,
                "directive": match.group(1),
                "unit": unit_name,
                "template_unit": template_name,
                "instance": _template_instance(unit_name),
                **status_record,
                **_script_metadata(
                    relative,
                    root,
                    wrappers,
                    tracked,
                    untracked,
                    repository_scope=repository_scope,
                    execution_root=execution_root,
                ),
            }
            records.append(record)
    return records


def _systemd_inventory(
    root: Path,
    wrappers: set[str],
    tracked: set[str],
    untracked: set[str],
    errors: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    references: list[dict[str, Any]] = []
    scanned_roots: list[str] = []
    states = {
        scope: _systemd_states(scope, errors)
        for scope in ("system", "user")
    }
    seen_logical: set[tuple[str, str]] = set()
    for scope, base in SYSTEMD_RUNTIME_ROOTS:
        if not base.is_dir():
            continue
        scanned_roots.append(f"{scope}:{base.as_posix()}")
        try:
            candidates = sorted(base.rglob("*"))
        except OSError:
            errors.add(f"systemd scan failed: {base}")
            continue
        for logical in candidates:
            if logical.suffix not in SYSTEMD_UNIT_SUFFIXES:
                continue
            identity = (scope, logical.name)
            if identity in seen_logical:
                continue
            seen_logical.add(identity)
            try:
                target = logical.resolve(strict=True)
                metadata = target.lstat()
                if (not stat.S_ISREG(metadata.st_mode) or
                        metadata.st_size > MAX_REFERENCE_FILE_BYTES):
                    continue
                text = target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                errors.add(f"systemd unit read failed: {logical}")
                continue
            unit_file_states, runtime_states = states[scope]
            template_match = SYSTEMD_TEMPLATE.fullmatch(logical.name)
            if template_match is None:
                references.extend(_scan_systemd_unit(
                    text=text,
                    root=root,
                    wrappers=wrappers,
                    tracked=tracked,
                    untracked=untracked,
                    scope=scope,
                    unit_name=logical.name,
                    template_name=_template_name(logical.name),
                    logical=logical,
                    unit_file_states=unit_file_states,
                    runtime_states=runtime_states,
                ))
                continue
            template_name = logical.name
            references.extend(_scan_systemd_unit(
                text=text,
                root=root,
                wrappers=wrappers,
                tracked=tracked,
                untracked=untracked,
                scope=scope,
                unit_name=template_name,
                template_name=template_name,
                logical=logical,
                unit_file_states=unit_file_states,
                runtime_states=runtime_states,
            ))
            actual_units = sorted({
                name
                for name in unit_file_states.keys() | runtime_states.keys()
                if _template_name(name) == template_name
            })
            for actual_unit in actual_units:
                references.extend(_scan_systemd_unit(
                    text=text,
                    root=root,
                    wrappers=wrappers,
                    tracked=tracked,
                    untracked=untracked,
                    scope=scope,
                    unit_name=actual_unit,
                    template_name=template_name,
                    logical=logical,
                    unit_file_states=unit_file_states,
                    runtime_states=runtime_states,
                ))
    return references, sorted(set(scanned_roots))


def _scan_cron_text(
    *,
    text: str,
    source: str,
    source_id: str,
    root: Path,
    wrappers: set[str],
    tracked: set[str],
    untracked: set[str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if (not stripped or stripped.startswith("#") or
                re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*=", stripped)):
            continue
        deployments = {
            deployment
            for token in SCRIPT_TOKEN.findall(stripped)
            if (deployment := _normalize_deployment_script(
                token, root, wrappers, None)) is not None
        }
        for relative, repository_scope, execution_root in sorted(deployments):
            records.append({
                "source": source,
                "source_id": source_id,
                "line": line_number,
                **_script_metadata(
                    relative,
                    root,
                    wrappers,
                    tracked,
                    untracked,
                    repository_scope=repository_scope,
                    execution_root=execution_root,
                ),
            })
    return records


def _cron_inventory(
    root: Path,
    wrappers: set[str],
    tracked: set[str],
    untracked: set[str],
    errors: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    scanned_sources: list[str] = []
    candidates = list(SYSTEM_CRON_FILES)
    for directory in SYSTEM_CRON_DIRECTORIES:
        if not directory.is_dir():
            continue
        try:
            candidates.extend(sorted(directory.iterdir()))
        except OSError:
            errors.add(f"system cron scan failed: {directory}")
    for logical in candidates:
        if not logical.exists():
            continue
        try:
            target = logical.resolve(strict=True)
            metadata = target.lstat()
            if (not stat.S_ISREG(metadata.st_mode) or
                    metadata.st_size > MAX_REFERENCE_FILE_BYTES):
                continue
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            errors.add(f"system cron read failed: {logical}")
            continue
        source_id = logical.as_posix()
        scanned_sources.append(f"system:{source_id}")
        records.extend(_scan_cron_text(
            text=text,
            source="cron-system",
            source_id=source_id,
            root=root,
            wrappers=wrappers,
            tracked=tracked,
            untracked=untracked,
        ))
    try:
        user_cron = _run_read_only(["crontab", "-l"])
    except OSError:
        errors.add("current-user crontab command unavailable")
    else:
        scanned_sources.append("user:current")
        if user_cron.returncode == 0:
            records.extend(_scan_cron_text(
                text=user_cron.stdout,
                source="cron-user",
                source_id="current-user",
                root=root,
                wrappers=wrappers,
                tracked=tracked,
                untracked=untracked,
            ))
        elif user_cron.returncode != 1:
            errors.add(
                "current-user crontab inventory failed: "
                f"exit {user_cron.returncode}")
    return records, sorted(set(scanned_sources))


def _process_inventory(
    root: Path,
    wrappers: set[str],
    tracked: set[str],
    untracked: set[str],
    errors: set[str],
    *,
    proc: Path = Path("/proc"),
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not proc.is_dir():
        errors.add("/proc is unavailable")
        return records
    try:
        entries = sorted(
            (entry for entry in proc.iterdir() if entry.name.isdecimal()),
            key=lambda entry: int(entry.name),
        )
    except OSError:
        errors.add("/proc inventory failed")
        return records
    excluded_pids: set[int] = set()
    if proc == Path("/proc"):
        current = os.getpid()
        while current > 1 and current not in excluded_pids:
            excluded_pids.add(current)
            try:
                fields = (proc / str(current) / "stat").read_text(
                    encoding="utf-8", errors="replace").split()
                current = int(fields[3])
            except (OSError, ValueError, IndexError):
                break
    for entry in entries:
        if int(entry.name) in excluded_pids:
            continue
        try:
            data = (entry / "cmdline").read_bytes()[:1024 * 1024]
        except OSError:
            continue
        command = data.replace(b"\0", b" ").decode(
            "utf-8", errors="replace")
        try:
            working_directory = (
                entry / "cwd").resolve(strict=True).as_posix()
        except OSError:
            working_directory = None
        deployments = {
            deployment
            for token in SCRIPT_TOKEN.findall(command)
            if (deployment := _normalize_deployment_script(
                token, root, wrappers, working_directory)) is not None
        }
        for relative, repository_scope, execution_root in sorted(deployments):
            records.append({
                "source": "process",
                "source_id": f"pid:{entry.name}",
                "pid": int(entry.name),
                **_script_metadata(
                    relative,
                    root,
                    wrappers,
                    tracked,
                    untracked,
                    repository_scope=repository_scope,
                    execution_root=execution_root,
                ),
            })
    return records


def _reference_sort_key(record: dict[str, Any]) -> tuple[Any, ...]:
    return (
        record["source"],
        record["source_id"],
        record.get("unit", ""),
        record.get("line", 0),
        record["script_path"],
    )


def _deduplicate_references(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for record in records:
        key = json.dumps(record, ensure_ascii=True, sort_keys=True)
        values[key] = record
    return sorted(values.values(), key=_reference_sort_key)


def _empty_host_runtime(reason: str) -> dict[str, Any]:
    return {
        "schema": HOST_RUNTIME_SCHEMA,
        "version": 1,
        "collected": False,
        "complete": False,
        "read_only": True,
        "redaction": {
            "command_arguments_recorded": False,
            "environment_recorded": False,
            "matched_repository_script_only": True,
        },
        "scanned_systemd_roots": [],
        "scanned_cron_sources": [],
        "errors": [reason],
        "script_references": [],
        "systemd_references": [],
        "cron_references": [],
        "process_references": [],
        "unique_wrapper_paths": [],
        "unique_direct_script_paths": [],
        "external_worktree_script_references": [],
        "summary": {
            "reference_count": 0,
            "systemd_reference_count": 0,
            "cron_reference_count": 0,
            "process_reference_count": 0,
            "unique_wrapper_count": 0,
            "unique_direct_script_count": 0,
            "template_reference_count": 0,
            "instantiated_template_reference_count": 0,
            "external_worktree_reference_count": 0,
            "unresolved_relative_reference_count": 0,
        },
    }


def _host_runtime_inventory(
    root: Path,
    wrappers: set[str],
    tracked: set[str],
    untracked: set[str],
) -> dict[str, Any]:
    errors: set[str] = set()
    systemd, scanned_systemd_roots = _systemd_inventory(
        root, wrappers, tracked, untracked, errors)
    cron, scanned_cron_sources = _cron_inventory(
        root, wrappers, tracked, untracked, errors)
    processes = _process_inventory(
        root, wrappers, tracked, untracked, errors)
    references = _deduplicate_references(systemd + cron + processes)
    wrapper_paths = sorted({
        record["script_path"]
        for record in references
        if record["script_kind"] == "root-wrapper"
    })
    direct_paths = sorted({
        record["script_path"]
        for record in references
        if (record["repository_scope"] == "audited-repository" and
            record["script_kind"] not in {
                "root-wrapper", "template-root-wrapper",
            })
    })
    external_worktree_scripts = _deduplicate_references([
        {
            "source": record["source"],
            "source_id": record["source_id"],
            "unit": record.get("unit"),
            "execution_root": record["execution_root"],
            "script_path": record["script_path"],
            "script_kind": record["script_kind"],
        }
        for record in references
        if record["repository_scope"] == "external-worktree"
    ])
    legacy = {
        "systemd": set(),
        "cron": set(),
        "process": set(),
    }
    for record in references:
        if record["script_kind"] != "root-wrapper":
            continue
        source = record["source"]
        category = (
            "systemd" if source.startswith("systemd-") else
            "cron" if source.startswith("cron-") else
            "process"
        )
        legacy[category].add(
            f"{source}:{record['source_id']}:"
            f"{record.get('line', 0)}:{Path(record['script_path']).name}")
    return {
        "schema": HOST_RUNTIME_SCHEMA,
        "version": 1,
        "collected": True,
        "complete": not errors,
        "read_only": True,
        "redaction": {
            "command_arguments_recorded": False,
            "environment_recorded": False,
            "matched_repository_script_only": True,
        },
        "scanned_systemd_roots": scanned_systemd_roots,
        "scanned_cron_sources": scanned_cron_sources,
        "errors": sorted(errors),
        "script_references": references,
        "systemd_references": sorted(legacy["systemd"]),
        "cron_references": sorted(legacy["cron"]),
        "process_references": sorted(legacy["process"]),
        "unique_wrapper_paths": wrapper_paths,
        "unique_direct_script_paths": direct_paths,
        "external_worktree_script_references": external_worktree_scripts,
        "summary": {
            "reference_count": len(references),
            "systemd_reference_count": sum(
                record["source"].startswith("systemd-")
                for record in references),
            "cron_reference_count": sum(
                record["source"].startswith("cron-")
                for record in references),
            "process_reference_count": sum(
                record["source"] == "process" for record in references),
            "unique_wrapper_count": len(wrapper_paths),
            "unique_direct_script_count": len(direct_paths),
            "template_reference_count": sum(
                record.get("template_unit") is not None and
                record.get("instance") is None
                for record in references),
            "instantiated_template_reference_count": sum(
                record.get("instance") is not None
                for record in references),
            "external_worktree_reference_count": sum(
                record["repository_scope"] == "external-worktree"
                for record in references),
            "unresolved_relative_reference_count": sum(
                record["repository_scope"] == "unresolved-relative"
                for record in references),
        },
    }


def inventory(
    root: Path,
    registry_path: Path,
    *,
    include_host_runtime: bool = False,
) -> dict[str, Any]:
    root = root.resolve(strict=True)
    registry_path = registry_path.resolve(strict=True)
    registry = load_registry(registry_path)
    tracked = _git_paths(root, "ls-files")
    untracked = _git_paths(root, "ls-files", "--others", "--exclude-standard")
    wrappers = sorted(
        path for path in root.iterdir()
        if path.is_file() and not path.is_symlink() and
        ROOT_WRAPPER.fullmatch(path.name)
    )
    references, dangling_references, repository_scan_errors = (
        _reference_inventory(
            root, {path.name for path in wrappers}, tracked | untracked))
    host_runtime = (
        _host_runtime_inventory(
            root, {path.name for path in wrappers}, tracked, untracked)
        if include_host_runtime else
        _empty_host_runtime("host runtime inventory was not requested"))
    host_by_wrapper: dict[str, list[str]] = {}
    for record in host_runtime["script_references"]:
        if record["script_kind"] != "root-wrapper":
            continue
        wrapper = Path(record["script_path"]).name
        value = (
            f"{record['source']}:{record['source_id']}:"
            f"{record.get('line', 0)}:{wrapper}")
        host_by_wrapper.setdefault(wrapper, []).append(value)
    jobs_by_executable: dict[str, list[str]] = {}
    for job_id, job in registry["jobs"].items():
        jobs_by_executable.setdefault(job["executable"], []).append(job_id)

    records: list[dict[str, Any]] = []
    for path in wrappers:
        metadata, data = _safe_regular(path)
        text = data.decode("utf-8", errors="replace")
        targets = sorted({
            f"scripts/{name}" for name in PYTHON_TARGET.findall(text)
        })
        mapped_jobs = sorted({
            job_id
            for target in targets
            for job_id in jobs_by_executable.get(target, [])
        })
        wrapper_references = references[path.name]
        if len(targets) > 1:
            status = "blocked-ambiguous-target"
        elif not mapped_jobs:
            status = "blocked-unmapped"
        elif wrapper_references or host_by_wrapper.get(path.name):
            status = "blocked-referenced"
        elif host_runtime["complete"] and not repository_scan_errors:
            status = "compatibility-window-candidate"
        else:
            status = "blocked-incomplete-inventory"
        if path.name in tracked:
            git_state = "tracked"
        elif path.name in untracked:
            git_state = "untracked"
        else:
            git_state = "ignored"
        records.append({
            "path": path.name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": metadata.st_size,
            "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
            "git_state": git_state,
            "python_targets": targets,
            "mapped_jobs": mapped_jobs,
            "references": wrapper_references,
            "host_references": sorted(host_by_wrapper.get(path.name, [])),
            "retirement_status": status,
        })

    status_counts = {
        status: sum(
            record["retirement_status"] == status for record in records)
        for status in (
            "blocked-ambiguous-target",
            "blocked-unmapped",
            "blocked-referenced",
            "compatibility-window-candidate",
            "blocked-incomplete-inventory",
        )
    }
    tracked_count = sum(record["git_state"] == "tracked" for record in records)
    untracked_count = sum(
        record["git_state"] == "untracked" for record in records)
    referenced_count = sum(
        bool(record["references"]) for record in records)
    mapped_count = sum(
        bool(record["mapped_jobs"]) for record in records)
    scan_complete = not repository_scan_errors and host_runtime["complete"]
    migration_complete = (
        not records and not dangling_references and scan_complete)
    registry_bytes = registry_path.read_bytes()
    return {
        "schema": SCHEMA,
        "version": REPORT_VERSION,
        "passed": not repository_scan_errors,
        "scan_complete": scan_complete,
        "migration_complete": migration_complete,
        "deletion_authorized": False,
        "paper_authorized": False,
        "live_authorized": False,
        "wrapper_count": len(records),
        "tracked_wrapper_count": tracked_count,
        "untracked_wrapper_count": untracked_count,
        "mapped_wrapper_count": mapped_count,
        "referenced_wrapper_count": referenced_count,
        "status_counts": status_counts,
        "records": records,
        "dangling_references": dangling_references,
        "repository_scan_errors": repository_scan_errors,
        "host_runtime": host_runtime,
        "registry": {
            "path": registry_path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(registry_bytes).hexdigest(),
        },
    }


def _write_private(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL |
        getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise InventoryError("failed to publish wrapper inventory")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=REPOSITORY)
    parser.add_argument(
        "--registry", type=Path, default=Path("ops/hepta-ops-v1.json"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--include-host-runtime", action="store_true")
    arguments = parser.parse_args()
    root = arguments.root.resolve(strict=True)
    registry = arguments.registry
    if not registry.is_absolute():
        registry = root / registry
    report = inventory(
        root, registry,
        include_host_runtime=arguments.include_host_runtime)
    payload = (
        json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) +
        "\n").encode("utf-8")
    if arguments.output is None:
        sys.stdout.buffer.write(payload)
    else:
        output = arguments.output
        if not output.is_absolute():
            output = root / output
        _write_private(output, payload)
    if arguments.require_complete and not report["migration_complete"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
