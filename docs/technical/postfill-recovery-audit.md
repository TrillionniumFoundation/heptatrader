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

## R3: same-turn callback drain and final monotonic publication

The original reduced-priority socket-free stress fixture reproduced a reconnect
in which account and position refresh were dispatched in connection epoch 2
but its epoch-2 PositionEnd had not been dequeued at the original deadline.
The prior failure and unaccepted state remain recorded; do not rewrite history.

After ReqRecoveryAuditRiskRefresh succeeds, the reconnect owner now drains
only callbacks already queued by that client (DrainAdapterEvents(-1)). It
does not resend risk/account/quote requests or any order, does not invoke an
extra SDK polling operation, and does not expand the readiness deadline.
The resulting callback state is rejected if fatal, nonauthoritative, on a
changed epoch, disconnected, or following a lost required market-data farm.

Two deterministic timing negative controls were run against the actual
runtime methods. First, a delayed PositionEnd could previously be accepted
against the clock captured before dispatch. Now every completed callback
is checked against a fresh monotonic deadline before evaluating readiness.
Second, a delayed Ready-publication hook demonstrated that checking time
before taking the lifecycle mutex was still insufficient. The final Ready
linearization now checks time and fatal state under that same mutex.
Both controls failed before their respective fix and passed afterward.

The original exact ACTIVE_ORDERS_UNSAFE reason is retained as a hard
broker-ownership refusal, not overwritten with generic timeout. Missing
PositionEnd, callback conflict, stale price and wrong-epoch evidence remain
fail-closed. The isolated tests prohibit broker sockets and order methods.

Pocket4's real config has HEPTA_IB_EXECUTION_READINESS_TIMEOUT_MS=12000.
The synthetic harness uses a deliberately stricter 1000ms. Under severe
CPU de-prioritization, an unrelated new fixture can still fail its startup
budget: this is retained as a failed qualification observation, not a
reason to extend any production timer or fabricate a completed snapshot.

The 12 selected CTest targets including both deadline-negative cases
passed together after the reason-priority fix. This remains an offline
held candidate: there has been no automated-driver unmask, production
Execution replacement, new broker order or live post-fill qualification.
Full account-level NAV and original native owner admission remain separate
unclosed release gates.

## R3 follow-up: exact response-window origin

A further deterministic negative control delayed the socket-free
ReqAllOpenOrders broker-read dispatch in connection epoch 2.
Before correction, the reconnect response deadline was measured from an
observedNow timestamp taken before quote admission and synchronous SDK read
dispatch. The delayed-but-accepted request therefore exhausted most or
all of its response window before the broker could deliver any End marker.
The negative reproduced IB_PAPER_BROKER_RECONNECT_SNAPSHOTS_TIMEOUT.

The accepted formal broker-read dispatch now establishes the start of the
existing readinessTimeoutMs response window. This timestamp is sampled
once, only after accepted dispatch, with the same min() cap against the
unchanged m_reconnectDeadline; neither repeated polls nor a partial
callback can reset the window. The exact negative then passed.

The short response deadline is verified before accepting completed
account/position/terminal/quote snapshots. Only subsequent durable
checkpoint and owner reconciliation may proceed within the separate
unchanged overall reconnect deadline. Ready publication is guarded by
a fresh time and fatal-state check under the lifecycle mutex.

Stress outcomes must retain both clean runs and fail-closed timeouts.
The original 1000ms synthetic fixture can reject under slow scheduling;
the installed Pocket4 original execution has its separate 12000ms
readiness configuration. No production setting or strategy mask changed.

## R3 offline verification record (2026-10-14)

After final correction of the broker-read dispatch timing origin, the
unmodified 1000ms synthetic readiness threshold was retained. Both
monotonic deadline-negative tests and the delayed formal read-dispatch
regression passed alongside the existing economic/recovery/quote tests:
13 of 13 selected CTest targets passed. The socket-free seeded
reconnect/old-epoch stress mode then completed 80 of 80 rounds without
creating any broker socket or calling order placement/cancellation.

Do not discard earlier failures: before dispatch-clock repair, a
reduced-priority stress run failed with PositionEnd unconsumed; another
failed after PositionEnd because formal read dispatch work had consumed
its pre-dispatch response timer. Test-only diagnostics preserve the
order, callback epochs, and timestamps for these failures. The new
response budget starts on accepted formal read dispatch, remains
single-shot, and stays capped by the existing reconnect global
deadline. A late response or final lifecycle publication still rejects.

These results qualify only an isolated source candidate for further
review. They do not authorize automatic entry, LIVE routes, model
promotion, migration into the resident binary, or unmasking either
Pocket4 strategy driver. Account NAV, native owner authority evidence,
and an independent post-fill validation remain outstanding.
