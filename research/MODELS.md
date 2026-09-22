# Explicit offline replay models on the canonical SDK

Status: EXPERIMENTAL; source-level APIs, not a legacy ABI replacement
Implementation: existing `src/replay.cpp` and `src/analytics.cpp`
Public headers: existing `replay.h` and `analytics.h`
Tests: existing replay executable and installed/relocated external C++11 consumer,
including `tests/research/replay_model_cases.h`

## One accounting implementation, distinct market assumptions

All models use the existing `ResearchLedger` / `ResearchPortfolio` accounting.
No Python cash ledger, alternative OMS, client transport, credential store,
Execution dependency, vendor SDK, new compiled target or production installation
is introduced. These are single-writer, bounded, in-memory research objects.
Research holdings and fills are never authoritative live state.

| API | Model identity | Liquidity and eligibility |
|---|---|---|
| `ReplayMatcher` (original constructor) | `offline-last-trade-liquidity-v1` | Incremental Tick volume, shared in submission order, strictly later timestamps, unchanged positive-price/no-slippage default |
| `ReplayMatcher` with `ReplayExecutionPolicy` | `offline-last-trade-grid-slippage-v1` | Same volume/lifecycle, explicitly configured adverse integer-grid slippage; limits checked against slipped price |
| `OrderFlowReplay` | `explicit-price-time-flow-v1` | Explicit order arrivals/cancellations; best price then arrival sequence; resting maker price; no inferred external replenishment |
| `NextBarReplay` | `observed-next-distinct-open-v1` | Explicit completed-bar target observation and a strictly later observed open; hypothetical target-delta fills, not a volume/queue model |

Choosing another model changes an assumption; it is not an optimization that
must secretly preserve all other models' output. None reconstructs historical
exchange queues from depth snapshots, supplies a margin engine, certifies a
strategy, or enables PAPER/LIVE trading. No automatic end-of-file liquidation,
funding, currency conversion or exchange calendar is invented.

## Prices and units

All event times are nonnegative UTC microseconds. Instrument identity, currency,
multiplier, cost convention, lot size, tick grid and fees are constructor inputs.
One portfolio has one initial capital and one explicit accounting currency.

`ResearchPriceDomain::Positive` is the original default. Explicitly selecting
`SignedFinite` in a research instrument or ledger accepts finite zero and negative
fills, marks and basis prices. This does not relax the Data SDK's positive-price
CSV, legacy BIN/XML, BarBuilder or strategy forecast contracts. It also does not
promote any production quote or venue capability. Callers must reconstruct and
rebuild against this exact SDK; old binary consumers are not certified compatible.

`ResearchPriceGrid::Price` converts integer indices with absolute value at most
2^40. It rejects overflow, nonzero underflow, or adjacent indices that collapse
to the same binary64 representation. `Index` uses the explicitly bounded nearest
rule `abs(price/tickSize - round(price/tickSize)) <=
8*epsilon(double)*max(1,abs(price/tickSize))`; this is not arbitrary epsilon repair
of a legacy Decimal stream and is not exact signed-64-bit/Decimal equivalence.
The positive domain additionally requires a strictly positive index/price.

Slippage is an integer in [0, 1000000]. A buy uses market index plus slippage;
a sell uses market index minus slippage. Last-trade orders must still cross their
limit at that slipped index. A positive-domain last-trade order whose slipped
index is nonpositive does not cross. Numeric/grid failures do not consume an
order, tick, target, fee or event identity. Zero tick size is permitted only for
the original ungridded last-trade policy with zero slippage.

Flow quantities and target positions are integral lot multiples, bounded by
10^12. Fee per fill is `quantity * (feePerUnit + abs(price)*multiplier*feeRate)`;
fees are finite/nonnegative, and feeRate is in [0,1]. Negative notional never
creates a negative fee. These are research cost inputs, not exchange tariffs.

## Explicit order-flow lifecycle

Construct `OrderFlowReplay` with `FlowInstrument` entries. Submit a `FlowEvent`
through `Consume`; it returns only research-account fills. `Order(id)` returns
quantity disposition for an individual order, and `ActiveOrders()` counts both
research and external resting orders.

New events have a positive globally increasing sequence and nondecreasing time,
including equal-time arrivals whose sequence determines priority. Exact historical
sequence retries return no new fills and do not rewind the clock. Changed fields
under an existing sequence fail. Identities are bounded ASCII letters, digits,
underscore, hyphen, period or colon; order/target IDs have a 96-character bound.
Do not truncate longer IDs or silently rewrite a source sequence starting at zero.

