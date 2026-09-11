# Gap register

Status: CURRENT
Applies to: repository HEAD
Machine source: [`gap-register.json`](gap-register.json)
Verification: `python3 scripts/check_gap_register.py`

HeptaTrader is an owner-operated, self-use system. Every gap in the supported baseline is closed in source and none blocks repository use. Source closure and Broker authorization remain separate claims.

The gap verifier checks concrete repository evidence, complementary CI ownership and executable-contract agreement. It deliberately does not treat duplicated workflow command strings, workflow file shape, issue labels or repository-governance ceremony as trading-safety evidence.

## Complete supported-scope closure

| Gap | State | Closure |
|---|---|---|
| DOC-001 | CLOSED_SOURCE | Canonical module/component ownership and substantive technical-document depth are machine-validated; the external PAPER harness interface and 12-scenario contract are reviewable source. |
| CI-001 | CLOSED_SOURCE | Core Runtime owns full build/Python/install/package/preflight/lifecycle behavior; GCC/Clang lanes independently own sanitizer coverage; documentation and exact-merge lanes validate source truth without rerunning the complete suite. |
| TEST-001 | CLOSED_SOURCE | Gap-critical executable/Python tests are inventoried by build facts, and the PAPER scenario document is machine-bound to the executable evidence verifier. |
| RISK-001 | CLOSED_SOURCE | Generic snapshot, unit/notional, pending exposure, loss, drawdown and guarded-exit rules are implemented and tested. |
| PENDING-EXPOSURE-001 | CLOSED_SOURCE | Pending exposure, exact V5 atomic flatten, machine-state nftables replacement/readback, deny-all fallback and Broker-observed qualification scenario/evidence contracts are behavior-bound. |
| VENUE-001 | CLOSED_SOURCE | CTP and XT/QMT scaffolds cannot manufacture venue success; legacy runtimes remain default-off and non-authorizing. |
| OMS-001 | CLOSED_SOURCE | Journal-before-send, uncertainty and authoritative reconciliation contracts match schema v4. |
| BUILD-001 | CLOSED_SOURCE | Canonical target/translation-unit ownership comes from fresh CMake File API data and exact Git-index component coverage. |
| RELEASE-001 | CLOSED_SOURCE | Canonical install, deterministic immutable publication, descriptor-pinned preflight, installed simulator lifecycle rollback/re-promotion and stable engaged kill-switch validation are behavior-tested. |

`source_state` is `READY`. `paper_authorized` and `live_authorized` remain `false`.

## Optional IB PAPER activation

IB PAPER is retained as a disabled, qualification-required capability, not as an unresolved project gap. Nothing in the repository enables it. An operator who later chooses to activate it must complete the exact dispatch-main, immutable-artifact, PAPER-only Broker campaign documented in [`modules/ib-paper.md`](modules/ib-paper.md), [`technical/ib-paper-harness-contract.md`](technical/ib-paper-harness-contract.md), [`ib-paper-qualification-scenarios-v1.json`](ib-paper-qualification-scenarios-v1.json), and [`adr/0003-immutable-artifact-paper-qualification.md`](adr/0003-immutable-artifact-paper-qualification.md).

The external SDK, pinned credential-bearing harness, PAPER credentials, TWS/IB Gateway, root-owned host controls and Broker account are owner-controlled runtime inputs. Their absence is not a source-code gap; it simply leaves the optional capability disabled. A failed or absent campaign cannot be converted into partial authorization.

The qualification workflow requires the candidate SHA to equal `main` at workflow dispatch. After the immutable candidate is built, later `main` movement is not a candidate change and does not invalidate the campaign. Any change to the bound source/artifact/builder/SDK/harness/profile/account/host/scenario tuple requires a new campaign.

LIVE trading remains unavailable.
