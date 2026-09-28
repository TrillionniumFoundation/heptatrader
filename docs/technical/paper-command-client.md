# Durable local PAPER command client

Status: source client, not a resident strategy or deployment claim.
Implementation: `research/examples/paper_command_main.cpp` and the existing
`NativeStrategyClient`; tests: `tests/research/native_client_tests.cpp` and
`tests/research/paper_command_behavior.py`.

## Boundary

`hepta-paper-command` is a thin process adapter over the existing native client.
It adds no Broker connection, account selector, lease store, order ledger,
authorization implementation, request codec or autonomous scheduling. The named
Gateway and its Execution service remain responsible for PAPER account/profile,
risk, price freshness, durable send identity and terminal reconciliation.
The utility cannot establish PAPER mode by its executable name. A caller must
verify the selected runtime and scope before scheduling any operation.

The explicit `PreparedOrder::MarketCash` factory is restricted to EUR.USD CASH
on IDEALPRO, BUY/SELL, DAY, positive quantity at most 25,000, a finite positive
reference price and a positive expiry. It does not change the prior LMT-only
constructor or futures mapping. Copies of requests do not mutate the proposal.

## Operations

Every operation takes absolute `--socket`, `--token-file` and `--directory`
paths. Account/login credentials are not arguments. The native client reads the
session token and binds UID, socket and exact credential through its established
recovery contract.

`prepare-open` also takes `--call-id`, `--side`, `--price` and `--expiry`. It
performs one native preview, takes the returned ID and permit, then persists the
exact prepared request using HSR1. It never submits. `prepare-close` takes only
`--call-id` in addition to the common paths and delegates the exact authoritative
flatten preview and persistence to the same client.

`submit` requires `--command-id`, restores the original HSR1 file and performs
one bound call with unchanged ID, expiry, fields and permit. `inspect` additionally
takes a distinct `--call-id`, restores the original record and sends only the
original-command status query. Missing records are not fabricated or recreated.
Only stored placement and flatten operations are accepted by this adapter.

A successful response field `ok` describes successful preparation/persistence or
transport, not a fill. Preview permits remain inside the native private outbox;
successful preparation does not echo them to stdout. Rejections and uncertain
transport retain their original result and reason. The adapter does not retry,
re-preview or allocate a replacement mutation identity.

## Controller integration still required

This utility is not an indefinitely running driver. A future local controller
must durably mark a possible send before invoking `submit`, query the same ID
after interruption, require actual terminal order/execution and current position
evidence, and preserve that uncertainty across restart. A missing record or a
momentarily flat snapshot is not evidence that a prior send did not happen.
Lease renewal, single-instance ownership, bounded process/output resources,
manual pause semantics and long-run storage management remain controller duties.

## Build and optional install

The root build creates `hepta-paper-command` alongside the existing native SDK
adapter. The independent `UserPaperClient` install component installs only this
executable when explicitly selected; it is excluded from default production
installation. It does not start a service or change the selected runtime release.
No test success here claims a Broker campaign, an autonomous driver, or LIVE.
