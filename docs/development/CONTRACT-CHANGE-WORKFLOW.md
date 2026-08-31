# 契约变更流程

Status: current normative
Applies to: public schemas, APIs, events and state formats
Verification: contract registry and compatibility tests
Authority: contract change authority

1. 创建 C2/A3 change record，列出 producer、consumer、state 和 rollout。
2. 决定 additive minor 或 breaking major version。
3. 更新 contract registry/schema、canonical examples 和 unknown-field policy。
4. producer 支持新格式；必要时双读或版本路由。
5. consumer 完成迁移和 negative tests。
6. 对 persisted state/journal 提供 replay/migration fixture。
7. 更新 capability/module/test registries。
8. merge candidate 运行 compatibility、fuzz、replay 和 rollback。

禁止在 C++ 与 Python 中分别手写无法机械验证的同名 schema。
