# OMS journal event schema

Status: current
Applies to: `HeptaTrade/oms_journal.*`, `HeptaTrade/execution/`, OMS replay tests
Verification: `canonical-full-suite` on the exact revision

## Purpose

The OMS journal is the durable state transition record used for journal-before-send, idempotency, restart replay and uncertain-command reconciliation. It is JSON Lines; one bounded event is appended per line.

## Current schema

`OmsJournal::kSchemaVersion` is `4`. Readers retain backward-compatible parsing for older additive records; new writers emit v4 fields.

Core fields:

```text
schema_version, event_type, ts_ms, event_id
order_id, req_id/client_req_id, trace_id, request_hash
venue, account, execution_domain, strategy
instrument, side, qty, price, status, risk_code, reason, source
venue_correlation_id
```

Optional broker callback evidence includes callback type, service/connection epoch, request/error IDs, message/reject detail, execution ID, remaining quantity and market-cap price.

## Event semantics

Critical mutation events include normalized intent/command acceptance, send attempt, sent/accepted/rejected result, cancel/flatten attempts and recovery blocks. Exact event names are code contracts and are covered by coordinator/journal tests; documentation must not invent an event that the writer does not emit.

## Durability invariant

A new venue mutation is sent only after its command/send-attempt event is durably appended. A failed durable write poisons mutation authority and blocks new risk. Dedicated execution daemons force synchronous critical writes; asynchronous/batched modes are not inherited from an interactive parent.

## Idempotency and replay

Replay proceeds in journal order. Stable command/request hashes and service-owned venue correlation IDs resolve duplicate retries and uncertain outcomes. A repeated command with a changed normalized payload is rejected; replay never fabricates a broker result absent authoritative evidence.

## File safety

The journal path is a private regular non-symlink file owned by the execution UID with no group/world access. Path/inode/owner/mode drift or a write failure fails closed.

## Tests

```bash
./scripts/dev_core.sh
ctest --test-dir build/core-release --output-on-failure \
  -R 'hepta_oms_journal_durability_tests|hepta_execution_coordinator_tests'
```
