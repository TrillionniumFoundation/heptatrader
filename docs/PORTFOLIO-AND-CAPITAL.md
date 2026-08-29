# Portfolio compiler and capital budget contract

Status: planned target contract
Applies to: future `HeptaTrade/portfolio/`, `HeptaTrade/risk/`, Agent intent path
Verification: same-revision CI

## Purpose

Agents and strategies express desired portfolio state. They do not independently create broker orders. A trusted portfolio compiler nets compatible intents, applies deterministic capital/risk budgets and emits one bounded execution plan.

## Inputs

- owner/session/strategy identity;
- forecast or target-position intent;
- authoritative current positions and active orders;
- strategy and portfolio budgets;
- normalized quote/liquidity state;
- execution epoch, fencing and state generation;
- expiry and urgency bounds.

## Processing

```text
validate intent
-> normalize target
-> aggregate by instrument/horizon
-> cross-strategy netting
-> apply strategy capital budget
-> apply portfolio gross/net/concentration budgets
-> derive execution delta
-> deterministic risk
-> execution plan or typed rejection
```

## Invariants

- opposite intents are netted before venue order creation;
- no strategy can widen its assigned budget;
- reduction remains available when new risk is blocked;
- crossing through zero is not treated as reduce-only;
- unknown PnL, margin, FX conversion, liquidity or generation fails closed for risk increase;
- every budget decision has a stable reason code and authoritative source.

## Initial scope

The first implementation is deterministic single-account, multi-strategy netting for Simulator. Dynamic optimization, leverage allocation and learned sizing remain out of scope until the deterministic compiler and replay parity are proven.
