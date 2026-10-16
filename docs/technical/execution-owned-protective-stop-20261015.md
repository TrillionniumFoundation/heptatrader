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

## 2026-10-15: SDK callback provenance and additive OMS v4 recovery

The existing IB C++ SDK wrapper previously reduced `openOrder` and
`completedOrder` to `IBEvent.order`, dropping the native `parentId`,
`permId`, `transmit`, `ocaGroup`, `ocaType`, `triggerMethod`, `tif`
and exact `Contract.conId` fields. These are required to distinguish the
original protective child from a similar-priced or foreign-client order.

This iteration adds an optional `IBBrokerNativeOrderWitness` to `IBEvent`.
Actual SDK `openOrder` and `completedOrder` callbacks capture those
original attributes **without normalizing or inferring them**. The ordinary
`orderStatus` callback carries only the reported permanent ID, and never
sets `present=true`; it cannot manufacture a complete stop callback.

The strict `ExactWorkingStopOpenOrder` matcher requires an active native
`openOrder(Submitted)`, correct Broker account and client, exact conId,
parent and child order IDs, permanent ID, original reference, OCA type/group,
stop action, original quantity and price, fresh broker epoch and SDK callback
ingress provenance. An ordinary MKT parent, completed order, lone orderStatus,
untransmitted child, PreSubmitted order, changed account, old epoch or
foreign OCA is **not** positive protective evidence.

An additive, optional OMS v4 `broker_open_order_bracket` event stores the
native parent/permanent IDs, OCA, transmit, trigger method, stop price,
contract ID, quantity and orderRef. It is registered as a *critical durable*
event, is emitted only for an owner-bound candidate, and round-trips through
the **existing** OmsJournal writer/parser without changing the schema of
legacy events. The parser rejects partial, unknown, malformed, off-grid or
PreSubmitted positive records. The production Broker callback observer uses
one shared pure projector also exercised by the OMS regression tests. Note:
this optional event is a **broker callback candidate**, not an independent
account-wide active-order/position attestation or a maintenance permit.

### Actual validation

The SDK-enabled C++ build of `hepta_ib_executiond` succeeds. Ten targeted
native regression tests passed, covering the complete Coordinator,
pre-existing OMS v4 schema, OMS fsync durability, new native OpenOrder
projection, native stop, IB PAPER profile, post-fill reconciliation, live
terminal recovery and the full IB runtime recovery test (44+ seconds).
All native callbacks and OMS replay tests are isolated: no real Broker orders,
service restarts, model promotion or production file replacement were made.

### Identified hard blocker before actual protective send

The resident `IbVenueCorrelationCodec` requires a **45-character H1**
base64url encoding of a service-owned `hepta-v1-sha256` correlation for
real IB orderRef. The current experimental `HEPTA-PARENT-` / `HEPTA-STOP-`
refs from this Draft PR are not recognized by that live-owner correlation
codec. The unsent shadow bracket, standalone stop and OCA sibling now
**explicitly reject refs exceeding IB's 45-byte orderRef limit**, including
long otherwise-valid original command IDs; they do not truncate or re-map them.
**This length check does not make non-H1 refs eligible for production.** The optional legacy
research-only OMS bracket witness now rejects refs longer than 45 bytes,
but **this does not make a HEPTA-STOP ref eligible for production.** A future
Execution-owned group must reserve and durably bind distinct valid H1 parent
and child correlations to the same original command and account, and it
must update the Broker owner projector and replay accordingly, before it
can call the existing native IB sender. The ongoing unprotected risk is
tracked in Issue #331; never claim this Draft as a live protective stop.

## 2026-10-15 — Frozen original H1 parent / stop / OCA identities

Existing Execution owner correlation is the strict 45-character `H1`
representation of a canonical `hepta-v1-sha256:<64 lowercase hex>`
digest. The earlier research-only `HEPTA-PARENT-*`, `HEPTA-STOP-*`
and `HEPTA-FX-OCA-*` values cannot be routed through that codec.

The new *non-dispatching* `ib_bracket_h1_identity.h` addresses the
identity collision problem without weakening the production encoder.
It derives domain-separated Parent, Stop and OCA digests from **one
frozen original-command group**. The canonical input is length-delimited
and binds account, owner, PAPER domain, exact conId 12087792,
original command, client, reserved consecutive parent/child order IDs,
initial broker epoch, position generation, owner fence token, decision
time, direction, original quantity, reference-price IEEE-754 bits and
stop price on verified MarketRule 3188 tick `0.00005`.

The helper uses the existing `IbVenueCorrelationCodec::EncodeOrderRef`
and decodes each role again for independent verification. Duplicate
roles, changed account/owner, stop, notional, fence, order identity,
or forged non-H1 byte strings must fail. Outputs never authorize
actual orders, independent stops, maintenance or release.

Local isolated validation was performed against the byte-identical
original production H1 codec (Git blob `1194bf99e4c3388c9dd91110e176299a28c61fc8`)
and the GitHub-published exact new header/test blobs: C++11 with warnings-as-errors, standalone CTest 1/1, ASan+UBSan
PASS. Corresponding verified source Git blobs:
- H1 header: `28236e4fc00b3c644ff67963a1a3984d848249eb`
- H1 test: `70c49262b58373ff3cbdc063a8eea42d892e1d04`

**Important remaining blocker**: this is the frozen identity helper
and negative-control test only, not a production OMS reservation, broker
sender or callback binding. In particular, a caller-supplied nonzero
owner-fence token is not proof of a durable fence. The Execution owner
must source and attest that token, reserve its broker-order ID pair,
persist the immutable group before any effect, then match the actual
Broker callback to the independently stored Parent/Stop H1 correlations
and permanent IDs. UNKNOWN must never cause a duplicate child
submission. Normal exits still require actual OCA/partial-fill proofs.

