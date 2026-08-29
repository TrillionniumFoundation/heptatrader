# PROD GO-LIVE CHECKLIST

上线前必须逐项勾选。任一 P1 未满足即禁止上线。

## A. 门禁与版本

- [ ] 当前分支 CI 通过（`.github/workflows/ci-gate.yml`）。
- [ ] 本地 `gate-local.ps1` 最近一次 `EXIT_CODE=0`。
- [ ] 目标提交已打 tag/可追溯。

## B. 配置与环境

- [ ] profile 与环境匹配（paper/live/sim）。
- [ ] 关键配置已双人复核（账号、端口、风控阈值）。
- [ ] IB Gateway/TWS 网络与端口联通正常。

## C. 可观测性与告警

- [ ] 最新 `summary.json` 存在且 `nextValidIdCount >= 1`。
- [ ] 最新 `alerts.json` 无 P1 告警。
- [ ] 近 7 天错误码趋势已检查并记录。

## D. 运行手册与应急

- [ ] 值守人已熟悉 `RUNBOOK-STARTUP`。
- [ ] 值守人已演练 `RUNBOOK-INCIDENT` 关键场景。
- [ ] 已确认暂停策略/只读/平仓等应急动作入口。

## E. 最终放行

- [ ] 负责人签字放行。
- [ ] 上线窗口、回退窗口已确认。
- [ ] 上线后 30 分钟重点观察并记录。

## F. System low-latency governance (Theme #5)

- [ ] Run dry-run host audit: `powershell -ExecutionPolicy Bypass -File scripts/optimize_ib_host_latency.ps1`
- [ ] If changes needed, run explicit apply mode and record mask/host: `... -Mode Apply -AffinityMaskHex 0x0000000F`
- [ ] Verify power + turbo/core-park guidance via: `powercfg /Q SCHEME_CURRENT SUB_PROCESSOR`
- [ ] Verify NIC low-latency baseline on active wired NIC(s)
- [ ] Verify IB colocation: `powershell -ExecutionPolicy Bypass -File scripts/check_ib_colocation.ps1 -IbHost 127.0.0.1 -Port 4002`
- [ ] Release gate includes `SYSTEM_LOW_LATENCY` + `IB_COLOCATION` pass in `release_check.json`
- [ ] 1-minute trading regression completed after host tuning
