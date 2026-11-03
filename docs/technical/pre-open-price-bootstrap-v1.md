# Durable PRICE bootstrap before PAPER FX OPEN

## Scope and version boundary

`PRICE_PERSISTENCE_300S_PREOPEN_V1` is an explicitly selected, separately pinned
producer protocol. It supplies a causal **PRICE persistence baseline** for an
actual original EUR.USD decision request before allowing the existing native
OPEN preparation. Its point forecast is the last real past bid/ask midpoint,
at the request start plus 300 seconds. The unit is USD per EUR price, not USD
profit. It has no parameter updates, learned Value head, action-selection
authority, Value coverage credit, or efficacy claim.

The Current100k `FIRST_32_CHRONOLOGICAL_CLOSED_NEW_PRODUCER_V1` policy is unchanged.
Those 32 rows govern the registered Value training and evaluation protocol;
they are not an algorithmic prerequisite for this separately identified PRICE
baseline. This protocol does not convert its records into Value predictions,
backfill an old trial, or claim that persistence improves trading outcomes.

The default remains the existing behavior. Enablement adds
`hepta_price_bootstrap_gate.py` to the exact runtime inventory and this manifest
field:

```json
{
  "price_bootstrap_gate": {
    "schema": "heptatrader.pre-open-price-bootstrap.v1",
    "profile": "PRICE_PERSISTENCE_300S_PREOPEN_V1",
    "receipt_root": "/absolute/private/receipt/root"
  }
}
```

The release must use `verified-bytes.v1`, PAPER, and EUR.USD. The manifest,
runtime module hashes and explicit selection identify the new behavior. This
is a new producer SHA. An old producer's 10/32 population remains historical
evidence under its original identity; it cannot be silently mixed with the new
producer or rewritten as having used this gate. Its installed v2 timer and
signed plans are not modified by preparing this release.

## Exact input and authority

The existing `Capture` receives the original infer CLI invocation, literal
prompt, controls, history, response envelope and decoded decision. The gate
accepts only its single-call context, actual returned decision object, and
unchanged decision/request digests. Equal dictionaries, the latest journal
entry, a previous request, recovered state, or a caller-supplied prediction
cannot authorize an OPEN. The in-memory admission is single-use, even when
native preparation rejects it. A restart does not restore OPEN permission.

The optional attribution Journal is asynchronous. `emit()` success is not a
durability acknowledgment. The new private files below are a distinct durable
authority, explicitly identified as such; they do not assert successful
Journal publication or a Value role signature. A Journal failure after Capture
has observed the request does not erase these complete private inputs. If the
Journal/Capture cannot be installed, the launcher installs OPEN interception
first, and the missing exact context rejects OPEN. CLOSE remains available.

Only flat decisions satisfying the original `entry_candidate_allowed()`
predicate are sealed. HOLD, insufficient-confidence and nonflat observations
do not produce synchronous PRICE seals or rejection files. A later attempted
OPEN without its own valid captured receipt still fails closed. The baseline
does not create requests, change the actor output, introduce trading
opportunities, or replace any native quote/risk check.

## Durability and causal order

Files live under `<receipt_root>/<producer_manifest_sha256>/<request_id>/`.
Each is an exclusive, private, immutable-by-protocol JSON file, limited to
512 KiB. It is fully written to a private temporary file and file-synced,
then published using Linux `renameat2(RENAME_NOREPLACE)` and
containing-directory `fsync`. There is no overwrite fallback if atomic
no-replace publication is unavailable. A half-written temporary file cannot
become authority or block retry of the final record. Creation of each new
directory also syncs its parent. Files have mode
0400, one link, and the current owner. Reads reject symlinks, changed file
identities, changed bytes, invalid ownership/mode and oversized records.

| File | Evidence and timing |
| --- | --- |
| `input.json` | Complete real request, raw response envelope and decoded decision, with their exact hashes. |
| `prediction.json` | Explicit PRICE baseline, last real past quote, target time, request/decision/producer/implementation hashes, and truthful computation time. |
| `seal.json` | Input and prediction file hashes and the time after both payload files and directories completed `fsync`. |
| `admission.json` | Same identity, sealed hashes, original signal expiry and the actual post-seal completion time; written only after rereading and verifying the sealed files. |
| `command-binding.json` | Actual native command ID, complete original PREPARED command and forecast receipt, hash of the original durable PREPARED state, and its post-`fsync` completion time. |
| `local-non-send.json` | Present only for a locally unsubmitted command that is being safely reconciled; retains the original command and full binding independently of mutable driver state and Journal availability. |

The OPEN path is:

1. Capture the real request and original response/decision. Validate all history
   times as past-only and all bid/ask/mid values as finite, positive and
   internally consistent. Preserve the original quote and request cutoffs.
2. Compute persistence and durably write input, prediction and seal. Only after
   file and directory sync return is the seal-completed timestamp sampled.
3. At the lowest `prepare_send(..., "OPEN", ...)` boundary, require the same
   object, unchanged digests, producer, period, side and original entry
   eligibility. Reread the exact sealed files. Consume this request and write
   and verify admission. Sample its completion time after `fsync` returns.
4. Wait, within the existing operation budget, until the clock is strictly
   later than admission completion. Call the original native prepare without
   altering its preview, quote, risk fields, quantity, permit or expiry.
5. Before the original PREPARED state write, attach
   `pending.price_bootstrap_forecast` and a digest-bound
   `pending.price_bootstrap_pre_submit_boundary`. Run the original durable
   state write, then recheck the original deadline and clock. Seal the actual
   command binding and recheck again.
