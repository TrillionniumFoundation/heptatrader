# Pocket4 R13 — IB PAPER cash generation revalidation on current main

## Scope and actual integration

R13 carries forward only the earlier R8 fail-closed cash monitor fix to
the current GitHub `main` baseline
`b3a5012870644f8e715f5a767aec7f11eb93d3c2` (2026-10-15).
The prior R8 branch is not running as the production Execution binary.
The R13 source worktree and build are isolated from the live user service.
Do **not** confuse a GitHub branch/test result with production deployment.

The original IB adapter deliberately invalidates a committed coherent risk
generation if an account-subscription CashBalance update differs. After
the invalidation the authoritative cash map becomes empty pending another
complete account request/end. Repeated subscription callbacks could
re-enter invalidation on that empty map, aborting an otherwise progressing
positions refresh. R13 prevents this repeated reset while retaining the
initial fail-closed cash change, the original cash startup-attestation
mismatch branch and the requirement for a newly verified coherent account
and positions generation. No cash value is serialized in the additional
bounded first-transition diagnostic.

## Security and evidence

Native fake-broker tests establish that:
* identical monitored cash does not revoke a good coherent generation;
* the first genuinely changed value does revoke broker risk-increase ability;
* stale/repeated cash values cannot reauthorize an expired generation;
* an independent positions refresh can reach its native PositionEnd even
  while stale subscription updates continue;
* position-only refresh or reverting to an old value is not sufficient;
* only a genuinely new full risk refresh with account/end, positions/end,
  matching attested cash and terminal identity can establish complete
  adapter-side internal snapshots;
* simulated brokerage send/cancel counts remain zero while refused.

The real Pocket4 read-only Broker observation around 16:37 UTC+8
reported IB connected, zero active orders, **one nonflat FX position**
with gross risk quantity 89,119, risk generation complete but recovery
barrier still false with
`IB_FX_CASH_BALANCE_OUT_OF_GENERATION_CHANGE`.
This observation does not prove a safe Trading Execution maintenance
window. PAPER drivers and original OMS remain active and untouched.

## Actual native tests

Source built in an independent Debug CMake/Ninja tree under
`~/.local/state/heptatrader/operator-evidence/pnl-r13-cash-main-20261015`.
**10/10 original native CTest suites passed**:

* observation refresh; original PAPER kill switch; PAPER profile;
* IB live terminal reconciliation and new cash-generation tests;
* IB admission/event policy; startup recovery hold;
* cumulative execution evidence; recent order replay;
* post-fill risk reconciliation; full runtime/connection recovery.

The full-runtime fixture isolates external IPv4/IPv6 and exec calls via
kernel seccomp; it does not operate real Broker orders. Its original
Unix-domain socket fixture requires a short private TMPDIR.
This Pocket4 host's overmounted /tmp is a full 4KiB tmpfs;
R13 therefore teaches two additional **test-only** fixtures to obey
TMPDIR, and runs tests against a private /var/tmp/r13 path.
Neither the system /tmp mount, production cash/OMS source nor
production user service state was changed.

## Deployment and merge boundary

R13 is **candidate source only**. Never turn this branch or its green
tests into an automatic Production Execution restart while a PAPER
position, active/pending command, terminal callback, or incomplete risk
recovery may exist. Before switching any live execution binary, acquire
one documented owner-maintenance window with broker-authoritative
position/open-order/terminal reconciliation and preserved original
identity, verify the cutover and rollback path, then perform a controlled
new-connection-epoch account, positions, cash and ReadIndex-equivalent
owner audit. If the original recovery barrier remains false, leave
new-risk submissions fenced, not artificially authorized.

No model promotion, new Broker order, forced FX flatten, security
threshold change, memory guard bypass, or original trading strategy
modification was performed by R13.
