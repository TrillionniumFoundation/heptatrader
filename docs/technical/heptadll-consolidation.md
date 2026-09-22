# HeptaDLL consolidation on the existing #107 line

Status: PARTIAL IMPLEMENTATION; not repository retirement
Date: 2026-09-22
Canonical continuation: `integration/heptadll-modular-20260920`, PR #107
Initial comparison source: `522e2eec63161e95e00e30269d4b6ffe00b5f591`
Initial comparison tree: `36244a97e13f39a9ef8a8f99f77da436c02debfb`
Delivery parent after concurrent update: `d3fd08198ce5fe08ac2e381c4a2e0b5cfa01ebe3`
Delivery parent tree: `41b61e323e635155fa74a4759f380e59446690af`
Reference #106: `acffe4ae84a8e4377fd92b0ac536a865c6f6b6f5`
Reference #108: `56fd92bc94fd36e064d18c383ffeef9994d85fea`
Retained original: `HeptaDLL-main@5f3703258bc4cad8f96e513d8d989c2441b4729d`

## One implementation line, not a fourth framework

The selected engineering line remains #107. No alternate `Tick`, `BarBuilder`,
Python ledger, standalone strategy gateway, transport, OMS or private SDK is
linked into it. Capability ports are implemented through its existing targets.
This working continuation does not merge or close the three remote PRs, assert
complete historical parity, change repository visibility, or authorize trading.

The complete #107 and #106 source snapshots were obtained from the respective
GitHub Actions artifacts. The #107 full tree was verified against the tree above.
The concurrent d3fd081 source artifact was also verified against its complete
tree. Its exact two changed blobs reproduce two unchanged #107 rotation
regression failures. The delivery patch therefore targets d3fd081, not a stale
522e2ee HEAD, and repairs that compatibility conflict.
A local Git snapshot commit is not the actual remote commit: publishing a patch
must use the actual #107 parent and must not import synthetic snapshot ancestry.
No private HeptaDLL Git history, native binary, vendor header, recorded market
data, account configuration or original Word manual is imported by this port.
The attempted #108 control-ID filter conflicted with #107 historical rotation
regressions and is NOT included. Its pending-activation and pre-intent-reject
fixes remain, along with unchanged original metadata/rotation/rebase assertions.

Public #106/#108 source references record provenance, not blanket licensing or
clean-room certification. Retain original author/vendor notices at their source.

## Capability comparison and disposition

“Existing” below means present in the #107 source, not a fresh acceptance claim.
“Ported” means code/test integration in this continuation, not a remote merge.
“Retained gap” means the source branch must remain recoverable; it is not deleted
or replaced with an empty successful implementation.

