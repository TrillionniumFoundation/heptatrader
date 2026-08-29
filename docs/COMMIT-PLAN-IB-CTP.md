# COMMIT PLAN (IB/CTP phase)

Repository: `D:\quant\HeptaTrader-master`

## Commit A — IB hardening
**Message**: `feat(ib): harden preflight/order gate and improve failure diagnostics`

**Files**
- `HeptaTrade/adapter_ib/ib_gateway_adapter.h`
- `HeptaTrade/adapter_ib/ib_gateway_adapter.cpp`
- `docs/IB-PROD-HARDENING.md`

## Commit B — CTP adapter scaffold
**Message**: `feat(ctp): add adapter scaffold and wire initial runtime hook`

**Files**
- `HeptaTrade/adapter_ctp/ctp_gateway_adapter.h`
- `HeptaTrade/adapter_ctp/ctp_gateway_adapter.cpp`
- `HeptaTrade/HeptaDemoStrategyTrader.cpp`
- `HeptaTrade/HeptaTrader.vcxproj`
- `HeptaTrade/HeptaTrader_Linux.vcxproj`
- `HeptaTrade/HeptaTraderConfig.xml.example`
- `HeptaTrade/HeptaTraderConfig.paper.xml`

## Commit C — Gate/release scripts
**Message**: `chore(gate): stabilize ib+ctp release gate and reconcile critical check`

**Files**
- `scripts/ci_gate.ps1`
- `scripts/ci_gate_release.ps1`
- `scripts/run_ib_regression_round.ps1`
- `scripts/check_reconcile_critical_block.ps1`
- `docs/CI-GATE.md`

## Commit D — Docs & misc cleanup
**Message**: `docs: phase freeze and integration notes; cleanup obsolete assets`

**Files**
- `docs/QMT-BRIDGE-MVP.md`
- `docs/XTQMT-VENUE-PLAN.md`
- `docs/PHASE-FREEZE-IB-CTP.md`
- `docs/GATE-STABILITY-REPORT.md`
- `pic/simnow_screenshot.png` (delete)
- `pic/simnow_screenshot1.png` (delete)
- `HeptaTrade/adapter_xt/` (keep as reserved scaffold)

## Order / dependency
1. A (IB hardening)
2. B (CTP scaffold)
3. C (gate scripts)
4. D (docs + cleanup)

## Validation after B+C
- Build: `MSBuild HeptaTrader.sln /p:Configuration=Release /p:Platform=x64`
- Gate: `powershell -ExecutionPolicy Bypass -File .\scripts\ci_gate_release.ps1 -ProjectRoot "D:\quant\HeptaTrader-master"`
- Expected: `RELEASE_POLICY=PASS`, scope `IB+CTP_ONLY (XT excluded)`.
