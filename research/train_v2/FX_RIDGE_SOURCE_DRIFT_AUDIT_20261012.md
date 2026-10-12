# EUR/USD Ridge original-source freshness and revised-OHLC feature drift audit
Date: 2026-10-12. Status: **HOLD / READ-ONLY RESEARCH / NO_TRADE**.

## Frozen evidence and intended question

The fixed four-window PAPER Ridge campaign (18:10, 18:30, 18:50, 19:10
UTC+08:00) is **already over**. Original window 0 was censored. This
work audits **why it was censored** and whether frozen historical features
are comparable to original-quote pseudo-closes. Nothing here restarts or
replaces any old window, rescores prospective outcomes, submits orders,
reconfigures a driver, changes broker quote TTL or promotes a model.

Canonical original campaign seal:
`e5da7854edfe2af4ab00a32ba0b7dc3cf1a41891e1010455de25ddf07c1c28dc`.

Frozen original model and cost/period hashes are verified. Historical
source has 43,538 complete, revised 5-minute paired BID/ASK/MIDPOINT
bars, producing 41,243 causal 8D training examples. Original live source
is locally SHA-256-chained IB PAPER quotes obtained from the existing
native quote gateway, *not an IB-signed market-data feed*.

## 18:10 original quote timeline — measured, not guessed

| Time (+08:00) | Evidence |
| --- | --- |
| 18:09:56.256 | Last distinct original IB quote event before grid |
| 18:09:56.391 | First local collection of that distinct event |
| 18:10:00.000 | Frozen five-minute grid boundary |
| 18:10:01.000 | Earliest preregistered decision time |
| 18:10:01.164 | Immutable `CENSORED` attempt receipt |
| 18:10:01.256 | The preceding quote's IB-reported expiry |
| 18:10:01.335 | Next distinct IB quote event, *after* grid |
| 18:10:01.391 | First collection of next event |

The first possible decision sees a quote **4,609 ms after its first
local availability**, versus frozen maximum **4,500 ms**; age from the
actual quote's *event time* is **4,744 ms**. Event-to-event gap is
**5,079 ms**, though the collector wrote duplicates in between.
At the original censor receipt, the quote age since first availability
was approximately **4,773 ms**, with about **92 ms** before expiry.

The next distinct event cannot substitute for the originally frozen
pre-grid close. Accordingly, `LAST_ORIGINAL_QUOTE_TOO_OLD` is an
appropriate fail-closed verdict, not an excuse to loosen TTL or change
the schedule. We did not observe evidence of a collector restart
causing this particular gap, but these timestamps alone cannot rule out
upstream IB subscription, gateway, network or callback effects.

**Evidence limitation:** the contributing live segment was completed
after 18:10 and was verified *retrospectively* from retained original
bytes. Its hash chain is local and can be modified by a privileged
operator without an independently preserved timestamp. This analysis
does not elevate a replay into an at-decision signed Broker witness.

## Revised-history vs original pre-frozen PAPER source

Source selection is fixed in code, bounded to at most 256 fully
terminated original-source segments with local file mtime preceding
the first frozen four-window study seal. On this host it selected
**242 segments**, 176,653,386 bytes and 105,436 distinct original
quote-event timestamps. Each file was re-parsed with the original
strict local SHA chain, expiry, PAPER period, and terminal END proof.
No synthetic quote, interpolation or future original cohort outcome
is used. The filesystem's historical mtime is **not** a cryptographic
proof the file existed at that past time; interpretation stays research.

The full preseal calendar observed:

| Calendar diagnostic | Original five-minute endpoints | No eligible causal close |
| --- | ---: | ---: |
| UTC Saturday/Sunday | 576 | 544 |
| UTC Monday–Friday | 876 | 168 |
| All | **1,452** | **712** |

UTC calendar days are **not** an IB official market-hours calendar.
Sunday FX activity can occur, while some weekday intervals can be
market closed. To reduce that confounder, on **718 identical UTC
endpoints with actual preexisting revised IB 5-minute bars**, 591 had
a causal original quote and **127 (17.7%) had none**. Historical
bar presence proves a retrospective IB price record, not an
independently attested tradeable original quote on that date.
Missing original inputs were not backfilled from historical OHLC.

Of all 1,452 endpoints, no new market quote in the last five seconds
accounts for 709 missing original closes; 3 had a near-boundary event
but local availability after the grid. Among the 740 available closes,
682 could pass the earliest fixed +1 second decision freshness check,
51 failed on age, and 7 on IB-reported expiry. These counters are
disjoint and together reconcile to 1,452. Importantly, most of the
overall 709 sparse-event count is confounded by market closure; it must
**not** be quoted as a market-hours feed failure rate.

