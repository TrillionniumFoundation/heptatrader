# Pocket4 price-history continuation

Scope: bounded CPU research using immutable Pocket4 inputs. These runners do
not select the resident model or authorize execution.

## Distinct data contracts

The original historical collector's three-class, 15-minute examples retain
their existing identity. The seven-class price path instead uses the frozen
`laya_cell.price_year_gpu` feature builder: 24 features, 12 lagged returns,
a 300-second input-close to next-close target, and
`bisect_right([-10, -3, -1, 1, 3, 10], return_bps)`.
Discontinuous windows are excluded. A new seven-bin dataset has its own array
hash and contract; three-class targets are never relabelled into this task.

## Readout diagnostics

`scripts/research_laya_price_readout_diagnostics.py` verifies the frozen
original source, prediction/outcome receipt hashes, target duration and bin
assignment before evaluating it. The fixed training partition alone fits
smoothed class frequencies, additive class intercepts and inverse temperature.
The pure NumPy implementation is
`research/neuron_learning/laya_cell/price_readout_diagnostics.py`.

Intercepts use fixed regularization and bounded Newton/Armijo iterations.
Temperature fits a fixed one-dimensional convex objective on [0, 10]; a zero
inverse temperature is a uniform-distribution boundary result. Receipts retain
argmax tie counts so a deterministic tie rule cannot be mistaken for evidence
of discrimination. An already-viewed holdout remains retrospective diagnostic
evidence. Neither these fits nor better diagnostic scores reopen a rejected
trial or establish production efficacy.

## Incremental frozen replay

`scripts/research_laya_history_increment_replay.py` checks the frozen model,
train-only standardizer, feature source and merged/base data identities. It
does no fitting. It selects newly matured outcomes by target-close time:

```text
old_closed_bar_cutoff < target_close_end <= last_available_bar_close
```

The old trailing feature bar can acquire its first label after an append.
The last newly downloaded bar still has no future label. Selecting only newly
downloaded feature timestamps would miss the old-tail case. The runner writes
protocol, per-row predictions and comparable prior/model scores.

## Next rolling CPU fold

`scripts/research_laya_next_history_cpu.py` pins the verified merged bars and
original fold protocol, advances the last fold by 15 days, and creates a
separate compressed seven-bin dataset. The saved contract binds target timing,
feature source, gap exclusions and revised-history status.

The runner reuses the existing training implementation on CPU: prior, linear,
and tiny MLP families; fixed per-fold seeds; train-only normalization and prior;
the existing epoch, batch, optimizer and early-stop limits. It writes both
candidate weights and the validation selection receipt before computing test
scores. Selection requires the existing proper-score comparison with the prior.

The planned next test interval may extend beyond the available history. The
result explicitly reports a matured partial historical test, with its available
end time. Previously inspected/revised historical rows are not a new prospective
qualification. Probability-score improvements alone do not establish economic
alpha, especially when the change in classification hits is small.

Run these dated, input-pinned experiments only on Pocket4 with its existing
environment and a bounded temporary CPU service. The completed continuation
used a 1.5 GiB memory limit, zero swap, 50% CPU quota, a 180-second runtime limit,
and AF_UNIX-only access. No additional Broker connection or GPU is needed.
Retain data, weights, selections and full result receipts on Pocket4; commit
only source, tests and method documentation.

## Verification

The readout tests check the analytic gradient, normalization invariances,
intercept repair, absent classes, the uniform temperature boundary and invalid
inputs. The existing `test_laya_price_year_gpu.py` tests verify causal features,
target maturity, cross-boundary exclusions, embargoes and model selection.
History request/session tests and resident entry-guard tests are independent
lanes; their results do not grant model promotion or trading readiness.
