# Test strategy

Status: current
Applies to: `tests/`, `tests/python/`, `.github/workflows/core-ci.yml`
Last verified commit: moving-main

## 1. Principle

Tests protect trading invariants and fast iteration. They do not create certification theater. The exact commit under review must build and pass its own checks; a narrative or earlier green commit is not evidence for a changed head.

## 2. Required PR layers

### L0 — static integrity

- shell/Python/JSON syntax;
- `git diff --check`;
- current-document path/reference validation;
- active source cannot include/link `legacy/`;
- ordinary Agent env examples cannot expose raw place capability;
- unsupported venue adapters cannot report connected/accepted success.

### L1 — deterministic unit tests

- risk rule matrix and finite-number rejection;
- strict reduce-only/no-cross-zero;
- command fingerprint and idempotency;
- protocol framing, schema and result bounds;
- session capability/environment rules;
- quote/snapshot freshness and generation consistency.

### L2 — component tests

- OMS journal durability and replay;
- execution coordinator accepted/duplicate/uncertain paths;
- tool registry and Gateway dispatch;
- snapshot store and refresh;
- decision lease and execution fencing;
- broker lifecycle projector and kill switch.

### L3 — end-to-end deterministic tests

Agent/MCP-equivalent request -> Gateway -> Execution -> simulator -> event/OMS projection. Cover place/intent, cancel, flatten, retry, process restart and reconciliation.

### L4 — optional/nightly reliability

- ASAN/UBSAN;
- protocol and journal fuzzing;
- crash points around journal-before-send;
- long deterministic replay;
- broker disconnect/correction/duplicate-event fault injection;
- latency baseline and p99 regression.

## 3. Risk test matrix

Every rule has allow, exact-boundary, over-boundary, NaN/Inf and stale/unknown cases. Important cross-products include:

- above-limit account + proven reduction;
- reduction that would cross zero;
- active-order/rate cap during cancel or flatten;
- stale quote with new exposure versus safe exit;
- position generation change between preview and apply;
- same command ID/same payload versus same ID/changed payload.

## 4. Research tests

- no-lookahead fixture;
- timezone/session/calendar boundaries;
- missing/duplicate/out-of-order data;
- deterministic feature/decision golden files;
- commission/spread/slippage/latency costs;
- purged walk-forward and embargo;
- regime/time-of-day stability;
- replay parity across unchanged code and manifest.

## 5. CI contract

The PR workflow is read-only and bounded. It runs:

```text
entry-point and repository-integrity checks
Release core configure/build (IB disabled)
core CTest
Python contract tests
minimal runtime install smoke
```

It has no `contents: write`, no `pull-requests: write`, no finalizer and no self-merge step.

## 6. Failure handling

A failed check reports the concrete command and reason. Do not add a second workflow that checks whether “all gaps are closed.” Fix the implementation or the smallest relevant test. Temporary diagnostics are removed in the same branch before review readiness.