The Pocket4 Desktop Commander path was not available in this tool
session, and the alternative Hepta Runtime endpoint refused the
invocation. Thus **Pocket4 native/full CI and PAPER positive order
admission were NOT run for this revision**. Do not infer those
qualifications from the local isolated test or earlier commits.
Issue #331 remains P0 OPEN; keep PR #337 Draft.

## 2026-10-15 — Existing OMS durable H1 parent/stop group (non-dispatching)

On Pocket4, the same previously verified H1 identity helper has been
connected to the **existing** `OmsJournal::AppendDurablePair`, without a
new journal service, order broker owner, TWS socket or change to current
strategy policy. The new `ib_bracket_h1_oms_reservation.h` derives
immutable parent/child H1 identities from a frozen PAPER original command
and writes ordered critical `order_intent` records for both reserved native
IDs under one durability barrier. Every record binds original command,
account, group SHA256, exact parent or stop H1 correlation, direction,
quantity and original pretrade reference/stop price.

The pair format is intentionally **not atomic if the process crashes**:
a prefix alone MUST NOT be interpreted as a completed group. Recovery
requires exactly one matching parent and one matching stop. Missing,
duplicated, substituted or price-altered records refuse admission.

A distinct `place_send_attempt` is synced before any proposed
parent/child send. On replay, either potential send invalidates the
clean-reservation state. The result is `UNKNOWN`: never zero-fill,
never a new parent/child ID, never permission to retransmit.
Only original Broker callbacks and separately authorized Execution
reconciliation could ever resolve uncertainty. The helper exposes
**no placeOrder/cancelOrder or Driver-stop method**, and the
`sendAuthorized` and `driverStopAllowed` outputs remain false
even for a fully validated pair.

Real Pocket4 IB-API-enabled Release build and 8/8 targeted CTests
passed, covering H1 identity, new OMS pair + UNKNOWN replay,
existing original shadow OMS, native bracket evidence, OMS schema
and durability, Coordinator and PAPER profile. This is bounded
research validation, **not** live broker protection.

The remaining production blocker is critical: this helper's
caller-supplied frozen owner-fence metadata is not an authoritative
Execution owner fence and this work does not reserve broker order IDs
through the live adapter or wire native two-order transport. A real
PAPER stop cannot be declared until the existing owner binds those
authorities, attests the Broker's permanent parent/child IDs, prevents
normal-exit/OCA reversal and proves risk continuity during Driver
absence. Keep Issue #331 OPEN and PR #337 Draft.


## 2026-10-15: force native H1 pre-send receipt durability under async OMS

The shadow H1 pair was already sealed by AppendDurablePair, but its
MarkPotentialSend used ordinary OmsJournal::Append. With
HEPTA_OMS_SYNC_CRITICAL=0 and HEPTA_OMS_ASYNC_FLUSH=1, ordinary Append
can acknowledge an **enqueued** critical marker before an fsync. A later
real broker send must never depend on this configuration-sensitive boundary.

The existing OMS now exposes a narrow additive AppendDurableCritical method:
it holds the pinned-file mutex, drains any queued/buffered journal prefix,
validates a critical event, writes the original v4 event, and explicitly
syncs the exact pinned file regardless of ordinary flush settings. Failure
keeps the writer poisoned and cannot authorize a sender. The H1 shadow
pre-send marker now uses this API, but this research lane retains
sendAuthorized=false and driverStopAllowed=false.

On a physically isolated Pocket4 CMake Release build, a new adversarial test
**first failed** with PRE_SEND_RETURNED_WITHOUT_FSYNC on the old path.
After the change, the original H1 role/pair tests and six neighboring
existing native OMS/stop/bracket tests passed **8/8**, including disabled
ordinary critical durability with async enabled. These are private scratch
OMS files, not the resident Execution ledger; no IB API, Broker order,
service restart, forced stop, live risk grant or price policy change.

**Remaining gate:** durable intent and durable pre-send receipts do not
prove a real broker parent/child placeOrder, successful venue native STP,
a surviving working stop, OCA-safe ordinary exit, or uncertainty-free
reconnect. Issue #331 remains open and this PR remains Draft.

## 2026-10-15 — Coordinator-owned Recovery-only fence + H1 OMS pair

The previous shadow H1 pair accepted a caller-supplied nonzero fence token.
That is **not sufficient authority**: the real Execution owner already has
a durable `EnterRecoveryOnlyOwner(agentId, sessionId, ingressFence)` API,
and the existing `PlaceOrder` path refuses new risk for a recovery-only
session. Reuse it instead of adding a second fence mechanism.

The original H1 `FrozenBracket` is now bound to `sessionId` in addition
to original command, owner/agent, account, Broker epoch, quantity, quote
and reserved IDs. The actual `ExecutionCoordinator` now has
`ReserveResearchH1BracketForRecoveryOnlyOwner`, implemented under
its existing mutex. It checks:

- exact `agentId + sessionId + original toolCallId`, expected PAPER FX
  execution domain and the current coordinator-owned ingress fence token;
- absence of active owner orders, ongoing risk dispatch/cancel, terminal
  mutation fence and matching original command in current/historical OMS;
- exact original H1 Parent/Stop pair via the existing
  `OmsJournal::AppendDurablePair`.

The records use distinct critical
`bracket_parent_reserved / bracket_child_reserved` event names, and
potential sends use `bracket_*_send_attempt`. These events cannot be
mistaken for ordinary, individually authorized `order_intent` and
`place_send_attempt` by the pre-existing Coordinator replay logic.
The full original owner + session are also recorded in the new
`trace_id / strategy` fields of each research reservation.

