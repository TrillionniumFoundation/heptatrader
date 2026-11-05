// Offline input-only inspection. This executable has no broker/runtime owner.
#include "execution/ib_paper_fx_cash_restart_catchup.h"
#include <openssl/sha.h>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
static int reject(const char* reason) {
    std::cout<<"{\"status\":\"INVALID_INPUT\",\"reason\":\""<<reason
             <<"\",\"effect_authority\":\"NONE\",\"qualification\":false}\n";
    return 2;
}
int main(int argc,char**) {
    if(argc!=1)return reject("STDIN_ONLY_NO_PATH_OR_RUNTIME_ARGUMENTS");
    const std::size_t maximumBytes=1024*1024;
    std::string bytes;char block[4096];
    while(std::cin) {
        std::cin.read(block,sizeof(block));bytes.append(block,static_cast<std::size_t>(std::cin.gcount()));
        if(bytes.size()>maximumBytes)return reject("INPUT_BYTE_BOUND");
    }
    if(!std::cin.eof())return reject("INPUT_READ_FAILED");
    std::istringstream input(bytes);std::string magic;
    if(!(input>>magic)||magic!="HEPTA_FX_DIAGNOSTIC_V1")return reject("FORMAT_VERSION");
    IbPaperFxCashRestartCatchUpContext c;std::size_t count=0;
    if(!(input>>std::quoted(c.toolSource)>>std::quoted(c.brokerSource)>>
        std::quoted(c.account)>>std::quoted(c.executionDomain)>>c.maxGrossPosition>>
        c.brokerClientId>>count)||count==0||count>64)return reject("CONTEXT_OR_BASELINE_BOUND");
    std::map<std::string,IbPaperFxCashBaseline> baselines;
    for(std::size_t i=0;i<count;++i) {
        IbPaperFxCashBaseline b;
        if(!(input>>std::quoted(b.account)>>std::quoted(b.instrument)>>std::quoted(b.currency)>>
            b.baselineCashBalance>>b.observedCashBalance>>b.campaignExecutionDelta>>b.observedAtMs))
            return reject("BASELINE_PARSE");
        if(!baselines.emplace(b.instrument,b).second)return reject("DUPLICATE_BASELINE");
    }
    if(!(input>>count)||count>4096)return reject("EVENT_COUNT_BOUND");
    std::vector<OmsJournalEvent> events;events.reserve(count);
    for(std::size_t i=0;i<count;++i) {
        OmsJournalEvent e;
        if(!(input>>std::quoted(e.eventType)>>e.tsMs>>e.orderId>>std::quoted(e.source)>>
            std::quoted(e.traceId)>>std::quoted(e.account)>>std::quoted(e.executionDomain)>>
            std::quoted(e.strategy)>>std::quoted(e.instrument)>>std::quoted(e.side)>>
            std::quoted(e.venueCorrelationId)>>std::quoted(e.reqId)>>std::quoted(e.brokerServiceEpoch)>>
            e.brokerConnectionEpoch>>e.brokerClientId>>std::quoted(e.brokerExecutionId)>>
            e.qty>>e.price>>std::quoted(e.status)>>std::quoted(e.brokerCallbackType)>>
            e.brokerRemainingQuantity))return reject("EVENT_PARSE");
        events.push_back(e);
    }
    input>>std::ws;if(!input.eof())return reject("TRAILING_INPUT");
    unsigned char digest[SHA256_DIGEST_LENGTH];
    SHA256(reinterpret_cast<const unsigned char*>(bytes.data()),bytes.size(),digest);
    std::ostringstream hash;hash<<std::hex<<std::setfill('0');
    for(unsigned char value:digest)hash<<std::setw(2)<<static_cast<unsigned>(value);
    const auto result=InspectIbPaperFxCashRestartCatchUp(events,c,baselines);
    const bool complete=result.disposition==IbPaperFxCashCatchUpDisposition::Complete;
    const bool pending=result.disposition==IbPaperFxCashCatchUpDisposition::AwaitOriginalOrderEvidence;
    std::cout<<"{\"schema\":\"hepta.fx_restart_offline_inspection.v1\",\"status\":\""
             <<(complete?"COMPLETE":pending?"AWAIT_ORIGINAL_ORDER_EVIDENCE":"INVALID_EVIDENCE")
             <<"\",\"reason\":\""<<result.reason<<"\",\"pending_count\":"<<result.pending.size()
             <<",\"input_bytes\":"<<bytes.size()<<",\"input_sha256\":\""<<hash.str()
             <<"\",\"source_projection_provenance_required\":true,"
             "\"effect_authority\":\"NONE\",\"broker_requests\":0,\"ledger_writes\":0,"
             "\"model_forwards\":0,\"promotion_authority\":false,\"qualification\":false}\n";
    return complete?0:pending?3:2;
}
