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

## CSV input stream exception policy

Every CSV entry point accepts caller-enabled `eofbit`, `failbit` and `badbit`
exception masks without disabling them or clearing the borrowed stream's state.
A clean end of input completes a valid final row even without a line terminator;
a subsequent cursor call returns `false` with unchanged output and row count.
A valid header-only tick/bar input is an empty dataset, not a missing header.

An actual source failure remains an error, including a stream-buffer exception
or `badbit` combined with `eofbit`. A syntactically complete row cut off by an
I/O fault is not salvaged as a valid final record. The existing bounded line,
row quota, schema validation and permanent cursor-failure rules still apply.
The same contract covers portable ticks, bars, sessions, merged tick streams
and the explicitly supported legacy tick/bar profiles. It grants no additional
completion evidence or authority to raw legacy data.

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

## Explicit legacy Tick CSV input

`LegacyTickCsvReader` adds a bounded mixed-instrument source adapter to **Data**.
It produces the existing incremental-volume `Tick`; BarBuilder, completed-bar
strategies, ReplayMatcher and ResearchPortfolio are unchanged. This is not a
second simulator, gateway, OMS or live feed. The installed SDK exposes the same
API as the source build; the single-instrument normalized replay CLI is unchanged.

The positional schemas were checked against `heptaDataFileHelper.cpp`, functions
`ParseheptaTickDataRow` and `ParseZS_CZCE_TickDataRow`, in retained reference
`HeptaDLL-main@5f3703258bc4cad8f96e513d8d989c2441b4729d` (blob
`dfeb7f9093f84b76a4fc7601326ab187c1418f67`). The reference's Wu Chang Sheng notices
remain there. This is a new implementation of explicit data contracts, not a
copy of that parser, private Git history, SDK or dataset, and not a clean-room or
redistribution-clearance certificate. It does not merge the separate Python
research implementation in PR #106 or create another integration branch.

Select ONE layout explicitly. Column indices are zero-based:

| Layout enum | Exact field count | Instrument / TradingDay | ActionDay | Clock / fraction | LastPrice / cumulative Volume | Turnover / OpenInterest |
|---|---:|---|---|---|---|---|
| `Hepta32` | 32 | 0 / 1 | supplied independently | 2 / 3 (milliseconds) | 4 / 5 | 7 / 29 |
| `Immsg34` | 34 | 2 / 3 | supplied independently | 4 / 5 (milliseconds) | 6 / 7 | 9 / 31 |
| `Immsg35` | 35 | 2 / 3 | 4 | 5 / 6 (milliseconds) | 7 / 8 | 10 / 32 |
| `Zs58` | 58 | 3 / 0 | supplied independently | 1 / 2 (microseconds) | 37 / 38 | 46 / 39 |

IMMSG column 1 must be `IMMSG`; its local receipt column 0 is preserved, not used
as the exchange clock. Clock syntax is exactly `HH:MM:SS`, or `HHMMSS` for ZS.
Milliseconds are 0..999 and microseconds are 0..999999. ZS fractions remain full
microseconds: they are not truncated to the old millisecond representation.
The ZS schema requires all 58 columns, including index 57; it does not inherit
the original reader's shorter length guard. Unknown layouts and extra fields
are not guessed. This API covers these four Tick layouts, not every old bar,
BIN-cache, XML, database or custom-callback input format.

The constructor requires 1..64 explicitly bound instruments with validated
`SessionSchedule` values, a row-clock resolver, and an explicit first-volume
policy. It copies/owns the schedule values and resolver function object; the
input stream and anything captured by the callback must outlive their use.
The reader is thread-affine. The caller must not concurrently read/reposition
the stream or re-enter the cursor through its resolver.

The resolver receives the one-based DATA row number, instrument, TradingDay and
source ActionDay (empty except in `Immsg35`). It must return the verified actual
civil date and the local-minus-UTC offset in minutes for THAT row. Missing
ActionDay is never replaced with TradingDay; night sessions and midnight
crossings require the correct per-row civil-date mapping. An explicit source
ActionDay cannot be overridden. Gregorian dates are validated, offsets are
bounded to -840..840, and converted UTC timestamps must be nonnegative. There
is no host timezone, `mktime`, daylight-saving inference or holiday service.
The caller resolves ambiguous/nonexistent local times and supplies the correct
offset; bounds alone do not certify a timezone mapping. Every converted tick
must belong to the bound instrument's half-open UTC session with exactly the
supplied TradingDay label.

