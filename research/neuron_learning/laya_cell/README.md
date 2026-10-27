# Pocket4 Laya DecisionCell: real pretrained specialization

This directory specializes the full reviewed `convaiinnovations/laya-multilingual`
checkpoint. It is not the old 562-parameter randomly initialized MLP renamed as
Laya. Existing remote PAPER entry/exit, native quote/risk checks and trading state
are outside this change. A stopped PAPER driver is not started by these tools.

## Model and scope

The base is pinned to `e4e9ddf21a7b1903b7acffd8814ad4307bf63a67` and the reviewed
Laya implementation to `a4a8921afebfd852bba0000475cfb6ab737a124c` / 0.3.28.
Upstream LFS SHA-256 or Git blob identities are checked for every loaded model,
encoder config and tokenizer file. Local cache bytes are not trusted just because
the cache is named verified. Complete `model.safetensors` loading is strict;
encoder-only, missing scorer, pickle, unknown architecture and tokenizer drift
are refused. No tokenizer artifact is rewritten for compatibility.

The real checkpoint has **321,908,995 pretrained parameters**. The initial profile
freezes them and adds **2,313 low-rank coefficients** on designated rows of the
existing `scorer.1` and `scorer.3` layers. At most 3,840 effective weight coordinates
can change. This stays within the existing native V2 4,096-delta budget; it is a
bounded first profile, not a claim of optimal rank or sufficient market capacity.
The organ adapter is frozen identity. Encoder, decision layers, embeddings and
action/abstention head remain frozen. Zero delta must reproduce the complete
pretrained forward output. The action head is neither trained nor connected to
orders.

Current resident environment (2026-10-22): Linux x86_64, Python 3.14,
PyTorch 2.13.0+rocm7.14.0 from AMD's multi-architecture index, Laya 0.3.28 and
Transformers 5.20.2. `requirements.lock.txt` matches the 47 runtime packages plus
the pip bootstrap in the GPU-tested resident environment. Runtime artifacts use explicit public URLs
and SHA-256 fragments; pip is pinned by version as the environment bootstrap.
This includes AMD's gfx1150 device packages, rather than relying on PyPI to select
a GPU build. Historical trial runtimes retain their own original environment
manifests. The pretrained tokenizer's list-valued extra-special-token format was
incompatible with Transformers 4.57.6; keep the original tokenizer configuration.
The training entry point requires actual `gfx1150` HIP execution; no CPU training
fallback exists.

## Bootstrap preservation

The old learner is explicitly stopped and disabled before training. The original
directory is kept. The frozen `baseline/bootstrap-v1` contains SQLite backups,
all candidate/dataset/receipt files, the chosen model and a per-file hash manifest.
`train.verify_baseline` checks it before training; every update also checks that
the old MLP is still inactive and disabled. No old evidence is deleted or treated
as a pretrained Laya artifact. These checks are process/data correctness checks,
not broker permission or new strategy qualification.

## Training and native composition

Reuse the existing immutable `heptatrader.neuron-dataset.v1` evidence, verify its
source prefixes, and select only outcomes known before a fixed training cutoff.
Prediction questions have seven fixed future-MID return intervals at 300/900
seconds. Missing, late, gapped and cross-period observations are retained as
exclusions. Exact matched remote scenarios can contribute a 0.1 auxiliary loss;
raw confidence/action strings are not input features, win probabilities or ground
truth. Primary labels come from the observed future price, not the model itself.

The first bounded job uses at most 16 two-row updates. The candidate records the
number of unique optimizer rows separately from all scored training rows. Huber
MLP figures must not be compared with Laya's categorical cross-entropy/Brier as
though they were the same metric. No actual execution-PnL training reward is
claimed; the financial reward weight remains zero pending complete economics.

Autograd computes the supervised adapter gradient. Existing native
`evaluate_recursive_utility` records independent negative-Brier prediction
utility. Existing native `propose_v2` and its verifier check effective weight
changes, not gauge-dependent A/B factor norms. Original 0.5% per-layer and 0.25%
global relative limits remain. The first real run exposed a cross-layer parameter
ID collision; IDs now include the layer, and a cross-layer-coordinate regression
covers it. This fixes identity, not acceptance thresholds.

`integrate_native.prepare_copy` adds `laya_proposal` to a copy of the existing
native adapter. `build_native.py` reuses 139 unmodified core Rust source files from
`hepta-private-ci@78fdb0cf8537e3a84fc6e0a849707559c80881e8` and its pinned Cargo
resolution. The old 562-only operation is not reused for Laya tensors. Every
proposal includes no-change and remains `RequiresIndependentAcceptance` with
`authorization_effect=NONE`.

