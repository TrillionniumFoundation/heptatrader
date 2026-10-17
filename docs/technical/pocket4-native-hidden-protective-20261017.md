# Pocket4: reject SDK-native protective orders hidden by Lite projection

**Source-only original IB Adapter correction, 2026-10-17.**
This work does not place/cancel Broker orders, admit new PAPER risk,
change FX or BTC driver policy, or authorize driver maintenance.

## Concrete gap

The account-wide original `reqAllOpenOrders` census and incremental
`openOrder` path use one Broker callback projected into two views:
Lite `IBEvent.order` plus genuine SDK `IBBrokerNativeOrderWitness`.
The earlier consistency guard ran only when **Lite** already looked like
STP/STP LMT/TRAIL, had OCA, or had parentId. A malformed Lite view
labeling an SDK-native STP/Parent/OCA as an unrelated plain MKT
skipped native consistency checks entirely, letting the Broker order
snapshot remain `complete=true` and omitting the protective candidate.

A synthetic genuine Adapter callback with same account, client,
native orderId, positive Broker permId and connection epoch was
intentionally changed to Lite MKT with zero parent/OCA while its
native SDK still said STP/GTC/Parent/OCA. Original CTest **ABORTED**
at `TestBrokerGlobalProtectiveSingleNativeAndLiteCannotContradict`
because the global census remained falsely complete. This is a real
fail-closed logic gap, **not evidence of a live bad Broker fill**.

## Narrow repair

Only `NativeAndLiteProtectiveShapeConsistent` is modified: if
**either** Lite **or** SDK-native fields indicate STP, STP LMT,
TRAIL, TRAIL LIMIT, OCA or nonzero parentId, compare the original
same-callback Lite/native order ID, permId, client, account, parent,
OCA, transmit, side, type, orderRef, quantity and stop trigger.
A mismatch revokes the complete account-order census. Source-less
native evidence is never fabricated into an H1 working-stop proof.

Adversarial tests cover both the original full-account `openOrder`
download and the subsequent incremental callback path. A separate
control with BOTH Lite and native ordinary MKT (no parent/OCA)
remains valid and does not invent a protective candidate.

## Proof limits

The latest original Broker-global permId/manual-order identity,
foreign-client order accounting and 10197 fail-closed behavior
are unchanged. The resident native+durable risk hold and
`RefuseManualStop` shield remain in place. The original H1
`mayStopDriver`, `maySendOrdinaryExit`, working-stop/cancel
and driver-death claims remain unqualified.

All compilation and tests use the original IB SDK 1030.01 in an
isolated Pocket4 CMake Release worktree. No resident executable or
original trading journal is changed.

## Actual Pocket4 red-green and compatibility receipts

Source baseline: original main `7ab7e5e`, original C++ SDK 1030.01,
CMake 4.2.3/Ninja Release with assertions enabled.

- Before repair, same original Adapter CTest **aborted** at the
  expected `!view.complete` assertion for Lite-hidden/native-STP:
  `operator-evidence/native-hidden-protective-20261017-v1/
  before-guard-red.log` SHA256
  `2b0e4cb4a741a4e2e1c32fefd69c4ae84407ba0330a30bed9102a494be3a4f82`.
- After repair, original native Adapter, Execution Coordinator, PAPER
  profile, startup hold and full synthetic Runtime recovery **5/5 passed**
  (43.52 sec). Log `native-five-regression.log` SHA256
  `2fe39cd7291f33e343ba42d20fe934ad98e085f550fc92fde68959cd810d1ae9`.
- A complete original **IB SDK-ON** Execution executable linked,
  SHA256 `956c612a78691d3067f7b2091872d36d207248afd268921b42b6f8b566008cf2`.
  The independent **SDK-OFF** executable also linked, SHA256
  `50a032d0b22aaf8feeae8dd2efc265d91c7c42e683fceb841faff7ccf1d20a88`.
- Ordinary MKT positive control and both full-account and incremental
  hidden-native-negative controls passed. Test-only fake Broker callbacks
  sent **zero** actual IB order/cancel operations. No host, systemd
  selector, account owner, native maintenance hold, execution journal,
  model or trading strategy was modified.
