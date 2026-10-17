# Pocket4 H1 SDK research × current Execution main integration

**Research only (2026-10-16 PDT).** Original main `e64f077`; H1
research `5b5f199`. All integration work occurs in a separate Pocket4
worktree, never in the active trading release or original dirty checkout.

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