The proposal bridge supplies native-mechanism candidate evidence. The canonical
Model owner handoff below additionally requires original future qualification
and real host enrollment. Neither path gives a sidecar authority to select a
trading model or initialize an Agentd LearningLedger deployment.

## Forward shadow

`shadow_source.py` is the current bounded reader of the existing Tool Gateway's
`market.get_quote`. It never opens another IB client, submits orders, unhalts or
restarts PAPER. It writes a separate private hash-chained observation stream;
failed/stale reads remain explicit. Source timestamps and the 5-second freshness
boundary are not rewritten. Every complete row is revalidated for hash-chain,
PAPER scope, source authority, original timestamp/expiry, unique quote identity
and file bounds. `shadow_quotes.py` is retained only to explain the earlier
operator experiment and is not the current source reader.

`forward_shadow.py predict` loads and verifies the frozen bootstrap and persisted
Laya adapter, then binds a single shared causal quote input and a 300-second
horizon before labels exist. The bootstrap is read-only numeric inference; Laya
runs on the 890M. The remote comparison uses the configured remote model with a
separate recorded seven-bin shadow prompt, not a modification to the resident
strategy. A remote identifier is not a cryptographic pin on vendor model weights.

Candidate generation and future evaluation registration are separate. Training
always binds its real data cutoff and records whether its originally requested
window was missed; a late completion is never labeled eligible for that window.
`register_validation.py` then binds an already committed immutable candidate,
bootstrap identity, evaluator source and quote source to an explicitly new future
window. It never rewrites the previous window or its censored result. No optimizer
runs inside the forward probe or its evaluator. Input and all model outputs are
committed before the horizon; the evaluator rejects mismatched input/model IDs,
changed plans and prospective-time violations. A not-yet-mature result remains
PENDING rather than occupying the final-result path.

A separate `evaluate` process joins the first qualifying quote at the predeclared
future endpoint, rejects input/path gaps and lookahead, and records censoring.
The bootstrap point estimate is not fabricated into a calibrated probability
vector. Brier scores apply only to real probability outputs. One forward window
is integration evidence, not statistical strategy qualification, net PnL or
permission to replace the remote model.

## Same-budget forward experiment

New v8 releases may opt into
`POWER_PLANNED_MATCHED_PARENT_SIGN_SCREEN_V3` with
`resident_training_v8.configuration_for_release(..., matched_parent_screen=True)`.
The default remains unchanged. This freezes the comparison protocol in each new
job and its future plan, and pins the promotion modules alongside the runtime.
Changing the current configuration never relabels an already frozen job or plan.

The experiment asks whether adapter updates add predictive information after
controlling for calibration and input representation:

| Arm | Model parameters | PRICE input | Calibration rows and budget | Role |
| --- | --- | --- | --- | --- |
| Candidate | Bounded update from the frozen parent | RAW | Original pre-cutoff rows, frozen fit policy | Tested forecast |
| Matched parent | Unchanged parent | Same RAW input | Same rows and fit policy | Primary paired comparison |
| Raw parent | Unchanged parent | Same RAW input | Original parent output | Diagnostic comparison |
| Uniform, training prior, linear softmax | Frozen simple baseline | Same causal observation | Original pre-cutoff rows | Required same-window controls |

V3 uses the matched parent's Brier loss for both mean improvement and paired sign
evidence. A win against the uncalibrated parent alone is insufficient. Each simple
baseline must still be beaten under the existing value rule. The alpha schedule,
trial history, power design, minimum observations, maximum censoring, input grace,
outcome horizon and fixed window closure remain unchanged. RAW input on both
sides and the frozen same-budget comparator are required before plan publication.
Only original forward observations can establish the extra information claim;
training loss, synthetic tests and a successful GPU job cannot establish it.

Existing v1/v2 plans retain their original comparisons and rules. In those plans
the same-budget comparator remains diagnostic. New v3 assessments identify it as
the selection gate without rewriting the original comparator artifact. A passed
research screen still awaits the independent canonical Model owner; it does not
select a trading model.

## Cumulative research lineage: implementation design

The current resident produces independent candidates from
`initial_parent_directory`. It does not yet advance an accepted research parent
after every successful job. Cumulative training needs the following separation:

| Identity | Meaning | Advance condition |
| --- | --- | --- |
| Candidate | One immutable job, adapter, data cutoff and evaluation plan | A bounded training job completes |
| Accepted research parent | Adapter from which subsequent research jobs start | Original forward screen passes, retained-task checks pass, and the expected parent generation is still current |
| Trading model | Model selected by the canonical owner | Existing independent owner publication and selection rules |

