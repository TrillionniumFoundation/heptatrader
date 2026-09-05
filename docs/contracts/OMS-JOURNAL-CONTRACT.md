# OMS Journal 与命令生命周期 V3

Status: current core contract; physical journal records default to schema version 4
Applies to: OMS, Execution, state projection and replay
Verification: journal durability, crash/replay, idempotency and migration tests
Authority: OMS durability authority

## Contract identity and implementation boundary

`hepta.oms-journal.v3` is the inter-module durability/lifecycle contract identifier. It is not the value of every physical record's `schema_version`. `OmsJournalEvent::schemaVersion` and `OmsJournal::kSchemaVersion` currently default to **4**; the v4 record adds optional broker callback diagnostics. Do not rename the contract merely to make these two independently versioned objects numerically equal.

The physical writer and reader are `OmsJournal` in `HeptaTrade/oms_journal.h` and `HeptaTrade/oms_journal.cpp`. Execution owns command admission, payload conflict checks, idempotency, fencing, venue submission and reconciliation. The generic journal does not implement those domain decisions: appending the same `event_id` twice produces two replay callbacks. A journal replay count is not a count of distinct accepted orders.

Durable command handling must bind command ID, normalized payload digest, owner/domain, epoch/fence, snapshot/permit reference, acceptance time, operation, status, reason, venue correlation and schema version. These are Execution-level identity requirements, not a claim that every item is a separate column in the generic `OmsJournalEvent` structure. Preserve the domain's command/replay projection when changing record fields.

## Physical record format and fields

The current format is one JSON object followed by one LF byte per record, in file append order. There is no binary length prefix, per-record checksum, hash chain or digital signature. An independently valid JSON object is not thereby an authenticated command or broker observation.

| Field group | Current physical keys and behavior |
|---|---|
| Version and event | `schema_version`, non-empty `event`, `ts_ms`, `order_id`; timestamps are integer epoch milliseconds, not a global ordering authority. |
| Request identity | `req_id`, `client_req_id`, `trace_id`, `event_id`; the writer prefers non-empty `req_id`, otherwise `client_req_id`, and writes that selected value to both request aliases. |
| Domain and provenance | `execution_domain`, `request_hash`, `venue_correlation_id`, `risk_code`, `venue`, `strategy`, `account`, `source`; their domain validity is enforced by Execution, not by string presence. |
| Order observation | `instrument`, `side`, `qty`, `price`, `status`, `reason`. The physical compatibility representation uses finite `double` values formatted with the classic locale and eight fractional decimal digits; it is not the canonical fixed-point risk boundary. |
| Broker diagnostics | `broker_callback_type`, `broker_service_epoch`, `broker_connection_epoch`, `broker_request_id`, `broker_error_code`, `broker_message`, `broker_advanced_order_reject_json`, `broker_why_held`, `broker_execution_id`, `broker_remaining_quantity`, `broker_market_cap_price`. Advanced reject JSON is stored as an escaped string, not as an embedded authority object. |

`Append` rejects an empty event type and non-finite quantity, price, remaining quantity or market-cap price. Strings are escaped by `EscapeJson`; selected control characters are replaced with spaces. Callers must not use arbitrary control-character payloads as byte-exact identity. A successful append alone does not validate order shape, ownership, fixed-point precision or venue authorization.

## Pinned file initialization

`Init(path)` is one-shot for a journal object. It opens the journal read/write and append-only, requests close-on-exec and no-follow where supported, and creates a new leaf with mode `0600`. Existing and opened objects must be regular files owned by the effective UID, with exactly mode `0600`, one hard link, and matching device/inode identities. A non-empty file must already end in LF; an unterminated tail prevents initialization rather than being silently truncated.

Initialization synchronizes the file with `fdatasync` and the parent directory with `fsync` before returning success. These system calls express the host-filesystem durability boundary; they are not remote replication or proof of a particular storage device's power-loss behavior. This leaf/path binding is not a claim that the journal uses the separate qualification receipt reader's retained-descriptor traversal for every parent component. Deployment must keep journal directories in the Execution trust domain.

