#include "../HeptaTrade/state/authoritative_trading_snapshot_store.h"

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
    TestMissingStaleAndStrictValidation();
    TestAtomicReplaceAndKnownEmpty();
    TestQuoteInvalidationPreservesRecordsButRevokesCompleteness();
    TestConcurrentReadersSeeCoherentVersions();
    TestAtomicReplaceDuringConcurrentReads();
    std::cout << "authoritative_trading_snapshot_store_tests: PASS" << std::endl;
    return 0;
}
