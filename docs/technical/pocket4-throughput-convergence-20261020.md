# Pocket4 throughput convergence — 2026-10-20

## Scope and operational state

This change implements the five-part consumer, training, next-Value, repository,
and progress-display convergence requested for Pocket4. Source is reviewed in
[PR #444](https://github.com/TrillionniumFoundation/heptatrader/pull/444).
All operational work and measurements were performed on Pocket4. The original
working directory and its pre-existing edits were preserved.

| Responsibility | Verified state |
| --- | --- |
| Consumer | Installed release `c625f0e77d5359d99bb5a9b55e8bd8ac2f8aab650043ff5eb640bf922ecb3cc5`; configured and executed through the same content-pinned config |
| Future resident training | Four-epoch v4 candidate completed in a controlled GPU experiment; paired handoff queued as `WAIT_FROZEN_TRIAL_43` |
| Current resident evaluation | Original trial 43 retains its evaluator and fixed outcome window ending **2026-10-20 18:10:14.881 UTC** |
| Next Value | Separate release `2e3734c2a301c0a445e28b20deb9c990072bd00a2cb205420ce1d53036250ce6` staged and sandbox-verified; no new plan registered |
| Progress | `heptatrader progress` and the progress section in `heptatrader status` separate updates, evidence, inventory and permission |
| Trading permission | `BLOCKED` by the independent native kill switch; protected execution, gateway, PAPER and crypto processes were preserved |

The durable evidence root is:

`/home/alex/.local/state/heptatrader/operator-evidence/pocket4-throughput-20261020-1028`

No order, rearm, model promotion or alteration of an old experimental outcome was
part of this operation.

## Consumer: repeated work removed, incremental receipts retained

The maintained exporter was reconciled with the actual incremental exporter
before optimization. Immutable per-episode receipts, the 128 MiB export budget,
the 1 GiB filesystem reserve and the historical snapshots remain in use.

The input checkpoint includes configuration/runtime identity, period, OMS,
producer registrations, journal membership and file identity, health business
fields, output identity and the current dataset hash. A heartbeat timestamp alone
does not force a full replay. A binding, fee, source segment, health error,
configuration or output change does. Future-dated input disables reuse until it
matures; clock reversal and the one-hour deadline also force a complete refresh.

Within a complete refresh, `ExactRequestIndex` reads each required source file
once up to its largest required prefix. Every requested prefix hash, newline
boundary, request identity, membership and economic reconstruction is still
checked. The observed full refresh required 10 prefix reads.

### Measured installed behavior

These measurements used the existing CPU quota, memory limit and 45-second
service timeout.

| Measurement | Complete refresh | Same-input refresh |
| --- | ---: | ---: |
| Refresh wall time | 10.302677 s | 0.083996 s |
| Unit CPU time | 2.706837 s | 0.062516 s |
| Dataset rows | 335 | 335 |
| New durable episodes | 0 | 0 |
| Complete evaluation skipped | false | true |
| Service result | success | success |

The dataset SHA remained
`af7158b9400a55e77ded1740195ec42b4d0704f39c99e6b2b2c27c3680e0ab46`.
The inventory did not grow; input checking and inventory growth have separate
timestamps.

The release inventory now contains the 16 files used by the consumer, replacing
the previous broad research snapshot. The exact maintained closure is in
[install_consumer.py](../../research/historical_training/install_consumer.py)
and is tested by
[test_consumer_runtime_inventory.py](../../tests/python/test_consumer_runtime_inventory.py).
An unrelated resident research edit no longer changes consumer release identity.
This is a file dependency boundary; a changed shared dependency still changes it.

Evidence: `consumer-shadow-verification.json`, `consumer-live-verification.json`,
`consumer-install.json` and `consumer-tests-final.log`.

## Training: fixed-input learning curves and honest accounting

The v4 objective is an explicit opt-in. Each episode keeps one parent, fixed
cutoff, exact dataset, row IDs, frozen labels and batch order. It uses one AdamW
optimizer through four epochs. A rejected update restores both weights and
optimizer state. Unsupported ACTION parameters have no gradient or optimizer
update. Tokenization reuse holds CPU tokenized inputs only.

Checkpoints at epochs 0, 1, 2 and 4 score the same training population. Final
selection is the fixed-budget final checkpoint, not a favorable checkpoint.
Job-to-job initialization still follows the explicit parent contract; this change
establishes optimizer continuity within an episode.

### Controlled Pocket4 experiment

- Cutoff: **2026-10-20 08:41:32.825 UTC** (`1792485692825`).
- Same parent as the existing 16-step job:
  `76d6b1bf3d1b00505277c88a6d93159f3f17fe2511a65f21fc99647cf56b8ff6`.
- Dataset SHA:
  `c05686dc3262e9102b8d33d5e9bb14956983b10d3f701368c9f039129ce9b725`.
- 32 unique rows, batch size 2, 4 epochs, 64 applied steps, 128 presentations,
  zero rejected steps.
- Candidate:
  `6f67016b5d172aa845d02ff4337513849b5a0ad1a9f511ab8990a9de70dd1641`.
- Complete process wall time: **189.101166 s**, within the predeclared **230 s**
  experiment budget.

| Epoch | Applied steps | Unique applied rows | Applied presentations | CE | Brier |
| --- | ---: | ---: | ---: | ---: | ---: |
| 0 | 0 | 0 | 0 | 2.363824434578 | 1.017868539318 |
| 1 | 16 | 32 | 32 | 2.363580167294 | 1.017777074128 |
| 2 | 32 | 32 | 64 | 2.363149024546 | 1.017616434023 |
| 4 | 64 | 32 | 128 | 2.361477103084 | 1.016995117068 |

This is measurable training improvement, approximately 0.10% relative CE
reduction. It is still weak: uniform Brier is 0.857143 and the training-fitted
class prior is 0.677844. That prior reuses the scored labels and is explicitly
not held out. There were 0 ACT-correct and 32 ESCALATE-wrong labels, so ACTION
updates remained zero. Repeated presentations are not counted as independent
new evidence. No future-value claim or new prospective registration was made
for this comparison candidate.

The earlier 180-second attempt applied 64 updates but timed out before a complete
candidate was delivered. Its failure is retained. The first 230-second attempt
never obtained resource admission because PSI was above the existing threshold.
A separately recorded retry used the same 230-second training budget and obtained
admission after slot 26's prediction was committed. Its initial free gap was
352.305 seconds, leaving the 90-second prediction prewarm intact. Slot 27 was
subsequently committed by the unchanged evaluator.

The 230-second budget belongs to this controlled comparison. The existing
production supervisor's 600-second timeout is a separate operational ceiling.
Future admission still uses the existing memory/PSI/global-worker lease and
original full-window rules.

### Preserve the running prospective trial

Trial 43's plan is
`90691957b91519235503257763b0c1376cca8265698e5a3f21ac734f34dc226c`.
At the 11:35 UTC observation it had 28 observed outcomes and zero censored.
Its candidate Brier was 1.028639, versus 1.028729 for its frozen parent,
0.807523 for linear softmax and 0.754767 for the class prior. These are partial,
same-window PRICE observations, not execution PnL or a final qualification.

The operational handoff is recorded under:

`/home/alex/.local/state/heptatrader/operator-maintenance/training-episode-rollout`

At 11:39 UTC preparation was verified. The old trainer and its readmission timer
are disabled/inactive. The original evaluator remains active at PID 1472659,
with its original wrapper and timer. The one-time
`heptatrader-laya-training-episode-rollout.path` is enabled/active; its apply
service has not run. The target v4 runtime is separate from the running evaluator.

The original plan SHA remains
`20654da21d15edaecb553054b57431abc1dd95b14ceb0808a6ab3ebb5465e912`.
All 42 historical terminal plan/qualification pairs were captured by raw SHA.
Both target wrappers changed only their original-config path and SHA; resource,
source and Value priority policies remain the original pinned policies. The
earliest window end is not an unconditional switch time: the complete terminal
record must pass validation before the paired handoff can execute.

The one-time helper verifies the exact original plan, its complete terminal
qualification, all retained historical plan/qualification hashes, pinned target
configs and an unchanged idle job set. It then installs trainer/evaluator units
as a pair. Durable commit state precedes starting the new pair; recovery after
that point only resumes the new pair. Completion is recorded before the path
trigger is retired. Seven fixture cases cover identity, early termination,
historical preservation and interruption recovery.

Evidence: `training-tests.log`, `training-stage-230s-admission-retry.json`,
`fixed-cutoff-v4-experiment/run-receipt.json`,
`fixed-cutoff-v4-experiment-230s/run-receipt.json`,
`fixed-cutoff-v4-experiment-230s-admission-retry/run-receipt.json`,
`training-rollout-tests.log` and `training-rollout-preparation.json`.

## Next Value: verified front-path reuse and actual OPEN timing

The next runtime uses verified per-turn ingress and registered-plan snapshots,
shared prefix revalidation, fresh requests before expired work and phase timings.
Publication deadlines, same-behavior binding and role signatures remain enforced.
The guard uses the original memory/PSI/shared-heavy-worker lease, without applying
Value background priority to itself or sweeping training caches. Guard acquisition
restores the caller's import path so source readiness cannot silently import an
older runtime into the next worker.

New assessments sign the exact `outcome_record_ids` order. Timing reconciliation
checks signed membership, byte identity, semantic hashes, role signatures, counts
and original OPEN time before reporting coverage. Assessments without this new
field retain their original verification path.

### Retained old experiments, exact signed OPEN cohorts

| Experiment | Actual OPENs | Signed before OPEN | Signed at/after OPEN | No verified commit | Pre-OPEN coverage |
| --- | ---: | ---: | ---: | ---: | ---: |
| Original | 304 | 0 | 0 | 304 | 0% |
| Trial3 | 303 | 63 | 126 | 114 | 20.7921% |

Trial3's 114 unverified commits comprise 113 missing predictions and one missing
exact binding. All 189 available predictor signatures and the evaluator signature
were checked against the complete signed cohort. Final verifier SHA:
`104283547206f46bdc82d7912f0e9592f70ce4022365b8514189bade4f215b1f`.
The final reports reproduce the earlier verified counts exactly.

For the 126 late commits, request-to-signed-commit P99 was **65.437 s**,
inference P99 **3.893 s**, and the unallocated pre-inference component P99
**63.049 s**. Fifty late records already exceeded their OPEN window before
inference. These quantiles describe separate distributions and are not additive.
The old experiment did not record detailed front phases, so it cannot distinguish
queueing, resource wait, validation, loading and other overhead precisely.
The next path records those phases explicitly.

### Actual sandbox check and remaining admission conditions

The staged release passed a real CPU-only Landlock/seccomp check using its
frozen source and original candidate evidence:

- 357 runtime files pinned; eight imported module origins checked explicitly.
- 4,432 requests and 335 bindings verified with zero source issues.
- One full ingress scan, two verified reuses; capture **1,203.722 ms**, reuse
  **144.843 ms** in this check.
- Candidate/bound-plan evidence revalidation **38.858 ms**.
- Other role keys and socket creation were denied.
- No torch import, GPU model load, plan registration or broker order.

This verifies the implemented path and sandbox dependency set. Actual future
OPEN coverage remains unmeasured because no new window has started.

Readiness currently reports three concrete blockers:

1. `ACTIVE_PRODUCER_DIFFERS_FROM_FROZEN_CANDIDATE`. The retained candidate uses
   producer manifest SHA `6957e3fc...`; the actual PAPER driver uses `7112db7a...`.
   A candidate/dataset bound to the active producer is needed. Existing binding
   records are preserved.
2. `WAIT_FRESH_ORIGINAL_FLAT_REQUEST_FOR_FROZEN_BINDING`.
3. `NATIVE_KILL_SWITCH_PRESENT`.

The sealed read-only entry `next-value-entry.py` records both producer paths,
role commands and the remaining registration steps. It does not create an empty
future window or substitute a new producer into an old experiment.

Evidence: `original-value-timing-status-final.json`,
`trial3-value-timing-status-final.json`, `next-value-stage.json`,
`next-value-readiness.json` and `next-value-sandbox-cpu-verified.json`.

## Repository and development entry cleanup

| Object | Disposition and retained responsibility |
| --- | --- |
| PRs #396, #400, #408, #411, #414 | Closed after ancestry/tree comparisons; #421 remains the single draft H1 STOP/OCA entry |
| PR #420 | Closed as superseded by merged #426's UID-mock portability fix |
| PR #418 | Closed; its two unique files retained byte-for-byte in #444, including four cache lifecycle regressions |
| Desktop qualification and availability dispatch workflows | Moved byte-for-byte to `.github/retired-workflows`; trust inventories and fixtures point to the new physical paths |
| Consumer release inventory | Explicit 16-file runtime closure |
| CURRENT documentation | Iteration, deployment, IB module, repository control, release acceptance and scripts usage updated |

Original branches, experiments, signatures and runtime dependencies remain
retained. The H1 cleanup does not merge H1 into production or close issue #331.

The CI mapping covers consumer, resident-episode, Value, cache-guard and progress
Python responsibilities. Their paired Python tests retain appropriate core or
learning feedback while skipping unrelated native work. V4 and lineage nested
CPU suites now have explicit learning-lane entries. Unknown/shared/CMake/CI
changes still fall back to full feedback; this mixed PR properly uses that full
path. Top-level new tests and historical-training suites retain their existing
automatic discovery.

## Progress and verification

`heptatrader progress` reports five distinct questions:

- PRICE: actual applied optimizer steps and unique samples.
- ACTION: label support and actual ACTION updates.
- Prospective evidence: fixed plan identity, observed/censored outcomes and
  qualification state.
- Value: durable label inventory, last complete evaluation, last input check,
  actual growth and signed OPEN timing.
- Permission: current PID-bound pause/heartbeat evidence and persistent control
  markers.

Historical `EXECUTION_EVENT_SERVICE_NOT_READY` text remains visible as history.
It is not treated as the current fault. Current driver pauses match the running
PIDs and identify the native kill switch. An absence of detected blockers does
not itself grant trading authority.

Validation groups overlap and must not be summed as a distinct-test total:

| Validation group | Result |
| --- | --- |
| Consumer checkpoints and runtime inventory | 19 passed |
| Resident v4, row worker, lineage, v3 and price baselines | 67 passed |
| Integrated Python behavior | 150 passed |
| Value and resident resource guard | 47 passed |
| Install behavior and bound-plan evidence | 25 passed |
| CI ownership, artifact provenance, Git/index authority and diagnostics | 34 passed |
| Documentation, component/build ownership and source workflow commands | 40 passed |
| Final observer/evaluator and CI-scope regression | 40 passed |
| One-time rollout helper fixtures | 7 passed |

GitHub CI status is separate from these Pocket4 results and is reported in the
PR. Protected service PIDs/restart counts and the kill marker are checked again
in the final deployment receipt.
