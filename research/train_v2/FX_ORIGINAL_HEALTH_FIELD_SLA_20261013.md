# Original PAPER source monitor 30-second freshness: corrected field audit

**2026-10-13, pocket4 local UTC+8**. **Verdict: current product
health-snapshot freshness FAIL under the separately precommitted study's
30,000ms rule; no production/service mutation or model promotion.**

## A necessary correction to previous evidence

An initial local path observer erroneously hard-coded a shortened
**42-character** `source-readiness` subdirectory instead of deriving
the real **64-character PAPER period SHA256** from the resident source
configuration and exact `pnl-period.json` bytes. Its two 60s/90s
`ENOENT` logs are **INVALID diagnostics**. They do **NOT** demonstrate
any filesystem disappearance, mount namespace change or monitor
malfunction, and must never be cited as such.

Both invalid original logs are retained, not erased, with a separately
write-once correction:

`~/.local/state/heptatrader/operator-evidence/fx-source-path-continuity-20261013-v1/invalid-path-diagnostics-v1.json`.

Correction receipt SHA256
`7b4efe4aec2840ebb932d9983d1a429a6a3120ecbbba7db03a5503c5856bb87c`.

The corrected two follow-up observations derive the exact source file
directory from `config.period_sha256`, check its length is 64, and
verify `SHA256(config.period_path raw bytes) == config.period_sha256`.
The valid period is
`61995fcedc2993d7ac33d2ed86c9613068c5a3ecd0d1c377e612639cd5a0958f`.
The corrected sampling path exists and is readable in **every** probe.

## Real deployed monitor, unchanged

`heptatrader-learning-source-health.service` remains a 25% CPU,
384MiB memory, `AF_UNIX`, no trading authority, read-only source
monitor in its existing production namespace. The old installed
`laya_cell/source_readiness.py` runs 24 one-minute-spaced, fixed
five-minute horizon probes, reports READY only with >=20/24 usable
and original quote freshness <=5s, then waits **60 seconds AFTER
each potentially CPU-throttled assessment**. This is a fixed rule
and cannot be post-hoc shortened by changing a frozen experiment.

The already sealed source-only future study (00:20 / 03:35 / 06:50 /
10:05 local; 15/30/60/120m price maturities) requires an as-of
monitor `at_ms` no older than **30 seconds** at the original entry
decision. Source READY with `at_ms` older than 30s correctly remains
ineligible. This monitor is **not** a model or trade admission system.

## Actual correct-period sampling

Read-only, owner-private, write-once output under:

`~/.local/state/heptatrader/operator-evidence/fx-source-path-continuity-20261013-v1/`

Two distinct correctly scoped 90-second monitors sampled
approximately every 250ms:

| Actual observation | Valid v1 | Stronger v2 |
| --- | ---: | ---: |
| Samples | 360 | 360 |
| Path missing / invalid | 0 | 0 |
| Producer states READY | 360 | 360 |
| Sampled producer ages <=30,000ms | 59 | **49** |
| Sampled producer ages >30,000ms | **301** | **311** |
| Maximum reported producer age | 92,376ms | **94,914ms** |

The second v2 sample captured the original `producer_at_ms` separately
from each observer `at_ms`, and the standalone research validator
**recomputed** `age_ms = at_ms - producer_at_ms`, SHA256-pinned raw
sampling bytes, the exact PAPER period, all 360 ordered clock samples,
bounded sampling intervals, counts, source file identities and the
correction that disqualifies the earlier false-path evidence.

V2 raw sampling SHA256:
`3f9362106fa65534bc6181e0fb2c1f26a1ce288d3e68e37bb2985ed7f0329181`

V2 original sampling summary SHA256:
`9d65cdbed92740fa20d98cb89ae1a7f346c8953f2c2e6a5ea5ecc0aa1826298a`

Independently recomputed V2 audit:
`~/.local/state/heptatrader/operator-evidence/fx-health-sla-field-audit-20261013-v1/independent-field-sla-v1.json`

Audit SHA256:
`0b2257c92fd93a51a943a7690b54e3385f25e5d2288534be6056a890347acf00`.

**Field interpretation:** 311/360 was *not* 311 genuinely independent
market opportunities: samples are autocorrelated 250ms probes over
one 90-second epoch. It demonstrates failure of continuous 30-second
monitor publication freshness at many actually sampled wall times,
not a universal long-run SLA or a direct IB quote feed outage.

No individual raw original IB ticks were independently broker-signed;
the monitor source SHA256 recorded by the sampler is **local** custody,
not a WITNESS receipt or proof that the source server was correct.

## Baseline vs sealed-cache candidate (separately run)

Existing frozen, isolated 25%-CPU `verified_sealed_readiness_cache`
research has already rehashed all original archived source bytes
each assessment, reused only SHA256-verified strict decoded pages,
reparsed unsealed tails and reproduced all 24 original probe decisions.
One real 24-cycle short canary reported no 30-second stale intervals,
max pre-next-publish age **18.098s**; the baseline uncached isolated
25%-CPU 15-second cadence previously showed **7/7** stale intervals.

Both are **research** not production durability proof. There is no
safe inference that a 24-cycle canary survives source archive rollover,
cold restart, hostile cache modification, CPU starvation or connection
failure. Merely raising CPUQuota or using a cached `READY` label
would not establish a product-native Owner/Witness or real strategy
economic alpha.

## Validation and release gate

New pure-Python adversarial tests are in
`tests/python/test_fx_health_live_period_field_audit.py` and require:
- Exact full 64-char period + precommitted plan identity, explicit
  invalidation of prior 42-character false-path sampling.
- Original producer as-of timestamps and independently recomputed
  ages; reject impossible future clocks and falsified age figures.
- 360 ordered sampled records, bounded intervals, SHA256 of exact
  raw JSONL and full disjoint READY/HOLD/stale/absent counters.
- No disguised trade/reward/model promotion fields.
- Private 0600 original evidence and write-once O_EXCL output.

**Operational decision: product monitor REPLACEMENT = HOLD until the
current 03:35/06:50/10:05 prospective price and independent source
custody studies finish, then separately stage SHA-verified candidate
with explicit old-service rollback, cold/archive rollover/corruption
tests, sustained 30-second freshness at original decision boundaries,
and full PAPER driver/FX/BTC/PnL health verification.**

This study sent **zero** broker queries/orders, made **zero** changes
to source data or running services, did not create a model or recover
original window-00 CENSORED evidence. Canonical Root/Owner,
LedgerWriter/Witness, true logged behavior propensities, and complete
broker Flex/Activity/account NAV remain independent HOLD gates.