The full-owner test uses **an actual ExecutionCoordinator instance**,
calling its existing durable `EnterRecoveryOnlyOwner`, then reserving
and replaying the H1 pair. It verifies that missing/stale fencing,
other sessions, duplicate original commands, changed tokens and a
restart cannot create a second approved order group. The reserved
pair is confirmed durable, but remains unqualified for submission,
and a real Coordinator restart must preserve the no-new-entry fence.

The native IB C++ SDK research adapter can now map a verified frozen
H1 `Reservation` to actual `Contract / Order` objects with 45-byte
Parent, Stop and OCA identifiers. Those objects are still **unsent**:
the mapping explicitly refuses OMS UNKNOWN, changed quantity, child
ID, direction, reference price, stop, account or Broker epoch.

**Critical open risk:** the Coordinator is validating its durable
recovery-only ingress fence, but native Broker IDs are still supplied
to this research helper; no actual broker-owned ID pair lease is
performed here. The user's active FX position is NOT covered by this
helper. The next allowed integration must atomically lease the two
order IDs under the actual adapter send lock, bind them to the OMS
transaction, restore them after crash, then run an isolated positive
IBKR PAPER bracket/OCA canary. Do not infer authority from the
research `Reservation` object or merge this PR as protection.

Only the isolated Pocket4 research worktree was changed; no
resident services, commands, positions or model selectors were touched.

## 2026-10-15 — Adapter-native parent/stop ID lease under original send mutex

The next research-only link is now implemented in the EXISTING
`HeptaIBGatewayAdapter`, not in a new Broker adapter or daemon.
`ReserveResearchH1NativeIdPair` acquires the same `m_apiMutex` held by
`PlaceOrderInternal`, rejects unknown/disconnected Broker epochs,
non-PAPER/wrong account, invalid `nextValidId` and near-overflow IDs,
then assigns two consecutive numbers from the maximum of the Broker's
current `nextValidId` and the adapter's existing local seed.

While holding that mutex, the method invokes a provided exact
`durablePair(parentId, stopId)` callback. On the tested path, this callback
is the real Coordinator's
`ReserveResearchH1BracketForRecoveryOnlyOwner`: the Coordinator checks
its durable Recovery-only Owner Fence and original command, then calls
`OmsJournal::AppendDurablePair` to pin the H1 group. The returned pair
always has **no broker-send, OCA-close or Driver-stop authority**.

Before invoking any callback, the adapter consumes the two IDs in memory.
If journal persistence returns false or throws, the IDs are not reused in
that process, and the result remains UNKNOWN. This deliberately does not
make a broker call. No change to normal `NextOrderId`/PlaceOrder code path
occurs, and production never invokes this research-only method.

The integration was verified in the existing native
`hepta_ib_live_terminal_reconciliation_tests` using a fake IIBApiWrapper
and the actual Coordinator + actual private scratch OmsJournal:
- wrong account, stale broker epoch, disconnected socket, invalid or
  near-overflow Broker ID reject before writing anything;
- the first valid pair gets Broker IDs 41/42 and is critically synced;
- failed callback consumes 43/44, throwing callback consumes 45/46;
  the next valid pair gets 47/48 rather than recycling uncertain IDs;
- a genuine Coordinator Recovery-only Fence is required before OMS writes;
  after real Coordinator restart, the same original command cannot reserve
  a second group, even if the Broker ID callback offers a fresh pair;
- the fake Broker's actual PlaceOrder counter remains ZERO throughout.

Pocket4 native IB-enabled hepta_ib_executiond and eight core
Coordinator/OMS/PAPER/native-runtime CTests pass; the full runtime recovery
test executes for ~43.5 s. The changed adapter test and integration checks
are in the same *isolated* research working tree.

**Unresolved release blocker:** This is an in-process lease, not a
restart-proof global Broker order-ID ownership protocol. An actual Broker
restart can offer an older `nextValidId` and only a verified full
account-wide Broker order/position/terminal scan, coordinated with the
live Execution owner and durable OMS generations, can rule out ID reuse.
There is NO parent/stop `placeOrder` send path, no real broker acceptance,
no OCA cancellation safety and no independent protection when Driver dies.
Do not deploy as live protection or close Issue #331 on these tests.

A further negative test verifies that the Broker API's nextValidId
accessor throwing an exception rejects native ID reservation with
H1_NATIVE_BROKER_NEXT_VALID_ID_EXCEPTION. No ID is consumed by that
failed read, no OMS write is attempted and no Broker order is sent.
This adds no production send authority or independent stop protection.

## Original native IB order-ID restart high-watermark — isolated audit

The existing Adapter's research H1 pair lease consumes two native IDs under
the normal API mutex before a durable OMS callback. This gives in-process
exclusion, but the broker's nextValidId after TWS reconnect can be lower
than *untransmitted* or UNKNOWN original group IDs. Reusing such IDs before
exact broker/OMS reconciliation could associate a new child with the
wrong parent.

Added a strictly read-only `ib_h1_native_id_restart_floor.h` helper that
replays the existing OMS critical `bracket_*_reserved` and
`bracket_*_send_attempt` events, calculates the maximum observed
native ID, and refuses malformed or orphaned reserved pairs. A future
broker-side allocator **must** require the broker's fresh nextValidId to
be strictly greater than this floor, and must still reconcile account-wide
active/completed orders plus unknown transmissions before any send.

This helper neither allocates IDs, mutates the live Adapter, places
orders nor grants driver maintenance rights. No assertion of
cross-restart Broker safety is possible from its test alone. A new
Pocket4 native IB-enabled CTest uses an isolated real OmsJournal and
verifies completed pair, potential child send, process restart, low/equal
Broker nextValidId and a one-sided orphan. The test passed 1/1.
The remaining production blocker is **wiring this independent recovery
high-watermark into the exact Coordinator+Adapter native lease path** under
the existing owner lock, followed by real PAPER Broker callbacks.

