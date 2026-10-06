#include "oms_economics_audit.h"
#include "../adapter_ib/ib_callback_economics.h"
#include <algorithm>
#include <cerrno>
#include <cmath>
#include <fcntl.h>
#include <iomanip>
#include <limits>
#include <locale>
#include <map>
#include <set>
#include <sstream>
#include <sys/stat.h>
#include <unistd.h>
#include <openssl/evp.h>

namespace hepta { namespace options {
namespace {
bool Safe(const std::string& s) {
    if(s.empty() || s.size()>512)return false;
    for(unsigned char c:s)if(c<32 || c==127)return false;
    return true;
}
bool Whole(double x) {return std::isfinite(x) && x>=0 && x<=100000 && std::floor(x)==x;}
std::string Side(const std::string& x) {return x=="BOT"?"BUY":x=="SLD"?"SELL":x;}
std::string Family(const std::string& s) {
    const auto dot=s.rfind('.');if(dot==std::string::npos || dot==0 || dot+1==s.size())return {};
    return s.find_first_not_of("0123456789",dot+1)==std::string::npos?s.substr(0,dot):std::string();
}
void Issue(OmsOptionAuditResult& r,const std::string& s) {
    if(std::find(r.issues.begin(),r.issues.end(),s)==r.issues.end())r.issues.push_back(s);
}
struct Command {const OmsJournalEvent* send=nullptr;std::size_t attempts=0;std::vector<const OmsJournalEvent*> fills;};
bool SameExecution(const OmsJournalEvent& a,const OmsJournalEvent& b) {
    const auto& x=a.brokerEconomics;const auto& y=b.brokerEconomics;
    // Download service/connection epochs and request ids may change. Broker
    // account/client/correlation/contract and actual economics may not.
    return a.account==b.account && a.executionDomain==b.executionDomain && a.venue==b.venue &&
        a.brokerClientId==b.brokerClientId && a.orderId==b.orderId &&
        a.venueCorrelationId==b.venueCorrelationId && Side(a.side)==Side(b.side) &&
        x.version==y.version && x.valid==y.valid && x.kind==y.kind &&
        x.contractId==y.contractId && x.contractIdentity==y.contractIdentity &&
        x.permanentOrderId==y.permanentOrderId && x.shares==y.shares &&
        x.executionPrice==y.executionPrice && x.cumulativeQuantity==y.cumulativeQuantity &&
        x.cumulativeAverage==y.cumulativeAverage && x.priceRevisionPending==y.priceRevisionPending &&
        a.qty==b.qty && a.price==b.price;
}
bool SameFee(const OmsJournalEvent& a,const OmsJournalEvent& b) {
    const auto& x=a.brokerEconomics;const auto& y=b.brokerEconomics;
    return a.executionDomain==b.executionDomain && a.venue==b.venue &&
        x.version==y.version && x.valid==y.valid && x.kind==y.kind &&
        x.commission==y.commission && x.commissionCurrency==y.commissionCurrency &&
        x.realizedPnlKnown==y.realizedPnlKnown && x.realizedPnl==y.realizedPnl;
}
bool Owned(const OmsJournalEvent& e,const OmsOptionAuditScope& s) {
    return e.venue=="IB" && e.executionDomain=="PAPER" && e.account==s.account &&
        e.source==(e.eventType=="place_send_attempt"?"agent.tool:":"agent:")+s.agentId && e.traceId==s.sessionId;
}
bool CallbackMatches(const OmsJournalEvent& e,const OmsJournalEvent& send,const OmsOptionAuditScope& s) {
    return e.venue=="IB" && e.executionDomain=="PAPER" && e.account==s.account &&
        e.brokerClientId==s.brokerClientId && Safe(e.brokerServiceEpoch) && e.brokerConnectionEpoch && e.orderId>=0 &&
        e.venueCorrelationId==send.venueCorrelationId &&
        (e.reqId.empty() || e.reqId==send.reqId) && (e.traceId.empty() || e.traceId==s.sessionId) &&
        (e.source=="ib-api-callback" || e.source=="agent:"+s.agentId) &&
        (e.instrument.empty() || e.instrument==send.instrument) &&
        (e.side.empty() || Side(e.side)==send.side);
}
std::string Fingerprint(const CanonicalOptionIdentity& c) {
    InstrumentRef r;r.symbol=c.symbol;r.secType="OPT";r.currency=c.currency;
    r.lastTradeDateOrContractMonth=c.expiry;r.right=c.right;r.strike=c.strikeMilli/1000.;
    r.multiplier=std::to_string(c.multiplier);r.tradingClass=c.tradingClass;r.localSymbol=c.localSymbol;
    return IbEconomicContractIdentity(r);
}
std::string Digest(const std::string& bytes) {
    unsigned char out[EVP_MAX_MD_SIZE];unsigned int size=0;
    EVP_MD_CTX* ctx=EVP_MD_CTX_new();if(!ctx)return {};
    const bool ok=EVP_DigestInit_ex(ctx,EVP_sha256(),nullptr)==1 &&
        EVP_DigestUpdate(ctx,bytes.data(),bytes.size())==1 && EVP_DigestFinal_ex(ctx,out,&size)==1;
    EVP_MD_CTX_free(ctx);if(!ok || size!=32)return {};
    std::ostringstream hex;hex.imbue(std::locale::classic());hex<<std::hex<<std::setfill('0');
    for(unsigned i=0;i<size;++i)hex<<std::setw(2)<<static_cast<unsigned>(out[i]);return hex.str();
}
}
OmsOptionAuditResult AuditOptionOms(const OmsOptionAuditScope& scope,const std::vector<OmsJournalEvent>& rows) {
    OmsOptionAuditResult r;r.records=rows.size();
    if(scope.account.size()<3 || scope.account.compare(0,2,"DU") || !Safe(scope.account) ||
       scope.account.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",2)!=std::string::npos || !Safe(scope.agentId) ||
       !Safe(scope.sessionId) || scope.brokerClientId<0 || scope.brokerClientId>65535 || scope.conId<=0 || !ValidateCanonicalOption(scope.identity).empty()) {
        Issue(r,"AUDIT_SCOPE_INVALID");return r;
    }
    const auto instrument="IBOPT:"+std::to_string(scope.conId);const auto fingerprint=Fingerprint(scope.identity);
    std::map<std::string,Command> commands;std::map<std::string,std::string> commandCorrelations;
    std::map<std::string,std::vector<const OmsJournalEvent*>> executions,fees,terminals;
    for(const auto& e:rows) {
        if(e.eventType=="place_send_attempt" && Owned(e,scope) && e.instrument==instrument) {
            ++r.sendAttempts;
            if(!Safe(e.reqId) || !Safe(e.requestHash) || !Safe(e.venueCorrelationId) ||
               (e.side!="BUY" && e.side!="SELL") || !Whole(e.qty) || e.qty<=0) {
                Issue(r,"NATIVE_SEND_IDENTITY_INCOMPLETE");continue;
            }
            auto& c=commands[e.venueCorrelationId];++c.attempts;
            if(c.send && (c.send->reqId!=e.reqId || c.send->requestHash!=e.requestHash || c.send->side!=e.side || c.send->qty!=e.qty))
                Issue(r,"NATIVE_CORRELATION_COMMAND_CONFLICT");
            else if(!c.send)c.send=&e;
            const auto old=commandCorrelations.find(e.reqId);
            if(old!=commandCorrelations.end() && old->second!=e.venueCorrelationId)Issue(r,"NATIVE_COMMAND_CORRELATION_CHANGED");
            commandCorrelations[e.reqId]=e.venueCorrelationId;
        }
        if(e.eventType=="broker_execution" && !e.brokerExecutionId.empty())executions[e.brokerExecutionId].push_back(&e);
        if(e.eventType=="broker_execution" && e.brokerExecutionId.empty() && e.account==scope.account &&
           e.brokerClientId==scope.brokerClientId && (e.instrument==instrument || e.brokerEconomics.contractId==scope.conId))
            Issue(r,"SCOPED_EXECUTION_ID_ABSENT");
        if(e.eventType=="broker_commission" && !e.brokerExecutionId.empty())fees[e.brokerExecutionId].push_back(&e);
        if(e.eventType=="broker_order_status" && !e.venueCorrelationId.empty())terminals[e.venueCorrelationId].push_back(&e);
        if(e.eventType=="broker_completed_order" && Owned(e,scope) && e.instrument==instrument)++r.ignoredCompletedOrders;
    }
    r.commands=commands.size();if(commands.empty())Issue(r,"NO_MATCHING_NATIVE_SEND");
    for(const auto& p:commands)if(p.second.attempts!=1)Issue(r,"NATIVE_SEND_ATTEMPT_COUNT_NOT_ONE");
    std::map<std::string,const OmsJournalEvent*> joined;std::map<std::string,std::string> families;
    for(const auto& p:executions) {
        bool relevant=false;
        for(const auto* e:p.second)if((e->account==scope.account && e->brokerClientId==scope.brokerClientId &&
             (e->instrument==instrument || e->brokerEconomics.contractId==scope.conId)) ||
             (e->account==scope.account && commands.count(e->venueCorrelationId)))relevant=true;
        if(!relevant)continue;
        const auto& e=*p.second.front();bool conflict=false;
        for(const auto* x:p.second)if(!SameExecution(e,*x))conflict=true;
        if(conflict){Issue(r,"EXECUTION_ID_COLLISION_OR_ECONOMIC_CONFLICT");continue;}
        r.duplicateExecutions+=p.second.size()-1;
        const auto c=commands.find(e.venueCorrelationId);
        bool ownerJoined=c!=commands.end();
        if(ownerJoined)for(const auto* variant:p.second)
            if(variant->brokerCallbackType!="execDetails" || !CallbackMatches(*variant,*c->second.send,scope))ownerJoined=false;
        if(!ownerJoined){Issue(r,"EXECUTION_NATIVE_OWNER_JOIN_UNPROVEN");continue;}
        const auto& econ=e.brokerEconomics;
        if(econ.Absent()){++r.legacyExecutions;Issue(r,"INDIVIDUAL_EXECUTION_EVIDENCE_ABSENT");continue;}
        if(!econ.ValidShape() || !econ.valid || econ.kind!="EXECUTION" || econ.priceRevisionPending ||
           econ.contractId!=scope.conId || econ.contractIdentity!=fingerprint ||
           !Whole(econ.shares) || econ.shares<=0 || !Whole(econ.cumulativeQuantity) ||
           econ.cumulativeQuantity>c->second.send->qty || e.qty!=econ.cumulativeQuantity || e.price!=econ.cumulativeAverage ||
           (Side(e.side)!="BUY" && Side(e.side)!="SELL")) {Issue(r,"EXECUTION_CONTRACT_OR_ECONOMICS_UNPROVEN");continue;}
        const auto family=Family(p.first);
        if(!family.empty()) {
            if(families.count(family) && families[family]!=p.first)Issue(r,"EXECUTION_CORRECTION_RECONCILIATION_REQUIRED");
            families[family]=p.first;
        }
        joined[p.first]=&e;c->second.fills.push_back(&e);
    }
    r.executions=joined.size();if(joined.empty())Issue(r,"NO_JOINED_INDIVIDUAL_EXECUTIONS");
    for(auto& p:commands) {
        auto& c=p.second;auto& fills=c.fills;
        std::sort(fills.begin(),fills.end(),[](const OmsJournalEvent* a,const OmsJournalEvent* b){
            return a->brokerEconomics.cumulativeQuantity<b->brokerEconomics.cumulativeQuantity;
        });
        double quantity=0,cost=0;long order=-1;std::int64_t permId=0;
        for(const auto* e:fills) {
            const auto& x=e->brokerEconomics;quantity+=x.shares;cost+=x.shares*x.executionPrice;
            if(quantity!=x.cumulativeQuantity || std::fabs(cost-quantity*x.cumulativeAverage)>1e-6*std::max(1.,std::fabs(cost)))
                Issue(r,"INDIVIDUAL_CUMULATIVE_CHECKPOINT_MISMATCH");
            if(order>=0 && order!=e->orderId)Issue(r,"COMMAND_BROKER_ORDER_CHANGED");order=e->orderId;
            if(permId && x.permanentOrderId && permId!=x.permanentOrderId)Issue(r,"COMMAND_PERMANENT_ORDER_CHANGED");
            if(x.permanentOrderId)permId=x.permanentOrderId;
        }
        const OmsJournalEvent* terminal=nullptr;
        for(const auto* receipt:terminals[p.first]) {
            const auto& e=*receipt;
            // completedOrder.qty is totalQuantity, never executed quantity.
            if(e.eventType!="broker_order_status" || e.venueCorrelationId!=p.first ||
               (e.status!="Filled" && e.status!="Cancelled" && e.status!="ApiCancelled" && e.status!="Rejected"))continue;
            if(!CallbackMatches(e,*c.send,scope) || (order>=0 && e.orderId!=order) || !Whole(e.qty) ||
               e.qty>c.send->qty || !std::isfinite(e.brokerRemainingQuantity) || e.brokerRemainingQuantity<0 ||
               (e.status=="Filled" && (e.qty!=c.send->qty || e.brokerRemainingQuantity!=0)) ||
               e.riskCode=="IB_FILLED_ECONOMIC_EVIDENCE_REQUIRED") {Issue(r,"TERMINAL_NATIVE_OWNER_OR_QUANTITY_UNPROVEN");continue;}
            if(terminal && (terminal->status!=e.status || terminal->qty!=e.qty))Issue(r,"TERMINAL_CALLBACK_CONFLICT");
            else terminal=&e;
        }
        if(!terminal || terminal->qty!=quantity)Issue(r,"TERMINAL_EXECUTION_JOIN_INCOMPLETE");
        if(c.send->side=="BUY"){r.boughtContracts+=quantity;r.executionCashFlowUsd-=cost*scope.identity.multiplier;}
        else {r.soldContracts+=quantity;r.executionCashFlowUsd+=cost*scope.identity.multiplier;}
    }
    r.executionCashFlowComplete=r.issues.empty();
    for(const auto& p:fees)if(!executions.count(p.first))++r.unmatchedFees;
    for(const auto& p:joined) {
        const auto fee=fees.find(p.first);
        if(fee==fees.end()){Issue(r,"EXECUTION_FEE_MISSING");continue;}
        const auto& f=*fee->second.front();bool valid=true;
        for(const auto* x:fee->second) {
            const auto& econ=x->brokerEconomics;
            if(!SameFee(f,*x) || x->brokerCallbackType!="commissionReport" || x->venue!="IB" || x->executionDomain!="PAPER" || x->orderId!=-1 ||
               x->brokerClientId!=-1 || x->source!="ib-api-callback" || !Safe(x->brokerServiceEpoch) || !x->brokerConnectionEpoch ||
               !x->account.empty() || !x->reqId.empty() || !x->clientReqId.empty() || !x->traceId.empty() ||
               !x->instrument.empty() || !x->side.empty() || !x->strategy.empty() || x->qty!=0 || x->price!=0 ||
               !x->venueCorrelationId.empty() || !econ.ValidShape() || !econ.valid || econ.kind!="COMMISSION" ||
               econ.commissionCurrency!="USD")valid=false;
        }
        if(!valid){Issue(r,"COMMISSION_RECEIPT_CONFLICT_OR_UNUSABLE");continue;}
        ++r.joinedFees;r.feesUsd+=f.brokerEconomics.commission;
    }
    r.feesComplete=r.executionCashFlowComplete && r.joinedFees==r.executions && r.executions>0;
    std::sort(r.issues.begin(),r.issues.end());return r;
}
std::string OmsOptionAuditResult::Json() const {
    std::ostringstream out;out.imbue(std::locale::classic());out.precision(17);
    out<<"{\"mode\":\"OFFLINE_READ_ONLY_ECONOMICS_AUDIT\",\"authorization_effect\":\"NONE\","
       "\"roundtrip_proven\":false,\"broker_position_verified\":false,\"broker_order_submission_enabled\":false,"
       "\"contract_binding_origin\":\"CALLER_SUPPLIED_FILTER_ONLY\",\"records\":"<<records<<
       ",\"send_attempts\":"<<sendAttempts<<",\"native_commands\":"<<commands<<",\"joined_executions\":"<<executions<<
       ",\"duplicate_executions\":"<<duplicateExecutions<<",\"joined_fees\":"<<joinedFees<<
       ",\"unassigned_commission_ids\":"<<unmatchedFees<<",\"legacy_executions\":"<<legacyExecutions<<
       ",\"completed_order_quantities_ignored\":"<<ignoredCompletedOrders<<
       ",\"execution_cash_flow_complete\":"<<(executionCashFlowComplete?"true":"false")<<
       ",\"fees_complete\":"<<(feesComplete?"true":"false")<<",\"execution_cash_flow_usd\":";
    if(executionCashFlowComplete)out<<executionCashFlowUsd;else out<<"null";
    out<<",\"commission_usd\":";if(feesComplete)out<<feesUsd;else out<<"null";
    out<<",\"net_execution_cash_flow_usd\":";if(feesComplete)out<<executionCashFlowUsd-feesUsd;else out<<"null";
    out<<",\"reported_net_contracts\":";if(executionCashFlowComplete)out<<boughtContracts-soldContracts;else out<<"null";
    out<<",\"realized_pnl_usd\":null,\"issues\":[";
    for(std::size_t i=0;i<issues.size();++i){if(i)out<<',';out<<'"'<<EscapeJson(issues[i])<<'"';}out<<"]}";return out.str();
}
bool ReadPrivateOmsAuditInput(const std::string& path,std::vector<OmsJournalEvent>& rows,std::string& hash,std::string& reason) {
    rows.clear();hash.clear();reason.clear();
    if(path.empty() || path[0]!='/'){reason="AUDIT_ABSOLUTE_PATH_REQUIRED";return false;}
    const int fd=::open(path.c_str(),O_RDONLY|O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK);
    if(fd<0){reason="AUDIT_INPUT_OPEN_FAILED";return false;}
    struct Close {int fd;~Close(){::close(fd);}}close{fd};struct stat before{},after{},named{};
    if(fstat(fd,&before) || !S_ISREG(before.st_mode) || before.st_uid!=geteuid() ||
       (before.st_mode&077)!=0 || before.st_nlink!=1 || before.st_size<=0 || before.st_size>64*1024*1024) {
        reason="AUDIT_PRIVATE_REGULAR_INPUT_REQUIRED";return false;
    }
    std::string data;data.reserve(static_cast<std::size_t>(before.st_size));char buffer[32768];
    while(data.size()<static_cast<std::size_t>(before.st_size)) {
        const auto n=::read(fd,buffer,std::min(sizeof(buffer),static_cast<std::size_t>(before.st_size)-data.size()));
        if(n<0 && errno==EINTR)continue;if(n<=0){reason="AUDIT_INPUT_READ_FAILED";return false;}data.append(buffer,n);
    }
    if(fstat(fd,&after) || lstat(path.c_str(),&named) || before.st_dev!=named.st_dev || before.st_ino!=named.st_ino ||
       before.st_size!=after.st_size || before.st_mtim.tv_sec!=after.st_mtim.tv_sec || before.st_mtim.tv_nsec!=after.st_mtim.tv_nsec ||
       before.st_ctim.tv_sec!=after.st_ctim.tv_sec || before.st_ctim.tv_nsec!=after.st_ctim.tv_nsec) {
        reason="AUDIT_INPUT_CHANGED_USE_IMMUTABLE_COPY";return false;
    }
    if(data.back()!='\n'){reason="AUDIT_TRUNCATED_TAIL";return false;}
    std::vector<OmsJournalEvent> parsed;std::size_t start=0;
    while(start<data.size()) {
        const auto end=data.find('\n',start);
        if(end==start || end-start>256*1024 || parsed.size()>=65536){reason="AUDIT_RECORD_LIMIT_OR_EMPTY";return false;}
        OmsJournalEvent row;
        if(!OmsJournal::ParseJsonLine(data.substr(start,end-start),row)){reason="AUDIT_NATIVE_RECORD_INVALID:"+std::to_string(parsed.size()+1);return false;}
        parsed.push_back(std::move(row));start=end+1;
    }
    hash=Digest(data);if(hash.empty()){reason="AUDIT_HASH_FAILED";return false;}rows=std::move(parsed);return true;
}
} }
