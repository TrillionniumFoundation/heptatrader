# Cost-aware PAPER admission and independent research

## Scope and current evidence boundary

This candidate continues the actual driver bytes from 387a1ed and the PnL source
lineage at 523bbcf. It imports the prior isolated six-helper package, preserves
its 140 tests, and adds actual-base integration plus field-observation regressions.
Before integration all 14 delivered Python source/test hashes were checked exactly.
The active original driver hash is
`df3e05610ffd29797db39ad231b765955a3fdf83b35e6f7c41a85534a213cefb`.

The package is under research/cost_aware. Its files do not replace Execution,
the main release link, PnL accounting, driver state or HSR1 commands. The optional
CostResearch install component creates separate tools and an independent read-only
user unit. Trading use still requires explicit --enable-paper-hooks, the
hash-pinned original driver, native Broker readiness and a separately accepted
remote-model strategy rollout. Resident remote-model admission does NOT read or
require a local training calibration artifact. The research observer never calls
driver main, prepare/submit, pending reconciliation or guard release. No automatic
local-model training, strategy promotion or trading switch exists.

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

The adapter refuses OPEN before prepare when the remote candidate, authoritative
quote, deterministic cost edge or evidence is invalid. The raw position-neutral
remote forecast is priced scenario-by-scenario against the current quote; actual
commission schedule, bid/ask spread, provisional slippage, holding reserve and the
configured minimum net-edge safety margin are deducted before admission. No local
training cohort or calibration file can grant or deny the resident entry. After
native prepare and immediately before the single original submit transport it
re-reads authoritative flatness/orders/owner readiness and a fresh quote, then
recomputes the same raw remote candidate. A failed last check is a definite
NON-SEND through the existing driver recovery path. The original command and
durable POSSIBLY_SENT marker are retained; no replacement ID or retry appears.
This is a driver-layer cost check, not an Execution-wide permit for all clients.
Native risk/identity/lease/reduce-only gates remain mandatory and unchanged.

Old positions retain original exit behavior. New confirmed fills persist the
entry forecast in the same first atomic state write that clears pending; flat
close settlement writes cooldown at that same boundary. Safety exits never wait
for a calibration file, storage success, cooldown or recovery of entry fees.

## Remote forecast, optional local training and no-adjustment region

The remote forecast is position-neutral signed underlying MID-return scenarios
with probabilities, horizon, invalidation price, source reference and exact model
identity. Actual held position is not supplied, permitting common candidates
across independent state paths. Model confidence is not calibration or execution
authority. Resident admission uses this raw distribution only as a candidate and
applies deterministic transaction-cost arithmetic before any OPEN.

Local-model training is a separate optional workflow. Its calibration builder
accepts only past-known, nonoverlapping observations per instrument, direction,
horizon and exact forecast/model version. local_training_ready requires at
least 30 independent samples across two UTC dates; extrapolation beyond observed
support, future labels, late unobservable labels, invalid hashes and synthetic
training data are refused. This readiness is informational for the current
remote-model strategy and is NOT a deployment fail/pass condition. A q90
overprediction haircut remains available for future local-model research but is
never read by the resident remote-model adapter.

Remote strategy qualification instead uses a fixed-time chronological OOS window
with no refit in that test period. The same raw remote predictions feed the
direction baseline and deterministic cost-aware paths. Strategy acceptance
requires completed observed paths, completed cost-aware round trips, positive net
return and positive net advantage versus the same-input direction baseline under
every configured fee/slippage stress, plus the explicit drawdown budget. HOLD-only,
zero-trade, incomplete or synthetic runs cannot qualify. These are engineering
screens, not statistical significance or live execution proof.

Discretionary remote reversals use a heuristic cost/no-adjustment region based on
the raw remote candidate and incremental exit costs; already-paid entry fees are
sunk. Safety exits remain independent of model training, recording, cooldown and
transaction-cost recovery.

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

### Reproducible remote-model chronological OOS

