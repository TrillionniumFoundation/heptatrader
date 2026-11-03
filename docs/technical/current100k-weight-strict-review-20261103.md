# Current100k strict pre-open review (2026-11-03)

The resident request ledger already marked failed, late, and interrupted attempts
as ineligible. The scalar-head evaluator previously read the durable publication
alone, so a prediction left on disk by one of those attempts could still receive
credit when its publication timestamp preceded PREPARED. The corrected evaluator
requires the original successful request terminal and exact PREOPEN_BOUND_RESEARCH
command receipt as well.

## Preserved experiment

The existing plan, candidate tensors, inputs, numerical profile, 14-day input
window, 24-hour settlement grace, score formulas, sample rules, and significance
thresholds are unchanged. This verifies existing provenance. It does not replay
requests, create predictions, register a new window, modify labels, select a
financial candidate, or send orders.

## Review an already registered study

Use the new release Python import paths and run:

```sh
python3 -B -m laya_cell.current100k_weight_shadow_eval \
  --root /absolute/original/prospective \
  --strict-preopen \
  --output-root /absolute/separate/strict-review \
  --once
```

The independent output contains assessment-strict.json, evaluator-status.json,
retained outcomes and command denominators, and strict-review-provenance.json.
Provenance includes the original plan, registration, activation, predictor-config
hashes, and the new verifier implementation hashes. Original reports, predictions,
and request receipts remain unchanged. Repeating the command preserves the same
window and does not resubmit inference. Strict review refuses registration.

## Ordered export for the weight workbench

New independent strict reviews also publish
`outcome-sets/<document-digest>.json` before the assessment. Its
`outcome_set_snapshot` pointer contains the raw file SHA-256, row count and the
existing ordered `outcome_set_digest`. The exporter receives the exact list from
the same evaluation. It preserves source ordering, status, missing-command
denominators, targets, predictions and original timestamps. Source canonical JSON
continues to use `ensure_ascii=True`.

Source-unavailable assessments export their censored known-command list before
the final unavailable receipt is sealed. The original lower-bound denominator
and any unenumerated retained commands remain explicit; no observed credit is
restored. Restarting a sealed final failure retains the same snapshot pointer
and original assessment clock. Existing terminal receipts are never backfilled.

`current100k_outcome_export.py` is pinned in the review's implementation hashes.
It uses complete temporary-file publication, a create-only link and directory
fsync before the assessment references the snapshot. A failed export does not
advance the assessment. An old review without this snapshot remains an explicit
missing export: sorting its individual outcome folders cannot reliably recover
the original list order.

This addition is for a new immutable independent-review release and output
directory. Existing continuation allowlists, registered plans, predictor source,
candidate hashes, original reports and window dates are unchanged. Deploying the
new review requires its own pinned release; no running release is rewritten.

The export is compatible with the runtime's Current100k USD handoff. It introduces
no new scoring or statistical rule, registration, model inference, selection or
trade authority.

## Permanent missing predictions and comparison eligibility

An original request terminal of
`NO_PREDICTION_BEFORE_ORIGINAL_COMMAND` is immutable in the existing ledger.
A later fill, closure, fee record, inference retry or runtime repair cannot turn
that request into a successful original pre-command prediction. When the number
of proven permanent misses exceeds the plan's frozen
`maximum_unresolved_at_final`, formal comparison success is already impossible.
The schedule still remains `PENDING` until the original deadline.

Independent strict reviews now attach separate `comparison_eligibility` metadata.
`IRRECOVERABLY_INELIGIBLE` and
`formal_success_possible_under_frozen_rule=false` require supported, verified
negative evidence. The original state, dates, heads, outcome rows/digest,
denominator, scoring functions and statistical comparisons remain unchanged.
This field is not an early final protocol report. The status file repeats the
separate eligibility status so that `PENDING` does not imply recoverability.

`PreopenEvidence.permanent_prediction_failure` captures the original terminal,
input record, protocol, command reference and ledger binding without writing to
the source. It checks fixed source pins, request and producer identity, the exact
original command and full current binding, the negative terminal's scope, and
request/PREPARED/terminal/binding/review time ordering. It exports a digest-bound
proof with the original safe terminal/reference records and raw byte hashes.
It does not export request text or account data. The currently supported terminal
is exactly `NO_PREDICTION_BEFORE_ORIGINAL_COMMAND`; other statuses do not
automatically establish permanent ineligibility.

