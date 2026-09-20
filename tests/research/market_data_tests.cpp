#include "hepta/research/market_data.hpp"
#include <iostream>
#include <limits>
#include <stdexcept>
#include <functional>
using namespace hepta::research;
namespace {
unsigned long checks=0;
void Check(bool b) { ++checks; if(!b) throw std::runtime_error("assertion "+std::to_string(checks)); }
void Reject(const std::function<void()>& f) {
    bool rejected=false; try { f(); } catch(const std::exception&) {rejected=true;} Check(rejected);
}
Tick T(std::int64_t time,std::uint64_t seq,std::int64_t price,std::uint64_t volume,
       std::string day="20260921") {return {"TEST",day,time,price,seq,volume};}
}
#include "series_cases.hpp"
#include "watermark_cases.hpp"
int main() {
 try {
    Check(CivilDay(1970,1,1).Serial()==0); Check(CivilDay(1970,1,1).Weekday()==4);
    Check(CivilDay(2000,1,1).Serial()==10957); Check(CivilDay(2026,9,20).Weekday()==0);
    Check(CivilDay(1900,3,1).Serial()-CivilDay(1900,2,28).Serial()==1);
    Check(CivilDay(2000,3,1).Serial()-CivilDay(2000,2,28).Serial()==2);
    for(int i=CivilDay(1600,1,1).Serial(); i<=CivilDay(2400,12,31).Serial(); ++i) {
        auto d=CivilDay::FromSerial(i); Check(d.Serial()==i);
        Check(CivilDay::Parse(d.String()).Serial()==i);
        Check(d.Weekday()==((i+4)%7+7)%7);
    }
    Reject([]{CivilDay::Parse("19000229");}); Reject([]{CivilDay::Parse("20260230");});
    Reject([]{CivilDay::Parse("2026-09-20");}); Reject([]{CivilDay(1599,1,1);});
    Reject([]{CivilDay::FromSerial(std::numeric_limits<int>::max());});
    Reject([]{BarBuilder("TEST",{{0,10,"20260921"},{9,20,"20260921"}},1,FirstVolume::BaselineOnly);});
    std::vector<Session> sessions{{0,100,"20260921"},{200,300,"20260921"},{400,500,"20260922"}};
    BarBuilder b("TEST",sessions,50,FirstVolume::BaselineOnly); Bar out;
    Check(b.Push(T(0,1,10,100),out)==PushResult::Buffered);
    Check(b.Push(T(0,1,10,100),out)==PushResult::Duplicate);
    Reject([&]{b.Push(T(0,1,11,100),out);});
    Check(b.Push(T(49,2,12,105),out)==PushResult::Buffered);
    Reject([&]{b.Push(T(48,3,9,106),out);});
    Reject([&]{b.Push(T(50,3,9,99),out);});
    Check(b.Push(T(50,3,9,107),out)==PushResult::ClosedBar);
    Check(out.open==10 && out.close==12 && out.high==12 && out.low==10 && out.volume==5 && out.ticks==2 && out.complete);
    Reject([&]{b.Push(T(100,4,8,108),out);});
    Check(b.Push(T(200,4,8,110),out)==PushResult::ClosedBar);
    Check(out.beginUs==50 && out.endUs==100 && out.volume==2);
    Check(b.Push(T(400,1,20,3,"20260922"),out)==PushResult::ClosedBar);
    Check(out.beginUs==200 && out.volume==3);
    Check(b.Finish(out) && out.volume==0 && !out.complete); Check(!b.Finish(out));
    Reject([&]{b.Push(T(401,2,1,4,"20260922"),out);});
    BarBuilder daily("TEST",sessions,0,FirstVolume::IncludeCumulative);
    daily.Push(T(0,1,10,100),out); daily.Push(T(200,2,20,110),out);
    Check(daily.Push(T(400,1,30,3,"20260922"),out)==PushResult::ClosedBar);
    Check(out.beginUs==0 && out.endUs==300 && out.volume==110 && out.high==20 && out.ticks==2);
    Check(daily.Finish(out) && out.volume==3 && out.open==30);
    const auto max=std::numeric_limits<std::int64_t>::max();
    BarBuilder huge("TEST",{{max-10,max,"20260921"}},max,FirstVolume::IncludeCumulative);
    huge.Push(T(max-1,1,-42,std::numeric_limits<std::uint64_t>::max()),out);
    Check(huge.Finish(out) && out.endUs==max && out.close==-42);
    BarBuilder one("TEST",{{0,10,"20260921"}},3,FirstVolume::BaselineOnly);
    one.Push(T(9,1,10,1),out); Check(one.Finish(out) && out.beginUs==9 && out.endUs==10);
    std::vector<Bar> bars(3); bars[0].high=2; bars[1].high=3; bars[2].high=3;
    bars[0].low=-1; bars[1].low=-2; bars[2].low=-2;
    Check(Highest(bars,0,2)==2 && Highest(bars,0,2,true)==1);
    Check(Lowest(bars,0,2)==2 && Lowest(bars,0,2,true)==1);
    Reject([&]{Highest(bars,2,1);}); Reject([&]{Lowest(bars,0,3);});
    TestBarSeries();
    TestWatermarks();
    std::cout<<"PASS "<<checks<<" checks (including exhaustive 1600..2400 date round trips)\n";
    return 0;
 } catch(const std::exception& e) { std::cerr<<e.what()<<'\n'; return 1; }
}
