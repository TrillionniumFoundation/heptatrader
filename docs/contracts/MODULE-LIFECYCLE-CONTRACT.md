# Module Lifecycle V1

Status: current target contract
Applies to: Management Control Plane, strategy/feature modules and proposal aggregation
Verification: state-machine, lease, health, quarantine, rollout and rollback tests
Authority: module lifecycle semantics

状态机：

```text
REGISTERED -> WARMING -> SHADOW -> ACTIVE -> DEGRADED
                                      |          |
                                      v          v
                                  DRAINING <- QUARANTINED
                                      |
                                      v
                                   RETIRED
```

每次 transition 绑定 module/instance/version、control generation、effective time、reason code、config/model/artifact digest、health evidence 和 actor identity。非法跳转、generation 回退或未验证 artifact 拒绝。

- WARMING 不产生可用 proposal；
- SHADOW 输出不参与 active capital allocation；
- ACTIVE 需要当前 contract、resource budget 和 deterministic validation；
- DEGRADED 可降低频率/预算，但不得隐式扩大权限；
- QUARANTINED 使 proposal 立即 expiry，不影响 Execution safe exit；
- DRAINING 停止新 proposal 并等待有界状态迁移；
- RETIRED 不再被调度，历史版本由 artifact/evidence 存档而非当前 docs。

自动 SHADOW→PAPER/LIVE promotion 禁止。Management 只能控制 module lifecycle，不能改变 Execution mutation authority。
