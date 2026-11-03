# Current100k pre-open research observer

This reader follows original attribution REQUEST records and original verified
COMMAND_BINDING records. It has no order, actor, position-size or risk authority.
A new shadow study pins the active producer. Old trial populations and timestamps
remain unchanged.

## Evidence and recovery

The resident source reader reuses parsed append-only records, while revalidating
manifest pins, health, file identity and producer chains. Appended segments must
retain the exact previously verified prefix. A restart discards source caches.

An ordinary append overlapping capture retries at most three times, with 10 ms
pauses and a 150 ms monotonic retry budget capped by the earliest known original
request deadline. An in-flight synchronous read cannot be preempted; a request
that expires during it receives no inference. Exhausted capture attempts yield
PREOPEN_SOURCE_SNAPSHOT_BUSY and never return a stale or unverified snapshot. A busy
source retains an already verified warm resident while inference waits. Original
guard checks, manual holds, worker handoff, request expiry and the fixed input
window still apply. Hash, producer, append-only-prefix and health violations
retain the original failure/handoff behavior. None of these retries renews a
request, the study window or a resource lease.

Every eligible original request gets a durable private ledger input. Resource
holds and expired inputs remain explicit records. One attempt is allowed per
original identity. Interrupted attempts remain unknown and are never backfilled.

One model is preloaded and warmed inside the original heavy-worker lease. All
frozen heads share the two action features. The plan fixes backend, threads and
batch size. The predictor never reads future outcomes.

Prediction files, commit, publication and post-publication observation are
independently hashed. The ledger uses the later actual completion of the store
call for its conservative pre-OPEN check. Only exact original command identity
and a strictly earlier completed prediction qualify as PREOPEN_BOUND_RESEARCH.
A request cannot bind two commands. No production Value coverage is granted.

## Bounded wait for a past stale quote

The new research lifecycle uses policy
`EXISTING_WARM_PAST_QUOTE_HOLD_15S_CUMULATIVE_V1`. The original guard governs NEW
heavy-worker admission; its negative decision is not automatically an instruction
to destroy an existing child. This exception applies only to a model already
warmed while holding the same original heavy-worker lease.

A pause requires the exact single refusal `LATEST_SOURCE_QUOTE_NOT_FRESH` plus
that current guard call's complete original-source proof. The report and quote
clocks must be causal, the quote must be genuinely older than five seconds, and
the original continuity and verified prefix extension must remain bound. Future,
missing, rewritten, malformed, or unbound timestamps do not qualify. Every other
resource, source, manual hold, or drain refusal retains normal cleanup/handoff.
No guard source, external pin, TTL, encoder, head, or statistical rule changes.

While paused, inference readiness is false: there is no attempt, model forward,
or prediction seal. Normal source capture, original-command reconciliation, and
request expiry continue on every poll. A new successful complete guard call and
a current verified attribution snapshot are required to resume. Warmup is also
followed by a new guard check. Before each attempt, the original quote's five-second
TTL and guard interval are rechecked, including after long reads or queued work;
a bare positive decision without its complete proof cannot authorize inference.

The budget is **15 cumulative monotonic seconds per resident session**, equal to
three maximum five-second guard intervals. A fresh interval or a new stale episode
does not reset it. The original session bound, input cutoff, stop signal, fair
handoff request, and each nonrenewable request deadline still apply. Poll sleeps
are capped at the remaining pause budget. Synchronous guard/source calls and
cleanup cannot be forcibly preempted: actual elapsed time may exceed the budget
while such a call runs, but return and recovery paths check the bound before any
inference. A regression makes a guard call return at 16 seconds and verifies
cleanup with zero forwards. This is not a hard real-time interruption guarantee.

Private immutable pause entry/end records bind the same lease ID and the guard
proof, record accumulated time, and verify that no attempt occurred during the
pause. Session receipts expose the policy, count, duration, and resumptions.
Cleanup remains model/tensor destruction followed by original lease release and
its actual-release cooldown. Audit-write failure cannot bypass that cleanup.

This is a lifecycle repair for subsequent engineering collection. The three
already recorded missing-prediction commands, their terminals, and the seen-command
denominator remain unchanged. They cannot receive retroactive predictions or
credit. The fixed window's zero-unresolved gate is already blocked; subsequent
coverage does not turn that window into a successful efficacy verification.

## CLI configuration

Run the integrated release, including the weight-study and weight-shadow modules:

