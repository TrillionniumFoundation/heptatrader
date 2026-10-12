# Pocket4 EUR/USD Ridge finite campaign — final independent research audit

**Date:** 2026-10-12. **Release:** HOLD (price-only, no broker trade or model
promotion). Research worktree `research/fx-ridge-finite-campaign-20261012`.

## Exact experiment and outcome

The one-time transient service
`heptatrader-fx-ridge-finite-20261012.service` exited naturally at 19:25
UTC+8, `Result=success`, `ExecMainStatus=0`, `Restart=no`. Normal exit
only means the four scheduled attempts were recorded; it is **not**
acceptance of statistical efficacy.

Four precommitted anchors (UTC+8 18:10 / 18:30 / 18:50 / 19:10), with
non-overlapping 15-minute price-only outcome windows and a 20-minute
inter-anchor period. Previous historical 4-fold Ridge selection was NO_TRADE
in every fold; model/training data/cost receipts were pinned unchanged
*before* this campaign. No cost thresholds were optimized on outcomes.

| Window | Original decision | Frozen forecast | Original 15m midpoint | Sign |
| --- | --- | ---: | ---: | --- |
| 00 / 18:10 | CENSORED: last original IB quote too old | N/A | N/A | N/A |
| 01 / 18:30 | FROZEN_PRICE_ONLY / NO_TRADE | -0.00729512 bps | -1.83296748 bps | correct |
| 02 / 18:50 | FROZEN_PRICE_ONLY / NO_TRADE | +0.05912145 bps | -3.57732157 bps | wrong |
| 03 / 19:10 | FROZEN_PRICE_ONLY / NO_TRADE | +0.08260233 bps | -0.80531148 bps | wrong |

**3 eligible price-only predictions / 4 prospective registered windows, 1
censored, 1/3 correct directions, mean absolute forecast error
2.1166763973 bps**. All 3 predictions are *strictly below* the same
previously frozen 10.9133557 bps stressed spread + fees + hypothetical
slippage + safety hurdle. Zero execution-PnL samples; no claims about
realized broker alpha. Uncertainty is enormous (n=3); direction agreement
cannot support model deployment.

## Source and independent verification

Original producer/pocket4 evidence, write-once and preserved:

`~/.local/state/heptatrader/operator-evidence/fx-ridge-finite-campaign-20261012-v1/`

- Original campaign seal SHA-256:
  `e5da7854edfe2af4ab00a32ba0b7dc3cf1a41891e1010455de25ddf07c1c28dc`
- Original final SHA-256:
  `448a043e1904192882d1a846132a627893cb3b5e8003d4909ca86a6ca224351e`

Independent read-only original-price/forecast replay was performed for all
three mature windows using a **separate** Ridge/quote verifier on pocket4.
This rechecked original source prefix hashes (six per window), recomputed
the model output with independent arithmetic, checked original 15-minute
first-mature-quote selection and Decimal price outcomes, and verified
no-trading status. Three runs all exited 0. It shares the upstream source
decoder and is therefore **not independent broker-signed market data**.

Result files, private to the local user:

`~/.local/state/heptatrader/operator-evidence/fx-ridge-four-window-independent-audit-20261012/`

The new `fx_ridge_finite_final_audit.py` additionally cross-checks
the fixed four-window calendar, precommit seals, plan/identity and frozen
model SHA-256s, valid quote-expiry-before-seal timing, one-attempt
censorship for all four windows, exact original decision/outcome links and
all three independent reports. Any changed snapshot, delayed
registration, retroactive label or renewed trade/promotion authority is
rejected. The **final v2 independent receipt**, written once in an owner
private (0700) directory, has SHA-256:

`af46b3c9f3028cac98e790e75a67c0919f4ba0ad192ab9477abb770580ce7a4c`

`--output` must be a NEW 0600 receipt path; existing results are never
overwritten. To revalidate at a fresh receipt path:

```sh
cd ~/work/heptatrader-fx-ridge-finite-campaign-20261012
export PYTHONPATH=research:research/neuron_learning:scripts
PY=~/.local/share/heptatrader/laya-runtime/venv/bin/python
E=~/.local/state/heptatrader/operator-evidence/fx-ridge-finite-campaign-20261012-v1
A=~/.local/state/heptatrader/operator-evidence/fx-ridge-four-window-independent-audit-20261012
"$PY" -B -m train_v2.fx_ridge_finite_final_audit \
  --root "$E" --independent "$A" \
  --output "$A/independent-recheck-NEW-NAME.json"
```

Exact regression outcomes: **38/38** related FX Ridge tests including
**14/14** new independent-final adversarial cases. Checks cover
pre-event registry mutation, stale quotes, altered original decisions,
first-maturity-window integrity, wrong independent prediction or price,
data censoring, unexpected trade or promotion, missing reports, and
non-overwrite of evidence.

## Additional blockers: original quote continuity and cross-host trust

The source-readiness monitor is active, but reported unstable
`PATH_GAP`, `ENDPOINT_GAP` and `INPUT_UNAVAILABLE` states
during this audit. It intermittently reported
`HOLD_NEW_FUSION_WINDOW`; later readings showed
`READY_FOR_REVIEW_NOT_AUTHORIZATION` with 22 usable of 24 overlapping
operational probes. This is **not** historical efficacy and must not be
used to retroactively rehabilitate censored window 00. Do not relax the
original quote freshness/continuity or minimum-usable thresholds.

New Mac/X230 physical-host verification is **not complete**. Strict
SSH host identity from pocket4 failed and qian-qi name resolution for
those hosts was unavailable in this round. Never accept a new SSH key
blindly or substitute older Mac/X230 receipts for these four windows.

## Release and next gate

1. **HOLD automatic trading and promotion.** Existing FX/BTC PAPER
   services remain independent from this finite research.
2. Restore stable quote-source continuity with evidence, preserving every
   missing/expired original observation; fix root cause rather than
   relaxing thresholds.
3. Re-establish separately authenticated physical-host paths (Mac/X230)
   and independently revalidate the exact frozen final bundle, including
   all three original future price labels and the censored window.
4. If a new future campaign is warranted, pre-register a genuinely
   separate frozen design with adequate sample size, pre-specified
   economic endpoint and real broker cost source; do not reuse these
   now-inspected outcomes for test-set selection.
5. Complete original owner/witness/ledger trust and IB Flex/Activity
   account-NAV evidence before any formal product admission.

**No broker requests, new trading orders, production service restarts,
actor updates, label backfills or price rewrites** were performed by
these research audits.
