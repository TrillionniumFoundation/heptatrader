#include "../tool_host/session_supervisor_lease_store.h"

#include <iostream>
#include <openssl/evp.h>
#include <string>
#include <unistd.h>

namespace {
std::string Hash(const std::string& value)
{
    unsigned char digest[EVP_MAX_MD_SIZE]; unsigned int size = 0;
    if (EVP_Digest(value.data(), value.size(), digest, &size, EVP_sha256(), nullptr) != 1)
        throw std::runtime_error("CATALOG_HASH_FAILED");
    static const char hex[] = "0123456789abcdef";
    std::string result;
    for (unsigned int i = 0; i < size; ++i) {
        result.push_back(hex[digest[i] >> 4]); result.push_back(hex[digest[i] & 15]);
    }
    return result;
}
}

// This executable deliberately links only the encrypted-store leaf. It cannot
// send an Execution request, create a lease or persist an inspected catalog.
int main(int argc, char** argv)
{
    if (::geteuid() == 0 || ::getegid() == 0 || argc != 7 ||
        std::string(argv[1]) != "--store" || std::string(argv[3]) != "--key" ||
        std::string(argv[5]) != "--cleanup-lock") {
        std::cerr << "CATALOG_USER_SCOPE_USAGE_REQUIRED\n"; return 2;
    }
    try {
        SessionSupervisorLeaseStore store; std::string reason;
        if (!store.LoadExisting(argv[2], argv[4], argv[6],
                ::geteuid(), ::getegid(), reason)) {
            std::cerr << reason << '\n'; return 2;
        }
        const auto records = store.List();
        std::cout << std::boolalpha
            << "{\"schema\":\"heptatrader.gateway-catalog.v1\",\"records\":[";
        bool first = true;
        for (const auto& r : records) {
            if (!first) std::cout << ','; first = false;
            std::cout << "{\"paper\":" << (r.templateId == "paper")
                << ",\"watch\":" << (r.templateId == "watch")
                << ",\"bootstrap_issuer\":" << (r.issuer == "hepta.os.bootstrap")
                << ",\"peer_uid\":" << r.peerUid
                << ",\"token_sha256\":\"" << Hash(r.token)
                << "\",\"agent_sha256\":\"" << Hash(r.agentId)
                << "\",\"session_sha256\":\"" << Hash(r.sessionId)
                << "\",\"account_sha256\":\"" << Hash(r.ownerAccount)
                << "\",\"domain_sha256\":\"" << Hash(r.ownerExecutionDomain)
                << "\",\"predecessor_sha256\":\""
                << (r.predecessorToken.empty() ? "" : Hash(r.predecessorToken))
                << "\",\"recovery_command_sha256\":\""
                << (r.recoveryCommandId.empty() ? "" : Hash(r.recoveryCommandId))
                << "\",\"generation\":" << r.leaseGeneration
                << ",\"expires_at_ms\":" << r.expiresAtMs
                << ",\"recovery_only\":" << r.recoveryOnly
                << ",\"fence_pending\":" << r.fencePending
                << ",\"fence_complete\":" << r.fenceComplete
                << ",\"finalization_required\":" << r.paperFinalizationRequired
                << ",\"finalization_state\":"
                << static_cast<unsigned int>(r.paperFinalizationState) << '}';
        }
        std::cout << "]}\n";
    } catch (const std::exception&) {
        std::cerr << "CATALOG_INSPECTION_FAILED\n"; return 2;
    }
    return 0;
}
