# Bounded Laya storage recovery and quote continuity (2026-10-08)

## Scope and retained evidence

This recovery changes learning infrastructure, not the USD100k original-0.62 FX
strategy, BTC policy, native execution, actor, model selection or financial A/B.
The current 2 GiB application state budget is explicitly raised to 8 GiB. There
is no auto-expansion. New learning admission also reserves 512 MiB inside the
state budget and 10 GiB on the filesystem. These are admission checks, not a
kernel/filesystem disk quota or a guarantee that any future faulty writer cannot
overrun. CPU/memory/job-count/time limits remain in force. All previous candidates,
source prefixes, decisions, evaluations and failed outcomes remain on disk.

`resident_budget.inspect` reports actual usage and reserves. It rejects symlinks,
invalid/unbounded configuration and lack of space without clearing evidence.
`recover_quota` permits only the reviewed 2-to-8 GiB transition with fixed reserves;
it cannot modify objective, model profile, period, trading authority or directories.
It requires no admitted/running trainer job and only terminal old evaluation
plans. Prior unit/config bytes and SQLite snapshot are retained. It updates only
Laya evaluator and trainer to the same content-addressed source; all trading,
collector and completed Fusion processes remain pinned. Old model/plan hashes
are verified after recovery; changed status/SQLite state is expected to advance.

## Two distinct quote failures

The 24-slot rejected Fusion experiment had four endpoint gaps, two history gaps
and one path gap. On the two history-failure slots, every one of 21 actual input
opportunities failed under the old selector. Raw adjacent quote timestamps were
within 30 seconds, but backward 10-second thinning selected gaps of 30.381 and
36.404 seconds and then rejected the entire history. The corrected selector
retains only its recent supported selected suffix; it does not interpolate,
duplicate prices, rewrite timestamps, reduce minimum six points, or increase the
30-second continuity rule. Training and inference share this selector. Records
excluded because the suffix is too short remain explicit dataset exclusions.

The four actual endpoints arrived 8.452, 7.178, 8.018 and 5.719 seconds after the
registered target, beyond the original five-second tolerance. The polling record
continued around one second and native replies became AUTHORITATIVE_QUOTE_STALE.
Those are real missing timely source updates in this capture, not evidence of a
blocked local collector. A new timestamp for an unchanged cached quote, an earlier
quote substituted for a later endpoint, or a wider tolerance would change the
experiment and is NOT a repair. The old predictions and REJECTED verdict are not
rewritten after the input selector fix.

## Source readiness, not automatic experiment acceptance

`source_readiness` examines a fixed grid of 24 matured minute probes with the same
5-minute horizon, 60-second input grace, 5-second endpoint tolerance, 30-second
path bound and minimum 20 usable/maximum four unavailable probes. The overlapping
probes measure operational coverage only, not 24 independent efficacy observations.
It commits no model prediction or reward, does no provider/trading operation and
never opens a new trial. A freshest-quote or coverage failure reports
HOLD_NEW_FUSION_WINDOW. A good report grants only manual review, not selection.
An optional confined resident monitor writes only its own current health file.
Its health timestamp is a read time, never an invented source-quote timestamp.

## Verification

Synthetic tests cover exact budget boundaries, disk reserves, malformed budgets,
symlink scope, full evidence retention, decimation-induced gaps, genuinely missing
history, original timestamps, TTL, absent endpoints, coverage and no effect
permission. Original 21+21 input opportunities are independently replayed only
for input availability; no new forecast is fabricated for the old time window.
Existing Laya/native, source/core and original trading tests remain required.

The quote and state fixes do not create current USD100k closed net-reward labels.
Old EUR25k/churn data and unrealized PnL remain excluded from its English-base
financial A/B. Financial-value training, canonical owner enrollment, new Fusion
window registration and model promotion are not side effects of this recovery.
