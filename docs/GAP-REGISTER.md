# Gap register

Status: CURRENT
Applies to: repository HEAD
Machine source: [`gap-register.json`](gap-register.json)
Verification: `python3 scripts/check_gap_register.py`

HeptaTrader is an owner-operated, self-use system. Every gap in the
supported baseline is closed in source and none blocks repository use.

## Complete supported-scope closure

| Gap | State | Closure |
|---|---|---|
| DOC-001 | CLOSED_SOURCE | Canonical documentation, capability, module and build inventories are machine-validated. |
| CI-001 | CLOSED_SOURCE | Exact-revision full Python discovery, canonical core/CTest, candidate and complete GCC/Clang core sanitizer checks are defined. |
| TEST-001 | CLOSED_SOURCE | Gap-critical executable tests are inventoried and built by the canonical aggregate. |
| RISK-001 | CLOSED_SOURCE | Generic snapshot, notional, pending exposure, loss, drawdown and guarded-exit rules are implemented and tested. |
| PENDING-EXPOSURE-001 | CLOSED_SOURCE | Pending exposure, exact V5 atomic flatten, JSON machine-state nftables replacement, exact readback and independently verified deny-all fallback are behavior-bound. |
| VENUE-001 | CLOSED_SOURCE | CTP and XT/QMT scaffolds cannot manufacture venue success. |
| OMS-001 | CLOSED_SOURCE | Journal-before-send, uncertainty and reconciliation contracts match schema v4. |
| BUILD-001 | CLOSED_SOURCE | Canonical target and translation-unit ownership are verified from fresh CMake File API data. |
| RELEASE-001 | CLOSED_SOURCE | Canonical install, immutable publication, descriptor-pinned preflight and exact stable engaged kill-switch validation are behavior-tested. |

`source_state` is `READY`. `paper_authorized` and `live_authorized`
remain `false`.

## Optional IB PAPER activation

IB PAPER is retained as a disabled, qualification-required capability,
not as an unresolved project gap. Nothing in the repository enables it.
An operator who later chooses to activate it must complete the
exact-current-main, immutable-artifact, PAPER-only Broker campaign
documented in [`modules/ib-paper.md`](modules/ib-paper.md). A failed or
absent campaign simply leaves the optional capability disabled.

LIVE trading remains unavailable.
