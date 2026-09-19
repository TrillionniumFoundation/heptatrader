#include "hepta/research/market_data.hpp"
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
bool Line(std::istream& in,std::string& out) {
    out.clear(); char c;
    while(in.get(c)) {
        if(c=='\n') { if(!out.empty() && out.back()=='\r') out.pop_back(); return true; }
        if(out.size()==4096) throw std::runtime_error("CSV row exceeds 4096 bytes");
        out.push_back(c);
    }
    if(!in.eof()) throw std::runtime_error("CSV read error");
    if(!out.empty() && out.back()=='\r') out.pop_back();
    return !out.empty();
}
std::vector<std::string> Fields(const std::string& s,std::size_t count) {
    std::vector<std::string> out; std::size_t start=0;
    for(std::size_t i=0; i<=s.size(); ++i) if(i==s.size() || s[i]==',') {
        if(i==start) throw std::invalid_argument("empty CSV field");
        out.push_back(s.substr(start,i-start)); start=i+1;
    }
    if(out.size()!=count) throw std::invalid_argument("CSV field count");
    return out;
}
void Digits(const std::string& s,bool sign) {
    std::size_t i=(sign && !s.empty() && s[0]=='-') ? 1:0;
    if(i==s.size()) throw std::invalid_argument("integer missing digits");
    for(; i<s.size(); ++i) if(s[i]<'0' || s[i]>'9') throw std::invalid_argument("integer syntax");
}
std::int64_t Signed(const std::string& s) { Digits(s,true); return std::stoll(s); }
std::uint64_t Unsigned(const std::string& s) { Digits(s,false); return std::stoull(s); }
void Print(const hepta::research::Bar& b) {
    std::cout<<b.instrument<<','<<b.tradingDay<<','<<b.beginUs<<','<<b.endUs<<','
             <<b.open<<','<<b.high<<','<<b.low<<','<<b.close<<','<<b.volume<<','
             <<b.ticks<<','<<(b.complete?1:0)<<'\n';
    if(!std::cout) throw std::runtime_error("CSV write error");
}
}
int main(int argc,char** argv) {
    try {
        if(argc!=5) throw std::invalid_argument(
            "usage: hepta-research-bars TICKS.csv SESSIONS.csv PERIOD_US baseline|include");
        const auto period=Signed(argv[3]);
        const std::string policy=argv[4];
        if(policy!="baseline" && policy!="include") throw std::invalid_argument("first-volume policy");
        std::ifstream sessionFile(argv[2]), tickFile(argv[1]);
        if(!sessionFile || !tickFile) throw std::runtime_error("cannot open CSV input");
        std::string row;
        if(!Line(sessionFile,row) || row!="begin_us,end_us,trading_day") throw std::invalid_argument("session header");
        std::vector<hepta::research::Session> sessions;
        while(Line(sessionFile,row)) {
            if(sessions.size()==1000000) throw std::invalid_argument("too many sessions");
            auto f=Fields(row,3); sessions.push_back({Signed(f[0]),Signed(f[1]),f[2]});
        }
        if(!Line(tickFile,row) || row!="instrument,trading_day,timestamp_us,sequence,price_ticks,cumulative_volume")
            throw std::invalid_argument("tick header");
        if(!Line(tickFile,row)) throw std::invalid_argument("empty tick stream");
        auto f=Fields(row,6);
        hepta::research::BarBuilder builder(f[0],sessions,period,
            policy=="baseline" ? hepta::research::FirstVolume::BaselineOnly : hepta::research::FirstVolume::IncludeCumulative);
        hepta::research::Bar out;
        std::cout<<"instrument,trading_day,begin_us,end_us,open,high,low,close,volume,ticks,complete\n";
        do {
            f=Fields(row,6);
            hepta::research::Tick t{f[0],f[1],Signed(f[2]),Signed(f[4]),Unsigned(f[3]),Unsigned(f[5])};
            if(builder.Push(t,out)==hepta::research::PushResult::ClosedBar) Print(out);
        } while(Line(tickFile,row));
        if(builder.Finish(out)) Print(out);
        return 0;
    } catch(const std::exception& e) { std::cerr<<"research input rejected: "<<e.what()<<'\n'; return 2; }
}