For example, after independently validating the session and civil-clock input:

```cpp
std::map<std::string, SessionSchedule> sessions = verifiedSessions;
LegacyTickCsvReader reader(input, LegacyTickCsvLayout::Immsg35, sessions,
    [&verifiedClockByRow](std::size_t row, const std::string&,
                         const std::string&, const std::string&) {
        return verifiedClockByRow.at(row); // actual ActionDay + explicit UTC offset
    }, false); // false: first cumulative observation is the baseline, not new volume
LegacyTickRecord record;
while (reader.Next(record)) {
    // Feed record.tick to the EXISTING per-instrument Data/Replay consumers.
    // Preserve sourceFields when an audit of unqualified columns is required.
}
// Only clean EOF establishes complete input; publish no success after failure.
```

`true` for the first-volume policy instead includes the first cumulative count
of EACH instrument/trading day. Cumulative volume is an unsigned-in-practice
int64 count; a decrease within one trading day rejects, including across a
session break. A later trading day resets the baseline. Interleaved instruments
have independent counter state. Global UTC time cannot reverse; equal-time
rows retain physical input order. No sorting or lookahead creates artificial
historical information availability.

The emitted sequence is the one-based source DATA row, not a broker event ID.
Repeated raw rows are preserved with new sequence IDs; an unchanged cumulative
counter contributes zero extra volume. There is no evidence to deduplicate
historical rows as exchange retries. Downstream normalized readers/matchers
retain their existing exact-Tick retry behavior. The record keeps TradingDay,
ActionDay, offset, original cumulative count, finite nonnegative turnover/open
interest, and all source columns. Raw depth columns are bounded text only, not
validated books, quote authority, execution liquidity or current market rules.
The current positive finite `Tick.price` contract is retained; negative-price
futures histories are explicitly unsupported by this reader and are not repaired.

Headerless input is the default. `hasHeader=true` validates selected positional
names case-insensitively: InstrumentID, TradingDay, UpdateTime, UpdateMillisec
(or UpdateMicrosec), LastPrice, Volume, Turnover, OpenInterest, ActionDay when
present, and Localtime/MsgType for IMMSG. Other header names are retained only
as unqualified schema positions. Rows use unquoted printable ASCII, nonempty
fields of at most 128 bytes and the existing 4096-byte line bound. CRLF is
accepted. Whitespace in numeric/identifier/date/clock fields, nonfinite values,
invalid times, signs/overflow in counters and malformed field counts reject.

The cursor retains one decoder per allowed instrument and one bounded row,
not the history. `maxRows` is a global emitted-row quota. EOF and every failure
leave the output and emitted count unchanged. A failed row permanently disables
this cursor, even after clearing/seeking the borrowed stream. It never skips,
retries or consumes the resolver again after failure. The selected cumulative
decoder is staged before publication. Caller callback side effects and previously
consumed research outputs cannot be rolled back: discard a later-failed run.

The existing Data test covers all four schemas, both first-volume policies,
header/no-header, interleaved counters, same-row repeats, night/midnight/day
rollover, precise ZS microseconds, Gregorian/offset oracles, malformed input,
wrong sessions, callback/I/O failure, immutable output and permanent failure.
A non-seekable lazy 100,000-row source checks independent values and one-row
read-ahead with the full 64-instrument binding limit. These are cases within
existing tests, not 100,000 separate test methods. The installed/relocated C++11
consumer sends raw mixed-instrument input through two existing BarBuilders,
strategies and ReplayMatchers plus one ResearchPortfolio. It checks same-time
nonfills, zero repeat volume, once-only fees, explicit forecast availability,
partial tails and no EOF liquidation. Its synthetic final equity is 1028.5
from 1000 initial capital, not investment or strategy-performance evidence.
EOF does not invent a completed bar, fill, credential or production permission.


## Explicit legacy completed-bar CSV input

