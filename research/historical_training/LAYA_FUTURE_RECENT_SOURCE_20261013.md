# Laya prospective fast original archive source — 2026-10-13

**Isolation:** this change is an opt-in source-profile candidate for a NEW, separately registered Laya shadow trial. It does not change the currently frozen 89-slot trial, the running Trainer/Evaluator, live quote writer, PAPER/OMS, canonical Owner or any trade permissions. Never retry old slots.

## Actual failure and qualification

The live trial-31 candidate was frozen before the original fixed 89 input slots. The first THREE immutable outcomes are CENSORED due to PREDICTION_PROCESS_FAILED with original exception INPUT_SLOT_EXPIRED:DURING_QUOTE_READ. The predeclared rule requires >=87 observed/non-ties out of 89 and allows at most TWO censored. Therefore this trial is already mathematically incapable of satisfying its ORIGINAL acceptance conditions; the evaluator may still report PENDING until its original outcome_end_ms. Its count and source failures must not be modified or reported as successful model forecasts.

The existing read_quotes verified all archive originals and parsed all source segments again on every poll. In live pocket4 input, this encompassed ~278 segments and >131k original quotes, even though the newest 6 minutes drive a 300-second prediction. The frozen worker timestamps quote availability using the BEFORE-read clock and checks freshness using the AFTER-read clock; an additional several seconds can turn an otherwise valid 2–3s old source into stale input. The observed error alone does not prove every failed iteration's time breakdown, because the frozen phase receipt did not record that internal latency.

## New future-only reader

The new opt-in mode is ARCHIVE_VERIFIED_RECENT_ORIGINAL_V2. Existing plan profiles with no new field call the ORIGINAL full reader without changing their output keys.

The original source archival inventory is still loaded and EVERY sealed segment prefix is SHA256-verified against its original immutable page/checkpoint chain. Before omitting archived QUOTE parsing, the reader verifies each original END footer SHA against its hash-chained source receipt and rejects any archived source whose upper timestamp bound overlaps the newest required as-of window. This is important because the recovery-live unsealed segment can carry older source timestamps; comparing only to the earliest unsealed quote would falsely reject valid archives or skip relevant events.

The unsealed tail is still fully parsed by the unmodified original hash-chain reader, with original period identity, as-of known_at, lexically sorted source paths and cross-source price-conflict detection. It checks a complete 360s recent input window (420s when requested for price outcome), then revalidates the archive inventory and **rehashes all sealed sources again** after tail collection. Concurrent page rotations, deleted/mutated archived bytes, symlinks, changed period, duplicate price conflicts, missing recent coverage and unregistered shorter-window requests all fail closed. A current quote is not fabricated when absent.

Because only archives strictly older than the selected window are skipped, original recent QUOTEs and exact Numeric/Laya feature inputs should be identical to the full reader on the SAME as-of time. The independent strict evaluator can still consume full original paths after maturity; this fast result alone is not product-signed data or a financial return.

The new profile is registered only if a future config explicitly enables it and pins the SHA256 of both new source-reader and selection modules in runtime_sha256. A newly registered plan also carries the explicit policy and those hashes in evaluation_runtime_sha256; the plan ID hashes all fields. Any tampered, missing, unregistered or non-matching code pin is refused. Old source policies are unchanged.

## Real Pocket4 read-only strict comparison

Exact immutable receipt:
~/.local/state/heptatrader/operator-evidence/train-v2-laya-recent-source-20261013/read-only-source-parity-v1.json

SHA256:
4ca3cb1b0592ea4b00091964f0d9b21929025f758bbf0925797a7466f2243434

During this ONE real run, full original reader read **131,695** quote records in **4.6552s**, while the new opt-in reader read **15,726** recent/unsealed records in **0.6998s**. Both produced the same 170 quote records within the declared 360s as-of window, the exact same native Numeric/Laya sample and state, and the same sample identity. These are measured single-run timings, NOT a guaranteed p99 or proof that the frozen 89-slot trial would pass.

The original append-only active source may grow while each reader runs, so their mutable tail prefix inventories need not be byte-identical at two different observation instants; exact quote and feature equality are checked against the fixed original decision time. Earlier baseline also reproduced 147 identical rows and exactly identical feature input.

No new broker request, model load/inference, GPU training, wallet, order, Laya promotion, or systemd deployment was performed by the smoke.

## Acceptance and next production gate

The next future plan must be registered before its NEW first slot with this explicit V2 profile and frozen code hashes. The model and independent scorer must seal original decisions/results without moving deadlines, and the independent strict full-data reader must validate the exact source and outcomes before admitting any statistical claim. Preserve ALL failed windows. Even a successful 89-slot sign-screen is conditional PRICE prediction qualification, not economic alpha, BUY/SELL propensity or Canonical Agentd Owner release.

