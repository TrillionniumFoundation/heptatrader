# Train V2 Phase 4B - explicit original IB broker-expiry decision profile

October 10, 2026, Pocket4. Research-only change, stacked on Phase 4A at commit 5c48efe. Existing V1 source freshness, Laya HOLD, Trial3, Neuron256, PAPER trading, live OMS and canonical owner settings are unchanged.

## Confirmed timing dead-end in Phase 4A

The former Train V2 LIVE adapter did not have the IB-reported source stale_after_ms field, so it conservatively set valid_until_ms equal to local quote arrival. This makes an AFTER-ARRIVAL independent Ed25519 signature and a BEFORE-DECISION source event almost impossible: source arrival and decision had to be the same millisecond. Phase 4A genuinely verified a crosshost PLAN signature, not an actual new qualified signed source event.

## Explicit V2 broker expiry contract

The original PAPER quote reader independently validates the private hash chain, UTC timestamp, IB source identity, bid/ask integrity, account period, 5-second expiry at initial collection and absence of trade permission. New causal_ttl.py rereads the exact same complete original prefix via O_NOFOLLOW, checks UID and file mode, full length, SHA256, source-entry digest, timestamp and prices, and then copies the ORIGINAL quote's broker-reported stale_after_ms.

Each new-profile decision is constrained by:

    original_quote_time <= local_availability <= decision_time
    decision_time <= min(original_quote_time + 5000, broker_stale_after_ms)

The original source also needs complete contiguous history, previously known inputs and explicit true feature/policy/period identity. Only the versioned feature profile EURUSD_BID_ASK_MID_RETURNS_BROKER_EXPIRY_V2 with expiry policy ORIGINAL_CHAIN_BROKER_EXPIRY_V2 may use this behavior. V1 remains arrival-only and rejects the new profile. Revised IB historical bars cannot use the broker-expiry profile.

Signed source event time may be BEFORE the future declared decision. Handoff now recompiles features AT the exact decision without assuming that the earlier source witness happened in the same millisecond. The distinct producer must sign after the original event and before the decision, while the event itself must still pass source chain, freshness, previously sealed plan and event-time checks. Observer signature cannot substitute for producer; no action or model grant exists. Original Numeric16 and Laya seven-bin feature constructors can consume the same verified causal input, but their tasks and action vocabularies remain distinct.

## Real original source smoke and synthetic signed test

Pocket4 read-only evidence used a previously completed original PAPER quote segment with 678 locally verified quotes. A bounded check found 255 consecutive examples with decision 200ms after local quote availability, but before original IB-reported expiry; V1 correctly rejected all 255 late decisions. Numeric16 and seven-bin Laya question both reconstructed on a valid delayed original input.

The recorded proof is a historical source replay, not a NEW prospective prediction or result. No new broker request, model inference, cryptographic live producer signature, order or autonomous trial was sent or scheduled.

Private proof path:
~/.local/state/heptatrader/operator-evidence/train-v2-broker-ttl-20261010/original-source-v2-ttl-smoke.json

Source proof SHA256:
340527aba5073ed3ff3e1eb64fc31ab51fd057e09011d3ab1cca64c387386486

New TEST-ONLY Ed25519 and quote fixtures verify successful after-arrival/before-decision producer sign and single event admission; original shorter TTL blocks a rehashed forged longer-TTL dataset; modified source chain bytes and concurrent source read mutation are refused. The test signer is NOT enrolled in Agentd and does not attest external broker market data.

## Remaining production prerequisites

No canonical LedgerWriter training facts, independent producer/observer owner enrollment, current artifact signed selection, real fresh source event/prediction, actual original model REQUEST/PREPARED timing, independent outcome or after-cost performance have been obtained. The quote's broker-reported expiry does not prove later executable fill. Phase 4B is not installed on the production service. Existing earlier windows and missed events must remain unchanged. Next is a newly preregistered forward trial only after true canonical host authorization, fresh qualified FX source and separately controlled signing roles.

