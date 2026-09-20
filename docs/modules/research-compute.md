# Research data, strategy planning and offline evaluation

Status: EXPERIMENTAL
Applies to: integration/heptadll-modular-20260919
Implementation: `research`
Tests: `tests/research/market_data_tests.cpp`, `tests/research/test_csv.py`, `tests/research/test_model.py`, `tests/research/test_pipeline.py`, `tests/research/install_smoke.py`, `tests/research/test_legacy.py`, `tests/research/series_cases.hpp`

## Responsibilities and dependencies

[The research contract](../../research/README.md) owns input schemas, public APIs,
build commands, source provenance and the feature migration matrix.
`hepta_research_data` is a C++11 SDK-free static library; `hepta_research_bars` is
its concrete CSV consumer. Both compile through the root CMake graph. The core
test aggregate builds the consumer as well as its test executable. The Python
model depends only on the standard library. No broker ports, credentials or
Gateway/Execution implementation libraries are dependencies.

[The standalone SDK and pipeline](../../research/INSTALL.md) provide a relocatable
CMake export, installed Python modules and `hepta-research-replay`. This concrete
consumer validates normalized bars, runs a new bounded reference moving-average
strategy, and writes a byte-hashed, cost-explicit offline report. It is not a
claim to reproduce a proprietary legacy strategy. Standalone SDK installation
does not add any files to the root privileged runtime installation.

## Failure and state model

A malformed/off-session/conflicting tick is rejected without advancing the bar
state. The stream is single-writer and single-instrument; session data are explicit
inputs, not a current trading-calendar assertion. EOF is an incomplete bar. CSV
consumers must discard staged output on nonzero exit. Research balances are
hypothetical and never become authoritative runtime snapshots. Planning refuses
stale/incomplete position projections and outstanding orders. Replay has explicit
next-bar timing and execution costs; undefined performance ratios remain null.

The offline report consumer rejects invalid OHLC, overlapping bars, mixed
instruments, data after an incomplete bar and nonrepresentable prices. It uses
exact decimal scaling. No report is published until all input and computation
succeed. Annualization is opt-in and uses equally spaced completed observations,
not a final incomplete mark. No automatic funding or forced exit is invented.

## Tests and operational boundary

C++ tests and Python/CLI behavior tests run in the existing core CTest lane and
in isolated GCC/Clang/sanitizer research CI. The standalone install test moves
the prefix and builds an external C++ consumer, then uses the installed converter
and Python launcher without PYTHONPATH. This is a selected responsibility port,
not complete Pegasus matching or HeptaDLL ABI compatibility. No private
history/SDK is imported and no legacy runtime switch is reopened. Research
results confer neither PAPER nor LIVE authorization.

## Legacy CSV and K-line series continuation

[The legacy import/series contract](../../research/LEGACY-IMPORT.md) covers the
installed `hepta-research-import` and `BarSeries`. Explicit epoch/timezone,
UTC sessions, source completeness and volume semantics prevent silent legacy
reinterpretation. The importer retains source fields and unknown tick counts,
and reuses the existing offline evaluator rather than creating a second core.
Series peak/trough results carry their right-neighbor confirmation time;
corrections, trimming, threshold queries and aggregation remain research-only.
The existing CTest discovery and installed smoke exercise these additions.

## Stock and Tick data consumers

[Additional data formats](../../research/DATA-FORMATS.md) define seven-field
end-labelled stock bars and four explicit legacy Tick layouts. Missing actual
civil dates require external input; they are never guessed from TradingDay.
Ticks reuse the existing compiled BarBuilder, and every format reuses the
existing evaluator/ledger. Raw depth observations are retained but unqualified.
`tests/research/test_legacy_formats.py` adds behavior and real-converter tests.
`tests/research/format_install_smoke.py` installs, relocates and invokes all new
formats without source imports and is registered in standalone CTest. No new
compiled target, broker adapter, OMS owner or privileged install entry is added.
