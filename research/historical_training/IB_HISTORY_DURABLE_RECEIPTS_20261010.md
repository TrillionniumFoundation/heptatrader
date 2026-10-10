# Durable IB history receipts and offline replay (2026-10-10)

Scope: Pocket4, an isolated heptatrader worktree based on 3b0a15c. This branch is research-only. No order permission, production-service changes, strategy promotion, historical deletions or synthetic market data.

## Defect and remediation

The fail-closed download refactor removed the loop-local receipt_path assignment but still called write_once(receipt_path, receipt). After a broker callback, the undefined local would prevent the terminal receipt from being written, leaving an issued request as an orphan that cannot safely resume. The code now writes the receipt to directory / 'receipt.json'.

The success-resume preflight also requires evidence of a real end callback (or head timestamp or schedule, as appropriate), an empty request-scoped error list, a list of bars and a matching row count. A RECEIVED label alone is not trusted. Failed and unfinalized requests remain immutable and non-retryable in their original output root.

Two deterministic, socket-free tests exercise the entire callback -> response -> receipt -> resume lifecycle and fail-closed code 162. Additional adversarial tests cover a false success lacking an IB completion callback and a receipt relabelled to success despite an IB error payload.

## Verification and historical data

- Historical research regression: 380 tests passing, 7 skipped, on Pocket4 after the final patch.
- Core Python regression after the final receipt-validation hardening: 1,716 tests passing, 21 skipped, on Pocket4.
- Legacy immutable EUR/USD 210-day source: preflight accepted all 31 planned HEAD/BID/ASK/MIDPOINT/SCHEDULE requests without network access. The frozen dataset still contains 42,716 paired 5-minute time points and is revised historical data, not a past real-time feed.
- Cost-calibrated EUR/USD price-only walk-forward rerun: 3 purged temporal folds, 40,451 eligible rows, every validation fold chose no trading. Exact output SHA-256 5de2f4e74ee9d4ab4185c2539b58ac899c563fec05c5496b5919c73d9edf234e matches the prior frozen result byte-for-byte. There is no demonstrated deployable alpha.

## One bounded PAPER gateway history probe

At 16:43 (+08), a distinct extension-v2 output root was created with the original pinned SDK, localhost:4002 PAPER connection, client 8984, one 30-day chunk, 15-second pacing, historical cutoff 2026-10-08 23:59:59 UTC, and trade_authority=false. IB reported cashhmds disconnected and returned request-scoped error 162 (Request Timed Out) for HEAD MIDPOINT (request 7800). The reader wrote a terminal ERROR receipt, stopped after exactly one history request, and sent zero orders. The version-1 and version-2 failed roots must never be resumed or relabelled success. Future queries require a new version-3 output root after independent HMDS health evidence.

Historical evidence directory: /home/alex/.local/state/heptatrader/operator-evidence/ib-history-durable-20261010. New v2 read-only history receipts: /home/alex/.local/state/heptatrader/neuron-learning/historical-offline/eurusd-5m-extension-20261010-v2.

## Production, PnL and model gates

The PAPER Execution service stayed running without additional restarts, but full account PnL and EUR/USD unrealized mark are incomplete. The current EUR/USD short remains under its original execution owner; weekend authoritative FX quote unavailability cannot be repaired using IB revised history. Live Laya trainer and evaluator source-health HOLD must remain unchanged. No model/actor update, no promotion and no change to PAPER trading settings is allowed based on this offline run.

Next prerequisites: recover cash historical data farm (no automatic retries), acquire new immutable BID/ASK/MIDPOINT/SCHEDULE receipts and prove full calendar coverage, obtain fresh broker quote and account cash-flow statements, and qualify prospective economic performance after fees/slippage against a no-trade baseline.
