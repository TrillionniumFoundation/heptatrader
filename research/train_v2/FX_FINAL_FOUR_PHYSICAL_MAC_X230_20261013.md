# Frozen FX 4×4 completion — independent Mac and X230 byte-level verification

Date: **2026-10-13** (pocket4 timezone UTC+08:00)
Status: **RESEARCH-INTEGRITY PASS, MODEL/PRODUCTION/ECONOMIC RELEASE HOLD**.

## Scope and immutable source

The old original IB PAPER EUR/USD study was precommitted before the
**00:20, 03:35, 06:50, 10:05** entry grids. Each grid fixed four
15/30/60/120-minute future price maturity attempts. The last source
attempt matured at **12:05:06.5**, after which the one-shot original
worker naturally exited. All four entries were legitimately rejected
by `ORIGINAL_READINESS_HOLD_OR_STALE`; all sixteen horizon attempts
subsequently retained `NO_ELIGIBLE_ORIGINAL_PREACTION`.

This audit **does not** run a price predictor, sample historical
revisions as live quotes, enable trading, create a model, lower a
source-freshness threshold, or create retrospective reward labels.

The original and cross-host-referenced hard pins are:

| Original private artifact | SHA-256 |
|---|---|
| Pretrial plan | `02aa4a2662b743203cc925d09d3250bcc0e02155cf5d00bb319b0526448aaa04` |
| Pretrial seal | `1f13ed5c7a8fcdc29cc0881bf5c7854c263876f5f89bb6bc87633d5b2b33860b` |
| Original one-shot worker `final.json` | `857914d7222cc6e85058d9a19c3bf8765690e172a91a4de9cd2d22ebecc3890a` |
| Separately preregistered future health custody plan | `9bdb268359c2befeecb66ff11cddf45161f72543678875ed86428cb3a6420737` |
| Actual completed health custody `witness-final.json` | `6b2c9b6a2c1931a26c415801495329591db99f56a1b19560191f3591470fb184` |

A physical qian-qi pretrial copy was created before the first
observation. The original pocket4 run and qian-qi
`fx_all_four_final_crosshost` and primary-final reconciliation had
already completed successfully. Their successful accounting is
explicitly **NO_TRADE/CENSORED**, not profitable trading evidence.

## New independent and bounded verifier

`research/train_v2/fx_final_physical_stdlib_verify.py` is a fresh
**Python 3.9-compatible, standard-library-only** implementation. It
does **not import the original Train V2 decoder, source observer, or
prior final qian-qi verifier**.

The bounded source ZIP has exactly **36** known private file paths,
with strict ZIP path/member permissions, size and duplicate guards.
It contains original precommit readback, plan, seal, worker start,
all four original decisions, 16 fixed maturity receipts, original
final summary, future-health custody plan/final and three exact
future health raw JSONs and corresponding custody receipts.
It deliberately **excludes broker credentials, original OMS orders,
private keys and original quote streams**.

ZIP SHA-256:
`022807b463991516b02c5989f89ab8b04e63ecff8eaf5c2d4a4ae52beecf03a2`.

The verifier independently checks:

1. Five independent pinned source root hashes, original pretrial
   physical copy and unmodified fixed four-grid calendar.
2. The original single-use worker start and final summaries.
3. **4/4** original decision receipt SHA-256 and exact
   `CENSORED / ORIGINAL_READINESS_HOLD_OR_STALE` reason within their
   predeclared decision intervals.
4. **16/16** original maturity receipt SHA-256, exact
   `CENSORED / NO_ELIGIBLE_ORIGINAL_PREACTION`, no late or premature
   timestamps, no synthetic price labels and zero order or reward.
5. For 03:35, 06:50 and 10:05, the exact unmodified monitor JSON
   from the old custody: all 24 actual probe records, disjoint
   counters, same PAPER period, locally rehashed source prefix
   metadata, original quote timestamp/age, original **30-second**
   monitor age boundary, and exact failed witness receipts.
6. A future monitor becoming READY later never reclassifies old
   attempts. The original 00:20 full monitor bytes **were not saved**
   and cannot be fabricated.
7. A `PASS` requires all exact source and health receipts; missing,
   duplicate, public, altered, re-ordered or retimed data fails closed.

## Actual independent host tests

Three physical hosts separately recomputed the **same** frozen packet.
The trusted routes from qian-qi were existing `ssh -o
StrictHostKeyChecking=yes mac-ts` and `x230-ts`. Neither system's
SSH identity was accepted opportunistically.

