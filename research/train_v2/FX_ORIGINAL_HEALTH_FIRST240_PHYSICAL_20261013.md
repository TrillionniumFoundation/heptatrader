# FX original quote health: 240/480 immutable cycles, physical-host verification

Date: 2026-10-13 (pocket4 UTC+08). Status: **PASS for exactly the first
240 frozen source-health cycles / HOLD for remaining 240 and production
monitor replacement**. The original 480-cycle, 15s-cadence one-shot worker
continues in its own sandbox. No trading, model fit, source producer
replacement, timestamp reset, broker API or native Owner admission.

## Exact original experiment

The original 2026-10-13 four-window EUR/USD PAPER price research finished
naturally: 4/4 decision attempts were `CENSORED` by
`ORIGINAL_READINESS_HOLD_OR_STALE`; 16/16 dependent price maturities
were `CENSORED`; no qualified price sample, fill, or broker trade resulted.
The later three original health snapshots were archived with 24-probe
READY status but were **too old** (approximately 37.9, 86.2 and 85.0 s)
for the frozen 30s decision-age rule. The first window's full original
decision-time health JSON was never captured and cannot be reconstructed.
The old evidence is preserved unchanged.

A separately preregistered, **480-cycle** original PAPER quote-health
study runs a bounded sandbox monitor every **15 seconds** (50% one-core CPU
quota, 384MiB, AF_UNIX only, no restart) and writes exact complete
24-probe status bytes and chained private cycle receipts; it does NOT
change the existing production monitor or trading services.

The new halfway check used the existing reviewed independent
`fx_original_health_sustained_frontier.verify_prefix(root, 240)`.
It did not relaunch the source collection, request original IB data, or
look ahead at cycles 240..479.

## First 240-cycle result

| Check | Actually observed |
| --- | ---: |
| Frozen cycle slots verified | **240/480** |
| Complete original source-status files | 240 |
| Exact SHA256-chain cycle receipts | 240 |
| Original source READY observations | **240** |
| Original source HOLD observations | 0 |
| Worst age at source-status publication | **12,607 ms** |
| Worst source age immediately before NEXT publication | **27,607 ms** |
| Worst actual assessment start offset from frozen target | **3 ms** |
| Existing source-status age gate | 30,000 ms |
| Independent broker-signed quote source | **NO** |
| Native model/trader/monitor release granted | **NO** |

The original plan was frozen for 480 cycles. The numbers above are a
**halfway prefix**, not a two-hour sustained verdict; a later missing,
HOLD or stale status must remain a failure in the final research receipt.
A READY health probe is an operational continuity observation, not an
economic forward model efficacy sample or signed IB price.

## Independently replayed original file bytes

Pocket4 exact pre-frozen identities:
- Plan: `490375e6cfc4cea69b0aeb8ece2bd836228d1b3d086793555ed35d920bc8f6be`
- Worker-start: `3b880a8bd59b515b128a5566e713767ac822862d695f1f8c4237bb018fbc0d59`
- Sandbox: `b18abaf07255b4183f96dcdad8ed5059cbbd8e2f82616c81cc5597fef513acaa`
- SHA chain head after cycle 239:
  `a73ee675430a2e78b1c09236d11046d322aec80dd7c5d32e4a1c74ed576a3b00`

Frozen prefix report:
`~/.local/state/heptatrader/operator-evidence/fx-original-health-crosshost240-20261013/independent-first240-pocket4-v1.json`
SHA256:
`41dadee62965ca2f648297a9d2e9222eee1c3b40158ac8dc75a2dc42ba9a4978`

Pocket4 exported exactly **483 private original files**: the old plan,
worker and sandbox plus 240 full health JSONs and 240 immutable
cycle receipts. The tar archive SHA256 is:
`38c38bc4d9ae24d6db8c887863c7c759965d5904e557a709fab6590ae1f42f5b`.

Portable, Python 3.9+ standard-library verifier:
`research/train_v2/fx_original_health_portable_240_verify.py`
SHA256:
`d5c6871b46ca5ce9ba94b0d23c7c3657b529c1cea74a961532925c7f00dd8973`.

The verifier pins exact original plan/worker/sandbox SHA256, cycle-239
chain head and **this** Pocket4 prefix-report SHA256 in its source. It
parses the untrusted tar **without extracting it**, refuses unknown
members, duplicates, symlink files, world-readable member permissions,
NaN/duplicate JSON keys, missing data and modified source. It recalculates
every original period, original 24-probe count, source-prefix digest,
broker quote age, SHA chain, 15s calendar, publication time, and
30s age-before-next-publication metric. It explicitly refuses
`final_480_accepted=true`.

The **same archive and same hard-pinned verifier** were copied via
strict existing known_hosts SSH from qian-qi to actual MacBook Pro and
ThinkPad X230 and run independently under each host's own Python.
Independent owner-private receipt SHA256:

- Pocket4: `787937575a1b424a21fcb5e851840289af933d63f34bf19e20e1c8a8da530a60`
- qian-qi: `f93e10cea4350e1f8556bd53ff1585c4307854cab709674bd33b7d3c07b8d2ba`
- MacBook Pro: `6da7386719c5680eb41ada0865caea7e4605222ac54ee9865e8b7a0b3a775ec1`
- ThinkPad X230: `da7e493f7e475fb82258a6a8d22d1844a3afd8eea878c482ddee182243277614`

All four agree on original plan/report and chain head, 240 READY and
0 HOLD, worst 12,607ms at publication, 27,607ms before the next
publication, and **HOLD production**. Different receipt hashes are
expected because inspection times differ.

The three other machines checked **one pocket4 original capture**, not
three separately broker-signed market feeds or three production hardware
signing identities. Original source snapshots and their raw hash archive
are retained privately on pocket4, qian-qi, Mac and X230; no broker
credentials, account IDs, OMS data, keys or model artifacts are exported.

## Tests, limits and next release gate

The new synthetic adversarial tests are the prior first160 suite
expanded for all 240 records, plus denial of tampering at the new
cycle-160 and cycle-239 boundaries and refusal of a forged first160
audit rebranded as first240. The prior first160 report/test code remains
unchanged; new tests were executed on Pocket4.

This **does not** authorize a switch to the faster monitor: first finish
the naturally exiting frozen 480-cycle run, then independently reparse
all 480 original status files and chained receipts and require
source-as-of freshness <=30s **between every pair** of publications,
retain true market source HOLD as HOLD, and separately verify cold restart,
CPU contention, broker disconnect/recovery, rollback and original user
service compatibility. No 120-minute native Train V2 spec admission,
positive FX alpha, actual filled net cost proof, IB Flex/Activity/NAV
completeness or Root/Owner/Witness release is established here.

**No change to original production FX/BTC PAPER entry/exit behavior.**
