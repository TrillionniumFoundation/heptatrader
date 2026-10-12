# Train V2 Phase 1: PIT feature contract and event-only research admission

Status: isolated pocket4 research code, 2026-10-10. No PAPER/Execution, OMS, order, model, Laya source hold, systemd unit or prior trial change. Stacked on the existing protected IB historical global-lock branch.

## The boundary

HeptaTrader currently has different model input profiles, horizons, feature availability times and policy identities. These cannot be unified merely by sharing a timestamp, concatenating data, or relabeling the Laya ACT/ESCALATE head as a trade BUY/SELL head.

The new train_v2 package defines a common data *provenance* schema. Its strictly causal profile verifies both event_at_ms and actual available_at_ms against the decision time, respects local quote expiry, contiguity and horizon maturity. It retains the exact source receipts and period/policy identities. An outcome arriving later may update the label but cannot change earlier features or the feature digest.

A 2026-03 broker historical bar downloaded in October is not a March-time observed live quote. The revised IB adapter verifies the original history plan and 31 hash-pinned receipts, then uses the conservative latest real receipt timestamp as data availability. Such observations can be explored but are always REVISED_HISTORICAL_RESEARCH_ONLY with locally_point_in_time=false, candidate_training_eligible=false and promotion_allowed=false.

The LIVE_CAUSAL_QUOTE adapter calls the existing locally verified hash-chained PAPER shadow quote reader. Since that reader does not expose each original broker quote expiry to the adapter, each quote is conservatively valid **only at its already recorded arrival instant**. Its per-event source witness hash is the causal chain entry digest, not a later segment-wide hash that could include future quotes.

## Components

- contracts.py: immutable FeatureSpec, explicit source mode, matched instrument/period/policy, earliest knowable decision values, mature/censored labels, no synthesized quotes or missing outcomes.
- adapters.py: read-only source-specific adapters, no network or broker calls.
- orchestrator.py: event-triggered isolated SQLite admission for SOURCE_SEALED, OUTCOME_SEALED and CORRECTION_REVOKES. Every event has deterministic identity, time and source witness; BEGIN IMMEDIATE ensures no duplicated row or review batch after concurrent process starts.
- event_ingest.py: one explicit event/file invocation, never periodic polling, training, broker calls, model selection or owner publication.
- smoke.py: reproduces a bounded, read-only provenance check against sealed IB history and Laya original source.
- tests/python/test_train_v2.py: timestamp, scope, future-leak, value maturity, correction, duplicate, cross-process and CLI adversarial tests; part of normal Core Python test discovery.

The event/orchestrator layer is **research-review-only**. It never starts a GPU job, changes model weights, invokes canonical Agentd or owns trader credentials. Hashes do not replace canonical producer/owner signatures, and caller-supplied local events must independently be verified against retained original sources before becoming trusted future producer hooks.

## Actual pocket4 smoke evidence

- 210-day EUR/USD IB revised historical source: 42,716 five-minute timestamps and 31 immutable broker request receipts retained. The adapter checked 31 source receipts, read the last 128 recorded bars, and produced a revised historical research label, with neither causal-live nor candidate eligibility.
- Frozen Laya PAPER quote segment: 671 locally hash-verified quote records; a real causal feature was compiled at its original availability time, and its outcome remained PENDING_OUTCOME at that decision.
- Original observed Walk-forward economic result remains no-trade in all three folds, and the old USD100k Value trial3 stays PENDING/unaltered.
- Smoke receipt stored privately at ~/.local/state/heptatrader/operator-evidence/train-v2-pit-events-20261010/real-source-smoke.json. Initial receipt SHA256: aa2254b47b90cead57e1ec135bc5dd92b4d8291304a729d71e2d04cb6135e54b.
Final five-module source-inventory smoke receipt is real-source-smoke-final.json,
SHA256 1a377ef21fe3d5889557a14c43b839437072c0e5fa1f11eceec50ea807334a47.
Both were created in new output paths; prior receipts were not overwritten.

## Preserved negative evidence and next gates

