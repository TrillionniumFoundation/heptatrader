# Train V2 Phase 4A: external Ed25519 research signatures

Date: 2026-10-10. Isolated Pocket4 branch based on Phase 3 (5d77ee3).
No live PAPER, Execution, OMS, current model, old Trial3, Laya source hold,
systemd unit, broker request or trade permission was changed.

## What exists versus the real canonical boundary

The hepta-private-ci product interface already defines typed LedgerWriter
authenticated decisions and independently observed outcomes, CreditBatch,
canonical dataset freeze, durable witness, withdrawal/Artifact Owner and
independent CURRENT selection. Its existing Agentd terminal-cell owner
integration exercises a tabular operator, NOT HeptaTrader Laya/financial
tensor training. This patch does not pretend a research JSON envelope has
become a registered Agentd wire schema, host trust distribution, owner ACK
or canonical LedgerWriter source.

The new external_signatures.py verifies incoming Ed25519 signatures only:
a product-host/operator PINNED public trust snapshot with distinct registrar,
original source producer and outcome observer public keys; complete exact
scope, accounting period and policy; bounded trust and witness lifetimes;
generation floor; revocation-list refusal; 64-byte Ed25519 signature;
deterministic domain separation and unknown-key rejection.

No private key import or signing method exists in the new production source
modules. The signature covers the complete plan identity, exact FeatureSpec,
original-source root, exact registry root, role, nonce, times and trust SHA.
Event witnesses bind the entire original event plus its exact source file.
Changing bytes, substituting a role/key, moving a root, invalidating a
signed plan or using a revoked key fails closed.

The signed_handoff.py component persists an externally signed registrar
proof before the Phase 3 local pre-event registration. A crash leaving an
uncommitted seal is not a successful registration. At original event use,
it revalidates current trust and registrar proof, independent producer or
observer signature, and the unchanged hash-chained original PAPER source.
Signed source evidence is durable before one SQLite event admission.

The special externally-verified-events.sqlite research ledger is separate
from the Phase 3 registered-events.sqlite. Old unsigned event CLIs refuse
both protected research ledger names. Nothing starts an event listener, GPU
trainer, broker client, order, model selection or canonical owner.

## Real two-machine proof

qian-qi generated three distinct ephemeral role private keys in memory,
signed a fresh prospective PLAN, then transferred ONLY public key snapshot,
signed PLAN, FeatureSpec and envelope via SSH to pocket4. Pocket4 used
the exact pinned SHA256, verified the registrar signature and sealed
one new PRIVATE research registration using its actual local host time.
Real preregistration lead: 119112 ms.

Pocket4 later independently verified the same sealed plan and rejected
a deliberately tampered, previously valid signed nonce. No private key
was stored on pocket4 or sent through SSH. No source-event subscriber,
future task, model inference, model training or broker operation ran.
Exactly zero events and zero orders were recorded.

Private proof directory:
~/.local/state/heptatrader/operator-evidence/train-v2-phase4-crosshost-1791640943715/

Public trust SHA256:
e3df4557ed9f1c0bbda71a5718c1ba7ea0bc1f1263882cd96d2370a9b4544b13

Local sealed-plan receipt SHA256:
d48fb7642e2f273811921f7d7c4267a4f20ddc2e1d28f914c0dd49487cd53317

This is a REAL crosshost cryptography smoke, not independent canonical owner
enrollment. Both physical machines and trial keys are operator-controlled.

## Outstanding mandatory product gates

1. Product host enrollment with genuinely distinct principal,
credential-chain and signing-key identities, native LedgerWriter records,
independently frozen DatasetSnapshotReceiptV3 and current authoritative
withdrawal witness. Research envelopes alone are not canonical.
2. A pre-provisioned and separately confined original-event producer.
Current Phase 1 LIVE quote TTL is conservative: only its witnessed arrival
instant is valid. A later remote signature cannot be backdated into a
before-decision prediction; do not loosen TTL or repair missed windows.
3. Future new test windows with genuine original model REQUEST / PREPARED
deadline, signed source, independent signed outcome and correction,
deduplicated execution/fee evidence and crash/restart recovery. No old
Trial3, Neuron256 or historical source can become an unseen future sample.
4. Complete execution-cost evidence, no-trade baselines, statistical
prospective qualification and separately selected canonical Artifact Owner.
Neither a positive modeled edge nor a valid Ed25519 signature grants trades.

The operator-provided pinned hash and minimum trust generation are local
operator constraints. They are NOT a root-signed, host-global antirollback
witness; an operator with filesystem/clock control could replace both.
This is a research signature verifier and deliberately reports
canonical_agentd_owner_enrolled=false, automatic_training=false,
trade_authority=false and broker_orders_sent=0.

Run tests/python/test_train_v2_external_signatures.py with existing
PYTHONPATH=research:research/neuron_learning:scripts, and the standard
full historical, core and source suites. Exact-commit GitHub CI and
independent product-host acceptance remain mandatory before any merge.

## Exact pocket4 regression and current hold

- New external-signature adversarial tests: 20 passed, in three repeated runs.
- Core Python full discovery: 1,833 passed, 21 pre-existing skips.
- Historical research full discovery: 400 passed, 7 pre-existing skips.
- Source/build ownership: 106 passed; documentation link validator passed.
- Core CI now explicitly installs python3-cryptography via the Ubuntu package
  manager so Ed25519 tests do not depend on the developer's local venv.
- Real cross-host public-signature receipt was independently checked twice;
  changing the registrar signed nonce produces an invalid-signature rejection.
- At final live read, PAPER Execution still running at 36 cumulative restarts,
  FX/BTC drivers active, Laya trainer blocked by original source admission,
  realized strategy net PnL USD -1325.4022956525, all-in account PnL UNKNOWN
  due to missing current qualified FX mark. No new broker requests or orders.

No native canonical Agentd owner, product LedgerWriter, signed CURRENT head,
independent product producer/evaluator trust, realtime original source hook,
actual economic alpha or model promotion was installed or inferred by this
research extension. Full product qualification remains a separate gate.
