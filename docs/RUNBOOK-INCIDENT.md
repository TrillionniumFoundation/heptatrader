# RUNBOOK - INCIDENT

适用范围：连接失败、下单异常、门禁失败等生产前/准生产事件。

## 1. 事故分级

- **SEV-1 / P1**：无法连接、下单连续拒绝、状态不一致。
- **SEV-2 / P2**：行情缺失、错误数异常上升。
- **SEV-3 / P3**：偶发错误且可自愈。

## 2. 通用处置顺序

1. **先止损**：暂停策略下单/切换只读。
2. **再取证**：固定证据目录（`runtime-logs/*`）。
3. **后恢复**：按分类修复并验证。

## 3. 场景化步骤

### A) 无法建立 IB 会话（NO_NEXT_VALID_ID）

1. 检查 TWS/Gateway 是否在线与端口配置。
2. 运行：
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\test-ib-ports.ps1
   ```
3. 重新执行回归轮次并汇总日志。
4. 若仍失败，升级 SEV-1 并禁止进入 go-live。

### B) 下单拒绝（error code 201/相关）

1. 检查账户权限、合约参数、风控阈值。
2. 复核 `check_ib_order_whitelist.py` 输出。
3. 在 paper 账户复现并确认问题闭环后再恢复。

### C) CI Gate 失败

1. 查看 `runtime-logs/ci-gate-*/ci_gate_summary.txt`。
2. 按失败项修复（Whitelist / Regression / Release Check）。
3. 必须重跑至 `EXIT_CODE=0`。

## 4. 复盘输出（必填）

- 事件编号、时间线、影响范围
- 根因（直接/系统性）
- 修复动作与回归证据
- 预防措施（脚本/文档/流程）
