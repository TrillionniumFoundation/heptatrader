# AuthoritativeSnapshot V2

Status: current core/target contract
Applies to: Execution state authority, risk, intent, portfolio and Global Decision
Verification: schema, generation, watermark, freshness, concurrent-update and replay tests
Authority: authoritative decision-state contract

Snapshot 只能由 Execution-owned state authority 组装。Gateway、Agent、策略和 allocator 可以请求或引用，不能供应、覆盖或扩展权威字段。

## Envelope

包含 execution epoch、fencing generation、state generation、collection/event watermark、collection window、captured/fresh-until time、account/execution domain、component digests 和 aggregate payload digest。

## Payload

至少包含 normalized quote/liquidity、account/cash/PnL/margin、positions、active/recent orders、risk limit/usage、venue/connection/reconcile/kill state。内部组件是 typed values；JSON 仅是验证后的边界序列化。

## Atomicity

fill、cancel、correction、reconnect 或 restart 在 capture 期间发生时，要么完整进入同一 generation，要么 capture 失败。调用者不得混合多个 snapshot。跨 shard 的 Global Decision 使用明确 SnapshotVector，并验证每个 component 的 temporal compatibility。

## Admission

quote positive/ordered/instrument-bound/fresh；required account/order/position/risk complete；时间和 watermark 单调；size bounded；digest match。任一失败返回 `DECISION_SNAPSHOT_*` 且 `authoritative` 不得为 true。

机器 schema 为 `schemas/authoritative-snapshot-v2.json`。

## Simulator-only spot-FX accounting projection

`SimulatorFxAccounting` in `HeptaTrade/portfolio/simulator_fx_accounting.*`
provides a supporting **derived value**, not an AuthoritativeSnapshot issuer.
The native helper identifier is `hepta.simulator-fx-accounting.v1`; Portfolio
Compiler module 1.1.0 adds it to `hepta_portfolio_core`. Its two pure operations
are Replay and Reconcile. Neither modifies the existing PortfolioCompiler,
Execution state, risk permissions, order lifecycle or any durable journal.
The snapshot wire schema remains V2; this helper has no public wire serializer.

### Exact supported model and ownership

This is one fully funded EUR/USD simulator cash book. Buying EUR increases EUR
cash and decreases USD cash; selling EUR does the reverse. The EUR cash balance
is this model's base-currency inventory, not a second asset balance to add again
to total equity. No conversion or sum of EUR and USD balances is used as a
portfolio valuation. Net quote trade flow is **not** realized profit and loss.

The supervisor independently selects the opening balances, instrument revision,
ordered events and reporting cut. A syntactically valid ID or SHA-256 digest does
not establish their issuer, completeness, latestness, settlement or entitlement.
Actual Broker input normalization and authoritative-state integration are not
present. In particular, this helper must not be used to manufacture an
`AuthoritativePortfolioInput.complete=true` or an Execution snapshot capability.

The instrument policy accepts exactly venue `simulator`, instrument `EUR.USD`,
base `EUR` and quote `USD`. Revision and book/event/execution IDs are 1..128 bytes
from ASCII letters, digits, dot, underscore, dash and colon. All monetary/quantity
raw values use scale 1,000,000 and the maximum magnitude 9,000,000,000,000,000.
Instrument rules are simulator configuration, not copied IB minimums or a claim
about a currently qualified broker contract.

| Input | Native fields and admission |
|---|---|
| SimulatorFxInstrument | The exact four scope strings, revision, positive whole-EUR quantityStepRaw, positive priceTickRaw, aligned minimum/maximumQuantityRaw, nonzero effectiveFromMs and strictly later effectiveUntilMs. All raw limits fit the numeric maximum. |
| SimulatorFxOpening | bookId, matching instrumentRevision, nonnegative bounded baseBalanceRaw and quoteBalanceRaw, and asOfMs within the half-open instrument interval. Opening records do not constitute a durable checkpoint or sequence authority. |
| SimulatorFxEvent | eventId, executionId, matching book/instrument/revision, nonzero canonical sequence, economic eventTimeMs and recordedAtMs. Economic time is at least the opening time; recording is not before economic time and is not after the requested reporting cut. |
| Replay arguments | Explicit asOfMs at or after opening time; physical input count at most maximumRecords. That limit is 1..4,096, default 4,096, and counts duplicate inputs too. |
| SimulatorFxObservation | Independently supplied book/revision, exact reporting cut and terminal sequence, both currency balances, total USD commission and fill/commission counts. No fuzzy timestamp or currency tolerance is inferred. |

