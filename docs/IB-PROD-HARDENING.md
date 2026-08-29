# IB Production Hardening (Task A)

Date: 2026-02-28
Scope: `HeptaTrade/adapter_ib/*` and `HeptaTrade/HeptaDemoStrategyTrader.cpp`

## 1) Patch set delivered

### A. Clearer preflight failure codes (machine-readable)
Files:
- `HeptaTrade/adapter_ib/ib_gateway_adapter.h`
- `HeptaTrade/adapter_ib/ib_gateway_adapter.cpp`
- `HeptaTrade/HeptaDemoStrategyTrader.cpp`

Changes:
- Added `RunPreflightChecksDetailed(std::string& reasonCode, std::string& detail)` to return explicit risk code + detail.
- Kept legacy `RunPreflightChecks(std::string& reason)` as compatibility wrapper.
- Upgraded preflight checks to return explicit codes such as:
  - `RISK_IB_API_NULL`
  - `RISK_NOT_CONNECTED`
  - `RISK_NEXT_VALID_ID_NOT_READY`
  - `RISK_ACCOUNT_NOT_CONFIGURED`
  - `RISK_ACCOUNT_NOT_WHITELISTED`
  - `RISK_LIVE_NOT_AUTHORIZED`
  - `RISK_LIVE_KILL_SWITCH_ON`
  - config-invalid codes for max qty/daily/dev/dup fields.
- `HeptaDemoStrategyTrader` now logs and journals startup preflight using explicit code + detail (instead of only free text).

### B. Order-gate safety tightened in adapter order path
File:
- `HeptaTrade/adapter_ib/ib_gateway_adapter.cpp`

Changes:
- `PlaceOrder(...)` now re-runs preflight on each order attempt (`RunPreflightChecksDetailed`) before risk engine evaluation.
- Added hard input validation before submit:
  - Reject if contract is incomplete (`RISK_CONTRACT_INVALID`)
  - Reject if qty is non-finite / <= 0 (`RISK_QTY_INVALID`)
  - Reject invalid LMT price (`RISK_PRICE_INVALID`)
- If IB API `PlaceOrder` returns false, adapter now sets `m_lastRejectReason = "IB_API_PLACE_REJECTED"` for deterministic diagnostics.

### C. Better failure observability and distinct exits
Files:
- `HeptaTrade/adapter_ib/ib_gateway_adapter.cpp`
- `HeptaTrade/HeptaDemoStrategyTrader.cpp`

Changes:
- Added observability events when circuit breaker trips (`risk.circuit_breaker`) with explicit reason:
  - `RISK_IB_ERROR_BLACKLIST`
  - `RISK_IB_ERROR_FUSE`
- Added explicit IB exit constants in trader and used them in IB startup/order-loop paths:
  - `-10` connect fail
  - `-11` readOnly/order-gate blocked
  - `-12` preflight failed
  - `-13` cancel failed
  - `-14` live not authorized
  - `-15` live kill switch ON
  - `-23` **new** IB test place rejected (no longer conflated with preflight `-12`)

---

## 2) Minimal regression commands

Run from project root: `D:\quant\HeptaTrader-master`

### A. IB healthcheck
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\ib_gateway_healthcheck.ps1
```

### B. Offline Tool Gateway execution regression
```powershell
python .\scripts\strategy_iterate_paper.py --build-dir .\build-agent-os-ci
```

PowerShell wrapper:
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_ib_regression_round.ps1 -BuildDir .\build-agent-os-ci
```

The retired direct-IB scripts are permanent fail-closed shims. Their original
implementations remain under `compat/unsafe-direct-broker/` for source archaeology
only and are not a PAPER certification or supported execution path.

---

## 3) Build result (Release|x64)

Command:
```powershell
D:\VSstudio\MSBuild\Current\Bin\amd64\MSBuild.exe D:\quant\HeptaTrader-master\HeptaTrader.sln /t:Build /p:Configuration=Release /p:Platform=x64 /m
```

Result:
- **Success**
- 0 errors
- 9 warnings (existing `C4244` narrowing warnings in `adapter_ib/ib_gateway_adapter.cpp`; no new build blockers)

---

## 4) Remaining risks / follow-ups

1. **Warning debt**: existing `C4244` conversions in adapter latency math remain; should be cleaned to avoid hidden overflow/precision loss.
2. **Preflight timeouts**: current preflight validates connectivity/nextValidId but not "data freshness" SLA (e.g., stale account/position snapshots).
3. **Strategy-side guardrails**: strategy intents still rely on adapter-side risk checks; consider additional per-strategy hard caps in `ib_fx_multi_strategy` for defense in depth.
4. **End-to-end CI**: add automated assertion that explicit risk codes appear in logs/journal for known reject scenarios.
