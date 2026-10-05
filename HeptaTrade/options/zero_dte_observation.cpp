#include "zero_dte.h"
#include <cmath>
#include <iomanip>
#include <locale>
#include <sstream>
#include <stdexcept>
#include <vector>
namespace hepta { namespace options {
namespace {
template<class T> void Put(std::ostringstream& out,const T& v){out << '\t' << v;}
void Put(std::ostringstream& out,const std::string& s){
    if(s.find_first_of("\t\r\n")!=std::string::npos)throw std::invalid_argument("OPTION_CAPTURE_STRING_INVALID");
    out << '\t' << s;
}
template<class T> bool Get(const std::string& s,T& v){
    std::istringstream in(s);in.imbue(std::locale::classic());in >> v;
    return bool(in) && in.peek()==std::char_traits<char>::eof();
}
bool Get(const std::string& s,std::string& v){v=s;return true;}
bool Get(const std::string& s,bool& v){if(s!="0" && s!="1")return false;v=s=="1";return true;}
}
std::string EncodeObservation(const Policy& p,const Contract& c,const Snapshot& s){
    std::ostringstream o;o.imbue(std::locale::classic());o.precision(17);o << "H0D1";
    Put(o,c.conId);
    Put(o,c.underlyingConId);
    Put(o,c.ref.symbol);
    Put(o,c.ref.secType);
    Put(o,c.ref.exchange);
    Put(o,c.ref.primaryExchange);
    Put(o,c.ref.currency);
    Put(o,c.ref.lastTradeDateOrContractMonth);
    Put(o,c.ref.right);
    Put(o,c.ref.strike);
    Put(o,c.ref.multiplier);
    Put(o,c.ref.tradingClass);
    Put(o,c.ref.localSymbol);
    Put(o,c.session.tradingDate);
    Put(o,c.session.openMs);
    Put(o,c.session.lastTradeMs);
    Put(o,c.session.brokerScheduleVerified);
    Put(o,c.european);
    Put(o,c.cashSettled);
    Put(o,c.pmSettled);
    Put(o,p.account);
    Put(o,p.owner);
    Put(o,p.maxContracts);
    Put(o,p.maxPremiumUsd);
    Put(o,p.maxDailyLossUsd);
    Put(o,p.maxQuoteAgeMs);
    Put(o,p.maxGreeksAgeMs);
    Put(o,p.entryCutoffBeforeCloseMs);
    Put(o,p.exitBeforeCloseMs);
    Put(o,p.maxAbsoluteSpread);
    Put(o,p.maxRelativeSpread);
    Put(o,s.nowMs);
    Put(o,s.epoch);
    Put(o,s.account);
    Put(o,s.owner);
    Put(o,s.executionDomain);
    Put(o,s.option.marketDataType);
    Put(o,s.option.entitlementDenied);
    Put(o,s.underlyingDataType);
    Put(o,s.option.bid.value);
    Put(o,s.option.bid.observedMs);
    Put(o,s.option.bid.epoch);
    Put(o,s.option.bid.valid);
    Put(o,s.option.ask.value);
    Put(o,s.option.ask.observedMs);
    Put(o,s.option.ask.epoch);
    Put(o,s.option.ask.valid);
    Put(o,s.option.bidSize.value);
    Put(o,s.option.bidSize.observedMs);
    Put(o,s.option.bidSize.epoch);
    Put(o,s.option.bidSize.valid);
    Put(o,s.option.askSize.value);
    Put(o,s.option.askSize.observedMs);
    Put(o,s.option.askSize.epoch);
    Put(o,s.option.askSize.valid);
    Put(o,s.underlying.value);
    Put(o,s.underlying.observedMs);
    Put(o,s.underlying.epoch);
    Put(o,s.underlying.valid);
    Put(o,s.greeks.delta);
    Put(o,s.greeks.gamma);
    Put(o,s.greeks.theta);
    Put(o,s.greeks.vega);
    Put(o,s.greeks.iv);
    Put(o,s.greeks.underlying);
    Put(o,s.greeks.observedMs);
    Put(o,s.greeks.epoch);
    Put(o,s.greeks.valid);
    Put(o,s.accountComplete);
    Put(o,s.positionsComplete);
    Put(o,s.ordersComplete);
    Put(o,s.tradingPermissionVerified);
    Put(o,s.marginPreviewVerified);
    Put(o,s.killNewEntries);
    Put(o,s.unresolvedSend);
    Put(o,s.ownedContracts);
    Put(o,s.ownedActiveOrders);
    Put(o,s.ownedContractKey);
    Put(o,s.dailyRealizedLossUsd);
    Put(o,s.estimatedFeesUsd);
    Put(o,DecisionJson(Evaluate(p,c,s,false)));
    return o.str();
}
bool DecodeObservation(const std::string& row,Policy& p,Contract& c,Snapshot& s,std::string& expected,std::string& reason){
    if(row.size()>32768 || row.find_first_of("\r\n")!=std::string::npos){reason="OPTION_CAPTURE_RECORD_SIZE";return false;}
    std::vector<std::string> f;std::size_t start=0;
    for(;;){const auto n=row.find('\t',start);f.push_back(row.substr(start,n==std::string::npos?n:n-start));if(n==std::string::npos)break;start=n+1;}
    if(f.size()!=82 || f[0]!="H0D1"){reason="OPTION_CAPTURE_SCHEMA";return false;}
    if(
        !Get(f[1],c.conId) ||
        !Get(f[2],c.underlyingConId) ||
        !Get(f[3],c.ref.symbol) ||
        !Get(f[4],c.ref.secType) ||
        !Get(f[5],c.ref.exchange) ||
        !Get(f[6],c.ref.primaryExchange) ||
        !Get(f[7],c.ref.currency) ||
        !Get(f[8],c.ref.lastTradeDateOrContractMonth) ||
        !Get(f[9],c.ref.right) ||
        !Get(f[10],c.ref.strike) ||
        !Get(f[11],c.ref.multiplier) ||
        !Get(f[12],c.ref.tradingClass) ||
        !Get(f[13],c.ref.localSymbol) ||
        !Get(f[14],c.session.tradingDate) ||
        !Get(f[15],c.session.openMs) ||
        !Get(f[16],c.session.lastTradeMs) ||
        !Get(f[17],c.session.brokerScheduleVerified) ||
        !Get(f[18],c.european) ||
        !Get(f[19],c.cashSettled) ||
        !Get(f[20],c.pmSettled) ||
        !Get(f[21],p.account) ||
        !Get(f[22],p.owner) ||
        !Get(f[23],p.maxContracts) ||
        !Get(f[24],p.maxPremiumUsd) ||
        !Get(f[25],p.maxDailyLossUsd) ||
        !Get(f[26],p.maxQuoteAgeMs) ||
        !Get(f[27],p.maxGreeksAgeMs) ||
        !Get(f[28],p.entryCutoffBeforeCloseMs) ||
        !Get(f[29],p.exitBeforeCloseMs) ||
        !Get(f[30],p.maxAbsoluteSpread) ||
        !Get(f[31],p.maxRelativeSpread) ||
        !Get(f[32],s.nowMs) ||
        !Get(f[33],s.epoch) ||
        !Get(f[34],s.account) ||
        !Get(f[35],s.owner) ||
        !Get(f[36],s.executionDomain) ||
        !Get(f[37],s.option.marketDataType) ||
        !Get(f[38],s.option.entitlementDenied) ||
        !Get(f[39],s.underlyingDataType) ||
        !Get(f[40],s.option.bid.value) ||
        !Get(f[41],s.option.bid.observedMs) ||
        !Get(f[42],s.option.bid.epoch) ||
        !Get(f[43],s.option.bid.valid) ||
        !Get(f[44],s.option.ask.value) ||
        !Get(f[45],s.option.ask.observedMs) ||
        !Get(f[46],s.option.ask.epoch) ||
        !Get(f[47],s.option.ask.valid) ||
        !Get(f[48],s.option.bidSize.value) ||
        !Get(f[49],s.option.bidSize.observedMs) ||
        !Get(f[50],s.option.bidSize.epoch) ||
        !Get(f[51],s.option.bidSize.valid) ||
        !Get(f[52],s.option.askSize.value) ||
        !Get(f[53],s.option.askSize.observedMs) ||
        !Get(f[54],s.option.askSize.epoch) ||
        !Get(f[55],s.option.askSize.valid) ||
        !Get(f[56],s.underlying.value) ||
        !Get(f[57],s.underlying.observedMs) ||
        !Get(f[58],s.underlying.epoch) ||
        !Get(f[59],s.underlying.valid) ||
        !Get(f[60],s.greeks.delta) ||
        !Get(f[61],s.greeks.gamma) ||
        !Get(f[62],s.greeks.theta) ||
        !Get(f[63],s.greeks.vega) ||
        !Get(f[64],s.greeks.iv) ||
        !Get(f[65],s.greeks.underlying) ||
        !Get(f[66],s.greeks.observedMs) ||
        !Get(f[67],s.greeks.epoch) ||
        !Get(f[68],s.greeks.valid) ||
        !Get(f[69],s.accountComplete) ||
        !Get(f[70],s.positionsComplete) ||
        !Get(f[71],s.ordersComplete) ||
        !Get(f[72],s.tradingPermissionVerified) ||
        !Get(f[73],s.marginPreviewVerified) ||
        !Get(f[74],s.killNewEntries) ||
        !Get(f[75],s.unresolvedSend) ||
        !Get(f[76],s.ownedContracts) ||
        !Get(f[77],s.ownedActiveOrders) ||
        !Get(f[78],s.ownedContractKey) ||
        !Get(f[79],s.dailyRealizedLossUsd) ||
        !Get(f[80],s.estimatedFeesUsd)) {reason="OPTION_CAPTURE_VALUE";return false;}
    expected=f.back();
    try {if(EncodeObservation(p,c,s)!=row){reason="OPTION_CAPTURE_REPLAY_MISMATCH";return false;}} catch(...) {reason="OPTION_CAPTURE_REENCODE_FAILED";return false;}
    reason.clear();return true;
}
} }
