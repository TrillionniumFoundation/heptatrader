# Train V2: actual swapless cgroup resource-lane preflight

Date: 2026-10-13, isolated pocket4 source branch stacked on the frozen PR #282
singlepass research code. This is an optional *candidate* for FUTURE train jobs.
No changes to live trader, native Execution, OMS, Laya Trainer/Evaluator,
original Guard, original source feed, prior trials, model weights, or existing
PAPER orders. The original Laya training admission remains HOLD.

## Genuine immediate blocker

Current pocket4 has roughly 15 GiB MemAvailable, memory full PSI 0.0 and
around 0.76 GiB SwapFree on an 8 GiB disk swapfile. The original pinned
resident_resource_guard demands >=8 GiB MemAvailable, >=1 GiB SwapFree,
and the unchanged original 24-probe causal source checks. By Oct 13 the
existing original source regained 24/24 readiness; the dominant resource
blocker was HOST_SWAP_RESERVE_BELOW_1GIB. The existing memory pressure and
swap allocation span multiple separate long-running system/app services.
Stopping them, swapoff, resizing swap, introducing zram, or relaxing the
original Guard would risk unrelated live workloads and has NOT been done.

## Actual transient kernel proof

New module research/train_v2/swapless_resource.py is NOT installed into the
original resident Guard. It uses the original resident_resource_guard.assess
to preserve ALL original reasons, the exact original Guard configuration
verified by verify_config, and the original resident_live_source.refresh
to prove the current hash-chain quote and original source continuity.
It does not fabricate a recent quote from a cached source status or change
the original 5000ms TTL, 30-second path gap or 20/24 source minimum.

Only a transient separately confined user-level cgroup can be considered:
- actual memory.max between 4 and 6 GiB; memory.high <= memory.max;
- actual memory.swap.max = 0 and memory.swap.current = 0;
- actual tasks cgroup cap <=64 and CPU quota <=100%;
- MemAvailable AFTER pessimistically charging the complete worker memory
  hard cap must still be >=8.5 GiB;
- all original source/manual-hold/PSI/memory guards unchanged and passing
  except the original global SwapFree >=1 GiB requirement.

The report retains original production Guard denial unchanged, and labels
a separate isolated result READY_FOR_OPERATOR_REVIEW_NO_JOB, not production
admission. The tool makes no GPU allocation, external broker request, model
activation, signal, socket or change to the host swap arrangement. Even a
successful preflight grants no model training, actor effect or trade authority.

A first genuine systemd-run experiment had MemoryMax=6G, MemoryHigh=5G,
MemorySwapMax=0, TasksMax=32 and CPUQuota=100%. Actual memory peak ~10.2MiB,
swap 0B, but plain status was 5 seconds stale, so it exited with HOLD.
This failure is preserved as actual-swapless-proof-v1.json. We did NOT
lengthen freshness or relabel that sample successful.

A second *new* transient cell used the ORIGINAL pinned Guard's source
refresh, plus IPAddressDeny=any and NoNewPrivileges. It passed actual
memory/swap/pids/cpu cgroup reads and the original local quote chain:
- original Guard: NOT admitted, HOST_SWAP_RESERVE_BELOW_1GIB;
- isolated preflight: READY_FOR_OPERATOR_REVIEW_NO_JOB, 0 hold reasons;
- actual kernel memory.max: 6,442,450,944 bytes;
- actual kernel memory.high: 5,368,709,120 bytes;
- actual kernel memory.swap.max/current: 0 / 0;
- actual CPU max: 100000 100000, pids.max: 32;
- MemAvailable: 16,409,571,328 bytes;
- SwapFree: 820,719,616 bytes;
- original quote available at ms 1791886350816, observed at 1791886350491,
  with original chain SHA and proof retained unchanged;
- process runtime ~82ms, memory peak 12.9MiB, actual swap peak 0;
- service was transient and exited normally, not enabled/scheduled.

Immutable second evidence under:
~/.local/state/heptatrader/operator-evidence/train-v2-swapless-cell-20261013/actual-swapless-proof-v2-original-source.json

SHA256:
05541c5e4952380d1a9a4f9362fc2e785d42b130c9540df024224b6221e33d6c

The control-plane request included IPAddressDeny=any; this receipt confirms
memory/swap/pids/cpu controls from actual kernel cgroup files. It does NOT
independently prove socket/BPF policy enforcement or GPU device isolation.
No actual 4-6GiB heavy GPU worker has been qualified inside this cell.

## Remaining hard requirements

1. Run independent no-swap Linux cgroup failure/recovery and GPU memory ceiling
   tests in a spare isolated cell, with verified no unwanted CPU/GPU contention.
2. Verify actual original source readiness at each startup and maintain the
   original resource lease and worker isolation. Never substitute the isolated
   preflight for the currently active production Guard result.
