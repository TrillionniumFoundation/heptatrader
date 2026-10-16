# Action V2 real frozen-parent GPU experiment — Pocket4 only

This is a **research-only** source candidate. It never writes the canonical
Laya Trainer, Evaluator, source collector, formal trials or model selector,
and has no Broker or paper-trading operation. Do not count a source-only
merge, an admitted resource check, a completed historical trial, or a frozen
CPU score as actual GPU model quality.

## Pinned original data and historical split

Immutable original training-only source (Pocket4):
  ~/.local/state/heptatrader/operator-evidence/laya-original-quote-asof-forensics-20261016/action-v2-real-closed-parent298-trainonly.json

Exact SHA256:
  3d701e3795cb438b5ba38f3bb7c44bed83003e040108d1378d1084635157e4a9

One original predecessor candidate ID:
  76d6b1bf3d1b00505277c88a6d93159f3f17fe2511a65f21fc99647cf56b8ff6

The complete source is 298 real, distinct historical closed original
forecast/outcome rows (14 parent-correct ACT / 284 parent-wrong ESCALATE).
The model's ACT does NOT mean BUY/SELL; it is whether the frozen
predecessor's seven-class price forecast was correct.

For a historical development test only, train on the records with input
and actual matured outcome available strictly before
2026-10-09T00:00:00Z: exactly 178 rows, 9 ACT / 169 ESCALATE.
Hold out all 120 original Oct 9 rows, 5 ACT / 115 ESCALATE.
The full original 298-row source is not independently prospective, and
the holdout has only five positive labels. Do not use the original
full-data 13 matched pairs if claiming the Oct 9 heldout split.

The #378 time-matched pair selector on the training-only population
produces eight genuine, nonoverlapping 1+1 ACT/ESCALATE pairs (3 Oct 7,
5 Oct 8), one unmatched ACT excluded. No synthetic or future labels.
The training-only frozen logits, never Oct 9, set the Action V2 P90
temperature; the correct original 9/178 train prevalence is restored
at inference. Avoid double-weighting already balanced pairs.

## Actual model and bounded GPU contract

Source script:
  scripts/research_laya_action_v2_gpu_isolated.py

- Real on-Pocket4 original source/manifest SHA and exact source row checks.
- Verify same frozen original-parent candidate, base ID, parent
  adapter SHA and full pretrained base snapshot.
- No GPU model load until real pinned
  historical_training.resident_resource_guard.Gate.acquire passes
  Value priority, current original source, memory/PSI and shared lease.
- No user config option to widen dataset, select other parent, weaken
  thresholds, change source location, or grant trading/model authority.
- Compare **actual original GPU seven-class price probabilities**
  against each SHA-bound original parent prediction. Absolute max
  difference must be <=5e-5 on every involved record. Parent correctness
  must match true original target, otherwise abort.
- For FIT: 178 true original train Action logits set one hash-frozen
  training-only V2 profile; 120 holdout labels are read only after
  the profile is determined. No model selection from holdout.
- Pretrained backbone and scorer LoRA frozen, only Action LoRA A/B
  trainable. Eight distinct one-positive/one-negative pairs; no
  duplicated labels or inverse-weighted class loss. Primary price
  CE is read-only numerical reference for Action auxiliary cap.
- AdamW, 1e-4 LR, no decay, 1.0 gradient norm cap, max eight updates;
  stop if Action norm violates existing absolute/relative profile.
  Full heldout GPU forwards **after** fitting must leave the
  original price vector invariant to <=1e-7.
- Numerical results for frozen parent with V2 temperature/prior,
  fitted V2 Action and train-only frequency prior all scored
  on the same 120 previously recorded future outcomes.
  OOD abstentions make the full score ungradable rather than
  silently shrinking the denominator.
- Any model artifact is saved only to a new owner-only
  operator-evidence/ directory. No producer state, release tree,
  selection registry, model candidate directory, formal prediction
  file, or Broker service is changed.

## Resource and execution rules

The tool is invoked only on Pocket4 and only under an isolated
user-systemd scope, **MemoryMax=5G, MemorySwapMax=0,
CPUQuota=150%**, with the actual existing Guard and global flock.
The tool additionally bounds the maximum lease occupancy to 530s,
below the actual Value background nonpreemptible limit of 620s.
The conditions are checked at original admission, not bypassed
using a test-only synthetic "admit" value.

Pilot loads original real parent and probes a bounded set of actual
original quotes, but does not train. Fit reads all 178 training and
120 heldout originals and runs exactly eight paired GPU updates.
The script writes a new, O_EXCL, fsynced receipt with original source
hash, research-only model outcome and no authority. A denial writes
NO_GPU_ADMISSION and exits before any model loads. A runtime exception
writes FAILED, preserving negative evidence, and releases the lease.