| Capability | #106 | #107 | #108 | Consolidated disposition |
|---|---|---|---|---|
| UTC sessions and intraday/daily bars | Integer-grid/us | Positive binary64/us; daily across breaks already exists | Binary64/ms | Existing #107 core; do not duplicate daily bars or alias incompatible Tick layouts |
| Cumulative volume and source order | Native integer-grid builder | Existing CumulativeVolumeDecoder/LegacyTickCsvReader | Native TickCursor | One #107 decoder; every imported file/layout shares the same instance |
| Causal range/extrema queries | Native series | Bounded OHLC/causal confirmations | Simpler window | Retain #107 series, tie rules and observation-time contract |
| Tick CSV layouts | Four selected profiles | Four selected profiles | Normalized CSV | Existing native profiles; ported XML bindings marshal into them |
| Completed bar CSV | Selected stock/futures layouts | Three selected profiles with delivery evidence | Not the same importer | Existing #107 import API; do not infer completion/availability from EOF |
| No-tick completion | Explicit watermark | Explicit watermark | Explicit watermark | Existing #107 implementation, no extra calendar/core |
| EOF builder lifecycle | Finish boundary | Previously only Current/Watermark | One incomplete tail and terminal state | Ported as BarBuilder::Finish/Finished in existing Data SDK |
| 424-byte BIN | Explicit LE/a8/82-byte layout, unique grid round-trip | Missing | Missing | Ported profile-only adapter to existing native Data decoder |
| XML CSV/BIN single/list | Inert XML and hash-bound explicit paths | Missing | Missing | Ported strict schema; one native invocation preserves state across all files |
| XML GB18030 | Explicit option | Missing | Missing | Retained in ported parser; not automatically detected |
| Price domain | Signed/zero integer ticks and decimal contracts | Finite positive binary64 | Positive tick-grid with tolerance | Retained gap: no claim these are equivalent; adapter rejects nonpositive prices and grid ambiguity |
| Identity domain | Some historical slots allow >64 characters | 64-character canonical identifiers | Native bounded identifiers | Retained gap outside native identity; reject, never truncate |
| Research ledger/FIFO | Decimal accounting; optional FIFO attribution | Multiplier-aware P&L, average/FIFO, immutable fills | Private average-price account | Keep existing #107 Analytics; do not copy another cash/position authority |
| Explicit accounting inputs | Caller inputs | CLI formerly fixed defaults | ReplayConfig inputs | Ported named capital/multiplier/fee options; legacy calls retain defaults |
| Offline variation settlement | Basis rebase has no cash transfer | Explicit variation-settlement events | Inline settlement | Existing #107 contract; distinguish cash settlement from attribution-only rebasing |
| Multi-instrument valuation | Next-bar portfolio consumer | ResearchPortfolio + merged tick composition | Single-instrument replay | Retained gap for #106 CLI and next-bar model; existing #107 portfolio is not labeled an equivalent consumer |
| Last-trade matching | Different next-bar/order-flow models | Explicit incremental-volume/next-timestamp model | Cumulative volume and next tick | Keep #107 model; import/replay never silently runs the #106 evaluator |
| External order-flow book | Explicit price/time orders/cancels, queue-ahead, GTC, self-trade prevention | Not implemented | Not implemented | Retained gap; port as a separately named input/model contract using shared accounting, not another framework |
| Slippage grid | Distinct model inputs | No equivalent slippage policy | Explicit slippageTicks/grid policy | Retained gap; do not claim raw-tick fills reproduce this model |
| Strategy transport/outbox | Python StrategyGateway/outbox | Typed NativeStrategyClient + existing transport + private durable records | ResearchIntentClient/outbox | Keep #107 only; alternate callers require explicit migration, not concurrent outbox formats |
| SDK installation and relocation | Separate mixed-language SDK | Separate offline/client SDKs | Root-integrated native targets | Preserve #107 packages; new importer only in POSIX offline SDK, never production install |
| Output error handling | Converter flush fix | Replay summary could return success on buffered failure | Not the same CLI | Ported final flush check, real /dev/full regression |
| OMS generation/control identity | Not consolidation authority | Deliberately retains inert metadata IDs and validates them in the v2 reader | Filters those IDs and repairs reject/activation status | Preserve #107 metadata/rotation compatibility; port ONLY missing pre-intent reject and pending-activation status behavior, with four adapted regressions |
| Installed cost experiment | Not equivalent evidence | Explicit 256/min load fixture, unchanged 8/40/168 samples, independent 1/min rejection test | Pacing under a different fixture budget | Retain #107 fixture and its exact tests unchanged; do not add redundant waits, raise quotas or drop rate-gate assertions |
| CTP/QDP/direct SPI | Explicit exclusions | Deferred/not authorized | Explicit exclusions | No restoration or capability promotion; XT priority and LIVE unavailable remain |

## Unified boundary contract

Market data and public research time are UTC microseconds. Supplied Gregorian
trading-day labels are not civil ActionDay or an exchange calendar. CSV/BIN
adapters resolve real source clocks explicitly; DST gaps/ambiguity and unresolved
clock evidence fail. Incremental volume belongs to the Data SDK, and a file split
is not a stream reset. The selected price contract remains finite positive
binary64; the import boundary additionally requires a uniquely representable
declared decimal tick grid. A source that cannot satisfy that contract fails
rather than silently rounding, truncating identities or inventing dates.

Bar completion is a separate assertion from its planned interval and observation
arrival. A watermark is caller completeness evidence; EOF only terminates the
builder and may return an incomplete tail. Forecast observation time cannot be
backdated to a pivot or planned bar close. Order IDs, execution permits and
uncertain-result replay remain owned by the existing typed client/service.

