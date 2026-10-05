# Native 0DTE options: shadow delivery and trading acceptance boundaries

Status: IMPLEMENTED SHADOW; OPTIONS TRADING NOT ENABLED
Base: user-owned PAPER line, d384e98c3a52485bfba97cd93cd89197d9872fa0
Scope: XSP and SPXW standard European, PM cash-settled options; long one-contract
Call/Put proposals, MKT/IOC; never naked option selling or a multi-leg spread.

## What is executable now

`hepta-options-observer` uses the pinned IB C++ API to qualify the underlying,
retrieve today's option chains, qualify a concrete Call and Put, subscribe to
real-time top-of-book prices/sizes and model Greeks, and record the complete
observation with its deterministic eligibility decision. It contains no call to
placeOrder, cancelOrder, reqGlobalCancel, or exerciseOptions. Connections are
restricted to local port 4002 and an explicitly verified singleton DU account.

Delayed index data is used only to choose diagnostic strikes. It is cancelled
and replaced with a separate type-1 subscription before constructing an
admission snapshot. Every delayed tick independently marks its book as delayed;
no stale/frozen/delayed value is a real-time authorization witness. Type changes
invalidate all cached prices, sizes and Greeks. Connection/data-farm loss aborts
the attempt and requires a new connection epoch and request set. Timeouts use a
monotonic clock. Broker-qualified option and chain underlying identities must
match their requests; supplied conIds are not accepted as qualification evidence. Candidate
lookup and a catalog containing MKT/IOC are not evidence of trading permission.
The observer requests no account-wide update stream and does not take over the
running Execution client's account subscription.

The zero-dte core validates full contract identity, New York trading date,
exchange-provided liquid hours and last-trade time, product settlement style,
per-side quote and size freshness, epochs, Greeks, independent premium/loss
budgets, owner/account completeness, explicit trading permission and margin
preview, and unresolved commands. Each decision records all refusal reasons.
An eligible library result remains `broker_authorized=false`: only the existing
Execution preview/permit path may grant a mutation capability.

`PreparedOrder::MarketOption` creates a one-contract BUY MKT/IOC proposal.
`IBOPT:<conId>` preserves the broker contract identifier in the canonical
instrument; expiry/right/strike/multiplier/tradingClass/localSymbol remain in the
existing rich HTT1/HEX contract fields. The server must independently resolve
that instrument and metadata. The factory does not widen the existing PAPER
security-type whitelist. Existing FX/BTC native entry paths are unchanged.

The new `EvidenceLedger` keeps individual execution shares/prices separate from
cumulative quantities/averages, deduplicates exact execution IDs, refuses
conflicting replays/corrections, handles fees arriving before or after execution,
and requires an exact join of all positive-fill terminals, individual executions
and fees. Every intermediate cumulative checkpoint must balance; checking only
the final cumulative quantity is insufficient. Unjoined fees or positive-fill
terminals prevent complete economics. A correlation cannot identify two commands.
An exact original execution replay after cash settlement remains idempotent.
Local flatness is FLAT_BROKER_AUDIT_REQUIRED, never ROUNDTRIP_PROVEN by itself. Round-trip proof consumes a complete authoritative Snapshot with exact PAPER
account, owner and contract key, a nonzero connection epoch, zero held contracts,
zero active orders and no unresolved send; a bare flat-position boolean is not
sufficient. Complete execution and fee joins remain independently required. Contract disappearance
at expiry is not a sell. A separately identified authoritative cash-settlement
receipt produces CASH_SETTLED, never a fake traded round-trip proof.

## Legacy cumulative execution repair

The current IB wrapper journals `execution.cumQty` and `execution.avgPrice` as
broker_execution qty/price. The old read model incorrectly made any second
execution ID ambiguous, freezing quantity at the first fill. The shared
`IbCumulativeExecutionAggregate` now preserves every real execution identity and
selects the greatest cumulative quantity and its cumulative average, without
summing cumulative amounts. Exact duplicate downloads and reverse arrival order
are idempotent. Same-ID conflicting economics and correction families remain
blocked rather than rewriting already-booked money silently.

The real order-159 read-only OMS-prefix replay contains cumulative 0.01 then
0.03313821 at cumulative average 86437.099117. This branch reproduces 0.03313821,
two execution IDs, Cancelled-with-partial-fill, and ambiguity=false. It does not
apply that result to production driver state or deploy the new Execution binary.
The production change remains a separate controlled rollout requiring the
original pending command and broker position audit; no hand-edited pending state.

Recent-orders JSON additionally exposes all cumulative execution witnesses and
their semantics. Its legacy scalar names the real last cumulative witness, not
a fabricated aggregate execution ID. Reconciliation of an absent active owner
no longer converts a partially filled Cancelled order into Filled.

## Native capture/replay

