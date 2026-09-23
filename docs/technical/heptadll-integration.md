# HeptaDLL modular integration record

Status: EXPERIMENTAL
Date: 2026-09-21
Target baseline: `heptatrader@5615b3ddb6badb1967771724d53b89c7ad194ddc`
Reference baseline: `HeptaDLL-main@5f3703258bc4cad8f96e513d8d989c2441b4729d`
Branch: `integration/heptadll-modular-20260920`
Module contract: [C++ research SDK](../modules/research-sdk.md)

## Publication and provenance

The repository owner explicitly requested a public integration branch and remote
CI. This change publishes new modular implementations and tests of reviewed
capability contracts. It does not import the private repository's Git history,
vendor SDK files/binaries, account configuration, recorded market data or Word
manual. No repository visibility, branch protection or trading authorization is
changed. The source reference remains recoverable at the pinned commit.

Historical headers reviewed for the capability mapping retain the Wu Chang Sheng
copyright and license notices in their original repository. No new permission
for that source or for third-party SDK redistribution is asserted here. This is
not a clean-room/legal-clearance certificate or a source/ABI-equivalence claim.
A later literal-source import must retain applicable notices and resolve its
publication/redistribution scope separately. No blanket repository license is
invented by this integration.

## Capability disposition

| Reference assets under `heptaHeptaDLL/` | Destination and present scope |
|---|---|
| `heptaKindleStick`, `heptaKindleStickSeries` | `research_data`: bar building, bounded series, replacement, OHLC-field extrema, latest strict threshold queries, confirmed peaks/troughs, reverse indexing, retained-day count and OHLCV merging. These are explicit new APIs, not compatibility aliases for historical signatures. |
| `heptaDate`, `heptaTimeStamp`, `heptaProductTradeTime`, `heptaChinaTradingCalendar` | Explicit UTC session windows and Gregorian trading-day validation. Old exchange-rule tables and implicit local-time assumptions are not republished as current rules. |
| CSV/data helpers | Strict portable tick/session/completed-bar CSV, four selected legacy Tick layouts and three selected completed-bar layouts with default or explicitly declared positive duration through the SAME Data SDK. Mixed Tick instruments, explicit source clocks, correct per-bar amounts, original-column retention and independent completion/observation evidence are supported. Proprietary binary cache layouts and historical datasets remain in the source repository. |
| `heptaNetValueEvaluation`, `heptaSettlement` | Research-only cash-flow-adjusted metrics and multiplier-aware P&L ledger with explicit undefined ratios, fees, fill identity and offline variation-settlement events. No full exchange settlement/margin equivalence is asserted. |
| `heptaPegasusSimulator`, `heptaSimMdSpi`, `heptaSimTradeSpi`, `heptaOrderBook` | Existing `ReplayMatcher` and `OrderFlowReplay` provide bounded offline last-trade and explicit price/time order-flow models, including caller-supplied external queue-ahead volume. They do not reconstruct an exchange queue from depth snapshots or restore legacy live-feed/order callbacks. The canonical deterministic execution simulator is unchanged. |
| `heptaTickTradeManager` | `CumulativeTopOfBookTradeInference` ports only the uniquely solvable cumulative-volume/turnover arithmetic for a caller-evidenced previous one-tick bid/ask. Output is explicitly inferred and research-only. Wider-spread/multi-price reconstruction, venue-specific heuristics, statistics/logging and live-feed behavior remain retained rather than silently emulated. |
| `heptaBasicAgent`, `heptaAgentManager`, `heptaBasicStrategy`, CTA/Kindle strategy bases | Completed-bar forecast contract, an example strategy and a NativeToolClient adapter with a separate relocatable developer package and private durable request recovery. Historical direct-order APIs and all user strategy implementations are not source-compatible or certified migrated. |
| `heptaFtdMdSpi`, `heptaFtdTradeSpi`, `heptaBasicMdSpi`, `heptaBasicTradeSpi`, `heptaBasicSimulator`, QDP and `Interface/` SDK trees | Retained at the reference commit only. CTP remains deferred; XT is still the selected next venue. No vendor library, broker transport or new production mutation capability is added. |
| `heptaMarketTime`, `heptaProductTradeTimeMgr`, base `heptaCalendar` | Do not port their implicit local-clock wrap/current-session cache. Canonical research callers use explicit UTC timestamps and immutable `SessionSchedule`; the selected Gregorian validation remains in Data. Historical exchange-rule tables stay reference data, not current calendar truth. |
| `heptaOrderReference` | Do not port the process-local numeric order-reference generator. Canonical mutation identity is the durable command/request identity issued and reconciled through `NativeStrategyClient` / Gateway / Execution; a PID-derived counter would create an unsafe second identity dialect. |
| `heptaMutex`, critical-section wrappers, `heptaShareObjPool`, `heptaCommonUtility`, cout/log/version helpers | Historical implementation infrastructure, not a portable trading capability. Existing platform/runtime primitives remain authoritative; no compatibility wrapper is added merely to reproduce file coverage. |
| Old local position/order maps, threading/process control, XML composition, logging and build overlay | Not restored as a parallel runtime. Existing Execution Service, journal, identity/recovery and Gateway lifecycle continue to own production behavior. |

