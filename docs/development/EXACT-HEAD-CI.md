# Exact-head CI verification contract

Status: current
Applies to: pull requests changing the active HeptaTrader runtime, schemas, research protocol, deployment surface, tests, or current documentation
Verification: GitHub Actions `core` on the pull request's exact head revision

A canonical gap may be recorded as closed only when the permanent, read-only
`core` workflow succeeds on the exact pull-request head. Historical successful
runs, merge-preview results for an older head, locally reported output, temporary
write-enabled workflows, manually created check conclusions, and prose receipts
do not substitute for that result.

The permanent pull-request workflow uses read-only repository permissions. It
checks repository/documentation integrity, deterministic schema bindings,
configuration truth, the capability-free research protocol, Release runtime
builds and core tests, and the minimal runtime installation surface. Optional
reliability lanes add sanitizers, crash/replay, malformed-protocol and performance
coverage without granting mutation authority to Agent or Gateway processes.

A same-head failure reopens the affected gap regardless of the state written in
`PLAN.md`. Promotion to Ready for review happens only after the exact-head result
is successful. Approval and merge remain independent reviewer actions.
