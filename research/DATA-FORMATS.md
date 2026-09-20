# Additional legacy data consumers: stock bars and Tick CSV

Status: EXPERIMENTAL
Applies to: integration/heptadll-modular-20260919
Implementation: `python/hepta_research/stock.py`, `python/hepta_research/legacy_ticks.py`, `python/hepta_research/legacy.py`
Tests: `tests/research/test_legacy_formats.py`, `tests/research/format_install_smoke.py`

## Scope and source

This extends the one-minute futures-bar schema in [LEGACY-IMPORT.md](LEGACY-IMPORT.md).
Existing futures invocations retain their default layout and required explicit
volume convention. The original source reference is
`HeptaDLL-main@5f3703258bc4cad8f96e513d8d989c2441b4729d`,
`heptaHeptaDLL/heptaDataFileHelper.h` and `.cpp` (blob
`dfeb7f9093f84b76a4fc7601326ab187c1418f67`). The reviewed readers are
`ReadheptaStockKindleFile`, `ParseheptaTickDataRow`, and
`ParseZS_CZCE_TickDataRow`. Their notices and author attribution to Wu Chang Sheng
remain at that source. These are newly implemented strict data readers, not
imports of the original parser, private Git history, CTP SDK or binaries.
No third-party redistribution clearance or complete legacy parity is asserted.

All routes reuse `pipeline.evaluate_bars`, `model.replay` and `ResearchLedger`.
Ticks also invoke the existing compiled `hepta-research-bars`/`BarBuilder`; there
is no second Python bar engine or matching implementation. Research values are
hypothetical, never authoritative quotes, fills, risk approval or trading rights.
The Gateway, Execution, OMS and venue implementations are unchanged. CTP remains
deferred, XT priority is unchanged and LIVE is not enabled.

## Existing futures headers

The positional futures reader additionally accepts the original documented
header aliases: `time`, `Volume`, `TurnOver`, `HighTimeStamp`, `LowTimeStamp`.
These map only to their already defined positions; names are case-insensitive.
Swapping fields, extra columns, empty prices and arbitrary header spellings
still reject. Both volume fields and both turnover fields remain preserved;
`--volume-field` is still required for the default `future` layout.

## Seven-field stock bars

Select `hepta-research-import --layout stock`. The optional exact header is:

```text
DateTime,Open,High,Low,Close,Volume,TurnOver
```

Headerless input uses this same order. The label is the interval END, not its
beginning. `--period-seconds` (1..86400) is mandatory; no period is inferred from
the filename or an old hard-coded 180-second branch. The selected IANA clock zone
is explicit. Opening wall time is closing wall time minus the chosen period.
Both endpoints must map unambiguously to UTC and retain the same UTC offset;
intervals spanning daylight-saving transitions reject. Labels are
`YYYY-MM-DD HH:MM:SS` or `YYYYMMDD_HHMMSS`.

Whole bars must fit within caller-supplied UTC sessions. OHLC must be positive,
consistent and exactly on the supplied tick grid. Volume is an unsigned per-bar
count and turnover is nonnegative. Missing prices are never forward-filled.
Unknown tick counts and open interest remain null. The source close label,
OHLC ticks, per-bar volume, turnover, offset and trading day remain in the report.
A source-owned `--complete-through-us` watermark determines completeness; only
one final incomplete bar is allowed. EOF does not complete it.

```sh
sdk=/absolute/path/to/research-sdk
"$sdk/bin/hepta-research-import" --layout stock \
  --bars stock.csv --sessions sessions.csv --output stock-report.json \
  --instrument TEST --clock-zone Asia/Shanghai --period-seconds 60 \
  --complete-through-us "$SOURCE_COMPLETE_THROUGH_US" \
  --tick-size 0.01 --capital 100000 --quantity 2 --fast 5 --slow 20
```

Do not pass `--columns` or `--volume-field` with `stock`; those are futures-only
options. This reader consumes only the seven-field layout, not arbitrary stock
vendor CSV, corporate-action adjustments, account data or a current calendar.

## Four explicitly selected Tick layouts

`hepta-research-ticks --layout NAME` accepts one exact positional layout per file.
Indices below are zero-based. Unspecified depth fields stay raw; they do not
participate in the bar calculation or the hypothetical fill model.

| Layout | Fields | Instrument / trading day | Action date | Clock / fraction | Last price / cumulative volume |
|---|---:|---|---|---|---|
| `hepta32` | 32 | 0 / 1 | externally supplied | 2 / 3, milliseconds | 4 / 5 |
| `immsg34` | 34 | 2 / 3 | externally supplied | 4 / 5, milliseconds | 6 / 7 |
| `immsg35` | 35 | 2 / 3 | field 4 | 5 / 6, milliseconds | 7 / 8 |
| `zs58` | 58 | 3 / 0 | externally supplied | 1 / 2, microseconds | 37 / 38 |

