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
- limit-price deviation from an authoritative reference;
- per-order base-currency notional;
- pending buy/sell notional;
- worst-case gross notional after all active orders fill;
- daily loss and drawdown limits;
- flatten-only exposure reduction without crossing zero.

All monetary values in the generic notional and PnL fields use one declared account base currency. Raw quantities from different instruments must never be added as a portfolio risk measure.

## Explicit authoritative snapshot contract

Portfolio controls do not accept default values, compatibility fields, or sentinel zeroes as evidence. A risk-increasing decision that enables worst-case gross, daily-loss, or drawdown limits requires an explicit `PreTradeRiskAuthoritativeSnapshot` with:

- `present=true` and `complete=true`;
- non-zero connection epoch and snapshot generation;
- positive observation and evaluation timestamps;
- a positive configured maximum snapshot age;
- every required section marked present;
- every required section bound to exactly the same generation as the snapshot identity.

Worst-case gross requires the exposure section, including current gross notional and both pending-buy and pending-sell notional. Daily-loss requires realized and unrealized PnL presence. Drawdown requires peak and current equity presence. A real observed value of zero is valid only when its section presence and generation are explicit. Missing identity, missing sections, stale data, or mixed generations reject risk increase.

`baseCurrencyOrderNotionalPresent` distinguishes an explicitly converted notional from an omitted value. When it is absent, an enabled notional limit may derive quantity multiplied by the positive authoritative price only when that calculation is the caller's declared base-currency contract. A present zero, negative, non-finite, or unit-ambiguous notional is invalid.

Compatibility fields retained in `PreTradeRiskContext` are not read as authoritative evidence for enabled portfolio limits. They exist only to keep non-canonical legacy sources buildable during migration.

## IB PAPER policy

The fixed IB PAPER profile additionally binds account, loopback host, allowed port, client ID, control directory, authorization credential, order type, maximum order quantity and notional, order rate, active-order count, gross position, quote freshness, and allowed security types. The profile hash is part of the authorization credential.

The canonical PAPER kill switch is a root/operator-owned filesystem marker. Missing or uncertain filesystem identity is fail-closed for risk increase. Cancel and guarded flatten remain separate exit paths.

## Snapshot requirements

A risk-increasing order requires a complete snapshot with an execution connection epoch and generation. Snapshot age must be inside its explicit freshness bound. The worst-case calculation includes current base-currency gross notional plus pending active-order exposure plus the candidate order.

The current IB adapter retains venue-specific quantity exposure for its bounded profile. Multi-asset expansion requires base-currency notional, contract multiplier, FX conversion provenance, and margin consumption in the authoritative snapshot before qualification.

## Decision output

Every decision returns an allow flag, stable reason code, detail, calculated order notional, calculated worst-case gross notional, and the epoch/generation used when a snapshot-bound policy is active. Accepted decisions should record the risk-policy digest, snapshot identity, quote identity, and calculated values in the execution journal.

## Failure semantics

Missing, non-finite, negative, stale, unit-ambiguous, generation-mixed, or internally inconsistent inputs reject risk increase. A risk callback exception or unavailable kill-switch reader is not an implicit allow. Limit equality is permitted only where the configured contract explicitly defines an inclusive maximum.

A verified flatten-only order may bypass breached portfolio loss/gross thresholds so an exit remains available, but it must still have a known finite position, move strictly toward zero, and never cross zero into reversed exposure. Production cancel and flatten retain dedicated guarded paths.

## Concurrency

Rate and active-exposure checks must be atomic with admission. The final adapter send lock revalidates the authoritative quote binding and venue state so the state cannot change silently between preview and send. Preview is advisory; only the final lock-bound decision authorizes external mutation.

## Observability

Count decisions by reason code and instrument; record order notional, worst-case gross notional, snapshot epoch/generation, snapshot age, quote age, rate-window utilization, active-order count, and kill-switch state. Do not expose account secrets.

## Test expectations

Use table-driven boundary and hostile tests for zero, exact limit, above limit, NaN, infinity, stale snapshot, missing identity, missing epoch/generation, missing exposure/PnL/equity presence, mixed generation, explicit observed zero, missing reference price, pending exposure, daily loss/drawdown, flatten-only over-flatten, rate-window restart recovery, kill-switch uncertainty, and concurrent admission.
