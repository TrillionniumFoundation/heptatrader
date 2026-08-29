#include "hepta_sessionctl_command.h"
#include "hepta_sessionctl_terminal_cleanup.h"
#include "../tool_host/unix_session_supervisor_client.h"

#include <iostream>
#include <string>

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

int main(int argc, char** argv)
{
    if (HeptaSessionCtlTerminalCleanup::IsCommand(argc, argv))
        return HeptaSessionCtlTerminalCleanup::Run(argc, argv);
    HeptaSessionCtlCommand command;
    std::string reason;
    if (!HeptaSessionCtlCommandParser::Parse(argc, argv, command, reason))
    {
        std::cerr << reason << '\n' << HeptaSessionCtlCommandParser::Usage() << '\n';
        return 2;
    }
    if (!HeptaSessionCtlCommandParser::ReadTokenFile(
            command.tokenFile, command.hasTokenOwnerUid,
            command.tokenOwnerUid, command.request.token, reason))
    {
        std::cerr << reason << '\n';
        return 2;
    }
    if (!command.replacementTokenFile.empty() &&
        !HeptaSessionCtlCommandParser::ReadTokenFile(
            command.replacementTokenFile, false, 0,
            command.request.replacementToken, reason))
    {
        std::cerr << reason << '\n';
        return 2;
    }
	if (!command.terminalEvidenceFile.empty() &&
		!HeptaSessionCtlCommandParser::ReadTerminalEvidenceFile(
			command.terminalEvidenceFile,
			command.request.terminalEvidenceSha256,
			command.request.terminalEvidence, reason))
	{
		std::cerr << reason << '\n';
		return 2;
	}

    SessionSupervisorResult result;
    if (!UnixSessionSupervisorClient::Call(
            command.socketPath, command.request, result, reason,
            command.ioTimeoutMs))
    {
        std::cerr << reason << '\n';
        return 3;
    }
    std::cout << "{\"accepted\":" << (result.accepted ? "true" : "false")
              << ",\"reason_code\":\"" << JsonEscape(result.ReasonCode())
              << "\",\"lease_generation\":" << result.leaseGeneration;
    if (!result.FinalizationId().empty())
    {
        std::cout << ",\"paper_finalization_state\":\""
                  << JsonEscape(result.PaperFinalizationState())
                  << "\",\"paper_finalization_required\":"
                  << (result.paperFinalizationRequired ? "true" : "false")
                  << ",\"recovery_id\":\""
                  << JsonEscape(result.RecoveryId())
                  << "\",\"finalization_id\":\""
                  << JsonEscape(result.FinalizationId())
                  << "\",\"expected_owner_set_sha256\":\""
                  << JsonEscape(result.ExpectedOwnerSetSha256())
                  << "\",\"expected_owner_count\":"
                  << result.expectedOwnerCount
                  << ",\"owner_token_sha256\":\""
                  << JsonEscape(result.OwnerTokenSha256())
                  << "\",\"finalization_receipt_sha256\":\""
                  << JsonEscape(result.FinalizationReceiptSha256())
                  << "\",\"finalization_receipt\":\""
                  << JsonEscape(result.FinalizationReceipt())
                  << "\",\"owner_audit_authoritative\":"
                  << (result.ownerAuditAuthoritative ? "true" : "false")
                  << ",\"owner_audit_complete\":"
                  << (result.ownerAuditComplete ? "true" : "false")
                  << ",\"owner_active_order_count\":"
                  << result.ownerActiveOrderCount
                  << ",\"owner_uncertain_command_count\":"
                  << result.ownerUncertainCommandCount
                  << ",\"owner_account\":\""
                  << JsonEscape(result.OwnerAccount())
                  << "\",\"owner_execution_domain\":\""
                  << JsonEscape(result.OwnerExecutionDomain())
                  << "\",\"execution_service_epoch\":\""
                  << JsonEscape(result.ExecutionServiceEpoch())
                  << "\",\"execution_service_fencing_generation\":"
                  << result.executionServiceFencingGeneration
                  << ",\"broker_connection_epoch\":"
                  << result.brokerConnectionEpoch
                  << ",\"broker_active_generation\":"
                  << result.brokerActiveGeneration
                  << ",\"broker_terminal_generation\":"
                  << result.brokerTerminalGeneration
                  << ",\"broker_risk_generation\":"
                  << result.brokerRiskGeneration
                  << ",\"broker_account_generation\":"
                  << result.brokerAccountGeneration
                  << ",\"broker_position_generation\":"
                  << result.brokerPositionGeneration
                  << ",\"broker_fx_cash_generation\":"
                  << result.brokerFxCashGeneration
                  << ",\"broker_exposure_generation\":"
                  << result.brokerExposureGeneration
                  << ",\"broker_terminal_exposure_generation\":"
                  << result.brokerTerminalExposureGeneration
                  << ",\"broker_risk_absorbed_exposure_generation\":"
                  << result.brokerRiskAbsorbedExposureGeneration
                  << ",\"broker_global_active_order_count\":"
                  << result.brokerGlobalActiveOrderCount
                  << ",\"broker_post_fill_risk_reconciliation_pending\":"
                  << (result.brokerPostFillRiskReconciliationPending ?
                        "true" : "false")
                  << ",\"broker_recovery_audit_barrier_complete\":"
                  << (result.brokerRecoveryAuditBarrierComplete ?
                        "true" : "false")
                  << ",\"broker_recovery_audit_new_connection_epoch_required\":"
                  << (result.brokerRecoveryAuditNewConnectionEpochRequired ?
                        "true" : "false")
                  << ",\"broker_position_quantity\":\""
                  << JsonEscape(result.BrokerPositionQuantity())
                  << "\",\"broker_gross_absolute_position\":\""
                  << JsonEscape(result.BrokerGrossAbsolutePosition())
                  << "\",\"preliminary_finalization_receipt_sha256\":\""
                  << JsonEscape(
                        result.PreliminaryFinalizationReceiptSha256())
                  << "\",\"terminalization_service_epoch\":\""
                  << JsonEscape(result.TerminalizationServiceEpoch())
                  << "\",\"terminalization_service_fencing_generation\":"
                  << result.terminalizationServiceFencingGeneration
                  << ",\"terminalization_generation\":"
                  << result.terminalizationGeneration
                  << ",\"terminal_latch_sha256\":\""
                  << JsonEscape(result.TerminalLatchSha256())
                  << "\",\"execution_mutation_gate_closed\":"
                  << (result.terminalMutationGateClosed ? "true" : "false")
                  << ",\"broker_transport_connected\":"
                  << (result.terminalBrokerTransportConnected ?
                        "true" : "false")
                  << ",\"broker_event_ingress_halted\":"
                  << (result.terminalBrokerEventIngressHalted ?
                        "true" : "false")
                  << ",\"broker_callback_queue_drained\":"
                  << (result.terminalBrokerCallbackQueueDrained ?
                        "true" : "false")
                  << ",\"broker_callbacks_in_flight\":"
                  << result.terminalBrokerCallbacksInFlight
                  << ",\"broker_reconnect_permitted\":"
                  << (result.terminalBrokerReconnectPermitted ?
                        "true" : "false")
                  << ",\"terminal_latch_durable\":"
                  << (result.terminalLatchDurable ? "true" : "false")
                  << ",\"terminal_runtime_latch_loaded\":"
                  << (result.terminalRuntimeLatchLoaded ? "true" : "false")
                  << ",\"terminal_runtime_verified\":"
                  << (result.terminalRuntimeVerified ? "true" : "false")
                  << ",\"terminal_replay\":"
                  << (result.terminalReplay ? "true" : "false")
				  << ",\"terminal_proof_kind\":\""
				  << JsonEscape(result.TerminalProofKind())
				  << "\",\"terminal_external_halt_latch_sha256\":\""
				  << JsonEscape(result.TerminalExternalLatchSha256())
				  << "\",\"transport_cutoff_receipt_file_sha256\":\""
				  << JsonEscape(result.TransportCutoffReceiptFileSha256())
				  << "\",\"transport_cutoff_receipt_body_sha256\":\""
				  << JsonEscape(result.TransportCutoffReceiptBodySha256())
				  << "\",\"post_cutoff_terminal_witness_file_sha256\":\""
				  << JsonEscape(result.PostCutoffTerminalWitnessFileSha256())
				  << "\",\"post_cutoff_terminal_witness_body_sha256\":\""
				  << JsonEscape(result.PostCutoffTerminalWitnessBodySha256())
				  << "\",\"terminal_evidence_sha256\":\""
				  << JsonEscape(result.TerminalEvidenceSha256())
				  << "\",\"terminal_evidence_body_sha256\":\""
				  << JsonEscape(result.TerminalEvidenceBodySha256())
				  << "\",\"egress_policy_sha256\":\""
				  << JsonEscape(result.EgressPolicySha256())
				  << "\",\"egress_publisher_pid\":"
				  << result.egressPublisherPid
				  << ",\"egress_publisher_start_ticks\":"
				  << result.egressPublisherStartTicks
				  << ",\"provider_trust_policy_body_sha256\":\""
				  << JsonEscape(result.ProviderTrustPolicyBodySha256())
				  << "\",\"signed_account_signature_sha256\":\""
				  << JsonEscape(result.SignedAccountSignatureSha256())
				  << "\",\"terminal_external_latch_loaded\":"
				  << (result.terminalExternalLatchLoaded ? "true" : "false")
				  << ",\"terminal_current_evidence_verified\":"
				  << (result.terminalCurrentEvidenceVerified ? "true" : "false");
    }
    else if (!result.TargetCommandId().empty())
    {
        std::cout << ",\"authoritative_command_status\":"
                  << (result.authoritativeCommandStatus ? "true" : "false")
                  << ",\"command_id\":\""
                  << JsonEscape(result.TargetCommandId())
                  << "\",\"command_status\":\""
                  << JsonEscape(result.CommandStatus())
                  << "\",\"command_reason_code\":\""
                  << JsonEscape(result.CommandReasonCode())
                  << "\",\"order_id\":" << result.orderId
                  << ",\"recovery_only\":"
                  << (result.recoveryOnly ? "true" : "false")
                  << ",\"paper_finalization_required\":"
                  << (result.paperFinalizationRequired ? "true" : "false")
                  << ",\"owner_fenced\":"
                  << (result.ownerFenced ? "true" : "false")
                  << ",\"execution_service_epoch\":\""
                  << JsonEscape(result.ExecutionServiceEpoch())
                  << "\",\"execution_service_fencing_generation\":"
                  << result.executionServiceFencingGeneration;
        std::cout << ",\"recovery_expires_at_ms\":"
                  << result.recoveryExpiresAtMs
                  << ",\"owner_audit_authoritative\":"
                  << (result.ownerAuditAuthoritative ? "true" : "false")
                  << ",\"owner_audit_complete\":"
                  << (result.ownerAuditComplete ? "true" : "false")
                  << ",\"owner_active_order_count\":"
                  << result.ownerActiveOrderCount
                  << ",\"owner_uncertain_command_count\":"
                  << result.ownerUncertainCommandCount
                  << ",\"broker_connection_epoch\":"
                  << result.brokerConnectionEpoch
                  << ",\"broker_active_generation\":"
                  << result.brokerActiveGeneration
                  << ",\"broker_terminal_generation\":"
                  << result.brokerTerminalGeneration
                  << ",\"owner_account\":\""
                  << JsonEscape(result.OwnerAccount())
                  << "\",\"owner_execution_domain\":\""
                  << JsonEscape(result.OwnerExecutionDomain()) << '\"';
    }
    std::cout << "}\n";
    return result.accepted ? 0 : 4;
}
