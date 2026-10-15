# Native options: Snowball market data, IB contract and execution authority

Status: SNOWBALL SOURCE REQUIRED; OFFICIAL LIVE TRANSPORT BLOCKED; NO OPTIONS ORDERS.
Deployed trading base: d384e98. Continue feat/native-0dte-20261005 / PR #165.
Scope: standard XSP and SPXW European, PM cash-settled options, long one-contract
Call/Put proposals, MKT/IOC, no naked selling, no multi-leg expansion.

## Source correction and actual inventory

The operator designates Snowball Securities as the options data provider. This
is the intended source, not evidence that a programmatic Snowball feed already
exists. The previous observation of IB 354/10090 proves only the outcome of the
IB data request. It does not prove anything about Snowball API entitlement.

The baseline 283e6a6 tracked tree contains no Snowball/Xueqiu/snbpy references.
A case-insensitive extended inspection of 25,719 other project/config/script/
document files on pocket4 found no matching transport or configuration. An
initial narrower 11,019-file scan overlaps that inspection; do not add the counts.
Scans excluded build/vendor/cache/session/history directories, large files and
symlinks. This is a scoped inventory, not a claim about every device or account.
The existing shadow env contained only the IB PAPER account.

The public Snowball SDK inspected exposes account/order/position/security-detail
operations, not a documented live option quote/Greeks subscription method. Its
production endpoint is not a license to invent a quote URL. Public App stock-
option features do not establish XSP/SPXW index-option and underlying coverage,
programmatic permission, source timestamp semantics or this account's rights.
No App cookies, private endpoints, purchased subscriptions or new agreements
were used by this delivery. No Snowball HTTP/stream transport is implemented.

## Executable components and their evidence limits

### SnowballOptionFeed: native normalized-feed admission, not a vendor SDK

`options/snowball_option_feed.{h,cpp}` accepts a trusted transport's normalized
records. It does not connect to Snowball and cannot confer trading authority.
The official transport must verify the source context before calling BeginEpoch:
provider, endpoint identity, account subject, API-use authority receipt, receipt
validity, exact XSP/SPXW product coverage and matching real-time index data.
These fields in synthetic tests are explicitly fixtures, not vendor receipts.
A JSON/config realtime=true flag cannot activate a real feed through the runner.

CanonicalOptionIdentity uses symbol, underlying type, currency, expiry, C/P,
integer strike in thousandths, multiplier, tradingClass and fixed-width OCC
symbol. The canonical HCOPT1 key contains neither a Snowball opaque symbol ID
nor an IB conId. MatchBrokerOption requires a complete Broker-qualified Contract
with an exact matching economic identity and permitted settlement/session data.
SPY, standard AM-settled SPX, a different expiry/multiplier/right/strike and proxy
underlyings cannot substitute for the requested product.

Each cache instance covers one vendor contract. BeginEpoch requires a new epoch
and session identity, clears cached data and records the source authority context.
Every frame must be a complete real-time normalized snapshot. Continuity sequence
is adapter-local and per contract; a future adapter must explicitly map the real
vendor's sequence/resync protocol, not assume its wire fields match these structs.

Each bid, ask, bidSize, askSize, index value and Greeks record carries source UTC,
receive UTC and receive monotonic time. Source time is never replaced by a poll or
heartbeat time. Epoch mismatches, missing/future/stale source or receive times,
clock/sequence regressions, conflicting duplicate frames and sequence gaps reject
readiness. Exact duplicates never renew freshness. Connection loss or entitlement
revocation must call Disconnect; recovery starts a new epoch with a fresh full
snapshot. Delayed/frozen fields cannot qualify as live. No local Greeks calculator
is implemented or silently substituted for Snowball Greeks in this delivery.

### Independent feed and broker epochs

Snapshot.epoch is the Broker account/order/position epoch. marketDataEpoch is the
separate feed epoch, with marketDataSource identifying its source. Quote and
Greeks checks compare against the feed epoch; Broker state and contract bindings
compare against the Broker epoch. An Execution/Broker restart requires a new
contract binding but never retags cached feed data or resets the independent
source epoch. A feed reconnect clears only its cache and does not alter Broker
positions, pending commands, send counts or PnL.

