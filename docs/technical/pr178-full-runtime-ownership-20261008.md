# PR #178: full runtime recovery and source-bound ownership evidence

This is the next step on the existing PR #178 branch. It does not deploy a
candidate, change a production owner, freeze a strategy, replace quote sources,
reset an accounting baseline, or place/cancel an order. Exact candidate SHA and
source/SDK/binary/receipt hashes belong to the execution manifests, not to a
self-referential test-result commit.

## Full runtime regression, not a quote-only bypass

[The native fixture](../../tests/ib_full_runtime_recovery_tests.cpp) calls the
actual `IbPaperExecutionRuntimeComposition::Start`, including private state,
authorization verification, journal recovery, coordinator, Broker snapshot
barriers, policy construction, real local Unix IPC and the actual polling
thread. It uses mixed BTC-primary/EUR-secondary `observationOnly=false`.
The production `BeginBrokerReconnect`, `DriveBrokerReconnect` and post-refresh
risk/order/quote/ownership conjunction execute. The fixture does not assign
ready=true or manually replace those coordinator methods.

The injected Broker has **no network transport**. Before constructing a
runtime, the fixture applies Linux seccomp with no-new-privileges: only
AF_UNIX socket creation is permitted; IPv4/IPv6 sockets and exec replacement
are denied. Kernel refusal is tested explicitly. Every order/cancel method
throws and counts the attempt; the injected kill switch is engaged. State,
credentials, original synthetic orders and IPC endpoints live in a new private
fixture directory. No production credential or journal is read by this test.
The canonical control-directory name is only a validated profile field; the
fixture supplies its own in-memory kill-switch reader and never opens the
real control plane.

Eight scenarios cover:

1. Full startup and 1100 -> 1101 reconnect with a new adapter epoch and all
   authoritative snapshot barriers rebuilt.
2. Full reconnect receiving 1102 maintained-data control. This tests the full
   reconnect phase; it does not falsely claim the upstream 1100 required no
   transport rebuild. The existing separate component tests cover 1102 alone
   without a new subscription or re-stamping.
3. Seeded original FX/BTC commands, nonzero positions and full reconnect with
   unchanged owner/session/command/order identity.
4. Stop, destruction and a new runtime using the SAME journal/fence/baseline
   and new IPC descriptors. Service epoch changes; original commands, Broker
   permanent IDs, execution IDs and nonzero positions survive. Intent/send
   counts do not increase. This is runtime reconstruction in the test process,
   not an assertion that an OS crash or a live Broker restart was exercised.
5. Missing positions End causes the real reconnect barrier to remain closed;
   no completed reconnect or order mutation is published.
6. Secondary CASH farm loss/restoration traverses the real poll loop. EUR ages
   past the original 5000ms bound; BTC remains usable and its ID is unchanged.
   Recovery requires two newer EUR leg callbacks, not the 2104 notification alone.
7. An active Broker order at reconnect keeps the runtime fail-closed. The test
   does not cancel or replace that order to obtain a passing result.
8. A durable original unknown send remains Uncertain across full runtime
   reconstruction when Broker history does not resolve it. No replacement
   intent or second send attempt is created.

All Broker controls, balances, orders, executions and fills in this fixture are
**synthetic**. Its receipt explicitly says `live_recovery_qualified=false`.
It demonstrates actual native code-path behavior, not live feed-outage proof.

## Independent authenticated PAPER read

[The Broker reader](../../scripts/hepta_broker_recovery_snapshot.py) opens one
isolated nonzero API client. Its exact outbound SDK-frame allowlist permits
StartApi, unbound all-open-orders read, API completed-order read, two exact
account/owner/instrument-filtered execution reads, positions-multi and paired
ledger/aggregate account reads, plus cancellation of only its own data requests.
It forbids market data, order submission/cancellation, client-zero binding,
auto-open-order binding and unknown/foreign messages.

Seven End groups, raw callbacks, late callbacks, request identities, read-only
wire counts, SDK/source/config hashes and immutable before/after journal bytes
are retained in a private, exclusive output directory. The period must not
change. A moving journal cannot serve as a stationary ownership cohort.
When available, the existing native FX cash checkpoint is also captured before
and after without modifying it. Neither an End nor an unchanged journal is a
common Broker valuation-time certificate.

API execution/completed-order visibility is bounded. A successful download
is not exhaustive historical coverage. Missing old executions remain UNKNOWN,
not zero fills or proof that a pending command may be resent.

## Original-command / permanent-order / execution join

[The offline verifier](../../scripts/hepta_recovery_identity_chain.py) requires
pinned raw bytes, the corresponding journal hash and exact period hash. It
checks registered account, agent, session, Broker client, instrument/contract,
canonical orderRef correlation, original intent+send request hash, temporary
order ID, permanent Broker order ID, execution ID, shares, price and cumulative
quantity. Duplicate sends, conflicting executions or ownership drift fail.

Real completedOrder callbacks may contain temporary orderId=0/clientId=0.
These absences do not replace the execution's original client/order identity:
completed evidence joins only through exact account, permanent ID and orderRef.
Native Broker callbacks use typed economic version 1 and integer valid=1;
boolean or other truthy values are not accepted as the native wire schema.
A callback's empty request_hash is not filled with a guessed value: the hash
is proven by a unique original intent+send pair and the absence is disclosed.

The complete positions read is separate from account currency cash. A virtual
CASH position is never added to EUR cash or labelled as a strategy position.
Checkpoint digest/algebra can be compared with independently observed currency
cash, but this comparison alone does not authenticate the original baseline
or qualify candidate restart position continuity.

## Qualification ceiling and remaining work

These two evidence lanes are complementary, not interchangeable: a full native
synthetic lifecycle cannot certify the live Broker; a live read of production
orders cannot certify a candidate that was not installed. Both remain bound to
their original source/period/window and retain negative outcomes.

Before any controlled PAPER cutover, obtain an exclusive safe owner window,
freeze new business intents through the existing mechanisms, preserve binary /
configuration / journal / checkpoint rollback, and verify the full candidate
with exact permanent-order/execution and FX/BTC ownership continuity. Unknown
results require reconciliation, never another command or flatten/reopen
workaround. Existing active-order/post-fill safety gates are not weakened.

NAV remains separate: source-backed arithmetic and transport completion do not
prove a common account valuation basis. No new source in this change certifies
currency/BASE/accruals at one Broker time. The existing strict NAV assurance
candidate remains fail-closed and has not been deployed.
