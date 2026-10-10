# Phase 4F: original REQUEST identity non-reuse across policy cohorts

2026-10-11, Pocket4. This is an **observational, read-only audit**, not
an independent policy sampler, production Controller credential, broker
order, qualified LedgerWriter event, dataset freeze or trial.

The attribution journal confirms causal order and exact original
REQUEST -> RESPONSE -> DECISION -> PREPARED command binding within its
locally SHA256-chained files. It does not cryptographically authenticate the
producer under a Canonical host root or record the original action-selection
distribution.

## Corrected duplicate admission

Earlier Phase 4E rejected reuse of (instrument, command_id) between
immutable releases. However, one original request_id could still be
attached to **two different command IDs** or to two release manifests and
then accidentally counted as two independent policy decisions.

The new read-only provenance gate:

- Requires a nonempty bounded source request_id and canonical SHA256
  formatting for input, forecast and prompt identity fields.
- Refuses a second original command binding to the same REQUEST within
  one release. Two commands do not imply two independent selections.
- Computes original (instrument, request_id) digests without exposing
  the raw IDs in the receipt; refuses reuse across releases
  even when command IDs differ.
- Retains separate trading_behavior_changed cohorts; no pooling or
  backfilling of unrecorded propensity.
- Does not reinterpret confidence or source checksums as original policy
  probabilities or native Generator/Observer signatures.

## Genuine Pocket4 evidence

Immutable original installed producer manifests and journal prefixes
were read again with the new checker, with current pinned runtime files.

- EUR.USD changed-policy cohort: **74** exact command bindings.
- EUR.USD unchanged-policy cohort: **2**.
- BTC.USD unchanged-policy cohort: **1**.
- Total: **77/77 distinct original REQUEST identities**,
  **0** repeated REQUESTs, **0** command-ID collisions,
  **0** original binding issues.
- No independent original action propensity was recorded; product
  learning and efficacy gates remain HOLD.

Private one-time evidence:
~/.local/state/heptatrader/operator-evidence/train-v2-original-identity-20261011/receipt-v1.json

SHA256:
fcbd1569f5aef0260536ab903cae3ae53df9434fcd88d4c5b3e50b5be01cd28f

Focused adversarial verification: **35 tests + 4 subtests, 0 failed**.
Expanded attribution, canonical readiness and native harness Python
regression: **103 passed, 17 subtests passed, 0 failed**.
Private regression: ~/.local/state/heptatrader/operator-evidence/train-v2-original-identity-20261011/python-regression.log
SHA256: 35479fa1785ab12c7c78ac26b9cdbb08720d5a3e5b69f087d604f3b1030624d6.
The native 210/210 Rust library tests and 256 synthetic test decisions
documented in Phase 4D are separate evidence, not proofs
of the production decision chain.

## Remaining hard gate

Do not reconstruct absent behavior propensity or complete action support
from model confidence, original BUY/SELL commands, past price movements,
or test-only deterministic keys. A future owner-authorized **new**
pre-action policy needs complete candidate enumeration, true sampled or
deterministic selection mechanics, explicit propensity and authenticated
Generator, independent Observer and host-controlled durable witness
before any native V3 dataset/training acceptance. No live PAPER service
or trade flow was modified by this phase.
