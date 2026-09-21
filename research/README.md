# Hepta research modules

Explicit C++11 data, analytics, offline replay and completed-bar strategy APIs,
plus a separately linked adapter to the existing NativeToolClient. These are
experimental developer components, not a restored HeptaDLL runtime or a
source/ABI-compatible replacement. The offline libraries also have a standalone
installable SDK; the native integration remains a separate root-build boundary.

From the repository root:

```sh
cmake -S research -B build/research -DCMAKE_BUILD_TYPE=Release
cmake --build build/research --parallel 2
ctest --test-dir build/research --output-on-failure
build/research/hepta-research-replay research/examples/ticks.csv research/examples/sessions.csv TEST.FUT 10 1 2 1
cmake --install build/research --prefix "$PWD/stage/research-sdk" --component ResearchSDK
```

The CSV files are synthetic. The example has no gateway address, credentials or
broker linkage and is not a profitability claim. The root build adds the native
client and real local Gateway fixture tests; standalone research does not.
Standalone CTest additionally installs, relocates and consumes the real SDK from
an external C++11 project. Root production packaging is unchanged.

See [SDK installation and consumption](PACKAGE.md),
[module contracts](../docs/modules/research-sdk.md) and the
[integration/provenance record](../docs/technical/heptadll-integration.md).

## Strategy observation time

The existing `BarStrategy` now has a nonvirtual `ObserveCompletedBar(bar,
observedAtUs, forecast)` entry. Use it when a closed bar is delivered later than
its period boundary, including historical replay gaps and delayed data feeds.
The observation must be in the bar's UTC-microsecond domain and at or after
`bar.endUs`. Invalid or incomplete input is rejected before the existing
`OnCompletedBar` callback is invoked. On emission, the callback must return the
same instrument and direction -1, 0 or +1; the published timestamp is the supplied
observation time. Suppression or failure leaves the caller's output unchanged.

```cpp
hepta::research::MovingAverageForecast calculation(5, 20);
hepta::research::BarStrategy& strategy = calculation;
hepta::research::Forecast forecast;
// completed is a validated closed bar; deliveryUtcUs is actual availability.
if (strategy.ObserveCompletedBar(completed, deliveryUtcUs, forecast)) {
    // Consume the forecast as research input, NOT an authorized broker order.
}
```

The original calculation callback, object layout and virtual table are retained.
Its bar-end output alone does not prove actual availability. The caller still
owns delivery sequencing: this stateless wrapper does not infer a wall clock,
reorder data, undo arbitrary callback state, or provide durable strategy replay.
A callback that throws or emits invalid output must be handled by its owner;
only forecast publication, not arbitrary user code, has an all-or-nothing bound.

The existing offline CLI calls the observation entry after `ReplayMatcher`
accepts each tick. It submits at that observation, and the unchanged matcher
rejects clock reversal and any same-timestamp fill. No new runtime, live state,
permit, client transport, order path or broker capability is introduced. The
existing replay test compares 30,720 delivered observations against independent
integer-sum forecasts and tests delayed/no-signal/invalid callbacks. The existing
installed/relocated C++11 consumer also links and executes this public method.
These are synthetic research tests, not a broker or latency qualification.

## Incremental completed-bar ingestion

`BarCsvReader` extends the existing Data SDK's completed-bar CSV boundary, not
its schema or its execution permissions. Like `TickCsvReader`, it borrows a
non-seekable stream and reads one bounded row per `Next` call. It keeps one
private previous bar for instrument, trading-day and non-overlap validation;
input length does not become retained history. The existing `ReadBarsCsv`
helper uses this same decoder and deliberately still returns an eager vector.

```cpp
hepta::research::BarCsvReader input(completedBarStream, rowLimit);
hepta::research::MovingAverageForecast calculation(5, 20);
hepta::research::Bar completed;
while (input.Next(completed)) {
    // Supply actual delivery time in UTC microseconds. CSV period boundaries
    // alone are NOT proof of when a historical value became available.
    const std::int64_t deliveryUtcUs = nextRecordedAvailability(completed);
    hepta::research::Forecast forecast;
    if (calculation.ObserveCompletedBar(completed, deliveryUtcUs, forecast)) {
        consumeResearchForecast(forecast);
    }
}
```

The stream, row limit, availability provider and forecast consumer in this
example belong to the application. No timestamp, strategy state, approved
order, broker fill or live quote is fabricated by the reader. It is not a
historical binary-cache decoder or source/ABI emulation of the retired library.

