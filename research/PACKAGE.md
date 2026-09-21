# HeptaResearch offline SDK package

This is an experimental developer artifact, not an installed HeptaTrader
trading service, a broker qualification, or a HeptaDLL ABI replacement. It
contains the four portable research libraries and the offline replay example.
The canonical NativeStrategyClient remains a separate root-build boundary;
its header and library are intentionally absent from this package. The separate
HeptaStrategyClient package (documented in the checkout at
`research/CLIENT_PACKAGE.md`) now exports that existing
forward-only boundary without changing this offline package.

## Build and install

From a complete HeptaTrader checkout:

```sh
cmake -S research -B build/research-sdk -DCMAKE_BUILD_TYPE=Release
cmake --build build/research-sdk --parallel 2
ctest --test-dir build/research-sdk --output-on-failure
cmake --install build/research-sdk --prefix "$PWD/stage/research-sdk" --component ResearchSDK
```

Installation is enabled only for the standalone research build. The root
HeptaTrader build and production package manifest are unchanged. GNUInstallDirs
customizations must be relative, without parent traversal. A staged prefix may
be moved before consumption; installed CMake interfaces must not reference the
original source/build/staging directories. Set HEPTA_RESEARCH_INSTALL_SDK=OFF
for a standalone compile-only build.

## Consume from a different project

```cmake
cmake_minimum_required(VERSION 3.16)
project(ResearchConsumer LANGUAGES CXX)
find_package(HeptaResearch CONFIG REQUIRED COMPONENTS Data Analytics Replay Strategy)
add_executable(research_consumer main.cpp)
target_link_libraries(research_consumer PRIVATE HeptaResearch::Replay HeptaResearch::Strategy)
```

Configure the consumer with CMAKE_PREFIX_PATH pointing to the installed prefix.
The exported names are HeptaResearch::Data, ::Analytics, ::Replay and ::Strategy.
Use headers under hepta/research. Replay pulls in Data and Analytics transitively;
Strategy pulls in Data. No vendor SDK, OpenSSL, Gateway, execution journal or
native client is a transitive package dependency. NativeClient or any other
unsupported required component fails configuration rather than silently
providing a stub.

The replay executable takes TICKS.csv SESSIONS.csv INSTRUMENT PERIOD_US FAST SLOW
UNITS [average|fifo]. The optional cost mode defaults to average. Synthetic input examples are installed under share/HeptaResearch/examples
with the default data directory. The research account is not authoritative
broker state, and EOF cancels remainders rather than inventing liquidation.

## Incremental historical input

The Data export also provides `TickCsvReader(std::istream&, maxRows)`. Use it
when a historical stream should not be materialized as a vector:

```cpp
hepta::research::TickCsvReader reader(input, 1000000);
hepta::research::Tick tick;
while (reader.Next(tick)) {
    // Deliver this row to the existing bar/replay pipeline in arrival order.
}
```

The caller owns `input` and must keep it alive; the cursor is non-copyable and
thread-affine. The parser keeps at most one bounded row, not the input history.
The quota limits total rows, not just simultaneous storage. EOF leaves `tick`
unchanged; malformed data, quota exhaustion and I/O errors throw and permanently
fail that cursor, even when the caller clears the underlying stream. Successful
row counts and prior output are unchanged on a rejected row. `ReadTicksCsv` stays
available for callers that explicitly need a complete vector.

The installed CLI uses this streaming path with the existing million-row quota.
Its valid-input numeric output, duplicate handling, execution causality and EOF
order treatment are unchanged. A late error cannot produce a successful summary;
nonzero exit means the invocation's partial stdout must be discarded. This API
does not add a broker feed, recovery store, background thread or trading authority.
The relocated external C++11 consumer calls the installed streaming symbols.

## Version, source identity and compatibility

The repository VERSION is the sole release-label source. CMake's numeric package
version uses its major.minor.patch prefix and accepts only an exact requested
numeric version. A matching label/version is NOT a source or ABI equivalence
proof: inspect sdk-build-info.txt and HeptaResearch_SOURCE_SHA, retain the exact
commit and compiler configuration, and rebuild consumers for the selected SDK.
Source exports without Git are labelled unavailable; a dirty/unverifiable
worktree is explicitly labelled, not silently certified. No broader binary
compatibility or redistribution clearance is inferred from successful builds.

## Acceptance

The standalone CTest suite includes a real installation/relocation acceptance:
it installs into a temporary prefix, moves it, removes the original prefix,
configures/builds/runs an external C++11 consumer against all four exported
libraries, verifies transitive includes, runs the installed replay CLI, and
rejects an unsupported native component and an incompatible package version.
Compiler and sanitizer flags are carried into the consumer, so sanitizer builds
cannot accidentally test a different link closure. Tests do not connect to a
broker or change host services. Failure is not skipped.

The public integration workflow retains successful GCC SDK archives, SHA-256
checksums and exact-source metadata as CI artifacts, not tagged production
releases. A queued workflow or an existing artifact from a different commit is
not acceptance of the current source.

## Finite-price numerical boundaries

Bar-series means and same-direction research entry costs use incremental convex
updates, rather than summing divided prices or price/quantity products. Constant
finite observations must remain constant, including the largest finite double;
a representable mean must not be rejected merely because an intermediate sum
rounded above that bound. The same behavior is exercised through the moving-
average strategy and through the installed, relocated SDK's Data/Analytics APIs.

This does not relax price validation, position capacity, fill identity or true
realized-P&L/fee overflow checks. A rejected fill leaves its identity and account
state uncommitted; exact successful fill retries remain idempotent. These are
research numerical guarantees, not exchange settlement, risk approval or broker
state. The integration PR records the executed configurations and keeps local
standalone acceptance separate from canonical/root and remote qualification.