H0D1 records contain 80 typed input fields plus the deterministic expected
result. The full contract, exchange session, policy, each quote timestamp/epoch,
Greeks and permission/budget gates are recorded with precision-17 classic-locale
numbers. Files are private, no-follow, single-writer and fsynced per complete
record. A decoder rejects schema/value/expected-result drift. The replay command
also rejects an unterminated tail; a crashed writer cannot turn a partial last
record into accepted evidence.

These captures are reproducibility artifacts, not a second trading OMS or a
signed authorization. Replay requires the matching evaluator version. Changing
a historical policy or feeding a diagnostic price as live data cannot produce
credit for the original decision. Missing market data currently gives NO_TRADE;
there is not yet enough real-time data to validate a strategy's profitability.

The private runner `heptatrader_options_shadow.py` records one bounded native
observation and replays it. Its single-writer lock prevents competing processes
from replacing the active status. It requires one successful PAPER account check,
one no-order-capability start, exactly one completion, an appropriate exit code,
zero order sends, and exact native replay record/readiness counts. A partial log,
reported error, failed process, interrupted attempt or count mismatch is ERROR,
not a successful observation. It writes a machine-readable status with the binary
SHA-256, capture path, counts, broker notices, and explicit false PAPER round-trip
and order-submission capability fields. It never launches a trading driver.

## Risk and calendar contract

The options premium and daily-loss budgets default to ZERO / UNSET. Do not reuse
BTC's USD 25,000 cash quantity or FX's 25,000 base units. No option entry is allowed
until the operator sets independent budgets and a privileged runtime validates
the account, permissions and margin preview. A displayed ask-based MKT cost is an
estimate, not a guaranteed final fill-price cap.

Entry cutoff is 60 minutes before the qualified last-trade time; planned exit is
30 minutes before it. These are initial engineering parameters, not an optimized
strategy. Half-day sessions use their actual earlier close. Exchange timezone
parsing supports US Eastern and Central, using post-2007 US DST rules for
2007-2099; the runtime must be updated if those timezone rules change. Unknown
zones, invalid dates, spring gaps and ambiguous fall-back local times fail.
Host midnight is not the New York trading-day boundary.

Exact reduce-only requires a positive actual option quantity and the same
complete contract key. It does not require a new-entry budget, fresh model
opinion, or nonzero Greeks, but it still requires authoritative state and a
valid quote. If the market is unavailable or expiry has passed, no fake zero
position is written; recovery/settlement evidence remains required.

## Tests and evidence ceilings

| Evidence | What it proves | What it does not prove |
|---|---|---|
| zero_dte_tests | Contract/date/DST/half-day/data/Greeks/permission/budget rejection; fee and partial-fill economics; late settlement; deterministic capture | IB trading permission or live fills |
| zero_dte_execution_fault_tests | Real canonical coordinator with synthetic venue; lost ACK, fork/_exit after durable send marker, no resend, exact rich identity replay, fill racing cancellation, final stale-quote rejection | Real broker round trip or production restart acceptance |
| ib_recent_execution_aggregation_tests | Same production read-model functions; multi-execution, duplicates, reverse order, conflicts, original Cancelled terminal | Deployed production repair |
| research_native_client_tests | Complete option proposal identity and MKT/IOC quantity survive HTT1 encoding; changed conId/right have different bytes | Server OPT permit or exchange acceptance |
| Native observer with actual IB connection | Actual contract discovery/market-data entitlement outcome and recorded NO_TRADE decisions | Profitability, subscribed live data when IB denies it, or account permissions |

The existing canonical coordinator cancellation/crash suites also remain active;
a cancel racing a fill cannot be called zero-fill cancellation. The new option
ledger separately tests partial fills followed by cancellation. One-contract
orders themselves cannot partially fill a fraction of a standard contract;
multiple-execution tests deliberately include larger *synthetic* fixtures and
must not be confused with permission to place larger canaries.

## Remaining work before one-contract PAPER acceptance

1. Activate and verify appropriate live underlying/option data in the operator's
   IB account and its PAPER mapping. Current actual probes return 354/10090 and
   zero usable real-time option quotes/Greeks. Do not accept agreements or buy
   subscriptions on the user's behalf without the appropriate specific approval.
2. Verify account trading permissions, and obtain options-only premium/daily-loss
   budgets. The observer cannot prove either and reports them as UNVERIFIED/UNSET.
3. Finish the privileged runtime integration: qualified option registry and
   dynamic session binding, live quote/Greeks snapshots, broker whatIf result
   persistence, commission callbacks, and settlement receipts feeding the native
   options guard/ledger. The current production profile STILL rejects OPT.
4. Deploy the reviewed shared execution aggregation repair through controlled
   original-command recovery; never change production driver JSON manually.
