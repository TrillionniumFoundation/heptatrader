# Deterministic risk model

Status: current  
Applies to: `HeptaTrade/risk/`, Simulator and IB PAPER policy authorities  
Last verified commit: moving `main`

## One policy, stricter venue rules

`DeterministicRiskPolicy` (`deterministic-risk-v2`) is the venue-independent pre-trade core. Simulator and IB PAPER construct normalized trusted inputs and evaluate:

- order-submission gate and global kill switch;
- BUY/SELL and MKT/LMT normalization;
- finite positive quantity and authoritative valuation price;
- per-order quantity and notional;
- rolling order-attempt rate and active-order budget;
- complete portfolio snapshot and fresh quote;
- projected gross absolute position;
- optional net-position and per-strategy gross budgets;
- optional daily-loss and drawdown budgets;
- flatten-only and strict reduce-only semantics;
- limit-price deviation from an authoritative reference.

A venue may enforce stricter rules such as security type, DAY-only, market-vs-limit mode, tick size, session hours or quote-generation binding. It must not bypass or loosen the common decision.

## Strict reduction proof

A trusted caller marks an order as exposure-reducing, but the risk core verifies the claim independently. One normalized order changes one instrument, so a non-crossing reduction must satisfy:

```text
projected_gross + order_quantity == current_gross
projected_gross < current_gross
```

within a narrow deterministic floating-point tolerance. A `+10 -> -5` position flip produced by `SELL 15` reduces gross from 10 to 5, but `5 + 15 != 10`; it is rejected as `RISK_REDUCE_ONLY_CROSS_ZERO`.

A proven strict reduction remains available when the account is already above a cap, the entry-rate/active-order budget is exhausted, or the new-entry/kill-switch gate is closed. Order shape, finite values, maximum single-order quantity/notional and limit-price sanity still apply. Cancel and authoritative flatten remain separate safety paths.

## Portfolio inputs

The common policy supports optional caps for:

```text
absolute projected net position
per-strategy projected gross position
daily loss
drawdown
```

A zero limit disables that optional dimension. Non-finite or structurally invalid portfolio values always fail closed. These fields must come from an authoritative decision snapshot; Agent-supplied values are never accepted as portfolio truth.

## Stable reason codes

The common layer emits machine-readable `RISK_*` codes, including:

```text
RISK_GLOBAL_KILL_SWITCH_ON
RISK_ORDER_SUBMISSION_DISABLED
RISK_ORDER_QUANTITY_LIMIT
RISK_ORDER_NOTIONAL_LIMIT
RISK_ORDER_RATE_LIMIT
RISK_ACTIVE_ORDER_LIMIT
RISK_GROSS_POSITION_LIMIT
RISK_NET_POSITION_LIMIT
RISK_STRATEGY_GROSS_LIMIT
RISK_DAILY_LOSS_LIMIT
RISK_DRAWDOWN_LIMIT
RISK_FLATTEN_ONLY
RISK_REDUCE_ONLY_CROSS_ZERO
RISK_QUOTE_STALE
RISK_SNAPSHOT_INCOMPLETE
RISK_PRICE_DEVIATION_LIMIT
RISK_POSITION_SNAPSHOT_INVALID
```

IB PAPER maps shared codes to its existing `IB_PAPER_*` external contract where compatibility requires it. Free-form detail is diagnostic only.

## Configuration and authority

Simulator limits are explicit non-secret environment values. Invalid, zero, negative, non-finite or out-of-range mandatory limits prevent runtime startup. IB PAPER binds its reviewed limits into its authorization credential.

Quote freshness and snapshot completeness are established by the Execution-owned venue/state path before policy evaluation. Agent request fields never provide `referencePrice`, current position, portfolio PnL, limit usage or freshness truth.

## Tests

`hepta_deterministic_risk_policy_tests` covers every common entry limit, stale/incomplete state, NaN/Inf, optional portfolio budgets, above-limit reduction escape and the cross-zero failure. Simulator E2E exercises the same policy before journal/send; IB PAPER retains venue-specific guard and broker-state tests.
