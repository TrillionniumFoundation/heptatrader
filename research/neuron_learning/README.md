# Pocket4 Neuron GPU learning sidecar

This is an executed candidate-learning path, not a replacement trading driver.
EUR/USD PAPER continues with its remote position-neutral forecast, confidence,
cost, quote-TTL, original-command, owner and risk-exit rules unchanged. The
student has its own model identity and no runtime consumer in the order path.
BTC is not enabled by this installation.

## What actually runs

The optional `hepta_learning_export.py` in the cost wrapper records exact remote
input snapshots and their digest before inference, then the returned forecast,
quotes, durable decision state and original-command settlement references. Its
nonblocking bounded queue feeds a private hash-chained append-only writer.
Overflow and disk errors invalidate learning evidence, not a trading risk exit.
Nothing in the learner invokes the remote model, native broker tools, paper-on,
order prepare/submit, or the resident trading service manager.

The numerical DecisionCell profile is **32 -> 16 tanh -> 2**, with **562 trainable
parameters** and 300/900-second future-MID prediction heads. Sixteen fixed-scale
causal features and sixteen native Neuron activations form the input. The small
head's forward pass, backward pass, gradient reduction and SGD update actually
execute on pocket4's Radeon 890M (`gfx1150`) through the already installed HIP
runtime. There is no PyTorch installation and no CPU fallback for training.
NumPy prepares inputs and provides a test oracle, not an alternate trainer.

The reference scales are fixed: returns are divided by 100 basis points. Feature
order, missingness and UTC time encoding are explicit in `data.py`. No normalizer
is fitted to a future evaluation window. Huber supervision uses observed market
outcomes; an exact, timely remote teacher contributes an auxiliary weight of
0.1 only where the corresponding real label exists. Confidence is never a win
probability or logged behavior propensity. This is supervised numerical learning,
not PPO or an inferred differentiable broker.

## Existing Hepta source reuse

The separate native bridge builds these existing mechanisms from
`TrillionniumFoundation/hepta-private-ci` at the frozen commit
`78fdb0cf8537e3a84fc6e0a849707559c80881e8`:

- `codex_hepta_neuron::sparse_tick`: bounded temporal state, competition and
  eligibility checkpoint, applied to the exact causal history.
- `codex_hepta_ndu::evaluate_recursive_utility`: deterministic Q32 recursive
  utility on independently observed negative prediction loss. Financial utility
  is a distinct evidence class and is not substituted with missing PnL.
- `codex_hepta_plasticity::propose_v2` and its verifier: exact Q32/Q64 parameter
  norm checks, a mandatory no-change option and candidate-only proposals.

The native sources are not rewritten or copied into this repository. The build
copies six crates into an isolated workspace, verifies all Rust source hashes,
and only narrows Cargo workspace metadata (including contracts dev-dependencies).
`native/Cargo.lock` pins the successful dependency resolution. Build receipts
bind all native source hashes, adapter, lock and resulting executable.

The practical native profile is a per-input bounded sparse mechanism plus a GPU
head. It is not the complete long-lived Agentd `NeuronRuntime` store composition,
a trained Laya/Transformer backend, full NDU FBSDE, or governed V3 Agentd
Plasticity activation. The local SQLite bridge is a recoverable operator spool,
**not** an assertion that the canonical Agentd `LearningLedger` or artifact owner
has been enrolled. V2 proposer/evaluator labels are not cryptographic independence.
These limits are retained in receipts; no authority is inferred from hashes.

## Evidence and update semantics

Sources are bounded immutable newline-complete prefixes. The bridge validates
source chains, PAPER identity, current accounting-period hash, source quote TTL,
exact anchors and outcome availability. It never interpolates a missing endpoint,
rewrites source timestamps or labels a missing outcome zero. Input/path gaps over
30 seconds are excluded with reasons. Past teacher records without exact inputs
remain counted but are not distilled. HOLD and rejected/late candidates remain
visible. A loss in the export queue disqualifies the affected producer stream.

Dense price samples may train a candidate but are correlated: receipts separately
report nonoverlapping outcome windows for each horizon. The 16-mature-row floor
is an engineering optimizer floor, not a statistical significance or deployment
threshold. It is not the old 30-sample/two-UTC-date local calibration dependency.
Nothing about local training readiness blocks the remote PAPER driver.

Each bounded training batch uses at most 256 rows and 16 epochs. Training is
considered at most every five minutes when the mature-data fingerprint changes.
Candidate deltas are conservatively projected before the existing native verifier
checks the actual per-layer 0.5% and global 0.25% norm limits. The NDU loss
comparison used for candidate generation is explicitly a training diagnostic,
not independent efficacy evidence. Worse/no-update candidates retain no-change.

