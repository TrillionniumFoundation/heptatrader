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

2026-10-16 native callback regression: an SDK event with the **same frozen
H1 child reference** but not `openOrder(Submitted)` (held, inactive, cancelled,
filled or status-only) now produces an explicit `Rejected`/not-working
observation without populating the Broker witness. It is no longer silently
ignored as though positive protection might persist. An event for a different
H1 parent/ref remains `Ignored`; this pure projector still does **not**
manage the live order lifecycle or grant an order/maintenance permission.
Pocket4 IBAPI-ON Release 5/5 focused tests and H1/OMS/nextValidId
ASan+UBSan 3/3 passed following the original red-before/green-after test.

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

## 2026-10-16: multi-client IB order ID readiness and last-hop refusal

IBKR requires a new TWS order ID to exceed IDs observed through native
`openOrder` / `orderStatus` callbacks in multi-client/master-client sessions,
not just the latest `nextValidId`. The existing, atomically published
`IBNextValidOrderIdReadiness` now tracks that second monotonic high-water mark.
The `openOrder`, `orderStatus`, and `completedOrder` EWrapper callbacks update
it before event publication. **Only the real `nextValidId` callback can make
order allocation ready**: older high-ID callbacks cannot mint permission to
send. Zero/invalid IDs are ignored. An observed `INT32_MAX` exhausts the
native ID space instead of wrapping or reusing an existing ID. Reconnect
resets both independent observations.

The existing adapter recomputes the effective floor immediately before its
Broker mutation call; the native `PlaceOrderWithTimeInForce` implementation
also rechecks at the final transport boundary. A request overtaken by a
newer native callback is locally refused **without SDK placeOrder**.
Pocket4 fake-Broker test first reproduced a later broker floor (41→900)
allowing a stale locally allocated order, then proved zero Broker sends
following the fix. A second test verifies that a higher foreign order ID
arriving before `nextValidId` cannot make the connection READY. Includes a
multi-threaded high-water regression test and signed-32-bit exhaustion.

This closes one allocation race window, **not** global atomicity against
another client that sends concurrently after the last local check, and
not the missing durable Execution-Owner parent+child Broker reservation.
No Broker credentials, strategy risk policy, running services or positions
were changed by this source-only patch. The native sender is not connected
to the research H1 bracket or authorized to submit STOP/OCA pairs.


## 2026-10-16 — adapter-local H1 native ID pair handoff (unsent)

The existing real IB Gateway adapter now exposes an **unsent, local-only**
`ReserveResearchUnsentH1LocalIdPair` method. While holding the same
`m_apiMutex` as ordinary order allocation, it checks the expected PAPER
account, exact IB clientId and current connection epoch, the actual current
`nextValidId`/observed-foreign-order composite floor, live wrapper identity,
and the two-slot signed-int32 capacity. It burns exactly two consecutive
local numeric IDs in the shared seed; single-ID ordinary sends cannot silently
reuse those local numbers. No order, cancel, TWS request, or OMS journal write
occurs in the method. A new or stale epoch, changed account/client, read-only
or disconnected wrapper, missing Broker floor or exhausted int32 space is
refused without producing a partial usable pair. The returned object contains
`sendAuthorized=false` and `brokerWorkingStopProven=false` unconditionally.

This is a **local ID handoff candidate, NOT a Broker reservation**. It does
not establish that TWS has accepted those IDs, or that the Coordinator holds
an exclusive original order group, or that the pair is durably present in the
OMS before a future send. New higher broker IDs may arrive on the reader
thread immediately after the local reservation; any future sender must recheck
fresh broker floor under its actual transport lock, and treat an overtaken
pair as unusable without blind regeneration.

