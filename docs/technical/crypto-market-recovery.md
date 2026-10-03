# PAPER crypto market-order recovery

Status: implemented and offline-tested; production activation and Broker acceptance are separate.

## Native contract

New BTC/PAXOS/USD buys are MKT/IOC with a USD cash budget. HTT1 adds optional
`cash_quantity` tag 35; HEX1 v11 adds optional `CashQuantity` tag 43.
The public order has exactly one size. The internal cash representation uses
base quantity zero, while the IB SDK sends `cashQty` and leaves `totalQuantity`
unset. Cash is finite, positive, cent-aligned, no more than USD 150, and bounded
by the fresh authoritative ask's remaining nominal 0.001 BTC capacity.
This is a quote-time estimate, not a hard maximum on actual MKT filled BTC.
Actual fills must be adopted even when price improvement changes base size;
exact reduce-only MKT/IOC SELL uses the authoritative owned position.

Cash participates in request storage, canonical identity, preview and audit
bindings. Cash absent/zero leaves legacy quantity request bytes and hashes
unchanged. Old LMT requests remain recovery inputs, not new-order templates.
The existing recent-order projection still rejects ambiguous execution evidence;
it must not invent a consolidated execution ID, fill quantity or PnL.

## Recovery invariants

PendingCancel and PreCancelled do not prove cancellation. Repeat cancellation
is suppressed. Error 10148 avoids the global fuse only when current-epoch,
client/account-compatible evidence agrees with locally pending cancellation.
An uncorroborated diagnostic retains the protective behavior.

A rejected command status after a possible send cannot alone clear pending.
An existing pending command cannot be replaced during order preparation.
Unknown cash-order remaining quantity is not zero-remaining proof. BTC post-fill
owner release waits for the original correlation's terminal AND execution
witnesses; a status callback arriving first must not orphan the later execution.

The resident crypto driver requires a completed entry plus original-entry-bound
reduce-only exits whose actual quantities close that entry. Original unresolved
orders must be reconciled before a new canary. Full Broker round-trip evidence
precedes resident startup; Execution-only restart acceptance follows it.

## Validation and activation

Production targets build with IB API enabled. Native CTest: 49/49 passed.
Driver tests: 38/38; protocol/reference tests: 4/4; MCP tests: 11/11.
Tests cover changed-cash same-command rejection, durable zero-base-size replay,
unknown-size completion, terminal/execution ordering, cancellation diagnostics,
partial exits, exact over-nominal recovery reduction and legacy request restore.
These tests are not a live Broker or PAPER fill certificate. Running services,
credential/profile bindings and the current release were not changed by this
patch. A final additional production-state read was unavailable; do not promote
an earlier observation into a newer terminal or restart acceptance result.