5. Perform the original one-contract MKT entry, exact broker execution/fees/owner
   reconciliation, same-conId reduce-only exit, and prove real PAPER round trip.
6. Only after that perform real driver-only / Execution-only restart acceptance
   with three-line state/identity/send-count invariants; only then enable the
   options trading resident. A shadow observer restart is not this acceptance.

No item in 1/2/3/5/6 is converted to PASS by a unit test or a source-only factory.

## Build and local use

Build through the normal top-level CMake project with the existing pinned
IBAPI_ROOT and IBAPI_DECIMAL_LIBRARY. Targets: hepta_options_observer,
hepta_zero_dte_tests, hepta_zero_dte_execution_fault_tests,
hepta_ib_recent_execution_aggregation_tests. Install the optional OptionsShadow
component to a separate immutable prefix; do not replace the current trading
release or existing systemd units for a shadow rollout.

Examples (read-only):

```sh
hepta-options-observer --account DU_EXPLICIT_PAPER_ACCOUNT \
  --capture /absolute/private/capture.h0d1 --duration-sec 60 --client-id 8662
hepta-options-observer --replay /absolute/private/capture.h0d1
```

Official primary references: IB TWS API option-chain, option-computation,
executions/commissions and what-if documentation; Cboe XSP contract specifications.
The runtime uses the locally pinned 10.30.01 C++ API; API and product documentation
must be rechecked at any SDK or contract-policy upgrade.

## Five-stage acceptance ledger (2026-10-06 JST)

The deployed trading base for this candidate is d384e98. The remote default
branch is older than this local deployed-base lineage; do not replace the current
FX/BTC runtime with default-branch binaries to install the observer.

| Stage | Current evidence | Acceptance state |
|---|---|---|
| 1: live data and permission | Actual Oct 5 New York session: four qualified candidates, 240 observations and exact 240-record replay; zero real-time-ready records; IB 354/10090 plus delayed-data notices | BLOCKED: data unavailable; trading permission remains UNVERIFIED |
| 2: native options support | Native contract/capture/risk/economic primitives, proposal encoding, coordinator faults and boundary regressions | PARTIAL: privileged registry, permission/what-if receipts, callback/fee/settlement integration remain unfinished; production still rejects OPT |
| 3: read-only recording | Bounded native capture and deterministic replay; independently installable no-order shadow worker | OBSERVATION MECHANISM VERIFIED; no valid market-data strategy sample or profitability proof |
| 4: one-contract MKT PAPER round trip | None; zero option order sends | NOT ATTEMPTED; must follow stages 1/2 and independent options-only budgets |
| 5: trading restarts and persistence | Synthetic coordinator crash/replay tests only | NOT ATTEMPTED on the production options trading lane |

The added boundary suite first reproduced nine failed checks, then passed after
repair. The rebuilt full CTest suite passed 54/54, including the runner test
which contains eighteen Python boundary cases. These are code checks, not broker
trading permission, real options executions or production restart acceptance.
Canonical identity and decision JSON are independent of the process locale.

## Read-only resident deployment

Only the OptionsShadow CMake install component belongs in
`~/.local/share/heptatrader/options-shadow/releases/<source-sha>/`.
Its separate `current` link must never replace `~/.local/share/heptatrader/current`.
The unit `heptatrader-options-shadow.service` requires
`~/.config/heptatrader/options-shadow.env` containing only the explicitly verified
`HEPTA_OPTIONS_PAPER_ACCOUNT`. It runs 300-second native capture windows with a
30-second interval and recreates contract discovery each time. Failures retry
without trading. The state directory is `~/.local/state/heptatrader/options-shadow`.
Existing evidence is never pruned automatically. The runner stops before starting
another capture when its dedicated state would exceed 2 GiB (including a 32 MiB
attempt reserve), or filesystem free space would fall below a 4 GiB reserve.
`--max-state-mib` and `--min-free-mib` explicitly configure these storage limits.
Every runner/observer output file is capped at 16 MiB. Storage exhaustion produces
STORAGE_BUDGET_BLOCKED with trading disabled, not a replay or readiness success.
The service also bounds each invocation to 480 seconds; previous capture evidence
remains available for deliberate archival or cleanup.

This worker is not `heptatrader-options-driver.service`. Starting or restarting
it does not pass stage 5. It has no placeOrder, cancelOrder, exerciseOptions,
reqGlobalCancel, or Execution mutation capability. Existing FX/BTC drivers and
shared Execution must remain in place; original pending BTC order 159 is not
cleared, replaced or reinterpreted by this deployment.

A profitable-looking PAPER result would still be pipeline/simulator evidence,
not proof of live strategy returns. Strategy qualification needs time-ordered,
out-of-sample observations, explicit all-in costs and conservative execution
assumptions, with failed/missing-data decisions retained rather than excluded.
No positive strategy result has been established by this delivery.
