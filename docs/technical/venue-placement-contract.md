# Typed venue placement binding

Status: CURRENT
Applies to: in-process coordinator, simulator and IB PAPER composition
Implementation: `HeptaTrade/execution/venue_placement.h`, `HeptaTrade/execution/venue_place_result.h`
Tests: `tests/venue_placement_cases.h`, `tests/execution_coordinator_tests.cpp`

## One dependency, two explicit execution modes

`ExecutionCoordinatorCallbacks::placement` is one frozen value copied when the
coordinator is constructed. `VenuePlacement::Immediate(Submit)` describes a
venue that performs the external submission during Dispatch.
`VenuePlacement::Reserving(Submit, Activate)` describes an inert reservation
that must not fill or publish accepted evidence before activation. Both modes
receive the full privileged PlaceOrderCommand and service-generated durable
correlation string. Empty factories throw before I/O. A default binding is
explicitly disabled, for a read/control-only coordinator; placing rejects.
A reserving binding also requires the coordinator's owner-projection callback
at construction. There is no priority-based fallback between three callbacks.

These types are not a new wire protocol, persistent schema or runtime-selected
plugin. HEX1, HSS1, journal schema 4, existing command hashes and reasons remain
unchanged. Venue mode is selected only by service composition, never an Agent.

## Result semantics and dispatch order

| Result | Evidence supplied by adapter | Coordinator action |
|---|---|---|
| Submitted(id) | immediate submission accepted with a nonnegative ID | establish owner and durable receipt; report existing accepted semantics |
| Reserved(id) | inert reservation with a nonnegative ID | establish owner, commit activation-pending receipt, activate, commit activation receipt |
| Rejected(reason, optional id) | definitive no-send rejection with nonempty reason | append existing rejection record; no activation |
| Uncertain(reason, optional id) | possible effect, exception or missing evidence | preserve uncertain journal state, fence new risk and reconcile |

Dispatch still follows durable intent and durable send attempt. A callback
exception is not rejection. A success with a negative ID, unknown disposition,
Submitted from a reserving binding, Reserved from an immediate binding, or
empty definitive rejection becomes uncertain. It never causes a fallback send.
Duplicate/restart handling continues to use the original command identity.

Activation returns `VenueActivationResult`: Activated or uncertain failure.
A failed or throwing activation and a missing durable activation receipt use
the existing uncertain path. The coordinator never decides to send a second
order because the first result is malformed. A reserved order remains inert
until durable ownership and the activation-pending receipt exist.

## Producer migration and locks

The simulator now constructs its typed result under its venue mutex. Its old
boolean PlaceOrderCorrelated entry is a compatibility wrapper around that same
implementation, not a second coordinator path. The production simulator uses
the Reserving factory; tests and compatibility callers explicitly choose
activation. Existing coordinator-then-venue lock order and event publication
outside the venue mutex are unchanged.

`IbGatewayAdapter::PlaceOrderWithResult` holds the existing recursive API mutex
across PlaceOrderCorrelated and capture of its definitive rejection detail.
Thus a concurrent cancel/other adapter operation cannot replace the reason
between a boolean return and a later error read. Exceptions produce uncertain.
The production IB composition retains the privileged full-command quote
binding and its authoritative quote-send mutex. No SDK, profile, order type,
contract universe, network permission or qualification condition is expanded.

The historical boolean adapter interfaces remain for direct compatibility
callers. They are not coordinator fallback fields. Cancel/flatten retain their
existing callbacks and error contracts; this placement migration does not
claim to have redesigned those independently guarded operations.

## Executable evidence

All maintained simulator, coordinator, tool-host and registry test writers use
the typed binding directly. The old monolith's source composition is migrated
as a retained consumer, without claiming its default-off build was qualified.
Constructor tests prove half-configured bindings fail before a journal write.
Hostile-result tests run the real coordinator and journal, retry the same ID,
restart and verify persistent uncertainty without a second venue call.
Existing owner projection, activation, receipt failure, uncertain send, final
risk, idempotency and recovery tests remain. SDK-free IB callback fixtures are
not a substitute for a real SDK-linked Broker qualification campaign.
