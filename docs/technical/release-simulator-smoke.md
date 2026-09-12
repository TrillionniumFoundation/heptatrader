# Release component integration and same-artifact slot-switch smoke

Status: CURRENT  
Implementation: `scripts/run_release_simulator_smoke.py`, canonical CMake install tree  
Tests: `tests/python/test_release_simulator_smoke.py`, `tests/agent_simulator_e2e_tests.cpp`

## Scope

This is a Broker-free dynamic release test. It is intentionally later than artifact preflight and earlier than any IB PAPER campaign. It proves that the exact verified archive can be extracted into immutable slots, selected atomically, and execute the installed Agent/Gateway/Execution/simulator lifecycle. It also checks rollback and re-promotion pointer mechanics without pretending that rerunning the same artifact is an N-1 compatibility test.

## Phases

1. copy the caller artifact through a stable no-follow regular-file descriptor into a private digest-bound snapshot;
2. run artifact-only `hepta-preflight` against that private snapshot and require a non-authorizing PASS bound to the approved digest;
3. extract only regular canonical archive members from the same snapshot into a new candidate slot;
4. seed a previous slot from the same verified artifact so pointer rollback mechanics can be tested without introducing an unverified binary;
5. atomically point `current` at the candidate and execute the installed `hepta_agent_simulator_e2e_tests`;
6. switch to the previous slot and verify the current pointer resolves to that slot;
7. switch back to the candidate and verify the current pointer resolves to the candidate;
8. publish a no-replace mode-`0600` deployment record.

The installed E2E program starts real Unix sockets, session-supervisor logic, Tool Gateway dispatch, Execution coordination, deterministic order fill/cancel behavior, service restart, journal replay, owner reconciliation, and final-flat assertions. The `previous` slot is deliberately content-identical to the candidate; an actual N-1 rollback remains a deployment prerequisite and must use a separately verified prior artifact whose journal/lease schema is compatible.

## Failure and authorization

A failed candidate run or deployment-record publication attempts to restore the previous slot. No deployment record is published on failure. Archive links or special files, source-path replacement during snapshot, non-executable smoke binaries, existing slots, output replacement, digest drift, or an authorizing preflight receipt fail closed.

The record always states `broker_mutation=false`, `authorization_effect=NONE`, `paper_authorized=false`, and `live_authorized=false`. Passing this smoke is not IB PAPER qualification.

## Separate installed-process evidence

The installed unit-test executable above is component integration, not proof that the installed daemons were launched as separate processes. [Installed process acceptance](installed-process-acceptance.md) now exercises the real CLI/MCP/Gateway/Execution processes and one distinct-source artifact pair separately. The two tests must retain these different evidence scopes.
