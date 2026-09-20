#include "../strategies/native/market_data.h"
#include "../strategies/native/replay.h"
#include "../strategies/native/strategy.h"
#include <cmath>
#include <functional>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>
using namespace hepta::research;
namespace {
#define CHECK(x) do { if (!(x)) throw std::runtime_error(std::string("check failed: ")+ #x); } while (false)
void Reject(const std::function<void()>& f) { bool failed=false; try {f();} catch(const std::exception&) {failed=true;} CHECK(failed); }
void Near(double a,double b) { CHECK(std::fabs(a-b)<=1e-9*std::max(1.0,std::fabs(b))); }
SessionWindow W(const char* day,std::int64_t begin,std::int64_t end) {
    SessionWindow w; w.tradingDay=day; w.beginMs=begin; w.endMs=end; return w;
}
SessionCalendar Calendar() {return SessionCalendar({W("20260921",1000,121000),W("20260921",181000,241000),W("20260922",301000,421000)});}
Tick T(std::int64_t stamp,std::uint64_t seq,double price,std::uint64_t volume) {
    Tick t; t.instrument="TEST.FUT"; t.timestampMs=stamp; t.sequence=seq; t.price=price; t.cumulativeVolume=volume; return t;
}
Bar B(std::int64_t begin,double price) {
    Bar b; b.instrument="TEST.FUT"; b.tradingDay="20260921"; b.beginMs=begin; b.endMs=begin+1000;
    b.open=b.high=b.low=b.close=price; b.complete=true; b.observations=1; b.volume=2; return b;
}
ReplayConfig Config() { ReplayConfig c; c.instrument="TEST.FUT"; c.tickSize=1; c.multiplier=10; c.feePerUnit=1; c.initialEquity=10000; return c; }
ReplayOrder Order(const char* id,int side,std::int64_t qty,double price,ReplayTif tif=ReplayTif::Day) {
    ReplayOrder o; o.id=id; o.side=side; o.quantity=qty; o.limitPrice=price; o.tif=tif; return o;
}
void Sessions() {
    auto c=Calendar(); CHECK(c.At(1000).tradingDay=="20260921"); CHECK(c.At(181000).beginMs==181000);
    CHECK(c.TradingDay("20260921").endMs==241000); Reject([&]{c.At(121000);}); Reject([&]{c.At(0);});
    Reject([]{SessionCalendar({W("20260229",1,10)});});
    SessionCalendar leap({W("20240229",1,10)}); CHECK(leap.At(1).endMs==10);
    Reject([]{SessionCalendar({W("20260921",1,10),W("20260921",9,20)});});
    Reject([]{SessionCalendar({W("20260922",1,10),W("20260921",11,20)});});
}
void Bars() {
    BarBuilder b("TEST.FUT",Calendar(),60000,InitialVolume::Baseline); std::vector<Bar> out;
    b.Push(T(1000,1,100,1000),out); b.Push(T(2000,2,102,1004),out);
    CHECK(b.Push(T(2000,2,102,1004),out)==TickDisposition::Duplicate);
    Reject([&]{b.Push(T(1500,3,99,1005),out);});
    Reject([&]{b.Push(T(3000,2,101,1005),out);});
    Reject([&]{b.Push(T(3000,3,101,999),out);}); CHECK(out.empty());
    b.Push(T(61000,3,101,1007),out); CHECK(out.size()==1 && out[0].complete);
    CHECK(out[0].beginMs==1000 && out[0].endMs==61000 && out[0].volume==4 && out[0].observations==2);
    Near(out[0].open,100); Near(out[0].high,102); Near(out[0].low,100); Near(out[0].close,102);
    b.AdvanceWatermark(121000,out); CHECK(out.size()==2 && out[1].volume==3);
    Reject([&]{b.Push(T(62000,4,101,1008),out);});
    b.Push(T(181000,4,104,1009),out); b.Push(T(301000,1,105,20),out);
    CHECK(out.size()==3 && out[2].volume==2);
    b.Finish(out); CHECK(out.size()==4 && !out[3].complete && out[3].volume==0);
    b.Finish(out); CHECK(out.size()==4); Reject([&]{b.Push(T(302000,2,105,21),out);});
}
void DailyAndEmptyGaps() {
    BarBuilder b("TEST.FUT",Calendar(),0,InitialVolume::IncludeCumulative); std::vector<Bar> out;
    b.Push(T(1000,1,100,5),out); b.Push(T(181000,2,99,8),out); CHECK(out.empty());
    b.AdvanceWatermark(241000,out); CHECK(out.size()==1 && out[0].volume==8 && out[0].endMs==241000);
    b.Push(T(301000,1,98,3),out); b.AdvanceWatermark(421000,out); CHECK(out.size()==2 && out[1].volume==3);
    b.Finish(out); CHECK(out.size()==2);
    BarBuilder gap("TEST.FUT",Calendar(),1000,InitialVolume::Baseline); out.clear();
    gap.Push(T(1000,1,10,1),out); gap.Push(T(61000,2,11,3),out); CHECK(out.size()==1);
}
void OverflowAndCsv() {
    const auto max=std::numeric_limits<std::uint64_t>::max();
    Tick t=T(1000,max,0.12345678901234566,max); auto parsed=ParseTickCsv(FormatTickCsv(t));
    CHECK(parsed.price==t.price && parsed.sequence==max && parsed.cumulativeVolume==max);
    CHECK(ParseTickCsv("TEST.FUT,1000,1,1.25,2\r").price==1.25);
    for (const std::string s:{"TEST.FUT,1,1,nan,0","TEST.FUT,1,1,inf,0","TEST.FUT,1,1,1,18446744073709551616",
        "TEST.FUT,9223372036854775808,1,1,0","TEST.FUT,1,1,1,0,","TEST.FUT,1,1, 1,0","\"TEST.FUT\",1,1,1,0","TEST.FUT,1,0,1,0"})
        Reject([&]{ParseTickCsv(s);});
    Bar a=B(1000,10),b=B(2000,11); a.volume=max; Reject([&]{MergeBars({a,b});});
    SessionCalendar huge({W("20990101",std::numeric_limits<std::int64_t>::max()-100,std::numeric_limits<std::int64_t>::max())});
    BarBuilder builder("TEST.FUT",huge,1000,InitialVolume::Baseline); std::vector<Bar> out;
    builder.Push(T(std::numeric_limits<std::int64_t>::max()-99,1,1,0),out);
    builder.AdvanceWatermark(std::numeric_limits<std::int64_t>::max(),out); CHECK(out.size()==1);
}
void WindowsAndSignals() {
    BarWindow h(3); h.Push(B(1000,10)); h.Push(B(2000,12)); h.Push(B(3000,12));
    CHECK(h.Highest(3)==0 && h.Highest(3,false)==1 && h.Lowest(3)==2); Near(h.MeanClose(3),34.0/3);
    auto merged=MergeBars({B(1000,10),B(2000,12)}); CHECK(merged.volume==4 && merged.complete); Near(merged.close,12);
    auto incomplete=B(4000,13); incomplete.complete=false; Reject([&]{h.Push(incomplete);}); CHECK(h.Size()==3);
    h.Push(B(4000,13)); CHECK(h.Size()==3); Reject([&]{h.Recent(3);});
    CtaSignal signal("test.ma",3,5,false,MovingAverageDecision(1,3,5,false));
    CHECK(signal.OnClosedBar(B(1000,10)).desiredUnits==0);
    CHECK(signal.OnClosedBar(B(2000,12)).desiredUnits==0);
    auto target=signal.OnClosedBar(B(3000,14)); CHECK(target.desiredUnits==5 && target.observationEndMs==4000);
    CHECK(signal.OnClosedBar(B(4000,5)).desiredUnits==-5);
    Reject([&]{signal.OnClosedBar(incomplete);});
    CtaSignal bounded("test.bounds",2,1,true,[](const BarWindow&)->std::int64_t{return -1;});
    Reject([&]{bounded.OnClosedBar(B(1000,10));}); Reject([&]{bounded.OnClosedBar(B(1000,10));});
}
void ReplayFifoAndIdempotency() {
    OfflineReplay r(Config(),Calendar()); auto z=Order("z-first",1,3,101);
    Reject([&]{r.Submit(z);}); r.Push(T(1000,1,100,100));
    r.Submit(z); r.Submit(z); r.Submit(Order("a-second",1,3,101));
    CHECK(r.Push(T(1000,1,100,100)).empty()); CHECK(r.Account().position==0);
    auto fills=r.Push(T(2000,2,100,104)); CHECK(fills.size()==2 && fills[0].orderId=="z-first" && fills[0].quantity==3);
    CHECK(fills[1].quantity==1 && r.Status("a-second").remaining==2); CHECK(r.Account().position==4); Near(r.Account().equity,9996);
    z.quantity=4; Reject([&]{r.Submit(z);}); CHECK(r.Status("z-first").status==ReplayStatus::Filled);
    r.Cancel("a-second"); r.Cancel("a-second"); CHECK(r.Push(T(3000,3,100,110)).empty());
}
void ReplayTifAndDay() {
    OfflineReplay r(Config(),Calendar()); r.Push(T(1000,1,100,100));
    r.Submit(Order("fok",1,5,101,ReplayTif::FillOrKill));
    r.Submit(Order("ioc",1,5,101,ReplayTif::ImmediateOrCancel));
    auto fills=r.Push(T(2000,2,100,103)); CHECK(fills.size()==1 && fills[0].orderId=="ioc" && fills[0].quantity==3);
    CHECK(r.Status("fok").status==ReplayStatus::Expired && r.Status("ioc").remaining==2);
    r.Submit(Order("unmarketable",1,1,90,ReplayTif::ImmediateOrCancel));
    r.Push(T(3000,3,100,104)); CHECK(r.Status("unmarketable").status==ReplayStatus::Expired);
    r.Submit(Order("day",1,1,101)); r.Push(T(301000,1,100,100)); CHECK(r.Status("day").status==ReplayStatus::Expired);
}
void ReplayPnlAndSettlement() {
    OfflineReplay r(Config(),Calendar()); r.Push(T(1000,1,100,0)); r.Submit(Order("buy",1,2,100));
    r.Push(T(2000,2,100,2)); Near(r.Account().equity,9998);
    r.Push(T(3000,3,110,2)); Near(r.Account().equity,10198);
    const auto before=r.Account().equity; r.Settle(110); Near(r.Account().equity,before); Near(r.Account().unrealizedPnl,0);
    r.Submit(Order("reverse",-1,3,105)); r.Push(T(4000,4,105,5));
    CHECK(r.Account().position==-1); Near(r.Account().averagePrice,105); Near(r.Account().realizedPnl,95);
    r.Push(T(5000,5,100,5)); Near(r.Account().equity,10145); Near(r.Account().fees,5);
    Near(r.Account().maximumDrawdown,103);
}
void ReplaySlippageAndTransactionalFailure() {
    auto c=Config(); c.slippageTicks=1; OfflineReplay r(c,Calendar()); r.Push(T(1000,1,100,0));
    r.Submit(Order("slip",1,1,100)); CHECK(r.Push(T(2000,2,100,2)).empty());
    auto fills=r.Push(T(3000,3,99,3)); CHECK(fills.size()==1); Near(fills[0].price,100); Near(r.Account().equity,9989);
    r.Submit(Order("pending",1,1,100));
    Reject([&]{r.Push(T(4000,4,99.5,4));}); CHECK(r.Status("pending").remaining==1);
    Reject([&]{r.Push(T(4000,4,99,1));}); CHECK(r.Push(T(4000,4,99,4)).size()==1);
    auto small=Config(); small.maximumOrders=1; OfflineReplay bounded(small,Calendar()); bounded.Push(T(1000,1,100,0));
    bounded.Submit(Order("one",1,1,100)); Reject([&]{bounded.Submit(Order("two",1,1,100));});
    auto overflow=Config(); overflow.feePerUnit=std::numeric_limits<double>::max(); OfflineReplay bad(overflow,Calendar());
    bad.Push(T(1000,1,100,0)); bad.Submit(Order("fee-overflow",1,2,100));
    Reject([&]{bad.Push(T(2000,2,100,2));}); CHECK(bad.Account().position==0 && bad.Status("fee-overflow").remaining==2);
}
void ProposalPreflight() {
    ProposalLimits l; l.maximumQuantity=10; l.maximumPriceTimesQuantity=1000; l.maximumLifetimeMs=1000;
    BoundedOrderProposal p; p.proposalId="proposal-001"; p.instrument="TEST.FUT"; p.side=1; p.quantity=2; p.limitPrice=100; p.expiresAtMs=2000;
    std::string reason; CHECK(ValidateProposal(p,l,1000,reason)); p.expiresAtMs=1000; CHECK(!ValidateProposal(p,l,1000,reason));
    p.expiresAtMs=2000; p.quantity=11; CHECK(!ValidateProposal(p,l,1000,reason));
    p.quantity=10; p.limitPrice=101; CHECK(!ValidateProposal(p,l,1000,reason));
    p.limitPrice=std::numeric_limits<double>::infinity(); CHECK(!ValidateProposal(p,l,1000,reason));
}
}
int main() {
    const std::pair<const char*,std::function<void()>> tests[]={
        {"sessions",Sessions},{"bars",Bars},{"daily-and-gaps",DailyAndEmptyGaps},{"csv-overflow",OverflowAndCsv},
        {"windows-signals",WindowsAndSignals},{"replay-fifo-idempotency",ReplayFifoAndIdempotency},
        {"replay-tif-day",ReplayTifAndDay},{"replay-pnl-settlement",ReplayPnlAndSettlement},
        {"replay-slippage-transaction",ReplaySlippageAndTransactionalFailure},{"proposal-preflight",ProposalPreflight}};
    for(const auto& t:tests) {try {t.second();std::cout<<"PASS "<<t.first<<'\n';} catch(const std::exception& e){std::cerr<<"FAIL "<<t.first<<": "<<e.what()<<'\n';return 1;}}
    return 0;
}
