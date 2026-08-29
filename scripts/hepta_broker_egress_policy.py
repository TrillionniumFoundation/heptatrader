#!/usr/bin/env python3
"""Apply the fixed HeptaTrader broker-port egress boundary.

The policy protects only configured local broker API destination ports. All
other egress remains untouched. The fixed IB execution UID is the only UID
allowed to connect to those ports; every other local process is rejected.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from typing import Any


DEFAULT_POLICY = Path(
    "/usr/share/heptatrader/hepta-broker-network-policy-v1.json")
NFT_CANDIDATES = (Path("/usr/sbin/nft"), Path("/sbin/nft"))
MAX_POLICY_BYTES = 16 * 1024
COMMAND_TIMEOUT_SECONDS = 5
SAFE_ENV = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
}
NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")


class PolicyError(RuntimeError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> Any:
    raise ValueError(f"non-finite JSON number: {value}")


def _read_policy(path: Path) -> dict[str, Any]:
    try:
        metadata = os.lstat(path)
    except OSError as error:
        raise PolicyError(f"cannot inspect policy: {error}") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size < 2
        or metadata.st_size > MAX_POLICY_BYTES
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise PolicyError("unsafe policy metadata")
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        raw = b""
        while len(raw) <= MAX_POLICY_BYTES:
            chunk = os.read(
                descriptor, min(65536, MAX_POLICY_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw += chunk
    finally:
        os.close(descriptor)
    if len(raw) > MAX_POLICY_BYTES:
        raise PolicyError("policy exceeds size limit")
    stable = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_size",
        "st_mtime_ns", "st_ctime_ns")
    if any(getattr(metadata, field) != getattr(opened, field)
           for field in stable):
        raise PolicyError("policy changed while reading")
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise PolicyError(f"invalid policy JSON: {error}") from error
    if not isinstance(value, dict):
        raise PolicyError("policy root must be an object")
    return value


def _validate_policy(
    value: dict[str, Any],
) -> tuple[str, str, str, tuple[int, ...], tuple[int, ...]]:
    expected = {
        "schema",
        "version",
        "family",
        "table",
        "chain",
        "protected_tcp_destination_ports",
        "authorized_uids",
        "preserve_other_egress",
    }
    if set(value) != expected:
        raise PolicyError("policy fields mismatch")
    family = value.get("family")
    table = value.get("table")
    chain = value.get("chain")
    ports = value.get("protected_tcp_destination_ports")
    uids = value.get("authorized_uids")
    if (
        value.get("schema") != "hepta.broker-network-policy.v1"
        or value.get("version") != 1
        or family != "inet"
        or not isinstance(table, str)
        or NAME.fullmatch(table) is None
        or not isinstance(chain, str)
        or NAME.fullmatch(chain) is None
        or value.get("preserve_other_egress") is not True
        or not isinstance(ports, list)
        or not ports
        or not isinstance(uids, list)
        or not uids
    ):
        raise PolicyError("invalid policy")
    if any(type(port) is not int or not 1 <= port <= 65535
           for port in ports):
        raise PolicyError("invalid protected port")
    if any(type(uid) is not int or not 1 <= uid <= 2**31 - 1
           for uid in uids):
        raise PolicyError("invalid authorized UID")
    normalized_ports = tuple(sorted(set(ports)))
    normalized_uids = tuple(sorted(set(uids)))
    if len(normalized_ports) != len(ports) or len(normalized_uids) != len(uids):
        raise PolicyError("duplicate policy value")
    return family, table, chain, normalized_ports, normalized_uids


def _nft_binary(explicit: str | None) -> Path:
    if explicit:
        candidate = Path(explicit)
        if not candidate.is_absolute() or not candidate.exists():
            raise PolicyError("--nft must name an existing absolute path")
        return candidate
    for candidate in NFT_CANDIDATES:
        if candidate.exists():
            return candidate
    discovered = shutil.which("nft", path=SAFE_ENV["PATH"])
    if discovered:
        return Path(discovered)
    raise PolicyError("nft executable not found")


def _run(
    nft: Path,
    arguments: list[str],
    stdin: bytes | None = None,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        [str(nft), *arguments],
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=SAFE_ENV,
        timeout=COMMAND_TIMEOUT_SECONDS,
        check=False,
    )
    if check and result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise PolicyError(f"nft failed: {detail or result.returncode}")
    return result


def _table_exists(nft: Path, family: str, table: str) -> bool:
    result = _run(nft, ["list", "table", family, table], check=False)
    return result.returncode == 0


def _ruleset(
    family: str,
    table: str,
    chain: str,
    ports: tuple[int, ...],
    uids: tuple[int, ...],
    *,
    deny_all: bool,
    replace_existing: bool,
) -> bytes:
    port_set = ", ".join(str(port) for port in ports)
    uid_set = ", ".join(str(uid) for uid in uids)
    lines: list[str] = []
    if replace_existing:
        lines.append(f"delete table {family} {table}")
    lines.extend([
        f"add table {family} {table}",
        f"add chain {family} {table} {chain} "
        "{ type filter hook output priority 0; policy accept; }",
    ])
    if not deny_all:
        lines.append(
            f"add rule {family} {table} {chain} "
            f"tcp dport {{ {port_set} }} meta skuid {{ {uid_set} }} accept")
    lines.append(
        f"add rule {family} {table} {chain} "
        f"tcp dport {{ {port_set} }} reject with tcp reset")
    return ("\n".join(lines) + "\n").encode("ascii")


def _apply(
    nft: Path,
    policy: tuple[str, str, str, tuple[int, ...], tuple[int, ...]],
    *,
    deny_all: bool,
) -> None:
    family, table, chain, ports, uids = policy
    batch = _ruleset(
        family,
        table,
        chain,
        ports,
        uids,
        deny_all=deny_all,
        replace_existing=_table_exists(nft, family, table),
    )
    _run(nft, ["-f", "-"], batch)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--nft")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--apply", action="store_true")
    action.add_argument("--deny-all", action="store_true")
    arguments = parser.parse_args()

    try:
        policy = _validate_policy(_read_policy(arguments.policy))
        nft = _nft_binary(arguments.nft)
        if arguments.deny_all:
            _apply(nft, policy, deny_all=True)
            return 0
        try:
            _apply(nft, policy, deny_all=False)
        except (OSError, subprocess.SubprocessError, PolicyError):
            _apply(nft, policy, deny_all=True)
            raise
        return 0
    except (OSError, subprocess.SubprocessError, PolicyError) as error:
        print(f"hepta-broker-egress-policy: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
