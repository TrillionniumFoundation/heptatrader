# Pocket4 original Execution owner: in-flight Broker dispatch evidence

2026-10-16. The existing native FenceSessionOwner is durable and synchronized
with the original coordinator Place/Flatten send boundary, but it fences ONLY
one authenticated agent/session owner. An already-dispatched order can be in
flight while the coordinator mutex is temporarily released for venue I/O.
An Accepted owner fence is NOT an account-global quiescence or stop permit.
The count returned by the original control operation includes in-flight
owner dispatch and owned active orders. An in-flight risk mutation can be
present even before a new Broker order ID has been projected.

This narrow patch makes the coordinator's actual dispatch bit observable
under its own mutex. A fixed-cardinality runtime metric exports
risk_mutation_dispatch_in_flight through the existing OMS report path.
An optional Prometheus gauge is emitted only for producers that actually
measured it; older missing data is never converted to observed idle.

In the PAPER profile, userScope health refuses mutation_ready=true while an
original Place/Flatten venue callback is in flight and reports the concrete
reason RISK_MUTATION_DISPATCH_IN_FLIGHT. The existing pinned native
kill-switch reader is also checked directly: an engaged or uncertain original
kill-switch must yield native_new_risk_guard_blocked=true and refuse mutation
readiness, rather than reporting readiness just because the Broker snapshots
are fresh. Exact existing owner fence results
distinguish an outstanding risk send from an installed owner-only fence with
zero observed outstanding risk. Both remain explicitly NOT STOP PERMITS.
Original idempotency, one owner, native quotes, orders and callbacks remain
unchanged. The patches never send a Broker order or unlock an old fence.

Tests pause a real Place and Flatten venue callback across the coordinator
mutex release, install the original owner fence, confirm the in-flight bit
is true, and prove the original release audit rejects until the Broker
return and terminal acknowledgement. Native reason exports and missing
producer compatibility are covered separately.

Remaining P0 issue #331: independently prove a durable, owner-authorized
account-wide no-new-entry state, global Broker active/unknown orders and
same-account snapshot, Broker-native stop survival for exposed positions,
and an owner-aware maintenance unlock/rollback. The native kill-switch
and RefuseManualStop stay in effect. This visibility and owner-fence
semantics patch is not an owner-wide maintenance-stop capability.
