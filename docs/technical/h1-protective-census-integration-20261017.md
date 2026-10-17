# Pocket4 H1 stop × Broker global protective census (research only)

2026-10-17. P0 #331. This is source-only; it does NOT release an
IB order/cancel sender, original Execution binary, stop shield, or model.

The isolated merge starts from current main `814ab41`, containing
post-fence active/risk/next-terminal-epoch evidence, distinct unbound
manual TWS permIds, and read-only Broker protective-order candidates.
It joins the original H1 terminal-epoch research `f359d3a`.
Four original Adapter/test files conflicted; all six merge hunks retain
BOTH main's global permanent-ID/client scope and H1 parent/child/OCA,
latest orderStatus and monotonic callback identity checks.

The same genuine IB SDK OpenOrder emits a global Broker permId and an
H1 native SDK permId. Newly added original-Adapter adversarial tests
proved that contradictory **positive** IDs can otherwise keep the
global correlation download complete. They also rejected a missing
global permId accompanied by a positive H1 native permId.
Both negative scenarios were actually RED (CTest assertion
`!all.complete` failed) before source correction.

Now, on original-client H1 STP native capture, missing global Broker
permId or contradictory positive native/global IDs mark callback
conflict; current global order download fails closed. A positive
global permId with missing SDK-native permId can remain globally
visible but does NOT qualify as a native H1 STOP. Existing synthetic
positive fixtures now attach their original global permId.

Original H1 native scope cannot turn a foreign-client or unbound
manual TWS order into a verified original STOP. Globally visible STP,
GTC, OCA and parent identifiers are diagnostic candidate metadata,
NEVER Broker-held protection or a safe-to-stop predicate.

Original PAPER Gateway/Execution and both strategy Drivers are not
restarted or changed. Native kill-switch and durable operator hold
stay engaged; all Broker orders/cancels and new API connections = 0.
`maintenance_stop_permitted`, `mayStopDriver`,
`maySendOrdinaryExit` and `nativeStopBrokerWorkingProven`
remain false. Real venue parent/child/OCA lifecycle and
Driver-absence continuation are still unproved.

## Actual isolated Pocket4 validation

Exact baseline main: `814ab41`; H1 terminal-epoch parent: `f359d3a`.
Source merge had four conflicting files, all resolved without replacing
main's global client/unbound manual-permId/protective candidate collection.
GNU 15.2/CMake 4.2.3/Ninja Release with original IB SDK 1030.01:
full Execution binary successfully built and re-linked after the new
identity check, SHA256
`76c67c5a4cb86f3f417bda9becac9b638d552eb3afc499abad64d97de1eaae4b`.

- Selected H1/OMS/OCA/Adapter/PAPER tests: **17/17 PASS**; SHA256
  `90bbb265edecebb1031255a5d201a987b67865bf208d297052a9593e23dbc997`.
- Coordinator/startup/quote/full-runtime recovery: **4/4 PASS**; SHA256
  `56c4672cb9775f5ecc33ea25b8f80f1499cda762130cc36818dd4aa63cf31197`.
- Total **21/21 PASS**. Full runtime 28 synthetic scenarios; reported
  `kernel_network_denied=true`, `broker_socket_calls=0`,
  `order_method_calls=0`, `live_recovery_qualified=false`.
- Evidence root:
  `~/.local/state/heptatrader/operator-evidence/
  h1-protective-census-20261017-v1/`.

This is a read-only/offline integration qualification ONLY; no real
Broker parent, server-held STOP, OCA, cancel race, partial-fill or
Driver-death continuity has been qualified. Do not remove either hold,
switch running binaries, or close P0 #331 based on these results.
