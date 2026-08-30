# EURUSD confirmed-momentum SHADOW v2

Status: experimental
Applies to: `strategies/eurusd-confirmed-momentum-shadow-v2.json`, deterministic market context and strategy evaluation
Verification: `canonical-full-suite` on the exact revision; no PAPER/LIVE claim

## Purpose

A deterministic EURUSD research strategy that emits `NO_TRADE` or a bounded target/intent draft. It cannot call a broker mutation API, create a session or authorize PAPER/LIVE.

## Pipeline

```text
immutable point-in-time quote/bar/calendar inputs
-> normalization and data-quality checks
-> deterministic context/features
-> strategy decision
-> append-only research EventLog
-> cost/fill replay
-> RunSummary
```

The canonical machine contract is `research/manifest-v1.json`; target object semantics are in `RESEARCH-PROTOCOL.md`. The static manifest is a checked-in verification contract, not a runnable economics manifest; a concrete replay supplies a full `RunManifest`. Existing large research scripts remain implementation details while G-009/G-010 are in progress and must not redefine the canonical run protocol.

## Input rules

- UTC and explicit market/session calendar;
- per-instrument strict ordering; independent instrument streams may be interleaved, and an exact unchanged timestamp repeat may be deduplicated;
- changed data at an identical authoritative timestamp is rejected;
- gaps/stale terminal samples fail closed;
- features use only information available at evaluation time;
- all source/strategy/config/data bytes are digest-bound.

## Decision rules

The strategy requires aligned quote momentum, EMA separation/slope, bounded spread/step volatility, expected movement above all-in cost and no configured EUR/USD event-exclusion window. Missing/conflicting provenance or any hard-gate failure returns `NO_TRADE`; narrative text cannot override a gate.

## Output

Each event/summary records strategy id/version, source/input digests, evaluation timestamp, features/gates, decision/target draft, cost assumptions and stable reason code. It is research evidence, never a runtime preview permit.

## Evaluation

Evaluation includes observed spread plus any conservative spread stress,
commission/fees, slippage, decision-to-fill delay, annualized borrow/funding,
adverse-cost sensitivity, purged walk-forward, final untouched OOS, time/regime
slices and capacity assumptions. Even a passing candidate remains SHADOW until
a separate reviewed runtime change provisions bounded PAPER capability.
