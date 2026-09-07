# Deterministic simulator

Status: CURRENT  
Applies to: repository HEAD  
Implementation: `HeptaTrade/simulator/`, `HeptaTrade/execution/hepta_executiond.cpp`  
Tests: `tests/agent_simulator_e2e_tests.cpp`

## Responsibilities

The deterministic simulator is the canonical local development venue. It exercises the same Agent, Gateway, Execution, risk, journal, event, snapshot, and command-id paths without a broker credential or network connection.

It is distinct from the legacy Pegasus simulator under top-level `HeptaSimulator/`, which is excluded from the default build.

## Venue contract

The simulator implements deterministic order acceptance, fill/cancel behavior, positions, active orders, and event publication from explicitly supplied inputs. Identical initial state, command sequence, and fault configuration must produce identical outcomes.

A simulator result is evidence about the software path, not evidence that an IB, CTP, XT, or LIVE venue would behave identically.

## Runtime

`hepta-executiond` composes the simulator venue with the Execution Service. Agent access still goes through `hepta-tool-gatewayd`; tests may compose the components in process for deterministic end-to-end coverage.

The simulator has no broker credential, broker port, or PAPER/LIVE authorization. Its Unix identities and sockets must not inherit the IB execution service's network or credential permissions.

## State and persistence

Execution mutations use the same durable journal contract as broker-backed execution. Simulator venue state may be reconstructed from deterministic fixtures and the journal, but command idempotency and owner fences remain authoritative in the journal.

Fixtures should declare instruments, initial positions, quote sequence, fill policy, timing, and injected failures. Wall-clock time should be replaced by a controlled clock in tests.

## Failure injection

Required deterministic fault points include:

- reject before send;
- exception during venue send;
- response loss after possible send;
- delayed/out-of-order event;
- partial fill;
- cancel race;
- reconnect/epoch change;
- journal failure before and after send attempt;
- stale quote and incomplete snapshot.

## Observability

Record the fixture identity, deterministic seed/configuration, command ID, venue order ID, event sequence, simulated timestamp, and fault point. Test output must make replay failures reproducible.

## Test expectations

The end-to-end suite verifies tool discovery, preview, place, duplicate replay, status, cancel, events, snapshots, owner fencing, and fault recovery. New Execution states must first be made reproducible in the simulator before broker qualification.

## Known limitations

The current simulator is an execution venue simulator, not a full exchange microstructure or historical market replay engine. Latency, queue position, market impact, borrow availability, exchange halts, and fee schedules require explicit models before strategy performance claims can rely on them.
