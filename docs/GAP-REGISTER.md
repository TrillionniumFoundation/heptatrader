# Historical source-remediation baseline

Status: CURRENT
Applies to: repository HEAD
Machine source: [`gap-register.json`](gap-register.json)
Verification: `python3 scripts/check_gap_register.py`

This is a fixed regression baseline for previously reviewed source repairs,
not a live defect tracker or a claim that the project has no unresolved issues.
The historical filename and v1 schema remain for existing tooling. `READY` and
`CLOSED_SOURCE` apply only to these recorded repairs. Do not change issue scope
or close a newly discovered defect merely to satisfy this baseline.

Active defects and feature/retirement decisions belong in the repository's
[GitHub Issues](https://github.com/TrillionniumFoundation/heptatrader/issues),
the single active issue source. Regressions in a recorded repair must still fail
the relevant behavior test and baseline validation. Broker qualification and
host deployment remain separate evidence domains.

The verifier checks source anchors and validator wiring; neither keyword
presence nor a green workflow can establish technical completeness or trading
authority. Actual tests run in their disjoint source/core/install partitions.

## Recorded remediation baseline

| Gap | State | Closure |
|---|---|---|
| DOC-001 | CLOSED_SOURCE | Canonical module/component ownership and document structure/references are machine-validated; technical completeness requires review; the external PAPER harness interface and 12-scenario contract are reviewable source. |
| CI-001 | CLOSED_SOURCE | Core Runtime owns build, core Python, dedicated install, package and lifecycle behavior; GCC/Clang lanes independently own sanitizer coverage; Documentation Control Plane owns source truth. Historical contexts retained solely for the live server-side ruleset are compatibility shims, not closure evidence. |
| TEST-001 | CLOSED_SOURCE | Gap-critical executable/Python tests are inventoried by build facts, and the PAPER scenario document is machine-bound to the executable evidence verifier. |
| RISK-001 | CLOSED_SOURCE | Generic snapshot, unit/notional, pending exposure, loss, drawdown and guarded-exit rules are implemented and tested. |
| PENDING-EXPOSURE-001 | CLOSED_SOURCE | Pending exposure, exact V5 atomic flatten, machine-state nftables replacement/readback, deny-all fallback and Broker-observed qualification scenario/evidence contracts are behavior-bound. |
| VENUE-001 | CLOSED_SOURCE | CTP and XT/QMT scaffolds cannot manufacture venue success; legacy runtimes remain default-off and non-authorizing. |
| OMS-001 | CLOSED_SOURCE | Journal-before-send, uncertainty and authoritative reconciliation contracts match schema v4. |
| BUILD-001 | CLOSED_SOURCE | Canonical target/translation-unit ownership comes from fresh CMake File API data and exact Git-index component coverage. |
| RELEASE-001 | CLOSED_SOURCE | Canonical install, deterministic immutable publication, descriptor-pinned preflight, unprivileged systemd unit/readiness lint, installed simulator execution, and atomic pointer rollback/re-promotion mechanics are behavior-tested; N-1 schema compatibility remains a deployment prerequisite. |

`source_state` is `READY`. `paper_authorized` and `live_authorized` remain `false`.

## Optional IB PAPER activation

IB PAPER is retained as a disabled, qualification-required capability, not as an unresolved project gap. Nothing in the repository enables it. An operator who later chooses to activate it must complete the exact dispatch-main, immutable-artifact, PAPER-only Broker campaign documented in [`modules/ib-paper.md`](modules/ib-paper.md), [`technical/ib-paper-harness-contract.md`](technical/ib-paper-harness-contract.md), [`ib-paper-qualification-scenarios-v1.json`](ib-paper-qualification-scenarios-v1.json), and [`adr/0003-immutable-artifact-paper-qualification.md`](adr/0003-immutable-artifact-paper-qualification.md).

The external SDK, pinned credential-bearing harness, PAPER credentials, TWS/IB Gateway, root-owned host controls and Broker account are owner-controlled runtime inputs. Their absence is not a source-code gap; it simply leaves the optional capability disabled. A failed or absent campaign cannot be converted into partial authorization.

The qualification workflow requires the candidate SHA to equal `main` at workflow dispatch. After the immutable candidate is built, later `main` movement is not a candidate change and does not invalidate the campaign. Any change to the bound source/artifact/builder/SDK/harness/profile/account/host/scenario tuple requires a new campaign.

LIVE trading remains unavailable.