The replay executable accepts one predeclared OOS start, a later last-decision
boundary, and the remaining quote-only outcome tail:

    python3 research/cost_aware/scripts/hepta_cost_oos.py evaluate \
      --capture /private/current-cycle/immutable-prefix.jsonl \
      --plan /private/current-cycle/preregistered-oos-plan.json \
      --output /private/current-cycle/remote-oos.json

Choose both boundaries before examining performance. The remote OOS path does not
call fit_from_tape, does not accept --calibration, and does not refit any holdout
outcome. Candidates known before the OOS start or after the last-decision boundary
are excluded with their identities/reasons retained. Post-decision quotes may
complete existing outcomes and exits but cannot start a new position.

The three paths start flat from the same fixed OOS input. The direction baseline
is cost-unaware at admission but uses the same risk exits. The two remote cost
paths use raw remote forecasts plus deterministic fees, spread, slippage, holding
reserve and the net-edge safety margin; one also applies the no-adjustment/reentry
band. Each path owns its own cash, positions, fees, exits and cooldown state under
each cost stress. A trade rejected by one path changes that path's later state;
the replay never deletes historical trades from another path.

strategy_accepted requires real IB_CAPTURE input, at least one observed
directional OOS candidate, complete directional outcomes, an explicit drawdown
budget, at least one completed round trip in each cost-aware path, positive
cost-aware net liquidation PnL and strictly positive net advantage over the
same-input direction baseline under every configured fee/slippage stress. A
zero-trade path cannot pass by merely avoiding fees. Local training cohort count,
30x2-day readiness and local calibration artifacts are absent from this decision.

research_acceptance_passed is retained as an alias of that fixed-time remote OOS
screen for report compatibility. Even a true value writes no active calibration,
switches no driver, grants no Broker permit and proves no live MKT execution
quality. Exact source/account/cycle, native PAPER qualification, operational
readiness and controlled rollout remain separate.

### Optional local training assessment

--fit-cutoff-ms remains an offline local-training/calibration operation. The
30 nonoverlapping-sample/two-UTC-date rule and q90 haircut apply there only.
local_training_ready and local_training_cohort_count describe whether local
model training has enough accepted history; local_training_deployment_blocker
is always false for the current remote-model strategy. Local-model training may
later use its own chronological evaluation, but it cannot retroactively gate the
current remote strategy or provide runtime authority.


## A new-cycle native WATCH path after an accounting reset

A global RESET-STOPPED marker continues to stop trading and all legacy launch
paths. The read-only collector alone accepts an explicit `--observation-profile`
whose identity is bound to the unchanged reset digest/account, a newly created
WATCH token and lease, one instrument, new private cycle directories and pinned
native client executables. The trading adapter has no equivalent bypass option.
A new or changed reset marker, changed profile/token, expired/unknown lease or
failed native authority check stops observation; none is repaired by clearing a
reset, importing old credentials or promoting a calibration.

The same native IB runtime now has `HEPTA_IB_PAPER_OBSERVATION_ONLY=1`. It uses
its original callback/quote-subscription engine and Broker account handshake,
but the wrapper is read-only, order submission is disabled and native place,
cancel and flatten guards reject independently. Its credential prefix is
`PAPER-OBSERVATION-V1` and is separately bound to the observation state directory;
it cannot be reused as a trading profile credential. This mode does not load,
create or refresh financial FX checkpoints, and publishes a permanently
mutation-blocked `IB_OBSERVATION_ONLY` health reason. Quotes/account-download
readiness is not financial account readiness, NAV acceptance or trading permission.

The Gateway is configured with `HEPTA_TOOL_ALLOW_TRADE=0` and only the `watch`
template. Exact BTC/PAXOS/USD and EUR/IDEALPRO/USD can be read in the same WATCH
universe without defining a crypto trading session. WATCH caps omit risk.preview,
trade.place, trade.cancel and trade.flatten. Native session provisioning and
renewal, not a JSON assertion, establish access. The collector uses only
`market.get_quote`; its fixed WATCH lease renewal cannot call paper-resume.

