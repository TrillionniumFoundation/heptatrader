# Independent prospective financial-value validation and guarded selection

## Implemented dataflow

This is an observation-only validation lane. The existing FX/BTC strategies,
order permission, actor/scorer trainers and financial-value fitting service are
not modified or restarted. Four independently confined roles operate on a fixed
candidate: registrar, warm predictor, evaluator, and selection verifier. None can
send orders. Local witness signatures are explicitly NOT canonical Agentd keys.

The active FX producer-policy identity must match the head's training behavior
scope. Historical direction-only rewards cannot validate the newer re-entry
policy. Candidate selection for a future trial is chronological, one active
candidate per behavior scope; its weights, profile, training-mean baseline and
rule stay frozen for the entire trial. Later training jobs never refit this head.

On each actual flat-position model REQUEST, before the remote decision returns,
the warm frozen Laya encoder generates BOTH BUY-conditional and SELL-conditional
USD predictions. The code never reads the model RESPONSE to choose which value
to publish, never changes an order decision, and never claims an unexecuted
counterfactual direction as a second observed trade. Raw exact prompts and the
pre-open action/quantity context use the same 2048-token readout profile as fitting;
truncation is forbidden. No market/commission/outcome values from the future enter
features. Pretrained and readout parameters remain in eval/no-grad mode.

A prediction payload is fsynced first, then a separate role-signed commit witness
is emitted. The conservative later signing timestamp must precede the original
PREPARED command and the first actual fill. A prompt arriving too late, context
failure, missing commit or late computation remains explicit rather than getting
backdated. Predictions for HOLD/non-trade requests remain visible and do not
inflate trade sample count. Runtime hashes and immutable producer manifests bind
the exact plan, inputs and code.

## Independent monetary outcome and qualification

The evaluator imports no torch/optimizer and replays current OMS/commission and
exact attribution. Only actual closed, settled, fee-complete behavior-bound
original commands can be scored. Corrections are immutable revisions; a current
training reward correction/withdrawal revokes its candidate's qualification.
Previously seen commands stay in a permanent denominator when provenance
vanishes. Closed real opens without exact attribution remain coverage failures.
Trades closing after the fixed maturity allowance cannot later become a PASS.
Large losses are retained (not clipped away) and extrapolation beyond the fit
support is explicitly disqualifying.

The initial precommitted rule is 14 calendar days of new inputs plus one day for
outcome maturity. It requires at least 20 fully observed actual trades, at least
three UTC dates, at least five trades in each fixed time half, both return signs,
no unresolved original trades, and positive replicated error reduction versus
both $0 and the frozen training mean. Estimated MAE improvement must exceed $0.10.
Actual-commission stresses are 1x, 1.5x and 2x; these are error-robustness checks,
not counterfactual actual trading PnL or forecasts of a changed behavior policy.
The five-/twenty-trade checks are minimum support, not a guarantee of skill.

A finite mixture of Hoeffding test supermartingales operates on paired scaled
Huber-loss differences. Huber is 1-Lipschitz, so the predictable bound is
|baseline_prediction - candidate_prediction| / 100, even for an unclipped large
outcome. Alpha is allocated 0.05/(trial*(trial+1)) across candidates and split
across the two benchmarks. This is valid under the stated conditional
no-advantage null; it does not assume independent market ticks. It does NOT
promise arbitrary-distribution-shift generalization or turn a single reward into
proof. A small/bad/zero-trade dataset cannot skip the fixed future window.
Primary reference: Howard, Ramdas, McAuliffe & Sekhon, Probability Surveys 17
(2020), 257-317, DOI 10.1214/18-PS321. Repeatedly polling status is not repeated
unadjusted testing or permission to stop a favorable window early.

## Role witnesses and durable correction handling

Four distinct Ed25519 local keys are created under private host storage. A
worker enters Landlock/seccomp before torch threads and can read ONLY its own
secret key, with real denial probes against the other roles and trading bearer.
The plan and its public trust digest are signed by the registrar, output commits
by the predictor, current assessments by the evaluator, and consumer state by
the selector. Editing a PASS string or substituting a same-name model cannot
mint another role's signature. Privileged host compromise is outside this local
software-isolation claim. No key files are repository artifacts or user reports.

Current assessment digests omit polling timestamps but retain outcome/fee
versions, so unrelated ledger suffixes do not make spurious candidate versions.
Missing/expired/invalid evaluation or canonical trust closes read-only selection
instead of retaining a stale fallback. Old candidate artifacts and decisions are
retained; no automatic pruning hides a bad trial. Finite 512 MiB storage stops
additional work before exhausting the host.

## Actual canonical native boundary, not a fabricated owner ACK

`financial_owner_verify.rs` links exact, unmodified
`hepta-private-ci@78fdb0cf8537e3a84fc6e0a849707559c80881e8` source for
`codex-hepta-learning-artifacts` and `codex-hepta-types`. It invokes canonical
`validate_artifact_manifest_v2` against actual candidate bytes. A successful
result is named `CANONICAL_MANIFEST_VALID_NOT_OWNER_ADMITTED`.

For a real externally enrolled owner, the bridge implements the actual canonical
`ArtifactOwnerVerifierV1::verify_current_registry_view`,
`ArtifactSelectionVerifierV1::verify`, `load_selected_candidate` and
`with_current` calls. They verify independent owner/selector keys, CURRENT
registry identity/epoch/expiry and retained anti-rollback requirements, exact
support/content/compatibility, eligibility and loaded bytes. A plain
`owner_accepted=true`, copied file, local statistical PASS, or publication status
is not a substitute for those signatures. The bridge contains NO writer,
publication, trust-enrollment or signing route.

Canonical source's Agentd product integration currently exposes a tabular
terminal-cell path, not this financial tensor lane. Therefore the installed
configuration does NOT invent a canonical owner registration, host receipt,
LedgerWriter-derived training snapshot, artifact publication ACK or signed
selection. Those product-host steps remain uncompleted; invoking a canonical
library checker must never be reported as Agentd admission.

When future evidence qualifies, the separate selector can verify an independently
operator-pinned canonical host registration and signed current selection bundle.
Absent that registration it reports
`AWAITING_CANONICAL_AGENTD_OWNER_ENROLLMENT`. No loaded financial readout can grant
FX/BTC order permission; selection scope is read-only diagnostics only. This is a
ready conditional consumer, not proof that actual auto-promotion has occurred.

## Deployment and tests

`build_financial_owner.py` builds a fresh isolated copy of canonical dependencies
with recorded source, adapter, lock and binary hashes. The original source and
existing NDU/Plasticity binary remain untouched. Run canonical artifact tests and
bridge tests independently of the Python financial/evidence tests. One canonical
process-fault child is declared ignored by its author and is invoked by its
parent fault/reopen matrix; do not present it as an independent unrun blocker.

`install_financial_forward --config <existing-financial-config>
--native-verifier <built-binary> --start` creates content-addressed code/configs,
local role keys and only the four new user services. Existing differing units
require explicit `--update-owned`, which preserves previous units and releases.
Every real prediction retains signature/hash/timing receipts. Future outcomes
must mature naturally; no canary, artificial trade, altered clock or historical
backfill is used to accelerate validation.

Passing unit tests, executing GPU inference, retaining a prospective prediction,
observing a closed trade, statistical qualification, canonical owner admission,
and selection are separate evidence stages. None is substituted for the next.
