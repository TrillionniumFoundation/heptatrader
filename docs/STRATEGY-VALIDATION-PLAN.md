# Strategy validation plan

Status: current
Applies to: all research strategies and replay outputs
Verification: `canonical-full-suite` on the exact revision; broader venue/host validation is separate

## Objective

Determine whether a strategy signal is reproducible, point-in-time correct, economically meaningful after costs, stable outside its tuning sample and compatible with deterministic runtime semantics. Validation never grants runtime capability by itself.

## Executable data contract

Every run records dataset URI/version/SHA-256, UTC/session calendar, symbol/contract mapping, adjustment/roll rules and missing/duplicate/out-of-order behavior. Reject when future information enters a feature/label, timestamps are ambiguous, changed data reuses an authoritative timestamp, a required gap crosses a decision window or exact inputs cannot be recovered.

## Experiment design

- purged walk-forward folds with embargo at least the maximum feature/label horizon;
- one untouched final OOS segment after design/parameter freeze;
- reported parameter/feature/trial search budget;
- previous deterministic baseline, refactor parity, candidate, no-skill benchmark and adverse-cost variants;
- no selection using final OOS results.

Fold boundaries, purge/embargo and all selected parameters are machine-readable RunManifest fields and validated by tests.

The checked-in deterministic fixture is executable without broker access:

```bash
python3 research/run_protocol.py verify --manifest research/manifest-v1.json
python3 research/run_protocol.py run \
  --manifest run-manifest.json \
  --quotes <quotes.json> --targets <targets.json>
```

`run` and `replay` emit the canonical RunSummary and reject malformed,
out-of-order, changed-timestamp, non-finite or capability-bearing inputs. The
fixture is evidence for protocol correctness, not a claim that a strategy is
profitable or PAPER/LIVE-ready.

## Execution and costs

Each evaluation explicitly models observed/conservative spread, commissions/fees, slippage, decision/queue/broker delay, partial fills/rejects where relevant, order-size/capacity, market impact, session/liquidity constraints, FX conversion, borrow/funding when applicable. In the compact runner, observed bid/ask is the point-in-time base; `spread_bps` is an additive adverse fill stress, `fee_bps` is charged on each fill notional, and annualized `borrow_bps`/`funding_bps` accrue on marked short/gross exposure over a fixed 365-day year. A concrete RunManifest records an explicit `final_out_of_sample` interval after the final walk-forward test fold.

A candidate fails when its edge disappears under a reasonable adverse-cost scenario or relies on a fill unavailable at the decision timestamp.

## Metrics

Minimum distributions and confidence intervals:

```text
net return, volatility, Sharpe/Sortino, max drawdown and duration,
turnover, hit rate, payoff ratio, trade count, exposure, tail loss,
cost share, capacity, time-in-market and drawdown recovery
```

Slice by fold, time-of-day, volatility, spread/liquidity and regime. Report the worst meaningful slice and concentration of PnL by event/day/instrument.

## Determinism and parity

The same source, manifest, input bytes and seed reproduce the decision stream and summary within declared tolerance. Refactors run a golden parity fixture. Replay, SHADOW and later PAPER share intent/risk semantics; fill-model differences are explicit.

## Failure-path tests

- stale/missing/out-of-order quote and changed duplicate timestamp;
- calendar/information gap and timezone boundary;
- zero/negative/NaN/Inf input;
- spread/cost spike and execution delay;
- partial/rejected/duplicate event;
- process restart and deterministic replay;
- position/generation change between preview and apply;
- kill switch, flatten-only and unavailable authority.

## Promotion stages

| Stage | Requirement | Capability |
|---|---|---|
| R0 deterministic fixture | feature/decision/data-quality golden tests | none |
| R1 historical replay | purged walk-forward, final OOS, costs/capacity and stability thresholds | none |
| R2 live SHADOW | real-time complete inputs, stable latency, sufficient independent decisions and replay parity | read-only |
| R3 PAPER proposal | separate human-reviewed runtime/config/risk/operations change | bounded PAPER if approved |
| R4 LIVE proposal | unsupported; separate legal/operational/security/activation architecture | none today |

## RunSummary decision

A compact RunSummary records validation checks as stable codes and values. Do not generate closure grades, campaign receipts, finalizer attestations or self-approving promotion artifacts.
