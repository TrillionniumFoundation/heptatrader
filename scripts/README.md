# Runtime, validation, and research scripts

## Canonical development and release checks

- `dev_core.sh`：仓库契约、Python tests、核心构建/CTest、安装树和 SBOM。
- `check_repo_contracts.py`：文档、版本、systemd/install、unsupported venue 和 source-size 门禁。
- `verify_install_tree.py`：验证 staged install 的必需文件、mode、symlink 与引用并生成 hash manifest。
- `generate_sbom.py`：生成 SPDX 2.3 JSON SBOM。
- `run_ib_paper_qualification.sh`：只把受控参数交给仓库外审核 harness，不包含凭据或 Broker 自动化。

## Agent runtime

- `hepta_agent_mcp_launcher.py`：固定身份、trust domain 和净化环境下启动 MCP bridge。
- `hepta_agent_trust_domain.py`：严格读取 trust-domain 配置。
- `hepta_broker_egress_policy.py`：为固定 execution UID/loopback PAPER 端口应用最小网络边界。
- `hepta_observability.py`：只读 OMS journal metrics/alerts collector。

## Research and strategy

`hepta_market_*`、`hepta_official_source_capture.py`、`hepta_strategy_*` 与 EURUSD shadow 策略服务于可复现研究。它们不拥有 execution capability。研究输出进入 TradeIntent 前必须经过 schema、数据新鲜度和 replay validation。

目录中没有 credential、动态 PAPER campaign、自动 disarm、LIVE 上线或宿主调优脚本。此类能力不得通过新增便捷脚本绕过 systemd、Gateway、Execution 与资格认证边界。
