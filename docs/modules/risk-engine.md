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
- an evaluation timestamp equal to the execution-owned context clock for the current decision, so replayed evidence cannot supply its own old freshness clock;
- a positive configured maximum snapshot age;
- every required section marked present;
- every required section bound to exactly the same connection epoch and generation as the snapshot identity;
- an explicit subject containing portfolio ID, account, venue, account base currency and the complete authorized set of full contract identities;
- the same subject on the snapshot and every required section, matching the execution authority's `authorizedSubject`; the order account/venue must match and its full instrument identity must belong to that set.

Worst-case gross requires the exposure section, including current gross notional and both pending-buy and pending-sell notional. Daily-loss requires realized and unrealized PnL presence. Drawdown requires peak and current equity presence. A real observed value of zero is valid only when its section presence, subject, epoch and generation are explicit. Missing identity, missing sections, stale data, mixed subjects or mixed generations reject risk increase. Matching numbers and timestamps never permit a snapshot from a different account, portfolio, venue, currency or instrument set.

## Converted order-notional evidence

An enabled per-order notional limit requires the same explicit fresh snapshot identity even when no portfolio limits are enabled. Per-order and gross-notional policies require `orderNotionalEvidence`; there is no generic quantity-times-price fallback. Historical names such as `baseCurrencyOrderNotionalPresent` and `baseCurrencyOrderNotional` have been removed; callers must provide the bound evidence object.

The execution authority supplies trusted `instrumentContract`, `authorizedQuoteSourceId` and `authorizedFxSourceId` independently of the evidence. Instrument metadata contains a specification ID/version, full instrument identity, instrument kind, quantity unit, price unit, positive multiplier and quote currency. These fields are authoritative configuration or adapter metadata, never user order assertions. The evaluator supports only these explicit arithmetic contracts:

- CASH FX quantities are base-instrument currency units, multiplier exactly one;
- stock quantities are shares, multiplier exactly one;
- futures quantities are contracts, with an explicit contract multiplier;
- option quantities are contracts, with quoted option premium and an explicit contract multiplier.

Prices are quote-currency amounts per underlying unit; other quotation conventions require a new supported contract. Full futures and option identities must include contract-specific attributes, not only root symbols.

Converted evidence must bind the same subject, connection epoch, snapshot generation, exact instrument specification and order quantity. It includes an explicit positive base-currency amount, authoritative quote source/instrument/currency/price/epoch/generation/observation time, and FX source/source currency/account base currency/rate/epoch/generation/observation time. Quote and FX evidence must belong to the same connection epoch and assembled snapshot generation, match the execution-owned source identities and remain within the configured age at evaluation. Same-currency conversion still requires explicit identity-rate evidence with rate exactly one.

The evaluator validates the supplied amount against quantity × multiplier × price × FX rate using those supported unit contracts. Limit orders use the greater of the limit and authoritative reference price; market orders use the authoritative reference. An understated amount, omitted conversion/multiplier, unsupported units, mismatched instrument/currency/source, stale or future-dated quote/FX evidence, numeric overflow or non-finite value rejects. Floating-point rounding tolerance never lowers the charged notional: the decision uses the greater of the validated supplied and calculated amounts.

The assignment-only legacy risk sink and all twelve old context member names have been removed after migrating the remaining test writers. There is no second scalar risk-input interface. The supported snapshot and evidence types are unchanged; see [`../technical/risk-legacy-compatibility.md`](../technical/risk-legacy-compatibility.md) for the migration map and compile-time rejection tests.

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

Use table-driven boundary and hostile tests for zero, exact limit, above limit, NaN, infinity, stale snapshot, missing identity, missing epoch/generation, missing exposure/PnL/equity presence, mixed generation, cross-account/venue/currency/portfolio/instrument-set snapshots and mixed sections, explicit observed zero, futures and option multipliers, explicit FX conversion, omitted or stale quote/FX evidence, unsupported quantity/price units, pending exposure, daily loss/drawdown, flatten-only over-flatten, rate-window restart recovery, kill-switch uncertainty, concurrent admission, and compiler-backed proof that retired compatibility members cannot be assigned or consumed and the supported authoritative API remains usable.
