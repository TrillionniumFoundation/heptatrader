#include "../HeptaTrade/state/authoritative_trading_snapshot_store.h"
#include "../HeptaTrade/state/ib_contract_identity.h"
#include "../HeptaTrade/state/ib_authoritative_quote_subscription_set.h"

#include <algorithm>
#include <atomic>
#include <cassert>
#include <cmath>
#include <iostream>
#include <limits>
#include <mutex>
#include <thread>
#include <vector>

namespace {

AuthoritativeQuote Quote(const std::string& instrument, double bid)
{
    AuthoritativeQuote quote;
    quote.instrument = instrument;
    quote.bid = bid;
    quote.ask = bid + 0.0002;
    quote.last = bid + 0.0001;
    quote.bidSize = bid * 10.0;
    quote.askSize = bid * 20.0;
    return quote;
}

AuthoritativeAccount Account(const std::string& account, double netLiquidation)
{
    AuthoritativeAccount value;
    value.account = account;
    value.currency = "USD";
    value.hasNetLiquidation = true;
    value.netLiquidation = netLiquidation;
    value.hasAvailableFunds = true;
    value.availableFunds = netLiquidation / 2.0;
    return value;
}

AuthoritativePosition Position(const std::string& instrument, double quantity)
{
    AuthoritativePosition position;
    position.account = "DU123";
    position.instrument = instrument;
    position.quantity = quantity;
    position.averageCost = 1.1;
    return position;
}

AuthoritativeActiveOrder ActiveOrder(long orderId)
{
    AuthoritativeActiveOrder order;
    order.venue = "SIM";
    order.orderId = orderId;
    order.account = "DU123";
    order.instrument = "EUR.USD";
    order.side = AuthoritativeOrderSide::Buy;
    order.type = AuthoritativeOrderType::Limit;
    order.status = AuthoritativeActiveOrderStatus::Submitted;
    order.totalQuantity = 100.0;
    order.filledQuantity = 25.0;
    order.remainingQuantity = 75.0;
    order.limitPrice = 1.1;
    return order;
}

void TestCryptoIdentityAndIndependentClosedFxQuotes()
{
    IBContractLite btc;btc.symbol="BTC";btc.secType="CRYPTO";btc.exchange="PAXOS";btc.currency="USD";
    IBContractLite fx;fx.symbol="EUR";fx.secType="CASH";fx.exchange="IDEALPRO";fx.currency="USD";
    assert(BuildIBAuthoritativeInstrumentIdentity(btc)=="BTC.USD");
    auto other=btc;other.exchange="ZEROHASH";
    assert(BuildIBAuthoritativeInstrumentIdentity(other)!="BTC.USD");
    other.exchange.clear();assert(BuildIBAuthoritativeInstrumentIdentity(other).empty());
    std::map<std::string,IBContractLite> contracts{{"BTC.USD",btc},{"EUR.USD",fx}};
    AuthoritativeTradingSnapshotStore store;
    IBAuthoritativeQuoteSubscriptionSet quotes(store);
    std::string reason;assert(quotes.Configure(contracts,"BTC.USD",reason,false,true));
    auto plan=quotes.BeginCycle(1,1,1000);assert(plan.accepted && plan.subscriptions.size()==2);
    int bid=0,fid=0;
    for(const auto& item:plan.subscriptions) {
        assert(quotes.RecordDispatchResult(1,item.requestId,true));
        if(item.instrument=="BTC.USD")bid=item.requestId;else fid=item.requestId;
    }
    auto tick=[&](int id,const char* field,double price,std::uint64_t at,std::uint64_t epoch=1) {
        IBEvent event;event.type=IBEventType::TickPrice;event.id=id;event.key=field;
        event.number=price;event.connectionEpoch=epoch;return quotes.ConsumeTick(event,at);
    };
    assert(!quotes.GetHealth().ReadyForOperations());
    tick(fid,"1",-1,1001);tick(fid,"2",-1,1002);
    tick(bid,"1",85000,1003);assert(!quotes.GetHealth().ReadyForOperations());
    assert(tick(bid,"2",85000.25,1004).write.accepted);
    assert(!quotes.IsComplete()); // Do not falsely report complete FX coverage.
    assert(quotes.GetHealth().ReadyForOperations());
    assert(store.GetQuote("BTC.USD",1004,100).state.availability==AuthoritativeSnapshotAvailability::Fresh);
    assert(store.GetQuote("EUR.USD",1004,100).state.availability==AuthoritativeSnapshotAvailability::Missing);
    assert(store.GetQuote("BTC.USD",2000,100).state.availability==AuthoritativeSnapshotAvailability::Stale);
    assert(tick(bid,"66",90000,1005).status==IBAuthoritativeQuoteConsumeStatus::Ignored);
    assert(tick(bid,"1",90000,1006,2).status==IBAuthoritativeQuoteConsumeStatus::Ignored);
    assert(quotes.GetQuote("BTC.USD").bid==85000);
    tick(fid,"1",1.1,1007);tick(fid,"2",1.1001,1008);assert(quotes.IsComplete());
    auto restarted=quotes.BeginCycle(2,2,1100);assert(restarted.accepted);
    assert(!quotes.GetHealth().ReadyForOperations());
    assert(tick(bid,"1",85001,1101).status==IBAuthoritativeQuoteConsumeStatus::Ignored);
    AuthoritativeTradingSnapshotStore partialStore;
    IBAuthoritativeQuoteSubscriptionSet partial(partialStore);
    assert(partial.Configure(contracts,"BTC.USD",reason,false,true));
    auto partialPlan=partial.BeginCycle(4,1,1200);assert(partialPlan.accepted);
    int partialBtc=0,partialFx=0;
    for(const auto& item:partialPlan.subscriptions) {
        if(item.instrument=="BTC.USD") {
            partialBtc=item.requestId;
            assert(partial.RecordDispatchResult(1,item.requestId,true));
        } else {
            partialFx=item.requestId;
            assert(partial.RecordDispatchResult(1,item.requestId,false));
        }
    }
    assert(partialBtc>0&&partialFx>0&&!partial.GetHealth().ReadyForOperations());
    IBEvent pb;pb.type=IBEventType::TickPrice;pb.id=partialBtc;pb.key="1";pb.number=85100;pb.connectionEpoch=4;
    IBEvent pa=pb;pa.key="2";pa.number=85100.25;
    partial.ConsumeTick(pb,1201);partial.ConsumeTick(pa,1202);
    assert(partial.GetHealth().ReadyForOperations());
    assert(partialStore.GetQuote("BTC.USD",1202,100).state.availability==
        AuthoritativeSnapshotAvailability::Fresh);
    assert(partialStore.GetQuote("EUR.USD",1202,100).state.availability==
        AuthoritativeSnapshotAvailability::Missing);

    AuthoritativeTradingSnapshotStore strictStore;IBAuthoritativeQuoteSubscriptionSet strict(strictStore);
    assert(strict.Configure(contracts,"BTC.USD",reason));
    assert(!strict.GetHealth().independentQuotes && !strict.GetHealth().ReadyForOperations());
    contracts["BTC.USD"]=other;assert(!strict.Configure(contracts,"BTC.USD",reason));
}

void TestStaleSecondaryCashRefreshKeepsPrimaryAndCannotForgeQuote()
{
    IBContractLite btc;btc.symbol="BTC";btc.secType="CRYPTO";btc.exchange="PAXOS";btc.currency="USD";
    IBContractLite eur;eur.symbol="EUR";eur.secType="CASH";eur.exchange="IDEALPRO";eur.currency="USD";
    AuthoritativeTradingSnapshotStore store;
    IBAuthoritativeQuoteSubscriptionSet set(store);
    std::string reason;
    assert(set.Configure({{"BTC.USD",btc},{"EUR.USD",eur}},"BTC.USD",reason,false,true));
    const auto initial=set.BeginCycle(17,1,1000);
    assert(initial.accepted && initial.subscriptions.size()==2);
    int btcId=0, eurId=0;
    for(const auto& item:initial.subscriptions) {
        assert(set.RecordDispatchResult(1,item.requestId,true));
        if(item.instrument=="BTC.USD") btcId=item.requestId;
        else eurId=item.requestId;
    }
    auto tick=[&](int id,const char* field,double value,std::uint64_t at,std::uint64_t epoch=17) {
        IBEvent event;event.type=IBEventType::TickPrice;event.id=id;
        event.key=field;event.number=value;event.connectionEpoch=epoch;
        return set.ConsumeTick(event,at);
    };
    tick(btcId,"1",85000,1001);tick(btcId,"2",85001,1002);
    tick(eurId,"1",1.12,1003);tick(eurId,"2",1.121,1004);
    assert(set.BeginStaleSecondaryCashRefresh("EUR.USD",17,1,7000,5000,15000).accepted==false);
    assert(set.BeginStaleSecondaryCashRefresh("BTC.USD",17,1,22000,5000,15000).accepted==false);
    assert(set.BeginStaleSecondaryCashRefresh("EUR.USD",18,1,22000,5000,15000).accepted==false);
    tick(btcId,"1",85100,20000);tick(btcId,"2",85101,20001);
    const auto refresh=set.BeginStaleSecondaryCashRefresh("EUR.USD",17,1,22000,5000,15000);
    assert(refresh.accepted && refresh.cancelRequestIds.size()==1);
    assert(refresh.cancelRequestIds[0]==eurId && refresh.subscriptions.size()==1);
    const int newEurId=refresh.subscriptions[0].requestId;
    assert(newEurId!=eurId && newEurId!=btcId);
    assert(set.GetHealth().contracts.at("BTC.USD").requestId==btcId);
    assert(set.GetHealth().contracts.at("EUR.USD").requestId==newEurId);
    assert(tick(eurId,"1",1.130,22001).status==IBAuthoritativeQuoteConsumeStatus::Ignored);
    assert(!set.GetQuote("EUR.USD").HasQuote());
    assert(!set.BeginStaleSecondaryCashRefresh("EUR.USD",17,1,22002,5000,15000).accepted);
    assert(set.RecordDispatchResult(1,newEurId,true));
    tick(newEurId,"1",1.122,22003);
    assert(!set.GetQuote("EUR.USD").HasQuote());
    assert(tick(newEurId,"2",1.123,22004).write.accepted);
    assert(set.GetQuote("EUR.USD").HasQuote());
    assert(set.GetHealth().contracts.at("BTC.USD").requestId==btcId);
    assert(set.GetHealth().ReadyForOperations());
    // Another stale interval must renew the same secondary instrument again,
    // without ever reusing a previous request ID or borrowing BTC's BID/ASK.
    const auto again=set.BeginStaleSecondaryCashRefresh("EUR.USD",17,1,41000,5000,15000);
    assert(again.accepted && again.cancelRequestIds.size()==1);
    assert(again.cancelRequestIds[0]==newEurId);
    assert(again.subscriptions.size()==1);
    const int thirdEurId=again.subscriptions[0].requestId;
    assert(thirdEurId!=newEurId && thirdEurId!=eurId);
    assert(set.GetHealth().contracts.at("BTC.USD").requestId==btcId);
    assert(tick(newEurId,"1",1.2,41001).status==IBAuthoritativeQuoteConsumeStatus::Ignored);
    assert(set.RecordDispatchResult(1,thirdEurId,true));
    tick(thirdEurId,"1",1.125,41002);
    assert(!set.GetQuote("EUR.USD").HasQuote());
    tick(thirdEurId,"2",1.126,41003);
    assert(set.GetQuote("EUR.USD").HasQuote());
    // Full broker reconnect: all old-epoch BID/ASK and request IDs are
    // untrusted, and both instruments need fresh callbacks.
    const auto reconnect=set.BeginCycle(18,2,50000);
    assert(reconnect.accepted && reconnect.subscriptions.size()==2);
    assert(tick(thirdEurId,"1",1.25,50001,17).status==IBAuthoritativeQuoteConsumeStatus::Ignored);
    assert(tick(btcId,"2",86000,50002,17).status==IBAuthoritativeQuoteConsumeStatus::Ignored);
    int nextBtcId=0,nextEurId=0;
    for(const auto& item:reconnect.subscriptions) {
        assert(set.RecordDispatchResult(2,item.requestId,true));
        if(item.instrument=="BTC.USD") nextBtcId=item.requestId;
        else nextEurId=item.requestId;
    }
    assert(nextEurId!=thirdEurId && nextBtcId!=btcId);
    assert(!set.GetHealth().ReadyForOperations());
    tick(nextBtcId,"1",85900,50003,18);
    assert(!set.GetHealth().ReadyForOperations());
    tick(nextBtcId,"2",85901,50004,18);
    assert(set.GetHealth().ReadyForOperations());
    assert(!set.GetQuote("EUR.USD").HasQuote());
    tick(nextEurId,"1",1.127,50005,18);
    assert(!set.GetQuote("EUR.USD").HasQuote());
    tick(nextEurId,"2",1.128,50006,18);
    assert(set.GetQuote("EUR.USD").HasQuote());
    assert(set.GetHealth().connectionEpoch==18);
}


void TestSecondaryCashFarmDeferralDoesNotReplayAnUnsentRequest()
{
    IBContractLite btc;btc.symbol="BTC";btc.secType="CRYPTO";
    btc.exchange="PAXOS";btc.currency="USD";
    IBContractLite eur;eur.symbol="EUR";eur.secType="CASH";
    eur.exchange="IDEALPRO";eur.currency="USD";
    AuthoritativeTradingSnapshotStore store;
    IBAuthoritativeQuoteSubscriptionSet quotes(store);
    std::string reason;
    assert(quotes.Configure({{"BTC.USD",btc},{"EUR.USD",eur}},
                            "BTC.USD",reason,false,true));
    const auto initial=quotes.BeginCycle(17,1,1000);
    assert(initial.accepted && initial.subscriptions.size()==2);
    int btcId=0, eurId=0;
    for(const auto& sub:initial.subscriptions) {
        assert(quotes.RecordDispatchResult(1,sub.requestId,true));
        if(sub.instrument=="BTC.USD") btcId=sub.requestId;
        if(sub.instrument=="EUR.USD") eurId=sub.requestId;
    }
    assert(btcId>0 && eurId>0);
    auto tick=[&](int id,const char* field,double price,
                  std::uint64_t when,std::uint64_t epoch=17) {
        IBEvent e;e.type=IBEventType::TickPrice;e.id=id;e.key=field;
        e.number=price;e.connectionEpoch=epoch;
        return quotes.ConsumeTick(e,when);
    };
    tick(btcId,"1",85000,1001);tick(btcId,"2",85001,1002);
    assert(quotes.GetHealth().ReadyForOperations());
    // The initial EUR ticker was sent, but its CASH farm subsequently
    // disappeared before BID/ASK arrived.
    const auto first=quotes.BeginStaleSecondaryCashRefresh(
        "EUR.USD",17,1,17000,5000,15000);
    assert(first.accepted && first.cancelRequestIds.size()==1);
    assert(first.cancelRequestIds[0]==eurId && first.subscriptions.size()==1);
    const int refused1=first.subscriptions[0].requestId;
    assert(quotes.RecordDispatchResult(1,refused1,false));
    assert(!quotes.GetQuote("EUR.USD").HasQuote());
    assert(quotes.GetHealth().contracts.at("BTC.USD").requestId==btcId);
    assert(!quotes.BeginStaleSecondaryCashRefresh(
        "EUR.USD",17,1,17001,5000,15000).accepted);
    // After 15s, an unsent request may be replaced WITHOUT cancelling the
    // unsent ID or reusing it. A prolonged farm outage can refuse twice.
    const auto second=quotes.BeginStaleSecondaryCashRefresh(
        "EUR.USD",17,1,32000,5000,15000);
    assert(second.accepted && second.cancelRequestIds.empty());
    assert(second.subscriptions.size()==1);
    const int refused2=second.subscriptions[0].requestId;
    assert(refused2!=refused1 && refused2!=eurId);
    assert(quotes.RecordDispatchResult(1,refused2,false));
    assert(tick(eurId,"1",1.12,32001).status==
           IBAuthoritativeQuoteConsumeStatus::Ignored);
    assert(tick(refused1,"2",1.13,32002).status==
           IBAuthoritativeQuoteConsumeStatus::Ignored);
    assert(!quotes.GetQuote("EUR.USD").HasQuote());
    // Only a new positive CASH-farm witness allows the SDK to send the third
    // renewal. Its two fresh actual BID/ASK callbacks are mandatory.
    const auto restored=quotes.BeginStaleSecondaryCashRefresh(
        "EUR.USD",17,1,47000,5000,15000);
    assert(restored.accepted && restored.cancelRequestIds.empty());
    assert(restored.subscriptions.size()==1);
    const int current=restored.subscriptions[0].requestId;
    assert(current!=eurId && current!=refused1 && current!=refused2);
    assert(quotes.RecordDispatchResult(1,current,true));
    tick(current,"1",1.127,47001);
    assert(!quotes.GetQuote("EUR.USD").HasQuote());
    tick(current,"2",1.128,47002);
    assert(quotes.GetQuote("EUR.USD").HasQuote());
    assert(quotes.GetHealth().contracts.at("BTC.USD").requestId==btcId);
    assert(quotes.GetHealth().contracts.at("EUR.USD").requestId==current);
    assert(quotes.GetHealth().ReadyForOperations());
    assert(!quotes.BeginStaleSecondaryCashRefresh(
        "EUR.USD",18,1,65000,5000,15000).accepted);
    assert(!quotes.BeginStaleSecondaryCashRefresh(
        "BTC.USD",17,1,65000,5000,15000).accepted);
}

void TestMissingStaleAndStrictValidation()
{
    AuthoritativeTradingSnapshotStore store;
    assert(store.SetExecutionState(true, true, 900, "SIM.connection").accepted);
    AuthoritativeTradingSnapshot initialState = store.GetSnapshot(900);
    assert(initialState.executionState.connected);
    assert(initialState.executionState.authoritative);
    assert(initialState.executionState.updatedAtVersion == 1);
    assert(!store.SetExecutionState(false, true, 901, "SIM.connection").accepted);
    assert(store.GetQuote("EUR.USD", 1000, 100).state.availability ==
           AuthoritativeSnapshotAvailability::Missing);

    const AuthoritativeSnapshotWriteResult quoteWrite = store.UpsertQuote(
        Quote("EUR.USD", 1.1), 1000, "SIM.quote");
    assert(quoteWrite.accepted);
    assert(quoteWrite.snapshotVersion == 2);
    assert(store.GetQuote("EUR.USD", 1050, 100).state.availability ==
           AuthoritativeSnapshotAvailability::Fresh);
    assert(store.GetQuote("EUR.USD", 1101, 100).state.availability ==
           AuthoritativeSnapshotAvailability::Stale);
    assert(store.GetQuote("EUR.USD", 999, 100).state.availability ==
           AuthoritativeSnapshotAvailability::Stale);

    AuthoritativeQuote invalid = Quote("EUR.USD", 1.2);
    invalid.ask = std::numeric_limits<double>::quiet_NaN();
    const AuthoritativeSnapshotWriteResult rejected = store.UpsertQuote(invalid, 1100, "SIM.quote");
    assert(!rejected.accepted);
    assert(rejected.reasonCode == "NONFINITE_QUOTE_FIELD");
    assert(rejected.snapshotVersion == 2);
    assert(store.SnapshotVersion() == 2);

    const AuthoritativeSnapshotWriteResult regression = store.UpsertQuote(
        Quote("EUR.USD", 1.0), 999, "SIM.quote");
    assert(!regression.accepted);
    assert(regression.reasonCode == "OBSERVATION_TIME_REGRESSION");
    assert(store.SnapshotVersion() == 2);

    AuthoritativeAccount invalidAccount = Account("DU123", 100000.0);
    invalidAccount.availableFunds = std::numeric_limits<double>::infinity();
    assert(store.UpsertAccount(invalidAccount, 1001, "SIM.account").reasonCode ==
           "INVALID_ACCOUNT_METRIC");
    AuthoritativePosition invalidPosition = Position("EUR.USD", 1.0);
    invalidPosition.quantity = std::numeric_limits<double>::quiet_NaN();
    assert(store.UpsertPosition(invalidPosition, 1001, "SIM.position").reasonCode ==
           "NONFINITE_POSITION_FIELD");
    AuthoritativeActiveOrder invalidOrder = ActiveOrder(76);
    invalidOrder.side = static_cast<AuthoritativeOrderSide>(99);
    assert(store.UpsertActiveOrder(invalidOrder, 1001, "SIM.order").reasonCode ==
           "INVALID_ORDER_ENUM");
    assert(store.SnapshotVersion() == 2);

    assert(store.UpsertAccount(Account("DU123", 100000.0), 1001, "SIM.account").accepted);
    assert(store.UpsertPosition(Position("EUR.USD", 100.0), 1002, "SIM.position").accepted);
    assert(store.UpsertActiveOrder(ActiveOrder(77), 1003, "SIM.order").accepted);
    assert(store.SnapshotVersion() == 5);

    AuthoritativeSnapshotFreshnessPolicy policy;
    policy.quoteMaxAgeMs = 1000;
    policy.accountMaxAgeMs = 1000;
    policy.positionMaxAgeMs = 1000;
    policy.activeOrderMaxAgeMs = 1000;
    const AuthoritativeTradingSnapshot snapshot = store.GetSnapshot(1050, policy);
    assert(snapshot.snapshotVersion == 5);
    assert(snapshot.quotes.size() == 1);
    assert(snapshot.accounts.size() == 1);
    assert(snapshot.positions.size() == 1);
    assert(snapshot.activeOrders.size() == 1);
    assert(!snapshot.quotesState.complete);
    assert(snapshot.quotes.begin()->second.state.updatedAtVersion <= snapshot.snapshotVersion);
    assert(snapshot.accounts.begin()->second.state.updatedAtVersion <= snapshot.snapshotVersion);
    assert(snapshot.positions.begin()->second.state.updatedAtVersion <= snapshot.snapshotVersion);
    assert(snapshot.activeOrders.begin()->second.state.updatedAtVersion <= snapshot.snapshotVersion);

    const AuthoritativeSnapshotWriteResult erasedOrder = store.EraseActiveOrder(
        "SIM", 77, 1004, "SIM.order_terminal");
    assert(erasedOrder.accepted && erasedOrder.snapshotVersion == 6);
    assert(store.GetActiveOrder("SIM", 77, 1004, 100).state.availability ==
           AuthoritativeSnapshotAvailability::Missing);
    const AuthoritativeSnapshotWriteResult erasedPosition = store.ErasePosition(
        "DU123", "EUR.USD", 1005, "SIM.position_flat");
    assert(erasedPosition.accepted && erasedPosition.snapshotVersion == 7);
    assert(store.GetPosition("DU123", "EUR.USD", 1005, 100).state.availability ==
           AuthoritativeSnapshotAvailability::Missing);
}

void TestAtomicReplaceAndKnownEmpty()
{
    AuthoritativeTradingSnapshotStore store;
    std::vector<AuthoritativeQuote> quotes;
    quotes.push_back(Quote("EUR.USD", 1.1));
    quotes.push_back(Quote("GBP.USD", 1.3));
    assert(store.ReplaceQuotes(quotes, 1998, "SIM.quote_batch").accepted);
    std::vector<AuthoritativeAccount> accounts;
    accounts.push_back(Account("DU123", 100000.0));
    assert(store.ReplaceAccounts(accounts, 1999, "SIM.account_end").accepted);

    std::vector<AuthoritativePosition> initial;
    initial.push_back(Position("EUR.USD", 40.0));
    initial.push_back(Position("GBP.USD", -40.0));
    const AuthoritativeSnapshotWriteResult first = store.ReplacePositions(initial, 2000, "SIM.position_end");
    assert(first.accepted);

    const AuthoritativeTradingSnapshot snapshot = store.GetSnapshot(2001);
    assert(snapshot.quotesState.complete);
    assert(snapshot.accountsState.complete);
    assert(snapshot.positionsState.complete);
    assert(snapshot.positionsState.recordCount == 2);
    assert(snapshot.positionsState.lastUpdatedVersion == first.snapshotVersion);
    double sum = 0.0;
    for (std::map<AuthoritativePositionKey, AuthoritativePositionRecord>::const_iterator it =
             snapshot.positions.begin(); it != snapshot.positions.end(); ++it)
    {
        sum += it->second.value.quantity;
        assert(it->second.state.updatedAtVersion == first.snapshotVersion);
    }
    assert(std::abs(sum) < 1e-12);

    const std::vector<AuthoritativeActiveOrder> noOrders;
    const AuthoritativeSnapshotWriteResult empty = store.ReplaceActiveOrders(
        noOrders, 2002, "SIM.open_order_end");
    assert(empty.accepted);
    const AuthoritativeTradingSnapshot knownEmpty = store.GetSnapshot(2003);
    assert(knownEmpty.activeOrders.empty());
    assert(knownEmpty.activeOrdersState.complete);
    assert(knownEmpty.activeOrdersState.availability == AuthoritativeSnapshotAvailability::Fresh);

    std::vector<AuthoritativePosition> duplicate;
    duplicate.push_back(Position("EUR.USD", 1.0));
    duplicate.push_back(Position("EUR.USD", 2.0));
    const std::uint64_t before = store.SnapshotVersion();
    const AuthoritativeSnapshotWriteResult rejected = store.ReplacePositions(
        duplicate, 2004, "SIM.position_end");
    assert(!rejected.accepted);
    assert(rejected.reasonCode == "DUPLICATE_POSITION_KEY");
    assert(store.SnapshotVersion() == before);
}

void TestQuoteInvalidationPreservesRecordsButRevokesCompleteness()
{
    AuthoritativeTradingSnapshotStore store;
    std::vector<AuthoritativeQuote> quotes;
    quotes.push_back(Quote("EUR.USD", 1.1));
    quotes.push_back(Quote("GBP.USD", 1.3));
    assert(store.ReplaceQuotes(quotes, 3000, "SIM.quote_batch").accepted);
    const AuthoritativeSnapshotWriteResult invalidated =
        store.InvalidateQuotes(3001, "IB.quote_epoch");
    assert(invalidated.accepted);
    const AuthoritativeTradingSnapshot snapshot = store.GetSnapshot(3001);
    assert(!snapshot.quotesState.complete);
    assert(snapshot.quotes.size() == 2);
    const AuthoritativeSnapshotWriteResult regressed =
        store.InvalidateQuotes(2999, "IB.quote_epoch");
    assert(!regressed.accepted);
    assert(regressed.reasonCode == "OBSERVATION_TIME_REGRESSION");
}

void TestConcurrentReadersSeeCoherentVersions()
{
    AuthoritativeTradingSnapshotStore store;
    std::atomic<bool> start(false);
    std::atomic<bool> writerDone(false);
    std::atomic<bool> failed(false);
    std::vector<std::uint64_t> versions;
    std::mutex versionsMutex;

    const int writerCount = 4;
    const int writesPerThread = 250;
    std::vector<std::thread> writers;
    for (int writer = 0; writer < writerCount; ++writer)
    {
        writers.push_back(std::thread([&, writer]() {
            while (!start.load()) std::this_thread::yield();
            for (int index = 0; index < writesPerThread; ++index)
            {
                const std::string instrument = "SIM." + std::to_string(writer);
                const double bid = 100.0 + static_cast<double>(writer) +
                                   static_cast<double>(index) / 10000.0;
                const AuthoritativeSnapshotWriteResult result = store.UpsertQuote(
                    Quote(instrument, bid),
                    100000 + static_cast<std::uint64_t>(index),
                    "concurrent.quote");
                if (!result.accepted)
                {
                    failed.store(true);
                    return;
                }
                std::lock_guard<std::mutex> lock(versionsMutex);
                versions.push_back(result.snapshotVersion);
            }
        }));
    }

    std::thread reader([&]() {
        while (!start.load()) std::this_thread::yield();
        std::uint64_t previousVersion = 0;
        while (!writerDone.load())
        {
            AuthoritativeSnapshotFreshnessPolicy policy;
            policy.quoteMaxAgeMs = std::numeric_limits<std::uint64_t>::max();
            const AuthoritativeTradingSnapshot snapshot = store.GetSnapshot(100249, policy);
            if (snapshot.snapshotVersion < previousVersion) failed.store(true);
            previousVersion = snapshot.snapshotVersion;
            for (std::map<std::string, AuthoritativeQuoteRecord>::const_iterator it =
                     snapshot.quotes.begin(); it != snapshot.quotes.end(); ++it)
            {
                const AuthoritativeQuoteRecord& record = it->second;
                if (record.state.updatedAtVersion > snapshot.snapshotVersion ||
                    std::abs((record.value.ask - record.value.bid) - 0.0002) > 1e-10 ||
                    std::abs(record.value.bidSize - record.value.bid * 10.0) > 1e-8 ||
                    std::abs(record.value.askSize - record.value.bid * 20.0) > 1e-8)
                    failed.store(true);
            }
        }
    });

    start.store(true);
    for (std::size_t i = 0; i < writers.size(); ++i) writers[i].join();
    writerDone.store(true);
    reader.join();

    assert(!failed.load());
    assert(versions.size() == static_cast<std::size_t>(writerCount * writesPerThread));
    std::sort(versions.begin(), versions.end());
    for (std::size_t i = 0; i < versions.size(); ++i)
        assert(versions[i] == static_cast<std::uint64_t>(i + 1));
    assert(store.SnapshotVersion() == versions.size());
}

void TestAtomicReplaceDuringConcurrentReads()
{
    AuthoritativeTradingSnapshotStore store;
    std::atomic<bool> done(false);
    std::atomic<bool> failed(false);

    std::thread writer([&]() {
        for (int generation = 1; generation <= 500; ++generation)
        {
            std::vector<AuthoritativePosition> positions;
            positions.push_back(Position("PAIR.A", static_cast<double>(generation)));
            positions.push_back(Position("PAIR.B", -static_cast<double>(generation)));
            if (!store.ReplacePositions(positions,
                                        200000 + static_cast<std::uint64_t>(generation),
                                        "concurrent.position_end").accepted)
            {
                failed.store(true);
                break;
            }
        }
        done.store(true);
    });

    std::thread reader([&]() {
        while (!done.load())
        {
            const AuthoritativeTradingSnapshot snapshot = store.GetSnapshot(200500);
            if (snapshot.positions.empty()) continue;
            if (snapshot.positions.size() != 2 || !snapshot.positionsState.complete)
            {
                failed.store(true);
                continue;
            }
            double quantitySum = 0.0;
            for (std::map<AuthoritativePositionKey, AuthoritativePositionRecord>::const_iterator it =
                     snapshot.positions.begin(); it != snapshot.positions.end(); ++it)
            {
                quantitySum += it->second.value.quantity;
                if (it->second.state.updatedAtVersion != snapshot.positionsState.lastUpdatedVersion ||
                    it->second.state.updatedAtVersion > snapshot.snapshotVersion)
                    failed.store(true);
            }
            if (std::abs(quantitySum) > 1e-12) failed.store(true);
        }
    });

    writer.join();
    reader.join();
    assert(!failed.load());
    assert(store.SnapshotVersion() == 500);
}

} // namespace

int main()
{
    TestCryptoIdentityAndIndependentClosedFxQuotes();
    TestStaleSecondaryCashRefreshKeepsPrimaryAndCannotForgeQuote();
    TestSecondaryCashFarmDeferralDoesNotReplayAnUnsentRequest();
    TestMissingStaleAndStrictValidation();
    TestAtomicReplaceAndKnownEmpty();
    TestQuoteInvalidationPreservesRecordsButRevokesCompleteness();
    TestConcurrentReadersSeeCoherentVersions();
    TestAtomicReplaceDuringConcurrentReads();
    std::cout << "authoritative_trading_snapshot_store_tests: PASS" << std::endl;
    return 0;
}
