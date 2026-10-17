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
