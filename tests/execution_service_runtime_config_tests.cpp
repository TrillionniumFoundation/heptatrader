#include "../HeptaTrade/execution/execution_service_runtime_config.h"

#include <cassert>
#include <iostream>
#include <map>
#include <string>

namespace
{
std::map<std::string, std::string> ValidValues()
{
    std::map<std::string, std::string> values;
    values["HEPTA_EXECUTION_SERVICE_MODE"] = "SIMULATOR";
    values["HEPTA_EXECUTION_GATEWAY_UID"] = "2001";
    values["HEPTA_EXECUTION_GATEWAY_AGENT_ID"] = "codex-agent-os-e2e";
    values["LISTEN_PID"] = "1234";
    values["LISTEN_FDS"] = "2";
    values["LISTEN_FDNAMES"] = "execution:events";
    values["STATE_DIRECTORY"] = "/var/lib/hepta-execution";
    values["CREDENTIALS_DIRECTORY"] = "/run/credentials/hepta-execution-simulator.service";
    return values;
}

void ExpectInvalid(std::map<std::string, std::string> values,
                   const std::string& expectedReason)
{
    ExecutionServiceRuntimeConfig config;
    std::string reason;
    assert(!ExecutionServiceRuntimeConfig::FromValues(values, 1234, config, reason));
    assert(reason == expectedReason);
}
}