`LegacyBarCsvReader` is an additional **Data** input boundary, not another
strategy, simulator or execution runtime. It emits the existing `Bar` with an
explicit observation record. The positional contracts were checked against
`ReadheptaFutureKindleFile`, `ParseheptaFutureKindleRow`, and the non-optional
`ReadheptaStockKindleFile` path of `heptaDataFileHelper.cpp`, plus
`heptaKindleStick.h` and `heptaTimeStamp.{h,cpp}`, at retained source
`HeptaDLL-main@5f3703258bc4cad8f96e513d8d989c2441b4729d`. This is a new
implementation of selected data contracts, not literal source import or a
certificate of legacy output/API/ABI parity.

### Select the actual layout, never autodetect it

The constructor requires one bound instrument, a `SessionSchedule`, a layout
and an evidence callback. These files do not contain instrument identity or a
reliable trading-day/UTC/arrival-time/completion declaration. The caller must
bind those independently; a filename is not an authenticated identity.

| Layout | Columns, in source order | Period label |
|---|---|---|
| `Futures11` | `TimeStamp,time,Open,High,Low,Close,Volume,LastVolume,TurnOver,LastTurnOver,OpenInterest` | Start of a 60-second bar |
| `Futures13` | The same 11 columns, then `HighTimeStamp,LowTimeStamp` | Start of a 60-second bar |
| `Stock7` | Civil end time, open, high, low, close, bar volume, bar turnover | End of a 180-second bar |

These are positional profiles, not a claim that every stock publisher uses a
particular header spelling. An empty `expectedHeader` selects headerless input;
otherwise the first line must exactly equal the caller-supplied bounded header
(after an optional CR). A header with the wrong field count is rejected before
reading the stream. There is no automatic header skipping, column permutation,
quoted-field grammar or guessing from a row's length.

**The futures column `LastVolume`, not `Volume`, becomes `Bar::volume`.**
Likewise, `LastTurnOver` becomes the record's per-bar `turnover`. The other two
counters are retained as `cumulativeVolume` and `cumulativeTurnover`, with
`hasCumulativeTotals=true`; open interest has a separate availability flag.
Stock volume/turnover are already per-bar; no cumulative totals or open interest
are invented. Source columns remain bounded text in `sourceFields`, not
additional qualified measurements. This differs intentionally from filling a
missing OHLC value using the preceding row: missing prices are rejected.

### Explicit clocks and completion evidence

Futures civil text is exactly `YYYYMMDD_HHMMSS`; stock text is exactly
`YYYY-MM-DD HH:MM:SS`. Dates are Gregorian, clocks reject leap-second/overflow
spellings, and the reviewed scope is nonnegative civil/UTC Unix microseconds.
No host timezone, DST, holiday table or trading-day-as-calendar-day fallback is
used. The caller provides the actual per-row offset in minutes east of UTC,
bounded to [-840,840], and explicit UTC session windows. The entire bar must
fit in one window, even if another adjacent window has the same trading day.
The window supplies `Bar::tradingDay`; it can differ from `sourceCivilDay`.
Session crossings and clipped/partial bars are rejected, not stretched.

The nonzero futures numeric `TimeStamp` uses **microseconds since the civil
1601-01-01 epoch** of the reviewed `heptaTimeStamp` representation, not Unix
microseconds or 100-nanosecond FILETIME ticks. Its Unix-epoch offset is
11,644,473,600,000,000 microseconds. It must agree with the source civil text
within that exact second; the numeric subsecond part is preserved. Zero selects
the required civil text. Nonzero high/low timestamps use the same explicit
basis, convert with the same supplied UTC offset and must lie in the half-open
bar interval. Zero means unknown, not the bar's start. Other epoch/timezone
variants need a separately reviewed profile rather than silent inference.

These columns do **not** prove tick count, completion or when the completed
bar became available. The required `LegacyBarEvidenceResolver` receives the
1-based data-row number, bound instrument and source civil day. It must supply
independently established `complete=true`, a positive `tickCount`, the UTC
offset, and the actual `observedAtUs >= bar.endUs`. Unknown evidence rejects
the row. A dataset row is not permission to invent tick count 1, infer tick
count from volume, declare completion at EOF or backdate a signal to bar close.
For delayed/offline data, supply the actual declared research availability
scenario; it is not evidence of historical feed arrival or live qualification.

