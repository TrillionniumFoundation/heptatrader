# Repository development and verification

`main` is the maintained integration branch. Integrating source, deploying an
artifact, selecting a model and permitting Broker orders are different actions.
No successful source test, commit or merge grants trading authority.

## Ordinary development

Core Runtime CI accepts PRs targeting **any** branch, including stacked PRs and
Drafts. It checks the exact head without persisted checkout credentials.
`scripts/ci_scope.py` selects native, Python core/source and learning feedback
for both PRs and main pushes. Unknown/shared changes run every lane; verified
Python leaves avoid an unrelated native rebuild. One runner executes the selected
native, Python core/source and learning steps; skipped lanes do not allocate
separate jobs. Native and core/source feedback precedes Torch installation.
No approval polling, reviewer-count requirement or cross-workflow receipt exists.
The owner may integrate reviewed, tested source directly; a missing hosted run
is reported as missing, never as PASS and never as a reason to wait indefinitely.

`python3 scripts/run_python_tests.py --lane core` runs portable behavior tests;
`--lane source` runs source/build contracts. `--lane external` retains the tests
that execute separately built Canonical Rust and Laya native fixtures. Missing fixtures fail that
selected lane rather than becoming a requirement for unrelated core development.
The pure wire-format tests remain in core. All discovered files have exactly one
partition; no skipped selected test is reported as complete acceptance.
`--lane learning` also discovers every in-package Laya fixture automatically.
Native conformance classes and the native verifier method are explicitly routed
to external; their CPU siblings, including registration recovery and calendar
admission, run in learning without a separately maintained module allowlist.

`--lane legacy` retains the archived Desktop qualification contract fixtures
and fixed HeptaDLL migration/source-disposition inventories.
[Archived qualification contracts](../../.github/workflows/legacy-qualification.yml)
runs that lane when the archived YAML, verifier, test runner, paired tests or
historical inventory JSON changes.
Routine research and driver edits do not run these retired workflow fixtures.
The archived protocol and historical evidence remain available.

Install/process tests still need disposable fixtures, never an active trading
host. Deep sanitizer and SDK integration remain available manually. Tagged
releases require their actual installed artifact acceptance; optional IB campaigns
retain their separate account, identity, network and execution boundaries.

## Scope: do not turn publication qualification into a research gate

| Work | Required scope | Not a prerequisite |
|---|---|---|
| Isolated CPU research / parsing / feature work | Authorized inputs, resource budget, causal labels appropriate to the claim, relevant tests; no Broker credentials | Live quotes, GPU ownership, product Root/Owner enrollment, complete account-return reporting |
| Canonical product integration | Actual typed interfaces, source/identity and recovery contracts of the integration being exercised | Unrelated model profitability or all other product modules |
| Model selection / deployment | Appropriate forward evaluation, costs, compatibility and explicit selection | Re-running unrelated frozen physical experiments |
| Trading recovery / new risk | Operator mode, original-order state, current Broker risk, identity, freshness and risk limits | A green source merge is never sufficient authority |

Historical data with unavailable label-availability timestamps remains development
data, not causal OOS. Unknown behavior propensity restricts estimators requiring it;
it does not prohibit honest supervised diagnostics. Old missing/failed trials and
original evidence are not overwritten or relabeled. Current PAPER masks and the
operator stop remain unchanged by source integration.

## Evidence reuse follows the affected behavior

A prior receipt remains bound to its original source, dependencies, environment
and artifact. It is never rewritten to attest a new hash. Reuse is a reference to
that result with its unchanged scope, not a new experiment or authorization.

For a change demonstrated not to affect executable semantics, protocol, dependency
or deployed bytes, retain the old evidence and record the actual difference in
the normal commit/review. Re-run affected cheap checks, not the whole cross-host
campaign merely because a Markdown file or a verified nonsemantic whitespace
change has a different hash. Whitespace inside strings, generated code, macros or
significant-indentation languages is **not** automatically nonsemantic.

Semantic, protocol, persistence, build-input and deployed-artifact changes still
need the matching regression or artifact acceptance. Source uncertainty means
retesting the affected boundary. No additional approval form, receipt schema or
mandatory impact-scoring program is introduced.

The source-integration waiting instructions in dated phase/campaign reports are
historical and superseded here. Their original measurements, failures, hashes,
model gates and trading qualification limits remain intact.

## Repository integrity

The owner-requested server configuration retains default-branch deletion and
force-push protection, not required approval counts or Merge Queue. Local link
checking remains; prose length, generated navigation and status repetition are not
development gates. The module catalog supports actual Git/CMake reachability.
Journal-before-send, original-command recovery, identity, current quotes, risk
limits, kill-switch and authoritative reduce-only behavior remain mandatory.

## Draft inventory reviewed on 2026-10-25

