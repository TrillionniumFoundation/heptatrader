# Temporal + Laya two-node fusion

This is the first shadow-only Hepta meta-neuron experiment. It does not replace the FX/BTC trader and exposes no order API.

The frozen Laya node and a new causal numeric temporal node predict the same registered five-minute EUR/USD MID-return class. The temporal node consumes the existing 16 fixed-scale causal features at every 10-second frame through a 32-hidden causal GRU. The frozen Laya seven-class probabilities are projected deterministically into a 32-dimensional node embedding. A small learned gate combines the two probability distributions as an exact convex mixture.

Every node emits a 32-dimensional embedding, normalized categorical-entropy uncertainty, exact input/model evidence, and effect_authority=false. The fusion output also records gate_numeric_weight. This is the intended stable interface for later sparse routing and graph message passing.

The first profile is fixed before prospective evaluation: 192 chronological mature rows train the numeric node and gate; the next 32 rows are internal diagnostics only and never qualify the candidate. Loss is fusion cross entropy plus 0.25 numeric cross entropy. The existing Laya base and adapter remain frozen. PnL, remote confidence and trading actions are not labels.

The candidate is frozen once. Twelve new future slots are then registered six minutes apart. Each slot commits a fresh causal prediction before its five-minute outcome. An independent evaluator compares Laya-only, numeric-only and fusion Brier scores using the exact future endpoint. It requires at least ten observed slots, at most two censored slots, at least two target classes, positive mean Brier improvement over both components, and a one-sided exact sign-test p <= 0.025 against each component. Even a pass is EXPERIMENTAL_FUSION_WIN_SHADOW_ONLY and grants no promotion or trade authority.

The initial internal diagnostic is deliberately not tuned away: the numeric node beat the current frozen Laya on the chronological tail, while the learned gate placed about 99.5% weight on numeric and fusion was slightly worse than numeric alone. The prospective window decides whether combining helps, whether a sparse router should select numeric alone, or whether the design should be rejected.

If prospective evidence later supports multiple complementary neurons, this interface can scale to a sparse top-k Graph-of-Hepta-Neurons: specialized numeric/regime/execution/semantic nodes -> uncertainty/evidence-aware router -> 2-3 message-passing rounds -> meta embedding -> price/value/uncertainty heads. Only compact embeddings, uncertainty and evidence digests need cross-machine transport. Execution authority remains a separate owner boundary.