Physical observed prerequisite: Pocket4 intermittent Full PSI avg60
above .10 can cause genuine HOST_FULL_MEMORY_PSI_PRESSURE denials;
Value original requests may also temporarily deny. A GPU-idle reading
is not enough to override either. The first isolated GPU attempt
correctly denied under PSI .14 with zero model forwards. Never
swapoff/restart the live Gateway, Driver, telephone agent, or
terminate important user sessions just to force this experiment.

## Verified model-replay failure and numerical isolation (Oct 16)

The **first** attempt was denied by the real Guard for Full PSI 0.14 and
loaded no model. A subsequent actual admitted Pocket4 trial loaded the
frozen parent in ~7.9s and halted BEFORE any V2 optimizer step because
its reconstructed seven-class probabilities disagreed with the previously
sealed original predictions by more than 5e-5. This is not a row-count
issue or acceptable FP32 tolerance.

A second genuinely Guard-admitted physical experiment replayed the exact
same frozen parent, parent adapter SHA and original input receipts on
**three numerical paths** (single example per forward):

| Original sample | ROCm default max abs error | ROCm MATH SDPA | CPU MATH |
| --- | ---: | ---: | ---: |
| first | 0.11482178 | 0.11482178 | 0.11482199 |
| second | 0.09311318 | 0.09311318 | 0.09311748 |
| third | 0.06188410 | 0.06188410 | 0.06188428 |

The original parent ID, adapter SHA, original plan parent directory,
prediction saved input, input digest and frozen parent probability vector
match the archived original receipts. Installed original Torch 2.11
+ ROCm 7.13 versions and archived Laya source version also agree.
The current CPU and GPU paths agree with **one another**, not with the
original sealed predictions. It is incorrect to blame ROCm attention
alone or to silently widen the probability tolerance. One of these three
examples also changes the seven-class top prediction. The historical
ACT-correctness labels are therefore not demonstrably tied to the
currently reconstructed parent. The exact original-versus-now cause
remains UNRESOLVED; no source-model equivalence or fit is claimed.

Immutable private 3-path evidence:
  ~/.local/state/heptatrader/operator-evidence/action-v2-original-parent-parity-20261016-a1/parity-diagnostic.json
SHA256:
  7b2b7e7be0e4706085930de340469dc1d6d42499549df8aa1f3dcf4bb1bc1e36

The separate real pilot refusal receipt is retained under
  ~/.local/state/heptatrader/operator-evidence/action-v2-original-gpu-pilot-20261016-a1/
And the numerical-failure receipt under
  ~/.local/state/heptatrader/operator-evidence/action-v2-original-gpu-pilot-20261016-a2/

## Enlarged real original cohort replay — 24/24 numerical failures

When Full PSI fell and the original shared GPU lease was admitted again,
we ran an immutable real-Pocket4 one-example-per-forward cohort check:
exactly eight evenly selected frozen records from each of Oct 7, 8 and 9.
No optimizer, predictor registration, model swap, history editing or
Broker order was performed. Original frozen-parent prediction receipt
SHA was the same as before; GPU forward model ID and parent adapter were
verified before any private diagnostic write.

| Day UTC | Original rows | Max probability difference | Different highest-probability bin |
| --- | ---: | ---: | ---: |
| 2026-10-07 | 8 | 0.360268 | 4 |
| 2026-10-08 | 8 | 0.206638 | 3 |
| 2026-10-09 | 8 | 0.130769 | 4 |

**All 24/24** original source probabilities violated the predeclared
5e-5 reproduction bound; 11/24 highest-probability bins changed.
No correctly/incorrectly classified *boolean* flipped among these 24,
but none of them was an ACT-positive original, so this does NOT show
label parity for all 14 positive examples or the full 298 records.
It would be invalid to use this observation to relabel the original
frozen parent population or to declare original label parity.

Private create-only evidence:
  ~/.local/state/heptatrader/operator-evidence/action-v2-original-parent-cohort-parity-20261016-a1/cohort-parity.json
SHA256:
  51b578ff7cb766abe040b2188cb4e189d815b3e7a67499b5bdc9f3f206e445a8

The very fact that the 24 original sealed predictors cannot be
reproduced in the same Pocket4 Torch/ROCm revision justifies keeping
the original-parent replay **RED** and withholding Action V2 LoRA
training. It does not independently prove corruption or identify the
precise upstream source of the old numerical mismatch.

**Stop rule: no Action V2 training from the original 178/120 cohort
until every original-parent price probability and correctness label
passes independent pinned re-derivation.** The research V2 GPU runner
encodes this refusal on every actual input; it does not automatically
relabel historical observations, modify production models or authorize
trading.

## Disposition

The initial new no-GPU adversarial regression covers source identity,
censorship, fixed train/holdout cutoffs, manifest/label tamper, immutable
record output, score denominators and forbidden Broker verbs.
The full original model numeric parity and actual fit are independent
physical-host prerequisites. Even a perfectly executed fit **cannot**
authorize model promotion or trades; genuinely new future quotes,
pre-event prediction seals and post-event scoring are still necessary.
