# Modular C++ research SDK

Status: EXPERIMENTAL
Applies to: source-built research, offline developer SDK and forward-only client integration
Implementation: `research`
Tests: `tests/research/market_data_tests.cpp`, `tests/research/analytics_tests.cpp`, `tests/research/replay_tests.cpp`, `tests/research/cli_behavior.py`

## Ownership and execution boundary

`hepta::research` is a new, explicit API, not a binary- or source-compatible
replacement for HeptaDLL. Data computation, offline accounting, matching and
strategy callbacks have separate CMake targets. None may assert authoritative
quotes, positions, fills, risk approval or broker success. The forward-only
`NativeStrategyClient` links the existing NativeToolClient, not Execution core
or a vendor SDK. Its header and source have the more specific `agent-entry`
module ownership; the rest of this research tree is `LOCAL_ONLY`. The privileged
production binaries do not link research code.

The existing Python SHADOW pipeline and its evidence/receipt contracts remain
unchanged. This C++ SDK does not replace those contracts or qualify SHADOW output
for automatic execution. Current simulator, IB PAPER, XT, CTP and LIVE capability
states are unchanged.

## Data and calendar contracts

`Tick` carries UTC microseconds, a positive stream sequence, a finite positive
last price and nonnegative **incremental** volume. Cumulative feed volume must
pass through `CumulativeVolumeDecoder`: a new day's first observation establishes
a zero-volume baseline by default. Counting that initial counter is an explicit
complete-day-start-capture option. Within-day decreases and sequence conflicts
are errors, not guessed reconnects. A new stream epoch requires a new decoder.

Sessions are explicit sorted, non-overlapping, half-open UTC intervals with a
Gregorian `YYYYMMDD` trading-day label. Multiple night/day sessions can share a
label. No hardcoded exchange holiday, daylight-saving, fee or trading-hours
schedule is presented as current. Missing session coverage fails closed.

`BarBuilder` anchors positive-duration bars to session opens and clips their end
to session closes. Duration zero produces trading-day bars across supplied
sessions. No synthetic empty bars are inserted. A watermark is a promise that
older ticks cannot arrive; it can close a complete bar and prevents older input.
Duplicates are idempotent; conflicting/reversed sequences, overflow and malformed
input are rejected without advancing normal state. Objects are thread-affine:
callers must serialize access, rather than rely on legacy `volatile` flags.

`BarSeries` keeps a bounded chronological window of complete bars. Range queries
have inclusive endpoints; highest/lowest ties select the latest bar by default.
The existing high/low overloads remain supported; typed overloads accept
`BarPrice::Open`, `High`, `Low` or `Close`. `AtLatest(0)` is the last retained bar.
Replacement requires an exact existing interval. Merging preserves OHLC, volume
and observation count and rejects overlap or mixed instruments/trading days.

`LatestHigher` and `LatestLower` search from the supplied end toward its begin,
using strict comparisons. They return `found=false` when absent, not an unsigned
minus-one index. The index is meaningful only when `found` is true. All indices
are relative to the current retained window; do not cache them across mutations.
`RetainedBarsInLatestTradingDay` counts only the resident tail sharing the latest
trading-day label, not a full-day count certified after retention or erasure.

### Confirmed extrema and signal availability

`ConfirmedPeaks(begin, observedEnd, radius, field)` and `ConfirmedTroughs` only
inspect the inclusive supplied range. A candidate needs `radius` complete bars
on **each** side within it and must be strictly above/below all neighbours.
Plateaus do not qualify. Radius zero returns every visible bar; a radius too
large for the range returns an empty vector without overflowing index arithmetic.
Results are in chronological order. Missing/invalid ranges, fields or non-finite
thresholds fail explicitly.

Each result separates the pivot's `beginUs` from `confirmedAtUs`, the end of its
last required right-hand neighbour. A strategy must not act on the pivot before
that confirmation time, and the caller must set `observedEnd` to the last actually
observed complete bar. Passing the end of a future offline dataset is not causal
merely because a confirmation timestamp is returned. Retention and interval
replacement change the visible dataset; this API is not a bitemporal record of
when corrections became known. Adjacent bars mean adjacent retained observations,
not a guarantee of continuous wall-clock/session coverage.

### Portable CSV

Tick, session and completed-bar parsing bound rows and line lengths, use the
classic numeric locale, and reject non-finite values, extra fields and overflow.
`TickCsvReader` consumes the header once and exposes one validated row per
`Next(Tick&)`. It retains no tick history, requires no seekable input, and defaults
to the existing 1,000,000-row work quota with a 4,096-byte line bound. A caller may
supply another positive row quota explicitly. The borrowed stream must outlive
the non-copyable, thread-affine cursor. `RowsRead()` counts successful rows only.
Clean EOF returns false repeatedly without changing the output tick. Parse,
quota and I/O failures leave output/count unchanged and permanently fail that
cursor; clearing or seeking the underlying stream cannot resume it after an error.
`ReadTicksCsv` remains the eager vector API and delegates to this same decoder.

