# Quote diagnostics and recovery evidence, 2026-11-21

## Outcome

A bounded read-only Pocket4 probe reproduced the currently observed failure:
the installed native quote command exited with code 4, emitted zero stdout
bytes, and emitted exactly `SOCKET_CONNECT_FAILED\n` on stderr. The installed
collector parses stdout before examining the exit status, so it reports
`QUOTE_TOOL_JSON_INVALID` for that pre-envelope transport failure.

The diagnostic repair already present in this Draft preserves the native
failure code while continuing to reject the response as a quote. No additional
acceptance rule, model update, runtime deployment, service change, or trading
instruction is introduced by this document.

This observation does not reconstruct every historical failure that discarded
stderr. It establishes this failure mode from a new, retained real response.

## Source identity and scope

The reviewed repository base is
`ffec655e69c610e0c4af41c00e3565e84edeb940`.
The existing code-and-test head is
`fe484dba616e093876c64a5444ade80f015e0ea6`.

The installed collector has SHA256
`f91d3d81b517d53958103da79a96fc9c5fd86916bc0718e1199526fabcc3c52f`.
The configured execution image has SHA256
`b18eb6e57e421803442927c0f81312d13bf5c13423f50dfd3b38c753e29b3370`;
its retained build receipt names source
`9682ccade08f936c1ffe48952e3036d06244a684`.
The actual image readback matches that receipt. The relevant catch-up and
runtime-state source blobs at that revision are identical to those at the
reviewed repository base.

Applicable `AGENTS.md` lookups at the repository root and the
`research/neuron_learning/laya_cell` ancestors returned no file at that base.
The change is a research document on the existing Draft branch.

## Reproduced dependency chain

The service and socket observations explain why a running process was
insufficient evidence of quote readiness:

1. Execution repeatedly rejected startup with
   `IB_FX_CASH_RESTART_CATCHUP_EXECUTION_INVALID`.
2. The gateway repeatedly reported
   `TOOL_GATEWAY_EXECUTION_EVENT_READINESS_TIMEOUT:connect failed`.
   Its process could appear active while waiting for that prerequisite.
3. The expected tools and supervisor sockets were absent at the bounded
   read-only filesystem observation.
4. The original native `market.get_quote` invocation failed before producing
   a stdout envelope.
5. The installed collector converted the empty stdout into a JSON error.

The existing parser change is in
[`shadow_source.py`](laya_cell/shadow_source.py). Stderr remains diagnostic:
it cannot supply quote data, renew a timestamp, or make a failed process
acceptable. A successful process must still satisfy the original authority,
instrument, subscription, numerical, and strict expiry conditions.

## The catch-up refusal is correct

The complete configured OMS journal was read within a separately recorded
64 MiB bound, after the initial 8 MiB proposal was explicitly extended.
The observed file was 53,092,563 bytes and contained 47,519 JSON records.
Its identity was stable during the read. The immutable baseline and restart
checkpoint passed their recorded proof and anchor checks.

After the checkpoint, the original CASH owner had one relevant accepted
submission with these retained lifecycle events:

- Local broker acceptance.
- A service-owned sent record.
- A zero-filled `PreSubmitted` status with positive remaining quantity.

There was no matching execution or authoritative `Cancelled` /
`ApiCancelled` terminal callback. Later farm error and recovery notifications
were not order-terminal evidence.

This reaches the final accepted-without-fill-or-zero-fill-cancellation check in
[`ib_paper_fx_cash_restart_catchup.cpp`](../../HeptaTrade/execution/ib_paper_fx_cash_restart_catchup.cpp).
The existing refusal must remain. Replacing `PreSubmitted` with a cancellation,
dropping the original submission, rewriting the checkpoint, or assuming an
absent fill is a zero fill would change the evidence.

## Isolated replay

The exact pinned production catch-up implementation was compiled in an offline
harness. Its event, baseline, and context declarations were extracted unchanged
from the pinned headers, avoiding unrelated daemon dependencies. This checks
the pure catch-up function; it is not a deployed daemon or complete production
recovery qualification.

The retained, pseudonymized three-event input reproduced the refusal and left
every baseline field unchanged. Twenty-seven cases passed in total:

- The real retained refusal.
- Synthetic valid cancellations and a synthetic full fill.
- Broken identity, epoch, side, quantity, timing, callback, and terminal fields.
- Partial-fill and fill-versus-zero-fill-cancellation conflicts.

Synthetic positive cases are implementation checks, not new broker facts or
financial labels.

An independent isolated copy of the installed collector received the retained
real exit code and output. The original parser returned
`QUOTE_TOOL_JSON_INVALID`; the repaired parser returned
`SOCKET_CONNECT_FAILED` with `STDERR_DIAGNOSTIC` provenance. Six parser cases
passed, including continued rejection of stderr quote-shaped content and of
a nonzero exit paired with quote-shaped stdout.

The existing
[`test_native_quote_diagnostics.py`](laya_cell/tests/test_native_quote_diagnostics.py)
already exercises the same exit-4, empty-stdout, bounded diagnostic-code
transport contract using `DISCOVERY_LIST_REJECTED`. The new real observation
is retained as evidence rather than adding a duplicate string-substitution
test.

