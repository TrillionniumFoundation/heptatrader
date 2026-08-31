# Iteration contract

每次普通功能提交都必须保持交易正确性、权限边界和可发布性，而不是只运行一个轻量测试集合。

## Pull-request gates

- repository contract：必需文档/工作流、版本一致、无开发者绝对路径、systemd/install 引用、unsupported venue fail-closed、source-size no-growth；
- Python 安全工具测试；
- GCC 与 Clang、Debug 与 Release 核心构建/测试；
- ASan + UBSan；
- hardened Release 安装、安装树校验、SPDX SBOM 和 CPack；
- Gateway privileged-symbol boundary。

夜间另行运行 TSan。IB PAPER 资格认证不在公共 hosted runner 上模拟，而由受控 self-hosted runner 手工执行。

## Local loop

```bash
./scripts/dev_core.sh
```

它构建默认 core runtime、运行全部 `core` 测试、生成 staging install manifest 与 SBOM。该命令不读取 Broker credential，也不宣称真实 Broker qualification。

## Change discipline

- 风控、协议、journal 或 state-machine 变更必须带回归测试和稳定 reason code。
- 新 venue 在真实 transport、authoritative reconciliation 和 qualification 完成前标记 Unsupported，并且 outbound fail-closed。
- 大型旧文件受 no-growth budget 约束；新增代码优先进入职责清晰的小模块。
- capability、安装图、systemd、runbook 和 CI 必须在同一变更中同步。
