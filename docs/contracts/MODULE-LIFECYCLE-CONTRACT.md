# 模块生命周期契约 V1

Status: current target contract
Applies to: Management Control Plane and all deployable modules
Verification: lifecycle state-machine, rollout and rollback tests
Authority: module lifecycle authority

```text
registered -> validating -> warmup -> shadow -> active
                     \-> rejected
active -> degraded -> quarantined -> draining -> retired
```

每个转换必须有 actor/authority、source/target state、module version/config/model digest、health/resource evidence、effective epoch、rollback target 和 stable reason code。

`active` 策略模块只能发布有 lease 的 proposal。`degraded` 可以降低频率或缩小候选范围；`quarantined` proposal 立即失效；`draining` 不能增加新风险；`retired` 不再被发现。

Management Control Plane 可以改变 lifecycle 和资源，不可以直接发送订单。
