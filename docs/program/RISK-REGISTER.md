# 项目风险注册表

Status: current normative program view
Applies to: roadmap execution and architecture migration
Verification: gap registry, incident reviews and milestone gates
Authority: program risk authority

| 风险 | 影响 | 缓解 |
|---|---|---|
| PR #4/#6 分叉导致双重真相 | 架构、CI、release 冲突 | M0 单一 integration train；主题化迁移 |
| 过早微服务化 | 延迟、2PC、运维复杂度 | 先 monorepo + module targets + process by trust domain |
| 全局 optimizer 成为热路径单点 | 延迟与不可用 | 分层分片、计划 expiry、safe fallback |
| 局部 proposal 信息不足 | 无法实现全局最优 | utility/uncertainty/factor/cost/capacity contract |
| 共享锁与全局 telemetry | p99 抖动 | per-thread/per-shard aggregation |
| 浮点跨模块漂移 | permit/digest/risk 不一致 | fixed numeric boundary + canonical quantization |
| 单人 CODEOWNER | 审批瓶颈与 bus factor | team ownership + backup + contract reviewer |
| 历史文档被误当现行能力 | 错误开发/部署 | active tree 只保留 V2；历史由 Git 保存 |
| mock 被误当 PAPER 证据 | 资格误报 | exact-artifact external qualification |
| 自动 promotion | 风险能力失控 | explicit reviewed lifecycle；无自动 LIVE |
