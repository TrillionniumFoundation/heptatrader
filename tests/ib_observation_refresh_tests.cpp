#include "../HeptaTrade/execution/ib_observation_refresh.h"
#include "../HeptaTrade/execution/ib_observation_initial_farm.h"
#include <cassert>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
struct Spy {
    bool accountOk=true,openOk=true,terminalOk=true,forbidHistory=false;
    std::vector<std::string> reads;
    bool ReqAccountSummary(){reads.push_back("account");return accountOk;}
    bool ReqAuthoritativeOpenOrders(){if(forbidHistory)throw std::runtime_error("observation requested trading history");reads.push_back("open");return openOk;}
    bool ReqTerminalCorrelations(){if(forbidHistory)throw std::runtime_error("observation requested terminal history");reads.push_back("terminal");return terminalOk;}
};
static void initialFarmWarmupControls() {
    using Gate=IbObservationInitialFarmWarmup;
    const auto start=Gate::Clock::time_point(std::chrono::seconds(10));
    const auto at=[&](int n){return start+std::chrono::milliseconds(n);};
    auto armed=[&](){Gate g;g.Arm(1,at(2000),1000);return g;};
    auto warned=[&](){auto g=armed();assert(g.ObserveConnecting("cashfarm",1,at(100),1100));return g;};
    // The actual observed prefix ended after cashfarm 2119. It is NOT success.
    {auto g=warned();assert(!g.CanFinish(1,at(500),1500,1490,1491,5000,250));}
    {auto g=warned();g.ObserveReady("hfarm",1,at(200),1200);assert(g.WaitingForSpecificFarm());assert(!g.CanFinish(1,at(500),1500,1490,1491,5000,250));}
    // Only these explicitly synthetic later callbacks make the positive case.
    {auto g=warned();g.ObserveReady("cashfarm",1,at(200),1200);assert(!g.CanFinish(1,at(449),1449,1440,1441,5000,250));assert(g.CanFinish(1,at(450),1450,1440,1441,5000,250));g.Complete();assert(!g.ObserveConnecting("cashfarm",1,at(500),1500));}
    {auto g=warned();g.ObserveReady("cashfarm",1,at(200),1200);assert(!g.CanFinish(1,at(500),1500,1199,1491,5000,250));}
    {auto g=warned();g.ObserveReady("cashfarm",1,at(200),1200);assert(!g.CanFinish(1,at(500),1500,1490,1200,5000,250));}
    {auto g=warned();g.ObserveReady("cashfarm",1,at(200),1200);assert(!g.CanFinish(1,at(500),1500,1490,1501,5000,250));}
    {auto g=warned();g.ObserveReady("cashfarm",1,at(200),1200);assert(!g.CanFinish(1,at(500),6500,1500,6499,5000,250));}
    {auto g=warned();g.ObserveReady("cashfarm",1,at(2000),3000);assert(g.Failed());assert(!g.CanFinish(1,at(2000),3000,2999,2999,5000,250));}
    {auto g=warned();g.ObserveReady("cashfarm",2,at(200),1200);assert(g.Failed());assert(!g.CanFinish(1,at(500),1500,1490,1491,5000,250));}
    {auto g=warned();g.ObserveReady("cashfarm",1,at(200),1099);assert(g.Failed());}
    {auto g=warned();g.ObserveReady("cashfarm",1,at(200),1200);assert(g.ObserveConnecting("cashfarm",1,at(300),1300));assert(!g.CanFinish(1,at(700),1700,1690,1691,5000,250));}
    {auto g=warned();assert(g.ObserveConnecting("hfarm",1,at(150),1150));g.ObserveReady("cashfarm",1,at(200),1200);assert(!g.CanFinish(1,at(500),1500,1490,1491,5000,250));g.ObserveReady("hfarm",1,at(600),1600);assert(g.CanFinish(1,at(900),1900,1890,1891,5000,250));}
    {auto g=armed();for(int i=1;i<=4;++i)assert(g.ObserveConnecting("cashfarm",1,at(i),1000+i));assert(!g.ObserveConnecting("cashfarm",1,at(5),1005));assert(g.Failed());}
    {auto g=armed();assert(!g.ObserveConnecting("usfarm",1,at(100),1100));assert(g.Failed());}
    {auto g=armed();assert(g.CanFinish(1,at(100),1100,1090,1091,5000,250));}
    {Gate g;assert(!g.ObserveConnecting("cashfarm",1,at(100),1100));assert(g.CanFinish(1,at(100),1100,0,0,0,250));}
}
int main(){
    initialFarmWarmupControls();
    {Spy s;s.forbidHistory=true;assert(DispatchIbReconnectReadEvidence(s,true));assert(s.reads==std::vector<std::string>({"account"}));}
    {Spy s;s.forbidHistory=true;s.accountOk=false;assert(!DispatchIbReconnectReadEvidence(s,true));assert(s.reads.size()==1);}
    {Spy s;assert(DispatchIbReconnectReadEvidence(s,false));assert(s.reads==std::vector<std::string>({"open","terminal"}));}
    {Spy s;s.openOk=false;assert(!DispatchIbReconnectReadEvidence(s,false));assert(s.reads==std::vector<std::string>({"open"}));}
    {Spy s;s.terminalOk=false;assert(!DispatchIbReconnectReadEvidence(s,false));assert(s.reads==std::vector<std::string>({"open","terminal"}));}
    {Spy s;s.accountOk=false;assert(DispatchIbReconnectReadEvidence(s,false));assert(s.reads.size()==2);}
    std::cout<<"PASS 16 initial-farm gate controls and 6 existing read-selection controls; synthetic; zero broker requests\n";
}