The normalized adapter overwrites only market data in a copied Broker snapshot.
Account/owner, orders, positions, trading permission and margin preview remain
Broker/runtime-owned. Existing Evaluate and DecisionJson still grant no order
capability; the privileged Execution preview/permit integration is incomplete.

### IB qualification without any market-data request

`hepta-options-observer --qualify-occ <exact-21-character-OCC>` uses the configured
singleton DU account and localhost PAPER gateway port 4002. It requests underlying
contract details and the exact option contract details; both must be unique and
match. No reqMktData or reqMarketDataType is issued on this route, and market-data
farm readiness is not an admission prerequisite. Connection/account identity and
secdef results remain required. No order/cancel/exercise interface is called.

Its JSON receipt includes canonical key, Broker contract key, conId, underlying
conId, Broker epoch, session dates and instrumented request counts. Input origin is
CALLER_SUPPLIED_OCC_NOT_A_LIVE_FEED. It explicitly does not prove Snowball live data
or account options trading permissions.

Actual Oct 5, 2026 New York session checks succeeded for:
- XSP   261005C00775000 -> IB 913173379, underlying 137851301.
- XSP   261005P00775000 -> IB 913174277, underlying 137851301.
- SPXW  261005C07750000 -> IB 913164396, underlying 416904.
Each performed two contract requests, zero market-data/type requests and zero
order sends. These are real Broker identity receipts, not Snowball-derived quotes.

### Shadow runner source selection

The existing `heptatrader-options-shadow.service` structure is retained. Its
explicit/default market-data source is SNOWBALL. Until the official transport is
implemented and account rights are verified, the runner atomically writes:

    BLOCKED_SNOWBALL_OFFICIAL_FEED_UNCONFIGURED
    market_data_source=SNOWBALL
    ib_market_data_required=false
    snowball_transport_connected=false
    stage1_proven=false
    broker_order_submission_enabled=false

It exits 78 before launching the IB observer. RestartPreventExitStatus=78 avoids
an endless retry of a missing configuration. A configuration-blocked unit is not
an active Snowball reader, and its enabled flag must not be reported as readiness.
There is no automatic fallback to IB. An explicit `--market-data-source
IB_DIAGNOSTIC` permits the old IB-only diagnostic capture, labeled as such; even
valid IB diagnostic records cannot pass Snowball Stage 1.

## Capture, economics and fault evidence preserved

H0D1 remains byte-compatible for legacy IB normalized observations. H0D2 adds
marketDataSource and marketDataEpoch while retaining the separate Broker epoch.
Decoder re-encoding rejects schema/value/expected-result drift and resets source
fields when reusing an output Snapshot for a legacy record. H0D2 is normalized
snapshot reproducibility, NOT a complete Snowball raw-feed/rights receipt. The
real provider recorder/replayer remains pending alongside the transport; Stage 3
must not be credited with raw-source replay based only on H0D2 unit tests.

Existing native capture files remain private/no-follow/single-writer and synced
per record. Unterminated tails are rejected. The runner verifies exactly one
completion, account check, no-order start, appropriate exit, zero sends and exact
replay counts; errors and interrupted attempts cannot become successful evidence.

EvidenceLedger keeps individual shares/prices separate from cumulative values,
checks every cumulative checkpoint, joins all positive-fill terminals/executions/
fees, rejects correction/conflicting replay, and permits identical execution
replay after settlement. Local flatness is FLAT_BROKER_AUDIT_REQUIRED. A true
RoundTripProven requires a complete exact PAPER account/owner/contract snapshot,
nonzero Broker epoch, no active order/unresolved send, flat position and complete
economic joins. Disappearance at expiry is not a sell. Authoritative cash
settlement is separately identified and never a traded round-trip proof.

The previously added shared cumulative-execution aggregation repair remains in
this candidate, including original order-159 offline tests. It is NOT deployed to
shared Execution. Original pending command recovery must precede any such rollout;
no driver JSON is hand-edited or pending order cleared by the source migration.

## Risk, calendar and resource limits

Options premium and daily-loss budgets remain ZERO/UNSET, independent of FX/BTC
sizes. MKT ask-based estimated cost is not a guaranteed execution price cap.
Entry cutoff is 60 minutes and planned exit 30 minutes before the qualified last
trade time. Actual half-day schedules apply. New York exchange date, not host
midnight, determines expiry. Current civil-time logic supports post-2007 US DST
for 2007-2099; unknown/invalid/ambiguous local times fail closed.

