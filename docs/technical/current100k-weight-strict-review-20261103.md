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
These are software checks, not prospective financial performance results. The
first actual new opening and settled future outcomes still require observation.