6. Continue the original durable POSSIBLY_SENT transition, checking the same
   deadline both before and after this final pre-transport fsync. Reverify the
   actual command binding and original deadline immediately before entering
   the single original submit call. Mark transport-entered in memory before
   that call; any exception or uncertain reply thereafter stays on the original
   unknown-send/Broker recovery path. A later POSSIBLY_SENT write from an
   uncertain reply cannot be mistaken for the first pre-transport transition.

Thus input and prediction are durably complete before native preparation,
not merely before a queued observer event. The actual command association
survives normal pending-state reconciliation and lost asynchronous journal
events. No extra post-submit write is necessary to establish that association.

The gate uses the original `ModelWorker` object, its unchanged quote-based
monotonic deadline and cancellation, the original 60-second signal limit, and
an additional 2-second budget per seal/admission/prepared-binding operation.
It rejects backwards wall or monotonic clocks and checks again after durable
I/O. It never extends a signal, plan, worker deadline or native permit, and
never backdates a receipt. A kernel I/O call itself is not interruptibly
bounded by Python; if it stalls, no OPEN can advance through that call, and a
late return fails the budget check. These files describe a baseline protocol,
not a deadline performance guarantee for the learned Value worker.

## Failure and recovery

Before native preparation, missing context, missing/invalid past quotes,
identity drift, stale signal, timeout, failed write/sync/read verification,
wrong producer or reuse all reject this OPEN. Failure records include the
actual request ID/SHA when available. An unknown context is explicitly unknown;
no nearby request is invented. Disk failure may prevent the failure record
itself from becoming durable; the raised error and stderr retain known
identity and do not claim a successful seal.

A failure after native prepare but before entering submit is **local**, not a
native rejection. The actual command is retained with
`LOCAL_PRE_SUBMIT_BLOCKED`, its original receipt/boundary, and an explicit
phase-bound `transport_call_entered: false` marker. This includes an expired
deadline discovered after the first POSSIBLY_SENT fsync returns. If the local
marker cannot be saved, the state that actually remains on disk determines
recovery. A durable POSSIBLY_SENT remains uncertain under the original
recovery and is never silently downgraded. A remaining durable PREPARED can
use its original pre-submit boundary after restart. If no state write can
complete, no durability is claimed:
known command/request identities are included in the failure output and no
submit occurs. The gate never retries submit and never prepares a replacement
command for that decision.

The local recovery branch accepts only durable PREPARED or
LOCAL_PRE_SUBMIT_BLOCKED with the exact boundary/receipt and no submit, Broker
ID or reconciliation evidence. It rereads all private inputs and hashes and
also requires the explicit transport-not-entered marker for a local block. It
requires the exact snapshot object just returned by the original native
`snapshot()` implementation. The snapshot call and its age must both be under
2 seconds; it must be authoritative, complete, have unchanged position and no
active orders or row for this original command. It writes and syncs an
immutable local-non-send archive before clearing pending. Counts are unchanged.
This archive does not claim a native rejection and is not a new order.

A process killed after the original PREPARED fsync but before command-binding
write can use the same discard-only path. The actual original PREPARED record
proves that the required durable POSSIBLY_SENT transition was not completed.
Neither absence of Broker rows nor a native expired/rejected status alone
authorizes this path. A POSSIBLY_SENT, acknowledged, partially filled or
otherwise uncertain command always uses the existing Broker recovery. CLOSE
and legacy commands without this new boundary also use the original path.

Failed or stale recovery proof leaves the original pending command intact.
An existing archive is verified and synced again on a retry; clearing state
does not require manufacturing a new identity. Release rollback/activation
must use the existing no-pending and authoritative-flat maintenance rules;
do not remove the recovery module while its local pending command remains.

## Preparation and acceptance

From a committed, reviewed checkout, as the ordinary service owner:

```sh
python3 -B scripts/install_decision_attribution.py \
  --instrument EUR.USD --prepare-upgrade --price-bootstrap-gate
```

This prepares the normal immutable candidate and new manifest; it does not
read or change live position/state/ledger, activate a unit, acquire a trading
lease, send an order, or alter existing plans. Repeating preparation for the
same source/selection produces the same candidate. Ordinary future upgrades
preserve an already selected gate; the API has no implicit disable option.

Before activation, use the existing prepared-driver verifier and normal
maintenance/zero-authoritative/no-pending activation workflow. Review the new
producer SHA and its impact on Current100k source registration explicitly.
Stage a compatible, newly bound consumer source when needed; its inventories
include this new module. Do not hot-swap an old signed plan or silently change
the old 32-row policy. The candidate is not proof of live deployment.

Local tests exercise actual Capture, ModelWorker, driver state I/O, native
prepare/submit ordering with fixture transport, a real SIGKILL after PREPARED
fsync, identity mismatch, original deadline/clock failures, private authority
with lost Journal events, local versus unknown-send recovery, manifest
preparation and launcher failure behavior. Fixture native transport never
places an order. Hardware acceptance still requires isolated target-host
tests and normal staged activation; after activation, the first natural
request must demonstrate real request/producer/command identity, full fsync
ordering, latency under the original limit, and recoverable command tracing.
No learned performance or live P0 completion follows from local tests alone.

Records are bounded individually and in-memory contexts are capped at 32.
Durable receipts are retained for audit; this patch adds no automatic deletion
of command evidence. Operational retention can be designed separately after
actual volume is known.
