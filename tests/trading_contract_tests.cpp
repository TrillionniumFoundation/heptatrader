#include "../HeptaTrade/execution/execution_authority.h"
#include <type_traits>
#include <utility>

static_assert(std::is_same<decltype(std::declval<ExecutionControlAuthority&>().QueryCommandStatus(
    std::declval<const ExecutionControlCommand&>())), ExecutionControlStatusResult>::value,
    "ordinary status must not return audit or terminal authority");
static_assert(std::is_same<decltype(std::declval<ExecutionControlAuthority&>().FenceSessionOwner(
    std::declval<const ExecutionControlCommand&>())), ExecutionControlStatusResult>::value,
    "fencing must not return unrelated terminal evidence");
static_assert(std::is_same<decltype(std::declval<ExecutionControlAuthority&>().RecoveryAuditOwner(
    std::declval<const ExecutionControlCommand&>())), ExecutionOwnerAuditResult>::value,
    "recovery audit must not return terminal evidence");
static_assert(!std::is_convertible<ExecutionControlStatusResult, ExecutionControlResult>::value,
    "wire widening must be explicit and start with non-authorizing defaults");
static_assert(!std::is_convertible<ExecutionOwnerAuditResult, ExecutionTerminalWitness>::value,
    "owner audit does not prove terminal shutdown");

#include "../HeptaTrade/execution/trading_contract.h"

#include <cassert>
#include <type_traits>

int main()
{
    static_assert(std::is_same<IBContractLite, InstrumentRef>::value,
                  "IB contract compatibility must adapt to InstrumentRef");
    static_assert(std::is_same<IBOrderLite, OrderIntent>::value,
                  "IB order compatibility must adapt to OrderIntent");

    InstrumentRef instrument;
    instrument.symbol = "EUR";
    instrument.currency = "USD";
    instrument.secType = "CASH";

    OrderIntent order;
    order.action = "BUY";
    order.orderType = "LMT";
    order.totalQuantity = 1000.0;
    order.lmtPrice = 1.1;

    assert(instrument.symbol == "EUR");
    assert(instrument.currency == "USD");
    assert(order.action == "BUY");
    assert(order.totalQuantity == 1000.0);
    assert(order.orderRef.empty());

    MarketQuoteSnapshot quote;
    quote.subscriptionId = "sim:EUR.USD";
    quote.instrument = "EUR.USD";
    quote.state = MarketSubscriptionState::Active;
    quote.bid = 1.1;
    quote.ask = 1.1002;
    quote.observedAtMs = 1000;
    quote.staleAfterMs = 2000;
    assert(quote.IsFresh(2000));
    assert(!quote.IsFresh(2001));
    quote.state = MarketSubscriptionState::Stale;
    assert(!quote.IsFresh(1500));
    return 0;
}