## 2026-10-15 — Mandatory native Adapter H1 restart floor (research-only)

After the H1 native-ID high-watermark audit, the existing
`HeptaIBGatewayAdapter::ReserveResearchH1NativeIdPair` now requires
the **original owner OmsJournal** argument; the older lease signature
cannot silently bypass this check. While holding the same `m_apiMutex`
as ordinary `NextOrderId` and native placement, it replays the original
OMS **before** pre-consuming new parent and child IDs. It refuses:

- A malformed H1 event, wrong PAPER/IB/EUR.USD provenance, duplicate
  numeric native IDs, mismatched original command/group, incomplete
  Parent/Stop pair or a child from another original request.
- A broker `nextValidId` that is equal to or below the highest
  persisted native H1 ID, including after an entirely NEW Adapter
  instance is created with a reset in-memory order-ID seed.
- ANY unreconciled `bracket_*_send_attempt`, even when the broker
  suggests a higher order ID. Such an original order remains UNKNOWN:
  no automatic resend or synthetic terminal/cancellation is created.

The original adapter's ledger-persistence callback still runs only
*after* these checks. Failed/throwing persistence consumes the
in-memory ID pair rather than reusing it for an uncertain command.
A positive read-only lease does not grant actual `placeOrder`,
cancel, stop, flatten or Driver-stop authority.

Actual isolated Pocket4 IB-API-enabled native Release build:
13/13 targeted CTests passed, including 43.49-second full runtime
recovery, native H1 pair, actual OMS journal, Coordinator owner fencing,
broker callback/projection, IB PAPER profile and live-terminal
reconciliation. SDK-disabled Execution compiled and 2/2 targeted
tests passed. The high-watermark test was also compiled with C++11
warnings-as-errors and executed under ASan/UBSan. A fresh mock Adapter
process reopens the same OMS and rejects reused 41/42 before
authorizing 43/44. No Broker orders were sent or cancelled.

**Still not production-ready:** `GetLastValidOrderId()` is a cached
API-observed value, not itself proof of a *currently complete*
account-wide Broker active/terminal order inventory. There is not yet
a reconnect-safe authority lease binding the exact production
Coordinator/Adapter to all historical OMS generations and the Broker's
fresh callbacks. Unsent TWS children, unknown submission, partial
fills, OCA cancellation and actual stop continuity across Driver
death must be positively tested on isolated PAPER, with original
`permId` correlation, before any production activation.

## 2026-10-15 — Coordinator-owned OMS handed to the real native Adapter

The prior Adapter research lease accepted a caller-supplied OmsJournal.
That was unsafe as an eventual Broker authorizer: a valid but unrelated
journal could omit old reserved/possibly-sent parent/child IDs. This
follow-up adds an **explicitly non-dispatching** Coordinator member
`ReserveResearchNativeH1ThroughOwnedJournal`. Unlike the raw Adapter
test helper, it accepts NO journal parameter; it always supplies the
existing Coordinator's `m_journal` to the Adapter native ID floor scan.
It then calls the existing Coordinator
`ReserveResearchH1BracketForRecoveryOnlyOwner` to validate the exact
agent/session/command, current durable Recovery-only fence, absence of
in-flight risk mutations and original-command duplication before
persisting the H1 parent/child pair.

The bound handoff further checks the actual configured IB PAPER account,
connection epoch and Broker clientId under the native Adapter's existing
`m_apiMutex`, with ID pair preconsumption before the callback. It
preserves the durable UNKNOWN/no-retransmit constraints and does not
create an alternate OMS writer, process, TCP endpoint or trading command.

The Coordinator method deliberately does not acquire its mutex before
the Adapter API lock; the existing normal PlaceOrder dispatch releases
the Coordinator lock before the venue call, and the binding's callback
takes the Coordinator mutex only after obtaining the Adapter lock.
Broader production lock-order and service lifecycle audit remains
required before enabling a real sender.

### Pocket4 adversarial validation

The original live-terminal adapter test was upgraded to use the actual
Coordinator-owned binding and to reject a different actual Broker clientId.
A second test creates TWO valid OmsJournal files: the Coordinator-owned
one and a foreign decoy containing much higher native IDs. Missing
Recovery-only owner authority is refused; the successful owned request
uses ONLY the Coordinator journal; the decoy's records remain untouched.
Another test launches two concurrent submissions of the same original
H1 command: exactly one may acquire the durable group, the other is
rejected, with zero Broker calls. The relevant test repeated **25
independent runs without failures**.

Actual Pocket4 IB-API-ON Release compilation of `hepta_ib_executiond`
and 8/8 native Coordinator/OMS/Adapter/PAPER targeted CTests PASS,
including the 44.48-second full runtime recovery test. IB-API-OFF
Execution and adapter test also compile and pass. The same test linked
against the existing original H1/OMS libraries; no research stub took
over the production callback path.

### Still blocking actual Broker qualification

This method and the original Adapter lease are **research-only** with
no placement call and no driver stop authorization. The legacy direct
Adapter research helper still takes a journal supplied by its C++
caller and is unsuitable as a privileged production entrypoint.
It must be made inaccessible or require the same verified owner
capability before any Broker transport can be enabled. In addition,
the owner-supplied recovery-only fence intentionally blocks normal
risk-increasing entry; it is NOT the final live protection authority
for already-exposed positions.

A real release still needs exact immutable Broker-order ID ownership,
PAPER parent-child and OCA submission, complete active/completed order
callbacks, partial/cancel/reconnect proof, and continuous working stop
through Driver downtime. The online EUR/USD PAPER position is untouched.
PR #337, #341, #342 and this stacked research work remain nonproduction;
Issue #331 stays OPEN.

