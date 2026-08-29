#include "state/ib_authoritative_account_position_consumer.h"
#include "state/ib_contract_identity.h"

#include <cassert>
#include <cmath>
#include <limits>

namespace {

IBEvent AccountValue(const std::string& account,
                     const std::string& key,
                     const std::string& value)
{
    IBEvent event;
    event.type = IBEventType::AccountValue;
    event.account = account;
    event.key = key;
    event.value = value;
    return event;
}

IBEvent Position(const std::string& account,
                 const IBContractLite& contract,
                 double quantity,
                 double averageCost)
{
    IBEvent event;
    event.type = IBEventType::PositionSnapshotItem;
    event.account = account;
    event.contract = contract;
    event.key = contract.symbol;
    event.number = quantity;
    event.number2 = averageCost;
    return event;
}

void TestAccountProjectionAndGeneration()
{
    AuthoritativeTradingSnapshotStore store;
    IBAuthoritativeAccountPositionConsumer consumer(store, "OLD");
    assert(consumer.ConfigureAccount("DU123"));
    consumer.BeginAccount(7);
    assert(!consumer.ConfigureAccount("OTHER"));
    assert(consumer.ConsumeAccountValue(AccountValue("OTHER", "NetLiquidation:USD", "999")) ==
           IBAuthoritativeSnapshotConsumeStatus::Ignored);
    assert(consumer.ConsumeAccountValue(AccountValue("DU123", "NetLiquidation:USD", "125000.5")) ==
           IBAuthoritativeSnapshotConsumeStatus::Applied);
    assert(consumer.ConsumeAccountValue(AccountValue("DU123", "AvailableFunds:USD", "75000")) ==
           IBAuthoritativeSnapshotConsumeStatus::Applied);
    assert(!consumer.CompleteAccount(6, 1000).accepted);

    const IBAuthoritativeAccountCompletion completed = consumer.CompleteAccount(7, 1000);
    assert(completed.accepted);
    assert(completed.account.account == "DU123");
    assert(completed.account.currency == "USD");
    assert(completed.account.hasNetLiquidation);
    assert(completed.account.netLiquidation == 125000.5);
    const AuthoritativeTradingSnapshot snapshot = store.GetSnapshot(1000);
    assert(snapshot.accounts.size() == 1);
    assert(snapshot.accounts.find("OTHER") == snapshot.accounts.end());
}

void TestDerivativePositionIdentity()
{
    AuthoritativeTradingSnapshotStore store;
    IBAuthoritativeAccountPositionConsumer consumer(store, "DU123");
    consumer.BeginPositions(11);

    IBContractLite option;
    option.symbol = "SPY";
    option.secType = "OPT";
    option.exchange = "SMART";
    option.currency = "USD";
    option.localSymbol = "SPY  260721P00500000";
    assert(consumer.ConsumePosition(Position("DU123", option, -2.0, 425.25)) ==
           IBAuthoritativeSnapshotConsumeStatus::Applied);

    IBContractLite optionWithoutLocal = option;
    optionWithoutLocal.localSymbol.clear();
    optionWithoutLocal.lastTradeDateOrContractMonth = "20260821";
    optionWithoutLocal.right = "CALL";
    optionWithoutLocal.strike = 550.0;
    optionWithoutLocal.multiplier = "100";
    optionWithoutLocal.tradingClass = "SPY";
    assert(consumer.ConsumePosition(Position("DU123", optionWithoutLocal, 3.0, 210.0)) ==
           IBAuthoritativeSnapshotConsumeStatus::Applied);

    IBContractLite future;
    future.symbol = "ES";
    future.secType = "FUT";
    future.exchange = "CME";
    future.currency = "USD";
    future.lastTradeDateOrContractMonth = "202609";
    future.tradingClass = "ES";
    assert(consumer.ConsumePosition(Position("DU123", future, 1.0, 6100.0)) ==
           IBAuthoritativeSnapshotConsumeStatus::Applied);

    IBContractLite ignored = future;
    ignored.symbol = "NQ";
    assert(consumer.ConsumePosition(Position("OTHER", ignored, 4.0, 20000.0)) ==
           IBAuthoritativeSnapshotConsumeStatus::Ignored);

    const IBAuthoritativePositionCompletion completed = consumer.CompletePositions(11, 2000);
    assert(completed.accepted);
    assert(completed.positions.size() == 3);
    assert(completed.quantities.at("OPT:SPY260721P00500000:USD:SMART") == -2.0);
    assert(completed.quantities.at("OPT:SPY:20260821:C:550:100:SPY:USD:SMART") == 3.0);
    assert(completed.quantities.at("FUT:ES:202609:ES:USD:CME") == 1.0);
    const AuthoritativeTradingSnapshot snapshot = store.GetSnapshot(2000);
    assert(snapshot.positions.size() == 3);
}

void TestInvalidPositionFailsClosed()
{
    AuthoritativeTradingSnapshotStore store;
    IBAuthoritativeAccountPositionConsumer consumer(store, "DU123");
    consumer.BeginPositions(12);
    IBContractLite cash;
    cash.symbol = "EUR";
    cash.secType = "CASH";
    cash.currency = "USD";
    assert(consumer.ConsumePosition(Position(
               "DU123", cash, std::numeric_limits<double>::quiet_NaN(), 1.1)) ==
           IBAuthoritativeSnapshotConsumeStatus::Rejected);
    const IBAuthoritativePositionCompletion completed = consumer.CompletePositions(12, 3000);
    assert(!completed.accepted);
    assert(completed.reasonCode == "NON_FINITE_POSITION_VALUE");
    assert(!store.GetSnapshot(3000).positionsState.complete);
}

}

int main()
{
    TestAccountProjectionAndGeneration();
    TestDerivativePositionIdentity();
    TestInvalidPositionFailsClosed();
    return 0;
}