Same-contract reduce-only still requires actual authoritative quantity and state;
no new entry budget or fresh model opinion is required. Missing usable market or
expiry does not erase a position; recovery/settlement evidence is still required.

The isolated OptionsShadow release/current link never replaces the trading
release/current link. State cap 2 GiB, minimum filesystem free reserve 4 GiB,
per-attempt reserve 32 MiB, per-file limit 16 MiB and invocation limit 480 seconds
remain. No previous evidence is automatically deleted. Storage exhaustion is a
separate blocked state, never proof of data readiness.

## Corrected five-stage ledger

| Stage | Required acceptance | Current result |
|---|---|---|
| 1: Snowball data/rights | Verified programmatic rights, exact product/index coverage, live bid/ask/sizes/index/Greeks with source+receive TTL across multiple real trading windows; immediate stale/disconnect rejection | BLOCKED: no verified official transport/configuration/entitlement; zero real Snowball frames |
| 2: native option chain | Snowball normalized identity -> canonical key -> actual IB qualification; privileged native risk/permit/economic callback path | Native admission/mapping implemented; real quote-free IB qualification verified; transport and privileged Execution integration unfinished |
| 3: Snowball shadow | Real source capture, source-aware reproducible signal/cost/cutoff/exit decisions; IB used for contract/account checks | Default switched and no-fallback block deployed; normalized replay primitives tested; no actual Snowball shadow run |
| 4: one-contract PAPER | Snowball live signal -> native IB MKT -> original command execution/fees -> exact reduce-only exit and full proof | NOT ATTEMPTED; OPT remains rejected by current production Execution |
| 5: resident/restarts | Feed reconnect and driver/Execution restarts with independent epochs, cross-date cutoff and invariant three-line economics/identity/send counters | Synthetic dual-epoch/disconnect tests and real contract-only process checks; no real Snowball reconnect or three-line trading restart acceptance |

The previous 240-record IB diagnostic and its shadow-restart result are retained
as historical IB diagnostics only, not as Snowball Stage 1/3/5 passes. No positive
strategy performance or live profitability has been established.

## Build and validation

Normal top-level CMake with pinned IB 10.30.01; no dependency change. New native
target: hepta_snowball_option_feed_tests. Observer still installs via the isolated
OptionsShadow component. Final local CTest: 55/55; runner target: 21 Python cases
(included in CTest, not added as 21 more targets); separate FX/BTC driver: 43/43.
The new feed tests are synthetic fixtures, not real provider observations. Hosted
CI must be reported from its actual run, not inferred from these local checks.

## External handoff needed before real feed implementation

Obtain the official authorized quote API/stream documentation and sample payloads
for this account: endpoint/protocol and authentication, separate market-data API
rights, XSP/SPXW and matching index coverage, full canonical identity, bid/ask and
sizes, source timestamps/clock units, real-time/delayed flags, sequence/snapshot/
reconnect behavior and Greeks provenance. A trading-only snbpy key or an App
screenshot is not sufficient. Do not put secrets into Git or the report.

Public primary references checked for this continuation:

```text
https://github.com/snowballsecurities/snbpy
https://raw.githubusercontent.com/snowballsecurities/snbpy/master/src/snbpy/snb_api_client.py
https://broker.xueqiu.com/activity/level/setting?action=us_lv1
https://cn.snowball-x.com/trade
```

## 2026-10-06 continuation: actual Broker economics and runtime incident

### Native execution/commission receipt capture (source candidate only)

The pinned IB C++ SDK callback path now retains individual Execution.shares and
Execution.price separately from cumQty and avgPrice. Legacy broker_execution
qty/price semantics remain cumulative so existing FX/BTC readers are not silently
changed. An additive, all-or-nothing broker_econ_* receipt retains the exact
contract fingerprint/conId, permanent order ID and pending-price-revision flag.
Version zero means absent evidence, not a zero fee or an inferred individual fill.

