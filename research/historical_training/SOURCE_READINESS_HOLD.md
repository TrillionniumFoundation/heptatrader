# Laya source-health HOLD diagnostics (PAPER research only)

This change does not authorize training, evaluation, orders, production model
promotion, altered slippage, revised sample windows, or weaker quote rules.

A completed 24-probe source-readiness status of `HOLD_NEW_FUSION_WINDOW`
already denies prospective fusion/training regardless of later quote arrivals.
The former resource guard attempted to refresh a live source before honoring
the immutable failed probe outcome. During the weekend IB FX closure that
produced a secondary `NO_CAUSAL_ORIGINAL_QUOTE` exception, obscuring the
original `0/24` and source-health reasons.

For that **exact** HOLD state, this guard now requires the pinned original
config/period hashes and the source-prefix digest to remain unchanged,
evaluates the identical memory/PSI/source quality rules, records the original
24-probe counts and bounded reason identifiers, and forces
`admit_new_worker=false`. No extra Broker request or live quote read is
performed on a foreclosed admission decision; diagnostic fields explicitly
state that **fresh source proof was NOT collected**.

For READY states, the original fast `resident_live_source.refresh` is still
mandatory, including verification of original source chain, pinned prefix
bytes, current period, timestamp causality, 5-second quote TTL, source status
age, 20/24 coverage and resource constraints. Invalid/missing status, bad
digest, future period and malformed reasons fail closed. The original
`guard_files_sha256` release attestation remains required.

Operational interpretation: IB Gateway may run in the GNOME session while
`ibgateway.service` is inactive. Investigate the actual owning PID and
loopback 4002 listener; do not restart a live Gateway based only on the
unrelated systemd unit state. Official IB describes 2108 as dormant/available
on demand and 2119 as market-data farm connecting; neither provides a
completed healthy EUR/USD quote window.

IB spot FX closes around Friday 17:00 ET and resumes Sunday evening, holiday
exceptions possible. In the 2026-10-10 pocket4 observation the last original
quote was Friday 2026-10-09 17:00:00 EDT, exactly normal weekly FX close.
Never substitute an old quote or backfill an observed future target as live
admission. Existing source-health and readmission timers remain the sole
operators and are not redefined.

Rollback restores the previous systemd research-guard import path/config;
retain raw source pages, signed/hashed receipts and denial history. Keep the
separate 2026-10-10 guard branch reviewable until real reopened-market
freshness and independent evaluation can be observed.
