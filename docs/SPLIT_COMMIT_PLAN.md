# Split Commit Plan (validated on 2026-03-03)

This plan is refreshed against the current dirty tree in `D:\quant\HeptaTrader-master`.

## 0) Preflight hygiene (do this first)

Restore tracked runtime/build assets that are currently deleted (do **not** commit those deletions):

```bash
git restore -- Interface/CTPTradeApi32/thostmduserapi_se.dll Interface/CTPTradeApi32/thostmduserapi_se.lib Interface/CTPTradeApi32/thosttraderapi_se.dll Interface/CTPTradeApi32/thosttraderapi_se.lib
```

Optional: if screenshot removals were accidental, restore them too:

```bash
git restore -- pic/simnow_screenshot.png pic/simnow_screenshot1.png
```

## 1) P0 core (IB core logic)

**Scope (tracked modified):**
- HeptaTrade/HeptaDemoStrategyTrader.cpp
- HeptaTrade/HeptaTraderConfig.xml.example
- HeptaTrade/ib_fx_multi_strategy.cpp
- HeptaTrade/ib_fx_multi_strategy.h
- HeptaTrade/oms_journal.cpp
- HeptaTrade/oms_journal.h
- HeptaTrade/order_watchdog.cpp
- HeptaTrade/adapter_ib/ib_api_wrapper.cpp
- HeptaTrade/adapter_ib/ib_api_wrapper.h

**Stage command**
```bash
git add -- HeptaTrade/HeptaDemoStrategyTrader.cpp HeptaTrade/HeptaTraderConfig.xml.example HeptaTrade/ib_fx_multi_strategy.cpp HeptaTrade/ib_fx_multi_strategy.h HeptaTrade/oms_journal.cpp HeptaTrade/oms_journal.h HeptaTrade/order_watchdog.cpp HeptaTrade/adapter_ib/ib_api_wrapper.cpp HeptaTrade/adapter_ib/ib_api_wrapper.h
```

**Commit message**
```text
feat(ib-core): harden strategy/order flow and API wrapper integration
```

## 2) P1 build/CI hygiene

**Scope (tracked modified):**
- HeptaTrade/HeptaTrader.vcxproj
- HeptaTrade/HeptaTrader_Linux.vcxproj
- docs/BUILD-HYGIENE.md
- docs/CI-GATE.md
- scripts/ci_gate.ps1
- scripts/ci_gate_release.ps1
- scripts/run_ib_regression_round.ps1

**Stage command**
```bash
git add -- HeptaTrade/HeptaTrader.vcxproj HeptaTrade/HeptaTrader_Linux.vcxproj docs/BUILD-HYGIENE.md docs/CI-GATE.md scripts/ci_gate.ps1 scripts/ci_gate_release.ps1 scripts/run_ib_regression_round.ps1
```

**Commit message**
```text
perf(ci): streamline build/ci gate and regression workflow
```

## 3) P2 advanced scheduler (flagged)

**Scope (untracked new):**
- docs/IB-ADV-SCHEDULER-P2-ROLLOUT.md
- docs/IB-P2-ADV-FLAGS.md
- scripts/ib_adv_scheduler_stress_30m.env
- scripts/run_ib_adv_scheduler_stress_30m.ps1
- scripts/monitor_ib_session.ps1

**Stage command**
```bash
git add -- docs/IB-ADV-SCHEDULER-P2-ROLLOUT.md docs/IB-P2-ADV-FLAGS.md scripts/ib_adv_scheduler_stress_30m.env scripts/run_ib_adv_scheduler_stress_30m.ps1 scripts/monitor_ib_session.ps1
```

**Commit message**
```text
feat(ib-p2): add advanced scheduler rollout docs and stress tooling (flagged)
```

## 4) Account-summary/reconnect hardening

**Scope (tracked, partially staged):**
- HeptaTrade/adapter_ib/ib_gateway_adapter.cpp
- HeptaTrade/adapter_ib/ib_gateway_adapter.h
- docs/IB-PROD-HARDENING.md

**Stage command**
```bash
git add -- HeptaTrade/adapter_ib/ib_gateway_adapter.cpp HeptaTrade/adapter_ib/ib_gateway_adapter.h docs/IB-PROD-HARDENING.md
```

**Commit message**
```text
fix(ib-gateway): stabilize account summary and reconnect handling
```

## 5) XT/CTP scaffold + rollout docs (recommended separate batch)

**Scope (untracked new):**
- HeptaTrade/adapter_ctp/ctp_gateway_adapter.cpp
- HeptaTrade/adapter_ctp/ctp_gateway_adapter.h
- HeptaTrade/adapter_xt/xt_gateway_adapter.cpp
- HeptaTrade/adapter_xt/xt_gateway_adapter.h
- docs/COMMIT-PLAN-IB-CTP.md
- docs/GATE-STABILITY-REPORT.md
- docs/IB_OBS_HYGIENE_REPORT_2026-03-03.md
- docs/PHASE-FREEZE-IB-CTP.md
- docs/QMT-BRIDGE-MVP.md
- docs/QMT-SDK-REVIEW.md
- docs/XT-CUTOVER-CHECKLIST.md
- docs/XT-HEPTA-MAPPING.md
- docs/XTQMT-VENUE-PLAN.md
- scripts/run_xt_scaffold_smoke.ps1
- scripts/xt_first_live_order.py
- scripts/xt_first_live_order_sim.py
- scripts/xt_first_order_stage.ps1
- scripts/xt_pretrade_final_check.ps1
- scripts/xtdata_history_check.py
- scripts/xtdata_history_check_auto.py
- scripts/xtdata_history_check_sim.py
- scripts/xtdata_history_check_single_real.py