Before returning an unsent pair, the adapter now performs a **second** SDK
composite-floor and connection-identity read while still under its local send
mutex. If the floor has overtaken the parent ID, regressed, disappeared, or
the wrapper disconnected, that allocation fails and both consumed local IDs
remain burned. A future call must obtain a fresh Broker floor rather than
persist stale parent/stop numbers. Real Pocket4 mock-Broker native CTest
reproduced the old failure at floor 41→900, then passed cases for floor
loss and 41→42, 41→43, and 41→900 advancement. The native IBAPI-ON Release
12-target suite and ASan/UBSan two-target suite pass. This narrows a local
race only: a different IB client may still consume an ID **after** the
second sample. There is no global Broker-atomic allocation, proof of a
working STP, or new sender authority. An additional research-only Coordinator path,
`ReserveResearchH1BracketFromAdapterForRecoveryOnlyOwner`, now calls the
adapter-local allocator via a narrow callback **only while holding the real
Coordinator owner mutex**, after checking the original recovery-only Owner
fence, session, command and mutation state. It refuses caller-preselected IDs,
replays the original OMS before consuming IDs to prevent duplicate commands,
then binds the returned parent/child pair through the existing
`AppendDurablePair` and frozen H1 identity codec. A callback failure writes
no new OMS pair. As a plain C++ callback it is **not** a security boundary:
only a callback bound internally to `ReserveResearchUnsentH1LocalIdPair` is
eligible for this research route, never an Agent-selected callback. A real
write/flush error must remain UNKNOWN rather than
induce blind broker resends. On Coordinator restart, the original durable
command prevents a new allocation. Tests cross the actual Coordinator,
original OMS and production IB Adapter against a wholly in-process fake
Broker; they cover no-fence, preselected IDs, duplicate command, stale/missing
nextValidId, correct callback recovery, owner restart, and no IB egress.

The live strategy **does not invoke** this research-only handoff. A stored
pair is not yet a TWS-accepted order-ID lease or proof that the Broker floor
has not advanced in the meantime. No sender, native working stop, OCA close or
risk-control release is enabled. Do not activate it on the current PAPER
trading chain until exact original Owner, durable Broker send attempt, true
PAPER openOrder/permanentId, and failure/ordinary-close recovery are proven.

Native mock-Broker tests validate account/client/epoch and read-only refusals,
shared ID cursor with ordinary mock sends, Broker floor advancement,
reconnect invalidation, missing `nextValidId`, no native send during the
reservation itself, and the last two valid signed-int32 order IDs. The test
`Broker` is an in-process fake; it is **not** IBKR PAPER acceptance evidence.

## 2026-10-16: preserve unsent H1 native IDs across Adapter process restart

IBKR `nextValidId` cannot include a locally reserved H1 parent/stop
pair that was durably recorded in OMS but NEVER sent to TWS. A new Adapter
used to return the same native order ID to an unrelated ordinary PAPER order
after restart. An actual isolated Pocket4 Mock Broker/real OMS test reproduced
`OMS_RESERVED_H1_NATIVE_IDS_REUSED_AFTER_RESTART` before the fix.

The existing Execution Coordinator reads the owner-private original OMS
and conservatively returns the maximum next native ID across **all** valid
PAPER/IB H1 parent/child reservations and possible-send records, including
those recorded under another DU account. IB numeric IDs belong to the
native Broker client, not a PAPER account; old H1 journal records do not
attest a disjoint native client, so changing the DU account must not recycle
an unsent original H1 order ID. Malformed non-PAPER H1 scope fails closed.
A lone durably written parent from a crashed `AppendDurablePair` still burns
its ID. Unexpected/malformed H1 records refuse recovery; this does NOT turn
an uncertain Broker send into a confirmed order or zero-fill. After the
real `nextValidId` handshake, and **before** user PAPER IPC is marked ready,
the Adapter adopts the recovered floor only for the exact configured account,
client and connection epoch. This is a one-time local cursor synchronization;
no Broker ID grant, API PlaceOrder, cancel or permission is issued.

Pocket4 IBAPI-ON isolated Release validation: full `hepta_ib_executiond`
compiled and five targeted native CTests passed, including the real 43-second
runtime restart test. The `nextValidId`, Coordinator and adapter-local
reconciliation tests also passed ASan+UBSan (3/3). Even this durable local
ID-floor recovery is **not** an active working stop or a safe driver shutdown.
A Pocket4 exact-head negative test reproduced a fresh DU account receiving
native ID 1 after a different DU account had durably reserved unsent H1 IDs
in the same original OMS. The tested fix burns the previous numeric IDs on
both accounts, rather than guessing that the old events came from another
IB client; it never makes the runtime eligible for a Broker stop send.