Use the existing candidate/job manifests and resident SQLite state. A small
transactional research-head record should bind its generation, candidate and
manifest hash, previous generation, qualifying plan and assessment, and pinned
training policy. It must not share or impersonate native `CURRENT`. There is no
second acceptance service or parallel receipt hierarchy. Publish immutable files
first; then compare-and-swap the expected research-head generation in the
existing transaction. A crash before the transaction leaves the previous parent
active, while a completed transaction names already verified immutable bytes.

Before admitting a job, freeze its actual research parent, data/source hashes,
cutoff, runtime, objective, optimizer policy and comparison protocol. The first
cumulative version can deliberately reset optimizer state for each bounded job
while carrying the accepted adapter forward. Persisting optimizer moments later
requires separately pinned state and compatibility checks; reusing model weights
must not silently imply optimizer-state continuation. Multiple independent
candidates may share a parent, but only one can advance a given head generation.

The closed ACT source is already bound to the parent that originally produced
its prospective decisions. Simply changing `initial_parent_directory` must
continue to fail that source binding. An advanced parent therefore needs either
new sealed ACT decisions and mature outcomes actually generated by that parent,
or a separately specified PRICE-only research objective. Old ACT labels must not
be reattributed to the new parent. A matured evaluation may enter later training
only after its original trial is closed; it cannot remain an independent holdout
for a descendant trained on those labels.

Before changing the research head, evaluate the new candidate against the same-
budget current parent and simple controls, plus a predeclared retained-task set
that detects forgetting. Retained-task identity, tolerances and resource budget
belong in the frozen policy before candidate training, not in a post-result
exception. A rollback creates a new, increasing research-head generation that
points to a previously accepted immutable adapter and records the reason. It
does not rewind trial numbers, alpha spending, source identity or failed evidence.

Implement this as a later change to the existing resident admission/registration
transaction, with crash-recovery, competing-candidate, stale-head and rollback
fixtures. No research-head pointer is activated by the present comparison change.

## Canonical Model owner and read-only advisory

After an unchanged `QUALIFIED_AWAITING_CANONICAL_OWNER` assessment,
`resident_evaluate` invokes `resident_owner.handoff`. It permits at most one new
attempt per tick and records immutable attempt intent before preparation or
publication. The payload contains the actual adapter bytes, complete base and
tokenizer manifest, training/runtime lineage and original evaluation evidence.
`build_financial_owner.py` builds the typed bridge against the same pinned
canonical source. Publication uses the existing owner writer fence and an
externally signed next CURRENT; selection remains independent. An attempt
without a final receipt does not automatically retry. Qualification, alpha,
plan clocks and outcomes are not rewritten by this handoff.

The opt-in config needs `canonical_verifier_binary` and its SHA-256, and
`canonical_owner_registration: {path, sha256}` pointing to an existing host's
public enrollment. That envelope has schema
`heptatrader.canonical-laya-owner-enrollment.v1`, route
`CANONICAL_LAYA_PRICE_READONLY_V1`, the pinned native source SHA and
`effect_authority=false`. Its `owner` supplies the existing store root, trust,
externally signed writer lease, independently retained CURRENT floor,
withdrawal evidence and storage binding. Publication additionally needs the
exact plan's pinned `publication_requests` entry; consumption needs
`selection_trust`, `selection_head_requirement`, pinned `selection_bundle` and
host-authenticated lifecycle evidence. These are external authority inputs;
the code does not create owner roots, private signers or selector enrollment.

Build and stage runtime/verifier bytes before deployment. The ordinary
`install_resident.runtime_files` glob includes the owner modules, and the v8
release builder refreshes their runtime pins. Complete immutable package and
CPU/native contract checks need no production enrollment. Preparing a qualified
owner payload through `resident_owner.prepare` does require the original
qualification and real enrollment, because its producer and manifest are bound
to that owner. `install_resident.install` also writes user units and reloads
systemd; it is not a staging-only call. Keep a viable trial's original runtime,
wrapper and frozen pins active until the existing maintenance boundary. Stage
new content-addressed bytes/configs separately without changing those units.

An enrolled operator can make one explicit read-only request:

```sh
PYTHONPATH="$OWNER_RUNTIME/learn:$OWNER_RUNTIME/scripts:$GUARD_PACKAGE_PARENT" \
  "$LAYA_PYTHON" -B -m laya_cell.resident_owner_consume \
  --wrapper /absolute/path/to/owner-capable-wrapper.json \
  --prepared /absolute/path/to/actual-prepared.json \
  --request /absolute/path/to/advisory-request.json
```

