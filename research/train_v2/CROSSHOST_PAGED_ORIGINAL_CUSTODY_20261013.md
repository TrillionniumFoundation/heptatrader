# Train V2: independent cross-host ORIGINAL paged-price custody (2026-10-13)

Status: isolated research audit based on **1e90e40** (paged original source,
PR #272). It does not modify the unfinished paged-future Native observer,
any old result, the live PAPER source writer, Execution, Laya training,
Canonical Agentd, model, service or trade authority.

## Why this acceptance matters

The original Pocket4 PAPER quote feed had grown beyond 200 MiB and
contained 285 naturally ended numeric source segments at the snapshot time.
The old exact-price source reader intentionally rejects >320 segments /
>256 MiB. The newer paged reader handles this growth, but had previously
been checked on the same source host.

An independent qian-qi computer now received a sequential, **read-only**
copy of the ORIGINAL naturally ended source files and its eight archive
pages/ledger. The active, unsealed last Pocket4 numeric segment and the
recovery-live alias were explicitly EXCLUDED. Source transport used one
rsync stream, not concurrent mechanical disk writes. No original source
files or checkpoints were modified.

The original archive pages contain absolute Pocket4 source file paths.
The standalone, Python-standard-library checker
paged_crosshost_custody.py maps those *exact unchanged source identities*
to the byte-for-byte copies in an explicitly named qian-qi root. It does
not edit a single stored page, prefix record, ledger line or original
receipt to make their paths match. It independently verifies each
complete original hash chain, quote authority/known-at time/TTL, real
zero-order PAPER header, all archived prefix SHA256/length/chain_head,
all page and independent ledger checkpoint SHA256 links, every original
quote receipt identity, and the original legacy segment fingerprint
formula. A missing early segment, old tamper, altered page or ledger,
duplicate source receipt, wrong root or forged economic-fill claim fails.

## Actual independent host proof

Snapshot owner host: qian-qi; original source producer: Pocket4
alex-G1628-04; source original period hash:
61995fcedc2993d7ac33d2ed86c9613068c5a3ecd0d1c377e612639cd5a0958f

- 285 naturally ended original numeric segments, 8 immutable archive pages,
  256 checkpoint-pinned segments and 29 hash-verified unpaged suffixes.
- 214,194,780 original numeric file bytes, 137,642 unique original quote
  receipts, full-snapshot fingerprint:
  bb71c016977df229d42fd274b577006e86c6ec9b07170c93615711932db2e81d
- Original **first 282** segment frozen fingerprint:
  bf5847eddedf53895f3d3a6b8701f1df020fa452a96855404591110ce034803e
- Original **first 283** segment frozen fingerprint:
  d9b8db2128a5b964b126d6dae3161156fdf1143cd65bd0b2030bc5e83ce8c570
- Page ledger independent chain head:
  ac420f74a84b7a56cec024ed636d1c0886597948bace29f1af262e073caa98a5
- Exact already frozen Pocket4 pair receipt SHA256:
  d850fa69de37834a408347f44524984274909ce340067631cb7a889be441deb5
- Independently verified original price outcomes SHA256:
  4e4fad4ab4daf21565f283d7e2b1a99acc1e7667255ec8ce1d18d6ddf427e53c
  and d8bfb687cd1b71398312a63919c50a10f81fa35cce48f0f1bc29afca11e1a287.
- Native 01: true original +0.626541179419017314913020000 bps,
  direction correct. Native 02: -0.1789957443761774563809750000 bps,
  direction wrong. Both ORIGINAL independent calculations exactly
  matched the already recorded immutable prices.
- Frozen model scores were +0.73047167 and +0.73336015 bps. BOTH price
  movements remain below the old 10.9133557 bps HYPOTHETICAL cost-plus-
  margin hurdle. Actual attributed strategy fills/net USD returns: ZERO.

The qian-qi private receipt is:
  /home/qian-qi/work/train-v2-independent-source-20261013/evidence/
  independent-two-native-price-labels-final.json
  Final qian-qi receipt SHA256: f3751b8037a3bbf107b36af997a6c1497bf45668820f2bd946a2ae95e8ba85b8

The full raw original 208 MiB snapshot is retained on qian-qi only;
it is not added to Git or uploaded to GitHub. The receipt contains the
exact standalone auditor source SHA256. All first-pass evidence remains
intact, including earlier WAIT/strict-f64-failure cases.

## Scope and negative qualifications

This is a **cross-machine byte-integrity and original-price calculation**
qualification. Both hosts, SSH transport and filesystem permissions are
operator-controlled; it is NOT a broker signature, independent legal
entity, canonical product-host trust distribution, native LedgerWriter
ACK, new source/REQUEST/PREPARED prediction, inference benchmark,
actual executable quote/slippage or profit. Independent result verification
is about price correctness, not positive alpha. The original frozen Native
f64 exact replay failures remain FAILED and are not retroactively healed.

A source excerpt containing a prospective unsealed segment is excluded,
never silently treated as an observed original outcome. A later new
Natural END creates new source evidence; no copied file is rewritten.

## Regression

The new standalone adversarial suite verifies 32 fully checkpointed
sources, a true 321-segment source exceeding the legacy cap, correct
two frozen synthetic 300-second outcomes, original quote tamper,
archived page/ledger corruption, missing early source, repeated receipt,
wrong original path identity and rehashed fake actual brokerage fills.

Existing Native/PAPER source and model code are not changed. All work
remains research-only. The unfinished future paged Native observer in
its separate branch is untouched; truly new signed prospective model
cohorts and actual net economics remain future, predeclared gates.


## Exact final two-host qualification and HOLD

- qian-qi standalone standard-library adversarial suite: 10 passing.
- Same new 10 tests on Pocket4 on the isolated exact source tree: PASS.
- Historical research tests: 539 passing, 7 pre-existing skips.
- Full Core Python discovery: 2,637 passing, 23 pre-existing skips.
- Source/build/ownership partition: 106 passing with no skips.
- Final private qian-qi receipt SHA256:
  f3751b8037a3bbf107b36af997a6c1497bf45668820f2bd946a2ae95e8ba85b8.
- Final portable auditor file SHA256 on qian-qi:
  b220ceaa873abbbd02cb77e0b45256c3e6407b92ff7966e8f896da8d85913f93.
- Exact post-copy source checkpoint chain head:
  ac420f74a84b7a56cec024ed636d1c0886597948bace29f1af262e073caa98a5.

No retrospective price outcome has been turned into a new signed
prospective model/economic experiment. The unfinished future paged observer
candidate and any active PAPER broker service remain unchanged. Canonical
root-trust, independent product Observer, behavior propensity, actual
execution/slippage and profitable after-cost evidence remain HOLD.