Original P0 #331 still requires real broker-side child acceptance, owner-
correlated permanent IDs, OCA-safe ordinary close and failure recovery.

## 2026-10-16: prevent same-adapter Init from reusing unsent H1 Broker IDs

The existing IB Adapter reserves H1 parent/child IDs locally under its native
send mutex. Previously `Init()` reset the local `m_localOrderSeed` to 1 on every
call; TWS `nextValidId` cannot see a parent/child pair reserved only in OMS.
A repeated `Init()` while still connected, or a disconnected reconfiguration
of the same Adapter with a different DU PAPER account, could therefore reuse
an unsent native ID under the same Broker client.

`Init()` now holds the Adapter API mutex, rejects configuration changes while
its own SDK connection is active, and retains the adapter-lifetime ID cursor
through explicit disconnect/reconfiguration/reconnect (even across DU accounts).
A newly constructed Adapter starts with the ordinary default cursor of 1;
process restart still requires the previously implemented verified OMS H1
floor adoption before normal PAPER mutation startup. This does **not** invent
Broker-side atomic ID reservation or grant native STP send authority.

Independent **Pocket4** IB-API-ON Release validation reproduced the old
`H1_CONNECTED_INIT_RESET_NATIVE_RESERVATION` failing CTest, then passed
13/13 relevant CTests including the full runtime-recovery scenario (~43.5s).
Separate ASan/UBSan `hepta_ib_live_terminal_reconciliation_tests` passed.
The synthetic Broker places no protective orders in the new case. No running
Execution, FX, BTC, Laya or Source Health unit was restarted or replaced.
This change remains source-only research until the full Broker PAPER bracket,
working stop and ordinary OCA exit lifecycle is demonstrated.


## 2026-10-16 — execDetails-only native order ID high-water

`ib_api_wrapper.cpp` now raises the atomic native order-ID high-water on
original SDK `execDetails` callbacks **before** publishing their queued
execution event. A fill can be the first or only delivered observation of a
higher order ID, even when no matching `openOrder` or `orderStatus` arrives.
The observed ID only **raises** the candidate floor. It never mints a
`nextValidId` callback, a pair reservation, a Broker send, or authority
for maintenance. Invalid/nonpositive and over-int32 IDs remain refused by
`IBNextValidOrderIdReadiness`, including when the callback is historical.

Pocket4 exact-source IBAPI-ON Release CTest: the real native readiness
concurrency test and mock IB terminal reconciliation **2/2 passed**; the
seven related Execution/OMS/IB tests also passed. Dedicated ASan+UBSan
Debug build of those two native cases **2/2 passed**. This is still
source-level callback wiring and offline test evidence, **not** an actual
Broker execution fill or order placement.

## 2026-10-16: reconcile native H1 callback after durable send attempts

The real H1 parent and STOP sender must persist a `POSSIBLY_SENT` marker
**before** either native Broker API send. Generic `ConfirmReplay` correctly
leaves this outcome UNKNOWN and denies reissue, but the existing native
`OpenOrder(Submitted)` candidate projector previously rejected the exact
same valid original history. That prevented later positive native callback
correlation after either original send was attempted.

The research-only `ConfirmReplayForOriginalNativeEcho` now accepts only the
single ordered original *pair reservation, parent send attempt, child send
attempt* sequence with exact account, Owner/session, ID, H1 role, quantity,
price, source and frozen provenance. It requires both send markers, rejects
missing, duplicate, swapped, forged Broker status, foreign alias or borrowed
role, and then reruns the original strict reservation replay over the full
history excluding **only** those two fully validated markers. It does not
rewrite, delete or settle any original OMS record: the full journal remains
UNKNOWN for resending and the returned research reservation stays uncertain.