The request contains `request_id`, `created_at_ms`, `expires_at_ms`, the existing
`period_sha256` and `horizon_sec: 300`; it expires within five minutes of creation.
Missing enrollment returns pending before starting any unit, native call or GPU
worker. Otherwise native selected loading precedes the existing resource gate
and confined `resident_launch owner-predict` worker. The original memory/PSI,
source freshness, Value priority and shared heavy-worker lease still apply;
viable original evaluations take priority. The worker reads the real current
source and emits PRICE7/ACT advisory output, with no BUY/SELL conversion.

The one-shot controller checks actual systemd cgroup/time limits, reaps all
owned descendants before releasing the lease and rechecks native CURRENT after
prediction. Actual unresolved cleanup records a separate resource hold while
preserving any existing marker. Request, selected artifact/generation, output
and final CURRENT are bound in a durable receipt. Expired or repeated requests
return historical receipt identity without fresh model output. A read-only
verified selection is not Agentd acceptance; a successful publication is not
selection or consumption. `product_consumed=true` requires the real prediction
and final currentness checks. None of these states grants trading authority.

## Reproduction

Use a dedicated user-owned environment and private model/output paths. No root,
GPU firmware update, system-Python modification or CPU fallback is necessary.

```sh
python3.14 -m venv "$VENV"
"$VENV/bin/python" -m pip install -r research/neuron_learning/laya_cell/requirements.lock.txt
export PYTHONPATH=research/neuron_learning
"$VENV/bin/python" -m laya_cell.integrate_native \
  --original research/neuron_learning/native/main.rs --destination "$NEW_NATIVE_SOURCE"
python3 research/neuron_learning/build_native.py --source "$PINNED_HEPTA_SOURCE" \
  --build "$NEW_NATIVE_BUILD" --adapter "$NEW_NATIVE_SOURCE"
LAYA_TEST_CONFIG="$JOB_CONFIG" "$VENV/bin/python" \
  research/neuron_learning/laya_cell/tests/test_contracts.py
"$VENV/bin/python" -m laya_cell.train --config "$JOB_CONFIG"
# Start the bounded read-only source in a separate terminal; no new IB client.
python3 -m laya_cell.shadow_source --output "$SHADOW_DIR" --seconds 900
# With the source running, register AFTER the candidate exists and BEFORE input:
"$VENV/bin/python" -m laya_cell.register_validation --training-config "$JOB_CONFIG" \
  --shadow-directory "$SHADOW_DIR" --output-config "$SHADOW_CONFIG"
"$VENV/bin/python" -m laya_cell.forward_shadow predict --config "$SHADOW_CONFIG"
"$VENV/bin/python" -m laya_cell.forward_shadow evaluate --config "$SHADOW_CONFIG"
```

All outputs are new immutable candidates/plans, never overwrites of selected
weights. The training command is one bounded job, not a silently enabled recurring
trainer. Independent future data is required before another selected generation
can be considered.

For the complete affected CPU learning/source partitions, use the existing CI
runner on Pocket4, with the pinned environment and `cmake`/`ctest` on PATH:

```sh
CUDA_VISIBLE_DEVICES='' HIP_VISIBLE_DEVICES='' ROCR_VISIBLE_DEVICES='' \
  OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=2 \
  "$LAYA_PYTHON" -B scripts/run_python_tests.py --lane learning
"$LAYA_PYTHON" -B scripts/run_python_tests.py --lane source
```

The learning lane discovers all in-package tests and explicitly excludes the
separately provisioned native/GPU cases. The normal core lane's
`test_historical_training` entry also discovers the original resource/source
and Value guard tests. Keep actual native-contract builds and any reserved GPU
validation separate; CPU fixtures and a systemd metadata smoke test do not
establish production enrollment, statistical qualification or live consumption.

## Current verification boundaries

The target performs strict full-checkpoint loads and true gfx1150 forward/backward
adapter updates. The records separately identify 321,908,995 frozen pretrained
parameters, 2,313 trainable coefficients and up to 3,840 effective coordinates.
Changing a coefficient and changing an effective matrix coordinate are different
counts. All frozen-parameter hashes and zero-delta behavior are checked.

Focused tests include the existing native NDU/Neuron/Plasticity libraries, actual
native Laya proposal calls, synthetic structure/contract failures, 16 source-chain
failure cases and 11 independent-window identity/time cases. Neither a test count
nor a successful bounded optimizer step proves market predictive value.

The one-window forward result, whether OBSERVED, CENSORED or PENDING, remains
research evidence. The same-input remote comparison uses a distinct seven-bin
shadow prompt, not the resident direction/confidence trading prompt. Bootstrap
point prediction receives interval accuracy only, not an invented Brier score.
No cost-gate restoration, 0.62 change, PAPER activation, execution-daemon upgrade
or automatic student replacement is part of this module.
