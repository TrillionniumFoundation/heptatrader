# Pocket4 Value343 — event-triggered pre-RESPONSE source shadow v2

Research only. This corrects a real sampling-coverage limitation of the
previous 7-second polling prototype. It cannot select a trade, alter
the original API inference, access broker secrets or qualify a model.

## Measured original Pocket4 latency, not assumed

The registered, hash-chained original EUR/USD producer contained 4,432
REQUEST/RESPONSE pairs at the time of this audit. Actual response
availability minus original request-start time:

| Elapsed-time criterion | Original completed requests |
| --- | ---: |
| Under 2 seconds | 2 |
| Under 5 seconds | 455 |
| Under 7 seconds | 3,092 |
| Under 10 seconds | 4,094 |
| Median | 6,296 milliseconds |
| Slowest | 76,511 milliseconds |

A 7-second **timer interval is not a confirmed 69.8% miss rate**,
because it depends on phase alignment, processing time and source
write order. But 3,092/4,432 original requests returned in under
7 seconds, which establishes a severe potential race for the old
periodic reader. Making the timer faster while rescanning all
30+MiB of historical REQUEST/RESPONSE data every time would waste
CPU and still provide no proof of a seal before RESPONSE.

## Versioned v2 observer and immutable evidence scope

This branch modifies the research-only
research/neuron_learning/laya_cell/value343_prospective.py,
based on original stacked PR #399 and frozen historical study #397.

A separate **v2** local freeze and owner-only evidence root must be
created after installing the pinned source. Old v1 freeze and all
historical original source remain intact.

v2 watches the registered original producer's private *.jsonl
directory using Linux inotify (IN_MODIFY, IN_CREATE, IN_CLOSE_WRITE
and IN_MOVED_TO) instead of launching a full new Python instance
every seven seconds. The original journal writer intentionally
keeps the *.jsonl file descriptor open; a CLOSE_WRITE-only observer
cannot reliably see each REQUEST. The watcher parses the inotify
file names and **ignores the producer's frequent *.health.json
rewrites**, so closed-market idle time does not cause rapid
full-archive rescanning.

Each relevant data-append event causes the existing complete,
SHA-chain-verified original reader to scan and try to seal a
NEW, previously unseen original REQUEST before its RESPONSE.
A fallback whole-reader pass every 20 seconds catches watch
restart, missed notifications, and actual new OMS reward maturity;
a kernel event-queue overflow triggers an immediate full rescan
and explicit warning. The watcher does not modify any producer,
trade driver or source.

It continues to freeze **both** possible BUY/SELL directions
from the same original pre-request snapshot, only for a flat
position, matching frozen USD100k producer, before the 90-second
request deadline, and with a source-only pinned model already known
to fail historical MAE improvement over the train median.

## Critical separate RESPONSE timing proof

The previous version rejected results after DECISION or outcome,
but it did not independently prove the prediction preceded the
original model's RESPONSE.

In v2, a preliminary prediction is **provisional**. When the
authentic original RESPONSE arrives, an independent append-only
response_proofs receipt checks the signed/pinned request identity,
the original event chain, original RESPONSE request digest,
and insists the prediction was sealed strictly before the
RESPONSE's original *available_at_ms* and capture timestamp.
A RESPONSE already in the first snapshot leads to an immutable
MISSED record; a RESPONSE that arrived between the source scan
and the prediction seal produces an immutable REJECTED record.

**Only an existing, identity-checked response_proofs receipt can
authorize a future research score**. The scorer rechecks that the
original RESPONSE availability still follows the sealed time,
then requires original DECISION and original OMS execution+fee
target times to follow it. Actual performed BUY/SELL original
trade outcome is the only score; unobserved alternate-side
returns are never manufactured.

All v2 output writes are fsynced to an owner-only temporary file
then atomically link-created as the final, immutable *.json.
A SIGTERM in mid-write cannot leave a partially valid *.json
receipt that prevents the next correct run from retrying.
No use of network, Broker APIs, GPU or transaction authority.

## Reproducible host process and promotion boundary

The v2 service template is at
systemd/user/heptatrader-value343-predecision-fast.service .
Actual host versioned release path is fixed in the unit;
it has 40% CPU quota, 512 MiB memory limit, no swap,
no new privileges, no network families beyond AF_UNIX,
strict read-only original/home mounts, and only the new
owner-private evidence directory writable.

Old v1 timer remains the immediate rollback path if v2 fails.
Do not overwrite its freeze or replace its previously stored
records. Never run old v1 and v2 as two producers of one output
path; they have entirely separate private evidence roots.

This is an operational source-observer qualification, NOT
proof of forward model accuracy or economic improvement.
No futures data, pre-response success, or prospective settled
trade may be claimed until a real original request after v2
activation is independently captured and later verified.
