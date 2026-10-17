# Fixed historical action-weight ablation

This Pocket4-only research run compares action weights 0, 0.01 and one weight
frozen from training-only initialization gradients. It reuses the resident model,
raw price/action cross entropy, AdamW (0.0001, zero decay), global L2 clipping at
one and full-adapter/optimizer rollback on the existing norm limit.

The sealed original cohort contains 178 chronological training rows (9 ACT) and
120 previously examined historical holdout rows (5 ACT). The same proportional
approximation of the training pool is fixed for all three arms: 2 ACT and 30
ESCALATE, ordered chronologically, in 16 disjoint batches. Initial uniform temporal
subsampling omitted ACT entirely; this was detected before any holdout forward.
No holdout score chooses samples, temperature, gradient weight or extra steps.

All calibration batches use the same frozen initialization without optimizer
steps. The third arm targets a median action-to-price gradient norm ratio of one
on nondegenerate calibration batches. Disjoint heads are explicitly described as
a global clip-budget heuristic; degenerate batches remain in the receipt.
Training keeps raw logits. A single temperature from all 178 training rows is
used only for additional probability scoring, alongside raw log-softmax NLL.

Original live insertion order is restored only for direct replay of sealed rows.
All 298 parent rows are rechecked against their original probabilities, and the
existing B2 calibration forwards check frozen-parent correctness consistency.
Production rows_for_training already reconstructs causal_state in its original
order and does not require a change.

The active resource thresholds, manual hold, Value priority and original
heavy-worker lease are reused in a separately declared offline research scope.
The existing Landlock/seccomp sidecar sandbox is entered before Torch loading.
No live-source qualification, native candidate promotion, trading or financial
reward learning is granted by this experiment.
