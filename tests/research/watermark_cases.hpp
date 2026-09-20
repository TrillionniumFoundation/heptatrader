#pragma once

namespace {
bool SameWatermarkBar(const Bar& a, const Bar& b) {
    return a.instrument==b.instrument && a.tradingDay==b.tradingDay &&
        a.beginUs==b.beginUs && a.endUs==b.endUs && a.open==b.open &&
        a.high==b.high && a.low==b.low && a.close==b.close &&
        a.volume==b.volume && a.ticks==b.ticks && a.complete==b.complete;
}
void TestWatermarks() {
    const std::vector<Session> sessions{{0,100,"20260921"},{200,300,"20260921"},
                                        {400,500,"20260922"}};
    Bar sentinel; sentinel.instrument="UNTOUCHED"; sentinel.volume=73;
    Bar out=sentinel;
    BarBuilder empty("TEST",sessions,50,FirstVolume::BaselineOnly);
    Reject([&]{empty.AdvanceWatermark(-1,out);});
    Check(SameWatermarkBar(out,sentinel));
    Check(!empty.AdvanceWatermark(50,out) && SameWatermarkBar(out,sentinel));
    Reject([&]{empty.Push(T(49,1,10,100),out);});
    Check(empty.Push(T(50,1,10,100),out)==PushResult::Buffered);
    Check(empty.AdvanceWatermark(100,out) && out.complete && out.ticks==1 && out.volume==0);
    Check(!empty.Finish(out));
    Reject([&]{empty.AdvanceWatermark(100,out);});
    Reject([&]{BarBuilder("TEST",sessions,50,static_cast<FirstVolume>(99));});

    BarBuilder b("TEST",sessions,50,FirstVolume::BaselineOnly);
    b.Push(T(0,1,10,100),out); b.Push(T(49,2,-2,105),out);
    out=sentinel;
    Reject([&]{b.AdvanceWatermark(48,out);});
    Check(SameWatermarkBar(out,sentinel));
    Check(!b.AdvanceWatermark(49,out) && SameWatermarkBar(out,sentinel));
    Check(b.AdvanceWatermark(50,out) && out.complete && out.beginUs==0 && out.endUs==50);
    Check(out.open==10 && out.close==-2 && out.high==10 && out.low==-2 && out.volume==5 && out.ticks==2);
    out=sentinel;
    Check(!b.AdvanceWatermark(50,out) && SameWatermarkBar(out,sentinel));
    Check(b.Push(T(49,2,-2,105),out)==PushResult::Duplicate);
    Reject([&]{b.Push(T(49,3,-2,106),out);});
    Reject([&]{b.Push(T(49,2,99,105),out);});
    Check(SameWatermarkBar(out,sentinel));
    Check(b.Push(T(50,3,7,107),out)==PushResult::Buffered);
    Check(b.AdvanceWatermark(150,out) && out.beginUs==50 && out.endUs==100 && out.volume==2);
    out=sentinel;
    Check(!b.AdvanceWatermark(200,out) && SameWatermarkBar(out,sentinel));
    Check(b.Push(T(200,4,3,110),out)==PushResult::Buffered);
    Check(b.AdvanceWatermark(300,out) && out.beginUs==200 && out.endUs==250 && out.volume==3);
    // Baseline-only must restart at a real trading-day transition, not after
    // a watermark, session break or the absence of a populated bar.
    b.Push(T(400,1,20,8,"20260922"),out);
    Check(b.Finish(out) && !out.complete && out.volume==0);
    Reject([&]{b.AdvanceWatermark(500,out);});
    Check(!b.Finish(out));

    BarBuilder daily("TEST",sessions,0,FirstVolume::IncludeCumulative);
    daily.Push(T(9,1,10,100),out); out=sentinel;
    Check(!daily.AdvanceWatermark(100,out) && SameWatermarkBar(out,sentinel));
    Check(!daily.AdvanceWatermark(199,out) && SameWatermarkBar(out,sentinel));
    daily.Push(T(200,2,20,110),out);
    Check(daily.AdvanceWatermark(300,out) && out.complete && out.endUs==300 && out.ticks==2 && out.volume==110);
    out=sentinel;
    Check(!daily.AdvanceWatermark(399,out) && SameWatermarkBar(out,sentinel));
    daily.Push(T(400,1,30,3,"20260922"),out);
    Check(daily.AdvanceWatermark(500,out) && out.volume==3 && out.endUs==500);
    Check(!daily.Finish(out));

    const auto max=std::numeric_limits<std::int64_t>::max();
    BarBuilder huge("TEST",{{max-10,max,"20260921"}},max,FirstVolume::IncludeCumulative);
    huge.Push(T(max-1,1,std::numeric_limits<std::int64_t>::min(),
                std::numeric_limits<std::uint64_t>::max()),out);
    Check(huge.AdvanceWatermark(max,out) && out.endUs==max && out.complete &&
          out.volume==std::numeric_limits<std::uint64_t>::max());
    Check(!huge.Finish(out));

    // Independent integer OHLC/volume oracle: 1023 input subsets x 5 periods
    // x 2 volume policies. Calling watermarks before every tick must not alter
    // numeric bars, consume volume, create an empty bar or leak a session gap.
    const std::vector<Session> shortSessions{{0,12,"20260921"},{20,32,"20260921"},
                                             {40,52,"20260922"}};
    const std::int64_t times[]={0,3,8,11,20,25,31,40,44,51};
    const std::int64_t periods[]={0,1,5,7,max};
    for(unsigned mask=1;mask<1024;++mask) for(auto period:periods) for(int include=0;include<2;++include) {
        const auto policy=include ? FirstVolume::IncludeCumulative : FirstVolume::BaselineOnly;
        BarBuilder stream("TEST",shortSessions,period,policy);
        std::vector<Bar> actual,expected;
        std::string previousDay;
        std::uint64_t previousVolume=0;
        for(unsigned i=0;i<10;++i) if(mask & (1u<<i)) {
            const auto time=times[i];
            const bool nextDay=time>=40;
            const std::string day=nextDay ? "20260922" : "20260921";
            const auto cumulative=static_cast<std::uint64_t>(nextDay ? time-35 : time+100);
            if(stream.AdvanceWatermark(time,out)) actual.push_back(out);
            if(stream.Push(T(time,i+1,static_cast<std::int64_t>(i)-5,cumulative,day),out)==PushResult::ClosedBar)
                actual.push_back(out);
            std::int64_t begin=nextDay ? 40 : (time>=20 ? 20 : 0);
            std::int64_t end=begin+12;
            if(period==0) { begin=nextDay ? 40 : 0; end=nextDay ? 52 : 32; }
            else { begin+=((time-begin)/period)*period; end=begin+std::min(period,end-begin); }
            const auto delta=day==previousDay ? cumulative-previousVolume : (include ? cumulative : 0);
            if(expected.empty() || expected.back().beginUs!=begin) {
                Bar bar; bar.instrument="TEST"; bar.tradingDay=day; bar.beginUs=begin; bar.endUs=end;
                bar.open=bar.high=bar.low=bar.close=static_cast<std::int64_t>(i)-5;
                bar.complete=true; expected.push_back(bar);
            }
            auto& bar=expected.back();
            bar.close=static_cast<std::int64_t>(i)-5;
            bar.high=std::max(bar.high,bar.close); bar.low=std::min(bar.low,bar.close);
            bar.volume+=delta; ++bar.ticks;
            previousDay=day; previousVolume=cumulative;
        }
        if(stream.AdvanceWatermark(52,out)) actual.push_back(out);
        Check(!stream.Finish(out));
        Check(actual.size()==expected.size());
        for(std::size_t i=0;i<expected.size();++i) Check(SameWatermarkBar(actual[i],expected[i]));
    }
}
}
