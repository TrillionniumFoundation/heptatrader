# IB cash HMDS gate, EUR CASH exposure, and validation-cost diagnostics

**2026-10-10, Pocket4, UTC+08.** Isolated research branch based on the reviewed terminal-receipt fix `6ab3a8a`. No changes to resident execution, gateway, drivers, NAV observers, PAPER order policy, Laya trainer/evaluator or persistent trading permissions.

## Exact broker signals and bounded historical continuity

IB's documented 2105 is **historical data farm disconnected**, 2106 is **connected**, and 2107 is **demand-idle (not disconnected)**. A global notice for `cashhmds` is the relevant farm for EUR/USD CASH history; a 2106 notice for `apachmds` does not repair an explicit `cashhmds` disconnect. After the normal PAPER handshake and before every new request dispatch (including after any pacing wait), the pinned historical downloader refuses the request with `CASH_HMDS_DISCONNECTED_NO_HISTORY_DISPATCH` when it has actually observed a still-active 2105. It never infers farm recovery from a different farm's notice.

This is **not** a claim of historical-farm health when the state is `UNKNOWN`: without a delivered broker notice, the original bounded first request might still be attempted and remains protected by the 162/timeout immutable failure receipt. Inactive 2107 does not trigger a false outage. This change is passive: no extra health-probe request, no production restart, no order wire code, and no automatic retry. The previously failed v1 and v2 output roots remain immutable; no v3 history download was attempted in this run.

IB reference: https://www.interactivebrokers.com/docs/tws-api/doc/error-handling/error-codes

## Exact EUR CASH account reconciliation

For this exact PAPER reset, the read-only audit compares five independent values: IB account EUR cash ledger, PnL broker position, PnL strategy position, authoritative execution observation and FX driver's persistent position. All five equal **-89,264 EUR**. The securities `positions` array has zero entries, but that does **not** mean the EUR CASH balance is zero. The audit binds exact account, reset period, journal fingerprint, source, freshness and absence of unsettled commands; mismatches fail closed. It never emits the broker account identifier.

The frozen audit receipt is at:

`/home/alex/.local/state/heptatrader/operator-evidence/ib-cashfarm-preflight-20261010/fx-exposure-frozen-originals-20261010/audit.json`

Its SHA-256 is `b0cce40386907f7d6f47da053a562813f06d44fb14920dd89e6f7e2cadc49fb6`. The exact three private JSON input snapshots are sealed next to it, mode 0600. Source timestamps were current, but the account observer explicitly had `broker_valuation_tick_attested=false`: **matching asynchronous observations do not prove atomic broker valuation**. The EUR/USD authoritative executable quote was unavailable, so unrealized PnL and all-in account return remain incomplete.

## Why frozen offline validation chooses no trading

Historical dataset: 210 calendar days, 42,716 paired 5-minute IB ASK/BID/MIDPOINT timestamps, 40,451 eligible features. This is **revised** historical data and its previously inspected validation periods cannot be relabeled as unseen independent tests.

The read-only frontier uses the **same** causal training rows, 30-day validation folds, ridge predictor, 15-minute nonoverlap rule, fees, spread and assumed slippage as the existing `eurusd_walkforward_cost` selection. This preserves the original threshold-selection logic. The original validation cost assumption is **3.6970942 bps** per completed roundtrip. Threshold 0 leads to:

| Fold | Roundtrips | Hypothetical gross USD | Hypothetical net USD |
|---|---:|---:|---:|
| 0 | 1,973 | -1,065.66 | -74,009.32 |
| 1 | 1,883 | +486.21 | -69,130.07 |
| 2 | 1,806 | +2,462.14 | -64,307.39 |

At threshold 1 bps the three validation folds have 3, 1 and 0 trades; the first two report hypothetical +$50.18 and +$19.38 net but do **not** pass the pre-existing **30-trade minimum**. All three original folds therefore correctly select **no trade**. Gross/net values above are historical simulation using the original $100,000 notional/price-fill assumptions, **not** realized account PnL or a forecast of production performance.

A new immutable receipt is stored at:

`/home/alex/.local/state/heptatrader/operator-evidence/ib-cashfarm-preflight-20261010/eurusd-validation-frontier-v1.json`

SHA-256: `e0138704f76dce12e2bb0dc955615cd9bdd4e97852d9f9721232932683065777`.

Both new tools have zero order authority and do not update scorers, actors, Laya promotion, or execution. Additional model tuning on these already-viewed periods remains exploratory until fresh, causally separated outcomes are available.

## Remaining actual gates

1. **External IB cash HMDS:** require an actual reconnect/healthy history response, then use a *new* immutable v3 root under the original PAPER-only client and request caps, not a replay of v1/v2 failures.
2. **PnL:** await new qualified IB FX quote and exact reset-to-now Flex/Activity cash-flow evidence. Do not substitute historical midpoint for an executable current mark, or label the current account NAV change as trading profit.
3. **Training:** test only predeclared, future data and independent prospective evidence. Preserve the 30-trade minimum and production guard; a tiny profitable set cannot pass. Prior validation demonstrates that high churn is uneconomic.
4. **Owner/CI:** merge only after stacked PR review and exact-commit CI verification; no automatic trading cutover.

## Test coverage

- Passive cash history state transitions, wrong-farm re-connect, 2107 idle, 2105 pre-request/after pacing, zero-transport and immutable error handling.
- Off-market EUR CASH ledger reconciliation, wrong account or journal, stale/future cohorts, pending orders and nonfinite exposures.
- Positive versus tiny-sample validation thresholds, actual spread/slippage cost burden and original simulation nonoverlap.

This branch is **research-only** and is intentionally not installed over the running Gateway or Execution.

## Exact Pocket4 verification

* 397 historical training tests passed, with 7 skipped.
* 1,733 core Python tests passed, with 21 skipped.
* Frozen EUR CASH account audit and historical validation frontier both replay byte-for-byte from retained inputs.
* The saved real IB 2105 cashhmds callback replay blocks dispatch before any new request, without contacting IB.
* Original 210-day dataset, historical v1/v2 error receipts, trading owner, model selectors and resource guards remain unchanged.
