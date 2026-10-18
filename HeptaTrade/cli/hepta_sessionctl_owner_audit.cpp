#include "hepta_sessionctl_owner_audit.h"
#include "hepta_sessionctl_command.h"
#include "../execution/unix_execution_service_client.h"
#include "../tool_host/session_supervisor_lease_store.h"

#include <chrono>
#include <cstdlib>
#include <iostream>
#include <set>
#include <string>
#include <unistd.h>

namespace
{
std::string JsonEscape(const std::string& value)
{
    static const char digits[] = "0123456789abcdef";
    std::string escaped;
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
    {
        const unsigned char byte = static_cast<unsigned char>(*it);
        if (*it == '\\' || *it == '"')
        {
            escaped.push_back('\\');
            escaped.push_back(*it);
        }
        else if (*it == '\n') escaped += "\\n";
        else if (*it == '\r') escaped += "\\r";
        else if (*it == '\t') escaped += "\\t";
        else if (byte < 0x20)
        {
            escaped += "\\u00";
            escaped.push_back(digits[byte >> 4]);
            escaped.push_back(digits[byte & 15]);
        }
        else escaped.push_back(*it);
    }
    return escaped;
}
}

int HeptaSessionCtlOwnerAudit::Run(int argc, char** argv,
    const AgentOsRuntimeConfig& osConfig,
    const ExecutionGatewayRuntimeConfig& gateway)
{
    const auto fail = [](const std::string& reason) {
        std::cerr << reason << '\n';
        return 2;
    };
    if (argc != 4 || std::string(argv[2]) != "--token-file")
        return fail("OWNER_AUDIT_USAGE: owner-audit --token-file PATH");

    const std::uint32_t uid = static_cast<std::uint32_t>(::geteuid());
    std::string reason;
    if (!osConfig.Validate(reason) || !gateway.Validate(reason))
        return fail(reason);
    if (uid == 0 || !osConfig.userPaperScope ||
        osConfig.supervisorUid != uid || osConfig.agentUid != uid ||
        gateway.mode != ExecutionGatewayMode::Paper ||
        !gateway.executionServiceUidConfigured ||
        gateway.executionServiceUid != uid)
        return fail("OWNER_AUDIT_USER_PAPER_SCOPE_REQUIRED");

    const auto envString = [](const char* name) {
        const char* value = std::getenv(name);
        return value == nullptr ? std::string() : std::string(value);
    };
    const std::string agent = envString("HEPTA_TOOL_AGENT_ID");
    const std::string account = envString("HEPTA_TOOL_ACCOUNT");
    std::string domain = envString("HEPTA_EXECUTION_DOMAIN_ID");
    if (domain.empty()) domain = "PAPER";
    if (agent.empty() || account.empty())
        return fail("OWNER_AUDIT_CONFIGURED_SCOPE_REQUIRED");

    std::string token;
    if (!HeptaSessionCtlCommandParser::ReadTokenFile(
            argv[3], true, uid, token, reason))
        return fail(reason);
    SessionSupervisorLeaseStore store;
    if (!store.LoadExisting(osConfig.supervisorLeaseStorePath,
            osConfig.supervisorLeaseKeyPath,
            osConfig.supervisorLeaseCleanupLockPath,
            osConfig.supervisorLeaseCleanupLockUid,
            osConfig.supervisorLeaseCleanupLockGid, reason))
        return fail(reason);
    SessionSupervisorLeaseRecord record;
    if (!store.Get(token, record))
        return fail("OWNER_AUDIT_EXISTING_TOKEN_REQUIRED");
    if (record.templateId != "paper" ||
        record.issuer != "hepta.os.bootstrap" || record.peerUid != uid ||
        record.agentId != agent || record.sessionId.empty() ||
        record.ownerAccount != account ||
        record.ownerExecutionDomain != domain)
        return fail("OWNER_AUDIT_CATALOG_SCOPE_MISMATCH");

    // Audit the existing catalog owner. Expiry/fence state does not create
    // mutation authority and is not renewed or changed by this command.
    ExecutionControlCommand command;
    command.context.agentId = record.agentId;
    command.context.sessionId = record.sessionId;
    command.context.account = record.ownerAccount;
    command.context.executionDomain = record.ownerExecutionDomain;
    command.context.venue = "IB";
    command.context.strategy = "agent-native";
    command.context.toolCallId = "owner-audit-" + std::to_string(::getpid()) +
        "-" + std::to_string(
            std::chrono::steady_clock::now().time_since_epoch().count());
    // Decision lease fields and recoveryIngressFence retain their zero
    // defaults: op15 uses the established trusted native control contract.
    UnixExecutionServiceClient client(gateway.executionSocket,
        gateway.ioTimeoutMs, gateway.maxResponseBytes,
        std::set<std::uint32_t>{gateway.executionServiceUid},
        gateway.responseTimeoutMs);
    const ExecutionOwnerAuditResult result = client.AuditSessionOwner(command);

    std::string validationReason;
    if (result.status == ExecutionCommandStatus::Accepted)
    {
        if (result.ownerAccount != record.ownerAccount ||
            result.ownerExecutionDomain != record.ownerExecutionDomain)
            validationReason = "OWNER_AUDIT_RESPONSE_SCOPE_MISMATCH";
        else if (!result.ownerAuditAuthoritative || !result.ownerAuditComplete)
            validationReason = "OWNER_AUDIT_RESPONSE_INCOMPLETE";
    }
    const bool accepted = result.status == ExecutionCommandStatus::Accepted &&
        validationReason.empty();
    // Native owner audit can reconcile OMS and request broker recovery.
    // This evidence is not a standalone permission to stop a service.
    std::cout << std::boolalpha
        << "{\"schema\":\"hepta_session_owner_audit_v1\","
        << "\"operation\":\"owner-audit\",\"accepted\":" << accepted
        << ",\"native_status\":" << static_cast<int>(result.status)
        << ",\"reason_code\":\"" << JsonEscape(result.reasonCode) << "\""
        << ",\"detail\":\"" << JsonEscape(result.detail) << "\""
        << ",\"validation_reason\":\"" << JsonEscape(validationReason) << "\""
        << ",\"command_id\":\"" << JsonEscape(result.commandId) << "\""
        << ",\"agent_id\":\"" << JsonEscape(record.agentId) << "\""
        << ",\"session_id\":\"" << JsonEscape(record.sessionId) << "\""
        << ",\"expected_owner_account\":\"" << JsonEscape(record.ownerAccount) << "\""
        << ",\"expected_owner_execution_domain\":\""
        << JsonEscape(record.ownerExecutionDomain) << "\""
        << ",\"service_epoch\":\"" << JsonEscape(result.serviceEpoch) << "\""
        << ",\"service_fencing_generation\":" << result.serviceFencingGeneration
        << ",\"affected_count\":" << result.affectedCount
        << ",\"mutation_blocked\":" << result.mutationBlocked
        << ",\"owner_account\":\"" << JsonEscape(result.ownerAccount) << "\""
        << ",\"owner_execution_domain\":\""
        << JsonEscape(result.ownerExecutionDomain) << "\""
        << ",\"owner_audit_authoritative\":" << result.ownerAuditAuthoritative
        << ",\"owner_audit_complete\":" << result.ownerAuditComplete
        << ",\"owner_active_order_count\":" << result.ownerActiveOrderCount
        << ",\"owner_uncertain_command_count\":" << result.ownerUncertainCommandCount
        << ",\"broker_connection_epoch\":" << result.brokerConnectionEpoch
        << ",\"broker_active_generation\":" << result.brokerActiveGeneration
        << ",\"broker_terminal_generation\":" << result.brokerTerminalGeneration
        << ",\"broker_risk_generation\":" << result.brokerRiskGeneration
        << ",\"broker_account_generation\":" << result.brokerAccountGeneration
        << ",\"broker_position_generation\":" << result.brokerPositionGeneration
        << ",\"broker_fx_cash_generation\":" << result.brokerFxCashGeneration
        << ",\"broker_exposure_generation\":" << result.brokerExposureGeneration
        << ",\"broker_terminal_exposure_generation\":" << result.brokerTerminalExposureGeneration
        << ",\"broker_risk_absorbed_exposure_generation\":" << result.brokerRiskAbsorbedExposureGeneration
        << ",\"broker_global_active_order_count\":" << result.brokerGlobalActiveOrderCount
        << ",\"broker_post_fill_risk_reconciliation_pending\":" << result.brokerPostFillRiskReconciliationPending
        << ",\"broker_recovery_audit_barrier_complete\":" << result.brokerRecoveryAuditBarrierComplete
        << ",\"broker_recovery_audit_new_connection_epoch_required\":" << result.brokerRecoveryAuditNewConnectionEpochRequired
        << ",\"broker_position_quantity\":\"" << JsonEscape(result.brokerPositionQuantity) << "\""
        << ",\"broker_gross_absolute_position\":\"" << JsonEscape(result.brokerGrossAbsolutePosition) << "\""
        << "}\n";
    return accepted ? 0 : 4;
}
