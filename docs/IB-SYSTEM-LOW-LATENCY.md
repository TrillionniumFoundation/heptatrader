# IB System Low-Latency Operational Checklist

Objective: make host-side low-latency tuning repeatable, auditable, and release-gated for IB Gateway + Hepta strategy colocation.

## 1) Dry-run audit (default, no mutation)
```powershell
powershell -ExecutionPolicy Bypass -File scripts/optimize_ib_host_latency.ps1
# equivalent explicit form:
# powershell -ExecutionPolicy Bypass -File scripts/optimize_ib_host_latency.ps1 -Mode DryRun
```
Output: `runtime-logs/host_latency_tuning_report.json`

## 2) Apply tuning (explicit opt-in)
```powershell
powershell -ExecutionPolicy Bypass -File scripts/optimize_ib_host_latency.ps1 -Mode Apply -AffinityMaskHex 0x0000000F
# or keep compatibility:
# powershell -ExecutionPolicy Bypass -File scripts/optimize_ib_host_latency.ps1 -Apply -AffinityMaskHex 0x0000000F
```
Applied actions:
- Power plan -> High performance (when available)
- NIC: attempt to disable Interrupt Moderation / EEE / Green Ethernet / Flow Control where supported
- Process affinity + High priority for `ibgateway` and `HeptaDemoStrategyTrader`

## 3) Power/turbo/core-park governance guidance
Dry-run output now records processor power settings from current scheme:
- Processor min/max state (AC/DC)
- Core parking min/max cores (AC/DC)

Recommended execution-host baseline (validate thermals first):
- AC `ProcessorMin=100`, `ProcessorMax=100`
- AC `CoreParkingMin=100`, `CoreParkingMax=100`
- Keep turbo enabled unless benchmarked evidence shows lower jitter when disabling

Repeatable checks:
```powershell
powercfg -GetActiveScheme
powercfg /Q SCHEME_CURRENT SUB_PROCESSOR
```

## 4) IB Gateway + strategy colocation checks
```powershell
powershell -ExecutionPolicy Bypass -File scripts/check_ib_colocation.ps1 -IbHost 127.0.0.1 -Port 4002
```
Output: `runtime-logs/ib_colocation_check.json`

Pass intent:
- IB host is loopback (`127.0.0.1`/`localhost`)
- `ibgateway` process exists locally
- `HeptaDemoStrategyTrader` process exists locally

## 5) Verification commands (repeatable)
```powershell
powercfg -GetActiveScheme
powercfg /Q SCHEME_CURRENT SUB_PROCESSOR
Get-Process -Name ibgateway,HeptaDemoStrategyTrader | Select Name,Id,PriorityClass,ProcessorAffinity
Get-NetAdapter | ? Status -eq Up | Select Name,Status,LinkSpeed
powershell -ExecutionPolicy Bypass -File scripts/check_ib_colocation.ps1 -IbHost 127.0.0.1 -Port 4002
```

## 6) Regression after host tuning
```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_ib_regression_10m.ps1 -DurationMinutes 1 -RunMode trading
```

## 7) Release checklist integration
Release gate includes:
- `SYSTEM_LOW_LATENCY` (dry-run baseline + NIC/power/process + colocation record)
- `IB_COLOCATION` (standalone colocation pass/fail)

```powershell
powershell -ExecutionPolicy Bypass -File scripts/release_check.ps1 -Profile paper -IbHost 127.0.0.1 -Port 4002
```

Pass criteria:
- `runtime-logs/host_latency_tuning_report.json` generated
- `runtime-logs/ib_colocation_check.json` generated
- Active NIC discovered
- No blocking failures in release gates
