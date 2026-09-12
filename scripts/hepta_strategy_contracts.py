#!/usr/bin/env python3

"""Strict, deterministic helpers for read-only strategy artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any


MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


class ContractError(RuntimeError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ContractError("STRATEGY_JSON_DUPLICATE_KEY")
        document[key] = value
    return document


def _reject_constant(_value: str) -> None:
    raise ContractError("STRATEGY_JSON_NON_FINITE")


def _finite_json_float(token: str) -> float:
    """JSON constants and exponent overflow are different parser paths."""
    value = float(token)
    if not math.isfinite(value):
        raise ContractError("STRATEGY_JSON_NON_FINITE")
    if value == 0.0 and any(digit in "123456789" for digit in token.lower().split("e", 1)[0]):
        raise ContractError("STRATEGY_JSON_NUMBER_UNDERFLOW")
    return value


def _file_identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read_document_bytes(path: Path, label: str, maximum_bytes: int) -> bytes:
    """Bound allocation and reject special/replaced leaves before parsing.

    Parent directories remain caller-owned trust inputs; this is not a
    privileged arbitrary-path loader or a substitute for deployment isolation.
    """
    if isinstance(maximum_bytes, bool) or not isinstance(maximum_bytes, int) or maximum_bytes < 1:
        raise ContractError(f"{label}_LIMIT_INVALID")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        descriptor = os.open(path, flags)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise ContractError(f"{label}_FILE_INVALID")
            if before.st_size > maximum_bytes:
                raise ContractError(f"{label}_TOO_LARGE")
            contents = bytearray()
            while len(contents) <= maximum_bytes:
                chunk = os.read(descriptor, min(65536, maximum_bytes + 1 - len(contents)))
                if not chunk:
                    break
                contents.extend(chunk)
            if len(contents) > maximum_bytes:
                raise ContractError(f"{label}_TOO_LARGE")
            if (_file_identity(before) != _file_identity(os.fstat(descriptor)) or
                    _file_identity(before) != _file_identity(path.lstat())):
                raise ContractError(f"{label}_FILE_CHANGED")
            return bytes(contents)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise ContractError(f"{label}_READ_FAILED") from error


def canonical_bytes(document: Any) -> bytes:
    try:
        return (
            json.dumps(
                document,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ContractError("STRATEGY_CANONICALIZATION_FAILED") from error


def digest_bytes(contents: bytes) -> str:
    return "sha256:" + hashlib.sha256(contents).hexdigest()


def digest_document(document: Any) -> str:
    return digest_bytes(canonical_bytes(document))


def digest_file(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def load_document(
    path: Path,
    label: str,
    maximum_bytes: int = MAX_DOCUMENT_BYTES,
) -> dict[str, Any]:
    contents = _read_document_bytes(path, label, maximum_bytes)
    try:
        value = json.loads(
            contents.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_finite_json_float,
        )
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ContractError(f"{label}_JSON_INVALID") from error
    if not isinstance(value, dict):
        raise ContractError(f"{label}_ROOT_INVALID")
    return value


def atomic_write_json(path: Path, document: Any, mode: int = 0o600) -> None:
    contents = canonical_bytes(document)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False,
        ) as output:
            temporary = Path(output.name)
            os.fchmod(output.fileno(), mode)
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def require_exact_fields(
    value: Any,
    fields: set[str] | frozenset[str],
    reason: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ContractError(reason)
    return value


def require_text(
    value: Any,
    reason: str,
    *,
    maximum: int = 2048,
    identifier: bool = False,
) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ContractError(reason)
    if identifier and IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ContractError(reason)
    return value


def require_digest(value: Any, reason: str) -> str:
    if not isinstance(value, str) or DIGEST_PATTERN.fullmatch(value) is None:
        raise ContractError(reason)
    return value


def require_int(
    value: Any,
    reason: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(reason)
    if minimum is not None and value < minimum:
        raise ContractError(reason)
    if maximum is not None and value > maximum:
        raise ContractError(reason)
    return value


def require_number(
    value: Any,
    reason: str,
    *,
    positive: bool = False,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(reason)
    try:
        number = float(value)
    except (OverflowError, ValueError) as error:
        raise ContractError(reason) from error
    if not math.isfinite(number):
        raise ContractError(reason)
    if positive and number <= 0.0:
        raise ContractError(reason)
    if minimum is not None and number < minimum:
        raise ContractError(reason)
    if maximum is not None and number > maximum:
        raise ContractError(reason)
    return number


def require_bool(value: Any, expected: bool, reason: str) -> bool:
    if value is not expected:
        raise ContractError(reason)
    return expected
