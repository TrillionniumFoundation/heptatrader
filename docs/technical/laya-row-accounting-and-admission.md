# Laya row accounting and observed-request admission

## Scope

This change affects research training and evaluation scheduling only. It does
not change orders, policy selection, the model objective, frozen candidate
weights, existing trial dates, missing-window outcomes, or statistical thresholds.
Runtime and validation stay on Pocket4. Existing historical artifacts are retained.

## Row accounting

The former scheduler reserved up to 64 rows, while the worker performed at most
16 two-row optimizer steps. The new shared batch budget admits at most 32 rows.
A final odd-sized batch is retained. New jobs carry
`heptatrader.laya-gradient-row-accounting.v1` and commit a receipt binding reserved,
backward-pass, applied, rolled-back, and measured row identities. Completion
reconstructs the receipt from actual per-step identities before committing.

The existing `consumed_rows` table remains a reservation/deduplication ledger,
not proof of a successful optimizer update. Failed or interrupted legacy jobs
are not silently replayed or relabeled. Old candidates without the new job
marker retain their old semantics, hashes, and completion records.

## Resource admission

The already deployed original priority implementation and its regression tests
are incorporated from commit `1aa41be729b2bb1c472c833fdab22d84bdfee357`.
Version 1 retains its historical cadence-based reservation behavior.
The opt-in `heptatrader.original-value-priority.v2` reserves only actually observed
flat-position requests whose original 60-second window has not expired.
A later non-flat request cannot hide another still-unexpired flat request.
An estimated next request is diagnostic, not an existing resource consumer.

Both versions preserve the same global nonblocking worker lock, original memory
and source checks, check-after-lock race protection, manual holds, and failure
records. Admission does not confer trading authority or guarantee future request
deadlines while a non-preemptive worker is running. No deadline is extended and
no missing result is backdated. The command-line check-only path applies priority
as well as resource/source checks; a positive preflight does not claim lock ownership.

## Validation

Pocket4 validation of the code change:

- 107 focused admission, receipt, recovery, and qualification tests passed.
- 50 Laya/actual native-bridge tests passed, including three synthetic-model CPU
  tests exercising the real worker and backward pass for 32 rows, a 21-row odd
  batch, and norm-rejected updates. These are not live-market efficacy evidence.
- 705 historical-training tests passed.
- 3,417 core and 114 source-partition tests passed without skips after using an
  executable user-owned temporary directory. Initial `/tmp` execution failures
  were retained; no system mount setting was changed.

Reproduce the lightweight receipt regression with the repository import roots
on `PYTHONPATH` and `python -B -m unittest test_laya_row_accounting`.
Run the historical-training discovery and the existing `scripts/run_python_tests.py`
core/source partitions. The optional Laya native suite uses the installed,
verified binary through `LAYA_RESIDENT_TEST_CONFIG`.

Operational deployment must retain a rollback copy of the effective service
configuration, drain only Laya supervisors, preserve existing trials, and compare
historical artifact hashes and unrelated service identities after switching.
Do not claim GPU training completion or improved forecast coverage solely from
these unit tests or from systemd active state.
