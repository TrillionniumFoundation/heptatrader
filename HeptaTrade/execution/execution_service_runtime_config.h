#pragma once

#include "execution_gateway_context_binding.h"

#include <cstddef>
#include <cstdint>
#include <map>
#include <set>
#include <string>

enum class ExecutionServiceRuntimeMode
{
    Disabled = 0,
    Simulator
};

struct ExecutionServiceRuntimeConfig
{
    ExecutionServiceRuntimeMode mode = ExecutionServiceRuntimeMode::Disabled;
    int listenFd = -1;
    int eventListenFd = -1;
    std::set<std::uint32_t> allowedGatewayUids;
    ExecutionGatewayContextBinding gatewayContextBinding;
    std::string stateDirectory;
    std::string journalPath;
    std::string fenceCredentialPath;
    std::size_t maxRequestBytes = 32768;
    int ioTimeoutMs = 3000;
    // Simulator quotes are owned and refreshed only by the Execution daemon.
    // These values are constructor inputs (not Agent/session inputs); tests
    // shorten them to prove behavior beyond the original quote TTL.
    std::uint64_t simulatorQuoteTtlMs = 60000;
    std::uint64_t simulatorQuoteRefreshIntervalMs = 10000;

    bool simulatorOrderSubmissionEnabled = true;
    bool simulatorGlobalKillSwitch = false;
    bool simulatorFlattenOnly = false;
    double simulatorMaxOrderQuantity = 25000.0;
    double simulatorMaxOrderNotional = 250000.0;
    std::size_t simulatorMaxOrdersPerMinute = 30;
    std::size_t simulatorMaxActiveOrders = 50;
    double simulatorMaxGrossPosition = 100000.0;
    double simulatorMaxPriceDeviationBps = 30.0;

    bool Enabled() const;
    bool Validate(std::string& reason) const;

    static const char* ModeName(ExecutionServiceRuntimeMode mode);
    static const char* ActivatedSocketName();
    static const char* EventActivatedSocketName();
    static const char* FenceCredentialName();

    static bool FromEnvironment(int currentPid,
                                ExecutionServiceRuntimeConfig& config,
                                std::string& reason);
    static bool FromValues(const std::map<std::string, std::string>& values,
                           int currentPid,
                           ExecutionServiceRuntimeConfig& config,
                           std::string& reason);
};
