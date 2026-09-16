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


def _next_simulator_projection(store: Path) -> tuple[dict[str, Any], int]:
    if not store.exists():
        return _simulator.new_state(), 0
    current = v1._read_current(store)
    if current is None:
        return _simulator.new_state(), 0
    generation = current["generation"]
    root = store / generation
    manifest = v1._load_json_private(root / "manifest.json")
    if not isinstance(manifest, dict):
        raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_PARENT_INVALID")
    hot_records = manifest.get("hot_replay_records")
    if type(hot_records) is not int or hot_records < 0:
        raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_PARENT_INVALID")
    state = _simulator.checkpoint_state(root)
    if state is None:
        # One-time migration path for generations created before the simulator
        # projection existed. Later seals carry the bounded projection forward.
        state = _simulator.rebuild_parent_state(store, generation)
    return state, hot_records


def seal_generation(journal: Path, store: Path, *, stopped: bool,
                    max_bytes: int = 64 * 1024 * 1024,
                    max_records: int = 65536,
                    max_record_bytes: int = 262144,
                    phase_hook: Callable[[str], None] | None = None) -> dict[str, Any]:
    state, parent_hot_records = _next_simulator_projection(store)
    original_project_hot = _core.v1._project_hot
    first_projection = True

    def project_hot(events: list[dict[str, Any]]):
        nonlocal first_projection
        result = original_project_hot(events)
        if not first_projection:
            return result
        first_projection = False
        if len(events) < parent_hot_records:
            raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_PARENT_HOT_MISMATCH")
        for event in events[parent_hot_records:]:
            _simulator.apply_event(state, event)
        checkpoint = result[0]
        checkpoint[_simulator.SCHEMA_FIELD] = _simulator.HEADER
        checkpoint[_simulator.FIELD] = _simulator.encode_state(state).hex()
        return result

    # The core computes checkpoint/runtime/manifest digests and publishes the
    # lineage pointer only after this first projection call. Injecting here
    # preserves its original single publication transaction and crash phases.
    _core.v1._project_hot = project_hot
    try:
        return _core_seal_generation(
            journal, store, stopped=stopped, max_bytes=max_bytes,
            max_records=max_records, max_record_bytes=max_record_bytes,
            phase_hook=phase_hook)
    finally:
        _core.v1._project_hot = original_project_hot


# The core parent-state path and CLI dispatch resolve these globals at runtime.
_core.verify_generation = verify_generation
_core.seal_generation = seal_generation


def main(argv: list[str] | None = None) -> int:
    return _core.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
