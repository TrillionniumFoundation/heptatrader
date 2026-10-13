# Train V2 — real kernel zero-swap OOM / recovery acceptance (2026-10-13)

Isolated disposable user-service research branch stacked on PR #284. No changes
to original resident Laya Guard, source reader, IB Gateway, OMS, PAPER driver,
current Trader, trial timers, GPU model weights, or producer credentials.

## Prior state

The unchanged original production Guard requires at least 1 GiB global SwapFree;
Pocket4 has roughly 0.76 GiB after many unrelated long-lived workloads.
Prior bounded 6 GiB zero-swap kernel preflight demonstrated occasional original
24/24 source readiness and frequent source-continuity HOLD, but no real process
death/restart proof. Neither a READY research preflight nor 24/24 quote coverage
grants production training or broker authority.

## Executed real transient cgroup sequence, no GPU

All transient units used the same actual user slice, CPUQuota=100%,
TasksMax=32, MemorySwapMax=0, IPAddressDeny=any and NoNewPrivileges. The
program checks /proc/self/cgroup, the actual unified /sys/fs/cgroup kernel
memory/cpu/pid/oom-group controls, before/after kernel memory.events, and
actual memory/swap peak. It never writes cgroup control files. No model,
client socket, Broker API, producer or existing service was used.

1. Initial disposable recovery smoke: a NEW 6 GiB max / 5 GiB high cgroup
   actually touched 64 MiB of pages, exited 0, 0 OOM kills and 0B swap.
   This earlier success is not misrepresented as recovery from failure.
2. First OOM fault attempt (ht-trainv2-cg-fault-20261013-r1):
   128 MiB max but 96 MiB high caused severe throttling; after 15.136s
   the service exited 1 with reported 107.1M peak; no qualified kernel
   OOM receipt was produced. The original user-journal lines were preserved
   as fault-timeout-negative-v1.json (FAILED). This first experiment remains
   a negative result and was not overwritten or promoted.
3. Revised cgroup ht-trainv2-cg-fault-20261013-r2:
   128 MiB max AND 128 MiB high, swap max/current/peak 0,
   memory.oom.group 0, pids 32 and CPU quota 100%.
   A disposable child attempted to touch 256 MiB pages and the actual
   kernel terminated JUST that child (return code -9), kernel
   memory.events oom_kill increased by 1, memory.peak was exactly
   134217728 bytes, and the parent survived and fsynced its receipt.
4. Independent *post-fault* cgroup ht-trainv2-cg-recover-20261013-r2:
   new unit/PID, 6 GiB max, 5 GiB high, swap current/peak 0. A new
   64 MiB touched-page child returned 0, kernel OOM kill delta 0,
   observed memory peak 82509824 bytes; systemd unit exited normally.
5. Fresh separately confined original-source preflight after recovery:
   6 GiB/5 GiB cgroup, zero swap, actual pinned production Guard original
   source refresh. Original quote continuity was genuinely ready at that
   moment (24/24), and isolated preflight was READY_FOR_OPERATOR_REVIEW_NO_JOB,
   while original product Guard remained DENIED strictly because global
   HOST_SWAP_RESERVE_BELOW_1GIB. No heavy work or GPU started.

A later source-ready historical instant cannot be reused indefinitely.
Every future candidate startup must independently recheck the ORIGINAL
source, real RAM, memory pressure, manual HOLD, the kernel resource lease,
and new effect-free policy. None of these receipts is an approval to
start current live Laya service or auto-train.

## Immutable machine receipts (Pocket4)

Directory:
~/.local/state/heptatrader/operator-evidence/train-v2-swapless-recovery-20261013/

- failed original timeout:
  fault-timeout-negative-v1.json
  SHA256 e8c9f34f22478acaa43277302e0e6565181de9e0ec01f2b70b7d0dad883b2349
- actual cgroup child OOM:
  fault-real-cgroup-v2.json
  SHA256 1b2008c8559fdd98c8d2021c816f10ab6bff89479272979db667336f7d683782
- separate post-fault healthy recovery:
  recover-post-fault-cgroup-v2.json
  SHA256 f42b80fa3f7702a2c3609d084eab8aa6a74f26bdf09c67f92ce81bce5a2acfe6
- original-source refresh AFTER this recovery:
  postfault-original-source-gate-v1.json
  SHA256 13e5edd1aa0a0a28a1e1454c6e50dd1b8a2d1e4a101679471ad494e382077b90
- independent immutable four-receipt closure:
  full-kernel-chain-closure-v1.json
  SHA256 bcdf0eaaecb9680a7ef3bbe65a9b885a437c85742cd460ca61204be8810f5e23

The closure independently validates real resource files, cgroup identity,
OOM deltas, source freshness, immutable original Guard denial and actual
time ordering. Input bytes/source hashes are included. A SIGKILL alone
cannot prove OOM; a successful 64 MiB process cannot prove recovery
without a prior kernel OOM. Every false success is rejected.

## Still NOT qualified

- NO real ROCm/GPU-memory ceiling test with a heavy 4–6 GiB model.
- NO GPU device allocation control or verified non-contention against other
  live GPU owners; CPU cgroup kernel memory.max does not automatically prove
  ROCm device memory accounting.
- NO 24/24 durable source continuity across a heavy training window.
- NO native Canonical owner, fresh statistically independent after-cost alpha,
  artifact CURRENT selection, production service deployment or automatic trade.
- Zero-swap research admission must NEVER retroactively override original
  global SwapFree product Guard or rewrite failed source windows.

Next safe gate: independently qualify a bounded short GPU test in an
EXCLUSIVE user-authorized resource lease when the original quote source
and all unrelated GPU owners are demonstrably healthy and disconnected.
Do not start an actual model load solely because the cgroup OOM test passed.

## GPU non-contention review during regression (19:00 local, Oct 13)

A separate read-only /dev/dri/renderD128 owner inspection on Pocket4
found multiple existing unrelated GPU users, including llama-server,
the desktop compositor, DroidLoom and active model/forward research
processes. These PIDs and device handles were not signaled, suspended,
unloaded or reconfigured. No exclusive GPU lease could be proven from
the cgroup result or source 24/24 instant alone, so a REAL HIP/ROCm
GPU allocation/4–6 GiB model memory-ceiling stress test was deliberately
NOT run. The GPU ceiling and non-contention qualification remain HOLD.

The normal broker/FX services and original Laya Guard continue to own
their resources and authorization state independently of this transient
CPU-only cgroup study.
