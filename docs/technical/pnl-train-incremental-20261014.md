# Completed-day incremental IB history, 2026-10-14

Research-only, no order capability or strategy cutover. Legacy 24-hour cutoff
and 30-day requests remain default. The explicit COMPLETED_UTC_DAY policy only
permits UTC 23:59:59 strictly before today's UTC midnight and at least five
minutes in the past. window_days is an integer 1..30; non-30-day windows require
exactly one chunk. The wire guard binds the bounded duration to the frozen plan.
Client 8984, process-global lock, original 15-second pacing, immutable requests
and receipts, no retry of UNKNOWN, request errors or unfinished chunks retained.
Head timestamp requests are cancelled using only their own read request IDs.

Target for this execution: end 2026-10-13T23:59:59Z, seven-day window, one chunk,
MIDPOINT/BID/ASK plus IB schedule and heads; overlap with the frozen prior dataset
is compared rather than overwritten. Revised historical bars cannot backfill any
old live missed/censored prediction, nor serve as contemporaneous reward.

## Actual Pocket4 execution and limits

Private evidence is under `~/.local/state/heptatrader/operator-evidence/pnl-train-next-20261014T0615Z/`.
No raw account data, order IDs or credentials are published in this source change.

### P0: applied research scheduling fix, exact bounded arithmetic

The original one-second finite Value timer repeatedly hit systemd's start limit.
A hash-checked drop-in sets a finite 10-second/12-start budget. The signed worker,
original calendar, 60-second request expiration, resource gates, old failures and
model remain unchanged. Only daemon-reload and reset-failed of the research unit
were used; the existing timer then ran naturally. Rollback deletes only this
matching-hash drop-in and reloads units, never replays missed slots.

The old FX driver-minus-ledger difference is reproduced by two fully visible
split-fill command aggregates: six-decimal VWAP rounding contributes -0.021126
and -0.011031 USD, summing to -0.032157 USD, residual 1.855e-10 USD. This is a
bounded 128-of-443-execution snapshot arithmetic explanation, NOT a full OMS
replay or a proven localization to one deployed callback serialization function.
No native driver, trading binary, financial state or source reward is changed.
The canonical current100k exporter already recomputes exact per-execution Decimal
cash/commissions and validates settled behavior-bound identities; it must not use
the rounded driver's display total as a monetary reward. Full live reward/ledger
replay was not performed after the platform blocked a bulk raw-journal audit.
All-in NAV, period opening NAV/flows/other charges remain unqualified.

The read-only Value projection separates 349 sealed predictions from 55 observed
monetary outcomes and 111 censored commands at the captured as-of. Censorship:
51 NOT_COMMITTED_BEFORE_ORIGINAL_OPEN; 60 NO_PREDICTION_FOR_ORIGINAL_REQUEST.
No currently pending outcome among these 166 commands; the overall future study
is still PENDING, with time-half counts [55,0]. It rechecks record hashes but does
not claim independent re-verification of role signatures or an atomic broker read.
Neuron's last evaluation at Oct14 01:39 +08 and next at Oct14 19:39 +08 match its
unchanged finite calendar; no forced rerun was justified.

### P1: implemented and tested, actual data collection blocked

The one actual independent-client invocation verified PAPER/server187, then
observed CASH_HMDS_DISCONNECTED_NO_HISTORY_DISPATCH. It sent only START_API once,
zero head/bar/schedule requests and zero orders, and terminated without retry or
Gateway restart. Zero new historical rows and no earliest-date query this turn.
The original failed session remains immutable. This is not a completed increment.

### P2: real CPU readout fitting, explicitly already-seen development data

Four 120D/30D/15D rolling numeric folds consumed the existing 43,538-bar joined
history, with 41,243 causally eligible rows and 11,357 distinct fold test rows.
Three separate time-ordered paired folds re-fit numeric/English/mmBERT readouts
using 512 previously frozen and exact-input-hash-matched cached feature rows.
13 persisted CPU ridge readouts bind training/validation/test times, source hashes,
normalization, weights and predictions. There was no new encoder training or GPU
use by this job. Every chosen policy abstained under the predeclared positive-net,
minimum-30-validation-trades criterion. Already exposed development data is NOT
fresh OOS and cached text rows are NOT the full 43,538-bar numerical experiment.

Stress keeps each policy's trade set fixed. A separate unfiltered-sign diagnostic
is not the selected policy and its hypothetical loss is not actual PAPER PnL.
Existing mixed-size fee p90=0.7147899bps is conservative for the current $100k lane;
126 complete $95k-$105k commands in the bounded captured snapshot give one-way
p90=0.2000234841bps. Repricing the same fixed diagnostic trades with the matched
fee reduces estimated costs but every dense/paired arm remains net negative.
This is a post-review sensitivity, no threshold retuning and not full-period cost
calibration. True execution slippage and guaranteed historical fills remain unproven.

Actual CPU fit ran in a 75%-CPU, 2GiB MemoryMax, zero-swap, AF_UNIX-only cgroup:
2.427s wall / 1.824s CPU / 225.2MiB reported memory peak / no swap.
The pytest launcher failed before collection because pytest is absent; no package
was installed. Existing unittest discovery then ran 570/570 successfully, no skips,
9.553s wall / 7.047s CPU / 352.9MiB reported memory peak / no swap.

### P3: no candidate or new future registration

Retain HOLD_NO_COST_POSITIVE_VALIDATED_CANDIDATE. Do not create another future
study just to replace a failed/insufficient one. Existing Value/Neuron schedules
remain intact. Native trading owner/model admission and independently observed
after-cost efficacy remain separate gates. No automatic promotion or trade authority.