The new public interfaces are deliberately distinct from `heptaBasicStrategy`
and `heptaBasicTradeSpi`. Consumers must migrate deliberately; linking a retired
application against this SDK is not a supported shortcut. No deprecated target
is replaced with an empty success-shaped library.

## Consumer and retirement boundary

The visible organization search for `heptaBasicStrategy` found the reference
library and the target repository's historical-retirement documentation. A follow-up
search for `heptaHeptaDLL` also returned the reference project's build files and
usage guide. Neither search enumerates private installations, other organizations,
binary consumers, all non-default branches or all external applications. The
reference README names the upstream Pegasus/HeptaTrader lineage; it is not evidence
that all consumers are retired.

Accordingly, the source repository is **not archived**, its release entry points
are not deleted and no history is rewritten. Archive readiness requires a named
consumer inventory, explicit disposition of remaining historical APIs/strategies,
cache and matching modes, applicable redistribution confirmation, and exact-head
build/behavior/recovery/permission evidence. A source copy or green offline test
alone does not satisfy those conditions.

## Replay clock and terminal lifecycle

`ReplayMatcher` has one monotonic clock shared by new submissions, ticks and
watermarks. An exact retry does not rewind that clock or resurrect an order.
`AdvanceWatermark(timestampUs)` expires orders without inventing a market tick,
including during a session break. DAY orders expire at the final close of the
explicit trading day, or their earlier explicit TTL; a midday break is not a
trading-day boundary.

`Finish(timestampUs)` expires due orders and cancels all other resting
remainders. Repeating the same finish is idempotent; changing its timestamp or
sending new ticks/orders afterward is rejected. Finalization does not invent
liquidity, a closing price, a final bar, or a flat account. The CLI reports
`finalized`, `active_orders` and `eof_terminal_events` and leaves positions marked
at the last observed price. This is research state only, never a broker event.

The lifecycle tests cover no-tick expiry, session breaks, clock reversal,
terminal retries, fee-overflow rollback and 528 small-book conservation cases
across both sides and all three supported time-in-force modes. The CLI test
also truncates input immediately after a signal and requires terminal treatment
of the unfilled order without a synthetic fill.

## Data-query and conversion continuation

This continuation starts from `17e6fccc42d19d3988e4c4a80d2c2257b82b605c`,
retaining the existing C++ modules, replay lifecycle fixes and relocatable SDK.
No second research framework or production state authority is added.

The additional range-query families correspond to capability categories in the
reviewed `heptaKindleStickSeries.h`, but have explicit typed fields, strict tie
rules and checked indices. Peak/trough output separates pivot time from the time
its final right-hand observation closes. The caller supplies its observed range;
a signal cannot honestly be backdated to a pivot because an offline dataset now
contains later bars. Replacement and retention are visible data changes, not a
reconstruction of historical information arrival.

Completed-bar CSV has a strict single-instrument schema and rejects partial bars,
OHLC inconsistencies, overlap, time/day reversal, invalid counts, non-finite
numbers and malformed/oversized rows. Export validates the dataset before writing.
No binary cache, historical market data or local-time calendar is guessed.

The existing market-data executable now compares all OHLC fields, tie policies,
latest threshold matches and causal extrema against independent oracles. Its
24 synthetic 17-bar datasets exercise 29,376 range/tie cases and 146,880
peak/trough cases; full-storage queries must agree with independently built
observed prefixes. Existing bar/session/cumulative tests remain in the executable.
Boundary tests include overflow, plateau/radius behavior, mutation/retention,
locale-independent CSV, finite-double round trips and stream failures.

