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
