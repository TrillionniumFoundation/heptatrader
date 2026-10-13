# Original EUR/USD PAPER quote expiry: strict end-exclusive boundary

Date: 2026-10-13. Status: **candidate source fix / research tests PASS /
production collector upgrade HOLD**.

## Exact bug

The original read-only source collector `laya_cell.shadow_source.valid_quote`
previously accepted a quote if

`0 < observed_at_ms <= received_ms <= min(observed_at_ms+5000, stale_after_ms)`.

That last inclusive comparison mistakenly admitted a quote at the **exact
reported expiration instant**, or at **exactly 5,000ms after the
original quote event**. The downstream original quote-derived preaction
and price validator already require `decision_ms < valid_until_ms`.
The collector and the consuming eligibility predicate therefore
disagreed at the exact TTL boundary.

This is a strict-time consistency and safety fix, **not** evidence that
the separate source outage was caused by one expired-boundary quote.
A 123,095ms original quote stream gap, 61 source failures and
correlated IB 10197 / execution epoch change remain a distinct root
cause investigation.

## Single-line, fail-closed correction

In the isolated source worktree only, replace:

`received <= min(original_quote_event+5000ms, broker_reported_expiry)`

with:

`received < min(original_quote_event+5000ms, broker_reported_expiry)`.

All source identity, strict original JSON parsing, PAPER period,
instrument/authority and numeric bid/ask requirements remain unchanged.
No quote is substituted, interpolated, re-stamped, or renewed. An
actually new original quote can be evaluated only with its new original
event timestamp.

## Negative and regression evidence

The exact new 9-case adversarial test suite was executed **before** the
one-line correction: five tests failed, including both exact-boundary
checks, "fresh quote recovery" and the end-exclusive source contract
identity assertion. Those failures are retained as the reproduced bug.

After the correction: **9/9** new cases passed. Existing
`test_shadow_data_behavior`, `test_source_json`,
`test_source_workflow_commands`, `test_fx_verified_cache_480`,
`test_fx_original_health_sustained` and `test_fx_ridge_original`
also passed. All **77/77** tested cases are green.

No original quote file, original SOURCE 480-cycle status/receipt, IB
Gateway, canonical Owner/Witness/LedgerWriter, live monitor or FX/BTC
PAPER driver was modified or restarted.

## Release constraint

This code lives only on a private branch. It must not be hot-deployed
into an active source collector while the original sealed 480-cycle
experiment is running. Any parallel gateway parser candidate test
that previously expected exactly-at-expiry acceptance must be updated
to the same strict end-exclusive contract **before merging branches**.
Then run actual isolated source callback, loss/recovery and first-party
readiness/rollback regression before any explicit production cutover.

**Trade/model and production admission: HOLD.** Source stream continuity,
actual broker execution economics, original FULL product authority and
account Flex/NAV reconciliation remain independent blockers.
