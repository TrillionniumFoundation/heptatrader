# Pocket4 IB PAPER original-quote transport: isolated combined gate

**2026-10-13 — research/source candidate; production cutover HOLD.**

This one new isolated branch combines two original quote intake fixes
without touching the existing live source collector, IB Gateway, trader
or the running 480-cycle original health evidence.

## Source handling and expiry

- Reject malformed, truncated or duplicate-key gateway JSON and nonfinite
  numeric values *before* recording an original IB PAPER quote.
- Preserve allowlisted reason codes for real native tool failures, including
  `IB_PAPER_REALTIME_MARKET_DATA_UNAVAILABLE_10197`,
  `EXECUTION_EVENT_RESPONSE_READ_FAILED`, stale/unready source and
  invalid subscriptions. Never print arbitrary untrusted gateway text.
- Require `subscription_state=active`, original IB source,
  authoritative=true, stale=false, EUR.USD, valid bid<=ask and strictly
  causal original quote event/expiry data.
- Accept a quote only while
  `observed_at_ms <= received_ms < min(observed_at_ms+5000,stale_after_ms)`.
  **At the exact expiry boundary the quote is expired.**
- Recover from a failure only upon receiving an actually new, fresh,
  original active-subscription quote. Never refresh or reuse an expired
  original event timestamp, and never trigger broker resubscription,
  account operations, model inference or orders from the parser.
- Preserve the original fixed `market.get_quote` read-only call;
  no new broker API entry points or revised-history substitutions.

## Conflict that was resolved

The earlier standalone parser candidate (separate uncommitted worktree)
had one test expecting an exactly expired quote to be accepted.
With the strict end-exclusive source contract, that test originally
failed **1/28**. The test was corrected to require fail-closed expiry.
The original other worktree was **not modified**. This isolated
integration is intended to make both source correctness branches
mergeable without silently reversing the strict TTL fix.

The earlier strict-boundary branch independently demonstrated five
failed cases against the untouched original collector before the
single-line expiry correction. Those negative run logs are retained.

## Actual pocket4 tests

- New malformed/fatal transport parser suite: **28/28**.
- Original expiry boundary suite: **9/9**.
- Existing behavior/JSON/workflow/cache/health/Ridge/gateway tests:
  **102/102**.
- **Overall 139/139 green**. Tested against the candidate source only;
  this is not evidence of a successful original live broker transport
  recovery or a sustainable production quote stream.

Private operator regression receipts reside under:
`~/.local/state/heptatrader/operator-evidence/fx-original-quote-transport-safe-20261013/`

The original 123,095ms source gap, 61 captured error events,
two correlated IB 10197 events, changed execution epochs and the
previous 0/4 censored forward cohort remain unchanged. The gap
started before 10197, so the sole root cause remains unproved.
The active 480-cycle original-source monitor has real HOLD episodes
and at least one >30s pre-next-publication freshness breach; no
source CUTOVER is authorized by this parser regression.

## Next real admission gate

After existing 480/480 immutable source evidence finishes, perform
isolated native transport fault injection with malformed/partial
replies, execution epoch change, actual subscription recreation,
correct strict quote expiry and explicit rollback. Require an
independent physical-host original byte reparse and exact fresh-quote
latency under the original 25% CPU budget. Do not bypass source TTL,
restore false samples, or alter the current trade strategy.

Product native Owner/LedgerWriter/Witness, original trader behavioral
propensity, confirmed broker fill-time slippage/funding and account
Flex/Activity/NAV are still independent HOLD gates.
