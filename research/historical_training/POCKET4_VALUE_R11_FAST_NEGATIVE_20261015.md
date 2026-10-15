# Pocket4 R11: less background work while Value is prioritized

R10 deferred new long Laya jobs but still performed expensive source refresh
before recording a denial. R11 reads the pinned original Value request schedule
first. Explicit priority denial skips ONLY the unnecessary background resource
and quote refresh. Any positive admission still requires all original memory,
Swap, PSI, quote continuity, shared flock and post-lock rechecks.

For a priority-held idle evaluator, supervisory polling is 10 seconds instead
of 2; any active child or unreserved/unverified request restores 2-second
polling. Existing children are never force-killed; original prediction
deadlines, broker orders, model weights and financial evidence are untouched.

R11 was shipped as sealed role-specific user-systemd source releases. Trainer
and evaluator were naturally drained with SIGUSR1, then the evaluator received
a second naturally drained idle-poll update. Both have on-failure/30s restart.

Verification: 689 historical training tests pass; 314 Laya/Value non-native
tests pass, one skipped. Both role candidates passed original input hash
verification and real priority rejection without the old refresh or flock.
The first R10 forward window had six nonflat requests and zero eligible
mature flat requests, so improved forecast seal rate remains unproven.
PAPER recovery cash-generation audit also remains open; no trade change made.

Pocket4 receipts and rollback details:
~/.local/state/heptatrader/operator-evidence/pnl-r11-fast-negative-20261015/
