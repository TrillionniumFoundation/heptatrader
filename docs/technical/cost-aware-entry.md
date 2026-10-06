# Cost-aware PAPER admission and independent research

## Scope and current evidence boundary

This candidate continues the actual driver bytes from 387a1ed and the PnL source
lineage at 523bbcf. It imports the prior isolated six-helper package, preserves
its 140 tests, and adds actual-base integration plus field-observation regressions.
Before integration all 14 delivered Python source/test hashes were checked exactly.
The active original driver hash is
`df3e05610ffd29797db39ad231b765955a3fdf83b35e6f7c41a85534a213cefb`.

The package is under `research/cost_aware`. Its files do not replace Execution,
the main release link, PnL accounting, driver state or HSR1 commands. The optional
CostResearch install component creates separate tools and an independent read-only
user unit. Trading use requires explicit `--enable-paper-hooks`, a hash-pinned
original driver and an accepted real calibration artifact. The research observer
never calls driver main, prepare/submit, pending reconciliation or guard release.
No automatic calibration promotion or trading switch exists.

## Deterministic costs and entry boundary

BTC uses conservative cash-paid 18 basis points, minimum USD 1.75 per original
executed order, before unconfirmed tier/cap rebates. FX EUR/USD uses 0.20 basis
points on USD notional, minimum USD 2 per order. Actual commissions and confirmed
rebates stay in the separate PnL ledger; estimates never change booked economics.
Every distribution scenario prices its own exit notional and fee before probability
weighting. Spread and provisional slippage are deducted once from MID returns.
Holding reserve and minimum net-edge margins are provisional assumptions, not
measured account financing or demonstrated execution quality. Official schedules
were checked on 2026-10-06; broker-specific additions still need account evidence.

The adapter refuses OPEN before prepare when calibration, quote, cost or evidence
is missing. After native prepare and immediately before the single original submit
transport it re-reads authoritative flatness/orders/owner readiness and a fresh
quote, and recomputes against the same anchored forecast. A failed last check is
a definite NON-SEND through the existing driver recovery path. The original command
and durable POSSIBLY_SENT marker are retained; no replacement ID or retry appears.
This is a driver-layer cost check, not an Execution-wide permit for all clients.
Native risk/identity/lease/reduce-only gates remain mandatory and unchanged.

Old positions retain original exit behavior. New confirmed fills persist the
entry forecast in the same first atomic state write that clears pending; flat
close settlement writes cooldown at that same boundary. Safety exits never wait
for a calibration file, storage success, cooldown or recovery of entry fees.

## Forecast, calibration and no-adjustment region

The forecast is position-neutral signed underlying MID-return scenarios with
probabilities, horizon, invalidation price, source reference and model identity.
Actual held position is not supplied, permitting common candidates across three
independent state paths. Model confidence is not calibration or execution authority.

Calibration accepts only past-known, nonoverlapping observations per instrument,
direction, horizon and exact forecast/model version. At least 30 independent
samples across two UTC dates are required. Extrapolation beyond observed support,
future labels, late unobservable labels, invalid hashes and synthetic calibration
are refused for resident entry. A q90 overprediction haircut is empirical and is
NOT guaranteed statistical coverage under regime change. Meeting sample minimums
alone is not sufficient for strategy qualification: chronological out-of-sample,
cost-stress and live-execution limitations must still be evaluated before promotion.

Discretionary reversals use a heuristic cost/no-adjustment region; this is not an
optimal-control proof. Already-paid entry fees are sunk, not a reason to delay a
necessary exit. No favorable real calibration artifact has been produced so far.

## Independent sampling and causal replay

The independent observer invokes only market.get_quote and position-neutral model
inference. It polls every two seconds by default and considers a model slot every 120 seconds;
model history retains the original ten-second grid, so denser source recording
does not silently increase inference cadence or alter the feature schedule.
Missing/stale quotes and busy/warmup slots are explicit events. Missed feature
slots are never filled by a later quote, and slow polls do not cause catch-up bursts. It does not pause
because a trading driver has a pending order. Recorded source timestamps remain
the native quote observed_at_ms, not a fabricated exchange tick or local heartbeat.
Only bounded safe error codes enter failure evidence; arbitrary exception output
or credentials are omitted. Current status is written only to the observer's own
private directory. Repeated windows retain one hash chain and never delete history.