The replay CLI now processes rows incrementally, retaining only its existing
bounded bar/strategy/order/fill state rather than the entire tick input. It still
requires at least one valid tick and retains the same row quota and matching
semantics. A late input error returns a nonzero exit without an EOF success
summary: consumers must discard partial stdout from any failed invocation.
Streaming does not make an incomplete run successful or authenticate feed data.

`ReadBarsCsv`/`WriteBarsCsv` use this exact single-instrument schema:

```text
instrument,trading_day,begin_us,end_us,open,high,low,close,volume,tick_count,complete
```

Times are UTC microseconds; days are Gregorian labels, not inferred calendars.
OHLC consistency, interval ordering/non-overlap, positive tick counts and
nonnegative volume are checked. The completed-bar interface requires `complete=1`;
`0` is rejected rather than upgraded to a finished bar. Mixed instruments and
reversed trading-day labels fail. Empty datasets have a header and zero rows.
LF, CRLF and a final row without newline are accepted. Output validates all input
bars before writing and uses `max_digits10` precision; stream failures are errors.
Neither a successfully parsed row nor its completion flag authenticates market
data provenance. These formats do not decode legacy ABI-dependent binary dumps;
historical input must be deliberately converted and its provenance retained.

## Research accounting and matching

`ResearchLedger` is a single-instrument, multiplier-aware futures-style P&L
ledger. It handles partial closes, reversals, fees and exact fill-ID deduplication.
It has no broker reconciliation, account permission, margin model, tax model or
production journal. Its mark-to-market output is **research state only**.

`EvaluateEquity` removes explicitly declared end-of-period external cash flows
before computing returns. Sampling frequency and the annual risk-free rate are
caller inputs; timestamps order the equally weighted sampling observations and
do not infer a business calendar. Zero-denominator ratios are undefined metrics,
not NaN/Inf or a fabricated zero. Inputs with nonpositive pre-flow capital fail.

`ReplayMatcher` implements a documented offline last-trade/liquidity assumption,
not exchange queue reconstruction. Limit orders are eligible only on a later
timestamp than their submission. All orders share each tick's finite volume in
submission order. DAY expiry, IOC partial cancellation and FOK all-or-cancel are
explicit. No signal may consume its own timestamp's tick. There is no gateway,
venue credential, production order API or exchange connection in this target.

The replay example uses synthetic data, fixed example capital/multiplier/fees,
and a moving-average direction callback. It marks the final open position rather
than inventing a last-tick liquidation. It is not a profitability demonstration
or behavioral-equivalence claim for every historical Pegasus strategy.

## Strategy-to-tool integration

`BarStrategy::OnCompletedBar` emits `Forecast`, not an authoritative order or
portfolio position. `MovingAverageForecast` is a small executable consumer of
this contract, not a claim that all historical strategies have been migrated.

`PreparedOrder` is immutable and supports the current LMT/DAY wire profile.
It rejects ancillary contract fields that HTT1 cannot serialize, including
option expiry/strike and multiplier, rather than silently discarding identity.
It does not infer open/close, close-today/close-yesterday, FAK or FOK production
semantics. The offline matcher's IOC/FOK support does not expand production TIF.

The caller first invokes `NativeStrategyClient::Preview`. Submission must use
the **Execution-issued command ID and permit from the matching successful
preview**, with the exact proposal and expiry. The caller must retain that
binding before submission and reuse it after an uncertain/lost response.
This SDK never allocates a mutation ID, retries automatically, rotates an owner
or fabricates a permit. Preview/query call IDs are ordinary client read-call IDs.

A `true` return means a response was transported, not that a mutation succeeded.
Inspect the result envelope: rejection and uncertainty remain unchanged. A failed
transport clears stale output and returns its diagnostic. Session tokens and
schema discovery remain owned by NativeToolClient and the existing Gateway.

### Guarded cancellation and flattening

`PreparedCancellation(serverOrderId)` selects only a nonnegative order ID returned
by the canonical service. `NativeStrategyClient::Cancel` forwards it to
`trade.cancel_order`; it has no cancel-any flag, account/owner override or local
order map. The service verifies ownership, recovery state and cancel eligibility.
An offline replay order ID is not a production order ID and must not be converted
or guessed. Cancel has no preview tool: the caller retains one canonical cancel
command ID with the service order ID before sending and reuses both after an
uncertain response. `Status` queries that cancel command, not its order number.

