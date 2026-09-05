# 模块拓扑

Status: current target contract
Applies to: active module graph and planned extraction
Verification: `docs/modules/module-registry-v2.json` and module architecture checker
Authority: module topology authority

模块按 authority、state ownership、contract、failure domain、concurrency domain 和 deployment unit 划分，而不是按人数或目录平均切割。

```text
contracts/numeric
   ├─ protocol
   ├─ data/state types
   ├─ proposal/allocation types
   └─ venue types

data -> feature -> strategy
                  |
             proposal aggregator -> global allocator -> portfolio compiler -> risk policy
                                                                   |
gateway/session -> intent ------------------------------------------+
                                                                   v
execution permit -> OMS/journal -> execution coordinator -> venue adapter
                      ^                     |
                      +---- state/reconcile-+
```

第一轮必须拆解的复合模块：

- `hepta_agent_os_core` → protocol/session/gateway/audit/control API/Unix IPC；
- IB runtime → transport/event normalization/state projector/order router/reconcile/composition；
- research runner → parser/evaluator/cost model/report/CLI；
- shared protocol sources → 独立 target，禁止 client 和 server 各自编译同一 `.cpp`。

完整模块集合和依赖声明位于 module registry。
