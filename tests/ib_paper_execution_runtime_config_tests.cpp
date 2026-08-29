#include "execution/ib_paper_execution_runtime_config.h"

#include <cassert>
#include <cstdlib>
#include <iostream>
#include <map>
#include <string>
#include <sys/stat.h>
#include <unistd.h>

namespace
{
std::string MakePrivateDirectory(const char* prefix)
{
    std::string pattern = std::string("/tmp/") + prefix + "-XXXXXX";
    char* buffer = &pattern[0];
    assert(::mkdtemp(buffer) != nullptr);
    assert(::chmod(pattern.c_str(), 0700) == 0);
    return pattern;
}

std::map<std::string, std::string> ValidValues(
    const std::string& stateDirectory,
    const std::string& credentialsDirectory)
{
    std::map<std::string, std::string> values;
    values["HEPTA_IB_EXECUTION_MODE"] = "PAPER";
    values["HEPTA_IB_PAPER_ACCOUNT"] = "DU123456";
    values["HEPTA_IB_PAPER_HOST"] = "127.0.0.1";
    values["HEPTA_IB_PAPER_PORT"] = "7497";
    values["HEPTA_IB_PAPER_CLIENT_ID"] = "701";
    values["HEPTA_IB_PAPER_MAX_ORDER_QTY"] = "1000";
    values["HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL"] = "250000";
    values["HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE"] = "2";
    values["HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS"] = "3";
    values["HEPTA_IB_PAPER_MAX_GROSS_POSITION"] = "5000";
    values["HEPTA_IB_PAPER_QUOTE_CONTRACTS"] =
        "EUR.USD|EUR|CASH|IDEALPRO|USD;GBP.USD|GBP|CASH|IDEALPRO|USD";
    values["HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT"] = "EUR.USD";
    values["HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS"] = "5000";
    values["HEPTA_IB_PAPER_CONTROL_DIRECTORY"] =
        "/run/hepta/ib-paper-control-codex-a";
    values["HEPTA_IB_EXECUTION_GATEWAY_UID"] = "2001";
    values["HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID"] = "codex-a";
    values["HEPTA_IB_EXECUTION_DOMAIN_ID"] = "PAPER:codex-a";
    values["LISTEN_PID"] = "1234";
    values["LISTEN_FDS"] = "2";
    values["LISTEN_FDNAMES"] = "execution:events";
    values["STATE_DIRECTORY"] = stateDirectory;
    values["CREDENTIALS_DIRECTORY"] = credentialsDirectory;
    return values;
}

void ExpectInvalid(const std::map<std::string, std::string>& values,
                   const std::string& expectedReason)
{
    IbPaperExecutionRuntimeConfig config;
    std::string reason;
    assert(!IbPaperExecutionRuntimeConfig::FromValues(
        values, 1234, config, reason));
    if (reason != expectedReason)
        std::cerr << "expected invalid reason " << expectedReason
                  << ", received " << reason << "\n";
    assert(reason == expectedReason);
}
}

