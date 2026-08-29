# Test strategy

Status: current
Applies to: `tests/`, `tests/python/`, `.github/workflows/core-ci.yml`
Verification: same-revision CI

## Principle

Tests protect trading invariants and fast iteration. A narrative, receipt or earlier green commit is not evidence for a changed head. The exact commit under review must pass its own checks.

## Fast PR lane

### L0 — repository and configuration truth

- shell/Python/JSON syntax and `git diff --check`;
- all current-document metadata and local links;
- no current `moving-main`, deleted command or legacy dependency;
- only supported profiles accepted;
- ordinary Agent examples cannot expose raw place authority;
- unsupported adapters cannot report connected/accepted success;
- install tree contains no stale/unsupported example.

### L1 — deterministic unit contracts

- risk matrix, finite values and strict reduction;
- snapshot generation/epoch/fence/watermark consistency;
- target normalization and permit digest/lifecycle;
- command fingerprint and idempotency;
- protocol framing/schema/result bounds;
- portfolio netting and budget rules;
- research manifest/data-quality/cost calculations.

### L2 — components

- OMS journal durability and replay;
- execution coordinator accepted/duplicate/uncertain paths;
- permit authority atomic consume/replay;
- Tool Registry/Gateway dispatch and capability visibility;
- state store and snapshot capture;
- decision lease/fencing/reconciliation;
- broker lifecycle projector and kill switch.

### L3 — deterministic end to end

```text
Agent-equivalent call -> Gateway -> Execution -> Simulator
-> journal -> venue event -> OMS/state -> read/snapshot
```

Cover target preview/apply/no-op, raw-place denial for ordinary Agent, cancel, flatten, duplicate retry, process restart, stale generation and reconciliation.

## Optional/nightly reliability lane

- ASAN/UBSAN;
- protocol, schema and journal fuzzing;
- crash points before/after journal and before/after send;
- long replay and deterministic parity;
- disconnect, callback correction, duplicate/out-of-order event injection;
- same-fixture latency baseline and p99 regression.

These are optional or scheduled and never disguised as ordinary source-development prerequisites.

## Research validation tests

- no-lookahead and point-in-time fixture;
- timezone/session/calendar boundaries;
- missing/duplicate/out-of-order/changed-same-timestamp input;
- deterministic features/decisions and output digest;
- commission/spread/slippage/delay/impact sensitivity;
- purged walk-forward and embargo boundaries;
- regime/time-of-day/worst-slice stability;
- replay parity after refactor.

## Required commands

```bash
python3 scripts/check_repository_integrity.py
./scripts/dev_core.sh
cmake --install build/core-release --component runtime
```

CI has read-only repository permission. It contains no finalizer, self-approval, self-merge or temporary source-export step at review readiness.
