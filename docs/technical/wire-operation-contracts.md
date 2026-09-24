# Wire operation contracts and recovery

Status: CURRENT
Applies to: native local clients, Gateway–Execution and operator session control

## Framing, values and authority

A Unix stream frame prefixes the body with a four-byte unsigned big-endian byte length. Clients must enforce the transport's configured frame limit before allocating. Connect/request framing and the post-delivery authority-response wait are separately bounded; a caller response timeout is not Execution cancellation and cannot authorize a retry. Server read, queue and response-write phases also have independent deadlines, while an already-dispatched authority completes exactly once. The codecs below do not replace peer UID, session capability, account/domain or risk checks. Parsing a valid request never grants authority. Do not send examples to a Broker service: the golden-vector test decodes memory buffers only.

`HSS1` has four magic bytes followed immediately by TLVs. Each TLV is a big-endian uint16 tag, a big-endian uint32 byte length and that many value bytes. It has no additional numeric version/operation header; operation is text in field 1. Each value is at most 4096 bytes except `TerminalEvidence` and `FinalizationReceipt` (12288). Duplicate tags and truncated values fail. Text validation rejects control bytes; producer-side limits below are byte limits, not Unicode character counts.

`HEX1` has magic, uint16 version **11**, uint16 operation, then the same TLVs. Requests require an exact operation-specific field set. Values are at most 4096 bytes, except command-response `ResultDetail` may be 32768. Response kind is zero. `HEV2` uses the same eight-byte header with version **2** and 4096-byte TLV values. Unknown versions are rejected, never guessed compatible.

All numeric TLV values are decimal **text**. Booleans are exactly `0` or `1`; floating-point values must be finite. Writers use the current codec's formatter. Digests in terminal request fields are `sha256:` plus 64 lowercase hexadecimal digits, not bare hex. The [generated field table](wire-field-reference.md) lists every tag and producer binding; it is not a second manually maintained registry.

## HSS1 exact request families

All requests contain `Operation` and `Token` (nonempty, at most 512 bytes). The following additions are the operation field sets, not suggestions to append arbitrary optional tags.

| Operation text | Additional fields | Bounds / restrictions |
|---|---|---|
| `provision` | TemplateId, AgentId, SessionId, PeerUid, TtlMs | template ≤32; agent ≤128; session ≤256; UID uint32; TTL positive uint64 milliseconds. Deployment policy further bounds TTL. |
| `revoke` | ExpectedGeneration | positive generation, not a new token or TTL. |
| `renew` | ExpectedGeneration, TtlMs | both positive; stale generation cannot renew. |
| `rotate` | ExpectedGeneration, TtlMs, ReplacementToken | positive generation/TTL; replacement token ≤512. |
| `recovery-query` | ExpectedGeneration, TargetCommandId; optionally PaperFinalizationRequired=`1` | positive generation; command ID ≤128; finalization requirement is not proof of completion. |
| `paper-finalize` | ExpectedGeneration, RecoveryId, FinalizationId, ExpectedOwnerSetSha256, ExpectedOwnerCount | IDs ≤128; positive generation; count 1–4096; no receipt or terminal evidence. |
| `paper-finalize-ack`, `paper-terminalize-ack`, `paper-terminal-witness-prepare` | previous finalization fields plus ReceiptSha256 | exact current receipt digest; terminal evidence fields forbidden here. |
| `paper-terminal-witness-ack` | previous acknowledgement fields plus TerminalEvidenceSha256, TerminalEvidence | evidence nonempty and ≤12288; digest canonical. |

The server authenticates the operator separately from Agent token possession. `accepted`, `ownerAuditComplete`, `terminalLatchDurable`, `terminalCurrentEvidenceVerified` and `authoritativeCommandStatus` answer different questions. An accepted lease operation is not a successful order or final flatness proof. Results always contain acceptance, reason and lease generation; recovery/finalization results carry their applicable owner, epoch, count and receipt fields. Use named fields/accessors, never the private `text[]` array layout as a protocol.

## HEX1 exact request field sets

**Context C** = AgentId, SessionId, ToolCallId, Strategy, Account, Venue, ExecutionDomain, AllowCancelAny. Agent/session/tool-call IDs must be nonempty; AllowCancelAny is `0` or `1` and still requires policy authority.

**Identity I** = ExpectedServiceEpoch (nonempty, ≤128 bytes), ExpectedServiceFencingGeneration (positive uint64). Every operation except identity discovery requires I. An identity mismatch requires rediscovery/reconciliation; it does not permit reissuing an uncertain mutation under a new command ID.

**Contract K** = Symbol, SecType, Exchange, PrimaryExchange, Currency, ContractMonth, Right, Strike, Multiplier, TradingClass, LocalSymbol. Required presence does not mean each text field must be nonempty: venue/profile validation owns economic contract validity. Strike is finite numeric text; multiplier remains text for explicit instrument validation.

