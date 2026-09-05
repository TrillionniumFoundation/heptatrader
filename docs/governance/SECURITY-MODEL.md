# Security and Threat Model

Status: current normative
Applies to: Agents, Gateway, Execution, global decision, management, deployment and qualification
Verification: authority-boundary, capability, install and external qualification gates
Authority: repository security model

## Adversaries and failures

假设模型输出可恶意或错误；本地 Agent 可越权；token/配置可过期或被替换；模块可崩溃、阻塞或发送畸形输入；Broker callback 可重复、乱序或缺失；网络可分区；进程可在 journal/send 任意边界崩溃；文档和能力声明可漂移。

## Required controls

- Agent/Gateway 与 Broker-owning Execution 使用不同 OS identity、socket、credential 和 network policy。
- Gateway 不链接 venue adapter，不读取 Broker credential，不维护账户真相。
- 所有跨 trust-domain 输入 typed、bounded、versioned、canonical 并带 identity/generation/expiry。
- credential 只由部署 authority 注入；日志、metric、SBOM 和 evidence 不包含 secret 或 raw account identifier。
- Management Control Plane 只能管理版本、配置、资源和 lifecycle，不能授予自己 venue mutation。
- Global Decision Plane 无 Broker credential；plan 必须在 Execution 重验。
- risk increase fail closed，safe exit 单独验证和调度。

## Security review triggers

新增 credential、network egress、runtime capability、venue、state authority、public schema、dynamic code loading 或 LIVE 路径均属于 A3/O4 变更，必须经过独立 security、execution-safety 和 operations review。
