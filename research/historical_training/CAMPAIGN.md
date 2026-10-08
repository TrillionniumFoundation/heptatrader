# Fixed-candidate multiwindow campaign and matched semantic development

## Boundaries

The candidate remains `f7f18c2476da44496fe9d5a8e987cbab5e3459701ed7ef9ff421c61290245c1e`, file SHA256
`538dd502589cf4ddad56c0dc2c13b7f75f5da402f2d53f08983b09fc6a377466`.
Its probability parameters, input profile, modeled cost head, ABSTAIN policy and
zero trading authority are unchanged. Earlier one-window smoke evidence is not
counted as a slot in the new campaign. No model can self-promote from this lane.

## Finite future campaign

`multiwindow` declares exactly 24 slots across six weekdays, four slots per day
at two-hour spacing. On the installed 2026-10-08 campaign, anchors are 11:10,
13:10, 15:10 and 17:10 UTC on October 8, 9, 12, 13, 14 and 15. Final bar maturity
is October 15 17:35 UTC. These are scheduled future events, not completed data.
Full spans [anchor-3600, anchor+1500] do not overlap; this reduces input/label
leakage but does not prove stochastic independence across market observations.

The existing one-bar-lead collector accepts an explicit predeclared anchor only
within its startup deadline. It writes original raw input and the frozen
prediction before target entry, then reads the outcome only after maturity.
No historical backfill replaces a forecast, no missed slot moves to a better
market time, and a previous claimed/interrupted slot cannot be replayed.
A fresh exact PAPER account handshake, original period hash, SDK inventory,
model hash and installed source hashes are checked. Actual egress is limited to
four fixed historical requests per slot and own issued read cancellation. There
are no financial order, order-cancel, account-query, or quote-subscription methods.
Maximum campaign budget: 96 historical requests plus bounded handshakes.

`install_multiwindow` creates only a new read-only research oneshot/timer. The
calendar lists every fixed UTC activation explicitly plus one final sweep.
Persistent activation and OnBootSec allow a restart to classify missed windows,
not to redo them. A nonblocking campaign lock prevents simultaneous invocation.
The installed job is limited to 1 GiB, 50% CPU and 2100 seconds, with Restart=no.
A final complete marker prevents further service work. The timer may remain
installed/enabled after its last event; no further calendar event is declared.

Every slot becomes OBSERVED or CENSORED. The final assessment runs only once all
24 are final. It requires at least 20 observed, at most 4 censored, at least five
UTC days and at least two classes, and compares the candidate with both frozen
training prior and uniform. Fixed mean Brier gain threshold is 0.001. Two
one-sided day-block bootstrap lower bounds use 0.025 per comparison. With only
six planned day blocks, inference remains limited; this is price-review
eligibility, not formal IID proof, economic approval or trading permission.
There is no interim efficacy verdict or automatic parameter adjustment.

Child-plan anchor, target interval, candidate ID/hash and registration time are
validated against the original parent campaign. Merely relabeling another
positive slot receipt is not sufficient. An inactive-only validator package
upgrade may add that check while retaining the exact parent plan, timer,
candidate, already recorded evidence and collection/prediction implementation.
Do not hot-replace a running collector or mutate installed source pins.

## Matched semantic comparison

`matched_data` uses only the already stripped train/validation data for profile
`EURUSD_5MIN_LOOKBACK12_LEAD1_HOLD3_V1`. Fixed time-stratified roles are 512 fit,
128 tune, 128 calibration and 256 check. All arms see the same rows, target
horizon, five-minute decision buffer and identical serialized input text. The
old test data is not exported or read by the model worker. Backbones remain
frozen complete checkpoints; silent token truncation is rejected.

Both multilingual and English encoders use the same CPU environment, float32,
masked mean pooling, normalization and fixed 64-coordinate projections. The
numeric arm uses the same serialized 25 causal inputs. All three fit the same
regularized softmax family, tune the same penalty grid on the tune segment,
refit on 640 fit+tune rows, and calibrate only on the separate calibration rows.
Semantic-only, raw and calibrated probabilities, fixed 50/50 mixtures, uniform
and training priors are retained. The original full-data frozen model is also
reported as an explicitly UNEQUAL-training-budget reference, not a matched arm.

