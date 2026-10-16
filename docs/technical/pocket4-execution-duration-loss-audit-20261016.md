# Pocket4 PAPER execution-cost duration audit — 2026-10-16

## What was actually verified

This study reads the resident **Pocket4-only** OMS journal through the original
`scripts/hepta_pnl_accounting.py` decoder and the frozen account-period file.
It creates no orders, subscriptions, model candidates, trading thresholds or
promotions. Evidence is stored only on the local Pocket4 machine:

`~/.local/state/heptatrader/operator-evidence/pnl-duration-clean-main-20261016/pnl-duration.json`

The source records **34,519 committed journal events**. The strategy period and
journal digest match the concurrently captured PnL observer snapshot; execution
counts, broker commissions, positions and realized PnL all reconcile exactly.
The report is not account NAV and excludes financing, personal taxes and
non-execution costs. No new broker data request occurred during this audit.

Latest matching execution-ledger/PnL read at the audit:

| Instrument | Closed roundtrips | Gross USD | Execution commissions USD | Commission-net USD |
| --- | ---: | ---: | ---: | ---: |
| EUR.USD | 357 | -965.741130 | 1,428.097729 | **-2,393.838859** |
| BTC.USD | 4 | +58.6967243475 | 235.861341 | **-177.1646166525** |
| Both | 361 | -907.0444056525 | 1,663.959070 | **-2,571.0034756525** |

The classifications below use **first original OPEN command send timestamp to
last original CLOSE command send timestamp**, not broker-attested fill duration.
They describe only the completed trades; neither the bucket nor the final return
was observable to an entry decision at the time of entry.

## Real EUR/USD source-linked ex-post decomposition

| Original send-to-send duration | Closed episodes | Gross USD | Commissions USD | Net USD |
| --- | ---: | ---: | ---: | ---: |
| <5 minutes | 66 | -1,458.974500 | 264.015712 | **-1,722.990212** |
| 5–15 minutes | 147 | -1,233.839440 | 588.032262 | **-1,821.871702** |
| 15–60 minutes | 127 | +1,860.757870 | 508.035442 | **+1,352.722428** |
| >=60 minutes | 17 | -133.685060 | 68.014313 | **-201.699373** |

This is a causal *accounting* reconstruction, **not causal evidence** that forcing
a minimum holding period would improve returns. Stops, quote context and
model reversals are endogenous to the actual duration. Retrospectively
selecting the 15–60m winners would have hindsight and survivorship bias.
Do not turn these bucket boundaries into production stop/hold rules.

Opening-side breakout and complete strict Decimal arithmetic are preserved in
`pnl-duration.json` under `instruments.EUR.USD.duration_side.groups`.
BTC: 3 longer 15–60m long episodes had +58.6954744725 gross, $235.020333
fees and -$176.3248585275 net; one <5m episode was also net negative.

## Specific next investigation, preserving genuine chronology

1. Preserve resident PAPER decisions as-is. Link each original opening
   `command_id`, its *pre-entry* model request, input quote, risk preview,
   spread and precommitted forecast with its eventual closing `command_id`,
   close trigger reason and broker fees. Keep missing/unknown reasons.
2. Diagnose the original `MODEL_REVERSAL` decision cadence and quote-age
   distribution compared with `MAX_ADVERSE_MOVE` and `HALT_REDUCE_ONLY`.
   A source-bound exit-trigger reason is necessary to attribute the observed
   short-trade losses; do not infer a reason merely from duration.
3. Freeze any alternative **shadow-only** entry-confirmation or reversal
   hysteresis rule *before* the next eligible forward market window. Preserve
   every scheduled opportunity including abstentions, risk-stop overrides and
   missed/late inputs. No hindsight fitting to the 357 completed episodes.
4. Compare actual driver fills and conservative independent counterfactual
   transaction costs, where observable. Unobserved quotes/fills remain
   censored; simulated next-bar fills are not broker executions.
5. Report true forward opportunity count, realized exact commission-net PAPER
   PnL, maximum loss and risk outcomes, and an unchanged no-trade benchmark.
   Do not promote to order authority absent a positive independently observed
   economic outcome. Historic Brier improvements alone are insufficient.

## Implementation and validation

- `research/pnl_cost_decomposition.py`: read-only complete original OMS replay,
  new grouping by opening side and original-send duration; strict reconciliation
  to the closed-trade totals, no action authority.
- `tests/python/test_pnl_cost_decomposition.py`: 8 existing tests.
- `tests/python/test_pnl_duration_side.py`: 7 additional boundary,
  missing-time, arithmetic, invalid-value and side tests.
- **Pocket4 on latest-main-isolated worktree:** 15/15 relevant unit tests
  passed, documentation check passed, read-only actual full-ledger replay passed,
  all six critical IB/Execution/PAPER/crypto/Laya services remained active.
- The source-only result on GitHub is a proposal; **the production trader and
  pinned model are not altered**. The Pocket4 main working tree's pre-existing
  uncommitted changes were not modified.