Do not install this code into the active pinned runtime for trial 31; changing its frozen evaluator/source bytes would invalidate the original plan. Once reviewed/CI-passed, a separate new candidate and explicit user-allowed research release can adopt this opt-in for *future* windows only.


## Independent review continuation — 2026-10-13

This candidate was copied to a **new separate review worktree** based on
the unmodified archive-Guard activation commit 15853b2. The in-progress
original source worktree, installed pinned Laya release, existing 89-slot
frozen trial, PAPER drivers, source-health monitor and Canonical Owner were
not changed.

The future-only source policy now verifies the *actual release bytes* at
the exact pinned runtime paths on every V2 policy admission, not merely
equality between caller-provided config and plan hashes. A symlinked or
different release directory, tampered source-policy module, unpinned future
plan and mutated actual reader bytes all fail closed. The predictor checks
that enrollment **before importing GPU model weights**; an invalid new
profile must not start a heavyweight predictor.

The standalone read-only comparison no longer imports the GPU/torch
predictor just to SHA256 a source Python file. It reads source code bytes
directly and remains runnable with base Python and no GPU visibility.
The original SOURCE reader, on the same live mutable feed at distinct
observation instants, may legitimately return a different growing tail
prefix than the candidate. The actual comparison checks the exact
predeclared as-of recent quotes, Numeric16/Laya sample and state. Such a
comparison is not a controlled statistical speed benchmark or a new
prospective model prediction.

Independent real follow-through smoke, after the actual code-byte pin:
~/.local/state/heptatrader/operator-evidence/train-v2-laya-fast-review-20261013/original-source-parity-final-bytepin.json

Immutable SHA256:
5240a41b0666b770afaab43d81a29a9517a9722d4d80e5ada85d84c1ea69e812

At this run the fast reader took 0.7687s, original full 4.3194s,
and the same recent 134 original quotes and model input were reproduced.
One measurement does NOT prove guaranteed p99 or exact 89-slot eligibility.

A separate immutable **mathematical futility** audit of existing Trial 31
confirmed eight already-censored original source-decision slots against
its frozen maximum two. Even if every later slot were observed, at most
81/89 could count, below 87 required. The frozen trial is therefore
ineligible independent of later market direction or new faster reader.
This finding does NOT rewrite its official PENDING state, alpha budget,
remaining slots, source rows or old observed outcomes.

The original trial's independent evaluator may finish its already frozen
calendar, but no old censored event may be retried, and no future source
profile may be applied to old trial bytes.


## Exact independent review closeout

At the final Pocket4 2026-10-13 original-source check, current full reader
4.3194 seconds versus the opt-in recent reader 0.7687 seconds. Both
generated the same 134 eligible original as-of quotes and identical
Numeric/Laya input. They were executed sequentially on a **growing**
original feed, and their complete prefixes were not byte-identical at
those different reads. Timing is diagnostic, not a p99 guarantee.

The new source/profile verifier now checks the actual runtime source bytes
at two reviewed paths even for a standalone model predictor, rejects
config/plan code-hash equality when the files differ, and preflights
before heavyweight model import. The research-only parity smoke does not
import torch just to compute SHA256, permitting GPU-free source audit.

Proof SHA256:
5240a41b0666b770afaab43d81a29a9517a9722d4d80e5ada85d84c1ea69e812

Private file:
~/.local/state/heptatrader/operator-evidence/train-v2-laya-fast-review-20261013/original-source-parity-final-bytepin.json

After re-reading the ORIGINAL sealed Trial31 outcomes at final audit,
**11** events were irreversibly CENSORED, with frozen maximum **2**.
The best attainable success count is at most **78/89**, below the
preregistered **87** required. The *separate read-only* mathematical
futility receipt, with all existing outcome file hashes, is:
~/.local/state/heptatrader/operator-evidence/train-v2-laya-fast-review-20261013/frozen-trial31-math-futility-final.json

SHA256: c84c7dcb4c5b3d6c3a92e6b53872c2db3b42990b76f988d30b013bd6b8655672

No GPU production model was loaded, no future trial started, no
PAPER order was sent, no current Laya release/systemd unit or old slot
was changed. Product Owner and automatic selection stay closed.

### Exact regression on this source worktree

- Historical training suite: 437 tests passed, 7 pre-existing skips.
- Full Laya-native suite: 327 tests passed, 1 pre-existing skip,
  using the installed Laya fixed virtualenv and disabling GPU visibility.
  The system Python alone lacks torch and cannot run this GPU-optional
  suite: do not count its initial missing-dependency errors as a pass.
- Core Python suite: 2,495 tests passed, 23 pre-existing skips.
- Source/build/documentation tests: 106 tests passed.
- Frozen futility negative tests 8 pass; new recent source reader and
  opt-in policy focused tests are included within the Laya-native suite.

The new candidate release remains **future-only and not deployed**.
The old 89-slot trial cannot be made statistically eligible through an
after-the-fact source-speed or evidence revision.
