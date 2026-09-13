# Online OMS capacity and operational headroom

Status: CURRENT
Applies to: simulator and IB PAPER Execution daemon process telemetry

## Implemented path

`OmsJournal::GetHealthSnapshot` publishes online written-record and byte counts
under the journal mutex. An empty initialized journal has an explicit zero
baseline. Existing nonempty bytes are unknown until complete validated replay.
Successful writes advance the baseline; queued/buffered records are reported
separately. File identity, private regular-file metadata and expected size are
checked when sampling. Replacement, unexpected size, failed replay and poisoned
writes never become a healthy zero. No scan of the growing ledger is added to
the order path or the periodic health path.

Both `hepta-executiond` and `hepta-ib-executiond` emit one structured JSON
`heptatrader.oms-capacity.v1` observation after startup and then at most once per
five seconds using a monotonic cadence. Service-manager logs carry the unit/PID;
the message contains no account, order ID, token, key, path or journal payload.
`SIGTERM`/`SIGINT` remain synchronously consumed by the owner thread. No Agent RPC
or network listener is added, and no telemetry value grants execution authority.

## Fields and units

| Field | Meaning |
|---|---|
| observed_at_ms | observation time, Unix epoch milliseconds |
| known | counts have a validated process baseline and matching current file identity/size |
| bytes / records | written journal bytes (including newlines) and records; null when unknown |
| max_bytes / max_records | this process's actual configured replay budgets |
| queue_depth / buffered_depth / pending_records | not-yet-written record counts |
| byte_headroom | max minus written bytes, saturated at zero; excludes queued byte sizes |
| record_headroom | max minus written plus pending records, saturated at zero |
| write_poisoned | existing writer safety state; not a capacity-policy decision |
| status | OK, WARNING, EXCEEDED or UNKNOWN |
| authorization_effect | always NONE |

WARNING starts at the inclusive 80% threshold for bytes or written-plus-pending
records. Equality with the limit is allowed by replay but has no remaining
headroom. EXCEEDED means either budget is exceeded. UNKNOWN is distinct from OK
and from zero, including before replay or after identity failure. Byte headroom
is not a promise about queued bytes, disk-free space, RSS, replay time, checksums
or economic correctness. Capacity is not a substitute for the strict replay
validator; same-size content tampering still requires normal integrity/replay
checks. The telemetry is an operational gauge, never a new mutation cutoff.

## Operator action and evidence

Read structured messages from `hepta-execution-simulator.service` (or the actual
instance unit) and `hepta-execution-ib-paper.service` using the deployment's
journal collector. Parse JSON and select the exact schema rather than deriving
counters by grep. A missing expected heartbeat beyond 15 seconds is a collector
policy alert; the repository supplies the message, not an installed alert daemon.

At WARNING, plan a stopped-state checkpoint and assess recovery capacity on an
authorized offline copy. At EXCEEDED, do not restart blindly and do not truncate
the ledger. Keep exit/reconciliation evidence writable, fence new risk through
the existing operator controls, complete required authoritative reconciliation,
and stop Gateway then Execution under the existing runbook. Preserve journal,
lease store, matching key and terminal witnesses together in protected custody;
never restore an old lease over a newer fence generation.

Use the existing offline capacity verifier with the intended deployment budgets,
then test replay/recovery of the checkpoint with the selected immutable artifact
before changing the trusted service environment. The host's memory/recovery-time
measurement determines whether raising a budget is acceptable. No automated
rotation, compaction, command-ID expiry, lease migration or production backup API
is introduced here. Daily or unattended indefinite operation is not implied.

## Executed boundaries

The existing native OMS durability target includes `oms_live_capacity_cases.h`.
Tests cross 80%, equality and over-limit states, preserve guarded-exit records,
reject over-budget replay before callbacks, reopen original and stopped-state
copied ledgers with an explicit larger budget, preserve all command identities,
handle pending records and path substitution, and verify cadence/serialization.
Installed process acceptance also requires real daemon observations before and
after restart. The existing lease-plus-key checkpoint fixture remains a separate
integration test; a copied journal alone does not prove deployment-wide restore.

See [recovery limits](oms-recovery-capacity.md),
[rollback and backup](../operations/rollback-backup.md) and
[persistence support](persistence-support-window.md). Actual target-host and
external IB qualification evidence remain outside source-only completion.
