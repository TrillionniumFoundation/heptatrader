# Release simulator installation and rollback smoke

Status: CURRENT  
Implementation: `scripts/run_release_simulator_smoke.py`, canonical CMake install tree  
Tests: `tests/python/test_release_simulator_smoke.py`, `tests/agent_simulator_e2e_tests.cpp`

## Scope

This is a Broker-free dynamic release test. It is intentionally later than artifact preflight and earlier than any IB PAPER campaign. It proves that the exact verified archive can be extracted into immutable slots, selected atomically, execute the installed Agent/Gateway/Execution/simulator lifecycle, roll back, and be promoted again.

## Phases

1. copy the caller artifact through a stable no-follow regular-file descriptor into a private digest-bound snapshot;
2. run artifact-only `hepta-preflight` against that private snapshot and require a non-authorizing PASS bound to the approved digest;
3. extract only regular canonical archive members from the same snapshot into a new candidate slot;
4. seed a previous slot from the same verified artifact for deterministic rollback-mechanism testing;
5. atomically point `current` at the candidate and execute the installed `hepta_agent_simulator_e2e_tests`;
6. switch to the previous slot and execute the same E2E lifecycle;
7. switch back to the candidate and execute it again;
8. publish a no-replace mode-`0600` deployment record.

The installed E2E program starts real Unix sockets, session-supervisor logic, Tool Gateway dispatch, Execution coordination, deterministic order fill/cancel behavior, service restart, journal replay, owner reconciliation, and final-flat assertions.

## Failure and authorization

A failed candidate run or deployment-record publication attempts to restore the previous slot. No deployment record is published on failure. Archive links or special files, source-path replacement during snapshot, non-executable smoke binaries, existing slots, output replacement, digest drift, or an authorizing preflight receipt fail closed.

The record always states `broker_mutation=false`, `authorization_effect=NONE`, `paper_authorized=false`, and `live_authorized=false`. Passing this smoke is not IB PAPER qualification.
