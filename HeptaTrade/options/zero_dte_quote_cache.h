#pragma once
#include "zero_dte.h"
namespace hepta { namespace options {
// A type transition invalidates values; it must never relabel cached ticks.
struct QuoteCache {
    Book book;
    Tick last, close;
    Greeks greeks;
    void Invalidate() {
        const bool denied=book.entitlementDenied;
        book=Book(); last=Tick(); close=Tick(); greeks=Greeks();
        book.entitlementDenied=denied;
    }
    void MarketDataTypeObserved(int type) {
        if(type<1 || type>4) type=0;
        if(type!=book.marketDataType) { Invalidate(); book.marketDataType=type; }
    }
    void EntitlementDenied() { Invalidate(); book.entitlementDenied=true; }
};
} }
