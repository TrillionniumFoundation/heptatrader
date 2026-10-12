# 2026-10-12 EUR/USD Ridge finite prospective cohort: independent final audit

**Release state: HOLD / price-only research / NO_TRADE.**

## Frozen population and actual terminal receipts

The pocket4 campaign was **pre-registered before its first decision**. Its
four non-overlapping fixed EUR/USD windows were 18:10, 18:30, 18:50 and 19:10
(UTC+08:00), with outcome deadlines 15 minutes later. Neither model weights
nor already-inspected historical validation results were selected in response
to this campaign. The previously viewed four historical folds had all chosen
NO_TRADE.

Original campaign proof, created before any of these four observations:

`~/.local/state/heptatrader/operator-evidence/fx-ridge-finite-campaign-20261012-v1/campaign-sealed.json`

SHA-256: `e5da7854edfe2af4ab00a32ba0b7dc3cf1a41891e1010455de25ddf07c1c28dc`

The finite transient runner completed at **2026-10-12 19:25:06
UTC+08:00** and exited normally. Its `systemd-run --user --collect`
unit automatically disappeared. This is expected; it must not be
restarted or re-registered to backfill an old window.

| Window | Frozen decision | Future price maturity | Price prediction (bps) | Original future midpoint move (bps) | Direction correct |
| --- | --- | --- | ---: | ---: | --- |
| 0 (18:10) | CENSORED: original last quote too old | CENSORED: no valid frozen decision | n/a | n/a | not scored |
| 1 (18:30) | NO_TRADE, frozen before broker quote expiry | MATURE_PRICE_ONLY | -0.0072951198 | -1.8329674849 | yes |
| 2 (18:50) | NO_TRADE, frozen before broker quote expiry | MATURE_PRICE_ONLY | +0.0591214514 | -3.5773215699 | no |
| 3 (19:10) | NO_TRADE, frozen before broker quote expiry | MATURE_PRICE_ONLY | +0.0826023290 | -0.8053114766 | no |

**Intention-to-observe count = 4, matured valid = 3, censored = 1,
directional correct = 1/3.** The censored sample does not enter the 3-row
prediction-error denominator, but remains in the overall cohort count.

The original fixed required absolute forecast edge (full stressed spread,
historically reported two-way commission, hypothetical slippage and
safety reserve) was **10.9133557 bps** for each matured window. None of
the three frozen forecasts approached the gate. NO_TRADE made no new
research order; a research directional prediction is **not** an actual
execution PnL observation.

## Independent verification

In a separate source worktree, `fx_ridge_independent_original_check.py`
was run **again** on each of windows 1/2/3. It independently reproduced
the price-feature math, Ridge weights, causal quotation timestamps,
cost hurdle, maturity quote and error, while verifying the exact
original-source prefix receipts. Three results passed.

Separate read-only receipts, permanently retained on pocket4:

`~/.local/state/heptatrader/operator-evidence/fx-ridge-finite-independent-20261012/window-0[1-3]-independent-original-v1.json`

The new reproducible `fx_ridge_finite_final_audit.py` then required
**all four** planned windows to be terminal, refused any retrofilled
censored outcome, re-hashed the original frozen plan/registration, all
per-window attempt receipts, actual decision and maturity pairings, and
the three independent replays. All passed. Final report:

`~/.local/state/heptatrader/operator-evidence/fx-ridge-finite-independent-20261012/final-independent-cohort-20261012-v1.json`

Report SHA-256:
`e3d91a0aaf7ce56871c4df15a332933ad54fc9b5d9bd0f199f3cd27dea5dd0c7`

An earlier original-file-inventory audit also covered about 180 MB
of preserved local numeric quotes, including first eligible maturity
quote selection, but does not independently attest deleted old files.

### Cohort metrics (price-only, not trading PnL)

- Sum of Ridge absolute forecast errors: **6.3500291920 bps**.
- Sum of zero-change reference absolute errors: **6.2156005314 bps**.
- Ridge MAE: **2.1166763973 bps** across the three matured windows.
- Zero-change reference MAE: **2.0718668438 bps** over the **same three**.
- Directional success: **1/3**. Comparison is descriptive, not a
  confidence interval or statistically reliable alpha measurement.

The observed Ridge MAE **did not beat** the zero-change baseline.
Research abstention, not strategy promotion, is warranted.

The original IB PAPER quote source is locally hashed, **not digitally
signed by IB**. Replaying the same original source and code on
another machine is useful but not an independent broker signature.
Existing Mac/X230 physical readback receipts are not a production
native root/Owner/LedgerWriter/Witness authorization for all four
windows. Actual trade-price slippage, external broker account charges,
financing and personal taxes remain unproven.

## Reproduce final audit without writing original receipts

```sh
cd ~/work/heptatrader-fx-ridge-finite-final-20261012
export PYTHONPATH=research:research/neuron_learning:scripts
python3 -B -m train_v2.fx_ridge_finite_final_audit \
  --root ~/.local/state/heptatrader/operator-evidence/fx-ridge-finite-campaign-20261012-v1 \
  --independent-root ~/.local/state/heptatrader/operator-evidence/fx-ridge-finite-independent-20261012 \
  --output ~/.local/state/heptatrader/operator-evidence/fx-ridge-finite-independent-20261012/another-NONEXISTING-filename.json
```

The output is write-once 0600. No originals are overwritten.
`test_fx_ridge_finite_final_audit.py` includes synthetic
anti-tamper challenges for retrofilling, altered forecast, shifted
maturity, broken seals, false owner/witness, duplicate JSON keys,
missing independent receipts, unsafe permissions and overwrites.

## Next qualified development, without trading effects

1. **Market-source continuity**: diagnose 18:10 original quote freshness
   failures with upstream callback and availability timing. Make a
   separate research proposal for source health; do not widen the
   original historical experiment's TTL retroactively.
2. **Domain shift**: the real inputs are 5-minute *pseudo-closes* from
   original quotes while the model was fitted to historically revised
   IB MIDPOINT OHLC. Establish matched historical-replay and
   original-timestamp prospective feature distribution evidence before
   claiming calibrated forecast magnitude.
3. **Cost / alpha**: test a pre-specified, sufficiently powered
   prospective population against price-prediction baselines, and
   separately obtain real execution-time broker fills/fees to claim a
   trading net-PnL benefit. A four-window cohort with only three
   valid prices cannot qualify efficacy.
4. **Product trust**: original trader action-propensity evidence,
   authoritative product Owner, signed producer/observer and durable
   native LedgerWriter/Witness receipts must all pass *before quote
   expiry*. Neither these local seals nor physical copies imply them.
5. **Account**: complete IB Activity/Flex provenance, reset NAV,
   financing and external flow coverage. Keep account-level net PnL
   unknown while those sources remain absent.

Do not train on or relabel these prospective test outcomes as if they
were new unseen samples after inspecting the results. No historical
window may be retroactively registered. Research runner has ended,
and **automatic training, risk/entry/exit modifications and promotion
were not performed**.
