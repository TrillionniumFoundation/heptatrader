# Laya original-PAPER future evaluator admission attribution

## The real Pocket4 trial38 failure was not forecast accuracy

The precommitted trial38 original plan starts at 2026-10-16
00:49:23.271 UTC and reaches its ORIGINAL deadline at 09:49:28.271 UTC
(05:49:28.271 America/New_York). The original POWER_PLANNED_SIGN_SCREEN_V2
rule allows at most two CENSORED windows in 90; at the source-only
inspection in this change there were 58 original CENSORED windows,
all with NO_PREDICTION_IN_FIXED_SLOT, and zero forecast files.
Do not treat this as 58 inaccurate model forecasts or as a trial
that may be repaired by later-arriving quotes.

The authoritative Pocket4 resident-resource guard denials show actual
Value original REQUEST priority conflicts at the first prewarm
windows. In source evidence, slot0 first refused 00:48:09 UTC
(VALUE_PRIORITY_CONSERVATIVE_RECENT_ORIGINAL_REQUEST), slot1 at
00:54:09 UTC (same) and 00:56:09 UTC
(VALUE_PRIORITY_FRESH_FLAT_ORIGINAL_REQUEST); slot2 at 01:00:09
(FRESH_FLAT) and 01:01:09
(VALUE_PRIORITY_DEFER_NEW_LONG_EVALUATION).
The Value trial3 original plan remains active until 2026-10-23
05:45:31.425 UTC. OTHER_RESEARCH_WORKER_ACTIVE and host-memory/PSI
denials were also independently recorded for Value tasks in that
period; they should not be falsely attributed to the Laya slots.

The guard's immutable denials were saved under a separate resource
state root, not carried into the eventual individual original
NO_PREDICTION result. The evaluator's wrapper formerly returned
without recording the rejected prewarm attempt. After more than
two missed original slots, existing fail-closed futility suppression
correctly stops adding NEW predictions to an already impossible
formal trial; trial38 must nevertheless keep its original cutoff
and immutable results until terminal qualification.

## Minimal diagnostic repair for NEW future pinned releases

- The same original global heavy-worker lease, period/source/memory/PSI
  admission, Original Value priority, original 60-second fixed input
  grace, 90-slot planned statistical rule, and trade/no-promotion
  semantics remain UNCHANGED.
- On an actual admission refusal, the future guarded evaluator writes
  one first-attempt admission-denial-SSS.json receipt bound to the
  candidate ID, original plan ID, slot, guard reason(s) and timestamp.
  A blocked evaluator child is classified separately from a Value or
  host resource denial. If resource error detail contains an
  excessively long path, only the human diagnostic is truncated, not
  the admission decision.
- This is not a permanent forecast FAILURE marker. Subsequent
  attempts may still succeed within the same original fixed slot.
  The first reason is retained as evidence; it is explicitly NOT
  evidence that every subsequent attempt was rejected.
- If and only if the slot actually expires with NO PREDICTION,
  its unchanged CENSORED result now references the first admission
  denial SHA256 and reason codes. It is not promoted, relabeled as
  OBSERVED, re-scored, postponed or retroactively repaired.
- Changed plan/slot/candidate and forged prior denial receipts
  fail closed. Successful first admissions create no denial record.

This research diagnostic is intended ONLY for a future fully
hash-pinned runtime. The existing trial38 plan is sealed to its
original code bytes. DO NOT upgrade or edit the existing trial38
runtime, plan or historical results to backfill diagnostic fields.
The original Value requester and trading system are not restarted
or weakened by this patch.

An isolated current-state --check-only evaluator admission using
existing Pocket4 guard returned admit_new_worker=true with a verified
fresh PAPER source and no live flat Value request at that instant.
This supports intermittent capacity, not any guaranteed 88/90 future
source or resource success. The Value trial overlaps the next
prospective Laya period, and its independent original deadlines
remain higher priority. The proper next evaluation must prove
actual, frozen, advance worker admissions and a complete future
90-window campaign, not merely more training jobs.

## Testing and evidence

Isolated synthetic-Pocket4 tests cover real GuardedEvaluator.launch
denials, immutable first-reason retention, a later successful
retry, still-censored original missed deadline, missing-denial
fallback, forged receipt identities, active prior child,
unclassified denial, and bounded long OS errors. They verify
that broker orders, training, model promotion and fixed slot dates
cannot be affected by attribution metadata.

This is SOURCE-ONLY code development; original formal trial38,
original Value trial3, the independent frequency-prior Shadow and
Tiny MLP Shadow remain installed/running as before. No deployment
or production admission is implied by tests or code merge.
