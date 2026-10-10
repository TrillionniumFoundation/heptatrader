# IB EUR/USD historical continuation: immutable failure and research-only training

**Scope (2026-10-10):** Pocket4, existing PAPER IB Gateway on localhost:4002, pinned historical client 8984. No trading, order modification, production restart, promotion, model-actor update, history deletion, or synthetic real-time quotes.

## Observed evidence

* Frozen 210-calendar-day IB dataset: 2026-03-11 to 2026-10-06, 42,716 aligned 5-minute BID/ASK/MIDPOINT observations. `full_requested_coverage_proven=true`, `scheduled_missing_count=0` and `revised_historical_data_not_contemporaneous=true`. Calendar response interval issues are retained (2 requests) but union coverage has been checked.
* 2026-10-10 extension v1: three request-scoped IB error **162 / Request Timed Out**, one further timeout, zero new bars. The original output root and its failed responses are immutable; do **not** delete these chunks, relabel ERROR as RECEIVED, retry against that directory, or present the old source as prospective.
* Independent frozen OMS-prefix cost receipt: 190 unique EUR/USD executions with fee callbacks and no missing fee evidence; **actual execution slippage not attested**. No Flex/cash-flow statement, so account-level all-in PnL is incomplete.
* Frozen price walk-forward from 210-day source: 3 purged train/validation/test folds, 40,451 eligible price rows; **all folds select no trading**. Replay SHA-256 `5de2f4e74ee9d4ab4185c2539b58ac899c563fec05c5496b5919c73d9edf234e` matches the original byte-for-byte. No alpha or deployment claim.

## Fix in this branch

The history reader now treats **every request-scoped IB error** (including code 162) as `ERROR` and immediately stops the session **after sealing** its response, receipt, and progress. Pacing messages produce `PACING_STOP`. Missing callback completion produces `TIMEOUT` and stops even if an event is signalled. An existing failed receipt is never silently skipped during resume: the preflight checks the entire root before opening a new IB connection, then refuses `FAILED_CHUNK_IMMUTABLE_USE_NEW_OUTPUT_ROOT`. Immutable successful receipts may still resume after SHA-256 and request-content checks. Before any network dispatch the tool fsyncs the sealed request and its parent directories, so a power loss cannot silently erase a previously issued request. An unfinalized request directory is treated as unknown transport outcome and may not be reissued. A shared parent-directory flock fences independent output roots using the same pinned 8984 client ID. The tool has no order wire codes.

Regression tests cover code 162, pacing, partial/missing callback, successful resume, failed receipt, failure late in an earlier session, orphaned dispatched requests, response and request tampering, global-client exclusion, and directory fsync. All **376 historical** tests and all **1,591 core Python** tests passed on Pocket4 using `PYTHONPATH=research:research/neuron_learning:scripts`. The 210-day successful source passes this new preflight; the original extension fails preflight without network I/O and its session SHA remains unchanged.

## Controlled next history run (NOT executed here)

Only after independently verifying that the IB cash historical farm responds and there is a free historical client slot, create a **new** config with the identical pinned contract/SDK/PAPER identity, 1 chunk, 15-second pacing, end cutoff `20261008 23:59:59 UTC`, **new** `output_root` suffixed `-v2`, and `trade_authority=false`. Its seven bounded requests are 3 earliest-head probes plus BID/ASK/MIDPOINT/SCHEDULE for the selected 30-day interval. Confirm that the new root is empty and the source has not changed, then run manually in a history-only isolated process; stop on the first error, never retry automatically. On completion, run `historical_training.dataset.collect` and require complete calendar schedule union, no unpaired timestamps, no unproven gaps, no duplicate conflicts and no missing BID/ASK/MIDPOINT bars. Do not append bars onto the frozen v2 dataset in place; derive a new sealed dataset version with provenance.

IB API docs: [historical bars](https://www.interactivebrokers.com/docs/tws-api/doc/market-data-historical/historical-bars/receiving-historical-bars); [error 162](https://www.interactivebrokers.com/docs/tws-api/doc/error-handling/error-codes). A historical-bar end callback confirms transport completion, not the entire desired calendar span. The old 60-per-10-minute limit is relevant to small bars, but even 5-minute bars may be softened/throttled and disconnected: preserve the bounded policy.

## Production and research gates

* The 2026-10-10 12:17 PAPER Execution exit was `IB_PAPER_STALE_CASH_REFRESH_STREAM_UNSAFE`; the first retry failed with `IB_PAPER_MARKET_DATA_FARM_LOST_DURING_REFRESH`, and the next started at 12:18:55 (+08). This is an external market-data refresh blocker, not a reason to relax the safety gate or manually restart the broker.
* The live EUR/USD PAPER position must remain managed only by the existing owner/execution chain. `MARK_MISSING_STALE_OR_FUTURE` on a closed-market weekend means **unknown unrealized PnL**, not zero. Do not backfill that mark with revised IB history.
* The existing Laya trainer HOLD (`NO_CAUSAL_ORIGINAL_QUOTE`) and independent evaluator HOLD remain intact. Offline price research must use a separate read-only lane; no trading permission or self-promotion.
* Before any later PAPER strategy adjustment: verify authoritative fresh quotes, account-wide reconciliation/statement evidence, absence of unresolved broker commands and post-reconnect drift, robust OOS net performance above zero and no-trade under multiple fee/slippage stress settings, and prospective causal evaluation. Today the economic gate fails, so **no model promotion**.
