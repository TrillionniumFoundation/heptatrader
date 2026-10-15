# Original PAPER net-value readout: observational holdout (2026-10-15)

This is a **research-only negative control** for the current100k consumer. It
uses the *actual pre-decision* 24-quote snapshots and the corresponding
fee-complete, settled PAPER execution rewards; it never treats revised
historical bars or hypothetical unexecuted actions as broker profit.

## Contract and reproducibility

- Source: original current100k `incremental-v1/dataset-current.json` and
  matching `status.json`, both SHA-256 pinned. The dataset fingerprint,
  synthetic/old25k exclusions and one original producer are checked.
- Execution maturity: verify the immutable SHA-256 OMS journal prefix before
  mapping every reward execution ID to its actual broker execution timestamp.
  If any execution is missing, refuse the experiment rather than pretending a
  reward was available early.
- Features: fixed 16-element vector from 24 ordered, pre-decision BID/MID/ASK
  snapshots, BUY/SELL side and time of day. A quote at/after the decision,
  stale quote, invalid price or input mismatch is rejected.
- Split: chronological 60/20/20, with 15-minute maturity purges for training
  versus validation and validation versus test.
- Fit: CPU Huber ridge; train-only standardization; alpha chosen on validation
  from fixed `1,10,100,1000`; test touched once for reporting.
- Controls: train-median and zero-outcome forecasts. No changes to frozen Laya,
  IB Gateway, PAPER driver, execution, strategy, trade permissions or selectors.
- Dependency: `read_private(prefix_bytes=...)` implemented in PR #326; while
  #326 is unmerged, run against its exact verified source checkout on Pocket4.

## Pocket4 real result

Receipt:
`~/.local/state/heptatrader/operator-evidence/p4-original-net-oos-20261015-chat-r2/receipt.json`

- 267 distinct settled original executions; 159 train, 52 validation, 54
  untouched test samples after time-boundary checks.
- Test MAE (USD): model **44.130661**, train median **42.479684**,
  zero prediction **43.831050**. Model does not beat either baseline.
- Test RMSE (USD): model **60.672938**, train median **59.431931**,
  zero prediction **57.261536**.
- Zero actual broker requests/orders, no candidate promotion.

**Disposition: no model selection or trading change.** This evaluation is
observational on decisions the original strategy actually took. There are no
causal counterfactual outcomes for skipped or alternative actions, and the
same paper-reset data have already been inspected. It does not establish a
new independent prospective economic advantage.
