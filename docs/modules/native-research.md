# Native market research and replay

Status: EXPERIMENTAL
Applies to: SDK-free native research and unprivileged strategy integration
Implementation: `strategies/native_research`
Tests: `tests/native_research_tests.cpp`, `tests/native_strategy_gateway_tests.cpp`, `tests/native_strategy_client_link_tests.cpp`

## Scope and ownership

`hepta_research_core` owns normalized market data, explicit sessions, candle
aggregation/query, offline matching/accounting, and a completed-bar example
strategy. It owns no broker credential, network listener, OMS, production
position, live approval or venue transport. `hepta_strategy_intent_client`
is owned by agent-entry and forwards only through the existing native client.
Neither library is linked into the production Gateway or Execution daemon.

## Inputs and outputs

The CSV header is exactly
`instrument,trading_day,timestamp_us,price,cumulative_volume`.
Rows are bounded to 4096 bytes, the numeric locale is classic, and timestamps
are positive signed 64-bit **microseconds**. Trading day is a validated
YYYYMMDD label supplied by the dataset. Session boundaries are absolute,
timezone-resolved microseconds. No historical holiday table is treated as a
current exchange calendar. Mixed instruments, malformed/overflowing/nonfinite
numbers, out-of-session ticks and intraday cumulative-volume rollback fail.

`BarSeries(periodUs=0)` aggregates all configured sessions of one trading day;
positive periods anchor to each session opening. Sessions are half-open. Gaps
are not filled. A consecutive exact duplicate is ignored. The default first
sample is a volume baseline; `FromTradingDayStart` is the explicit alternative.
A midnight change of civil date is not implicitly a change of trading day.
`Finish(watermarkUs)` is irreversible and labels an unfinished tail rather than
inventing a completed candle. History capacity is bounded. Range endpoints are
inclusive reverse indices: zero is newest, matching the donor interface's
convention; ties default to newest and can explicitly select oldest.

## Research matching and settlement

`ReplayMatcher` is an offline last-trade crossing assumption, not a Broker.
It uses the **shared** tick-volume delta in insertion order and only ticks
strictly later than submission. Resting, FAK and FOK behaviour, partial fills,
expiry-before-fill, cancellation and stable research IDs are explicit. The
model does not claim queue position, spread, market impact, auction behavior,
exchange-level matching fidelity or execution qualification. It has no IPC or
production adapter entry point. Maximum retained orders/fills are bounded;
capacity exhaustion fails instead of silently dropping idempotency history.

`ReplayLedger` is single-instrument, futures-style cash-settled PnL with an
explicit multiplier and fee per unit. A fill ID is applied once; conflicting
reuse fails. Reversals close the old side before establishing the new basis.
It is not an equity-security cash ledger, margin engine or production portfolio.
`NetAssetTracker` uses units for explicit deposits/withdrawals, tracks NAV and
maximum drawdown, and never injects cash automatically. Zero-NAV recapitalization
and full redemption require a new accounting lifecycle, not division by zero.

## Strategy and execution boundary

`BreakoutSignal` demonstrates migration of completed-candle strategy callbacks.
It compares the current close with **previous** bars, not the current high/low,
and emits a `BoundedIntent`. It is not a full port of all old CTA/Kindle/Agent
strategies. Duplicate callbacks do not emit twice; incomplete, stale, future,
conflicting and out-of-order bars are rejected. The caller owns invocation,
clock, persistent strategy state and synchronization. Strategy timestamps used
for intent issuance must be millisecond-aligned and no older than 60 seconds.

`StrategyIntentClient::Preview` uses `risk.preview_order`. The caller must
inspect the response and durably retain the **Execution-issued** `command_id`
and matching `preview_permit` before `Submit`. Submission uses LMT/DAY only;
legacy FAK/FOK or close-today intent is NOT silently downgraded into this path.
Execution/session configuration binds the contract/account and validates intent.
A true method return means a decoded transport response, not broker acceptance;
inspect the returned status. After an uncertain response, query the retained
command ID. No new ID, token, position or approval is manufactured by the client.
Late retries of an expired signal must use `Query`, not a newly dated Submit.
`elapsedUs` includes native discovery/transport cost, not a production latency SLA.

## Build and tests

Run the existing `./scripts/dev_core.sh`: all three integration test binaries
are dependencies of `hepta_core_test_binaries` and use the `core` CTest label.
For an already configured build, `ctest --test-dir build/core -L
research-integration --output-on-failure` selects just the integration tests.
The branch workflow additionally uses GCC/Clang and ASan/UBSan and checks that
the standalone client binary defines no execution/broker authority symbols.

The Gateway test uses a real UnixToolServer, maintained NativeToolClient,
TradingToolHost, ExecutionCoordinator, OmsJournal and deterministic simulator.
Only the preview issuer/readiness policy is a test fixture; the existing core
suite continues to own full simulator/PAPER profile and permission validation.
The test checks duplicates, identity conflict, refusal, disconnected transport,
command recovery and reports fixture transport latency. It is not a host/broker
or production strategy qualification campaign.

## Packaging, migration and limits

These are source/build libraries, not new installed daemons or automatic
strategy runners. Existing release packaging and service units are unchanged.
No binary ABI compatibility or complete legacy regression parity is claimed.
See [the integration disposition](../technical/heptadll-integration.md) for
pinned provenance, excluded assets, priority and archival conditions.
