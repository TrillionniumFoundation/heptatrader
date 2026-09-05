# Model and Strategy Promotion

Status: current normative
Applies to: research artifacts, strategy modules and runtime lifecycle
Verification: validation evidence, module manifest, shadow/canary and independent approval
Authority: promotion boundary

Promotion 是新 module artifact/config/version 的显式治理变更，不是研究结果中的布尔字段。候选需绑定 source/model/data/config digest、validation summary、resource budget、contracts、failure behavior和rollback identity。

允许路径：research candidate → reviewed module → SHADOW → ACTIVE Simulator；之后可提出 IB PAPER qualification。每次 transition 需要独立 evidence和approval。模型不得携带session/permit/credential，Management不得自动提升到PAPER/LIVE。回退可由health/risk/evidence触发并使旧proposal立即过期。
