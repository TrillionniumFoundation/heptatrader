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
| `heptaKindleStick`, `heptaKindleStickSeries` | `research_data`: bar building, bounded series, replacement, range queries and OHLCV merging under an explicit new contract. Additional historical indicator APIs are not claimed compatible. |
| `heptaDate`, `heptaTimeStamp`, `heptaProductTradeTime`, `heptaChinaTradingCalendar` | Explicit UTC session windows and Gregorian trading-day validation. Old exchange-rule tables and implicit local-time assumptions are not republished as current rules. |
| CSV/data helpers | Strict portable tick/session CSV conversion. Proprietary binary cache layouts and historical datasets remain in the source repository. |
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
library and the target repository's historical-retirement documentation. That
search does not enumerate private installations, other organizations, binary
consumers or all external applications. The reference README names the upstream
Pegasus/HeptaTrader lineage; it is not evidence that all consumers are retired.

Accordingly, the source repository is **not archived**, its release entry points
are not deleted and no history is rewritten. Archive readiness requires a named
consumer inventory, explicit disposition of unmigrated indicators/strategies/
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

## Build ownership

The reviewed inventory includes the twelve research library/executable/test
aggregate targets in both profiles and the existing core aggregate's dependency
on `hepta_research_test_binaries`. No pre-existing target, translation unit,
module owner or SDK boundary was removed. Records are serialized one target per
line; JSON schema and strict fresh-model comparison are unchanged. The inventory
is not runtime registration and does not grant trading or packaging authority.
A review of source-declared targets is not a substitute for the exact-head fresh
CMake comparison in CI, particularly the separately supplied IB SDK profile.

## Acceptance scope

The public branch integrates real CMake targets and behavioural tests. The root
core test aggregate must include the research binaries, and module/build ownership
must cover their real translation units. Existing strict ownership, source,
recovery, permission, installed-process and qualification checks are not weakened.
Remote CI must check the exact PR head with read-only credentials and retain
logs; queued work is not a passed verification.

Local pure-SDK verification covers GCC Release, Clang Release, and Clang
AddressSanitizer plus UndefinedBehaviorSanitizer, five CTest entries each,
including the executable's numeric/error-path and EOF checks. Those checks do
not compile or qualify the native Gateway integration in isolation; that is a
separate root-build test boundary. Detailed observed results and any remaining
CI failures belong in the PR, not in a new approval authority or a hardcoded
success file.

Remaining full-parity, external-consumer, production packaging and optional CTP
work stays explicit. This branch is a capability integration candidate, not a
claim that the historical library has been fully replaced or LIVE trading has
been enabled.