| Host | Python | Original decisions | Mature receipts | Verdict |
|---|---:|---:|---:|---|
| qian-qi | stdlib | 4/4 CENSORED | 16/16 CENSORED | PASS research |
| MacBook-Pro.local | 3.9.6 | 4/4 CENSORED | 16/16 CENSORED | PASS research |
| qian-ThinkPad-X230 | 3.12.3 | 4/4 CENSORED | 16/16 CENSORED | PASS research |

Both remote machines used the exact same final verifier source with
SHA-256:
`8da5e10994aeaef8e07577bce6d851775c61e1c15837920f7b1719feeb813679`.

Their actual, local `independent-v2.json` SHA-256s were:
- **Mac:** `11e30b5e62c39205fe9f33c125108787057f4728d9328e5433764e03a792b967`
- **X230:** `0e485dce3f0c2b83deff38ffab46e81ea23cbca5e631474c9452618ee411a8ca`

The qian-qi three-host field-by-field common-result receipt SHA-256:
`e0e1b3039a314bca91c3d8c038b0fded9d440012c692866a9adf7cda2d53a062`.

Independent state-aligned source monitor ages:
**37,938ms / 86,233ms / 85,031ms** at the remaining three
original decision anchors, all exceeding the unchanged 30,000ms
freshness requirement. Each archived monitor reported 24/24
operational probes usable, but only at its **older** evaluation time,
not at the original trade-decision time.

These are three independently operated physical validators on
**the same pocket4-produced source bytes**, not three separately
broker-signed quotation feeds, hardware-signed producer roots, or
an independently attested exchange timestamp.

## Negative evidence and regressions

Actual offline fixed-archive mutation challenges on qian-qi:
**9/9 REJECTED**. Cases include forged successful future label,
spurious broker order, late maturity, falsified primary-final reward,
witness falsely admitting old entry, health timestamp retcon,
modified pretrial plan, revived original CENSORED entry and missing
maturity receipt. These attacks only mutate memory copies; all
real original pocket4 and physical-host bytes remain untouched.

Negative challenge receipt SHA-256:
`c68cd5796649b41e8c3b8735701e078a3c989475b7554447677f2ea8f88120b9`.

**128/128** targeted Python tests passed: 23 new portable verifier
tests, and 105 existing original final cross-host, source-health
custody and fixed future protocol tests. Early synthetic test-fixture
errors were corrected before this final regression. No production
driver, execution service, active model, cost gate or quote TTL
was changed.

The separate pocket4 one-shot `systemd` journal confirmed actual
exit `Result=success / ExecMainStatus=0`. The new ZIP verifier
intentionally **does not claim to prove an OS process exit from
`final.json` alone**.

## Reproduction without broker access

The retained source and evidence roots on qian-qi are under:

`~/.local/state/heptatrader-fx-final-physical-mac-x230-20261013/`

Archive file:
`frozen-4x4-papercustody-v1.zip`; its original copy is not
overwritten or amended.

The portable verifier accepts:

```sh
python3 -B verify-v2.py --mode verify \
  --bundle frozen-4x4-papercustody-v1.zip \
  --output independent-NEW-UNUSED-NAME.json
```

Use a fresh owner-private `0700` output directory and distinct
`0600` receipt filename. The program refuses overwriting any
previous evidence. It runs with no local HeptaTrader installation,
IB API, trading account, credential, or NumPy dependency.

Source-code and tests are commit-safe, but original private ZIP,
health snapshots and local source files must **never be committed
or copied to public GitHub**.

## Next actual admission gate: source availability, not new Ridge fit

The installed legacy health monitor runs its roughly 7–20-second
assessment, **then sleeps another 60 seconds**, so source-health
snapshots easily exceed a hard 30-second decision age even when all
24 probes are nominally usable. The existing separately tested
15-second sandbox-cache alternative reduces the *latency* but
a real 24-cycle experiment still had **three genuine
`LATEST_AUTHORITATIVE_QUOTE_NOT_FRESH` failures** when the original
IB PAPER quote was older than 5 seconds.

Therefore **neither a faster monitor alone nor a later READY status
makes this old experiment valid**. Before a controlled read-only
production monitor cutover, require independently frozen multi-hour
source continuity, producer/callback freshness, source-clock
causality and rollback evidence. Keep all historical failures.

Native Train V2 >60-minute FeatureSpec, canonical Owner/Witness,
LedgerWriter, signed producer, IB Flex/Activity + account NAV, true
execution/slippage/financing costs and adequate prospectively
independent model economic efficacy all remain **HOLD**.

**Verdict: source custody and physical-host final evidence PASS;
model effectiveness, trade release, net account profit and
production source-monitor rollout HOLD.**
