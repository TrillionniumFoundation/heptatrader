# Pocket4 H1 SDK research × current Execution main integration

**Research only (2026-10-16 PDT).** Original main `e64f077`,
advanced to `3f795d8` in the same research-only integration; H1 research
`5b5f199`. Both main updates (`9e97521`, `3f795d8`) preserve native
one-lock Broker account evidence and refuse unknown-as-zero. All work
remains in a separate Pocket4 worktree, never in the active trading
release or original dirty checkout.

Source merge preserved both original-main global Broker order/client
identity and the original H1 native STOP echo/status diagnostics.
IB openOrder from a foreign client remains in the account-global active
inventory, but cannot establish an original-client H1 protective order.
Refresh and invalidation clear both the client-scoped inventory and H1
echo/status cache. The only merge conflicts were in two IB Adapter
translation units; no order sender, owner or risk permissions added.

Pocket4 CMake 4.2.3 Release/GNU C++, original IB SDK 1030.01
enabled. Native integration build **148/148** Ninja steps succeeded,
then the whole new `hepta_ib_executiond` service binary linked
**16/16** further build steps in the same isolated tree (SHA256
`819badf271cb71892b82aaf8a80e88012dc2b27c3d8182d0cb2163d6647062f9`).
The original Execution Coordinator, H1/OMS/OCA, native Adapter, IB
profile, startup/quote and full synthetic Runtime recovery suite
passed **20/20 CTests** in 43.48s. Saved isolated test log SHA256
`57194ac2fc9a0e3dc01c414dd0b8fddb6d5d1d4f2788d60518fd5038d414593a`
at `~/.local/state/heptatrader/operator-evidence/
h1-main-integration-20261016-v1/h1-main-integrated-regression.log`.
The original full-Runtime fixture is synthetic (28 scenarios and
no Broker socket/order method calls); it cannot prove a server-held
protection or authorize a real PAPER canary.

The original Execution and both strategy drivers remain active; native
kill switch and durable maintenance hold remain in force. No Broker
order/cancel, real unit stop/restart, or model promotion is authorized.
`nativeStopBrokerWorkingProven`, `maySendOrdinaryExit` and
`mayStopDriver` remain false pending genuine Broker lifecycle proof.

This branch is deliberately **research only**. The current production
Execution and Driver units still use their preexisting pinned binaries;
no systemd selector, broker credentials, PAPER entry risk rule, or native
protective stop order was altered. A synthetic test cannot certify
the independently protected Driver-stop lifecycle from P0 #331.

## New original-main Broker account evidence incorporated

During this integration the original main advanced to `3f795d8`
(`9e97521` adds one-lock Broker evidence with conservative UNKNOWN
rather than zero; `3f795d8` merges it). It was merged into this
isolated H1 research branch **without conflict**. The resulting original
Execution service binary linked successfully (SHA256
`d741f104b6a5d5e22439c6ab719e0d7b6b658f8dcde37ad096ea095c3962d320`).
Latest combined Pocket4 original-SDK-enabled H1/Coordinator/Adapter/PAPER
profile/startup/quote/full-runtime recovery suite was rerun and passed
**20/20** in **45.46 seconds**, zero new failures. Frozen log:
`~/.local/state/heptatrader/operator-evidence/h1-main-integration-20261016-v1/h1-latest-main-3f795d8-20-ctest.log`,
SHA256 `98ad46255d20eaffd1bfce8c3b70ab44fbc3785d9ba7507168633af8568b8806`.

**This remains source-only research and does not certify actual Broker
STP/OCA acceptance, protective coverage during Driver absence, or safe
systemd maintenance.** The resident Execution binary was not replaced.

## 2026-10-16 Pocket4: SDK wire and four-source clock evidence

Original IB SDK 1030.01 **EClient::placeOrder serializer**, routed into a
strictly no-network in-memory sink, now tested as an extra native CTest.
The two wire frames must contain original 45-character H1 parent/STOP
refs, exact EUR/USD conId/amount/account, original parent MKT/DAY with
transmit=false, child STP/GTC/parentId with transmit=true, OCA Type 1 and
original tick-aligned auxPrice. Individual negative mutations prove the
wire's exact parentId, transmit, TIF, OCA type/group, H1 ref and trigger
fields cannot be silently dropped. The in-memory SDK server version 200
is **synthetic**, not negotiated with the running Gateway.

The research Adapter additionally observes four **host-monotonic** ages:
account-global `OpenOrderEnd`, coherent Account/Positions/FX-CASH risk
refresh, original SDK-native STOP `openOrder`, and matching latest full-
remaining `orderStatus`. All four must be <=3 seconds old for the
diagnostic `brokerObservationClockBounded`; stale SDK echo combined with
fresh `orderStatus` correctly yields false even with matching original
permanent ID. Any reversed or contradictory permanent-ID callback still
invalidates the original Broker correlation generation. None of these
facts authorizes an order or any Driver-stop decision.

Final Pocket4 SDK-enabled native isolated build and **21/21 relevant
CTests PASS** (including Coordinator concurrency, H1/OMS/OCA, real
Adapter callback regression, SDK wire serializer, PAPER profile, and
full synthetic Runtime recovery). Log:
`~/.local/state/heptatrader/operator-evidence/h1-wire-freshness-20261016-v1/
sdk-wire-freshness-native-ctest.log`; SHA256
`c94dbff1e480be733be0928e786f5eaa911d160397b6494cf0c766fbe54029de`.
Rebuilt isolated Execution binary SHA256
`dd5ded4a161a1e9c548e85cd722dd5b933f0fb15a206de61e82c7ceb90b4e38a`.
**Actual Broker orders sent=0, network connections opened=0, runtime
service restarts=0, maintenance holds released=0.** Broker-native stop
acceptance/survival, OCA exit, partial fill and safe unattended Driver
handoff remain unproven, so keep P0 #331 and the research PR open.
