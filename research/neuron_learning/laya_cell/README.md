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

Actual target environment: Python 3.14; PyTorch 2.11.0+rocm7.13.0 from AMD's gfx1150
index; Laya 0.3.28; Transformers 5.3.0. The pretrained tokenizer's list-valued
extra-special-token format is incompatible with the tested Transformers 4.57.6
loader. Use the compatible implementation, not an edited tokenizer_config.json.
`requirements.lock.txt` records the installed environment; install ROCm torch from
its explicit AMD index before the remaining packages. The training entry point
requires actual `gfx1150` HIP execution; no CPU training fallback exists.

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

This is a native-mechanism candidate bridge, not full canonical Agentd
LearningLedger, LearningOperator service enrollment, or production model owner
activation. No sidecar can self-select into the trading runtime.

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

## Reproduction

Use a dedicated user-owned environment and private model/output paths. No root,
GPU firmware update, system-Python modification or CPU fallback is necessary.

```sh
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --index-url https://repo.amd.com/rocm/whl/gfx1150/ \
  'torch==2.11.0+rocm7.13.0'
"$VENV/bin/python" -m pip install 'laya==0.3.28' 'transformers==5.3.0' 'numpy==2.4.1' protobuf
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
