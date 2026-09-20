#include "research_intent_client.h"
#include "../tools/trading_tool_wire_contract.h"
#include <cerrno>
#include <cstdint>
#include <fcntl.h>
#include <limits>
#include <map>
#include <sys/stat.h>
#include <unistd.h>

namespace {
const char* const kStoredToken = "hepta-research-outbox-v1-not-a-credential";
bool Fail(std::string& reason,const char* code) {reason=code;return false;}
struct Fd {
    int value;
    explicit Fd(int fd):value(fd) {}
    ~Fd(){if(value>=0)::close(value);}
    Fd(const Fd&)=delete; Fd& operator=(const Fd&)=delete;
};
void Space(const std::string& s,std::size_t& i) {while(i<s.size() && (s[i]==' '||s[i]=='\n'||s[i]=='\r'||s[i]=='\t'))++i;}
// Slice top-level fields ONLY AFTER the canonical envelope parser has validated
// the complete JSON (including nested duplicate keys and number grammar).
// Escaped field names/receipt strings fail closed; no substring key search.
bool Fields(const std::string& s,std::map<std::string,std::string>& out) {
    std::size_t i=0; Space(s,i); if(i==s.size()||s[i++]!='{')return false;
    for (;;) {
        Space(s,i); if(i>=s.size())return false; if(s[i]=='}')return true;
        if(s[i++]!='"')return false;
        const auto keyStart=i;
        while(i<s.size() && s[i]!='"') {if(s[i]=='\\')return false;++i;}
        if(i==s.size())return false;
        const std::string key=s.substr(keyStart,i-keyStart);++i;Space(s,i);
        if(i==s.size()||s[i++]!=':')return false;
        Space(s,i);const auto start=i;int depth=0;bool quoted=false,escape=false;
        for(;i<s.size();++i) {
            char c=s[i];
            if(quoted) {if(escape)escape=false;else if(c=='\\')escape=true;else if(c=='"')quoted=false;continue;}
            if(c=='"'){quoted=true;continue;}
            if(c=='['||c=='{'){++depth;continue;}
            if((c=='}'||c==',') && depth==0)break;
            if(c==']'||c=='}')--depth;
        }
        if(i==s.size())return false;
        auto end=i;while(end>start && (s[end-1]==' '||s[end-1]=='\n'||s[end-1]=='\r'||s[end-1]=='\t'))--end;
        if(!out.emplace(key,s.substr(start,end-start)).second)return false;
        if(s[i]=='}')return true;
        ++i;
    }
}
bool ReceiptString(const std::map<std::string,std::string>& fields,const char* key,std::string& out) {
    auto it=fields.find(key);if(it==fields.end())return false;const auto& s=it->second;
    if(s.size()<3||s.front()!='"'||s.back()!='"'||s.find('\\')!=std::string::npos)return false;
    out=s.substr(1,s.size()-2);return true;
}
bool PositiveTime(const std::string& s,std::int64_t& out) {
    if(s.empty())return false;
    std::uint64_t n=0;
    for(char c:s){if(c<'0'||c>'9')return false;const unsigned d=c-'0';
        if(n>(static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max())-d)/10)return false;
        n=n*10+d;}
    out=static_cast<std::int64_t>(n);return n>0;
}
int OpenDirectory(const std::string& directory) {
    const int fd=::open(directory.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    if(fd<0)return -1;
    struct stat s;
    if(::fstat(fd,&s)!=0||!S_ISDIR(s.st_mode)||s.st_uid!=::geteuid()||(s.st_mode&07777)!=0700){::close(fd);return -1;}
    return fd;
}
bool ReadRecord(int dir,const std::string& name,std::string& bytes,bool sync=false) {
    Fd f(::openat(dir,name.c_str(),O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC));if(f.value<0)return false;
    struct stat before,after,path;
    if(::fstat(f.value,&before)!=0||!S_ISREG(before.st_mode)||before.st_uid!=::geteuid()||
        before.st_nlink!=1||(before.st_mode&07777)!=0600||before.st_size<5||before.st_size>65536)return false;
    bytes.assign(static_cast<std::size_t>(before.st_size),'\0');std::size_t done=0;
    while(done<bytes.size()) {
        const auto n=::read(f.value,&bytes[done],bytes.size()-done);
        if(n<0&&errno==EINTR)continue;
        if(n<=0)return false;
        done+=static_cast<std::size_t>(n);
    }
    char extra;
    if(::read(f.value,&extra,1)!=0||::fstat(f.value,&after)!=0||::fstatat(dir,name.c_str(),&path,AT_SYMLINK_NOFOLLOW)!=0)return false;
    if(sync && ::fsync(f.value)!=0)return false;
    return before.st_dev==after.st_dev&&before.st_ino==after.st_ino&&before.st_size==after.st_size&&
        before.st_mode==after.st_mode&&before.st_nlink==after.st_nlink&&before.st_uid==after.st_uid&&
        before.st_mtim.tv_sec==after.st_mtim.tv_sec&&before.st_mtim.tv_nsec==after.st_mtim.tv_nsec&&
        before.st_ctim.tv_sec==after.st_ctim.tv_sec&&before.st_ctim.tv_nsec==after.st_ctim.tv_nsec&&
        after.st_dev==path.st_dev&&after.st_ino==path.st_ino&&S_ISREG(path.st_mode)&&path.st_nlink==1;
}
}
ResearchIntentClient::ResearchIntentClient(const NativeToolClientConfig& client,const hepta::research::ProposalLimits& limits)
    :client_(client),limits_(limits) {}