~~~bash
python scripts/run_current100k_preopen_research.py --config /private/config.json --sha256 CONFIG_SHA256
~~~

The config schema is heptatrader.current100k-preopen-research-config.v1.
trade_authority, automatic_selection and actor_update must all be false.

| Field | Contract |
|---|---|
| runtime_root | Absolute integrated source release |
| runtime_files_sha256 | Complete relative source-path to hash inventory |
| study_plan_path, study_plan_sha256 | Newly frozen study and file hash |
| source_registration | Exact active manifest path and sha256 |
| period_path, period_sha256 | Original current period |
| state_root | New private ledger directory |
| guard_config, guard_config_sha256 | Existing deployed guard wrapper and pin |
| encoder_runtime | Pinned original Current100k source release |
| encoder_runtime_files_sha256 | Relative source inventory, including model.py and feature-text core.py |
| encoder_base | Pinned original base directory |
| encoder_backend | Exact measured profile fixed by the study |
| encoder_threads | 1, 2 or 4 |
| encoder_batch_size | 1 or 2 |
| cpu_affinity | Explicit available CPU IDs, at least the thread count |
| warmup_request_path, warmup_request_sha256 | Pinned original development REQUEST |
| max_resident_seconds | At most 1800 |
| cooldown_seconds | At least 60, at most 900 |
| guard_check_seconds | At most 5 |
| poll_ms | 50 to 2000; default 250 |
| max_request_age_ms | Nonrenewable deadline, at most 60000 |
| max_requests | Finite ledger capacity, default 10000 |

Warmup must predate study registration and never becomes a prospective case.
The CLI imports the deployed guard in a private namespace and uses its original
rules and lease. The encoder similarly uses its original pinned loader namespace.
Use actual hashes and measured profile settings; placeholder profiles cannot run.

## Scheduling and operational proof

A durable lease reservation precedes model loading. A crash or unknown cleanup
retains the full reserved deadline plus the 60-second interval. Normal early
handoff writes a separate immutable close receipt after verified tensor cleanup
and lease release; its cooldown starts at the actual release time. Guard and source checks continue
between inference calls. A manual hold is checked every loop. Another worker's
lease-contention denial requests handoff only if it is from after this acquisition
and at most 15 seconds old; a stale admission file cannot repeatedly evict workers.
Model memory is released before the heavy lease.

Use a finite supervisor bounded by the new study window, with at least 60 seconds
between sessions and enough termination time for one bounded model forward.
The observer must not replace or stop the original driver. Requests across service
gaps remain in the original journal and receive explicit missing/expired records
on the next capture.

Warm residency removes repeated loading costs but does not establish timely
predictions. Historical cold-load latency and CPU benchmarks are diagnostic
evidence only. A new real request, durable prediction and exact original OPEN
must demonstrate the required order. CPU batch-2/4-thread measurement was slower
than the observed 5.609-second original OPEN; no CPU timeliness claim is made.

Tests cover identity, receipt tampering, strict timing, restart, interrupted
attempts, monotonic deadlines, command rebinding, resource denial, cooldown,
handoff, independent-import parity, source health, append-only prefixes,
truncation, inode replacement, deletion, partial writes and manifest changes.

## Explicit HIP FP32 profile

For the GPU path set encoder_device to hip and add hip_config_path plus its actual
hip_config_sha256. Hash the actual old config; its file name is not a hash witness.
The config must retain its Python, base, period, source inventory and accepted HIP
manifest. The new shadow plan separately pins that same HIP manifest.

The CLI verifies that runtime and reexecutes its configured Python with the
accepted dynamic-library environment when needed, before importing Torch.
Only after the original guard grants the lease does the encoder activate the
accepted SDK, verify loaded libraries and load the original model in FP32.
Its profile is LAYA_HIP_FP32_SINGLE_ROW_MAX2048_NO_TRUNCATION_V1, or BATCH2.
Both retain the full original text and 2048-token bound. There is no autocast,
backward pass or weight update. All heads share CPU FP32 execution. GPU allocation
is capped at four GiB. Features carry an actual BEFORE_PUBLISH library proof.

Cleanup destroys model tensors and synchronizes this process's work. If supported,
it clears this process's BLAS handle workspaces before emptying the allocator cache.
Allocated bytes must still return to the pre-model baseline. Each phase's byte count
is retained in the close receipt. Failed cleanup never releases the heavy lease.
