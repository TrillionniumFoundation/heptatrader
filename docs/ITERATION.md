# Iteration contract

Status: current
Applies to: `scripts/dev_core.sh`, `tests/`, `.github/workflows/core-ci.yml`, `.github/workflows/canonical-full-suite.yml`
Verification: `canonical-full-suite` on the exact revision

HeptaTrader 的普通开发循环只保护会造成交易错误、权限越界或配置漂移的核心 invariant；它不是发布认证流水线。

## 本地入口

```bash
./scripts/dev_core.sh
```

默认行为：

1. Release、IB-disabled、legacy-disabled 配置；
2. 检查 repository/documentation、schema/catalog 和 module ownership 契约；
3. 构建 `hepta_core_test_binaries`；
4. 运行 `core` CTest；
5. 运行 `tests/python` 中的轻量 contract tests。

可覆盖：

```bash
HEPTA_BUILD_TYPE=Debug HEPTA_JOBS=4 ./scripts/dev_core.sh
```

## CMake presets

```bash
cmake --preset core-release
cmake --build --preset core-release
ctest --preset core-release
```

## PR gates

`.github/workflows/core-ci.yml` 在 pull request 和 main push 上运行快速的
`dev_core.sh` feedback。`.github/workflows/canonical-full-suite.yml` 是最终
审查候选的永久、只读证据门禁：它在同一 revision 上再运行安装 allowlist
以及 ASAN/UBSAN、crash/replay、malformed-protocol 和 performance fixtures。
两者都不恢复 round、soak、bundle、manifest、evidence closure、VM 或宿主认证森林。

## 必须留在快速门禁中的契约

- OMS journal durability 与 execution idempotency；
- Gateway/Execution 权限边界；
- typed framing、peer/session/capability validation；
- authoritative snapshot、event relay 与 reconciliation；
- simulator E2E；
- IB order lifecycle 与 PAPER kill switch 的 IB-off contract tests；
- 配置来源冲突、profile lock 与 template rejection。

## 独立的慢速/外部 lane

IB SDK 编译、真实 PAPER smoke、sanitizer、目标宿主 systemd/network 测试可由 nightly 或手动 workflow 执行。它们不得阻塞每次策略编辑，但正式 PAPER/LIVE 变更必须有相应外部验证结果。