```cpp
// `evidenceByRow` is independently prepared and matches this exact input;
// it is not derived from OHLC, volume, a wall-clock guess or row presence.
LegacyBarCsvReader reader(input, LegacyBarCsvLayout::Futures13,
    instrument, schedule,
    [&](std::size_t row, const std::string& symbol, const std::string& civilDay) {
        return evidenceByRow.lookup(row, symbol, civilDay);
    }, expectedHeader);
LegacyBarRecord record;
Forecast forecast;
while (reader.Next(record)) {
    if (strategy.ObserveCompletedBar(record.bar, record.observedAtUs, forecast)) {
        // Forecast only. Any selected order must still use the existing
        // NativeStrategyClient -> Tool Gateway -> Execution Service path.
        consumeForecast(forecast);
    }
}
```

`BarSeries`, `MergeBars` and the existing portable bar CSV codec can consume
these bars. `WriteBarsCsv` serializes only its existing bar schema: it does not
retain the record's arrival evidence, turnover, original columns or extremum
timestamps. Preserve that evidence separately when converting archives and
supply it again at strategy observation. There is no automatic OHLC-to-tick
expansion, implied liquidity, fill generation or authoritative account state.

### Bounded streaming, ordering and failure

The cursor reads one non-seekable row at a time, retains only its previous
validation record, and does not cache history. Existing limits apply: 4096 bytes
per physical line, 128 printable unquoted ASCII bytes per nonempty field,
exact field count, and a positive global emitted-row quota (default 1,000,000).
Finite numeric values, checked integer ranges and consistent positive OHLC
are mandatory. Intervals cannot overlap, and trading days and actual observation
times cannot reverse. Futures totals cannot reverse within a trading day;
volume increase must cover that row's `LastVolume`. Missing intervals may
contain other volume; gaps are not silently filled or declared data-complete.
Totals may reset at a supplied new trading day; an intraday counter reset requires
an explicit new input segment, not automatic counter repair.

Each successful row publishes a fully validated independent value. A parse,
clock, evidence, quota, session, allocation or I/O error leaves the output and
emitted count unchanged and permanently fails the reader. The stream and
caller callback are not rolled back: callers must not catch an error and skip
to a later row. EOF is repeatable and leaves output unchanged; it does not
complete a bar. Reader/callback use is thread-affine and non-reentrant.

The existing Data executable covers all three layouts, header/headerless input,
five offsets, numeric/text epochs, microseconds, independent cumulative/bar
amounts, unknown/invalid completion evidence, OHLC/calendar errors, corrupt
continuations, quota/I/O failure and single-window semantics. A generated
10,000-row non-seekable fixture compares every emitted bar with an independent
clock/counter/price oracle across daily resets. The installed/relocated external
C++11 consumer calls all three profiles through the exported Data library,
passes actual delayed observations to the existing Strategy library, then
merges and round-trips portable bars. No previous test or assertion is removed.

## Runnable legacy import and completed-bar forecasts

The existing installed `hepta-research-replay` executable now exposes the seven
selected legacy layouts through the SAME Data and Strategy libraries. Its
original positional normalized-tick replay and output remain unchanged. There
is no second importer library, matching engine, native transport or live-order
path, and no source-tree-only replacement for the installed executable.

### Tick import into the existing replay

```sh
hepta-research-replay --import-legacy-ticks \
  Immsg35 raw-ticks.csv row-clocks.csv sessions.csv TEST.FUT \
  baseline headerless 1000000 > normalized.candidate.csv && \
  mv normalized.candidate.csv normalized.csv

hepta-research-replay normalized.csv sessions.csv TEST.FUT 60000000 5 20 1 fifo
```

Select exactly `Hepta32`, `Immsg34`, `Immsg35` or `Zs58`, and exactly one bound
instrument per invocation. `baseline` assigns zero incremental volume to the
first cumulative observation; `day-start` counts that first value and requires
the caller's guarantee of complete day-start capture. Select `headerless` or
`header` explicitly; the latter uses the existing reader's positional header
checks. No column count, filename or trading-day label selects a clock/layout.
The SDK's mixed-instrument API remains available separately.

`row-clocks.csv` has this exact header and one entry per source DATA row:

