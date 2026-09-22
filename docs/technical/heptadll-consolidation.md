# HeptaDLL consolidation on the existing #107 line

Status: MERGED CORE; PARTIAL CONSUMER MIGRATION; not repository retirement
Date: 2026-09-22
Canonical continuation: `integration/heptadll-modular-20260920`, PR #107
Initial comparison source: `522e2eec63161e95e00e30269d4b6ffe00b5f591`
Initial comparison tree: `36244a97e13f39a9ef8a8f99f77da436c02debfb`
Delivery parent after concurrent update: `d3fd08198ce5fe08ac2e381c4a2e0b5cfa01ebe3`
Delivery parent tree: `41b61e323e635155fa74a4759f380e59446690af`
Reference #106: `acffe4ae84a8e4377fd92b0ac536a865c6f6b6f5`
Reference #108: `56fd92bc94fd36e064d18c383ffeef9994d85fea`
Retained original: `HeptaDLL-main@5f3703258bc4cad8f96e513d8d989c2441b4729d`

## Current baseline and historical records

PR #107 was merged normally as `245554c49725fee81dc5e86fea99c43bb1ce2a6c`,
with accepted head `bb4d55fc89c83d672fb022bc22e3a6d2be29ed47` and identical
complete tree `1fcd9846c93348e47f31a59175d5d7d9747d3f3b`. The sections
below preserve earlier implementation/delivery observations, not a claim that
#107 is still unmerged. New continuation uses this merged source on the SAME
`integration/heptadll-modular-20260920` line. Main merge and original repository
retirement remain separate decisions.

## One implementation line, not a fourth framework

The selected engineering line remains #107. No alternate `Tick`, `BarBuilder`,
Python ledger, standalone strategy gateway, transport, OMS or private SDK is
linked into it. Capability ports are implemented through its existing targets.
The initial pre-merge continuation did not merge or close the three remote PRs,
assert complete historical parity, change visibility, or authorize trading.
The later normal #107 merge is recorded above; compatibility retention remains.

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

## Continuation from 14097ff: shared accounting and named replay models

Delivery baseline: `14097ff8feba41a9d07be4eeb3fb7de790ea52e1`, complete tree
`cb2effe22522c163ed2164335d5a959ccf2fdc05`. The original matrix above records
what the BIN/XML delivery did; this continuation advances the rows below.
No fourth branch, framework, compiled target, transport or account core is added.

| Previous retained gap | Implemented continuation | Still distinct / retained |
|---|---|---|
| Signed/zero prices | Explicit SignedFinite in the existing ledger and portfolio; named replay models accept the declared signed grid | Data CSV/BIN/XML and BarBuilder stay positive-only; full signed-64/Decimal range and ABI parity are not claimed |
| Explicit external order flow | Native OrderFlowReplay: price/time queues, maker prices, external queue-ahead, partial fills/cancel, IOC/FAK mapping, FOK preflight, DAY/GTC and self-trade prevention | Full #106 JSON manifest/JSONL/report CLI and Python API remain retained; no depth-to-queue inference |
| Next-bar portfolio model | Native NextBarReplay with one same-currency capital, explicit delayed observations, strictly later opens and close-first reversal | Canonical marks must remain fresh; #106 CLOSE-based/null report and ingestion orchestration are not silently substituted |
| Slippage | Optional immutable grid/slippage policy in ReplayMatcher and NextBarReplay | Original ungridded positive last-trade default unchanged; bounded binary64 is not an arbitrary Decimal equivalence claim |
| Installed API consumer | Existing relocated C++11 SDK consumer exercises the new public models and signed accounting | This migrates that actual native consumer, not every historical CLI, strategy or binary application |

The new modes call the existing ResearchPortfolio/ResearchLedger; external flow
orders are offline liquidity input, not a second live OMS. Whole-event staging
retains failed-event atomicity and request identities without a persistence or
throughput claim. Public contract and exceptions are in `research/MODELS.md`,
installed in the separate offline developer package. The original four headers
and four archives remain the install boundary. No production target links these
models, and no broker permission, venue state or required CI check changes.

The native replay executable keeps all preceding assertions and adds independent
slippage/conservation oracles, signed accounting, self-trade/FOK/fee-failure
rollback, multi-instrument next-open and stale-mark fixtures. The same fixture
header is compiled against only the installed archives after relocation. Actual
local and exact-head remote outcomes belong on PR #107, not a hardcoded source
success status. A bounded differential comparison against the pinned #106
implementation is evidence only for the tested shared domain, not full parity.

Consumer disposition remains explicit: the #107 external native consumer uses
the new API; existing #107 replay/import and durable-client consumers retain
their original paths; #106 Python command/report/Decimal consumers and #108
client-record consumers remain on their pinned references until adapted. Unknown
external or binary users retain the original library. This retention is not
"all consumers migrated" and does not authorize closing #106/#108 as completely
superseded, deleting releases, changing visibility or archiving HeptaDLL-main.
Main acceptance and that later retirement decision remain independent.

## Post-merge normalized-portfolio consumer continuation

