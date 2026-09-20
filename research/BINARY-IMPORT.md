# Explicit binary depth-cache import

Status: EXPERIMENTAL
Applies to: integration/heptadll-modular-20260919
Implementation: `python/hepta_research/legacy_binary.py`
Tests: `tests/research/test_legacy_binary.py`, `tests/research/binary_install_smoke.py`

## Source and scope

The reference is `HeptaDLL-main@5f3703258bc4cad8f96e513d8d989c2441b4729d`:
`heptaHeptaDLL/heptaTradeCommonDefine.h` (blob
`f7d6bcff9377fcd99e7ceddf2afcb4d4ed4e916f`) defines the depth structure;
`heptaHeptaDLL/heptaPegasusSimulator.cpp` (blob
`cb25a118c7bdc9dd156027a17377f3fff0619061`) writes its `sizeof` bytes directly
in the CSV cache loop. The source credits Wu Chang Sheng and retains its
copyright and license notices. This is a new, explicit data reader, not a copy
of the original runtime, a new license grant or third-party clearance.

**The original file has no magic, version, packing, endian or record-count
header.** Select `--layout hepta-depth82-le-a8-v1` only with independent
knowledge of the producing build. File length alone cannot establish an ABI.
This profile means little endian, IEEE binary64, 32-bit milliseconds, signed
64-bit cumulative volume, 82-byte instrument storage, five quote levels and
8-byte native alignment. Each record is 424 bytes. Other packing, old field
sizes, big endian, vendor CTP structs and arbitrary `.bin` files are not supported.

| Selected field | Byte offset | Storage |
|---|---:|---|
| ExchangeID | 0 | NUL-terminated char[11], unqualified metadata |
| TradingDay | 11 | NUL-terminated char[9] |
| ActionDay | 20 | NUL-terminated char[9], possibly empty |
| UpdateTime | 29 | NUL-terminated char[9], HH:MM:SS |
| UpdateMillisec | 40 | little-endian uint32, 0..999 |
| InstrumentID | 44 | NUL-terminated char[82] |
| Buy/Sell quote arrays | 128 / 208 | five 16-byte native levels each; not imported |
| LastPrice | 288 | little-endian binary64 |
| Volume | 328 | little-endian int64, nonnegative |
| Turnover / OpenInterest | 336 / 344 | little-endian binary64 |

The last source field ends at 424. Fields not used by this adapter, native
padding and bytes after a string's first NUL are covered by the input and
per-record hashes but are not exported or interpreted. Native padding can
contain stale process memory. A record hash is not an exchange event ID.
The reader never invokes ctypes, pickle, a vendor library or native-object
loading on source bytes. ctypes is used only as an independent test fixture
producer, with separately asserted size and offsets.

## Clock and numeric semantics

Supply an explicit IANA `--clock-zone` and the same UTC `--sessions` file used
by the existing Tick importer. TradingDay is not the civil/action date.
Nonempty ActionDay must be valid and is not overridden. Empty ActionDay
requires `--action-day YYYYMMDD` or `--action-days dates.csv`. The latter has
exact header `row,action_day` and one consecutive, one-based record per Tick.
An external date must agree with every nonempty recorded ActionDay. This
supports mixed present/missing dates and midnight crossings without guessing.
A date recorded incorrectly by an old writer is not automatically repairable:
this importer validates the declared input, not the historical clock's truth.

LastPrice must map to a **unique** point of the supplied decimal tick grid
whose correctly rounded binary64 value equals the stored value. There is no
epsilon tolerance. For example, the stored binary64 for decimal 0.6 can map
to three 0.2 ticks; a nearby arithmetic-noise value does not. Adjacent grid
points must remain distinguishable in binary64. Signed 64-bit tick overflow,
nonfinite/sentinel/excessive values and ambiguous fine grids reject. Negative
historical futures prices and signed zero are allowed; source float hex is
retained even when signed zero normalizes to integer zero.

Turnover and open interest use Python's shortest round-trip decimal spelling
and the existing model's bounded decimal policy (magnitude <= 1e18, at most
18 decimal places). The original float hex is retained. Open interest cannot
be negative. These auxiliary fields do not drive the fill model.

## One existing computation path

```text
explicit binary records
  -> selected-field adapter
  -> existing normalize_ticks / tick_report
  -> same compiled hepta-research-bars / BarBuilder
  -> same evaluate_bars / replay / ResearchLedger
  -> same atomic report writer
```

An internal IMMSG-shaped CSV is only a private adapter for the selected fields.
Unused slots are placeholders, NEVER source depth or quote evidence; they are
removed from report provenance. The original binary SHA-256, record hashes,
recorded dates, selected numeric fields/float hex, external-date digest,
session digest and intermediate/normalized byte hashes remain available.
There is no second Python bar builder, matching engine, OMS or authority.
EOF does not complete the final bar. Repeated records retain their source-row
sequence and do not acquire invented venue de-duplication semantics.

```sh
sdk=/absolute/path/to/research-sdk
"$sdk/bin/hepta-research-binary" \
  --layout hepta-depth82-le-a8-v1 --ticks legacy-cache.bin \
  --sessions sessions.csv --output binary-report.json \
  --instrument TEST --clock-zone Asia/Shanghai --tick-size 0.2 \
  --period-us 60000000 --first-volume baseline \
  --capital 100000 --quantity 2 --fast 5 --slow 20
```

Use `--action-days` when required; no TradingDay fallback exists. A source
module invocation needs an absolute `--bars-executable` or HEPTA_RESEARCH_BARS.
The installed launcher defaults to the compiled converter in the SAME relocated
prefix. The caller selects trusted local SDK code, never an uploaded executable.

## Failure, installation and verification

Only no-follow regular files are read. Size is bounded by 64 MiB and 100000
records; size/count/partial-record and changed-during-read checks reject.
Foreign instruments, unterminated strings, invalid dates, DST ambiguity/gaps,
off-session or backward observations and same-day cumulative-volume resets
reject. Output cannot alias the source, sessions, date map or converter.
Parsing, building and evaluation must all succeed before publication. A
pre-replace failure preserves an existing report; post-replace directory-fsync
failure follows the existing writer's explicit error semantics, not a claimed
rollback. Neither result is trading authorization.

The new behavior tests use independent native-layout fixtures, exhaustive
short-record truncations, multiple exact price grids, source-mutation faults,
night/midnight dates, independent expected normalized observations, actual
compiled conversion and hand-computed fills/cost/equity. A separate standalone
CTest installs and relocates the SDK, then invokes the new command with
`python -I`, without PYTHONPATH/source imports. Existing CSV, client, outbox,
original install and canonical multi-UID acceptance tests remain required.
Local targeted success is not whole-repository or broker qualification.

## Remaining source responsibilities

This closes ONE verified binary-cache profile, not all HeptaDLL/Pegasus parity.
Other BIN ABIs, XML configuration/instrument consumers, complete queue matching,
margin/exchange settlement and remaining strategy behavior are not implemented
by this reader. The original `type_DB` branch in `StartMarketDataServer` merely
returns true without starting a reader. That is not an existing working DB
capability to claim as migrated; no success-shaped DB replacement is added.

Gateway, Execution, OMS, venue code, credential handling and runtime installs
are unchanged. CTP remains deferred; XT priority and PAPER/LIVE authorization
are unchanged. No private history, SDK or actual account data is published.
External-consumer completeness and upstream/vendor redistribution clearance
remain unverified; the old private repository and history remain intact.