```csv
row,instrument,trading_day,action_day,utc_offset_minutes
1,TEST.FUT,20260921,20260920,540
```

The example is an explicitly declared synthetic night-session clock, not an
exchange-calendar assertion. Supply the independently verified civil date and
local-minus-UTC offset for EVERY row, together with the existing UTC session
CSV. The row, instrument and trading-day bindings must match the source. A
source ActionDay in `Immsg35` must agree as well. Other layouts cannot obtain
ActionDay from their TradingDay or from the machine timezone. Repeated source
rows retain distinct source-row sequences with zero repeated cumulative-volume
increase; these sequences are not authenticated exchange event identifiers.
ZS microseconds are retained without millisecond truncation.

Success emits only the existing portable five-column Tick CSV. Turnover,
open interest, depth and original source columns are not in that portable
schema: retain the source, clock evidence and session files separately. This is
an explicit projection for research, not a lossless legacy archive migration.
The unchanged replay consumes that output with its existing next-tick and
terminal-order semantics; importing never constitutes broker authorization.

### Bar forecasts without fabricated ticks or fills

```sh
hepta-research-replay --forecast-legacy-bars \
  Futures13 raw-bars.csv bar-evidence.csv sessions.csv TEST.FUT 5 20 1000000 \
  > forecasts.candidate.csv && mv forecasts.candidate.csv forecasts.csv
```

Select `Futures11`, `Futures13` or `Stock7`. This CLI accepts **headerless** source
bars only; explicit custom-header selection remains available in the Data SDK.
The existing 60-second start-labelled futures and 180-second end-labelled stock
default profiles, civil-1601 futures timestamps, and per-bar versus cumulative
amounts are unchanged. An explicit `--period-us` may select a positive duration;
no period or epoch is inferred or silently rescaled.
`FAST` and `SLOW` select the existing illustrative moving-average calculation;
this is not a port of every historical strategy or a performance recommendation.

`bar-evidence.csv` has this exact header:

```csv
row,instrument,source_civil_day,utc_offset_minutes,tick_count,observed_at_us,complete
```

Each entry binds the corresponding one-based source DATA row and instrument,
its actual civil day, explicit signed UTC offset, positive independently known
tick count, actual declared research availability in UTC microseconds, and
completion `1`. A false/unknown completion or missing evidence rejects the row.
The SDK checks availability at or after the bar end and preserves its monotonic
sequence. Tick count is a checked unsigned 64-bit value; it is not inferred from
volume, and it does not determine liquidity. The metadata file is a caller
assertion, not a cryptographic certificate of real historical arrival times.

Output is a research forecast CSV with exactly:

```csv
instrument,trading_day,bar_begin_us,bar_end_us,observed_at_us,direction
```

Only changed directions from the existing strategy are emitted, including a
transition to zero. The strategy uses `ObserveCompletedBar` at the supplied
availability time, not an earlier period boundary. A valid warm-up-only dataset
may produce just the header. An empty source is an error. Bars are not expanded
into fictional ticks, fills, equity or authoritative positions. Any downstream
trading intent must independently use the existing NativeStrategyClient -> Tool
Gateway -> Execution Service boundary and its ordinary authorization rules.

### Validation, output storage and acceptance

Both modes require an exact metadata header and exactly one matching metadata
row per source row: missing, extra, reordered, conflicting or malformed evidence
fails the whole invocation. Sidecar lines are bounded to 4096 bytes and fields
to 128 printable unquoted ASCII bytes; CRLF and a final line without LF are
accepted. Offsets are integer minutes in [-840,840]; there is no DST/calendar
fallback. `MAX_ROWS` defaults to 1,000,000 and must be 1..10,000,000. Session,
source-field and SDK state validation remain in the existing Data library.

These two new modes validate ALL input and evidence before publishing any
stdout. Parsed rows are incremental; output is spooled to a temporary file,
using bounded RAM plus disk proportional to the encoded output. A late source,
evidence, quota, session, parsing or spool-write/flush failure returns nonzero
without publishing even a CSV header. No unbounded history vector is added.

