# HeptaDLL capability integration

Status: EXPERIMENTAL
Applies to: native research libraries and unprivileged proposal client; no venue promotion
Implementation: `strategies/native/`, `HeptaTrade/client/research_intent_client.*`, `cmake/HeptaResearch.cmake`
Tests: `tests/research_core_tests.cpp`, `tests/research_intent_client_tests.cpp`, `tests/fixtures/research/`

## Source and publication scope

The integration baseline is HeptaTrader commit
`5615b3ddb6badb1967771724d53b89c7ad194ddc` (tree
`741e36805f21469e98db5d5ebb0e05cfb0bcb26d`). The historical capability reference
is `TrillionniumFoundation/HeptaDLL-main` commit
`5f3703258bc4cad8f96e513d8d989c2441b4729d` (tree
`e05dd0640b82824f702b26590a514d2392af5bb4`).

The owner explicitly requested a public integration branch in HeptaTrader.
This change publishes newly implemented, dependency-separated capabilities,
not a verbatim source-tree import or a merge of the private repository's Git
history. No CTP/QDP binaries, vendor headers, old market samples, account
configuration, private credentials, Word documents or upstream build workflow
are imported. The old source and its notices remain untouched. In particular,
its Wu Chang Sheng copyright / consult-your-license notices are not replaced
by a claim that this work grants new rights over that source or its SDKs.
This record is not an independent licensing certification.

An organization default-branch code search for `heptaHeptaDLL` identified the
source repository's own build and usage references; the retired HeptaTrader
consumer is documented in [legacy retirement](legacy-retirement.md).
That search cannot certify external users, deployed artifacts, unindexed
repositories or all historical refs. Consequently the source repository is
NOT archived, deleted, renamed, made public or disabled by this integration.

## Capability disposition

| Historical reference | Current destination / disposition |
|---|---|
| `heptaKindleStickSeries`, `heptaKindleStick` | `hepta_research_market_data`: closed/incomplete OHLCV bars, bounded series, merge and extrema operations. New API; not binary or bitwise historical parity. |
| `heptaProductTradeTime`, calendar/date helpers | `SessionCalendar`: caller-supplied explicit UTC windows and trading-day labels. No dated exchange schedules are assumed current. |
| CSV data helpers and Pegasus data input | Strict versioned-by-header normalized tick/session CSV interface. Legacy CSV variants, BIN caches, DB feeds and live replay sources are not silently guessed. |
| `heptaBasicCTAStrategy`, Kindle callbacks, BasicAgent | `CtaSignal`: closed-bar callback to bounded signed target exposure. The example MA strategy is new demonstration code, not an old alpha strategy or profitability claim. |
| Pegasus matching, `heptaSettlement` | `hepta_research_replay`: standalone deterministic OFFLINE research matching/accounting. It does not replace the canonical Execution simulator. |
| Old direct order methods | No restoration. `ResearchIntentClient` submits explicitly bounded LMT/DAY proposals through the maintained NativeToolClient and Gateway. |
| `heptaFtdMdSpi`, `heptaFtdTradeSpi`, QDP and SDK overlay | Retained at the source baseline, not linked or published here. CTP remains deferred; XT/QMT keeps its selected priority. |
| Old position/order authority, mutex wrappers, logs, shutdown and `exit` behavior | Not adopted as a second core. Canonical Execution, OMS, state, risk, journal and process lifecycle remain authoritative. |

The table distinguishes an implemented responsibility from unsupported legacy
formats/modes. It is not a statement that every old method or output was
reproduced. Porting an additional format requires explicit schema and fixtures,
not importing the old process control and broker path to satisfy a linker.

## Build boundaries and consumers

`hepta_research_market_data`, `hepta_research_replay` and
`hepta_research_strategy` depend only on the C++ standard library.
`hepta_research_client` links the pure strategy contract and the existing native
client, not Execution implementation or a broker adapter. These modules are
owned by the existing `shadow-research` and `agent-entry` catalog entries.
The executable `hepta-research` is an offline development tool. It is not a new
daemon or automatically installed/released trading product.

```text
explicit historical data -> pure bars / strategy targets / offline replay
                                  |
                 separately bounded, deliberately supplied proposal
                                  |
                  NativeToolClient -> Tool Gateway
                                  |
                       sole Execution Service -> venue
```

A research target or offline account never becomes an authoritative live
position. There is intentionally no automatic target-minus-local-position
bridge into trading. A caller must construct an explicit bounded proposal;
service-side preview, session policy, authoritative state, risk and permission
checks remain mandatory. CTP, XT, IB PAPER and LIVE capability status is unchanged.

## Market-data contract

The tick header is exactly:

```text
instrument,timestamp_ms,sequence,price,cumulative_volume
```