## 2026-10-15 — Native SDK H1 parent/stop wire-preparation and mock transport

The existing Execution owner/OMS chain now has a **research-only** bridge
from its frozen, durable H1 reservation to *native IBKR C++ SDK* `Contract`
and `Order` fields. `ib_native_h1_pair_research.h` verifies the exact
original frozen order H1 values (45-byte parent/child/OCA), account,
conId 12087792, side, quantity, confirmed 3188 minTick `0.00005`,
parent and child IDs, and constructs a `MKT/DAY/transmit=false` parent
and reverse-side `STP/GTC/transmit=true` child with exact `parentId`.
The proposed OCA group and `ocaType=1` are **not broker-qualified**.

The **transport callback exists only under the
`HEPTA_IB_NATIVE_BRACKET_FAKE_DISPATCH_TEST` compile definition**, set for
a single native CTest binary and never for the installed execution adapter.
It writes a critical fsynced `bracket_parent_send_attempt` before the
first mocked `EClient::placeOrder` call and a separate critical fsynced
`bracket_child_send_attempt` before the last mock call. The test uses
actual native SDK Order objects and real isolated `OmsJournal` bytes
but a fake EClient that cannot send network traffic.

Positive local *call-order* and injected exceptions on parent and child
were tested. In all cases the outcome stays **UNKNOWN or
LocalCallsReturnedBrokerUnconfirmed**; no active server-held stop, no
cancel/partial/OCA fill evidence, no retry permission and no Driver
maintenance approval are produced. Replaying the journal prevents
retransmission of both native IDs.

Pocket4 IB-API-ON CMake Release eight relevant CTests PASS; independent
C++11 warnings-as-errors test under AddressSanitizer+UBSan PASS. This is
not a working native broker transport in production; **no actual
EClient::placeOrder was invoked**, and no PAPER positions or resident
services changed. Production integration must come only through the
current Coordinator's authoritative owner fence, fresh broker order
inventory, complete OMS/ID floor, independent risk admission and the
same existing Adapter API lock. A positive isolated IBKR PAPER bracket,
OCA-safe ordinary close, disconnect and Driver-death test remain
mandatory before retiring Issue #331.

## 2026-10-16: reconciled Recovery-only owner and OMS alias negative control

The uncommitted Pocket4 owner-fence candidate was independently captured as
an unchanged private patch and re-applied in a new worktree based on #337's
latest protected H1 OMS code (`678a0c9`), retaining the existing rejection
of forged `Submitted`, native broker callback and other shadow-intent proof.
The parallel developer's original worktree was NOT reset or overwritten.

In this research-only lane the real ExecutionCoordinator mutex verifies the
persisted recovery-only owner/session ingress fence and refuses duplicate
original commands before the existing OMS syncs a distinct
`bracket_parent_reserved` / `bracket_child_reserved` event pair. Research
attempt markers are named `bracket_*_send_attempt`; neither name is an
ordinary executable `place_send_attempt` nor any Broker placeOrder permit.
A restart recovers the original owner fence, refuses stale epochs and
must not mint another H1 parent/stop reservation.

An additional physically reproduced issue was found in OMS replay: a record
whose `req_id` matched the original command could retain a *different*
`client_req_id` alias, or a `bracket_child_send_attempt` could hide behind a
foreign `req_id` with the original `client_req_id`. Both records were ignored
or accepted by the old checker. The same native CTest went RED before repair.
Replay now evaluates BOTH aliases, rejects disagreement as UNKNOWN, and
continues to reject forged Broker callback/Submitted attributes and
original-source, timestamp, venue and instrument drift.

Real Pocket4 validation: native IB SDK enabled, isolated Release build,
**13/13** existing and new Coordinator/OMS/Bracket/PAPER tests passed,
including the full runtime recovery test (43.50 seconds). Separate
ASan/UBSan build passed **3/3** H1 identity, OMS and real Coordinator
owner-fence tests. No existing Execution, FX, BTC or Gateway process was
stopped, restarted or replaced. No real broker order was sent or cancelled.

**Still not qualified:** external broker-native ID allocation/send, the
original Owner's two-order transport and positive PAPER openOrder/permId
proof, OCA ordinary-exit overlap, reconnect unknown-submit recovery and
actual ongoing protection after Driver death. `sendAuthorized=false`,
`driverStopAllowed=false` and Issue #331 remains OPEN. Research code is
not a substitute for a live protective stop or a production release.

## 2026-10-16 — Coordinator original-H1 admission collision boundary

The research-only, recovery-owner-fenced Coordinator now derives the **same**
immutable H1 Group/Parent/Stop/OCA correlations before attempting a durable
OMS parent/child reservation. Its existing exact journal replay checks both
original request aliases **and** identity reuse by any other request: a
foreign event borrowing the frozen group digest or any of the three H1 roles
is refused, before either new reservation record is appended. Unrelated
historical requests do not block the original command. This is an additional
identity/admission boundary under the existing Coordinator mutex, **not** an
account-wide Broker ownership proof, cross-process lock or send authorization.

Pocket4 native IBSDK-ON Release negative test reproduced RED on the preceding
exact head: a foreign original command could borrow the H1 group and cause a
new pair reservation. After repair the positive owner-fence and all eight
foreign-role collision variants, plus a disjoint-history control, passed.
The bounded native regression suite passed **15/15**, and exact H1/Coordinator
ASan+UBSan passed **3/3**. These tests use isolated OMS files and no Broker
orders; existing real positions, services and Kill Switch were unchanged.
Broker send, OCA-safe ordinary exit and real original-client reattestation
remain open under Issue #331.

## 2026-10-16 — Native H1 long original command compatibility