The actual commissionReport callback now enters the same Execution event queue
and the same critical/fsynced OMS. Its only economic join identity from the SDK
is execId; it supplies no account, numeric order ID, API client ID or orderRef.
The runtime deliberately never assigns it a numeric-order owner. Account, order,
command and correlation remain unbound until a verified execution joins them.
Currency/amount and known/unknown realized-PnL status are retained separately.
Nonfinite SDK sentinels are explicitly marked unusable, never usable zero values.

The grouped extension rejects missing fields, unknown extension fields, wrong
types, inconsistent flags, malformed valid economics and partially populated
absent envelopes. Old records remain byte-identical when the extension is absent.
Callback tests prove fee-before-execution durable ordering and native OMS reopen
and replay, NOT a complete option ledger or Broker round trip. The economic
replay bridge into the option EvidenceLedger was not delivered in this turn;
one write action for that component was blocked by the tool safety check and was
not retried through another route. OPT permission, fees-to-owner reconciliation,
settlement integration and live Snowball capture therefore remain incomplete.

### Official Snowball MCP discovery

The official AI page now documents https://openapi.snowball-x.com/mcp for order,
account and portfolio access. Its public feature list is not a proven realtime
XSP/SPXW quote/Greeks protocol. An unauthenticated initialize request from pocket4
returned HTTP 403. No credentials or tools/call were sent, no order tool was used,
and no VPN or alternate-network bypass was attempted. Zero real Snowball frames
or entitlement proofs were obtained; shadow remains explicitly blocked.

### Shared Execution incident found before this continuation changed anything

Initial observation found the existing Execution in a restart loop (252 recorded
restarts), rejecting IB_PAPER_QUOTE_ADMISSION_CALLBACK_UNSAFE. Actual Broker error
2172 was a global advance notice that Gateway 1045.1 would require upgrading to at
least 1050.1 from 2026-12-15. The generic unknown-error latch treated it as fatal.

The scoped fix preserves and journals that notice but excludes ONLY the global,
unscoped 2172 from the sender-admission latch and error fuse. Order/account-scoped
2172, advanced order rejects, unknown errors, data entitlement errors, connection
loss and event overflow retain their prior rejection semantics.

A minimal backport was built from the exact deployed d384e98 base, not from this
options candidate. Its separate 49-test suite passed. It was installed through an
Execution-only ExecStart drop-in, without changing the main release symlink or
including the unqualified options/aggregation/commission source changes.

With new-entry protection engaged and both FX/BTC drivers paused, the deployed
2172 patch removed the first startup blocker. It exposed a second, real blocker:
IB_FX_CASH_ATTESTED_BALANCE_MISMATCH. Independent account-scoped Broker reads
completed account, positions, orders and current executions. The EUR cash balance
is 3432.58 EUR below the persisted attestation; the cause is UNVERIFIED until an
actual cash-transaction/statement receipt explains it. No interest/fee/deposit
cause is assumed and no attestation or baseline is rebased automatically.
The Broker also reports 0.03313821 BTC held and zero active orders. Driver BTC=0
is not an authoritative flatness witness. The original pending order 159 and its
command remain preserved. An empty current execution download is not zero-fill
proof and does not undo historical executions.

This is a diagnosed, protected but UNRECOVERED trading runtime, not completed
three-line restart acceptance. The paper/options canary must not run while this
money mismatch or source/permission/budget prerequisites remain unresolved.

### Validation and evidence boundary

Current candidate native build: 57/57 CTest; separate FX/BTC Python driver 43/43.
The isolated minimal 2172 backport: 49/49 CTest. These are local tests, not hosted
CI acceptance. Raw private source probes, JUnit, service logs, before-state copies
and the independent Broker audit are retained under operator-evidence/
options-native-economics-20261005T235055Z on pocket4. No real options order was
submitted. Existing OMS prefix and original driver pending identities are retained.

## 2026-10-06 continuation: offline economic audit and complete normalized trace

The newly implemented tools below remain read-only/offline qualifications. They
are NOT the previously undelivered privileged EvidenceLedger/runtime bridge,
are not connected to order permits, and are not a second authoritative OMS.
No shared Execution binary, service config, pending command or cash attestation
was changed by this continuation. FX/BTC drivers were already paused and remain
paused. Snowball source remains configuration-blocked; no live frame was obtained.

### Native OMS economics audit