Run the dedicated tests and the original full historical/core/source partitions. CI and review of the entire stacked PR chain are separately required before any merge.

## Final source-identity and regression gates

A second immutable read-only receipt was sealed after the final source and
parity changes. It records the exact Python SHA256 values for contracts,
adapters, original broker-expiry parser, original model parity/handoff and
external signature verifier, rather than presenting an unbound count.

Final receipt:
~/.local/state/heptatrader/operator-evidence/train-v2-broker-ttl-20261010/original-source-v2-ttl-final.json

Final receipt SHA256:
b8bd3946e32e826728fd1fbc0bbce19f66d9b873998606e486e4cf18cf1359fb

The legacy available_at_ms field of the original Numeric/Laya parity receipt
continues to report the original local quote arrival. A separate
decision_at_ms now reports the later V2 feature decision. The original
source handoff similarly records source_event_at_ms and
feature_decision_at_ms separately. This prevents an innocent later signed
decision from being misreported as zero-latency original availability.

Signed, delayed source event success in unit tests uses synthetic fixture
quote rows, distinct test-only Ed25519 roles and simulated historical clocks.
It does NOT assert a live crosshost producer has signed a fresh market
input or that actual GPU inference finished before its broker expiry.

The current old LIVE V1 profile, original source reader, broker quote TTL
policy, source-health admission, current Trial3, and live trading units are
not changed or installed by this branch.

## Additional pre-decision admission fence

Phase 4B added a second distinct check at signed source-event final use.
An independently valid signature emitted BEFORE the decision is not enough
if the locally sealed source witness is only verified or fsynced AFTER the
decision. For the NEW broker-expiry profile, the actual local ingestion time
must be no later than the predeclared decision cutoff both during original
source replay and again after the signed event witness is fsynced. An
expired local event returns ORIGINAL_SOURCE_ADMITTED_AFTER_DECISION.
The old V1 timing contract and frozen trial evidence are unchanged.

This enforces the local signed-witness fsync BEFORE the decision.
The subsequent separate SQLite research-index COMMIT can still happen
later; this source-only research ledger is not itself evidence of a
model prediction fsynced before a real trading order.

New negative test: producer Ed25519 signature made earlier is valid,
but final source ingestion occurs 1ms AFTER the original decision.
The attempted admission is rejected. Distinct source-event,
feature-decision and proof-evaluation times remain separately recorded.

Post-fence final source/code inventory smoke:
~/.local/state/heptatrader/operator-evidence/train-v2-broker-ttl-20261010/original-source-v2-ttl-final-post-fence.json

SHA256: 644a657b0759348ebd76fcd747e80a4cdf3a008f7361f6c00acdee6b4f40e1a3

The original two receipts remain intact and are not reclassified as
post-fence code proofs.

## Exact final Pocket4 verification after source-before-decision fence

- New Phase 4B unit suite: 10 passing adversarial tests, including valid
  after-quote/before-decision Ed25519 fixture admission, sign-too-late,
  sealed-witness-too-late, original-shorter-broker-expiry forgery, original
  hash-chain tamper and second-read mutation.
- Full historical research: 400 passing, 7 existing skipped.
- Full core Python: 1,843 passing, 21 existing skipped.
- Source/documentation/build ownership partition: 106 passing.
- Original quote source: 678 rows locally verified; 255 new bounded late
  decision candidates versus 255 correctly refused under V1.
- Execution still running at 36 cumulative restarts; FX/BTC drivers active,
  resident Laya trainer HOLD preserved. Full EURUSD unrealized and account
  net PnL remain incomplete: no current qualified mark.
- No source hook, training worker, GPU inference, new broker query, model
  selection, trade order, systemd service or historical trial was altered.

This is a research timing/cryptographic verification improvement only.
Canonical host enrollment, NEW independently observed market event,
actual signed prediction sealed BEFORE original broker PREPARED and new
unseen net-profit validation are still mandatory.
