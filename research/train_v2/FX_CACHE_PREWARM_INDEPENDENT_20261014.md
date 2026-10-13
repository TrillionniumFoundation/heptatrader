# Original IB PAPER source cache: independent 12-slot cold-prewarm closure

2026-10-14 | **PRICE/SOURCE RESEARCH PASS for finite pilot only**;
**production monitor replacement, native model training and trading HOLD**.

## Exact comparison (immutable original failure vs new future experiments)

Three DIFFERENT prospective studies, never reinterpreted as one repeated
test or upgraded in place:

1. The frozen **480-cycle, 25% CPU original status trial**: 293 real
   READY, 187 non-READY, only 479/480 full source statuses. Cold first
   assessment cost ~19.15s, its next fixed slot missed; **31,654ms**
   peak as-of age before next actual publication (>30,000ms original
   age cutoff). Original final SHA256
   `8ac37d9c202505e00bcc90daf7ede67dabdc7167eafef526ee17457cc1745353`.
   This is a real failure and its source HOLD episodes persist.

2. **Prewarm v1** (a new 12-slot plan): full original first archive
   assessment really finished before its first fixed slot, but a
   research wrapper wrongly passed plan field `original_period_sha256`
   directly to the existing monitor verifier expecting `period_sha256`.
   Every one of 12 outcomes was correctly recorded
   `HOLD_ORIGINAL_SOURCE_READ_FAILURE / ASSESSMENT_KeyError`.
   Do not mistake this for 12 IB quote failures.
   Preserved final SHA256:
   `3bb2b69a8018fd586ba8422a0d8465561653b4f0466cf6497d37e7c45acb96e1`.
   Actual before-fix regression failed; after-fix regression passed.

3. **Prewarm v2**, a distinct new genuinely future 12-slot frozen
   schedule, with exact original PAPER period scope now explicitly
   converted into the verifier's required field and rejected if absent.
   This was actually executed in pocket4's isolated source-readonly
   sandbox at **CPUQuota=25%, MemoryMax=384MiB, AF_UNIX only,
   Restart=no**, without modifying the live health monitor.
   The worker naturally exited, zero orders, no production side effects.

## Actual v2 result, as independently recomputed

Original v2 plan SHA256:
`91f2daa169e0e001f761e826f80a55db73af3daf5fee3f192a35e8632dd3b096`.

Original final SHA256:
`8cc5b7c5d6f44ff01b28b4abc0042c3a11d8ae440fd7ef4639a3a11224a6a0dd`.

Original prewarm receipt SHA256:
`4791d16deb1cc144fbde7f81f52099355e91c7840cdedd5d1eb22b18b9dbe46c`.

| Field | Original 480-cycle trial | New prewarm v2 pilot |
| --- | ---: | ---: |
| Frozen observations | 480 | 12 |
| Original full source status files | 479 | **12** |
| Original READY | 293 | **11** |
| Genuine original HOLD, incomplete or missed | 187 | **1 real HOLD** |
| Missed fixed source assessment | **1** | **0** |
| Max age at OWN publication | 19.152s | **3.029s** |
| Max PRIOR as-of age before NEXT actual publication | **31.654s** | **18.029s** |
| Original 30s SLA | **FAIL** | **PASS for pilot only** |
| Production monitor cutover / trading qualification | NO | **NO** |

The original v2 prewarm took ~23.6 seconds, before its separately
frozen first fixed source slot. Cached sealed source records were still
fully SHA256-reverified each cycle, unsealed segments strictly reparsed.
All 12 original read-only source status JSONs/receipts are 0600/0700
owner-private and written once; no original source was amended.

Slot 10 genuinely reported `HOLD_NEW_FUSION_WINDOW` despite 24/24
usable five-minute probes, because **the latest original IB quote age
was 5,122ms**, above the unchanged **5,000ms** broker quote TTL.
It remained HOLD. It is not counted as an eligible entry or used to
increase the good-case percentage.

The existing original 480 failure remains a separate retained
negative control; the 12-slot recovery is **not** proof that the source
will remain healthy through another 480 full cycles, failover, cold
start under contention or an IB connection outage.

## Independent, byte-exact physical-host readback

New standalone Python-stdlib verifier:
`research/train_v2/fx_cache_prewarm_independent.py`.

It does not import the generating runner and does not contact IB.
It hard-pins the actual v2 precommitted plan and generator code
SHA256, independently verifies private JSON file identities/contents,
12 source-status hashes, original 24-probe counts, original quote age,
source-prefix metadata checksum, strict original Rule, original PAPER
period, continuous 12-receipt hash chain, fixed event timestamps,
READY/HOLD accuracy, prewarm-finished-before-first-slot proof, and
the exact terminal 11/1/0 counts and 18,029ms interpublication bound.
Changed original status bytes, duplicate keys, false READY,
rewritten HOLD, late as-of quote, wrong period and partial final all
fail closed. This is not independent broker-signed source custody.

The verified source-status and receipt bytes were exported as a
bounded **read-only custody archive**, SHA256:
`bde247e7655ce44eae37c55aba70b9d4d8db2b55ed21392d82acc17d062b83a2`.

The independent verifier was executed on BOTH actual hosts:

- Pocket4 independent recheck v2 SHA256:
  `bf757c83833624f8e3a52893ee2ae46610230623ee061b7ef3a27a9dd402653b`.
- qian-qi physical-host copy recheck v2 SHA256:
  `47073fb8ed20e9d4d8d251d5ba997630f08c104ae9968d9b299fe49d786c1fc1`.

The two JSONs include their own host identity/timestamp and are
therefore not byte-identical; the original pinned raw input receipt
chain and all computed outcomes agree. The qian-qi report was copied
back to a new pocket4 private evidence file. This is cross-host
**evidence reproducibility**, not independent live IB prices,
production time attestation, a signed owner witness, or original
trader behavior-propensity attestation.

## Release decision and next hard gates

The new candidate may proceed to **a further separately frozen
480-cycle, post-reconnect source-health stress and recovery test**.
The 12 pilot cannot substitute for that or the original source
transport fault, strict expiry, IB PAPER invocation identity and
account NAV/Flex closure.

The monitoring algorithm, original safety policy, quote TTL,
20/24 minimum readiness, native original Train V2 FeatureSpec horizon
cap, original 4-window CENSORED cohort and PAPER trade/driver services
are unchanged. There are zero new orders and zero model updates.

Formal product, long-horizon strategy and monitor cutover:
**HOLD until all source continuity, rollback, independent hardware
witness, cost/Flex/NAV and profitable OOS product gates pass.**
