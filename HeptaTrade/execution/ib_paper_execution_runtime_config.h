#pragma once

#include "execution_gateway_context_binding.h"
#include "ib_paper_execution_profile.h"

#include <cstddef>
#include <cstdint>
#include <map>
#include <set>
#include <string>

enum class IbPaperExecutionRuntimeMode
{
    Disabled = 0,
    Paper
};

struct IbPaperFxCashBaseline
{
    std::string account;
    std::string instrument;
    std::string currency;
    double baselineCashBalance = 0.0;
    double observedCashBalance = 0.0;
    double campaignExecutionDelta = 0.0;
    std::uint64_t observedAtMs = 0;
    std::string proof;
};

// Process-bound configuration for the broker-owning IB PAPER execution daemon.
// The runtime is deliberately separate from the Simulator execution daemon and
// cannot be enabled without systemd socket activation and a complete PAPER
// profile.
struct IbPaperExecutionRuntimeConfig
{
    IbPaperExecutionRuntimeMode mode = IbPaperExecutionRuntimeMode::Disabled;
    int listenFd = -1;
    int eventListenFd = -1;
    std::set<std::uint32_t> allowedGatewayUids;
    ExecutionGatewayContextBinding gatewayContextBinding;

    IbPaperExecutionProfileConfig profile;
    std::string stateDirectory;
    std::string journalPath;
    std::string controlDirectory;
    std::string fenceCredentialPath;
    std::string fxCashBaselineCredentialPath;
    std::string authorizationCredentialPath;

    // Broker market-data authority belongs to the Execution Service.  The
    // Agent/Gateway may select only one of these reviewed instrument keys; it
    // never supplies a raw broker contract or request id.
    std::map<std::string, InstrumentRef> quoteContracts;
    // Populated from the execution-owned, mode-0600 reconciliation record
    // before broker startup (or directly by offline tests). It is never
    // inferred from the current balance when prior executions exist.
    std::map<std::string, IbPaperFxCashBaseline> fxCashBaselines;
    std::string primaryQuoteInstrument;
    std::uint64_t quoteMaxAgeMs = 5000;

    std::size_t maxRequestBytes = 32768;
    int ioTimeoutMs = 3000;
    int readinessTimeoutMs = 10000;
    // Total wall-clock budget for a broker reconnect, including local API
    // transport retries, upstream 1101/1102 recovery, and one authoritative
    // snapshot rebuild.  This is deliberately independent of the normal
    // snapshot readiness timeout because IB upstream recovery can take much
    // longer than a healthy snapshot cycle.
    int reconnectTimeoutMs = 180000;

    bool Enabled() const;
    bool Validate(std::string& reason) const;
    bool ValidateProductionIdentity(std::uint32_t effectiveServiceUid,
                                    std::string& reason) const;

    static const char* ModeName(IbPaperExecutionRuntimeMode mode);
    static const char* ActivatedSocketName();
    static const char* EventActivatedSocketName();
    static const char* FenceCredentialName();
    static const char* FxCashBaselineCredentialName();

    static bool FromEnvironment(int currentPid,
                                IbPaperExecutionRuntimeConfig& config,
                                std::string& reason);
    static bool FromValues(const std::map<std::string, std::string>& values,
                           int currentPid,
                           IbPaperExecutionRuntimeConfig& config,
                           std::string& reason);
};
