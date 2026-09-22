# HeptaDLL modular integration disposition

Status: CURRENT
Applies to: integration branch; not a production authorization

## Pinned inputs and publication scope

Destination baseline: `TrillionniumFoundation/heptatrader` at
`5615b3ddb6badb1967771724d53b89c7ad194ddc`.
Donor baseline: `TrillionniumFoundation/HeptaDLL-main` at
`5f3703258bc4cad8f96e513d8d989c2441b4729d`.
The repository owner explicitly requested a public heptatrader integration
branch and remote CI on 2026-09-22. This records publication instructions, not
an independent certification of all third-party redistribution rights.

Only the bounded, newly refactored research/intent boundary is published here.
The donor repository's history, vendor SDKs/binaries, credentials, market sample
archives, Word manual and old runtime are NOT imported. Original author notices
are retained where interfaces/formulae informed the adaptation. No new blanket
license is asserted over the donor or vendors. The public implementation is a
semantic refactoring with the differences below, not an unchanged library dump.

## Inspected donor surfaces and disposition

| Donor family | Destination / decision |
|---|---|
| heptaKindleStick, heptaKindleStickSeries | `strategies/native_research/market.*`: initialized values, aggregation and inclusive reverse-index extrema/mean. Not every legacy series query or binary layout. |
| heptaDataFileHelper, csvparser, date/time/product-calendar usage | Strict normalized TickCsvReader and explicit SessionCalendar. Raw binary/cache layouts, XML configurations and dated exchange tables are retained only at donor baseline. |
| heptaPegasusSimulator, heptaSimMdSpi, heptaSimTradeSpi | Decomposed offline ReplayMatcher; not restoration of the Pegasus runtime. Last-trade volume-budget semantics are explicit new assumptions, not demonstrated legacy parity. |
| heptaSettlement, heptaNetValueEvaluation | Offline futures-style ReplayLedger and unitized NAV/drawdown. No implicit funding, hardcoded 16:00 roll or undefined risk ratios. Full historical accounting formats/fees are not automatically converted. |
| heptaBasicStrategy, heptaBasicCTAStrategy, heptaBasicKindleStrategy, heptaBasicAgent, heptaAgentManager | Completed-bar example signal plus maintained NativeToolClient forwarding. No source/binary compatibility facade and no claim all historical strategies are ported. |
| heptaFtdMdSpi, heptaFtdTradeSpi, heptaQdpMdSpi | Preserved in donor; CTP remains deferred/no-transport in destination. XT/QMT priority and all qualification requirements are unchanged. |
| Old account/position/order maps, order reference, cancel-risk macros, thread/log/exit control | Not reintroduced as execution authority. Existing Execution Service, OMS and deterministic risk remain sole maintained authority. |
| tinyxml, CTP/QDP/oneAPI vendor assets, build projects and binaries | Not copied; independent vendor/consumer authorization and platform qualification are not inferred. |

Inspected GitHub organization default-branch code search for `heptaHeptaDLL`
returned donor build references; destination history documents former consumers.
This is a bounded search, **not proof that external binaries, private forks,
non-default branches or deployed hosts have no remaining consumers**.

## Consumer graph

Normalized research data -> market/series -> strategy proposal
-> StrategyIntentClient -> NativeToolClient -> Tool Gateway
-> existing unique Execution Service -> qualified adapter.

Offline replay/settlement outputs stay research outputs and never overwrite
Execution-owned positions or observations. No new production daemon, direct
broker client, secondary order journal or strategy-owned risk approval is added.

## Validation and economic scope

The ordinary core target runs the new source-level tests alongside the existing
lifecycle, recovery and authority tests. A dedicated SDK-free integration
workflow runs GCC/Clang with ASan/UBSan, real Unix Gateway routing and the
standalone client link boundary. Do not infer broker qualification from these
results. Exact commit/run receipts belong to the PR and Actions, not static
claims in this document. A local pre-publication run passed 98 research checks
with GCC and AddressSanitizer/UndefinedBehaviorSanitizer.

The build inventory's SDK-free profile is regenerated from CMake. The new
unconditional SDK-independent target records and dependencies are mirrored
into the retained IB inventory without claiming an IB SDK build was run.
No existing SDK-specific target record is deleted or replaced. The existing
IB inventory verifier still requires the real pinned SDK/BID archive.

## Remaining decisions and repository retirement

The donor stays unchanged and readable. Archiving it now would be premature:
full old-strategy/format parity, all deployed/external consumer migration, and
third-party SDK redistribution rights have not been established. A complete
retirement requires an actual consumer inventory and conversions with golden
input/output comparisons, followed by deployment/release-owner approval.
Keeping those assets pinned is not a request to re-enable their old runtime.

Current deliverables are an additive modular integration and real validation,
not a declaration that every donor feature has been migrated. Further source
ports must add actual consumers/tests and continue to use this same execution
boundary. No deletion of donor history or modification of destination main is
part of this branch.
