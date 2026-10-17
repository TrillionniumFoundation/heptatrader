# Prospective resident forward-plan admission — 2026-10-17

## Problem and scope

Pocket4 completed a real 32-row historical training job on Saturday and registered trial 41 while the regular EUR.USD session was closed. Its immutable prediction interval starts at 2026-10-17 22:05:50.417 UTC and its last outcome deadline is 2026-10-18 07:23:55.417 UTC: the entire 9 h 18 min 5 s interval precedes the next regular Sunday opening at 21:15 UTC. The candidate's declared validation deadline is 22:02:21.139 UTC on Sunday, leaving about 47 minutes after that opening.

The old resident path did not enforce the candidate's declared validation end when creating a future plan. This change introduces an explicitly **new admission policy**. It does not retrospectively reinterpret the old contract.

Trial 41, every earlier plan, original outcome, qualification, candidate manifest, model adapter and alpha allocation remain immutable. A previously registered trial is recovered on its original terms even when the market is now closed or its deadline has passed.

## New registration policy

The opt-in profile is `REGULAR_FX_FULL_WINDOW_ORIGINAL_SOURCE_V1`, with a positive, recorded `future_plan_admission_effective_at_ms`. The three policy modules must be SHA256-pinned in the installed runtime.

A new plan can reserve an alpha-bearing trial only when:

1. Its candidate is the exact manifest bound to a COMPLETED scheduler job.
2. The complete proposed prediction/outcome interval fits within the candidate's declared validation window and one continuous regular FX session.
3. Creation itself is during a regular open session; the existing two-minute lead, rule, slot spacing, horizon and grace are preserved.
4. The enrolled future reader supplies a causal, contiguous EUR.USD input backed by an original verified receipt prefix. The original quote is authoritative IB data, has an active subscription, and remains within both its own expiry and the strict five-second TTL at commitment.
5. Calendar, candidate identity and source freshness remain valid after the source read.
6. The historical trial counter has a complete, unique plan history and no unpublished reservation remains.

The existing evaluator source/resource guard and historical training resource policy remain in force. Historical training does not require a currently live quote.

The regular calendar uses America/New_York DST, Sunday 17:15 opening, Friday 17:00 closing and the existing weekday 17:00–17:15 rollover exclusion. A future open session is not a promise of quotes, holiday availability or outage recovery.

The timing decision ignores model scores, losses and PnL. Its search is bounded to seven days; original receipt resolution is bounded to 200 paths and 64 MiB. Files are checked as private, owned, regular, non-symlink original sources, and exact prefix bytes are hashed again. File modification time is only a search-order hint.

## States and durable recovery

| State | Meaning | Trial / alpha |
| --- | --- | --- |
| DEFERRED | A feasible later interval remains, the original source is temporarily unready, or the previous original trial is still pending | Not allocated |
| NO_FORWARD_TRIAL | No complete interval can fit in the remaining declared candidate window | Not allocated; training stays COMPLETED |
| RESERVED | Complete frozen plan and incremented trial counter committed in one SQLite transaction | Allocated exactly once |
| PUBLISHED | Original plan and deterministic unadmitted owner handoff are completely published | Same original allocation |

NO_FORWARD_TRIAL is an untried trained candidate, not a model performance rejection. A terminal database record alone cannot hide a waiting candidate: the immutable receipt must exist and match its manifest, policy, job and record digest.

Registration uses BEGIN IMMEDIATE before reading/updating the allocation state. A RESERVED record contains the entire original plan. Publication writes and fsyncs a complete private staging file, then atomically links it into the final name without replacement and fsyncs the directory. Recovery never changes a frozen time or rechecks source to manufacture a replacement trial.

The RESERVED-to-PUBLISHED compare/update uses a separate explicit write transaction. A changed reservation fails and rolls back. Reconciliation replays RESERVED records before queue inspection, so a crash after plan publication but before handoff/metadata completion is recoverable in the next loop without restarting the service.

An unmatched older counter is a fail-closed historical allocation problem. It is not reclassified as zero alpha consumption.

## Validation

Pocket4 source tests:

- 31 new calendar/registration/original-source tests passed.
- 153 existing resident, native proposal, reward, continuity, archive, future-source, feed-health and asynchronous evaluator regression tests passed.
- The same 31 new tests passed using the exact prepared runtime with its original installed dependencies, independently of the development source path.
- Exact installed resident and guard configurations validated.

Coverage includes DST and exact closing endpoints, internal rollover, Saturday no-fit versus genuine future deferral, original short expiry, inactive subscriptions, stale quotes behind new heartbeats, source reads crossing closure, clock regression, rewritten receipt prefixes, concurrent one-time registration, interrupted publication, terminal receipt corruption, real queue recovery and publication CAS rollback.

The two old asynchronous source fixtures initially failed identically on an unmodified control branch. They assumed eager double source scans, while the existing evaluator now scans once for mature predictions. The test-only correction retains nonblocking prewarm, no early source scan or outcome, exact target plan/slot, and a fresh causal as-of time taken after prediction loading. The evaluator implementation is unchanged by this work.

A read-only counterfactual using each of the 41 original plans and its original registration time produced 33 READY, 4 DEFERRED and 4 NO_FORWARD_TRIAL calendar decisions. These are diagnostic classifications under the new policy, not rewritten trials, source availability proofs or performance results.

## Prepared runtime boundary

The release replaces only `learn/laya_cell/resident.py` and adds:

- `learn/laya_cell/resident_plan_admission.py`
- `learn/laya_cell/resident_plan_calendar.py`
- `learn/laya_cell/market_session.py`

