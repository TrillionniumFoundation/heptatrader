# Risk engine

Status: CURRENT  
Applies to: repository HEAD  
Implementation: `HeptaTrade/risk/`, `HeptaTrade/execution/ib_paper_execution_profile.cpp`  
Tests: `tests/pre_trade_risk_engine_tests.cpp`, `tests/ib_paper_kill_switch_tests.cpp`

## Responsibilities

Risk is enforced in layers. The Gateway applies session capability, rate, and coarse quantity bounds. The Execution Service applies the final deterministic policy using authoritative quote, active orders, positions, account state, kill switch, venue identity, and durable command context. A strategy confidence score never overrides a hard risk gate.

## Generic pre-trade contract

The generic engine evaluates:

- global order gate and kill switch;
- quantity and daily-order limits;
- account and PAPER/LIVE authorization;
- position/snapshot completeness and freshness;
- limit-price deviation from an authoritative reference;
- per-order notional;
- pending buy/sell notional;
- worst-case gross notional after all active orders fill;
- daily loss and drawdown limits;
- flatten-only exposure reduction.

All monetary values in the generic notional and PnL fields must use one declared account base currency. Raw quantities from different instruments must never be added as a portfolio risk measure.

## IB PAPER policy

The fixed IB PAPER profile additionally binds account, loopback host, allowed port, client ID, control directory, authorization credential, order type, maximum order quantity and notional, order rate, active-order count, gross position, quote freshness, and allowed security types. The profile hash is part of the authorization credential.

The canonical PAPER kill switch is a root/operator-owned filesystem marker. Missing or uncertain filesystem identity is fail-closed for risk increase. Cancel and guarded flatten remain separate exit paths.

## Snapshot requirements

A risk-increasing order requires a complete snapshot with an execution connection epoch and generation. Snapshot age must be inside its explicit freshness bound. The worst-case calculation includes current base-currency gross notional plus pending active-order exposure plus the candidate order.

The current IB adapter retains venue-specific quantity exposure for its bounded profile. Multi-asset expansion requires base-currency notional, contract multiplier, FX conversion provenance, and margin consumption in the authoritative snapshot before qualification.

## Decision output

Every decision returns an allow flag, stable reason code, and detail. Accepted decisions should record the risk-policy digest, snapshot epoch/generation, quote identity, calculated order notional, and calculated worst-case gross notional in the execution journal.

## Failure semantics

Missing, non-finite, negative, stale, unit-ambiguous, or internally inconsistent inputs reject risk increase. A risk callback exception or unavailable kill-switch reader is not an implicit allow. Limit equality is permitted only where the configured contract explicitly defines an inclusive maximum.

## Concurrency

Rate and active-exposure checks must be atomic with admission. The final adapter send lock revalidates the authoritative quote binding and venue state so the state cannot change silently between preview and send.

## Observability

Count decisions by reason code and instrument; record order notional, worst-case gross notional, snapshot age, quote age, rate-window utilization, active-order count, and kill-switch state. Do not expose account secrets.

## Test expectations

Use table-driven boundary tests for zero, exact limit, above limit, NaN, infinity, stale snapshot, missing reference price, pending exposure, daily loss/drawdown, flatten-only over-flatten, rate-window restart recovery, kill-switch uncertainty, and concurrent admission.
