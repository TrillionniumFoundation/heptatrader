# Atomic authoritative-flatten result

Status: CURRENT
Applies to: coordinator and IB adapter in-process composition; existing wire/OMS schemas

## Single result at the send boundary

`ExecutionCoordinatorCallbacks::flattenOrder(plan, correlation)` returns a
`VenueFlattenResult`. It replaces the Boolean/out-order-ID callback and the
coordinator's separately sampled `lastIbRejectReason`. The callback is internal
composition, not a second client API or a new trading authority.

| Disposition | Required evidence | Coordinator action |
|---|---|---|
| Submitted | Nonnegative assigned order ID | Existing owner projection and durable `flatten_sent` receipt |
| RejectedBeforeSend | Supported rejection enum, nonempty diagnostic and no assigned order ID | Existing durable `flatten_reject` with the stable mapped reason code |
| Uncertain | Possible send or missing evidence; an assigned ID may be retained | Existing `flatten_outcome_uncertain`, admission fence and authoritative reconciliation |
| Default, invalid enum, contradictory rejection or thrown callback | No proof of a no-send rejection | Uncertain; never infer rejection from unrelated diagnostic state |

`venue_flatten_result.h` owns the enum-to-existing-reason mapping. Rejection
classification is independent of diagnostic prose. The adapter converts its
stable legacy machine reason once, while still holding `m_apiMutex`; the
coordinator does not parse an error-string allowlist. Updating a returned
explanation cannot change its typed classification.

## Adapter boundary and exceptions

`PlaceReduceOnlyOrderCorrelated` checks the exact authoritative position,
connection epoch/generation, empty active-order snapshot, reduction side,
quantity, reserved correlation and final send conditions as before. Position,
quote and kill-switch protections are not removed by the result refactor.

An explicit marker is set immediately before the SDK wrapper's place call.
A false result after entering that call is conservatively uncertain, even if
the wrapper populated a legacy last-error string. Earlier known refusals remain
pre-send rejections. The wrapper and post-send bookkeeping are inside the typed
adapter's exception boundary. Once the SDK returns acceptance, the assigned ID
is captured before subsequent bookkeeping so an allocation failure does not
unnecessarily erase known correlation evidence.

The adapter's in-memory duplicate-signature streams throw on fail/bad state.
A stream allocation failure may not silently produce a partial signature and
then report successful post-send bookkeeping. The ordinary typed place path
also benefits from those checked streams; no durable request hash is changed.

## Persistence and retry

Intent and send-attempt records still precede dispatch. A possible send is not
retried with a new ID. Uncertain outcomes, including malformed results, survive
restart, return the same uncertain state on same-ID retry, and reject conflicting
ID reuse. Only authoritative correlation/terminal evidence resolves the state.
A callback that throws before returning cannot convey an unknown order ID; its
service-owned durable correlation remains available for reconciliation.

Zero-position proof and its lock-bound durable no-op callback are unchanged.
This refactor neither changes the HSL/OMS formats nor grants PAPER/LIVE authority.

## Executed regression owners

`tests/flatten_result_cases.h`, included by the existing coordinator target,
covers exceptions, missing and invalid results, contradictory rejection, negative
submitted ID, durable uncertainty, restart, same-ID no-resend, conflicting IDs
and diagnostic-independent reasons. Existing flatten projection/no-op/rate,
capacity and recovery tests remain in that same target.

`tests/ib_live_terminal_reconciliation_tests.cpp` executes the real adapter with
a synthetic wrapper: known refusal before invocation; accepted, false and thrown
post-send results; and a one-shot post-return allocation failure. It changes the
legacy diagnostic after capture without changing the returned typed result.
Its generic STK fixture does not expand the deployed single-CASH PAPER profile.

`tests/python/test_execution_reason_metrics.py` compiles the supported callback
and proves the retired last-error member is not assignable. Native core and both
sanitizer runs remain distinct from real SDK/Broker qualification.
