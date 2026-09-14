# Typed venue placement binding

Status: CURRENT
Applies to: in-process coordinator, simulator and IB PAPER composition
Implementation: `HeptaTrade/execution/venue_placement.h`, `HeptaTrade/execution/venue_place_result.h`
Tests: `tests/venue_placement_cases.h`, `tests/execution_coordinator_tests.cpp`, `tests/python/test_venue_place_rejection.py`

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
| Rejected(code, detail, optional id) | definitive no-send rejection with valid classification and nonempty diagnostic | append existing rejection record; no activation |
| Uncertain(reason, optional id) | possible effect, exception or missing evidence | preserve uncertain journal state, fence new risk and reconcile |

Dispatch still follows durable intent and durable send attempt. A callback
exception is not rejection. A success with a negative ID, unknown disposition,
Submitted from a reserving binding, Reserved from an immediate binding, invalid
rejection classification, or empty definitive rejection becomes uncertain.
It never causes a fallback send. Duplicate/restart handling continues to use
the original command identity.

Activation returns `VenueActivationResult`: Activated or uncertain failure.
A failed or throwing activation and a missing durable activation receipt use
the existing uncertain path. The coordinator never decides to send a second
order because the first result is malformed. A reserved order remains inert
until durable ownership and the activation-pending receipt exist.

## Stable rejection codes, separate diagnostic prose

`VenuePlaceRejection` is a fixed in-process classification, not a serialized
integer. `VenuePlaceRejectionCode` maps it to the existing strings:

| Classification | Existing wire / journal reason |
|---|---|
| Generic | `IB_PLACE_REJECT` |
| KillSwitchEngaged | `IB_PAPER_KILL_SWITCH_ENGAGED` |
| PostFillRefreshPending | `IB_POST_FILL_RISK_REFRESH_PENDING` |
| KillSwitchUncertain | `IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN` |
| QuoteBindingRequired | `IB_PAPER_PLACE_QUOTE_BINDING_REQUIRED` |
| ContractMismatch | `IB_PAPER_PLACE_CONTRACT_MISMATCH` |
| QuoteChangedBeforeSend | `IB_PAPER_PLACE_QUOTE_CHANGED_BEFORE_SEND` |

New producers may use `Rejected(code, detail, id)`. The existing string factory
remains a narrow source-compatibility conversion: exact old known strings map
once to their classification and every other string keeps the historical
generic fallback. No diagnostic substring matching is allowed. Changing a
result's detail afterwards cannot change its machine reason. The coordinator
no longer owns a string set or infers classification from human prose.

A malformed enum returns no code and is uncertain, not generic rejection.
This classification cannot turn a possible send into a proven no-send outcome.
Current adapter locks, risk checks and all durable boundaries still apply.

## Producer migration and locks

The simulator constructs its typed result under its venue mutex. Its old
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

The historical boolean placement interfaces remain for direct compatibility
callers. They are not coordinator fallback fields. Cancellation now has its
own [typed contract](venue-cancellation-contract.md); the separately guarded
flatten path retains its existing callbacks and error contract.

## Executable evidence

Maintained simulator, coordinator, tool-host and registry test writers use the
typed binding directly. The old monolith and watchdog are
[retired](legacy-retirement.md), not retained production consumers.
Constructor tests prove half-configured bindings fail before a journal write.
Hostile-result tests run the real coordinator and journal, retry the same ID,
restart and verify persistent uncertainty without a second venue call.
The native suite also edits diagnostic prose, verifies every preserved reason
in actual rejection records, restarts, and checks duplicate/conflicting IDs.
The Python compiler-backed vectors test the result factories independently.

Existing owner projection, activation, receipt failure, uncertain send, final
risk, idempotency and recovery tests remain. SDK-free IB callback fixtures are
not a substitute for a real SDK-linked Broker qualification campaign.