The installed Trial3 predictor currently triggers as often as every second and has shown start-limit-hit. Do NOT edit its current registration, timers or missed outcomes: that could invalidate its frozen prospective protocol. Its successor should use genuinely new signed producer events and precommitted prediction deadlines, not retrospective callbacks.

The current local event CLI is an implementation of an admission API, not an installed host watcher or live trading trainer. Integrating a new producer hook requires source identity enrollment before new observation windows, independent outcome/evaluator roles, the existing GPU resource lease, correction and revocation proofs, net economic evaluation, and an actual Canonical Owner artifact-selection receipt. Nothing in Train V2 creates any of those missing authorities.

The next design step is separate, declared feature adapters for Numeric, Laya and Value with true matched offline/online parity, then a net-PnL evaluator that keeps actual commissions, unproved slippage, financing, position status and the no-trade baseline distinct. Do not reuse already-inspected historical test periods for new positive efficacy claims.

## Run regression

Set PYTHONPATH=research:research/neuron_learning:scripts and run Python unittest discovery for tests/python and research/historical_training/tests. No broker credentials or model checkpoints are necessary for these tests.

## Exact pocket4 validation

- 32 Train V2 adversarial unit tests pass, including CLI and independent Python
  subprocess delivery to the same SQLite ledger.
- Full historical research suite: 400 passed, 7 pre-existing skips.
- Full core Python suite: 1,768 passed, 21 pre-existing skips.
- Source/ownership/documentation partition: 106 passed.
- The source-admission and economic efficacy results are unchanged. No additional
  broker requests, GPU training jobs, account transfers or orders were launched.

## Phase 2 original evidence and model semantics

See [Phase 2](PHASE2.md) for the independently validated original Numeric16/Laya
seven-class feature reconstruction, fresh original PAPER source-chain event handoff
and read-only broker-commission cost frontier. Phase 2 does not add a producer
daemon, actor update, canonical owner or broker API capability.


## Phase 3: distinct local prospective preregistration (research only)

See [PHASE3.md](PHASE3.md). A new research trial must now write a
clock-checked, immutable local registration at least 30 seconds before the
first decision. The registered ledger accepts only original-source events
whose exact registered plan and local segment are reverified. The old
unverified one-event CLIs cannot write into this special ledger. These
checks do not constitute an independent canonical owner signature or
an installed producer hook.

## Phase 4A: externally signed local research envelopes, no canonical owner

See [PHASE4.md](PHASE4.md). The new receive-only Ed25519 verifier
checks pinned public trust, distinct registrar/producer/observer keys,
exact original plan/source/registry binding, current expiry/revocation
and immutable signed receipts. A separate signed-only research ledger
cannot be written through the old unverified CLIs. Test-only cross-host
signatures are NOT Canonical Agentd LearningLedger facts or product
selection evidence. No new model, running service or broker action is
created by this package.


## Phase 4B optional original broker TTL profile (research only)

See [PHASE4B.md](PHASE4B.md). Old V1 remains arrival-only; new feature
profile alone may reuse original, locally chained IB-reported expiration,
never more than 5s from the observed quote. Its SOURCE event can be signed
after quote arrival but before the future decision, which must itself be
before the original expiry. This closes a timing impossibility found in
Phase 4A's after-arrival external signing pipeline, without repairing
earlier missed predictions or admitting any model/trade authority.


## Phase 4C exact canonical source requirements

See [PHASE4C.md](PHASE4C.md). The pinned original Rust LedgerWriter
cannot accept a research price JSON as an authenticated decision. The
new code audits real original source, original Rust type/method layout
and the missing complete candidate set, logged policy propensity,
independent controller and root trust facts. It reports HOLD only:
no native LedgerWriter, owner/selection, model or broker side effect.

## Phase 4D: exact original native Rust library qualification (isolated only)

