# Application-key Python consumer migration

Status: CONTRACT-FIRST CONTINUATION; implementation and acceptance tracked in the continuation PR
Baseline: main and integration/heptadll-modular-20260920 at 7f24748aeaaa5f32f42293954c85f2b7acd95e48
Reference caller: #106 research/python/hepta_research/gateway.py at acffe4ae84a8e4377fd92b0ac536a865c6f6b6f5
Date: 2026-09-22

## What is already merged

#107, #110, #111 and #112 have already merged Data/Analytics/Replay/Strategy,
selected CSV/BIN/XML and model/portfolio consumers, opaque HSR1 preparation,
and record-bound read-only inspection. This continuation does not recreate
those modules. Earlier successful tests are baseline evidence, not acceptance
of a subsequent patch.

## Remaining concrete consumer

The #106 Python StrategyGateway associates an application key with a normalized
limit intent. Preparation never places an order. Once a send may have happened,
repeated submit calls query the original command; they do not submit it again.
Merely adding native Prepare or Inspect methods did not migrate that application
policy. This continuation targets a runnable Python caller of the existing
NativeStrategyClient and its existing transport, not another wire protocol,
execution service, ledger, broker adapter or strategy engine.

The migration must preserve:

- application-key locking and rejection of changed intents;
- canonical server-issued command IDs and original permits/expiry;
- durable canonical HSR1 request storage before any possible placement;
- a durable conservative possibly-sent marker before the first send;
- status-only recovery after that marker, including transport failure, process
  death and unknown/not-found responses;
- private owner-only filesystem state, bounded input/output, original binding
  and explicit failures on unsafe files or precision loss;
- all existing explicit native same-ID resend APIs and existing tests.

Application-key bookkeeping is not an OMS and never owns positions or fills.
A possibly-sent marker is not evidence that a send or fill actually happened.
A crash after marking but before sending deliberately sacrifices automatic
liveness: recovery queries the original command and does not guess a new order.

## Non-compatible records and domains

This is an explicitly selected source-migration destination, not an alias for
old Python record dictionaries or an unrestricted Decimal API. Existing #106
JSON outboxes and #108 HRO1 records must not be silently converted, recredentialed,
removed or replayed under new IDs. A new canonical application store must reject
legacy records rather than mistake them for an empty store. Numeric inputs must
fit the existing canonical wire domain without silent decimal rounding.
Original record consumers remain retained until their actual owners complete
reconciliation and decide their disposition.

## Delivery and acceptance

The Python caller and a small native command-line adapter belong only to the
opt-in StrategyClientSDK; ordinary production installation must not acquire
research/client artifacts. The adapter calls the existing SDK for preparation,
persistence, validation, submission and inspection; it does not serialize HSR1,
implement a second socket client or obtain broker credentials.

Acceptance must exercise source and installed/relocated consumers, concurrent
same-key calls, intent conflicts, failed preparation, lost responses, marker
write/sync failures, unsafe/malformed files, unchanged request bytes and actual
Gateway/Execution journal send counts. Existing native behavioral and recovery
assertions must remain. Candidate and merge-head CI must be observed separately.
No success is claimed by this contract-first commit.

## Original repository disposition

HeptaDLL-main, its builds/releases/history and #106/#108 references remain
retained. Unknown external/private/binary installations and applicable source
publication rights are not established by a bounded organization code search.
Repository archival requires real named consumer/artifact and publication
confirmation. No visibility, protection, credential, venue or LIVE permission
changes are part of this continuation.
