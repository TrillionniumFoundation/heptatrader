# Train V2 Phase 4I: exact observed original all-action model provenance
# and physically separated research signer custody

2026-10-11 Pocket4; stacked on Phase 4H. Research source audit only.
No original broker query, trade, model change, credential enrollment or
Canonical product LedgerWriter write is granted by this phase.

## Original all-action captured MODEL outputs, not policy propensities

Previously the Phase 4E/4F auditor checked only 77 original PAPER
OPEN PREPARED command bindings. The unchanged original attribution
journal also locally captures original BUY, SELL and HOLD model outputs.

original_decision_population.py verifies the actual pinned original
runtime/manifest bytes and bounded owner-private local source journals,
checks every SHA256 entry, full per-producer sequence and predecessor
chain, full REQUEST -> RESPONSE -> DECISION identity, original prompt
SHA, decoded original model envelope, response availability and code
scope. Captured original incomplete responses and model decisions are
never synthesized into an invented HOLD or score. Results omit all
raw prompts, original model output text, rationale, raw request IDs
and any trading credentials; only count/digest summaries are saved.

The installed BTC wrapper is materially different from the simple
three-field original model decoder. It *intentionally and deterministically*
adds issued/available timestamps, source midprice, original input SHA,
forecast schema and model identity. The auditor requires the exact
observed installed BTC economics wrapper and helper SHA256, then verifies
all these fields against the original model and up-to-24 quote input.
A new wrapper version must fail closed pending requalification.

## Actual, locally verified immutable source snapshot

Final v3 readback:
- 3,642 captured REQUEST entries;
- 3,641 captured RESPONSE entries;
- 3,627 captured DECISION entries;
- **3,627** source-verified, correctly linked original model output
  graphs after checking exact approved BTC transformation;
- 15 captured original requests without a complete recorded DECISION:
  do not impute an action, reward, source timing or behavior probability;
- Observed ORIGINAL model outputs: **758 BUY / 2,141 HOLD / 728 SELL**.
- EUR/USD changed strategy: 1,320, with 243 BUY / 858 HOLD / 219 SELL.
- EUR/USD unchanged strategy: 20, with 5 BUY / 11 HOLD / 4 SELL.
- BTC/USD unchanged strategy: 2,287, with
  510 BUY / 1,272 HOLD / 505 SELL.
- The same observed OPEN PREPARED bindings remain 77.

The readback is a bounded immutable snapshot of the source at that
instant. BTC is still actively generating more logs. The model-output
graph does NOT prove the denominator of all possible original policy
decisions, no-call branches, vetoed choices or known action-selection
probabilities. Model confidence remains unrelated to actual behavior
policy sampling probabilities.

Thus qualified original off-policy samples remain **ZERO**; production
Generator/Observer/root, Native LedgerWriter market decisions, durable
Witness ACK, SnapshotV3, Owner/CURRENT and after-cost forward efficacy
remain **HOLD**, not presumed from locally hashed attribution.

Private v3 evidence:
~/.local/state/heptatrader/operator-evidence/train-v2-decision-population-20261011/original-observed-all-actions-v3.json

SHA256:
f5b9d76688bd2a721b2e330c4a1fbc18f1afa59abc1d835c4b96f54fd91f5311

Earlier v1/v2 receipts, including the initially overly strict BTC
model-output comparison, are retained. Do not overwrite them.

## Real three-machine research signing / isolation experiment

Unlike the prior qian-qi-only key generation, three ephemeral Ed25519
signer keys were actually generated, stored privately and used to sign
separate scoped TEST ONLY challenge payloads on THREE physical hosts:

| Research role | Signing host |
| --- | --- |
| Registrar | qian-qi |
| Producer | MacBook Pro |
| Observer | ThinkPad X230 |
| Verifier | Pocket4 |

Only public keys, signature bytes and public scoped research witness
contents arrived on Pocket4. Mac did not receive the Observer private
key and X230 did not receive the Producer private key. The unchanged
research Ed25519 verifier checked all roles and refused role switching,
payload/binding mutation, Observer revocation and trust-generation
rollback. The signed PLAN was NOT registered into the trial registry;
producer/observer EVENT identifiers were explicitly SYNTHETIC, NOT
the authentic original market source or an actual economic outcome.

After signing, all three signer hosts deleted their temporary private
key files and test directories. Fresh SSH readback saw zero matching
private test-signing directories. This does not prove cryptographic
secure erase or independent legal/administrative ownership.
Pocket4 later revalidated historical signatures from public evidence
only, and rejected current-time reuse of the expired witness records.

Research only SHA256 evidence:
- Initial distinct-host signature receiver:
  313c6d001ff2fd0e176fef57891d2a64d6bb98d231382a649977c22677fa20fc
- Three-machine private-key cleanup re-read:
  e3505e45790beb394c2917f1eff3e2762a80154f4f5daaa6350346e3c9018fb1
- Historical public signature revalidation and expired-replay rejection:
  aca31a48d5b9b38e4770df771982ac41758982890764b111b0d44837aae7df77

Pocket4 private role evidence:
~/.local/state/heptatrader/operator-evidence/train-v2-independent-host-roles-20261011/

These public trust keys are operator-pinned RESEARCH keys, not
root-activated canonical Agentd trust or separately administered
product Generator/Observer credentials. No product V2 signed
Decision/Outcome, local prospective event or trading authority
was created. This experiment verifies physically separated
signing operations; it does not by itself establish full P1 product identity.

## Tests and preserved gate

The adversarial new model-graph tests cover original FX and BTC
literal response, exact BTC postprocessor recomputation, source
and prompt integrity, missing/duplicate Request IDs, hash mutation,
unknown output, partial final line, model clock and version drift.
Targeted new graph + original previous OPEN provenance tests:
43 passed, 12 subtests, no failures.

Full core Python + historical research:
**1,653 passed, 21 skipped, 733 subtests passed, zero failures**.
Exact log SHA256:
ff3c378d46848ceed24b223a278471d511b7ec23a0389b12cc229963d0ec1963

### Final immutable Pocket4 Phase 4I evidence seal

This consolidates the exact original observed model snapshot, 15 missing
decision records, all test outputs, the three real different-machine
research signatures, signer key cleanup recheck and expired witness
replay refusal. The output explicitly marks ALL production-native
Generator/Observer, product witness, DatasetSnapshotV3, OPE admission
and actual after-cost efficacy as FALSE.

~/.local/state/heptatrader/operator-evidence/train-v2-decision-population-20261011/phase4i-final-source-and-roles-receipt-v1.json

SHA256:
918e112d34f0a31c820f83dc47315c32d229964cffbaa387143423925d19ccc0

The next real product gate is an actual order-capable Generator with
complete legal candidate enumeration (including HOLD and risk
vetoes), true original selected action propensity and a real
host-pinned root signature; an independently administered Observer
must then sign actual later outcomes. That source must enter
native product LedgerWriter plus durable witness before
revalidation/freeze DatasetSnapshotReceiptV3 and Owner selection.
Fresh post-open market forecasts and independently assessed
fees, funding and fill-based net efficacy remain absent and
must not be fabricated. Production admission remains HOLD.

### Reproduction

With PYTHONPATH=research:research/neuron_learning:scripts:

python3 -m pytest -q tests/python/test_train_v2_original_decision_population.py
python3 -m train_v2.original_decision_population --releases-root
  ~/.local/share/heptatrader/decision-attribution/releases
  --output NEW_EMPTY_PRIVATE_ROOT/all-model-actions.json

Never overwrite historical v1/v2/v3 receipts or interpret
a model-output frequency as its actual behavior propensity.
