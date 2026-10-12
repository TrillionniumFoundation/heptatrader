# FX original-quote price-only cost ceiling — 2026-10-12

Status: **research-only / NO_TRADE / model training HOLD / promotion HOLD**.

## Question

In the previously frozen 15-minute EUR/USD Ridge design, are observed
original-market midpoint moves even large enough to overcome the **already
declared** spread, commission, hypothetical slippage and safety margin if a
magically informed observer knew the correct future direction?

This is intentionally an **omniscient, retrospective price-only ceiling**,
not evidence that a predictor can know direction, can obtain a broker fill,
or can earn a monetary reward. The old prospective cohort (1/3 correct, 1
censored) is separately retained; no previous prospective outcome is
retrofitted into the model, and no live strategy is changed.

## Immutable inputs, exact-scope checks

All inputs are existing private `0600` files on Pocket4:

- Original quote-only causal dataset:
  `operator-evidence/fx-original-native-dataset-20261012/native-source-price-examples-v3.json`
  SHA-256 `50e780b057d90a26593c8a7b92f19c70e6c156e21959ce4b58f418d2562ede6f`
  (359 price-only samples, no orders/rewards or independent new OOS labels).
- Original quote feature contract `native-source-contract-v3.json`
  SHA-256 `d541df9a605e71d9083074b6643608f88c5ae1cfbfc467e15ff40d7ff10a7d29`.
- Exact frozen Ridge model *file bytes* SHA-256
  `6851be915780af782acabc407e6558ff4d801d13d8e8eff6bed5fb42bc755f83`.
- Original frozen campaign seal SHA-256
  `e5da7854edfe2af4ab00a32ba0b7dc3cf1a41891e1010455de25ddf07c1c28dc`.
- Pre-frozen source drift receipt SHA-256
  `be72d5a5e0b176121e47b3db805837ab6bba8a770e6c5dfbb7796df82e150f0a`.
- Original PERIOD-scoped IB PAPER commission and quote-spread cost receipt
  `ib-live-execution-cost-exact-period-v3.json`;
  the script requires its exact SHA and period to equal those pinned in
  the original Ridge model and checks `actual_slippage_verified=false`.

Every original quote-only row must have a strictly ascending unique 5-minute
anchor, 13 immutable original input IDs, an original source available before
its specified decision and unexpired at decision, and the first mature quote
in the **frozen 15-minute +0..5s** future window, already available before
the original *preseal* cutoff. A bad/missing input refuses the entire run;
no interpolated/missing prices or execution fees are introduced.

The **80-minute non-overlap** cohort is a greedy selection by increasing
anchor time *only*, before reading direction/magnitude labels. It exactly
reproduced the previously sealed cohort count of 29; it does not magically
create 29 independent prospective rewards. Previously defined research-fit
admission requires 1,024 such samples and remains `false`.

## Frozen cost calculation, not an execution-cost oracle

The old model's historical price research used:

```
spread full p99 from original PAPER quote receipts        3.4837759 bps
+ 2 * one-way commission p90 from IB commission callback   1.4295798 bps
+ assumed 2 bps per side slippage                         4.0000000 bps
= assumed roundtrip cost                                  8.9133557 bps
+ separate declared safety reserve                        2.0000000 bps
= frozen entry threshold                                 10.9133557 bps
```

With *perfect hindsight direction only*, price magnitude `abs(midpoint
return)` greater than this threshold indicates that a trade **might** have
had sufficient movement under these *fixed assumed costs*. It cannot
constitute a pretrade forecast, IB executable fill, positive broker account
PnL, or a lower bound on future profit; an actual spread, fee or slippage
could be much worse or better. The historical p90 fee/p99 spread are
not guaranteed bounds on prospective costs.

## Actual result on original quote-only samples

| Metric | All retrospective samples | Label-blind 80m non-overlap |
| --- | ---: | ---: |
| Rows | 359 | 29 |
| Absolute original 15min midpoint movement, median | 1.692326 bps | 1.516889 bps |
| Absolute midpoint move, maximum | 9.952691 bps | 8.453499 bps |
| Hindsight directional movement >8.913356 bps | 1 | 0 |
| Hindsight directional movement >10.913356 bps | **0** | **0** |
| Frozen Ridge prediction magnitude >10.913356 bps | 0 | 0 |
| Largest frozen Ridge prediction magnitude | 0.256280 bps | 0.225825 bps |
| Frozen Ridge price MAE | 2.273950 bps | 2.266585 bps |
| Zero-move reference MAE | 2.271606 bps | 2.257054 bps |

In this **selected and short** development cohort, even a hindsight
perfect-direction signal cannot clear the original economic margin.
The Ridge error is also *slightly worse* than the zero-move baseline.
These are descriptive retrospective statistics, **not evidence that all
future trades at all horizons will fail**.

There is no statistical evidence justifying retraining or deploying the
same 15-minute Ridge; don't lower the predeclared cost threshold merely
to manufacture positive results. Instead separately validate more
economic holding periods/real commissions and same-source features
under a **new future** development/validation plan, with predeclared
cost and sufficient original non-overlap sample count. Longer horizons
increase price excursions but also open-position risk; no new horizon
is validated here.

## Verification and rollback

The portable read-only analyzer is:
`research/train_v2/fx_original_cost_ceiling.py`.
It never imports broker or driver clients and has no permission to trade,
register a new future trial, change a model or rewrite original sources.
The output is write-once (O_EXCL), 0600 under a 0700 private directory.

V2 result receipt:
`~/.local/state/heptatrader/operator-evidence/fx-original-cost-ceiling-20261012/price-only-oracle-cost-v2.json`

SHA-256:
`7a0585ea9ec74c793516484937dc50c382d3e7061355b9cb62d92a69c6699a27`.

New adversarial tests: **18/18**. Existing FX Ridge tests: **57/57**.
Independent per-row numerical parity against the original numpy
Ridge `predict()`: **359/359**, max absolute difference
`8.33e-17` bps.

Original `PAPER` trading, account NAV, Flex/Activity, native
Owner/Witness/LedgerWriter, original four-window failures, source-sparsity
diagnostics and frozen historical test sets remain unchanged. Stop using
this Ridge model for potential product promotion; do not infer from this
research receipt that future candidate features/rewards are available.
