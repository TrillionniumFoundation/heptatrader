# FX opening hysteresis; original risk exits stay unchanged

This scoped PAPER change addresses repeated immediate direction changes and
commission turnover, not the validity of uncalibrated remote confidence. It does
not restore the former general COST gate, require a made-up expected move, alter
BTC policy or train a financial model into the execution path.

## Fixed initial policy

Only EUR/USD OPEN is affected. The original confidence threshold remains 0.62.
Two distinct completed directional model decisions must agree, separated by at
least 60 seconds and within 300 seconds. HOLD, a below-threshold result, opposite
direction, an exposed position or stale decision resets/opening confirmation.
The base decision loop is still every 120 seconds, not an extra model call.

Open attempts must be at least 15 minutes apart, at most two within a rolling
hour. Following a fully reconciled CLOSE, wait ten minutes before another OPEN.
Deployment begins with a conservative ten-minute opening delay; this is labelled
an activation cooldown and not invented as an actual historical execution time.
Recent original sends seed the opening ledger so restart cannot start a fresh
hourly allowance. Each actual PREPARED original command reserves its timestamp
once, including an unsuccessful later submission. There is no retry bypass.

A threshold-qualified reversal while exposed still triggers the original CLOSE
immediately. MAX_ADVERSE_MOVE, HALT_REDUCE_ONLY, native quote authority, owner,
lease and original-command recovery remain unchanged. There is no minimum
holding period, no requirement to regain losses, and no cost veto on a risk exit.
The change separates closing exposure from taking a new opposite position.

## Commissions and evidence

The inspected FX fills report $2 commission per executed original order. The
public IBKR first tier is 0.20 basis point with a $2/order minimum. These rates
are not configurable fee discounts; changing trading frequency does not alter
the broker tariff. Source: https://www.interactivebrokers.com/en/pricing/commissions-spot-currencies.php

At 25,000 EUR, a typical complete opening/closing pair costs $4 before considering
price/spread effects. The new timing/confirmation values are initial engineering
choices, not optimized parameters or proof of future savings/profitability.
Do not add the minimum commission to an already reported actual fee twice.

## Persistence, attribution and deployment

The original base Python code remains byte-identical. An optional inner FX
adapter runs underneath the existing exact-request observer, preserving the
request/response/decision/original-command attribution chain and the identity of
the returned decision object. The financial-value learner is not called by this
adapter and can neither grant nor veto orders.

The installer copies immutable original base and provenance bytes, binds the new
profile and current PAPER period, and registers a new separate producer journal.
Activation requires no pending/active order and an authoritative native position
matching the recorded strategy state. A nonzero position is allowed ONLY with
complete entry identity and the exact same native quantity before restart; its
exit code is unchanged. It is never force-flattened for installation. Concurrent
exposure/period changes abort installation and restore the old unit. Execution,
Gateway, BTC and learning services are not restarted by this installer.

The reader must explicitly register the new producer manifest while retaining
previous registrations. Runtime files/configs are immutable. A new model decision
is needed after activation; a cached pre-deployment decision cannot count as the
first confirmation. This module neither clears an existing safety halt nor
creates native mutation authority. Unexpected opening-control corruption blocks
new entry, while original risk exits continue to persist.
