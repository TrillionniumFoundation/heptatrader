# EURUSD confirmed-momentum SHADOW v2

Status: experimental
Applies to: `strategies/eurusd-confirmed-momentum-shadow-v2.json`, `scripts/hepta_market_*`, `scripts/hepta_eurusd_confirmed_momentum_strategy.py`, `scripts/hepta_strategy_*`
Last verified commit: moving-main

## Purpose

This is a deterministic, replayable EURUSD research strategy. It emits `NO_TRADE` or a bounded TradeIntent draft. It cannot call a broker mutation API, create a session or authorize PAPER/LIVE.

## Pipeline

```text
immutable input bytes / normalized quote history
  -> market evidence normalization
  -> context/features
  -> deterministic strategy decision
  -> bounded SHADOW receipt
  -> later replay evaluation with explicit costs
```

Current entry points:

- `scripts/hepta_market_official_source_extractor.py`
- `scripts/hepta_market_evidence_normalizer.py`
- `scripts/hepta_market_context_builder.py`
- `scripts/hepta_eurusd_confirmed_momentum_strategy.py`
- `scripts/hepta_strategy_shadow_runner.py`
- `scripts/hepta_strategy_replay_evaluator.py`
- `scripts/validate_hepta_strategy_decision_receipt.py`

The canonical machine contract is `research/manifest-v1.json`. There is no campaign opener, renewer, repair step, root custodian, finalizer or promotion authority in this research path.

## Input contract

The run binds exact bytes/digests for:

- normalized quotes and bars;
- economic-calendar information used as a risk veto;
- official-source payloads when present;
- strategy JSON and implementation source revision;
- cost and delay assumptions.

Data requirements:

- UTC timestamps;
- strict ordering;
- changed data at an identical timestamp is rejected;
- gaps or stale terminal samples fail closed;
- feature construction uses only data available at evaluation time;
- closed-bar rules are explicit and replayable.

## Decision rules

The strategy considers the configured trend regime and requires:

- aligned quote momentum, EMA separation and slope;
- bounded spread and realized step volatility;
- expected movement exceeding the configured all-in cost hurdle;
- no high-impact EUR/USD exclusion window;
- complete and fresh input provenance.

Any hard-gate failure returns `NO_TRADE`. Narrative/confidence text cannot override a gate. News/calendar input is a veto/provenance signal in this version, not directional alpha.

## Output

Each receipt includes:

```text
strategy id/version
source revision and input digests
evaluation timestamp
features and hard-gate outcomes
NO_TRADE or bounded intent draft
cost assumptions
reason code
```

A receipt is research evidence, not a runtime preview permit.

## Evaluation

The evaluator must include spread, commission, slippage and decision-to-fill delay. Results are reported by time-of-day and regime, with purged walk-forward plus a final untouched OOS segment. Capacity/market-impact assumptions are required before any capital recommendation.

Promotion requirements are defined in `STRATEGY-VALIDATION-PLAN.md`. Even a passing candidate remains SHADOW until a separate reviewed runtime change provisions PAPER capability.