`PreparedFlatten(instrument)` selects only a server-bound instrument.
`PreviewFlatten` calls `risk.preview_flatten`; `Flatten` calls
`trade.flatten_position` with the matching Execution-issued command ID and opaque
preview permit. Quantity, side, price, local cost basis and research position
snapshots cannot be provided through this proposal. The service alone derives
and revalidates the position/snapshot plan. Missing or rejected previews do not
authorize a fallback place order, locally synthesized opposite-side order or
reuse of a permit for another instrument.

Both are immutable request builders and ordinary clients of existing tools,
not new tools or new Execution entry points. Capability discovery, session
revocation, owner checks and all recovery/terminal gates still apply to exits.
A composition without the optional flatten tools remains unsupported; the SDK
does not install a handler. No method automatically retries a mutation, turns an
uncertain outcome into success or clears a service fence. As for placement, false
transport or validation results clear prior output and retain a diagnostic.

The existing native tests cover byte-identical retry requests, zero/positive/maximum
order-ID wire round trips, proposal immutability, absent client position fields,
malformed inputs and stale-success clearing. The real local Gateway fixture
extends its negative-only authority to cancellation/flattening and checks one
forwarded call, missing capabilities and revoked sessions. Its denied preview
and uncertain mutation callbacks have no venue and issue no risk-approval permit;
these assertions are not production risk, durable recovery or broker acceptance.
No offline SDK export, production target, vendor SDK or runtime capability state
is changed by these source-built client APIs.

## Build and verification

From the repository root, `scripts/dev_core.sh` also builds the research test
aggregate and executes its `core`-labelled tests. The root build includes the
native client and real local-socket Gateway fixture tests. Pure research can be
built separately without any vendor SDK:

```sh
cmake -S research -B build/research -DCMAKE_BUILD_TYPE=Release
cmake --build build/research --parallel 2
ctest --test-dir build/research --output-on-failure
build/research/hepta-research-replay research/examples/ticks.csv research/examples/sessions.csv TEST.FUT 10 1 2 1
```

The tests use runtime assertions active in Release, independent numeric oracles,
causality/volume conservation, conflict/error cases, deterministic CLI output,
wire round trips and a real NativeToolClient/UnixToolServer boundary. Market-data
coverage includes independent field/query/extrema oracles over 24 synthetic
17-bar sequences and every visible subrange; queries on a full dataset must agree
with a separately constructed observed prefix. CSV coverage includes numeric
round trips, extreme finite prices, integer overflow, partial bars, malformed
fields, locale changes, ordering conflicts and input/output stream failure.

The Gateway fixture's execution authority deliberately returns uncertainty and
has no venue; it checks forwarding, lack of automatic retry and session revocation.
It is not broker, deployed-process isolation or durable-recovery qualification.
Existing canonical recovery, installed-process and broker-qualification suites
remain required. Source-level evidence must not be relabelled LIVE readiness.

## Offline SDK installation

The standalone build installs a separate `ResearchSDK` component with the four
portable libraries, four public headers, replay CLI, synthetic examples and a
relocatable CMake package. See [package usage](../../research/PACKAGE.md).
`HeptaResearch::Data`, `::Analytics`, `::Replay` and `::Strategy` are exported;
NativeStrategyClient, the Gateway, Execution, vendor libraries and credentials
are not. Unknown required components fail rather than resolve to placeholders.
The repository VERSION supplies the label; exact-source/compiler metadata and
explicit dirty/unavailable state accompany it. A matching numeric version is
not an ABI or source-equivalence guarantee.

The sixth standalone CTest entry installs to a temporary prefix, moves it,
builds/runs an external consumer with CXX_STANDARD explicitly set to 11, checks
transitive links and the additional query/CSV symbols, executes the installed
replay CLI and rejects unsupported native components/wrong versions. It inherits
sanitizer flags. Successful GCC CI builds may publish the staged SDK and checksum
as a developer artifact; queued CI does not certify that artifact. The root
production install/package manifest and native Gateway test boundary are unchanged.
See the [integration record](../technical/heptadll-integration.md) for source
provenance, retained assets and the remaining migration boundary.

## Separate installable strategy client

The offline four-library package remains unchanged. The root build now exports
the existing forward-only adapter through a separate `StrategyClientSDK`
component; see [client package](../../research/CLIENT_PACKAGE.md). It is excluded
from default production installation. Its headers carry only transport values
and the existing client APIs, not host/session or execution-authority classes.
The [Agent entry module](agent-entry.md) owns that consumer and its tests.
