# Deterministic risk model

Status: current  
Applies to: `HeptaTrade/risk/`, Simulator and IB PAPER policy authorities  
Last verified commit: moving `main`

## One policy, stricter venue rules

`DeterministicRiskPolicy` is the venue-independent pre-trade core. Simulator and IB PAPER construct the same normalized inputs and evaluate:

- order submission gate and global kill switch;
- BUY/SELL and MKT/LMT normalization;
- maximum order quantity;
- maximum order notional at an authoritative valuation price;
- rolling order-attempt rate;
- maximum active orders;
- projected gross absolute position;
- flatten-only exposure reduction;
- limit-price deviation from an authoritative reference.

A venue adapter may enforce stricter rules such as security type, DAY-only, market-vs-limit mode, tick size, session hours or quote-binding. It must not bypass or loosen the common decision.

## Projection semantics

The risk input contains current and projected gross absolute position. Simulator computes the exact projection from its authoritative instrument position. IB PAPER uses the broker-owned risk snapshot and a conservative projection for new risk-increasing orders.

If an authoritative account is already above a cap, a proven exposure-reducing action remains available; a flat or increasing action above the cap is rejected. Cancel and authoritative flatten remain separate safety paths and are not consumed by the normal place-order budget.

## Stable reason codes

The common layer emits `RISK_*` codes, including:

```text
RISK_GLOBAL_KILL_SWITCH_ON
RISK_ORDER_SUBMISSION_DISABLED
RISK_ORDER_QUANTITY_LIMIT
RISK_ORDER_NOTIONAL_LIMIT
RISK_ORDER_RATE_LIMIT
RISK_ACTIVE_ORDER_LIMIT
RISK_GROSS_POSITION_LIMIT
RISK_FLATTEN_ONLY
RISK_PRICE_DEVIATION_LIMIT
RISK_POSITION_SNAPSHOT_INVALID
```

IB PAPER maps these to its existing `IB_PAPER_*` external contract where compatibility requires it. New callers should treat the code as machine-readable and the detail as diagnostic only.

## Configuration

Simulator limits are explicit non-secret environment values in `systemd/hepta-execution-simulator.env.example`. Invalid, zero, negative, non-finite or out-of-range limits prevent runtime startup.

IB PAPER continues to bind limits into its authorization credential. A Simulator result is comparable to PAPER only when the strategy run records the exact risk-policy values used by both environments.

## Tests

`hepta_deterministic_risk_policy_tests` covers each common limit, invalid snapshots and the above-limit reduction escape. Simulator E2E exercises the same policy before journal/send; IB PAPER retains venue-specific guard tests and broker-state checks.
