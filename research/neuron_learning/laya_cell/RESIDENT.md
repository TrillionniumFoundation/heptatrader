# Resident Laya learning: implemented boundary and remaining owner integration

This is pocket4-local, bounded recurrent **candidate** training plus independent
future-price evaluation. It does not enable PAPER, change the legacy confidence
strategy, send an order, or silently select a student into execution. The old
562-parameter MLP remains disabled; its frozen baseline and records are retained.

## What is installed

Three user services are separate from the trading target:

| Service | Responsibility |
|---|---|
| `heptatrader-laya-feed.service` | Read `market.get_quote`, immutable source segments and failures |
| `heptatrader-laya-trainer.service` | Admit distinct mature data batches; launch bounded GPU jobs |
| `heptatrader-laya-evaluator.service` | Predict before each reserved horizon, observe outcomes, assess qualification |

The active configuration and each immutable job declare the scheduling interval,
row budget, worker timeout, queue limit and storage limits. Training requires
at least 16 previously unconsumed mature sample/horizon/outcome identities. The
v2/v3 objectives admit at most 32 rows and 16 disjoint two-row optimizer steps;
older objective records retain their original limits. Source and output budgets
stop work when full, without pruning unfavorable history. Installer defaults
are not evidence of the limits in a running service: inspect its configured
release and loaded runtime identity together.

The bootstrap seed and all previously closed probe sources can enter *later*
training, including sources from censored probes. They cannot retroactively
validate the model used by those probes. No new training label is made from a
missing endpoint or a changed timestamp. Original source files are reverified.
Candidate training retains a fixed reference predecessor until an actual owner
can select a different one; it does not compound unaccepted updates into a
production model. Optimizer state is explicitly reset per bounded job.

The scheduling SQLite file is a recoverable **local spool**, not the canonical
learning ledger. Job fingerprint and consumed rows are committed together;
completed candidates are bound to parent, data and native-binary digests.
Restart either recovers the exact committed candidate or marks an interrupted
uncommitted job failed. It never blindly repeats a partially written job.

## Versioned training policy and the 300-second experiment

`resident_training_v3.configuration_for_release` prepares an explicit successor
configuration. It preserves the fixed parent, state directories, native identity
and prior trial history, and binds the new objective and runtime files. Installing
source code alone does not change a frozen candidate or an existing future plan.

| Objective | Data and action semantics | Future comparison |
|---|---|---|
| v1 | Preserved in its original candidate and job records | Original frozen plan rule |
| v2 | Existing 300/900-second selection and action calibration semantics | Existing frozen plan, with baseline reporting when declared |
| v3 | Mature 300-second history across time buckets; disjoint action pairs use unweighted CE | New plans also require improvement over all three simple price comparators |

For v3, the upstream collector checks the complete available original quote
history before its 512-anchor cap. It requires a mature 300-second endpoint,
original availability timestamps, a continuous raw path and an unreserved row
identity. It spreads eligible anchors across six-hour buckets, combines them
with valid seed history, then applies the final 768-sample cap. The worker still
uses at most 32 rows. Wider historical coverage describes the training pool;
it does not establish independent observations or future model performance.

Action calibration still uses only the frozen parent's genuine training labels.
A new hash-bound `DISJOINT_ONE_ACT_ONE_ESCALATE_UNWEIGHTED_CE` marker tells the
loss that each admitted action batch already has one ACT and one ESCALATE row.
It therefore does not apply inverse-frequency weights a second time. A 4 ACT /
28 ESCALATE pool yields four disjoint action pairs; the remaining rows can train
price prediction. Unsupported one-class batches abstain, and their action
parameter gradients remain `None` so Adam momentum cannot move the action head.
Old profiles without this marker keep their original semantics.

Each v3 future plan freezes the uniform, training class-prior and linear-softmax
comparators before evaluation. On the same observed 300-second windows, Laya
must improve mean paired Brier loss by more than `1e-6` against each comparator,
in addition to the existing parent, bootstrap, observation, censoring and
alpha-spending requirements. Missing or inconsistent baseline scores fail
validation. Old plans without the additional rule retain their original result.
A pass remains shadow qualification; it neither proves trading profitability nor
selects a model for execution. Representation-cache work remains deferred until
this prospective comparison supplies evidence that the model route is useful.

