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

1. Run `python3 -B -m unittest discover -s tests/python -p 'test_pocket4_readonly_audit.py' -v` from repository root; then `python3 -B scripts/run_python_tests.py --lane core`. On failure fix tests, never patch evidence. No training or broker connections are invoked by the new audit tests.
2. Run `python3 -B -m research.historical_training.pocket4_readonly_audit --pnl ~/.local/state/heptatrader/pnl/snapshot.json --dataset ~/.local/state/heptatrader/neuron-learning/historical-offline/eurusd-5m-joined-1yr-20261014-r4/paired-bars.json --dataset-sha256 3988a917554fc7322ae06a38560d4a9c91b5137fceccdc561830ec4327987f12` while neither file is being rewritten. Capture stdout and exit status. No broker API involved.
3. Account PnL: reconcile original opening NAV, dated funding/fees, stable FX valuation time with broker statements; do not widen tolerance to force acceptance. Continue to display strategy net independently.
4. Diagnose cashhmds state on existing PAPER gateway with strictly read-only isolated history client. Official IBKR event 2105 means a genuinely disconnected HMDS farm and is **not** interchangeable with 2107 (idle/on-demand). Allow a bounded wait for a real 2106 (connected) or 2107 (idle) callback; if still 2105, record status and stop without dispatch, and diagnose broker connectivity / Gateway session. If healthy, fetch ONLY missing completed UTC windows from 2026-10-14 onward and separately fetch BID, ASK, MIDPOINT with existing rate limits, retries only after definitive failures. Verify duplicate-free exact overlaps and source revisions before joining. Do not assume real-time cashfarm implies historical cashhmds connectivity.
5. Leave the existing one-year immutable historical training source intact. Compare no-trade baseline, simple linear, tiny MLP, and cost-conscious policies using chronological train/validation/test and actual commission receipts plus conservative slippage. Historical revisions never count as precommitted live forecasts.
6. Do NOT train the ACT/ESCALATE action head on the all-negative 32-row set. Require mixed independently labeled examples, inspect logit scaling, then stage a new offline candidate without changes to original forward trials.
7. Preserve current PAPER driver/service identities. No automatic candidate promotion; if any runtime or tests fail, retain the logs and original runtime.

## Reproduction notes

Original device evidence root:
`/home/alex/.local/state/heptatrader/operator-evidence/pnl-train-readonly-audit-20261016-1792151033573649889/`

New `pocket4_readonly_audit.py` is deliberately independent of the broker SDK and has no write calls; it reports arithmetic inconsistencies, stale broker marks, reported fee/ledger completeness, break-even deficit and historical spread-shape risks. It reads each input once and rejects symlinked or changing source files. Its fee-per-execution figure is **not** a per-round-trip cost. It does **not** infer profitability, execution fills or account NAV. 

## Cost concentration at the previously captured closed-trade counts

Previous driver evidence reported 354 EUR/USD and 4 BTC/USD closed roundtrips within the same reset period. Conditional on those counts matching the strategy period, the implied per-closed-trade gross / commissions / net were:

| Strategy | gross per closed trade | reported commissions per closed trade | net per closed trade |
|---|---:|---:|---:|
| EUR/USD | -$2.83 | $4.00 | -$6.83 |
| BTC/USD | +$14.67 | $58.97 | -$44.29 |

This is an **aggregate period ratio**, not evidence of any individual trade's realized PnL or cause. Reconcile command identities and real execution fills before attributing performance to a decision path. Reported commissions explain about 63.6% of aggregate strategy net loss in magnitude; even before fees, period gross PnL was negative.

## 2026-10-16 06:20 PDT follow-up: real Pocket4 readback

The initial disconnected-`cashhmds` result above remains valid for its original timestamp, but a **later distinct PAPER download session succeeded** with `cashhmds` 2106 CONNECTED and three 1,173-row MIDPOINT/BID/ASK responses. No orders were sent by the collector. This is not a reinterpretation of the old failed probe.

- Immutable derived subwindow: `eurusd-5m-increment-20261016-proven-subwindow-v1`, 602 paired bars, 2026-10-13 21:15 to 2026-10-15 23:50 UTC; signed parent-range limitations retained in `coverage.json` / `derivation-proof.json` (a parent API interval is **not** claimed complete).
- Forward-appended one-year data set: `eurusd-5m-joined-1yr-through-20261015-v1`, **73,739** complete 5-minute bars (570 new). New `paired-bars.json` SHA256 `124e5f20af474e238ea8df20f7dbf399bb1986df7135186ba353bf2bb5afbcd4`; 32 exact overlapped bars; zero merge conflicts, zero calendar missing according to the recorded source-window schedule. Old 73,169-bar source remains intact.
- The actual `pocket4_readonly_audit` was run against this extended dataset and the live strategy PnL snapshot on the Pocket4 isolated PR worktree. Hash accepted; **48,091 / 73,739** bars (65.218%) have zero BID/ASK closing spread and cannot justify free fills; `reported_execution_fee_coverage=true` and account all-in NAV remains unknown.
- At that observation the strategy period contained 719 EUR/USD and 24 BTC/USD executions, with combined **gross -$907.0444056525 / reported commissions $1,663.959070 / strategy net -$2,571.0034756525** (USD), flat. These are not account return figures and can change on the resident PAPER driver.
- Pocket4 tests verified: **11 focused unit tests passed**; historical training suite **705 tests passed, 7 skipped**, when invoked with `PYTHONPATH=research:research/neuron_learning:scripts`. Initial plain `unittest discover` failed due to absent module search paths; it was a test launcher error, not a code regression.
- Separate already-preserved incremental new-data cost screen: `operator-evidence/pr371-new570-incremental-cost-20261016/result.json`. The tested Ridge and momentum cases for 15/30/60-minute horizons supplied no positive net-alpha pass; zero simulated trades is no outperformance claim. A historical fixed simulation cannot establish real PAPER fill profitability.
- A separately sealed episode-level PAPER fee attribution for an earlier snapshot reported **355 FX roundtrips (115 net winners) and 4 BTC roundtrips (0 net winners)**. See `operator-evidence/neuron-pnltrain-20261016-next/paper-cost-decomposition-final.json`; do not confuse that earlier capture with the latest snapshot.
- Resident trainer now reports 39 completed and 1 failed, with **8 genuine exact-behavior-bound PnL rewards** and 361 separate execution rewards; those 361 are not automatically model-credit labels. A current trial remains pending and model promotion remains disabled.

### Updated next action

Do not redownload the year or repeat an already-complete Oct14-15 repair. Preserve these sources, use only **new subsequent completed UTC days** for future bounded IB backfills, and prioritize (a) genuine broker-cost net outcome labels, (b) the all-negative ACT target defect and target-blind temporally disjoint Laya examples, and (c) prospective original-quote evaluation before any change to order policy. This remains a **source-only research PR**, with zero model deployment and no IB order authority.