Construction consumes only the header. A successful `Next` publishes one
validated complete candle and increments `RowsRead`. Clean EOF returns false
repeatedly without replacing output. Invalid, overlapping, reversed, partial,
oversized or over-quota rows and I/O errors leave output/count unchanged and
permanently fail that cursor; clearing the stream cannot skip the bad record.
Returned values are independent: changing one cannot relax subsequent checks.
The stream must outlive its noncopyable cursor; access must be serialized.
Consumers must not treat an emitted prefix as a complete run after a later error.

The existing data test exercises 50,000 lazily generated candles against an
independent field oracle with a seven-bar consumer window, eager/streaming
parity, malformed rows, failure permanence and non-seekable EOF. The existing
installed/relocated C++11 consumer reads this API and passes its output to the
existing observation-aware strategy. These extend existing test entries and
link targets; no production installation or privileged dependency is added.


## Bounded multi-instrument historical merge

`MergedTickCsvReader` supplies the missing multi-input boundary within the
existing Data SDK. It composes `TickCsvReader`; it does not replace its schema,
change the single-file reader, or add another replay engine. A caller can route
each returned instrument to its existing `BarBuilder`, `BarStrategy` and
`ReplayMatcher`, with fills/observations accumulated in `ResearchPortfolio`.
The same offline libraries remain the only link dependencies.

```cpp
std::ifstream first("instrument-a.csv"), second("instrument-b.csv");
hepta::research::MergedTickCsvReader input({&first, &second}, 1000000);
hepta::research::Tick tick;
while (input.Next(tick)) {
    // Route by tick.instrument to existing, application-owned research objects.
    // Use globally distinct order IDs when sharing a ResearchPortfolio.
}
```

Supply 1 through 1,024 distinct, exclusively borrowed input streams; they must
outlive the noncopyable, nonmovable, thread-affine cursor. The pointer vector is
not retained. Each source uses the existing tick CSV header and incremental
volume convention. Header-only sources are allowed. Every nonempty source must
have its own instrument and nondecreasing timestamps with increasing sequences;
exact adjacent duplicate rows are emitted unchanged for downstream idempotency.
Instrument switches, conflicting/reversed sequences and reversed timestamps
are rejected, not silently repaired. Two files for the same instrument cannot
be merged as if they shared a trustworthy sequence namespace.

Construction reads headers only. The first `Next` obtains one head or checked
EOF from every source. Later calls refill only the previously emitted source,
then select the smallest timestamp, breaking ties by the original numeric input
index while retaining each source's row order. There is no seek, background
thread, sort-all vector, synthetic tick or deduplication. Storage is proportional
to source count and heap selection is logarithmic in source count per row. The
global `maxRows` bounds emitted records including duplicates; reading ahead is
bounded by one head per source, not an unbounded suffix. Invalid configuration
is rejected before any header is consumed; header/row parsing does not rewind
streams on failure.

This is a synchronous **offline event-time ordering convention**, not proof of
historical cross-feed receipt times, actual information availability, live-feed
watermarks, latency tolerance or exchange queue position. Equal timestamps do
not establish a real causal order. A backtest requiring arrival-time fidelity
must supply and validate that separate data/ordering contract instead of treating
this merger as evidence. Buffered heads are private and never exposed as early
signals. Observation-aware strategies still require explicit availability time.

EOF repeatedly returns false without changing output or `RowsRead`. Any source
validation, quota, allocation or I/O failure leaves that call's output/count
unchanged and permanently fails the cursor. Clearing or seeking an input cannot
resume it or skip a rejected row. A later error does not roll back an already
emitted prefix or mutations in caller-owned research objects: discard the failed
run rather than publish a success summary.

The existing Data test covers independent stable-sort oracles, same-time ties,
exact duplicates, immutable output/validation state, bad initial/refill records,
global quota and permanent failure, plus 100,000 lazily generated rows with
measured per-source read-ahead and the full 1,024-source boundary. The existing
installed/relocated external C++11 consumer runs two actual ReplayMatchers and
one ResearchPortfolio from the merged stream. It checks no same-time fills,
duplicate idempotency, both positions, fees, marked equity and EOF finalization
without liquidation. No new target, translation unit, installed-header path,
production installation, broker permission or execution mutation path is added.
The single-instrument replay CLI and canonical execution simulator are unchanged.