**Stage command**
```bash
git add -- HeptaTrade/adapter_ctp/ctp_gateway_adapter.cpp HeptaTrade/adapter_ctp/ctp_gateway_adapter.h HeptaTrade/adapter_xt/xt_gateway_adapter.cpp HeptaTrade/adapter_xt/xt_gateway_adapter.h docs/COMMIT-PLAN-IB-CTP.md docs/GATE-STABILITY-REPORT.md docs/IB_OBS_HYGIENE_REPORT_2026-03-03.md docs/PHASE-FREEZE-IB-CTP.md docs/QMT-BRIDGE-MVP.md docs/QMT-SDK-REVIEW.md docs/XT-CUTOVER-CHECKLIST.md docs/XT-HEPTA-MAPPING.md docs/XTQMT-VENUE-PLAN.md scripts/run_xt_scaffold_smoke.ps1 scripts/xt_first_live_order.py scripts/xt_first_live_order_sim.py scripts/xt_first_order_stage.ps1 scripts/xt_pretrade_final_check.ps1 scripts/xtdata_history_check.py scripts/xtdata_history_check_auto.py scripts/xtdata_history_check_sim.py scripts/xtdata_history_check_single_real.py
```

**Commit message**
```text
feat(xt-ctp): add gateway scaffolds and venue rollout documentation
```

## 6) Local helper scripts/docs (optional; keep separate)

Likely local workflow helpers; commit only if intended:
- HeptaTrade/HeptaTraderConfig.paper.xml
- hepta.bat
- scripts/SCRIPTS_UNIFIED.md
- scripts/build_release_ib.ps1
- scripts/check_pythonw_version.py
- scripts/check_reconcile_critical_block.ps1
- scripts/hepta.ps1
- scripts/hepta_env_profiles.ps1
- scripts/ib_regression_10m_report_template.md
- scripts/run_ib_regression_10m.ps1
- scripts/set_hepta_env.ps1
- scripts/set_strategy_profile.ps1
- scripts/stage_commit_batch.ps1
- scripts/start_heptatrader_with_profile.ps1
- scripts/start_ib_paper_oneclick.ps1

## Recommended commit sequencing

```bash
# 0) restore required assets first
git restore -- Interface/CTPTradeApi32/thostmduserapi_se.dll Interface/CTPTradeApi32/thostmduserapi_se.lib Interface/CTPTradeApi32/thosttraderapi_se.dll Interface/CTPTradeApi32/thosttraderapi_se.lib

# 1) P0
git add -- HeptaTrade/HeptaDemoStrategyTrader.cpp HeptaTrade/HeptaTraderConfig.xml.example HeptaTrade/ib_fx_multi_strategy.cpp HeptaTrade/ib_fx_multi_strategy.h HeptaTrade/oms_journal.cpp HeptaTrade/oms_journal.h HeptaTrade/order_watchdog.cpp HeptaTrade/adapter_ib/ib_api_wrapper.cpp HeptaTrade/adapter_ib/ib_api_wrapper.h
git commit -m "feat(ib-core): harden strategy/order flow and API wrapper integration"

# 2) account-summary/reconnect
git add -- HeptaTrade/adapter_ib/ib_gateway_adapter.cpp HeptaTrade/adapter_ib/ib_gateway_adapter.h docs/IB-PROD-HARDENING.md
git commit -m "fix(ib-gateway): stabilize account summary and reconnect handling"

# 3) P1 build/CI
git add -- HeptaTrade/HeptaTrader.vcxproj HeptaTrade/HeptaTrader_Linux.vcxproj docs/BUILD-HYGIENE.md docs/CI-GATE.md scripts/ci_gate.ps1 scripts/ci_gate_release.ps1 scripts/run_ib_regression_round.ps1
git commit -m "perf(ci): streamline build/ci gate and regression workflow"

# 4) P2 flagged
git add -- docs/IB-ADV-SCHEDULER-P2-ROLLOUT.md docs/IB-P2-ADV-FLAGS.md scripts/ib_adv_scheduler_stress_30m.env scripts/run_ib_adv_scheduler_stress_30m.ps1 scripts/monitor_ib_session.ps1
git commit -m "feat(ib-p2): add advanced scheduler rollout docs and stress tooling (flagged)"

# 5) XT/CTP scaffold (optional batch)
# ...stage list from section 5...
# git commit -m "feat(xt-ctp): add gateway scaffolds and venue rollout documentation"
```

## Patch artifacts (regenerated for current HEAD working changes)

- docs/patchsets/P0-core.patch
- docs/patchsets/P1-perf.patch
- docs/patchsets/P2-advanced-flagged.patch
- docs/patchsets/account-summary-reconnect.patch

Generation basis:
- tracked changes: `git diff HEAD -- <files>`
- untracked files: `git diff --no-index` against an empty file (concatenated)
