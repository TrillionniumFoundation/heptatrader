#!/usr/bin/env python3
"""Verify the exact reviewed GitHub-hosted build toolchain without network use."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
from typing import Any

SCHEMA = "heptatrader.hosted-toolchain-lock.v1"
FULL_SHA256 = re.compile(r"^[0-9a-f]{64}$")
VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+)+(?:[-+~.0-9A-Za-z:]*)$")
RUNNER_KEYS = frozenset({"image_os", "image_version", "os_version_id"})
TOOL_KEYS = frozenset(
    {
        "cmake",
        "ninja",
        "python",
        "git",
        "openssl",
        "libssl_dev_package",
        "gcc",
        "clang",
    }
)


class ToolchainError(ValueError):
    pass


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ToolchainError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_lock(path: Path) -> tuple[dict[str, Any], str]:
    data = path.read_bytes()
    try:
        payload = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ToolchainError) as error:
        raise ToolchainError(f"invalid toolchain lock: {error}") from error
    if not isinstance(payload, dict) or frozenset(payload) != frozenset(
        {"schema", "runner", "tools"}
    ):
        raise ToolchainError("toolchain lock has unexpected top-level keys")
    if payload["schema"] != SCHEMA:
        raise ToolchainError(f"unsupported toolchain lock schema: {payload['schema']!r}")
    if not isinstance(payload["runner"], dict) or frozenset(payload["runner"]) != RUNNER_KEYS:
        raise ToolchainError("toolchain lock runner keys are invalid")
    if not isinstance(payload["tools"], dict) or frozenset(payload["tools"]) != TOOL_KEYS:
        raise ToolchainError("toolchain lock tool keys are invalid")
    for section in (payload["runner"], payload["tools"]):
        for key, value in section.items():
            if not isinstance(value, str) or not value or not value.isascii():
                raise ToolchainError(f"toolchain lock {key} is not a canonical string")
    for key, value in payload["tools"].items():
        if VERSION.fullmatch(value) is None:
            raise ToolchainError(f"toolchain lock {key} is not a canonical version: {value!r}")
    return payload, hashlib.sha256(data).hexdigest()


def command_output(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=15,
            env={**os.environ, "LC_ALL": "C", "LANG": "C"},
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ToolchainError(f"unable to run {' '.join(command)}: {error}") from error
    return completed.stdout.strip()


def first_match(pattern: str, text: str, label: str) -> str:
    match = re.search(pattern, text, re.MULTILINE)
    if match is None:
        raise ToolchainError(f"unable to parse {label} version from: {text!r}")
    return match.group(1)


def os_version_id() -> str:
    values: dict[str, str] = {}
    for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
    value = values.get("VERSION_ID", "")
    if not value:
        raise ToolchainError("/etc/os-release lacks VERSION_ID")
    return value


def observe(compiler: str) -> dict[str, Any]:
    observed_tools = {
        "cmake": first_match(
            r"^cmake version ([^\s]+)$", command_output(["cmake", "--version"]), "cmake"
        ),
        "ninja": command_output(["ninja", "--version"]),
        "python": platform.python_version(),
        "git": first_match(
            r"^git version ([^\s]+)$", command_output(["git", "--version"]), "git"
        ),
        "openssl": first_match(
            r"^OpenSSL ([^\s]+)", command_output(["openssl", "version"]), "openssl"
        ),
        "libssl_dev_package": command_output(
            ["dpkg-query", "-W", "-f=${Version}", "libssl-dev"]
        ),
        "gcc": first_match(
            r"^([0-9][^\s]*)$",
            command_output(["g++", "-dumpfullversion", "-dumpversion"]),
            "gcc",
        ),
        "clang": first_match(
            r"(?:Ubuntu clang version|clang version) ([0-9][^\s]*)",
            command_output(["clang++", "--version"]),
            "clang",
        ),
    }
    selected = {
        "base": None,
        "gcc": observed_tools["gcc"],
        "clang": observed_tools["clang"],
    }[compiler]
    return {
        "schema": "heptatrader.hosted-toolchain-observation.v1",
        "runner": {
            "image_os": os.environ.get("ImageOS", ""),
            "image_version": os.environ.get("ImageVersion", ""),
            "os_version_id": os_version_id(),
        },
        "tools": observed_tools,
        "selected_compiler": compiler,
        "selected_compiler_version": selected,
    }


def compare(lock: dict[str, Any], observed: dict[str, Any], compiler: str) -> list[str]:
    errors: list[str] = []
    for key, expected in lock["runner"].items():
        actual = observed["runner"].get(key)
        if actual != expected:
            errors.append(f"runner.{key}: expected {expected!r}, observed {actual!r}")
    required_tools = {"cmake", "ninja", "python", "git", "openssl", "libssl_dev_package"}
    if compiler in {"gcc", "clang"}:
        required_tools.add(compiler)
    for key in sorted(required_tools):
        expected = lock["tools"][key]
        actual = observed["tools"].get(key)
        if actual != expected:
            errors.append(f"tools.{key}: expected {expected!r}, observed {actual!r}")
    return errors


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--lock", type=Path, default=Path("ci/hosted-toolchain.lock.json")
    )
    parser.add_argument("--compiler", choices=("base", "gcc", "clang"), default="base")
    parser.add_argument("--write-observed", type=Path)
    args = parser.parse_args()

    try:
        lock, lock_sha256 = load_lock(args.lock)
        if FULL_SHA256.fullmatch(lock_sha256) is None:
            raise ToolchainError("internal lock digest failure")
        observed = observe(args.compiler)
        observed["lock_sha256"] = lock_sha256
        errors = compare(lock, observed, args.compiler)
        if args.write_observed:
            atomic_json(args.write_observed, observed)
        if errors:
            raise ToolchainError("; ".join(errors))
    except (OSError, ToolchainError) as error:
        print(f"ERROR: CI toolchain rejected: {error}", file=sys.stderr)
        return 1

    print(
        "CI toolchain PASS: "
        f"image={observed['runner']['image_version']} compiler={args.compiler} "
        f"lock_sha256={lock_sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
