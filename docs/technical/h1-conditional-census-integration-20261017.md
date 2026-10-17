# Pocket4 H1 research × Broker conditional-order census, 2026-10-17

This is **research integration only**, not a PAPER order sender,
live stop protection, model promotion or Driver stop permission.

Original main `265cc4a` (PR #422: conditional MIT/LIT for FX and
STP PRT for futures enter the Broker-global conditional candidate
inventory; native/Lite discrepancy fails closed) is joined to
reviewed H1 research `f77cc41` (draft PR #421), in a new
Pocket4-only isolated worktree. The merge had one conflict, only
in the synthetic Adapter test call list; both main's conditional
test and H1's original Parent/STOP/OCA, Broker permId, SDK and
stale-freshness tests were retained.

Conditional-candidate visibility cannot be promoted to Broker-held
working stop proof, real OCA order acceptance, physical protective
coverage, ordinary-exit authority, or Driver stop permission. The
H1/Coordinator/Adapter code has no real Broker H1 pair sender.

All compilation and regression must run on the original IB SDK
in isolated test mode. The installed Executive/FX/BTC Driver
binaries, original OMS account data, durable maintenance hold and
native risk switch remain untouched. Issue #331 is not closed.

## Pocket4 original SDK 10.30.01 native evidence

Fresh Pocket4 CMake 4.2.3 Release/Ninja configured original IBAPI.
The full isolated `hepta-ib-executiond` and all native H1/OMS/OCA,
Adapter, PAPER profile, Coordinator, startup/quote and full synthetic
Runtime recovery targets compiled (166 build steps).
**21/21 selected native CTests PASS**, 43.55 seconds total.
**116/116 Python Source tests PASS**, proper owner-private executable
TMPDIR, no host mount or installed trade configuration changed.
Documentation link and staged whitespace checks PASS.

The exact isolated binary SHA256 is
`746b042a615cda8db9e3b55f81a0cf4d03187d122d795ec7bf74e6da5f4bf550`.
Logs are retained on Pocket4 under
`~/.local/state/heptatrader/operator-evidence/h1-conditional-main-20261017-v1/`:
- `native-regression.log` SHA256
  `902631c288f04a746243d33fe17cf7a0ef5e2e7a33ba6b206af1801efb22fc7e`;
- `source-tests.log` SHA256
  `fd47763e614b39642f5d7886f87a8497cd81eba77ee2781ea3381155365ee4e1`.

All tests use fake Broker callbacks or memory-only IB SDK wire encoding.
There were **zero actual Broker orders/cancels/sessions** and no native
stop/OCA authority change. A genuine original-owner PAPER lifecycle
canary remains required before live Driver maintenance or strategy
risk-policy changes.