Observation config and state live below `observations/<cycle>/`; they never
replace execution.env/gateway.env or the live trading release. Credential cleanup
retains only metadata/digests as evidence, not plaintext secret backups. IB login,
model authentication, unrelated SSH credentials and separately collected new
financial baseline evidence are not part of that deletion scope.

A capture header records its reset/cycle/account/agent/session/source identity.
The writer refuses old unscoped captures and a different observation cycle.
Same-cycle process restart can append to a verified hash chain, but Gateway
restart invalidates native WATCH sessions: it requires successful new WATCH
provisioning rather than treating an on-disk lease as restored authority.
The original 5-second quote freshness and source-clock checks, 2-second capture,
10-second features, 120-second model cadence, nonoverlap/date calibration minima
and cost acceptance remain in effect. Enabling read-only observation never
activates a calibration, unmasks a trading driver or asserts profitability.


## Sampling quality and restart freshness (2026-10-06)

The replay tool can diagnose a complete immutable capture prefix without placing
orders, selecting a strategy, changing TTLs or activating calibration:

```bash
python3 research/cost_aware/scripts/hepta_cost_replay.py \
  --capture /private/current-cycle/frozen-prefix.jsonl --quality \
  --expected-cycle-id "$CURRENT_OBSERVATION_CYCLE" \
  --expected-reset-sha256 "$CURRENT_RESET_DIGEST" \
  --output /private/current-cycle/quality.json
```

This reports valid reads separately from distinct subscription/timestamp updates,
repeated cached reads, the union of genuinely usable quote-time intervals,
source-age and model-latency quantiles, 30-second source gaps, forward outcomes,
per-candidate calibration exclusions and nonoverlapping cohort/date shortfalls.
A repeated read never extends the original source TTL or becomes an independent
calibration sample. Failure events and uncovered time remain visible. The
record-level valid fraction is not a claimed exchange delivery probability.
Conflicting same-subscription/timestamp prices prevent fitting that history.
Expected cycle/reset identities are checked; an integrity hash is not a vendor
signature. All reports have authorization_effect=NONE and never promote a model.

A real collector restart exposed a startup race: the first read could be stale
although the next genuine quote was about to arrive. Previously this was treated
as permanent configuration failure (exit 2). The observer now retries only the
explicit native/source stale-quote codes, at most ten attempts with two-second
spacing and a twenty-second pre-attempt retry budget. Existing native I/O timeouts
remain; an already in-flight read may finish after that retry budget. Exhaustion
uses temporary exit 75 for the existing thirty-second systemd retry policy.
Wrong identity, missing permissions, bad data and reset holds do not enter this
retry path. No capture is opened and no COLLECTING status is published before a
real authoritative, within-TTL quote passes. Tests verify both finite retries and
no retry after credential/authority/reset failures.

Startup attempts and retained stale reasons are recorded in collector status,
with the actual observer implementation SHA256. A failed/late restart is still a
real gap; historical capture bytes are never removed or backfilled to pass a
path-continuity check. Original WATCH native endpoint identity in the header is
not confused with the updated Python collector implementation.

These diagnostics and retry fixes do not complete financial-cycle readiness,
remote-model OOS, live execution quality or a trading rollout. The 30
nonoverlapping-sample/two-UTC-date rule now describes optional local-model
training readiness only and does not block the remote-model OOS or deployment
decision. Remote OOS still requires complete observed outcomes, actual cost-aware
round trips, positive net advantage across stress cases and the explicit risk
budget. BTC remains spot-long-only for new entries; a SELL forecast may be useful
as observational or exit evidence but cannot authorize a new short position.


A live observation-only reconnect also exposed an incorrect shared trading
barrier: reconnect dispatched completed-order history and waited for terminal
reconciliation before refreshing the account. Observation reconnect now uses
the same account-plus-authoritative-quote requirement as observation startup.
It never requests open-order/terminal history on that route, creates a financial
checkpoint or changes the permanent no-mutation guard. The normal PAPER trading
open/terminal/risk sequence is unchanged and covered by the same request-selector
regression tests. A real observation transport recovery is not a trading restart
qualification.