`hepta-options-oms-audit` reads an immutable private copy using O_RDONLY and the
existing strict native OmsJournal::ParseJsonLine. It never calls OmsJournal::Init
or Append on the input. It rejects unsafe ownership/modes, links, changed inputs,
malformed records, oversize input and truncated tails; successful output binds an
input SHA-256. Input size is bounded to 64 MiB / 65,536 records. Terminal receipts
are indexed by correlation rather than scanned quadratically per command.

The filter names an exact PAPER account, agent/session/client, canonical OCC and
conId. It is explicitly CALLER_SUPPLIED_FILTER_ONLY, NOT fresh Broker qualification.
Native send witnesses use the coordinator's actual agent.tool: source prefix;
bound callback receipts use agent: or the documented unbound callback source.
Cash amounts are joined through original command/requestHash/correlation and
account/client/full contract fingerprint, never numeric order ID alone.

Every individual fill and cumulative checkpoint must agree. Missing legacy
individual evidence, source/owner mismatch, overfill, conflicting execution-ID
replay, correction families, pending price revision, changed permanent order ID,
unexplained repeated sends and missing/conflicting terminals make amounts unknown.
Raw commissions join only through globally unambiguous execution IDs; fee-before-
execution and exact later replay are accepted without double charging. Currency
mismatch, missing/invalid fees and SDK sentinels never become zero commission.
Globally unassigned commission IDs are reported separately, not assigned to an
arbitrary order or account.

Important callback boundary: the current native completedOrder projection stores
Order.totalQuantity, NOT the executed quantity. The audit explicitly ignores that
quantity. A 2-contract order with 1 fill and cancellation requires a matching
orderStatus cumulative fill receipt; it cannot become a 2-contract execution from
the completed-order total. The multiple-contract fixture is synthetic only and
does not increase the one-contract PAPER canary limit.

The result names execution cash flow and reported net contracts. It does NOT
call a purchase cash outflow a realized trading loss. Realized PnL stays null,
Broker position verification and roundtrip_proven stay false. Missing evidence
also leaves net cash flow null; a zero-execution query is never zero-fill proof.
A completed report is not permission to release cash/source/owner fences.

The real copied 13,695,961-byte OMS contained 14,852 records, 2,183 broker_execution
records, zero broker_commission records and zero IBOPT native sends. Running the
actual CLI against the explicit option filter produced NO_MATCHING_NATIVE_SEND
and unknown economics, not a fake empty-account or profitable-round-trip result.
This is a prefix audit, not a claim that unseen history or Broker activity is zero.

### Complete normalized Snowball event trace

`SnowballFeedTrace` / `SnowballTraceWriter` add HSFT1, an INTERNAL normalized trace,
not a guessed Snowball wire protocol. Unlike a single H0D2 evaluated snapshot,
the trace contains BEGIN (rights context/session/epoch), FRAME (full identity,
quotes, sizes, Greeks and all six independent source/receive/monotonic clocks),
DISCONNECT, EVALUATE (policy, Broker binding/state, evaluation clock and reduce-
only flag), and END. Failed operations are recorded with their refusal results.
Every evaluation retains the deterministic action, reasons, estimated cost and
entry/exit deadlines. This is gate/decision replay, not a newly invented strategy
or an executed hypothetical exit price. The provider's raw payload transport and
actual live shadow integration still need the official documented access.

Records have a strict version, trace ID and contiguous sequence. Replay recomputes
actual SnowballOptionFeed outcomes and rejects schema/value/decision drift, gaps,
mixed traces and records after END. Replay failures do not partially advance the
replay state. Encoding is independent of locale and preserves full double precision.
These records are reproducibility evidence, not signed vendor-authenticity proof;
matching replay always reports live_source_proven=false and API entitlement=false.

The exclusive new-file writer is owner-private/no-follow, fsyncs the directory
entry and each complete record, detects path replacement, and poisons itself after
write/sync failure. The caller serializes the callback stream on one owner thread.
File size is bounded to 16 MiB, record size 64 KiB and count 16,384. A crash without
END remains incomplete even when all preceding records end with newlines. Neither
writer nor reader silently truncates or repairs old evidence.

A separate process CLI, hepta-snowball-trace-replay, successfully replayed a
clearly labeled synthetic 12-record / 6-evaluation trace. Tests cover fresh data,
source-clock expiry, missing API-use authority, each per-field clock, disconnect,
new feed epoch, stale Broker qualification, requalification without feed retagging,
New York date rollover, conflicting outcomes, duplicate/gapped frames, process
crash, missing END, truncated final newline, unsafe files and locale changes.
No synthetic accepted evaluation is counted as a real Snowball window.

