# 模型与策略 Promotion

Status: current target governance
Applies to: research artifact to runtime module lifecycle
Verification: lifecycle, validation and independent review evidence
Authority: model promotion authority

```text
research-valid -> simulator-shadow -> simulator-active
  -> paper-proposal -> paper-qualified -> paper-active
```

不存在自动 PAPER/LIVE promotion。每一步绑定 model/config/data/code digest、validation summary、module version、capability state、rollback 和有效期。

模型更新不会继承旧版本的 PAPER qualification；若改变 strategy economics、contract、resource behavior 或 failure mode，需要重新验证相应阶段。
