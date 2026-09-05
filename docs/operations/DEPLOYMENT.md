# 部署架构

Status: current normative; environment capability remains registry-derived
Applies to: simulator and conditional IB PAPER deployments
Verification: install-tree, service identity, network and startup gates
Authority: deployment authority

默认公开部署是 IB-disabled core：Tool Gateway、deterministic Simulator Execution、CLI/native/MCP、active systemd/socket templates、policies/schemas/documentation validators 和 capability-free research tools。

不包含 Broker SDK、credential、CTP overlay、XT transport 或 LIVE capability。

## 信任域

- Agent/Gateway UID：无 Broker 网络和 credential；
- Execution UID：唯一 Broker egress，持有受控 credential；
- Management UID：module/config/lifecycle，无 Broker egress；
- Operator：通过受控本地接口和审批执行部署/安全操作。

配置必须来自单一 authority，记录 config digest；环境变量、文件和 CLI 冲突时 fail closed。部署启动前验证 install manifest、service identity、socket permissions、network policy、kill switch、journal path 和 authoritative recovery。
