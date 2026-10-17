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
