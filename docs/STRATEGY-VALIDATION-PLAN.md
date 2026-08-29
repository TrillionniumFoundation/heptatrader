# Strategy validation plan

Status: current
Applies to: all research strategies and replay outputs
Last verified commit: moving-main

## 1. Objective

Establish whether a strategy signal is reproducible, leakage-free, economically meaningful after costs, stable outside its tuning sample and operationally compatible with the deterministic runtime. The process never grants runtime capability by itself.

## 2. Data contract

Every run records dataset identity and SHA-256 digests, UTC/session calendar, symbol/contract mapping, missing/duplicate/out-of-order policy and any adjustment/roll rule. Inputs are immutable for the run.

Reject a run when:

- future information enters a feature or label;
- bar close or calendar publication time is ambiguous;
- a changed observation reuses the same authoritative timestamp;
- an unexplained data gap crosses a decision window;
- time-zone/session conversion is not versioned;
- the exact run cannot be reproduced from recorded inputs.

## 3. Experiment design

Use purged walk-forward folds with an embargo at least as long as the maximum feature/label horizon. Keep one final OOS segment untouched until design and parameters are frozen. Parameter selection, feature selection and repeated trials are included in the reported search budget.

Required comparisons:

- previous deterministic baseline;
- refactor parity at identical parameters;
- candidate strategy;
- simple/no-skill benchmark;
- stress variants for costs, delay and missing data.

## 4. Execution and cost model

Include:

- observed/conservative spread;
- commissions and fees;
- slippage distribution;
- decision, queue and broker delay;
- partial fills and rejects where applicable;
- order-size/capacity and market-impact assumption;
- trading-session and liquidity constraints.

A candidate fails when the economic edge disappears under a reasonable adverse-cost scenario or when profitability depends on fills unavailable at the decision timestamp.

## 5. Metrics

Report distributions and confidence intervals, not one headline Sharpe. Minimum set:

```text
net return, volatility, Sharpe/Sortino, max drawdown and duration,
turnover, hit rate, payoff ratio, trade count, exposure, tail loss,
cost share, capacity, time-in-market and recovery from drawdown
```

Slice by year/month, time-of-day, volatility, spread/liquidity and market regime. Report the worst meaningful slice and concentration of PnL by event/day/instrument.

## 6. Determinism and parity

Same code, manifest and input bytes must reproduce the decision stream and summary within the declared tolerance. Refactors run a golden parity fixture. Simulator, SHADOW and later PAPER must share intent/risk semantics; differences in fill model are explicit.

## 7. Failure-path tests

At minimum:

- stale/missing/out-of-order quote;
- changed duplicate timestamp;
- calendar/information gap;
- zero/negative/NaN/Inf input;
- spread/cost spike;
- delayed or partial execution;
- process restart and replay;
- duplicate/uncertain command result;
- position change between preview and apply;
- kill-switch and flatten-only mode.

## 8. Promotion stages

### R0 — deterministic unit fixture

Feature and decision golden tests pass.

### R1 — historical replay

Purged walk-forward and final OOS pass declared statistical/economic thresholds.

### R2 — live SHADOW

Observe real-time data without mutation. Require complete packets, stable latency, no authority/session failures and enough independent decisions across regimes.

### R3 — PAPER proposal

A human-reviewed change may provision bounded PAPER capability after research, risk and operations review. This document does not authorize it.

### R4 — capped LIVE proposal

Unsupported. Requires a separate architecture, risk, legal/operational and activation review. No current artifact implies LIVE readiness.

## 9. Run output

Each run writes a compact JSON summary containing manifest/source/input digests, fold boundaries, parameters, costs, metrics, reason codes and decision-stream digest. Do not generate round manifests, closure bundles, root attestations or campaign finalizer receipts.
