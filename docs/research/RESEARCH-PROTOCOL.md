# 研究与回放协议

Status: current normative
Applies to: `research/`, strategy artifacts and offline validation
Verification: research schema, self-test, point-in-time and digest parity
Authority: research protocol authority

当前研究路径只有三个 durable object：

1. `RunManifest`：code/data/parameters/costs/folds/numeric policy identity；
2. `EventLog`：append-only normalized observation/decision/simulated execution；
3. `RunSummary`：data quality、metrics、failures 和 digests。

必需属性包括 source/strategy/config/dataset/runner digest、UTC calendar/session/symbol mapping、fold/purge/embargo/final OOS、commission/spread/slippage/delay/impact/borrow/funding、capacity/regime/worst slice、deterministic seed/numeric tolerance/output digest，以及 changed-same-timestamp、missing、duplicate、out-of-order fail-closed。

Research 产物永不携带 runtime token、preview permit、Broker credential、PAPER/LIVE grant 或自动 promotion capability。
