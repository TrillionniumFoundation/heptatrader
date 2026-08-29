# Deterministic risk model

Status: current
Applies to: `HeptaTrade/risk/`, Simulator, portfolio compiler and IB PAPER policy authorities
Verification: same-revision CI

## One common policy, stricter venue rules

`DeterministicRiskPolicy` is the venue-independent pre-trade core. A venue may add stricter security type, session, tick-size, order-shape or quote-generation rules; it may never bypass or weaken the common decision.

## Rule/source contract

Every enabled dimension must have:

```text
field -> authoritative owner -> generation/freshness -> missing behavior -> reason code -> tests
```

| Dimension | Required authority | Missing/invalid behavior |
|---|---|---|
| quantity/order shape | normalized trusted plan | reject |
| valuation/reference price | Execution-owned fresh quote | reject |
| rate/active orders | OMS/venue projection | reject risk increase |
| gross/net position | authoritative position snapshot | reject risk increase |
| strategy gross/budget | portfolio compiler/budget authority | reject risk increase |
| daily PnL/drawdown | authoritative account/PnL state | reject when rule enabled |
| snapshot completeness | state authority generation | reject |
| kill/submission/flatten mode | reviewed runtime policy | enforce; safe reduction separate |

A rule must not be advertised as active if its source is not wired. Zero may disable an explicitly optional limit; unknown data never silently becomes zero usage.

## Strict reduction proof

A trusted caller may mark an order exposure-reducing, but the policy verifies that one normalized instrument order satisfies:

```text
projected_gross < current_gross
projected_gross + order_quantity == current_gross
```

within a deterministic tolerance. A `+10 -> -5` flip from `SELL 15` fails because the order crosses zero and creates opposite exposure.

A proven strict reduction remains available above a cap or when entry/rate/active-order gates are closed. It still obeys finite values, maximum single-order quantity/notional and valid order shape.

## Evaluation order

1. validate policy values;
2. validate normalized side/type/quantity and finite authoritative prices;
3. validate authoritative snapshot values and freshness;
4. prove strict reduction if claimed;
5. enforce kill/submission/flatten and rate/active-order gates;
6. enforce gross/net/strategy/PnL/drawdown budgets;
7. enforce venue-independent price band;
8. return stable reason code and bounded detail.

## Consistency with preview/apply

Preview and apply use the same policy version and authoritative snapshot type. Apply rejects or re-previews when the bound generation changes. Agent-provided position, PnL, quote, margin, limit usage or freshness is ignored.

## Required tests

- allow, exact boundary and over-boundary for every rule;
- NaN/Inf/negative/unknown values;
- stale/incomplete generation;
- above-limit account with proven safe reduction;
- cross-zero pseudo reduction;
- rate/active-order exhaustion during safe exit;
- preview/apply generation change;
- same command same payload and same command changed payload;
- Simulator/IB PAPER parity for common decisions.
