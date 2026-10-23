# Value343 prospective observation

The collector reads original REQUEST, RESPONSE and COMMAND_BINDING journals. It
has no broker connection or order authority. Both BUY and SELL predictions must
be durably sealed before the original response. A score additionally requires
original execution and commission finality. Synthetic test scores are never
market evidence.

The historical fitter, journal cache and original collector were recovered from
deployed source commit 1a11be935598ff8d56a55b4432073cf5e04f154a.
The fitter and cache remain byte-identical. The historical model stays pinned.

## Separate training and prospective producers

The historical training cohort stays in TRAINING_COHORT. The --config option
selects the prospective manifest independently. Registration reads the running
PAPER unit's MainPID, process start ticks and actual manifest argument. The new
freeze stores both cohorts, training request/outcome cutoffs, registration time,
predecessor freeze hash and current producer identity. Requests at or before
activation are never backfilled.

An unfinished previous trial does not block a new registration. Its freeze and
statistics stay intact. Existing receipt closure can continue after a producer
stops or changes. The collector never promotes the historical model, which
failed to beat the training-median MAE baseline on its original holdout.

## Register and run

Create a private, immutable JSON config with schema
heptatrader.value343-rollover-config.v1 and these fields:

- output_directory: a new absolute evidence directory
- source_manifest_path, source_manifest_sha256, source_period_sha256
- previous_freeze_path, previous_freeze_sha256

With the intended release on PYTHONPATH, run
python -B -m laya_cell.value343_prospective --config CONFIG --mode init.
Initialization refuses a mismatched running producer before creating the trial.
Then use --mode status to check the current producer and receipt counts.
The --mode watch loop uses original journal inotify events and the incremental
hash-verified parser; it does not invoke systemctl on the prediction path.
A producer mismatch in status is distinct from a scored economic rejection.

The reusable heptatrader-value343-prospective@.service template uses its instance
name for the immutable release and separate evidence directories. Only evidence
is writable. It has no IP address family and retains the existing 40% CPU and
512 MiB memory budget.

## Focused validation

Run the five synthetic Value343 suites using the checkout import paths:

```sh
PYTHONPATH=research/neuron_learning:scripts python3 -B -m unittest discover -s tests/python -p 'test_value343_*.py'
```

The test directory is added by unittest discovery, matching the CI runner's
fixture imports without relying on the repository root being on PYTHONPATH.
They exercise cohort separation, immutable registration, no retrospective
prediction, response races, fsync durability, hash-chain reuse and actual fee
recomputation. Live archives and hardcoded historical row counts are not CI
prerequisites.
