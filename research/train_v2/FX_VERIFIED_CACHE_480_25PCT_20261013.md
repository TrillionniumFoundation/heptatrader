# Pocket4 original-source verified-cache: 480 future cycles at 25% CPU

**2026-10-13 • Audit/research only. No production monitor cutover, no trading authorization.**

## Prior hard evidence

The original 15/30/60/120-minute four-window PAPER source study ended with
**0/4 eligible preactions and 16/16 CENSORED outcomes**. This is not
retroactively repaired by future READY health assessments. Existing
`heptatrader-learning-source-health.service` has 25% CPU and sleeps
60 seconds *after* assessment, often publishing as-of ages >30 seconds.

Previously completed benchmarks are distinct: four physical hosts verified
480 cycles with **475 real READY / 5 real HOLD**, full-source snapshots,
and 27,607ms worst next-publication age at 50% CPU; two separate 24-cycle
25%-CPU SHA-verified cache tests showed worst ages 18,098ms and 21,907ms.
The 11-cycle real source-monitor cutover was reversed successfully.
All previous receipts explicitly **HOLD permanent monitor cutover**.

## This new study and immutable policy

Create a NEW, future-precommitted, nontrading 480-cycle source-health archive
at 15-second cadence and the original 25% CPU budget. Do not represent this
as the previous 480 sample; do not train, submit broker orders, or change
any PAPER FX/BTC driver, model, risk, trader or source files.

The sampler `research/train_v2/fx_verified_cache_480.py` explicitly pins:

- Currently installed original health source SHA256
  `63bcef1a6d8bc22e3c2fb10d1dcebc1457559538e6a959004e61eeb88c09c320`;
- Existing opt-in source health module SHA256
  `8cbedb6a5f792891f0141f7b2ae0da91a7738080cd14c45995436ed55473c309`;
- Previously reviewed cache SHA256
  `1d1bdea5702d1fab8c9412eb2cbf0d224dd34d2736d4ab822e71bff5b9acb61c`;
- The actual original PAPER period/config and untouched original
  24-probe/20-usable/4-missing/5-second quote TTL/30-second path rules.

The cache still runs the original complete source archive inventory and
SHA256 verifier at *every* evaluation, re-verifies sealed bytes before
each cached hit, and strictly re-parses unsealed segments each time.
No source completeness, ownership, expiry or trading authority is inferred
from cached data. The cache is bounded to 320 segments / 180,000 records.

One immutable plan precommits all **480** future 15-second start times
before the first observation. An overdue, corrupt, missing, future-dated
or modified source result becomes HOLD/CENSORED. A *genuine* original
`HOLD_NEW_FUSION_WINDOW` remains HOLD and is not counted as READY.
Every raw original source-health JSON is privately stored `0600`,
write-once and fsync'd before its publication timestamp is recorded.
Each cycle has an independent SHA-chained receipt, strict input source
identity, and true as-of and durable-published timestamps.

The study's complete trusted bound is the **maximum original as-of
age immediately before the next actual publication**, not just nominal
15-second cadence or ages sampled at publication. Individual late cycles,
cold startup, CPU contention and source archive rollover must remain
visible as failures.

## Execution, rollback and admission

The isolated worker may run as a single transient user service under
`CPUQuota=25%`, `MemoryMax=384M`, `RestrictAddressFamilies=AF_UNIX`,
`NoNewPrivileges=yes`, `Restart=no`, finite hard runtime, and the same
PAPER read-only sandbox. It must leave the installed health monitor
alone. No user-level persistent timer/unit is installed and stopping
the transient process never rewrites old records.

The previously proven hostile source archive rollover/recovery and
verified-cache tests are reused, along with new future-plan/schedule,
source-code SHA, old health RULE, 30-second cutoff, stale/false READY,
no-orders and append-only tests. A separate independent physical-host
replay must still validate **all 480** original records and statuses,
not a self-asserted final summary.

**Release: HOLD** until all 480 authentic cycles finish and independent
SLA/recovery/rollback and real workload tests pass. Even then this is
only a source-observability improvement. It cannot grant native Agentd
Owner/LedgerWriter/Witness, 120-minute model admission, valid original
behavior propensity, profitable execution, IB Activity/Flex or all-in
account NAV and PnL.