## Append, buffering and the durable commit point

`Append` holds the journal's mutex, validates the pinned file/path binding and constructs a JSON line. Its return value has different meanings for different configured paths:

| Path | Meaning of successful `Append` |
|---|---|
| Non-critical event with buffering or async enabled | Accepted into memory, or written without an immediate data sync. It is **not** an independent durable-command acknowledgement. |
| Critical event with `syncCritical=true` | All older queued/buffered records are drained first; the complete new record and LF are written; path identity is checked; `fdatasync` succeeds before return. |
| Critical event with `syncCritical=false` | The generic API can queue or write without that synchronous durability guarantee. This is not an admissible configuration for venue-mutation authority. |
| Failed write, invalid identity or failed data sync | Return failure. A partial or complete record may already exist; failure never proves that zero bytes reached storage. |

`WriteLineToPinnedFileLocked` retries interrupted writes and sync operations and handles short writes. It rechecks identity around the write, marks the writer poisoned on detected identity/write/sync failures, and records diagnostic failure counters. A poisoned writer must not be cleared in place to resume risk-increasing work. Preserve the suspect journal, stop admission, and recover through the owning domain's procedure.

The critical synchronous barrier always drains older asynchronous records. `HEPTA_OMS_CRITICAL_FLUSH_QUEUED=0` cannot disable this ordering requirement. After the barrier's `fdatasync`, earlier writes to the same pinned file have also reached that synchronization boundary.

Current environment inputs are `HEPTA_OMS_BATCH_SIZE` (default 8), `HEPTA_OMS_FLUSH_INTERVAL_MS` (default 250), `HEPTA_OMS_ASYNC_FLUSH`, `HEPTA_OMS_SYNC_CRITICAL` (default true), and the compatibility knob above. The generic numeric configuration parser falls back to defaults for invalid numeric inputs; this is not a substitute for deployment configuration validation. Neither batch size nor flush interval is a hard queue-capacity limit.

## Execution command lifecycle and crash windows

```text
received -> rejected
received -> durable-accepted -> send-attempted
         -> acknowledged / partially-filled / filled / cancelled / rejected
         -> uncertain -> reconciled-terminal or terminal-latched
```

A successful durable command record must precede the first venue send. Same command ID with the same normalized payload is resolved through the durable Execution command projection; the same ID with a different payload conflicts. Neither a socket acknowledgement nor a generic journal event independently proves final venue truth.

| Failure window | Required recovery interpretation |
|---|---|
| Before durable admission | No new venue send is authorized by this attempt. Do not turn a buffered append into an acceptance receipt. |
| During write or `fdatasync` | Admission is not proven. Stop sending; retain any partial/full record and follow strict journal recovery. |
| After durable admission, before a proven venue outcome | Reconstruct the same command identity. A restarted process must not invent a new command ID and blindly send again. |
| After a venue send but before acknowledgement is durably projected | Treat the outcome as uncertain; query/reconcile broker orders and executions using stable service-owned correlation. |
| During callback projection or terminal reconciliation | Rebuild from durable records and authoritative venue observations; unresolved divergence keeps new-risk admission closed. |

Cancellation acknowledgement and fill can race. Partially filled or cancelled orders are resolved by the Execution/venue lifecycle, not by sorting timestamps or assuming the last arbitrary JSON record is final. Legitimate safe-exit operations retain their own validated authority; uncertain accounting does not authorize a weaker order path.

## Replay, corruption and callback behavior

`Replay` drains queued/buffered records, synchronizes the file, snapshots its size, reads the pinned descriptor with `pread`, parses every non-empty LF-delimited record and validates path binding again. It collects the events before invoking any callback. Parse/read/binding failure returns `-1` without invoking a valid prefix's callbacks; success returns the number of physical records.

Callbacks run in append order **after releasing the journal mutex**. This permits reentrant diagnostic access without retaining the journal lock across caller code. Callback exceptions and side effects are not rolled back by this API: all-or-none parsing is not a transactional guarantee for the caller's projection. Records appended after the captured replay boundary are not part of that replay batch.

