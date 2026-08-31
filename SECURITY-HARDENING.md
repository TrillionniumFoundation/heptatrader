# HeptaTrader security hardening contract

HeptaTrader 的安全目标不是让 Agent “更谨慎”，而是让高权限操作在确定性代码、OS 身份和耐久状态层面不可绕过。

## Secret 与身份

- 仓库只保存无凭据的 `.example` 配置。
- Broker credential、execution fence、session token 和授权材料必须通过 systemd credentials 或等价 secret store 注入。
- Agent、Gateway、Simulator Execution 与 IB PAPER Execution 使用不同的非 root UID/GID。
- token/credential 文件必须为受信任 owner、单链接 regular file，且不得被 group/world 访问。
- 不使用开发者工作区绝对路径，不从当前 shell 继承任意环境变量启动生产进程。

## 执行边界

- Execution Service 是唯一 Broker authority。
- Gateway 二进制构建后执行禁止符号扫描，防止静默链接 Broker、OMS 或 privileged Execution 实现。
- IB PAPER 仅允许 loopback Broker endpoint，并由独立 nftables policy 限制固定 UID/端口。
- CTP、XT/QMT 和 LIVE 未认证路径必须 fail closed，不能返回模拟的 Broker 成功状态。

## 风控与持久化

- 所有数量、价格、参考价、持仓和限额先进行 finite/range 检查。
- flatten-only 只能向零减少仓位，禁止穿过零点形成反向风险。
- 限价单必须有正且有限的限价；启用 price collar 时必须有正且有限的参考价。
- mutation 遵守 journal-before-send；关键记录执行耐久同步。
- journal 使用固定 fd、`O_NOFOLLOW`、owner/mode/link/inode 检查；路径替换或 I/O 不确定会 poison 写入并 fail closed。
- 不确定下单结果先 reconciliation，retry 复用原 command ID。

## 构建与供应链

- Pull request 必须通过仓库契约、GCC/Clang、Debug/Release、核心测试、ASan/UBSan、安装树和 SBOM 门禁。
- TSan 独立运行，避免与 ASan 组合产生无效配置。
- release tag 必须与 `VERSION` 一致；制品附带 manifest、SPDX SBOM、SHA256SUMS 和 build provenance。
- 默认 release 不包含 vendor Broker SDK。CTP 来源/许可未闭合时不得分发。

## 本地检查

```bash
./scripts/dev_core.sh
```

该入口不读取 Broker credential，也不尝试连接真实 venue。真实 IB PAPER 资格认证只能通过受控自托管 runner 和外部审核 harness 执行；证据必须绑定确切 commit。

发现潜在凭据泄漏时，立即撤销凭据、停止相关服务、保留审计证据，并按 `docs/RUNBOOK-INCIDENT.md` 处理。不要仅删除 Git 历史中的文件而继续使用原凭据。
