# Modular C++ research SDK

Status: EXPERIMENTAL
Applies to: source-built research, offline developer SDK and forward-only client integration
Implementation: `research`
Tests: `tests/research/market_data_tests.cpp`, `tests/research/analytics_tests.cpp`, `tests/research/replay_tests.cpp`, `tests/research/cli_behavior.py`, `tests/research/sdk_package_behavior.py`

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
Replacement requires an exact existing interval. Merging preserves OHLC, volume
and observation count and rejects overlap or mixed instruments/trading days.

CSV parsing bounds rows and line lengths, uses the classic numeric locale and
rejects non-finite values, extra fields and overflow. It is not a decoder for
legacy ABI-dependent binary dumps. Historical input must be explicitly converted
and its provenance retained before use.

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
wire round trips and a real NativeToolClient/UnixToolServer boundary. The Gateway
fixture's execution authority deliberately returns uncertainty and has no venue;
it checks forwarding, lack of automatic retry and session revocation. It is not
broker, deployed-process isolation or durable-recovery qualification. Existing
canonical recovery, installed-process and broker-qualification suites remain
required. Source-level compile/test evidence must not be relabelled LIVE readiness.

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
builds/runs an external C++11 consumer, checks transitive links, executes the
installed replay CLI and rejects unsupported native components/wrong versions.
It inherits sanitizer flags. Successful GCC CI builds may publish the staged SDK
and checksum as a developer artifact; queued CI does not certify that artifact.
The root production install/package manifest and native Gateway test boundary
are unchanged. See the [integration record](../technical/heptadll-integration.md)
for source provenance, retained assets and the remaining migration boundary.