int main()
{
    ExecutionServiceRuntimeConfig config;
    std::string reason;
    assert(ExecutionServiceRuntimeConfig::FromValues(
        std::map<std::string, std::string>(), 1234, config, reason));
    assert(!config.Enabled());
    assert(config.mode == ExecutionServiceRuntimeMode::Disabled);
    assert(config.listenFd == -1);
    assert(config.eventListenFd == -1);

    std::map<std::string, std::string> values = ValidValues();
    values["HEPTA_EXECUTION_MAX_REQUEST_BYTES"] = "16384";
    values["HEPTA_EXECUTION_IO_TIMEOUT_MS"] = "2500";
    assert(ExecutionServiceRuntimeConfig::FromValues(values, 1234, config, reason));
    assert(config.Enabled());
    assert(config.mode == ExecutionServiceRuntimeMode::Simulator);
    assert(config.listenFd == 3);
    assert(config.eventListenFd == 4);
    assert(config.allowedGatewayUids.size() == 1);
    assert(config.allowedGatewayUids.count(2001) == 1);
    assert(config.gatewayContextBinding.agentId == "codex-agent-os-e2e");
    assert(config.gatewayContextBinding.account == "SIM");
    assert(config.gatewayContextBinding.venue == "SIMULATOR");
    assert(config.gatewayContextBinding.executionDomain ==
        "SIM:codex-agent-os-e2e");
    assert(config.stateDirectory == "/var/lib/hepta-execution");
    assert(config.journalPath == "/var/lib/hepta-execution/oms-journal.jsonl");
    assert(config.fenceCredentialPath ==
        "/run/credentials/hepta-execution-simulator.service/hepta-execution-fence");
    assert(config.maxRequestBytes == 16384);
    assert(config.ioTimeoutMs == 2500);
    assert(config.simulatorQuoteTtlMs == 60000);
    assert(config.simulatorQuoteRefreshIntervalMs == 10000);
    assert(config.Validate(reason));

    ExecutionServiceRuntimeConfig invalidQuoteConfig = config;
    invalidQuoteConfig.simulatorQuoteTtlMs = 9;
    assert(!invalidQuoteConfig.Validate(reason));
    assert(reason == "EXECUTION_SIMULATOR_QUOTE_TTL_INVALID");
    invalidQuoteConfig = config;
    invalidQuoteConfig.simulatorQuoteTtlMs = 600001;
    assert(!invalidQuoteConfig.Validate(reason));
    assert(reason == "EXECUTION_SIMULATOR_QUOTE_TTL_INVALID");
    invalidQuoteConfig = config;
    invalidQuoteConfig.simulatorQuoteRefreshIntervalMs = 0;
    assert(!invalidQuoteConfig.Validate(reason));
    assert(reason == "EXECUTION_SIMULATOR_QUOTE_REFRESH_INTERVAL_INVALID");
    invalidQuoteConfig = config;
    invalidQuoteConfig.simulatorQuoteRefreshIntervalMs =
        invalidQuoteConfig.simulatorQuoteTtlMs;
    assert(!invalidQuoteConfig.Validate(reason));
    assert(reason == "EXECUTION_SIMULATOR_QUOTE_REFRESH_INTERVAL_INVALID");
    invalidQuoteConfig = config;
    invalidQuoteConfig.simulatorQuoteTtlMs = 80;
    invalidQuoteConfig.simulatorQuoteRefreshIntervalMs = 41;
    assert(!invalidQuoteConfig.Validate(reason));
    assert(reason == "EXECUTION_SIMULATOR_QUOTE_REFRESH_INTERVAL_INVALID");

    values = ValidValues();
    values["LISTEN_PID"] = "1235";
    ExpectInvalid(values, "EXECUTION_SYSTEMD_SOCKET_ACTIVATION_INVALID");
    values = ValidValues();
    values["LISTEN_FDS"] = "1";
    ExpectInvalid(values, "EXECUTION_SYSTEMD_SOCKET_ACTIVATION_INVALID");
    values = ValidValues();
    values["LISTEN_FDNAMES"] = "execution:supervisor";
    ExpectInvalid(values, "EXECUTION_SYSTEMD_SOCKET_ACTIVATION_INVALID");
    values = ValidValues();
    values["LISTEN_FDNAMES"] = "events:execution";
    assert(ExecutionServiceRuntimeConfig::FromValues(values, 1234, config, reason));
    assert(config.eventListenFd == 3);
    assert(config.listenFd == 4);
    values = ValidValues();
    values["LISTEN_FDNAMES"] = "execution:execution";
    ExpectInvalid(values, "EXECUTION_SYSTEMD_SOCKET_ACTIVATION_INVALID");
    values = ValidValues();
    values.erase("HEPTA_EXECUTION_GATEWAY_UID");
    ExpectInvalid(values, "EXECUTION_GATEWAY_UID_INVALID");
    values = ValidValues();
    values["HEPTA_EXECUTION_GATEWAY_UID"] = "4294967296";
    ExpectInvalid(values, "EXECUTION_GATEWAY_UID_INVALID");
    values = ValidValues();
    values["HEPTA_EXECUTION_GATEWAY_UID"] = "0";
    ExpectInvalid(values, "EXECUTION_GATEWAY_UID_NOT_ISOLATED");
    values = ValidValues();
    values.erase("HEPTA_EXECUTION_GATEWAY_AGENT_ID");
    ExpectInvalid(values, "EXECUTION_GATEWAY_CONTEXT_BINDING_INVALID");
    values = ValidValues();
    values["HEPTA_EXECUTION_GATEWAY_AGENT_ID"] = "Other-Agent";
    ExpectInvalid(values, "EXECUTION_GATEWAY_CONTEXT_BINDING_INVALID");
    values = ValidValues();
    values["STATE_DIRECTORY"] = "/var/lib/../tmp/hepta";
    ExpectInvalid(values, "EXECUTION_PRIVATE_DIRECTORY_INVALID");
    values = ValidValues();
    values["CREDENTIALS_DIRECTORY"] = "/run/credentials/a:b";
    ExpectInvalid(values, "EXECUTION_PRIVATE_DIRECTORY_INVALID");
    values = ValidValues();
    values["HEPTA_EXECUTION_MAX_REQUEST_BYTES"] = "999";
    ExpectInvalid(values, "EXECUTION_MAX_REQUEST_BYTES_INVALID");
    values = ValidValues();
    values["HEPTA_EXECUTION_IO_TIMEOUT_MS"] = "30001";
    ExpectInvalid(values, "EXECUTION_IO_TIMEOUT_INVALID");
    values = ValidValues();
    values["HEPTA_EXECUTION_SERVICE_MODE"] = "PAPER";
    ExpectInvalid(values, "EXECUTION_RUNTIME_MODE_UNSUPPORTED");
    values = ValidValues();
    values["HEPTA_EXECUTION_SERVICE_MODE"] = "LIVE";
    ExpectInvalid(values, "EXECUTION_RUNTIME_MODE_UNSUPPORTED");

    std::cout << "execution_service_runtime_config_tests: PASS" << std::endl;
    return 0;
}
