# Reconciliation engine technical reference

Status: CURRENT  
Applies to: `HeptaTrade/reconcile/`, authoritative snapshot consumers, and execution recovery

## Inputs and authority

Reconciliation combines the durable OMS journal with complete authoritative venue snapshots for accounts, positions, active orders, executions, and connection generation. Neither source is sufficient alone: the journal proves local intent and send attempts; the venue proves observed economic state.

## State machine

A reconnect, startup, uncertain send, callback conflict, or incomplete snapshot enters a mutation-blocked recovery state. The coordinator requests fresh snapshot barriers, correlates venue orders and executions with durable command identities, terminalizes resolved commands, preserves unresolved possible sends as uncertain, and publishes a new authoritative generation only after all required snapshot families are complete.

Risk increase remains blocked while any required family is stale, incomplete, or from a superseded connection generation. Cancel and exact reduce-only recovery operations remain available only through their explicit guarded paths.

## Matching rules

Stable command IDs, venue order IDs, account, normalized contract identity, side, quantity, price, and execution identifiers are used together. A plausible-but-ambiguous match is not accepted. Duplicate callbacks are idempotent; conflicting terminal states invalidate the projection. Unknown venue orders are surfaced for operator resolution rather than adopted silently.

## Crash and replay behavior

Journal replay rebuilds command ownership, durable send-attempt state, terminal state, and fencing before mutation admission opens. A crash between durable send attempt and observed outcome remains uncertain. Reconciliation may close that uncertainty only from authoritative evidence; it never blindly resends the command.

## Tests and operations

`tests/execution_coordinator_tests.cpp`, `tests/ib_live_terminal_reconciliation_tests.cpp`, snapshot-store tests, recovery-coordinator tests, and the installed simulator E2E smoke cover restart, duplicate/out-of-order callbacks, owner fencing, terminal replay, unresolved send blocking, and final-flat recovery. Operational records should include generation, snapshot completeness, unresolved command IDs, foreign orders, final positions, and recovery duration.
