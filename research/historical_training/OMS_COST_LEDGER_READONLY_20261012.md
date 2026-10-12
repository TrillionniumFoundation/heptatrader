# Exact OMS fee attribution — isolated PAPER research (2026-10-12)

## Problem and scope

The live `heptatrader-pnl.service` computes all strategy executions, but
bounds each instrument's JSON *display tail* to 128 fills. An analyst must
not compute commission, per-fill cost, or turnover directly from that tail.

`oms_cost_audit.py` reads the **exact 0600 original OMS journal prefix**
already hashed and recorded by a single PnL snapshot, even if append-only
journal growth has occurred since that snapshot. It replays **the installed
observer's identical Python accounting source** against all frozen events,
validates the accounting period and every fee, execution and component, and
writes a private write-once evidence report. The program never connects to
IB, submits/cancels/replays orders, changes the trading driver, repairs a
prior snapshot, or trains/promotes a model.

This is a second execution of the same ledger accounting logic, *not*
independent broker-statement confirmation. Commissions are IB callback
receipts. Revisions, personal tax, financing, off-account liability, and
a common broker valuation tick remain outside its evidence scope.

## Exact snapshot contract

- `period_id`, `period_sha256`, PAPER execution domain and installed
  `hepta_pnl_accounting.py` bytes must match the existing read-only observer.
- A file is owned by the current UID, regular, nonlinked and private. The
  journal must be the same device/inode, at least as long as the snapshot
  prefix, and its first N bytes must match the snapshot's `journal_sha256`.
  Journal replacement, prefix rewrite, truncation, stale or inconsistent
  snapshot, conflicting execution identity, missing USD fee, or amount
  mismatch fail closed. Later append-only data is **not** admitted.
- Full replay counts must match `execution_count`,
  `execution_details_total`, `command_count`, validated balances,
  realized gross PnL, commissions and strategy totals. The 128-row
  `executions` display tail is never used as the input.
- Report contains only aggregate fill economics and source hashes. In
  particular, it does not export account numbers, tokens, order IDs,
  per-fill prices, original private OMS events, or new live behavior.

## Run

```sh
cd /home/alex/work/heptatrader-oms-cost-ledger-20261012
PYTHONPATH=scripts:research python3 -B -m unittest \
    historical_training.tests.test_oms_cost_audit -v

PYTHONPATH=scripts:research python3 -B -m historical_training.oms_cost_audit \
    --snapshot ~/.local/state/heptatrader/pnl/snapshot.json \
    --period ~/.config/heptatrader/pnl-period.json \
    --journal ~/.local/state/heptatrader/execution/oms-journal.jsonl \
    --installed-accounting \
      ~/.local/share/heptatrader/observer-releases/c42bd15a99fe485cbf34d21b2d211d2793ea4254/hepta_pnl_accounting.py \
    --output ~/.local/state/heptatrader/operator-evidence/oms-cost-ledger-20261012/oms-cost-audit-new.json
```

`--output` **must name a new file**; the command does not overwrite evidence.

## Actual pocket4 research receipt

Read-only actual execution succeeded with 244 EUR/USD and 24 BTC/USD
verified unique fills, zero Broker requests and zero orders. The sealed
receipt on pocket4 is:

`~/.local/state/heptatrader/operator-evidence/oms-cost-ledger-20261012/oms-cost-audit-v1.json`

On this specific frozen journal prefix the observed executed notional and
fees were USD 21,276,052.79 / 486.03 (EUR/USD) and USD 138,271.19 /
235.86 (BTC/USD). The turnover-weighted effective **single-execution**
commission was 0.2284 bps and 17.0579 bps respectively. BTC effective
commission p90 was ~100 bps; partial fills and small notional can skew
the equal-weighted percentiles. These are retrospective fees on the
observed PAPER executions, not guaranteed cost quotes or evidence of an
actionable future edge.

New strict unittest coverage: 16/16 pass, including append-only growth,
prefix tampering, inode replacement, truncation, JSON duplicates, fee
completeness and currency rejection, lost executions, and output
overwrite refusal.

## Hold and next qualified action

1. Keep existing PAPER driver behavior unchanged. BTC already has a
   separate fee-aware entry guard that protects original exit paths;
   verify **pre-trade** rejection and exit reachability before considering
   any new BTC gate. No extra commission cap may block risk exits.
2. For EUR/USD, test *only in a new shadow lane* a frozen minimum
   expected move versus live spread, same-scope commission and a
   predeclared slippage reserve. The original live strategy currently
   lacks an independently certified expected-return forecast; its
   confidence is **not** a cost-covering return estimate. Never convert
   historical outcome or raw confidence into a future edge.
3. Once a prospective shadow action survives precommitted OOS,
   unseen cost-stress and real execution-time quote/fee verification,
   submit it for the original native Ledger/Owner/Witness admission.
   No history-only gate may self-promote.
4. Independently import exact-account Activity/Flex XML and the
   documented reset-opening NAV/flows using the existing bounded
   `hepta_account_activity.py` path. Do not loosen the fixed USD 1 FX
   reconciliation bound on a failed same-broker-time comparison.
   Missing Flex/flow/liability/tax evidence remains `UNKNOWN`.

The local evidence files and historical market datasets are retained.
