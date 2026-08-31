# HeptaTrader Agent OS plugin

该插件把本地 HeptaTrader Tool Gateway 暴露为 MCP 工具。插件不包含 Broker credential、账户密钥、PAPER/LIVE 授权或本地执行权。

默认源码构建和 CMake install 会提供 `/usr/libexec/hepta-agent-mcp-launcher` 与 `/usr/libexec/hepta-mcp-server`。launcher 校验 Agent UID/GID、trust-domain 配置、bridge 文件 owner/mode/link，并使用净化环境启动。每个不互信 Agent 必须使用独立 OS identity、socket 和 session token。

开发入口：

```bash
./scripts/dev_core.sh
```

部署时先验证 release manifest/SBOM，再按 `docs/RUNBOOK-STARTUP.md` 创建身份、credential 和 session。Agent 只能调用当前 session catalog 中的能力；最终订单授权、风控、幂等、journal、对账和 kill switch 始终由 Gateway/Execution 的确定性代码执行。插件目录或 MCP discovery 结果本身不是 Broker 权限证明。
