# EUR/USD original IB PAPER price envelope: 15 / 30 / 60 / 120 minutes

**2026-10-12** • isolated original-quote, retrospective price-only research
**Verdict:** HOLD for model fit, trading strategy, production Owner/Witness,
execution-PnL claims, or model promotion.

## Research question and deliberately frozen alternatives

After the first 15-minute four-window prospective Ridge experiment had
concluded with 1/3 correct directions, does the **original IB PAPER quote
population already available before that experiment** contain 30-, 60- or
120-minute midpoint excursions large enough to exceed the previous frozen,
stressed **8.9133557 bps assumed cost** and **10.9133557 bps cost plus
safety margin**?

Fixed candidates in code: **15, 30, 60, 120 minutes**. These are *not*
independently pre-registered future experiments: these quote outcomes have
already happened and this investigation does not create new unseen efficacy
samples. The longer horizon has more price variability and more exposure,
including possible financing, carry, drawdown, margin and gap risk. The
same per-roundtrip cost scenario is a **comparison device only** and must
not be considered a duration-invariant total broker cost.

Do not select a production horizon from this retrospective result.
Any new candidate requires a new prospective protocol with separate
training/development/validation/OOS phases and fully costed broker fills.

## Inputs and source contract

The read-only analyzer is
`research/train_v2/fx_original_multi_horizon_envelope.py`. It reuses
existing, reviewed original-source components:

- Fixed old four-window campaign seal
  SHA256 `e5da7854edfe2af4ab00a32ba0b7dc3cf1a41891e1010455de25ddf07c1c28dc`.
- Original native 13 pseudo-close features and first future quote
  `fx_original_native_dataset.py`, unchanged. No revised historical
  OHLC substitution, forward-looking source, model retraining or new
  hypothetical trade action.
- 242 original preseal, strictly terminated local SHA-chained IB PAPER
  quote segments; **105,436 unique event timestamps**, with exact
  prefix SHA and byte-count verification against `native-source-contract-v3.json`.
  The local capture is **not IB-signed**; file mtimes do not prove
  independent precommit.
- The original source-derived 15-minute labels are re-created **exactly**
  from the same first quote at the original due time, and all 359
  original timestamps and Decimal outcomes must equal the frozen
  `native-source-price-examples-v3.json`. A discrepancy fails the
  entire audit.
- Historical IB `SCHEDULE` callback from the already captured, hashed
  30-day request, identity-bound to
  `ib-session-scope-original-quote-coverage-v1.json`. A full horizon
  is labeled `IB_HISTORICAL_SESSION_FULL_HORIZON_OPEN` *only if*
  the original anchor and due+5s lie within the SAME reported broker
  session. Dates outside that schedule request are **UNKNOWN**, not
  assumed open or closed. This is historical broker metadata,
  neither live authorization nor digitally signed market data.
- Frozen, same-period original IB PAPER commission callback p90
  **0.7147899 bps per side**, original quote full spread p99
  **3.4837759 bps**, hypothetical slippage 2 bps per side,
  plus a separate 2 bps safety margin. These are stress assumptions,
  not ex-ante guaranteed execution prices or confirmed true slippage.

All 13 causal input quote IDs must be unique, available before the
fixed 5-minute decision grid, fresh under the original unrelaxed
4.5-second decision age rule and unexpired under original IB TTL.
The first quote in the originally declared 5-second outcome window
is the only eligible price. Missing, late or post-preseal data yields
CENSORED, never a synthetic quote or zero return.

## Actual research result on pocket4

The shared preaction population is **375** anchors with all 13 complete
original quote inputs (740 complete pseudo-closes, 344 rejected for
missing contiguous history, 21 for original quote age/expiry).
No broker queries or orders were created by this study.

| Original quote-only research metric | 15m | 30m | 60m | 120m |
|---|---:|---:|---:|---:|
| Matured price labels; overlapping | 359 | 352 | 336 | 317 |
| Historical full-horizon IB session OPEN labels | 235 | 230 | 219 | 206 |
| Label-blind, preaction-time nonoverlap selectors | 31 | 26 | 21 | 16 |
| Nonoverlap matured labels | 28 | 25 | 20 | 14 |
| Full overlapping abs price >10.9134bps | 0 | 5 | 18 | 77 |
| Nonoverlap abs price >10.9134bps | 0 | 1 | 0 | 4 |