See [PHASE4D.md](PHASE4D.md). Real original LedgerWriter and Artifact Owner
Rust library tests passed (210/210), and a 256-Decision Pocket4 on-disk
segmented-witness target-host test recovered exactly. The harness uses an
isolated six-crate generated Cargo manifest, test-only signing keys and
synthetic Decisions. The original complete Canonical workspace later
resolved its exact lock and independently passed the same 210 native tests
(see Phase 4D). Real product controller enrollment, logged behavior
propensity, production host instance, V3 dataset from original outcomes
and unseen after-cost prospective efficacy remain HOLD.

## Phase 4E: original behavior-policy cohort provenance

See [PHASE4E.md](PHASE4E.md). A new strictly read-only tool revalidates
actual installed PAPER runtime SHA256, exact period, and original
REQUEST→RESPONSE→DECISION→PREPARED log identity. Pocket4 verified
77 unique instrument/command bindings with zero chain issues or cross-release duplicate IDs, but 74 belong to
a trading-behavior-changed EUR/USD release and must not be pooled with
the 3 unchanged-policy bindings. No original generator logged a complete
legal candidate set and selected behavior probability; model confidence
is not propensity. Production Canonical LedgerWriter, identity, V3
dataset and prospective after-cost efficacy still HOLD.

## Phase 4F: reject reused source REQUESTs

See [PHASE4F.md](PHASE4F.md). Read-only original decision-provenance
checks now also refuse using one original REQUEST in multiple PREPARED
commands or across different release cohorts with different command IDs.
The observed 77 original command bindings have 77 distinct REQUESTs;
no original logged behavior propensity or production identity is inferred.


## Phase 4G: two-bit preregistered shadow signal policy

See [PHASE4G.md](PHASE4G.md). A new isolated, **uninstalled** research
policy implements a complete three-signal grammar with exactly known
selection probabilities, a six-quote original-chain freshness gate and
mandatory Phase 3 presealed local plan. It does not infer probabilities
for the opaque original trading LLM. Pocket4 actual EUR/USD source
returned HOLD (no fresh valid quote). A separately marked FABRICATED
quote-source lab test verified software composition but provides zero
real broker, trading, model or efficacy evidence. Production gates HOLD.


## Phase 4H: exact IB weekly close vs locally witnessed original quote gap

See [PHASE4H.md](PHASE4H.md). A new one-shot **read-only** auditor
uses New York DST-aware IB regular FX hours and the actual source-chain
receipts from the running Pocket4 EUR/USD Laya PAPER feed. The Friday
last authoritative quote and the first unready failure align with the
published 17:00 ET weekly close. Across 165 original source segments,
86,642 distinct original quote timestamps match the unchanged Laya
source reader with zero cross-segment price conflicts. Current state
HOLD_FX_REGULAR_SESSION_CLOSED; this is a likely normal weekend market
closure, NOT verified future entitlement. The unchanged original
Canonical Agentd product-host lab additionally passes 2 integration
tests and an explicitly requested 2,368-record history/concurrency
test, all with test-only signing keys and no production identity.
No scheduled worker or trading/model/product identity path is changed.


## Phase 4I: Mac/X230 test signers and original native product ledger

See [PHASE4I.md](PHASE4I.md). The original pinned native LedgerWriter
accepted a **test-only** Mac Generator signature and separate X230
Observer signature under a qian-qi-signed Root test distribution,
persisted a 3-record durable witnessed chain, froze/revalidated V3,
recorded test-source withdrawal and rejected stale V3. A separate
native process recovered from the acknowledged witness without
private keys; all 6 exact native messages verified offline and all 5
ephemeral test-key files were then removed from their host machines.
This is not original LLM behavior propensity, independent production
administrators or real original trading/outcome authorization.


## Phase 4J: one real original-market causal shadow Decision + 5min label

