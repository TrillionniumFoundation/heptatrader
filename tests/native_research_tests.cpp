#include "../strategies/native_research/market.h"
#include "../strategies/native_research/replay.h"
#include "../strategies/native_research/signal.h"
#include <cmath>
#include <functional>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
using namespace hepta::research;
namespace {
unsigned checks = 0;
void Check(bool ok, const char* name) { ++checks; if (!ok) throw std::runtime_error(name); }
void Near(double a, double b, const char* name) { Check(std::abs(a-b) < 1e-9, name); }
void Reject(const std::function<void()>& f, const char* name) {
    ++checks; bool rejected = false;
    try { f(); } catch (const std::exception&) { rejected = true; }
    if (!rejected) throw std::runtime_error(name);
}
Tick T(std::int64_t t, double p, std::uint64_t v, int day=20260922) {
    Tick x; x.instrument="TEST"; x.tradingDay=day; x.timestampUs=t; x.price=p; x.cumulativeVolume=v; return x;
}
Session S(std::int64_t begin, std::int64_t end, int day=20260922) {
    Session s; s.beginUs=begin; s.endUs=end; s.tradingDay=day; return s;
}
Bar B(std::int64_t begin, double high, double low, double close) {
    Bar b; b.instrument="TEST"; b.tradingDay=20260922; b.beginUs=begin; b.endUs=begin+1000000;
    b.open=close; b.high=high; b.low=low; b.close=close; b.complete=true; return b;
}
void TestCsv() {
    const std::string h="instrument,trading_day,timestamp_us,price,cumulative_volume\r\n";
    std::istringstream good(h+"TEST,20260922,1000000,100.25,123\r\nTEST,20260922,2000000,1.01e2,130");
    TickCsvReader csv(good); Tick t;
    Check(csv.Next(t),"csv first"); Near(t.price,100.25,"csv price"); Check(t.cumulativeVolume==123,"csv volume");
    Check(csv.Next(t),"csv final no newline"); Near(t.price,101,"csv exponent"); Check(!csv.Next(t),"csv eof");
    for (const std::string row : {"TEST,20260229,1,10,0", "TEST,20260922,0,10,0",
        "TEST,20260922,1,nan,0", "TEST,20260922,1,10,-1", "TEST,20260922,1,10,18446744073709551616",
        "TEST,20260922,9223372036854775808,10,0", "TEST,20260922,1,10junk,0", "TEST,20260922,1, 10,0",
        "TEST,20260922,1,10,0,", "TEST,20260922,1,0,0", "bad name,20260922,1,10,0"}) {
        Reject([&]{ std::istringstream in(h+row); TickCsvReader r(in); Tick out; r.Next(out); },"csv malformed rejection");
    }
    Reject([&]{std::istringstream in("wrong\n"); TickCsvReader r(in);},"csv header");
    Reject([&]{std::istringstream in(h+std::string(4097,'x')); TickCsvReader r(in); Tick out; r.Next(out);},"csv bound");
}
void TestBars() {
    SessionCalendar c({S(1000000,4000000),S(6000000,9000000),S(10000000,13000000,20260923)});
    Reject([]{SessionCalendar c({S(2,1)});},"session reversed");
    Reject([]{SessionCalendar c({S(1,5),S(4,9)});},"session overlap");
    Reject([&]{c.At(4000000,20260922);},"half open session end");
    Reject([&]{c.At(5000000,20260922);},"session lunch gap");
    Reject([&]{c.At(1000000,20260923);},"trading day mismatch");
    BarSeries b("TEST",c,1000000,3); Bar current;
    b.Push(T(1000000,100,100)); b.Push(T(1500000,102,104));
    Check(b.Push(T(1500000,102,104))==TickResult::Duplicate,"duplicate suppressed");
    Reject([&]{b.Push(T(1400000,90,105));},"out of order rejected");
    Reject([&]{b.Push(T(1600000,90,103));},"intraday volume reset rejected");
    b.Push(T(1800000,99,107)); b.Push(T(2000000,101,109));
    Check(b.Closed().size()==1,"bar emitted"); const Bar& first=b.Closed().front();
    Near(first.open,100,"open"); Near(first.high,102,"high"); Near(first.low,99,"low"); Near(first.close,99,"close");
    Check(first.volume==7 && first.complete,"delta not cumulative volume");
    Check(first.highTimeUs==1500000 && first.lowTimeUs==1800000,"extreme timestamps");
    Check(b.Current(current) && current.volume==2,"boundary volume once");
    b.Push(T(6000000,103,110)); Check(b.Closed().size()==2,"no fabricated gap bars");
    b.Push(T(10000000,104,3,20260923)); Check(b.Current(current) && current.volume==0,"new day baseline");
    b.Push(T(11000000,105,8,20260923)); Check(b.Closed().size()==3,"bounded history");
    Reject([&]{b.Finish(1);},"backward finish"); b.Finish(11500000);
    Check(!b.Closed().back().complete,"partial tail explicit"); Check(!b.Current(current),"finished no current");
    Reject([&]{b.Push(T(12000000,106,9,20260923));},"closed stream rejects mutation");
    BarSeries daily("TEST",c,0); daily.Push(T(1000000,100,1)); daily.Push(T(6000000,102,5));
    Check(daily.Closed().empty(),"daily spans lunch"); daily.Finish(9000000);
    Check(daily.Closed().size()==1 && daily.Closed()[0].volume==4,"daily volume");
    BarSeries volume("TEST",c,1000000,4,FirstVolume::FromTradingDayStart);
    volume.Push(T(1000000,10,7)); volume.Finish(2000000); Check(volume.Closed()[0].volume==7,"explicit zero baseline");
    const std::int64_t hi=std::numeric_limits<std::int64_t>::max();
    BarSeries edge("TEST",SessionCalendar({S(hi-10,hi)}),hi);
    edge.Push(T(hi-1,10,0)); edge.Finish(hi); Check(edge.Closed()[0].endUs==hi,"time arithmetic no overflow");
}
void TestRangesAndSignal() {
    std::deque<Bar> bars={B(1000000,11,8,10),B(2000000,12,7,11),B(3000000,12,6,10)};
    Check(ExtremeIndex(bars,0,2,true)==0,"newest equal maximum");
    Check(ExtremeIndex(bars,0,2,true,true)==1,"oldest equal maximum");
    Check(ExtremeIndex(bars,0,2,false)==0,"minimum reverse index"); Near(MeanClose(bars,0,2),31.0/3,"mean close");
    Reject([&]{ExtremeIndex(bars,2,1,true);},"range ordering");
    BreakoutSignal strategy("TEST",2,3,1000,5000); BoundedIntent intent;
    Check(!strategy.OnClosedBar(bars[0],2000,intent),"warmup 1");
    Check(!strategy.OnClosedBar(bars[1],3000,intent),"warmup 2");
    Bar breakout=B(3000000,15,12,14);
    Check(strategy.OnClosedBar(breakout,4000,intent),"breakout excludes current high");
    Check(intent.action=="BUY" && intent.quantity==3 && intent.expiresAtMs==9000,"bounded proposal");
    Check(!strategy.OnClosedBar(breakout,4000,intent),"duplicate callback no resignal");
    Bar conflict=breakout; conflict.close=13; Reject([&]{strategy.OnClosedBar(conflict,4000,intent);},"revision conflicts");
    Bar partial=B(4000000,16,14,15); partial.complete=false;
    Reject([&]{strategy.OnClosedBar(partial,5000,intent);},"unfinished bar cannot trade");
    Reject([&]{ValidateIntent(intent,9000);},"intent expiry");
    BoundedIntent invalid=intent; invalid.quantity=std::numeric_limits<double>::infinity();
    Reject([&]{ValidateIntent(invalid,5000);},"nonfinite intent");
}
ReplayOrder O(const std::string& id, std::uint64_t qty, TimeInForce tif=TimeInForce::Resting) {
    ReplayOrder o; o.id=id; o.instrument="TEST"; o.quantity=qty; o.limit=100;
    o.submittedUs=1000000; o.expiresUs=9000000; o.tif=tif; return o;
}
void TestReplay() {
    ReplayMatcher m("TEST"); Check(m.Submit(O("one",5)),"submit"); Check(!m.Submit(O("one",5)),"stable order identity");
    Reject([&]{m.Submit(O("one",6));},"order identity conflict");
    m.Submit(O("two",5,TimeInForce::FAK)); m.Submit(O("three",4,TimeInForce::FOK));
    Check(m.OnTick(T(1000000,99,100)).empty(),"no same tick fill");
    auto f=m.OnTick(T(2000000,99,106)); Check(f.size()==2,"one shared volume budget");
    Check(f[0].quantity==5 && f[1].quantity==1,"insertion priority");
    Check(m.State("one").status==ReplayStatus::Filled,"resting filled");
    Check(m.State("two").status==ReplayStatus::Cancelled,"FAK remainder cancelled");
    Check(m.State("three").status==ReplayStatus::Cancelled,"FOK insufficient budget");
    Check(m.OnTick(T(2000000,99,106)).empty(),"replay duplicate no fills");
    Reject([&]{m.OnTick(T(1500000,99,107));},"replay reorder rejected");
    Check(m.OnTick(T(3000000,99,107)).empty(),"rejected tick did not reset baseline");
    ReplayOrder expire=O("expire",1); expire.submittedUs=3000000; expire.expiresUs=4000000;
    m.Submit(expire); Check(m.OnTick(T(4000000,99,120)).empty(),"expiry precedes matching");
    Check(m.State("expire").status==ReplayStatus::Expired,"expired state");
    Check(!m.Cancel("one"),"terminal cancel no-op");
    Reject([&]{m.Submit(O("backdate",1));},"backdated order refused");
    ReplayMatcher fok("TEST"); fok.Submit(O("fok",2,TimeInForce::FOK)); fok.OnTick(T(1000000,99,0));
    Check(fok.OnTick(T(2000000,99,2)).size()==1,"FOK full fill");
    ReplayMatcher bounded("TEST",1); bounded.Submit(O("a",1)); Reject([&]{bounded.Submit(O("b",1));},"order capacity");
}
ResearchFill F(const std::string& id, Side side, unsigned qty, double price) {
    ResearchFill f; f.id=id; f.orderId="order"; f.instrument="TEST"; f.side=side;
    f.quantity=qty; f.price=price; f.timestampUs=1000000; return f;
}
void TestLedger() {
    ReplayLedger l("TEST",1000,10,1); Check(l.Apply(F("a",Side::Buy,2,100)),"first fill");
    Near(l.Cash(),998,"fees on open"); Near(l.Equity(110),1198,"mark to market");
    Check(!l.Apply(F("a",Side::Buy,2,100)),"duplicate fill not reapplied");
    Reject([&]{l.Apply(F("a",Side::Buy,2,101));},"fill conflict");
    l.Apply(F("b",Side::Sell,3,110)); Near(l.Position(),-1,"position reversal");
    Near(l.AverageEntry(),110,"reversal basis"); Near(l.RealizedPnl(),200,"realized long pnl");
    Near(l.Cash(),1195,"reversal cash and fees"); Near(l.Equity(100),1295,"short mark");
    l.Apply(F("c",Side::Buy,1,100)); Near(l.Position(),0,"flat"); Near(l.Cash(),1294,"closed short pnl");
    Near(l.Fees(),6,"fees no duplicated fills");
    Reject([&]{l.Equity(std::numeric_limits<double>::quiet_NaN());},"NaN mark");
    NetAssetTracker n(1000); n.Observe(1200); n.Funding(600); Near(n.NetAsset(),1.2,"funding no false returns");
    n.Observe(1500); Near(n.NetAsset(),1,"unitized drawdown"); Near(n.MaxDrawdown(),1.0/6,"drawdown reference");
    Reject([&]{n.Funding(-1500);},"full redemption explicit rejection");
    n.Observe(0); Near(n.MaxDrawdown(),1,"total loss"); Reject([&]{n.Funding(10);},"no auto recapitalization");
}
}
int main() {
    try { TestCsv(); TestBars(); TestRangesAndSignal(); TestReplay(); TestLedger();
        std::cout << "native research: " << checks << " checks passed\n"; return 0;
    } catch (const std::exception& e) { std::cerr << "FAIL: " << e.what() << '\n'; return 1; }
}
