# IB Gateway 可观测性与一键健康检查

## 1) 整理日志（run-gateway.log / ib_connect_trace.log）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\summarize_ib_logs.ps1 -ProjectRoot D:\quant\HeptaTrader-master
```

输出：
- `runtime-logs\ib-log-summary-<timestamp>\summary.md`
- 同目录归档 `run-gateway.log` / `ib_connect_trace.log`

## 2) 一键健康检查（连接 + nextValidId + USD/CNH tick）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\ib_gateway_healthcheck.ps1 -ProjectRoot D:\quant\HeptaTrader-master
```

检查项：
- CONNECTIVITY：是否出现 `IB_CONNECTED` / `IB connect returned: true`
- NEXT_VALID_ID：是否出现 `nextValidId=<number>` 或 `IB_NEXTVALIDID_OK`
- USD_CNH_TICK：是否检测到 `USD/CASH/IDEALPRO/CNH` 订阅且有 `tickPrice`

结果：
- 控制台逐项输出 `PASS/FAIL`
- 最终输出 `OVERALL: PASS/FAIL`
- 归档路径：`runtime-logs\ib-healthcheck-<timestamp>`
  - `summary.json`
  - `summary.txt`
  - `run-gateway.log` / `ib_connect_trace.log`（如存在）

退出码：
- `0` = PASS
- `1` = FAIL

## 3) 仅复盘现有日志（不启动进程）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\ib_gateway_healthcheck.ps1 -ProjectRoot D:\quant\HeptaTrader-master -NoLaunch
```

## 4) Release gate (single PASS/FAIL + release advice)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\release_check.ps1 -ProjectRoot D:\quant\HeptaTrader-master -IbHost 127.0.0.1 -Port 4002
```

Outputs:
- Console: `OVERALL=PASS/FAIL` and `RELEASE_ADVICE=...`
- Archive: `runtime-logs\release-check-<timestamp>`
  - `release_check.json` (machine-readable)
  - `release_check.txt` (human-readable / CI grep)
  - archived stdout/stderr from sub-check scripts

Optional flags:
- `-ConfigPath <path>`: validate a specific config file for `<IBServer>/<IBRisk>`
- `-NoLaunch`: pass-through to healthcheck for log replay only
- `-SkipHealthcheck` / `-SkipRegression`: partial checks for debugging
