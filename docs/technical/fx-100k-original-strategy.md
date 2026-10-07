# EUR/USD PAPER $100k notional original-strategy lane

This lane restores the resident EUR/USD strategy's original entry semantics:
BUY or SELL is eligible from one completed model decision at confidence >=0.62.
The later FX opening hysteresis/cooldown adapter is not active. Existing
MAX_ADVERSE_MOVE, model-reversal CLOSE, HALT_REDUCE_ONLY, owner fencing, fresh
quote checks, one-active-order limit, durable original-command reconciliation,
PAPER-only mode and LIVE prohibition remain unchanged.

Each new EUR/USD OPEN targets at most USD 100,000 notional at the authoritative
reference price. Whole-EUR quantity is floor(100000/reference_price), capped at
100,000 EUR, so preview notional never exceeds the requested USD ceiling. The
exact quantity is passed to the native PAPER command client and persisted in the
pending command; reconciliation checks that command-specific quantity rather
than assuming a fixed 25,000-unit fill. Existing positions are never resized.

The user PAPER execution profile permits at most 100,000 EUR order quantity,
USD100,000 reference notional and 100,000 EUR gross position. These are PAPER
risk ceilings, not LIVE limits. The BTC lane retains its separate exact USD25,000
cash policy and is not widened. System-level max active orders=1 and max
orders/minute=2 remain execution safety controls; the removed restrictions are
the later strategy-level 15-minute spacing, 10-minute post-close cooldown,
two-opens/hour limit and two-signal confirmation.

At roughly EUR/USD 1.12, a new order is approximately 89,000 EUR and close to
USD100,000 notional. This reduces the percentage impact of the $2/order minimum
commission; it does not guarantee lower all-in cost or positive PnL.