## Original Laya action head, not a new trade-action network

The complete Laya checkpoint and 321,908,995 original parameters remain frozen.
Existing scorer adapters are retained. A rank-one update is added only to the
first 64 columns of the original `act_head.2` matrix, whose shape is 2 x 256.
This is 66 additional coefficients and 128 effective coordinates: total
**2,379 trainable coefficients** and at most **3,968 effective coordinates**.
The native 4,096-delta, 0.5% per-layer and 0.25% global limits are unchanged.

The checkpoint's original action meanings are **ACT / ESCALATE** (answer locally
or request escalation), not BUY / SELL. The original configuration gives a wrong
answer cost and an escalation cost. The supervised correctness/routing target
compares the frozen predecessor's predicted interval with a later real market
label. The original v1 objective adds ACT/ESCALATE cross entropy at weight 0.01;
v2/v3 use their declared train-only calibration and bounded action contribution.
Targets are fixed before the candidate update. Market prediction supervision
stays primary, with an optional
exact-input remote-distribution teacher. Remote confidence and model prose never
become outcome truth, win probabilities or broker authorization.

This means a trained action tensor proves parameter learning, not an improved
routing policy. Highly imbalanced error targets, saturated logits, calibration,
and future performance still need independent evaluation. All frozen-parameter
hashes are checked. Only actual gfx1150 ROCm execution is supported; no CPU
training fallback and no random replacement backbone exist.

## Financial rewards: separate and exact, not invented policy gradients

`resident_rewards.current` invokes the existing `hepta_pnl_accounting.replay` on
the complete committed OMS prefix. It preserves command identity, broker
execution identity, callback epoch, corrections and actual commission handling.
A closed flat-to-flat episode also needs the original OPEN command linked to its
exact exported input and forecast. Missing commission is not zero; incomplete
settlement, unbound input, nonzero opening holdings and unsupported cross-zero
lot splits remain blocked. Both positive and negative net results are retained.
Repeated commission callbacks cannot double-credit the same execution.

Reward scope is realized strategy execution PnL after actually reported USD
commissions. It excludes financing, tax and unreported non-execution charges and
is not account NAV, all-in return or LIVE profitability. Historical reward bytes
are kept even after correction; the active reward set is rederived on every pass.

**A remote behavior reward is not an on-policy Laya reward.** The current
training class is a future value-target input, never an unsupported off-policy
policy gradient. This resident version records available rewards but does not
train a financial critic/value head from them. No qualifying executed round trip
means `pnl_reward_ready=false`, not a manufactured zero-return training target.

## Independent automatic qualification, not completed automatic promotion

Each candidate gets a fixed future plan with slots six minutes apart, each
predicting a five-minute price interval with a one-minute input window. The
plan's frozen rule determines slot and minimum-observation counts: the legacy
rule uses twelve slots and ten observations, while the declared power protocol
can require a larger plan. Missing and censored slots are preserved.
Qualification also requires the plan's censoring limit, at least two outcome
classes, positive mean paired Brier improvement over the fixed predecessor,
and no worse interval hit count than the frozen bootstrap. A one-sided paired
sign screening rule uses a
summable alpha allocation `0.05 / (trial * (trial + 1))` across repeated candidates.
Nonoverlapping intervals do not prove independent market observations; this
screen is not a complete causal/clustered statistical efficacy qualification.

The plan binds evaluator/predictor/rule code and candidate identity. An old
pre-deployment plan lacking those bindings is retained as rejected, not rewritten
or silently resumed under changed semantics. Candidate, input, timestamps and
weights cannot be substituted. Child prediction never imports or runs an
optimizer. No additional remote model requests are made just to obtain a score:
parent and bootstrap remain the paired reference models, with the additional
frozen price comparators required by new v3 plans. An unpaired remote output is
not presented as a same-input comparison.

