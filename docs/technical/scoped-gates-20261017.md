# Scoped admission and routine development — 2026-10-17

This change removes checks from operations that cannot benefit from them, while
keeping the checks at the boundaries they actually protect.

## Runtime behavior

| Operation | Required evidence | Removed dependency |
| --- | --- | --- |
| Reconcile a pending command | Original command identity and authoritative settlement | A new market quote |
| Owner-fence or existing HALT close | Valid position/order snapshot and native reduce-only CLOSE | Driver-side quote collection before attempting the close |
| Price-based exit or new entry | Fresh authoritative IB quote and existing entry/native checks | None |
| Historical trainer admission | Original config/period/runtime pins, resource reserves, manual hold, shared worker lease and Value priority | Current live quote/continuity readiness |
| Prospective evaluator admission | The same resources plus original live-source freshness, prefixes and continuity | None |
| Account observation query | Completed v3 Broker download and source-verified arithmetic reconciliation | Full-account return, external liability and tax qualifications |
| Ordinary independent Python tool change | Every core/source Python test and documentation checks | Full native rebuild and Torch installation |

A CLOSE still follows the existing native prepare/submit operation. Missing
driver quote diagnostics never supply a default order price or evidence of a fill.
Unknown sends retain their original command ID. Native execution may still reject
a close if its own order requirements cannot be met.

Historical resource admission is not dataset approval. The resident trainer still
collects and validates retained historical sources, checks mature labels and
consumption identity, and applies its original queue, cadence and storage rules.
Trainer and evaluator retain the same global heavy-worker lease. A source hold
still denies prospective evaluation.

## Account observation contract

The account object adds:

- `observation_complete`: completed capture and reconciled source arithmetic.
- `observed_nav`: the validated observation, or null.
- `observation_observed_at_ms`: its source observation time, or null.

The parent report adds `account_observation_complete` and
`account_observed_nav`. Existing strict `nav`, `nav_complete`, `complete`,
`account_nav`, and all-in return fields retain their previous qualifications.

`heptatrader pnl --account` returns 0 when the current account observation is
complete and 2 when it is unavailable. Strategy PnL commands keep their existing
execution-accounting exit status. Funding-adjusted return, personal tax and
outside-account liabilities remain individually unknown when their evidence is
missing. No arithmetic observation resets the accounting period or creates an
opening NAV receipt.

## Maintenance visibility

`heptatrader maintenance-status` reports the post-action native marker origin,
the current blockers, and the persistent hold's recorded owner, age and optional
context. Older records remain readable; an unrecorded reason stays null.

New holds may include optional context:

```sh
heptatrader maintenance-hold --reason "planned driver update" \
  --task "driver update" --resume-when "original owner completes the maintenance procedure"
```

These fields do not create a timeout or an automatic release. Repeated hold
creation preserves the first record. A failed release restores the exact original
record. Native independent holds and service-stop protections retain their
existing authority.

## Python CI

`scripts/run_python_tests.py` has explicit, disjoint core, source, learning,
install, process and external lanes. Newly discovered tests default to core.
The learning lane contains the Laya action numerical tests and the seven archived
kernel parity tests formerly nested inside the historical suite. Those tests run
with Torch; missing dependencies are not accepted as skipped success.

`scripts/ci_scope.py --base SHA --format github` always emits
`run_native`, `run_python` and `run_learning`. The only Python-only exemption
covers exact paths for the account observation/report and maintenance tools and
their paired tests. Missing or symlinked paired files, an unknown/shared path,
changes to CI itself, an unavailable base, or an uncertain diff restore full CI.
Every Python-only change still runs the entire core/source suite.

Core/source runs before installing the pinned CPU Torch dependency. Native
feedback remains independent. Existing exact commit checkout, manual integration
suites and release/Broker qualifications retain their respective scopes.

## Pocket4 verification and deployment

Run executable test fixtures in a private Pocket4 development directory when
the operator session's default temporary mount is noexec. Do not remount it or
relax its permissions. Core/source must pass in an interpreter without Torch;
run the learning lane separately with its existing dependency.

Deploy versioned copies after verification. The trainer update retains the
original configuration, period, queue, consumption state and shared lock paths;
drain its current wrapper before switching the service reference. Keep the
prospective evaluator's frozen release unchanged. Point the PnL service and CLI
observer selector at the same release.

A verified trading-driver candidate is distinct from a running service update.
If the existing maintenance entry has no stop authority, retain the prepared
candidate and report that deployment as pending. Do not clear native holds,
rewrite historical state, or weaken service-stop protection to activate it.