The session header is exactly:

```text
trading_day,begin_ms,end_ms
```

Times are positive UTC epoch milliseconds; sessions are half-open `[begin,end)`.
Labels are valid Gregorian `YYYYMMDD`, explicit, sorted and nondecreasing.
The caller is responsible for supplying a verified calendar including holiday,
night-session and timezone conversions. Gaps are closed-market intervals.
No holiday API, host timezone, hard-coded exchange clock or network is used.
Fixtures deliberately use synthetic small times and explicit labels; they are
not representations of real September 2026 market sessions.

Rows are bounded to 512 bytes, instruments to 128 ASCII identifier bytes.
CSV quoting, implicit whitespace, unknown fields, negative/overflowing counters,
non-finite/nonpositive prices and malformed headers are rejected. CRLF is
accepted. Price formatting uses classic locale and round-trip precision.
The parser does not infer historical vendor column layouts.

Within a trading day, timestamps are nondecreasing, sequences strictly
increasing, cumulative volume nondecreasing. Only an exact repeat of the last
accepted tick is idempotent. Same-sequence changed data and out-of-order input
are errors, not silent corrections. Trading-day changes explicitly reset the
volume/sequence baseline; lunch/session breaks alone do not. Initial volume is
an explicit `Baseline` versus `IncludeCumulative` policy.

An intraday bar is anchored at its session start and truncated at the session
end; interval 0 selects a trading-day bar. No empty bars are fabricated. A later
accepted tick or an explicit monotone watermark closes eligible bars. Ticks
behind a watermark are rejected. `Finish()` emits an incomplete tail, never a
fabricated closed bar. Only complete bars can reach `CtaSignal`. Arithmetic
rejects counter/time overflow before committing state. Bounded windows expose
recent-indexed extrema with explicit newest/oldest tie policy and mean close.

## Offline matching and accounting

The replay engine is single-thread-owned, bounded by `maxOrders`, and has no
network, credentials, background threads, process exit, authoritative OMS or
production event output. Orders require a prior observation and can only fill
on a later distinct tick. Same-tick/lookahead fills are forbidden. Eligible
orders share that tick's cumulative-volume delta in submission FIFO order.
This is a deliberately simple liquidity assumption, not a validated exchange
queue, depth model or market-impact model.

DAY orders expire on trading-day rollover. IOC can partially fill and expires
the remainder; FOK requires all remaining units. Prices must align to the
configured tick size. Unfavourable configured slippage cannot be capped into an
otherwise invalid limit fill. Exact duplicate IDs are idempotent; changed IDs'
contents are conflicts. Rejecting invalid input does not partially advance the
account or tick cursor.

Accounting is futures-style signed inventory with average basis, multiplier,
per-unit fees, realized/unrealized P&L, equity and drawdown. Explicit settlement
moves marked P&L to realized P&L without changing equity. It is not a cash-equity,
margin, tax, FX or exchange-specific clearing model. Long-only signal restrictions
are explicit. No position is silently liquidated at end of input and the last
incomplete bar is not traded. The MA example stays an offline example.

## Proposal and recovery contract

`Prepare()` takes an explicit proposal and the supported wire contract identity,
performs non-authoritative input bounds, then calls `risk.preview_order` through
real native discovery. Proposal IDs are not execution command IDs. Only an
approved single-use receipt can create a `ResearchPreparedOrder`; its command
ID and permit come from Execution. Malformed, nested-decoy, duplicate-key,
nonapproved or expired receipts fail closed. The current adapter supports
LMT/DAY and the contract fields carried by the maintained tool wire; other
contract dimensions are rejected rather than silently dropped.

Before `Submit()`, `Persist()` must write or verify an immutable private outbox
record. The caller supplies an owned mode-0700 directory. Records are regular,
owned, single-link mode-0600 files with bounded bytes, canonical name, no symlink,
file fsync and directory fsync. Existing different bytes are a conflict.
Interrupted/partial records are not overwritten or treated as authorized sends.
The envelope stores a noncredential placeholder, not the Agent session token;
NativeToolClient injects the current token only at call time. The opaque preview
permit is sensitive and stays in the private record.

`Load()` validates the stored wire request, ID, placeholder and canonical
roundtrip. `Submit()` requires durable preparation and makes exactly one native
call. Neither transport loss nor an `uncertain` response causes re-preview,
expiry extension or generation of a new command ID. Recover in the same
service/session scope and query `execution.get_command_status` with the original
ID. Server epoch/session/permit validation remains authoritative. A transport
success or an `ok` envelope is not evidence of a fill. Only service/broker state
can establish that outcome. Outbox retention/cleanup belongs to the operator
after authoritative reconciliation, not an automatic age-based deletion.

