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

## Mandatory checks before rollout
Run the new test_laya_evaluator_idle_futility.py and existing test_promotion_reachability.py with PYTHONPATH=research/neuron_learning:research. Also rerun archive tamper regressions and the true source feature-parity smoke for any newly enrolled fast-read profile.

Current frozen evaluation_runtime_sha256 pins the original release. Never modify that release in-place: it can trigger EVALUATOR_SOURCE_UNPINNED_OR_CHANGED. After the old trial closes, use a new separately pinned release and newly registered future trial.

## Explicitly still open
- Changes are GitHub branch-only and not deployed to Pocket4.
- Remote test execution and hosted CI were not available during this session; new fixture tests are NOT claimed to have passed.
- PnL observer incremental caching, Value 60-second original deadline coverage and account NAV completeness remain separate tasks; no order authority was changed.
