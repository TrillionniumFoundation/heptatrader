# Independent review of finite original FX Ridge quote campaign

**Status:** research-only; no broker API, orders, cancel, model fitting, production
LedgerWriter/Owner admission or promotion. The fixed four-window producer is
unchanged and continues on its original exact pinned commit.

This module is intentionally implemented using Python standard library and
does **not** import the campaign/Train V2 producer. It reads the actual sealed
private campaign, its 0700 subdirectories and 0600 files, actual SQLite WAL
registration and locally hashed original source prefixes. Every review writes
a new 0600 evidence file, never replaces the campaign's original proof.

## What is independently checked

- Four original precommitted anchors at 20-minute spacing, 15-minute
  price-maturity horizon, <4.5-second decision band and immutable cost/model
  source hashes. No reused plan identity, later registration, late code switch
  or widening of the original quote expiry.
- All four model, cost, feature, plan and registry IDs independently parsed
  and hashed; SQLite registration and the separately sealed JSON must be the
  same bytes and timestamps.
- If an original decision has really been durably frozen: reconstruct the
  13 causal quote pseudo-closes, 8-feature vector, Ridge50 normalization,
  clipped linear prediction and original stressed fee/slippage hurdle using
  independent Python math. Confirm every quote was available by its own
  frozen bucket, and hash the original source prefixes without editing them.
- If a future research label is mature: require predecision hash binding,
  exact 15-minute horizon and original 5-second outcome eligibility window,
  validate stored forecast/price-error algebra and mature source prefixes.
  This does NOT independently parse the full broker feed to prove that the
  chosen result was the earliest eligible event. A separate original-feed
  parser/cross-host replay remains required for full price-outcome approval.
- Failure, unreadable or unregistered source, missed deadline and missing
  outcome never count as success. Pending != censored != mature, and all
  reward/production/trading claims remain false.
- All checks explicitly fail closed on duplicate/non-finite JSON, modified
  model/plan/registered timestamp, fake SQLite receipt, forged local root
  signature or fabricated success when no actual frozen quote exists.

## Actual original source and evidence

The producer's sealed root, unaffected by this checker:

`~/.local/state/heptatrader/operator-evidence/fx-ridge-finite-campaign-20261012-v1`

The independent checker writes to a separate private evidence root:

`~/.local/state/heptatrader/operator-evidence/fx-ridge-finite-independent-20261012`

Original first decision (18:10 UTC+8) correctly remained
`CENSORED: LAST_ORIGINAL_QUOTE_TOO_OLD`; *no retiming, rollback or
re-run is authorized*. Remaining preregistered windows at 18:30, 18:50,
19:10 are unchanged. The first read-only review checked the frozen four
windows and recognized the initial failure without admitting economic
sample credit.

## Run (one-off review; not an automatic timer)

```sh
cd ~/work/heptatrader-fx-ridge-finite-independent-20261012
export PYTHONPATH=research:research/neuron_learning:scripts
ROOT=$HOME/.local/state/heptatrader/operator-evidence/fx-ridge-finite-campaign-20261012-v1
OUT=$HOME/.local/state/heptatrader/operator-evidence/fx-ridge-finite-independent-20261012
python3 -B -m train_v2.fx_ridge_finite_independent_review \
  --root "$ROOT" --output "$OUT/review-NEW-UNIQUE-NAME.json"

HEPTA_FX_FINITE_FROZEN_ROOT="$ROOT" python3 -B -m unittest \
  discover -s tests/python -p test_fx_ridge_finite_independent_review.py -v
```

**No auto promotion:** even four complete, directionally correct forecasts
would be far below a statistically qualified policy/economic sample.
Historical four-fold selections were all NO_TRADE. This reviewer does not
attest to broker-signed market data, native signed owner or witness,
actual trades or account-wide after-cost net PnL. The resulting product
state remains **HOLD**.

Host-key trust must not be bypassed to make remote checks appear successful.
