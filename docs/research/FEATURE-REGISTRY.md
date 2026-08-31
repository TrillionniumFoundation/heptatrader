# Feature Registry 目标契约

Status: current target contract
Applies to: feature definitions, offline replay and online parity
Verification: feature schema, leakage and parity tests
Authority: feature registry authority

Feature definition 必须绑定 feature ID/version、implementation/config/input dataset digests、lookback/horizon/warm-up、event-time/available-at semantics、missing/stale policy、normalization/numeric policy、offline/online parity tolerance、resource/latency budget、owner 和 deprecation。

Feature 不得读取未来数据、Broker credential 或运行时 mutation state。在线与离线实现不一致时，策略 artifact 不得 promotion。
