# Train V2 / Laya — two actual preregistered original-source inputs (2026-10-13)

This is a finite SOURCE-ONLY, research-only continuation of PR #258
(`feat/laya-prospective-fast-original-review-20261013`). No current Laya
runtime, previously frozen Trial 31, original market writer, PAPER/OMS,
crypto/FX driver, canonical Agentd Owner, GPU inference, or trading authority
is changed.

## Why this is the next genuine gate

The previous archive-verified recent Reader correctly reconstructed the
original Numeric/Laya input on old market timestamps, reducing one observed
read from about 4.3–4.7 seconds to 0.7–0.9 seconds. Those comparisons were
NOT prospective predictions. The original Trial31 already has >2 permanent
CENSORED slots against its 89-slot maximum two, making qualification
mathematically impossible. It must not be retried with the faster source.

The new `historical_training.laya_fast_future_canary` is an intentionally
separate canary schema, NOT `laya-resident-future-plan.v1`. It freezes a
source configuration, account period, source-directory identity, exact Python
module hashes and one future 16-second slot with at least 60 seconds
registration lead. It performs one explicitly invoked time-bounded read from
the already archived/verified PAPER quote source, using the real fresh quote
and the existing full Numeric/Laya feature selector. It records only
sample ID, original quote/receipt/time identity and original source-prefix
hashes, not a prediction or financial label. A separate strict full-reader
replay checks identical original `sample_id`, complete sample hash,
Laya causal state and receipt at the original fixed source-as-of time.

A late source read or late fsync cannot be upgraded into a successful input,
and no missing slot becomes a zero price/negative reward. Each immutable
plan/input/ACK is created once and fsynced; a second capture refuses to
overwrite existing evidence. A code/config/period changed after registration
is also refused. The same-UID owner and local system clock remain local
trust boundaries, not independently signed Agentd authority.

## Actual Pocket4 future SOURCE-only trial #1

The initial implementation was physically preregistered 101,552ms before
2026-10-13 11:40:15 +08:00. On the actual slot, the fast reader selected an
original quote after registration, took 889.586ms and fsynced the source
input by 11:40:17.035, 13,965ms before its exact deadline.
Independent strict full-source replay at the frozen as-of reconstructed the
same Numeric/Laya sample and source receipt.

The first code exposed an unrelated *process-wide umask side effect*
when imported by the existing Python core suite. The first failing
whole-tree run had 4 failures and 2 errors in unrelated packaging/privacy
tests. This was a real bug, not counted as passing. Before modifying the
source, its exact original code bytes were preserved as the immutable
original-v1-implementation.py under the first slot and validated against
the original plan code hash
c557409ceebe14df4fe263d8e32a1f1a9af267a06e418cf3c9f24d7722e88c47.
No original plan or model trial was overwritten.

## Scoped umask fix and actual future trial #2

The library-level register function no longer changes the process-wide
umask. Only its finite CLI main sets a restrictive umask, and input/plan
files are already explicitly written with private permissions. The new
regression confirms a caller's original umask is unchanged.

A NEW second immutable plan was frozen with the FIXED code hash
1faf05b66eba9b8fb8fbeb7b034db14a26a7771911fadd92bdf205bda5f4a460.
Registration led the second real 11:45:15 +08 slot by 100,494ms.
At the actual future slot, fast read took 826.264ms (two bounded attempts),
the original quote became known after registration, and the input was
fsynced at 11:45:16.859, 14,141ms BEFORE the registered cutoff.

Original independent full-reader replay again produced the exact original
source quote and same Numeric/Laya sample/state. No source bytes were
rewritten or backdated; the first and second source receipts are distinct.

## Immutable independent double-window closeout

Private path on Pocket4:
~/.local/state/heptatrader/operator-evidence/train-v2-future-source-canary-20261013/two-prospective-original-source-final.json

SHA256:
27743d643bc7bc947a5eddc5d476896006d122d058482b8b4d1b550dd36b4713

The create-only report pins original plan/input/ACK bytes, full reader
verification logs, both exact source-code identities, and final test log
hashes. It verifies both distinct future original source events and their
true source event -> original arrival -> source as-of -> decision -> fsync
-> cutoff order. Results are exactly TWO source-only inputs, ZERO genuine
model forecasts, ZERO independent financial outcomes, ZERO broker requests
and ZERO orders. Both receipts carry real wall-clock and OS fsync proof,
but do not prove independent antirollback, a broker market-data signature,
an executable fill, or a deterministic p99 latency SLA.

## Regression on fixed implementation

- New prospective source/clock/umask adversarial tests: 14 / 14 passing.
- Historical research full discovery: 451 passed, 7 existing skips.
- Core Python full discovery: 2,509 passed, 23 existing skips.
- Laya-native virtualenv tests: 327 passed, 1 existing skip, with the
  separately existing native binary fixture configuration.
- Source/build/ownership lane: 106 passing.
- The earlier Laya suite invocation without LAYA_TEST_CONFIG and
  LAYA_RESIDENT_TEST_CONFIG failed with two missing-fixture setup errors.
  They were not disguised as passes: the final qualifying rerun supplies
  those original test-only config paths and passes.

## Next true product gate

A NEW candidate/plan, independently preregistered under this future-only
source profile, must actually produce a Laya prediction and original
independent outcome before deadlines. A single input-only canary never
qualifies the statistically failed 89-slot Trial31 or reduces its alpha
budget. Any candidate still requires a fair predeclared sign-screen and
after-cost financial evaluation versus NO_TRADE, actual broker
REQUEST/PREPARED, true behavior propensity, separate product Root/Owner
and complete account PnL evidence before model release.

There was NO current model change, no automatic future timer, no training
run, no strategy promotion, no order authority, and no modification of
old failed/missed windows in this follow-through.
