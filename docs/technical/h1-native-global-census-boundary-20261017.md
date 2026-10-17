# Pocket4 H1: reconcile SDK-native STOP with Broker-global census

2026-10-17. P0 #331. **Source-only research; NOT production Broker stop
approval, ordinary exit permission, or a safe-to-stop certificate.**

Original main `23db2b2` was merged on Pocket4 with the latest H1
read-only research `cf72c67` in isolated worktree
`heptatrader-h1-protective-main-20261017`. One merge conflict in the
native Adapter C++ test invocation list was resolved by keeping ALL
original-main client/permId/manual TWS/terminal tests and ALL H1
Contract/OCA SDK-wire and recovery tests.

Isolated Release/Ninja/CMake 4.2.3, IB SDK 1030.01:
- full original Execution binary and target tests built (166 steps);
- baseline merged source: 21/21 native tests passed, 43.56s;
- baseline build/test logs in Pocket4
  `operator-evidence/h1-main-20261017-integration-v1/`.

## Further red test: one OpenOrder, two inconsistent views

The original native IB wrapper creates both Lite `brokerParentOrderId`,
`brokerOcaGroup`, `brokerTransmit` and full SDK-native Parent/OCA/
Transmit from the **same** Broker `openOrder`.
The H1 read-only diagnostic checked its full native STOP but not that
the original Broker-global protective-candidate census and Lite
observation confirmed the SAME parent, OCA group and transmit state.

New adversarial real-Adapter fixture creates a synthetically inconsistent
callback: valid SDK-native H1 STOP, but a different Lite OCA group,
Parent ID or transmit bit. Prior research returned a positive
`sdkNativeStopEchoMatches`; dedicated test **failed** as intended.
The original positive fixture was also completed with consistent Lite
fields, exactly as the actual SDK wrapper populates them.

## Fail-closed repair

In `InspectResearchH1OcaAccountScope`, require the original SDK-native
STOP, the Broker-global positive permanent-ID owner mapping and the
Broker-visible protective candidate to agree on account/client, child,
exact H1 orderRef, Parent ID, OCA group/type, transmit, STP/GTC,
contract ID, stop trigger and full quantity. On mismatch withhold the
diagnostic H1 match and remaining-volume claim. Keep Broker-global
active-risk observations separately visible; do NOT cancel a foreign
order, reduce risk based on unsigned evidence, issue an order or create
new trading authority.

**Verification disposition:** the red CTest and successful isolated C++
compile are recorded; a subsequent remote CTest invocation was rejected
by the operator tool safety gate. Therefore a post-fix test **PASS is
NOT claimed** until an authorized run produces a result. The branch is
held as Draft/unreleased.

The original Pocket4 Execution and FX/BTC driver binaries/PIDs, native
kill-switch and durable user maintenance hold are unchanged. Original
Broker orders sent by this source edit: 0. All
`nativeStopBrokerWorkingProven`, `maySendOrdinaryExit`,
`mayStopDriver`, and `maintenance_stop_permitted` remain false.
