# HeptaTrader Agent OS plugin

该插件把本地 HeptaTrader Tool Gateway 暴露为 MCP 工具。插件不包含 broker credential、账户密钥、PAPER/LIVE 授权或本地执行权。

仓库不再携带 OS runtime 的发布安装/认证流水线。开发时先从源码构建 `hepta-tool-gatewayd`、`hepta-sessionctl` 和相关运行时，再由运维环境把固定 launcher 路径写入 MCP 配置。仓库内 `.mcp.json` 的 `/usr/libexec/hepta-agent-mcp-launcher` 只是部署约定，不代表源码构建会安装该文件。

每个互不信任的 Agent 必须使用独立 OS 身份、socket、session token 和 trust-domain 配置。Agent 只可调用当前会话暴露的工具；不得推断 PAPER 或 LIVE 权限。最终订单授权、风控、幂等、对账和 kill switch 始终由 Gateway/Execution Service 的确定性代码执行。

源码开发入口：

```bash
cmake -S . -B build -DBUILD_TESTING=ON -DHEPTA_ENABLE_IBAPI=OFF
cmake --build build --target hepta_tool_gatewayd hepta_sessionctl --parallel 2
python3 adapters/mcp/hepta_mcp_server.py
```

生产部署应从固定 commit 在外部发布流程中完成；插件目录本身不是授权或发布证明。
