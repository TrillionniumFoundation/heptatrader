# Read-only PRICE gate acceptance

`scripts/audit_price_bootstrap_gate.py` reads a finite set of actual observations
and prints JSON to stdout. It has no order, inference, native control, service
or output-file API. It does not modify the producer or the 32-row Value policy.
Run as the private files' owner from the pinned auditor checkout, keeping the
three imported repository modules beside it. It reports all four source hashes;
the gate and Capture code must also match the exact producer runtime pins.

## Actual activated producer invocation

```sh
python3 -B scripts/audit_price_bootstrap_gate.py \
  --manifest /home/alex/.local/share/heptatrader/decision-attribution/releases/8c6b6ab0f719b279baa128f4c9006e10aa410766-prepared-7447c035fba194ba93fde3623bdc3aa79e2092d29ddd56d49e8f660f0746b313/EUR.USD/manifest.json \
  --manifest-sha256 7cd27ee2919f893020d4dea9db90484cb7a49859e9a67588d4975ed529d98113 \
  --initial-writer-id ab0dbb69a76249d0a52db211161e7b39 \
  --oms-journal /home/alex/.local/state/heptatrader/execution/oms-journal.jsonl \
  --oms-base-bytes 50948692 \
  --oms-base-sha256 2ef5c7c09648e31546addc65f0d777db11bb1200c9482678841574185dbb032b \
  --oms-base-observed-at-ms 1793613083866
```

These pins come from this activation's hold-time handoff. They are not defaults
for other deployments. Each accepted request must start strictly after that
anchor observation. Do not move the anchor past an unexamined request. Its
full old prefix is hashed, but only its bounded new suffix is parsed.

Without repeatable `--journal-file`, discovery reads at most 128 direct
`*.jsonl` headers in the manifest's one directory, and bodies only for the
exact producer. The initial writer must remain present. A restarted driver
may add a writer only with the same producer/period/PAPER header and a full
sequence-zero hash chain; IDs and exact prefix hashes are reported. An explicit
`--journal-file` from a foreign producer fails. No recursive discovery, latest
record substitution, missing-segment repair or cross-producer borrowing occurs.

## Proof and scope

The five private files are validated together: original input, PRICE
prediction, seal, admission and actual command binding. Checks include full
request/response/decision hashes, actual past-only quotes, persistence formula,
unit, no-learning flags, producer and implementation pins. Their original
Journal graph and actual native command ID must match exactly. A queued event
or a nearby record cannot replace the join.

True admission completion is the post-file-and-directory-fsync time retained
in the PREPARED command, not the timestamp sampled before writing admission.
The fixed gate ordering and Journal binding capture time bound the durable
command binding before transport. File ctime is only an inconsistency check;
it never supplies a missing durability time. These are local owner-held,
pinned-code receipts, not external timestamps or Value role signatures.

Native checks reuse `hepta_pnl_accounting.replay` for account, owner, command,
correlation, order, epoch, execution ID and economics. Replayed executions are
deduplicated. First fill means the native OMS `execDetails` **local receipt
time**, not an IB-server execution timestamp; the latter relation remains
explicitly unproven. CLOSE/flatten never increases the OPEN denominator. A
send without a validated fill remains incomplete, not a fabricated zero fill.

Actual native `place_sent` commands after the anchor form the observed
acceptance denominator. Unmatched native OPENs fail. Eligible decisions,
Journal bindings, private directories and local discard archives are separate
counts. `LOCAL_PRE_SUBMIT_NO_SEND_RECORDED` is the pinned gate's local record,
not a broker rejection or fill. This auditor does not independently reconstruct
the archive's historical fresh Broker snapshot.

Ordinary HOLD/low-confidence observations with no real command binding return
`WAITING` without reading the native prefix. The native file is only statted:
its unverified byte growth is visible, while native count/fraction remain null.
Once a private or original-Journal command binding exists, the old prefix is
hashed once per invocation and only new suffix records are parsed. Incomplete
trailing lines are counted, never treated as facts.

Capacity limits are 32 requests/cases, 128 Journal segments, 32 MiB selected
Journal bodies and 16 MiB new native suffix. A limit fails explicitly instead
of truncating the denominator. This is a finite acceptance observer.

## Report and finite scheduling

Schema: `heptatrader.price-bootstrap-readonly-audit.v1`.

| Status | Meaning |
| --- | --- |
| `WAITING` | No bound OPEN or private attempt; native prefix unscanned, count/fraction null. HOLD is expected. |
| `PARTIAL` | A private attempt, Journal binding, send or first fill is incomplete; or Journal health reports gaps. Per-case status identifies the missing link. |
| `VERIFIED_OBSERVED` | At least one native OPEN/first-fill chain passed, every observed native OPEN after the anchor is accounted for, and no Journal loss is reported. |
| `FAILED` | Invalid/late evidence, missing seal for a bound OPEN, conflicting local discard or unmatched native OPEN. |
| `INVALID_SOURCE` | Changed source pin, broken chain, missing initial writer, invalid private file or exceeded bound. |

`cases[]` retains request/command IDs, source hashes and causal times without
repeating prompts/responses. Other machine fields are `producer`,
`auditor_sources_sha256`, `journal.writer_ids`, `native.pinned_prefix`,
`counts` and `scope`. Exit code is 2 for FAILED/INVALID_SOURCE and 0 otherwise;
process success alone is not an acceptance verdict.

An installer can schedule this one-shot command at a modest cadence with a
fixed wall-clock stop condition and execution/resource limits, retaining stdout
in a new observer artifact or service journal. Stop after a verified observation,
a substantive error needing review, or the finite window. Expiry without a
natural OPEN remains not yet observed. The auditor never installs/stops timers
or generates requests/orders to obtain a sample.

`VERIFIED_OBSERVED` is finite acceptance, not a future guarantee or learning
result. `value_coverage_credit`, `efficacy_claim`, `broker_orders_sent`,
`synthetic_predictions_created` and `files_written` remain false. It does not
claim a closed net-USD outcome or count these observations toward 32 Value rows.