A missing final LF prevents `Init`; a blank line, malformed JSON object, missing event type or invalid parsed range makes replay fail. There is no automatic torn-tail truncation, skip-bad-record mode or online repair. Preserve the original file for diagnosis and recover using a separately reviewed backup/reconciliation procedure rather than editing the active journal until it parses.

### Typed record decoding and compatibility

Execution Runtime 1.1.0 replaces the syntax-check-plus-substring extraction with
`JournalRecordReader`, a cursor-based decoder for the **33 registered scalar
fields** above. It is a journal codec, not a general-purpose JSON library. The
physical writer, its version-4 default, eight-digit floating representation and
existing control-character normalization are unchanged.

| Input condition | Reader behavior |
|---|---|
| Whitespace, ordering and escapes | Accept JSON spaces/tabs/CR around tokens and any field order. Decode standard string escapes and valid Unicode surrogate pairs; equivalent escaped keys map to the same field. LF still separates physical records, so multiline pretty-printed objects are not JSONL records. |
| Duplicate keys | Reject, including duplicates revealed by escape decoding. A fixed field-bit mask tracks the known field set; no first/last-value rule is used. |
| Numeric types | The six integer fields require integer tokens in their destination type's range, without fraction/exponent notation. `broker_connection_epoch` uses the full unsigned 64-bit range; signed fields retain their declared signed ranges. The four floating fields accept JSON decimal/exponent notation, use the classic locale and reject overflow, non-finite values and nonzero tokens rounded completely to zero by underflow. |
| Missing versus invalid | An absent optional field retains its documented default. A present null, boolean, string-for-number, out-of-range or otherwise malformed field rejects the record; it is never replaced with zero or a sentinel. Domain-required fields are still enforced by Execution. |
| Versions and aliases | Read the registered field union for versions 1 through 4. A missing version denotes historical version 1. Reject future/invalid versions. A missing or empty request alias is copied from the other; two nonempty unequal aliases reject. |
| Nested/unknown fields | Reject unknown keys, objects and arrays. Embedded broker advanced-reject JSON remains a string. Data inside strings/nested objects cannot be extracted as top-level order identity. |
| Unicode | Accept well-formed UTF-8 and valid escaped Unicode scalars, including escaped control characters. Reject malformed UTF-8, overlong encodings, out-of-range scalars, lone surrogates and raw unescaped controls. No Unicode normalization beyond escape decoding is applied. |

`ParseJsonLine` builds a temporary event and publishes it only on complete record
success. `Replay` retains its existing whole-batch validation before callbacks.
The reader does not authenticate the producer, verify the economic meaning of
quantities, or detect syntactically valid fraudulent/corrupted observations.

### Single-record resource boundary

`OmsJournal::kMaximumRecordBytes` is **1,048,576 JSON bytes excluding LF**.
`Append` bounds the sum of the 23 supplied text fields before constructing the
record and validates their UTF-8. It then checks the encoded record size before
queueing, flushing older records or writing the new record. An over-limit,
invalid-UTF-8 or future-version input returns false without discarding existing
queued work or poisoning an otherwise healthy writer. The existing normalization
of nonpositive caller-side schema versions to version 4 is retained.

`Replay` checks each LF-delimited length before copying/decoding and rejects an
unfinished line once it exceeds the same bound. Its incremental input string can
transiently contain the limit plus one 8,192-byte read chunk. Rejection preserves
the file and emits no callbacks, including for an earlier valid prefix. This is
a record/decoder bound, **not a total queue, whole-replay, RSS, disk or latency
limit**. Allocation failure is not turned into a hard process-memory guarantee.

### Upgrade and rollback

Current writer-generated records within the bound retain the same representation;
`BuildJsonLine` and `EscapeJson` are unchanged. In particular, the existing writer
still normalizes selected control characters rather than promising arbitrary
byte-exact control-character round trips. The decoder now interprets valid
escaped controls correctly when they occur in an input record.

