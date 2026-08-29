#include "state/ib_authoritative_open_order_consumer.h"

#include <cassert>
#include <atomic>
#include <thread>
#include <vector>

namespace {

IBEvent OpenOption(long orderId, const std::string& account, const std::string& orderType)
{
    IBEvent event;
    event.type = IBEventType::OpenOrder;
    event.id = orderId;
    event.account = account;
    event.value = "Submitted";
    event.contract.symbol = "SPY";
    event.contract.secType = "OPT";
    event.contract.exchange = "SMART";
    event.contract.currency = "USD";
    event.contract.localSymbol = "SPY  260721P00500000";
    event.order.action = "BUY";
    event.order.orderType = orderType;
    event.order.totalQuantity = 2.0;
    event.order.lmtPrice = 4.25;
    return event;
}

IbPlaceOrderCommand PlacedFx()
{
    IbPlaceOrderCommand command;
    command.context.account = "DU123";
    command.instrument = "EUR.USD";
    command.contract.symbol = "EUR";
    command.contract.secType = "CASH";
    command.contract.exchange = "IDEALPRO";
    command.contract.currency = "USD";
    command.order.action = "SELL";
    command.order.orderType = "LMT";
    command.order.totalQuantity = 100.0;
    command.order.lmtPrice = 1.1;
    return command;
}

void TestRefreshAndImmediateProjectionShareOneAggregate()
{
    AuthoritativeTradingSnapshotStore store;
    IBAuthoritativeOpenOrderConsumer consumer(store, "OLD");
    assert(consumer.ConfigureAccount("DU123"));
    consumer.BeginRefresh(4);
    assert(!consumer.ConfigureAccount("OTHER"));
    assert(consumer.IsRefreshInFlight());
    const IBAuthoritativeOrderProjectionResult option =
        consumer.ConsumeOpenOrder(OpenOption(41, "DU123", "LMT"), 1000);
    assert(option.status == IBAuthoritativeOrderProjectionStatus::Applied);
    assert(option.order.instrument == "OPT:SPY260721P00500000:USD:SMART");
    assert(consumer.ConsumeOpenOrder(OpenOption(42, "OTHER", "LMT"), 1001).status ==
           IBAuthoritativeOrderProjectionStatus::Ignored);
    assert(consumer.ProjectPlaced(PlacedFx(), 43, 1002).status ==
           IBAuthoritativeOrderProjectionStatus::Applied);
    assert(consumer.ProjectOrderStatus(
        43, "Cancelled", 0.0, 100.0, 0.0, false, 1003).status ==
           IBAuthoritativeOrderProjectionStatus::Applied);
    assert(!consumer.CompleteRefresh(3, 1004).accepted);
    const IBAuthoritativeOpenOrderCompletion completed = consumer.CompleteRefresh(4, 1004);
    assert(completed.accepted);
    assert(completed.orders.size() == 1);
    assert(completed.orders[0].orderId == 41);
    assert(!consumer.IsRefreshInFlight());
    const AuthoritativeTradingSnapshot snapshot = store.GetSnapshot(1004);
    assert(snapshot.activeOrdersState.complete);
    assert(snapshot.activeOrders.size() == 1);
}

void TestRejectedBrokerOrderKeepsSnapshotIncomplete()
{
    AuthoritativeTradingSnapshotStore store;
    IBAuthoritativeOpenOrderConsumer consumer(store, "DU123");
    consumer.BeginRefresh(5);
    const IBAuthoritativeOrderProjectionResult rejected =
        consumer.ConsumeOpenOrder(OpenOption(51, "DU123", "TRAIL"), 2000);
    assert(rejected.status == IBAuthoritativeOrderProjectionStatus::Rejected);
    const IBAuthoritativeOpenOrderCompletion completed = consumer.CompleteRefresh(5, 2001);
    assert(!completed.accepted);
    assert(completed.reasonCode == "UNSUPPORTED_AUTHORITATIVE_ORDER_TYPE");
    assert(!store.GetSnapshot(2001).activeOrdersState.complete);
}

void TestAbortInvalidatesGeneration()
{
    AuthoritativeTradingSnapshotStore store;
    IBAuthoritativeOpenOrderConsumer consumer(store, "DU123");
    consumer.BeginRefresh(6);
    consumer.AbortRefresh(6);
    assert(!consumer.IsRefreshInFlight());
    assert(!consumer.CompleteRefresh(6, 3000).accepted);
}

void TestOpenOrderProjectsOutsideRefresh()
{
    AuthoritativeTradingSnapshotStore store;
    assert(store.ReplaceActiveOrders(std::vector<AuthoritativeActiveOrder>(), 4000, "test.bootstrap").accepted);
    IBAuthoritativeOpenOrderConsumer consumer(store, "DU123");
    const IBAuthoritativeOrderProjectionResult projection =
        consumer.ConsumeOpenOrder(OpenOption(61, "DU123", "LMT"), 4001);
    assert(projection.status == IBAuthoritativeOrderProjectionStatus::Applied);
    assert(!consumer.IsRefreshInFlight());
    const AuthoritativeTradingSnapshot snapshot = store.GetSnapshot(4001);
    assert(snapshot.activeOrders.size() == 1);
}

void TestConcurrentProjectionAndCompletionSerialize()
{
    AuthoritativeTradingSnapshotStore store;
    IBAuthoritativeOpenOrderConsumer consumer(store, "DU123");
    consumer.BeginRefresh(7);
    std::atomic<bool> start(false);
    std::atomic<bool> projected(false);
    std::thread projectionThread([&]() {
        while (!start.load()) std::this_thread::yield();
        projected.store(consumer.ProjectPlaced(PlacedFx(), 71, 5000).status ==
                        IBAuthoritativeOrderProjectionStatus::Applied);
    });
    start.store(true);
    const IBAuthoritativeOpenOrderCompletion completed = consumer.CompleteRefresh(7, 5000);
    projectionThread.join();
    assert(completed.accepted);
    assert(projected.load());
    const AuthoritativeTradingSnapshot snapshot = store.GetSnapshot(5000);
    assert(snapshot.activeOrdersState.complete);
    assert(snapshot.activeOrders.size() == 1);
}

}

int main()
{
    TestRefreshAndImmediateProjectionShareOneAggregate();
    TestRejectedBrokerOrderKeepsSnapshotIncomplete();
    TestAbortInvalidatesGeneration();
    TestOpenOrderProjectsOutsideRefresh();
    TestConcurrentProjectionAndCompletionSerialize();
    return 0;
}