**Nonoverlap selection is by time and preaction availability only,
before reading each horizon's future label.** It is consequently more
conservative than retroactively picking only matured labels.
Future-label availability still makes the eligible population selective;
even nonoverlap does not prove random sampling, independent trading
returns, alpha or statistical power.

### Paired exactly identical entry timestamps and entry prices

To distinguish genuine horizon length from changing input populations,
a second check requires the *same original entry source and anchor* to
have all four first-matured future labels. At the longest horizon, 185
minutes of input + label purge are applied **before reading outcome**.

| Same-anchor outcome >10.9134bps | 15m | 30m | 60m | 120m |
|---|---:|---:|---:|---:|
| All 283 fully matured common anchors | 0 | 4 | 18 | 67 |
| Longest-horizon purged, 11 matured common anchors | 0 | 0 | 1 | 2 |
| All 175 common anchors within one cached IB session | 0 | 4 | 13 | 42 |

Here the 185-minute purge selected **16 anchors before looking at any
future price**; only 11 subsequently obtained all four qualifying
mature labels. Restricting to a common matured population is itself
a retrospective, outcome-source-availability filter. Prices are
midpoint quote observations rather than executable ask/bid fills.

The comparison establishes **increased observed price movement
opportunity** with longer horizons in these particular recorded
source intervals. It *does not* establish predictability, expected
after-cost profitability, sufficient nonoverlap sample count, or
permission to choose two hours as a trading hold time.

## Bounded verification and failure modes

`test_fx_original_multi_horizon_envelope.py` tests:
- Exact four horizons and sample-purging clock rules.
- Unsorted/duplicate anchors and overlapping outcome prevention.
- Quote at or after the 15/30/60/120m future boundary must be first
  available within 5 seconds; a later quote cannot replace a missing
  or late first quote.
- Historical IB session request interval bounds, same-session
  full-horizon check, unknown outside request rather than "closed".
- Censored outcomes remain visible in the denominator.
- Causal same-entry cross-horizon comparisons and complete-common-label
  censoring for a missing horizon.
- Cost and safety margin remain separate and finite.
- Exact 359 original frozen 15m label parity during actual pocket4 run.

All immutable original dataset/model/source roots remain unmodified.
New output uses write-once `O_EXCL` at 0600 under a 0700 research
directory. The procedure has no broker sockets, order-client imports,
production service changes or model-training behavior.

Private final receipt:
`~/.local/state/heptatrader/operator-evidence/fx-native-multi-horizon-envelope-20261012/retrospective-horizons-v2.json`

## Next **qualified** architecture work

**P0 – Train/live causal feature parity:** native original-source
features are now verifiable, but the eligible 80-minute cohort has only
29 prior development candidates (and 28 after prospective censoring);
the original 1,024 nonoverlap model-fit gate remains HOLD. Do not
treat 359 overlapping labels as independent training samples.

**P1 – Economic endpoint + guard:** after fixing the development design
*without reusing the current labels as an OOS test*, pre-register a new
study comparing an explicit reference no-trade policy against any
longer-horizon strategy, actual time-stamped broker bid/ask executions,
commission, market impact, financing, adverse excursion and drawdown.
Do not weaken the previous frozen 15m threshold or automatically
promote a horizon based on this report.

**P2 – Trusted and financially complete evidence:** original full
decision propensities, production native Root/Owner/LedgerWriter/Witness
and signed acknowledgment remain unavailable. The IB Flex/Activity
statement, reset NAV, external flows, outside costs and taxes remain
account-wide PnL blockers. Price-only research does not fill them.

**Production release:** HOLD. The original PAPER FX/BTC drivers,
historical source bytes, old censored window, prior four-fold negative
validation and automatically disabled model promotion are unchanged.