The native **unsent** H1 parent+STP adapter previously called the legacy
`HEPTA-PARENT-` / `HEPTA-STOP-` preparer before replacing those refs with H1.
This rejected an otherwise valid 64-character frozen original key because
legacy display refs exceed IBKR's 45-byte limit. The native mapping now uses
the **same** original Broker/Owner/flatness/quote/size/tick/20bp-stop policy
through a common validated plan that accepts *caller-verified* H1 parent/stop
refs; the original legacy wrapper still rejects oversized legacy refs.
The optional OCA grouping is supplied as the same validated 45-byte H1 ref.
Boundary tests cover 64/96-byte safe original keys for BUY and SELL, and
refuse invalid 97-byte keys, while maintaining the existing no-send,
no-driver-stop flags. This is type translation and NOT actual IB order
placement, an IB accepted stop, or authority for maintenance.

## 2026-10-16: reject ambiguous native-order-ID reuse (research only)

This research Coordinator now checks existing owned native IDs and original
OMS history before writing H1 bracket reservations. A foreign command on the
same PAPER/IB account may have a distinct H1 group and still collide with
one of the reserved numeric parent/child order IDs. This is a **refusal**,
not a new TWS order-ID allocator. The exact Broker-client owner of legacy
records is not independently attested, so an ambiguous collision cannot be
considered a safe reservation.

The scoped H1 OMS replay independently refuses a foreign command reusing a
frozen parent or child numeric native order ID, or any frozen Group/Parent/
Stop/OCA correlation. A truly disjoint prior command with a different native
order ID still replays without blocking the pair. Existing experimental
`HEPTA-*` references are not eligible for real sends.

Independent Pocket4 proof: IB-API-ON Release/Ninja 11/11 native CTests pass,
including the exact Coordinator and OMS tests; ASan/UBSan 2/2 pass. The
foreign numeric-ID negative controls were real red-before/green-after tests,
not just a source-code assertion. No broker placement/cancellation, no
production service restart, and no new Owner permission were involved.

**Still blocked:** durable reservation of actual Broker next-valid IDs under
the exact verified client/connection, owner-fenced parent+child transport,
Broker working-stop permanent-ID callbacks, OCA-safe ordinary close, and
post-crash original Broker order-set reattestation. No deployment or stop
permission derives from this research result.

## 2026-10-16 — Separate H1 native OpenOrder candidate projection and OMS v2 witness

A genuine 45-byte H1 child ref is incompatible with the existing
`HEPTA-STOP-`-only `ProjectOriginalWorkingStop` and v1 native bracket receipt
shape; silently reusing the v1 positive callback would either drop the H1
event or falsely assert original-owner protection. This change keeps the
existing v1 producer and parser intact and adds an **additive version-2 H1
bracket witness shape** (canonical 45-byte H1 child and distinct H1 OCA group,
strict contract/parent/permanent-ID, transmitted STP, GTC, owner-independent
market-rule shape). Version 2 shape is NOT sufficient to prove protection.

The **pure, research-only** `ProjectFrozenH1WorkingStop` requires a complete
original frozen H1 Parent+Child pair in the existing OMS journal (including
replay refusal for partial, changed or POSSIBLY_SENT records), and also
requires the original Owner/session/command/account/contract/client/epoch,
actual native SDK-origin `openOrder(Submitted)` fields, parent/permanent IDs,
quantities, trigger, original 20bp stop and exact H1 Parent/Stop/OCA derived
roles. It refuses foreign pre-bound OMS request hashes, correlation IDs,
existing Broker status/callback/event IDs, held/partial/uncertain callbacks
and false positive standalone H1 shape. It **never changes** the existing
production callback routing or grants a PlaceOrder, maintenance permit, Driver
stop or automatic model promotion.

Pocket4 isolated, IBAPI-ON Release verification exercised the actual existing
`OmsJournal::AppendDurablePair`, the new pure projector, an exact durable
version-2 Broker candidate record and actual v4 OMS journal replay, with
negative callback/OMS mutation cases. Native regression **16/16** and
H1/v1 OMS + identity ASan/UBSan **3/3** passed. No real Broker call was
made, and an `openOrder` callback candidate is not account-wide working
protection. A real owner-fenced Broker sender, child accepted permId,
unknown-submit recovery, OCA-safe normal exit, and driver-death proof remain
P0 Issue #331 blockers.

## 2026-10-16: native IB order-ID int32 boundary (source-only)

The existing ordinary IB Adapter stores its next order ID as a Linux `long`,
while the TWS `OrderId` wire value is int32. Before a real Broker submission,
`NextOrderId()` now refuses a broker-reported next-valid ID or consumed
local seed outside the positive signed-32-bit range, and the single-order
submission path refuses the invalid sentinel before invoking the SDK.

An isolated Pocket4 fake-Broker native test first reproduced a value greater
than `INT32_MAX` reaching the local broker stub. After the fix, it verifies
that such values never cause even a fake SDK send, that `INT32_MAX` remains
representable, and that a subsequent exhausted allocation is rejected.
IBAPI=ON Release regression 5/5 and ASan/UBSan native order lifecycle 1/1
passed. This is a numeric safety fix, **not** Broker-native bracket ID
reservation, stop activation, OMS boot recovery or trading authorization.

## 2026-10-15 — Reconciled latest H1 Broker callback + native transport research

The protected PAPER stop-request shield has been separately merged into
`main` via PR #347 and was verified effective for all nine Pocket4
risk-path and target units with no process restart. That mitigation only
blocks **manual** stop requests; it is NOT a broker-native protective order.

This branch deliberately combines the latest root H1 research from PR #337
with the Coordinator-owned OMS native-ID lease and test-only IB SDK parent
and child transport of PRs #341, #342, #344 and #346. The merge occurred
on a separate worktree, preserving the original source trees. The stricter
root checks were retained for original command alias mismatch, H1 role
collisions, native ID int32 limits, untrusted positive Broker witness fields
and exact frozen H1 `openOrder(Submitted)` comparison. The owner-only
Adapter/Coordinator OMS bridge, restart high-watermark, durable pre-send
barriers and mock EClient parent/child transaction were also retained.

