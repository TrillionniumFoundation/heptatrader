# Issue #331 — Execution-owned protective FX stop: bounded research prototype (2026-10-15)

## Purpose and exact scope

The original strategy driver was intentionally stopped for ~4h34m with
a real IB **PAPER** EUR/USD position still exposed (see Issue #331).
A systemd restart setting does not protect positions during that interval.
The current Execution owner has authoritative positions, order identity,
OMS and an independent poll loop, but **does not have a released,
continuously armed broker-side stop implementation**.

This change provides a non-dispatching contract prototype inside the existing
`HeptaTrade/execution/` tree. It does **not** grant a service-stop permit, place
orders, enable LIVE, cancel an order, or change existing PAPER policy. There is
no new standalone agent, daemon, job scheduler, or governance framework.

### Implemented contract, with CTest negative checks

`ib_paper_execution_stop_research.h` validates synthetic, coherent PAPER
EUR.USD CASH exposure and constructs an **unsent** counter-side
`STP` intent. It refuses:
- LIVE / wrong account / wrong original broker contract ID 12087792,
  unknown original entry execution ID or owner substitution;
- account, position, pending-command or open-order evidence that is
  incomplete, conflicting, non-fresh, or from mismatched broker epochs;
- missing no-new-entry owner fence, non-integer / >100k EUR exposure, stale
  prices or a stop that would already be effectively triggered;
- forged/changed client, permanent order ID, identity-bearing orderRef,
  broker status, full original quantity, stop price, trigger method or
  connection generation.

The fixed shadow threshold mirrors the **currently pinned FX Driver**
`MAX_ADVERSE=0.002` (20bp), with minTick supplied by an attested
future broker contractDetails call. Rounding aims to trigger on the safer
side of the threshold; it **cannot** guarantee a 20bp realized loss cap.

A non-dispatching synthetic receipt sequence requires an original immutable
command key before treating any possible send as recorded. Duplicate sending
is always disallowed. A disconnect invalidates previous "working" evidence;
reconfirmation requires a new complete broker epoch, same permanent order
ID and the same original execution/contract/quantity. Cancellation, partial
fill or unresolved send revokes shadow evidence. All paths return
`MayStopDriver() == false`.

### Actual Pocket4 build verification

The new test binary is registered as
`hepta_execution_owner_stop_shadow_tests` in the existing core CTest suite.
Pocket4 native CMake **Release** build and its CTest case pass, as does an
independent `g++ -std=c++11 -Wall -Wextra -Werror -pedantic` build.

These are synthetic tests: they neither assert broker acceptance nor model
future prices, nor do they prove driver-stop risk protection.

## Real blocker before even a PAPER canary

The existing `OrderIntent` / Execution RPC has no protected bracket
`parentId`, `transmit` and OCA linkage. Current EUR/USD PAPER profile
restricts open orders to `MKT`. A naked `STP` is not a *reduce-only*
guarantee on IB CASH: it may later **reverse** an account whose position
was already closed by ordinary trading. Treating a broker "Submitted"
status as proof of safe maintenance would be an error.

IBKR may simulate stops, which may fail or trigger unexpectedly during
market-data outages. Even an IB acknowledgement is not an unlimited
loss guarantee. Official reference:
https://www.interactivebrokers.com/docs/tws-api/ref/order
https://www.interactivebrokers.com/docs/tws-api/doc/orders/place-order/adding-a-profit-taker-and-stop-loss

## Finite integration and acceptance path — no live modification in this PR

1. **Extend existing Execution owner atomically**, not another driver:
   attach bracket/stop to the original entry before its first risk-increasing
   broker send, or prove an equivalent execution-side independently protected
   ownership and open-order lifecycle. Original OMS command, service epoch,
   position generation, orderRef/permanent ID and the broker's exact account
   must bind the entire order group. A separate after-fill stop leaves an
   unprotected entry window unless explicitly mitigated.
2. **Protect every ordinary exit:** broker-native OCA or a verified atomic
   cancel/flatten protocol must prevent protective-stop overfill and later
   reversal; uncertain cancellation must not be marked zero-fill.
3. **Real isolated PAPER canary** with a new bounded original command
   while existing positions are unaffected: broker accepts the group,
   prove working/trigger/cancel/partial states through real callbacks, then
   deliberately kill the *strategy* only while protective cover is attested
   and confirm the broker-side stop still works. Repeat through Gateway
   reconnect, Execution restart, unknown submission, source failure and
   quote gaps; verify exact order identity, no duplicate sends, no live
   modifications, native safe recovery and rollback.
4. **Only after owner and broker attestations** consider a controlled release
   with prospective signed safeguards. An active service, a synthetic
   CTest pass, or this research header cannot close Issue #331.

**Disposition: research-only / fail-closed / no transport / no auto-promotion.**

## Follow-up: shadow parent+child native-wire binding and actual OMS disk replay

The non-dispatching shadow now covers the official TWS parent-child transmission
sequence **before the next real IB API integration**:

- An original flat PAPER owner with one exclusive risk-increase decision,
  exact account/conId/client ID, fresh same-epoch position and order snapshots,
  and reserved consecutive native order IDs produces an **unsent** parent
  `MKT/DAY/transmit=false` and opposite-side child
  `STP/GTC/parentId=<original parent>/transmit=true`.
- A fake TWS stages the parent without executing it, then simulates final-child
  group release. Fake driver death retains the fake working stop. A fake TWS
  restart **before** the child clears the held parent and prohibits replay.
- New CTest `hepta_execution_bracket_shadow_tests` rejects wrong account,
  owner, conId, original command, stale mixed broker epochs, active orders,
  invalid grid/quantity, changed child identity, wrong permanent IDs,
  partial parent or stop, unknown trigger method, disconnection, and any
  ordinary exit lacking verified OCA protection. The final "protected" label
  remains permanently false for real trading, regardless of fake status.
- `hepta_execution_bracket_shadow_oms_tests` exercises the **existing
  production OmsJournal implementation** on a disposable isolated filesystem
  path. An actual critical `order_intent` is synced before the fake venue
  can stage the parent; a SHA256 request fingerprint binds original command,
  exact parent and child IDs, owner, account, quantity, stop and transmit
  modes. Mutating stop price or child order ID invalidates the original
  persisted fingerprint. On reopen, the exact command is replayed, but an
  untransmitted parent is NEVER interpreted as protection or an excuse to
  send an extra child. The test creates **no resident OMS event**.
- A mock order `Submitted` and a matching permanent ID prove nothing about
  future stop triggering or safe manually initiated close. Broker-level
  parent/child atomicity, fill timing, FX GTC/STP availability, actual IBKR
  stop simulation, unknown child delivery, and OCA overfill protection still
  require original-owner, exact-account, isolated PAPER callbacks.

This work deliberately does **not** add broker-send calls, order transport,
parent/child fields to the production RPC, or an authority to stop Driver.
IBKR specifically notes that untransmitted TWS orders can be cleared on TWS
restart; the shadow state machine does not treat them as durable protection.

Verified Pocket4: CMake Release build; CTest of shadow bracket, shadow OMS,
original single-stop, and existing broker PAPER profile targets; C++11 strict
warnings; ASan/UBSan for pure shadow bracket; no Gateway/Driver/Execution
restart and no order network messages. This work is still **Draft** and
**cannot** close Issue #331.

## Live PAPER read-only contract/tick observation — not order approval

A one-shot IBKR **PAPER-only** connection at local port `4002` was made with a
separate bounded read-only client identity. `managedAccounts` confirmed PAPER
(`DU...`, exact account never written into the report). The network egress
method was restricted to TWS `START_API=71`, `REQ_CONTRACT_DATA=9`, and on
a second independent session `REQ_MARKET_RULE=91`. No order, market data,
portfolio mutation, or historical request was sent.

The broker returned:

| Read-only IB result | Exact evidence |
| --- | --- |
| Contract | `EUR / USD / CASH / IDEALPRO` |
| Broker conId | `12087792` |
| ContractDetails.minTick | `5e-05` |
| ContractDetails.marketRuleIds | `3188` |
| MarketRule 3188 price band | `lowEdge=0.0; increment=5e-05` |

Receipts live in the private Pocket4 evidence root:
`~/.local/state/heptatrader/operator-evidence/owner-stop-shadow-20261015-r1/`

- `ib-paper-contract-details-probe.json`, SHA256
  `424f4a2355e28ff52614078ee6769beb58080a3151d8244a225ca25c6a31c8b6`.
- `ib-paper-market-rule3188-probe.json`, SHA256
  `ab15fdc9ba3e7ad2e850b863ea14d8af47951875ec305f905b7170eafe72535e`.

The exact-conId *shadow* stop and bracket now **refuse** a supplied price
increment other than `0.00005` rather than silently using the old synthetic
`0.00001`. If IB contract rules change in the future, these tests must be
re-qualified from an independent current broker callback. A verified tick and
market rule **do not** attest that the PAPER account accepts native GTC/STP,
that the stop will be held by IB, or that an OCA/child prevents reversal.

## Broker-paired shadow release and OMS evidence

Added `hepta_execution_bracket_shadow_tests` and
`hepta_execution_bracket_shadow_oms_tests` to the canonical core CTest lane.
The shadow parent is MKT/DAY/transmit=false, and the child stop is
STP/GTC/transmit=true with the exact parentId and consecutive broker-order-ID
reservation in the same synthetic IB client. A fake TWS supports staged parent
then child release, failure before release and driver death. Those are **mock
behaviors**, not proof of TWS implementation or gap-free transmission.

The existing `OmsJournal` class is exercised against a **disposable local
ledger**, not the resident journal. The original critical intent is synced
before simulated staging. SHA256 of a length-delimited canonical group binds
parent and child order IDs, direction, reference stop price, quantity, account,
owner, client ID, broker epoch and transmit flags. Tampered child price/ID
cannot replay the original intent; the journal is reopened after a simulated
restart. A lost untransmitted parent cannot be redispatched from the local
journal alone.

All paths remain research-only with no broker transport, no maintenance
permission, no production order profile changes, and no automatic strategy
selection. The final IBKR PAPER group, actual parent partial fills, failure
unknown after child transmit, ordinary exit/OCA interactions, and persistent
broker stop across a *real* Driver outage remain **unproven**.

## Further negative evidence: unknown child transmission and original notional

The OMS shadow test now writes **two real critical records** on its isolated
journal path: a previously durable `order_intent` before the held parent and a
`place_send_attempt` *before* the simulated final-child transmit. The intent
and attempt share the same immutable SHA256 bound group. After a simulated
crash with the child potentially already released, exact journal replay shows
one intent plus one send attempt; it must recover as **UNKNOWN**. There is no
new broker submission, no forged cancellation, no zero-fill claim and no
maintenance permission. This is still synthetic venue evidence; testing the
real uncertain-release outcome requires broker callback data in PAPER.

Additional shadow refusal requires the original EUR/USD **USD 100,000
reference-notional budget**, not just a <=100,000-EUR order quantity, and
rejects unavailable / overflowing broker numeric order IDs. These are not
production field/behavior changes: the original Execution send path remains
unchanged in this PR.

## SDK-native order objects and actual PAPER contract capabilities

The previous shadow objects did not exercise real TWS SDK `Order` and
`Contract` types. Added `HeptaTrade/adapter_ib/ib_native_bracket_research.h`
which accepts only a bounded verified PAPER pre-entry observation and produces
real, in-process **IB SDK C++** native types. Tests are registered only if the
existing `HEPTA_ENABLE_IBAPI=ON` build has already qualified the SDK and the
Intel BID Decimal library. Production `OrderIntent`, Execution RPC,
`PlaceOrderWithResult`, `EClient::placeOrder` and all live gateways are
unchanged. No network sender is exposed from this header.

The native pair sets:

- EUR/USD `Contract` conId 12087792, IDEALPRO/CASH/USD.
- Explicit broker account, original command-bound orderRefs, same broker
  client and consecutive reserved order IDs.
- Parent `MKT/DAY/transmit=false`; child `STP/GTC/parentId=<parent>`,
  `transmit=true`, exact reverse action, full original quantity and
  `auxPrice` snapped to the independently witnessed 0.00005 tick.
- A **hypothetical** future stop OCA group, `ocaType=1` (cancel-with-block).
  That group's ability to coexist with bracket activation, and its exact
  behavior under ordinary strategy exits, are NOT verified by Broker.

A second **non-dispatching** native SDK object `ProposeUnsentOcaExit` refuses
ordinary close unless the original working stop already has fresh Broker
attestation of the same group/account/client/owner, permanent order ID,
exact position and full remaining stop quantity, same connection epochs,
complete active orders, no unknown command and a durable no-new-entry fence.
It creates a hypothetical `MKT/DAY` same-OCA `ocaType=1` closing order,
but its `sendAuthority`, `mayStopDriver` and `automaticTradingEnabled`
remain false. If the original stop is partially filled, cancelled, replaced,
or non-OCA, ordinary close is REFUSED until independent recovery. This is a
research-only candidate, not a production trading or stop execution feature.

**Actual Pocket4 SDK compile+CTest:** separate native IB API ON build with
pinned C++ SDK 10.30.01 and existing Intel BID Decimal static library:
6/6 targeted tests pass, including native Decimal ABI, real SDK field mapping,
original stop, bracket, OMS replay, and existing IB PAPER profile. The native
shadow tests include wrong account/conId, LIVE, wrong market tick, unproven
working OCA group, client-ID mismatch, stale broker epoch, pending command,
partial stop and changed physical position. These tests never call
`EClient::placeOrder`.

**Third PAPER-only contract query:** `reqContractDetails` now independently
confirmed that EUR/USD CASH on IDEALPRO advertises `MKT`, `STP`,
`STPLMT`, `OCA`, `GTC` and `WHATIF` order capabilities. This establishes
instrument-level advertised types, *not* account-level order acceptance, true
server-held stop lifecycle, guaranteed stop triggering, or overfill prevention.

- Pocket4 receipt: `~/.local/state/heptatrader/operator-evidence/owner-stop-shadow-20261015-r1/ib-paper-contract-order-types-probe.json`.
- SHA256: `dcde19c060603327d9b97f87fc8aed46c5cf560011a1bffe6accf2521f243cd3`.
- Allowlisted API outbound message codes: `71` and `9`; 0 orders sent.

**Next real product blocker remains**: a single Execution-owned OMS-bound,
restart-proof parent/child broker send, OCA-safe ordinary exit and real IBKR
PAPER callbacks across order partials/unknown submission/driver death.
No existing exposed FX trade was amended in this work. Issue #331 is OPEN.
