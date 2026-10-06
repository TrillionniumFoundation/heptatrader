#pragma once
#include "ib_api_wrapper.h"
#include "../oms_journal.h"
#include <cmath>
#include <iomanip>
#include <locale>
#include <sstream>

// Economic contract identity deliberately excludes routing/execution exchange:
// SMART qualification and an actual execution venue need not have the same name.
// The exact conId is retained independently; no localSymbol-only equivalence.
inline std::string IbEconomicContractIdentity(const InstrumentRef& c) {
    std::ostringstream out;out.imbue(std::locale::classic());out.precision(17);out<<"HIBE1:";
    for(const auto& s:{c.symbol,c.secType,c.currency,c.lastTradeDateOrContractMonth,c.right,
                      c.multiplier,c.tradingClass,c.localSymbol})out<<s.size()<<':'<<s;
    out<<':'<<c.strike;return out.str();
}
inline bool IbUsableEconomicNumber(double value){return std::isfinite(value)&&std::fabs(value)<1e100;}
inline double IbRetainFiniteEconomicNumber(double value){return IbUsableEconomicNumber(value)?value:0;}
inline IbEconomicEvidence CaptureIbExecutionEconomics(const InstrumentRef& contract,std::int64_t conId,
    std::int64_t permId,double shares,double price,double cumulative,double average,bool pendingRevision) {
    IbEconomicEvidence e;e.version=1;e.kind="EXECUTION";e.contractIdentity=IbEconomicContractIdentity(contract);
    e.contractId=conId>0?conId:0;e.permanentOrderId=permId>0?permId:0;
    e.shares=IbRetainFiniteEconomicNumber(shares);e.executionPrice=IbRetainFiniteEconomicNumber(price);
    e.cumulativeQuantity=IbRetainFiniteEconomicNumber(cumulative);e.cumulativeAverage=IbRetainFiniteEconomicNumber(average);
    e.priceRevisionPending=pendingRevision?1:0;
    e.valid=conId>0 && permId>=0 && IbUsableEconomicNumber(shares) && shares>0 &&
        IbUsableEconomicNumber(price) && price>0 && IbUsableEconomicNumber(cumulative) && cumulative>=shares &&
        IbUsableEconomicNumber(average) && average>0;
    return e;
}
inline IbEconomicEvidence CaptureIbCommissionEconomics(double commission,const std::string& currency,double pnl) {
    IbEconomicEvidence e;e.version=1;e.kind="COMMISSION";e.commission=IbRetainFiniteEconomicNumber(commission);
    e.commissionCurrency=currency;e.realizedPnlKnown=IbUsableEconomicNumber(pnl)?1:0;
    e.realizedPnl=IbRetainFiniteEconomicNumber(pnl);
    e.valid=IbUsableEconomicNumber(commission)&&std::fabs(commission)<=1000000 && currency.size()==3 &&
        currency.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZ")==std::string::npos;
    return e;
}
inline void RetainIbCallbackEconomics(const IBEvent& event,OmsJournalEvent& record) {
    record.brokerEconomics=event.economics;
    if(event.type!=IBEventType::CommissionReport)return;
    // CommissionReport supplies execId but NO account, order id, client id or
    // orderRef. Do not borrow the owner of numeric order 0 (or any other order).
    record.eventType="broker_commission";record.brokerCallbackType="commissionReport";
    record.status="CommissionReport";record.brokerExecutionId=event.key;record.orderId=-1;
    record.reqId.clear();record.clientReqId.clear();record.account.clear();record.instrument.clear();
    record.traceId.clear();record.strategy.clear();record.side.clear();record.venueCorrelationId.clear();
    record.source="ib-api-callback";record.brokerClientId=-1;record.qty=0;record.price=0;
    record.brokerRemainingQuantity=0;
}
