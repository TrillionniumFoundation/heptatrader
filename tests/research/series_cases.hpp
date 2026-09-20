#pragma once
#include "hepta/research/bar_series.hpp"

namespace {
Bar SeriesBar(std::int64_t i, std::int64_t value) {
    Bar b;
    b.instrument="TEST"; b.tradingDay="20260921";
    b.beginUs=i*10; b.endUs=(i+1)*10;
    b.open=b.close=value; b.high=value+1; b.low=value-1;
    b.volume=2; b.ticks=1; b.complete=true;
    return b;
}
void TestBarSeries() {
    Reject([]{BarSeries("",1);}); Reject([]{BarSeries("TEST",0);});
    Reject([]{BarSeries("A/B");});
    BarSeries s("TEST",6);
    Check(s.Size()==0 && s.CountSinceTradingDay()==0 && s.Revision()==0);
    Reject([&]{s.At(0);}); Reject([&]{s.FromLatest();});
    Reject([&]{s.Highest(0,0);}); Reject([&]{s.Peaks(0,0,0,0);});
    for(int i=0;i<5;++i) s.Append(SeriesBar(i, i%2 ? 3 : 1));
    Check(s.Size()==5 && s.Instrument()=="TEST" && s.Revision()==5);
    Check(s.Highest(0,4)==3 && s.Highest(0,4,PriceField::High,true)==1);
    Check(s.Highest(0,4,PriceField::Close)==3);
    Check(s.Lowest(0,4)==4 && s.Lowest(0,4,PriceField::Low,true)==0);
    Check(s.FromLatest().beginUs==40 && s.FromLatest(4).beginUs==0);
    Check(s.NextHigher(3,0,4).found && s.NextHigher(3,0,4).index==1);
    Check(!s.NextHigher(4,0,4).found);
    Check(s.NextHigher(3,2,4).index==3);
    Check(s.NextLower(1,0,4).found && s.NextLower(1,0,4).index==0);
    Check(!s.NextLower(0,0,4).found);
    Check(s.Peaks(0,4,1,29).empty());
    auto peaks=s.Peaks(0,4,1,30);
    Check(peaks.size()==1 && peaks[0].index==1 && peaks[0].confirmedAtUs==30);
    peaks=s.Peaks(0,4,1,50);
    Check(peaks.size()==2 && peaks[1].index==3 && peaks[1].confirmedAtUs==50);
    const auto troughs=s.Troughs(0,4,1,50);
    Check(troughs.size()==1 && troughs[0].index==2 && troughs[0].confirmedAtUs==40);
    Check(s.Peaks(0,4,0,30).size()==3);
    Check(s.Peaks(0,4,std::numeric_limits<std::size_t>::max(),50).empty());
    Reject([&]{s.Peaks(0,4,1,-1);});
    Reject([&]{s.NextHigher(0,4,3);}); Reject([&]{s.NextLower(0,0,5);});
    Reject([&]{s.Highest(0,4,static_cast<PriceField>(99));});
    Reject([&]{s.Peaks(0,4,99,50,static_cast<PriceField>(99));});
    Check(s.CountSinceTradingDay()==5 && s.CountTradingDay("20260922")==0);
    Reject([&]{s.CountTradingDay("20260230");});
    Bar aggregate=s.Aggregate(0,4);
    Check(aggregate.open==1 && aggregate.close==1 && aggregate.high==4 && aggregate.low==0);
    Check(aggregate.beginUs==0 && aggregate.endUs==50 && aggregate.volume==10 && aggregate.ticks==5 && aggregate.complete);
    const auto rev=s.Revision();
    Bar bad=SeriesBar(4,1); bad.high=0;
    Reject([&]{s.Replace(4,bad);});
    bad=SeriesBar(4,1); bad.instrument="FOREIGN";
    Reject([&]{s.Replace(4,bad);});
    bad=SeriesBar(4,1); bad.tradingDay="20260922";
    Reject([&]{s.Replace(4,bad);});
    bad=SeriesBar(4,1); bad.endUs=51;
    Reject([&]{s.Replace(4,bad);});
    bad=SeriesBar(3,3); bad.complete=false;
    Reject([&]{s.Replace(3,bad);});
    Reject([&]{s.Replace(5,SeriesBar(5,1));});
    Reject([&]{s.Append(SeriesBar(4,1));});
    Check(s.Revision()==rev && s.Size()==5 && s.At(4).endUs==50);
    Bar partial=SeriesBar(4,1); partial.complete=false;
    s.Replace(4,partial);
    Check(s.Peaks(0,4,1,50).size()==1 && !s.Aggregate(0,4).complete);
    Reject([&]{s.Append(SeriesBar(5,2));});
    s.Replace(4,SeriesBar(4,1));
    Check(s.Peaks(0,4,1,50).size()==2);
    Bar tomorrow=SeriesBar(5,2); tomorrow.tradingDay="20260922";
    s.Append(tomorrow);
    Check(s.CountSinceTradingDay()==1 && s.CountTradingDay("20260921")==5);
    Reject([&]{s.Aggregate(0,5);});
    Reject([&]{s.Append(SeriesBar(6,1));});
    s.RemoveBefore(20);
    Check(s.Size()==4 && s.At(0).beginUs==20);
    s.RemoveAfter(30);
    Check(s.Size()==2 && s.At(1).beginUs==30 && s.CountSinceTradingDay()==2);
    const auto afterTrim=s.Revision();
    s.RemoveBefore(20); s.RemoveAfter(30);
    Check(s.Revision()==afterTrim);
    Reject([&]{s.RemoveBefore(-1);}); Reject([&]{s.RemoveAfter(-1);});
    s.RemoveAfter(0); Check(s.Size()==0 && s.CountSinceTradingDay()==0);
    s.Append(SeriesBar(0,-5)); Check(s.At(0).low==-6);

    BarSeries plateau("TEST");
    for(int i=0;i<4;++i) plateau.Append(SeriesBar(i, i==1 || i==2 ? 3 : 1));
    Check(plateau.Peaks(0,3,1,40).empty());
    BarSeries gap("TEST"); gap.Append(SeriesBar(0,0)); gap.Append(SeriesBar(2,1));
    Check(!gap.Aggregate(0,1).complete);
    BarSeries overflow("TEST");
    Bar huge=SeriesBar(0,1); huge.volume=std::numeric_limits<std::uint64_t>::max();
    overflow.Append(huge); overflow.Append(SeriesBar(1,1));
    Reject([&]{overflow.Aggregate(0,1);});
    huge.volume=0; huge.ticks=std::numeric_limits<std::uint64_t>::max();
    overflow.Replace(0,huge); Reject([&]{overflow.Aggregate(0,1);});
    BarSeries invalid("TEST"); bad=SeriesBar(0,1); bad.tradingDay="20260230";
    Reject([&]{invalid.Append(bad);});
    bad=SeriesBar(0,1); bad.endUs=0; Reject([&]{invalid.Append(bad);});
    Check(invalid.Size()==0 && invalid.Revision()==0);

    // Exhaustive strict-peak/trough and prefix-causality checks over 3^5
    // short series, including plateaus. Expected indices come from a direct
    // comparison oracle rather than the implementation's query helpers.
    for(int pattern=0;pattern<243;++pattern) {
        BarSeries full("TEST"); int code=pattern;
        std::vector<int> values;
        for(int i=0;i<5;++i) { values.push_back(code%3-1); code/=3; full.Append(SeriesBar(i,values.back())); }
        BarSeries prefix("TEST");
        for(std::size_t count=1;count<=5;++count) {
            prefix.Append(full.At(count-1));
            for(std::size_t radius=0;radius<=3;++radius) {
                const auto asOf=static_cast<std::int64_t>(count*10);
                const auto hp=full.Peaks(0,4,radius,asOf);
                const auto lp=full.Troughs(0,4,radius,asOf);
                const auto hprefix=prefix.Peaks(0,count-1,radius,asOf);
                const auto lprefix=prefix.Troughs(0,count-1,radius,asOf);
                std::vector<std::size_t> high,low;
                for(std::size_t i=0;i<count;++i) {
                    if(i<radius || radius>=count-i) continue;
                    bool h=true,l=true;
                    for(std::size_t j=i-radius;j<=i+radius;++j) if(j!=i) {
                        h=h && values[i]>values[j]; l=l && values[i]<values[j];
                    }
                    if(h) high.push_back(i);
                    if(l) low.push_back(i);
                }
                Check(hp.size()==high.size() && lp.size()==low.size());
                Check(hprefix.size()==hp.size() && lprefix.size()==lp.size());
                for(std::size_t i=0;i<high.size();++i)
                    Check(hp[i].index==high[i] && hprefix[i].index==high[i] && hp[i].confirmedAtUs==static_cast<std::int64_t>((high[i]+radius+1)*10));
                for(std::size_t i=0;i<low.size();++i)
                    Check(lp[i].index==low[i] && lprefix[i].index==low[i] && lp[i].confirmedAtUs==static_cast<std::int64_t>((low[i]+radius+1)*10));
            }
        }
    }
}
}
