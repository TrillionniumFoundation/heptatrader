# Deployment Topology

Status: current target contract
Applies to: process, OS identity, socket, network and shard deployment
Verification: install-tree, systemd, network isolation and qualification gates
Authority: deployment topology authority

推荐拓扑是“模块化 monorepo、按信任域分进程、按 authority 分片”，不是每个目录一个微服务。

```text
unprivileged Agent UID(s) -> Tool Gateway UID
strategy/feature workers  -> Global Decision shard(s)
management UID            -> lifecycle/config APIs only
                           -> Execution UID per domain -> Broker/Venue
```

- 每个 execution domain 只有一个 active mutation leader，并使用 epoch/fencing 防双主。
- Broker credential 和 broker network egress 仅授予 Execution UID。
- 不同不可信 Agent 使用独立 UID、socket、token 与 capability set。
- Global Decision 可按 capital pool/account/risk book 分片；Management 不进入 tick 热路径。
- state/journal 路径按 execution domain 独立，禁止多个 authority 共享可写 journal。
- deployment topology、binary digest、config digest 和 module set 必须进入 evidence identity。
