# Pocket4 original-parent Action V2 numerical NO-GO, October 16 2026

This was a real Pocket4 GPU research attempt with the ORIGINAL shared
heavy-worker Guard and its single-writer lease. It is NOT a successful
Action V2 LoRA fit, future trading validation, model promotion or order.
No installed Trainer/Evaluator/Execution/Gateway/FX/BTC unit, frozen plan,
model source, or original closed-trial artifact was changed.

## Input / identity provenance

The original closed training data have SHA256
3d701e3795cb438b5ba38f3bb7c44bed83003e040108d1378d1084635157e4a9,
manifest a6457e18d362989fda2cacb110550538ac985508106439b20d1f4608c631fab3,
298 original observations from frozen predecessor candidate
76d6b1bf3d1b00505277c88a6d93159f3f17fe2511a65f21fc99647cf56b8ff6.
The original first historical prediction plan really points to the same
parent directory; its saved original input state and parent probability
vector match the collected sealed first row. The current model.py,
resident_model.py, dataset.py, resident_predict.py and integrity.py
have exact SHA256 parity with the installed resident Release at inspection,
and the 644 MB frozen base and scorer adapter pass file identity checks.
That does NOT prove historical execution environment numerical equivalence.

A strictly separated older cohort has 178 train rows Oct7-8
(9 ACT /169 ESCALATE) available before 2026-10-09T00:00Z and
120 Oct9 historical holdout rows (5 ACT/115 ESCALATE). Matching only
the 178 true earlier training rows yields 8 disjoint ACT/ESCALATE pairs,
not the 13 pairs available across all 298 already-known historical cases.
The latter cannot be used for a supposedly independent Oct9 holdout.

## Physically observed frozen-parent failure

The original Guard first returned admit_new_worker=true (60s full PSI
0.09 <= 0.10, no unexpired Value request). The private experiment then
acquired that same shared heavy-worker lease, verified and loaded the
actual frozen parent on the Pocket4 gfx1150 GPU, and attempted the
first saved original input.

The HISTORICALLY SEALED parent probability vector was:

  0.21397866 0.03540749 0.11749482 0.10098173
  0.10405884 0.21110259 0.21697582

The PHYSICALLY RECOMPUTED frozen-parent vector was:

  0.26050282 0.03109936 0.07616259 0.10843025
  0.10584861 0.31580225 0.10215405

Maximum absolute seven-class probability drift 0.11482177674770355
exceeds prespecified 0.0002 numerical tolerance, and top-1 changed
from class 6 to class 5. The model fitting script aborted BEFORE
any optimizer step. Do not waive this failure, enlarge the tolerance
or substitute different original inputs.

The source-bound negative receipt is stored privately only on Pocket4:

  ~/.local/state/heptatrader/operator-evidence/action-v2-real-parent-gpu-fit-20261016/parent-parity-mismatch-v1.json

SHA256 e7702a9318f3a4f5b29b5607be42a900eb05650de705ffa2082ad7510724e91b.
The original model/data provenance, two actual probabilities, model ID,
zero gradient steps and no orders are retained.

## Subsequent physical Guard refusal and causal next experiment

A same-input repeated GPU forward experiment was prepared to
distinguish current repeated-forward instability from stable historical
numeric/runtime drift. Actual subsequent admission at about 08:02 PDT
was rejected as HOST_FULL_MEMORY_PSI_PRESSURE: no model loaded and
no repeated same-input results were obtained in that attempt.

Therefore neither GPU nondeterminism, historical runtime change,
checkpoint mismatch, nor input encoding has been conclusively proven
as root cause. Next action: obtain a fresh naturally admitted shared
lease, repeat the EXACT same original input three times with the
same immutable weights, and compare numeric results; only if
intra-run parity holds, investigate CPU/ROCm and historical runtime
lineage with source and module fingerprints.

Research fitting, model superiority, prospective price/action
accuracy and broker-authority claims remain UNPROVEN.
This branch is for SOURCE DIAGNOSIS only.