Pocket4 native IBAPI-ON Release CMake compiled `hepta_ib_executiond`.
11 targeted CTests passed, including exact frozen H1 parent/child replay,
fake SDK send failure on either leg, original Broker openOrder v2 candidate,
existing native bracket, Coordinator Fence, OMS v4 durability, ID high-watermark,
PAPER profile and terminal reconciliation. Five additional native core CTests
passed: original Decimal ABI, Coordinator, original native bracket callback,
post-fill reconciliation and full runtime restart (44.47 seconds).
IBAPI-OFF `hepta_ib_executiond` compiled and two compatibility tests passed.
Strict C++11 AddressSanitizer/UBSan runs passed for the H1 mock native
transport and frozen H1 Broker callback. Docs checks and Git diff checks pass.

**Non-qualification:** There has been NO actual
`EClient::placeOrder` call, active stop or OCA accepted by IBKR, nor any
real Driver restart. The latest v2 H1 `openOrder` projector is an isolated
candidate and does not grant production Broker order authority or maintenance
permission. The exact original account's native bracket send still must be
bound to Execution-owned current Broker order inventory, full no-new-entry
fence, actual Broker permanent parent/child IDs and cancellation/partial fill
lifecycle before enabling a genuine PAPER canary or claiming loss continuity.
PR and Issue #331 must remain Draft / OPEN respectively.

## 2026-10-16 — H1 v2 post-send Broker callback proof without retransmission

An adversarial review of stacked PR #349 revealed a real integration
dead-end: `ProjectFrozenH1WorkingStop` reused the pre-send/no-retry
`ConfirmReplay` to validate a native `openOrder(Submitted)` callback.
That original function correctly treats **any** durable
`bracket_parent_send_attempt` or `bracket_child_send_attempt` as UNKNOWN
and rejects new sending. Therefore a legitimate broker echo after an
attempted pair transmission could never qualify even as a candidate.

The fix does **not** weaken the restart/duplicate-send rule. The existing
`ConfirmReplay` remains unchanged. A separate strictly read-only
`ConfirmReplayForBrokerCallback` accepts a frozen original group only
when the actual OMS replay shows, in exact order:

1. One immutable original Parent reserved;
2. One immutable original Child reserved;
3. One original Parent `POSSIBLY_SENT` durable pre-send;
4. One original Child `POSSIBLY_SENT` durable pre-send.

Every pre-send must match original account, owner, session, H1 digest and
role, order ID, quantity, stop price, timestamps, venue, source, original
request alias and Broker-free provenance. The existing pre-send only
`ConfirmReplay` is then re-used **over the reservation subset** to reject
foreign account/native-ID/H1 reuse, duplicate parents/children and
malformed immutable original intent.

If either attempt is absent, reordered, duplicated, reassigned to another
H1, spoofed through `client_req_id`, or changes stop price, the new
projection is refused. In particular, native OMS v4 canonicalizes the
legacy `client_req_id` to the original `req_id` during disk replay.
The reconciler requires that **exact same original command**, not an
empty or arbitrary foreign alias.

Successful replay returns only `BrokerCallbackReplayProof` with
`maySubmitOrRetry=false`, `mayStopDriver=false`, and
`brokerWorkingStopProven=false`. Only an independently matched,
same-epoch original native `openOrder(Submitted)` with exact parent/child
Permanent IDs and OCA can become a **candidate**; full account-wide
open-order/position, parent fill, OCA safe ordinary exit, stop trigger
and continuing protection still require separate real Broker proof.

Pocket4 independent IB-API-ON Release `hepta_ib_executiond` build and
**12/12** relevant CTests passed, including original H1 no-retry,
full runtime recovery (**45.45 s**), native callback, OMS durability,
post-fill reconciliation and fake native bracket transport. Standalone
C++11 warnings-as-errors AddressSanitizer/UBSan passed for the enhanced
native H1 callback tests. No actual broker `placeOrder`, active stop,
manual stop, service restart, account change or model promotion occurred.

This is **not** a native stop-loss production release and does **not**
close P0 Issue #331.

## 2026-10-16 — Frozen H1 OCA normal-close race negative control

Issue #331's native Parent+Stop admission is not complete until ordinary
strategy CLOSE and the working protective STP cannot both overfill or later
reverse the CASH EUR/USD account. The existing legacy `HEPTA-OCA-*`
helper is intentionally not production-compatible with the 45-byte H1
venue codec.

`ib_native_h1_oca_exit_research.h` creates an **UNSENT native SDK**
ordinary-close candidate using a fourth H1 role
`hepta-bracket-h1-role/v1 + <frozen-group> + ORDINARY_EXIT`.
Parent, Stop, OCA group, and Ordinary Exit identities remain distinct,
reproducible and bound to the original immutable group.

Construction requires fresh same-epoch full-account Broker position and
active orders, exact original entry execution evidence and permanent ID,
the original stop still `Submitted`, exact stop permanent ID / action /
price / full remaining quantity, original Broker-attested OCA group with
`ocaType=1`, no pending cancel/replace, no unknown original command,
an independently attested no-new-entry fence, an exact separately reserved
native exit ID and matching owner/account/client. A changed TWS epoch or
replayed old callback cannot pass a new snapshot. Zero or partially
closed exposure, stop partial fill, canceled stop, unverified OCA, wrong
broker permanent ID, extra open orders, stale quote/order epoch and
unreserved exit ID all fail closed.

