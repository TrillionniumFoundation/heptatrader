# Development Traceability Model

Status: current normative
Applies to: capability, module, contract, test, gap, milestone and evidence registries
Verification: documentation-control-plane cross-reference checks
Authority: end-to-end development traceability

完整追踪链为：

```text
product capability
  -> providing/consuming modules
  -> versioned contracts and schemas
  -> source/build/deployment ownership
  -> verification check IDs and fault/performance budgets
  -> gap/workstream/milestone
  -> exact-revision evidence / external qualification
```

任何 capability 如果缺少 module、contract、verification 或 maturity/qualification 映射，只能是 `planned` 或 `unsupported`。任何 current module 如果没有 owner、backup、state/concurrency/failure/resource contract，不得作为独立团队交付面。

生成视图只展示注册表结果，不创建新状态。PR 描述、issue、dashboard 和 release note 必须引用同一 ID，不能发明平行命名。