A distinct executable-side modeled net head is fit for each arm with a fixed
penalty grid; modeled costs are 0.4 bp roundtrip commission and 0.5 bp slippage,
with bid/ask spread already present. A buffer is admitted using only calibration
with at least 30 trades and positive modeled net. A zero-buffer check when no
policy passes is diagnostic, not an admitted trading strategy. Fixed-time/side
cost stress does not reselect trades. Model selection never changes the frozen
future campaign. A matched development result is not new future efficacy.

`matched_audit` independently checks training-only normalizers, classifier and
value-head stationarity, calibration selection, original features/text/role
hashes, all saved probabilities, Brier and recorded trade costs. This reuses
hash-verified frozen features and does not claim to rerun encoders independently.

## Actual resource incident and corrective boundary

The first paired CPU extraction ran on pocket4 with a 4 GiB job memory cap and
no swap allocation. Despite a pre-read of about 8 GiB available host memory,
GLOBAL OOM occurred at 2026-10-08 11:02:07, 11:02:52, 11:02:54 and 11:03:39 UTC.
The kernel killed existing Laya evaluator workers (three restarts) and the new
encoder job. Its recorded peak was 2,370,920,448 bytes, below its 4 GiB cap;
therefore the cap did not isolate the host's concurrent UMA/memory pressure.
System swap was full. It is incorrect to claim this attempt did not interfere
with existing learning just because eight other main service states matched.

The failed attempt, kernel log, evaluator state, partial progress and source
plan remain retained. No result or complete paired feature file was produced.
No heavy job was restarted on pocket4, no kernel/swap limit was relaxed, no
existing evaluation result was overwritten, and no trading service was manually
restarted. Laya's own service recovered, but active status alone is not a claim
that missed evaluation slots were recovered.

Recovery is offloaded to the authorized qian-qi CPU host. `resource_admission`
requires that exact compute host, at least 64 GiB total and 32 GiB available,
nonexhausted swap and low full-memory PSI, in addition to a 4 GiB process cap.
The same original paired plan/input hashes and model bytes are used; the host,
Python version and CPU-only PyTorch wheel are disclosed as a backend change.
Both arms use the same recovered environment. Full checkpoint and source hashes
are reverified before inference. This is recovery from an incomplete resource
failure, not another try selected because of a bad model score.

Do not copy account credentials or reuse a production environment. The private
old-test file is copied only as a forbidden-file denial probe; the learning
sandbox cannot read it. Asset files are copied unchanged; no user model weights
are committed into this source repository.

## Account/production evidence stays separate

The newer #178 real Broker readback matched 11 returned executions to their
original native commands. None matched the three unresolved BTC orders
281/290/299 by original account, permanent ID and orderRef. There are 56 sent
commands outside that returned history scope. Neither absent records nor zero
open orders prove that the original remainders were canceled. No new empty
Broker query or guessed terminal journal update was performed.

Read-only strict NAV assessment still fails. Neither returned account callback
ends nor a legacy numerical NAV flag supplies common broker valuation time,
opening NAV and external-flow coverage. Source statements were not found in the
inspected local account directories or non-generated Library search results;
that is not a claim that statements do not exist elsewhere. No mailbox access
was performed when that connector required authorization that was declined.

## Operation

Use the installed config for recurring collection, never a mutable working tree:

```sh
/path/to/python -B -m historical_training.multiwindow --config /private/campaign/config.json
/path/to/python -B -m historical_training.multiwindow --config /private/campaign/config.json --status-only
```

Before a host move, preserve the exact paired plan/input/model and failed-attempt
receipts; use a new compute output namespace with disclosed custody. The admitted
encoding entrypoint is `historical_training.resource_admission`, followed by
`matched_fit` and `matched_audit`. The broker-capable research collector stays on
pocket4; the heavy offline encoder worker receives no broker or account authority.

To stop the research campaign, disable its own timer and stop its own service.
Stopping an in-flight window censors it. It never authorizes a replay or affects
existing FX/BTC strategy ownership. Keep all original snapshots and negative
results; no source/strategy/model can be promoted by this finite campaign alone.