`Add` supports GTC, DAY, IOC (including an explicitly mapped FAK) and FOK. Only
IOC/FOK may omit a limit (`hasLimit=false`, `priceTicks=0`); they never rest.
Best-price makers execute before inferior prices, and equal-price orders use
original arrival sequence. Partial cancellations retain that priority. Explicit
external orders represent queue-ahead volume. External/external matches consume
that volume but never create research fills, position or fees.

Research/research crossing cancels the aggressor remainder. For ordinary orders,
prior external matches in the same walk remain valid. FOK preflights the entire
walk, including the self-trade boundary, before any fee/account change; inadequate
liquidity cancels the whole incoming quantity without consuming maker volume.

`Cancel` must match instrument, actor and order ID. Quantity zero means all
remaining; over-cancellation and off-lot quantities fail. Cancelling a terminal
order with quantity zero is inert. `SessionEnd` expires DAY orders for the named
instrument but preserves GTC. It is an explicit caller event, not an inferred
calendar or proof of exchange closure. No EOF operation fabricates fills or flat
inventory. Resting orders and positions remain inspectable at the end of input.

`Mark` supplies explicit observed valuation evidence. A position-changing fill
invalidates the canonical portfolio's prior mark, so callers must submit a new
mark before valuing an open position. `Snapshot(asOfUs,maxMarkAgeUs)` rejects a
past query, missing mark or stale mark; unlike #106's report it does not return a
partly-null valuation. No fill price is silently substituted for a market mark.

`BasisRebase` uses the existing offline settlement arithmetic to transfer P&L
attribution to the explicit basis while preserving quantity, fees, external cash
flows and marked equity. This named flow event also supplies its explicit mark.
It is not a cash deposit, cash variation-margin transfer or an exchange settlement
certificate. Direct `ResearchPortfolio::Settle` still does not refresh marks.

## Next-distinct-open lifecycle

`NextBarTarget` supplies a completed `sourceBar`, a unique `targetId`, desired
signed quantity and `observedAtUs >= sourceBar.endUs`. All OHLC values must be
consistent and on the declared grid. An incomplete bar or observation backdated
before its end fails. The actual observation time cannot be inferred from EOF.

`SetTarget` replaces only the pending target for that instrument. An exact retry
of an old target ID never replaces a newer pending target; conflicting reuse
fails. Multiple instruments share one global delivery clock and one capital.
`ObserveOpen` takes an explicit Tick-shaped open observation. It is not derived
from the close of a future completed bar. Each instrument's open sequence and
timestamp must increase; exact last-open retries are inert.

An open at the target observation timestamp is ineligible. The first strictly
later open consumes its target. A sign reversal produces an explicit closing
fill followed by an opening fill; unchanged exposure creates no order. The
configured adverse slippage and fees apply to both legs. The open's volume is
retained for identity only; this model does not promise volume-constrained fills.

The supplied open is also valuation evidence after hypothetical fills. A target
observation alone does not refresh a position's mark. Stale other-instrument marks
therefore still prevent a complete Snapshot. This is a deliberate canonical
boundary, not identical to every CLOSE-marking/report convention in #106.

## Bounds, failure and migration limits

Both new models accept 1..1024 fixed instruments and an explicit event bound in
[1,1000000]. The same bound also limits the shared portfolio's fill/settlement
identities; a many-match input can reach that budget before exhausting external
events. Exact retries do not consume capacity. Rejected events leave prior
state and identities intact. Whole-event staging copies bounded retained state;
this is correctness-first offline replay, not a latency or scalability claim.
No persistence, resumable cross-version schema, concurrent-writer guarantee,
streaming report format or old client-outbox conversion is asserted.

Capability provenance is #106's explicit order flow/next-bar concepts and #108's
explicit slippage convention. Their source branches remain references, not linked
alternative runtimes. The current migration supplies new native SDK APIs and
exercises them through installed consumers. It does NOT replace #106's full
JSON manifest/JSONL commands, Decimal magnitude domain, all null-valued reports,
or source-compatible Python APIs. It does not turn #108's ResearchIntentClient
records into NativeStrategyClient records. Those callers must be explicitly
adapted or remain on their pinned reference versions; never relabel them migrated
because a native model test passes.

The original positive-only data formats, unknown external/private/binary users,
platform ABI validation, historical proprietary strategies and redistribution
scope remain separate decisions. Keep the original repository/history/releases
and alternate source branches until those decisions and consumer checks exist.
See `docs/technical/heptadll-consolidation.md` for the retained consumer register.
