# IB historical client lock: UID-scoped global fence (2026-10-10)

Research-only patch stacked on `fix/ib-history-cashfarm-preflight-20261010`. No production service, account, order, broker profile, or Laya promotion change.

## Defect and fix
The original `global_history_lock(root)` used `root.parent / ib-history-client-8984.lock`. Two jobs with output roots under different parent directories could obtain different lock files while claiming the same PAPER IB Client ID 8984.

The new lock is anchored to the OS account's canonical UID home under `.local/state/heptatrader/neuron-learning/historical-offline`. This reuses the lock location of prior canonical sessions; changing `output_root` or the `HOME` environment variable cannot escape the single-client fence. Symlinked lock directories are rejected.

Unit tests verify two unrelated output parents, independent process exclusion and subsequent release, symlink refusal, and HOME spoof resistance without opening any IB network connection.

## External blockers and evidence
Pocket4's historical EUR/USD source contains 42,716 aligned five-minute timestamps across 210 days, but this revised historical dataset is not proof of a historical real-time quote. Previously failed extension roots v1 and v2 are immutable. IB cashhmds had not demonstrated recovery in the latest history session; no new query was submitted by this branch.

IBKR's public system status announced maintenance on 2026-10-10 from 07:00 to 17:00 ET (19:00 Saturday to 05:00 Sunday at UTC+8). This does not prove that maintenance caused every cashhmds error.

The frozen three-fold walk-forward still selects no trade, and full FX/account net PnL is not qualified without a fresh authoritative FX quote and full reset-scoped broker statement evidence. No model/actor promotion is permitted.

## Next acceptance conditions
Use a new output root only after independent farm recovery evidence. Keep historical v1/v2 failed receipts unchanged. Require bounded IB PAPER read-only requests, complete timestamp/calendar/BID/ASK/MIDPOINT coverage, immutable source hashes, prospective withheld economic evidence, and explicit trader-owner approval before any strategy promotion.
