# Original IB PAPER source-health: verified sealed-decoder cache

**2026-10-13 (pocket4 UTC+8)** • **Research-only candidate, NOT installed in product monitor.** The existing fixed EUR/USD future study, all original window-00 CENSORED records and ordinary FX/BTC PAPER execution remain unchanged.

## Proven bottleneck and why raising CPU alone is premature

`source_readiness.assess` applies the frozen versioned `heptatrader.quote-readiness.v1` rules: 24 one-minute probes, 300-second maturity, 5-second source TTL, 30-second inter-quote path gap, original 20/24 minimum READY boundary. The separate prospective price-only study requires the actual producer `at_ms` to be no more than 30 seconds old at the fixed decision.

The installed monitor still uses CPUQuota **25%** and a legacy cycle cadence of **60 seconds AFTER assessment**. A previous isolated source lab at 25% CPU and 15s requested cadence observed **7/7 stale publication intervals**, reaching **33.986 seconds** old before replacement. A separate 50% CPU lab was fresh for 11/11 intervals but was short-lived and not a production release.

A real on-pocket4 read-only timing probe on the original strict monitor found:

| Single original 24-probe assessment | Wall seconds |
| --- | ---: |
| Reading, hashing and JSON decoding original source segments | **4.298** |
| All 24 health probes including source selector | **0.012** |
| Whole assessment | **4.326** |

The primary bottleneck is repeated Python strict JSON decoding, NOT computing 24 probes. The actual source root contained **261** source segments: **256 fully archived and SHA-256-pinned**, **5 unsealed**.

## Conservative source cache: hash every time, decode once

The opt-in module `research/neuron_learning/laya_cell/verified_sealed_readiness_cache.py` wraps only the source-readiness reader, never trading or the original quote collector.

Every assessment still calls the *unmodified canonical* `source_archive.inventory(config, feed_paths)`, which:

1. Re-parses and verifies the original append-only genesis ledger and 32-segment archived pages.
2. Opens and **hashes the exact original bytes** of EVERY sealed source file via the original `O_NOFOLLOW` + owner/mode/size + SHA-256 algorithm. Fails closed for truncated/replaced/reordered pages or files.
3. Produces the exact pinned `read_source` prefix tuple. The cached header and quote records can be reused ONLY when that pinned entire prefix tuple exactly equals the original strict parser's previously accepted output.
4. Calls the existing strict `read_source` parser on **every unsealed source on every read**, including partial tails; new/changed sealed files must be strictly parsed once before admission.
5. Performs the existing PAPER period SHA-256 check, as-of quote availability, unique timestamp/price conflict checks and original output ordering. A changed config, backward clock, archive ledger conflict or stale source fails closed.

The only additional trust assumption is the same original local SHA-256 archive verifier; this is **not broker-signed original data**. The cached decoded quote data never represents an IB fill, model, independent cryptographic Witness, native Agentd Owner or production training authorization.

Cache bounded to **320 segments or 180,000 quote records**, evicting older entries instead of relaxing integrity gates. The feature is disabled by default and only reachable through explicit
`--sandbox --verified-sealed-cache`. Product monitor command line, CPU budget and default 60-second cadence do **not** change.

## Real original quote parity before the timed canary

On the actual pocket4 source with a single fixed as-of epoch, three identical semantic checks were performed: existing reader, cold verified cache, warm verified cache. They exactly matched:

- all **24 individual probe results**, rejected statuses, applicable original quote timestamps;
- READY/HOLD, usable/missing counts, freshness and source-period rules;
- every downstream model/automatic-trading flag (still false).

Original quote file hashes could legitimately differ across the sequential physical reads because the **unsealed real quote tail was still being appended**. This is not a future quote substitution. Every quote was filtered against the **same fixed as-of** timestamp, and the canonical sealed page inventory was reverified at each check.

| Actual unfenced wall time | Seconds |
| --- | ---: |
| Existing exact original loader + probes | 3.99537 |
| Cold strict cache loader + probes | 3.83773 |
| Warm SHA256-verified cache loader + probes | **0.27279** |

Original committed output:
`~/.local/state/heptatrader/operator-evidence/fx-readiness-verified-cache-20261013-v1/real-causal-24probe-parity-v1.json`

SHA-256:
`925b5f9c854d1ba143df4612340033e9bed3597cdefc1b5fae31a37d28e6ef4b`.

## Actual 25%-CPU systemd sandbox trial

A **new isolated** transient service
`heptatrader-health-cache25-20261013.service`
used the exact original source, `--sandbox --refresh-seconds 15 --max-cycles 24 --verified-sealed-cache`, CPUQuota **25%**, 384MiB hard memory ceiling, AF_UNIX-only socket family, NoNewPrivileges, `Restart=no`, 15-minute hard runtime. It wrote only its own private 0700 operator-evidence directory, not the existing monitor status path, original source, trading runtime or precommitted future study.

Actual systemd result: natural exit 0 after **5m48.528s wall / 10.829s CPU**, peak **119.9MiB**. The second verifier re-parsed the **unaltered systemd journal's** 24 authentic event lines, requiring exact ordinal/clock/cadence/shape and one exact final source cache terminal counter. It refused any missing/fake record or authority claim. Crucially, it computed the oldest possible status immediately **before the next real publication** and not simply a nominal refresh interval.

| Frozen 25% CPU / 15sec cadence | Previous no-cache | New verified cache |
| --- | ---: | ---: |
| Cycle count | 8 | **24** |
| Actual completed inter-publish intervals | 7 | **23** |
| First cold assessment | N/A | 16.098s |
| Warm assessment mean / max | ~16s | **1.098 / 1.492s** |
| Maximum as-of snapshot age BEFORE replacement | **33.986s** | **18.098s** |
| Intervals violating frozen 30s freshness budget | **7/7** | **0/23** |
| Stale exposure duration | **23.097s** | **0s** |
| Actual READY / probe state | READY throughout | **24/24 READY, 24 cycles** |

