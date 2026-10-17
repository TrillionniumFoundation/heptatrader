# Pocket4 H1 STOP and original post-fence terminal-epoch integration

**Research-only, 2026-10-17.** Parents: H1 original-owner post-fence
research `3a944160` (draft PR #400), original Execution main `5a4081c`.
One test-call-table conflict was resolved without dropping either
H1 permanent-ID checks or next-Broker-epoch recovery tests.
The original account fence, Broker client-ID separation and all
non-authorizing H1 SDK/OMS/Parent/STOP/OCA checks are retained.

## New cross-module risk regression

The original Adapter's fake Broker first establishes an account-flat
post-fence global-order -> coherent-risk snapshot and an authorized
request for a **new terminal connection epoch**. After reconnect, a
fresh SDK-native H1 STP/GTC child with exact original orderRef, parent,
permanent ID, account and client is returned in the new account-wide
open-order download. The current physical risk snapshot is flat.
A leftover STOP is nevertheless an ACTIVE Broker order, so the
new-epoch proof must show `globalActiveOrders=1`,
`brokerZeroObserved=false`, and `maintenanceStopPermitted=false`.
A subsequent contradictory native Broker permanent ID must revoke the
new-epoch terminal proof and original global active-order completeness.
No later apparently good callback can manufacture a safe-stop permit.

The initial test fixture correctly failed until its original read-only
poll advanced OpenOrderEnd -> requested coherent Risk refresh.
The final test uses this actual Adapter transition; it does NOT fabricate
an authoritative snapshot or relax callback validation.

## Actual Pocket4 acceptance

Original IB SDK 1030.01, Release CMake 4.2.3/Ninja:
- Integrated full `hepta-ib-executiond` binary builds; SHA256
  `ee180338c03ac7ac0e06974e6fca6548f9bc48a6ddebed03786f6e852dcd4c3c`.
- H1/OMS/OCA/Coordinator/SDK-wire/Adapter/PAPER/startup/quote/full
  synthetic Runtime native **21/21 CTests PASS**; 43.55s; log SHA256
  `f862c1aabf8ab06ae4b085e5eac616d5d3f1177b35877f25840cefa9d65c21da`.
- Python Source **116/116 PASS** after setting test TMPDIR to a
  private host-executable evidence path (native /tmp has noexec).
  No mount alteration or suppressed assertions; log SHA256
  `5cd746df394d6cafa7cc5feb18c20fc8c629e6d90a9ebeb245b4885703d6b0b0`.
- All receipts are only on Pocket4 under
  `~/.local/state/heptatrader/operator-evidence/h1-terminal-epoch-20261017-v1/`.

## Remaining real Broker authorization boundary

No new IB session/order/cancel, no source install or production PID
restart, no risk hold release, and no model training/rollout change.
The native STOP is simulated, NOT an accepted/working Broker protective
order. Original manual-TWS unbound orders, cross-client OCA cancel/partial
fill races, disconnected FX protection and driver-death continuity
remain unproved. `nativeStopBrokerWorkingProven`,
`maySendOrdinaryExit`, `mayStopDriver` remain **false**.
Keep this source research isolated until P0 #331's authentic PAPER
Broker lifecycle and owner-independent protection requirements pass.
