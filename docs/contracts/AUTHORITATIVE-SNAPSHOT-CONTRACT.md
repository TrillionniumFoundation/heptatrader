# AuthoritativeSnapshot V2

Status: current core contract; target expansion
Applies to: state, Execution, risk, strategy and global decision
Verification: snapshot consistency and stale/incomplete negative tests
Authority: authoritative state contract

Snapshot envelope：

```text
execution_domain
execution_service_epoch
fencing_generation
state_generation
collection_watermark
event_watermark
captured_at_ms
fresh_until_ms
component digests
```

Typed payload 包括 health、quotes、FX、account、cash、PnL、positions、active/recent orders、risk limits/usage、liquidity 和 connection/reconcile state。

authoritative 成立必须满足 epoch/fence/generation capture 前后稳定、watermark 未被 invalidating event 改变、必需组件完整且来源明确、quote/FX 时序合法且未过期、payload bounded、数字 finite、component fingerprint 与 envelope 一致。

capture 期间的 fill/cancel/correction/reconnect/restart 要么完整进入同一 generation，要么本次 snapshot 失败。任何 consumer 不得拼接多个 snapshot。