### WATCH access continuity after native renewal

A rotated access segment keeps its token and lease within the exact reset-cycle
and instrument namespace. The pinned profile digest, token digest, native lease
identity, expiry, and no-trade mode remain mandatory. Initial native issuance may
precede profile persistence by at most 60 seconds. A later accepted native WATCH
renewal must not be rejected merely because the original profile was created
more than 60 seconds earlier; the next local check and reader restart must retain
that accepted renewal without changing the profile or token. Expired, future,
foreign-session, changed-token and reset-mismatched evidence still fails closed.
This does not restore a revoked WATCH token or grant PAPER trade authority.


### Fixed remote OOS preregistration and acceptance hardening (2026-10-06)

Use `research/cost_aware/scripts/hepta_cost_oos.py register` before observing any
of the decision window, then `evaluate` on a verified immutable capture prefix.
The legacy replay CLI remains diagnostic: a user-labelled `IB_CAPTURE` tape and
an implicit file tail cannot grant strategy acceptance. No extra branch, local
training cohort, or calibration artifact is required.

The spec has exactly these fields, with no inferred monetary or sample limits:
`training_end_ms`, `decision_start_ms`, `decision_end_ms`, `outcome_end_ms`,
`min_directional_candidates`, `min_completed_round_trips`, `max_drawdown_usd`,
`max_turnover_usd`, `max_reentries`, `size`, and `capital`.
`training_end_ms` freezes the pretest evidence/configuration boundary; it does
not mean fitting a local model. Choose research limits before the window; a
small engineering count is not a statistical significance claim or a live risk
budget. There is no local 30-sample/two-UTC-date prerequisite for remote OOS.

    python3 research/cost_aware/scripts/hepta_cost_oos.py register \
      --capture /private/current-cycle/registration-prefix.jsonl \
      --spec /private/current-cycle/fixed-oos-spec.json \
      --expected-cycle-id "$OBSERVATION_CYCLE" \
      --expected-reset-sha256 "$RESET_SHA256" \
      --output /private/current-cycle/preregistered-oos-plan.json

The exclusive-create, fsynced plan binds the WATCH context hash, reset and cycle,
registration prefix/chain head, implementation hashes, model identity, cost
policy, stress cases and all limits. Registration must precede decision_start;
backdated registration, in-window implementation/policy changes, a changed
prefix, wrong cycle and synthetic sources fail. A local hash proves integrity,
not vendor authentication or a trusted external timestamp. Protect the private
operator directory; an actor who can rewrite code and local evidence is outside
this offline research trust boundary.

Evaluation does not expand outcome_end when more data arrives. The final mark
must be observable by that endpoint, never borrowed from a later quote. HOLD,
late results, missing outcomes and gap-crossing candidates remain in the original
capture and the outcome/rejection evidence. Late directional forecasts do not
inflate the timely candidate minimum; missing selected outcomes reject the
assessment. Historical gaps before decision_start remain in the bound prefix,
but are not invented as continuous observations or eligible holding paths.

Every cost-aware path in every predefined stress case requires sufficient
completed round trips, positive simulated net PnL, complete observed holding
paths, no residual simulated position, explicit drawdown/turnover/reentry bounds,
and net PnL not below the same-input direction-only baseline. Equality with a
positive baseline is allowed; neither equality at zero nor HOLD-only activity
passes. Reentry count is the number of opens after the first open in that path.

Costs remain conservative schedule-based simulations, not actual account
commission receipts. BTC rebates are not credited until confirmed. Existing
quote TTL, pre-submit quote checks, no-adjustment band, reentry gate, spot-BTC
short prohibition, missing-fee handling, and priority risk exits are unchanged.
PENDING/FAIL/PASS reports always have `authorization_effect=NONE`, no orders and
no driver switch. Only a separately authorized, subsequently verified controlled
PAPER cutover may change runtime admission.
