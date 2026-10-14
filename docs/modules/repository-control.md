# Repository development and verification

`main` is the maintained integration branch. Integrating source, deploying an
artifact, selecting a model and permitting Broker orders are different actions.
No successful source test, commit or merge grants trading authority.

## Ordinary development

Core Runtime CI accepts PRs targeting **any** branch, including stacked PRs and
Drafts. It checks the exact head without persisted checkout credentials. Native
`./scripts/dev_core.sh` and Python core/source tests run in independent jobs;
Torch installation cannot delay native feedback. Main pushes also run CI.
No approval polling, reviewer-count requirement or cross-workflow receipt exists.
The owner may integrate reviewed, tested source directly; a missing hosted run
is reported as missing, never as PASS and never as a reason to wait indefinitely.

`python3 scripts/run_python_tests.py --lane core` runs portable behavior tests;
`--lane source` runs source/build contracts. `--lane external` retains the tests
that execute separately built Canonical Rust fixtures. Missing fixtures fail that
selected lane rather than becoming a requirement for unrelated core development.
The pure wire-format tests remain in core. All discovered files have exactly one
partition; no skipped selected test is reported as complete acceptance.

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
