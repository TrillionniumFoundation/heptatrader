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
