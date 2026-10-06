#include "../HeptaTrade/execution/ib_observation_refresh.h"
#include <cassert>
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
int main(){
    {Spy s;s.forbidHistory=true;assert(DispatchIbReconnectReadEvidence(s,true));assert(s.reads==std::vector<std::string>({"account"}));}
    {Spy s;s.forbidHistory=true;s.accountOk=false;assert(!DispatchIbReconnectReadEvidence(s,true));assert(s.reads.size()==1);}
    {Spy s;assert(DispatchIbReconnectReadEvidence(s,false));assert(s.reads==std::vector<std::string>({"open","terminal"}));}
    {Spy s;s.openOk=false;assert(!DispatchIbReconnectReadEvidence(s,false));assert(s.reads==std::vector<std::string>({"open"}));}
    {Spy s;s.terminalOk=false;assert(!DispatchIbReconnectReadEvidence(s,false));assert(s.reads==std::vector<std::string>({"open","terminal"}));}
    {Spy s;s.accountOk=false;assert(DispatchIbReconnectReadEvidence(s,false));assert(s.reads.size()==2);}
}
