# Pocket4 — independent cache-vs-original monitor at the frozen 10:05 EUR/USD slot

**2026-10-13, pocket4 UTC+8** — strictly research-only, no trading, no
model training or promotion, no production health monitor replacement.

## Fixed evidence boundary

The pre-registered original price-only EUR/USD experiment uses four exact
entry times, with 10:05:00 UTC+8 as its final anchor. The first three
were irrevocably rejected because the original deployed readiness file
was either HOLD or older than the fixed 30-second TTL at the actual
decision. The separate 03:35/06:50 custody stream preserved exact monitor
bytes: both reported 24/24 usable, but their `at_ms` timestamps were
37.938s and 86.233s old. The old CENSORED judgments and lack of broker
orders must remain unchanged.

The installed original monitor computes 24 probes then sleeps *another*
60s; field polling found status ages up to 94.914s. Verified sealed
source-cache research (prior branch `972a375`) on real original IB
PAPER data gave exact original per-probe parity, 24 warm cached cycles
at 25% CPU, original source SHA re-verification at every use, 0/23
stale intervals and an 18.098s maximum pre-replacement as-of age.
Synthetic crash/rollover/tamper tests also passed. These are short
research trials, not durable production-monitor admission.

## New predeclared fixed-slot comparison

This commit adds
`research/train_v2/fx_fixedslot_cache_canary.py`:

- Bind to exact original frozen plan SHA256
  `02aa4a2662b743203cc925d09d3250bcc0e02155cf5d00bb319b0526448aaa04`
  and exact original seal SHA256
  `1f13ed5c7a8fcdc29cc0881bf5c7854c263876f5f89bb6bc87633d5b2b33860b`.
- Original user-time anchor: **2026-10-13 10:05:00 UTC+8**.
  Predeclare exact 09:59:40 cache-canary start and 10:05:02 readback
  (inside original +1..+4.5sec interval). Registration must precede
  the canary start by at least **30 minutes**.
- Pin original PAPER period, config bytes, old deployed monitor file
  path, frozen rule (24 probes; 20 usable; <=4 missing; 5s quote TTL;
  30s path/30s status TTL), candidate monitor source and verified cache
  module SHA256.
- Opt-in **sandbox-only** cached monitor to a **NEW 0700/0600**
  output path, with `--verified-sealed-cache --refresh-seconds 15
  --max-cycles 24`, CPU quota 25%, memory max 384MiB, no network
  address family other than AF_UNIX, NoNewPrivileges, Restart=no.
  It is started by a one-time systemd user calendar timer; no default
  or production source-readiness unit changed.
- At the exact original +2sec target, one separate one-shot readback
  captures BOTH source monitor status files as exact private bytes,
  independently recomputes the same original 24-probe totals, original
  SHA256 `source_prefixes` metadata digest, PAPER period, quote
  freshness and producer `at_ms` age. Records a true, unmodified
  original health status even if it is stale.
- Any missing file, slow timer, late readback, invalid quote/rule/period,
  or unauthorized status becomes HOLD/MISSED; no retries or evidence
  overwrites (O_EXCL, stable file descriptor, fsync).
- A `healthy_at_exact_time` field means only that the **separate
  experiment status** satisfied the frozen health predicates in a
  research observation. It **cannot** rescue window 03's original
  preaction, turn on Train V2 model fitting, or supply the missing
  canonical Root/Owner/Witness/LedgerWriter product proofs.

An installed systemd transient timer indicates that the process is
**scheduled**, not that future source data or comparison outputs already
exist. The definitive result is the actual immutable on-time
`comparison.json` produced only after 10:05; no prospective effect
is claimed in advance. If this check misses the original clock window
it is explicitly MISS/HOLD forever.

## Why this is the next experimental gate

The source-health cadence mismatch is a concrete engineering defect
independent of the negative 15-minute Ridge trading economics.
A within-decision-window cached-vs-production original status pair
will help distinguish the 60sec-plus-evaluation source-monitor
publication lag from quote-event/path gaps. The independent process
does not change the ongoing experiment's original timestamps, inputs,
failure statuses, or continuous source capture.

Do not deploy a 15s cache-enabled monitor as the production source
until the already-frozen four-window run ends, independent long-duration
cold-start/rollover/CPU-contention tests complete, rollback is frozen
and all FX/BTC/PnL/Account NAV original business boundaries are
reverified. No rule relaxation, order/cancel, broker account flow,
strategy fit/promotion, positive broker PnL or native Train V2 120m
admission is authorized.

### Private paths

Research worktree:
`~/work/heptatrader-fx-fixedslot-cache-shadow-20261013`.

One-time comparison root:
`~/.local/state/heptatrader/operator-evidence/fx-cache-fixedslot-1005-20261013-v1`.

Original source output (read-only):
`~/.local/state/heptatrader/neuron-learning/source-readiness/61995fcedc2993d7ac33d2ed86c9613068c5a0958f/status.json`.

New cached sandbox output:
`fx-cache-fixedslot-1005-20261013-v1/cache-status.json`.

No private IB passwords, source quote files or trade-account identifiers
are committed to the code repository.
