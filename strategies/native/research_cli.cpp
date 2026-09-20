#include "market_data.h"
#include "replay.h"
#include "strategy.h"
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <locale>
#include <sstream>
#include <stdexcept>
using namespace hepta::research;
namespace {
template<class T> T Number(const std::string& s) {
    T value;std::istringstream in(s);in.imbue(std::locale::classic());in>>std::noskipws>>value;
    if(in.fail()||in.peek()!=std::char_traits<char>::eof())throw std::invalid_argument("invalid numeric argument");
    return value;
}
bool Line(std::istream& input,std::string& row) {
    row.clear();char c;
    while(input.get(c)) {if(c=='\n')break;if(row.size()>=512)throw std::invalid_argument("input row exceeds 512 bytes");row+=c;}
    if(input.bad())throw std::runtime_error("input read failed");
    if(row.empty()&&input.eof())return false;
    if(!row.empty()&&row.back()=='\r')row.pop_back();
    return true;
}
SessionCalendar Sessions(const std::string& path) {
    std::ifstream input(path);std::string row;if(!input||!Line(input,row)||row!="trading_day,begin_ms,end_ms")throw std::invalid_argument("invalid session CSV header");
    std::vector<SessionWindow> windows;
    while(Line(input,row)) {auto a=row.find(','),b=row.find(',',a==std::string::npos?row.size():a+1);
        if(a==std::string::npos||b==std::string::npos||row.find(',',b+1)!=std::string::npos||windows.size()>=100000)
            throw std::invalid_argument("invalid session CSV row");
        SessionWindow w;w.tradingDay=row.substr(0,a);w.beginMs=Number<std::int64_t>(row.substr(a+1,b-a-1));w.endMs=Number<std::int64_t>(row.substr(b+1));windows.push_back(w);}
    return SessionCalendar(windows);
}
void PrintBar(const Bar& b) {
    std::cout<<b.instrument<<','<<b.tradingDay<<','<<b.beginMs<<','<<b.endMs<<','<<b.open<<','<<b.high<<','<<b.low<<','<<b.close<<','<<b.volume<<','<<(b.complete?"true":"false")<<'\n';
}
}
int main(int argc,char** argv) {
    try {
        std::cout.imbue(std::locale::classic());std::cout<<std::setprecision(17);
        if(argc<2)throw std::invalid_argument("usage: hepta-research bars INSTRUMENT INTERVAL_MS SESSIONS.csv TICKS.csv | backtest INSTRUMENT INTERVAL_MS SESSIONS.csv TICKS.csv FAST SLOW UNITS TICK_SIZE MULTIPLIER FEE_PER_UNIT SLIPPAGE_TICKS INITIAL_EQUITY");
        const std::string mode=argv[1];if((mode=="bars"&&argc!=6)||(mode=="backtest"&&argc!=14)||(mode!="bars"&&mode!="backtest"))throw std::invalid_argument("invalid command or argument count");
        const std::string instrument=argv[2];auto calendar=Sessions(argv[4]);
        BarBuilder bars(instrument,calendar,Number<std::int64_t>(argv[3]),InitialVolume::Baseline);
        std::ifstream ticks(argv[5]);std::string row;if(!ticks||!Line(ticks,row)||row!=TickCsvHeader())throw std::invalid_argument("invalid tick CSV header");
        if(mode=="bars") {
            std::cout<<"instrument,trading_day,begin_ms,end_ms,open,high,low,close,volume,complete\n";
            while(Line(ticks,row)){std::vector<Bar> completed;bars.Push(ParseTickCsv(row),completed);for(const auto& b:completed)PrintBar(b);}
            std::vector<Bar> tail;bars.Finish(tail);for(const auto& b:tail)PrintBar(b);return 0;
        }
        const auto fast=Number<std::size_t>(argv[6]),slow=Number<std::size_t>(argv[7]);const auto units=Number<std::int64_t>(argv[8]);
        if(units<=0||units>500000000)throw std::invalid_argument("units must be in [1,500000000]");
        CtaSignal strategy("example.ma",slow,units,false,MovingAverageDecision(fast,slow,units,false));
        ReplayConfig config;config.instrument=instrument;config.tickSize=Number<double>(argv[9]);config.multiplier=Number<double>(argv[10]);
        config.feePerUnit=Number<double>(argv[11]);config.slippageTicks=Number<std::int64_t>(argv[12]);config.initialEquity=Number<double>(argv[13]);
        OfflineReplay replay(config,calendar);std::string active;std::uint64_t orders=0,fills=0;
        while(Line(ticks,row)) {
            const Tick tick=ParseTickCsv(row);fills+=replay.Push(tick).size();
            std::vector<Bar> completed;bars.Push(tick,completed);
            for(const auto& bar:completed) {
                const auto target=strategy.OnClosedBar(bar);
                if(!active.empty())replay.Cancel(active);
                const auto delta=target.desiredUnits-replay.Account().position;
                if(delta!=0) {ReplayOrder order;order.id="offline-"+std::to_string(++orders);order.side=delta>0?1:-1;
                    order.quantity=std::abs(delta);order.limitPrice=tick.price+order.side*config.slippageTicks*config.tickSize;
                    replay.Submit(order);active=order.id;}
            }
        }
        const auto account=replay.Account();
        std::cout<<"OFFLINE_RESEARCH example.ma orders="<<orders<<" fills="<<fills<<" position="<<account.position
            <<" realized_pnl="<<account.realizedPnl<<" unrealized_pnl="<<account.unrealizedPnl<<" fees="<<account.fees
            <<" equity="<<account.equity<<" maximum_drawdown="<<account.maximumDrawdown<<'\n';
        return 0;
    } catch(const std::exception& error) {std::cerr<<"RESEARCH_ERROR: "<<error.what()<<'\n';return 2;}
}
