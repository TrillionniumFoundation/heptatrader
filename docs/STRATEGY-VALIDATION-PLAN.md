# Strategy validation plan

Status: EXPERIMENTAL  
Applies to: deterministic simulator and SHADOW research

## Stages

1. **Deterministic fixtures:** identical source, configuration, clock, and seed produce identical events, positions, PnL, and receipts.
2. **Regression equivalence:** compare baseline and refactor at the event/fill level before comparing aggregate performance.
3. **Walk-forward research:** use no-lookahead train/validation/test windows with realistic spread, fee, slippage, delay, rejection, and missing-data models.
4. **SHADOW:** consume fresh authoritative read-only evidence; track completeness, decision counts, missed intervals, regime stability, drawdown, and transaction-cost sensitivity.
5. **Bounded PAPER candidate:** only after separate risk challenge, protected approval, execution preview, small hard limits, operator kill switch, reconciliation, and broker-observed qualification.

Return, Sharpe, win rate, and drawdown are insufficient without sample size, turnover, capacity, tail loss, parameter stability, data provenance, and failure-mode evidence. Strategy validation never authorizes LIVE, which remains unavailable.