The existing installed-consumer acceptance also calls the new public symbols
and completed-bar codec through the exported SDK. It now requests CXX_STANDARD
11 explicitly rather than only a minimum language feature, and retains its
relocation, transitive linkage, replay CLI and negative-component/version checks.

## Separate forward-only strategy-client package

The continuation from `19dac90912a3c59fbc59ab6e39bd7691a994c6eb` closes the
source-tree-only delivery boundary of the native strategy client. The new
`HeptaStrategyClient` developer package exports the existing client, native
transport and typed protocol archives; it does not create another implementation
or link the offline research computation into a privileged runtime. Its install
component is explicit and excluded from ordinary production installation.
The four-library offline SDK remains independently installable and client-free.
See [client package](../../research/CLIENT_PACKAGE.md).

Existing request/result and wire-value declarations are moved without changing
fields or defaults into two public headers. Public client headers no longer
transitively declare the host, registry, session binding or execution authority.
Private host code keeps those dependencies; supported wire requests, preview
permits, command identities, risk policy and recovery behavior are unchanged.
The optional package has exactly eight headers and three archives, and discovers
its platform thread dependency through CMake. It carries source/build metadata,
not broker credentials or trading permission.

The new root `core;research;install` CTest actually installs and relocates this
component, removes its old prefix, and compiles the existing native-client
behavioral test unchanged as an external C++11 consumer. It checks the installed
include/link/symbol boundary, default-install exclusion and rejected privileged
classes/components. The existing full Gateway/Execution and SIGKILL tests remain
separate acceptance of the same canonical client implementation. No compiled
target, translation unit, vendor capability or alternate order path is added.
CI stages this developer archive only after exact-head research acceptance and
fresh build ownership, with its checksum and source commit; upload is not a
release or a completed remote acceptance claim.

## Client request durability continuation

The continuation from `40f15c68cf6e1b172ffffc621c55122bf8e5a7cc` adds the missing
on-disk restart path **inside** the existing NativeStrategyClient SDK. It does
not import the alternate branch's `ResearchIntentClient` or introduce another
client target, transport, OMS or research framework. The alternate public
branches remain untouched; this is not a claim that their differing CSV/BIN/XML
profiles or all historical strategy interfaces were mechanically combined.

The three prepared mutation types now have immutable, checksummed, credential-
bound persistence and exact-ID load/submission. File data and directory entries
are synced; atomic no-replace publication avoids both overwrite and a stranded
hard-link interval. A new NativeToolClient bound-call path snapshots the current
credential once for discovery and forwarding, without persisting its bytes.
The existing native SHA-256 implementation is shared rather than duplicated.
See [client durability contract](../../research/CLIENT_PACKAGE.md) for permission,
filesystem, token-rotation, crash and retained-record limits.

The existing client behavioral executable and its actual installed/relocated
external consumer now exercise persistence, concurrent publishers, unsafe-file
and binding rejection. The actual Gateway/Execution fixture additionally kills
and execs client processes before first send and after accepted placement/cancel,
then checks same-ID replay across client and service restart against the real
OMS send-attempt journal. Existing lost-reply, revoked-session, simulator and
Execution SIGKILL tests are retained. These are synthetic local-service tests,
not a broker campaign, power-loss qualification or full consumer migration.

No build target, translation unit, exported-header path, production installation,
privileged dependency, workflow permission, legacy-runtime flag or broker
capability is added. Full retained-capability equivalence, external-consumer
retirement and source archival conditions above remain separate from this
implemented client recovery boundary. Exact-head validation belongs in the PR
observations, not in an unconditional success declaration in this document.

## Borrowed-input client continuation

The existing native/strategy client captures borrowed input strings before
clearing result, request or diagnostic outputs. Direct operations, persisted
request recovery, bound calls and token-file reads now support input values
borrowed from their prior outputs without erasing command IDs or permits. The
storage schema, exact request bytes, retry policy, permissions and execution
boundary are unchanged. Output-to-output aliases are not supported.

The existing native behavioral test adds alias/non-alias comparisons and
immutable-record checks for all three stored operations. The existing real
Gateway fixture runs both original and borrowed-input variants, preserving
placement, cancellation, flatten, uncertain-result and revoked-session checks.
The same behavioral source is also compiled as the installed/relocated external
C++11 SDK consumer. No target, dependency or additional runtime is introduced;
red/green observations and exact-head acceptance are recorded in the PR.

## Offline settlement continuation

