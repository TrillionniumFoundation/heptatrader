# ModuleManifest V2 规范

Status: current normative
Applies to: all current, experimental, planned and unsupported modules
Verification: module schema, registry, CMake target and dependency checks
Authority: module manifest authority

模块边界由 authority、state、failure domain、contract、concurrency、target 和 deployment 决定，而不是由团队组织图或目录大小决定。

每个模块必须声明：

- stable ID、semantic version、lifecycle、kind、trust domain 和 authority；
- `exclusive` 或临时 `shared-migration` ownership；后者必须绑定开放 gap；
- source roots、build targets、provided/consumed contracts；
- allowed/forbidden dependencies；
- state model、persistence 和唯一 writer；
- concurrency、shard key、blocking-I/O 和 cross-module-lock policy；
- backpressure/overflow、risk-increase failure 和 safe-exit behavior；
- resource budget、DRI、backup、reviewers 和 verification IDs。

完成模块化的条件是 manifest 与实际 source/target/link/runtime graph 一致。目录存在、类名存在或单测通过均不等于 capability 已集成。

`shared-migration` 仅描述当前组合 target 的技术债；M2 出口要求这些共享面拆成单一 target ownership。模块不能通过 shared utility、header-only 或 test source 注入绕过依赖声明。
