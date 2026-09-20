# Standalone research SDK and end-to-end offline consumer

Status: EXPERIMENTAL
Applies to: integration/heptadll-modular-20260919
Implementation: research/CMakeLists.txt; research/python/hepta_research/pipeline.py
Tests: tests/research/test_pipeline.py; tests/research/install_smoke.py

Build this SDK separately from the canonical privileged runtime package:

```sh
cmake -S research -B build/research -DCMAKE_BUILD_TYPE=Release
cmake --build build/research --parallel 2
ctest --test-dir build/research --output-on-failure
cmake --install build/research --prefix /absolute/path/to/research-sdk
```

The prefix is relocatable. C++ consumers use `find_package(HeptaResearch CONFIG
REQUIRED)` and link `Hepta::ResearchData`; the exported target requires no source
checkout or vendor SDK. `Hepta::ResearchBars` names the installed CSV executable.
Python 3.10+ modules are installed under `share/heptatrader/research/python` and
the installed `hepta-research-replay` launcher resolves that relative to itself.
This is a CMake-distributed SDK, not a separately versioned PyPI distribution.
Root `cmake --install` does NOT install these research artifacts into the
privileged service package. No broker library, credential or service is added.

For normalized input formats, see the source `research/README.md`. For example:

```sh
set -eu
sdk=/absolute/path/to/research-sdk
# The converter can emit partial stdout on a late error: use a staging file.
"$sdk/bin/hepta-research-bars" ticks.csv sessions.csv 60000000 baseline > bars.csv.tmp
mv bars.csv.tmp bars.csv
"$sdk/bin/hepta-research-replay" --bars bars.csv --output report.json \
  --tick-size 0.01 --capital 100000 --quantity 2 --fast 5 --slow 20 \
  --slippage 0.01 --fee-per-unit 0.005
```

The reference moving-average strategy is a new, bounded, closed-bar example,
not a reproduction of proprietary HeptaDLL strategies. It has no Gateway import
or order route. Signals can fill only at a subsequent bar's open, with explicit
slippage, fees and multiplier. The final position is not silently liquidated;
a final complete-bar target may remain pending. EOF-incomplete data do not
produce a signal. All fills and balances are offline hypothetical values.

The consumer validates the complete bounded stream, OHLC, integer limits,
trading days, single-instrument order and completeness. Tick scale conversion
uses decimal arithmetic, never binary floating-point prices. It hashes the same
input bytes it reads. The JSON report includes data identity, explicit model
assumptions, decimal strings, costs, marked equity and unfilled final intent.
Reports replace the destination only after full validation, computation,
serialization and file fsync. A pre-replace failure preserves existing output;
a post-replace directory fsync failure reports an error without claiming rollback.
The output cannot be the input path or a hard-link/symlink alias of it.

Annualization is disabled by default. `--periods-per-year N` additionally requires
equally spaced complete-bar observations; final incomplete marks are excluded.
Nonpositive equity or insufficient complete observations produce an explicit
undefined-metric reason, not manufactured positive capital. Session calendars
are supplied data, not a claim of current exchange hours. Default input limit is
100000 bars; `--max-bars` permits an explicit bound up to 1000000.

`tests/research/install_smoke.py` installs, relocates and exercises the SDK:
a fresh external C++ project links it, the installed C++ converter feeds the
installed Python launcher, and import resolution is checked without PYTHONPATH.
CI runs this with the same compiler/sanitizer flags as the source behavior tests.
These checks are not broker, external host/PID1, or live-trading qualification.

The installed `hepta-research-import` consumes the explicitly qualified legacy
one-minute CSV layout through the same offline evaluator. Its required epoch,
clock-zone, volume-field, UTC session and completeness-watermark inputs, and the
installed C++ `BarSeries` API are documented in [LEGACY-IMPORT.md](LEGACY-IMPORT.md).
The installed smoke also exercises both new interfaces after relocating the SDK.

The same installed importer additionally supports `--layout stock` with an
explicit close-label period. The installed `hepta-research-ticks` consumes four
reviewed legacy Tick layouts using this prefix's existing C++ bar executable.
[DATA-FORMATS.md](DATA-FORMATS.md) owns those schemas and required date inputs.
Standalone CTest exercises both after relocation via
`tests/research/format_install_smoke.py`; the original install smoke remains
unchanged and required.
