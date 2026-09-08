# Gap register

Status: CURRENT  
Applies to: repository HEAD  
Machine source: [`gap-register.json`](gap-register.json)  
Verification: `python3 scripts/check_gap_register.py`

The register separates repository-controlled closure from controls that only organization administrators, protected environments, dedicated runners, host operators, and a real IB PAPER session can materialize. Source code is not allowed to mark an external control complete.

## Repository-controlled closure

| Gap | State | Closure |
|---|---|---|
| DOC-001 | CLOSED_SOURCE | Canonical README, module/capability catalogs, module designs and operations are machine-validated. |
| CI-001 | CLOSED_SOURCE | Required ordinary checks run on PR, main and Merge Queue revisions. |
| TEST-001 | CLOSED_SOURCE | Release tests execute their assertions and the integrated supervisor test compiles. |
| RISK-001 | CLOSED_SOURCE | Generic snapshot/notional/pending exposure/loss/drawdown checks and boundary tests exist. |
| PENDING-EXPOSURE-001 | CLOSED_SOURCE | Qualifying IB PAPER source scope is one active order and one CASH contract until aggregate pending notional is authoritative. |
| VENUE-001 | CLOSED_SOURCE | CTP and XT/QMT scaffolds fail closed and cannot manufacture success. |
| OMS-001 | CLOSED_SOURCE | Public journal documentation and strict verification match schema v4. |
| BUILD-001 | CLOSED_SOURCE | Shared protocol/session implementations replace duplicated compilation and global compiler flags. |

`CLOSED_SOURCE` means the exact candidate contains implementation, documentation, and automated source evidence. It does not authorize merge, PAPER, or LIVE.

## External authorization blockers

| Gap | State | Tracking issue | Required result |
|---|---|---|---|
| G-TEAM-001 | OPEN_EXTERNAL | [#8](https://github.com/TrillionniumFoundation/heptatrader/issues/8) | Live organization teams/grants, zero-error CODEOWNERS readback, active no-bypass ruleset, required checks, genuine Merge Queue revision and verifier-issued governance receipt. |
| G-IB-001 | OPEN_EXTERNAL | [#9](https://github.com/TrillionniumFoundation/heptatrader/issues/9) | Protected environment, distinct restricted runners, pinned builder/SDK/harness, host identity/network/kill-switch controls, exact bounded IB PAPER campaign and verifier-issued broker receipt. |

While either external blocker remains open:

- `paper_authorized` remains false;
- `live_authorized` remains false;
- IB stays `QUALIFICATION_REQUIRED`;
- LIVE stays `UNAVAILABLE`;
- direct/admin merge, synthetic receipts, earlier-head evidence, or manually edited status cannot close the blocker.
