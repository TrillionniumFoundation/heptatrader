#include "snowball_option_feed.h"
#include <algorithm>
#include <cmath>
#include <iomanip>
#include <limits>
#include <locale>
#include <sstream>

namespace hepta { namespace options {
namespace {
bool Digits(const std::string& s) { return !s.empty() && s.find_first_not_of("0123456789")==std::string::npos; }
bool Safe(const std::string& s) {
    if(s.empty() || s.size()>256) return false;
    for(unsigned char c:s) if(c<32 || c==127) return false;
    return true;
}
std::string Occ(const CanonicalOptionIdentity& c) {
    std::ostringstream out;out.imbue(std::locale::classic());
    out << std::left << std::setw(6) << c.tradingClass << c.expiry.substr(2) << c.right
        << std::right << std::setfill('0') << std::setw(8) << c.strikeMilli;
    return out.str();
}
bool FreshSource(const SourceStamp& t, std::int64_t now, std::int64_t mono, std::int64_t ttl) {
    // No replacement of vendor time with local receipt/heartbeat time.
    return ttl>0 && now>0 && mono>0 && t.sourceMs>0 && t.sourceMs<=t.receivedMs &&
        t.receivedMs<=now && now-t.sourceMs<=ttl && t.receivedMonotonicMs>0 &&
        t.receivedMonotonicMs<=mono && mono-t.receivedMonotonicMs<=ttl;
}
bool SameStamp(const SourceStamp& a,const SourceStamp& b) {
    return a.sourceMs==b.sourceMs && a.receivedMs==b.receivedMs && a.receivedMonotonicMs==b.receivedMonotonicMs;
}
bool SameTick(const Tick& a,const Tick& b) {
    return a.value==b.value && a.valid==b.valid && a.epoch==b.epoch && a.observedMs==b.observedMs;
}
bool SameFrame(const SnowballOptionFrame& a,const SnowballOptionFrame& b) {
    const Tick at[]={a.option.bid,a.option.ask,a.option.bidSize,a.option.askSize,a.underlying};
    const Tick bt[]={b.option.bid,b.option.ask,b.option.bidSize,b.option.askSize,b.underlying};
    const SourceStamp as[]={a.bid,a.ask,a.bidSize,a.askSize,a.underlyingStamp,a.greeksStamp};
    const SourceStamp bs[]={b.bid,b.ask,b.bidSize,b.askSize,b.underlyingStamp,b.greeksStamp};
    for(int i=0;i<5;++i)if(!SameTick(at[i],bt[i]))return false;
    for(int i=0;i<6;++i)if(!SameStamp(as[i],bs[i]))return false;
    const auto& g=a.greeks;const auto& h=b.greeks;
    return a.option.marketDataType==b.option.marketDataType && a.option.entitlementDenied==b.option.entitlementDenied &&
        g.valid==h.valid && g.epoch==h.epoch && g.observedMs==h.observedMs && g.delta==h.delta &&
        g.gamma==h.gamma && g.theta==h.theta && g.vega==h.vega && g.iv==h.iv && g.underlying==h.underlying;
}
bool TimesAdvance(const SnowballOptionFrame& a,const SnowballOptionFrame& b) {
    const SourceStamp current[]={a.bid,a.ask,a.bidSize,a.askSize,a.underlyingStamp,a.greeksStamp};
    const SourceStamp previous[]={b.bid,b.ask,b.bidSize,b.askSize,b.underlyingStamp,b.greeksStamp};
    for(int i=0;i<6;++i)if(current[i].sourceMs<previous[i].sourceMs || current[i].receivedMs<previous[i].receivedMs ||
                          current[i].receivedMonotonicMs<previous[i].receivedMonotonicMs)return false;
    return true;
}
bool StampMatches(const Tick& t,const SourceStamp& stamp,std::uint64_t epoch) {
    return t.epoch==epoch && t.observedMs==stamp.sourceMs;
}
}
std::string CanonicalOptionIdentity::Key() const {
    std::ostringstream out;out.imbue(std::locale::classic());
    out << "HCOPT1|" << symbol << '|' << underlyingType << '|' << currency << '|' << expiry << '|'
        << right << '|' << strikeMilli << '|' << multiplier << '|' << tradingClass << '|' << localSymbol;
    return out.str();
}
std::string ValidateCanonicalOption(const CanonicalOptionIdentity& c) {
    if(!((c.symbol=="XSP" && c.tradingClass=="XSP") || (c.symbol=="SPX" && c.tradingClass=="SPXW")) ||
       c.underlyingType!="IND" || c.currency!="USD") return "SNOWBALL_OPTION_PRODUCT_UNSUPPORTED";
    if(c.expiry.size()!=8 || !Digits(c.expiry) || !ExchangeTimestamp(c.expiry,"1200","US/Eastern") ||
       (c.right!="C" && c.right!="P") || c.multiplier!=100 || c.strikeMilli<=0 || c.strikeMilli>99999999)
        return "SNOWBALL_OPTION_IDENTITY_INVALID";
    if(c.localSymbol!=Occ(c)) return "SNOWBALL_OPTION_OCC_MISMATCH";
    return {};
}
bool ParseCanonicalOcc(const std::string& value,CanonicalOptionIdentity& out,std::string& reason) {
    // Fixed-width OCC identity. Do not strip arbitrary spaces or guess aliases.
    if(value.size()!=21 || !Digits(value.substr(6,6)) || !Digits(value.substr(13,8))) {
        reason="SNOWBALL_OCC_FORMAT_INVALID";return false;
    }
    CanonicalOptionIdentity c;c.localSymbol=value;c.tradingClass=value.substr(0,6);
    while(!c.tradingClass.empty() && c.tradingClass.back()==' ')c.tradingClass.pop_back();
    c.symbol=c.tradingClass=="SPXW"?"SPX":c.tradingClass;
    c.expiry="20"+value.substr(6,6);c.right=value.substr(12,1);c.multiplier=100;
    c.strikeMilli=std::stoll(value.substr(13,8));reason=ValidateCanonicalOption(c);
    if(!reason.empty())return false;out=c;return true;
}
std::string MatchBrokerOption(const CanonicalOptionIdentity& a,const Contract& b) {
    auto reason=ValidateCanonicalOption(a);if(!reason.empty())return reason;
    reason=ValidateContract(b);if(!reason.empty())return reason;
    const auto& r=b.ref;
    if(r.symbol!=a.symbol || r.secType!="OPT" || r.currency!=a.currency || r.lastTradeDateOrContractMonth!=a.expiry ||
       r.right!=a.right || r.multiplier!=std::to_string(a.multiplier) || r.tradingClass!=a.tradingClass ||
       r.localSymbol!=a.localSymbol || !std::isfinite(r.strike) || std::fabs(r.strike*1000-a.strikeMilli)>1e-6)
        return "SNOWBALL_IB_CONTRACT_IDENTITY_MISMATCH";
    return {};
}
bool SnowballOptionFeed::BeginEpoch(std::uint64_t epoch,const std::string& session,
    const SnowballFeedAuthority& authority,std::string& reason) {
    if(!epoch || epoch<=epoch_ || !Safe(session) || session==sessionIdentity_) {
        reason="SNOWBALL_NEW_FEED_EPOCH_REQUIRED";return false;
    }
    epoch_=epoch;sessionIdentity_=session;authority_=authority;frame_=SnowballOptionFrame();
    haveFrame_=false;connected_=true;fault_.clear();reason.clear();return true;
}
void SnowballOptionFeed::Disconnect() {
    connected_=false;haveFrame_=false;frame_=SnowballOptionFrame();fault_="SNOWBALL_FEED_DISCONNECTED";
}
bool SnowballOptionFeed::Observe(const SnowballOptionFrame& frame,std::string& reason) {
    if(!connected_ || !fault_.empty()){reason=fault_.empty()?"SNOWBALL_FEED_DISCONNECTED":fault_;return false;}
    // A stale callback cannot invalidate a newer connection or refresh its data.
    if(frame.feedEpoch!=epoch_){reason="SNOWBALL_STALE_FEED_EPOCH";return false;}
    auto fail=[&](const std::string& error){fault_=reason=error;haveFrame_=false;return false;};
    const auto identity=ValidateCanonicalOption(frame.identity);if(!identity.empty())return fail(identity);
    if(!Safe(frame.vendorContractId) || !frame.sequence || !frame.fullSnapshot || !frame.realtime)
        return fail("SNOWBALL_FULL_REALTIME_SNAPSHOT_REQUIRED");
    if(haveFrame_ && (frame.identity.Key()!=frame_.identity.Key() || frame.vendorContractId!=frame_.vendorContractId))
        return fail("SNOWBALL_CONTRACT_CHANGED_WITHIN_FEED");
    if(haveFrame_ && frame.sequence==frame_.sequence && !SameFrame(frame,frame_))
        return fail("SNOWBALL_SEQUENCE_REPLAY_CONFLICT");
    if(haveFrame_ && frame.sequence<=frame_.sequence){reason="SNOWBALL_DUPLICATE_OR_REORDERED_SEQUENCE";return false;}
    if(haveFrame_ && !TimesAdvance(frame,frame_))return fail("SNOWBALL_SOURCE_TIME_REGRESSION");
    if(haveFrame_ && frame.sequence!=frame_.sequence+1)return fail("SNOWBALL_SEQUENCE_GAP_RESYNC_REQUIRED");
    if(!StampMatches(frame.option.bid,frame.bid,epoch_) || !StampMatches(frame.option.ask,frame.ask,epoch_) ||
       !StampMatches(frame.option.bidSize,frame.bidSize,epoch_) || !StampMatches(frame.option.askSize,frame.askSize,epoch_) ||
       !StampMatches(frame.underlying,frame.underlyingStamp,epoch_) || frame.greeks.epoch!=epoch_ ||
       frame.greeks.observedMs!=frame.greeksStamp.sourceMs)
        return fail("SNOWBALL_SOURCE_TIMESTAMP_OR_EPOCH_MISMATCH");
    frame_=frame;haveFrame_=true;reason.clear();return true;
}
Decision SnowballOptionFeed::Evaluate(const Policy& policy,const BrokerOptionBinding& binding,
    const Snapshot& broker,std::int64_t mono,bool reduceOnly) const {
    // Start with Broker state, then overwrite only the market-data view. The
    // account/owner/position/order/permission fields never come from the feed.
    Snapshot normalized=broker;normalized.marketDataSource="SNOWBALL";normalized.marketDataEpoch=epoch_;
    normalized.option=Book();normalized.underlying=Tick();normalized.greeks=Greeks();normalized.underlyingDataType=0;
    if(haveFrame_){normalized.option=frame_.option;normalized.underlying=frame_.underlying;normalized.greeks=frame_.greeks;normalized.underlyingDataType=1;}
    auto decision=hepta::options::Evaluate(policy,binding.contract,normalized,reduceOnly);
    auto reject=[&](const std::string& why){decision.reasons.push_back(why);decision.eligible=false;decision.marketDataReady=false;decision.action="NO_TRADE";};
    if(!connected_ || !haveFrame_)reject(fault_.empty()?"SNOWBALL_NO_CURRENT_FRAME":fault_);
    if(!binding.qualified || !binding.brokerEpoch || binding.brokerEpoch!=broker.epoch)
        reject("SNOWBALL_IB_QUALIFICATION_EPOCH_UNPROVEN");
    const auto& auth=authority_;
    if(auth.provider!="SNOWBALL" || !Safe(auth.receiptId) || !Safe(auth.endpointIdentity) || !Safe(auth.accountSubject) ||
       !auth.apiUseVerified || !auth.realtimeOptions || !auth.productCoverage || !auth.realtimeUnderlying ||
       auth.verifiedAtMs<=0 || auth.verifiedAtMs>broker.nowMs || auth.validUntilMs<=broker.nowMs)
        reject("SNOWBALL_API_REALTIME_AUTHORITY_UNVERIFIED");
    if(haveFrame_){
        const auto identity=MatchBrokerOption(frame_.identity,binding.contract);if(!identity.empty())reject(identity);
        if(auth.productClass!=frame_.identity.tradingClass || auth.underlyingSymbol!=frame_.identity.symbol)
            reject("SNOWBALL_EXACT_PRODUCT_COVERAGE_UNVERIFIED");
        const SourceStamp stamps[]={frame_.bid,frame_.ask,frame_.bidSize,frame_.askSize,frame_.underlyingStamp};
        const char* names[]={"BID","ASK","BID_SIZE","ASK_SIZE","UNDERLYING"};
        for(int i=0;i<5;++i)if(!FreshSource(stamps[i],broker.nowMs,mono,policy.maxQuoteAgeMs))
            reject(std::string("SNOWBALL_")+names[i]+"_SOURCE_OR_RECEIVE_STALE");
        if(!reduceOnly && !FreshSource(frame_.greeksStamp,broker.nowMs,mono,policy.maxGreeksAgeMs))
            reject("SNOWBALL_GREEKS_SOURCE_OR_RECEIVE_STALE");
    }
    return decision;
}
} }
