# 策略验证

Status: current normative
Applies to: all strategy modules before shadow/active lifecycle
Verification: research and simulator validation gates
Authority: strategy validation authority

验证顺序：

1. point-in-time/no-lookahead；
2. deterministic replay；
3. purged walk-forward + embargo；
4. final OOS；
5. cost、delay、impact、borrow/funding sensitivity；
6. capacity/liquidity；
7. regime、time-of-day、worst-slice；
8. parameter-search budget 和 selection bias；
9. refactor golden parity；
10. Simulator SHADOW；
11. champion/challenger；
12. 受控 active Simulator。

盈利回测不等于 promotion。策略必须输出完整 StrategyProposal 经济信息，才能参与全局 allocator。
