# Hepta research modules and legacy migration

Status: EXPERIMENTAL
Applies to: integration/heptadll-modular-20260919

## Ownership and source boundary

Integration baseline: `heptatrader@5615b3ddb6badb1967771724d53b89c7ad194ddc`.
Legacy reference: `HeptaDLL-main@5f3703258bc4cad8f96e513d8d989c2441b4729d`.
The existing integration workflow began at `570a4d9821d36147280b630d0e82d09a25684f1c`.
The owner explicitly requested a public integration branch. This change adds new,
small interfaces implementing selected legacy responsibilities; it does not copy
private Git history, the vendor SDK overlay, binaries, account data or credentials.
It does not assert that organization ownership grants third-party redistribution
rights. The legacy files credit Wu Chang Sheng and retain their original copyright
and license notices at the pinned source. No new license grant is invented here.

This is a behavior-oriented port, NOT a drop-in HeptaDLL ABI or a claim of complete
feature/differential equivalence. Legacy classes are not made an alternate runtime.

| Legacy responsibility / source | Destination | Deliberate difference or remaining work |
|---|---|---|
| `heptaDate`, calendar arithmetic | `CivilDay` | Validated Gregorian arithmetic, not a current holiday calendar |
| `heptaProductTradeTime`, `heptaMarketTime` | explicit `Session` input | No silently current hard-coded exchange hours; caller supplies UTC windows and trading days |
| `heptaKindleStickSeries` | `BarBuilder`, `BarSeries` | Integer ticks, ordered bounded history, corrections/trims, OHLC queries, strict confirmed swings and aggregation; not old ABI equivalence |
| data-file helpers | `hepta-research-bars`, `hepta-research-import`, `hepta-research-ticks`, `hepta-research-binary` | Normalized ticks, futures/stock bars, four Tick CSV layouts and one explicit 424-byte binary profile; other BIN ABIs and DB/XML remain unsupported |
| `heptaBasicCTAStrategy::SetStrategyPosition` | `TargetPolicy.delta` | Bounded close-first target planning; no local claim that a close filled |
| `heptaBasicAgent` direct order methods | `StrategyGateway` | Existing heptactl/NativeToolClient only, Execution-issued identity and permit, durable uncertainty recovery |
| Pegasus replay and settlement | `replay`, `ResearchLedger` | Offline next-bar-open model, explicit costs and multiplier; NOT full queue matching, margin or exchange settlement parity |
| `heptaNetValueEvaluation` | `performance` | Explicit sampling frequency; undefined ratios are null, no automatic deposits |
| Ftd/CTP SPI and SDK | retained at legacy source | Deferred; no CTP connection or trading enabled, XT priority unchanged |
| monolith, old OMS/order references, threads, logging and process exit | no parallel production owner | Existing Execution/OMS/Gateway remain the owners; old ABI is not restored |

No exhaustive external-consumer census or upstream license clearance has been
established. Accordingly the private legacy repository and its release/history
are NOT archived, deleted, renamed or made public by this change. Completing a
subset port is not authorization to remove the remaining source. Do not claim
all HeptaDLL features migrated or broker qualification from these tests.

## Build and use

From the repository root, `./scripts/dev_core.sh` includes these targets and tests.
For an isolated SDK-free research build:

```sh
cmake -S research -B build/research -DCMAKE_BUILD_TYPE=Release
cmake --build build/research --parallel 2
ctest --test-dir build/research --output-on-failure
PYTHONPATH=research/python python3 -B -m unittest discover -s tests/research -p 'test_*.py'
```

The last command alone requires `HEPTA_RESEARCH_BARS` to name the built CSV tool;
CTest supplies it automatically. Python 3.10+ and POSIX are required by the client.
C++ consumers link `Hepta::ResearchData` and include
`hepta/research/market_data.hpp`. These libraries do not link into the privileged
Gateway/Execution binaries. No new daemon or vendor dependency is introduced.
Research artifacts are not installed into the privileged core runtime package.

## Data contract

`hepta-research-bars ticks.csv sessions.csv PERIOD_US baseline|include` writes CSV.
Headers are exact, fields unquoted, and rows bounded to 4096 bytes:

```text
instrument,trading_day,timestamp_us,sequence,price_ticks,cumulative_volume
EUR.USD,20260105,1000000,1,110010,100
EUR.USD,20260105,2000000,2,110020,105
```

```text
begin_us,end_us,trading_day
0,60000000,20260105
```

Timestamps/session bounds are explicit UTC microseconds since the epoch; the
short numbers above are synthetic test data, not real calendar observations.
A session is `[begin,end)`. `PERIOD_US=0` groups all supplied sessions sharing a
trading day; positive periods align to each session's open. No gap bars are
invented. The caller fixes the instrument's tick scale before ingestion.
`baseline` excludes the first cumulative volume of each trading day; `include`
includes it. Later same-day resets, conflicting duplicate identities, ordering
regressions, foreign instruments and off-session data are rejected. An exact
repeat of the last tick is ignored. The final EOF bar is incomplete, not a
finished trading interval. Earlier bars become complete only when a later bar
is observed. Tick arrays are not retained in memory.

