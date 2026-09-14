"""Strict, bounded JSON input for repository-development checks only.

Parents are trusted checkout/build directories. This is not the credential,
archive, journal or privileged deployment reader and must not be installed as
one. Consumers retain ownership of their schemas and diagnostic categories.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import stat
from typing import Any

MAX_SOURCE_JSON_BYTES = 16 * 1024 * 1024


class SourceJsonError(ValueError):
    pass


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SourceJsonError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise SourceJsonError(f"non-finite JSON number: {value}")


def _number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise SourceJsonError("non-finite JSON exponent")
    coefficient = value.lower().partition("e")[0]
    if number == 0 and any(digit in coefficient for digit in "123456789"):
        raise SourceJsonError("nonzero JSON number underflows to zero")
    return number


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def load_source_json(path: Path | str, *, max_bytes: int = MAX_SOURCE_JSON_BYTES) -> Any:
    """Read one stable regular single-link UTF-8 file; never follow a leaf link.

    The read is bounded before parsing. JSON shape is intentionally not fixed:
    a schema may require an object or an array. Errors share one exception type.
    """
    if type(max_bytes) is not int or max_bytes < 1:
        raise SourceJsonError("JSON byte bound must be a positive integer")
    path = Path(path)
    try:
        if path.is_symlink():
            raise SourceJsonError("expected a regular single-link file, not a symlink")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, flags)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise SourceJsonError("expected a regular single-link file")
            if before.st_size > max_bytes:
                raise SourceJsonError("source JSON exceeds byte bound")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise SourceJsonError("source JSON exceeds byte bound")
            if (_identity(before) != _identity(os.fstat(fd)) or
                    _identity(before) != _identity(path.lstat())):
                raise SourceJsonError("source JSON changed while reading")
        finally:
            os.close(fd)
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_object,
                          parse_constant=_constant, parse_float=_number)
    except SourceJsonError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        raise SourceJsonError(f"cannot read JSON {path}: {error}") from error
