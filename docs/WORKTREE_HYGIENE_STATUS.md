# Worktree Hygiene Status

Generated: 2026-03-03 (Asia/Shanghai)
Repo: `D:\quant\HeptaTrader-master`

## Summary

- Dirty tree confirmed.
- Critical hygiene issue: tracked runtime/build binaries under `Interface/CTPTradeApi32/` are currently deleted in worktree.
- `docs/SPLIT_COMMIT_PLAN.md` refreshed to match current file set.
- Patchsets regenerated under `docs/patchsets/` for the planned IB batches.

## Tracked modifications/deletions (current)

```text
 M HeptaTrade/HeptaDemoStrategyTrader.cpp
 M HeptaTrade/HeptaTrader.vcxproj
 M HeptaTrade/HeptaTraderConfig.xml.example
 M HeptaTrade/HeptaTrader_Linux.vcxproj
 M HeptaTrade/adapter_ib/ib_api_wrapper.cpp
 M HeptaTrade/adapter_ib/ib_api_wrapper.h
MM HeptaTrade/adapter_ib/ib_gateway_adapter.cpp
MM HeptaTrade/adapter_ib/ib_gateway_adapter.h
 M HeptaTrade/ib_fx_multi_strategy.cpp
 M HeptaTrade/ib_fx_multi_strategy.h
 M HeptaTrade/oms_journal.cpp
 M HeptaTrade/oms_journal.h
 M HeptaTrade/order_watchdog.cpp
 D Interface/CTPTradeApi32/thostmduserapi_se.dll
 D Interface/CTPTradeApi32/thostmduserapi_se.lib
 D Interface/CTPTradeApi32/thosttraderapi_se.dll
 D Interface/CTPTradeApi32/thosttraderapi_se.lib
 M docs/BUILD-HYGIENE.md
 M docs/CI-GATE.md
A  docs/IB-PROD-HARDENING.md
 D pic/simnow_screenshot.png
 D pic/simnow_screenshot1.png
 M scripts/ci_gate.ps1
 M scripts/ci_gate_release.ps1
 M scripts/run_ib_regression_round.ps1
```

## Untracked files (current)

```text
?? HeptaTrade/HeptaTraderConfig.paper.xml
?? HeptaTrade/adapter_ctp/ctp_gateway_adapter.cpp
?? HeptaTrade/adapter_ctp/ctp_gateway_adapter.h
?? HeptaTrade/adapter_xt/xt_gateway_adapter.cpp
?? HeptaTrade/adapter_xt/xt_gateway_adapter.h
?? docs/COMMIT-PLAN-IB-CTP.md
?? docs/GATE-STABILITY-REPORT.md
?? docs/IB-ADV-SCHEDULER-P2-ROLLOUT.md
?? docs/IB-P2-ADV-FLAGS.md
?? docs/IB_OBS_HYGIENE_REPORT_2026-03-03.md
?? docs/PHASE-FREEZE-IB-CTP.md
?? docs/QMT-BRIDGE-MVP.md
?? docs/QMT-SDK-REVIEW.md
?? docs/SPLIT_COMMIT_PLAN.md
?? docs/WORKTREE_HYGIENE_STATUS.md
?? docs/XT-CUTOVER-CHECKLIST.md
?? docs/XT-HEPTA-MAPPING.md
?? docs/XTQMT-VENUE-PLAN.md
?? docs/patchsets/P0-core.patch
?? docs/patchsets/P1-perf.patch
?? docs/patchsets/P2-advanced-flagged.patch
?? docs/patchsets/account-summary-reconnect.patch
?? hepta.bat
?? scripts/SCRIPTS_UNIFIED.md
?? scripts/build_release_ib.ps1
?? scripts/check_pythonw_version.py
?? scripts/check_reconcile_critical_block.ps1
?? scripts/hepta.ps1
?? scripts/hepta_env_profiles.ps1
?? scripts/ib_adv_scheduler_stress_30m.env
?? scripts/ib_regression_10m_report_template.md
?? scripts/monitor_ib_session.ps1
?? scripts/run_ib_adv_scheduler_stress_30m.ps1
?? scripts/run_ib_regression_10m.ps1
?? scripts/run_xt_scaffold_smoke.ps1
?? scripts/set_hepta_env.ps1
?? scripts/set_strategy_profile.ps1
?? scripts/stage_commit_batch.ps1
?? scripts/start_heptatrader_with_profile.ps1
?? scripts/start_ib_paper_oneclick.ps1
?? scripts/xt_first_live_order.py
?? scripts/xt_first_live_order_sim.py
?? scripts/xt_first_order_stage.ps1
?? scripts/xt_pretrade_final_check.ps1
?? scripts/xtdata_history_check.py
?? scripts/xtdata_history_check_auto.py
?? scripts/xtdata_history_check_sim.py
?? scripts/xtdata_history_check_single_real.py
```

## Safe cleanup actions (exact commands)

### A) Restore required runtime/build assets (recommended mandatory)

```bash
git restore -- Interface/CTPTradeApi32/thostmduserapi_se.dll Interface/CTPTradeApi32/thostmduserapi_se.lib Interface/CTPTradeApi32/thosttraderapi_se.dll Interface/CTPTradeApi32/thosttraderapi_se.lib
```

### B) Decide screenshot deletions explicitly

Restore if accidental:

```bash
git restore -- pic/simnow_screenshot.png pic/simnow_screenshot1.png
```

Or commit intentional removal in a dedicated docs/assets commit.

### C) Drop only local helper/untracked files you do not want to commit

Preview first:

```bash
git clean -nd -- HeptaTrade/HeptaTraderConfig.paper.xml hepta.bat scripts/SCRIPTS_UNIFIED.md scripts/build_release_ib.ps1 scripts/check_pythonw_version.py scripts/check_reconcile_critical_block.ps1 scripts/hepta.ps1 scripts/hepta_env_profiles.ps1 scripts/ib_regression_10m_report_template.md scripts/run_ib_regression_10m.ps1 scripts/set_hepta_env.ps1 scripts/set_strategy_profile.ps1 scripts/stage_commit_batch.ps1 scripts/start_heptatrader_with_profile.ps1 scripts/start_ib_paper_oneclick.ps1
```

Delete after preview:

```bash
git clean -fd -- HeptaTrade/HeptaTraderConfig.paper.xml hepta.bat scripts/SCRIPTS_UNIFIED.md scripts/build_release_ib.ps1 scripts/check_pythonw_version.py scripts/check_reconcile_critical_block.ps1 scripts/hepta.ps1 scripts/hepta_env_profiles.ps1 scripts/ib_regression_10m_report_template.md scripts/run_ib_regression_10m.ps1 scripts/set_hepta_env.ps1 scripts/set_strategy_profile.ps1 scripts/stage_commit_batch.ps1 scripts/start_heptatrader_with_profile.ps1 scripts/start_ib_paper_oneclick.ps1
```

## Commit sequencing

Use `docs/SPLIT_COMMIT_PLAN.md` sequence:
1. Restore `Interface/CTPTradeApi32/*` deleted binaries.
2. Commit `P0 core`.
3. Commit `account-summary/reconnect`.
4. Commit `P1 build/CI`.
5. Commit `P2 advanced (flagged)`.
6. Optionally commit XT/CTP scaffold batch.

## Patchsets regenerated

- `docs/patchsets/P0-core.patch`
- `docs/patchsets/P1-perf.patch`
- `docs/patchsets/P2-advanced-flagged.patch`
- `docs/patchsets/account-summary-reconnect.patch`

