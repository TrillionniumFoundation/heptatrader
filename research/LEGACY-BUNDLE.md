# Legacy BIN/XML input through the canonical research SDK

Status: EXPERIMENTAL
Source: public PR #106 at `acffe4ae84a8e4377fd92b0ac536a865c6f6b6f5`.
Integration base: PR #107 at `522e2eec63161e95e00e30269d4b6ffe00b5f591`.
Implementation: `import_legacy.py`, existing Data SDK and `hepta-research-replay`.
Tests: `../tests/research/legacy_bundle_behavior.py`, the existing CLI/data tests,
and actual installed/relocated `sdk_package_behavior.py`.

## One data/replay/accounting implementation

`hepta-research-import` is an offline, standard-library Python file adapter in
**the existing ResearchSDK**, not another SDK or strategy runtime. Its binary
record and inert XML profile parsing is ported from #106. It does not import
that branch's BarBuilder, ResearchLedger, matching engine, portfolio evaluator,
strategy client, or old HeptaDLL/vendor code. No private source/history/data is
published by this patch.

```text
explicit BIN or XML + explicit file bindings + caller clock/session evidence
 -> captured bounded source bytes
 -> selected fields + per-row clock evidence
 -> ONE existing C++ LegacyTickCsvReader (all source files/layouts in order)
 -> canonical Tick CSV with incremental volume and UTC microseconds
 -> optional SAME native replay / strategy / ResearchLedger
 -> one atomic ZIP file with data, manifest and optional replay output
```

A Zs58-shaped private intermediate preserves microseconds across source layouts.
Its zero-filled unselected columns are adapter placeholders, **not source depth**.
CSV original fields are retained as unqualified metadata; binary padding and
unselected native memory are never exported. Original bytes are hash-bound.
There is no CSV-to-Python-Tick model or second cumulative-volume decoder.

## Input profiles

The binary profile is explicitly `hepta-depth82-le-a8-v1`: 424-byte records,
little-endian, the reviewed 82-byte instrument slot and field offsets. No ABI
auto-detection, native deserialization, vendor SDK or executable configuration
loading occurs. Strings must terminate; partial records, nonfinite prices,
invalid counters, missing dates and foreign instruments fail. Declared decimal
price-grid points must round-trip uniquely to the stored binary64; there is no
epsilon repair, implicit tick rounding or collapsed adjacent tick identity.

The inherited XML schema accepts inert configuration, instrument catalog and
indexed MDFile lists. UTF-8 is default; GB18030 is explicit. DTDs, entities,
namespaces, processing instructions, CDATA, unknown attributes and duplicate
singletons are rejected. Modes 0/1/2/3 select CSV/BIN single/list inputs; modes
4/5/6 (database/network/custom callback) remain unsupported, never pretend-success.
`Front`, `Instrument`, cache paths and file-list references are opaque strings.
Only the caller's explicit absolute paths in the separate binding JSON are read.
They must match the XML references and declared SHA-256s. The retained binding
schema is `hepta.research.xml-bindings.v1` with `schema`, `instrument_reference`,
`front_reference`, and `sources`. Each source has `index`, `reference`, `path`,
`sha256`, and `layout`; optional fields are `action_day`, `action_days_path`,
`action_days_sha256`, and `has_header`. A date-file path requires its digest.

CSV lists may mix `hepta32`, `immsg34`, `immsg35` and `zs58`. They are processed
in ascending numeric DateIndexId, never XML element order and never interpreted
as calendar dates. One native decoder preserves sequence/cumulative-volume/day
state across every boundary; a bad later source invalidates the entire run.

An actual civil ActionDay and explicit IANA clock zone are required. Present
source ActionDay cannot be overridden; absent dates require an explicit fixed
date or `row,action_day` map covering exactly every row. TradingDay is never a
fallback. DST gaps/ambiguities reject. Historical subminute UTC offsets reject
because the existing native clock contract accepts integer minutes only. Supplied
UTC sessions remain the authority for offline session membership; neither a
zone name nor a TradingDay invents an exchange calendar. Sessions can use the
canonical `open_us,close_us,trading_day` header or #106's equivalent begin/end
header; only the header is translated.

## Usage and output

The importer is installed next to the native replay executable in the POSIX
ResearchSDK. It defaults to that sibling **after relocation**. A source invocation
must supply `--native-executable` explicitly. Executables are selected by the
caller, never by input XML; the manifest records the actual executable SHA-256.
Python 3.9+ with timezone data and ordinary POSIX file operations is needed for
this import helper. The four C++ libraries remain independently usable.