A statistical pass has status `QUALIFIED_AWAITING_CANONICAL_OWNER`, and **cannot
change a selected model**. It prepares an explicitly unadmitted local owner-outbox
record. There is no configured owner endpoint or inferred owner acknowledgement.
This version therefore does NOT complete true automatic promotion. The fixed
reference is not silently switched when the local screen passes.

### Source readiness before allocating a forward trial

The resident resource guard checks prospective source readiness inside the
existing registration transaction, through the supervisor's proposal hook.
For a calendar-feasible new plan, both the proposal before source capture and
the proposal after capture must pass the same source, memory and pressure
checks used by the evaluator. Historical training's resource-only admission
does not waive this prospective source check.

A denied preflight rolls back before a trial or reservation is allocated.
The completed candidate stays in the existing waiting queue and is retried;
waiting also prevents additional training jobs from accumulating. The
`FORWARD_REGISTRATION_GUARD_HOLD` diagnostic identifies that candidate and is
not a performance rejection. Existing immutable plans, reserved publications
and terminal receipts retain their original recovery paths and times.
Non-registration calendar probes do not invoke the guard.

`trainer-forward-admission.json` records the subject, check time and reasons
separately from actual worker admission. Passing this preflight grants no
worker lease or GPU access. Each future prediction still needs the original
launch-time source/resource check and shared heavy-worker lease; later source
loss can still censor its original slot. Evaluator admission deferrals are
recorded in the existing per-plan diagnostic path when supported by its runtime.

## Canonical Agentd boundary: explicitly incomplete

The canonical source snapshot is `hepta-private-ci@78fdb0cf...`. Its
`learning.operator` root is `codex-rs/hepta-bellman-operator`, and the existing
Agentd owner integration test exercises an authenticated terminal-value table,
not the Laya GPU worker. A full integration requires all of the following:

1. Product host enrollment of distinct generator, observer and evaluator trust
   identities and current key/controller bindings; no fixture keys or renamed
   strings can substitute.
2. Actual `LedgerWriter` append of authenticated V2 decisions, outcomes and
   corrections, plus independent durable witness; source freezes derived by that
   owner rather than accepting a caller's JSON as a canonical snapshot.
3. A registered Laya tensor-training input under the learning operator and exact
   source/target/estimator bindings, not invoking the tabular-fit API with fake
   data or labelling a direct PyTorch job as an Agentd call.
4. Actual artifact publication through scoped current withdrawal head,
   payload/registry/witness durability and owner acknowledgement; independent
   evaluation admission followed by the separate selection owner.
5. Runtime final-use checks, revocation and restart/rollback across that whole
   chain. A native norm proposal does not grant these capabilities.

Relevant existing contracts (kept unchanged):
`docs/lane-e/END_TO_END_LEARNING_SEQUENCE.md`,
`codex-rs/hepta-learning-ledger/DURABLE.md`,
`docs/modules/learning.operator/TECHNICAL.md`, and
`codex-rs/hepta-agentd/tests/terminal_cell_owner.rs`.
No canonical Agentd binary/owner has been enrolled on pocket4 by this version.
`canonical_agentd_owner_connected=false` is an actual unfinished boundary, not a
successful bridge receipt disguised as full product activation.

## Runtime installation and confinement

Use `laya_cell.integrate_resident_native` to generate a NEW bridge source from
`research/neuron_learning/native/main.rs`; `build_native.py` then compiles the
pinned original native libraries with both Laya proposal operations. All core
Rust sources remain unchanged. Build artifacts and a generated combined source
stay outside versioned code. `resident_proposal.rs` and the generator are tracked.

`laya_cell.install_resident --config CONFIG --start` creates a content-addressed
release with exact Python, accounting and native bytes. `--update-owned` is
required to replace any of the three already-owned service units; previous units
are backed up, and old runtime releases are not overwritten. No other unit is
stopped or altered. Each service has CPU, memory, task and low-priority limits.

