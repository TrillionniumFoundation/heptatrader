# Pocket4 IB historical incremental recovery & no-promotion decision — 2026-10-16

## Outcome verified on the **Pocket4** host

**Research data recovery succeeded. Model economic release did not qualify.**
This document records reproducible on-host evidence, not an order request or
authorization to alter the current PAPER strategy.

- Existing immutable EUR/USD 5-minute IB historical dataset: 73,169 paired bars
  from 2025-10-16 UTC through 2026-10-13 23:50 UTC; SHA256
  `3988a917554fc7322ae06a38560d4a9c91b5137fceccdc561830ec4327987f12`.
- Existing IB PAPER Gateway recovered connectivity; a bounded MIDPOINT 1D
  request received 33 historical bars and then a `cashhmds` 2106 callback.
  This first probe had **one** historical request and **zero** orders.
- Subsequent *isolated* 4D, 3D and 5D historical research requests obtained
  MIDPOINT, BID, ASK and SCHEDULE callback receipts; all ended with
  `cash_hmds_state=CONNECTED`. Each of the three sessions used exactly four
  broker data requests and **zero** broker orders.
- Important failure preserved: these original `N D` API requests had **not
  proven full requested UTC calendar interval coverage**, because broker
  trade-session schedule returns start much later than naïve UTC subtraction.
  The 4D/3D/5D parent coverage statuses remain **false**. Increasing `N D`
  repeatedly is not a fix. Never overwrite their original receipts or amend
  their coverage report after the fact.
- A **separately declared, immutable derived schedule-contained subwindow**
  `2026-10-13T21:15:00Z ... 2026-10-15T23:59:59Z` is fully supported by
  the raw broker schedule header and session intervals, without inventing
  missing bars. Exactly **602/602** scheduled 5-minute bars are present,
  unpaired=0, crossed=0, missing=0. Of those, **32** are exact previous overlap,
  **570** are new, with zero revision conflicts.
- The existing `historical_training.merge_ib_incremental` accepted this
  independently-proven derived research interval with **no bypass** or
  authority changes, producing **73,739** paired EUR/USD 5-minute bars ending
  2026-10-15 23:50 UTC, coverage complete **for the composite of the proven
  old interval and the newly derived schedule scope**.

## Verifiable Pocket4 data / SHA references

All paths below are local to Pocket4 under
`/home/alex/.local/state/heptatrader/`, not checked into GitHub:

| Artifact | Relative location / SHA256 |
|---|---|
| Single-bar recovery diagnostic | `operator-evidence/pr371-readonly-hmds-probe-20261016-1792155968200757661/probe` |
| Original 5D request (parent interval unproven) | `neuron-learning/historical-offline/eurusd-5m-increment-20261016-5d-v1/` |
| Original 5D paired research set | `neuron-learning/historical-offline/eurusd-5m-increment-20261016-5d-dataset-v1/` |
| Independent derivation script | `operator-evidence/pr371-derived-subwindow-20261016/derive.py`, SHA256 `f31844b256ed68b17c4ac1f9a2606d5a6d5deef2eaa3c01f0c4bb7e776f0f192` |
| Derived 602-bar data | `neuron-learning/historical-offline/eurusd-5m-increment-20261016-proven-subwindow-v1/paired-bars.json`, SHA256 `09debecf7dd56805a130f971596e22a81584d96a3e080d01798f4b0d0b823496` |
| Derived interval proof | `neuron-learning/historical-offline/eurusd-5m-increment-20261016-proven-subwindow-v1/derivation-proof.json`, SHA256 `c713c2a54d3242bb3db6611ccedb7568ccd327b4c12294049ed5fad1005870c2` |
| Merged 73,739-bar data | `neuron-learning/historical-offline/eurusd-5m-joined-1yr-through-20261015-v1/paired-bars.json`, SHA256 `124e5f20af474e238ea8df20f7dbf399bb1986df7135186ba353bf2bb5afbcd4` |
| Exact overlap merge proof | `neuron-learning/historical-offline/eurusd-5m-joined-1yr-through-20261015-v1/merge-proof.json`, SHA256 `8d48f10240391bf9dc8247034f9e734e70350a08c3cecb257ff3566f9a293d66` |

The revised historical prices are not contemporaneous quotes. They cannot
be relabeled as previously sealed production predictions, broker executions,
or real-world live fills. The composite full coverage flag is scoped to the
two proven intervals; it does not retroactively certify the original 5D
API request boundaries.

