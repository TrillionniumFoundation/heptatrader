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

Training is considered every 15 minutes, only with at least 16 previously
unconsumed mature sample/horizon/outcome identities. A job uses at most 64 rows,
16 disjoint two-row optimizer steps, and a 600-second worker budget. At most two
candidates wait for evaluation. Source and output budgets stop work when full,
without pruning unfavorable history. The read-only feed is capped at 512 MiB;
the combined resident state is capped at 2 GiB. Those are explicit finite capacity
limits, not a claim of unlimited unattended operation.

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

## Original Laya action head, not a new trade-action network

The complete Laya checkpoint and 321,908,995 original parameters remain frozen.
Existing scorer adapters are retained. A rank-one update is added only to the
first 64 columns of the original `act_head.2` matrix, whose shape is 2 x 256.
This is 66 additional coefficients and 128 effective coordinates: total
**2,379 trainable coefficients** and at most **3,968 effective coordinates**.
The native 4,096-delta, 0.5% per-layer and 0.25% global limits are unchanged.

The checkpoint's original action meanings are **ACT / ESCALATE** (answer locally
or request escalation), not BUY / SELL. The original configuration gives a wrong
answer cost and an escalation cost. This first integration trains a supervised
correctness/routing target, not a cost-optimal routing policy: the frozen
predecessor's predicted interval is compared with a later real market label, then
ACT/ESCALATE cross entropy is added at weight 0.01. Targets are fixed before the
candidate update. Market prediction supervision stays primary, with an optional
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

Each candidate gets a fixed future plan: twelve slots six minutes apart, each
predicting a five-minute price interval with a one-minute input window. Missing
and censored slots are preserved. Qualification needs ten observable windows,
no more than two censored slots, at least two outcome classes, positive mean
paired Brier improvement over the fixed predecessor, and no worse interval hit
count than the frozen bootstrap. A one-sided paired sign screening rule uses a
summable alpha allocation `0.05 / (trial * (trial + 1))` across repeated candidates.
Nonoverlapping intervals do not prove independent market observations; this
screen is not a complete causal/clustered statistical efficacy qualification.

The plan binds evaluator/predictor/rule code and candidate identity. An old
pre-deployment plan lacking those bindings is retained as rejected, not rewritten
or silently resumed under changed semantics. Candidate, input, timestamps and
weights cannot be substituted. Child prediction never imports or runs an
optimizer. No additional remote model requests are made just to obtain a score:
parent and bootstrap are the current paired baselines; an unpaired remote output
is not presented as a same-input comparison.

A statistical pass has status `QUALIFIED_AWAITING_CANONICAL_OWNER`, and **cannot
change a selected model**. It prepares an explicitly unadmitted local owner-outbox
record. There is no configured owner endpoint or inferred owner acknowledgement.
This version therefore does NOT complete true automatic promotion. The fixed
reference is not silently switched when the local screen passes.

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