bool ResearchIntentClient::Prepare(const hepta::research::BoundedOrderProposal& p,const InstrumentRef& contract,
    std::int64_t now,ResearchPreparedOrder& prepared,NativeToolClientResult& result,std::string& reason) const {
    prepared=ResearchPreparedOrder();result=NativeToolClientResult();
    if(!hepta::research::ValidateProposal(p,limits_,now,reason))return false;
    if(!TradingToolWireContract::IsCanonicalCommandId(p.proposalId))return Fail(reason,"RESEARCH_PREVIEW_ID_INVALID");
    if(contract.symbol.empty()||contract.currency.empty()||contract.secType.empty()||contract.exchange.empty()||
        !contract.primaryExchange.empty()||!contract.lastTradeDateOrContractMonth.empty()||
        !contract.right.empty()||contract.strike!=0.0||!contract.multiplier.empty()||
        !contract.tradingClass.empty()||!contract.localSymbol.empty())
        return Fail(reason,"RESEARCH_CONTRACT_NOT_SUPPORTED_BY_WIRE");
    TradingToolHostRequest request;request.sessionToken=kStoredToken;request.toolCallId=p.proposalId;
    request.call.name="risk.preview_order";request.call.instrument=p.instrument;request.call.ibContract=contract;
    request.call.ibOrder.action=p.side==1?"BUY":"SELL";request.call.ibOrder.orderType="LMT";
    request.call.ibOrder.totalQuantity=static_cast<double>(p.quantity);request.call.ibOrder.lmtPrice=p.limitPrice;
    request.call.timeInForce="DAY";request.call.referencePrice=p.limitPrice;request.call.expiresAtMs=p.expiresAtMs;
    std::string validation;
    if(!TypedToolProtocol::EncodeRequest(request,validation,reason))return false;
    if(!client_.Call(request,result,reason))return false;
    TypedToolResultEnvelope envelope;
    if(!TypedToolProtocol::DecodeResultEnvelope(result.responseJson,envelope,reason))return false;
    if(envelope.status!="ok"||envelope.toolName!="risk.preview_order")return Fail(reason,"RESEARCH_PREVIEW_NOT_APPROVED");
    std::map<std::string,std::string> fields;
    std::string command,permit;std::int64_t permitExpiry=0;
    if(!Fields(envelope.payloadJson,fields)||fields["approved"]!="true"||fields["single_use"]!="true"||
        !ReceiptString(fields,"command_id",command)||!TradingToolWireContract::IsCanonicalCommandId(command)||
        !ReceiptString(fields,"preview_permit",permit)||permit.size()>80||
        !PositiveTime(fields["permit_expires_at_ms"],permitExpiry)||permitExpiry<=now)
        return Fail(reason,"RESEARCH_PREVIEW_RECEIPT_INVALID");
    request.toolCallId=command;request.call.name="trade.place_order";request.call.previewPermit=permit;
    std::string encoded;if(!TypedToolProtocol::EncodeRequest(request,encoded,reason))return false;
    prepared.request_=request;prepared.bytes_="HRO1"+encoded;reason.clear();return true;
}
bool ResearchIntentClient::Persist(const std::string& directory,ResearchPreparedOrder& prepared,std::string& reason) {
    prepared.persisted_=false;
    if(!prepared.Ready()||!TradingToolWireContract::IsCanonicalCommandId(prepared.CommandId()))return Fail(reason,"RESEARCH_OUTBOX_UNPREPARED");
    Fd dir(OpenDirectory(directory));if(dir.value<0)return Fail(reason,"RESEARCH_OUTBOX_DIRECTORY_UNSAFE");
    const std::string name=prepared.CommandId()+".hro";
    const int raw=::openat(dir.value,name.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
    if(raw<0) {
        if(errno!=EEXIST)return Fail(reason,"RESEARCH_OUTBOX_CREATE_FAILED");
        std::string existing;if(!ReadRecord(dir.value,name,existing,true)||existing!=prepared.bytes_)
            return Fail(reason,"RESEARCH_OUTBOX_CONFLICT_OR_UNSAFE");
        // Re-sync an existing immutable record as well as its directory entry.
        if(::fsync(dir.value)!=0)return Fail(reason,"RESEARCH_OUTBOX_SYNC_FAILED");
    } else {
        Fd file(raw);std::size_t done=0;bool ok=true;
        while(done<prepared.bytes_.size()) {
            const auto n=::write(file.value,prepared.bytes_.data()+done,prepared.bytes_.size()-done);
            if(n<0&&errno==EINTR)continue;
            if(n<=0){ok=false;break;}
            done+=static_cast<std::size_t>(n);
        }
        if(!ok||::fsync(file.value)!=0||::fsync(dir.value)!=0)return Fail(reason,"RESEARCH_OUTBOX_SYNC_FAILED");
        std::string reread;if(!ReadRecord(dir.value,name,reread)||reread!=prepared.bytes_)return Fail(reason,"RESEARCH_OUTBOX_VERIFY_FAILED");
    }
    prepared.persisted_=true;reason.clear();return true;
}
bool ResearchIntentClient::Load(const std::string& directory,const std::string& commandId,ResearchPreparedOrder& prepared,std::string& reason) {
    prepared=ResearchPreparedOrder();
    if(!TradingToolWireContract::IsCanonicalCommandId(commandId))return Fail(reason,"RESEARCH_OUTBOX_ID_INVALID");
    Fd dir(OpenDirectory(directory));if(dir.value<0)return Fail(reason,"RESEARCH_OUTBOX_DIRECTORY_UNSAFE");
    std::string bytes;if(!ReadRecord(dir.value,commandId+".hro",bytes,true)||bytes.compare(0,4,"HRO1")!=0)
        return Fail(reason,"RESEARCH_OUTBOX_READ_FAILED");
    if(::fsync(dir.value)!=0)return Fail(reason,"RESEARCH_OUTBOX_SYNC_FAILED");
    TradingToolHostRequest request;if(!TypedToolProtocol::DecodeRequest(bytes.substr(4),request,reason))return false;
    if(request.call.name!="trade.place_order"||request.toolCallId!=commandId||request.sessionToken!=kStoredToken||request.call.previewPermit.empty())
        return Fail(reason,"RESEARCH_OUTBOX_BINDING_INVALID");
    std::string roundtrip;if(!TypedToolProtocol::EncodeRequest(request,roundtrip,reason)||"HRO1"+roundtrip!=bytes)
        return Fail(reason,"RESEARCH_OUTBOX_NONCANONICAL");
    prepared.request_=request;prepared.bytes_=bytes;prepared.persisted_=true;reason.clear();return true;
}
bool ResearchIntentClient::Submit(const ResearchPreparedOrder& prepared,NativeToolClientResult& result,std::string& reason) const {
    result=NativeToolClientResult();
    if(!prepared.Ready()||!prepared.Persisted())return Fail(reason,"RESEARCH_OUTBOX_NOT_DURABLE");
    // Expiry is deliberately NOT refreshed or locally re-authorized on retry.
    return client_.Call(prepared.request_,result,reason);
}
bool ResearchIntentClient::Status(const std::string& commandId,const std::string& requestId,NativeToolClientResult& result,std::string& reason) const {
    result=NativeToolClientResult();
    if(!TradingToolWireContract::IsCanonicalCommandId(commandId)||!TradingToolWireContract::IsCanonicalCommandId(requestId))
        return Fail(reason,"RESEARCH_STATUS_ID_INVALID");
    TradingToolHostRequest request;request.toolCallId=requestId;request.call.name="execution.get_command_status";
    request.call.targetCommandId=commandId;return client_.Call(request,result,reason);
}
