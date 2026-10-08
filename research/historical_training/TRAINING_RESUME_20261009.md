# Controlled training resumption — 2026-10-09

Research delivery only. No actor selection, production model promotion, trading authority or change to the frozen 24-slot campaign is claimed. Private evidence is retained on pocket4 under `~/.local/state/heptatrader/operator-evidence/training-execution-20261009T0154/`.

## P0: evidence, repair and actual resumption

The previous-boot kernel recorded GLOBAL OOM at 2026-10-08 21:52:30 and 21:53:15 UTC+08, killing Laya evaluator workers. Per-service memory caps had not bounded concurrent trainer/evaluator UMA allocation. The existing shared heavy-worker lease is retained; this delivery does not claim to have invented that earlier containment.

A 61-sample preflight found healthy memory, zero memory PSI and no new `oom_kill`, but zero valid admissions. The original source-health process refreshes about every 60 seconds whereas admission requires the actual source quote to be no older than five seconds. Cached source freshness cannot meet that contract reliably.

`resident_live_source.refresh` retains the original coverage decision, rule, period and cached decision time. It separately verifies an append-only extension of an ORIGINAL source prefix, using the original installed hash-chain reader, then checks the actual quote's original observed/known timestamps. It does not write or retimestamp source status, issue a broker request, widen TTL or turn continuity probes into efficacy samples.

The first implementation's live check selected an old `segment-recovery-live` by lexical order. That failure is retained. The corrected selector considers only registered, not-ended segments; file mtime is only a search hint, never freshness evidence. Source symlinks, private ownership/mode failures, changed prefixes, corrupt chains, noncausal quotes, stale continuity and stale real quotes all fail closed.

Final narrow guard regression: 44 tests. Live corrected preflight: 31 samples, 29 with only the operator Hold remaining, two additional genuine quote-staleness refusals; all 31 have original-chain proofs. Two competing lease descriptors verified mutual exclusion. The original Hold was atomically preserved as `released-original-manual-hold.json` after an actual fresh admission and idle-supervisor check. It was not deleted to manufacture a pass.

Immutable guard deployment: `e845c83a41c36c045a366574b8d04a76e393812912a6afa0fd40aa1ad7148d40`. Only the two idle research supervisor unit paths were replaced. Original configuration, weights, source runtime, risk permissions and campaign rules were retained.

The evaluator naturally closed the two previously expired pending plans; old outcomes and windows were not replayed. The trainer then produced completed candidate number 25, `e44ca7d816aa884b821f2ff25718f56f24a980c387fbf15cf214ecba1225c902`. The trainer's recorded cgroup peak was 4,791,975,936 bytes. Current-boot OOM count remained zero. This is a bounded resumption measurement, not immunity to arbitrary unrelated host/UMA load.

## P1: future evidence remains prospective

The original finite campaign, candidate file SHA256 `538dd502589cf4ddad56c0dc2c13b7f75f5da402f2d53f08983b09fc6a377466`, and plan SHA256 `a7a09d12c8b05f8a79d2469a2c622d66f3ebdf26f5fe49c3596afdc0ce0b887a` are unchanged. Both original observed slots were independently reconstructed, including comparator scores. There are four finalized slots: two observed and two censored; twenty future slots remain at the recorded audit. No closed slot was retried or shifted.

The new resident candidate also has an original time-bound plan and a genuine subsequent observation. Neither the new resident observation nor healthy input probes replace the campaign's old missing outcomes. Statistical and canonical-owner acceptance remain open.

## P1: current100k monetary learning, not policy-gradient credit

`current100k_value_trial.py` replays the original importer before and after fitting and verifies every original row through `to_row`. It freezes the original eleven-row dataset SHA256 `0fefdd5cdc1ba83e4a6a9282b92b581e7ac20d0f6281f2a8fb1e5c2d10ee290d`. Extra subsequently closed rows are not silently imported. The twenty-one unbound episodes remain excluded.

The real training job uses the same heavy-worker gate and irreversible Landlock/seccomp sandbox. The complete pretrained mmBERT/Laya backbone is frozen, strictly loaded, hash-checked before/after, and encoded one row at a time. There is no random replacement, network access, bearer access or model download. Only a new 769-parameter monetary readout receives sixteen bounded updates on actual net USD targets divided by a declared fixed USD100 scale. Targets outside the bound are rejected, not clipped.

Produced value candidate: `daba1892e8a7d92de7ee8d1fa32bf6cc7a717bc32986dc4942fa10649bebce7b`. The eleven training rewards total USD -359.857989. Training-only Huber changed 0.2100911587 → 0.1923157424. Maximum torch GPU allocation was 1,603,796,992 bytes; measured run duration 20.431 seconds. The approximately constant readout and eleven-row fit do NOT prove useful out-of-sample value prediction.

