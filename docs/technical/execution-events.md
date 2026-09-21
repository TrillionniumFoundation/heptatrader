# Execution events technical reference

Status: CURRENT  
Applies to: `HeptaTrade/events/` and `HeptaTrade/execution/execution_event_feed*`

## Purpose and ownership

Execution events are ordered, owner-scoped observations emitted after the Execution Service has accepted a state transition. They are not mutation commands and cannot directly call a venue. The in-process hub owns bounded publication; the Unix feed owns transport; the Gateway relay exposes only the caller's authorized execution domain, agent, and session.

## Event identity and ordering

Each accepted event has a monotonically increasing sequence within its feed, an execution domain, owner identity, event type, venue, instrument, order correlation, status, fill quantities, average price, and bounded detail. Consumers resume with an `after_sequence` cursor. A cursor older than retained history fails explicitly rather than silently returning a partial timeline.

Venue callbacks may be duplicated or arrive out of order. Adapters normalize callback state before publication. Publication order is the order accepted by the hub, not proof of exchange time. Authoritative snapshots and the OMS journal remain the sources used for economic recovery.

## Concurrency and backpressure

Publishers and waiters are synchronized without holding a lock across Broker I/O. Queues are bounded. Slow or disconnected readers cannot grow process memory without limit. Shutdown fences response publication, wakes queue waiters, and joins the accept and worker threads. Individual request reads, event waits and writes retain their configured bounds; `Stop()` itself has no aggregate wall-clock deadline and must not be described as a timed-drain result API.

The queue condition-variable predicate is `m_stop || !m_pendingClients.empty()`.
Both queue changes and shutdown publication use `m_mutex`, the mutex held by
`WorkerLoop` while checking that predicate. An atomic stop flag alone is not
sufficient: a notify between a false predicate check and the worker's atomic
unlock/wait can otherwise be lost, leaving `Stop()` blocked in a join.

Normal shutdown acquires `m_responseMutex` first to retain the final-response
fence, then briefly acquires `m_mutex` to publish stop. Neither lock is held
across notification or any thread join. Workers release the queue mutex before
handling requests or acquiring the response fence, so there is no reverse nested
lock order. Partial worker-start failure publishes stop under the same queue
mutex before notifying and joining the workers that did start. These changes do
not grant an event read, reset a cursor, modify a journal or authorize a mutation.

## Failure matrix

- malformed frame or unsupported protocol version: reject before dispatch;
- unknown owner/domain/session: reject without data disclosure;
- stale cursor: explicit history-loss result;
- timeout: typed wait timeout, not an empty successful event;
- socket replacement or peer-identity mismatch: fail closed;
- callback conflict: invalidate affected state and require reconciliation.

## Tests and diagnostics

`tests/execution_event_hub_tests.cpp`, `tests/execution_event_feed_tests.cpp`, `tests/agent_simulator_e2e_tests.cpp`, and Gateway composition tests cover ordering, cursor behavior, owner isolation, timeout, transport shutdown, and full simulator lifecycle. The feed test also performs 24 start/stop cycles on the same server with one,
two, four or eight workers, covering not-ready, immediate idle and post-identity
response shutdown. It checks repeated stop, owned descriptor closure and that
unrequested owner events remain unread. This is scheduling stress, not a claim
that every interleaving is exercised; test timeouts remain a failure boundary,
not an operational shutdown guarantee.

Metrics should expose published events, dropped/rejected events, retained depth, oldest/newest sequence, active waiters, and drain duration.

## Exact wire reference

See [`wire-operation-contracts.md`](wire-operation-contracts.md) for HEV2 v2
framing, exact Wait/identity request fields, timeout units and golden examples;
[`wire-field-reference.md`](wire-field-reference.md) contains generated field
tags and producer bindings. Sequence continuity, service identity and read
status must be evaluated together before treating an event as current state.