## Running and validation scope

From a normal checkout:

```sh
./scripts/dev_core.sh
ctest --test-dir build/core --output-on-failure -L research
# Locate the built hepta-research executable in the configured build tree.
build/core/hepta-research bars TEST.FUT 60000 \
  tests/fixtures/research/sessions.csv tests/fixtures/research/ticks.csv
build/core/hepta-research backtest TEST.FUT 60000 \
  tests/fixtures/research/sessions.csv tests/fixtures/research/ticks.csv \
  1 2 2 1 10 1 0 10000
```

The core aggregate explicitly builds the new test binaries and offline CLI;
all four tests carry `core;research`, so existing GCC/Clang sanitizer and core
workflows execute them. Pure tests cover sessions, leap days, gaps, daily bars,
watermarks, duplicate/out-of-order ticks, volume resets, overflow, CSV roundtrip,
closed-bar bounds, extrema ties, FIFO partial fills, FOK/IOC/DAY, settlement,
reversal, fees, slippage and transactional numeric rejection.

The client test uses the real native discovery, typed Unix socket, Gateway host
and private filesystem. Its preview issuer and RecordingAuthority are explicitly
synthetic fixtures. It proves no send before durable preparation, identical
restart/retry IDs, cached uncertainty preservation, unreachable-Gateway failure,
no stored session token, and unsafe outbox/receipt rejection. A fresh executable
is stopped after durable preparation and killed with SIGKILL; another executable
loads the same outbox and sends without another preview. A cold Gateway fixture
forwards the original request after the test authority resolves uncertainty.
The in-memory authority and Gateway-local lease cache are fixtures, not evidence
that a real broker/session fencing recovery campaign has completed. It is not an IB/CTP
broker experiment or a replacement for canonical coordinator, installed-process
and host acceptance tests.

Source correctness, remote CI completion, installed release acceptance, legacy
behavior equivalence, external consumers, licensing evidence and broker
qualification are separate claims. Final source archival remains conditional on
consumer migration and explicit acceptance; preserving the source is deliberate.

## Integration branch acceptance and baseline repair

The four existing module/build projections were refreshed from the actual CMake
core graph in commit `9b66b921906689059341c52ae8e51b5abb1deb29`. The temporary
metadata-writing Integration Workbench and its helper are now removed; ordinary
read-only PR core, source/monitoring and GCC/Clang sanitizer workflows own
acceptance. Their criteria and required checks are unchanged. The retained IB
inventory's additive SDK-independent target projection is not an executed IB
SDK build. Manual workflow dispatch remains available.

Three different defects are distinguished:

- The integration client fixture previously expected a cached uncertain result
  to become duplicate. The production Gateway deliberately preserves uncertainty.
  The corrected test requires uncertainty until an explicit authoritative status
  resolution, and covers warm-cache and cold-Gateway paths using the original
  command ID, expiry and payload. No production permission or replay check was
  weakened to satisfy the test.
- The existing research smoke check assumed every implementation was Python.
  Native research sources and standalone headers now undergo real C++ syntax
  compilation and compiler-emitted dependency inspection. Repository dependencies
  must remain inside the catalogued pure research module; a negative fixture
  proves an Execution header is detected. Existing Python compile/import checks
  remain active. Native link/behavior tests remain in the canonical CMake suite.
- Main Core Runtime CI run `35439003534` (2026-09-19) already failed its installed
  generation cost-curve test with `OMS_GENERATION_RUNTIME_INDEX_RECORD_INVALID`.
  The Python checkpoint projector indexed owner/fence/control IDs as mutation
  commands, unlike native `ApplyRecoveredOwnershipEventLocked`. In particular,
  `order-terminal-N` produced an empty-operation/unknown-status row. The repair
  excludes those exact control event types from command indexing while retaining
  ledger bytes and hot owner/fence state. Ordinary pre-intent `reject` events
  still retain command identity and default to the place operation, as in native
  recovery. The wire/index format and strict V2 reader remain unchanged.

The regression uses production-shaped independent terminal IDs, repeated seals,
control-state retention, byte-identical legacy export, rebase and pre-intent
rejections. Its failures were reproduced before the projector repair. Corrupt
or previously malformed generations are still rejected, not silently adopted;
this change does not repair or migrate a deployed host's existing generation.

Local targeted and full-suite outcomes are reported on the PR for the tested
revision. Source integration must not be called release-accepted until the
ordinary exact-head CI, installed-process and PID1 acceptance succeed. No
expected-failure marker, skipped assertion or relaxed admission rule is added.
External consumer migration, all legacy format/output parity, vendor licensing
and real venue qualification are not certified by these tests. The source
repository remains retained and unchanged pending those independent decisions.