IMMSG requires field 1 to equal `IMMSG`; field 0 is retained but not used as the
exchange clock. Clocks are `HH:MM:SS`, except `zs58` uses `HHMMSS`. Milliseconds
must be 0..999; ZS microseconds 0..999999 and are not truncated to milliseconds.
The old ZS reader accesses index 57; this reader requires all 58 fields rather
than inheriting its insufficient length guard.

For formats without ActionDay, supply exactly one of:

- `--action-day YYYYMMDD` for a file whose rows all have that actual civil date;
- `--action-days dates.csv` with exact header `row,action_day` and one
  consecutive one-based DATA-row entry for every Tick, excluding an optional header.

The latter supports midnight crossings. Missing/extra/map-duplicate rows reject.
TradingDay is NEVER substituted for ActionDay: a night session may belong to a
later trading day. The `immsg35` source date cannot be overridden. All formats
must agree with explicit UTC sessions and trading days; clock ambiguity, backward
time/day, foreign instruments and same-day cumulative-volume resets reject.

Input is headerless by default. `--has-header` explicitly consumes one header;
selected columns must be named `InstrumentID`, `TradingDay`, `UpdateTime`,
`LastPrice`, `Volume`, `Turnover`, `OpenInterest`, `UpdateMillisec` (or
`UpdateMicrosec` for ZS), and `ActionDay` when present. IMMSG also requires
`Localtime,MsgType` in columns 0/1. Matching is case-insensitive, never a reorder.
Unselected header names are retained, not asserted as a qualified depth schema.

Rows are assigned monotonically increasing source-row sequence numbers. These
are NOT exchange event identities. Repeated source rows are preserved because
this format does not establish whether they are legitimate events or duplicate
callbacks. There is no invented venue de-duplication. Cumulative volume still
prevents duplicated volume increments for repeated equal-volume observations.

```sh
"$sdk/bin/hepta-research-ticks" --layout immsg35 \
  --ticks old-ticks.csv --sessions sessions.csv --output tick-report.json \
  --instrument TEST --clock-zone Asia/Shanghai --tick-size 0.2 \
  --period-us 60000000 --first-volume baseline \
  --capital 100000 --quantity 2 --fast 5 --slow 20
```

`--period-us 0` selects the existing trading-day builder. `baseline` excludes the
first cumulative count of each trading day; `include` includes it. The choice is
mandatory. The final C++ bar remains incomplete. Normalization snapshots the
parsed sessions before invoking the builder, so changing the original file cannot
substitute an unhashed second calendar read. The report preserves original input,
session and optional date-map hashes, normalized-byte hashes, selected observations
and all raw source columns. Raw depth remains explicitly UNQUALIFIED, not a broker
order book. Negative finite future prices are allowed only when exactly on-grid.

The installed launcher defaults to the compiled builder in the SAME relocated
prefix. A source invocation via `python -m hepta_research.legacy_ticks` requires an
explicit absolute `--bars-executable` or `HEPTA_RESEARCH_BARS`. No shell, PATH search,
CSV-success stub or alternate fill engine is used. The executable is trusted local
SDK code, not a user-uploaded program; do not point it at untrusted binaries.

## Bounds, publication and acceptance

New stock/Tick sources are regular files, unquoted ASCII, nonempty fields, maximum
4096 bytes per row and 64 MiB per source file. Tick fields are at most 128 characters
and at most 100000 records are accepted. Stock bars also obey the existing explicit
bar bound (default 100000, maximum 1000000). Input is validated before computation;
malformed late input, builder failure or timeout does not publish a new report.
Output may not replace any input, date map or builder executable. The existing
atomic report writer and its file/directory fsync error semantics are reused.

`test_legacy_formats.py` tests the real C++ converter, independent expected Tick
streams, known next-bar financial results, old header aliases, date maps, session
and DST boundaries, exact prices, truncation, failure and publication behavior.
The unchanged `test_legacy.py` remains required for the earlier futures interface.
Standalone CTest also installs to a temporary prefix, relocates it, and invokes
stock plus all four Tick commands with `python -I`, no PYTHONPATH and no source
imports. It validates known fills/equity, source hashes and late-failure output
preservation. These tests are discovered by the EXISTING remote research matrix;
no workflow permission, ownership verifier or canonical test is weakened.

[BINARY-IMPORT.md](BINARY-IMPORT.md) adds one explicitly selected 424-byte native
cache profile through this SAME Tick/bar/replay path; the CSV contracts above
are unchanged. Other BIN ABIs, DB/XML layouts, Pegasus queue/margin/settlement
parity and proprietary strategy behavior still require verified contracts and
fixtures. These are specific input-consumer additions, not all HeptaDLL
functionality. No unsupported capability is advertised as working. The old
private repository and history remain intact.
