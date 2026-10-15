# Pocket4 R8 — cash monitor and original Value latency

This fixes one repeat-invalidation defect, **not** the live broker recovery
audit. The current Execution and PAPER drivers remain unchanged.

## Cash accounting behavior

A changed CashBalance in an existing subscribed account stream must invalidate
the committed account/risk generation and prevent new risk-increasing orders.
It must never modify the authoritative cash inventory or authorize trading.

The old adapter then repeatedly called InvalidateRiskSnapshot() on later
numeric subscription pushes when its authoritative cash map was already empty.
Those redundant invalidations also cleared pending independent positions
refreshes. The R8 adapter now treats further numeric pushes as nonauthoritative
while accountComplete=false, fxCashComplete=false and no authoritative cash
map exists. Fresh account request/end callbacks remain authoritative, not
monitor pushes. The original mismatch-on-startup/physical-crypto exception
remains deliberately stricter.

Native synthetic tests demonstrate: first cash change revokes recovery and
order authority; repeated callbacks cannot end an in-flight positions download;
positions-only completion cannot restore authority; a new coherent account
and positions refresh with attested cash is needed; and no simulated broker
orders are sent during these checks. This patch does not erase old failures,
upgrade expired predictions or bypass a new recovery connection epoch.

## Deployment boundary

R8 C++ code is on a separate Git branch and **has not** replaced the live
Execution binary. FX PAPER can be nonflat even when a prior snapshot was flat;
the next binary switch requires a fresh Broker/open-order/position and original
command reconciliation, followed by a bounded owner-controlled new connection
epoch. There is no safe standing approval to restart Production Execution while
its position state is changing.

## Value priority and evidence

The active Pocket4 original Value predictor systemd unit now has a small,
reversible drop-in (the exact source copy is
`research/historical_training/deployment/value-predictor-priority-r8.conf`):
Nice=5 and CPUWeight=50 instead of 15 and 10. Production Execution remains
at Nice=0 with normal/default CPUWeight=100; Laya trainer stays at Nice=15
and CPUWeight=10. CPUQuota=150%, MemoryHigh/Max=6GiB, all admission limits,
the frozen model, signing rules and original 60s deadline remain unchanged.
No user service was force-restarted.

Drop-in rollback: remove its Pocket4 user-systemd copy and run
`systemctl --user daemon-reload`. This is separate from the earlier
runtime-only `vm.swappiness=15` adjustment, reversible to 60.

The first R8 post-priority coverage audit found **zero mature** eligible
requests, so a success-rate improvement is NOT established. Previous
rolling-12h observation had 83 mature, 12 verified early commits, 70 immutable
deadline misses and one unresolved expired request. These histories were
not rewritten. The training candidate still has no trade authority.

## Completed tests

Four IB native test executables passed: terminal reconciliation, startup
recovery hold, observation refresh, and PAPER execution profile. Fixed all
seven hardcoded temp-directory fixtures in the last one to use TMPDIR, without
changing the host /tmp mount. Historical-training Python suite 665 passed;
non-native Laya/Value 314 passed and one skipped. No order was issued by
R8 verification tools.

The unrelated IB historical `cashhmds` disconnected state and cash recovery
barrier remain distinct open operational questions.
