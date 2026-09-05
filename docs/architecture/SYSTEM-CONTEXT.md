# 系统上下文

Status: current normative
Applies to: all runtime, research and operations components
Verification: `python3 scripts/check_documentation_control_plane.py` and architecture tests
Authority: system context authority

Hepta 的外部参与者只有四类：Agent/Strategy developer、Operator、Broker/Venue 和 Research user。

```text
Research inputs -> Research/Replay -> Strategy artifact
                                        |
Market/Venue -> Data/State -> Strategy Proposal
                                  |
                                  v
                          Global Decision Plane
                                  |
                           AllocationPlan
                                  |
                                  v
                           Execution Authority
                                  |
                               Venue
Management Control Plane -- lifecycle/config/resource --> all non-Broker modules
```

Broker credential 只存在于 broker-owning Execution deployment identity。管理面、研究面、Agent 面和全局优化器均无 Broker 网络权限。
