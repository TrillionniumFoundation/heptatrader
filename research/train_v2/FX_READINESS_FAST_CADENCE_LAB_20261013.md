# Original PAPER source-readiness cadence: isolated short-cycle lab

**2026-10-13, pocket4 local UTC+8.** Status: **RESEARCH LAB PASS,
NO PRODUCTION DEPLOYMENT**. Related source mismatch issue:
TrillionniumFoundation/heptatrader#241.

## Verified defect in the current deployed monitor

The installed exact read-only producer
`laya_cell/source_readiness.py` has SHA256
`63bcef1a6d8bc22e3c2fb10d1dcebc1457559538e6a959004e61eeb88c09c320`.
Its unchanged algorithm computes 24 five-minute overlapping probe
outcomes and returns `READY_FOR_REVIEW_NOT_AUTHORIZATION` only at
>=20/24 usable, <=4 missing, original source 5s freshness. Its
**existing loop sleeps 60 seconds AFTER each calculation**.

The frozen current-price study independently requires a monitor
`at_ms` no older than 30 seconds at its fixed original decision.
Actual pocket4 consecutive producer `at_ms` samples were
`1791824841378`, `1791824916678` (75.300s) and
`1791824993066` (76.388s after the second). One observed
READY/24 sample was already 78.908s old when checked. File
publication lag after the producer's initial evaluation clock was
approximately 16.3s on the original 25% CPU quota.

**Do not reinterpret** the old 00:20 source-health CENSORED receipt
based on these later timestamps; no original 00:20 full health JSON
was preserved. Do not edit the original four-window precommit, original
readiness status file, quote TTL, source eligibility or missing sample
labels mid-experiment.

## Isolated code change, default preserved

`research/neuron_learning/laya_cell/source_readiness.py` adds:

- Opt-in `--refresh-seconds` (10..60), **default 60**. Default still
  waits 60 seconds **after** evaluation, preserving original cadence.
- Non-default refresh may be used **only with `--sandbox`**.
- Start-to-start monotonic scheduling for faster sandbox cadence, with
  a minimum 1s recovery pause on long assessment overrun. No
  speculative old timestamps or re-stamping original source times.
- Bounded opt-in `--max-cycles` (1..24), also sandbox only; the
  worker exits naturally. No permanent timer/daemon is installed.
- Lab-only summary reports true source `at_ms`, actual publish time,
  assessment duration, READY/HOLD, usable probes, and explicit
  no-trading authority. Does not publish source tokens or account data.

No original `RULE` threshold, quote parser, source hash, trading
driver, backend model or native Owner/Witness code was changed.

## Actual pocket4 proof: four sandbox cycles at CPU quota 50%

A separate, transient `heptatrader-health-fast-lab-20261013.service`
ran with AF_UNIX-only sockets, 384MiB cap, NoNewPrivileges and
`--refresh-seconds 15 --max-cycles 4 --sandbox`.

| Cycle | Original assessment start (epoch ms) | Published snapshot age |
|---|---:|---:|
| 1 | 1791825570968 | 7,605ms |
| 2 | 1791825585968 | 7,513ms |
| 3 | 1791825600968 | 8,473ms |
| 4 | 1791825615968 | 7,697ms |

Source assessment starts are **exactly 15,000ms apart**. All four
status results were `READY_FOR_REVIEW_NOT_AUTHORIZATION`, 24/24
usable, `orders_sent=0`, `trading_authority=false`. Lab process
exited naturally 0 with **15.804s CPU / 52.787s wall time** and
115.8MiB peak memory. Maximum publish age was 8.473s, allowing
headroom under 30s at each measured check. A separate one-shot
50%-CPU assessment was 8.286s wall, 4.163s CPU, 111.9MiB peak.

Write-once, private pocket4 cycle-event log:
`~/.local/state/heptatrader/operator-evidence/fx-health-cadence-lab-20261013/cycles-v1.jsonl`

Cycle-event SHA256:
`7d6c36796bd145378b972f286cf43bd669cfa645fc2956a36e1e5ab133d424ce`.

## Remaining hard gates

This is only a **short controlled feasibility run**, not a sustained
workload or a signed independently attested IB quote. The existing
frozen original research and health custody workers remain untouched,
and `CENSORED` at 00:20 remains irrevocably CENSORED.

Before any production monitor cadence rollout: run separately
precommitted repeated load/cold-start/throttle/CPU contention tests,
assert actual persisted snapshot `at_ms` age <=30s at every new
future decision, and verify source continuity, FX cost, product-native
Owner/Witness/LedgerWriter and account Flex/NAV independently.
Do not use this lab to lower the 5s original quote TTL or turn on
model promotion. Production release remains **HOLD**.
