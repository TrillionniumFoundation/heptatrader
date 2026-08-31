# 模块创建指南

Status: current normative
Applies to: new modules and major extractions
Verification: ModuleManifest V2, dependency and module tests
Authority: module creation process

1. 明确 authority、state owner、failure domain 和 deployment。
2. 注册 module ID、owners、provides/consumes、dependencies。
3. 定义版本化 input/output contract。
4. 创建唯一 CMake/Python package target。
5. 添加 unit、negative、contract、performance 和 fault tests。
6. 加入 capability 和 roadmap，仅在集成证据完成后升级状态。
7. 配置 shadow/canary/rollback。
8. 验证 active graph 无环、无跨模块 `.cpp`、无 `legacy/` 依赖。

不应为了“一人一个目录”创建无独立 contract 或 failure domain 的伪模块。
