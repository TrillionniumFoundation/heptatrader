# HeptaTrader 开发执行基线（trnm-autopilot-workflow）

> 目的：统一 HeptaTrader 生产化改造阶段的工程实践，作为“开发文档对齐与实施基线”。
> 适用范围：`D:\quant\HeptaTrader-master` 全仓，含 C++ 主工程、脚本与运维门禁。

---

## 0. 文档对齐结论（现状）

已对齐并引用以下现有文档：

- `README.md`（分支现状、目录结构、构建方式）
- `docs/PRODUCTION-READINESS-PLAN.md`（P0/P1/P2 生产化路线）
- `docs/BUILD-HYGIENE.md`（构建污染治理、最小发布门禁）
- `scripts/README-PRECOMMIT.md`（提交前 secrets 检查）
- `scripts/README-SECURITY-CHECK.md`（凭据/环境变量/配置检查）
- `scripts/README-IB-HEALTHCHECK.md`（IB 健康检查与 release gate）

识别到的主要缺口：

1. 缺少统一的“编码规范 + Git 流程 + 测试门禁 + 发布门禁 + 回滚策略”单一基线文档。
2. 分支/提交规范在 README 中仅有历史说明（`master`/`develop_6_5_1`），缺乏可执行约束。

本文件用于补齐该缺口。

---

## 1) 编码规范（Coding Standard Baseline）

### 1.1 通用原则

- **安全优先**：禁止提交真实交易凭据（UserID/PassWord/AuthCode 等）。
- **最小改动面**：优先小步提交，避免单次混入功能+重构+格式化大杂烩。
- **可审计性**：交易链路关键节点必须可追踪（与 `PRODUCTION-READINESS-PLAN` 的审计模型一致）。

### 1.2 C++ 代码规范（最低要求）

- 新增/修改代码保持与所在文件现有风格一致（缩进、命名、括号风格）。
- 禁止在交易回调中执行长耗时阻塞逻辑；耗时计算下沉线程或异步任务。
- 明确错误处理分支：外部接口调用失败必须有日志与错误码语义。
- 对风险/下单相关分支，要求“**显式原因**”日志（建议 `RISK_XXX` 风格）。

### 1.3 脚本规范（PowerShell/Python）

- 脚本参数需提供默认值与帮助可读性（已有脚本保持该实践）。
- 返回码语义明确：`0=PASS`，非0=FAIL；禁止“失败但返回0”。
- 输出必须包含机器可读结果（json/txt）与归档路径，便于 CI 采集。

---

## 2) 分支与提交规范（Branch / Commit Baseline）

### 2.1 分支策略

- `master`：生产稳定分支，仅允许通过门禁后的发布合并。
- `develop_6_5_1`：集成与验证分支，承接生产化改造日常开发。
- 功能分支命名（建议）：
  - `feat/<module>-<topic>`
  - `fix/<module>-<topic>`
  - `chore/<topic>`
  - `docs/<topic>`

### 2.2 合并策略

- 禁止直接在 `master` 提交功能代码。
- 合并到 `master` 前，至少满足：
  1. secrets 检查通过；
  2. release_check 通过（`OVERALL=PASS`）；
  3. 关键变更有回滚说明。

### 2.3 Commit 规范

- 提交信息建议遵循：
  - `feat(module): ...`
  - `fix(module): ...`
  - `chore(ci): ...`
  - `docs(prod): ...`
- 单次提交应可独立解释，且可回退（避免“顺手修一堆”）。

---

## 3) 测试门禁（Test Gates）

### 3.1 提交前门禁（本地）

1. 安装 pre-commit hook（一次性）：
   ```powershell
   pwsh -File .\scripts\install_precommit_hook.ps1
   ```
2. 执行 secrets 检查（或由 hook 自动触发）：
   ```powershell
   pwsh -File .\scripts\hepta_secrets_check.ps1
   ```
3. 构建卫生检查：确保 `git status --short` 无大规模二进制污染。

### 3.2 变更验证门禁（建议最小集）

- 涉及 IB 交易链路时必须执行：
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\scripts\ib_gateway_healthcheck.ps1 -ProjectRoot D:\quant\HeptaTrader-master
  ```
- 涉及发布候选时必须执行：
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\scripts\release_check.ps1 -ProjectRoot D:\quant\HeptaTrader-master
  ```

### 3.3 门禁判定

- `release_check.ps1` 输出 `OVERALL=PASS` 才允许进入发布流程。
- 失败项必须附修复记录，至少覆盖：`RISK_CONFIG / IB_HEALTHCHECK / IB_REGRESSION_ROUND`。

---

## 4) 发布门禁（Release Gates）

发布前必须全部满足：

1. **配置门禁**：`IBServer/IBRisk` 核心字段完整、阈值合理（由 `release_check.ps1` 校验）。
2. **连通门禁**：IB 连接、`nextValidId`、`USD/CNH tick` 检查通过。
3. **回归门禁**：下单生命周期回归（place->status->cancel->final）通过。
4. **日志门禁**：关键日志中无致命模式（如 preflight fail、placeOrder fail、1100/1101/1102 等连接错误未收敛）。
5. **安全门禁**：仓库无明文凭据泄露。

建议发布产物归档目录：`runtime-logs/release-check-<timestamp>/`。

---

## 5) 回滚策略（Rollback Baseline）

### 5.1 触发条件

以下任一成立应立即触发回滚或熔断：

- 订单状态不可追踪或出现重复下单风险；
- 风控规则失效（关键 `IBRisk` 保护未生效）；
- 网关不稳定导致连续关键错误（1100/1101/1102 等）；
- 发布后核心门禁脚本在同环境复跑失败。

### 5.2 回滚步骤（最小闭环）

1. **交易止血**：暂停策略/启用 kill switch（禁止新单）。
2. **版本回退**：切回上一个已验证通过的 commit/tag。
3. **配置回退**：恢复上一个通过门禁的配置快照。
4. **验证复测**：执行 `release_check.ps1`，确保 `OVERALL=PASS`。
5. **恢复运行**：先 paper/canary，再恢复生产。

### 5.3 回滚证据

- 必须保存回滚前后门禁报告（json/txt）与关键日志归档。
- 事件复盘需记录“触发条件-处理动作-验证结果-改进项”。

---

## 6) 执行建议（下阶段）

结合 `docs/PRODUCTION-READINESS-PLAN.md`，优先推进：

1. 将本基线固化为 PR 模板 checklist；
2. CI 中强制接入 `hepta_secrets_check.ps1` + `release_check.ps1`；
3. 优先落地 P0 项（订单日志恢复、硬风控、配置单一真源、审计事件模型）；
4. 增加 7-day paper soak 出场标准并文档化。

---

## 7) 快速命令清单

```powershell
# 安装 pre-commit
pwsh -File .\scripts\install_precommit_hook.ps1

# secrets 检查
pwsh -File .\scripts\hepta_secrets_check.ps1

# IB 健康检查
powershell -ExecutionPolicy Bypass -File .\scripts\ib_gateway_healthcheck.ps1 -ProjectRoot D:\quant\HeptaTrader-master

# 发布总门禁
powershell -ExecutionPolicy Bypass -File .\scripts\release_check.ps1 -ProjectRoot D:\quant\HeptaTrader-master
```
