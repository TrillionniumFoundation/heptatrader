#include "options/zero_dte.h"
#include "options/snowball_option_feed.h"
#include "options/zero_dte_quote_cache.h"
#include "DefaultEWrapper.h"
#include "EClientSocket.h"
#include "EReader.h"
#include "EReaderOSSignal.h"
#include "Decimal.h"
#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <csignal>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <set>
#include <sstream>
#include <stdexcept>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

namespace opt=hepta::options;
namespace {
volatile std::sig_atomic_t stopped=0;
void Stop(int){stopped=1;}
std::int64_t Now(){return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count();}
std::string J(const std::string& s){return '"'+opt::EscapeJson(s)+'"';}
void Emit(const std::string& s){std::cout<<s<<std::endl;}
class Capture {
    int fd_=-1;
public:
    explicit Capture(const std::string& file){
        fd_=::open(file.c_str(),O_WRONLY|O_APPEND|O_CREAT|O_NOFOLLOW|O_CLOEXEC,0600);
        struct stat st{};
        if(fd_<0 || fstat(fd_,&st) || !S_ISREG(st.st_mode) || st.st_uid!=geteuid() ||
           (st.st_mode&077)!=0 || st.st_nlink!=1 || flock(fd_,LOCK_EX|LOCK_NB))
            throw std::runtime_error("OPTIONS_CAPTURE_UNSAFE_OR_BUSY");
    }
    ~Capture(){if(fd_>=0)::close(fd_);}
    void Append(const std::string& s){
        if(s.size()>32768 || s.find('\n')!=std::string::npos)throw std::runtime_error("OPTIONS_CAPTURE_SIZE");
        const std::string line=s+'\n';std::size_t pos=0;
        while(pos<line.size()){
            auto n=::write(fd_,line.data()+pos,line.size()-pos);
            if(n<0 && errno==EINTR)continue;if(n<=0)throw std::runtime_error("OPTIONS_CAPTURE_WRITE_FAILED");pos+=n;
        }
        if(::fdatasync(fd_))throw std::runtime_error("OPTIONS_CAPTURE_SYNC_FAILED");
    }
};
using Quote=opt::QuoteCache;
struct Chain {int underlyingConId=0;std::string exchange,tc,multiplier;std::set<std::string> expiries;std::set<double> strikes;};
class Observer final:public DefaultEWrapper {
    EReaderOSSignal signal_{200};
    EClientSocket client_{this,&signal_};
    std::unique_ptr<EReader> reader_;
    std::string account_;
    bool marketDataRequired_=true;
public:
    const std::uint64_t epoch=(static_cast<std::uint64_t>(Now())<<16)|(getpid()&0xffff);
    bool ready=false,accountVerified=false;
    int contractRequestCount=0,marketDataRequestCount=0,marketDataTypeRequestCount=0;
    std::string connectionFault;
    std::map<int,std::vector<ContractDetails>> contracts;
    std::map<int,std::vector<Chain>> chains;
    std::map<int,Quote> quotes;
    std::set<int> done;
    std::vector<int> subscriptions;
    explicit Observer(const std::string& account,bool marketDataRequired=true):account_(account),marketDataRequired_(marketDataRequired){}
    bool Connect(int clientId){
        if(!client_.eConnect("127.0.0.1",4002,clientId,false))return false;
        reader_.reset(new EReader(&client_,&signal_));reader_->start();
        return Wait([&]{return ready&&accountVerified;},8000);
    }
    ~Observer(){
        for(int id:subscriptions)if(client_.isConnected())client_.cancelMktData(id);
        client_.eDisconnect();reader_.reset();
    }
    template<class F> bool Wait(F condition,int timeout){
        const auto end=std::chrono::steady_clock::now()+std::chrono::milliseconds(timeout);
        while(!stopped && Connected() && std::chrono::steady_clock::now()<end){
            if(condition())return true;signal_.waitForSignal();reader_->processMsgs();
        }
        return !stopped && Connected() && condition();
    }
    void Pump(int milliseconds){Wait([]{return false;},milliseconds);}
    bool Connected()const{return client_.isConnected() && connectionFault.empty();}
    void InvalidateConnection(const std::string& reason){
        if(connectionFault.empty()) connectionFault=reason;
        for(auto& item:quotes)item.second.Invalidate();
    }
    void connectionClosed()override{InvalidateConnection("OPTIONS_BROKER_CONNECTION_CLOSED");}
    bool Details(int id,const ::Contract& c){++contractRequestCount;client_.reqContractDetails(id,c);return Wait([&]{return done.count(id);},7000);}
    bool OptionChain(int id,const std::string& symbol,int conId){client_.reqSecDefOptParams(id,symbol,"","IND",conId);return Wait([&]{return done.count(id);},8000);}
    void DataType(int t){++marketDataTypeRequestCount;client_.reqMarketDataType(t);}
    void Subscribe(int id,const ::Contract& c){++marketDataRequestCount;quotes[id]=Quote();client_.reqMktData(id,c,"",false,false,TagValueListSPtr());subscriptions.push_back(id);}
    void CancelData(int id){client_.cancelMktData(id);subscriptions.erase(std::remove(subscriptions.begin(),subscriptions.end(),id),subscriptions.end());}
    opt::Tick Tick(double value){opt::Tick t;t.observedMs=Now();t.epoch=epoch;t.valid=std::isfinite(value)&&value>0&&value<1e100;t.value=t.valid?value:0;return t;}
    void nextValidId(OrderId)override{ready=true;}
    void managedAccounts(const std::string& accounts)override{
        std::istringstream in(accounts);std::string a;std::set<std::string> names;while(std::getline(in,a,','))if(!a.empty())names.insert(a);
        accountVerified=names.size()==1 && names.count(account_);
        Emit("{\"event\":\"paper_account_check\",\"verified\":"+std::string(accountVerified?"true":"false")+"}");
    }
    void error(int id,int code,const std::string& message,const std::string&)override{
        if(code==354 || code==10090 || code==10089 || code==10167)quotes[id].EntitlementDenied();
        // Recovery starts a new attempt/epoch; old cached data is never trusted.
        if(code==1100 || code==1101 || code==1102 || code==1300 || code==2110 || (code==2103 && marketDataRequired_) || code==504)
            InvalidateConnection("OPTIONS_BROKER_DATA_BOUNDARY_"+std::to_string(code));
        if(code==200 || code==321 || code==322)done.insert(id);
        if(code==2104 || code==2106 || code==2107 || code==2108 || code==2158)return;
        Emit("{\"event\":\"ib_notice\",\"request_id\":"+std::to_string(id)+",\"code\":"+std::to_string(code)+",\"message\":"+J(message)+"}");
    }
    void contractDetails(int id,const ContractDetails& c)override{contracts[id].push_back(c);}
    void contractDetailsEnd(int id)override{done.insert(id);}
    void securityDefinitionOptionalParameter(int id,const std::string& ex,int underlyingConId,const std::string& tc,const std::string& mult,
        const std::set<std::string>& expiries,const std::set<double>& strikes)override{
        Chain c;c.underlyingConId=underlyingConId;c.exchange=ex;c.tc=tc;c.multiplier=mult;c.expiries=expiries;c.strikes=strikes;chains[id].push_back(c);
    }
    void securityDefinitionOptionalParameterEnd(int id)override{done.insert(id);}
    void marketDataType(TickerId id,int type)override{quotes[id].MarketDataTypeObserved(type);}
    void tickPrice(TickerId id,TickType field,double price,const TickAttrib&)override{
        auto& q=quotes[id];
        if(field==DELAYED_BID || field==DELAYED_ASK || field==DELAYED_LAST || field==DELAYED_CLOSE)
            q.MarketDataTypeObserved(3);
        if(field==BID || field==DELAYED_BID)q.book.bid=Tick(price);
        if(field==ASK || field==DELAYED_ASK)q.book.ask=Tick(price);
        if(field==LAST || field==DELAYED_LAST)q.last=Tick(price);
        if(field==CLOSE || field==DELAYED_CLOSE)q.close=Tick(price);
    }
    void tickSize(TickerId id,TickType field,Decimal size)override{
        const double value=size==UNSET_DECIMAL?0:DecimalFunctions::decimalToDouble(size);
        if(field==DELAYED_BID_SIZE || field==DELAYED_ASK_SIZE)quotes[id].MarketDataTypeObserved(3);
        if(field==BID_SIZE || field==DELAYED_BID_SIZE)quotes[id].book.bidSize=Tick(value);
        if(field==ASK_SIZE || field==DELAYED_ASK_SIZE)quotes[id].book.askSize=Tick(value);
    }
    void tickOptionComputation(TickerId id,TickType type,int,double iv,double delta,double,double,
                              double gamma,double vega,double theta,double underlying)override{
        if(type!=MODEL_OPTION && type!=DELAYED_MODEL_OPTION_COMPUTATION)return;
        if(type==DELAYED_MODEL_OPTION_COMPUTATION)quotes[id].MarketDataTypeObserved(3);
        auto& g=quotes[id].greeks;
        const bool valid=std::isfinite(iv)&&std::isfinite(delta)&&std::isfinite(gamma)&&std::isfinite(vega)&&
            std::isfinite(theta)&&std::isfinite(underlying)&&std::fabs(theta)<1e6&&iv>=0&&iv<=5&&std::fabs(delta)<=1&&
            gamma>=0&&gamma<1e6&&vega>=0&&vega<1e6&&underlying>0&&underlying<1e8;
        g=opt::Greeks();g.valid=valid;g.epoch=epoch;g.observedMs=Now();
        if(valid){g.iv=iv;g.delta=delta;g.gamma=gamma;g.vega=vega;g.theta=theta;g.underlying=underlying;}
    }
};
opt::Contract Convert(const ContractDetails& d,int underlying){
    if(d.underConId!=underlying || d.underConId<=0)
        throw std::runtime_error("OPTIONS_QUALIFIED_UNDERLYING_MISMATCH");
    opt::Contract c;c.conId=d.contract.conId;c.underlyingConId=d.underConId;
    c.ref.symbol=d.contract.symbol;c.ref.secType=d.contract.secType;c.ref.exchange="SMART";c.ref.currency=d.contract.currency;
    c.ref.lastTradeDateOrContractMonth=d.contract.lastTradeDateOrContractMonth;c.ref.right=d.contract.right;
    c.ref.strike=d.contract.strike;c.ref.multiplier=d.contract.multiplier;c.ref.tradingClass=d.contract.tradingClass;c.ref.localSymbol=d.contract.localSymbol;
    // Narrow, documented Cboe product allowlist. Not inferred from a generic OPT.
    const bool allowed=(c.ref.symbol=="XSP"&&c.ref.tradingClass=="XSP")||(c.ref.symbol=="SPX"&&c.ref.tradingClass=="SPXW");
    c.european=c.cashSettled=c.pmSettled=allowed;
    c.session=opt::ParseIbSession(d.realExpirationDate,d.liquidHours,d.lastTradeTime,d.timeZoneId);
    return c;
}
struct Candidate {opt::Contract contract;int ticker=0,underlyingTicker=0;};

// Public read-only contract route: no price subscription and no quote used to
// choose a strike. Input is an exact caller-supplied canonical OCC identity.
int QualifyOnly(const std::string& account,const std::string& occ,int clientId){
    opt::CanonicalOptionIdentity identity;std::string reason;
    if(!opt::ParseCanonicalOcc(occ,identity,reason))throw std::runtime_error(reason);
    if(identity.expiry!=opt::NewYorkDate(Now()))throw std::runtime_error("OPTION_NOT_TODAY_EXPIRY");
    Observer ib(account,false);if(!ib.Connect(clientId))throw std::runtime_error("OPTIONS_PAPER_CONNECT_UNVERIFIED");
    ::Contract underlying;underlying.symbol=identity.symbol;underlying.secType="IND";
    underlying.exchange="CBOE";underlying.currency=identity.currency;
    if(!ib.Details(8700,underlying)||ib.contracts[8700].size()!=1)
        throw std::runtime_error("OPTIONS_UNDERLYING_QUALIFICATION_NOT_UNIQUE");
    const auto& qualifiedUnderlying=ib.contracts[8700][0].contract;
    if(qualifiedUnderlying.conId<=0 || qualifiedUnderlying.symbol!=identity.symbol ||
       qualifiedUnderlying.secType!="IND" || qualifiedUnderlying.currency!=identity.currency)
        throw std::runtime_error("OPTIONS_QUALIFIED_UNDERLYING_MISMATCH");
    ::Contract requested;requested.symbol=identity.symbol;requested.secType="OPT";
    requested.exchange="SMART";requested.currency=identity.currency;
    requested.lastTradeDateOrContractMonth=identity.expiry;requested.right=identity.right;
    requested.strike=identity.strikeMilli/1000.0;requested.multiplier=std::to_string(identity.multiplier);
    requested.tradingClass=identity.tradingClass;requested.localSymbol=identity.localSymbol;
    if(!ib.Details(8701,requested)||ib.contracts[8701].size()!=1)
        throw std::runtime_error("OPTIONS_CONTRACT_QUALIFICATION_NOT_UNIQUE");
    const auto contract=Convert(ib.contracts[8701][0],qualifiedUnderlying.conId);
    reason=opt::MatchBrokerOption(identity,contract);
    if(!reason.empty())throw std::runtime_error(reason);
    if(!ib.Connected() || !ib.accountVerified)throw std::runtime_error("OPTIONS_QUALIFICATION_CONNECTION_LOST");
    Emit("{\"event\":\"options_contract_qualification\",\"qualified\":true,\"broker\":\"IB\","
         "\"input_origin\":\"CALLER_SUPPLIED_OCC_NOT_A_LIVE_FEED\",\"canonical_key\":"+J(identity.Key())+
         ",\"broker_contract_key\":"+J(contract.key())+",\"local_symbol\":"+J(identity.localSymbol)+
         ",\"broker_epoch\":"+std::to_string(ib.epoch)+",\"con_id\":"+std::to_string(contract.conId)+
         ",\"underlying_con_id\":"+std::to_string(contract.underlyingConId)+
         ",\"exchange_date\":"+J(contract.session.tradingDate)+
         ",\"session_open_ms\":"+std::to_string(contract.session.openMs)+
         ",\"last_trade_ms\":"+std::to_string(contract.session.lastTradeMs)+
         ",\"contract_requests\":"+std::to_string(ib.contractRequestCount)+
         ",\"market_data_requests\":"+std::to_string(ib.marketDataRequestCount)+
         ",\"market_data_type_requests\":"+std::to_string(ib.marketDataTypeRequestCount)+
         ",\"snowball_realtime_proven\":false,\"trading_permission_proven\":false,\"broker_order_send_count\":0}");
    return 0;
}

int Replay(const std::string& file){
    std::ifstream in(file);if(!in)throw std::runtime_error("OPTIONS_REPLAY_OPEN_FAILED");
    std::string row;std::size_t n=0,ready=0;
    while(std::getline(in,row)){
        if(in.eof())throw std::runtime_error("OPTIONS_CAPTURE_TRUNCATED_TAIL");
        opt::Policy p;opt::Contract c;opt::Snapshot s;std::string expected,reason;
        if(!opt::DecodeObservation(row,p,c,s,expected,reason))throw std::runtime_error("OPTIONS_REPLAY_LINE_"+std::to_string(n+1)+":"+reason);
        ready+=opt::Evaluate(p,c,s,false).marketDataReady;n++;
    }
    if(!in.eof()||n==0)throw std::runtime_error("OPTIONS_REPLAY_INCOMPLETE");
    Emit("{\"replay_pass\":true,\"records\":"+std::to_string(n)+",\"market_data_ready_records\":"+std::to_string(ready)+",\"broker_authorized\":false}");return 0;
}
}
int main(int argc,char** argv){
    try{
        std::string account,file,replay,qualifyOcc;int duration=60,clientId=8661;
        for(int i=1;i<argc;++i){const std::string k=argv[i];if(i+1==argc)throw std::runtime_error("MISSING_OPTION_VALUE");const std::string v=argv[++i];
            if(k=="--account")account=v;else if(k=="--capture")file=v;else if(k=="--replay")replay=v;
            else if(k=="--qualify-occ")qualifyOcc=v;
            else if(k=="--duration-sec")duration=std::stoi(v);else if(k=="--client-id")clientId=std::stoi(v);
            else throw std::runtime_error("UNKNOWN_OPTION");}
        if(!replay.empty()){if(!qualifyOcc.empty())throw std::runtime_error("OPTIONS_MODE_CONFLICT");return Replay(replay);}
        if(geteuid()==0 || account.size()<3 || account.compare(0,2,"DU") ||
           account.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",2)!=std::string::npos ||
           (qualifyOcc.empty() && (file.empty() || file[0]!='/')) || (!qualifyOcc.empty() && !file.empty()) ||
           duration<15 || duration>3600 || clientId<8000 || clientId>9999)throw std::runtime_error("OPTIONS_READONLY_CONFIG_INVALID");
        std::signal(SIGTERM,Stop);std::signal(SIGINT,Stop);
        if(!qualifyOcc.empty())return QualifyOnly(account,qualifyOcc,clientId);
        Capture capture(file);Observer ib(account);
        if(!ib.Connect(clientId))throw std::runtime_error("OPTIONS_PAPER_CONNECT_UNVERIFIED");
        const std::string date=opt::NewYorkDate(Now());
        Emit("{\"event\":\"options_readonly_started\",\"exchange_date\":"+J(date)+",\"order_send_capability\":false}");
        struct Under {std::string symbol,tc;::Contract contract;int delayed=0,live=0,chain=0;};
        std::vector<Under> underlying;
        ib.DataType(3); // Diagnostic strike discovery only. Never copied into admission snapshot.
        for(int i=0;i<2;++i){
            Under u;u.symbol=i?"SPX":"XSP";u.tc=i?"SPXW":"XSP";u.contract.symbol=u.symbol;u.contract.secType="IND";
            u.contract.exchange="CBOE";u.contract.currency="USD";
            int id=8100+i;if(!ib.Details(id,u.contract)||ib.contracts[id].size()!=1)continue;
            u.contract=ib.contracts[id][0].contract;u.contract.exchange="CBOE";
            u.delayed=8200+i;u.live=8300+i;u.chain=8400+i;ib.Subscribe(u.delayed,u.contract);
            if(!ib.OptionChain(u.chain,u.symbol,u.contract.conId))continue;underlying.push_back(u);
        }
        ib.Wait([&]{ for(const auto& u:underlying) {
            const auto& q=ib.quotes[u.delayed]; if(!q.last.valid && !q.close.valid)return false;
        } return !underlying.empty(); },15000);
        ib.DataType(1);std::vector<Candidate> candidates;int next=8500;
        for(const auto& u:underlying){
            const auto diagnostic=ib.quotes[u.delayed];
            const double reference=diagnostic.last.valid?diagnostic.last.value:(diagnostic.close.valid?diagnostic.close.value:0);
            ib.CancelData(u.delayed);ib.Subscribe(u.live,u.contract);
            const Chain* chain=nullptr;
            for(const auto& x:ib.chains[u.chain])if(x.underlyingConId==u.contract.conId&&x.exchange=="SMART"&&x.tc==u.tc&&x.multiplier=="100"&&x.expiries.count(date)){
                if(chain)throw std::runtime_error("OPTIONS_CHAIN_AMBIGUOUS");chain=&x;}
            Emit("{\"event\":\"discovery_diagnostics\",\"symbol\":"+J(u.symbol)+
                ",\"underlying_con_id\":"+std::to_string(u.contract.conId)+
                ",\"chain_count\":"+std::to_string(ib.chains[u.chain].size())+
                ",\"chain_matched\":"+std::string(chain?"true":"false")+
                ",\"diagnostic_reference\":"+std::to_string(reference)+"}");
            if(!chain||reference<=0||chain->strikes.empty()){
                Emit("{\"event\":\"candidate_unavailable\",\"symbol\":"+J(u.symbol)+",\"reason\":\"NO_DIAGNOSTIC_REFERENCE_OR_TODAY_CHAIN\"}");continue;}
            const auto strike=*std::min_element(chain->strikes.begin(),chain->strikes.end(),[&](double a,double b){return std::fabs(a-reference)<std::fabs(b-reference);});
            for(const std::string right:{"C","P"}){
                ::Contract q;q.symbol=u.symbol;q.secType="OPT";q.exchange="SMART";q.currency="USD";
                q.lastTradeDateOrContractMonth=date;q.tradingClass=u.tc;q.multiplier="100";q.right=right;q.strike=strike;
                const int req=next++;if(!ib.Details(req,q)||ib.contracts[req].size()!=1)continue;
                const auto& details=ib.contracts[req][0];
                const auto& actual=details.contract;
                if(actual.conId<=0 || actual.symbol!=q.symbol || actual.secType!=q.secType ||
                   actual.currency!=q.currency || actual.right!=q.right || actual.strike!=q.strike ||
                   actual.multiplier!=q.multiplier || actual.tradingClass!=q.tradingClass ||
                   actual.lastTradeDateOrContractMonth!=q.lastTradeDateOrContractMonth)
                    throw std::runtime_error("OPTIONS_QUALIFIED_CONTRACT_REQUEST_MISMATCH");
                Candidate c;c.contract=Convert(details,u.contract.conId);c.underlyingTicker=u.live;c.ticker=next++;
                const auto err=opt::ValidateContract(c.contract);
                Emit("{\"event\":\"candidate_contract\",\"con_id\":"+std::to_string(c.contract.conId)+",\"local_symbol\":"+J(c.contract.ref.localSymbol)+
                    ",\"liquid_hours\":"+J(details.liquidHours)+",\"last_trade_time\":"+J(details.lastTradeTime)+",\"time_zone\":"+J(details.timeZoneId)+
                    ",\"validation_error\":"+J(err)+",\"diagnostic_reference_used_for_signal\":false}");
                if(!err.empty())continue;
                ::Contract real=details.contract;real.exchange="SMART";ib.Subscribe(c.ticker,real);candidates.push_back(c);
            }
        }
        const auto end=std::chrono::steady_clock::now()+std::chrono::seconds(duration);std::size_t records=0,readyRecords=0;
        opt::Policy policy;policy.account=account;policy.owner="options-shadow";
        while(!stopped&&ib.Connected()&&std::chrono::steady_clock::now()<end&&opt::NewYorkDate(Now())==date){
            ib.Pump(1000);
            if(stopped || !ib.Connected())break;
            for(const auto& c:candidates){
                opt::Snapshot s;s.nowMs=Now();s.epoch=ib.epoch;s.account=account;s.owner=policy.owner;s.executionDomain="PAPER";
                s.option=ib.quotes[c.ticker].book;s.greeks=ib.quotes[c.ticker].greeks;
                const auto& u=ib.quotes[c.underlyingTicker];s.underlying=u.last;s.underlyingDataType=u.book.marketDataType;
                // Discovery cannot prove trading permission, margin, owner state,
                // or a risk budget. Defaults remain unknown/blocked, never true.
                const auto decision=opt::Evaluate(policy,c.contract,s,false);
                capture.Append(opt::EncodeObservation(policy,c.contract,s));records++;readyRecords+=decision.marketDataReady;
                if(records<=candidates.size()||records%(30*candidates.size())==0)
                    Emit("{\"event\":\"shadow_decision\",\"con_id\":"+std::to_string(c.contract.conId)+",\"decision\":"+opt::DecisionJson(decision)+"}");
            }
            if(candidates.empty())break;
        }
        if(stopped)throw std::runtime_error("OPTIONS_OBSERVATION_INTERRUPTED");
        if(!ib.Connected())throw std::runtime_error(ib.connectionFault.empty()?"OPTIONS_BROKER_DISCONNECTED":ib.connectionFault);
        Emit("{\"event\":\"options_readonly_complete\",\"records\":"+std::to_string(records)+",\"realtime_ready_records\":"+std::to_string(readyRecords)+
            ",\"trading_permission\":\"UNVERIFIED\",\"premium_budget\":\"UNSET\",\"broker_order_send_count\":0}");
        return records?0:2;
    }catch(const std::exception& e){Emit("{\"event\":\"options_readonly_failed\",\"reason\":"+J(e.what())+",\"broker_order_send_count\":0}");return 1;}
}