int main()
{
    IbPaperExecutionRuntimeConfig config;
    std::string reason;
    assert(IbPaperExecutionRuntimeConfig::FromValues(
        std::map<std::string, std::string>(), 1234, config, reason));
    assert(!config.Enabled());
    assert(config.mode == IbPaperExecutionRuntimeMode::Disabled);
    assert(config.listenFd == -1);
    assert(config.eventListenFd == -1);
    assert(config.Validate(reason));

    std::map<std::string, std::string> disabled;
    disabled["HEPTA_IB_EXECUTION_MODE"] = "DISABLED";
    assert(IbPaperExecutionRuntimeConfig::FromValues(disabled, 1234, config, reason));
    disabled["HEPTA_IB_PAPER_ACCOUNT"] = "DU123456";
    ExpectInvalid(disabled, "IB_PAPER_RUNTIME_DISABLED_CONFIGURATION_PRESENT");
    disabled.clear();
    disabled["HEPTA_IB_EXECUTION_MODE"] = "DISABLED";
    disabled["HEPTA_IB_PAPER_CONTROL_DIRECTORY"] =
        "/run/hepta/ib-paper-control";
    ExpectInvalid(disabled, "IB_PAPER_RUNTIME_DISABLED_CONFIGURATION_PRESENT");
    disabled.clear();
    disabled["HEPTA_IB_EXECUTION_MODE"] = "DISABLED";
    disabled["HEPTA_EXECUTION_EXTERNAL_P1_CANARY_LMT_DAY"] = "1";
    ExpectInvalid(disabled, "IB_PAPER_RUNTIME_DISABLED_CONFIGURATION_PRESENT");

    const std::string stateDirectory =
        MakePrivateDirectory("hepta-ib-paper-runtime-state");
    const std::string credentialsDirectory =
        MakePrivateDirectory("hepta-ib-paper-runtime-credentials");
    std::map<std::string, std::string> values =
        ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_EXECUTION_MAX_REQUEST_BYTES"] = "16384";
    values["HEPTA_IB_EXECUTION_IO_TIMEOUT_MS"] = "2500";
    values["HEPTA_IB_EXECUTION_READINESS_TIMEOUT_MS"] = "12000";
    values["HEPTA_IB_EXECUTION_RECONNECT_TIMEOUT_MS"] = "180000";
    assert(IbPaperExecutionRuntimeConfig::FromValues(
        values, 1234, config, reason));
    assert(config.Enabled());
    assert(config.mode == IbPaperExecutionRuntimeMode::Paper);
    assert(config.listenFd == 3);
    assert(config.eventListenFd == 4);
    assert(config.allowedGatewayUids.size() == 1);
    assert(config.allowedGatewayUids.count(2001) == 1);
    assert(config.gatewayContextBinding.agentId == "codex-a");
    assert(config.gatewayContextBinding.account == "DU123456");
    assert(config.gatewayContextBinding.venue == "IB");
    assert(config.gatewayContextBinding.executionDomain == "PAPER:codex-a");
    assert(config.profile.enabled);
    assert(config.profile.account == "DU123456");
    assert(config.profile.host == "127.0.0.1");
    assert(config.profile.port == 7497);
    assert(config.profile.clientId == 701);
    assert(config.profile.maxOrderQuantity == 1000.0);
    assert(config.profile.maxOrderNotional == 250000.0);
    assert(config.profile.maxOrdersPerMinute == 2);
    assert(config.profile.maxActiveOrders == 3);
    assert(config.profile.maxGrossPosition == 5000.0);
    assert(config.quoteContracts.size() == 2);
    assert(config.quoteContracts.at("EUR.USD").symbol == "EUR");
    assert(config.primaryQuoteInstrument == "EUR.USD");
    assert(config.quoteMaxAgeMs == 5000);
    assert(config.stateDirectory == stateDirectory);
    assert(config.journalPath == stateDirectory + "/oms-journal.jsonl");
    assert(config.controlDirectory ==
        "/run/hepta/ib-paper-control-codex-a");
    assert(config.fenceCredentialPath ==
        credentialsDirectory + "/hepta-execution-fence");
    assert(config.authorizationCredentialPath ==
        credentialsDirectory + "/hepta-ib-paper-authorization");
    assert(config.profile.authorizationCredentialPath ==
        config.authorizationCredentialPath);
    assert(config.maxRequestBytes == 16384);
    assert(config.ioTimeoutMs == 2500);
    assert(config.readinessTimeoutMs == 12000);
    assert(config.reconnectTimeoutMs == 180000);
    assert(config.Validate(reason));

    std::map<std::string, std::string> externalValues =
        ValidValues(stateDirectory, credentialsDirectory);
    externalValues["HEPTA_EXECUTION_EXTERNAL_P1_CANARY_LMT_DAY"] = "1";
    externalValues["HEPTA_EXECUTION_MAX_ORDER_NOTIONAL"] = "5000";
    externalValues["HEPTA_IB_PAPER_MAX_ORDER_QTY"] = "1";
    externalValues["HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL"] = "5000";
    externalValues["HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS"] = "1";
    externalValues["HEPTA_IB_PAPER_MAX_GROSS_POSITION"] = "1";
    assert(IbPaperExecutionRuntimeConfig::FromValues(
        externalValues, 1234, config, reason));
    assert(config.profile.UsesExternalLimitDay());
    assert(config.profile.orderMode ==
        IbPaperOrderMode::ExternalLimitDay);
    assert(config.profile.externalQuoteMaxAgeMs == 5000);
    assert(config.quoteMaxAgeMs == 5000);
    externalValues["HEPTA_EXECUTION_EXTERNAL_P1_CANARY_LMT_DAY"] = "";
    ExpectInvalid(externalValues,
        "IB_PAPER_EXTERNAL_ORDER_MODE_CONFIGURATION_INVALID");
    externalValues["HEPTA_EXECUTION_EXTERNAL_P1_CANARY_LMT_DAY"] = "1";
    externalValues["HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS"] = "5001";
    ExpectInvalid(externalValues,
        "IB_PAPER_EXTERNAL_ORDER_MODE_LIMITS_INVALID");
    externalValues["HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS"] = "5000";
    externalValues["HEPTA_IB_PAPER_MAX_ORDER_QTY"] = "1.01";
    ExpectInvalid(externalValues,
        "IB_PAPER_EXTERNAL_ORDER_MODE_LIMITS_INVALID");

    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_PAPER_ACCOUNT"] = "DUH838270";
    values["HEPTA_IB_EXECUTION_MAX_REQUEST_BYTES"] = "16384";
    values["HEPTA_IB_EXECUTION_IO_TIMEOUT_MS"] = "2500";
    values["HEPTA_IB_EXECUTION_READINESS_TIMEOUT_MS"] = "12000";
    values["HEPTA_IB_EXECUTION_RECONNECT_TIMEOUT_MS"] = "180000";
    assert(IbPaperExecutionRuntimeConfig::FromValues(
        values, 1234, config, reason));
    assert(config.profile.account == "DUH838270");

    assert(config.ValidateProductionIdentity(2002, reason));
    assert(!config.ValidateProductionIdentity(0, reason));
    assert(reason == "IB_PAPER_SERVICE_UID_NOT_ISOLATED");
    assert(!config.ValidateProductionIdentity(2001, reason));
    assert(reason == "IB_PAPER_GATEWAY_UID_NOT_ISOLATED");

    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_EXECUTION_DOMAIN_ID"] = "PAPER";
    values["HEPTA_IB_PAPER_CONTROL_DIRECTORY"] =
        "/run/hepta/ib-paper-control";
    assert(IbPaperExecutionRuntimeConfig::FromValues(
        values, 1234, config, reason));
    assert(config.gatewayContextBinding.executionDomain == "PAPER");
    assert(config.controlDirectory ==
        "/run/hepta/ib-paper-control");

    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_PAPER_CONTROL_DIRECTORY"] =
        "/run/hepta/ib-paper-control-codex-b";
    ExpectInvalid(
        values, "IB_PAPER_CONTROL_DIRECTORY_DOMAIN_MISMATCH");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_EXECUTION_DOMAIN_ID"] = "PAPER:codex-b";
    values["HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID"] = "codex-b";
    ExpectInvalid(
        values, "IB_PAPER_CONTROL_DIRECTORY_DOMAIN_MISMATCH");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_PAPER_CONTROL_DIRECTORY"] =
        "/run/hepta/ib-paper-control-Codex-A";
    ExpectInvalid(values, "IB_PAPER_CONTROL_DIRECTORY_INVALID");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_PAPER_CONTROL_DIRECTORY"] =
        "/run/hepta/ib-paper-control-codex/a";
    ExpectInvalid(values, "IB_PAPER_CONTROL_DIRECTORY_INVALID");

    values = ValidValues(stateDirectory, credentialsDirectory);
    values["LISTEN_FDNAMES"] = "events:execution";
    assert(IbPaperExecutionRuntimeConfig::FromValues(
        values, 1234, config, reason));
    assert(config.eventListenFd == 3);
    assert(config.listenFd == 4);

    values = ValidValues(stateDirectory, credentialsDirectory);
    values.erase("HEPTA_IB_PAPER_QUOTE_CONTRACTS");
    ExpectInvalid(values, "IB_PAPER_QUOTE_CONTRACTS_INVALID");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_PAPER_QUOTE_CONTRACTS"] =
        "EUR.USD|EUR|STK|SMART|USD";
    ExpectInvalid(values, "IB_PAPER_QUOTE_CONTRACTS_INVALID");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_PAPER_QUOTE_CONTRACTS"] =
        "WRONG|EUR|CASH|IDEALPRO|USD";
    ExpectInvalid(values, "IB_PAPER_QUOTE_CONTRACTS_INVALID");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT"] = "USD.JPY";
    ExpectInvalid(values, "IB_PAPER_QUOTE_CONTRACTS_REQUIRED");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS"] = "99";
    ExpectInvalid(values, "IB_PAPER_QUOTE_MAX_AGE_INVALID");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values.erase("HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS");
    ExpectInvalid(values, "IB_PAPER_QUOTE_MAX_AGE_INVALID");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_EXECUTION_MODE"] = "LIVE";
    ExpectInvalid(values, "IB_PAPER_RUNTIME_MODE_UNSUPPORTED");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["LISTEN_PID"] = "1235";
    ExpectInvalid(values, "IB_PAPER_SYSTEMD_SOCKET_ACTIVATION_INVALID");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["LISTEN_FDS"] = "1";
    ExpectInvalid(values, "IB_PAPER_SYSTEMD_SOCKET_ACTIVATION_INVALID");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["LISTEN_FDNAMES"] = "execution:execution";
    ExpectInvalid(values, "IB_PAPER_SYSTEMD_SOCKET_ACTIVATION_INVALID");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["LISTEN_FDNAMES"] = "execution:supervisor";
    ExpectInvalid(values, "IB_PAPER_SYSTEMD_SOCKET_ACTIVATION_INVALID");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values.erase("HEPTA_IB_EXECUTION_GATEWAY_UID");
    ExpectInvalid(values, "IB_PAPER_GATEWAY_UID_INVALID");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_EXECUTION_GATEWAY_UID"] = "4294967296";
    ExpectInvalid(values, "IB_PAPER_GATEWAY_UID_INVALID");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_EXECUTION_GATEWAY_UID"] = "0";
    ExpectInvalid(values, "IB_PAPER_GATEWAY_UID_NOT_ISOLATED");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values.erase("HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID");
    ExpectInvalid(values, "IB_PAPER_GATEWAY_CONTEXT_BINDING_INVALID");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID"] = "Other-Agent";
    ExpectInvalid(values, "IB_PAPER_GATEWAY_CONTEXT_BINDING_INVALID");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID"] = "openclaw-b";
    ExpectInvalid(values, "IB_PAPER_GATEWAY_CONTEXT_BINDING_INVALID");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_EXECUTION_DOMAIN_ID"] = "PAPER:";
    ExpectInvalid(values, "IB_PAPER_GATEWAY_CONTEXT_BINDING_INVALID");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_EXECUTION_DOMAIN_ID"] = "SIM:codex-a";
    ExpectInvalid(values, "IB_PAPER_GATEWAY_CONTEXT_BINDING_INVALID");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_EXECUTION_DOMAIN_ID"] = "PAPER:Codex-A";
    ExpectInvalid(values, "IB_PAPER_GATEWAY_CONTEXT_BINDING_INVALID");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_EXECUTION_MAX_REQUEST_BYTES"] = "999";
    ExpectInvalid(values, "IB_PAPER_MAX_REQUEST_BYTES_INVALID");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_EXECUTION_IO_TIMEOUT_MS"] = "30001";
    ExpectInvalid(values, "IB_PAPER_IO_TIMEOUT_INVALID");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_EXECUTION_READINESS_TIMEOUT_MS"] = "99";
    ExpectInvalid(values, "IB_PAPER_READINESS_TIMEOUT_INVALID");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_EXECUTION_RECONNECT_TIMEOUT_MS"] = "999";
    ExpectInvalid(values, "IB_PAPER_RECONNECT_TIMEOUT_INVALID");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_EXECUTION_RECONNECT_TIMEOUT_MS"] = "300001";
    ExpectInvalid(values, "IB_PAPER_RECONNECT_TIMEOUT_INVALID");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_EXECUTION_READINESS_TIMEOUT_MS"] = "12000";
    values["HEPTA_IB_EXECUTION_RECONNECT_TIMEOUT_MS"] = "11999";
    ExpectInvalid(values, "IB_PAPER_RECONNECT_TIMEOUT_INVALID");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values.erase("HEPTA_IB_PAPER_CONTROL_DIRECTORY");
    ExpectInvalid(values, "IB_PAPER_CONTROL_DIRECTORY_INVALID");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_PAPER_CONTROL_DIRECTORY"] = stateDirectory + "/control";
    ExpectInvalid(values, "IB_PAPER_CONTROL_DIRECTORY_INVALID");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_PAPER_ACCOUNT"] = "U123456";
    ExpectInvalid(values, "IB_PAPER_ACCOUNT_REQUIRED");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_PAPER_ACCOUNT"] = "DUH";
    ExpectInvalid(values, "IB_PAPER_ACCOUNT_REQUIRED");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_PAPER_ACCOUNT"] = "DUh838270";
    ExpectInvalid(values, "IB_PAPER_ACCOUNT_REQUIRED");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_PAPER_ACCOUNT"] = "DUH-838270";
    ExpectInvalid(values, "IB_PAPER_ACCOUNT_REQUIRED");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_PAPER_HOST"] = "192.0.2.1";
    ExpectInvalid(values, "IB_PAPER_LOOPBACK_HOST_REQUIRED");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_PAPER_PORT"] = "7496";
    ExpectInvalid(values, "IB_PAPER_PORT_INVALID");
    values = ValidValues(stateDirectory, credentialsDirectory);
    values["HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL"] = "nan";
    ExpectInvalid(values, "IB_PAPER_HARD_LIMITS_INVALID");
    ExpectInvalid(ValidValues(stateDirectory, stateDirectory),
                  "IB_PAPER_RUNTIME_CREDENTIAL_PATHS_INVALID");

    assert(::chmod(stateDirectory.c_str(), 0750) == 0);
    ExpectInvalid(ValidValues(stateDirectory, credentialsDirectory),
                  "IB_PAPER_RUNTIME_STATE_DIRECTORY_UNSAFE");
    assert(::chmod(stateDirectory.c_str(), 0700) == 0);

    assert(::chmod(credentialsDirectory.c_str(), 0720) == 0);
    ExpectInvalid(ValidValues(stateDirectory, credentialsDirectory),
                  "IB_PAPER_RUNTIME_CREDENTIAL_PATHS_INVALID");
    assert(::chmod(credentialsDirectory.c_str(), 0700) == 0);
    const std::string credentialSymlink = credentialsDirectory + "-link";
    assert(::symlink(credentialsDirectory.c_str(), credentialSymlink.c_str()) == 0);
    ExpectInvalid(ValidValues(stateDirectory, credentialSymlink),
                  "IB_PAPER_RUNTIME_CREDENTIAL_PATHS_INVALID");
    assert(::unlink(credentialSymlink.c_str()) == 0);

    values = ValidValues(stateDirectory, credentialsDirectory);
    assert(IbPaperExecutionRuntimeConfig::FromValues(
        values, 1234, config, reason));
    config.allowedGatewayUids.insert(2002);
    assert(!config.Validate(reason));
    assert(reason == "IB_PAPER_GATEWAY_UID_REQUIRED");
    config.allowedGatewayUids.erase(2002);
    config.profile.controlDirectory += ".wrong";
    assert(!config.Validate(reason));
    assert(reason == "IB_PAPER_CONTROL_DIRECTORY_INVALID");

    assert(::rmdir(credentialsDirectory.c_str()) == 0);
    assert(::rmdir(stateDirectory.c_str()) == 0);

    std::cout << "ib_paper_execution_runtime_config_tests: PASS" << std::endl;
    return 0;
}