See [PHASE4J.md](PHASE4J.md). After the live EUR/USD IB PAPER market
reopened, one new local shadow research-only plan was preregistered
44,994ms before a fresh ORIGINAL price-bound NO_TRADE decision at
08:24:03.939 +08. Its exact six input receipts and feature ID were
frozen as PENDING_OUTCOME. At actual 5-minute maturity, original
continuous quotes gave OBSERVED midprice return -0.98103481 bps.
A physically separate X230 research test Observer independently
verified 148 original quote path points from a COPY of the same
Pocket4 local data and recomputed/signed exactly the same value.
Pocket4 independently verified its detached Ed25519 signature and
the unmodified original source-file prefix. This is NOT actual
trade-net-PnL, an independent broker quote source, original opaque
LLM action propensity or production Canonical signed dataset/Owner.


## Phase 4K: original live opaque LLM action census versus partial gates

See [PHASE4K.md](PHASE4K.md). The exact original PAPER FX/BTC
REQUEST/RESPONSE/DECISION source chain was verified, including real
BUY/SELL/HOLD model outputs, request-time position/confidence and
original true PREPARED OPEN model-object bindings. The existing
all-action source reader from separate Draft #200 is reused by hash
in this #202-lineage worktree, with additional adversarial static
gate and cross-process identity controls. The gap between partial
static candidates and PREPARED must NEVER be labelled actual risk
vetoes, and model confidence or observed action frequency must
NEVER be promoted as the original LLM action propensity. Product
root, native Writer, actual net reward and OPE remain HOLD.


## Phase 4L: new real market source to original native signed ledger

See [PHASE4L.md](PHASE4L.md). A genuinely new original IB PAPER EUR/USD
quote-bound no-order shadow decision was preregistered and Mac test
Generator-signed/ACKed by the original full Canonical native durable
LedgerWriter and separate witness **before** the actual future
five-minute outcome. After horizon, physical X230 independently
verified 146 points of the same original quote source, and its separate
test Observer signed the native price-only terminal Outcome. Native
Witness advanced to 2 and original DatasetSnapshotV3 freeze and
current-state revalidation passed with a test-only Evaluator.
Five ephemeral private test files were then removed at source hosts;
native cold recovery and 3 corrupt copy rejections still passed.
All test identities, price-bps units, and original policy propensity
limitations are deliberately quarantined; no real broker order,
cost-complete PnL, eligible product dataset or production root enrolled.


## Phase 4M: original source-time signatures, native exact retry & expiry HOLD

See [PHASE4M.md](PHASE4M.md). Reuses the ACTUAL Phase 4L original
IB PAPER shadow market Decision, independent Mac Generator / X230
Observer canonical signed bytes, original native 2-record ledger/witness
and its already-deleted temporary test keys. Six isolated native
Rust copies prove IdempotentReplay, wrong retry rejection,
one-record witness-lag reconciliation, incomplete-tail recovery,
current-time signer expiration rejection and exact original record
identity. An additional zero-write current-clock recovery guard
returns ALREADY_ACKNOWLEDGED for fully acknowledged sources,
or HOLD when a historically signed Outcome is no longer eligible
for current-time resubmission and the independent Witness is one
event behind. Historical issuance-time simulation is TEST-ONLY,
not permission to backdate product admission or claim production
Root, net PnL, original LLM policy probabilities or Artifact Owner.


## Phase 4N: independently-held preappend Escrow candidate — HOLD

See PHASE4N.md. A test-only original Rust LedgerWriter call-site
now enforces a separately signed, owner-private Escrow intent receipt
BEFORE a Decision or Outcome append. Local synthetic native Rust and
Python tests reject missing receipts, changed signed payloads,
roles, hashes, sequence/clock or unsafe permissions. The first full
regression exposed and fixed a new process-global umask leak.
Actual independent remote Escrow custody/root enrollment has NOT
been demonstrated and the new locally preregistered real-price plan
was not consumed. No order, model, native production service or
economic Outcome was generated; product remains HOLD.


## Phase 4O: exact old Escrow receipt readback; native ACK-only HOLD

See PHASE4O.md. A claimed test-only Escrow ACK requires byte-exact
origin file readback and public signature/source/role verification.
The candidate signer returns an already-fsynced identical receipt
idempotently after lost network ACK, without overwriting or re-signing,
but rejects the first submission after its original source-time
window. All six old real original native market test Ledger/Witness
copies are separately rechecked: four ACKed no-op, two lagged HOLD
because no original independent preappend Escrow or production
RecoveryGrant existed. Remote Mac/X230 Escrow enrollment remains
unverified, no production keys, orders or model promotion.