| Kind | Operation | Exact fields beyond C + I |
|---:|---|---|
| 1 / 9 | PlaceIbOrder / PreviewOrder | K plus Instrument, ExpiresAtMs, ReferencePrice, Action, OrderType, Quantity, LimitPrice, AuxPrice, OutsideRth, TimeInForce, OrderRef, PreviewPermit. |
| 2 | CancelIbOrder | OrderId, Instrument, Side. |
| 3 | QueryCommandStatus | TargetCommandId (nonempty). |
| 4 / 5 / 6 | FenceSessionOwner / ReleaseSessionOwnerFence / ReconcileAuthoritativeState | none. |
| 7 | GetServiceIdentity | **No C, no I, no TLVs at all.** |
| 8 | ReadAuthoritativeState | ReadQuery, Instrument. ReadQuery is nonempty; query-specific validation is in Execution. |
| 10 / 11 | PreviewFlattenPosition / FlattenPosition | K plus Instrument, PreviewPermit. Quantity/side are not caller-selected flatten authority. |
| 12 | RecoveryQueryCommandStatus | TargetCommandId, positive RecoveryIngressFence. |
| 13 | RecoveryAuditOwner | RecoveryIngressFence (uint64; zero is allowed by codec, not a completed barrier). |
| 14 | TerminalizeRecoveryOwner | TargetCommandId, positive RecoveryIngressFence, TerminalPreliminaryReceiptSha256. |

Order expiry is epoch milliseconds. Reference/limit/aux/strike and quantity are finite numbers; codec validity does not bypass final authoritative quote, dimensional risk or quantity limits. Instrument is bounded to 128 bytes and preview permit to 80; preview operations require an empty preview permit. Preview and submission are distinct operations: a successful preview cannot substitute for final risk admission.

Response status, command ID, order ID, reason and detail are typed separately from control/recovery fields. `UNCERTAIN` or a lost response after send requires status lookup and authoritative reconciliation using the original command identity. Do not add retries that allocate new IDs. Events do not substitute for the command result or durable OMS ledger.

## HEV2 requests and cursor recovery

Kind 1 is GetServiceIdentity with **no TLVs**. Kind 2 is Wait with exactly ExecutionDomain, AgentId, SessionId, ExpectedServiceEpoch, ExpectedServiceFencingGeneration, AfterSequence and TimeoutMs. Owner components and service identity must be valid; timeout is 0–30000 milliseconds and sequence is uint64. The wait is bounded and read-only.

The response's service identity, stream epoch, dropped-through sequence, latest sequence and read status must be interpreted together. A history-loss/gap result is not an empty successful event: discard continuity assumptions, read authoritative snapshots and reconcile. A timeout is not proof that no Broker change occurred. A changed service identity requires a new identity handshake; never merge sequences from different stream epochs.

Event sequence is local publication order. EventTimestampMs is milliseconds; order ID is signed; filled/remaining quantities and average fill price are finite numeric text with instrument-specific units. No event timestamp or callback ordering replaces Broker correlation or authoritative refresh barriers.

## Golden examples and failure actions

The complete HEX1 identity-request **body** is `48 45 58 31 00 0a 00 07`; the length-prefixed frame begins `00 00 00 08`. HEV2 identity body is `48 45 56 32 00 02 00 01`. For HSS1, `Operation="revoke"` is tag 1, length 6, followed by those six ASCII bytes; token and positive ExpectedGeneration are still required. These examples are exercised by `tests/protocol_reference_vectors.cpp`, compiled and run by `tests/python/test_protocol_reference.py` without sockets or credentials.

| Failure class | Caller action |
|---|---|
| bad magic, unsupported version, field-set/length/duplicate/type error | reject input; fix producer/consumer contract, not timeout values. |
| stale service identity or lease generation | obtain current identity/status and restore fencing context; do not expand authority. |
| uncertain send or incomplete recovery audit | keep stable command ID; query and reconcile before any new risk. |
| journal budget/corruption or incomplete terminal witness | preserve bytes and tombstones; use the recovery runbook, never delete history to obtain green status. |
| event gap/epoch mismatch | rebuild read state from authoritative snapshots; retain command identity separately. |

## Persistent-format support is separate

Wire versions, OMS schema and encrypted lease layout are separate compatibility axes. See [lease layouts](session-lease-format.md) and [persistence support](persistence-support-window.md). Retiring an old reader requires proof that no supported deployment needs it; source age alone is not evidence. A same-VERSION, distinct-source rollback test certifies only that exact artifact pair and tested persisted states.


### Futures intent fields in HEX1 v11

Place/preview order requests carry `PositionEffect` (tag 42) as part of the
exact field set. The value is empty for the existing stock/FX profile or one of
`OPEN`, `CLOSE`, `CLOSE_TODAY`, `CLOSE_YESTERDAY` for an explicitly
migrated futures proposal. The complete contract identity continues to use tags
14–24. A profile that does not implement futures offset semantics must reject a
nonempty value; dropping or inferring it is not wire compatibility.

The Agent-side HTT1 codec remains protocol version 1 with optional full contract
identity/position-effect fields. Matching-source clients and servers discover
the current schema hash; older decoders reject unknown fields rather than
silently narrowing the request.

## Narrow internal evidence, unchanged wire envelope

`ExecutionControlStatusResult`, `ExecutionOwnerAuditResult` and
`ExecutionTerminalResult` own distinct domain outcomes. Supervisor audit and
receipt helpers consume the audit result only; terminalization consumes the
owner-bound terminal result and one-way witness only. Encoding and decoding
reuse the same terminal invariant.

`ExecutionControlResult` remains a compatibility envelope at the explicit HEX1
v11 and HPT2 persistent-latch boundaries. Server-side widening and client/replay
narrowing are explicit, so audit counters cannot be mistaken for terminal proof
and terminal proof cannot manufacture an audit. No field ID, default, reason
code or journal/lease format changes; distinct wire response versions remain
future protocol work rather than an internal type-safety prerequisite.
