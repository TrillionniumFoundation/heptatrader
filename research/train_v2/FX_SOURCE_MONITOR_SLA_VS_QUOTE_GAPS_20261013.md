# Separating health-monitor publish SLA from genuine IB quote eligibility

**2026-10-13, pocket4, research only.** No trade, order, cancellation,
native model training, production source-health cutover, Owner/Agentd/Witness,
broker-signed quote or account PnL admission is authorized by this work.

## Why the completed future cohort was correctly censored

The entire predeclared four-window / four-horizon EUR/USD future price-only
cohort is **finished, 4/4 decisions CENSORED, 16/16 future prices CENSORED**.
The finite worker exited normally, with original_preaction_frozen_count=0,
mature_quote_only_samples=0, broker_orders_sent=0.

The first 00:20 window lacks an independent original moment-of-decision
health snapshot. The later 03:35, 06:50 and 10:05 snapshots were
physically retained and locally SHA-verified. All three showed
**READY, 24/24 usable**, but they were respectively
**37,938 / 86,233 / 85,031 milliseconds old** when the original
research decision occurred. Frozen gate: source status no older than
**30,000 milliseconds**. The original, unchanged HOLD decisions were
correct; none can be rehabilitated by later timestamps or prices.

This exposes **two distinct eligibility axes** which must not be conflated:

1. **Monitor publication freshness:** can a complete, source-identity and
   24-probe verified status be published often enough to satisfy the 30s
   prospective decision bound, including worst age immediately *before*
   the next publication?
2. **Actual market quote availability:** does the original verified IB
   PAPER quote and 24-probe history *at that original evaluation time*
   satisfy the unchanged **5s IB quote TTL**, 20/24 minimum usable, <=4
   missing, original broker-reported quote expiry, path and endpoint rules?

The currently installed production read-only health monitor retains
the default 60-second **sleep after evaluation**, under 25% CPU.
Previous sandbox timing evidence measured 8 cycles at 25% CPU with a
**33,986ms** worst between-publication age and 19.2478% stale
exposure. The 50%-CPU, 15-second sandbox comparison recorded 12 cycles
with **23,977ms** worst age and zero stale exposure in that short study.
Neither is an independently trusted signed broker quote.

Another staged 24-cycle run passed its timestamp budget but reported
3 genuine HOLD_NEW_FUSION_WINDOW status results despite usable=24
because original quote availability (5s TTL) was stale. A postevent
reconstruction measured original IB quote ages above 5s.
Those HOLD events remain **HOLD** and are not monitor publishing failures.
The staged 24-cycle audit explicitly withheld unattended production cutover.

## Current bounded experiment

A NEW separate, predeclared **24-cycle, 15-second, 50%-CPU** sandbox
monitor is running on the actual original local IB PAPER feed. It uses
the existing candidate implementation's opt-in refresh 15s and
max-cycles 24 in sandbox, not the active production instance.
The original production source-health ExecStart, Python SHA and quote
rules are unchanged. The transient research unit has:

- NoNewPrivileges=yes, RestrictAddressFamilies=AF_UNIX,
  MemoryMax=384M, CPUQuota=50%, Restart=no, finite execution cap.
- Original PAPER config/period identity and existing original quote
  collector, with **no broker requests or orders** from this study.
- Monotonic 15-second target cadence; log each original true
  source_asof_ms, actual published_ms, live state and 24-probe usable.
  Local source_asof_ms is not reset on publish.
- The study plan and source SHA are sealed in 0600 private evidence
  **before** the first cycle.

Private plan and outputs:

~/.local/state/heptatrader/operator-evidence/fx-source-freshness-separated-20261013/

The deterministic independent stdlib audit is
research/train_v2/fx_source_monitor_timing_audit.py. It checks all
24 consecutive actual monitor source-as-of events; exact time order,
per-snapshot age, and **age from the previous original source timestamp
immediately before the NEXT actual publication**. This rejects a
monitor which is fresh when it publishes but stale between publications.

The auditor separately counts READY and HOLD cycles and refuses any
time-safety result falsely promoted to market-source quality.
It verifies the **final exact persisted 24-probe status** against
its last cycle, exact unrelaxed 5s TTL, historical period and original
source_prefix_digest; prior intermediate full status JSONs were *not
durably retained* and may not be independently reconstructed.

Missing/duplicate/bogus journal cycles, changes to the pinned candidate
or installed production source hash, a wrong PAPER period, changed
minimum-usable/quote TTL, false READY with fewer than 20 usable,
fake age arithmetic, source stamp reversal, altered source prefix SHA,
loose file permissions, nonfinite JSON and attempts to overwrite
outputs fail closed.

## Actual pocket4 source run and independent verification

The **24-cycle** transient sandbox service
'heptatrader-fx-cadence-separated-20261013.service'
exited naturally with result success, zero broker queries/orders,
CPUQuota 50%, memory peak **130.5 MiB**, and **5m56.654s** wall time.

Exact frozen original journal (24 cycle events):
'journal-original-v1.log', SHA-256
'2d954912fa0215508027489ddf0e58ddb81d7265776f42a2d1c3afa375776dca'.
Both source snapshots were SHA-pinned:
candidate '0172c3470e7e0b821c39aac00eb077d18d436b1711c32d46f4a99bfd32199f12';
unchanged active product
'63bcef1a6d8bc22e3c2fb10d1dcebc1457559538e6a959004e61eeb88c09c320'.

Independent O_EXCL owner-private report:
'final-independent-freshness-and-quote-v1.json'
SHA-256 '95f178c9ee61e0b46ad368013afbda98f1314c7b9b019eda5877a5ac0700c568'.

| Measured fixed protocol metric | Actual result |
| --- | ---: |
| Durably journaled local source evaluations | **24/24** |
| Original source READY | **17/24** |
| Original source HOLD | **7/24** |
| Maximum local source age at publish | **12,241 ms** |
| Maximum local source age immediately before next publish | **27,241 ms** |
| Publishing intervals exceeding 30,000 ms | **0/23** |
| Source READY in all cycles | **NO** |
| Models promoted / broker orders issued | **0 / 0** |
| Unattended production monitor cutover approved | **NO** |

The 7 HOLD cycles are real unqualified original-source observations.
The last full persisted status shows only 18/24 probes usable;
some earlier HOLD results had 19. They must not be overwritten or
relabelled by the independently demonstrated monitor-timing performance.
This audit validates the **journal timestamps as locally recorded** and
the exact final 24-probe source JSON. Intermediate complete health
status bytes were *not* saved and there is no broker-signed/hardware
independent publication timestamp.

## Result and release interpretation

The test is a short, original-source, read-only operational **diagnostic**.
Passing 24 measured publication intervals establishes only
PASS_SHORT_SANDBOX_TIMING_NOT_PRODUCTION_RELEASE, not proof of
multi-hour CPU contention, service cold start and graceful/abrupt restart,
PAPER broker connection recovery, the IB source's market availability
or true original 30s decision as-of on a new future cohort.

**Continue to HOLD** unattended production monitor cutover, model
training admission and automatic strategy promotion until independently
verified durability and rollback criteria are closed. No permanent
systemd timer or production service override is introduced by this
artifact. Existing failed experimental windows remain unchanged.