Logs containing formerly tolerated unknown fields, ambiguous aliases, invalid
UTF-8, malformed numeric fields or oversized records require explicit
classification and authoritative reconciliation before upgrading. Preserve their
original bytes; this change supplies no automatic rewrite, skip or repair mode.
Do not roll back to the old substring reader as a way to make rejected data pass:
that reader may misinterpret whitespace, escapes and unsigned epoch values.
Restoring an older binary requires its own compatible source/log/configuration
selection and recovery review. No runtime promotion is implied by this codec
repair. The JSON syntax reference is RFC 8259:
`https://www.rfc-editor.org/rfc/rfc8259.html`.


## Verification and explicit limits

| Requirement | Direct implementation / assertion evidence |
|---|---|
| File identity, permissions and torn-tail rejection | `OpenPinnedFileLocked`, `ValidatePinnedPathLocked`; `TestPathReplacementPoisonsBeforeWriting`, `TestMissingPathPoisonsBeforeWriting`, `TestSymlinkReplacementPoisonsBeforeWriting`, `TestUnsafePermissionsLinksAndTornFilesFailClosed` in `tests/oms_journal_durability_tests.cpp`. |
| One-shot init and intact original journal | `Init`; `TestRepeatedInitFailsWithoutDisturbingOriginal`. |
| Callback-atomic parse and reentrant diagnostics | `Replay`; `TestStrictReplayIsCallbackAtomicAndReentrant`. |
| Older queued records precede critical sync records | `Append`; `TestAsyncCriticalWritesPreserveAppendOrder`. |
| Buffered append is not a durable acceptance | `Append`, `WriteLineDirect`; `TestBufferedAppendIsNotDurableUntilCriticalBarrier`. |
| Record version and request-alias compatibility | `BuildJsonLine`, `ParseJsonLine`; `TestRecordVersionAndRequestAliasRoundTrip`. |
| Whitespace, escaped fields and historical versions | `JournalRecordReader`, `ParseJsonLine`; `TestTypedWhitespaceUnicodeAndHistoricalVersions`. |
| Invalid fields and valid-prefix callback isolation | `ReadInteger`, `ReadDouble`, `ReadString`, `Replay`; `TestInvalidFieldsNeverBecomeDefaults`. |
| Every registered key rejects duplicates | `Mark`, `ReadField`; `TestEveryPhysicalFieldRejectsDuplicateKeys`. |
| Full unsigned epoch and text-field mapping | `ReadInteger`, string-field bindings; `TestAllTextFieldsAndIntegerLimitsRoundTrip`. |
| Record capacity and non-destructive append rejection | `ValidJournalStrings`, `Append`, `Replay`; `TestRecordSizeBoundariesAndRejectedAppendPreserveState`. |
| Locale-independent numeric decoding and signed endpoints | `ReadInteger`, `ReadDouble`; `TestNumericTokensAreLocaleIndependent`. |
| Generic replay does not implement command deduplication | `Replay`; `TestJournalReplayDoesNotInventCommandDeduplication`. |
| Committed critical record survives process exit without destructors | `tests/oms_crash_replay_tests.cpp`, which synchronizes parent/child through a pipe and verifies the recovered record after `_exit`. This is not a power-cut storage test. |

Command idempotency, venue mutation gating and uncertain outcome handling additionally require `tests/execution_coordinator_tests.cpp`; their success must be checked on the same revision rather than inferred from the journal tests.

The generic journal currently has no explicit byte/record limit for its in-memory async queue or entire-file replay vector, no segment rotation/compaction protocol, and no authenticated remote backup or replication. Batch thresholds are not resource bounds. Deployment sizing, total-capacity enforcement, corruption recovery and target-storage fault qualification remain distinct engineering work; this document does not close them by describing the current implementation. Any future record reinterpretation or schema-major migration requires writer/reader compatibility rules, golden old/new records, restart/replay tests and a rollback procedure.

No journal test, generated document or hosted workflow grants IB PAPER or LIVE authority. The protected exact-artifact qualification requirements remain unchanged.