`retained_registration_configs` binds an older DEFERRED receipt to its original
config and runtime. The isolated reader executes only its original negative
calendar and serial queue check; an unchanged wait is not rewritten. A possible
positive registration reports `ORIGINAL_FORWARD_RESOURCE_GUARD_REQUIRED` until
its original guarded registrar can act. The original calendar can still close an
expired unallocated candidate. Such retained waits remain visible and release
only the explicitly opted-in future candidate slot; they do not release viable
trials, restore alpha, move deadlines or bypass resource admission.

The guard release pins its common, live-source, priority and wrapper modules.
The optional `TERMINAL_TASK_COMGR_ONLY_V1` profile also pins
`resident_comgr_cache.py`; trainer and evaluator can use the same reviewed
guard release with their own existing resident configurations. This profile
retains the already-deployed cleanup of terminal compiler-cache files under
the shared heavy-worker lock, including identity checks and cleanup receipts.
It does not remove model artifacts, source evidence or active worker files.

GPU workers enter existing Landlock/seccomp confinement before torch threads
start. Only declared assets and input prefixes are readable; writes are confined
to the task output. Credential reads, new sockets, process-memory access and
out-of-output writes are denied. Actual denial-probe receipts are stored per
worker. The supervisor needs read-only service status and the feed uses the
existing read gateway; neither contains an order operation. This is not a defense
against a privileged user rewriting trusted application code, and is not itself
canonical Agentd enrollment.

Stopping the three new Laya services leaves PAPER and the frozen bootstrap alone.
The committed candidate, inputs, native proof and missed windows remain available
for diagnosis. No schema flag, local metric or JSON status enables LIVE trading.

## Independent reward bookkeeping

Reward/settlement synchronization runs before the candidate queue and heavy-worker
admission checks, under the existing scheduler lock. Waiting for a frozen trial,
a prepared candidate, or a free GPU must not freeze observed execution rewards.
The same process replays at most once per 60-second monotonic interval; a restart
refreshes immediately. `reward_sync_at_ms` reports source-refresh time separately
from service liveness. Conflicting reward identities roll back database updates;
an unavailable source clears current eligibility without deleting prior evidence.

This does not take the GPU lease, collect new training quotes, reserve training
rows, overlap frozen evaluations, change promotion rules, or send orders. Existing
storage bounds remain effective. Observed execution rewards remain distinct from
input-bound model credit; negative and unbound outcomes are not discarded.

## Prospective v7 PRICE input

The opt-in v7 objective renders the existing ten causal quote-state features
as compact PRICE input, with fixed six-place rounding and no fitted statistics
or labels. Original raw quote receipts remain immutable. ACT inference still
uses a separate raw-input forward, matching its closed-source training profile.
The child uses compact input; the paired predecessor uses its own artifact input
profile (legacy artifacts default to raw quotes). The comparator remains
`FROZEN_PARENT_RAW_PRICE_SOFTMAX`: predecessor calibration is not applied.
Comparing calibrated predecessor outputs would require a separate prospective
protocol. Zero-step compact child receipts separate input effects from gradient
updates. v6 recency, 64 PRICE steps, native masks/norm limits, Brier calibration,
exact-prior no-trial rule and all original future gates remain unchanged.

## Prospective v8 raw PRICE and centered calibration

v8 restores the original raw quote PRICE input and retains the job-local frozen
scorer-input cache. ACT keeps raw input. The predecessor still uses its own
artifact input profile; when that profile is raw, zero-step child and predecessor
probabilities must match exactly. Published v7 artifacts and plans retain their
original compact-input and calibration semantics.

Calibration v3 uses the same training rows: `q = prior + alpha * (p - mean_p)`.
Its Brier-optimal coefficient is bounded by
`0 <= alpha <= min(1, min_j prior_j / mean_p_j)` for positive `mean_p_j`, so every
future probability vector remains in the simplex. The fitted mean is the prior;
a constant output cannot gain merely by sharpening class frequencies. Constant
probabilities or labels have exactly zero covariance. Zero alpha retains the
existing no-forward-trial rule and allocates no alpha budget.

