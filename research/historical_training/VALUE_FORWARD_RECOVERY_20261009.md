# Value forward runtime recovery — 2026-10-09

The original `d20801c` Value predictor failed on its first naturally flat future
request at 13:27:40.880 UTC+8. Six actual timer invocations failed with
`RESEARCH_ROLE_REQUIRED` before model loading or shared-lease acquisition:
`current100k_forward_worker` requested `value-predictor`, while `Gate` accepted
only trainer/evaluator. After the request expired, the next WAIT status hid the
failure and no persistent request failure existed. Original logs, request,
configuration and source hashes are preserved separately. No late prediction
may be produced for this request.

The guard now admits this explicit research role using the same lease path,
resource requirements, source freshness and manual Hold. The worker keeps
immutable held/failed attempts while a fresh request can be retried, persists
expired requests without loading a model, and includes retained failure counts
in WAIT status. Exception frames, including chained exceptions, are cleared
before model collection/CUDA cleanup and lease release. This covers the observed
interface failure and a separately reproduced exception-lifetime defect.

## Frozen protocol and prospective continuation

Original trial 2 plan `4ab35a51baf9b8aba6fb5252b09c29cfcad60d8d6ed92a59ed0cd379e14973b3`
pins the entire worker inventory. Existing code has no authenticated runtime
amendment entry point. Its plan, signature, candidate, data, old runtime,
calendar, failed request and alpha must not be rewritten. Its original
evaluator continues; until the fixed terminal date its state remains PENDING.

Recovery uses a separate trial 3 registry and a Value-only immutable release.
Only worker/guard runtime bytes change. It retains the same frozen 30-row
candidate/head/data, behavior, baselines and statistical rule; no refit or actor
update. Registration must independently verify and retain original trials 1 AND
2, use their maximum trial plus one, and start genuinely new inputs five minutes
after actual registration. Trial 3 alpha is 0.05/(3*4), with family allocation
0.0375 through trials 1–3. The missed trial 2 request cannot be replayed.

The new guard configuration must retain the exact original shared-lock path and
inode, source-health path, Hold path, original Laya configuration, period and
resource rule. Original Laya runtime/config/pins stay intact. Keep the old trial
2 evaluator/calendar and evidence; only stop the broken predictor timer when
idle and activate the dedicated prospective trial 3 timers. No trading service,
broker credential, order or policy cutover is part of this repair.

## Independent evidence before recovery

At 13:27:33.472 UTC+8, Laya trial 29 had 3 OBSERVED, 0 CENSORED, one sealed
forecast awaiting its outcome and 82 future inputs. Candidate/parent mean Brier
was 1.0005575918/1.0006243281, improvement 0.0000667363. Three non-tie wins gave
descriptive paired sign p=0.125 versus alpha=0.0000574712644. Candidate hit 0/3,
bootstrap hit 3/3, all targets class 3. This is not a mature qualification.
All predictions, causal input receipts, six runtime pins and 97 source prefixes
matched. Historical gaps remain retained; these three actual windows meet the
frozen 30-second path and 5-second endpoint rules. All 1,502 old Laya evaluation
files and 28 rejections match; prior alpha is retained.

At 13:28:49.930, Value trial 2 had nine original requests (eight non-flat, one
flat), zero plan-bound OPENs, zero seals and zero scored outcomes. The 13:25:52
execution was a CLOSE of the 12:55:41 pre-registration position. Its settled net
USD 23.606501 is excluded from prospective Value efficacy. All frozen 30 training
rows replayed exactly, with original fees and settlement; their historical net
USD -640.942681 is a training-label total, not the new head's performance.
Both future benchmark MAE improvements are null and log-e=0. Trial 1 remains
REVOKED with five censored cases; no old alpha or failed subject is removed.

Neuron256 prior-anchored residual mixing remains a design hypothesis with no
fixed rho/candidate or signed seven-bin future protocol. The existing graph has
already seen all graph-fit labels; a later subset is not an unseen inner split.
The inspected development population and three-bin campaign cannot replace a
new seven-bin future test. Original negative calibration/check economic
diagnostics and ABSTAIN remain intact. No Neuron256 rerun is needed for this
runtime repair.

Focused regressions use synthetic fixtures and are not market-effect evidence.
Deployment receipts and fresh actual readbacks belong in the PR discussion and
private operator evidence; a planned continuation is not a completed validation.
