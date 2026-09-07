# Deterministic simulator

Status: CURRENT  
Applies to: repository HEAD  
Implementation: `HeptaTrade/simulator/`, `HeptaTrade/execution/hepta_executiond.cpp`  
Tests: `tests/agent_simulator_e2e_tests.cpp`, `tests/simulator_risk_runtime_tests.cpp`

## Responsibilities

The deterministic simulator is the canonical local development venue. It exercises the same Agent, Gateway, Execution, risk, journal, event, snapshot, and command-id paths without a broker credential or network connection.

It is distinct from the legacy Pegasus simulator under top-level `HeptaSimulator/`, which is excluded from the default build.

## Venue contract

The simulator implements deterministic order acceptance, fill/cancel behavior, positions, active orders, and event publication from explicitly supplied inputs. Identical initial state, command sequence, and fault configuration must produce identical outcomes.

A simulator result is evidence about the software path, not evidence that an IB, CTP, XT, or LIVE venue would behave identically.

## Runtime

`hepta-executiond` composes the simulator venue with the Execution Service. Agent access still goes through `hepta-tool-gatewayd`; tests may compose the components in process for deterministic end-to-end coverage.

The shared `hepta_simulator_runtime` target supplies the venue and generic risk evaluator. `hepta_execution_service_runtime` supplies the production composition used by both the daemon and runtime IPC tests. The production loop advances submitted, fill and cancel events every 20 ms or faster, independently of the configured quote refresh interval. Every event binds the durable owner; a journal or event projection failure disables service admission. Terminal owner removal commits to the journal before the in-memory owner is removed.

Preview and final order reservation evaluate the same risk function. Final evaluation and reservation hold the venue mutex, so concurrent orders see all earlier pending reservations. Before commit, owner projection emits only `order.reserved`, never accepted evidence. The coordinator activates a reservation only after durable `place_sent` and owner creation; event processing skips unactivated reservations. The final command result requires the durable activation receipt. Event sinks run outside the venue mutex. The lock order is coordinator then venue during reservation and activation; event publication releases the venue mutex before calling the coordinator.

The production fixture accepts only the reviewed EUR.USD and GBP.USD CASH contracts in USD. Its limits are 1,000 units per order, USD 2,500 per order, USD 10,000 worst-case gross notional and 10,000 admitted orders per retained journal. Holdings and pending buys/sells participate in the gross calculation. Quote observations, unit metadata and identity-bound notional evidence come from the venue; Agent reference values cannot supply their authority. Unsupported instruments and unavailable marks fail closed. The journal-backed order count is conservative across day boundaries; restarting does not reset that budget.

The simulator has no broker credential, broker port, or PAPER/LIVE authorization. Its Unix identities and sockets must not inherit the IB execution service's network or credential permissions.

## State and persistence

Execution mutations use the same durable journal contract as broker-backed execution. Simulator venue state may be reconstructed from deterministic fixtures and the journal, but command idempotency and owner fences remain authoritative in the journal.

Startup restores positions from durable simulated fill records and the admitted-order count from unique `place_sent` order IDs. Exact duplicate fills are applied once; conflicting side, quantity, instrument or price and fills without a matching admission reject startup. Active orders are not revived after restart: the existing complete empty active-order reconciliation durably terminalizes their owners while preserved fills and admission counts continue to constrain risk.

Fixtures should declare instruments, initial positions, quote sequence, fill policy, timing, and injected failures. The venue constructor accepts a clock provider; deterministic risk, freshness and activation tests use a controlled clock.

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

The risk/runtime suite verifies concurrent pending-order admission, preview/final parity, unsupported units, stale evidence, activation ordering and reentrant event sinks. It also starts the actual runtime composition, places and cancels through authenticated local IPC, observes automatically pumped events after quote TTL rollover, and replays terminal owner records. The production IPC portion requires a non-root Gateway identity, matching the daemon policy.

## Known limitations

The current simulator is an execution venue simulator, not a full exchange microstructure or historical market replay engine. Latency, queue position, market impact, borrow availability, exchange halts, and fee schedules require explicit models before strategy performance claims can rely on them.
