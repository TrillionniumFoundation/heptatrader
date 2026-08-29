# Runtime and research scripts

本目录只保留直接服务于运行、研究、故障注入和安全恢复的工具。

- Agent/session：`hepta_agent_*`
- 策略与市场上下文：`hepta_strategy_*`、`hepta_market_*`
- paper runtime/recovery：`hepta_local_*`、`run_paper_*`
- broker 运维与故障注入：`ib_*`、`fault_injection_*`
- 配置与回放：`resolve_hepta_config.py`、`validate_sim_data.py`、`verify_oms_journal_replay.py`

发布 manifest、source bundle、evidence closure、安装树、VM/rootful systemd certification 和 repository governance 脚本已移除。不要把这些职责重新接回普通 PR CI。