Exit 0 means the entire input was accepted. A later error may occur after some
CSV has been emitted; stage output and discard it on nonzero exit. Do not treat
partial stdout as a successful import. No broker snapshot is produced here.

## Legacy CSV and series extension

[LEGACY-IMPORT.md](LEGACY-IMPORT.md) specifies the installed one-minute legacy
CSV consumer and C++ `BarSeries` API. The importer requires a clock zone, explicit
UTC sessions, volume-field choice and completeness watermark; it converts the
legacy 1601 epoch rather than treating it as Unix time. Raw bytes are hashed and
source fields retained. Unknown tick counts remain null. The importer reuses the
same `evaluate_bars`/`replay` accounting path and has no Gateway import.

`BarSeries` adds bounded ordered history, replacement/trimming, OHLC-selectable
extrema, first threshold crossings, trading-day counts, aggregation and strict
peaks/troughs with their actual confirmation time. Rejected mutations preserve
state. A future comparison bar cannot turn a centered historical peak into an
earlier signal. Existing normalized schemas and execution authority are unchanged.

## Additional input consumers

[DATA-FORMATS.md](DATA-FORMATS.md) specifies stock bars and four legacy Tick
CSV layouts. [BINARY-IMPORT.md](BINARY-IMPORT.md) specifies the installed binary
consumer for an explicitly selected 424-byte little-endian, 8-byte-aligned
82-character-instrument depth cache. Its native padding is hashed but never
exported. Dates and price-grid interpretation must be explicit; no ABI guessing,
TradingDay-as-ActionDay fallback or epsilon price repair is performed.
All routes reuse the existing compiled bar builder and offline accounting.
No original SPI, SDK, private history or parallel execution core is imported.

## Strategy and replay contract

`TargetPolicy.delta` accepts a bounded target and a complete, fresh client
projection of a position with no active orders. It returns a proposed delta,
not a risk approval. Reversals close toward zero first; the caller must obtain a
NEW authoritative observation before planning the opposite opening leg.

`replay` takes complete-bar signals and fills only at the NEXT bar's open with
explicit fixed slippage/fees. The last target is left pending; incomplete bars
produce no signal. `ResearchLedger` has cash, signed inventory, explicit multiplier
and fees; it never manufactures capital to keep a strategy solvent. Hypothetical
positions, prices and fills must never be sent to Gateway as execution truth.
`performance` requires equally spaced, cash-flow-adjusted positive equity and an
explicit periods-per-year value. This is not a full exchange settlement model.

## Gateway client contract

The only order route is:

```text
strategy -> StrategyGateway -> heptactl / NativeToolClient
         -> Tool Gateway -> existing Execution Service -> qualified adapter
```

Construct `HeptactlTransport` with explicit absolute installed CLI, Gateway socket
and owner-only session-token paths; construct `Outbox` with a private absolute
directory under the Agent's state area. Broker credentials are never inputs.
`prepare(key, LimitIntent(...))` only requests a preview and durably saves the
Execution-issued command/permit. `submit(key)` explicitly attempts that saved
intent. Current CLI field coverage limits this adapter to CASH/STK LMT DAY;
FUT/OPT and CTP open/close semantics are rejected, never approximated.

The exact fields, expiry, command ID and permit are persisted before placement.
A write/fsync failure prevents a new send. Once `sending` is persisted, subsequent
submits query `execution.get_command_status` for the original command, including
after timeout, process crash or restart. They never create a second placement.
A crash between the durable marker and actual I/O may need operator recovery;
a missing command is NOT automatic permission to re-place. An accepted command
is not a fill. Read authoritative state before the next strategy decision.

The outbox is request bookkeeping, NOT an OMS, position store or risk authority.
It uses owner-only files, no-follow opens, single-link checks, per-intent flock,
atomic replacement and file/directory fsync. Token/socket changes require explicit
recovery instead of rebinding old commands into a new session. Do not delete an
uncertain record to obtain a fresh ID. Returned prepare records contain a permit:
do not log, upload or publish them.

## Validation and limits

`market_data_tests.cpp` covers Gregorian anchors and exhaustive 1600..2400 date
round trips, session boundaries, daily breaks, extrema ties, duplicate/conflicting
input, cumulative volume policy and overflow boundaries. Python tests cover the
actual CSV executable, planning/accounting, next-bar causality, malformed input,
client restart, concurrent submissions, response loss, corrupt records, fsync
failure and file-permission/symlink/hard-link rejection. Transport doubles are
explicit test doubles, not evidence of a real venue.

`tests/research/process_smoke.py` separately reuses the canonical installed-runtime
fixture and digest-admitted artifact. On a disposable root Linux host it exercises
real installed heptactl/Gateway/Execution processes under different UIDs, response
loss after a send, a new client process, a service restart, risk rejection and final
zero-position reconciliation. It emits PASS only after clean shutdown, with exact
artifact/source identifiers. It is not a systemd/PID1 or broker qualification test.
Its cold CLI timings are observations, not a latency guarantee. Existing canonical
CI, source ownership checks and release acceptance are unchanged and still required.
