# Runtime integration record — 2026-09-12

Status: CURRENT
Applies to: integration of the September 11 PAPER branches and the September 12 fixes

## Branch reconciliation

The source base is main `f7f0ded8852baf70a37c8934e211a5dba9c55839` and progressive
candidate `2ab3dab1c7e336dbf31925f73521c3afd197f2cf` (PR #67).
The complete source bundle and original commit identities were retained before edits.

| Original branch/head | Content resolution |
|---|---|
| `hardening/immutable-paper-artifact-20260911` — `246070563260d9631fa0f86af01bd063881cdba0` | Exact tree equals main's squash commit `6753c29e8f9be100462a63cbb6785c7651c29ca6` (#64). Retain immutable-artifact behavior, not moving-main polling. |
| `hardening/minimal-compatibility-gates-20260911` — `5a6770404d8a8feac83de6f6b62379a2c4e39f3a` | Exact tree equals current main `f7f0ded8852baf70a37c8934e211a5dba9c55839` (#66). No separate unique source patch is missing. |
| `refactor/paper-progressive-rollout-20260911` — `9b924c97cf78159547a2ebb9504372983548776b` | Compared against v2 across its ten differing paths. Keep v2's evidence symlink rejection and test fixes; replace both revisions' sanitizer shims with real compiler executions and replace text-based gap anchors with executable workflow structure. Redundant merge-context implementations remain non-evidence pending server ruleset retirement. |
| `refactor/paper-progressive-rollout-v2-20260911` — `2ab3dab1c7e336dbf31925f73521c3afd197f2cf` | Direct implementation base. Retain P1 limits, immutable builder, heavy V5 separation; replace self-reported v1 rollout with v2 cross-checked transcripts and persistent continuation. |

No non-main branch should be deleted merely because this record exists. First verify
the actual resulting main tree, test results and original head identities. Source
integration and server-side merge/branch deletion are separate observable operations.

## Implemented behavior

Failure evidence survives wrapper failure and termination. The portable harness,
host-driver protocol, v2 verifier and campaign store are now reviewable source. A
verified stage resumes without sending again; failed/interrupted attempts retain their
identity and block blind retry. Historical artifact selection binds an exact successful
owner/main build and reuses its bytes without rebuilding or following today's main.

Required GCC/Clang status names execute real ASan/UBSan work. Only demonstrably
Markdown-only PR changes are not applicable; Gateway/Session and unknown paths are
not excluded. The periodic job shares this implementation instead of duplicating PR
work. The two historical core/merge shims remain only because the live ruleset still
requires their names; they are not engineering evidence.

Document byte/topic counts and total Gateway symbol count are advisory. Concrete
metadata, capability, privileged-symbol, build-inventory and behavior tests remain.
Native risk source word scans and their spelling-only tests were removed, not replaced
with comments masquerading as proof. The assignment-swallowing risk compatibility
members are removed; stale callers fail compilation instead of silently losing data.

A pure multi-asset exposure assembler reuses authoritative unit/FX valuation, requires
complete matching contract coverage, includes pending buys/sells and rejects overflow,
identity/generation mismatch or incomplete barriers. It is not wired to broaden IB
PAPER profiles. The SHADOW bar builder excludes quotes not fully read before bar close.
Functional research tests exercise timing gaps, aggregation and transaction costs.
Journal diagnostics are bounded, read-only, non-secret and explicitly non-authorizing.

## Verification and remaining operational work

Local non-root canonical CTest passed all 25 tests after the native changes. Full
Python discovery passed 297 tests with one default-skipped install integration test;
the dedicated build-directory-enabled install suite was then run locally and all four methods passed. CI retains the same dedicated step. New native scenarios are
inside the existing risk test executable, not a claim of 25 new test executables.
Remote commit-specific CI remains the integration authority for the submitted head;
this record does not predict its result.

No real Broker campaign was executed by these source tests. The real root-owned host
driver, SDK, qualified account/runtime, host policy and protected campaign admission
remain external prerequisites. Multi-asset IB activation, margin/Greeks integration,
long-duration operating evidence, statistical strategy qualification and true N−1/N
persistent-schema rollback are not claimed complete. The current diagnostics never
rotate journals or clear unresolved order state.

Main was protected by active ruleset `22597364` at inspection: two approvals, strict
required checks and Merge Queue, with no bypass for the connected principal. This
source change does not silently alter that rule or impersonate independent review.
