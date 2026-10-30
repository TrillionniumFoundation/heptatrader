# Current100k centered residual readout

This is an explicit new candidate profile. Existing candidates and prospective
plans retain their original tensors, producer identity, runtime pins, windows,
and statistical trial number. It does not select an actor or send orders.

## Why a separate profile

The legacy readout starts both its linear weights and bias at zero. For identical
unit-norm features in the quadratic part of Huber loss, its common prediction
after 16 SGD steps at learning rate 0.05 is
`mean(target) * (1 - 0.9 ** 16)`. With training mean -12.52655 USD this is
-10.205355 USD. Almost collinear embeddings therefore spend the fixed budget
learning the common target offset, with very small updates for input differences.
This mechanism can be tested without interpreting training fit as future skill.

The opt-in `CENTERED_RESIDUAL_PROFILE` in `laya_cell.financial_value_profile`:

- freezes the original training feature mean and RMS residual magnitude;
- centers new features using those frozen values and projects them into the unit
  ball, with a fixed RMS floor for identical inputs;
- freezes the training target mean as the initial baseline, clamped to the
  existing prediction bound, without clipping observed training labels;
- fits the same 769 residual parameters for the same 16 SGD steps, keeping the
  original 0.1 parameter-norm bound;
- bounds published predictions to the existing 15 USD absolute limit.

The additional frozen buffers are part of `value-head.safetensors`. The forward
plan binds the exact profile and its source code. Future inference only loads
those buffers; it never derives normalization or baselines from future requests
or rewards. A missing or changed profile cannot reinterpret the new tensor set.
Omitting `readout_profile` keeps the legacy algorithm and tensor schema.

## Running a new candidate on Pocket4

Stage a separate reviewed runtime and a new trial configuration using the
existing resident guard and pinned HIP runtime. Retain the selected frozen
dataset path and hash. Set `readout_profile` to an exact copy of
`CENTERED_RESIDUAL_PROFILE`, preserve `steps=16`, `max_rows=64` and
`scale_usd=100`, and choose a fresh, nonexistent output directory. The source
inventory must include the trial module, `financial_value_train.py` and
`financial_value_profile.py` at their staged absolute paths, along with the
existing complete runtime inventory.

Run through the normal bounded entry point; do not bypass admission to warm the
GPU or refit an existing artifact:

```sh
python -B -m historical_training.current100k_value_trial --config NEW_TRIAL_CONFIG
```

The existing runner replays authoritative rewards before and after fitting,
preserves the frozen backbone, and publishes a new immutable candidate. The new
diagnostic reports feature RMS, target and prediction standard deviations, and
training MAE against frozen mean, median and zero baselines. It labels these as
training fit, with no efficacy claim. Registration must create a new prospective
trial under the existing signed framework. The original trial is not amended.

The geometry change only removes the optimization bottleneck. With at most 64
training rows, causal input information may still be insufficient; amplification
of small embedding differences may amplify noise. Future performance must beat
the original frozen baselines under the unchanged trial budget and observation
rules before any efficacy claim.

## Focused regression checks

On Pocket4, use the existing runtime Python with `OMP_NUM_THREADS=1` and
`OPENBLAS_NUM_THREADS=1`, and set
`PYTHONPATH=research/neuron_learning:research:scripts` from the repository root:

```sh
python -m unittest discover -s research/neuron_learning/laya_cell/tests -p test_financial_value.py
python -m unittest discover -s research/historical_training/tests -p test_current100k_value_trial.py
python -m unittest discover -s research/historical_training/tests -p test_current100k_forward_scope.py
```

These checks use synthetic tensors and isolated fixtures. They verify the legacy
finite-step mechanism, input discrimination under nearly collinear features,
constant-input behavior, frozen serialization, output bounds, source inventory,
and candidate-to-plan binding. They are not training or trading efficacy evidence.