3. Preserve all previous frozen trials and NO_TRADE economics. 3 source decisions
   / 12 correlated PRICE labels did not beat the 10.9133557bps assumed hurdle.
4. No canonical signed Owner/positive after-cost alpha has been established.
5. Only after full validation may an operator consider an optional, fresh
   research-only service using an explicitly versioned cgroup-aware admission
   policy. No automatic PAPER or production rollout by this branch.

If the original source is expired or host memory/PSI/manual HOLD fails,
the isolated preflight MUST reject even with kernel swap disabled.


## Final preflight source-fluctuation negative control

The optional final version now REQUIRES the pinned original Guard config;
the periodic source status alone cannot produce a READY isolated preflight.
The actual subsequent v3 transient systemd unit retained the identical
memory/swap/CPU/tasks limits, used the ORIGINAL source refresh and correctly
refused when the original IB stream stopped. At that time the cached source
reported 24/24 prior usable probes, but its latest quote was ~115 seconds
old. It was NOT a valid fresh input and was NOT repaired by this candidate.

The genuine v3 immutable failed/HOLD receipt:
~/.local/state/heptatrader/operator-evidence/train-v2-swapless-cell-20261013/actual-swapless-proof-v3-final-sourcebound.json
SHA256: 515a8ff4a439582d5a327de84e5f21bf3341704a1b2498ff6e212a45bfdf2bde.
It proves the current resource cell refuses both
LATEST_SOURCE_QUOTE_NOT_FRESH and SOURCE_CONTINUITY_NOT_READY
even when the kernel cgroup remains zero-swap.

The external IB API error-code documentation lists 10197 as
No market data during competing session, potentially linked to
simultaneous live and PAPER market data requests. This local machine has
logged that error repeatedly; it does NOT prove which other IB session
or entitlement triggered each instance. No IB session was logged out,
credentials changed, market data downgraded or denied quotes fabricated.
See https://www.interactivebrokers.com/docs/tws-api/doc/error-handling/error-codes

The pass-v2 + fail-v3 sequence is a controlled dynamic-source
preflight demonstration, not a stable continuous new-worker admission,
not a full GPU train inside the cell, and not an online efficacy result.


## Final fail-closed hardening on the isolated source tree

The initial optional CLI accepted a cached status argument without
requiring the original Guard file. The final version requires the
EXACT pinned original Guard config; stale periodic status alone can
never qualify the isolated research cell. The source reader uses the
unchanged original chain and 5s endpoint rule. After reading the
original source, a second actual wall clock is sampled for admission,
so a quote that expires while its SHA chain is being verified is
rejected. Manual HOLD checks use os.path.lexists, matching production
behavior even for a dangling symlink.

The isolated focused suite includes adversarial tests for those two
additional cases. Original Guard source hashes, resource admission
thresholds, current installed timer and old services stay untouched.

At a subsequent read on October 13 ~18:22 UTC+8, original 24-probe
source readiness had only 18 usable and 6 unusable probes, confirming
that fresh latest quote alone does not repair incomplete original
endpoint/path coverage. Neither the swapless alternative nor the
existing supervisor is allowed to start a heavyweight worker from
that source. No automatic repeat or production promotion is scheduled.


## Exact final source, tests and post-hardening kernel proof

The final source tree was re-tested after adding the post-source clock and
dangling-manual-hold safety tests:
- focused swapless resource checks: 14 passed, zero skips;
- pinned Laya Python historical training: 539 passed, zero skips;
- full core Python: 2,688 passed, 23 pre-existing skips;
- source/ownership/documentation partition: 106 passed, zero skips.

A fourth new transient real cgroup run used those exact hardened bytes.
Kernel zero-swap, 6 GiB memory cap, 5 GiB high, 32 tasks and CPU 100%
were attested again. Original source freshness was current, but its
24-probe path continuity had not recovered, so the isolated cell correctly
exited HOLD_ISOLATED_RESEARCH with SOURCE_CONTINUITY_NOT_READY.
The legacy product Guard simultaneously retained both
HOST_SWAP_RESERVE_BELOW_1GIB and SOURCE_CONTINUITY_NOT_READY.
No model was loaded and zero broker requests/orders were made.

Final hardened source private receipt:
~/.local/state/heptatrader/operator-evidence/train-v2-swapless-cell-20261013/actual-swapless-proof-v4-final-code.json
SHA256: ecb59522db1285d1f6068a96dadf3ce92c0c69939a385bc2b00dd092c0f2cb18.

The earlier v2 actual READY resource receipt and v1/v3 failures remain
unchanged. The v4 result is a correct ORIGINAL source refusal, not a
failed cgroup isolation or a model/training throughput test.