## Phase 4P complete original signed-byte escrow contract

See PHASE4P.md. The earlier real dual-host Phase4O escrow protects
SHA256 identities but cannot reconstruct LOST native evidence bytes.
The new TEST-ONLY offline archive validates exact typed payload,
original signed evidence and detached Ed25519 signature, without
minting new signatures or changing the Canonical Witness. Old
digest-only escrows remain HOLD, not retroactively upgraded.


## Phase4Q actual two-host full original signed-byte TEST escrow

See [PHASE4Q.md](PHASE4Q.md). Unlike prior digest-only physical
receipts and local complete-byte fixtures, two new synthetic
native-formatted requests were fsynced and independently read back
in full from X230 and Mac. Complete original byte and separate remote
receipt signatures are preserved. A lost ACK gets the exact archived
receipt, even after original signer expiry, but can never mint a first
late signed intent. No real source-time product LedgerWriter, Owner,
RecoveryAuthority or trade permission has been claimed.


## Phase 4R: original Rust typed complete-byte custody check

See [PHASE4R.md](PHASE4R.md). The old synthetic X230/Mac Phase4Q
signing message used the incorrect TRUST/SCOPE/OBJECTIVE digest order.
It remains immutably archived and is rejected by the actual original
Rust SignedLearningEvidenceV1 reparser. New SYNTHETIC messages use the
correct native order; both physical hosts passed real native Rust typed
byte-roundtrip and independent actor/host signature checks. The final
read-only recheck created no further source, remote signature or event.
No canonical Root, policy propensity, LedgerWriter event or order grant.


## Phase 4S: native preaction deadline vs preoutcome signature

See [PHASE4S.md](PHASE4S.md). The real original Phase4L Mac TEST
Generator Ed25519 signature and native Witness ACK were AFTER the
original IB quote's 5-second expiry despite being BEFORE the 5-minute
price-outcome maturity. Original PRICE evidence and LedgerWriter test
durability remain valid only within their explicit research scope.
The new read-only gate proves these exact time/role/source facts from
unchanged original bytes and blocks promotion to PREPARED/trading
eligibility. No old prospective record, model or broker service is
reconfigured or rewritten.

## Phase 4T fresh original PAPER quote signature and independent readback

See [PHASE4T.md](PHASE4T.md). This phase verifies one actual NEW
local original quote's TEST-only prewarmed Mac Ed25519 witness, the
previously preregistered research source event and its single source-only
SQLite entry. It uses pinned SSH host keys and an immutable SQLite
read-only checkpoint; it neither runs the original policy/LLM inference
nor mints a native product LedgerWriter or trading credential.


## Phase4U presealed original writer-segment identity

See [PHASE4U.md](PHASE4U.md). For genuinely new, separate TEST-ONLY
source windows, the V2 worker seals its original numeric source segment
at local preregistration before the start of the future input window.
Rotation during the lead, quote input, signature or fsync fails closed;
an ordinary newer segment is never silently resampled as first.
Old Phase4T signed worker and public readback are byte-identical and
retain their own independent original reference identity. No change
to current trading, trained models or broker authorization.


## Phase 4V fresh source -> Canonical native TEST-only abstain ACK

See [PHASE4V.md](PHASE4V.md). One newly observed original PAPER source
was actually preregistered, Mac TEST signed, and passed to a separately
prewarmed original Rust LedgerWriter. A fixed, model-free single NO_TRADE
test policy yielded a real signed V2 Decision/Witness fsync ACK before
original IB expiration. Independent readback used **copies** of the
unchanged original ledger and witness; Rust verified exact typed
Decision, corrupt-witness rejection, and acknowledged-history loss
rejection. This is not original LLM behavior propensity, broker PREPARED,
native product owner authorization, economic alpha, or model release.


## Full original Canonical workspace evidence (parallel track merged)

