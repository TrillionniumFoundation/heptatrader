# Modular C++ research SDK

Status: EXPERIMENTAL
Applies to: source-built research, offline developer SDK and forward-only client integration
Implementation: `research`
Tests: `tests/research/market_data_tests.cpp`, `tests/research/analytics_tests.cpp`, `tests/research/replay_tests.cpp`, `tests/research/cli_behavior.py`, `tests/research/legacy_bundle_behavior.py`, `tests/research/sdk_package_behavior.py`

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

The replay example uses synthetic data and explicit capital/multiplier/fee options
(with unchanged defaults for old invocations),
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

## Explicit offline settlement

`ResearchLedger::Settle(ResearchSettlement)` and `ResearchPortfolio::Settle`
realize variation at a caller-supplied accounting price and rebase the remaining
cost, without a fill, quantity change, fee, deposit or market observation. FIFO
and weighted-average accounting retain their separate cost policies. Settlement
receipts are immutable, bounded and idempotent, with a separate ID namespace and
the same monotonic event clock. The ledger's historical `maxFillIds` argument now
bounds combined fill/settlement receipts; portfolio events share `maxEventIds`.

Input, capacity, clock, arithmetic and destructive-precision failures leave
accounting, event identity and time unchanged. Existing quote value, age and
validity are preserved: settlement cannot revive a missing/stale/invalidated
portfolio mark. Exact retries do not rebase trades that arrived later. See the
[SDK settlement contract](../../research/PACKAGE.md#explicit-offline-variation-settlement).

The existing Analytics test and installed/relocated C++11 consumer exercise the
public methods. Independent fill-cash oracles cover both bases, multiple
multipliers, partial closes and reversals. This adds no translation unit, target,
installed-header path, privileged dependency, production install or venue status.
Exchange-specific clearing calendars, margin and broker reconciliation are not
inferred from this offline operation.

## Separate installable strategy client

The offline four-library package remains unchanged. The root build now exports
the existing forward-only adapter through a separate `StrategyClientSDK`
component; see [client package](../../research/CLIENT_PACKAGE.md). It is excluded
from default production installation. Its headers carry only transport values
and the existing client APIs, not host/session or execution-authority classes.
The [Agent entry module](agent-entry.md) owns that consumer and its tests.


## Typed preview response consumption

The exported client now offers `PreviewAuthorized` for both order and flatten
proposals. It calls the same native preview path and decodes the complete service
response through the existing `TypedToolProtocol` parser. The typed record keeps
the exact command ID, permit, expiry, service epoch and fencing generation;
unknown/duplicate/missing fields, malformed values, mismatched tools and
non-approved statuses yield no authorization. Raw transported errors remain
available for diagnosis. This removes caller JSON scraping, not service-side
validation or final execution checks. No client method mints a permit, refreshes
expiry, retries a mutation, infers a flatten side/quantity or changes HSR1.

See the [typed SDK example](../../research/CLIENT_PACKAGE.md#typed-preview-approval-without-caller-json-scraping).
The existing installed/relocated consumer exercises the exported codec and both
overloads. Existing real Gateway/Execution and process-crash tests now extract
their actual preview values through that path while retaining their original
journal and permission assertions. New codec fixtures are explicitly synthetic;
only the existing service issues usable test permits. No new target, public
header path, privileged dependency or production capability is added.


## Reproducible native-path latency observations

The existing `hepta_research_native_execution_tests` now measures the actual
NativeStrategyClient -> Tool Gateway -> separately exec-launched Execution
Service path. It uses eight independent, freshly initialized synthetic fixtures,
each with one warmup and three measured serial place/cancel cycles. This respects
the unchanged four-cancel/session/minute limit. It is **not** session rotation for
production trading, a sustained-load measurement or a rate-limit bypass.

The single `NATIVE_EXECUTION_LATENCY_JSON` record retains all 24 measured rows
with fixture/cycle identities and integer `steady_clock` nanoseconds:

| Field | Measured interval |
|---|---|
| `preview_ns` | Ordinary typed preview call, including transport and decoding |
| `place_persist_ns` | Immutable request persistence, including required syncs |
| `submit_stored_ns` | Stored-request read/validation through accepted response |
| `place_pipeline_ns` | Preview start through submit response; includes intervening caller checks |
| `status_ns` | Same-command status request/response |
| `cancel_persist_ns` | Immutable cancellation persistence |
| `cancel_stored_ns` | Stored cancellation through accepted response |
| `cancel_terminal_observed_ns` | Cancellation start through durable terminal observation; **includes fixture IPC and 2ms polling** |
| `duplicate_ns` | Exact-ID resubmission after cancellation; must remain a duplicate |

No raw sample is emitted until all fixtures have shut down normally and their
journals and Gateway audit chains have been verified. The test requires exactly
32 placement and 32 cancellation send attempts including warmups, one send per
command, no resurrected orders, and final zero position/active orders. Existing
SIGKILL, lost-reply, revocation, forged-permit and installed-client checks remain.
There is no new CMake target, installed API, broker connection or authority path.

`tests/research/native_latency_report.py` requires matching successful CTest
JUnit and full log records, rejects missing/duplicate/malformed samples and
inconsistent counters, and computes integer nearest-rank p50/p95/p99. It retains
raw observations, input hashes, clean local Git commit/tree identity, compiler,
build flags and host context. Its behavioral self-tests run in the existing
modular-integration CI; the resulting `native-latency.json` joins the existing
`research-canonical-<head>` artifact. Missing evidence fails report generation;
there is no invented duration or relaxed timing threshold.

These are small-sample observations across independent fixtures. p99 is the
largest of just 24 observed values, not a confident tail estimate. Startup and
warmup are excluded; no CPU pinning or resource exclusivity is asserted. The
Gateway uses threads in the client process, the Execution service is a separate
process, and all use the same test UID. This does **not** qualify different-UID
host isolation, LIVE/CTP, broker latency, exchange callbacks, HFT performance or an
SLO. Build instrumentation and host contention can materially change results.

For targeted reproduction, the same executable accepts `--latency-only`; a raw
standalone run is diagnostic and cannot replace the complete CTest/JUnit input
required by the report exporter. Local reconstructed Git commits must not be
relabelled as remote source identities. The original source and external-consumer
retirement boundaries in the [integration record](../technical/heptadll-integration.md)
remain unchanged.

## Legacy file bundle and EOF continuation

The existing Data SDK now receives the selected #106 BIN/XML profiles through
an import-only adapter; it does not acquire a parallel Python bar/ledger/client.
The existing BarBuilder adds explicit incomplete-tail EOF finalization from
#108, and the existing native replay accepts explicit capital, multiplier and
per-unit fees and rejects buffered output failure. Source and installed/relocated
tests exercise the same implementations. See [contract and remaining differences](../../research/LEGACY-BUNDLE.md).
These capabilities do not certify full historical parity, external consumer
retirement, remote exact-head CI, main merge or old-repository archival.

## Distinct offline model continuation

`research/MODELS.md` defines the new explicit order-flow and observed next-open
contracts, bounded grid slippage, and signed-price accounting opt-in. They are
implemented in the existing Replay/Analytics translation units, using one native
ledger/portfolio. The original positive Data codecs, NativeStrategyClient and
production installation remain unchanged. `tests/research/replay_model_cases.h`
is compiled by the existing replay test and by the installed/relocated external
SDK consumer; it adds no compiled target or runtime registration. Source-level
model ports are not certification of the alternative Python CLI, wide Decimal
domain, old client-record formats or unknown legacy consumers.