```sh
hepta-research-import binary \
  --layout hepta-depth82-le-a8-v1 --ticks /data/history.bin \
  --sessions /data/sessions.csv --instrument TEST.FUT \
  --clock-zone Asia/Shanghai --tick-size 0.2 \
  --first-volume baseline --output /results/import.zip

hepta-research-import xml \
  --config /data/simulator.xml --instruments /data/instruments.xml \
  --bindings /data/explicit-bindings.json --file-list /data/list.xml \
  --sessions /data/sessions.csv --instrument TEST.FUT \
  --clock-zone Asia/Shanghai --first-volume baseline \
  --replay 60000000 5 20 1 fifo --initial-equity 100000 --fee-per-unit 0.2 \
  --output /results/replay.zip
```

`--first-volume day-start` counts the first cumulative counter only when the
caller actually has complete day-start capture. It is not a heuristic. Replay
requires explicit initial equity and fee per unit. For XML the multiplier comes
from the selected catalog; competing overrides reject. Binary replay also
requires `--multiplier`. XML PreBalance remains unapplied metadata. No automatic
funding, FX, margin, settlement schedule or live permission is inferred.

A successful ZIP contains `ticks.csv`, canonical `sessions.csv`, `manifest.json`
and, when requested, `replay.csv`. Member digests, input digests, source sequence
ranges and clock/model assumptions are recorded. This is provenance of the
observed files/code, not third-party authenticity or a trading certificate. The
native replay default model stays `offline-last-trade-liquidity-v1`; it is **not**
#106's next-bar model or explicit order-flow book. Different model results must
not be described as historical-output parity.

Input data/date files share a 64 MiB capture budget and at most 100,000 total
rows / 128 XML-bound files. XML metadata documents are individually capped at
4 MiB and bounded in depth/elements/attributes. Captures reject observed file
mutation, final-component symlinks, FIFOs and devices. Parent directories and the
caller-selected executable remain trusted local environment, not a sandbox.
No output may alias an input or executable. The ZIP is fsynced then atomically
replaced and its directory fsynced. Failure before replacement preserves the old
output; a directory-fsync failure **after** replacement reports failure but may
leave the new visible file. No crash/power-loss certificate is asserted.

## Native lifecycle and accounting continuation

`BarBuilder::Finish(Bar&)` exports a populated incomplete tail once and ends that
builder. Empty/repeated Finish leaves output unchanged. Push/AdvanceWatermark
after Finish reject, including an exact last-tick retry. EOF never implies that
the bar completed, creates an empty bar or changes its planned interval. A
previous explicit completeness watermark may already have closed the bar.
This imports #108's EOF capability into the existing #107 builder, not its
millisecond/cumulative-volume Tick type.

The existing native replay CLI retains its old positional calls and additionally
accepts `--initial-equity N`, `--multiplier N`, `--fee-per-unit N`. Defaults for
old calls remain 100000, 1 and 0.01. Unknown/repeated/invalid options fail. The
same existing ResearchLedger and ReplayMatcher consume these values. Its final
stdout flush is checked so a buffered write failure cannot return success.

## Preserved differences and retirement limits

#106's signed/zero-price integer-grid domain is **not** silently relabeled as
#107's positive-price binary64 domain. Nonpositive prices reject explicitly;
81-character historical identities outside the native 64-character contract
also reject. That retained source capability still needs a deliberate contract
extension and regression proof. Other BIN ABIs, arbitrary XML dialects, mixed
instruments in one import, #106's next-bar portfolio/order-flow consumers,
slippage models, external strategy compatibility and old DLL API/ABI parity are
not certified by this adapter. The original branches/source remain retained.

This importer does not change Gateway, native Execution/OMS, credentials, broker
qualification, venue order paths, workflow permissions or production installation.
The separate consolidation port repairs the offline OMS checkpoint projector;
see the integration record for that explicitly tested production-helper change. CTP
remains deferred, XT priority is unchanged, and LIVE remains unavailable.
Remote commit/CI/merge and old-repository retirement are separate acceptance
claims; local test results alone cannot close them.

The file-capture helper is installed and tested on POSIX only. The portable C++ SDK remains independently buildable elsewhere; no Windows filesystem equivalence is asserted.

The import acceptance matrix uses `python3 -I -S`: isolated mode with site initialization disabled. All helper imports are standard-library modules; no installed third-party Python package or source-tree module fallback is required. System timezone data must still be available.