The proposed ordinary exit uses MKT/DAY, parentId=0, matching H1 OCA
group with cancel-with-block (type 1), and correct opposite direction
and entire remaining original size. Every result explicitly has
`sendAuthority=false`, `workingProtectionProven=false`,
`mayRetry=false` and `maintenanceAuthority=false`. The supplied
Broker evidence is synthetic and this is **NOT a live broker owner
admission or a send API**. Actual OCA type-1 broker semantics do not
guarantee stop triggering, execution price, or protection during outage.

Pocket4 CMake Release IBAPI-ON selected **12/12 CTests PASS** including
full Runtime Recovery 43.45s. Independent native C++11 strict
`-Wall -Wextra -Werror` AddressSanitizer/UBSan PASS.
No actual `EClient::placeOrder`, no Broker cancel/OCA modification, no
position mutation, and no resident unit restarts. The existing 9-unit
PAPER manual-stop shield remains separately deployed from merged PR #347.
The IBKR PAPER account still has no independently proven STP/OCA
protective acceptance, so PR stays Draft and Issue #331 remains P0 Open.

Official API order metadata:
https://www.interactivebrokers.com/docs/tws-api/ref/order

## 2026-10-16 — actual Adapter-owned account-wide H1 OCA snapshot consistency

Issue #331's original unsent OCA ordinary-close candidate used an entirely
caller-populated `BrokerStopAndPositionEvidence`. A caller setting flags like
`brokerPositionAuthoritative=true` was **not** proof of the current Broker.

This iteration adds **read-only**
`HeptaIBGatewayAdapter::InspectResearchH1OcaAccountScope`.
It samples the Adapter's *actual* `BuildRecoveryAuditSnapshotLocked()`
under the existing `m_apiMutex` (no alternate Broker client, owner, store or
API request). It requires the exact PAPER account and configured client,
EUR/USD CASH/IDEALPRO conId, canonical frozen Parent/Child/OCA H1 roles, a
current complete active-order inventory containing **exactly one** broker
order and mapping the original STOP H1 correlation to the original child ID,
and a coherent complete account+positions+FX-cash risk refresh from the
same epoch that has absorbed all known economic fills. It rejects pending
postfill reconciliation, callback conflicts, missing FX position, another
working order, altered Broker account/client, changed H1 or original quantity,
incomplete source observations, changed cash balances and any stale epoch.

The structured observation is intentionally **not** a Broker stop proof:
its `nativeStopBrokerWorkingProven`,
`stopPriceTriggerProven`, `accountSnapshotFreshnessProven`,
`maySendOrdinaryExit` and `mayStopDriver` fields remain **false**
even when all currently available account-wide projections agree. The
existing Adapter correlation snapshot contains only orderRef / numeric ID;
it does not contain the original full SDK stop openOrder status, permanent
ID, remaining quantity, trigger, OCA server acceptance or a clock freshness
receipt. Read time is not completion time. Positive native Broker callbacks
and fresh owner-fenced account/position/order snapshot evidence are still
required before any real OCA sibling can be admitted.

Pocket4 actual IB-API-enabled CMake Release isolated test uses the existing
fake Broker callbacks to populate the **real Adapter** (not a hand-filled
snapshot struct); it demonstrates one exact H1 stop / EUR cash position
consistency as advisory only, refuses every changed account, client, stop
ID, quantity, H1, second active order and out-of-generation cash balance.
It does not issue, cancel, or modify a Broker order and leaves resident
PAPER drivers running. See the native `hepta_ib_live_terminal_reconciliation_tests`
regression.
## 2026-10-16 — Actual Adapter SDK-native original STOP echo (diagnostic only)

The existing real Adapter's H1 OCA account-scope diagnostic previously
checked a frozen child ID in the account-wide correlation snapshot but
did **not** retain the native SDK `openOrder` fields proving whether it
was actually the original STP/parent/OCA contract.

In this isolated research extension, `ib_gateway_adapter` now
retains a bounded in-memory copy of exact Broker `openOrder(Submitted)`
SDK-native fields **only during `ReqAllOpenOrders`** for the current
configured account/client and connection epoch. New account-wide
refresh, disconnect and invalidated correlation views clear the cache.
Contradictory permanent ID/parent/ref/stop-price echoes for the same
native order ID invalidate the entire refresh.

The frozen H1 OCA observation has two new, narrowly diagnostic fields:
`sdkNativeStopEchoMatches` and `sdkNativeStopPermanentId`. A match
requires native conId 12087792, exact parent/child numeric IDs, positive
Broker Permanent ID, original 45-byte Stop H1, `STP/GTC/Submitted`,
transmit=true, complete original quantity, trigger method 0, verified
stop price, matching H1 OCA group + type 1, exact DU account/client,
current broker epoch and SDK callback ingress provenance. Neither a
legacy Lite-only orderRef nor a single-client `ReqOpenOrders` view
can be promoted to an account-wide working protective witness.

**The state remains deliberately unqualified:** fields
`nativeStopBrokerWorkingProven`,
`accountSnapshotFreshnessProven`, `stopPriceTriggerProven`,
`maySendOrdinaryExit` and `mayStopDriver` remain **false**
even when the SDK native echo matches. SDK fields alone cannot prove
the stop remains server-held, that original parent actually filled,
that OCA cancellation handles partial fills, or that the stop survives
gateway failure.

Pocket4 isolated native IB SDK-enabled Release target
`hepta_ib_live_terminal_reconciliation_tests` PASS with
real Adapter instance and injected native callbacks. Negative controls
include absent SDK-native witness, altered permanent/parent ID,
OCA group/type, transmit, stop price, GTC, quantity, contract ID and
provenance, and conflicting duplicated native IDs. The rest of the
original account/positions/active-order tests remain unchanged.
No actual Broker traffic, positions, systemd state or original OMS
was mutated by this research branch.