Research fills/cash/positions are never authoritative live state. Model identity
is explicit: `offline-last-trade-liquidity-v1` is not an order-flow reconstruction,
next-bar strategy evaluator or an exchange slippage/margin/clearing model.
Different models may be added with separate contracts and independent oracles,
but common parsing/accounting/transport semantics must not have parallel cores.

## Concrete consumer register and retirement decision

| Consumer / source of evidence | Current disposition | Acceptance still required |
|---|---|---|
| Existing #107 synthetic replay CLI | Preserved; accepts explicit accounting parameters; same native modules | Exact final source/installed tests; not strategy performance |
| New BIN/XML import command | Uses canonical decoder and optional same replay; absolute hash-bound inputs | Exact final POSIX install/relocation tests and real caller profile sign-off |
| #107 external C++ SDK example/tests | Extended with installed Finish and actual relocated importer invocation | Exact final GCC/sanitizer package tests |
| #107 NativeStrategyClient callers | Existing transport/durability preserved, no schema/outbox migration introduced | Exact final installed client, Gateway/Execution and restart tests |
| Existing public Python SHADOW consumers | Retained unchanged, not silently migrated to a different model | Existing contract/process suites; source tests are not host qualification |
| #106 next-bar portfolio, order-flow CLI, signed-price consumers | Source retained at pinned branch; not replaced by last-trade replay | Port named model/input contracts, preserve all relevant scenarios and intentional differences |
| #106 StrategyGateway/outbox and #108 ResearchIntentClient callers | Source retained; do not claim wire/local-record compatibility | Named caller migration onto NativeStrategyClient; no automatic record-format conversion |
| Historical heptatrader monolith, HeptaStrategy, Pegasus, watchdog | Already retired in main, per legacy-retirement.md | Do not restore their exclusive direct-trading build graph |
| Original heptaBasicAgent/AgentManager/SimMdSpi and VS/CMake builds | Original repository retained, not reclassified as unused | Determine actual deployments/packaged consumers before retirement |
| Upstream Pegasus HeptaTrader, external/private/binary-only installations | Unknown; not proven absent | Named owners/artifact versions and migrate/retain decisions |
| Vendor SDKs, legacy data, original manual | Remain in original repository; not published by this change | Applicable publication/redistribution determination, separate from code tests |

On 2026-09-22, organization/default-branch searches for `heptaBasicStrategy` and
`heptaHeptaDLL` returned original source/build references and the canonical
historical-retirement document. This is a bounded indexed search, not an external
consumer census. It omits non-default branches, unindexed installations, outside
organizations and binary-only callers. It cannot justify deletion or archival.

**Decision: retain the original repository, releases, history and #106/#108
branches.** Do not close alternate PRs as “fully superseded” while the retained
gaps above remain. Capability integration can enter main after its own review
and exact-head acceptance; complete legacy retirement is a later independent
claim. Repository archival does not migrate persistent host state or revoke an
old trading binary. No archive, branch deletion, visibility/protection change or
trading authorization has been performed by this working continuation.

## Acceptance and publication

Use the existing development/CI paths and the same target ownership checks.
Standalone CTest must execute a nonempty suite, install/relocate the offline SDK,
compile external C++11 users, and execute the installed importer without a
source-tree binary fallback. Root tests must retain installed client, actual
Gateway/Execution and recovery coverage. Python core/source lanes remain
nonempty and reject skipped cases. The installed process cost experiment retains
its original 8/40/168 samples, explicit finite fixture budget and independent
rate-gate denial test. The #108 sleep is not imported: it would duplicate an
existing resolved fixture boundary. This is not maximum-throughput evidence.

Exact commands, source tree and observed outcomes belong in the execution report,
not an unconditional success declaration here. Baseline #107 CI does not qualify
a modified source tree. A queued remote run, local synthetic Git parent, or a
successful related-source test is not a remote commit/merge or host/PID1/broker
qualification. Apply a reviewed patch only to the intended #107 ancestry, reject
concurrent HEAD movement, run the final source, then use normal reviewed merge.
