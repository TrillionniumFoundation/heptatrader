# Pocket4 IB global conditional trigger census (2026-10-17)

**Read-only original Adapter correction.** This is a source fix, not a
Broker protective stop, new order type admission, exit authorization, model
promotion, or safe-to-stop-Driver certificate.

## Observed gap and actual failing fixture

The original `IsIbVisibleProtectiveCandidate` in
`HeptaTrade/adapter_ib/ib_gateway_adapter_event_state.cpp` treated only
STP, STP LMT, TRAIL and TRAIL LIMIT (plus nonzero Parent/OCA) as
potentially conditional. IBKR officially documents `MIT` and `LIT` for
CASH/forex and `STP PRT` for futures. A real account-wide
`openOrder(Submitted)` may have such an order **without** Parent or OCA.
These types may open new risk as well as close existing risk: the
diagnostic must never call them proven protective stops.

A new negative source-bound C++ Adapter fixture uses the existing
original IBAPI-enabled Adapter, with a separate foreign Broker client,
same exact account and a native SDK Order and Lite event from the **same**
original callback. Test-only first commit `d5b7177` was actually built
on Pocket4 with IB SDK 1030.01; `hepta_ib_live_terminal_reconciliation_tests`
**aborted at the newly introduced positive visibility assertion** because
the native order failed to enter the global conditional candidate census.
The red test sent **zero** Broker orders, cancels or account mutations.

The fixture then requires, for each MIT/LIT/STP PRT type:
- Valid matching Native/Lite callback + positive Broker permId is in
  the global candidate census (never granted stop-working authority).
- A Lite projection forged as ordinary MKT while the actual native SDK
  Order retains the conditional type invalidates the full Broker order
  snapshot in both full-download and incremental paths.
- Existing ordinary MKT with neither parent nor OCA stays ordinary.
- Foreign-client conditional orders remain globally visible but never
  establish the original H1 owner identity.

## Bounded fix and release semantics

A single private `IsIbConditionalTriggerType` helper adds
`MIT`, `LIT`, and `STP PRT` to the existing original known conditional
types. The original account-wide and incremental visible candidate
decisions, and native-vs-Lite same-event mismatch check, use the **same**
predicate. Original quantity, Broker permId, client, account, Parent/OCA,
trigger and send semantics are unchanged. The check is conservative and
may refuse a contradictory Broker download until a genuine coherent
refresh; it does **not** create a new risk-accepting path.

**No existing Execution binary, Broker Gateway/client, OMS event, native
or persistent new-risk hold, FX/BTC strategy service or model is changed
by this research/source test.** The resident original Executor remains
on its pinned binary. This does not by itself qualify independent stop
coverage during Driver absence (P0 #331).

Official references:
https://www.interactivebrokers.com/campus/?p=195739&post_type=ibkr-api-page
https://www.interactivebrokers.com/docs/tws-api/ref/order

## Pocket4 red/green and broader regression receipts

Original native C++ SDK 10.30.01 Release CMake/Ninja test on the
isolated Pocket4 worktree:
- Test-only `d5b7177`: original `hepta_ib_live_terminal_reconciliation_tests`
  **failed**, actual assert `good.visibleProtectiveCandidatesByPermId.size()==1`.
  Red log SHA256 `ae04264d71377680215e31dbe45852a91c278fe15b8ec0bd64064de09716c622`.
- Fix `ef5f84e`: same genuine Adapter fixture **passed**, including
  type visibility, native/Lite contradictory global and incremental
  fail-closed cases and ordinary MKT control.
  Green log SHA256 `731c3ef521cd67749cd1584c90b5f7519648b822293a3ee9d233a7ed1b24ec5c`.
- Complete original `hepta-ib-executiond` binary built successfully
  under an **isolated** path, SHA256
  `2e6c8f8a687facb7736ef32419cba93849419aebf2cce6771b8a8b7bf88c70ca`.
  Actual original Coordinator, Adapter, PAPER Profile, startup
  recovery hold and full synthetic Runtime recovery: **5/5 CTests
  passed** in 43.53s, log SHA256
  `09303308a727da80e9fdd1ab4256605cbcc39c645c88b1dfc702c9dd03a02d2c`.
- **116/116 Python Source tests passed**, proper owner-local
  executable TMPDIR on Pocket4 to accommodate its noexec /tmp;
  Source log SHA256
  `a0ca723e64c223c105a70ddf66e631f79817b3f5c329834b8d2c3d5468335225`.

All receipts remain private to the single Pocket4 host in
`~/.local/state/heptatrader/operator-evidence/mit-lit-census-20261017/`.
No IB Broker client/session was opened by these tests; no order,
cancel, source credential, service restart or risk hold release.
The active Execution remained PID 222401 (historic restarts 367);
original FX/BTC Drivers stayed active, current strategy PnL was
complete with no strategy or Broker positions and net -$2571.0034756525.
The existing native risk switch and persistent operator hold stayed
engaged, `may_stop_driver=false`. In particular **this fix does not
qualify the H1 PAPER broker-held stop or ordinary OCA exit**.