Baseline: actual main `245554c49725fee81dc5e86fea99c43bb1ce2a6c`, not a local
synthetic snapshot commit. No new integration branch, framework, compiled target,
translation unit, installed-header path, privileged SDK or account core is added.
The existing model executable and installed Python adapter now implement the
selected #106 normalized-portfolio manifest/CSV entry point using the SAME
NextBarReplay, ResearchPortfolio/ResearchLedger and Strategy SDK.

| Consumer or previous gap | Current decision | Remaining boundary |
|---|---|---|
| #106 normalized CSV/manifest, per-instrument MA, shared capital | Adapted through `hepta-research-models portfolio` | Whole-quantity, bounded binary64 execution domain; new report schema |
| CLOSE(t) before OPEN(t), incomplete final bar | Explicit opt-in AfterClosePhase; untimed partial close remains metadata | StrictlyLater remains the default for actual observed target/open callers |
| Historical stale/null valuation policy | Opt-in partial Valuation, null equity/gross when held marks unavailable | Original strict Snapshot still rejects; no live risk implication |
| Integer MA, fractional fixed-price slippage | Existing Strategy SDK exact-grid signal; explicit common decimal execution subgrid | Rescaling/representation failures reject instead of rounding |
| #107 offline/client SDK callers | Existing build/install/relocation/behavior assertions retained | Exact continuation-head acceptance is required |
| #106 arbitrary Decimal/fractional quantity/custom Python target/report APIs | Retain pinned #106 source and caller contracts | No source-compatible or arbitrary-magnitude claim |
| #106 StrategyGateway/outbox and #108 ResearchIntentClient records | Retain old records/callers until explicitly reconciled and adapted | No automatic record conversion or new-ID mutation retry |
| Original source/VS/CMake/binary/external consumers | Retain original library/history/releases | Named deployment/artifact owners and publication scope remain unresolved |

The original repository README now points new development to main and states its
legacy-compatibility retention role without changing source, build entry points,
visibility or copyright notices. Bounded organization source searches still do
not enumerate outside/private/binary installations. This continuation therefore
does not authorize archive/deletion or claim all historical consumers migrated.

The tests extend the existing replay and CLI hosts (including actual installed
and relocated consumers). They retain all preceding assertions and add exact
integer MA oracles, partial/strict valuation compatibility, phase and mark
atomicity, per-instrument costs, source splits, signed multi-instrument Decimal
test oracles, incomplete tails and failed-input/output preservation. Reports
record real local/remote outcomes; neither this document nor an older green
head substitutes for acceptance of the resulting source. Contract details are
`research/MODELS.md` and `research/MODEL-CLI.md`.


## Post-#110 prepared-client continuation

Baseline: main `e61dfcced6d95e9f81469f1e5c4eaf164cfb90d9`, tree
`562e58f4fd0cc4213b2960ab154a426dd6620457`. #107 and #110 are already
merged; the existing `integration/heptadll-modular-20260920` branch continues
from that real main parent. This is not another research framework or a claim
that a prior candidate's CI qualifies a new commit.

The remaining #108-style prepared-client workflow is now represented by
`PreparedStrategyCommand` and additive Prepare/Persist/Restore/Submit overloads
inside the existing NativeStrategyClient. Bound typed previews, HSR1 storage,
filesystem validation and NativeToolClient forwarding are reused rather than
copying the alternate client's JSON slicer, HRO1 store or transport. Supported
order/cancel/flatten proposals retain their original identities, payloads,
expiry and permits. Preparing is not sending, and an unpersisted object cannot
submit. A failed persistence retains the request but clears durability. Each
submission rereads the immutable record and rejects changed request bytes or
credential binding before forwarding. The complete method/failure and consumer
contract is in [the installed client SDK contract](../../research/CLIENT_PACKAGE.md#opaque-prepared-command-lifecycle-and-consumer-migration).

This migrates concrete checked-in consumers: the SDK behavioral executable and
its installed/relocated external C++11 copy exercise the new public methods;
the actual Gateway/Execution fixture additionally runs the same crash/restart
scenario through the opaque lifecycle. Its original scenario is retained, not
replaced. The real OMS journal is the send-count oracle, not a locally asserted
success flag. No new target, translation unit, archive, installed-header path,
production installation entry, command schema, OMS or broker authority is added.

The new API provides a destination for source-adapting #108 callers, not binary
or old-record compatibility. HRO1 records lack the canonical binding and remain
with the original client. #106's Python application-key/Decimal/outbox state
machine and its status-only handling of uncertain submissions remain on their
pinned reference. Those policies cannot be replaced by unconditional same-ID
Submit without an application decision. No historical record is converted,
credentialed anew, deleted or marked successfully migrated by this change.
Existing #107 HSR1 records remain usable under their original binding.

The previous model/portfolio/CSV/BIN/XML migrations and all of their tests remain
in the merged baseline. Full legacy ABI and arbitrary Decimal equivalence,
actual external/private/binary deployments, remaining original strategies,
publication/redistribution scope and broker/host qualification remain explicit
retained conditions. Original HeptaDLL-main source/history/releases and alternate
branches are preserved. Archival is still conditional on genuine named consumer
and publication evidence, not this engineering continuation or a green CI run.
