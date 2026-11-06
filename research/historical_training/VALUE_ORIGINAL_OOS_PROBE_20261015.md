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

## Exact execution economics: bounded original-behavior attribution

A follow-up receipt was generated from the same 267 source episodes, pinned
consumer snapshot and exact OMS prefix, with no trading side effects:

`~/.local/state/heptatrader/operator-evidence/p4-original-net-oos-tail-20261015-chat-r3/receipt.json`

Receipt SHA256:
`14a26b08136715cc9bf9b39ffd970f1249ea2b2db7caded39c6224b163dea0cd`

The analyzer reconstructs **first original open execution to last original close
execution** timestamps. This is an execution-span proxy, not a guaranteed
continuous position residence measurement. It verifies Decimal-exact
`gross - reported commission = net` for every episode and refuses missing
original execution timestamps or order identities. Pretrade spread uses the
*captured quote before decision*, **not** assumed fill-time executable spread.

| Span | Episodes | Gross USD | Fees USD | Net USD | Net winners |
| --- | ---: | ---: | ---: | ---: | ---: |
| <5 minutes | 46 | -1097.44096 | 184.013043 | -1281.454003 | 0 |
| 5–15 minutes | 111 | -829.22934 | 444.026509 | -1273.255849 | 26 |
| 15–60 minutes | 94 | 1148.08309 | 376.027700 | 772.055390 | 50 |
| 1–4 hours | 12 | 315.13936 | 48.012272 | 267.127088 | 8 |
| >=4 hours | 4 | -505.91114 | 16.000920 | -521.912060 | 3 |
| **Total** | **267** | **-969.35899** | **1068.080444** | **-2037.439434** | **87** |

BUY: 124 episodes, net -852.114593 USD. SELL: 143 episodes, net
-1185.324841 USD. Fourteen episodes had positive gross but nonpositive net.
Worst observed net loss: -650.119327 USD. Worst five account for 22.8952%
of all negative episode losses; worst ten for 29.9334%.

**Interpretation:** Short losses are **post-decision conditioned observations**, not
proof that forced delayed exit would improve outcomes. The <5m all-loss cohort
may include valid protective exits; do not suppress stops or lengthen holding
from this table. Similarly, the pre-decision quote-spread groups cannot be
read as realized bid/ask spread at the time of execution. Before proposing
any new entry/exit rule, join original risk/reason codes and actual broker
fills; evaluate shadow-only and prospectively freeze policy thresholds.

The 267 bound episodes are **not** the full strategy PnL denominator: other
closed or unbound episodes and open exposures may differ. The overall PnL
observer and full account NAV have separate accounting scopes.
