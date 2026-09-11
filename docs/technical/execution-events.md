# Execution events technical reference

Status: CURRENT  
Applies to: `HeptaTrade/events/` and `HeptaTrade/execution/execution_event_feed*`

## Purpose and ownership

Execution events are ordered, owner-scoped observations emitted after the Execution Service has accepted a state transition. They are not mutation commands and cannot directly call a venue. The in-process hub owns bounded publication; the Unix feed owns transport; the Gateway relay exposes only the caller's authorized execution domain, agent, and session.

## Event identity and ordering

Each accepted event has a monotonically increasing sequence within its feed, an execution domain, owner identity, event type, venue, instrument, order correlation, status, fill quantities, average price, and bounded detail. Consumers resume with an `after_sequence` cursor. A cursor older than retained history fails explicitly rather than silently returning a partial timeline.

Venue callbacks may be duplicated or arrive out of order. Adapters normalize callback state before publication. Publication order is the order accepted by the hub, not proof of exchange time. Authoritative snapshots and the OMS journal remain the sources used for economic recovery.

## Concurrency and backpressure

Publishers and waiters are synchronized without holding a lock across Broker I/O. Queues are bounded. Slow or disconnected readers cannot grow process memory without limit. Shutdown closes admission, wakes waiters, drains accepted work within a timeout, and reports incomplete drain as a failure.

## Failure matrix

- malformed frame or unsupported protocol version: reject before dispatch;
- unknown owner/domain/session: reject without data disclosure;
- stale cursor: explicit history-loss result;
- timeout: typed wait timeout, not an empty successful event;
- socket replacement or peer-identity mismatch: fail closed;
- callback conflict: invalidate affected state and require reconciliation.

## Tests and diagnostics

`tests/execution_event_hub_tests.cpp`, `tests/execution_event_feed_tests.cpp`, `tests/agent_simulator_e2e_tests.cpp`, and Gateway composition tests cover ordering, cursor behavior, owner isolation, timeout, transport shutdown, and full simulator lifecycle. Metrics should expose published events, dropped/rejected events, retained depth, oldest/newest sequence, active waiters, and drain duration.
