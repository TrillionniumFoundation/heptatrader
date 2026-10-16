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

## Actual Pocket4 GPU validation and deployment status

An independent historical-data replay completed on Pocket4 gfx1150 using the
original shared heavyweight lock and worker sandbox. It did not register a
formal trial, update the live scheduling ledger, or promote a model.

- 32 reserved rows, 32 backward-pass rows, 32 applied rows, 32 measured rows.
- 16 optimizer steps; zero rolled-back rows.
- Training wall time 73.108 seconds; total worker time 76.347 seconds.
- Peak GPU allocation 1,590,521,344 bytes.
- Candidate manifest SHA256: `91b358a5c4aa66e1cec67fbbc1b1f53ba2eccdb0753cc5b72bcb746df987b01f`.

This is an implementation/receipt check, not out-of-sample forecast or profit
evidence. The action-label fixture remained entirely ESCALATE; action-head
calibration and generalization are not fixed by the accounting change.

Post-validation checks retained 112 historical manifests/plans/qualifications
and 1,175 historical outcome/adapter files byte-for-byte. Laya, Execution,
Gateway, and both trading-driver process identities were unchanged.

Production service-configuration preparation was blocked by the execution
channel. No runtime unit was replaced, reloaded, stopped, or restarted. The
release is staged and hash-verified, NOT deployed. Existing trial38 was
created independently by the old runtime, not this patch; its original dates
and missed-window records are retained. Source completion, GPU validation,
production cutover, and future-coverage acceptance are separate statuses.

## Viable training queue and deferred formal registration

When an original trial is irreversibly unable to qualify under its own unchanged
censor limit, one new candidate may be trained. Its immutable artifact and row
receipt are committed, but formal evaluation registration is deferred until the
original trial actually closes. The original rule allowing only one PENDING
trial remains enforced inside registration itself. Trial numbers, alpha spending,
end times, missing outcomes and final assessments are not changed.

At most one such completed candidate can wait. A viable pending evaluation or a
waiting trained artifact is checked before acquiring the heavy-worker lease or
rebuilding all rewards/quotes. This avoids wasting the shared lease on a known
no-training iteration. Waiting candidates are reconciled after restart and the
original trial's closure without repeating optimizer steps.

## Deployed follow-through on 2026-10-16 UTC

This section supersedes the earlier staged-only status. The validated row and
observed-request patch was deployed to both Laya supervisors. The subsequent
viable/deferred queue update was deployed to Trainer only. Trading Execution,
Gateway, EURUSD driver and BTC driver retained their original process identities.
No trading model was selected or promoted.

### Autonomous training, not a replay

The live supervisor selected fresh eligible rows and committed candidate
`387481b75280ce115161949ba2e2cd5ab8cef7770e9f1ecfe4d0b096543d56a6`.
All 32 reserved rows participated in gradients, all 32 were applied and measured,
and none rolled back. There were 16 steps on Pocket4 gfx1150, 74.287 seconds
training time, and 1,590,521,344 bytes peak GPU allocation. Completed jobs rose
from 37 to 38; the one historical failed job was retained. The new artifact
was not registered as an overlapping formal trial or promoted.
Candidate manifest SHA256: `181d9fa606dc697d890f4272fc852646af54fb4451be0f2f114e9738f44aeb13`.

### One independently observed five-minute future window

A separate operational check used the already frozen predecessor candidate
`d0d410d3f78485d0996bd91a1b9e869c8adfec713938055c8ea141e68983fe3d`,
not the newly trained candidate above. Input was 799 milliseconds old at
selection; quote read took 538.646 milliseconds. Prediction was committed
3,915 milliseconds after its quote anchor, before the five-minute outcome.
The real outcome was observed with no path gap and a valid original quote
receipt. This is one operational success, NOT an 88/90 coverage acceptance,
a statistical qualification or a profit claim. Candidate interval hit was
false, and action-head Brier was 2.0; calibration remains unresolved.

### Live restart and retained evidence

The deployed Trainer was gracefully drained and restarted after completing
the candidate. Job identities, consumed-row count, candidate hash and trial
counter were unchanged; the counter remains 38. It recovered exactly one
waiting candidate and state `CANDIDATE_READY_AWAITING_FROZEN_TRIAL_END`.
Original trial38 keeps its original end time and missing-window records.
The evaluator still collects its results; no retrospective repair was made.

The final checks preserved 112 historical manifest/plan/qualification files,
1,175 historical outcome/adapter files and all 38 current original plan hashes.
The final code passed 3,437 core, 114 source, 705 historical-training and 50
Laya/native-worker tests, with no skips. Twenty new queue/deferral tests cover
the original trial bound and avoiding unnecessary shared-lock acquisition.

Rollback copies are retained, but a downgrade while a new deferred candidate
exists has NOT been validated: the old scheduler could register it too early.
Do not claim old-version rollback acceptance from the successful new-version
restart. Hosted CI status is independent of these Pocket4 test results.
