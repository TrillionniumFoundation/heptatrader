# Strategy Validation

Status: current normative
Applies to: research strategies, StrategyProposal producers and promotion reviews
Verification: point-in-time, walk-forward, cost, capacity, stability and shadow-parity evidence
Authority: strategy validation gates

验证顺序：data integrity → leakage controls → deterministic replay → purged walk-forward/embargo → untouched final OOS → cost/slippage/impact/borrow/funding sensitivity → capacity/liquidity → regime/time/worst-slice stability → failure-path → refactor parity → shadow observation。

指标至少包括 net return、drawdown、tail loss/CVaR、turnover、trade count、time in market、factor/concentration、cost share、capacity、parameter sensitivity和worst slice。单一 Sharpe/收益不能构成 promotion。

Research artifact 只可进入 reviewed module version。SHADOW→ACTIVE Simulator需要module/contract/resource evidence；Simulator→IB PAPER需要runtime parity与外部 qualification；任何 LIVE promotion都不在本流程范围。自动 promotion 禁止。
