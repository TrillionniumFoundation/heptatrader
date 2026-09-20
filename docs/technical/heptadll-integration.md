# HeptaDLL modular integration record

Status: EXPERIMENTAL
Date: 2026-09-20
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
| CSV/data helpers | Strict portable tick/session and completed-bar CSV conversion. Proprietary binary cache layouts and historical datasets remain in the source repository. |
| `heptaNetValueEvaluation`, `heptaSettlement` | Research-only cash-flow-adjusted metrics and multiplier-aware P&L ledger with explicit undefined ratios, fees and fill identity. No full exchange settlement/margin equivalence is asserted. |
| `heptaPegasusSimulator`, `heptaSimMdSpi`, `heptaSimTradeSpi`, `heptaTickTradeManager`, `heptaOrderBook` | Bounded offline replay/matching model and executable consumer. Legacy queue-position, live-feed and binary-cache modes are retained for later explicit evaluation, not silently emulated. The canonical deterministic execution simulator is unchanged. |
| `heptaBasicAgent`, `heptaAgentManager`, `heptaBasicStrategy`, CTA/Kindle strategy bases | Completed-bar forecast contract, an example strategy and a forward-only NativeToolClient adapter. Historical direct-order APIs and all user strategy implementations are not source-compatible or certified migrated. |
| `heptaFtdMdSpi`, `heptaFtdTradeSpi`, QDP and `Interface/` SDK trees | Retained at the reference commit only. CTP remains deferred; XT is still the selected next venue. No vendor library, broker transport or new production mutation capability is added. |
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

For this continuation, the modified data implementation and its expanded existing
test executable were built and run locally with explicit C++11 and warnings as
errors under GCC -O2, Clang -O2, Clang ASan/UBSan with leak checking, and GCC
checked iterators/assertions. All passed. Original retrieved files were checked
against Git blob identities before editing, and published code blobs were checked
against tested local bytes. Python syntax and the exact added Data portion of
the installed consumer were also checked; that Data portion was compiled and run.

These are targeted tests of actual sources, not a complete repository clone,
full four-library SDK installation, canonical native Gateway build, source/build
ownership run, installed service campaign or broker qualification. Earlier full
standalone-SDK results remain historical PR evidence, not results for a new head.
Detailed observed results and current remote status belong in PR #107, not in a
new approval authority or a hardcoded success file.

Remaining exact historical API/ABI parity, external-consumer disposition,
production packaging and optional CTP work stay explicit. This branch is a
capability integration candidate, not a claim that the historical library has
been fully replaced or LIVE trading has been enabled.
