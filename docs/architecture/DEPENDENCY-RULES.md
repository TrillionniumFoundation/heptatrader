# 依赖规则

Status: current normative
Applies to: CMake targets, C++/Python modules and generated bindings
Verification: `docs/modules/module-registry-v2.json` and dependency-DAG validation
Authority: dependency-rule authority

1. 一个 active source 只属于一个模块；一个 build target 只属于一个模块。
2. 模块只能依赖 registry 中的 `allowed_dependencies`。
3. 禁止测试通过直接编译另一模块 `.cpp` 绕开 target 边界。
4. contract/types 可以被多模块依赖，但不得反向依赖实现。
5. pure-policy 模块不得依赖 socket、credential、filesystem mutation、venue 或 Execution server。
6. adapter 只翻译 transport/event，不拥有 strategy、capital 或 final risk。
7. Gateway 不依赖 Broker adapter；Execution 不依赖 Agent implementation。
8. active graph 不依赖 `legacy/`。
9. dependency graph 必须无环；例外只能通过 accepted ADR 且有移除期限。
10. public contract 变化必须先更新 registry/schema，再更新 producer/consumer。

推荐层级：

```text
L0 contracts/numeric
L1 protocol/data primitives
L2 state, policy, adapters
L3 orchestration services
L4 process composition/CLI
```

只允许同层受控依赖或从高层指向低层。
