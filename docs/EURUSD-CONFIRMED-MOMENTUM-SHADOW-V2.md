# EUR.USD confirmed momentum SHADOW v2

This is a deterministic research strategy. It can emit a bounded decision receipt or `NO_TRADE`; it cannot provision a trading session, call Gateway mutations, connect to a Broker, authorize PAPER, or authorize LIVE.

## Checked-in source path

The current research pipeline is composed from:

- `scripts/hepta_official_source_capture.py`
- `scripts/hepta_market_official_source_extractor.py`
- `scripts/hepta_market_evidence_normalizer.py`
- `scripts/hepta_shadow_market_history.py`
- `scripts/hepta_market_context_builder.py`
- `scripts/hepta_strategy_contracts.py`
- `scripts/hepta_eurusd_confirmed_momentum_strategy.py`
- `scripts/hepta_strategy_shadow_runner.py`
- `scripts/hepta_strategy_replay_evaluator.py`
- `scripts/validate_hepta_strategy_decision_receipt.py`
- `strategies/eurusd-confirmed-momentum-shadow-v2.json`

These files are source/research entry points and are not installed by the default core runtime package. A research deployment must package them separately with hashes and without granting execution capability.

## Evidence contract

Every decision binds the exact strategy JSON, code hashes, evaluation time, normalized quote/bar history, account/position snapshot, calendar/information evidence and previous durable state. Parsers reject duplicate JSON keys, non-finite numbers, malformed timestamps, inconsistent hashes and stale or incomplete evidence.

Official-source capture and extraction remain separate from strategy evaluation. The evaluator must operate on retained bytes and deterministic normalized artifacts; it cannot fetch network data during a decision. Quiet feeds do not imply missing coverage when a successful capture receipt proves the fetch boundary.

## Decision gates

A trade draft is considered only when all hard gates pass: data/provenance complete and fresh, EUR/USD quote momentum and trend features aligned, spread/volatility bounded, expected movement exceeds modeled transaction cost, no guarded high-impact event, and authoritative portfolio state is known. Confidence text cannot override a failed gate.

`NO_TRADE` is the required result for missing evidence, stale quote, unknown position, non-flat state where flat is required, invalid cost model, out-of-window decision, malformed state or any authority ambiguity.

## Validation and promotion

1. deterministic replay on immutable hashed datasets;
2. walk-forward and held-out evaluation including spread, slippage and delay;
3. shadow receipts with zero authority/cleanup/audit failure;
4. stability across time-of-day and regimes;
5. independent review of concentration, drawdown and failure modes;
6. Simulator through the canonical Gateway/Execution/OMS path;
7. separate risk challenger and controlled PAPER qualification before any mutation.

No SHADOW result alone promotes the strategy to PAPER or LIVE. Capability remains governed by `CAPABILITY-MATRIX.md` and `PROD-GO-LIVE-CHECKLIST.md`.