A field regression found that a model may return after the cached quote's 5-second
TTL but before the next 10-second sample. The converter now delays evaluation to
the next actually observed fresh quote, retaining the original model-availability
time, reference price and expiry. It never assigns that future price to the earlier
model timestamp. No later valid quote leaves a rejected/unknown candidate in the
record. An expired missing label window is distinguished from an unmatured horizon.

Three replay states independently maintain cash, quantity, fees, stops, holding
clock and reentry state: direction baseline, cost admission, cost plus band/cooldown.
Baseline means the direction rule on this NEW position-neutral tape with common
risk exits, not a reconstruction of the old position-conditioned model trades.
Rejected and HOLD candidates retain their forward outcomes. Missing marks or
unobserved held paths produce unknown main metrics; they are not silently removed.
Five cost/slippage stress cases rebuild all three paths. A simulation is never
reported as Broker PAPER or live profit, and an all-blocked zero-return path does
not establish strategy merit.

## Observed first on-host window

Two independent 1,200-second read-only runs completed with exit 0. BTC recorded
75 successful quote observations, 45 quote-read failures and 7 actual model
candidates; FX recorded 95 observations, 25 failures and 5 candidates. These are
not 170 trades. Initial labels were either not mature or missing/gapped; no valid
calibration cohort was produced. Neither actual account credentials nor raw private
market/model captures are committed to this repository. Private receipts remain
on pocket4 in operator-evidence/cost-integration-20261006T063154Z.

The shared Execution process restarted independently during this work. The two
trading drivers were left on their original bytes with original pending commands.
Unchanged process identity across the whole run is therefore NOT claimed. A live
switch remains blocked by missing qualified calibration and requires a fresh
runtime/command-state verification, not this document or a zero-order simulation.

## Installation and operational limits

Explicit `cmake --install <build> --component CostResearch --prefix <new-release>`
installs six helpers and the user-unit template, not any native execution binary.
The chosen private release/current link is separate from the main trading release.
The instance names are BTC.USD and EUR.USD; other instruments fail argument parsing.
The user unit has no Wants/Requires dependency capable of starting trading.

Each capture is owner-only, no-follow, single-writer, fsynced and bounded at 64 MiB;
free filesystem reserve remains 4 GiB. No pruning is automatic. The user unit limits
individual files to 80 MiB, memory to 1 GiB, invocation to 86,700 seconds and permits
170 seconds to finish an already requested model call. Configuration/storage errors
exit 2 without automatic retry; ordinary bounded completion can start a new window.
Model inference consumes resources independently of trading and must be included
when assessing whether the research service is worth keeping resident.

Rollback of independent research is stop/disable of its own two units only; no
trading state restore, pending deletion, cash rebasing or Desktop Commander change.
A future trading rollout must retain the existing state directories and native
command files, save service drop-ins and exact versions, verify original pending
commands, and exercise reject/close/restart behavior before new entry is enabled.

## Tests and references

The cost suite is registered with the existing core Python partition through
`tests/python/test_cost_research.py`; no test-count gate is lowered. The real-base
fixture checks original source bytes and uses a private copy for a group-writable
source checkout; the launcher itself continues rejecting unsafe source modes.
An explicit HEPTA_COST_BASE_DRIVER points acceptance at the installed immutable base.
All venue effects in tests are mocked. Process/all root fixture tests are not run
on this trading host. Test receipts distinguish this host from prior containers.

Primary references:
```
https://www.interactivebrokers.com/en/pricing/commissions-cryptocurrencies.php
https://www.interactivebrokers.com/en/pricing/commissions-spot-currencies.php
https://www.ibkrguides.com/clientportal/aboutpapertradingaccounts.htm
```
PAPER top-of-book simulation cannot establish achievable live MKT slippage.

## Denser evidence without weaker freshness

The initial ten-second poll interval exceeded the five-second endpoint-label
window. The observer now accepts --sample-sec 1/2/5/10 (default 2), while model
features remain on the original ten-second grid and model slots on 120 seconds.
Failed history slots remain missing; no future quote is backfilled into them.
Slow reads skip missed polling slots rather than producing a catch-up burst.
Evidence writes on the observer quote path are mandatory: storage failure stops
that sample instead of incrementing successful samples after a swallowed write.
The existing five-second quote/label TTL and thirty-second path-gap checks are
unchanged. This improves collection opportunity, not source availability or
statistical sufficiency. It does not authorize trading.

