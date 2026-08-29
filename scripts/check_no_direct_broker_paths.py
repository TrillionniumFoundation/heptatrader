#!/usr/bin/env python3
"""Fail closed when canonical code bypasses the execution authority boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import stat
import sys


VENDOR_MUTATION_PATTERNS = (
    (
        "IB",
        re.compile(
            r"\b(?P<symbol>placeOrder|cancelOrder|exerciseOptions|reqGlobalCancel)"
            r"\s*\("
        ),
    ),
    (
        "CTP",
        re.compile(
            r"\b(?P<symbol>Req(?:Order|ExecOrder|Quote|CombAction)"
            r"(?:Insert|Action))\s*\("
        ),
    ),
    (
        "XT_QMT",
        re.compile(
            r"\b(?P<symbol>(?:order_stock|cancel_order_stock)(?:_async)?)"
            r"\s*\("
        ),
    ),
    (
        "GENERIC_ADAPTER",
        re.compile(
            r"\b(?P<symbol>(?:submit|insert|send|place|cancel|withdraw)_order"
            r"(?:_async)?)\s*\("
        ),
    ),
    (
        "GENERIC_ADAPTER",
        re.compile(
            r"\b(?P<symbol>submitOrder|insertOrder|sendOrder|withdrawOrder)"
            r"\s*\("
        ),
    ),
)
ADAPTER_MUTATION_SYMBOL_PATTERN = re.compile(
    r"\b(?P<symbol>(?:Place|Submit|Insert|Send|Cancel|Withdraw)"
    r"[A-Za-z0-9_]*Order[A-Za-z0-9_]*)\s*\("
)
ADAPTER_MUTATION_CALL_PATTERN = re.compile(
    r"\b(?P<receiver>(?:[A-Za-z_][A-Za-z0-9_]*(?:Adapter|adapter)"
    r"|adapter|m_(?:venue|api|TradeChannel)))\s*(?:->|\.)\s*"
    r"(?P<symbol>(?:Place|Submit|Insert|Send|Cancel|Withdraw)"
    r"[A-Za-z0-9_]*Order[A-Za-z0-9_]*)\s*\("
)
SOURCE_SUFFIXES = {".cc", ".cpp", ".cxx", ".h", ".hpp", ".py", ".ps1", ".sh"}
SCANNED_SOURCE_ROOTS = ("HeptaTrade", "scripts", "adapters", "plugins")
NON_RUNTIME_SOURCE_PREFIXES = ("tests/",)
AGENT_OS_SOURCE_POLICY = "policies/heptatrader-agent-os-source-v2.json"
EXPECTED_VENDOR_MUTATION_COUNTS = {
    "HeptaTrade/adapter_ib/ib_api_wrapper.cpp": {
        ("IB", "placeOrder"): 1,
        ("IB", "cancelOrder"): 1,
    },
}
ALLOWED_ADAPTER_MUTATION_SYMBOLS = {
    "HeptaTrade/adapter_ib/ib_api_wrapper.cpp": frozenset(
        {"PlaceOrder", "CancelOrder"}
    ),
    "HeptaTrade/adapter_ib/ib_api_wrapper.h": frozenset(
        {"PlaceOrder", "CancelOrder"}
    ),
    "HeptaTrade/adapter_ib/ib_gateway_adapter.cpp": frozenset(
        {
            "CancelOrder",
        }
    ),
    "HeptaTrade/adapter_ib/ib_gateway_adapter_order_submission.cpp": frozenset(
        {
            "PlaceOrder",
            "PlaceOrderCorrelated",
            "PlaceOrderInternal",
            "SubmitValidatedOrder",
        }
    ),
    "HeptaTrade/adapter_ib/ib_gateway_adapter_reduce_only.cpp": frozenset(
        {
            "PlaceReduceOnlyOrderCorrelated",
            "PlaceOrderInternal",
        }
    ),
    "HeptaTrade/adapter_ib/ib_gateway_adapter.h": frozenset(
        {
            "PlaceOrder",
            "PlaceOrderCorrelated",
            "PlaceReduceOnlyOrderCorrelated",
            "PlaceOrderInternal",
            "SubmitValidatedOrder",
            "CancelOrder",
        }
    ),
}
FROZEN_LEGACY_ADAPTER_MUTATION_SYMBOLS = {
    "HeptaTrade/adapter_xt/xt_gateway_adapter.cpp": frozenset(
        {"PlaceOrder", "CancelOrder"}
    ),
    "HeptaTrade/adapter_xt/xt_gateway_adapter.h": frozenset(
        {"PlaceOrder", "CancelOrder"}
    ),
}
ALLOWED_ADAPTER_MUTATION_CALLS = {
    "HeptaTrade/adapter_ib/ib_gateway_adapter.cpp": frozenset(
        {
            ("m_api", "CancelOrder"),
        }
    ),
    "HeptaTrade/adapter_ib/ib_gateway_adapter_order_submission.cpp": frozenset(
        {
            ("m_api", "PlaceOrder"),
        }
    ),
    "HeptaTrade/execution/execution_service_runtime_composition.cpp": frozenset(
        {
            ("m_venue", "PlaceOrderCorrelated"),
            ("m_venue", "CancelOrder"),
        }
    ),
    "HeptaTrade/execution/ib_paper_execution_runtime_policy.cpp": frozenset(
        {
            ("m_adapter", "PlaceOrderCorrelated"),
            ("m_adapter", "PlaceReduceOnlyOrderCorrelated"),
            ("m_adapter", "CancelOrder"),
        }
    ),
}
FROZEN_LEGACY_ADAPTER_MUTATION_CALLS = {
    "HeptaTrade/HeptaDemoStrategyTrader.cpp": frozenset(
        {
            ("m_ibAdapter", "PlaceOrder"),
            ("m_ibAdapter", "PlaceOrderCorrelated"),
            ("m_ibAdapter", "CancelOrder"),
            ("m_TradeChannel", "CancelOrder"),
        }
    ),
    "HeptaTrade/order_watchdog.cpp": frozenset(
        {
            ("ctpAdapter", "CancelOrder"),
            ("ibAdapter", "CancelOrder"),
        }
    ),
}
EXPECTED_ADAPTER_MUTATION_SYMBOL_COUNTS = {
    "HeptaTrade/adapter_ib/ib_api_wrapper.cpp": {
        "PlaceOrder": 2, "CancelOrder": 2,
    },
    "HeptaTrade/adapter_ib/ib_api_wrapper.h": {
        "PlaceOrder": 1, "CancelOrder": 1,
    },
    "HeptaTrade/adapter_ib/ib_gateway_adapter.cpp": {
        "CancelOrder": 3,
    },
    "HeptaTrade/adapter_ib/ib_gateway_adapter_order_submission.cpp": {
        "PlaceOrder": 2, "PlaceOrderCorrelated": 1,
        "PlaceOrderInternal": 3, "SubmitValidatedOrder": 2,
    },
    "HeptaTrade/adapter_ib/ib_gateway_adapter_reduce_only.cpp": {
        "PlaceReduceOnlyOrderCorrelated": 1,
        "PlaceOrderInternal": 1,
    },
    "HeptaTrade/adapter_ib/ib_gateway_adapter.h": {
        "PlaceOrder": 1, "PlaceOrderCorrelated": 1,
        "PlaceReduceOnlyOrderCorrelated": 1,
        "PlaceOrderInternal": 1, "SubmitValidatedOrder": 1,
        "CancelOrder": 1,
    },
    "HeptaTrade/adapter_xt/xt_gateway_adapter.cpp": {
        "PlaceOrder": 1, "CancelOrder": 1,
    },
    "HeptaTrade/adapter_xt/xt_gateway_adapter.h": {
        "PlaceOrder": 1, "CancelOrder": 1,
    },
}
EXPECTED_ADAPTER_MUTATION_CALL_COUNTS = {
    "HeptaTrade/adapter_ib/ib_gateway_adapter.cpp": {
        ("m_api", "CancelOrder"): 2,
    },
    "HeptaTrade/adapter_ib/ib_gateway_adapter_order_submission.cpp": {
        ("m_api", "PlaceOrder"): 1,
    },
    "HeptaTrade/execution/execution_service_runtime_composition.cpp": {
        ("m_venue", "PlaceOrderCorrelated"): 1,
        ("m_venue", "CancelOrder"): 1,
    },
    "HeptaTrade/execution/ib_paper_execution_runtime_policy.cpp": {
        ("m_adapter", "PlaceOrderCorrelated"): 1,
        ("m_adapter", "PlaceReduceOnlyOrderCorrelated"): 1,
        ("m_adapter", "CancelOrder"): 1,
    },
    "HeptaTrade/HeptaDemoStrategyTrader.cpp": {
        ("m_ibAdapter", "PlaceOrder"): 1,
        ("m_ibAdapter", "PlaceOrderCorrelated"): 1,
        ("m_ibAdapter", "CancelOrder"): 1,
        ("m_TradeChannel", "CancelOrder"): 1,
    },
    "HeptaTrade/order_watchdog.cpp": {
        ("ctpAdapter", "CancelOrder"): 2,
        ("ibAdapter", "CancelOrder"): 1,
    },
}
RETIRED_ENTRYPOINTS = {
    "ib_paper_order_loop.py",
    "fx_strategy_paper.py",
    "xt_first_live_order.py",
    "xt_first_live_order_sim.py",
}
RETIRED_COMPATIBILITY_CONTRACT_VERSION = (
    "hepta.retired-compatibility-shim.v1"
)
EXPECTED_RETIRED_COMPATIBILITY_SHA256 = {
    "scripts/fx_strategy_paper.py":
        "0228addab41bd85057ad31e41d0a2f897095d1e878c697219c1d399c55c295b0",
    "scripts/ib_paper_order_loop.py":
        "e1b6b72fc4a30716f0dfb043c88e1dcfeb26667c33f42bff6d3ae22da447ea5d",
    "scripts/xt_first_live_order.py":
        "7e4dcac363ab6118abe88ec329671213964a08433dd13993adce168167d8f4d8",
    "scripts/xt_first_live_order_sim.py":
        "c144c7a744b86b3c132588f1da863605c8507bafeb8e61dd93d1fc7d7618e085",
}
FORBIDDEN_COMPATIBILITY_IMPORT_PATTERN = re.compile(
    r"^\s*(?:from|import)\s+(?:ibapi|xtquant)\b", re.MULTILINE
)
READ_ONLY_AGENT_PREFIXES = (
    "scripts/hepta_bounded_shadow_",
    "scripts/hepta_market_",
    "scripts/hepta_shadow_",
    "scripts/hepta_strategy_",
)
FORBIDDEN_READ_ONLY_NETWORK_IMPORT_PATTERN = re.compile(
    r"^\s*(?:from|import)\s+"
    r"(?:ibapi|xtquant|requests|httpx|aiohttp|socket|urllib\.request)\b",
    re.MULTILINE,
)
FORBIDDEN_READ_ONLY_BROKER_ENDPOINT_PATTERN = re.compile(
    r"(?<![0-9])(?:4001|4002|7496|7497)(?![0-9])"
)
CANONICAL_RUNNERS = {
    "scripts/strategy_iterate_paper.py",
    "scripts/run_ib_regression_round.ps1",
}
MAX_PROFILE_MANIFEST_BYTES = 16 * 1024 * 1024
HEX64 = frozenset("0123456789abcdef")
FILE_RECORD_FIELDS = frozenset({"path", "mode", "size", "sha256"})
STRICT_PROFILE_FIELDS = frozenset({
    "schema", "bundle_class", "version", "git_head", "root", "file_count",
    "files_sha256", "security_manifest_sha256",
    "security_manifest_file_count", "excluded_unsafe_tree",
    "excluded_legacy_runtime_tree",
    "excluded_nonredistributable_vendor_prefixes",
    "redistributable_vendor_metadata_allowlist",
    "nonredistributable_vendor_payload_included",
    "excluded_prebuilt_payload_paths",
    "excluded_prebuilt_overlay_prefixes",
    "compiled_payload_suffixes_denied",
    "compiled_payload_policy_version", "compiled_payload_policy_sha256",
    "prebuilt_payload_included", "paper_authorized", "live_authorized",
    "files",
})
AGENT_PROFILE_FIELDS = frozenset({
    "schema", "version", "bundle_class", "release_version", "root",
    "file_count", "files_sha256", "policy_sha256",
    "parent_strict_source", "excluded_non_product_prefixes",
    "excluded_non_product_files", "excluded_legacy_prefixes",
    "excluded_legacy_files", "paper_authorized", "live_authorized",
    "files",
})
AGENT_PARENT_FIELDS = frozenset({
    "schema", "git_head", "root", "file_count", "files_sha256",
    "bundle_sha256", "manifest_sha256",
})


def _unique_object(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError(f"duplicate JSON key: {key}")
        document[key] = value
    return document


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value is forbidden: {value}")


def _entry_exists(path: pathlib.Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _stat_identity(metadata) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_nlink, metadata.st_size, metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _stable_anchored_regular(
        root: pathlib.Path, relative: str, *, limit: int,
        require_protected: bool = False) -> tuple[bytes, int]:
    relative = _canonical_relative(relative, "anchored path")
    parts = pathlib.PurePosixPath(relative).parts
    try:
        root_before = root.lstat()
    except OSError as error:
        raise ValueError(f"cannot anchor source root safely: {error}") from error
    if (not stat.S_ISDIR(root_before.st_mode) or
            stat.S_ISLNK(root_before.st_mode)):
        raise ValueError("source root must be a real directory")
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if no_follow == 0 or directory_flag == 0:
        raise ValueError(
            "platform does not provide O_NOFOLLOW/O_DIRECTORY")
    common_flags = os.O_RDONLY | no_follow | getattr(os, "O_CLOEXEC", 0)
    directory_flags = common_flags | directory_flag
    descriptors: list[int] = []
    edges: list[tuple[int, str, tuple[int, ...]]] = []
    try:
        root_descriptor = os.open(root, directory_flags)
    except OSError as error:
        raise ValueError(
            f"cannot open source-root anchor without links: {error}") from error
    descriptors.append(root_descriptor)
    try:
        root_opened = os.fstat(root_descriptor)
        root_identity = _stat_identity(root_opened)
        if _stat_identity(root_before) != root_identity:
            raise ValueError("source root changed while opening anchor")
        parent_descriptor = root_descriptor
        for part in parts[:-1]:
            try:
                child_descriptor = os.open(
                    part, directory_flags, dir_fd=parent_descriptor)
            except OSError as error:
                raise ValueError(
                    f"cannot open anchored directory {part!r}: {error}"
                ) from error
            descriptors.append(child_descriptor)
            child_metadata = os.fstat(child_descriptor)
            if not stat.S_ISDIR(child_metadata.st_mode):
                raise ValueError(
                    f"anchored component is not a directory: {part}")
            child_identity = _stat_identity(child_metadata)
            edges.append((parent_descriptor, part, child_identity))
            parent_descriptor = child_descriptor
        try:
            descriptor = os.open(
                parts[-1], common_flags, dir_fd=parent_descriptor)
        except OSError as error:
            raise ValueError(
                f"cannot open anchored regular file {parts[-1]!r}: {error}"
            ) from error
        descriptors.append(descriptor)
        opened = os.fstat(descriptor)
        opened_identity = _stat_identity(opened)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1):
            raise ValueError(
                "must be a regular, single-link, non-symlink file")
        if opened.st_size < 0 or opened.st_size > limit:
            raise ValueError("exceeds the bounded file-size contract")
        if require_protected and opened.st_mode & 0o022:
            raise ValueError("must not be group/world writable")
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        if remaining == 0 and os.read(descriptor, 1):
            raise ValueError("grew beyond the bounded file-size contract")
        after = os.fstat(descriptor)
        after_identity = _stat_identity(after)
        if opened_identity != after_identity:
            raise ValueError("changed during stable read")
        data = b"".join(chunks)
        if len(data) != opened.st_size:
            raise ValueError("size changed during stable read")
        try:
            named_file = os.stat(
                parts[-1], dir_fd=parent_descriptor,
                follow_symlinks=False)
        except OSError as error:
            raise ValueError(
                f"anchored file name changed during read: {error}") from error
        if _stat_identity(named_file) != opened_identity:
            raise ValueError("anchored file name changed during read")
        for edge_parent, edge_name, edge_identity in reversed(edges):
            try:
                named_directory = os.stat(
                    edge_name, dir_fd=edge_parent,
                    follow_symlinks=False)
            except OSError as error:
                raise ValueError(
                    f"anchored directory changed during read: {error}"
                ) from error
            if _stat_identity(named_directory) != edge_identity:
                raise ValueError("anchored directory changed during read")
        try:
            root_after = root.lstat()
        except OSError as error:
            raise ValueError(
                f"source root changed during anchored read: {error}") from error
        if (_stat_identity(root_after) != root_identity or
                _stat_identity(os.fstat(root_descriptor)) != root_identity):
            raise ValueError("source root changed during anchored read")
        return data, stat.S_IMODE(opened.st_mode)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _valid_sha256(value, *, prefixed: bool = False) -> bool:
    if not isinstance(value, str):
        return False
    digest = value
    if prefixed:
        if not value.startswith("sha256:"):
            return False
        digest = value[7:]
    return len(digest) == 64 and all(character in HEX64 for character in digest)


def _valid_git_head(value) -> bool:
    return (
        isinstance(value, str) and len(value) == 40 and
        all(character in HEX64 for character in value)
    )


def _canonical_relative(value, label: str) -> str:
    if (not isinstance(value, str) or not value or "\0" in value or
            "\\" in value):
        raise ValueError(f"{label} is invalid")
    parsed = pathlib.PurePosixPath(value)
    if (parsed.is_absolute() or parsed.as_posix() != value or
            any(part in {"", ".", ".."} for part in parsed.parts)):
        raise ValueError(f"{label} is not canonical")
    return value


def _canonical_path_list(
        value, label: str, *, prefixes: bool = False,
        require_sorted: bool = False) -> None:
    if not isinstance(value, list) or any(
            not isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be an array of strings")
    normalized: list[str] = []
    for item in value:
        if prefixes:
            if not item.endswith("/"):
                raise ValueError(f"{label} entries must end in slash")
            _canonical_relative(item[:-1], label)
        else:
            _canonical_relative(item, label)
        normalized.append(item)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label} must be unique")
    if require_sorted and normalized != sorted(normalized):
        raise ValueError(f"{label} must be sorted")


def _validated_file_records(
        document: dict, root: pathlib.Path) -> set[str]:
    records = document.get("files")
    if not isinstance(records, list):
        raise ValueError("files must be an array")
    paths: list[str] = []
    for record in records:
        if not isinstance(record, dict) or set(record) != FILE_RECORD_FIELDS:
            raise ValueError("file record fields do not exactly match schema")
        relative = _canonical_relative(record.get("path"), "file-record path")
        if relative.startswith(".hepta/"):
            raise ValueError("internal profile marker entered file records")
        if record.get("mode") not in {"0644", "0755"}:
            raise ValueError("file-record mode is invalid")
        size = record.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError("file-record size is invalid")
        if not _valid_sha256(record.get("sha256")):
            raise ValueError("file-record SHA256 is invalid")
        paths.append(relative)
    if paths != sorted(set(paths)):
        raise ValueError("file-record paths must be sorted and unique")
    file_count = document.get("file_count")
    if (not isinstance(file_count, int) or isinstance(file_count, bool) or
            file_count <= 0 or file_count != len(records)):
        raise ValueError("file_count does not match file records")
    canonical = json.dumps(
        records, ensure_ascii=True, separators=(",", ":"),
        sort_keys=True).encode("utf-8")
    if hashlib.sha256(canonical).hexdigest() != document.get("files_sha256"):
        raise ValueError("files_sha256 does not match canonical file records")
    paths_set = set(paths)
    gate = "scripts/check_no_direct_broker_paths.py"
    if gate not in paths_set:
        raise ValueError("broker boundary gate is absent from file records")
    for record in records:
        relative = record["path"]
        try:
            data, opened_mode = _stable_anchored_regular(
                root, relative, limit=record["size"])
        except (OSError, ValueError) as error:
            raise ValueError(
                f"profile-record payload is unsafe: {relative}: {error}"
            ) from error
        if (opened_mode != int(record["mode"], 8) or
                len(data) != record["size"] or
                hashlib.sha256(data).hexdigest() != record["sha256"]):
            raise ValueError(
                f"profile-record payload drift: {relative}")
    return paths_set


def _validate_strict_profile(document: dict, root: pathlib.Path) -> set[str]:
    if set(document) != STRICT_PROFILE_FIELDS:
        raise ValueError("strict marker fields do not exactly match schema")
    version = document.get("version")
    if (document.get("schema") != "hepta.clean-source-bundle.v2" or
            document.get("bundle_class") != "strict-source-only" or
            document.get("root") != root.name or
            not isinstance(version, str) or not version or
            document.get("root") != f"heptatrader-{version}" or
            not _valid_git_head(document.get("git_head")) or
            document.get("paper_authorized") is not False or
            document.get("live_authorized") is not False):
        raise ValueError("strict marker identity/authorization is invalid")
    if (not _valid_sha256(
            document.get("security_manifest_sha256"), prefixed=True) or
            not isinstance(document.get("security_manifest_file_count"), int) or
            isinstance(document.get("security_manifest_file_count"), bool) or
            document.get("security_manifest_file_count") <= 0 or
            document.get("excluded_unsafe_tree") !=
            "compat/unsafe-direct-broker" or
            document.get("excluded_legacy_runtime_tree") != "Tools" or
            document.get("nonredistributable_vendor_payload_included")
            is not False or
            document.get("prebuilt_payload_included") is not False or
            document.get("compiled_payload_policy_version") !=
            "hepta.strict-source-payload-policy.v1" or
            not _valid_sha256(
                document.get("compiled_payload_policy_sha256"),
                prefixed=True)):
        raise ValueError("strict marker distribution boundary is invalid")
    _canonical_path_list(
        document.get("excluded_nonredistributable_vendor_prefixes"),
        "excluded vendor prefixes", prefixes=True)
    _canonical_path_list(
        document.get("redistributable_vendor_metadata_allowlist"),
        "vendor metadata allowlist")
    _canonical_path_list(
        document.get("excluded_prebuilt_payload_paths"),
        "excluded prebuilt payload paths")
    _canonical_path_list(
        document.get("excluded_prebuilt_overlay_prefixes"),
        "excluded prebuilt overlay prefixes", prefixes=True)
    suffixes = document.get("compiled_payload_suffixes_denied")
    if (not isinstance(suffixes, list) or
            any(not isinstance(item, str) or not item.startswith(".")
                for item in suffixes) or
            suffixes != sorted(set(suffixes))):
        raise ValueError("compiled payload suffix list is invalid")
    return _validated_file_records(document, root)


def _validate_agent_profile(document: dict, root: pathlib.Path) -> set[str]:
    if set(document) != AGENT_PROFILE_FIELDS:
        raise ValueError("Agent marker fields do not exactly match schema")
    release_version = document.get("release_version")
    if (document.get("schema") != "hepta.agent-os-source-bundle.v1" or
            document.get("version") != 1 or
            document.get("bundle_class") != "agent-os-source-only" or
            document.get("root") != root.name or
            not isinstance(release_version, str) or not release_version or
            document.get("root") !=
            f"heptatrader-agent-os-{release_version}" or
            not _valid_sha256(document.get("policy_sha256")) or
            document.get("paper_authorized") is not False or
            document.get("live_authorized") is not False):
        raise ValueError("Agent marker identity/authorization is invalid")
    parent = document.get("parent_strict_source")
    if (not isinstance(parent, dict) or set(parent) != AGENT_PARENT_FIELDS or
            parent.get("schema") != "hepta.clean-source-bundle.v2" or
            not _valid_git_head(parent.get("git_head")) or
            not isinstance(parent.get("root"), str) or
            parent.get("root") != f"heptatrader-{release_version}" or
            not isinstance(parent.get("file_count"), int) or
            isinstance(parent.get("file_count"), bool) or
            parent.get("file_count") <= 0 or
            not _valid_sha256(parent.get("files_sha256")) or
            not _valid_sha256(parent.get("bundle_sha256")) or
            not _valid_sha256(parent.get("manifest_sha256"))):
        raise ValueError("Agent marker strict-source parent is invalid")
    _canonical_relative(parent["root"], "Agent parent root")
    _canonical_path_list(
        document.get("excluded_non_product_prefixes"),
        "excluded non-product prefixes", prefixes=True)
    _canonical_path_list(
        document.get("excluded_non_product_files"),
        "excluded non-product files")
    _canonical_path_list(
        document.get("excluded_legacy_prefixes"),
        "excluded legacy prefixes", prefixes=True)
    _canonical_path_list(
        document.get("excluded_legacy_files"),
        "excluded legacy files")
    return _validated_file_records(document, root)


def _source_profile(
        root: pathlib.Path) -> tuple[str, set[str] | None, list[str]]:
    strict_marker = root / ".hepta" / "source-bundle-manifest.json"
    agent_marker = root / ".hepta" / "agent-os-source-manifest.json"
    strict_present = _entry_exists(strict_marker)
    agent_present = _entry_exists(agent_marker)
    if strict_present and agent_present:
        return (
            "invalid", None,
            [".hepta: strict and Agent source markers are both present"])
    if not strict_present and not agent_present:
        return "repository", None, []
    errors: list[str] = []
    if _entry_exists(root / ".git"):
        errors.append(
            ".git: Git metadata is forbidden beside a source-bundle marker")
    marker = agent_marker if agent_present else strict_marker
    kind = "agent" if agent_present else "strict"
    try:
        marker_relative = marker.relative_to(root).as_posix()
        data, _ = _stable_anchored_regular(
            root, marker_relative, limit=MAX_PROFILE_MANIFEST_BYTES,
            require_protected=True)
        document = json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant)
        if not isinstance(document, dict):
            raise ValueError("marker root must be an object")
        paths = (
            _validate_agent_profile(document, root)
            if kind == "agent" else
            _validate_strict_profile(document, root))
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        errors.append(
            f"{marker.relative_to(root).as_posix()}: invalid source profile: "
            f"{error}")
        paths = None
    if errors:
        return "invalid", None, errors
    return kind, paths, []


def _source_only_manifest_paths(
        root: pathlib.Path, *, agent_source: bool) -> set[str] | None:
    profile, paths, errors = _source_profile(root)
    expected = "agent" if agent_source else "strict"
    return paths if not errors and profile == expected else None


def _agent_os_source_only(root: pathlib.Path) -> bool:
    return _source_only_manifest_paths(
        root, agent_source=True) is not None


def _strict_source_only(root: pathlib.Path) -> bool:
    return _source_only_manifest_paths(
        root, agent_source=False) is not None


def _regular_source(path: pathlib.Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode) and
        not stat.S_ISLNK(metadata.st_mode) and
        path.suffix in SOURCE_SUFFIXES
    )


def _policy_selected_source_files(root: pathlib.Path) -> set[pathlib.Path]:
    policy = root / AGENT_OS_SOURCE_POLICY
    try:
        document = json.loads(
            policy.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
        )
    except (OSError, ValueError):
        return set()
    if (not isinstance(document, dict) or
            document.get("schema") != "hepta.agent-os-source-policy.v2"):
        return set()
    selected: set[pathlib.Path] = set()
    for relative in document.get("include_files", []):
        if not isinstance(relative, str):
            continue
        if relative.startswith(NON_RUNTIME_SOURCE_PREFIXES):
            continue
        candidate = root / relative
        if _regular_source(candidate):
            selected.add(candidate)
    for relative in document.get("include_prefixes", []):
        if not isinstance(relative, str):
            continue
        if relative.startswith(NON_RUNTIME_SOURCE_PREFIXES):
            continue
        directory = root / relative
        if not directory.is_dir() or directory.is_symlink():
            continue
        for candidate in directory.rglob("*"):
            if _regular_source(candidate):
                selected.add(candidate)
    return selected


def _source_files(root: pathlib.Path):
    selected = _policy_selected_source_files(root)
    for name in SCANNED_SOURCE_ROOTS:
        directory = root / name
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if _regular_source(path):
                selected.add(path)
    for path in root.iterdir():
        if _regular_source(path):
            selected.add(path)
    yield from sorted(selected)


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _forbidden_legacy_paths(
        root: pathlib.Path) -> tuple[set[str] | None, set[str] | None, str | None]:
    policy = root / AGENT_OS_SOURCE_POLICY
    try:
        metadata = policy.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or
                stat.S_ISLNK(metadata.st_mode) or
                metadata.st_size > 16 * 1024 * 1024):
            return None, None, "is not a bounded regular file"
        document = json.loads(
            policy.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
        )
    except (OSError, ValueError) as exc:
        return None, None, f"cannot be read safely: {exc}"
    if (not isinstance(document, dict) or
            document.get("schema") != "hepta.agent-os-source-policy.v2"):
        return None, None, "has an unsupported schema"
    forbidden_files = document.get("forbidden_files")
    forbidden_prefixes = document.get("forbidden_prefixes")
    if (not isinstance(forbidden_files, list) or
            any(not isinstance(item, str) for item in forbidden_files)):
        return None, None, "has an invalid forbidden_files list"
    if (not isinstance(forbidden_prefixes, list) or
            any(not isinstance(item, str) for item in forbidden_prefixes)):
        return None, None, "has an invalid forbidden_prefixes list"
    return set(forbidden_files), set(forbidden_prefixes), None


def _is_forbidden_legacy_path(
        relative: str,
        forbidden_files: set[str],
        forbidden_prefixes: set[str]) -> bool:
    return (
        relative in forbidden_files or
        any(relative.startswith(prefix) for prefix in forbidden_prefixes)
    )


def violations(root: pathlib.Path) -> list[str]:
    failures: list[str] = []
    profile, source_bundle_paths, profile_errors = _source_profile(root)
    failures.extend(profile_errors)
    agent_os_source_only = profile == "agent"
    if not agent_os_source_only:
        forbidden_files, forbidden_prefixes, policy_error = (
            _forbidden_legacy_paths(root)
        )
        if policy_error is not None:
            failures.append(f"{AGENT_OS_SOURCE_POLICY}: {policy_error}")
        else:
            assert forbidden_files is not None
            assert forbidden_prefixes is not None
            frozen_legacy_paths = (
                set(FROZEN_LEGACY_ADAPTER_MUTATION_CALLS) |
                set(FROZEN_LEGACY_ADAPTER_MUTATION_SYMBOLS)
            )
            for relative in sorted(frozen_legacy_paths):
                if not _is_forbidden_legacy_path(
                        relative, forbidden_files, forbidden_prefixes):
                    failures.append(
                        f"{relative}: frozen broker mutation exception is not "
                        f"forbidden by {AGENT_OS_SOURCE_POLICY}"
                    )

    for path in _source_files(root):
        relative = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="strict")
        if relative.startswith(READ_ONLY_AGENT_PREFIXES):
            match = FORBIDDEN_READ_ONLY_NETWORK_IMPORT_PATTERN.search(text)
            if match is not None:
                failures.append(
                    f"{relative}:{_line_number(text, match.start())}: "
                    "read-only Agent module imports a broker/network client"
                )
            endpoint = FORBIDDEN_READ_ONLY_BROKER_ENDPOINT_PATTERN.search(text)
            if endpoint is not None:
                failures.append(
                    f"{relative}:{_line_number(text, endpoint.start())}: "
                    "read-only Agent module embeds a broker endpoint"
                )
        observed_vendor: dict[tuple[str, str], int] = {}
        expected_vendor = EXPECTED_VENDOR_MUTATION_COUNTS.get(relative, {})
        for family, pattern in VENDOR_MUTATION_PATTERNS:
            for match in pattern.finditer(text):
                key = (family, match.group("symbol"))
                observed_vendor[key] = observed_vendor.get(key, 0) + 1
                if key in expected_vendor:
                    continue
                failures.append(
                    f"{relative}:{_line_number(text, match.start())}: direct "
                    f"{family} broker mutation symbol"
                )
        for key, expected_count in sorted(expected_vendor.items()):
            observed_count = observed_vendor.get(key, 0)
            if observed_count != expected_count:
                failures.append(
                    f"{relative}: reviewed {key[0]} broker mutation "
                    f"{key[1]} count drifted: expected {expected_count}, "
                    f"observed {observed_count}"
                )

        adapter_source = (
            relative.startswith("HeptaTrade/adapter_") or
            relative.startswith("adapters/") or
            "/adapter_" in relative or
            "/adapters/" in relative
        )
        if adapter_source:
            allowed_symbols = ALLOWED_ADAPTER_MUTATION_SYMBOLS.get(
                relative, frozenset()
            )
            frozen_legacy_symbols = (
                FROZEN_LEGACY_ADAPTER_MUTATION_SYMBOLS.get(
                    relative, frozenset()
                )
            )
            observed_symbols: dict[str, int] = {}
            for match in ADAPTER_MUTATION_SYMBOL_PATTERN.finditer(text):
                line_number = _line_number(text, match.start())
                symbol = match.group("symbol")
                observed_symbols[symbol] = observed_symbols.get(symbol, 0) + 1
                if symbol in allowed_symbols:
                    continue
                if symbol in frozen_legacy_symbols:
                    if agent_os_source_only:
                        failures.append(
                            f"{relative}:{line_number}: frozen legacy "
                            "adapter mutation symbol is present in an "
                            "Agent OS source-only bundle"
                        )
                    continue
                failures.append(
                    f"{relative}:{line_number}: unreviewed adapter "
                    f"mutation symbol {symbol}"
                )
            for symbol, expected_count in sorted(
                    EXPECTED_ADAPTER_MUTATION_SYMBOL_COUNTS.get(
                        relative, {}).items()):
                observed_count = observed_symbols.get(symbol, 0)
                if observed_count != expected_count:
                    failures.append(
                        f"{relative}: reviewed adapter mutation symbol "
                        f"{symbol} count drifted: expected {expected_count}, "
                        f"observed {observed_count}"
                    )

        allowed_calls = ALLOWED_ADAPTER_MUTATION_CALLS.get(
            relative, frozenset()
        )
        frozen_legacy_calls = FROZEN_LEGACY_ADAPTER_MUTATION_CALLS.get(
            relative, frozenset()
        )
        observed_calls: dict[tuple[str, str], int] = {}
        for match in ADAPTER_MUTATION_CALL_PATTERN.finditer(text):
            line_number = _line_number(text, match.start())
            call = (match.group("receiver"), match.group("symbol"))
            observed_calls[call] = observed_calls.get(call, 0) + 1
            if call in allowed_calls:
                continue
            if call in frozen_legacy_calls:
                if agent_os_source_only:
                    failures.append(
                        f"{relative}:{line_number}: frozen legacy broker "
                        "mutation is present in an Agent OS source-only bundle"
                    )
                continue
            failures.append(
                f"{relative}:{line_number}: unreviewed direct adapter "
                f"mutation call {call[0]}.{call[1]}"
            )
        for call, expected_count in sorted(
                EXPECTED_ADAPTER_MUTATION_CALL_COUNTS.get(
                    relative, {}).items()):
            observed_count = observed_calls.get(call, 0)
            if observed_count != expected_count:
                failures.append(
                    f"{relative}: reviewed direct adapter mutation call "
                    f"{call[0]}.{call[1]} count drifted: expected "
                    f"{expected_count}, observed {observed_count}"
                )

    if not agent_os_source_only:
        for relative in sorted(CANONICAL_RUNNERS):
            path = root / relative
            if not path.is_file():
                failures.append(
                    f"{relative}: missing canonical offline regression runner")
                continue
            text = path.read_text(encoding="utf-8", errors="strict")
            for retired in sorted(RETIRED_ENTRYPOINTS):
                if retired in text:
                    failures.append(
                        f"{relative}: references retired direct broker "
                        f"entrypoint {retired}")

    for retired in sorted(RETIRED_ENTRYPOINTS):
        relative = f"scripts/{retired}"
        path = root / "scripts" / retired
        if (source_bundle_paths is not None and
                relative not in source_bundle_paths):
            if path.exists() or path.is_symlink():
                failures.append(
                    f"{relative}: excluded compatibility shim is materialized")
            continue
        try:
            data, _ = _stable_anchored_regular(
                root, relative, limit=1024 * 1024)
        except (OSError, ValueError) as error:
            failures.append(
                f"{relative}: missing or unsafe fail-closed compatibility "
                f"shim: {error}")
            continue
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            failures.append(
                f"{relative}: compatibility shim is not strict UTF-8")
            continue
        if FORBIDDEN_COMPATIBILITY_IMPORT_PATTERN.search(text):
            failures.append(
                f"{relative}: compatibility shim imports a broker SDK")
        expected_digest = EXPECTED_RETIRED_COMPATIBILITY_SHA256.get(relative)
        observed_digest = hashlib.sha256(data).hexdigest()
        if (expected_digest is None or observed_digest != expected_digest):
            failures.append(
                f"{relative}: {RETIRED_COMPATIBILITY_CONTRACT_VERSION} "
                "exact-byte contract drift")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=pathlib.Path,
        default=pathlib.Path(__file__).resolve().parents[1],
    )
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    failures = violations(root)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("PASS: source has no unapproved direct broker mutation path")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