Actor, scorer and pretrained parameters remain unchanged. These are remote-behavior-bound off-policy value targets, not Laya on-policy policy gradients. The standalone candidate has no automatic selection or live handoff. Independent replay after the observed native restart found all original training rows still valid; the current consumer then contained fifteen rows, with twenty-one unbound episodes still excluded.

## P2: actual archived market training and explicit boundaries

`neuron256_market_trial.py` uses the previously verified 1,024-row matched historical bundle and complete frozen mmBERT raw vectors; it performs no additional encoder forward passes. Original numeric inputs, text order, raw-vector receipt, source plan and all hashes must match. The original test population remains absent and is denied by the worker sandbox.

The declared chronological, full-input/outcome-disjoint roles are 512 expert-fit rows, 128 graph-fit rows, 128 economic-calibration rows and 256 development-check rows. Two seven-class expert readouts are fitted on the first role and frozen before 64 graph updates. The 256-dimensional graph input/output contract is retained. The archived adapter matches the unchanged live soft graph kernel numerically, but does not emit live packets or backdate availability. Its target is explicitly archived one-bar lead / three-bar holding, not the live five-minute target. The original frozen encoder text asked a three-class question; fitting a seven-class readout on its retained features is declared as a different archived readout task.

Frozen numeric, semantic, equal-mixture, training-prior and uniform references are reported. The graph has 128 extra fitting labels beyond the frozen experts, so comparison alone is not equal-total-label-budget causal evidence of a fusion advantage. Previously inspected development periods are not advertised as unseen prospective tests.

Historical economic diagnostics use original bid/ask side prices, USD100k reference notional, 0.4 bp commission, 0.5 bp slippage, a fixed 0.5 bp predicted-net buffer, nonoverlapping positions and a fixed-trade extra-1-bp cost stress. Economic calibration needs at least 30 trades, positive net, and maximum drawdown at most USD250; failure yields an ABSTAIN admitted policy. Historical modeled costs are not broker execution rewards. No monetary label from the eleven-row value trial is mixed into the graph experiment.

The first actual market-gradient run failed at checkpoint serialization because the cost matrix was noncontiguous. Its logs and output directory are retained. The sole retry code change packs checkpoint tensors contiguously without changing their values; data, seed, role assignments, optimizer steps, objectives and economic rules are unchanged. The serialization regression is included in 22 archive tests. A gradient run with no valid checkpoint is not a completed candidate.

At the 2026-10-09 02:33–02:38 UTC+08 audit, the corrected retry was denied BEFORE model loading by actual source continuity 19/24. No gate is lowered to finish the retry. Consult the final private receipt for subsequent admitted execution, if any; this document does not preclaim a checkpoint.

## Independent runtime incident and remaining acceptance

Execution exited at 2026-10-09 02:23:18 UTC+08 with exit code 9 after logging `IB_POST_FILL_RISK_RECONCILIATION_FAILED`, then systemd restarted it at 02:23:48. This was not SIGKILL or a recorded new OOM. Execution PID/restart identity changed; Gateway and both PAPER-driver PIDs were unchanged at the preservation check. The training operations did not request an Execution restart, but this delivery must not be described as zero-interference or complete native financial recovery.

The subsequent quote gap degraded the real source continuity score and correctly stopped new heavy-work admissions. The native owner must separately close post-fill risk reconciliation and its strict valuation/recovery evidence. Do not bypass that boundary through training or claim an account-wide profit figure from the training-label subtotal.

All 1,448 preexisting pinned research evidence files matched at the preservation check (the original Hold is verified at its preserved new path). No immutable source, old candidate, rejected qualification, censored window or frozen campaign configuration was rewritten.

Full corrected-source validation: Core 1,512 tests with no skips; Source 106 tests; standalone existing Neuron256 63 tests. The earlier system-Python core run had six optional-torch skips and correctly returned nonacceptance; it is retained, not presented as full acceptance. Use the pinned runtime with CPU-only GPU visibility for the complete test partition.

## Operator follow-through

Keep resident shared admission enabled and the original future campaign calendar intact. Only admit the unchanged serialization retry after genuine original-source recovery and memory/lease checks. Do not loop over new seeds or optimization settings to convert development results into efficacy. Value/graph candidate activation still requires separately bound prospective evidence, valid current reward provenance and the real canonical owner's acceptance. No IQL/CQL/SPIBB actor training, five-expert learned-risk rollout or production model selection is claimed here.
