# Feature Registry

Status: current target contract
Applies to: deterministic research and future feature runtime
Verification: implementation/input digest, lookback, generation, replay and leakage tests
Authority: feature identity and availability

Feature entry 声明 ID/version、implementation/config digest、input contracts、lookback、availability lag、warm-up、missing policy、numeric type/scale、state model、shard key、resource budget和output schema。Feature output 绑定 input watermark与generation。

在线/离线实现必须通过 golden parity。未来数据、revision leakage、跨 instrument隐式共享或未声明状态使 feature不可用于proposal。Feature registry 不授予 strategy activation。