This is not a durable artifact transaction. After validation, a write failure,
broken output pipe or process crash during final stdout delivery can leave a
prefix; callers MUST require exit zero. Stage output to a distinct candidate
file and publish it only after success, as above, preserving the original
inputs. The example rename does not certify fsync/power-loss behavior. Never
interpret a file's presence, a CSV prefix or a research forecast as execution
success. These offline commands do not stream live signals or persist a
strategy execution outbox.

The existing CLI behavior test now exercises all seven layouts with independent
clock/quantity/forecast oracles, header and first-volume choices, actual
normalized replay, subsecond and night-session semantics, 10,000 streamed raw
rows, missing/late-corrupt evidence and input, quota failures, and real Linux
output/spool-storage failures. The existing SDK installation test runs that SAME
matrix against the relocated installed executable, retaining the original
external C++11 consumer, component/version rejections and default CLI checks.
No existing test, target, installed-header path, production installation set,
Gateway/Execution permission or venue status is removed or weakened.

These commands close selected runnable-consumer gaps, not all historical
API/ABI, BIN/XML/database, queue-position or external-user-strategy equivalence.
The retained HeptaDLL source and alternate integration branches remain intact;
CTP remains deferred behind XT and source archival still requires actual
consumer/authorization disposition. Exact-head CI observations belong on PR #107,
not in an unconditional source-code success declaration.

## Explicit duration for already formed legacy bars

The existing `LegacyBarCsvReader` now has a second constructor accepting a
positive `std::int64_t periodUs` immediately after the evidence resolver. The
original constructor remains defined, with its original 60-second futures and
180-second stock defaults. Both use the same Data implementation; no extra
reader library, price transformation, matcher, or trading authority is introduced.

```cpp
LegacyBarCsvReader bars(raw, LegacyBarCsvLayout::Futures13, "TEST.FUT",
                        sessions, independentEvidence, 300000000LL);
```

The value declares the duration of bars ALREADY in the input. It does not
aggregate one-minute OHLC into five-minute OHLC. Futures remain start-labelled;
Stock7 remains end-labelled, regardless of the chosen duration. Subsecond
futures labels retain their numeric microseconds; the stock text label retains
its source second precision. Gaps remain gaps. Quantity, turnover, tick count,
completion and actual availability keep their existing independent contracts.

The installed CLI exposes the same choice only in completed-bar forecast mode:

```sh
hepta-research-replay --forecast-legacy-bars \
  Futures13 actual-five-minute-bars.csv bar-evidence.csv sessions.csv TEST.FUT \
  5 20 1000000 --period-us 300000000 > forecasts.candidate.csv && \
  mv forecasts.candidate.csv forecasts.csv
```

`[MAX_ROWS] [--period-us POSITIVE_MICROSECONDS]` are trailing optional arguments
in that order. Omitting the option retains the original profile duration. Zero,
negative, fractional/scientific notation, overflow, a missing value, an unknown
option or use in Tick import mode fails. The SDK rejects nonpositive durations
before consuming an optional header. Positive durations still undergo checked
start/end arithmetic, same-session, non-overlap, extremum-time and observation
time validation. Neither adjacent sessions nor their breaks are silently joined.
Daily bars spanning breaks require a separate explicit contract; this option
does not turn them into a one-session bar or infer their exchange calendar.

The existing Data test covers explicit durations from one microsecond to one
hour, signed UTC offsets, optional headers, preserved source fields and actual
observation times, original/default-overload parity, extreme arithmetic,
end-exclusive extrema, pre-epoch underflow, overlap, session boundaries, quota,
poisoned cursors and a 1,000-row non-seekable sparse stream. The existing CLI
and relocated-SDK tests consume the new interface, compare clocks/forecasts
with independent fixtures, and reject late-invalid input before stdout release.
All earlier input, replay, client and installation assertions remain active.

## Legacy file bundle and EOF continuation

The existing Data SDK now receives the selected #106 BIN/XML profiles through
an import-only adapter; it does not acquire a parallel Python bar/ledger/client.
The existing BarBuilder adds explicit incomplete-tail EOF finalization from
#108, and the existing native replay accepts explicit capital, multiplier and
per-unit fees and rejects buffered output failure. Source and installed/relocated
tests exercise the same implementations. See [contract and remaining differences](LEGACY-BUNDLE.md).
These capabilities do not certify full historical parity, external consumer
retirement, remote exact-head CI, main merge or old-repository archival.
