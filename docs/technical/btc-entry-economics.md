# BTC-only entry economics and anti-churn profile

This profile is opt-in and restricted to new BTC/USD PAPER longs. It keeps the
historical `confidence >= 0.62` direction predicate, then checks a real explicitly
provided expected-move forecast and conservative round-trip costs. It does not
restore the former whole-strategy COST gate to FX or change either base exit rule.

## Evidence behind the change

The previously valid period report at 1791355813217 ms used an immutable 425-row
OMS prefix with SHA256
`baa099a4038705a323a9bfbec2f49d8dbdee1dca456d5562737151ab6bcb1739`.
Replaying this exact prefix gave BTC turnover USD 73,505.9467561675 and commissions
USD 134.287230. A flat 0.18% of that turnover is USD 132.31070416110150 (98.53% of
the reported commission). Only USD 1.97652583889850 exceeded the flat proportional
fee, including the small canary and minimum-fee effects. This does NOT support
slowing required partial-position liquidation to save many minimum commissions.
The dominant problem was insufficient expected move relative to proportional
entry/exit commissions, not merely the number of partial callback records.

Source schedule verified 2026-10-07:
https://www.interactivebrokers.com/en/pricing/commissions-cryptocurrencies.php
IB's footnote describes upfront 18bps per executed order with minimum USD1.75;
volume/1%-cap adjustments can be rebated later. No anticipated rebate is treated
as cash already received. The live account's fee receipts remain authoritative
for PnL; this module does not rewrite them or resolve accounting conflicts.

## Exact proposed/installed profile

`hepta_btc_entry_policy.PROFILE` is versioned with the executable. A runtime
configuration must match it exactly; there is no permissive override switch.

| Control | Value |
|---|---|
| Eligible entry | BTC BUY, flat, no pending, no halt, confidence >= 0.62 |
| Forecast | Numeric expected MID move in bps, fixed 900/1800/3600-second horizon |
| Source quote | IB, authoritative, non-stale, original source age <= 5 seconds |
| Forecast age | <= 60 seconds from the final model-input quote |
| Cash-size limit | Existing USD25,000; not increased to dilute commissions |
| Upfront commission assumption | max(USD1.75, 0.18% of each side's notional) |
| Additional slippage reserve | 2bps total round trip, an explicit unmeasured assumption |
| Minimum remaining net edge | 2bps, an explicit engineering margin |
| Minimum opening interval | 30 minutes |
| Cooldown after confirmed flat | 30 minutes |
| Opening reservation limit | 2 per rolling 60 minutes |

The predicted future MID is the *original input MID* times
`1 + expected_move_bps / 10000`; it is not recentered on a favorable current quote.
The entry is priced at current ask; the exit assumption is predicted future MID
minus current half-spread. This counts spread once. Both commissions, slippage
reserve and the minimum net margin are then required. Fees are not rounded down
to make a candidate pass. A $25,000 position generally requires roughly 40bps
plus spread and the exit-notional adjustment under these assumptions. This is an
expected-value screen, not calibrated predictive skill or a profitability proof.

Confidence is retained as the user-requested legacy threshold, not treated as a
win probability, move magnitude, risk permit or replacement for the forecast.
The model prompt requests the forecast without supplying a target profit hurdle.
Missing/invalid move forecasts are not inferred from confidence or rationale.
The existing 120-second model cadence is retained; it does NOT imply a new order
every 120 seconds. The horizon is a forecast horizon, not a minimum holding time.

## Integration and persistence

`heptatrader_btc_economics_driver.py` loads the exact pinned original base driver
with SHA256 `2a047583b092bde6c3112581b61ca18f826d5c52485bbc8f9a02f1971747bfbb`.
No code in that base is changed. The separate wrapper augments only the BTC model
schema and new-OPEN admission. Before native prepare and again before actual
submit it reacquires an authoritative quote and checks PAPER owner readiness.
It never creates a second command to replace an unknown result.

At durable PREPARED, the original command ID is recorded once in
`btc_entry_control.open_reservations`; the same pending command is excluded from
its own final frequency check. Failed/no-fill attempts remain counted
conservatively. The final check may return a definite *non-send* only before the
actual submit is called. The base then persists DEFINITELY_REJECTED and clears it
only through its unchanged authoritative reconciliation. POSSIBLY_SENT remains
pending and is never retried as a new command. Restart preserves reservations and
cooldown. Initial history is seeded from same-period, same-owner durable BTC BUY
intents and the exact most recent flat reconciliation.

## Exit and scope invariants

`should_close` is not replaced. MAX_ADVERSE_MOVE, MODEL_REVERSAL at 0.62,
HALT_REDUCE_ONLY, existing partial-close continuation, owner/session fences,
original command identity and native risk preview are retained. No minimum edge,
profit target or cooldown blocks CLOSE. Even malformed *entry bookkeeping* cannot
block base CLOSE persistence. The profile does not flatten manually, slow needed
risk exits, increase size, change IOC/MKT routing, run another canary or alter FX.

## Deployment and verification boundary

`install_btc_entry_runtime.py` requires a clean commit matching remote main,
PAPER/4002 configuration, a stopped BTC service, local and authoritative flat/no
pending state, current native readiness and the existing crypto roundtrip proof.
It installs an immutable release and one BTC ExecStart drop-in. It neither clears
halts nor restarts FX/Execution/Gateway. A precondition failure stops deployment.
It must not be used to paper over a reset, owner fence or cash attestation failure.

Unit/integration fixtures use the actual base Python logic with fake transport;
they are not broker execution proofs. A live runtime check must separately verify
service status, active profile, same period and observed future candidates.
No historical re-evaluation of an old direction-only forecast is allowed to
invent its missing expected_move_bps. Lower realised fees and higher net PnL must
be measured prospectively; deployment itself does not establish either.