Actual parser/cache reuse counts:
- 24 complete source inventory+SHA-256 revalidations (no trust shortcut);
- 256 strict first-time archived segment JSON decodings;
- 5,888 reuse hits for archived, unchanged original segments;
- **120 strict re-parses** for the 5 unsealed source segments;
- 116,289 cached quote records, below the 180,000-record bound;
- 0 broker orders/queries or native model promotions.

Unmodified journal evidence (canonicalized from systemd) and independent calculations are sealed privately under:

`~/.local/state/heptatrader/operator-evidence/fx-readiness-verified-cache-20261013-v1/`

Independent `cached-sla-review.json` SHA-256:
`ab9b06ed41ada195c986021b7e3e47a38a57b075fe3a97e9b75c6edd34ff4802`.

`cached-original-24-cycles.jsonl` SHA-256:
`acc66f9c8069d85e2c64ef6360edaccea3df4666dbc5f87e5772bb4ef5b3615c`.

The cache counters come from the **same research process**; journal+separate audit can verify their presence and consistency, not provide independent hardware signing of physical CPU quota or upstream IB ticks.

## Test and trust boundary

Relevant regression suites include negative cases for:
- same-length SHA-pinned sealed body tampering, changed page digest, reordered archive pins;
- config/period changes, backward wall clock, as-of availability after originally frozen timestamp;
- mandatory re-parsing of unsealed segments even when frozen body is unchanged;
- bounded memory eviction, no-readiness-cache without sandbox, old default monitor path preserved;
- deliberately forged/missing/duplicate journal cycles, impossible timestamps or counters, late statuses;
- false product owner/signature/order claims and overwriting write-once results;
- original source-archive validation, Train V2 source-readiness, old forward/custody and original cost guard compatibility.

This research run is more informative than merely increasing monitor quota to 50%: it achieves observed continuous 30-second status freshness while keeping **25% CPU** under actual concurrent pocket4 workloads.

### Release status / next hard gate

**Research candidate PASS for 24-cycle benchmark and semantic parity; product rollout HOLD.**

The 24-cycle run does NOT prove sustained reliability across archive page rollovers, corrupt/stale source files, heavy CPU load, cold restart, clock discontinuity, holiday sessions or real-world service recovery. The original true 00:20 `CENSORED` and 15/30/60/120-minute outcomes cannot be backfilled. Do not alter the active 03:35 / 06:50 / 10:05 prospective experiment or its source identity.

Before replacing product monitor (and only in a separately approved maintenance window after the active study), finish hostile source rollover/clock/load tests and 30-second precommit duty cycle at real future decision times, stage an explicit rollback to identical previous service identity, and revalidate ordinary PAPER Execution, FX/BTC drivers, PnL/Account NAV, source monitor and model behavior independently. **No model/Owner/Witness/LedgerWriter/production or all-in PnL admission follows from this cache alone.**


## 2026-10-13 additional synthetic archive-rollover fault injection

A **separate** regression runner
`research/train_v2/fx_verified_cache_rollover_fault_injection.py`
was added to the research release. It uses only the original
`laya_cell.tests.test_source_archive` **synthetic PAPER fixture**
under a private `TemporaryDirectory` and never opens real IB source
files, production status paths, trader APIs or credentials.

Eight strictly verified phases: original 166-source cold/warm parity;
first real canonical archive-API rollover to 192 synthetic source
segments / six archive pages; warm and cold post-roll parity;
synthetic power loss *after* seventh page fsync but *before* its
ledger checkpoint with fail-closed refusals from **both** the strict
baseline reader and cached reader; canonical writer-only orphan
recovery and exact 224-source / seven-page parity; and finally
same-length corruption of one newly archived segment, rejected
by **both** readers. No cache hit may bypass the source SHA256 pins.

Private write-once initial execution receipt on pocket4:
`~/.local/state/heptatrader/operator-evidence/fx-cache-adversarial-rollover-20261013/synthetic-rollover-v1.json`,
SHA-256
`941d782433d020bd8057616c62003faf557dbd24e0fb0b2a445e3766795f49d1`.
The original runner was preserved alongside the report at
`synthetic-rollover-v1.py` (SHA-256
`c8c827fc13cb02eff949d74150dcd7437e52f710fd2aa472d4f0d0ebf251f878`).
The additional repo runner accepts a NEW `--output` receipt path
and refuses overwrites.

Verified in a **distinct clean review worktree** copied from the
existing *uncommitted* candidate, without editing its original
working tree: 17/17 verified-cache tests, 16/16 independent SLA
review tests, 18/18 cadence budget tests, 9/9 old cadence tests,
22/22 source-archive tests, 2/2 new full rollover/recovery tests.
**84/84 pass.** The original candidate’s preexisting 24-cycle
25%-CPU benchmark is unchanged and is not evidence of production
cold-start or continuous real archive rollover qualification.

**Rollout remains HOLD**: lab-only `--verified-sealed-cache --sandbox`;
no mutation to the installed 25%-CPU source monitor while the
pre-registered 03:35/06:50/10:05 original research is active.
At **03:35** the original monitor had `READY`/24 useful probes but
an original `at_ms` **37.938 seconds old**, causing a correct frozen
`CENSORED` response. Its raw status was durably retained in an
independent *research* custody stream and reverified on qian-qi,
not accepted as a product Broker/Witness attestation.
