#!/usr/bin/env python3
"""Install or tighten the canonical HeptaTrader Broker egress boundary."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any

SCHEMA = "hepta.broker-network-policy.v1"
FAMILY_RE = re.compile(r"^[A-Za-z0-9_]+$")
NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_POLICY = Path(
    "/usr/share/heptatrader/hepta-broker-network-policy-v1.json"
)
CANONICAL_POLICY_SHA256 = (
    "5eddd44a588ac3269804cb62adb19c3879febce8569df30ab86886028e969e6b"
)
CANONICAL_POLICY_MODE = 0o644
CANONICAL_POLICY_OWNER_UID = 0
CANONICAL_POLICY_GROUP_GID = 0
CANONICAL_POLICY_PARENT_PARTS = ("usr", "share", "heptatrader")
NFT_CANDIDATES = (Path("/usr/sbin/nft"), Path("/sbin/nft"))
MAX_POLICY_BYTES = 16 * 1024
COMMAND_TIMEOUT_SECONDS = 5
APPLY_ATTEMPTS = 3
RULE_COMMENTS = {
    "allow_ipv4": "hepta-egress-v1-allow-ipv4",
    "allow_ipv6": "hepta-egress-v1-allow-ipv6",
    "deny_ipv4": "hepta-egress-v1-deny-ipv4",
    "deny_ipv6": "hepta-egress-v1-deny-ipv6",
}
COMPILED_POLICY = (
    "inet",
    "hepta_broker_egress_v1",
    "output",
    (4001, 4002, 7496, 7497),
    (2003,),
)

PolicyTuple = tuple[
    str, str, str, tuple[int, ...], tuple[int, ...]
]


class PolicyError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PolicyError(f"duplicate key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise PolicyError(f"non-finite number: {value}")


def _metadata_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _same_metadata(
    left: os.stat_result, right: os.stat_result
) -> bool:
    return _metadata_identity(left) == _metadata_identity(right)


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        stat.S_IFMT(left.st_mode),
    ) == (
        right.st_dev,
        right.st_ino,
        stat.S_IFMT(right.st_mode),
    )


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _policy_file_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )


def _clear_nonblocking(descriptor: int) -> None:
    nonblocking = getattr(os, "O_NONBLOCK", 0)
    if not nonblocking:
        return
    flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
    if flags & nonblocking:
        fcntl.fcntl(
            descriptor, fcntl.F_SETFL, flags & ~nonblocking
        )


def _validate_directory(
    metadata: os.stat_result,
    label: str,
    expected_uid: int,
    expected_gid: int,
) -> None:
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or metadata.st_gid != expected_gid
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise PolicyError(
            f"{label} must be an owner/group-bound directory "
            "that is not group/world writable"
        )


def _open_policy_parent(
    root_descriptor: int,
    *,
    expected_uid: int,
    expected_gid: int,
) -> int:
    current = os.dup(root_descriptor)
    try:
        for part in CANONICAL_POLICY_PARENT_PARTS:
            following = os.open(
                part, _directory_flags(), dir_fd=current
            )
            metadata = os.fstat(following)
            _validate_directory(
                metadata,
                f"canonical policy parent component {part}",
                expected_uid,
                expected_gid,
            )
            os.close(current)
            current = following
        return current
    except Exception:
        os.close(current)
        raise


def _read_bounded_policy(descriptor: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(
            descriptor,
            min(64 * 1024, MAX_POLICY_BYTES - total + 1),
        )
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > MAX_POLICY_BYTES:
            raise PolicyError("policy exceeds size bound")
        chunks.append(chunk)


def _canonical_id(value: Any, label: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
    ):
        raise PolicyError(f"{label} must be a non-negative integer")
    return value


def _read_policy(
    path: Path,
    *,
    root: Path = Path("/"),
    expected_directory_uid: int = CANONICAL_POLICY_OWNER_UID,
    expected_directory_gid: int = CANONICAL_POLICY_GROUP_GID,
    expected_policy_uid: int = CANONICAL_POLICY_OWNER_UID,
    expected_policy_gid: int = CANONICAL_POLICY_GROUP_GID,
    expected_sha256: str = CANONICAL_POLICY_SHA256,
) -> Any:
    if os.fspath(path) != os.fspath(DEFAULT_POLICY):
        raise PolicyError(
            "policy path must be exactly "
            + os.fspath(DEFAULT_POLICY)
        )
    if not root.is_absolute():
        raise PolicyError("policy root must be absolute")
    directory_uid = _canonical_id(
        expected_directory_uid, "expected directory UID"
    )
    directory_gid = _canonical_id(
        expected_directory_gid, "expected directory GID"
    )
    policy_uid = _canonical_id(
        expected_policy_uid, "expected policy UID"
    )
    policy_gid = _canonical_id(
        expected_policy_gid, "expected policy GID"
    )
    if SHA256_RE.fullmatch(expected_sha256) is None:
        raise PolicyError("expected policy SHA-256 is invalid")

    root_descriptor = -1
    parent_descriptor = -1
    confirmation_descriptor = -1
    policy_descriptor = -1
    try:
        root_descriptor = os.open(root, _directory_flags())
        root_metadata = os.fstat(root_descriptor)
        _validate_directory(
            root_metadata,
            "policy root",
            directory_uid,
            directory_gid,
        )
        root_current = os.stat(root, follow_symlinks=False)
        if not _same_inode(root_metadata, root_current):
            raise PolicyError(
                "policy root identity changed during admission"
            )

        parent_descriptor = _open_policy_parent(
            root_descriptor,
            expected_uid=directory_uid,
            expected_gid=directory_gid,
        )
        parent_metadata = os.fstat(parent_descriptor)
        policy_descriptor = os.open(
            DEFAULT_POLICY.name,
            _policy_file_flags(),
            dir_fd=parent_descriptor,
        )
        pinned = os.fstat(policy_descriptor)
        current = os.stat(
            DEFAULT_POLICY.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(pinned.st_mode)
            or pinned.st_nlink != 1
            or pinned.st_uid != policy_uid
            or pinned.st_gid != policy_gid
            or stat.S_IMODE(pinned.st_mode)
            != CANONICAL_POLICY_MODE
            or pinned.st_dev != parent_metadata.st_dev
            or pinned.st_size <= 0
            or pinned.st_size > MAX_POLICY_BYTES
            or not _same_metadata(pinned, current)
        ):
            raise PolicyError(
                "policy must be the canonical root-owned mode-0644 "
                "regular single-link file"
            )

        _clear_nonblocking(policy_descriptor)
        raw = _read_bounded_policy(policy_descriptor)
        after = os.fstat(policy_descriptor)
        current_after = os.stat(
            DEFAULT_POLICY.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            len(raw) != pinned.st_size
            or not _same_metadata(pinned, after)
            or not _same_metadata(pinned, current_after)
        ):
            raise PolicyError(
                "policy identity changed while being read"
            )

        confirmation_descriptor = _open_policy_parent(
            root_descriptor,
            expected_uid=directory_uid,
            expected_gid=directory_gid,
        )
        confirmed_parent = os.fstat(confirmation_descriptor)
        confirmed_policy = os.stat(
            DEFAULT_POLICY.name,
            dir_fd=confirmation_descriptor,
            follow_symlinks=False,
        )
        if (
            not _same_metadata(
                parent_metadata, confirmed_parent
            )
            or not _same_metadata(pinned, confirmed_policy)
        ):
            raise PolicyError(
                "policy parent or final path changed during read"
            )

        observed_sha256 = hashlib.sha256(raw).hexdigest()
        if observed_sha256 != expected_sha256:
            raise PolicyError(
                "policy SHA-256 does not match the compiled "
                "canonical policy"
            )
        try:
            return json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
        except (
            UnicodeError,
            json.JSONDecodeError,
            PolicyError,
        ) as error:
            raise PolicyError(
                f"invalid canonical policy JSON: {error}"
            ) from error
    finally:
        if policy_descriptor >= 0:
            os.close(policy_descriptor)
        if confirmation_descriptor >= 0:
            os.close(confirmation_descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
        if root_descriptor >= 0:
            os.close(root_descriptor)


def _validate_policy(value: Any) -> PolicyTuple:
    fields = {
        "schema",
        "version",
        "family",
        "table",
        "chain",
        "protected_tcp_destination_ports",
        "authorized_uids",
        "preserve_other_egress",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise PolicyError("policy fields are not canonical")
    if value.get("schema") != SCHEMA or value.get("version") != 1:
        raise PolicyError("unsupported policy schema/version")
    if value.get("preserve_other_egress") is not True:
        raise PolicyError(
            "policy must preserve unrelated destination ports"
        )

    family = value.get("family")
    table = value.get("table")
    chain = value.get("chain")
    if (
        not isinstance(family, str)
        or FAMILY_RE.fullmatch(family) is None
        or not isinstance(table, str)
        or NAME_RE.fullmatch(table) is None
        or not isinstance(chain, str)
        or NAME_RE.fullmatch(chain) is None
    ):
        raise PolicyError("nftables family/table/chain is invalid")

    ports = value.get("protected_tcp_destination_ports")
    if (
        not isinstance(ports, list)
        or not ports
        or any(
            not isinstance(port, int)
            or isinstance(port, bool)
            or port < 1
            or port > 65535
            for port in ports
        )
        or ports != sorted(set(ports))
    ):
        raise PolicyError(
            "protected port list must be sorted and unique"
        )

    uids = value.get("authorized_uids")
    if (
        not isinstance(uids, list)
        or not uids
        or any(
            not isinstance(uid, int)
            or isinstance(uid, bool)
            or uid <= 0
            for uid in uids
        )
        or uids != sorted(set(uids))
    ):
        raise PolicyError(
            "authorized UID list must be sorted and unique"
        )
    return (
        family,
        table,
        chain,
        tuple(ports),
        tuple(uids),
    )


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
    del chain
    port_set = ", ".join(str(port) for port in ports)
    uid_set = ", ".join(str(uid) for uid in uids)
    lines = []
    if replace_existing:
        lines.append(f"delete table {family} {table}")
    lines.extend(
        [
            f"add table {family} {table}",
            f"add chain {family} {table} output "
            "{ type filter hook output priority -150; policy accept; }",
        ]
    )
    if not deny_all:
        lines.extend(
            [
                f"add rule {family} {table} output "
                f"ip daddr 127.0.0.0/8 tcp dport {{ {port_set} }} "
                f"meta skuid {{ {uid_set} }} accept comment "
                f'"{RULE_COMMENTS["allow_ipv4"]}"',
                f"add rule {family} {table} output "
                f"ip6 daddr ::1 tcp dport {{ {port_set} }} "
                f"meta skuid {{ {uid_set} }} accept comment "
                f'"{RULE_COMMENTS["allow_ipv6"]}"',
            ]
        )
    lines.extend(
        [
            f"add rule {family} {table} output "
            f"ip daddr 127.0.0.0/8 tcp dport {{ {port_set} }} "
            "reject with tcp reset comment "
            f'"{RULE_COMMENTS["deny_ipv4"]}"',
            f"add rule {family} {table} output "
            f"ip6 daddr ::1 tcp dport {{ {port_set} }} "
            "reject with tcp reset comment "
            f'"{RULE_COMMENTS["deny_ipv6"]}"',
        ]
    )
    return ("\n".join(lines) + "\n").encode("ascii")

def _nft_binary(requested: Path | None) -> Path:
    candidates = (requested,) if requested is not None else NFT_CANDIDATES
    for candidate in candidates:
        if (
            candidate is not None
            and candidate.is_absolute()
            and candidate.is_file()
            and not candidate.is_symlink()
            and os.access(candidate, os.X_OK)
        ):
            return candidate
    raise PolicyError("trusted nft binary is unavailable")


def _run_nft(
    nft: Path, payload: bytes
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [str(nft), "-f", "-"],
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=COMMAND_TIMEOUT_SECONDS,
        check=False,
    )


def _run_nft_query(
    nft: Path, arguments: tuple[str, ...]
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [str(nft), "-j", "-n", "-y", *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=COMMAND_TIMEOUT_SECONDS,
        check=False,
    )


def _nft_json_objects(
    result: subprocess.CompletedProcess[bytes], label: str
) -> list[dict[str, Any]]:
    if result.returncode != 0:
        detail = result.stdout.decode("utf-8", "replace")
        raise PolicyError(
            f"{label} failed ({result.returncode}): "
            f"{detail[-2000:]}"
        )
    try:
        value = json.loads(
            result.stdout.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (
        UnicodeError,
        json.JSONDecodeError,
        PolicyError,
    ) as error:
        raise PolicyError(
            f"{label} returned invalid JSON: {error}"
        ) from error
    objects = value.get("nftables") if isinstance(value, dict) else None
    if not isinstance(objects, list) or any(
        not isinstance(item, dict) for item in objects
    ):
        raise PolicyError(f"{label} returned a non-canonical object set")
    return objects


def _table_exists(nft: Path, family: str, table: str) -> bool:
    objects = _nft_json_objects(
        _run_nft_query(nft, ("list", "tables", family)),
        "nftables table inventory",
    )
    matches = []
    for item in objects:
        if set(item) == {"metainfo"}:
            continue
        candidate = item.get("table")
        if not isinstance(candidate, dict):
            raise PolicyError(
                "nftables table inventory contains an unexpected object"
            )
        if (
            candidate.get("family") == family
            and candidate.get("name") == table
        ):
            matches.append(candidate)
    if len(matches) > 1:
        raise PolicyError("nftables table inventory is ambiguous")
    return bool(matches)


def _integer_tuple(value: Any, label: str) -> tuple[int, ...]:
    if isinstance(value, dict) and set(value) == {"set"}:
        value = value["set"]
    if isinstance(value, int) and not isinstance(value, bool):
        return (value,)
    if (
        isinstance(value, list)
        and value
        and all(
            isinstance(item, int) and not isinstance(item, bool)
            for item in value
        )
    ):
        return tuple(sorted(value))
    raise PolicyError(f"invalid {label} expression in nftables readback")


def _network(value: Any, protocol: str) -> str:
    if isinstance(value, dict) and set(value) == {"prefix"}:
        prefix = value["prefix"]
        if not isinstance(prefix, dict):
            raise PolicyError("invalid network prefix readback")
        address = prefix.get("addr")
        length = prefix.get("len")
        if not isinstance(address, str) or not isinstance(length, int):
            raise PolicyError("invalid network prefix readback")
        candidate = f"{address}/{length}"
    elif isinstance(value, str):
        candidate = value
        if "/" not in candidate:
            candidate += "/128" if protocol == "ip6" else "/32"
    else:
        raise PolicyError("invalid network expression in nftables readback")
    try:
        network = ipaddress.ip_network(candidate, strict=False)
    except ValueError as error:
        raise PolicyError("invalid network in nftables readback") from error
    expected_version = 6 if protocol == "ip6" else 4
    if network.version != expected_version:
        raise PolicyError("network family mismatch in nftables readback")
    return str(network)


def _rule_projection(rule: dict[str, Any]) -> tuple[Any, ...]:
    expressions = rule.get("expr")
    if not isinstance(expressions, list):
        raise PolicyError("nftables rule expression list is missing")
    network = None
    ports = None
    uids = None
    verdict = None
    for statement in expressions:
        if not isinstance(statement, dict) or len(statement) != 1:
            raise PolicyError("nftables rule contains an unexpected statement")
        if "match" in statement:
            match = statement["match"]
            if not isinstance(match, dict) or match.get("op") not in {
                "==",
                "in",
            }:
                raise PolicyError("nftables match operator is unexpected")
            left = match.get("left")
            right = match.get("right")
            if not isinstance(left, dict) or len(left) != 1:
                raise PolicyError("nftables match left side is unexpected")
            payload = left.get("payload")
            metadata = left.get("meta")
            if isinstance(payload, dict):
                protocol = payload.get("protocol")
                field = payload.get("field")
                if protocol in {"ip", "ip6"} and field == "daddr":
                    if network is not None:
                        raise PolicyError("duplicate network match")
                    network = _network(right, protocol)
                elif protocol == "tcp" and field == "dport":
                    if ports is not None:
                        raise PolicyError("duplicate port match")
                    ports = _integer_tuple(right, "port set")
                else:
                    raise PolicyError("unexpected payload match")
            elif isinstance(metadata, dict) and metadata.get("key") == "skuid":
                if uids is not None:
                    raise PolicyError("duplicate UID match")
                uids = _integer_tuple(right, "UID set")
            else:
                raise PolicyError("unexpected nftables match expression")
        elif "accept" in statement and statement["accept"] is None:
            if verdict is not None:
                raise PolicyError("duplicate nftables verdict")
            verdict = "accept"
        elif "reject" in statement:
            reject = statement["reject"]
            if (
                not isinstance(reject, dict)
                or reject.get("type") != "tcp reset"
                or set(reject) - {"type", "expr"}
            ):
                raise PolicyError("unexpected nftables reject verdict")
            if verdict is not None:
                raise PolicyError("duplicate nftables verdict")
            verdict = "reject"
        else:
            raise PolicyError("unexpected nftables rule statement")
    if network is None or ports is None or verdict is None:
        raise PolicyError("incomplete nftables rule readback")
    if verdict == "accept" and uids is None:
        raise PolicyError("allow rule is missing UID scope")
    if verdict == "reject" and uids is not None:
        raise PolicyError("deny rule unexpectedly contains UID scope")
    return network, ports, uids, verdict


def _expected_rule_projection(
    policy: PolicyTuple, *, deny_all: bool
) -> dict[str, tuple[Any, ...]]:
    _, _, _, ports, uids = policy
    expected = {
        RULE_COMMENTS["deny_ipv4"]: (
            "127.0.0.0/8",
            ports,
            None,
            "reject",
        ),
        RULE_COMMENTS["deny_ipv6"]: (
            "::1/128",
            ports,
            None,
            "reject",
        ),
    }
    if not deny_all:
        expected.update(
            {
                RULE_COMMENTS["allow_ipv4"]: (
                    "127.0.0.0/8",
                    ports,
                    uids,
                    "accept",
                ),
                RULE_COMMENTS["allow_ipv6"]: (
                    "::1/128",
                    ports,
                    uids,
                    "accept",
                ),
            }
        )
    return expected


def _verify_table(
    nft: Path,
    policy: PolicyTuple,
    *,
    deny_all: bool,
) -> None:
    family, table, chain, _, _ = policy
    objects = _nft_json_objects(
        _run_nft_query(
            nft,
            ("list", "table", family, table),
        ),
        "nftables table readback",
    )
    table_objects = []
    chain_objects = []
    rule_objects = []
    for item in objects:
        if set(item) == {"metainfo"}:
            continue
        if set(item) == {"table"} and isinstance(item["table"], dict):
            table_objects.append(item["table"])
        elif set(item) == {"chain"} and isinstance(item["chain"], dict):
            chain_objects.append(item["chain"])
        elif set(item) == {"rule"} and isinstance(item["rule"], dict):
            rule_objects.append(item["rule"])
        else:
            raise PolicyError(
                "nftables readback contains an unexpected object"
            )
    if len(table_objects) != 1:
        raise PolicyError("nftables readback table count mismatch")
    observed_table = table_objects[0]
    if (
        observed_table.get("family") != family
        or observed_table.get("name") != table
        or observed_table.get("flags", []) not in ([], None)
    ):
        raise PolicyError("nftables readback table identity mismatch")
    if len(chain_objects) != 1:
        raise PolicyError("nftables readback chain count mismatch")
    observed_chain = chain_objects[0]
    if (
        observed_chain.get("family") != family
        or observed_chain.get("table") != table
        or observed_chain.get("name") != chain
        or observed_chain.get("type") != "filter"
        or observed_chain.get("hook") != "output"
        or observed_chain.get("prio") != -150
        or observed_chain.get("policy") != "accept"
        or observed_chain.get("dev") not in (None, "")
    ):
        raise PolicyError("nftables readback chain contract mismatch")
    observed_rules: dict[str, tuple[Any, ...]] = {}
    for rule in rule_objects:
        if (
            rule.get("family") != family
            or rule.get("table") != table
            or rule.get("chain") != chain
        ):
            raise PolicyError("nftables readback rule scope mismatch")
        comment = rule.get("comment")
        if not isinstance(comment, str) or comment in observed_rules:
            raise PolicyError("nftables readback rule identity mismatch")
        observed_rules[comment] = _rule_projection(rule)
    if observed_rules != _expected_rule_projection(
        policy, deny_all=deny_all
    ):
        raise PolicyError("nftables readback rule set mismatch")


def _apply(
    nft: Path,
    policy: PolicyTuple,
    *,
    deny_all: bool,
) -> None:
    family, table, chain, ports, uids = policy
    failures: list[str] = []
    for attempt in range(APPLY_ATTEMPTS):
        try:
            replace_existing = _table_exists(nft, family, table)
            result = _run_nft(
                nft,
                _ruleset(
                    family,
                    table,
                    chain,
                    ports,
                    uids,
                    deny_all=deny_all,
                    replace_existing=replace_existing,
                ),
            )
            if result.returncode != 0:
                detail = result.stdout.decode("utf-8", "replace")
                failures.append(
                    f"attempt {attempt + 1} command failed "
                    f"({result.returncode}): {detail[-1000:]}"
                )
                continue
            _verify_table(nft, policy, deny_all=deny_all)
            return
        except (
            OSError,
            subprocess.SubprocessError,
            PolicyError,
        ) as error:
            failures.append(f"attempt {attempt + 1}: {error}")
    try:
        _verify_table(nft, policy, deny_all=deny_all)
        return
    except (
        OSError,
        subprocess.SubprocessError,
        PolicyError,
    ) as error:
        failures.append(f"final readback: {error}")
    raise PolicyError(
        "nftables target state was not verified after bounded "
        "machine-state retries: " + "; ".join(failures)
    )

def _execute(
    nft: Path,
    policy_path: Path,
    *,
    apply_requested: bool,
) -> None:
    if not apply_requested:
        _apply(nft, COMPILED_POLICY, deny_all=True)
        return

    try:
        policy = _validate_policy(_read_policy(policy_path))
        if policy != COMPILED_POLICY:
            raise PolicyError(
                "policy semantics do not match the compiled "
                "canonical deployment boundary"
            )
        _apply(nft, policy, deny_all=False)
    except (
        OSError,
        subprocess.SubprocessError,
        PolicyError,
    ) as error:
        try:
            _apply(nft, COMPILED_POLICY, deny_all=True)
        except (
            OSError,
            subprocess.SubprocessError,
            PolicyError,
        ) as fallback_error:
            raise PolicyError(
                f"{error}; deny-all fallback failed: "
                f"{fallback_error}"
            ) from error
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--policy", type=Path, default=DEFAULT_POLICY
    )
    parser.add_argument("--nft", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--deny-all", action="store_true")
    args = parser.parse_args(argv)

    try:
        nft = _nft_binary(args.nft)
        _execute(
            nft,
            args.policy,
            apply_requested=args.apply,
        )
    except (
        OSError,
        subprocess.SubprocessError,
        PolicyError,
    ) as error:
        print(f"[BROKER-EGRESS] {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
