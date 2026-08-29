#pragma once

#include <cstddef>
#include <cstdint>
#include <map>
#include <string>

enum class ExecutionGatewayMode
{
    Disabled = 0,
    Simulator,
    Paper,
    Invalid
};

struct ExecutionGatewayRuntimeConfig
{
    ExecutionGatewayMode mode = ExecutionGatewayMode::Disabled;
    std::string executionSocket;
    std::string eventSocket;
    std::uint32_t executionServiceUid = 0;
    bool executionServiceUidConfigured = false;
    int ioTimeoutMs = 1000;
    std::size_t maxResponseBytes = 32768;
    bool limitsValid = true;
    bool mutationToolsEnabled = false;
    // Exact discriminator for the externally managed one-connector PAPER
    // canary.  Its system health is execution-authoritative; legacy local
    // PAPER and Simulator health remain gateway-local.
    bool externalP1CanaryLimitDay = false;
    bool flagsValid = true;

    bool Enabled() const;
    const char* ModeName() const;
    bool Validate(std::string& reason) const;

    static ExecutionGatewayRuntimeConfig FromEnvironment();
    static ExecutionGatewayRuntimeConfig FromValues(
        const std::map<std::string, std::string>& values);
};
