#!/usr/bin/env python3
"""Canonical OMS lifecycle entry point.

The reviewed lifecycle implementation is kept in ``hepta_oms_lifecycle_core``.
This facade installs the bounded simulator recovery projection and the streaming
V2 verifier without duplicating the rest of the lifecycle state machine.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Any

import hepta_oms_lifecycle_core as _core
import hepta_oms_lifecycle_verify as _verify
import hepta_oms_simulator_recovery as _simulator

for _name in dir(_core):
    if _name not in {"verify_generation", "seal_generation", "main"}:
        globals()[_name] = getattr(_core, _name)

_core_seal_generation = _core.seal_generation
verify_generation = _verify.verify_generation


def seal_generation(journal: Path, store: Path, *, stopped: bool,
                    max_bytes: int = 64 * 1024 * 1024,
                    max_records: int = 65536,
                    max_record_bytes: int = 262144,
                    phase_hook: Callable[[str], None] | None = None) -> dict[str, Any]:
    existing = {entry.name for entry in store.iterdir() if entry.is_dir()} if store.exists() else set()
    user_hook = phase_hook or (lambda _phase: None)

    def hook(phase: str) -> None:
        if phase == "generation-durable":
            created = [entry for entry in store.iterdir()
                       if entry.is_dir() and entry.name not in existing and
                       (entry / "manifest.json").exists()]
            if len(created) != 1:
                raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_GENERATION_AMBIGUOUS")
            _simulator.augment_generation(store, created[0])
        user_hook(phase)

    return _core_seal_generation(
        journal, store, stopped=stopped, max_bytes=max_bytes,
        max_records=max_records, max_record_bytes=max_record_bytes,
        phase_hook=hook)


# The core parent-state path and CLI dispatch resolve these globals at runtime.
_core.verify_generation = verify_generation
_core.seal_generation = seal_generation


def main(argv: list[str] | None = None) -> int:
    return _core.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
