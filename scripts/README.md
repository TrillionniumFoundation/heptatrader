# Runtime and research scripts

`scripts/` 只保留直接服务于核心 OS 的小型入口。

## 开发

- `dev_core.sh`：配置、构建并运行核心测试。
- `resolve_hepta_config.py`：解析运行配置。
- `validate_sim_data.py`：校验 simulator 数据。
- `verify_oms_journal_replay.py`：校验 OMS journal replay。

## Agent runtime

- `hepta_agent_mcp_launcher.py`：固定身份和环境下启动 MCP bridge。
- `hepta_agent_trust_domain.py`：严格读取 trust-domain 配置。
- `hepta_broker_egress_policy.py`：加载固定 UID/端口的最小 nftables 边界。

## Research and strategy

- `hepta_market_*`、`hepta_official_source_capture.py`：市场上下文与数据规范化。
- `hepta_strategy_*`：策略契约、shadow runner 和 replay evaluation。
- `hepta_eurusd_confirmed_momentum_strategy.py`：当前 EURUSD 策略实现。
- `validate_hepta_strategy_decision_receipt.py`：验证有界 shadow 决策 receipt。

本目录不再包含发布打包、质量门禁、P1/round、动态 PAPER campaign、repair/renew/supervisor、attestation、terminal witness、Windows 一键上线或硬编码用户工作区脚本。
