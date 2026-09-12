# Reviewable IB PAPER harness and evidence

Status: CURRENT
Applies to: optional owner-operated PAPER rollout and certification
Implementation: `scripts/hepta_ib_paper_harness.py`, `scripts/hepta_paper_campaign.py`, `scripts/verify_ib_paper_rollout.py`

## Build once, admit one identity

A build-only owner dispatch produces one immutable candidate. Later dispatches select
an exact GitHub artifact ID and source SHA, not a moving branch or latest artifact.
The metadata resolver requires an owner-dispatched main build and its successful
candidate-build job. A failed later campaign does not invalidate that successful
build. Download uses exact artifact ID plus original run ID; each target host verifies
archive and executable bytes before use. New source is not checked out as build input
during continuation.

The root-admitted campaign schema is `heptatrader.paper-campaign.v1`, with exactly
`schema`, `binding`, `created_at_ms`, `account_mode=PAPER`. Binding comprises:

```text
campaign_id, candidate_sha, artifact_sha256, binary_sha256,
harness_sha256, driver_sha256, controller_sha256, profile_sha256,
account_fingerprint, host_fingerprint, instrument, quote_currency, base_currency
```

All digest/fingerprint fields are canonical SHA-256 except the Git SHA (40 hex).
The controller digest covers the portable controller, its imported evidence/verifier/
state/host code, shell wrappers and P1 policy. It is computed by
`hepta_evidence_io.controller_digest()`. Thus unrelated main movement does not change
an admitted campaign, but an actual controller/profile/driver change does. Prior
v1 self-reported rollout evidence cannot be promoted into a v2 campaign.

## Portable controller and host driver

The reviewable controller uses a separately installed digest-pinned host driver with
the [typed host contract](ib-paper-host-driver.md). The repository no longer leaves
stage orchestration, failure retention or consistency verification in an opaque
external program. Credentials and privileged installation stay external.

The production controller has no synthetic mode. Its tests use in-memory test
doubles without Broker credentials. A real host driver and account-qualified runtime
must still be supplied and verified before a genuine Broker campaign can execute.

## Strict v2 evidence

`verify_ib_paper_rollout.py` requires an independently admitted campaign path. Result
schema is `heptatrader.ib-paper-rollout-result.v2`; a digest alone is insufficient.
Three bounded digest-addressed evidence sources are parsed and cross-checked:

| Evidence | Contract |
|---|---|
| `authoritative-snapshot` | An explicit complete flat before/after barrier for every cycle, matching identity and connection epoch, increasing generation and non-overlapping times. |
| `oms-journal` | Each stable command has ordered durable intent, send attempt and reconciled outcome, with consistent order identity and LMT/DAY fields. Journal sequence is increasing; timestamps cannot regress. |
| `broker-callbacks` | Correlated economic fills carry execution IDs and valid quantities/prices, followed by authoritative terminal state. Duplicates must agree exactly. Missing economic proof, unexplained commands, overfills and limit violations reject. |

The verifier derives cycle counts and economic flatness from these relationships,
then checks reported totals. Empty snapshots and merely plausible text cannot pass.
The same one-unit, USD 5,000, one-active-order and one-unit gross-position P1 bounds
apply at every stage. Counts are **exactly 1, 3, 10 additional terminal cycles** for
canary, pilot and extended; reaching extended therefore observes 14 cycles total.
These are operational sample counts, not statistical reliability or strategy claims.

Every cycle is verified before starting the next. A stage receipt is
`heptatrader.ib-paper-rollout-verification.v2`, includes start/end time, exact binding,
evidence digests and result digest, and always declares `authorization_effect=NONE`,
`paper_authorized=false`, `live_authorized=false`.

## Durable continuation and failure evidence

`hepta_paper_campaign.py` maintains one private persistent store per binding. It takes
an exclusive nonblocking lock, records the active attempt before any possible send,
then verifies evidence and commits completion. Repeating a completed stage reverifies
its original receipt/bytes and sends nothing. A higher stage requires the contiguous,
verified earlier stages and chronological terminal boundaries.

A failure or interrupted attempt blocks automatic resubmission. A completion-state
write failure also retains the fence even if evidence verification succeeded. The
pre-send running record remains durable if the later diagnostic update itself fails.
No automatic recovery clears a failed state; real existing-command reconciliation is
required. Different artifacts or controller bindings cannot reuse that store.

Both PAPER wrappers create the final evidence directory **before** launching a
harness. Exit code, state and identities are synchronized to `campaign-exit.json`.
Nonzero exit, TERM and even KILL preserve already written evidence. KILL/power loss
may leave `outcome=running`, which means unresolved, not successful. Private scratch
HOME is separate and is never packaged as trading evidence. A harness returning zero
is not a verifier passing and is not trading authorization.

## Optional heavy certification

The twelve-scenario V5 campaign remains explicitly selected with `certify`, after the
same artifact's extended P1 stage. It uses the independently pinned heavy qualifier
and original [scenario contract](../ib-paper-qualification-scenarios-v1.json); P1
cannot select its larger limits. A fixed persistent certification evidence destination
refuses accidental re-execution of an uncertain run. This revision does not claim an
external V5 harness or a completed twelve-scenario real account campaign.

## Operations and tests

See [continuous PAPER operations](../operations/paper-continuation.md) for build,
admission, continuation and failure handling. Maintained tests cover transcript
correlations, duplicate/conflicting executions, empty/forged evidence, durable failure
retention, skipped/repeated stages, stale identities, simultaneous processes and
exact artifact metadata admission. Core CI owns these behavior tests; source workflow
checks do not substitute for them or for Broker observations.
