# Pocket4 H1 / original Broker census integration — 2026-10-17

**Research-only integration. Never use this branch as a live protective
stop or an authorization to stop the original FX/BTC PAPER drivers.**

## Exact source and original-owner scope

Merge original main `28cb29e` (includes PR #419 fail-closed genuine
SDK-vs-Lite protective-order detection) with original H1 research
`ef644c9` (Parent/STP/GTC/OCA and original-owner callback evidence).
All work occurs on a new, isolated Pocket4 Git worktree. The three
pre-existing unstaged files in the original trading checkout stay intact.

Four C++ files had eight merge-conflict hunks. Preserve the original
`ib_native_order_evidence::Capture` as the **single** real SDK Order
capture implementation; orderStatus carries only a permanent ID, never
a complete native Order. Its native witness struct is identical in
non-comment source across both parents. Preserve PR #419's account-wide
and incremental `NativeAndLiteProtectiveShapeConsistent` rejection
before any H1 native-STOP observation. Retain the H1 chronology,
original Parent/permanent ID/OCA, cash exposure, account/client and
strictly diagnostic stop-remaining checks.

Original H1 synthetic tests used incomplete Lite/native snapshots
and expected to qualify the same Broker epoch after deliberate source
contradictions. The original main now invalidates the global census
for those contradictory events; **do not relax the production guard**.
Update synthetic positive controls to match actual dual-view
`openOrder` fields, leave Lite/native negative controls in the
independent main Adapter test, and never requalify a contradicted
Broker generation within the same fixture.

## Pocket4 evidence, original C++ IB SDK enabled

- Isolated Release CMake/Ninja: complete `hepta-ib-executiond`
  executable and native Adapter compiled and linked (initial
  104-step Ninja build); no active binary selectors touched.
- Complete selected original Coordinator / H1 OMS / Bracket /
  original SDK wire / OCA / Broker Adapter / PAPER profile /
  startup and quote recovery / full synthetic Runtime recovery:
  **20/20 native CTests passed** (~43.5 seconds).
- Original source-only Python CI lane: **116/116 tests passed**
  with the Pocket4 python system; source/lane ownership checked.
- Separate test-fixture-only original-main PR #420:
  **8/8 attribution installer tests passed on Pocket4**.
- Full original native test log:
  `~/.local/state/heptatrader/operator-evidence/
  h1-main419-merged-20261017/native-21-regression.log`
  SHA256 `0a7a42024775c5c02d5e67ac6418869e0d4dc0e1b46614b42fa56609c4875c08`.
- Compiled isolated candidate binary SHA256
  `73a7f1a24aeaf4649a5d5d37213d5afae63535b6f48a585d8eb8a0f810bed12e`.
- Durable Python source test log stored under
  `~/.local/state/hepta-runtime-operator-host/jobs/
  1a149be9912-1b6abb-57/stderr.log`,
  SHA256 `c0969128375345d124ba50f5e7be6a08df2c0a43579d95cc02e4636b5d181d0d`.

These tests use synthetic Broker callbacks. They do **not**
prove a Broker-held protective order survives Gateway/Driver loss,
the ordinary OCA exit cannot reverse a position, or a safe
PAPER stop/maintenance cutover. H1 `nativeStopBrokerWorkingProven`,
`maySendOrdinaryExit` and `mayStopDriver` remain false. The
original Execution owner, account-wide durable/native risk holds,
IB Gateway and both active strategy services are unchanged.
