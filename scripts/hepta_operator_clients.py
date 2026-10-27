#!/usr/bin/env python3
"""Select an operator release's pinned clients without changing global current."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

REQUIRE_PINNED_CLIENTS = False  # A prepared operator copy fixes this to True.
INACTIVE_RUNTIME_SCHEMA = "heptatrader.inactive-fenced-owner-runtime.v1"
INACTIVE_RUNTIME_SOURCES = frozenset({
    "HeptaTrade/oms_journal.cpp",
    "HeptaTrade/execution/execution_coordinator.cpp",
    "HeptaTrade/execution/execution_coordinator_recovery.cpp",
    "HeptaTrade/execution/ib_paper_execution_runtime_composition.cpp",
    "HeptaTrade/execution/ib_paper_execution_runtime_policy.cpp",
    "HeptaTrade/execution/ib_paper_execution_profile.cpp",
    "HeptaTrade/adapter_ib/ib_crypto_settlement_observation.h",
    "HeptaTrade/adapter_ib/ib_gateway_adapter_event_state.cpp",
})


def _read(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or before.st_nlink != 1 or before.st_mode & 0o022
                or before.st_size > 2 * 1024 * 1024):
            raise ValueError("OPERATOR_RELEASE_FILE_UNSAFE")
        parts = []
        total = 0
        while chunk := os.read(fd, 65536):
            total += len(chunk)
            if total > before.st_size:
                raise ValueError("OPERATOR_RELEASE_FILE_CHANGED")
            parts.append(chunk)
        after = os.fstat(fd)
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size,
                                  value.st_mtime_ns, value.st_ctime_ns,
                                  value.st_mode, value.st_uid, value.st_nlink)
        if identity(before) != identity(after):
            raise ValueError("OPERATOR_RELEASE_FILE_CHANGED")
        return b"".join(parts)
    finally:
        os.close(fd)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("OPERATOR_MANIFEST_DUPLICATE_KEY")
        result[key] = value
    return result


def load_config(root: Path):
    """An absent declaration keeps the legacy paths; a bad declaration refuses."""
    path = root / "MANIFEST.json"
    if not os.path.lexists(path):
        if REQUIRE_PINNED_CLIENTS:
            raise ValueError("OPERATOR_CLIENT_DECLARATION_REQUIRED")
        return None
    manifest = json.loads(_read(path), object_pairs_hook=_unique)
    if not isinstance(manifest, dict):
        raise ValueError("OPERATOR_MANIFEST_INVALID")
    if "native_clients" not in manifest:
        if REQUIRE_PINNED_CLIENTS:
            raise ValueError("OPERATOR_CLIENT_DECLARATION_REQUIRED")
        if "native_startup_guard" in manifest:
            raise ValueError("OPERATOR_CLIENT_DECLARATION_INCOMPLETE")
        return None
    files = manifest.get("files")
    required = {"heptatrader-user", "hepta_maintenance_latch.py",
                "hepta_operator_clients.py", "hepta_native_reader.py",
                "hepta_verified_loader.py"}
    if not isinstance(files, dict) or not required.issubset(files):
        raise ValueError("OPERATOR_RELEASE_SOURCE_PINS_MISSING")
    for name, expected in files.items():
        if (not isinstance(name, str) or Path(name).name != name or name in {".", ".."}
                or not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None
                or hashlib.sha256(_read(root / name)).hexdigest() != expected):
            raise ValueError("OPERATOR_RELEASE_SOURCE_CHANGED")
    from hepta_verified_loader import load_verified_module
    reader = load_verified_module("hepta_operator_pinned_reader",
                                  root / "hepta_native_reader.py", files["hepta_native_reader.py"])
    reader.validate_client_bindings(manifest["native_clients"])
    guard = manifest.get("native_startup_guard")
    reader.validate_binary_binding(guard)
    # This is the original configured startup helper, not the new CLI's copy.
    # Its exact bytes and file identity remain part of the existing unit proof.
    with reader.verified_binary(guard):
        pass
    result = {"native_clients": manifest["native_clients"], "native_startup_guard": guard,
              "files": files}
    runtime = manifest.get("inactive_owner_runtime")
    if runtime is not None:
        if (not isinstance(runtime, dict) or set(runtime) != {
                "schema", "execution_binary", "build_receipt", "source_commit", "source_leaf_sha256"}
                or runtime.get("schema") != INACTIVE_RUNTIME_SCHEMA
                or "hepta_inactive_owner.py" not in files
                or not isinstance(runtime.get("source_commit"), str)
                or re.fullmatch(r"[0-9a-f]{40}", runtime["source_commit"]) is None):
            raise ValueError("INACTIVE_OWNER_RUNTIME_BINDING_INVALID")
        reader.validate_binary_binding(runtime["execution_binary"])
        reader.validate_binary_binding(runtime["build_receipt"])
        leaves = runtime["source_leaf_sha256"]
        if (not isinstance(leaves, dict) or not INACTIVE_RUNTIME_SOURCES.issubset(leaves)
                or len(leaves) > 128 or any(not isinstance(name, str)
                    or name.startswith("/") or ".." in Path(name).parts
                    or not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
                    for name, value in leaves.items())):
            raise ValueError("INACTIVE_OWNER_RUNTIME_SOURCE_BINDING_INVALID")
        # Deployment supplies the existing build evidence after comparing the
        # compiled leaves. The explicit inactive path also verifies this
        # receipt and the actual running image on every native proof.
        result["inactive_owner_runtime"] = runtime
    return result


def exec_client(name: str, arguments: list[str], root: Path):
    config = load_config(root)
    if config is None:
        binary = str(Path.home() / ".local/share/heptatrader/current/bin" / name)
        os.execv(binary, [binary, *arguments])
    if name not in config["native_clients"]:
        raise ValueError("OPERATOR_CLIENT_NAME_INVALID")
    binding = config["native_clients"][name]
    from hepta_verified_loader import load_verified_module
    reader = load_verified_module("hepta_operator_exec_reader", root / "hepta_native_reader.py",
                                  config["files"]["hepta_native_reader.py"])
    with reader.verified_binary(binding) as fd:
        # Retain the caller's argv and command identity. Only exec's image is FD-bound.
        os.set_inheritable(fd, True)
        os.execve("/proc/self/fd/" + str(fd), [binding["path"], *arguments], os.environ)


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in {"heptactl", "hepta-sessionctl", "hepta-paper-command"}:
        raise ValueError("OPERATOR_CLIENT_NAME_INVALID")
    exec_client(sys.argv[1], sys.argv[2:], Path(__file__).resolve().parent)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"status": "error", "reason_code": str(error)[:200]}), file=sys.stderr)
        sys.exit(2)
