# H1 native stop orderStatus time-order protection — Pocket4 2026-10-16

**RESEARCH ONLY / no order send / no live driver upgrade.** This is an incremental
fix stacked on the frozen original H1 SDK echo research branch, not a trading release.

## Actual defect reproduced with the real SDK-enabled Adapter

The existing original `HeptaIBGatewayAdapter::ApplyActiveCorrelationEvent`
bounded H1 stop diagnostic recorded the latest `OrderStatus` received in
**processing order**, without rejecting an event whose original
`queueIngressMonotonicNs` was **older** than the status already retained.

The source-bound synthetic Broker fixture reproduced the following sequence
for one frozen EUR/USD PAPER H1 native Stop, in the **same Broker connection
epoch, account, client, order ID, and original parent/child H1 identity**:

1. Valid real-shaped `openOrder(Submitted)` native STOP echo.
2. Valid `orderStatus(Submitted)`, filled=0, remaining=original frozen
   quantity, matching positive Broker permanent ID, monotonic ingress 555100.
   The advisory `sdkNativeStopUnfilledRemainingMatches` is true.
3. **Later**, contradictory `orderStatus` with a *different permanent ID*,
   ingress 555300. The advisory full-remaining match becomes false.
4. Replay the original valid-but-**older** ingress-555100 status.

At the previous research HEAD, step 4 erroneously left the Adapter's
account-wide active-order snapshot complete. The new adversarial
`hepta_ib_live_terminal_reconciliation_tests` therefore **failed at its
explicit expected-incomplete assertion** before the fix:
`TestH1StopOldStatusCannotRestoreAfterBrokerPermIdConflict`.

Separately, a test using a genuine partial fill already caused the Adapter
to invalidate the wider order/position snapshot. That initial negative test
proved a **previously implemented protection**, not a newly found flaw;
this regression correctly focuses on the *later contradictory permanent
ID* case instead. No actual IBKR PAPER order was sent during either test.

## Minimal source change

In the existing `ib_gateway_adapter_event_state.cpp` callback path,
before replacing the bounded latest status, compare the **actual ingress
timestamps** of old and incoming status for the same native stop ID. If
`incoming.queueIngressMonotonicNs <= lastStored.queueIngressMonotonicNs`,
record a callback conflict and invalidate the **entire current active-order
correlation snapshot** as `H1_NATIVE_STOP_STATUS_OUT_OF_ORDER`.
An equal-timestamp replay also refuses to qualify; a later valid
monotonic status is unaffected.

This preserves the original single IB Adapter / Execution owner, full
account risk checks, client/session fences, no-send recovery, and
`RefuseManualStop`. The H1 fields
`nativeStopBrokerWorkingProven`, `maySendOrdinaryExit`, and
`mayStopDriver` **remain false even on positive diagnostics**.

## Actual Pocket4 negative-to-positive verification

An isolated Pocket4 build was configured with system **CMake 4.2.3**,
GNU compiler, **IB SDK 1030.01 enabled**, and Ninja Release. The
existing H1 branch and current resident Driver/binaries were untouched.

- Test-only commit `df8d645`: exact real-Adapter CTest
  **ABORTED** at the stale-rollback assertion, thereby demonstrating
  a regression that the old code genuinely did not handle.
- Research fix commit `32c066d`: rebuilt the exact same native test;
  **`hepta_ib_live_terminal_reconciliation_tests` PASS**.
- The same isolated build also compiled all 14 H1/stop/OCA/OMS CTest
  targets; **14/14 PASS, zero skips, zero Not Run**.
- No new IB session, order, cancel, service restart, model promotion, risk
  limit override, original journal write or Gateway restart.

Scope limits: this proves chronological integrity for a research-only
native callback diagnostic, **not** a Broker-held protective stop. There
is still no attested automatic independent FX risk exit surviving actual
strategy Driver death, and no validated cancel/OCA race protection.
P0 Issue #331 and the controlled cutover blockade remain open.

## Additional identity-conflict regression: monotonic callbacks can still lie

At the original `7d9293a` research HEAD, a newer conflicting positive
`Broker permanentId` could be temporarily stored and followed by an even
newer apparently valid `Submitted/0-full` callback on the **same numeric
child ID, client, account and connection epoch**. Because both timestamps
increased, the first time-order repair did not invalidate the global order
projection, and the later valid-looking event could regain the diagnostic
full-remaining match. Test-only commit `6b6f88a` replayed the contradictory
newer permId and then a later good one; the **actual SDK-enabled Adapter
CTest failed on the expected-incomplete global inventory assertion**.

Minimal additional fix `89636dc`: if a same-scope H1 `OrderStatus`
provides a positive Broker permanent ID that differs from the original
account-wide SDK `openOrder` permanent ID, immediately record callback
conflict and invalidate that **generation's entire** correlation snapshot
(`H1_NATIVE_STOP_STATUS_PERMID_CONFLICT`). Subsequent old **or** newer
good-looking callback cannot recover the same generation; it takes a new
authoritative Broker epoch and all original risk/order/terminal constraints.
A missing/zero permanent ID is never accepted as affirmative proof and does
not itself invent Broker identity.

A pre-existing Adapter test erroneously attempted to reuse the same
connection after intentionally injecting a contradictory permanent ID.
Because conflict is permanent for that same epoch, the original test
could never honestly requalify. Commit `420c55f` separated the
adversarial permId fixture from ordinary account/source positive fixtures
instead of resetting the risk conflict or weakening the checks.

**Pocket4 release-guarded isolation and final evidence:**
- IB SDK 1030.01-enabled Release C++ Adapter target rebuilt.
- Real Adapter `hepta_ib_live_terminal_reconciliation_tests` passes, 1/1.
- H1/OMS/Bracket/OCA native CTest group passes, 14/14.
- Original source file hash changes only the research Adapter cache; no
  production order-send API, actual IB session, stop, cancel, or broker action
  introduced. `nativeStopBrokerWorkingProven`, `maySendOrdinaryExit`,
  `mayStopDriver` remain false.
- Local owner-private evidence under
  `~/.local/state/heptatrader/operator-evidence/h1-native-status-identity-v2/`:
  `h1-oms-oca-14-tests.log` SHA256
  `8814f52a139dc391059db388e0816eba9556c286f2669a2923e120cbe66649a7`;
  `native-adapter-test.log` SHA256
  `dcb8837b3886e018cccccf3b2dbae2cc19b48cf8f2f3017808b1009c1be0d918`.
- The *existing* original Execution PID 222401 / cumulative restarts 367
  remained unchanged, native + durable operator maintenance holds stayed
  engaged, and original FX/BTC PAPER drivers were not switched. Current
  underlying IB owner returned authoritative empty active orders and
  positions and current strategy PnL observer was complete.

**Future real gate:** original Execution-owned PAPER Parent/STP/OCA place,
cancel and partial-fill lifecycle with authentic positive Broker source,
safe outage/reconnect and Driver-death continuity. These tests do not
license a production rollback, a new PAPER order or releasing native
protection.