This continuation starts from `808ffd0aa0f9ae241a323a7645fed0daac5fff6c`
and closes the explicit settlement-event gap inside the existing Analytics SDK.
It does not import the alternate native research framework or the old settlement
class, order maps, process lifecycle or SDK dependencies. The source reference's
`heptaSettlement` responsibilities are mapped to a distinct public accounting
contract, not certified historical output/API/ABI equivalence.

The single-instrument ledger and same-currency portfolio now accept bounded,
immutable variation-settlement events. Both cost modes rebase remaining inventory
without changing quantity, fees, external flows or quote freshness. New events
share the existing delivery clock; exact retries preserve later account state.
Validation, capacity, arithmetic and destructive-precision failures are atomic.
The [SDK contract](../../research/PACKAGE.md#explicit-offline-variation-settlement)
defines the accounting and remaining exchange-specific limitations.

The existing Analytics executable adds 4,608 independently checked fill/cash
states with 1,152 settlement events, plus identity, capacity, timestamp, overflow,
precision-loss and stale/missing-quote fixtures. Existing FIFO, portfolio, data,
replay and client tests remain active. The installed/relocated external C++11
consumer calls both exported methods. No build inventory, runtime target,
production install set, required test, workflow permission or capability is
changed. Exact-tree local results and exact-head remote outcomes belong on the
existing PR, not in a hardcoded source success declaration.

The organization default-branch consumer search was repeated for this
continuation. It again identifies reference-library uses and the target's retired
consumer documentation; it does not certify unindexed or external installations.
Consequently the original repository/history/releases and alternate integration
branches remain retained, and CTP remains deferred behind the selected XT work.

## Build ownership

The reviewed inventory includes the twelve research library/executable/test
aggregate targets in both profiles and the existing core aggregate's dependency
on `hepta_research_test_binaries`. No pre-existing target, translation unit,
module owner or SDK boundary was removed. Records are serialized one target per
line; JSON schema and strict fresh-model comparison are unchanged. The inventory
is not runtime registration and does not grant trading or packaging authority.
The data-query continuation changes existing source/header/tests only; it adds
no target, translation unit, public-header path or production package dependency.
A review of source-declared targets is not a substitute for the exact-head fresh
CMake comparison in CI, particularly the separately supplied IB SDK profile.

## Acceptance scope

The public branch integrates real CMake targets and behavioural tests. The root
core test aggregate includes the research binaries, and module/build ownership
must cover their real translation units. Existing strict ownership, source,
recovery, permission, installed-process and qualification checks are not weakened.
Remote CI must check the exact PR head with read-only credentials and retain
logs; queued work is not a passed verification. Integration CTest invocations
reject an empty selected suite rather than treating no executed tests as success.

The current integration additionally requires the separate installed-client
consumer and unchanged default production installation to pass. GCC/Clang and
sanitizer results, exact tested tree identity and observed remote run status are
recorded in PR #107 for each continuation, not hardcoded here as a success file.
A previous head's acceptance does not automatically qualify a later source tree.

Remaining exact historical API/ABI parity, external-consumer disposition,
exchange-specific cache/matching/settlement behavior, production deployment and
optional CTP work stay explicit. This branch is a capability integration
candidate, not a claim that the historical library has been fully replaced or
LIVE trading has been enabled. The original repository and release history
remain retained until actual consumer and retirement conditions are satisfied.


## Typed preview-to-durable-client continuation

The continuation from `dc9463c71ae019ca5f6605aff75a8248de59c652` closes a client
consumption gap: extracting Execution-issued preview command IDs and permits no
longer requires application-side JSON scraping. A typed value and strict decoder
extend the existing `TypedToolProtocol` result codec; two `PreviewAuthorized`
overloads extend the existing NativeStrategyClient. No additional parser,
transport, target, installed-header path, production authority, persistence format
or venue is introduced. See [typed client usage](../../research/CLIENT_PACKAGE.md#typed-preview-approval-without-caller-json-scraping).

The existing installed-client behavior test covers exact 64-bit values, both
preview tools, malformed/truncated/deep/oversized payloads, duplicate/unknown
fields, non-approved responses and borrowed inputs. The existing real
Gateway/Execution fixture compares typed IDs/permits with independently inspected
service payloads and retains its original place/cancel, uncertain/lost reply,
restart, revocation and SIGKILL assertions. Unsupported simulator flatten still
returns no typed approval. These are concrete codec/client behavior checks, not
new broker qualification, a locally issued permit or historical ABI parity.

The source reference, external-consumer and archival limitations above remain.
Exact-head CI outcomes are recorded on PR #107, not inferred from the preceding
commit's completed CI. Existing required checks, production installation,
module/build ownership and read-only workflow permissions are unchanged.


## Native-path timing continuation

Continuation from `7956c596234a3f0e459039aa98822ffa03dc5013` adds reproducible
[phase-level latency observations](../modules/research-sdk.md#reproducible-native-path-latency-observations)
to the existing real-service test and public CI artifact, without a new runtime,
installed interface or weakened risk limit. Independent journal/audit assertions
remain mandatory; observations are not broker, HFT or historical-consumer parity
claims. Source archival and the remaining qualification boundaries above are
unchanged. Exact-head results belong to the PR and its run artifacts.

## Mixed-instrument legacy Tick CSV continuation

Continuation parent: `98d30a37164f1620900fb52689ce91695320c5a9`; full parent
tree: `cfc522560e9169b3982d99645a6f668e4381a1dd`. This work extends the existing
public PR #107 branch, not PR #106/#108 or another parallel framework.

`LegacyTickCsvReader` implements the four reviewed HeptaDLL positional Tick
schemas in the existing Data target. It normalizes explicitly bound mixed
instruments into the same incremental-volume Tick. The caller supplies the
actual civil date and UTC offset for each row; TradingDay is never substituted
for ActionDay. ZS microseconds are preserved. Independent instrument counters,
source-row identities, finite bounds and permanent-failure publication semantics
are explicit. Unselected source fields remain unqualified text, never live quote
or fill authority. See the complete [input contract](../../research/README.md#explicit-legacy-tick-csv-input).

The existing relocated SDK consumer proves this input can drive the maintained
BarBuilder, observation-aware strategy, ReplayMatcher and ResearchPortfolio
without a second bar/matching/ledger implementation. The original positive-price
contract, single-instrument normalized CLI, native strategy-client path and all
Execution/OMS/risk/venue interfaces are unchanged. No target, translation unit,
installed-header path, privileged installation component or workflow permission
is introduced. No original test is removed or relaxed.

This closes these four raw Tick-schema input boundaries on this branch, not all
legacy BIN/XML/database/strategy behavior or source/ABI parity. It does not
merge the separate Python framework merely because it also has format readers.
External-consumer migration, applicable literal-source redistribution and source
archival still require independent evidence. The reference repository remains
private, retained and unchanged; CTP remains deferred behind XT and LIVE remains
unavailable. Exact new-commit local/remote validation is reported on PR #107;
its predecessor's passing CI is not presented as acceptance of the new delta.


## Legacy completed-bar input continuation

Continuation from `a63aa10714886872221f523dc35cb76f19b0d202` extends the existing
Data source/header, its current behavioral executable and its real installed
SDK consumer. It does not create a new target, translation unit, public-header
path, parser framework, simulator, strategy engine or execution authority.

Three explicit profiles cover the reviewed futures 11/13-column start-labelled
one-minute bars and stock seven-column end-labelled three-minute bars. Numeric
futures timestamps are interpreted only in their declared civil-1601
microsecond basis and checked against the civil string. Source cumulative
volume is retained separately; **LastVolume is the futures bar volume**.
Sessions/UTC offsets, completion, tick count and actual observation time are
supplied independently. Missing evidence or OHLC, conflicting clocks, session
crossings, reversed ordering/counters, quota and I/O failures reject atomically
and permanently fail the cursor. No forward-filled price, fake tick count,
completion at EOF or bar-to-fill conversion is introduced. Detailed fields,
evidence responsibilities and limitations are in the
[input contract](../../research/README.md#explicit-legacy-completed-bar-csv-input).

The installed/relocated consumer drives all three profiles through the exported
Data archive and the existing observation-aware MovingAverageForecast, proving
per-bar volume, trading-day binding, delayed signal time, merging and portable
bar round trips. The Data executable retains previous tests and adds profile,
clock, corruption, evidence and failure boundaries plus a 10,000-row lazy
non-seekable oracle. The workload is records inside existing tests, not 10,000
independent test cases. Current-tree local and exact-head remote observations
belong in PR #107; the previous head's green CI does not qualify this delta.

No historical implementation, Wine-derived time conversion, SDK/binary, market
history or private Git ancestry is imported. Original notices stay with their
retained sources; this is not a clean-room or blanket licensing certificate.
Other period/epoch variants, BIN/XML/database/live feeds, exchange queue and
clearing fidelity, historical strategy/API parity and all external consumers
remain unverified rather than declared migrated. Source/archive/release-entry
retirement conditions above still apply. CTP remains deferred behind XT, LIVE
remains unavailable, and Gateway/Execution/OMS/risk code and existing workflow
permissions and required checks are unchanged.


## Shared CSV stream exception boundary

Continuation input: `26f47d11983999dd42fbb4a59fe98c3ff9c37ef6`, tree
`fa268dd4b0a87ff1583a0adb406b3ec840573cda`. The common Data SDK line reader
now distinguishes a caller-requested EOF exception from an actual input failure.
Valid unterminated rows and header-only datasets no longer poison the cursor
under enabled stream exceptions. Actual source faults, corruption, size/quota
limits and permanent failure remain enforced; the caller's exception mask and
stream state are never rewritten.

The existing Data executable covers all eight exception masks, three line-ending
forms, the portable/merged/session APIs and all seven supported legacy layouts,
including actual throwing non-seekable source failures. The existing relocated
C++11 consumer reads exception-enabled input through the exported Data archive
and delivers completed bars to the observation-aware Strategy API. This adds no
runtime, target, source file, dependency, production install, broker path or new
workflow. Exact-tree local results and exact-commit remote results must be
reported separately; the prior head's green CI does not accept this delta.

## Explicit-duration input continuation

Continues public head `bf973c1a653ec197b15a9ff2d25a4782ee87788e` on the existing
PR #107. The previous unpublished EOF-exception correction is reapplied to that
head and independently re-tested; its prior local success is not treated as
acceptance of this candidate. The shared bounded CSV reader distinguishes clean
EOF with exception masks from genuine I/O failure, without clearing stream
state, weakening quotas, skipping records or reviving failed cursors.

The SAME Data SDK adds an explicit positive-duration overload for its selected
legacy bar layouts. The existing overload/symbol and fixed defaults remain.
The existing offline executable exposes it as trailing `--period-us`; all
original invocations remain supported. Actual bars are interpreted, not
resampled: the profile's label, numeric epoch, source quantities, clock evidence,
completion and actual observation time stay authoritative for this research
input. Source fields are retained; no synthetic ticks, fills or completion are
created. Cross-session/daily-with-break profiles and all other cache/epoch modes
are not silently claimed implemented.

The existing behavioral tests add independent duration/clock/forecast cases,
exact default parity, exception-enabled EOF, checked interval arithmetic,
future/extremum/session/overlap rejection, preserved output/counters after
failure, a non-seekable sparse reader and a relocated external C++11 consumer.
The CLI's all-input-before-output rule and failure-on-late-evidence remain.
No test assertion is removed or relaxed. No target, translation unit, installed
header path, production installation, workflow permission, venue capability,
Gateway/Execution/journal/risk implementation or private-source import changes.

Exact-tree local validation and exact-head remote CI observations are recorded
on the PR. Consumer inventory, external strategies, other historical formats,
matching/clearing equivalence and source-archive readiness remain separate,
explicit conditions. The retained source/history and alternate branches are
untouched; CTP remains deferred behind XT and LIVE remains unavailable.

## Legacy file bundle and EOF continuation

The existing Data SDK now receives the selected #106 BIN/XML profiles through
an import-only adapter; it does not acquire a parallel Python bar/ledger/client.
The existing BarBuilder adds explicit incomplete-tail EOF finalization from
#108, and the existing native replay accepts explicit capital, multiplier and
per-unit fees and rejects buffered output failure. Source and installed/relocated
tests exercise the same implementations. See [contract and remaining differences](../../research/LEGACY-BUNDLE.md).
These capabilities do not certify full historical parity, external consumer
retirement, remote exact-head CI, main merge or old-repository archival.

## Consolidation disposition

See [the three-PR capability/consumer register](heptadll-consolidation.md) for the
selected implementation, concrete ports and retained incompatible models.
The missing OMS pre-intent reject/pending-activation status fixes from #108
are retained. Its cost-fixture sleep is not imported: #107 already has an
explicit finite load budget and a separate rate-denial test, both unchanged.
Its control/status-ID filter is NOT retained:
#107 deliberately preserves those identities as non-mutating historical metadata,
and its original rotation/rebase regressions remain unchanged. Native wire
contracts and journal bytes are not changed. Neither this record nor local tests certify remote merge or retirement.