### Fill and commission events

A Fill has side Buy or Sell, positive quantity within the configured lot/min/max
rules, and positive tick-aligned price. Its commissionRaw must be zero and
commissionCurrency empty. A Commission has side None, zero quantity and price,
a nonnegative bounded commissionRaw and currency exactly USD. It must refer to
an earlier admitted execution and have economic time not before that fill.
Exactly one commission report is admitted per fill; an explicitly zero fee is
valid and necessary when that execution has no fee. Unknown enum values and
nonzero unused fields are rejected, not ignored.

Fill economic time must be in the half-open instrument-effective interval.
A delayed commission for an already admitted fill may arrive after that interval
ends. Unique events have contiguous journal sequence starting at one and
nondecreasing recordedAtMs. Economic times need not be monotonic across different
executions; recorded order, not an unstable timestamp sort, determines replay.

A previously seen event ID is accepted only when **every** field, including
sequence and both times, is identical to its original record. That replay adds
only to the duplicate diagnostic count; it does not advance the reporting cut,
post money again or alter the digest. It may repeat an old record after newer
ones. A changed reuse is a conflict. A different event ID for an already posted
execution is also a conflict, even if its quantity matches; upstream notification
normalization must deliberately select the canonical record rather than silently
overwrite it. Fee corrections, rebates and trade busts are not implicit updates.

### Exact cash equations and numeric failure

For quantity raw Q, price raw P and scale S, admitted quantities have Q divisible
by S, so quote notional raw N is exactly `(Q / S) * P`. The implementation checks
`Q / S <= maximumRaw / P` before multiplication. There is no floating-point
conversion, truncation of fractional cash, price rounding or exchange-rate lookup.
Quantity increments below one whole EUR and quote amounts outside the raw range
are rejected. A future fractional-base profile needs its own explicit rounding
and residual contract; it is not silently enabled by decreasing a step field.

```text
Buy:  EUR balance += Q; USD balance -= N
Sell: EUR balance -= Q; USD balance += N
Fee:  USD balance -= commissionRaw

EUR balance = opening EUR + netBaseTradeRaw
USD balance = opening USD + netQuoteTradeRaw - commissionsRaw
```

Both currency balances must remain nonnegative after each unique posting. Every
balance, signed cumulative trade flow and total commission stays within the raw
range; out-of-range intermediate totals fail rather than wrap or cancel later.
This is a deliberately unlevered model, not a margin engine. Incomplete fee
reports leave Replay accepted with `feesComplete=false`; known balances then
exclude unknown future fees and are not independently certified spendable cash.
Reconcile refuses such a projection even when the observed partial cash happens
to match. The helper does not estimate a missing fee as zero.

Worked vector, in human units: start with EUR 0 / USD 1,000; buy EUR 100 at 1.10;
post USD 1 commission; sell EUR 40 at 1.20; post USD 0.50 commission. The final
balances are EUR 60 / USD 936.50, net EUR trade flow +60, net USD trade flow -62,
and total commission USD 1.50. Remaining EUR inventory is not implicitly valued.

### Atomic replay, reconciliation and digest identity

Replay preflights bounded current inputs before allocating normalization/maps or
hash contexts. It uses local event/execution indices and local totals, never
mutates input records and never publishes a valid prefix. A malformed suffix,
sequence/conflict/funding failure, crypto failure or thrown allocation cannot
produce an accepted partial projection. Typed rejections have an empty digest,
empty identity and zero balances/counts. failedIndex identifies a failing physical
record when applicable; configuration/digest errors use size_t(-1). Allocation
exceptions propagate without changing the caller's input; no allocating error
object is promised under sustained memory exhaustion.

On success, SimulatorFxProjection carries book/revision, both balances, signed
trade flows, total fees, last unique sequence and recording time, requested cut,
fill/commission/duplicate counts, fee completeness and digest. Result construction
finishes before accepted becomes true; the return move is compile-time no-throw.
There is no persistent state or shared mutable map, and separate calls may run
concurrently. Callers must not mutate referenced inputs concurrently.

