# Research data, strategy planning and offline evaluation

Status: EXPERIMENTAL
Applies to: integration/heptadll-modular-20260919
Implementation: `research`
Tests: `tests/research/market_data_tests.cpp`, `tests/research/test_csv.py`, `tests/research/test_model.py`, `tests/research/test_pipeline.py`, `tests/research/install_smoke.py`, `tests/research/test_legacy.py`, `tests/research/series_cases.hpp`, `tests/research/test_legacy_binary.py`, `tests/research/binary_install_smoke.py`, `tests/research/test_legacy_xml.py`, `tests/research/xml_install_smoke.py`, `tests/research/test_portfolio.py`, `tests/research/portfolio_install_smoke.py`, `tests/research/test_fifo.py`, `tests/research/test_matching.py`, `tests/research/test_order_flow.py`, `tests/research/order_flow_install_smoke.py`, `tests/research/watermark_cases.hpp`, `tests/research/test_watermark.py`, `tests/research/watermark_install_smoke.py`

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

## Explicit binary-cache consumer

[The binary import contract](../../research/BINARY-IMPORT.md) specifies exactly
one 424-byte little-endian, 8-byte-aligned depth-cache profile. The installed
`hepta-research-binary` requires an explicit producing ABI, clock zone, tick grid
and UTC sessions. It never infers ActionDay from TradingDay, deserializes native
objects, exports memory padding or treats unused depth slots as valid quotes.
Selected fields enter the SAME Tick normalization, compiled bar builder and
replay/accounting pipeline; no alternate execution or matching core is added.
Unique decimal-grid/binary64 round trips reject off-grid or ambiguous prices.
The existing unittest discovery includes the new behavior tests; an additional
standalone CTest installs, relocates and invokes the real command in isolation.
All earlier tests and privileged-runtime acceptance remain required. Other BIN
ABIs, XML consumers, full Pegasus semantics and external licensing/consumer
closure remain outside this specific format addition.

## Explicit XML/catalog/list consumer

[The XML input contract](../../research/XML-IMPORT.md) extends the input route
with bounded inert configuration, instrument catalogs and indexed file lists.
Every actual source is explicitly bound to a local path and digest; embedded
paths, caches, broker credentials and old output settings are never executed.
CSV/BIN single/list modes reuse the original normalizers and one extracted
`normalized_report` compiled-builder entry. File boundaries do not reset bar,
strategy or ledger state. Clock/session/date identity remains explicit.

The installed `hepta-research-xml` takes tick size and research multiplier from
the selected historical futures record, but requires caller-supplied capital
and costs. That catalog is not authoritative broker state or a current-market
rulebook. Duplicate references, malformed XML, unsupported DB/network/callback
modes, digest mismatches and cross-file time/volume regressions reject.

The existing CTest discovery includes behavior and actual converter tests.
The additional standalone XML install test performs CMake installation,
relocation and isolated CSV/BIN single/list command invocation. Original tests,
canonical runtime CI and permissions are retained. This is not full XML-dialect,
Pegasus matching/margin/settlement or legacy strategy equivalence; external
consumer/licensing closure and old-repository retirement remain unresolved.

## Causal multi-instrument portfolio consumer

[The portfolio contract](../../research/PORTFOLIO.md) defines the installed
`hepta-research-portfolio` and explicit normalized-bar manifest. The original
single-file parser still rejects mixed symbols; bounded portfolio composition
merges separately validated instrument streams using CLOSE/OPEN events.
Per-instrument signals and accounting reuse `MovingAverageTarget` and
`ResearchLedger`; portfolio capital is counted once, never once per symbol.

Future closes cannot affect earlier cross-symbol valuations. An incomplete
bar's untimed close is retained separately, not stamped with its nominal end.
Stale held-position marks produce null equity and an explicit valuation gap.
There is no silent FX, margin, settlement, financing or annualization model.
File splits preserve strategy, position and fee state. Source digests and exact
local bindings remain mandatory; rejected input preserves an earlier report.

Behavior/differential tests run through existing unittest discovery. Standalone
CTest adds actual CMake installation, relocation and isolated-command success/
rejection tests. The root privileged installation, client/Gateway/Execution/OMS,
CTP deferral and XT priority are unchanged. This closes the normalized-bar,
single-currency portfolio composition profile, not arbitrary mixed legacy Tick/
BIN/XML ingestion or full Pegasus/strategy equivalence. The old repository and
all original runtime acceptance remain intact.

## Explicit order-flow matching and FIFO attribution

[The order-flow contract](../../research/ORDER-FLOW.md) defines the installed
`hepta-research-order-flow`, bounded mixed-instrument event stream and independent
FIFO attribution over the SAME `ResearchLedger`. Explicit external/research
orders share price/time queues; partial fills, FAK/IOC, all-or-none FOK, partial
cancel priority, self-trade prevention and DAY expiry have executable behavior.
A file boundary does not reset queues, order identities, capital or FIFO lots.

This is an offline input profile, not a second Execution authority and not a
claim to infer historical order queues from raw Tick/BIN/XML snapshots. Fees and
marks remain explicit, stale held marks suppress valuation, and a FIFO basis
rebase changes attribution only: no exchange cash settlement or margin is
invented. The existing normalized-bar and portfolio consumers remain unchanged.

The three new unittest files run under existing discovery; independent slow
queue/per-unit FIFO references check results against the original cash ledger.
Standalone CTest adds actual CMake installation, relocation and isolated CLI
success/rejection through `order_flow_install_smoke.py`. The research-only SDK
installs the new command, not the privileged runtime. Existing workflows,
Gateway/Execution/OMS, CTP deferral, XT priority and licensing/consumer retirement
requirements remain unchanged. Full Pegasus/strategy/exchange parity and exact
revision remote acceptance are separate claims, not inferred from these tests.

## Explicit no-tick completion

[The watermark contract](../../research/WATERMARK.md) adds
`BarBuilder::AdvanceWatermark` and the optional converter `--watermark-us` flag.
An explicit caller completeness promise can close an elapsed populated bar
without inventing a tick, price, empty interval or fill. Daily bars span supplied
session breaks; sequence and cumulative-volume baselines survive closure.
Clock regression, late new/conflicting ticks and activity after EOF reject.
Without the flag, existing converter/importer EOF semantics remain unchanged.

The existing C++ executable includes boundary tests and an independent integer
OHLC/volume oracle; existing Python discovery includes sixteen real-converter
methods. Buffered stdout failure is checked at flush and returns a failure code.
A new standalone CTest performs actual installation, prefix relocation, external
CMake consumption and installed-converter checks. No compiled target, build
ownership record, privileged installation, execution client or broker path is
added or replaced. The alternative #107 branch remains a retained comparison,
not a second linked implementation; further capability parity is not asserted.