### Current money protection independently rechecked

At 2026-10-06T01:13:56Z an independent exact-account reqAccountUpdatesMulti /
reqPositionsMulti audit completed with matching account and request IDs and
AccountReady=true. These separate subscriptions do not take over Execution's
legacy account stream. An earlier group-All account summary was explicitly NOT
accepted as an exact-account cash authority and was replaced by this scoped read.

The persisted HFXR1 proof hash and baseline-plus-owned-quantity arithmetic are
valid: baseline EUR -1,274,877.77 plus campaign quantity 25,000 equals attested
cash EUR -1,249,877.77. The scoped Broker cash is EUR -1,253,310.35, leaving an
unexplained EUR -3,432.58 difference. The cash-transaction cause is not verified;
no fee/interest/cash-transfer explanation is assumed and no baseline is rebased.
Broker BTC position remains 0.03313821; open orders are zero. The empty recent
execution download cannot erase this holding or the original pending command.

Execution is process-alive but still reports risk/account/position snapshots
incomplete with IB_FX_CASH_BALANCE_OUT_OF_GENERATION_CHANGE. The protected runtime
has NOT recovered. A new option canary or three-line restart is not qualified.

### 2026-10-15 R7: read-only observation is not PAPER authorization

The `heptatrader.ib-runtime-observation.v1` serializer deliberately publishes
`authorization_effect=NONE`, `paper_authorized=false`, and
`live_authorized=false` for **every telemetry event**. These literals mean
the *observation has no authorization effect*. They are **not** a broker
permission flag, are **not** the owner-scoped `mutation_ready` health value,
and cannot prove that live PAPER orders were disabled.

R7 read-only OMS evidence recorded five EUR/USD entry sends, five
flatten sends and ten broker execution callbacks after 09:20 local device
time by the later 10:26 local checkpoint, while the runtime-observation
record still carried those fixed false fields. An independent `recovery_barrier_complete=false` refers to the
recovery-owner audit path; do not generalize it into a claim of zero normal
PAPER traffic. To assess the actual current trading state, correlate the
Execution service epoch with real OMS sends/fills, original command state,
owner-scoped mutation health, broker positions and open orders.

The separate callback diagnosis `risk.fx_cash_monitor_invalidated` records
the subscription request ID, connection and risk generations and whether
a committed checkpoint was present **without serializing balance values**.
It is a diagnostic of why a committed risk generation becomes stale. The
original fail-closed invalidation, new-epoch recovery rules and required
reconciliation remain unchanged; R7's native test confirms a risk-increasing
broker send is refused after a cash generation invalidation.

### Validation and installation boundaries

Rebuilt local CTest: 59/59, with the two new native audit/trace suites; separately
run FX/BTC driver suite: 43/43. The trace suite plus all its source/evaluator
implementation was additionally compiled with AddressSanitizer and
UndefinedBehaviorSanitizer and passed with leak detection enabled. These are
local synthetic/source tests, not hosted CI or real provider acceptance.

OptionsAudit is an explicitly installed optional CMake component containing only
the offline CLI tools; it never replaces the trading or shadow current links.
Examples use historical filters on a private copied journal, not permissions:

```sh
hepta-options-oms-audit --journal /private/copied-oms.jsonl \
  --account DU_EXPLICIT --agent OWNER --session SESSION --client-id 701 \
  --con-id 913173379 --occ 'XSP   261005C00775000'
hepta-snowball-trace-replay --trace /private/normalized-trace.hsft1
```

Current five-stage ceiling: (1) zero real Snowball frames, official source/rights
still missing; (2) callback capture plus offline economic audit implemented but
privileged permits/settlement/runtime ledger still incomplete; (3) full normalized
trace implemented, no live capture or strategy/exit-result validation; (4) no
option PAPER orders; (5) synthetic recovery checks, no real Snowball reconnect or
three-line trading restart acceptance. Cash protection additionally blocks new
trading work. The official Snowball MCP page describes orders/assets functions;
that alone does not verify the required option/index quote and Greeks service.
