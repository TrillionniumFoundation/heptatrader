# HeptaTrader

HeptaTrader 是一个面向 Agent/AI 的**确定性交易执行运行时**。它把策略推理与订单权限分离：Agent 只能提交受约束的查询或交易意图；Tool Gateway 负责身份、会话和能力校验；Execution Service 是唯一可以持久化、风控、路由、撤单和对账的订单 authority。

当前仓库重点维护 Simulator 与受控 IB PAPER 路径。它不是一个已经认证的 LIVE 多券商产品，也不会因为存在 adapter 目录就宣称对应 venue 可用。

## 当前能力边界

| 能力 | 状态 | 说明 |
|---|---|---|
| Agent / MCP / native client | Implemented | 仅通过受认证 Unix socket 调用 Gateway；不持有 Broker 凭据 |
| Tool Gateway / session fencing | Implemented | peer identity、token、capability、schema、lease 和审计边界 |
| Execution / OMS / reconciliation | Implemented | journal-before-send、幂等、uncertain recovery、authoritative snapshot |
| Deterministic Simulator | Implemented | 本地开发、回放、故障注入和核心回归 |
| IB PAPER | Conditional | 代码路径存在；必须通过外部 SDK 构建和受控自托管资格认证 |
| IB LIVE | Unsupported | 无已认证 LIVE 发布路径 |
| CTP | Unsupported | 公开树没有授权且完整的 vendor transport；adapter fail-closed |
| XT / QMT | Unsupported | 仅保留事件语义与研究文档；所有 outbound 操作 fail-closed |
| Legacy monolith | Deprecated / Off | 默认不构建，不能作为生产执行旁路 |

完整矩阵见 [docs/CAPABILITY-MATRIX.md](docs/CAPABILITY-MATRIX.md)。

## 架构

```text
Agent / MCP / CLI
        |
        v
Tool Gateway ── peer identity / session / capability / schema
        |
        v
Execution Service ── risk / journal / idempotency / fencing / reconcile
        |
        +── Deterministic Simulator
        +── IB PAPER (only after controlled qualification)
```

不可破坏的核心规则：

1. 只有 Execution Service 可以向 venue 发送订单。
2. Agent 和 Gateway 不持有 Broker credential，也不能直接连接 Broker API。
3. mutation 必须在外部发送前进入耐久 journal。
4. retry 必须复用原 command ID；不确定结果先对账，不能盲目重发。
5. 风控、行情、持仓、权限、配置或持久化状态不确定时 fail closed。
6. CTP/XT/QMT 未完成 transport 不得伪造 connected、accepted、submitted 或 cancelled。

## 开发与验证

Linux 开发机需要 CMake、C++ 编译器、Python 3 和 OpenSSL development headers。执行：

```bash
./scripts/dev_core.sh
```

该命令会依次运行仓库契约检查、Python 安全测试、Release 核心构建、全部 `core` CTest、安装树验证和 SPDX SBOM 生成。默认使用 IB-disabled 构建，不需要也不会下载任何 Broker SDK。

手工构建：

```bash
cmake -S . -B build -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_TESTING=ON \
  -DHEPTA_ENABLE_IBAPI=OFF \
  -DHEPTA_ENABLE_HARDENING=ON
cmake --build build --parallel 2
ctest --test-dir build --output-on-failure -L core
```

Sanitizer 通过 `HEPTA_ENABLE_ASAN_UBSAN=ON` 或 `HEPTA_ENABLE_TSAN=ON` 启用；二者不能同时开启。GitHub Actions 对 GCC/Clang、Debug/Release、ASan/UBSan、安装包与 SBOM 执行门禁，夜间单独运行 TSan。

## 安装与发布

```bash
cmake --install build --prefix /tmp/heptatrader-stage/usr
python3 scripts/verify_install_tree.py --root /tmp/heptatrader-stage/usr
cpack --config build/CPackConfig.cmake -B dist
```

正式 release 只允许由 `v<VERSION>` tag 触发。流程会重新构建、运行测试、验证安装树、生成 hash manifest、SPDX SBOM、SHA256SUMS 和 GitHub build provenance。详细步骤见 [docs/RELEASE-PROCESS.md](docs/RELEASE-PROCESS.md) 与 [docs/SUPPLY-CHAIN.md](docs/SUPPLY-CHAIN.md)。

核心发布包故意排除 IB SDK、CTP vendor payload、Broker credential 和任何 LIVE 授权。IB PAPER 资格认证只能在标记为 `heptatrader-ib-paper` 的受控自托管 runner 上由人工触发，并依赖仓库外受审查 harness；未产生同一提交的通过证据时，IB PAPER 状态仍为 Conditional。

## 运维入口

- 启动与安装检查：[docs/RUNBOOK-STARTUP.md](docs/RUNBOOK-STARTUP.md)
- kill switch：[docs/RUNBOOK-KILLSWITCH.md](docs/RUNBOOK-KILLSWITCH.md)
- 事故处置：[docs/RUNBOOK-INCIDENT.md](docs/RUNBOOK-INCIDENT.md)
- 可观测性：[docs/OBSERVABILITY-METRICS.md](docs/OBSERVABILITY-METRICS.md)
- 上线门禁：[docs/PROD-GO-LIVE-CHECKLIST.md](docs/PROD-GO-LIVE-CHECKLIST.md)

## 安全与许可

不要把账号、密码、token、授权文件或真实账户配置提交到仓库。生产凭据必须通过 systemd credentials 或等价的受控 secret store 注入。安全边界见 [SECURITY-HARDENING.md](SECURITY-HARDENING.md)。

本仓库未授予开源使用许可；适用条款见 [LICENSE](LICENSE)。第三方组件与 vendor SDK 边界见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