Missing or unsupported evidence yields `UNKNOWN` and a null formal-success flag.
Invalid proof material adds an independent evidence error without changing the
existing scoring or source issues. `CENSORED` alone never proves an irreversible
miss, and no absence of negative evidence is interpreted as positive eligibility.
If current source replay is unavailable, this metadata becomes `UNKNOWN`; the
existing immutable prior assessment and `last_completed_assessment` reference
retain any earlier verified negative result. Unknown current evidence does not
erase that historical fact or restore formal eligibility.

Covered OBSERVED rows remain available for descriptive diagnostics even after the
family becomes irrecoverably ineligible. Their metrics cannot repair missing
pre-command evidence or produce comparative selection credit. The runtime USD
handoff checks proof digests, original safe receipt bytes, command population,
clock order and the exact frozen limit. The original strict evaluator remains
responsible for full private request and ledger verification.

Omit --once for a resident observer that checks every 60 seconds and exits
successfully after writing its final assessment at or after the original outcome
deadline. The ordinary evaluator retains its existing 10-second loop. New normal
evaluator deployments automatically verify pre-open receipts when the study has
a supervisor activation. Existing frozen releases retain their original code;
use this independent review for their corrected interpretation.

## Source loss and fixed-deadline closeout

Strict review retains a create-once copy of the already registered plan and
registration in review-plan-snapshot.json. This permits restart and closeout if
the original source later disappears; a first launch without a valid original
registration still fails. The retained copy never registers or extends a window.

When source identity, initialization, ingress, or authoritative ledger replay is
unavailable, the current assessment withdraws all OBSERVED credit, numeric error
metrics, and comparative acceptance. It keeps known original commands in the
CENSORED denominator and preserves the prior reported count as a lower bound if
local enumeration is unavailable. The report explicitly marks the command count
as incomplete, records source_issues, and separates known command identities from
any retained but unenumerated count. It never creates substitute command IDs or
zero-valued outcomes. The zero OBSERVED count and coverage describe withdrawn
verification credit over the retained lower bound; current_source_totals keeps the
actual unavailable source counts and coverage explicitly null. Previously complete
reports remain archived and referenced
through last_completed_assessment; they are historical evidence, not current
credit.

Source failures before the original outcome deadline produce PENDING assessments.
At or after that exact deadline, the observer writes FINAL_INCOMPLETE and a
create-once strict-terminal-unavailable.json, then exits successfully. Restart
restores the same final report without replay or a new timestamp. A complete
current replay can restore verifiable credit before finalization. A rejection of
one prediction remains a local CENSORED case; it does not falsely describe the
entire source as unavailable.

All these files and locks are confined to the independent review output directory.
Use a fresh versioned output directory for a changed verifier release because
strict-review-provenance.json pins the verifier implementation hashes. Retain prior
outputs rather than changing their provenance in place.

## Verification

The 57 existing shadow, pre-open ledger, and supervisor tests and 10 new strict
source-failure regressions pass. The latter cover actual identity read failure,
whole-source disappearance and restart, first launch without a plan, retained
but unenumerated command counts, normal-return incomplete replay, preserved last
complete reports, local prediction rejection, and fixed-deadline exit. Regressions
cover failed and interrupted terminals, a monotonic deadline miss despite an early
wall-clock publication, completion at PREPARED, missing terminal, conflicting
command receipt, configuration changes, isolated outputs and restart, changed
attempt/original-command receipts with unchanged terminal/binding files, and
strict-loop final exit. All rejected commands remain in the denominator.

The cache tracks every prediction and pre-open receipt read during validation.
These are software checks, not prospective financial performance results. Actual
pre-command publications and settled future outcomes require separate original
lineage evidence. The export suite additionally checks permanent-negative
receipts, invalid/unsupported evidence, exact original-command binding, retained
observed diagnostics and source-loss history without altering the final clock.
