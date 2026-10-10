# Phase 4E: original policy cohorts and true propensity gate

Pocket4, 2026-10-11. Stacked on Phase 4D. **Read-only, research-only;
no original trading/strategy modification, broker request, order, inference,
model update, production identity, Canonical LedgerWriter append, or promotion.**

## What was actually audited

`python3 -m train_v2.original_policy_audit` inspects existing immutable
PAPER decision-attribution producer manifests and their exact registered
pinned runtime file SHA256, original period SHA256, local journal HMAC-free
SHA256 chain, exact REQUEST → RESPONSE → DECISION → original durable PREPARED
command provenance, and source-prefix witnesses. It does not ingest model
confidence as an action probability; no arbitrary caller can set an
`authenticated_propensity` or `canonical_owner_accepted` result.

Strict per-release separation is essential: the original installed
`trading_behavior_changed` value is a boolean identity dimension.
A changed controller version does not become a sample of the unchanged
baseline simply because the instrument, prompt, or return code matched.

Real Pocket4 source readback (3 locally installed signed-by-self manifest
hashes, not external signed trust) verified:

| Instrument | Declared policy changed | Exact command bindings | Chain issues |
|---|---:|---:|---:|
| EUR.USD release A | true | 74 | 0 |
| EUR.USD release B | false | 2 | 0 |
| BTC.USD release | false | 1 | 0 |
| Total | mixed (do not pool) | 77 | 0 |

The exact per-release command identity sets have **77/77 distinct**
\`(instrument, command_id)\` keys across all three versions, with **zero**
cross-release duplicates. The auditor now detects and rejects duplicate
command identities across cohorts, rather than summing overlapping keys.


Owner-permitted read-only evidence file:
`~/.local/state/heptatrader/operator-evidence/train-v2-original-policy-verified-20261011/original-policy-v3.json`

Evidence SHA256:
`907f0bb3822cdb027d188730fd8a136ed6a45dbabb2f37284ec3d0748aedbbc0`

A combined source/native-attribution regression passed **100 cases and 13 subtests**; exact log SHA256 \`594e46b1019bdb1561f7f32021e809ed8ced207db850d23bbaf9c97a862e1bd0\`.\n\nThe first ad-hoc 77-binding scan lacks runtime-file identity enforcement.
The stricter implementation validates every runtime file, original period
and source, and separately reports unchanged vs. changed policy cohorts.
This is provenance of real recorded command paths, NOT an outcome join,
counterfactual distribution or independently attested learning row.

## Reproduction

```sh
cd /home/alex/work/heptatrader-train-v2-native-ledger-qualification-20261010
PYTHONPATH=research python3 -m pytest -q tests/python/test_train_v2_original_policy_audit.py
PYTHONPATH=research python3 -m train_v2.original_policy_audit \
  --releases-root /home/alex/.local/share/heptatrader/decision-attribution/releases \
  --output /home/alex/.local/state/heptatrader/operator-evidence/NEW-PRIVATE-ROOT/original-policy.json
```

Output is created once (O_EXCL), private 0600 with directory fsync and
cannot be rewritten to relabel a previous outcome. Invalid/changed
producer manifests are explicitly counted, not skipped as successful rows.

## Exact remaining source-level problem

The present model interface returns `action`, `confidence` and
`rationale`. The original `entry_candidate_allowed` predicate only
checks returned direction and a legacy confidence threshold; other
broker/owner/position/risk gates can veto an OPEN. Neither the original
REQUEST nor PREPARED attribution event includes a generator-enumerated
complete legal action set, its precise hard filters/omission bound, or
the logged probability of the action selected by the entire behavior
policy *at the original decision time*. A model confidence scalar is
not the action's probability. The existing SHA256-linked research journal
is also not a product root-signed Generator identity or independently
retained LedgerWriter witness.

These 77 past decision paths are **not fixable by backfilling guessed
propensity**, by assuming a deterministic post-model predicate gives
the pre-model action distribution, or by using an on-policy evaluation
against another behavior-profile version.

### Mandatory next gate (still HOLD)

1. Under actual operator/host authority, instrument one NEW future
   pre-action decision owner to enumerate complete legal candidates,
   canonical order, generator code/grammar/filter/truncation digests
   (omissions = 0), actual action selection distribution and random
   mechanism. Preserve the actual predecision time and REQUEST/PREPARED
   identity. A hidden stochastic LLM sampling distribution is not
   recoverable from the returned confidence.
2. Register and protect actual Generator and independent Observer
   principals/controllers/credentials/signing keys through native
   root-signed trust; separate credit allocator, evaluator and Owner.
3. Write each genuinely new original Decision through native
   LedgerWriter and its independent durable witness, reconcile unknown
   acknowledgement without resending an order, and add real independent
   corrections/revocation, DatasetSnapshotV3 freeze and final revalidation.
4. Only then preregister and seal a truly unseen quote window before
   future outcomes and evaluate independently using actual fill/slippage,
   commissions, financing and post-cost net reward. All existing PAPER
   driver controls, risk exits, and service state remain unchanged.

## Original full-workspace Cargo closure (2026-10-11)

The exact original Canonical checkout at 78fdb0cf8537e3a84fc6e0a849707559c80881e8
now has 8,665 clean tracked files, 193 workspace members and **1,517 locked
packages resolved offline**, with no Cargo.lock changes. All 1,316
registry archives were verified against their original SHA256 checksums.
The native original LedgerWriter and Artifact Owner packages built and
passed 210 library tests in that full workspace, with zero failures and
two ignored tests. See Phase 4D for commands, full scope, and evidence
hashes. Earlier missing actix-http and v8 archive gaps were resolved.

This is a source and native library qualification only. Original complete
PAPER behavior-policy candidates and true propensity, production independent
signers/observer, native product facts/witness, V3 final revalidation
and independent unseen after-cost efficacy remain HOLD.
