# Hepta Long-Term Engineering Governance

## Objectives
- Keep release behavior reproducible across machines and sessions.
- Maintain safe-by-default trading controls.
- Prevent configuration drift between runners/scripts.
- Keep operational diagnostics actionable (not noisy, not blind).

## Mandatory Release Gates
1. Build gate
   - `Release|x64` IB build must pass.
2. Regression gate
   - Run 10m `safe` and 10m `trading` via `scripts/run_ib_regression_10m.ps1`.
   - Classify result using `GateRecommendation`.
3. Snapshot gate
   - Export User env snapshot using `scripts/export_ib_user_env.ps1`.
4. Workspace gate
   - `git status --short` must be empty before release tag.

## Governance Audit (weekly or before release)
Run:
```powershell
powershell -ExecutionPolicy Bypass -File scripts/governance_audit.ps1 -ProjectRoot D:\quant\HeptaTrader-master -ExportUserEnv
```

Audit tracks:
- script hardcoded path hits
- `catch(...)` footprint in `HeptaTrade`
- UTF-8 text check pass/fail
- latest 10m regression statuses
- env snapshot presence

## Coding Rules
- Avoid new hardcoded absolute paths; prefer `$PSScriptRoot` or derived root.
- Prefer typed exceptions; reserve `catch(...)` for top-level crash guards only.
- Keep critical IB defaults explicit:
  - `HEPTA_IB_MARKET_DATA_TYPE=3` (delayed stream fallback for paper/safe regression)
  - safe mode should not enable live by default.
- Any new runner script must read key runtime parameters from User env first.

## Operational Rules
### 10m regression outcome matrix (`scripts/run_ib_regression_10m.ps1`)
- `StrictTimeout=true`:
  - all checks pass + timeout -> `PASS_WITH_TIMEOUT`
  - all checks pass + no timeout -> `PASS`
- `StrictTimeout=false` (default):
  - all checks pass + timeout -> `PASS`
  - all checks pass + no timeout -> `PASS`
- any failed checks -> `FAIL` / `FAIL_TIMEOUT` / `FAIL_CONNECT_STALL` (block)

### Gate handling
- `PASS`: release gate green.
- `PASS_WITH_TIMEOUT`: warning; keep rationale in release notes.
- `FAIL_CONNECT_STALL`: investigate IB/TWS/Gateway availability before code changes.

Always archive:
- commit hash
- env snapshot path
- 10m reports (safe + trading)

## Owner Checklist
- [ ] Build passed
- [ ] 10m safe done
- [ ] 10m trading done
- [ ] env snapshot exported
- [ ] governance audit report generated
- [ ] git clean before tag

## Daily Low-Latency Governance Routine

Run once per day (or before opening session):

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_latency_governance_daily.ps1 -ProjectRoot D:\quant\HeptaTrader-master
```

Optional apply mode (requires operator confirmation policy):

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_latency_governance_daily.ps1 -ProjectRoot D:\quant\HeptaTrader-master -ApplySystemTuning
```

This routine executes:
1) host latency tuning audit/apply (`optimize_ib_host_latency.ps1`)
2) IB colocation check (`check_ib_colocation.ps1`)
3) release gate core system checks (`release_check.ps1` with system gates)
