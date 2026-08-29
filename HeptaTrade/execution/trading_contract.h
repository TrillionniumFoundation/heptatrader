#pragma once

#include <cmath>
#include <cstdint>
#include <string>

// Venue-neutral instrument identity carried across the Agent OS and Execution
// Service boundary. Venue adapters may translate this representation into
// their native contract type, but must not expose native SDK objects upstream.
struct InstrumentRef
{
    std::string symbol;
    std::string secType;
    std::string exchange;
    std::string primaryExchange;
    std::string currency;
    std::string lastTradeDateOrContractMonth;
    std::string right;
    double strike = 0.0;
    std::string multiplier;
    std::string tradingClass;
    std::string localSymbol;
};

// Venue-neutral order intent. Correlation is assigned by the authoritative
// Execution Service; Agent callers cannot supply or widen it.
struct OrderIntent
{
    std::string action;
    std::string orderType;
    double totalQuantity = 0.0;
    double lmtPrice = 0.0;
    double auxPrice = 0.0;
    bool outsideRth = false;
    std::string orderRef;
};

enum class MarketSubscriptionState
{
    Pending = 0,
    Active,
    Stale,
    Unavailable
};

struct MarketQuoteSnapshot
{
    std::string subscriptionId;
    std::string instrument;
    MarketSubscriptionState state = MarketSubscriptionState::Unavailable;
    double bid = 0.0;
    double ask = 0.0;
    std::uint64_t observedAtMs = 0;
    std::uint64_t staleAfterMs = 0;

    bool IsFresh(std::uint64_t nowMs) const
    {
        return state == MarketSubscriptionState::Active && !subscriptionId.empty() &&
            !instrument.empty() && std::isfinite(bid) && std::isfinite(ask) &&
            bid > 0.0 && ask > 0.0 && ask >= bid && observedAtMs > 0 &&
            observedAtMs <= nowMs && staleAfterMs >= observedAtMs &&
            nowMs <= staleAfterMs;
    }
};

// Transitional source aliases for venue adapters and downstream integrations.
// New Agent OS and Execution Service code must use the neutral names above.
using IBContractLite = InstrumentRef;
using IBOrderLite = OrderIntent;
