# Train V2 Phase 3: local pre-event registration

October 10, 2026, pocket4. An isolated, uninstalled research extension based on Train V2 Phase 2 (commit 4201540). There are no modifications to PAPER, OMS, Execution, source admission, original Trial3, Neuron256, model weights, systemd or canonical Agentd authority.

## What the change closes

Phase 1 generated prospective plan JSON with a self-hashed registered_at_ms supplied by its caller. Phase 2 independently validated original locally hash-chained PAPER quotes, exact feature identity, and delivery within three seconds, but a self-hashed plan time was never independent evidence that a future evaluation was registered BEFORE the market decision.

Phase 3 adds a distinct current-host-clock preregistration step. register_trial accepts a fixed FeatureSpec and future UTC-millisecond window. The registrar requires the declared registration time to agree with the current host clock within three seconds and the earliest decision to be at least 30 seconds in the future. It binds the accounting period, original source root, instrument, policy, exact plan and FeatureSpec, then writes an immutable fsynced seal BEFORE inserting the registration into its separate SQLite WAL registry.

A crash leaving an orphan seal cannot be silently resumed or called successful. Across all plans in ONE registry root, the same instrument/period cannot have overlapping future windows, even under a changed behavior policy. A different registry root or privileged local host compromise are NOT addressed by local file locks.

## Original event ingress

registered_event_ingest requires the exact pre-existing local registry row and unchanged seal, the original source segment beneath the preregistered directory, a correct local hash-chain source replay and fresh actual event timestamp. The ledger name is fixed to registered-events.sqlite under the private registry. Earlier event_ingest and original_event_ingest CLIs refuse that ledger name so they cannot inadvertently bypass preregistration. They may still be used with separate unregistered research fixture ledgers.

This is NOT a producer signature, broker market-data signature, canonical Agentd LearningLedger or product-trust enrollment. Same-UID users can still modify local code/files. No event listener, model inference, GPU job, order authorization or automatic retraining is introduced.

## Test and evidence boundary

Adversarial tests cover retroactive timestamps, >=30-second real lead, duplicate and cross-policy overlapping windows, orphan/missing/tampered seals, original root mismatch, arbitrary ledgers, once-only original-source ingestion, old events, and bypass through legacy CLI. Deterministic tests may patch the local clock; such fixtures are not real independent forward evidence.

The separate read-only smoke uses the current real host clock to seal a private future-window research registration with zero event rows and no future execution scheduled. It must not be described as a live trial, economic qualification or actual broker model prediction.

## Next product gates

Independent canonical producer/observer/owner enrollment and actual signed preregistration remain necessary before a real new forward trial. The existing current100k Trial3 time windows, sealed requests and denominator are unchanged. A future plan must preserve true original model REQUEST/PREPARED deadlines and avoid hindsight selection, prove executable-side economics against no-trade with uncertain slippage and full commission/cash-flow scope, and pass genuinely unseen statistical outcomes plus canonical owner selection. No part of this branch claims financial improvement.

All code remains a research-only candidate stacked on PR #191. Full Core, Historical and Source regressions and exact-commit remote CI are required independently before merge.

## Exact Pocket4 live clock registration smoke

An actual unpatched host clock call on October 10 sealed a private research
plan approximately 119,964 ms before the first declared local decision.
This was a preregistration **smoke**, NOT a launched forward trial: one
locally indexed plan, zero submitted source or outcome events, zero timers,
zero model inference jobs, zero broker queries and zero orders. The source
directory was the existing original Laya PAPER feed and its contents were
not modified. Independent Owner was NOT enrolled.

The immutable private registration receipt SHA256 was
2ac5b8db786818b0cc8570a8bbb08ae96fdb525c5993d23ccf7abca848c70d9f.
Local evidence and the sealed plan reside under:
~/.local/state/heptatrader/operator-evidence/train-v2-phase3-20261010/

The registrar bounds each prospective window to at most 16 days and refuses
a first decision more than 16 days into the future, preventing accidental
indefinite blocking of future trial windows in that registry.

## Adversarial completion

Phase 3 focused tests cover cross-process simultaneous registrar racing:
exactly one of two competing local processes can reserve an overlapping
window. The other is rejected without a second sealed plan.
The legacy CLI bypass, missing/tampered/abandoned seals, backdated
registration and original-source root escape all fail closed.

## Final isolated pocket4 regression (2026-10-10)

- Phase 3 focused registry suite: 18 tests passing. Five complete repeated
  executions of that suite also passed, including real two-process contention.
- Historical training regression: 400 tests passed, 7 pre-existing skipped.
- Core Python regression: 1,813 tests passed, 21 pre-existing skipped.
- Source/ownership/documentation regression: 106 tests passed.
- Final real-clock preregistration lead: 119,964 ms; one plan and zero events.
- No production model, source HOLD, trader, PAPER broker query, order or
  systemd unit changed. These are source and local registry acceptance only.
