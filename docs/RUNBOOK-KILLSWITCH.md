# PAPER kill switch

## 当前语义

Canonical IB PAPER daemon 使用只读文件系统 kill switch：

- control directory 由 runtime 配置提供；当前 fixed service 使用 `/run/hepta/ib-paper-control`。
- marker 名称固定为 `kill-switch`。
- **合法 marker 存在**：`Engaged`，阻断新增风险。
- **marker 稳定缺失**：`Disarmed`，仍需通过其余风控才能新增风险。
- **目录、marker、inode、权限、owner、link count 或 I/O 状态不确定**：`Uncertain`，与 engaged 一样阻断新增风险。

Execution service 只读取状态，不能自行解除 kill switch。Agent、MCP adapter、Tool Gateway 和 venue adapter 均不得修改 control directory。

## 文件系统约束

生产读取器要求：

- service 以非 root 身份运行；
- control directory 为 root owner、service group、`0750`、稳定目录 identity；
- marker 为 root owner、同一 service group、`0440`、single-link regular file；
- symlink、hard-link、目录替换、跨设备 marker 或 observation 期间发生变化均视为 uncertain；
- marker 缺失必须经过二次确认，目录 identity 变化会被永久 latch 为 uncertain，直到进程重启并重新建立安全边界。

## 操作原则

### Engage

由受控的 root/operator 路径原子创建合法 marker，并在允许新增风险前确认 Execution 已观察到 engaged。发生事故时，先 engage，再处理撤单、减仓和对账。

### Disarm

仓库不提供自动 disarm、one-shot operator 或 campaign 脚本。解除必须由部署侧的受控 operator 完成，并至少确认：

1. broker/Execution session identity 正确；
2. authoritative position 与 active orders 已完成 reconciliation；
3. 风险限额、账户和 instrument 配置正确；
4. control directory/marker 操作是原子的；
5. 任何不确定结果都回到 engaged。

不要让 Agent 通过 shell、工具参数或环境变量解除 canonical PAPER kill switch。

## 退出路径

Kill switch 的目标是阻断风险增加，不应无条件阻断安全退出。cancel、reduce-only 或 authoritative flatten 仍必须满足 owner、fencing、订单状态和 venue 约束。

## Legacy 说明

`HEPTA_GLOBAL_KILL_SWITCH` 和 `HEPTA_FLATTEN_ONLY` 仅属于 legacy monolith 路径，不是 `hepta-ib-executiond` 的 canonical PAPER 控制面。新代码不得依赖这些环境变量绕过文件系统 kill switch。

## 核心验证

```bash
cmake --build build/core --target hepta_ib_paper_kill_switch_tests
ctest --test-dir build/core --output-on-failure -R hepta_ib_paper_kill_switch_tests
```
