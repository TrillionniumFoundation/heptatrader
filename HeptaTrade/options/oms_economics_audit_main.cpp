#include "oms_economics_audit.h"
#include <iostream>
#include <limits>
#include <map>
#include <stdexcept>
using namespace hepta::options;
namespace {
std::int64_t Positive(const std::string& value,bool zero=false) {
    if(value.empty() || value.size()>18 || value.find_first_not_of("0123456789")!=std::string::npos)
        throw std::runtime_error("AUDIT_INVALID_INTEGER");
    const auto n=std::stoll(value);if(n<0 || (!zero && n==0))throw std::runtime_error("AUDIT_INVALID_INTEGER");return n;
}
}
int main(int argc,char** argv) {
    try {
        if(argc==2 && std::string(argv[1])=="--help") {
            std::cout<<"hepta-options-oms-audit --journal PRIVATE_COPY --account DU... --agent OWNER "
                "--session SESSION --client-id N --con-id N --occ '21-character OCC'\n"
                "Offline report only. Caller-supplied contract filter; no Broker/position/PnL/trading proof.\n";return 0;
        }
        std::map<std::string,std::string> a;
        for(int i=1;i<argc;++i) {
            const std::string key=argv[i];
            if(i+1>=argc || (key!="--journal" && key!="--account" && key!="--agent" && key!="--session" &&
                key!="--client-id" && key!="--con-id" && key!="--occ") || a.count(key))throw std::runtime_error("AUDIT_ARGUMENT_INVALID");
            a[key]=argv[++i];
        }
        if(a.size()!=7)throw std::runtime_error("AUDIT_ARGUMENTS_MISSING");
        OmsOptionAuditScope scope;scope.account=a.at("--account");scope.agentId=a.at("--agent");scope.sessionId=a.at("--session");
        const auto client=Positive(a.at("--client-id"),true);if(client>65535)throw std::runtime_error("AUDIT_CLIENT_OUT_OF_RANGE");
        scope.brokerClientId=static_cast<int>(client);scope.conId=Positive(a.at("--con-id"));std::string reason,hash;
        if(!ParseCanonicalOcc(a.at("--occ"),scope.identity,reason))throw std::runtime_error(reason);
        std::vector<OmsJournalEvent> records;
        if(!ReadPrivateOmsAuditInput(a.at("--journal"),records,hash,reason))throw std::runtime_error(reason);
        auto report=AuditOptionOms(scope,records).Json();report.pop_back();
        std::cout<<report<<",\"input_sha256\":\""<<hash<<"\"}\n";
        return 0; // Report generation succeeded, NOT trading/economic qualification.
    } catch(const std::exception& e) {
        std::cout<<"{\"mode\":\"OFFLINE_READ_ONLY_ECONOMICS_AUDIT\",\"input_valid\":false,"
            "\"authorization_effect\":\"NONE\",\"roundtrip_proven\":false,\"error\":\""<<EscapeJson(e.what())<<"\"}\n";return 1;
    }
}