Immutable candidate and training receipts precede a single local SQLite commit
of the model, training fingerprint and receipt. The human-readable
`latest-training-candidate.json` pointer is rebuilt from that committed record;
a restart does not silently repeat the same committed training batch. This pointer
has no reader in the PAPER execution path. A changed accounting period blocks the
learner/evaluator until a new explicit scoped configuration is created.

`economics.py` reuses the existing `hepta_pnl_accounting.replay` unchanged on the
committed OMS journal. It keeps original command/execution identities, fee
aggregation, epoch checks, missing/conflicting fees and unresolved settlement.
Unknown or unbound own executions mean `pnl_reward_ready=false`, not zero fees.
Only complete closed, matched PAPER execution economics can yield a numeric net
reward, and it is not account NAV or LIVE profitability. Market-outcome learning
continues independently while financial rewards are unavailable.

## Independent forward-time evaluation

`evaluate.py` is a separate process. It does not train or select. The learner
persists each fresh prediction before observing the horizon, together with model
identity and training cutoff. The evaluator only later joins observable outcomes,
rejects lookahead, records censoring and compares per-model/horizon price loss
against zero-return and exact same-input remote forecasts where available.
Neither an offline training-loss reduction nor HOLD-only avoidance of fees grants
strategy acceptance. Prediction quality is not cost-aware execution PnL. Student
replacement remains a separate, as-yet-unperformed execution integration with a
new model identity and independent strategy evaluation.

## Build, tests and installation

Requirements already verified on pocket4: Python with NumPy, HIP compiler/runtime
for `gfx1150`, and Rust capable of edition 2024. Paths below are placeholders for
private operator directories, not credentials.

```sh
/opt/rocm/bin/hipcc --offload-arch=gfx1150 -O2 -std=c++17 -fPIC -shared \
  research/neuron_learning/native/neuron_head_hip.cpp -o "$BUILD/libneuron_head_hip.so"
python3 research/neuron_learning/build_native.py \
  --source "$PINNED_HEPTA_CHECKOUT" --build "$BUILD/native" \
  --adapter research/neuron_learning/native/main.rs
PYTHONPATH=research/neuron_learning:scripts python3 \
  research/neuron_learning/tests/test_learning.py --config "$CONFIG" --report "$TEST_RECEIPT"
python3 research/neuron_learning/install.py --config "$CONFIG" --start
```

Installation snapshots runtime Python/native/GPU/accounting bytes into a
content-addressed immutable release and config. It creates only
`heptatrader-neuron-learning.service` and `heptatrader-neuron-shadow-eval.service`.
Each uses user-level CPU/memory/task limits and low priority. A fail-closed
Landlock/ seccomp sandbox is entered before GPU worker threads: read-only
allowlisted source files, write access confined to the learner output and GPU
devices, no socket creation/connect, no ptrace/process-memory calls, and no
trading-bearer read. No privileged namespace or system-wide setting is changed.
HIP/COMGR temporary and cache files are redirected into the allowed private
output directory; the sandbox does not grant global /tmp write access.
Real bearer-read, socket and out-of-output write denial probes run at startup;
their receipt is required before learning. Unsupported sandbox support stops
the sidecar instead of silently falling back. GPU devices remain accessible.
This is local process confinement, not a claim to be the full Agentd authority
owner or a defense against a user who deliberately edits trusted application code.

The exporter is a separately tested opt-in flag on the existing cost wrapper:
`--learning-export-dir /private/neuron-learning/input/EUR.USD`. Installing the
learner does not add that flag, restart PAPER, release a halt or enable trading.
An exporter version change needs its own controlled flat/no-pending deployment.

Stop only learning and evaluation without touching PAPER:

```sh
systemctl --user stop heptatrader-neuron-learning.service heptatrader-neuron-shadow-eval.service
```

Private diagnostics: `status.json`, `economics.json`, `shadow-status.json`,
`bridge.sqlite`, immutable `datasets/`, `candidates/`, `receipts/`. Storage is
bounded and never pruned to hide evidence. Resource exhaustion makes learning
unavailable and does not disable the remote trading loop. No new periodic
ChatGPT task or future personal monitoring promise is implied by these services.

## Core SDK build ownership

The HIP translation unit is explicitly outside the SDK-free CMake core profile
and is listed under the existing research-sdk module unbuilt-in-core paths.
It is not an uncompiled implementation claim: the opt-in hipcc build above
produces the separately hashed library actually tested and installed on pocket4.
Ordinary core builds do not acquire a ROCm dependency. The build-ownership check
remains enabled; this exact experimental backend exclusion does not wildcard
other research sources or weaken normal core reachability.
