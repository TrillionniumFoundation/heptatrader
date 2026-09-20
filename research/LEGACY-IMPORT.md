# Legacy data import and native K-line series

Status: EXPERIMENTAL
Applies to: integration/heptadll-modular-20260919
Implementation: `python/hepta_research/legacy.py`, `include/hepta/research/bar_series.hpp`
Tests: `tests/research/test_legacy.py`, `tests/research/series_cases.hpp`, `tests/research/install_smoke.py`

## Source and authority boundary

Source contract reviewed: `HeptaDLL-main@5f3703258bc4cad8f96e513d8d989c2441b4729d`,
`heptaDataFileHelper.cpp` (`ReadheptaFutureKindleFile`), `heptaKindleStick.h`,
`heptaTimeStamp.h`, and `heptaKindleStickSeries.h`. Legacy notices credit
Wu Chang Sheng and reserve rights. This implementation is a new SDK-free
responsibility port, not a copy of private history, vendor code or runtime.
It does not grant redistribution rights over that source or its dependencies.

The importer and series have no broker credential, network, Gateway, order,
OMS or daemon dependency. Import routes to the SAME `evaluate_bars`/`replay`
consumer used for normalized CSV, rather than creating a second matching or
accounting engine. The Gateway client and sole Execution authority are unchanged.
Neither imported data nor offline fills are accepted as execution truth.

## Supported legacy format

The default `--layout future` uses the reviewed one-minute, start-labelled CSV.
[DATA-FORMATS.md](DATA-FORMATS.md) specifies the additional explicit stock/Tick
layouts and historical header aliases. The futures layout remains:

```text
TimeStamp,DateTime,Open,High,Low,Close,TotalVolume,LastVolume,TotalTurnOver,LastTurnOver,OpenInterest
```

`--columns 13` adds `HighTime,LowTime` at the end. Headerless files are supported
with the explicit selected column count. Headers, when present, are validated
in order; the date label also accepts `Time`, `StartTime` or `szStartTime`.
No columns are inferred, sorted or silently dropped. Fields must be nonempty,
unquoted ASCII without padding. Lines are bounded to 4096 bytes and total bars
to the explicit `--max-bars` (default 100000, maximum 1000000). Files must be
regular files; final-component symlinks, FIFOs and devices are rejected.

OHLC must be consistent and exactly representable at `--tick-size` in signed
64-bit ticks. Arithmetic uses decimal values rather than binary price rounding.
Both volume fields, both turnover fields, open interest, OHLC ticks and optional
extremum timestamps are retained in `input.source_fields` in the report.
`--volume-field LastVolume|TotalVolume` is mandatory: exports differ in their
cumulative/in-bar convention, so the importer never guesses. The original field
values are retained regardless of this selection. The present reference strategy
uses close prices, not those volume fields.

**Legacy bars do not supply tick counts.** The import records `tick_count: null`;
it does not invent one tick, and does not weaken the existing normalized tick/bar
CSV contract. It creates validated `ReplayBar` objects and preserves extra fields
as report metadata instead. Turnover signs are preserved; negative open interest,
invalid counters, prices or OHLC are rejected. This is not exchange settlement.

## Clock, session and completeness rules

The reviewed `heptaTimeStamp` representation is microseconds from **1601-01-01**,
NOT Unix microseconds from 1970. The old code can encode supplied local wall-clock
fields without a timezone. Therefore `--clock-zone` is mandatory. Supply `UTC`
only for exports actually using UTC; otherwise supply the correct IANA zone,
for example `Asia/Shanghai`. The importer applies that explicit interpretation
and preserves the UTC offset used for every bar. It refuses nonexistent or
ambiguous daylight-saving wall times rather than selecting a fold silently.
System timezone data must be installed; no current trading-hours data is bundled.

Nonzero numeric time must agree with the second-resolution text label;
microseconds are retained. A zero timestamp uses the label. Supported labels
are `YYYYMMDD_HHMMSS` and `YYYY-MM-DD HH:MM:SS`. Optional nonzero high/low times
must lie within the bar. The bar lasts exactly 60000000 UTC microseconds.
Arbitrary end-labelled or multi-minute formats are not this schema.

Supply a second CSV with the exact header `begin_us,end_us,trading_day`.
Windows use UTC Unix microseconds and `[begin,end)` intervals. They must be
ordered, nonoverlapping and have nondecreasing valid trading days. A whole bar
must fit in one window. Trading-day assignment comes from this supplied calendar,
not the civil date, so a night session can belong to the next trading day.
No hard-coded exchange calendar is claimed current.

