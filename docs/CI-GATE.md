# Core CI

CI 的唯一职责是快速阻止交易运行时回归，而不是证明一个发布包满足所有部署环境。

## PR 必跑

1. IB-disabled Release 配置。
2. 构建 `hepta_core_test_binaries`。
3. 执行带 `core` 标签的 CTest。

核心测试覆盖 OMS journal、幂等协调器、Agent 工具边界、Unix transport、session supervisor、decision lease、authoritative snapshot、simulator E2E、IB order lifecycle 和 paper kill switch。

## 不在 PR/源码仓库执行

- source/no-Git bundle
- release manifest 与 source baseline
- evidence index、closure 和 ingestion receipt
- install tree、VM bundle、rootful systemd/AppArmor certification
- round 编号 soak/canary 认证
- self-hosted licensed SDK 发布封装

真实 broker SDK 编译、部署镜像和发布签名如有需要，由独立外部发布流程消费固定 commit。它们不得成为普通功能 PR 的前置条件。
