# 2026-10-14: frozen 480-slot verified-cache cold-prewarm source health

**Scope:** research-only original IB PAPER quote health; **no live monitor
cutover, no trading, no model update, no reward or production authorization**.
This work addresses the previously observed 30-second fresh-status SLA
failure, not the original strategy's negative PnL or its commercial validity.

## Baseline negative control and pilot, neither reclassified

- Prior 480 x 15-second original archived health trial at 25% CPU:
  293 genuine READY, 187 non-READY, 479/480 full-source results;
  **31.654-second** maximum source-asof-to-next-publication age, violating
  the existing 30-second health TTL; original final SHA256
  `8ac37d9c202505e00bcc90daf7ede67dabdc7167eafef526ee17457cc1745353`.
  The late cold-cache first assessment is a permanent failure.
- Later PREWARM pilot v1: 12/12 invalid due to a genuine research wrapper
  bug mapping `original_period_sha256` into the established
  `period_sha256` monitor verifier. All 12 remain failed.
- Separate repaired PREWARM pilot v2: 12/12 original status files,
  **11 genuine READY, 1 true quote-expiry HOLD (5122ms > 5000ms)**;
  18.029-second peak age before the next actual publication, CPUQuota 25%,
  real original source strict revalidation. Final SHA256:
  `8cc5b7c5d6f44ff01b28b4abc0042c3a11d8ae440fd7ef4639a3a11224a6a0dd`.

The new 480-slot code pins the two previous actual final hashes and
refuses missing, changed, or otherwise reinterpreted baseline evidence.
A code reuse/frozen period mismatch is a hard failure, never a pass.

## New 480-slot original source study

**New future plan** frozen before all observations. Exactly 480
slots spaced 15 seconds apart, for two hours; 25% CPU, 384 MiB max
memory, no automatic restart, no trading/network sockets except AF_UNIX.
The plan pins this exact research runner source, current sandbox source
parser and verified-sealed original cache SHA256, original installed
read-only monitor SHA256, source config, exact PAPER period, old failure
hash, repaired 12-slot pilot hash, 24-probe readiness rule and
original immutable 5-second quote TTL / 30-second status age requirement.

The source cache is **fully initialized before any slot** and cannot
be retrospectively counted as a successful fixed grid observation.
Its original source archive inventory is re-SHA256 checked before every
subsequent assessment; original quote segments that are not sealed
are strictly reparsed each time. The previous cache's successful
12-slot cold prewarm does not automatically prove this 480-slot
sustained run will pass under future source/IB transport faults.

Every slot has exactly one scheduled actual attempt; capture-time miss,
source integrity mismatch, quote expiring, original HOLD, 30-second
source-age breach, publishing too late or sandbox failure all remain
HOLD/CENSORED. Original full JSON and linked receipts use private
write-once 0600 files, within a 0700 evidence directory.
No old slot is backfilled/retried, no success is inferred from a late
publish, no threshold/quote-age relaxation, and the full chain remains
locally hashed rather than independently signed by IB.

## Result gates — **DO NOT ASSUME PASS before 480 complete**

Eligibility to *review* a reversible production health-monitor canary
requires ALL of the following in **this specific new prospective plan**:

1. **480/480 actual original full-status receipts**, independently
   verified against original source metadata and chained hashes.
2. No missed fixed source observation; no prewarm overrun.
3. Max source as-of age at its own physical publication **<=30 seconds**,
   and before the next actual publication **<=30 seconds**.
4. CPU/memory/sandbox confinement, source period/config identity and
   quote integrity retained; no unaccounted mutations.
5. All genuine original IB quote TTL/gaps and HOLD events preserved,
   **not** converted into READY. "480/480 original status files" does
   not require all 480 to be READY, because real market source can fail.
6. Cross-host readback on pocket4 and qian-qi (and additional physical
   hosts if available) using a **separate original-quote verifier**.
7. Explicit rollback/cold recovery and load contention proof before
   any production monitor cutover. This code does not do a cutover.

The 30-second freshness is original source-monitor **status age**,
not a new guarantee that all FX trades would be profitable or that
Native Train V2 gains 120-minute product admission. No code in this
experiment changes original health policy, live FX/BTC PAPER drivers,
IB Gateway, PnL, Train V2 Root/Owner/LedgerWriter/Witness or signed
broker-source identity.

Run against a strictly new private root; execute the separately
frozen program once under an existing transient user service.
No persistent timer or installation to production systemd targets.
Existing final bad receipts MUST remain untouched. The user can stop
this particular trial; incomplete future windows are retained as
failures, never retroactively counted as successful.

## Expected evidence and release

Source module:
`research/train_v2/fx_source_cache_prewarm_480.py`

Private original evidence:
`~/.local/state/heptatrader/operator-evidence/fx-prewarmed-original-cache480-20261014-v1/`

Tests:
`tests/python/test_fx_source_cache_prewarm_480.py` and existing
12-slot/verified cache/source health suites; fail-closed SHA and period
tests included.

**Production:** HOLD. Even a true source SLA 480/480 pass does not
qualify a new FX entry policy, execution fee/slippage, financing,
realized account net PnL, IB Flex/Activity, native Owner/Witness or
model-alpha promotion.
