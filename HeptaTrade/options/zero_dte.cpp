#include "zero_dte.h"

#include <algorithm>
#include <cmath>
#include <ctime>
#include <iomanip>
#include <limits>
#include <locale>
#include <sstream>
#include <stdexcept>

namespace hepta { namespace options {
namespace {
const std::int64_t hour = 3600000, dayMs = 24 * hour;
bool Finite(double v) { return std::isfinite(v); }
bool Digits(const std::string& s) {
    return !s.empty() && s.find_first_not_of("0123456789") == std::string::npos;
}
bool ParseDay(const std::string& s, int& y, int& m, int& d) {
    if (s.size() != 8 || !Digits(s)) return false;
    y = std::stoi(s.substr(0,4)); m = std::stoi(s.substr(4,2)); d = std::stoi(s.substr(6,2));
    if (y < 2007 || y > 2099 || m < 1 || m > 12 || d < 1) return false;
    const int lengths[] = {31,28,31,30,31,30,31,31,30,31,30,31};
    const bool leap = y % 4 == 0 && (y % 100 != 0 || y % 400 == 0);
    return d <= lengths[m-1] + ((m == 2 && leap) ? 1 : 0);
}
std::int64_t Days(int y, int m, int d) {
    // Gregorian civil date to days since 1970-01-01.
    y -= m <= 2;
    const int era = (y >= 0 ? y : y-399) / 400;
    const unsigned year = static_cast<unsigned>(y-era*400);
    const unsigned doy = (153*(m+(m>2?-3:9))+2)/5 + d-1;
    const unsigned doe = year*365 + year/4 - year/100 + doy;
    return era*146097LL + doe - 719468;
}
int Sunday(int y, int m, int n) {
    const int wd = static_cast<int>((Days(y,m,1)+4)%7);
    return 1 + (7-wd)%7 + (n-1)*7;
}
std::string DateAt(std::int64_t ms) {
    std::time_t t = static_cast<std::time_t>(ms/1000); std::tm tm{};
#if defined(_WIN32)
    if (gmtime_s(&tm,&t) != 0) return {};
#else
    if (!gmtime_r(&t,&tm)) return {};
#endif
    char b[16]; if (!std::strftime(b,sizeof(b),"%Y%m%d",&tm)) return {}; return b;
}
bool Fresh(const Tick& t, std::int64_t now, std::uint64_t epoch, std::int64_t age) {
    return t.valid && epoch > 0 && t.epoch == epoch && Finite(t.value) &&
        t.value > 0 && t.value < 1e8 && t.observedMs > 0 && t.observedMs <= now && now-t.observedMs <= age;
}
void Field(std::ostringstream& out, const std::string& s) { out << s.size() << ':' << s; }
bool Same(const Execution& a, const Execution& b) {
    return a.account==b.account && a.owner==b.owner && a.contractKey==b.contractKey &&
        a.commandId==b.commandId && a.correlationId==b.correlationId &&
        a.executionId==b.executionId && a.side==b.side && a.currency==b.currency &&
        a.contracts==b.contracts && a.cumulativeContracts==b.cumulativeContracts &&
        a.price==b.price && a.cumulativeAveragePrice==b.cumulativeAveragePrice;
}
std::string Family(const std::string& id) {
    const auto dot=id.rfind('.');
    return dot!=std::string::npos && dot>0 && Digits(id.substr(dot+1)) ? id.substr(0,dot) : std::string();
}
}

std::string NewYorkDate(std::int64_t utcMs) {
    const std::string utc = DateAt(utcMs); int y,m,d;
    if (!ParseDay(utc,y,m,d)) return {};
    const auto start=Days(y,3,Sunday(y,3,2))*dayMs+7*hour;
    const auto end=Days(y,11,Sunday(y,11,1))*dayMs+6*hour;
    return DateAt(utcMs-((utcMs>=start && utcMs<end)?4:5)*hour);
}
std::int64_t ExchangeTimestamp(const std::string& date, const std::string& time,
                              const std::string& zone) {
    int y,m,d; if (!ParseDay(date,y,m,d)) return 0;
    std::string s; for (char c:time) if(c!=':') s+=c;
    if ((s.size()!=4 && s.size()!=6) || !Digits(s)) return 0;
    const int h=std::stoi(s.substr(0,2)), n=std::stoi(s.substr(2,2));
    const int sec=s.size()==6?std::stoi(s.substr(4,2)):0;
    if(h>23 || n>59 || sec>59) return 0;
    int offset;
    if(zone=="US/Eastern" || zone=="America/New_York") offset=5;
    else if(zone=="US/Central" || zone=="America/Chicago") offset=6;
    else return 0;
    const int spring=Sunday(y,3,2), autumn=Sunday(y,11,1);
    if ((m==3 && d==spring && h==2) || (m==11 && d==autumn && h==1)) return 0;
    const bool dst=(m>3 && m<11) || (m==3 && (d>spring || (d==spring && h>=3))) ||
        (m==11 && (d<autumn || (d==autumn && h<1)));
    return Days(y,m,d)*dayMs + (h+offset-(dst?1:0))*hour + n*60000LL + sec*1000LL;
}
Session ParseIbSession(const std::string& expiry, const std::string& hours,
                       const std::string& lastTrade, const std::string& zone) {
    Session result; result.tradingDate=expiry;
    const auto last=ExchangeTimestamp(expiry,lastTrade,zone);
    if(!last) return result;
    std::istringstream segments(hours); std::string item;
    while(std::getline(segments,item,';')) {
        const auto dash=item.find('-'); if(dash==std::string::npos) continue;
        const std::string a=item.substr(0,dash), b=item.substr(dash+1);
        if(a.size()<13 || b.size()<13 || a[8]!=':' || b[8]!=':' || a.substr(0,8)!=expiry) continue;
        const auto begin=ExchangeTimestamp(a.substr(0,8),a.substr(9),zone);
        const auto end=ExchangeTimestamp(b.substr(0,8),b.substr(9),zone);
        if(begin>0 && end>begin && last>begin && last<=end && NewYorkDate(last)==expiry) {
            result.openMs=begin; result.lastTradeMs=last; result.brokerScheduleVerified=true; return result;
        }
    }
    return result;
}
std::string Contract::key() const {
    std::ostringstream s; s.imbue(std::locale::classic()); s.precision(17); s << "HOPT1:" << conId << ':' << underlyingConId << ':';
    Field(s,ref.symbol);Field(s,ref.secType);Field(s,ref.exchange);Field(s,ref.primaryExchange);Field(s,ref.currency);
    Field(s,ref.lastTradeDateOrContractMonth);Field(s,ref.right); s << ref.strike << ':';
    Field(s,ref.multiplier);Field(s,ref.tradingClass);Field(s,ref.localSymbol);
    return s.str();
}
std::string ValidateContract(const Contract& c) {
    const auto& r=c.ref; int y,m,d;
    if(c.conId<=0 || c.underlyingConId<=0 || r.secType!="OPT" || r.currency!="USD" ||
        (r.exchange!="SMART" && r.exchange!="CBOE" && r.exchange!="IBUSOPT") ||
        (!r.primaryExchange.empty() && r.primaryExchange!="CBOE") ||
        !((r.symbol=="XSP" && r.tradingClass=="XSP") || (r.symbol=="SPX" && r.tradingClass=="SPXW")))
        return "OPTION_CONTRACT_IDENTITY_INVALID";
    if(!ParseDay(r.lastTradeDateOrContractMonth,y,m,d) || (r.right!="C" && r.right!="P") ||
        !Finite(r.strike) || r.strike<=0 || r.strike>99999.999 ||
        std::fabs(r.strike*1000-std::round(r.strike*1000))>1e-6 || r.multiplier!="100")
        return "OPTION_EXPIRY_STRIKE_MULTIPLIER_INVALID";
    std::ostringstream occ; occ.imbue(std::locale::classic()); occ << std::left << std::setw(6) << r.tradingClass
        << r.lastTradeDateOrContractMonth.substr(2) << r.right << std::right << std::setfill('0')
        << std::setw(8) << static_cast<long long>(std::llround(r.strike*1000));
    if(occ.str()!=r.localSymbol) return "OPTION_LOCAL_SYMBOL_MISMATCH";
    if(!c.european || !c.cashSettled || !c.pmSettled) return "OPTION_SETTLEMENT_STYLE_NOT_ALLOWED";
    if(!c.session.brokerScheduleVerified || c.session.tradingDate!=r.lastTradeDateOrContractMonth ||
        c.session.openMs<=0 || c.session.lastTradeMs<=c.session.openMs ||
        NewYorkDate(c.session.openMs)!=c.session.tradingDate ||
        NewYorkDate(c.session.lastTradeMs)!=c.session.tradingDate)
        return "OPTION_BROKER_SCHEDULE_UNPROVEN";
    return {};
}
Decision Evaluate(const Policy& p,const Contract& c,const Snapshot& s,bool close) {
    Decision d; auto add=[&](const std::string& why){ d.reasons.push_back(why); };
    d.entryCutoffMs=c.session.lastTradeMs-p.entryCutoffBeforeCloseMs;
    d.exitAtMs=c.session.lastTradeMs-p.exitBeforeCloseMs;
    const auto contractError=ValidateContract(c); if(!contractError.empty()) add(contractError);
    if(p.account.size()<3 || p.account.compare(0,2,"DU")!=0 || p.owner.empty() ||
        s.account!=p.account || s.owner!=p.owner || s.executionDomain!="PAPER") add("OPTION_PAPER_OWNER_MISMATCH");
    if(s.nowMs<=0 || NewYorkDate(s.nowMs)!=c.session.tradingDate) add("OPTION_NOT_TODAY_EXPIRY");
    if(s.nowMs<c.session.openMs) add("OPTION_SESSION_NOT_OPEN");
    if(s.nowMs>=c.session.lastTradeMs) add("OPTION_EXPIRY_SETTLEMENT_PENDING");
    if(p.maxQuoteAgeMs<=0 || p.maxGreeksAgeMs<=0 || p.maxContracts!=1 ||
        p.entryCutoffBeforeCloseMs<=p.exitBeforeCloseMs || p.exitBeforeCloseMs<=0 ||
        !Finite(p.maxAbsoluteSpread) || !Finite(p.maxRelativeSpread) ||
        p.maxAbsoluteSpread<=0 || p.maxRelativeSpread<=0) add("OPTION_POLICY_INVALID");
    if(!s.accountComplete || !s.positionsComplete || !s.ordersComplete || !s.epoch) add("OPTION_ACCOUNT_SNAPSHOT_INCOMPLETE");
    if(s.unresolvedSend || s.ownedActiveOrders!=0) add("OPTION_ORIGINAL_COMMAND_UNRESOLVED");
    const auto beforeData=d.reasons.size();
    if(s.option.entitlementDenied) add("OPTION_MARKET_DATA_NOT_SUBSCRIBED");
    if(s.option.marketDataType!=1 || s.underlyingDataType!=1) add("OPTION_REALTIME_DATA_REQUIRED");
    if(!Fresh(s.option.bid,s.nowMs,s.epoch,p.maxQuoteAgeMs)) add("OPTION_BID_STALE_OR_MISSING");
    if(!Fresh(s.option.ask,s.nowMs,s.epoch,p.maxQuoteAgeMs)) add("OPTION_ASK_STALE_OR_MISSING");
    if(!Fresh(s.option.bidSize,s.nowMs,s.epoch,p.maxQuoteAgeMs) ||
       !Fresh(s.option.askSize,s.nowMs,s.epoch,p.maxQuoteAgeMs)) add("OPTION_SIZE_STALE_OR_MISSING");
    if(!Fresh(s.underlying,s.nowMs,s.epoch,p.maxQuoteAgeMs)) add("OPTION_UNDERLYING_STALE_OR_MISSING");
    const double spread=s.option.ask.value-s.option.bid.value;
    if(!Finite(spread) || spread<0 || spread>p.maxAbsoluteSpread ||
       (s.option.ask.value>0 && spread/s.option.ask.value>p.maxRelativeSpread)) add("OPTION_SPREAD_REJECTED");
    const auto& g=s.greeks;
    if(!close && (!g.valid || g.epoch!=s.epoch || g.observedMs<=0 || g.observedMs>s.nowMs ||
       s.nowMs-g.observedMs>p.maxGreeksAgeMs || !Finite(g.delta) || !Finite(g.gamma) ||
       !Finite(g.theta) || !Finite(g.vega) || !Finite(g.iv) || !Finite(g.underlying) ||
       g.gamma<0 || g.gamma>=1e6 || g.vega<0 || g.vega>=1e6 || std::fabs(g.theta)>=1e6 || g.iv<0 || g.iv>5 || g.underlying<=0 ||
       (c.ref.right=="C" ? (g.delta<0 || g.delta>1) : (g.delta>0 || g.delta< -1)))) add("OPTION_GREEKS_UNPROVEN");
    d.marketDataReady=beforeData==d.reasons.size();
    if(close) {
        if(s.ownedContracts<=0 || s.ownedContractKey!=c.key()) add("OPTION_EXACT_REDUCE_ONLY_OWNER_MISMATCH");
    } else {
        if(s.ownedContracts!=0) add("OPTION_FLAT_BEFORE_OPEN_REQUIRED");
        if(s.killNewEntries) add("OPTION_NEW_ENTRIES_DISABLED");
        if(!s.tradingPermissionVerified) add("OPTION_TRADING_PERMISSION_UNVERIFIED");
        if(!s.marginPreviewVerified) add("OPTION_MARGIN_PREVIEW_UNVERIFIED");
        if(s.nowMs>=d.entryCutoffMs) add("OPTION_ENTRY_CUTOFF_REACHED");
        if(!Finite(p.maxPremiumUsd) || p.maxPremiumUsd<=0 || !Finite(p.maxDailyLossUsd) || p.maxDailyLossUsd<=0)
            add("OPTION_RISK_BUDGET_UNSET");
        if(!Finite(s.dailyRealizedLossUsd) || s.dailyRealizedLossUsd<0 || s.dailyRealizedLossUsd>=p.maxDailyLossUsd)
            add("OPTION_DAILY_LOSS_LIMIT");
        d.estimatedPremiumUsd=s.option.ask.value*100;
        d.estimatedCostUsd=d.estimatedPremiumUsd+s.estimatedFeesUsd;
        if(!Finite(s.estimatedFeesUsd) || s.estimatedFeesUsd<0 || !Finite(d.estimatedCostUsd) ||
            d.estimatedCostUsd<=0 || d.estimatedCostUsd>p.maxPremiumUsd ||
            d.estimatedCostUsd+s.dailyRealizedLossUsd>p.maxDailyLossUsd) add("OPTION_PREMIUM_BUDGET_EXCEEDED");
    }
    d.eligible=d.reasons.empty();
    if(d.eligible) d.action=close?"SELL_TO_CLOSE":(c.ref.right=="C"?"BUY_CALL":"BUY_PUT");
    return d;
}
std::string EscapeJson(const std::string& value) {
    std::ostringstream o; o.imbue(std::locale::classic());
    for(unsigned char c:value) {
        if(c=='"' || c=='\\') o << '\\' << c;
        else if(c<32) o << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<unsigned>(c) << std::dec;
        else o << c;
    }
    return o.str();
}
std::string DecisionJson(const Decision& d) {
    std::ostringstream o; o.imbue(std::locale::classic()); o.precision(17);
    o << "{\"broker_authorized\":false,\"market_data_ready\":" << (d.marketDataReady?"true":"false")
      << ",\"eligible\":" << (d.eligible?"true":"false") << ",\"action\":\"" << d.action
      << "\",\"entry_cutoff_ms\":" << d.entryCutoffMs << ",\"exit_at_ms\":" << d.exitAtMs
      << ",\"estimated_premium_usd\":" << (Finite(d.estimatedPremiumUsd)?d.estimatedPremiumUsd:0)
      << ",\"estimated_cost_usd\":" << (Finite(d.estimatedCostUsd)?d.estimatedCostUsd:0) << ",\"reasons\":[";
    for(std::size_t i=0;i<d.reasons.size();++i) { if(i)o<<',';o<<'"'<<EscapeJson(d.reasons[i])<<'"'; }
    return o.str()+"]}";
}

EvidenceLedger::EvidenceLedger(const Contract& c,const std::string& a,const std::string& o)
    :contract_(c),account_(a),owner_(o) {
    failure_=ValidateContract(c);
    if(a.size()<3 || a.compare(0,2,"DU") || o.empty()) failure_="OPTION_PAPER_OWNER_MISMATCH";
}
bool EvidenceLedger::Fail(const char* s) { failure_=s; return false; }
bool EvidenceLedger::ExecutionObserved(const Execution& e) {
    if(!failure_.empty()) return false;
    if(e.account!=account_ || e.owner!=owner_ || e.contractKey!=contract_.key() || e.currency!="USD" ||
        e.commandId.empty() || e.correlationId.empty() || e.executionId.empty() ||
        (e.side!="BUY" && e.side!="SELL") || e.contracts<=0 || e.contracts>100000 ||
        e.cumulativeContracts<e.contracts || e.cumulativeContracts>100000 ||
        !Finite(e.price) || e.price<=0 || e.price>=1e8 || !Finite(e.cumulativeAveragePrice) || e.cumulativeAveragePrice<=0 || e.cumulativeAveragePrice>=1e8)
        return Fail("OPTION_EXECUTION_IDENTITY_OR_ECONOMICS_INVALID");
    const auto same=executions_.find(e.executionId);
    if(same!=executions_.end()) return Same(same->second,e) || Fail("OPTION_EXECUTION_REPLAY_CONFLICT");
    // A broker download after settlement may replay the exact original fill.
    // It is the same evidence, not a new post-settlement trade.
    if(!settlementId_.empty()) return Fail("OPTION_EXECUTION_AFTER_SETTLEMENT");
    if(executions_.size()>=512) return Fail("OPTION_EXECUTION_LEDGER_CAPACITY");
    for(const auto& item:executions_) {
        const auto& x=item.second;
        if(!Family(e.executionId).empty() && Family(e.executionId)==Family(x.executionId))
            return Fail("OPTION_CORRECTION_REQUIRES_RECONCILIATION");
        if(e.side=="BUY" && x.side=="BUY" && e.commandId!=x.commandId)
            return Fail("OPTION_SINGLE_ENTRY_EPISODE_REQUIRED");
        if(x.correlationId==e.correlationId && x.commandId!=e.commandId)
            return Fail("OPTION_CORRELATION_COMMAND_CONFLICT");
        if(x.commandId==e.commandId && (x.correlationId!=e.correlationId || x.side!=e.side))
            return Fail("OPTION_COMMAND_EXECUTION_IDENTITY_CONFLICT");
    }
    executions_.emplace(e.executionId,e); return true;
}
bool EvidenceLedger::FeeObserved(const std::string& id,double fee,const std::string& currency) {
    if(!failure_.empty()) return false;
    if(id.empty() || currency!="USD" || !Finite(fee) || std::fabs(fee)>1000000) return Fail("OPTION_FEE_INVALID");
    const auto old=fees_.find(id);
    if(old!=fees_.end()) return old->second==fee || Fail("OPTION_FEE_REPLAY_CONFLICT");
    if(fees_.size()>=512) return Fail("OPTION_FEE_LEDGER_CAPACITY");
    fees_[id]=fee; return true;
}
bool EvidenceLedger::TerminalObserved(const Terminal& t) {
    if(!failure_.empty()) return false;
    if(!t.authoritative || t.commandId.empty() || t.filledContracts<0 || t.filledContracts>100000 ||
       (t.status!="Filled" && t.status!="Cancelled" && t.status!="ApiCancelled" && t.status!="Rejected"))
        return Fail("OPTION_TERMINAL_UNPROVEN");
    const auto old=terminals_.find(t.commandId);
    if(old!=terminals_.end() && (old->second.status!=t.status || old->second.filledContracts!=t.filledContracts))
        return Fail("OPTION_TERMINAL_CONFLICT");
    if(old==terminals_.end() && terminals_.size()>=512) return Fail("OPTION_TERMINAL_LEDGER_CAPACITY");
    terminals_[t.commandId]=t; return true;
}
bool EvidenceLedger::ExecutionSetComplete() const {
    if(!failure_.empty() || executions_.empty()) return false;
    std::map<std::string,std::vector<const Execution*>> commands;
    for(const auto& item:executions_) commands[item.second.commandId].push_back(&item.second);
    // Completion is an exact join. A terminal with positive fills but no
    // execution must not disappear from the proof merely because it arrived first.
    for(const auto& item:terminals_)
        if(item.second.filledContracts>0 && !commands.count(item.first)) return false;
    for(auto& item:commands) {
        auto& fills=item.second;
        std::sort(fills.begin(),fills.end(),[](const Execution* a,const Execution* b){
            return a->cumulativeContracts<b->cumulativeContracts;
        });
        int quantity=0; double cost=0;
        // Validate every cumulative checkpoint, not just the largest one.
        // Arrival order is irrelevant; missing, overlapping or inconsistent
        // increments keep the original command in reconciliation.
        for(const Execution* fill:fills) {
            quantity+=fill->contracts; cost+=fill->contracts*fill->price;
            if(quantity!=fill->cumulativeContracts ||
               std::fabs(cost-quantity*fill->cumulativeAveragePrice)>1e-6*std::max(1.0,cost)) return false;
        }
        const auto terminal=terminals_.find(item.first);
        if(terminal==terminals_.end() || terminal->second.filledContracts!=quantity) return false;
    }
    return KnownPosition()>=0;
}
bool EvidenceLedger::FeesComplete() const {
    if(!failure_.empty() || executions_.empty() || fees_.size()!=executions_.size()) return false;
    for(const auto& p:executions_) if(!fees_.count(p.first)) return false;
    return true;
}
int EvidenceLedger::KnownPosition() const {
    if(!settlementId_.empty()) return 0;
    int q=0; for(const auto& p:executions_) q+=(p.second.side=="BUY"?1:-1)*p.second.contracts; return q;
}
double EvidenceLedger::GrossPnl() const {
    if(!ExecutionSetComplete()) return std::numeric_limits<double>::quiet_NaN();
    int bought=0,sold=0;double cost=0,proceeds=0;
    for(const auto& p:executions_) {
        const auto& e=p.second;
        if(e.side=="BUY"){bought+=e.contracts;cost+=e.contracts*e.price*100;}
        else {sold+=e.contracts;proceeds+=e.contracts*e.price*100;}
    }
    if(!bought || sold>bought) return std::numeric_limits<double>::quiet_NaN();
    return settlementId_.empty() ? proceeds-cost*sold/bought : proceeds+settlementCash_-cost;
}
double EvidenceLedger::NetPnl() const {
    if(!FeesComplete()) return std::numeric_limits<double>::quiet_NaN();
    double fee=settlementFee_; for(const auto& p:executions_) fee+=fees_.at(p.first); return GrossPnl()-fee;
}
bool EvidenceLedger::CashSettlementObserved(const std::string& id,double cash,double fee,
    bool flat,bool complete,std::int64_t observed) {
    if(!failure_.empty()) return false;
    if(id.empty() || !Finite(cash) || cash<0 || !Finite(fee) || fee<0 || !flat || !complete ||
        observed<contract_.session.lastTradeMs || !ExecutionSetComplete())
        return Fail("OPTION_CASH_SETTLEMENT_UNPROVEN");
    if(!settlementId_.empty()) return (settlementId_==id && settlementCash_==cash && settlementFee_==fee) || Fail("OPTION_SETTLEMENT_CONFLICT");
    if(KnownPosition()<=0) return Fail("OPTION_SETTLEMENT_WITHOUT_POSITION");
    settlementId_=id;settlementCash_=cash;settlementFee_=fee; return true;
}
bool EvidenceLedger::RoundTripProven(const Snapshot& audit) const {
    if(audit.account!=account_ || audit.owner!=owner_ || audit.executionDomain!="PAPER" ||
       audit.ownedContractKey!=contract_.key() || !audit.epoch || audit.nowMs<=0 ||
       !audit.accountComplete || !audit.positionsComplete || !audit.ordersComplete ||
       audit.ownedContracts!=0 || audit.ownedActiveOrders!=0 || audit.unresolvedSend ||
       KnownPosition()!=0 || !ExecutionSetComplete() || !FeesComplete() || !settlementId_.empty()) return false;
    bool buy=false,sell=false;for(const auto& p:executions_){buy|=p.second.side=="BUY";sell|=p.second.side=="SELL";}
    return buy && sell;
}
std::string EvidenceLedger::State(std::int64_t now) const {
    if(!failure_.empty())return failure_;
    if(!settlementId_.empty() && !ExecutionSetComplete())return "EXECUTION_RECONCILIATION_PENDING";
    if(!settlementId_.empty())return FeesComplete()?"CASH_SETTLED":"SETTLED_FEES_PENDING";
    if(executions_.empty())return "NO_EXECUTIONS";
    if(!ExecutionSetComplete())return "EXECUTION_RECONCILIATION_PENDING";
    if(KnownPosition()>0)return now>=contract_.session.lastTradeMs?"EXPIRY_SETTLEMENT_PENDING":"OPEN";
    return FeesComplete()?"FLAT_BROKER_AUDIT_REQUIRED":"FLAT_FEES_PENDING";
}

} }