## FIFO attribution and multi-instrument research accounts

`ResearchLedger(instrument, initialEquity, multiplier, maxFillIds, CostBasis::Fifo)`
selects FIFO cost allocation explicitly; existing source calls retain weighted
average allocation. FIFO uses quantity-compressed lots, including partial closes
and position reversals, rather than allocating one object per contract. Updates
stage active lots before committing: invalid input, capacity and numeric failure
leave lots, quantities, fees, fill identity and the event clock unchanged. Runtime
work is proportional to active lots, not the absolute contract quantity. Both
conventions must agree on total marked equity; their realized/unrealized split
can differ. With multiplier 10, buying one at 100 and one at 120, then selling
one at 130, realizes 300 under FIFO versus 200 under average cost. Marked at 140,
both have total gross P&L of 500. This recovers a reviewed historical settlement
capability; it is not a copy of the old runtime or proof of full settlement parity.

`ResearchPortfolio` aggregates a fixed universe of at most 1,024 instruments with
explicit multipliers and a common, caller-declared three-letter currency. Mixed
currencies are rejected; FX is not inferred. Initial capital is counted once.
`Apply(ResearchFill)` deduplicates fill IDs globally across instruments, while
`ApplyCashFlow(ResearchCashFlow)` uses a separate immutable flow-ID namespace.
A combined capacity bounds fill/flow/settlement receipts, and successful exact retries stay
idempotent after the clock advances or the capacity is reached. Deposits and
withdrawals change capital explicitly, not through trade P&L or an automatic top-up.

`Observe(Tick)` uses the existing normalized incremental tick contract. New fills,
flows, settlements and ticks share a monotonic delivery clock; equal timestamps retain caller
order. Each position-changing fill invalidates that instrument's cached mark.
An open position then needs a subsequent **new-sequence** tick, not an exact retry
of an old observation. `Snapshot(asOfUs, maxMarkAgeUs)` rejects missing/stale marks
or an as-of time before accepted events. It is a read-only current-state valuation,
not a watermark, historical-state query, or bitemporal store. Flat positions need
no quote and carry no invented mark. Negative equity is reportable research output,
not margin approval. Snapshot reports the per-instrument split and the identity
`equity = initialEquity + externalFlows + realizedGross + unrealized - fees`.

The CLI reports `cost_basis`, `realized_gross` and `unrealized` alongside its existing
terminal/equity fields. The mode only changes accounting; tick eligibility,
orders, liquidity and fills are unchanged. Unknown modes fail before output.
The installed SDK consumer exercises FIFO and a two-instrument portfolio after
relocation, including missing/stale valuation rejection, explicit cash flow and
the installed CLI's FIFO option.

This extends existing Analytics source/header/test targets and existing exports;
it creates no new trading runtime, authority, installed-header path or CMake
target. Rebuild consumers: source defaults are preserved, **binary ABI is not**.
Net positions/FIFO cost accounting do not implement hedge-mode long/short books,
exchange-specific clearing calendars, margin, tax lots, broker reconciliation, historical
cache decoding, or CTP close-today/close-yesterday order semantics. The source
reference remains retained until those separately required capabilities and named
external consumers have an explicit migration disposition.

## Explicit offline variation settlement

`ResearchSettlement` supplies an immutable `settlementId`, `instrument`,
`timestampUs` and finite positive `price`. Both `ResearchLedger::Settle` and
`ResearchPortfolio::Settle` return true only for a newly applied event. This is
an explicit caller-delivered accounting operation, not a timer, inferred exchange
close, order, fill, deposit, quote update or broker instruction.

```cpp
hepta::research::ResearchSettlement settlement;
settlement.settlementId = "research-day-001";
settlement.instrument = "TEST.FUT";
settlement.timestampUs = 200;
settlement.price = 105.0;
ledger.Settle(settlement); // ledger is an existing ResearchLedger
```

The operation realizes the marked P&L at that supplied price and rebases the
remaining inventory. FIFO realizes each old compressed lot before combining the
now-equal-cost remainder; weighted-average cost uses its retained internal basis.
Quantity and fees do not change. At the settlement price, unrealized P&L becomes
zero; marked equity at a fixed price is conserved within checked floating-point
arithmetic. A flat account stays flat with zero basis and no invented P&L.
Finite prices are not enough: arithmetic overflow or a rebase that loses nearby
representable entry-price distinctions or existing realized P&L is rejected with
no receipt/account/clock mutation. Precision rejection is explicit, not a silently
rounded success or a new production risk policy.

Settlement IDs are separate from fill IDs (and portfolio cash-flow IDs), but are
immutable within their namespace. Portfolio settlement IDs are global across its
instrument universe. Changed payloads, wrong instruments, reversed new events and
exhausted event budgets fail. An exact successful retry remains a no-op after
newer events or capacity exhaustion, without rebasing later trades. The historical
ledger constructor argument `maxFillIds` now bounds combined fill/settlement
receipts; calls that do not use settlement keep their existing behavior. Portfolio
fills, flows and settlements share its existing `maxEventIds` limit. Equal event
timestamps retain caller delivery order, not a fabricated historical order.

A portfolio settlement preserves the previous quote's value, timestamp and
validity. It neither refreshes a stale mark nor repairs a mark invalidated by a
fill. `Snapshot` still requires the same subsequent new-sequence observations and
age bound as before. External flows remain unchanged. Source calls are additive;
rebuild consumers because class layout/binary ABI equivalence is not promised.
The existing installed/relocated C++11 SDK consumer executes both new methods.
This is not a CTP clearing, margin, tax or brokerage settlement implementation.
