# 产品范围与当前能力边界

Status: current normative
Applies to: repository-wide product claims
Verification: `docs/product/capability-registry-v2.json` and same-revision evidence
Authority: product claim authority

HeptaTrader 当前产品定义为：

> **面向 AI Agent 的模型无关、确定性交易控制与执行运行时，以及能力隔离的可复现研究平面。**

模型和策略是可替换、低信任客户端；它们不拥有 Broker session、账户真相、OMS、最终风险、对账或 kill switch。

## 当前核心

- typed local Gateway、native/MCP client；
- identity/session/capability enforcement；
- deterministic Simulator；
- OMS journal、stable command ID、replay/recovery；
- authoritative state 和 generation-consistent snapshot；
- target-position preview/apply；
- deterministic risk；
- Simulator/core 范围的 portfolio compiler；
- capability-free research/replay。

## 条件或实验能力

- IB PAPER：需要授权 SDK、受控 host 和 exact-artifact qualification；
- 多策略组合编译器：当前是可信纯策略边界，尚不是生产 multi-Agent allocator；
- research strategy：只产生 SHADOW/离线结果。

## 明确不支持

- 任意 LIVE mutation；
- CTP 和 XT/MiniQMT 的真实 transport/order lifecycle；
- 自动 SHADOW → PAPER/LIVE promotion；
- Agent 自选 Broker session 或提供 authoritative state；
- 未经证明的“全局最优”宣传。

对外声明必须由 capability registry 和 evidence 派生，不能根据目录、类名、示例或 PR 描述推断。