`--complete-through-us` is a source-supplied UTC Unix watermark. A bar is complete
only when its end does not exceed that value; EOF is not a completion signal.
Only the final bar may be incomplete. Backdated, overlapping or out-of-session
input is rejected, not repaired. The supplied watermark is an offline input
assumption, not broker evidence or an authorization credential.

## Installed consumer

Build and install the standalone SDK as described in `INSTALL.md`. The installed
launcher works after relocating the prefix and does not require PYTHONPATH.
For a reviewed export with matching session data and watermark:

```sh
sdk=/absolute/path/to/research-sdk
"$sdk/bin/hepta-research-import" \
  --bars legacy_1m.csv --sessions utc_sessions.csv --output legacy-report.json \
  --instrument TEST --clock-zone Asia/Shanghai --volume-field LastVolume \
  --complete-through-us "$SOURCE_COMPLETE_THROUGH_US" --columns 11 \
  --tick-size 0.01 --capital 100000 --quantity 2 --fast 5 --slow 20 \
  --slippage 0.01 --fee-per-unit 0.005
```

`SOURCE_COMPLETE_THROUGH_US` must come from the input's actual completeness
contract, not the current clock or the last row. No trading is performed.
The report records raw input and session SHA256, selected layout/zone/volume,
watermark, retained data fields, hypothetical equity/fills and explicit costs.
It is published only after both inputs and computation succeed. A pre-replace
error preserves old output; a directory-fsync error after replacement does not
pretend to roll back the visible file. Source/session path aliases cannot be
used as the output. Exit 2 is rejection, not a usable new report.

## Native C++ series contract

C++11 consumers include `<hepta/research/bar_series.hpp>` and link the already
exported `Hepta::ResearchData`. `BarSeries(instrument, capacity)` is single-writer,
single-instrument and bounded. `Append`, `Replace`, `RemoveBefore`, `RemoveAfter`,
chronological `At`, reverse `FromLatest`, OHLC-selectable `Highest`/`Lowest`,
`NextHigher`/`NextLower`, `Peaks`/`Troughs`, trading-day counts and `Aggregate`
cover additional former K-line responsibilities without a legacy strategy base.

Indices and query ranges are chronological and inclusive. Extrema ties select
the latest occurrence by default; an explicit flag selects the earliest.
Next-higher/lower queries are strict. Trim boundaries retain an exactly matching
start time. Replacement preserves instrument, interval and trading day, and
cannot turn a non-final bar incomplete. Mutations increment `Revision`; rejected
mutations leave data/revision unchanged. Callers invalidate cached indices and
references after changes. Historical corrections are explicit new revisions,
not evidence of what an old strategy could have known before the correction.

Centered peaks/troughs require strictly higher/lower values than both sides;
plateaus are not classified as swings. Every bar in the comparison window must
be complete, and the rightmost neighbor must have ended by the explicit as-of
time. Results contain `confirmedAtUs`, so a future neighbor is never presented
as knowledge available at the peak's own timestamp. Prefix-consistency tests
check this on exhaustive five-bar ternary price sequences.

Aggregation sums observed counts/volume with checked overflow and preserves OHLC.
It refuses cross-trading-day aggregation and marks a result incomplete across a
gap, because it has no session calendar proving that the gap was a market break.
No missing prices, gap bars or completed intervals are invented.

## Acceptance and remaining scope

Existing pipeline tests remain unchanged. Import behavior tests cover epoch
anchors, timezone offsets, DST fold/gap rejection, night-day attribution,
strict fields, unknown tick counts, failure atomicity, aliases and equality with
the normalized consumer's financial results. The installed smoke builds an
independent C++ series consumer and executes the relocated import launcher.
All are part of the existing research CTest/matrix/installed-consumer workflow;
no separate approval layer or weakened canonical check is introduced.

This does not claim full HeptaDLL ABI/behavior, all legacy data layouts, raw native
BIN struct decoding, database or XML support, Pegasus queue matching, margin or
settlement equivalence. Those need verified layouts, provenance and differential
fixtures, not a format guessed from `sizeof`. The reference moving-average
strategy is not a recovered proprietary strategy. CTP remains deferred and XT
priority, PAPER/LIVE authorization and private repository retention are unchanged.
The local research build is not full-root or multi-UID runtime acceptance; the
existing remote canonical workflow retains those checks on the exact commit.
