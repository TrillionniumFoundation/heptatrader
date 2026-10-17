# Pocket4 H1 × original-owner post-fence Broker read

Research-only integration; no production Execution or Driver changes.

## Exact source scopes

- Base H1 integration: `b46f45a`, with native SDK wire serialization,
  four monotonic Broker callback ages, original H1 Parent/STP/OCA IDs,
  and strictly non-authorizing stop diagnostics.
- Original main `4ea8f65`: original Coordinator durable account fence,
  one-lock local dispatch drain, account-global client-ID mapping and
  fresh `ReqAllOpenOrders` followed by coherent Account/Positions risk
  refresh. No new owner, terminal authority or order sender.
- Actual merge conflicts: only Adapter header observation structures and
  native test fixture; both H1 clock/permId fields and post-fence
  generation/read counters were preserved.

## Integrated adversarial tests

The same real IB-SDK-enabled Adapter fixture now asserts that a native
H1 STOP with contradictory Broker permanent-ID `openOrder` callbacks
invalidates the original post-fence global active-order download before
the risk refresh can begin. It cannot synthesize an empty account.

A separate complete, internally consistent original H1 STOP still
counts as an outstanding global order. Post-fence ordered Active/Risk
receipts must report `globalActiveOrders=1`, not zero, and must never
grant `maintenanceStopPermitted`.

Finally, after OpenOrderEnd and the risk refresh request, a contradictory
H1 orderStatus permanent ID must invalidate the already completed
active-order projection. New coherent risk data cannot resurrect it.

These assertions use an isolated synthetic Broker. No real IB request,
order, cancel, service restart or account exposure was created.

## Actual Pocket4 SDK-enabled acceptance and hard limit

GNU 15.2.0, CMake 4.2.3/Ninja Release, original IB SDK 1030.01 enabled.
Full original `hepta-ib-executiond` plus 21 native test targets compiled
in **166 Ninja build steps**. Current isolated executable SHA256:
`2b5c0a73f8a2129fd475be91d78b09555a0cc39e35f66f4706cff00e9bec9b32`.
The full **21/21 original Coordinator/H1/OMS/OCA/native SDK wire/Adapter/
PAPER profile/startup/quote/full-runtime CTests passed** (43.47 seconds);
all changes were in an isolated Pocket4 worktree.
Local private evidence:
`~/.local/state/heptatrader/operator-evidence/h1-postfence-20261017/`
with final native CTest log SHA256
`e74e6e10fe925920283777194298096394d7ad809d830f38649169a4e8647667`
and native build log SHA256
`4c1b8bcaabba4d49952602d00c85eb6dee16a71d1b776468ae28e28c643a11ac`.

The original Broker API's untagged `OpenOrderEnd` cannot prove exact
post-fence request identity; terminal downloads are not post-fence; and
unbound manual TWS orders cannot be fully reconciled from client-scoped
numeric order IDs. Even a positive test **never** sets
`terminalPostFenceComplete` or `maintenanceStopPermitted`, never proves
independent Broker STP/OCA survival, and cannot release risk or Driver
stop shields. This research result must not be promoted as live protection.