## Actual training/evaluation results on Pocket4

Research scripts executed on Pocket4 with low-priority CPU, no broker/order
API and model promotion disabled:

1. Existing `historical_training.incremental_holdout_cost` fitted only
   pre-existing historical training/validation data, using the *new 570 bars
   strictly as later evaluation*. Scenario notional $100k; assumed roundtrip
   commission 0.4bps; slippage stress 0/0.5/2.0bps:
   - 15min: 128 nonoverlapping test observations; ridge, momentum,
     mean_revert = **0 trades each** in all scenarios.
   - 30min: 72 test observations; likewise **0 trades**.
   - 60min: 38 test observations; ridge/momentum **0 trades**;
     mean_revert: **7** trades and **-$207.5070** net at 0.5bps slippage
     (7 trades, -$172.5070 at zero slippage; 4 trades, -$3.2156 at 2bps).
     Larger slippage changes admission counts; do not treat the apparent
     improvement as a trade benefit.
   - Result SHA256 `ea1ab6158420fc859aaa1525af34de24f9053cd5205880f8751ce29fefd15205`;
     original Pocket4 file
     `operator-evidence/pr371-new570-incremental-cost-20261016/result.json`.
2. Existing `historical_training.nonlinear_fx_cost_study` ran **12** rolling
   folds over the merged one-year dataset. Neither engineered_linear nor
   nonlinear_ridge at 15/30/60 minutes passed the conservative cost screen.
   All six arms selected **0 trades**, not positive economic return.
   SHA256 `e53e9a0d55f1a611891cde0791306aaf81c5c774d57ba4b83dfb3b091165c187`;
   file `operator-evidence/pr371-merged-nonlinear-cost-20261016/result.json`.

### Qualification

**Do not promote any model** from the above. A zero-trade backtest has no
positive net alpha evidence. The source is retrospectively revised. The
time-rolling and incremental evaluation remain valuable for rejecting false
optimism, not granting production or even PAPER auto-replacement authority.
Existing single-class action-head saturation and original-forward trial
coverage failures remain unresolved; none are repaired by this backfill.

## Tests on Pocket4

- PR #371 read-only audit: **11/11** tests passed on isolated detached
  `2c7fd2335382e160cebb1a2b41a5fb39bb7c9c2b` worktree.
- Historical module tests: `test_history_farm_readiness` 19;
  `test_ib_history_failclosed` 22; `test_incremental_completed_day` 14;
  `test_merge_ib_incremental` 3; `test_incremental_holdout_cost` 1;
  `test_nonlinear_fx_cost_study` 2; `test_cost_horizon_study` 3.
  **64/64 passed** via Python standard-library `unittest`; Pocket4 research
  environment lacked `pytest`, so no package was installed.
- **Total selective real host tests: 75 passed.**
- IB Gateway, PAPER Execution, FX/BTC drivers, and Laya trainer remained
  active after the recovery. No manual production service restart occurred.
- Intermittent IB transport reconnection may yield a **temporarily null**
  strategy-PnL valuation. Keep null as UNKNOWN (never zero). The PR audit
  tool currently rejects such a snapshot with `INVALID_NUMERIC_VALUE`;
  the partial-state formatting improvement is **not completed**. Existing
  live PnL observer resumes valid valuation when its broker reads recover.

## Next engineering work (useful, not ceremonial)

1. Fix the source-history `N D` / trading-schedule semantic mismatch for
   **new** downloads: explicitly distinguish actual broker-returned schedule
   coverage from independently declared *derived* intervals. Never alter
   previously signed/hashed observations. Reuse the existing downloader,
   global client lock, and immutable history join.
2. Keep historical datasets as offline training/research only. Avoid swapping
   the live Laya/PAPER strategy model. Next train objectives should first
   resolve saturated action head / single-class labels on actual diverse
   outcomes, then demonstrate separate unseen original-market forecasts and
   cost-adjusted executed returns.
3. Measure strategy gross versus broker fees per trade class using an
   authorized read-only evidence lane; preserve distinction between account
   NAV (currently not reconciled) and strategy-period PnL.
4. Broker connectivity may drop while services still show active:
   use actual connected/read-authoritative state and real account/position
   reconciliation, not systemd active alone. Any interruption requires
   retaining unvalued/null PnL and no unsafe new order admission.