The full ledger acquisition used 0.350 CPU seconds and 1.796 execution wall
seconds. The local native compilation/replay and parser replay used 2.004 CPU
seconds, including compiler children, and 2.006 wall seconds. An earlier
metadata directory scan reached its 6,000-entry bound and failed; that failed
attempt is retained. Tool transport latency and unmeasured infrastructure
costs are recorded separately and are not assumed to be zero.

## One independent read-only broker snapshot

Eight retained broker snapshots in the known recovery locations were inspected.
Their own observation times were October 8–16, before the unresolved November
submission. They could not supply its later terminal disposition.

A single new snapshot was then admitted under a frozen contract with a
distinct non-owner client, a pinned interpreter, pinned source and SDK files,
a four-second CPU soft limit, a five-second CPU hard limit, a 30-second wall
limit, and 256 MiB memory limit. Source was placed in a new private directory;
bytecode writes were disabled and a previously absent cache prefix was used.

The unchanged
[`hepta_broker_recovery_snapshot.py`](../../scripts/hepta_broker_recovery_snapshot.py)
enforces exact outbound read frames. It allows its own account/position
subscription cleanup, but no order placement, order cancellation, global
cancel, quote subscription, or full execution startup.

The one run completed with all seven End barriers:

- Open orders.
- Completed orders.
- EUR executions.
- BTC executions.
- Positions.
- Currency ledger.
- Account aggregate.

It returned 447 account-value records, zero late events, no errors, and no
open-order, completed-order, execution, or position records. Eleven allowed
wire frames were sent; order sends and quote subscriptions were zero. The
period, complete original journal, and FX checkpoint were unchanged across
the snapshot. The run used 0.270 CPU seconds and 2.451 execution wall seconds.

The reader explicitly reports
`API_AVAILABLE_CURRENT_DAY_NOT_ALL_HISTORY` and
`broker_history_complete=false`. Empty current-day execution and completed
order results do not establish the old order's outcome. Two EUR CashBalance
rows were observed and were not both zero, so an empty security-position
download must not be interpreted as flat FX cash. No account balance, account
identifier, original order identifier, or raw customer record is published here.

A narrow follow-up in the same 36 known recovery directories found no newer
candidate terminal evidence. This was not a global account-directory search.

## Retained evidence identifiers

The complete private artifacts remain in the isolated research evidence
package. These digests permit exact comparison without publishing raw records.

| Artifact | SHA256 |
| --- | --- |
| Complete original OMS journal | `1ca0aac88a70ba36cac799b2e6b91805f2a08ab2debc5cf0682a8048a04a2e80` |
| Original FX restart checkpoint | `d8cd76d6474bd318bbc30b7be80399fbb017f58cd8ec755bb9f942b17e57b447` |
| Pseudonymized catch-up input | `ed612758a1fd8b3026a822e55a7df739f6608d6327653d60df9eac61da444afc` |
| Isolated replay result | `c0290edc9022e44819b4312603680de4acbf9272ef701ec331b8f3660527eeeb` |
| Frozen broker-read contract | `a69cc1af87f84dbfcfc72e811811ab0f3ac8cd81148af8dabe246e541b1654bd` |
| Broker raw snapshot | `4abf67dcce19a0881f7f2f7b4a89adf04ed84bdb2b982d155cc7147307e5902d` |
| Broker execution receipt | `791348acda85f888b3390c24161f47831aa32037609991099b8d3e59c987f2ce` |
| Minimal recovery review | `15a5dc8fee1c6c7aa5ae44547ea26e557ee30b9e221400030b9e13e58b876496` |

## Remaining recovery and financial evaluation conditions

The safe operational prerequisite is authoritative terminal/execution evidence
for the original accepted submission, bound to its original account, owner,
command, correlation, contract, and economic consequences. If the current API
cannot provide that historical evidence, the existing recovery owner needs an
admissible historical broker source and complete reconciliation. This research
does not append broker callbacks, alter the original journal, reset the cash
anchor, clear the hold, restart services, or deploy the collector.

Quote recovery subsequently requires an actual fresh, authoritative quote.
The installed readiness contract checks 24 overlapping operational probes,
with a five-minute endpoint, five-second endpoint tolerance, and 30-second
maximum path gap. Such probes are not independent efficacy samples and do not
define continuous first-passage barriers or reset semantics.

The inspected monetary forward evaluator has a 14-day input window plus a
one-day settlement grace, at least 20 observed trades across three UTC dates,
and at least five trades in each time half. It scores only exactly attributed,
closed, fee-complete original behavior. Its 25,000-EUR profile is a separate
scope from the unresolved original submission.

BUY and SELL predictions use one side-conditioned value readout. Only the
actually chosen direction has an observed settlement label; the other
prediction is not an identified counterfactual. The ACT/ESCALATE adapter has
different semantics and cannot be renamed into Call/Put channels.

No financial efficacy search or new independent financial labels were produced.
KEEP/STOP remains available. The diagnostic defect and the correct rejection
are explained and replayed; operational recovery and future-label availability
remain open conditions.