Reconcile **recomputes Replay from the original inputs**, rather than trusting a
caller-edited successful projection. It first requires fee completeness, then
compares the exact book/revision/time/sequence cut, then both balances and the
fee/fill counters. One raw-unit mismatch fails. Only a match returns matched=true
and the projection digest. This is structural/numeric agreement with supplied
observations, not a signed reconciliation receipt or proof that a broker agreed.

The SHA-256 transcript uses eight-byte unsigned big-endian integers and
length-prefixed ASCII strings; negative trade totals use uint64 two's-complement
encoding. Its order is: helper Version; venue/instrument/base/quote/revision;
quantity step, price tick, min/max quantity, both effective times; opening
book/revision/base/quote/time; requested reporting cut. Each first-seen record
then has tag 1, five identity strings, sequence/economic/recorded time,
kind/side/quantity/price/commission and commission currency. Final tag 2 covers
base/quote balances, signed base/quote trade flows, total commissions, terminal
sequence/time, fill/commission counts and fee-complete flag. Exact duplicate
transport records are excluded; maximumRecords and the duplicate diagnostic are
not economic identity. All OpenSSL initialization/update/final results are checked.
The digest binds deterministic content but does not authenticate provenance.

### Resource, rollout and compatibility limits

All 4,096 physical records and bounded IDs are checked before body-sized copies;
subsequent ordered maps and incremental hashing have bounded per-call work and
retention. These are input/work bounds, not a hard wall deadline, total-process
RSS guarantee or admission budget across callers. No disk, network, clock lookup,
Broker call or financial mutation occurs. Replay does not infer a live feed is
complete simply because it is contiguous within this supplied subset.

No durable financial ledger, funding/deposit/withdrawal event, trade correction,
bust/reversal, settlement calendar, pending-versus-settled cash, lending, interest,
margin, foreign-currency fee, tax-lot cost basis or realized/unrealized PnL engine
is implemented. The profile does not extend CTP/XT/LIVE support or change IB PAPER
qualification. Any move to those semantics requires distinct reviewed contracts,
actual adapters and execution evidence rather than changing a status string.

Default tick/quantity choices are simulator fixtures. Broker fee reporting is a
separate input concern: the official API defines execution-linked commission and
fee amounts with their own currency; it must not be assumed to always be the pair's
quote currency. Reference definition, not a qualification receipt:
`https://www.interactivebrokers.com/docs/tws-api/ref/commission-and-fees-report`.

### Requirement-to-assertion evidence

`tests/simulator_fx_accounting_tests.cpp` and the two tests in
`tests/python/test_simulator_fx_accounting.py` exercise the real helper and OpenSSL.

| Invariant | Direct native regression |
|---|---|
| Both cash conservation equations and known worked vector | TestCashConservationAndGoldenReplay |
| Exact replay idempotence and changed-ID/economic conflicts | TestDuplicateRecordsNeverDoublePost |
| Contiguous sequence, recording order and execution/fee linkage | TestSequenceCaptureAndFeeAssociation |
| Missing fees never reconcile; one-unit or wrong-cut mismatch | TestMissingFeeAndReconciliationCutFailClosed |
| No lending, hidden rounding or foreign-currency fee assumption | TestNoMarginRoundingOrCrossCurrencyAssumptions |
| Scope, lot/tick and half-open metadata intervals | TestMetadataAndEffectiveTimeBounds |
| Large products and balance boundaries checked before overflow | TestIntegerRangeBeforeMultiplication |
| Exact capacity, bounded IDs and allocation-free preflight rejection | TestPreflightCapacityAndNoBodySizedRejectionCopies |
| Policy/opening/cut/record digest binding and crypto errors | TestDigestBindsPolicyOpeningCutAndEventSemantics |
| Failed allocations preserve input; independent parallel calls agree | TestAllocationFailuresAndThreadIndependentReplay |

The Python oracle uses arbitrary-precision integer multiplication/divmod and
independently encodes the canonical transcript. Its seeded valid/invalid streams
compare acceptance, both balances, trade flows, fees, all counts, reporting cut
and digest against a test-only C++ batch adapter. The fixed worked-vector digest
is separately encoded in Python, not obtained by calling production C++. This is
finite behavioral evidence, not a proof of all market/accounting semantics or an
independent financial, security or deployment approval.
