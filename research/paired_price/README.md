# Prospective independent Numeric × Laya price test

This source tree is experimental, has no trading or model-selection API, and
never mutates the pre-existing FX/BTC PAPER trader, Execution, Gateway, Laya
candidate scheduler or previously rejected temporal fusion trial.

## Causal training

The registered 300-second target is the existing seven-bin future EUR/USD MID
return. Each training input comes from an immutable source prefix and is
available before its recorded label. Only source-authoritative quote histories
are used. No remote strategy prediction, PnL, future fee, HOLD label or future
outcome is a training input.

A fresh numeric GRU and a separate 768-to-7 head on a frozen, *full pretrained*
Laya encoder are fitted independently on 256 mature rows. Their parameters
are then frozen. A new learned probability gate is fitted on 64 chronologically
later rows. The last expert outcome is known more than 305 seconds before the
first gate input; sample and outcome IDs are disjoint. This fits the gate;
it is NOT unbiased validation on those same 64 rows.

Five immutable forward arms: independent numeric-only, semantic/Laya-only,
fixed 50/50 average, learned gate, and the original frozen numeric baseline
from the earlier rejected experiment. The original numeric comparator is
included because weakening a newly trained numeric is not proof that fusion
improved over the strongest existing simple expert.

The gate can consume compressed 32D features projected deterministically from
full 768D encoder hidden representations, not merely seven class probabilities.
It still does NOT prove semantic complementarity. All trained models remain
frozen throughout future evaluation.

## Prospective experiment

A separate plan registers 24 future slots six minutes apart with a five-minute
label horizon. It is created only after the full model and code are immutable.
Each slot must commit one complete five-arm forecast within the original 60s
input grace. The independent evaluator imports no torch or optimizer and uses
original actual source timestamps, anchor prices and max-30s path continuity,
with a five-second future endpoint tolerance. Late inference and missing quotes
remain censored.

The research screen requires at least 20 observed slots, at most four censored,
two target classes, at least eight samples per time half, positive mean Brier
improvement and a one-sided exact sign screen at 0.0125 against each of four
comparators. Because market labels are serially dependent, this naive sign
screen is an exploratory filter, **not time-dependence robust canonical
qualification**. It never promotes, selects or trades even if all checks pass.

The previous candidate's 10 observed/2 censored REJECTED assessment is retained
and is not counted or amended as an observation for the new candidate.

## Remaining financial A/B blocker

Laya-English and Laya-mmBERT official full checkpoints already passed
identical-source-input, strict state-dict and ROCm engineering checks. That is
not predictive gain. The USD100k policy currently has zero independently
settled closed net-USD rewards with exact original-command attribution. The
currently open short cannot be used as a training target. Earlier 25k/churn
behavior rewards are not re-labelled as the USD100k policy.

A separate pair of USD monetary readouts must train on the *same exact closed
episodes*, budget known at request time, freeze before a future 14-day+1-day
net-PnL window, seal both before original PREPARED, and score actual realized net
USD including all reported commissions with the identical original-command
denominator. The later PREPARED integer EUR quantity/reference price are only
verification evidence; they cannot enter an earlier model-request input.
This current price trial does not solve or bypass that blockade.

Future directions are conditional: distributional value requires adequate
independent outcomes and tail calibration; IQL/CQL or SPIBB requires real
behavior-policy coverage and distinct effect authority. Meta never sends orders,
and canonical Agentd admission is a separate product path.

## Independent reconstruction of the live fixed window

`paired_price.independent_audit` is a separately invoked **read-only** reviewer.
It verifies the frozen candidate/plan and runtime file digests, each prediction
against its immutable prefix, the actual source quote path and future endpoint,
and every saved per-arm Brier against a fresh calculation. Its 24-slot denominator
also retains slots not yet due, missed slots, recorded input failures and pending
outcomes. It never modifies source forecasts, the original assessor or trader.

An audit before window maturity must report `WAIT_FIXED_OUTCOMES_NO_ADVANTAGE_CLAIM`.
Its sign screen remains exploratory with serially dependent prices; no independent
canonical owner, production selection or model effect is implied. Tests include
actual first-slot replay against the verified source and tampered copies of
plans, predictions, stored Brier and source prefixes. The sandboxed resident
services continue to own only their original experiment artifacts.


## Fail-closed source-quality gate for later trial registration

`paired_price.install` now refuses to register **any new candidate trial** unless
an independently recomputed source-health check of the *current* immutable quote
prefixes passes the original operational screen: 24 fixed matured one-minute
probes, at least 20 usable, at most four missing, latest source quote under the
existing five-second TTL, all 30-second path and five-second endpoint bounds
unchanged. It validates exact accounting-period identity, fixed probe chronology,
nonfuture observations, real-prefix digest and NO trade/effect authority; it does
not trust a caller-supplied `READY` status file. The quality snapshot is sealed
into any future trial plan, but is not an independent future-efficacy result.

A previously registered candidate cannot start another prospective plan, even
if its original outcome was rejected; a substantively changed/frozen candidate
with its own identity and multiplicity accounting is required. The original
24-slot plan, its 17 observed/7 censored and negative numeric-only/fixed-mix
comparisons are immutable. On insufficient source quality, the installer fails
before creating a new release, new plan or systemd unit, and never alters IB
source timestamps or restarts any execution/Gateway service.
