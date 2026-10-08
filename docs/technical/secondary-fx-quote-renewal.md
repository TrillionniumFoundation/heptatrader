# Secondary FX quote renewal — isolated PAPER implementation

## Scope and safety
Applies to mixed BTC.USD primary / EUR.USD secondary PAPER broker subscriptions, not general recovery or trading. It does not create or cancel orders, change risk limits, mark stale prices fresh, or alter the 5-second quote validity policy.

Only an instrument with an actual positive IB bid and ask previously witnessed in the same connection epoch can be selectively renewed. Cold-start missing quotes stay missing. Subscription identity changes to a new request ID; the former ID cannot update authorization. The BTC quote record is preserved while the EUR record is invalidated.

## Bounded recovery path
Execution samples quote-health periodically. After at least 15 seconds without a recent FX observation, it cancels only the original FX market-data ticker, requests a new EUR/CASH/IDEALPRO/USD ticker via the existing IB CASH farm and admission fence, and waits for genuine bid and ask callbacks before exposing a new quote. It does not accept delayed tick fields or re-stamp a remembered price.

The check is limited to once every 30 seconds, with at most three attempts without a fresh quote. A definite pre-send refusal leaves the instrument unavailable and eligible for a subsequent bounded retry. Uncertain cancellation, mismatched dispatch accounting or SDK exception is fatal, not permission to silently retry.

## Release and verification gates
The isolated source is not production-qualified by successful compilation or unit tests. Before replacing a live Execution binary, obtain original IB farm/connection-epoch and old/new ticker request-ID trace, verify real new bid and ask callbacks after simulated staleness, and verify never borrowing BTC quote freshness. Check stop/restart recovery, original owner/command identity, active and terminal orders, FX/BTC positions, PnL continuity, kill switch and re-entry fencing. In particular, do not interrupt the running FX position without controlled exit or equivalent safe maintenance evidence.

The NAV FX reconciliation problem is separate. This patch neither derives a valuation from stale broker ExchangeRate nor expands the strict 1 USD cross-currency reconciliation bound.

