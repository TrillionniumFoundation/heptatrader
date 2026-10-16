# Pocket4 HeptaTrader PnL/history continuation — 2026-10-16

**Status:** Code-only research patch on branch. Pocket4 runtime deployment is **NOT verified** in this continuation because the remote tunnel did not poll; do not report an installation, successful IB backfill, live model improvement or order change.

## Latest directly observed Pocket4 facts (07:44 New York, prior session)

- Strategy reset: 2026-10-07 01:03:10 New York.
- EUR.USD: 712 fills, gross -1002.677140 USD, reported commissions 1416.097728 USD, net -2418.774868 USD, flat.
- BTC.USD: 24 fills, gross +58.6967243475 USD, commissions 235.861341 USD, net -177.1646166525 USD, flat.
- Strategy total: gross -943.9804156525 USD, commissions 1651.959069 USD, net -2595.9394846525 USD.
- Account NAV is not reconciled; this is strategy period PnL, not full account return.
- Trainer: 38 completed, 1 failed; evaluator: 38 rejected, 1 pending; no promotion.
- One-year EUR/USD 5m paired bars: 73,169 rows, 2025-10-16 to 2026-10-13, SHA256 `3988a917554fc7322ae06a38560d4a9c91b5137fceccdc561830ec4327987f12`.
- 47,655 / 73,169 (65.13%) historical bars have identical BID/ASK close. Do not treat this as a directly executable zero-spread offer.
- Brier on 14 offline walk-forward folds improved from 0.63059 to 0.59223; this is **not** net financial alpha.
- The bounded PAPER historical probe for Oct 15 received `cashhmds` code 2105 (disconnected). It issued **zero** historical requests and retrieved **zero** new bars.
- Frozen real parent model action head on 32 samples always chose ACT while all 32 training labels were ESCALATE_wrong. Treat this as a diagnosed candidate flaw, **not** proven cause of live strategy losses.

## Executable next sequence (Pocket4 only)

1. Run `python3 -B -m unittest tests.python.test_pocket4_readonly_audit` from repository root; on failure fix tests, never patch evidence.
2. Run `python3 -B -m research.historical_training.pocket4_readonly_audit --pnl ~/.local/state/heptatrader/pnl/snapshot.json --dataset ~/.local/state/heptatrader/neuron-learning/historical-offline/eurusd-5m-joined-1yr-20261014-r4/paired-bars.json --dataset-sha256 3988a917554fc7322ae06a38560d4a9c91b5137fceccdc561830ec4327987f12` while neither file is being rewritten. Capture stdout and exit status. No broker API involved.
3. Account PnL: reconcile original opening NAV, dated funding/fees, stable FX valuation time with broker statements; do not widen tolerance to force acceptance. Continue to display strategy net independently.
4. Diagnose cashhmds state on existing PAPER gateway with strictly read-only isolated history client. If disconnected, record status and stop. If healthy, fetch ONLY missing completed UTC windows from 2026-10-14 onward and separately fetch BID, ASK, MIDPOINT with existing rate limits, retries only after definitive failures. Verify duplicate-free exact overlaps and source revisions before joining.
5. Leave the existing one-year immutable historical training source intact. Compare no-trade baseline, simple linear, tiny MLP, and cost-conscious policies using chronological train/validation/test and actual commission receipts plus conservative slippage. Historical revisions never count as precommitted live forecasts.
6. Do NOT train the ACT/ESCALATE action head on the all-negative 32-row set. Require mixed independently labeled examples, inspect logit scaling, then stage a new offline candidate without changes to original forward trials.
7. Preserve current PAPER driver/service identities. No automatic candidate promotion; if any runtime or tests fail, retain the logs and original runtime.

## Reproduction notes

Original device evidence root:
`/home/alex/.local/state/heptatrader/operator-evidence/pnl-train-readonly-audit-20261016-1792151033573649889/`

New `pocket4_readonly_audit.py` is deliberately independent of the broker SDK and has no write calls; it reports arithmetic inconsistencies and historical spread-shape risks. It does **not** infer profitability, execution fills or account NAV. 
