# Iteration contract

HeptaTrader 的开发循环只保护会造成交易错误或权限越界的运行时 invariant，不维护发布认证流水线。

## 核心测试范围

`./scripts/dev_core.sh` 构建并运行 19 个测试，覆盖：

- OMS journal durability 与 execution coordinator 幂等
- Agent Tool Gateway、Unix transport 与 session supervisor
- decision lease 与 execution authority
- authoritative snapshot 与 refresh
- simulator E2E
- IB order lifecycle
- paper kill switch

## 非目标

开发循环不生成或验证 bundle、manifest、evidence closure、安装树、VM、systemd 镜像、AppArmor、SBOM、签名或 round 证明。

这些对象若未来确有分发需求，应在独立发布系统按固定 commit 生成；不得向普通功能提交反向渗透。
