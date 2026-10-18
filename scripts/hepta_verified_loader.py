#!/usr/bin/env python3
"""Load a pinned Python module from the exact bytes that were verified."""
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

class SourceHashMismatch(RuntimeError):
    """The source buffer does not match the required immutable identity."""


_MISSING = object()


def load_verified_module(module_name: str, path: str | Path,
                         expected_sha256: str) -> ModuleType:
    """Verify once, then execute the same buffer with normal module metadata."""
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("a lowercase SHA-256 pin is required")
    path = Path(path).resolve()
    source = path.read_bytes()
    if not hmac.compare_digest(hashlib.sha256(source).hexdigest(), expected_sha256):
        raise SourceHashMismatch(f"verified Python source SHA-256 mismatch: {path}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot create Python module spec: {path}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(module_name, _MISSING)
    sys.modules[module_name] = module
    try:
        exec(compile(source, str(path), "exec", dont_inherit=True), module.__dict__)
    except BaseException:
        if previous is _MISSING:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous
        raise
    return module
