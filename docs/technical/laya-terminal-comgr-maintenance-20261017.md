# Pocket4 terminal COMGR cache maintenance — 2026-10-17

## Observed storage result

The existing 12 GiB resident state quota was largely occupied by per-task
reproducible compiler caches. The original global heavy-worker lease covered
task selection, the before/after evidence manifests and pruning.

| Measurement | Result |
|---|---:|
| Verified terminal task directories | 77 |
| Removed regular COMGR files | 3,062 |
| Removed bytes | 10,640,150,600 |
| Non-COMGR evidence files checked | 4,818 |
| Changed evidence content hashes | 0 |
| Resident state bytes before | 11,285,003,567 |
| Resident state bytes after | 644,857,011 |
| Resident state quota bytes | 12,884,901,888 |
| Required admission reserve bytes | 536,870,912 |

The task-directory byte decrease equals the receipt's removed byte count.
The 77 extra sentinel/nonmatching entries in the initial inventory were
preserved. Existing storage accounting and admission thresholds are unchanged.

Evidence remains on Pocket4 under
/home/alex/.local/state/heptatrader/operator-evidence/train-cache-maintenance-20261017.
The cleanup receipt SHA256 is
c093c29060039312ecdf136aa2254065ebedb77217e01b762b766e44f8346aae;
the preserved evidence manifest SHA256 is
77ad7f9c23b79a63aa7f3c5e0d2d1e22282042b2e7ad1b9b9303209ec4ed8ed5.

## Selection and deletion contract

TERMINAL_TASK_COMGR_ONLY_V1 selects scheduler COMPLETED/FAILED candidates and
sealed terminal evaluations under the original global lease. A rechallenge
binds through its sealed plan to the original candidate. Process maps, file
descriptors, working directories, command lines and training config references
exclude still-used tasks. Unreadable research processes prevent selection.

Only direct regular single-link gpu-tmp/comgr-cache/llvmcache-* files qualify.
Directory components use O_NOFOLLOW; there is no recursive deletion or
symlink/hardlink traversal. A complete hashed plan is fsynced before deletion,
and file identity is rechecked immediately before unlink. A caller-held lease
is duplicated and only that duplicate is closed, without LOCK_UN.

Original captures, datasets, labels, model artifacts, identity files,
prediction/outcome/qualification receipts, logs and other gpu-tmp files remain.
The operation neither expands the quota nor hides bytes from its accounting.

## Continuous lifecycle deployment

Maintenance runs before the original lease release after normal worker
wait/reap. The existing idle evaluator reap loop supplies an opportunistic
check throttled to once per 60 seconds. Maintenance and status-write failures
cannot overwrite the training result or prevent lease release. Empty sweeps
create no new receipts; the small current status records NO_WORK or
HEAVY_LEASE_BUSY.

Each role keeps its own prior guard implementation and model runtime. Only
the reviewed maintenance/release and configuration-validation transformations
are applied to each pinned prior guard; resident_comgr_cache.py becomes the
fifth pinned module.

| Role | Immutable guard release suffix | Preserved model runtime |
|---|---|---|
| Trainer | terminal-comgr-trainer-334f5ef1defd6bf0cb0d | reward-sync-62c2a4edec0eef8f |
| Evaluator | terminal-comgr-evaluator-515d48e483782e8f8a7b | row-accounting-913f4d24cf1d9e01 |

Cache module SHA256:
1c0fae029b16b34bc80e344fe5d194654dcfc6a256b76bed7654e0aa1835f372.

Real import checks confirm the new guard/cache paths and unchanged laya_cell
model runtime paths. Original configuration SHA256, Value priority, source
gates, resource limits, manual hold and period are intact.

The original global lease covered the restart of both idle services. Each old
cgroup contained only its parent. New argv and PYTHONPATH were checked after
exec. The live evaluator emitted HEAVY_LEASE_BUSY during installation and
NO_WORK after lease release. Execution, Gateway and both paper-driver process
identities were unchanged. An initial pre-exec identity sample caused a
conservative rollback; successful activation waits for the actual Python argv.
Prior dropins and all activation records are retained in evidence.

## Validation and rollback

Forty focused tests pass across pruning, lifecycle hooks and the existing
resource guard. Cases cover held-lock preservation, wrong lock binding, busy
leases, terminal/rechallenge identity, active failed-job references,
symlinks/hardlinks, evidence integrity, receipt-before-unlink, identity changes,
empty-sweep throttling, profile pinning and maintenance/logging exceptions.

guard-deployment-plan.json records exact old/new config, release, argv,
PYTHONPATH and owned dropin paths. Reverting those owned dropins with the same
idle-worker/global-lease procedure restores the previous runtime. Original
configs and releases remain available. Future ROCm runs regenerate compiler
cache as needed; historical training evidence is neither rebuilt nor relabelled.