An exact native `OpenOrder(Submitted)` with permanent ID, parent linkage,
OCA and original H1 receipt can now yield a **read-only candidate**. It is
**not** proof of Broker-wide active orders, current position, ordinary OCA
close safety or surviving protection after driver death; no order sender or
maintenance grant has been added. The old pair-only shadow candidate path
remains a non-authorizing compatibility case, not a live send qualification.

Actual Pocket4 proof: prior real IB-SDK Release CTest RED for two fsynced
send markers plus exact positive OpenOrder; after source repair and negative
controls, 11/11 relevant native Release tests and 2/2 H1 ASan/UBSan pass.
No Broker place/cancel, production binary replacement or service restart.

## 2026-10-16: H1 native stop reattestation after previously sealed callback

An exact native Broker `OpenOrder(Submitted)` H1 stop callback may itself have
been fsynced to OMS during an earlier Broker connection. The previous
non-dispatching H1 replayer rejected that original callback as an unexpected
additional order event and could never re-attest the **same** protective
stop after reconnect. A real Pocket4 IB-API-ON Release CTest reproduced that
rejection before the fix.

The research replayer now accepts a historical native callback as *prior
observation only* after the exact original pair and two critically durable
pre-send records, and only with strict account, original Owner/session,
Group/Stop H1 correlations, original child and parent native IDs, `Submitted`
OpenOrder provenance, SDK client, positive permanent ID, OCA group/type,
price/quantity and monotonic Broker connection epoch. Every prior callback
remains on disk unchanged. The new callback must have a strictly newer epoch
**and the same permanent ID**; a duplicate old epoch, substituted foreign
account/OCA or changed permanent ID is refused. Two prior valid epochs still
allow the third epoch's exact positive native observation.

These are read-only observation candidates, **not** proof of a live protective
order across driver death or ordinary exit. `sendAuthorized=false` and
`driverStopAllowed=false` remain unchanged. No native Broker placement,
cancellation or maintenance permission was added.

## 2026-10-16: no native H1 witness without both durable pre-send markers

Research H1 Broker `OpenOrder(Submitted)` evidence may not be promoted to
a working-stop *candidate* from two durable native order-ID reservations
alone. The prior pair-only compatibility path produced such a candidate
without a single durable original parent/child Broker send attempt. A real
Pocket4 native IB-API-ON CTest reproduced this defect before the fix.

The native H1 projector now requires the strict original pair followed by
**both** exact fsynced parent and child `POSSIBLY_SENT` markers. A missing
parent or child, swapped or forged marker, or an old candidate with only
reserved IDs is rejected. Neither matching Broker-looking fields nor a
positive permanent ID can replace the pre-send provenance. The separate
strict full-journal replay continues to forbid retry of uncertain sends.
This is a non-dispatching research-only evidence boundary, not a stop sender
or authorization to stop drivers or modify active positions.

## 2026-10-16: H1 ordinary-exit OCA sibling native fields (unsent)

The native SDK research adapter now constructs a **distinct 45-byte H1
ordinary-exit orderRef** from the original frozen H1 group, one reserved exit
order ID and the reattested original stop permanent order ID. The child stop
and exit share the **same** H1 OCA group with `ocaType=1` (cancel-all-with-
block), while an exit has `parentId=0`, type `MKT` and `tif=DAY`. The legacy
`HEPTA-*` OCA research path keeps its existing public behavior. Native H1
BUY/SELL positive and negative tests refuse stale epochs, unknown stop/permanent
ID, changed owner/OCA group, pending entries, partial stop fills and altered
frozen reference roles. A reconnect to the same original stop permanent ID
must produce the **same** ordinary-exit H1 reference; never use the connection
epoch as a new trade identity.

This helper has **no** `placeOrder`, cancel, broker credentials, native exit
reservation authority, or production wiring. Its synthetic Broker witness
booleans do **not** prove a working PAPER stop, account-wide position/order
coherence, or atomic OCA overfill prevention. Real IBKR API acknowledgement,
original Execution Owner durable ordinary-exit identity, and a complete
PAPER OCA close/partial/driver-death test remain necessary for #331.
