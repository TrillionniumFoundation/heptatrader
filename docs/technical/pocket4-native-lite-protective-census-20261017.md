# Pocket4 original Broker protective census: one-callback Lite/native consistency

**2026-10-17 / source-only original IB Adapter correction.** This change
does not create, cancel, modify, retry, or protect a Broker order and does not
grant original Execution/Driver maintenance or PAPER admission authority.

## Actual issue

Original Broker-global `reqAllOpenOrders` and incremental `openOrder`
processes used the Lite fields to classify visible STP/OCA/parent candidates.
The H1 research Adapter needed the true SDK `Order` (permanent ID, Parent,
OCA, transmit, stop, quantity) to qualify even an **advisory** exact H1
candidate. Original main did not carry these complete fields in `IBEvent`.
With a single synthetic Broker callback carrying contradictory Lite and
SDK-native attributes, the previous original global candidate remained
`complete=true`. It would be inaccurate to treat this as proven Broker
protective coverage; the source inventory itself was inconsistent.

## Minimal original-Adapter implementation

1. Add `IBBrokerNativeOrderWitness` to `IBEvent` as **optional read-only**
   evidence. Populate it ONLY for genuine order-bearing original SDK
   `openOrder` and `completedOrder`; native `orderStatus` carries only
   `permId` and must not pretend to contain a complete order. IB SDK-disabled
   original build retains a default `present=false` witness.
2. For STP/STP LMT/TRAIL/OCA/parent protective candidates, original account-
   wide and incremental active-order processing compares Broker host permId,
   numeric/client order identity, original account, Parent/OCA/transmit,
   action/type/ref and quantity between the **same** callback's Lite and SDK
   views; stop-type aux price must match and be finite.
3. An inconsistent single callback invalidates the **entire** original
   account-wide protective correlation census, not just the candidate H1 row.
   Existing foreign client/manual positive-permId orders remain in global
   active-risk inventory; order transport, owner identity and all
   `maintenance_stop_permitted` / independent-protection flags remain false.
   Lite-only events continue to be treated as Lite evidence, not upgraded
   into H1 native proof.

## Independent Pocket4 red → green

- A deliberately earlier **compilable** revision with the native witness
  already present but the consistency guard absent was built with the
  original TWS SDK 1030.01, CMake 4.2.3, Release assert-enabled CTest.
  Native `hepta_ib_live_terminal_reconciliation_tests` **ABORTED** at
  `TestBrokerGlobalProtectiveSingleNativeAndLiteCannotContradict`:
  `Assertion '!view.complete' failed`. This proves a real logic defect,
  NOT merely a test failing to compile from an unavailable H1 event type.
  Red receipt:
  `operator-evidence/native-lite-shape-pre-guard-20261017/before-guard-red.log`
  SHA256 `3ddab6a57993b3586fbb5194a5ccab068bb8f3c947b748cb740913a45c93aab4`.
- With the guard in place, the **same real Adapter synthetic callbacks**,
  including **12 adversarial field mutations and one positive control**,
  passed. Full original Execution/Coordinator/Gateway/PAPER/full synthetic
  Runtime recovery **5/5 native CTests PASS**; **116/116 Python Source PASS**
  using an owner-private executable TMPDIR (system `/tmp` is `noexec`).
- Original complete IB-SDK-on Execution binary linked SHA256
  `965c0d86c97cf8a71e1ac2f8ebc65d9485b944776e385f34679f5ddfcf0add8f`.
  **SDK-off** complete original Execution binary also compiled, SHA256
  `5e0e61510a086ea89eb56e262120a1a87267bcb95ba35afda9ba3ed14bb606d8`.
- Post-fix native and source receipts:
  `operator-evidence/native-lite-shape-20261017/native-regression.log`
  SHA256 `273a9138e26b18b68f0b52425489b592cc6d5b56098e1a95c23dbe0d69600d44`;
  `operator-evidence/native-lite-shape-20261017/source-regression.log`
  SHA256 `fd6af61034a75dfef719c5651f5bc7c867e4b24d967fd534defb17952a7ecbfd`.

- Independently compiled IB-SDK-enabled native Adapter with AddressSanitizer
  and UndefinedBehaviorSanitizer (Debug, leak detection and halt-on-error).
  The same protective callback adversarial CTest **1/1 PASS**.
  `operator-evidence/native-lite-shape-20261017/asan-native-test.log`,
  SHA256 `a5f96f94e14616d050f388cdfffac552fdf13be39161aa4b7916168b564d6b13`.

## Distinct, still unproven

The entire exercise used **isolated fake SDK callbacks** and no real Broker
socket/order method. Neither a globally consistent visible `STP` candidate
nor a matching H1 `openOrder`/remaining-status diagnostic proves server-
held protection surviving Broker/Gateway/Driver interruption, OCA cancel
safety or a realized 20bp maximum-loss cap. All existing original PAPER
risk holds and the `RefuseManualStop` shield must remain intact.
This does NOT qualify Issue #331 for closure or a resident FX driver cutover.
