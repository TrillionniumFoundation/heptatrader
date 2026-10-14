# Post-fill risk and recovery-audit barriers

## Distinct evidence and failures

`IB_FX_CASH_BALANCE_OUT_OF_GENERATION_CHANGE` invalidates an unscoped
account cash observation. Never promote a subscription push to a committed
cash snapshot. Normal post-fill `ReqRiskRefresh` downloads a coherent
account/positions pair but does not recreate the independent recovery audit.

`IB_PAPER_STALE_CASH_REFRESH_STREAM_UNSAFE` is instead produced by
`RefreshStaleSecondaryCashQuote`: CASH here denotes a quote contract. It
must not be described as an account-cash download failure.

## Candidate sequencing

Keep the runtime post-fill mutation gate closed during the entire sequence:

1. Match native cumulative execution, terminal evidence, expected position,
   account/positions generations, and absorbed exposure. Preserve the existing
   stability interval and persist the cash checkpoint.
2. Acknowledge the adapter live-fill latch while the runtime gate is still
   closed. Request exactly one independent recovery-audit risk pair from the
   original API client. Respect any required newer connection epoch.
3. Require the audit barrier, exact generations, expected position, and another
   stability interval before retiring the owner and releasing runtime pending.

The additional audit shares the original eight-second hard deadline. It does
not retry blindly, widen quote TTL, clear sticky callback faults, replace an
order identity, or authorize a new send from a status flag alone. Even complete
late evidence cannot pass after the deadline.

Risk-increasing final admission now requires a complete recovery audit.
Late broker callbacks continue to invalidate it, including a duplicate terminal
callback after audit completion. Such invalidation must block new risk rather
than silently reuse the old proof. Operator/new-epoch recovery remains separate.

## Reproduction and negative tests

The native postfill regression can reproduce the baseline defect with a
changed unscoped cash value followed by valid cash redownload. The original
core leaves the audit stale after its ordinary postfill ACK. The new path
requires fresh audit End markers. The same test then sends a late duplicate and
confirms that the actual risk-increasing admission gate denies it.

`hepta_ib_postfill_cash_audit_tests` runs through ordinary CTest. It includes
missing PositionEnd, mismatched position, accountReady=false, completion beyond
the hard deadline, and a newer-connection fence. These cannot release the gate.

The full-runtime synthetic suite distinguishes a scoped secondary farm outage,
an accepted but silent primary ticker, and unsafe/foreign/old-epoch callbacks.
The silent primary remains stale in the actual authoritative snapshot until
new BID and ASK callbacks arrive. Its accepted dispatch is not price evidence.
The full-runtime fixture enforces kernel network denial and issues zero order
method calls. This is offline regression, not live trading qualification.

## Operational boundary

Deployment must retain independent autonomous-driver masks and use immutable
source, dependency, binary, and journal-prefix identities. A no-order startup
canary can verify real downloads and preserved state, but does not replace a
future live postfill regression or account-wide NAV/statement reconciliation.

## Qualification record: 2026-10-14

The exact baseline commit is `486583c259484a489e4040ceba7225195834a38a`.
Rebuilding with the retained IB 10.30.01 C++ client and libbid produced the
same 5,658,288-byte native binary and SHA-256 as the running artifact:
`42045e594ffe15d9a758c5b77280059444820f620a5693dbe569f8c342e9d542`.
The original core reproduced `POSTFILL_AUDIT_STAYS_STALE_AFTER_VALID_CASH_REDOWNLOAD`.
The candidate passed the six-case cash-audit invocation, original postfill
regressions, and the 24-scenario full-runtime suite. An expanded CTest run
passed all nine selected targets.

Qualification remains HOLD. A subsequent ten-target CTest run passed nine
and failed the full-runtime reconnect scenario with
`FULL_RECONNECT_FAILED: IB_POSITIONS_REFRESH_PENDING`. A bounded diagnostic
three-run series returned failure, success, success. With additional callback
trace collection three runs passed, and the focused `--reconnect-stress` mode
passed twenty seeded reconnect/old-epoch rounds. Instrumentation changes timing;
these successes do not erase the recorded failure or prove its root cause.
No deadline or quote freshness threshold was widened to obtain a pass.
The diagnostic build retains bounded event traces on reconnect failure.

The unchanged original binary was restarted with both autonomous drivers
persistently masked. Fresh real broker downloads restored its recovery barrier,
with the pre-restart journal byte prefix and order-send counters unchanged.
This restoration is not deployment of the candidate and is not a new-fill
qualification. Keep the candidate isolated until the intermittent reconnect
failure, full current regression, owner/permission admission, and required
account-economics checks are resolved. Do not unmask drivers from a passing
process-health indicator or an isolated successful retry.
