# Production and go-live checklist

本项目当前没有已认证 IB LIVE、CTP、XT 或 QMT 自动执行能力。本文的 “go-live” 先指 **Simulator 部署或 IB PAPER 受控运行**；任何真实资金 LIVE 必须另行完成治理和资格认证。

## Repository and artifact

- [ ] 当前 commit 的 CI 全绿：repository contracts、GCC/Clang、Debug/Release、ASan/UBSan、package。
- [ ] release tag 与 `VERSION` 一致。
- [ ] SHA256SUMS、安装 manifest、SPDX SBOM、build provenance 验证通过。
- [ ] 安装树无 symlink、无 group/world writable artifact，systemd 引用全部存在。
- [ ] capability matrix 没有把 Conditional/Unsupported 描述为 Implemented。

## Host and identity

- [ ] Agent、Gateway、Simulator Execution、IB PAPER Execution 使用隔离的非 root UID/GID。
- [ ] socket owner/mode、trust domain、session token 与 execution context binding 一致。
- [ ] credential 为单链接私有 regular file，不在 env、日志、仓库或命令行泄漏。
- [ ] systemd sandbox 和 Broker egress policy 已在目标主机验证。
- [ ] 时间同步、磁盘空间、journal durability 和日志保留策略满足运行要求。

## Trading safety

- [ ] kill switch engage/disarm/uncertain 行为通过目标主机验证。
- [ ] 风控覆盖 NaN/Inf、数量上限、每日订单、账户 allowlist、price collar、陈旧报价、flatten-only 不穿零。
- [ ] command ID 幂等、重复 callback、部分成交、cancel race、断线重连和 outcome uncertain 有测试/证据。
- [ ] authoritative positions、open orders、fills 与本地 projection 完成 reconciliation。
- [ ] safe exit 路径（cancel、reduce-only、authoritative flatten）经演练。

## IB PAPER additional gate

- [ ] 使用授权且 pin 住的 IB C++ API 构建成功。
- [ ] 手工触发 `IB PAPER Qualification` workflow，并由受控自托管 runner 产出同一 commit 的证据。
- [ ] read-only 轮次通过后，才允许人工批准 bounded-mutations 轮次。
- [ ] 断线、1100/1101/1102、201 拒单、行情陈旧、重复 status、部分成交和重启恢复均有预期结果。
- [ ] 无 unresolved P1 alert。

## Explicit NO-GO

任一 checkbox 未完成，或证据来源/commit 不明确，即 NO-GO。CTP、XT/QMT 和 IB LIVE 当前始终 NO-GO，除非未来提交同时修改实现、测试、资格认证和 capability matrix，并通过独立安全评审。
