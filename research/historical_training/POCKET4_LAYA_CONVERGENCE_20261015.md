# Pocket4 Laya training convergence — 2026-10-15

Research-only evidence and rollout gates; not PAPER strategy activation.

## Pocket4 observation (2026-10-15 10:39 UTC)
- Resident trainer: 36 completed, 1 failed jobs; no active heavy training worker at the observation.
- Resident evaluator: 35 rejected and one pending plans, with no automatic promotion. The pending 90-slot trial already had 58 censored outcomes (38 no fixed-slot prediction; 20 process failure), whereas the original rule requires 88 observed and at most 2 censored.
- Incremental current100k consumption: 260 net-value rows. The latest Laya candidate says pnl_reward_used=false; only 8 direct attributable Laya PnL rewards existed. Rows must not be presented as Laya policy gradients.
- Source readiness separately showed 24/24 usable probes. Failed prediction processes reported INPUT_SLOT_EXPIRED:DURING_QUOTE_READ and CURRENT_SHADOW_INPUT_NOT_FRESH. These errors do not by themselves demonstrate upstream quote absence.

## Work in this branch
- Preserve every frozen plan, original slot receipt and preregistered statistical budget; never issue a premature PASS/FAIL verdict.
- After validated irreversible censoring makes a frozen trial impossible to qualify, stop NEW GPU launches but continue fixed-roster bookkeeping and evaluate already-started predictions.
- Do not run an expensive history source scan in an evaluator tick with no mature prediction. The status now explicitly reports whether a scan took place.
- The existing mainline source policy already has the SHA-pinned prospective-only recent quote reader. Do NOT replace the original read_quotes for frozen plans or silently enroll their original predictions.
- Mainline source validation had made the new strict (< expiry) native quote intake rule retroactively reject genuine historical rows produced by the older original collector (<= expiry). Restrict historical equality compatibility to exact source collector SHA256 b35f3d118283e453973d654d0b1562b59cf74ae9e7a87a0e32d8141fb19372d6; new collector/native reply remains strict, and any genuinely late legacy row still fails.

## Mandatory checks before rollout
Verified on ROG and an independent, non-active Pocket4 QA copy at /home/alex/.local/share/heptatrader/laya-p0-qa-20261015 (PYTHONPATH=research/neuron_learning:research):
- ROG historical_training tests matching test_laya_*.py: 86/86 PASS; prior statically frozen rule reachability tests: 15/15 PASS.
- Pocket4 staged historical_training tests matching test_laya_*.py: 86/86 PASS; test_promotion_reachability.py: 15/15 PASS. No live unit was replaced.
- Three boundary regression cases: old exact-expiry historical row accepted, new intake equality rejected, old 1 ms late row rejected. Those tests are included in the 86 Laya tests.
- Pocket4 true original-source parity smoke: 197 exact recent quote rows matched and model input/features were identical. Full original reader: 6.8096 seconds; existing opt-in recent reader: 0.6709 seconds. Read-only audit receipt SHA256 89858c847d40d58328ceb9518f6d28aa248b6b85f66304a21115c76ef572a3a3, stored at operator-evidence/pocket4-laya-p0-20261015/source-parity-r1.json; reported broker_orders_sent=0. This is one source-as-of observation, not a guaranteed performance gain or profitability proof.
- Main branch reconciled into the isolation worktree after these changes; rerun affected tests if upstream changes again before merging.

Current frozen evaluation_runtime_sha256 pins the original release. Never modify that release in-place: it can trigger EVALUATOR_SOURCE_UNPINNED_OR_CHANGED. After the old trial closes, use a new separately pinned release and newly registered future trial.

## Explicitly still open
- Candidate source is in a GitHub branch and a separate Pocket4 QA directory. It is NOT loaded by the production-resident trainer/evaluator or original trial.
- ROG and Pocket4 isolated tests passed as listed above. Hosted CI, full-service admission and new prospective qualification remain pending; a passing isolated fixture is not a live deployment or economic-efficacy result.
- PnL observer incremental caching, Value 60-second original deadline coverage and account NAV completeness remain separate tasks; no order authority was changed.