Every existing evaluator, predictor, model, training, dataset, native binary and future-source runtime pin is copied byte-for-byte. The running scoped resource guard, cache maintenance, manual holds, budgets, accounting period, objectives and value-priority policy are preserved.

Prepared release: `forward-plan-admission-b5c698626c6f3818ff6c`.

At preparation the live scheduler had 41 COMPLETED jobs, 1 FAILED job, no running/admitted child, counter 41 and one pretrained candidate waiting behind the immutable trial 41. The trainer-only activation and before/after verification are recorded separately; preparation alone does not imply activation.

Pocket4 evidence directory:

```text
/home/alex/.local/state/heptatrader/operator-evidence/train-forward-plan-admission-20261017
```

The evidence contains focused and regression logs (including the failed baseline control), calendar compatibility records, the complete immutable release inventory, exact configurations, and the proposed trainer service override. No broker order, execution authority or model promotion is introduced.


## Activation and live verification

The trainer-only switch completed at **2026-10-17 23:20:24 UTC** (19:20 New York). Trainer PID 2966959 loaded the immutable release and validated its exact guard/configuration identities.

The initial SIGUSR1 drain did finish normally. The existing readmission timer immediately reactivated the old definition, so activation loaded the new trainer definition and explicitly stopped/started the idle trainer. No active training/GPU child was present at the switch. The readmission timer was not changed.

Post-switch checks proved:

- All **41 original plan files** retained their SHA256.
- All **82 candidate manifest/adapter files** retained their SHA256.
- The trial counter remained **41**, with **41 COMPLETED / 1 FAILED / 0 RUNNING / 0 ADMITTED** jobs.
- The latest candidate has one bound DEFERRED registration with reason PRIOR_ORIGINAL_TRIAL_PENDING and trial_allocated=false.
- The live state is CANDIDATE_READY_AWAITING_FORWARD_ADMISSION. The previous original trial remains pending on its frozen deadline; no replacement trial was allocated.
- Guard modules, evaluator/source pins, objectives, budgets and the 6 GiB trainer memory fence were preserved.
- Execution, broker/gateway, EUR/BTC drivers, feed and Laya evaluator process identities remained unchanged. Of 28 observed non-trainer services, one separately scheduled value-successor evaluator changed state during the observation interval; no control request was sent to it.
- The trainer error record was inactive after a successful new-runtime loop.

Live state use was 670,759,547 bytes against the unchanged 12 GiB quota, and storage admission was ready.

The newest historical training candidate is 7becfed5397437dedf9d385fe57b3e35e720830f680fbba8a9f8288ce00f3f69 (job d08687f663923e0a3fd2adbc795e16c79e88671850e800f6b8b3455ecdff4da0). Its 32 reserved rows were all applied in 16 steps, with no rollback, 73.2649 seconds wall time and 1,587,737,088 bytes peak GPU allocation. Training-only price CE changed from 2.45851035416 to 2.45823210850, with Brier 1.01501643099. Its action targets were 0 ACT / 32 ESCALATE. PnL reward was not used. These figures are execution/diagnostic evidence, not prospective performance or trading eligibility.

Activation evidence: trainer-activation.json and live-verification.json in the Pocket4 evidence directory above. The original failed drain-wait observation is retained as evidence; it is not presented as a completed inactive-state check.


## Runtime evidence snapshot at 23:27 UTC

The new trainer PID remained 2966959 over subsequent successful loops. Its error record was inactive, no manual hold was present, and all original plan/candidate hashes remained unchanged.

The evaluator reported **322 OBSERVED / 1,101 CENSORED**, with 40 closed REJECTED plans and the original trial 41 still PENDING. These pooled counts span different historical plans; they are not a new independent model-quality sample.

| Historical censor reason | Count |
| --- | ---: |
| ENDPOINT_GAP | 27 |
| NO_PREDICTION_IN_FIXED_SLOT | 679 |
| PREDICTION_PROCESS_FAILED | 353 |
| PATH_GAP | 31 |
| OUTCOME_MISSING | 11 |

Among the 40 closed qualifications, 33 list insufficient observable windows and excess censoring, 37 list insufficient paired-sign evidence, 21 list accuracy below the bootstrap, and 14 list no positive mean improvement. Reasons overlap and must not be summed as distinct trials. Missing source/prediction evidence and model weakness both need attention; closed rejections alone do not identify a single cause.

Feed health remained CAPTURE_UNAVAILABLE with quote_fresh_now=false. The latest verified quote was observed at **2026-10-16 16:15:16.801 UTC** (12:15:16.801 New York). The current segment had 0 quote captures and 245 failure captures at this snapshot. Segment counts reset at rotation; they are not the lifetime outage count. KNOWN_OPEN_FAILURE_UNRECOVERED is retained across weekend rotation.

The pinned regular-session calendar next opens Sunday **2026-10-18 21:15 UTC / 17:15 New York**. That calendar fact does not prove that the prior open-session outage, subscriptions or future original source will recover.

Both recent historical training completions generated automatic, completed COMGR receipts. Each removed only 52 terminal COMGR files totaling **191,734,856 bytes**, with training_evidence_pruned=false. The final resident state footprint was **670,890,632 bytes** against the unchanged 12 GiB quota.

Detailed snapshot and failure diagnostics are retained as final-runtime-audit.json and worker-failure-detail-audit.json in the Pocket4 evidence directory. This is source/operation evidence; no performance acceptance, model promotion or order follows from it.