Only **396** contiguous 13-close causal original feature windows
could be constructed from this fixed source population. Those
396 are source/feature comparators, NOT 396 new prospective
predictions, rewards or independent alpha outcomes.

## Exact UTC-time matched representation check

Historical `features()` and original `bar_close_features()` were
paired at **256 identical five-minute UTC anchors** from
2026-10-07 09:40 to 2026-10-09 20:40 UTC. The original cohort itself
and all matured prospective price outcomes remain excluded from this
representation comparison.

| Feature, absolute original vs revised historical difference | p50 | p95 |
| --- | ---: | ---: |
| 1-bar return (bps) | 0.17807755 | 0.44664891 |
| 3-bar return (bps) | 0.17810370 | 0.45841429 |
| 6-bar return (bps) | 0.17847798 | 0.49051619 |
| 12-bar return (bps) | 0.15615549 | 0.49002428 |
| 12-bar return std (bps) | 0.05795544 | 0.17095968 |
| 12-bar mean abs return (bps) | 0.04843732 | 0.15628805 |
| Same fixed Ridge forecast difference (bps) | **0.01204951** | **0.03521756** |

Same-time hour sine/cosine features also matched. The paired sample is
selected by the availability of original quotes, so its relative
agreement **does not certify an unconditional distribution match**.
Unconditional 41,243 historical vs 396 original features also compares
very different dates and regimes.

The original frozen forecast magnitudes for the last cohort were below
0.1 bps, while the previously declared roundtrip cost and margin hurdle
was 10.9133557 bps. The paired representation differences are too
small in these measured windows to provide evidence of an economically
qualified signal. They do not prove that every possible future
representation error is bounded or that a better model cannot exist.

The terminal 4-window sample is 3 valid, 1 censored; Ridge direction
was right on 1/3, and its 2.1167-bps price MAE did not beat a
2.0719-bps zero-change reference. No real trading net return was earned
by this research cohort.

## Deliverables, integrity and no mutation

Retained private write-once **v4** report on pocket4:

`~/.local/state/heptatrader/operator-evidence/fx-ridge-source-drift-20261012/real-preseal-causal-feature-audit-v4.json`

SHA-256:
`be72d5a5e0b176121e47b3db805837ab6bba8a770e6c5dfbb7796df82e150f0a`

Older v1/v2/v3 analyses remain retained as evidence of incremental
diagnosis; they are **not new independent experiment outcomes**.
Only v4 contains weekend/weekday and same-date actual-history
availability partitions needed for responsible interpretation.

The audit imports the frozen model and original-source decoders but
uses no execution client. It strictly refuses conflicting source
timestamps, absent source END, changed original model/period/bar hashes,
historical feature-norm mismatch and invalid matched clock features.
Any output path must be previously nonexistent and under a 0700
research directory. Signed-broker or production ownership is NOT
inferred from local file metadata.

New adversarial tests include the exact 4,609-ms age/5,079-ms event
gap, future quote exclusion, market sparsity vs collector lateness,
UTC weekend classification, same-date quote-coverage accounting,
lossy pseudo-bar windows, duplicate source contradictions,
mismatched model clocks, and a low-paired-overlap HOLD.

## Architectural next action (new prospective study required)

**P0 — Source eligibility and calendar:** Track fresh distinct
original quote events, not repeated polling receipts. Obtain and
bind original IB trading-session evidence for the future experiment,
and report quote gap/availability separately for scheduled open
markets; never relax the old five-second expiry or 4.5-second
predecision age check to rescue historical windows.

**P1 — Train/live representation contract:** Use an identical,
versioned, causally available quote-to-feature transform for any
new research model, with missing-market bucket flags and a
precommitted eligibility rule. Never splice revised historical
BID/ASK OHLC into the live experiment to fill gaps. The current
Ridge weights and all inspected test labels remain frozen.

**P2 — Economic viability:** Select any future cost-calibrated model
only in a new preregistered training/validation design, then score
genuinely unseen forward windows and independently verified
execution-time prices, commissions, slippage, funding and broker
returns. The present historical result selected NO_TRADE and
cannot self-promote.

**P3 — Product trust and account PnL:** Keep native
Owner/CandidateSet/LedgerWriter/Witness admissions, real behavior
propensities and IB Activity/Flex/NAV cash-flow completeness as
separate hard gates. This report authorizes none of them.

No new IB requests, orders, cancellations, fills, automatic training,
model promotions, production service restarts or quote-data edits were
performed. The original four-window `CENSORED` decision remains
unchanged.