Default runtime packaging excludes the cost-observer unit; only explicit
CostResearch installation supplies that unit together with its helpers. This
avoids publishing a service whose research executable was intentionally omitted.


## Reset-aware continuation and a fixed chronological holdout

The source is maintained on `main`. The factory-reset marker
`~/.local/state/heptatrader/RESET-STOPPED.json` is an explicit operator stop,
not a transient quote failure. Its presence (including a broken link or unreadable
marker) blocks both executable entry points before loading the pinned driver,
creating a capture or requesting a model/native quote. It is checked again at
sampling, model request/result and new-entry preparation/final-submit boundaries.
Exit 78 does not trigger the observer's automatic restart. These paths never
remove the marker, unmask a service, recreate account credentials, replace a
pending command or restore deleted historical samples.

A reset between prepare and submit returns the existing definite-non-send result
through the original command path. Existing reduce-only handling is not blocked
by cost calibration, recording or the added new-entry check. This is not authority
to restart a stopped trading process to liquidate an account. Resuming the
factory-reset installation still requires a new, explicitly configured PAPER
runtime/account profile. A removed marker by itself is not authentication or a
calibration receipt. Both executable launchers provide the reset check; injectable
host fixtures are not a production launch route.

A collector must obtain and validate an actual first authoritative quote before
it opens its capture or reports COLLECTING. Missing credentials or an unavailable
native endpoint therefore cannot masquerade as an operational observation stream.
No initial quote is replaced with a synthetic value. Existing quote TTLs, feature
sampling cadence, model identity, calibration sample/date minima and order-cost
policies remain unchanged.

### Late inference is excluded individually, not an entire failed fit

`fit_from_tape` preserves every candidate's forward outcome, including HOLD and
late results, but separately lists why a record cannot train a directional
calibration. A result that arrived after the entry TTL cannot train the model;
it also must not cause all timely records in the same input to be discarded by
an exception. HOLD outcomes do not increase usable directional sample counts.
Input corruption, duplicate candidate identity or a broken capture hash chain
still fails rather than being repaired or silently ignored.

### Reproducible chronological assessment

The existing replay executable now supports a fixed training cutoff, a later
last-decision boundary, and the remaining observed quote-only outcome tail:

```bash
python3 research/cost_aware/scripts/hepta_cost_replay.py \
  --capture /private/new-cycle/immutable-prefix.jsonl \
  --fit-cutoff-ms "$TRAINING_CUTOFF" \
  --holdout-decision-end-ms "$LAST_TEST_DECISION" \
  --max-drawdown-usd "$EXPLICIT_RESEARCH_RISK_BUDGET" \
  --output /private/new-cycle/chronological-assessment.json
```

Choose the boundaries before examining performance; this tool does not optimize
cutoffs or select the best run. Training sees only rows strictly before its cutoff
and labels actually known then. The one resulting calibration is frozen; no
holdout outcome is refitted into it. Forecasts that straddle the split are purged
with their identities/reasons retained. Post-decision quotes complete existing
outcomes and exits but cannot start a new decision. The three simulated paths
start flat together, then each maintains its own positions, fees, risk exits and
reentry clock under each of five cost/slippage stresses.

The report includes all candidate outcomes, per-cohort nonoverlap/date coverage,
all three complete paths, per-stress comparisons and explicit rejection reasons.
A no-trade, HOLD-only, uncalibrated, open-ended, nonpositive, missing-price or
synthetic run cannot qualify. The heldout engineering screen uses the same
30 nonoverlapping samples / two UTC dates minimum, and requires at least 30
completed simulated round trips in each cost-aware path under every stress.
Those floors are engineering checks, not statistical significance guarantees.
The drawdown budget is explicit; the tool does not invent an account risk limit.

`research_acceptance_passed` is only a fixed-holdout research screen. Even a true
value writes no active calibration, switches no driver, grants no Broker permit
and proves no live MKT execution quality or profitability. Exact source/account/
cycle, native paper qualification, operational readiness and controlled rollout
remain separate decisions. Ordinary source CI is not conditioned on meeting
these financial sample counts; tests use synthetic inputs to exercise behavior.
