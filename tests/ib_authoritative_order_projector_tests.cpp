#include "state/ib_authoritative_order_projector.h"

#include <cassert>
#include <cstdint>
#include <limits>
#include <vector>

namespace {

IbPlaceOrderCommand MakeLimitOrder()
{
    IbPlaceOrderCommand command;
    command.context.account = "DU123";
    command.instrument = "eur/usd";
    command.order.action = "BUY";
    command.order.orderType = "LMT";
    command.order.totalQuantity = 100.0;
    command.order.lmtPrice = 1.1;
    return command;
}

void TestCoordinatorLifecycleProjection()
{
    AuthoritativeTradingSnapshotStore store;
    assert(store.ReplaceActiveOrders(std::vector<AuthoritativeActiveOrder>(), 1000, "test.bootstrap").accepted);
    IBAuthoritativeOrderProjector projector(store);

    const IBAuthoritativeOrderProjectionResult placed = projector.ProjectPlaced(MakeLimitOrder(), 41, 1001);
    assert(placed.status == IBAuthoritativeOrderProjectionStatus::Applied);
    assert(placed.hasOrder);
    assert(placed.order.instrument == "EUR.USD");
    assert(placed.order.status == AuthoritativeActiveOrderStatus::PendingSubmit);

    AuthoritativeActiveOrderRecord record = store.GetActiveOrder(
        "IB", 41, 1001, std::numeric_limits<std::uint64_t>::max());
    assert(record.state.availability == AuthoritativeSnapshotAvailability::Fresh);
    assert(record.value.remainingQuantity == 100.0);

    const IBAuthoritativeOrderProjectionResult submitted =
        projector.ProjectOrderStatus(41, "Submitted", 20.0, 80.0,
                                     0.0, false, 1002);
    assert(submitted.status == IBAuthoritativeOrderProjectionStatus::Applied);
    record = store.GetActiveOrder("IB", 41, 1002, std::numeric_limits<std::uint64_t>::max());
    assert(record.value.status == AuthoritativeActiveOrderStatus::PartiallyFilled);
    assert(record.value.filledQuantity == 20.0);
    assert(record.value.remainingQuantity == 80.0);

    const IBAuthoritativeOrderProjectionResult executionFill =
        projector.ProjectOrderStatus(41, "PartiallyFilled", 25.0, 75.0,
                                     1.1, true, 1003);
    assert(executionFill.status == IBAuthoritativeOrderProjectionStatus::Applied);

    const IBAuthoritativeOrderProjectionResult cancel = projector.ProjectCancelSent(41, 1004);
    assert(cancel.status == IBAuthoritativeOrderProjectionStatus::Applied);
    record = store.GetActiveOrder("IB", 41, 1004, std::numeric_limits<std::uint64_t>::max());
    assert(record.value.status == AuthoritativeActiveOrderStatus::PendingCancel);

    const IBAuthoritativeOrderProjectionResult terminal =
        projector.ProjectOrderStatus(41, "Cancelled", 25.0, 75.0,
                                     0.0, false, 1005);
    assert(terminal.status == IBAuthoritativeOrderProjectionStatus::Applied);
    record = store.GetActiveOrder("IB", 41, 1005, std::numeric_limits<std::uint64_t>::max());
    assert(record.state.availability == AuthoritativeSnapshotAvailability::Missing);

    const AuthoritativeTradingSnapshot snapshot = store.GetSnapshot(1005);
    assert(snapshot.activeOrdersState.complete);
    assert(snapshot.activeOrders.empty());
}

void TestBrokerOpenOrderAndMissingStatus()
{
    AuthoritativeTradingSnapshotStore store;
    IBAuthoritativeOrderProjector projector(store);

    IBEvent openOrder;
    openOrder.type = IBEventType::OpenOrder;
    openOrder.id = 52;
    openOrder.account = "DU456";
    openOrder.key = "SPY";
    openOrder.contract.symbol = "SPY";
    openOrder.contract.secType = "OPT";
    openOrder.contract.exchange = "SMART";
    openOrder.contract.currency = "USD";
    openOrder.contract.localSymbol = "SPY  260721P00500000";
    openOrder.value = "PreSubmitted";
    openOrder.order.action = "SELL";
    openOrder.order.orderType = "STP";
    openOrder.order.totalQuantity = 2.0;
    openOrder.order.auxPrice = 500.0;
    const IBAuthoritativeOrderProjectionResult projected =
        projector.ProjectOpenOrder(openOrder, "DU-FALLBACK", 2000);
    assert(projected.status == IBAuthoritativeOrderProjectionStatus::Applied);
    assert(projected.order.type == AuthoritativeOrderType::Stop);
    assert(projected.order.instrument == "OPT:SPY260721P00500000:USD:SMART");
    assert(projected.order.stopPrice == 500.0);
    assert(projected.order.account == "DU456");

    const IBAuthoritativeOrderProjectionResult missing =
        projector.ProjectOrderStatus(99, "Submitted", 0.0, 1.0,
                                     0.0, false, 2001);
    assert(missing.status == IBAuthoritativeOrderProjectionStatus::Missing);
    assert(missing.reasonCode == "AUTHORITATIVE_ORDER_NOT_FOUND");

    IBEvent unsupported = openOrder;
    unsupported.id = 53;
    unsupported.order.orderType = "TRAIL";
    const IBAuthoritativeOrderProjectionResult rejected =
        projector.ProjectOpenOrder(unsupported, "DU-FALLBACK", 2002);
    assert(rejected.status == IBAuthoritativeOrderProjectionStatus::Rejected);
    assert(rejected.reasonCode == "UNSUPPORTED_AUTHORITATIVE_ORDER_TYPE");

    const IBAuthoritativeOrderProjectionResult unknown =
        projector.ProjectOrderStatus(99, "UnknownStatus", 0.0, 1.0,
                                     0.0, false, 2003);
    assert(unknown.status == IBAuthoritativeOrderProjectionStatus::Ignored);
}

void TestInvalidBrokerQuantityFailsClosed()
{
    AuthoritativeTradingSnapshotStore store;
    IBAuthoritativeOrderProjector projector(store);
    assert(projector.ProjectPlaced(MakeLimitOrder(), 61, 3000).status ==
           IBAuthoritativeOrderProjectionStatus::Applied);

    const IBAuthoritativeOrderProjectionResult invalid = projector.ProjectOrderStatus(
        61, "Submitted", 10.0, 0.0, 0.0, false, 3001);
    assert(invalid.status == IBAuthoritativeOrderProjectionStatus::Rejected);
    assert(invalid.reasonCode == "INVALID_BROKER_ORDER_QUANTITY");
}

void TestFilledRequiresEconomicEvidence()
{
    AuthoritativeTradingSnapshotStore store;
    IBAuthoritativeOrderProjector projector(store);
    assert(projector.ProjectPlaced(MakeLimitOrder(), 71, 4000).status ==
           IBAuthoritativeOrderProjectionStatus::Applied);

    const IBAuthoritativeOrderProjectionResult zeroFill =
        projector.ProjectOrderStatus(71, "Filled", 0.0, 0.0,
                                     0.0, false, 4001);
    assert(zeroFill.status == IBAuthoritativeOrderProjectionStatus::Rejected);
    assert(zeroFill.reasonCode == "IB_FILLED_ECONOMIC_EVIDENCE_REQUIRED");
    assert(store.GetActiveOrder(
        "IB", 71, 4001, std::numeric_limits<std::uint64_t>::max()).state.availability ==
        AuthoritativeSnapshotAvailability::Fresh);

    const IBAuthoritativeOrderProjectionResult economicFill =
        projector.ProjectOrderStatus(71, "Filled", 100.0, 0.0,
                                     1.1, false, 4002);
    assert(economicFill.status == IBAuthoritativeOrderProjectionStatus::Applied);
    assert(store.GetActiveOrder(
        "IB", 71, 4002, std::numeric_limits<std::uint64_t>::max()).state.availability ==
        AuthoritativeSnapshotAvailability::Missing);

    assert(projector.ProjectPlaced(MakeLimitOrder(), 72, 4003).status ==
           IBAuthoritativeOrderProjectionStatus::Applied);
    const IBAuthoritativeOrderProjectionResult executionFill =
        projector.ProjectOrderStatus(72, "Filled", 100.0, 0.0,
                                     0.0, true, 4004);
    assert(executionFill.status == IBAuthoritativeOrderProjectionStatus::Applied);
}

} // namespace

int main()
{
    TestCoordinatorLifecycleProjection();
    TestBrokerOpenOrderAndMissingStatus();
    TestInvalidBrokerQuantityFailsClosed();
    TestFilledRequiresEconomicEvidence();
    return 0;
}