These are training-only measurements, not held-out or future quality evidence.
The same 32-row/24-hour selection, 64 PRICE steps, native masks/norm limits,
resource admission and all original future observation and baseline gates apply.

### Prospective same-budget frozen-parent comparison

New v8 releases can opt in to
`parent_calibration_comparator_profile=FROZEN_PARENT_SAME_ROWS_CENTERED_BRIER_V1`
using `configuration_for_release(..., parent_calibration_comparator=True)`.
The release pins `resident_parent_calibration.py` and all affected training,
registration, prediction and assessment code. Each newly admitted job fits the
same centered closed-form Brier calibration to the frozen parent's existing
forward receipts on exactly the child's selected rows, labels, cutoff and prior.
This adds no encoder call, optimizer step, selected training row or future refit.
The parent adapter and manifest remain frozen and bound to the comparison.

New immutable plans carry that comparison into prediction and report the child's
paired future Brier gain against the calibrated parent, alongside the original
raw-parent gate. Identical raw parent and child probabilities therefore receive
identical calibration output. If the input profiles differ, the report explicitly
does not isolate parameter updates. The paired report is descriptive and does
not change promotion thresholds, alpha spending or trading authority. A job
without the declaration cannot acquire it when its evaluation is registered;
existing artifacts, plans, slots and results are not upgraded or backfilled.

### Fixed-slot dispatch and publication time

The evaluator processes known failed or expired slots before admission, then
dispatches eligible prediction workers before decoding mature outcome archives.
Plans with an eligible current-runtime slot take priority over historical CPU
closeout. New evaluator releases use a 30-second model-preparation lead, shared
by scheduling, worker timing, status and futility receipts. This shortens the
time a prepared child holds the heavy-worker lease before its slot. The existing
Guard's longest idle poll delay is ten seconds; its original
resource checks and bounded denial retries still decide admission.

This lead belongs to the evaluator runtime pinned by each newly registered
plan. The trainer and evaluator must declare the same evaluator source pin for
new plans. Keep unfinished plans on their original runtime and lead; an upgrade
must not edit their pins or re-time their slots. The 120-second registration lead,
fixed input/outcome deadlines and statistical rules remain unchanged. The wall
clock is checked again after resource preflight immediately before process
creation, so an expensive check cannot start an already missed slot. No
historical slot is rescheduled or filled later.

Model-ready timing includes less than the complete input path: optional source
decoding may still run until the original slot start. That preparation selects
no input and grants no source authority. The worker waits for the fixed slot,
rehashes the original source at use and retains its original input deadline;
insufficient preparation time or resource pressure does not extend the window.

New plans declare `FIXED_SLOT_DURABLE_PUBLICATION_V1`. The existing phase file
records model-ready time, remaining input budget, the fixed input and prediction
deadlines, write start and actual completion after prediction file/directory
fsync. Its final publication receipt binds the immutable prediction digest.
Future scoring uses that observed publication time; a late publication is
censored, and a crash before the publication receipt is conservatively censored
after the original outcome grace. Payload preparation time is not presented as
proof that a prediction was durably available. Legacy plans keep their original
publication semantics and evaluator runtime.

### Validation reuse within a single call

The current v8 adapter invokes the underlying PRICE/closed-ACT validation
directly after checking its own exact objective and parent input. It no longer
re-enters v7 and v6 just to project the objective repeatedly. One independent
candidate verification reads and hashes the selected PRICE population once,
then checks child calibration, input-effect receipts and the optional parent
comparison against those same rows. Standalone verifiers still read originals;
there is no persistent dataset or adapter trust cache.

The guard can pass its just-completed configuration and queue preflight to the
same controller call. A changed configuration invalidates that reuse, actual
training admission rereads storage after the resource gate, and the worker
validates its pinned runtime again in the separate process. Pending evaluator
progress reuses immutable outcomes read during that round and adds only its own
new writes. Terminal qualification rereads the original outcomes. Proven-futile
plans remain in CPU closeout and cannot take dispatch priority from viable plans.