See [FULL_CANONICAL_WORKSPACE_20261012.md](FULL_CANONICAL_WORKSPACE_20261012.md).
Original 193-member locked workspace native LedgerWriter, Artifacts
and Agentd terminal-cell Owner test evidence is retained alongside
the separate Phase4S/V original-quote source cutoff checks.
This is a merged TEST tree, not new product Owner enrollment, model
behavior action support, a broker PREPARED decision or alpha.


## Combined full original native workspace + source cutoff revalidation

See [UNIFIED_NATIVE_SOURCE_CUTOFF_20261012.md](UNIFIED_NATIVE_SOURCE_CUTOFF_20261012.md).
This separate merged TEST tree re-runs exact original full-workspace native
tests, independently reopens the original signed NO_TRADE-only Ledger and
Witness on private copies, and joins real original source-cutoff timing.
The 51 ms ACK lead, root TEST keys, absence of original LLM action
propensities, and non-production product Owner HOLD remain explicit.


## Original opaque LLM inference latency (read-only)

See [ORIGINAL_LLM_INFERENCE_LATENCY_20261012.md](ORIGINAL_LLM_INFERENCE_LATENCY_20261012.md).
The original release-pinned REQUEST/RESPONSE/DECISION graph is
measured against an optimistic 5000ms original quote lifetime.
The observed model usually finishes AFTER that source window;
an early model output is not a witnessed PREPARED/Native ACK or
an original action propensity. This module is observational only
and does not change the original driver or enrolled model.


## Post-model fresh quote timing feasibility (retrospective, NEW policy only)

See [POSTMODEL_REQUOTE_ASOF_20261012.md](POSTMODEL_REQUOTE_ASOF_20261012.md).
The existing opaque LLM regularly exceeds its original quote TTL; this
isolated read-only auditor joins already captured EUR/USD model returns
against separately sealed original PAPER quote availability at each
actual return. It reports hypothetical *new policy* source-freshness
upper bounds and explicit unknown unsealed source periods, not old
LLM behavior-propensity, executable trades, net alpha or deployment.


## Captured-request denominator for opaque model re-quote (read-only)

See [ORIGINAL_REQUEST_DENOMINATOR_20261012.md](ORIGINAL_REQUEST_DENOMINATOR_20261012.md).
This Phase 4 follow-through counts every captured original EUR/USD model
REQUEST, including incomplete response/decision graphs, against the
separately verified model-return/source-asof upper bound. Completed model
returns are **not** the entire model dispatch or trading opportunity
population. Unknown requests remain unknown; no original model confidence
becomes a genuine logged behavior-policy propensity, and no
training/trading/Canonical Agentd authority is granted.


## New fixed original multi-horizon economic research (NO_TRADE)

See [ORIGINAL_MULTIHORIZON_CAPACITY_20261012.md](ORIGINAL_MULTIHORIZON_CAPACITY_20261012.md).
A source-frozen, retrospective price-only capacity study compares fixed
5/15/30/60-minute original PAPER midpoint windows under the UNCHANGED
10.9133557-bps historical cost-plus-safety hurdle. It selects
time-disjoint windows BEFORE future price labels, preserves missing path
and endpoint censoring, and never trains, selects or promotes a model.
Any later new sealed-source fingerprint must create a new research
cohort, not silently widen this inspected one.


## New original PAPER future four-horizon source population (research only)

See [PROSPECTIVE_ORIGINAL_MULTIHORIZON_20261013.md](PROSPECTIVE_ORIGINAL_MULTIHORIZON_20261013.md).
Three UTC-grid source-capture slots are frozen before the first decision,
using ORIGINAL postregistration local PAPER quotes only. An independent
read-only post-maturity observer module can subsequently seal price-only
outcomes or exact censorship, but not canonical financial rewards. Fixed 5/15/30/60
minute label horizons cannot be outcome-shopped; unknown/uninvoked
slots are NOT no-market-quote or zero-profit results. On-demand CLI
only, no watcher or trading/model/Canonical authority.
