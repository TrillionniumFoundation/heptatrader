# Exact-head CI verification contract

Status: current
Applies to: pull requests changing the active HeptaTrader runtime, schemas, research protocol, deployment surface, tests, or current documentation
Verification: GitHub Actions `canonical-full-suite` and `core-runtime` on the pull request's exact head revision

A canonical gap may be recorded as closed only when the permanent, read-only
`canonical-full-suite` workflow succeeds on the exact pull-request head. The
fast `core-runtime` workflow remains an early signal and does not replace the
full-suite result. Historical successful
runs, merge-preview results for an older head, locally reported output, temporary
write-enabled workflows, manually created check conclusions, and prose receipts
do not substitute for that result.

The permanent pull-request workflow uses read-only repository permissions. It
checks repository/documentation integrity, deterministic schema bindings,
configuration truth, the capability-free research protocol, Release runtime
builds and core tests, the minimal runtime installation surface, and the
sanitizer/crash/replay/malformed-protocol/performance fixtures. It grants no
mutation authority to Agent or Gateway processes.

Each checkout pins the pull-request head SHA (`github.event.pull_request.head.sha`)
and uses `github.sha` only for push or manual runs; merge-preview refs are not
used as closure evidence.

A same-head failure reopens the affected gap regardless of the state written in
`PLAN.md`. Promotion to Ready for review happens only after the exact-head result
is successful. Approval and merge remain independent reviewer actions.
