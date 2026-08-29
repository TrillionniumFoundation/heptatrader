# ISSUE-P0-002 Phase-1 Skeleton (Runnable)

## Scope delivered in this phase

Implemented a **local file append-only OMS journal** (`JSONL`) with startup replay entrypoint.

- Event model (minimum set):
  - `order_intent`
  - `place_sent`
  - `status`
  - `cancel`
  - `reject`
- Append API: `OmsJournal::Append(...)`
- Replay API: `OmsJournal::Replay(callback)`
- Main-flow probes (IB path):
  - order placement path (`order_intent` + `place_sent` / `reject`)
  - cancel path (`cancel` / `reject`)
  - order status callback path (`status`)

File location default:
- `runtime-logs/oms_journal.jsonl`
- override with env: `HEPTA_OMS_JOURNAL_PATH`

## Replay behavior (phase-1)

On startup, `HeptaDemoStrategyTrader` initializes journal and replays all events once.
Current replay action:
- Count replayed rows
- Rebuild minimal in-memory `order_id -> last_status` map for visibility/logging

> This is intentionally minimal to establish durability + replay hook before adding full reconciliation/state machine.

## Validation scripts

- Generate sample journal:
  - `python scripts/gen_oms_journal_sample.py`
- Replay/verify sample or runtime journal:
  - `python scripts/verify_oms_journal_replay.py --journal runtime-logs/oms_journal.sample.jsonl`
  - `python scripts/verify_oms_journal_replay.py --journal runtime-logs/oms_journal.jsonl`

Expected result:
- Required event types are present
- Replay rebuilds object summary without parse failure

## Current boundaries (explicitly NOT in phase-1)

- No broker reconciliation (belongs to ISSUE-P0-003)
- No idempotency-key enforcement across restart yet (tracked by ISSUE-P0-002 phase-2)
- No fill/ack/cancel_ack sub-types yet
- No WAL rotation / compaction / checksum / corruption recovery yet
- No cross-process lock on journal file yet

## Phase-2+ expansion points

1. Add deterministic idempotency key and restart duplicate suppression.
2. Extend event taxonomy (`ack`, `fill`, `cancel_ack`, `risk_block`).
3. Add reconciliation runner against broker open orders at startup.
4. Add rotation + checksum + crash consistency test cases.
5. Promote replay state from map to formal order lifecycle FSM.
