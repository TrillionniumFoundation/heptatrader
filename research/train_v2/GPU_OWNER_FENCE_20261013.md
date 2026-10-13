# Train V2 GPU resource-owner fence — 2026-10-13

Isolated research branch stacked on PR #285; **not installed**.
No changes to the original Laya Guard, PAPER trader, OMS, source reader,
current Trial3, active GPU owners or model weights.

## Why GPU qualification remains HOLD

Previous experiments established a real kernel cgroup OOM kill and
separate zero-swap recovery. They did not test AMD ROCm device VRAM or
independent GPU ownership. The current Pocket4 has multiple live
desktop/LLM/DroidLoom GPU clients. CPU memory.swap.max=0 does not
prove device-memory noncontention.

## Implemented research-only fence

The new gpu_owner_fence.py uses Linux /proc/<pid>/fd character-device
major/minor identities to inspect DRM render/card and KFD holders.
It reads PID starttime before and after each FD inspection and runs
two independent observations. All missing FD data, permission denial,
PID reuse, changed owner sets, excess PID/FD counts and unresolved
kernel identities are fail-closed. It never opens GPU devices, allocates
VRAM, signals other processes or creates an exclusive device lease.

Even an apparently idle owner census is explicitly NOT a valid exclusive
lease. A transient FD closure detected by adversarial tests was initially
misclassified as fully inspected and corrected before real acceptance.

The separate gpu_owner_join.py runs the existing pinned ORIGINAL
resident resource/source preflight and this GPU owner inspection in one
bounded, disposable zero-swap kernel cgroup. Both checks are preserved
independently and the combined GPU model admission always remains HOLD.

## Real hardware evidence (Pocket4)

GPU handle snapshot: 10 actual DRM/KFD owner processes in both scans,
including GNOME, llama-server, Sway, Java and DroidLoom. The separate
Linux fuser observation corroborated those ten owners. About 589 PID
records per scan could not be fully inspected; this is unresolved
coverage, not proof that other clients do not exist.

Create-only original owner receipt:
~/.local/state/heptatrader/operator-evidence/train-v2-gpu-owner-fence-20261013/gpu-current-owner-census-v2.json

SHA256:
4856f903a732f7a6ccf175e58ef7050020c06eefa1983b05ea7e6f3aab0b821f

The real transient user-level unit used 6 GiB MemoryMax, 5 GiB
MemoryHigh, MemorySwapMax=0, CPUQuota=100%, TasksMax=32,
IPAddressDeny=any and NoNewPrivileges=yes. Readback from actual
kernel cgroup files verified memory.max=6442450944,
memory.high=5368709120, memory.swap.max/current=0,
pids.max=32 and cpu.max=100000 100000.

The original source refresh and isolated resource preflight were
READY_FOR_OPERATOR_REVIEW_NO_JOB at the measured instant. The
unchanged production Guard still denied with
HOST_SWAP_RESERVE_BELOW_1GIB. The GPU scan found 10 active owners
and 589 unavailable/racing PID records, so combined result was
HOLD_NO_EXCLUSIVE_GPU_LEASE and intentional exit code 2.
No GPU workload, Broker request, model update or trading order ran.

Combined actual cgroup+original-source+GPU-owner receipt:
~/.local/state/heptatrader/operator-evidence/train-v2-gpu-owner-fence-20261013/joint-original-gpu-cgroup-v1.json

SHA256:
957722aa49c155c564887b0b3b2a37dcb69e2df31a1828251294e151288e8676

## Required next gate

An actual 4-6 GiB ROCm/GPU model ceiling test requires an independently
ENFORCED GPU owner lease for all relevant device consumers, verified
noncontention, actual original source readiness and resource reservation
throughout the whole model window, GPU memory accounting, fault recovery
and independent cross-host review. FD samples alone never grant this.
The installed Guard and any active production owner must not be
suspended or bypassed. Frozen no-trade economics and canonical
Artifact Owner CURRENT are still unqualified.

New focused tests cover PID identity/FD churn, missing permission,
faked devices, cross-scan changes, empty-snapshot non-authority,
original source HOLD and impossible GPU lease claims. Full core,
historical and Source partitions remain separate mandatory gates.