The 28 remaining open Draft heads were checked against main
`9bfd56f3c15a46666b2e45ccf09da58daeb0d6b4` (including #454), using commit
ancestry, patch equivalence and each PR's own base-to-head diff.
None was an ancestor of main, and every head retained unmatched patches. No PR
in this inventory is therefore classified as fully absorbed by main.

| Entries | Retained work and next disposition |
|---|---|
| #421; older #337, #360 | #421 is the documented H1 STOP/OCA integration entry. Neither older head was its ancestor; complete source absorption still needs proof. Broker protection qualification remains separate. |
| #365 | Durable WhatIf one-shot locking and the original account-isolation result; this is already the consolidated preview entry. |
| #172, #174, #180, #184 | Strict NAV provenance, secondary FX renewal and stale-quote/recovery diagnostics. Retain until nested source absorption is proven. |
| #371, #340 | Read-only PnL/history and frozen-admission auditors whose PR bodies still lack Pocket4 verification; preserve that explicit unfinished status. |
| #425, #388, #383, #381, #378, #374 | Action saturation, original-parent replay failures, confidence baseline, loss correction and temporal-label research. Preserve the distinct experiments and negative results. |
| #376, #328, #232, #213 | Original execution-cost attribution, temporal net-value negative control and the price-movement cost ceiling. Preserve original losses and attribution limits. |
| #294, #286, #276, #223, #218, #217, #215 | Independent source/price audits, the cache SLA failure, finite Ridge outcomes and the zero-alpha control. Keep the original receipts and censored outcomes. |
| #181 | Frozen multiwindow and matched-comparison research, including failed runs and their evidence. |

Nested ancestry did establish that #360 head
`a9b7f6f4888b927dcbf43f7ad070c65bb1139018` is retained by #365 head
`20572a48b17e46e9e58b18ca0a49a3a0561688b9`. #365 is the WhatIf entry, so
that relationship alone does not establish #421 as the complete retaining H1
entry.

Pocket4 also checked the complete base-to-head changes of #337 head
`5f33a55401f4584e9e5a1ecba470d7b81137f579` and #360 against #421 head
`8ea5c5214fbdadce7a60d10648112cd8fbf43b31`, using a temporary index and
`git apply --cached --reverse --check`. Both checks failed on native and
documentation files. Complete absorption remains unproven, so these entries
stay open. No additional duplicate closure was performed.

Any subsequent duplicate closure keeps the original branch and evidence, and
identifies the exact retaining head. Old body statements about account state,
running experiments or CI availability describe their recorded dates; they are
not current runtime observations. Integrate useful independent deltas with
affected tests rather than reopening an entire historical stacked chain.

## Convergence performed on 2026-10-14

The 96-commit cumulative research stack through `e1c4f0ff` was fast-forward
integrated with the development cleanup. Of the original 124 open Draft PRs,
65 unchanged head commits were verified as ancestors of the actual remote main
and their redundant PRs were closed; their branches and evidence were retained.
The 59 other branch tips were not silently merged or deleted. Old branch snapshots
do not inherit a new workflow file automatically: their next integration should
use current main or an explicit workflow-dispatch, not wait for an excluded event.

Local combined source `efadca44` passed 61 native CTest targets, 2,746 Python core
tests, 111 source tests plus documentation links, and 10 separately invoked real
external Rust-fixture tests with zero skips. The real hosted runs remain distinct:
initial checkout TLS failures were repaired with HTTP/1.1, then 12 child-process
import failures were repaired by propagating PYTHONPATH. On `519a2d9d` the hosted
native job passed, but Python still recorded two ten-second CMake symbol-check
timeouts; those failures were not erased, ignored or resolved by widening a test
deadline. The integrated head has a new actual CI run; its result must be read
from GitHub, not inferred from the local results.

No production Execution replacement/restart, driver unmask, stop removal, model
promotion or trading request is part of these source changes.

### Engineering watchdog versus execution freshness

The remaining hosted failures on `519a2d9d` were both the ten-second CMake
subprocess watchdog in the Gateway symbol-boundary fixture. Its assertions test
privilege separation, not a ten-second performance SLO. This development-only
watchdog is now 30 seconds, matching the fixture's existing compiler budget.
All allowed/forbidden-symbol assertions remain, and timeout still fails. This
does not widen any quote TTL, order deadline, runtime limit or frozen experiment.
The old hosted failures remain recorded; a new run, not this edit, establishes
whether the shared-runner failure is resolved.

### Concurrent operational observation

The final read-only Pocket4 inspection found Execution still at PID 1824767 and
its original `486583c` release. FX/BTC drivers were now running at PIDs 2324730/
2324731, with start time 2026-10-14 20:57:45 UTC+08; the old runtime stop marker
was absent. These are changes relative to the initial masked/held snapshot.
This convergence task issued no start/unmask/disarm/order operation, and has not
established which concurrent operator changed that state. Do not report that
the two drivers are still masked, or that zero calls by this task means there
were zero account-wide trades. The new persistent-mode source was not deployed.

Superseded broker-free main-push and PR feedback now cancels rather than
serializing newer heads behind obsolete jobs. Manual dispatch is not canceled by
this rule; release and IB qualification workflows are separate and unchanged.
Canceled runs remain canceled, not passing evidence for a later revision.
