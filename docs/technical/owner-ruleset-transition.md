# Owner-operated main protection

The 2026-10-06 consolidation replaces the former cross-workflow status-polling
scheme. One maintained `main` contains all current implementation work.

Retain default-branch deletion and non-fast-forward protection. Remove required
status aggregation, compulsory approvals and Merge Queue from ordinary owner
changes. Core Runtime CI still runs actual native and Python tests and reports
its real result; it does not pretend queued, missing or skipped jobs have passed.

The obsolete `canonical-admission-exact-head` poller and its duplicate workflow-ID
policy were removed, not renamed. Deep sanitizer/SDK checks are explicitly
invoked; tagged releases and IB account qualification remain independently tested.
No source-control rule changes a host's execution permit, account, cash ledger,
original pending command or live-trading eligibility.

Repository-side settings must be checked from the live API; the convergence
receipt records the before/after ruleset. See [branch disposition](main-convergence-20261006.md).
