# New future original EUR/USD quote and cost study — 2026-10-12

**Status:** prospective research **without a trading model**, no orders,
no automatic training, no model promotion; new source data remain unverified
as of registration. This is NOT permission to use a 120-minute native
Train V2 FeatureSpec or change the existing EUR/USD/BTC PAPER strategy.

## Motivation, evidence already seen

The previous 15/30/60/120-minute original IB PAPER *retrospective* study
(HeptaTrader PR #234) reported higher absolute price excursions at longer
horizons but very limited independent price observations, actual commission
reconciliation and source availability. Four horizons were inspected and
**cannot now be called new, unseen historical model validation**.

Original 15-minute Ridge had NO_TRADE in all four validation folds and
direction agreement 1/3 in the frozen future price study; the cost hurdle
was 10.9133557 bps under stress assumptions. The longer horizon does not
automatically improve forecast direction or net profit.

The existing native Train V2 `FeatureSpec` validator caps horizon at
**3,600,000 ms (60 minutes)**. This new 120-minute work runs exclusively as
an isolated *price/cost observation*, **not native Train V2 admission**.

At preparation time original source readiness was occasionally
`HOLD_NEW_FUSION_WINDOW`, with as few as **16/24** usable probes. A new
fixed entry requires its *actual* contemporaneous monitoring sample to be
`READY_FOR_REVIEW_NOT_AUTHORIZATION`, at least 20 usable of 24,
at most four missing, and observed within 30 seconds. HOLD, stale or
partially complete data **must produce permanent CENSORED**, not a changed
deadline or a reconstructed original quote. The source monitor itself
does **not** confer trade authority even on a READY result.

## Frozen future calendar

- Exactly four new, genuinely future 5-minute UTC grid entry anchors.
- All four anchors and all horizons **15, 30, 60, 120 min**
  recorded in the first irreversible plan and seal before any observation.
- 195-minute anchor spacing, exceeding the 60-minute original 13-close
  lookback + 120-minute outcome + 5-minute purge. No two windows' input
  and complete 120-minute label intervals overlap.
- Only entry from anchor **+1.0 to +4.5 seconds** is eligible.
  All 13 pre-grid 5-minute pseudo-closes must be original IB PAPER
  locally chained quotes, strictly available by the original grid
  timestamp, unique, and the last quote age <=4.5s and original
  IB-reported expiry *after* the physically durable preaction seal.
- Each horizon outcome attempted **once** at its frozen due
  **+6.5 seconds**, with max attempt lateness +15 seconds.
  Original first quote event must be within +0..5.0 seconds and locally
  available then. A quote arriving too late can never be replaced by
  a later convenient result. If entry invalid, every dependent outcome
  remains permanently censored; no backfill.
- The canonical action is always `NO_TRADE`. There is **no prospectively
  fitted predictive model** in this population. It observes price/cost
  availability and potential future trade-related evidence needs, not
  a winning strategy, alpha, or an execution reward.

Versioned precommit pins: original PnL PAPER period, authorized read-only
feed directory and config bytes, the already validated original
source decoder's 15m `FeatureSpec`, current frozen IB commission/spread
cost receipt, source readiness path, and the **exact runner source SHA**.
The runner refuses changed pinned bytes on startup.

The original quote chain remains **locally hashed, not digitally signed
by IB**. This local registry and file-clock are not a canonical native
Root/Owner/Witness signature or durable LedgerWriter ACK.

## Price-only economic measurements

An accepted entry records exact original predecision bid/ask, 13 quote
receipt IDs, frozen causal 8D features, original quote expiry and
local source-prefix SHA. Independently, each matured future observation
records:
- Original *midpoint* future/entry price return.
- **Hypothetical** long entry ask -> exit bid and short entry bid ->
  exit ask moves: explicit quote-side crossing, spread counted **once**.
- Predeclared conservative commission/spread/slippage/margin comparison
  from the earlier immutable PAPER callback-based cost receipt.
- Explicit absence of actual broker fills, time-stamped execution slippage,
  financing/carry, drawdown-path proof, margin costs, account total PnL
  and positive strategy forecast.

These quote-side prices may not have been simultaneously executable at
a user order's fill time. Do not count them as actual bid/ask trade PnL.
No prediction on this campaign may be used to change the freeze.

## Hard tests and execution confinement

The standalone one-shot runner is:
`research/train_v2/fx_original_forward_price.py`.

Original read-only decoding uses the installed frozen source scope and
`newest_source_chain`, checks all original SHA-chain prefixes at each
maturity, and never connects to TWS/IB Gateway.

Adversarial unit tests cover:
- Four fixed nonoverlapping anchors and 120m outside the product cap.
- HOLD/unavailable/stale source monitoring, future quote, expired
  preaction and timing gates.
- O_EXCL 0600 private files, no second attempt or file overwrite,
  original input SHA tampering and first future quote lateness.
- Distinct quote-side vs midpoint returns with explicitly no broker fill.
- No native training, action, execution client or model promotion.

A single transient per-user process can be launched with AF_UNIX-only
sockets, NoNewPrivileges, strict CPU/memory bounds, no auto-restart
and finite runtime. It ends naturally after the final frozen 120-minute
maturity; no new HeptaTrader default service or timer is installed.
The user can stop that one transient unit without affecting the
original PAPER drivers, but any incomplete windows remain unqualified.

## Production gates

**HOLD** until independent signed source custody, native Owner/Root,
full original behavior propensities, LedgerWriter/Witness admission,
actual quote-aligned brokerage execution/funding/Financing costs,
account NAV/Flex/Activity reconciliation and statistically credible
independent OOS efficacy all pass.

The old four-window CENSORED sample, rejected trading performance,
Ridge weights, prior cost estimates, and current live PAPER strategy
are unchanged.
