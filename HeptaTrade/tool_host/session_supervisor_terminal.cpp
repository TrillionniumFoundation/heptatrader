#include "unix_session_supervisor_server.h"

#include "typed_tool_protocol.h"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <fcntl.h>
#include <cstring>
#include <limits>
#include <openssl/evp.h>
#include <poll.h>
#include <sstream>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <thread>
#include <unistd.h>

#include "session_supervisor_internal.h"
using namespace HeptaSessionSupervisorInternal;

bool UnixSessionSupervisorServer::EnterPaperRecovery(
	SessionSupervisorLeaseRecord& record,
	std::uint64_t nowMs,
	const std::string& targetCommandId,
	ExecutionControlStatusResult& commandResult,
	ExecutionOwnerAuditResult& ownerAudit,
	std::string& reason)
{
	if (m_leaseStore == nullptr || record.templateId != "paper" ||
		record.paperFinalizationState !=
			SessionSupervisorPaperFinalizationState::None ||
		targetCommandId.size() > 128 ||
		nowMs > std::numeric_limits<std::uint64_t>::max() -
			m_maxSessionTtlMs)
	{
		reason = "SESSION_PAPER_RECOVERY_RECORD_INVALID";
		return false;
	}
	SessionSupervisorLeaseRecord recovery = record;
	std::string durableCurrentToken = record.token;
	recovery.expiresAtMs = nowMs + m_maxSessionTtlMs;
	recovery.fencePending = false;
	recovery.fenceComplete = false;
	recovery.fenceReason.clear();
	recovery.recoveryOnly = true;
	if (!targetCommandId.empty() && recovery.recoveryCommandId.empty())
		recovery.recoveryCommandId = targetCommandId;
	TradingToolHostSessionBinding local;
	bool localExists = m_controlPlane.m_host.GetSession(recovery.token, local);
	if (localExists &&
		(local.session.executionContext.agentId != recovery.agentId ||
		 local.session.executionContext.sessionId != recovery.sessionId ||
		 local.session.executionContext.account != recovery.ownerAccount ||
		 local.executionDomain != recovery.ownerExecutionDomain))
	{
		reason = "SESSION_RECOVERY_FENCE_BINDING_MISMATCH";
		return false;
	}
	if (localExists && local.leaseGeneration != recovery.leaseGeneration)
	{
		recovery.leaseGeneration = local.leaseGeneration;
		recovery.predecessorToken.clear();
		recovery.predecessorGeneration = 0;
	}
	else if (!localExists && !recovery.predecessorToken.empty() &&
		m_controlPlane.m_host.GetSession(recovery.predecessorToken, local))
	{
		if (local.session.executionContext.agentId != recovery.agentId ||
			local.session.executionContext.sessionId != recovery.sessionId ||
			local.session.executionContext.account != recovery.ownerAccount ||
			local.executionDomain != recovery.ownerExecutionDomain)
		{
			reason = "SESSION_RECOVERY_FENCE_BINDING_MISMATCH";
			return false;
		}
		recovery.token = recovery.predecessorToken;
		recovery.leaseGeneration = recovery.predecessorGeneration;
		recovery.predecessorToken.clear();
		recovery.predecessorGeneration = 0;
		localExists = true;
	}
	if (!localExists)
	{
		if (!m_leaseStore->Replace(
				durableCurrentToken, recovery, reason))
			return false;
		record = recovery;
		durableCurrentToken = record.token;
		TradingToolHostSessionBinding binding;
		if (!ResolveLeaseBinding(record, nowMs, binding, reason) ||
			!m_controlPlane.Provision(record.issuer, binding, reason))
			return false;
	}
	else
	{
		// Keep this adjusted identity in memory until the host commits the
		// durable recovery fence under its mutation-dispatch lock.  This
		// avoids publishing recovery-only state while the local bearer is
		// still entry-enabled.
		record = recovery;
	}
	return m_controlPlane.EnterRecoveryOnlyAndQuery(
		record.issuer, record.token, record.leaseGeneration,
		targetCommandId, *m_leaseStore, record, commandResult, reason,
		&ownerAudit, recovery.expiresAtMs, durableCurrentToken);
}

bool UnixSessionSupervisorServer::FinalizePaperRecovery(
	const SessionSupervisorLeaseRecord& record,
	ExecutionOwnerAuditResult& ownerAudit,
	std::string& reason)
{
	if (m_leaseStore == nullptr || record.templateId != "paper" ||
		!record.recoveryOnly || record.paperFinalizationRequired ||
		record.paperFinalizationState !=
			SessionSupervisorPaperFinalizationState::None)
	{
		reason = record.paperFinalizationRequired ?
			"PAPER_FINALIZATION_OPERATION_REQUIRED" :
			"SESSION_PAPER_RECOVERY_RECORD_INVALID";
		return false;
	}
	if (!m_controlPlane.FinalizeRecoveryOnlyOwner(
			record.issuer, record.token, record.leaseGeneration,
			record, ownerAudit, reason))
		return false;
	return m_leaseStore->Remove(record.token, reason);
}

bool UnixSessionSupervisorServer::HandlePaperFinalize(
	const SessionSupervisorRequest& request,
	SessionSupervisorResult& result)
{
	result.leaseGeneration = request.expectedGeneration;
	result.paperFinalizationRequired = true;
	result.PaperFinalizationState() = "NONE";
	result.RecoveryId() = request.recoveryId;
	result.FinalizationId() = request.finalizationId;
	result.ExpectedOwnerSetSha256() = request.expectedOwnerSetSha256;
	result.expectedOwnerCount = request.expectedOwnerCount;
	result.OwnerAccount() = "unavailable";
	result.OwnerExecutionDomain() = "unavailable";
	result.ExecutionServiceEpoch() = "unavailable";
	if (!PaperOwnerTokenSha256(
			request.token, result.OwnerTokenSha256()))
	{
		result.ReasonCode() = "PAPER_FINALIZATION_OWNER_HASH_FAILED";
		return false;
	}
	if (m_leaseStore == nullptr)
	{
		result.ReasonCode() = "SUPERVISOR_DURABLE_LEASE_STORE_REQUIRED";
		return false;
	}
	SessionSupervisorLeaseRecord record;
	if (!m_leaseStore->Get(request.token, record))
	{
		result.ReasonCode() = "SESSION_LEASE_NOT_FOUND";
		return false;
	}
	result.PaperFinalizationState() =
		SessionSupervisorPaperFinalizationStateName(
			record.paperFinalizationState);
	if (record.leaseGeneration != request.expectedGeneration)
	{
		result.ReasonCode() = "SESSION_LEASE_GENERATION_MISMATCH";
		return false;
	}
	if (record.templateId != "paper" || !record.recoveryOnly ||
		record.fencePending || record.fenceComplete)
	{
		result.ReasonCode() = "PAPER_FINALIZATION_RECOVERY_ONLY_REQUIRED";
		return false;
	}
	if (!record.paperFinalizationRequired)
	{
		result.ReasonCode() =
			"PAPER_FINALIZATION_TRANSITION_REQUIRED";
		return false;
	}
	const std::vector<SessionSupervisorLeaseRecord> initialRecords =
		m_leaseStore->List();
	std::string ownerSetCanonical;
	std::string actualOwnerSetSha256;
	std::string account;
	std::string domain;
	if (initialRecords.size() < request.expectedOwnerCount ||
		!CanonicalPaperOwnerSet(initialRecords, ownerSetCanonical,
			actualOwnerSetSha256, result.ReasonCode()) ||
		actualOwnerSetSha256 != request.expectedOwnerSetSha256)
	{
		if (result.ReasonCode().empty())
			result.ReasonCode() =
				"PAPER_FINALIZATION_OWNER_SET_MISMATCH";
		return false;
	}
	std::uint64_t actualOwnerCount = 0;
	for (std::size_t i = 0; i < initialRecords.size(); ++i)
		if (initialRecords[i].templateId == "paper") ++actualOwnerCount;
	if (actualOwnerCount != request.expectedOwnerCount ||
		!CommonPaperScope(
			initialRecords, account, domain, result.ReasonCode()))
	{
		if (result.ReasonCode().empty())
			result.ReasonCode() =
				"PAPER_FINALIZATION_OWNER_SET_MISMATCH";
		return false;
	}
	for (std::size_t i = 0; i < initialRecords.size(); ++i)
	{
		if (initialRecords[i].templateId != "paper") continue;
		if (!initialRecords[i].paperFinalizationRequired)
		{
			result.ReasonCode() =
				"PAPER_FINALIZATION_TRANSITION_REQUIRED";
			return false;
		}
		if (
			initialRecords[i].paperFinalizationState ==
				SessionSupervisorPaperFinalizationState::None)
			continue;
		if (!SameFinalizationGroup(initialRecords[i], request))
		{
			result.ReasonCode() =
				"PAPER_FINALIZATION_GROUP_MISMATCH";
			return false;
		}
	}
	if (record.paperFinalizationState !=
			SessionSupervisorPaperFinalizationState::None &&
		!SameFinalizationGroup(record, request))
	{
		result.ReasonCode() = "PAPER_FINALIZATION_GROUP_MISMATCH";
		return false;
	}
	if (record.paperFinalizationState ==
		SessionSupervisorPaperFinalizationState::AuditSealed)
	{
		result.FinalizationReceiptSha256() =
			record.finalizationReceiptSha256;
		result.PreliminaryFinalizationReceiptSha256() =
			record.finalizationReceiptSha256;
		result.FinalizationReceipt() = record.finalizationReceipt;
		if (!PopulateAuditFromReceipt(
				record.finalizationReceipt, result, result.ReasonCode()))
			return false;
		result.accepted = true;
		result.ReasonCode() = "PAPER_FINALIZATION_AUDIT_SEALED";
		return true;
	}
	if (record.paperFinalizationState ==
		SessionSupervisorPaperFinalizationState::None)
	{
		if (m_crashPointHook &&
			m_crashPointHook(
				"before_paper_finalization_pending_commit"))
		{
			result.ReasonCode() =
				"SUPERVISOR_FAULT_INJECTED:before_paper_finalization_pending_commit";
			return false;
		}
		SessionSupervisorLeaseRecord pending = record;
		pending.paperFinalizationState =
			SessionSupervisorPaperFinalizationState::FencePending;
		pending.recoveryId = request.recoveryId;
		pending.finalizationId = request.finalizationId;
		pending.expectedOwnerSetSha256 =
			request.expectedOwnerSetSha256;
		pending.expectedOwnerCount = request.expectedOwnerCount;
		pending.ownerTokenSha256 = result.OwnerTokenSha256();
		if (!m_leaseStore->AdvancePaperFinalization(
				record.token,
				SessionSupervisorPaperFinalizationState::None,
				pending, result.ReasonCode()))
			return false;
		record = pending;
		result.PaperFinalizationState() = "FENCE_PENDING";
		if (m_crashPointHook &&
			m_crashPointHook(
				"after_paper_finalization_pending_commit"))
		{
			result.ReasonCode() =
				"SUPERVISOR_FAULT_INJECTED:after_paper_finalization_pending_commit";
			return false;
		}
	}
	if (record.paperFinalizationState ==
		SessionSupervisorPaperFinalizationState::FencePending)
	{
		if (!m_controlPlane.FenceRecoveryOnlyOwner(
				record.issuer, record.token, record.leaseGeneration,
				record, result.ReasonCode()))
			return false;
		if (m_crashPointHook &&
			m_crashPointHook(
				"after_paper_finalization_remote_fence_before_complete_commit"))
		{
			result.ReasonCode() =
				"SUPERVISOR_FAULT_INJECTED:after_paper_finalization_remote_fence_before_complete_commit";
			return false;
		}
		SessionSupervisorLeaseRecord complete = record;
		complete.paperFinalizationState =
			SessionSupervisorPaperFinalizationState::FenceComplete;
		if (!m_leaseStore->AdvancePaperFinalization(
				record.token,
				SessionSupervisorPaperFinalizationState::FencePending,
				complete, result.ReasonCode()))
			return false;
		record = complete;
		result.PaperFinalizationState() = "FENCE_COMPLETE";
		if (m_crashPointHook &&
			m_crashPointHook(
				"after_paper_finalization_complete_commit"))
		{
			result.ReasonCode() =
				"SUPERVISOR_FAULT_INJECTED:after_paper_finalization_complete_commit";
			return false;
		}
	}
	const std::vector<SessionSupervisorLeaseRecord> completedRecords =
		m_leaseStore->List();
	std::vector<SessionSupervisorLeaseRecord> paperRecords;
	bool allComplete = true;
	bool allSealed = true;
	for (std::size_t i = 0; i < completedRecords.size(); ++i)
	{
		if (completedRecords[i].templateId != "paper") continue;
		paperRecords.push_back(completedRecords[i]);
		if (completedRecords[i].paperFinalizationState !=
				SessionSupervisorPaperFinalizationState::None &&
			!SameFinalizationGroup(completedRecords[i], request))
		{
			result.ReasonCode() =
				"PAPER_FINALIZATION_GROUP_MISMATCH";
			return false;
		}
		allComplete = allComplete &&
			(completedRecords[i].paperFinalizationState ==
				 SessionSupervisorPaperFinalizationState::FenceComplete ||
			 completedRecords[i].paperFinalizationState ==
				 SessionSupervisorPaperFinalizationState::AuditSealed);
		allSealed = allSealed &&
			completedRecords[i].paperFinalizationState ==
				SessionSupervisorPaperFinalizationState::AuditSealed;
	}
	if (!allComplete || paperRecords.size() != request.expectedOwnerCount)
	{
		result.ReasonCode() = "PAPER_FINALIZATION_GROUP_PENDING";
		return false;
	}
	if (allSealed)
	{
		const SessionSupervisorLeaseRecord& sealed = paperRecords.front();
		result.PaperFinalizationState() = "AUDIT_SEALED";
		result.FinalizationReceiptSha256() =
			sealed.finalizationReceiptSha256;
		result.PreliminaryFinalizationReceiptSha256() =
			sealed.finalizationReceiptSha256;
		result.FinalizationReceipt() = sealed.finalizationReceipt;
		if (!PopulateAuditFromReceipt(
				sealed.finalizationReceipt, result,
				result.ReasonCode()))
			return false;
		result.accepted = true;
		result.ReasonCode() = "PAPER_FINALIZATION_AUDIT_SEALED";
		return true;
	}
	std::sort(paperRecords.begin(), paperRecords.end(),
		[](const SessionSupervisorLeaseRecord& left,
			const SessionSupervisorLeaseRecord& right) {
			if (left.ownerTokenSha256 != right.ownerTokenSha256)
				return left.ownerTokenSha256 < right.ownerTokenSha256;
			return left.leaseGeneration < right.leaseGeneration;
		});
	ExecutionOwnerAuditResult audit;
	bool firstAudit = true;
	for (std::size_t i = 0; i < paperRecords.size(); ++i)
	{
		ExecutionOwnerAuditResult ownerAudit;
		std::string auditReason;
		if (!m_controlPlane.AuditFinalizedRecoveryOwner(
				paperRecords[i].issuer, paperRecords[i],
				ownerAudit, auditReason))
		{
			CopyFinalizationAudit(ownerAudit, result);
			result.ReasonCode() = auditReason.empty() ?
				"PAPER_FINALIZATION_AUDIT_INCOMPLETE" : auditReason;
			return false;
		}
		if (!ExactGlobalFinalizationAudit(
				account, domain, ownerAudit, result.ReasonCode()))
		{
			CopyFinalizationAudit(ownerAudit, result);
			return false;
		}
		if (!firstAudit &&
			!SameCompositeFinalizationBarrier(audit, ownerAudit))
		{
			CopyFinalizationAudit(ownerAudit, result);
			result.ReasonCode() =
				"PAPER_FINALIZATION_COMPOSITE_BARRIER_DRIFT";
			return false;
		}
		if (firstAudit)
		{
			audit = ownerAudit;
			firstAudit = false;
		}
	}
	CopyFinalizationAudit(audit, result);
	if (m_crashPointHook &&
		m_crashPointHook(
			"after_paper_finalization_audit_before_seal_commit"))
	{
		result.ReasonCode() =
			"SUPERVISOR_FAULT_INJECTED:after_paper_finalization_audit_before_seal_commit";
		return false;
	}
	const std::string receipt = BuildPaperFinalizationReceipt(
		request.recoveryId, request.finalizationId,
		request.expectedOwnerSetSha256, request.expectedOwnerCount,
		ownerSetCanonical, audit);
	std::string receiptSha256;
	if (!Sha256(receipt, receiptSha256) ||
		!m_leaseStore->SealPaperFinalizationGroup(
			request.recoveryId, request.finalizationId,
			request.expectedOwnerSetSha256, request.expectedOwnerCount,
			receiptSha256, receipt, result.ReasonCode()))
	{
		if (result.ReasonCode().empty())
			result.ReasonCode() = "PAPER_FINALIZATION_RECEIPT_FAILED";
		return false;
	}
	result.PaperFinalizationState() = "AUDIT_SEALED";
	result.FinalizationReceiptSha256() = receiptSha256;
	result.PreliminaryFinalizationReceiptSha256() = receiptSha256;
	result.FinalizationReceipt() = receipt;
	if (m_crashPointHook &&
		m_crashPointHook(
			"after_paper_finalization_audit_seal_commit"))
	{
		result.ReasonCode() =
			"SUPERVISOR_FAULT_INJECTED:after_paper_finalization_audit_seal_commit";
		return false;
	}
	result.accepted = true;
	result.ReasonCode() = "PAPER_FINALIZATION_AUDIT_SEALED";
	return true;
}

bool UnixSessionSupervisorServer::HandlePaperFinalizeAck(
	const SessionSupervisorRequest& request,
	SessionSupervisorResult& result)
{
	result.leaseGeneration = request.expectedGeneration;
	result.paperFinalizationRequired = true;
	result.PaperFinalizationState() = "NONE";
	result.RecoveryId() = request.recoveryId;
	result.FinalizationId() = request.finalizationId;
	result.ExpectedOwnerSetSha256() = request.expectedOwnerSetSha256;
	result.expectedOwnerCount = request.expectedOwnerCount;
	result.OwnerAccount() = "unavailable";
	result.OwnerExecutionDomain() = "unavailable";
	result.ExecutionServiceEpoch() = "unavailable";
	if (!PaperOwnerTokenSha256(
			request.token, result.OwnerTokenSha256()))
	{
		result.ReasonCode() = "PAPER_FINALIZATION_OWNER_HASH_FAILED";
		return false;
	}
	result.ReasonCode() = "PAPER_FINALIZATION_LEGACY_ACK_DISABLED";
	return false;
}

bool UnixSessionSupervisorServer::HandlePaperTerminalizeAck(
	const SessionSupervisorRequest& request,
	SessionSupervisorResult& result)
{
	result.leaseGeneration = request.expectedGeneration;
	result.paperFinalizationRequired = true;
	result.PaperFinalizationState() = "NONE";
	result.RecoveryId() = request.recoveryId;
	result.FinalizationId() = request.finalizationId;
	result.ExpectedOwnerSetSha256() = request.expectedOwnerSetSha256;
	result.expectedOwnerCount = request.expectedOwnerCount;
	result.OwnerAccount() = "unavailable";
	result.OwnerExecutionDomain() = "unavailable";
	result.ExecutionServiceEpoch() = "unavailable";
	if (!PaperOwnerTokenSha256(
			request.token, result.OwnerTokenSha256()))
	{
		result.ReasonCode() = "PAPER_FINALIZATION_OWNER_HASH_FAILED";
		return false;
	}
	// The v2 operation depended on an unobservable vendor raw/socket drain.
	// Keep the wire command parse-compatible, but permanently disable it.
	result.ReasonCode() = "PAPER_TERMINAL_ACK_V2_DISABLED";
	return false;
}

bool UnixSessionSupervisorServer::HandlePaperTerminalWitnessPrepare(
	const SessionSupervisorRequest& request,
	SessionSupervisorResult& result)
{
	result.leaseGeneration = request.expectedGeneration;
	result.paperFinalizationRequired = true;
	result.PaperFinalizationState() = "NONE";
	result.RecoveryId() = request.recoveryId;
	result.FinalizationId() = request.finalizationId;
	result.ExpectedOwnerSetSha256() = request.expectedOwnerSetSha256;
	result.expectedOwnerCount = request.expectedOwnerCount;
	result.OwnerAccount() = "unavailable";
	result.OwnerExecutionDomain() = "unavailable";
	result.ExecutionServiceEpoch() = "unavailable";
	if (!PaperOwnerTokenSha256(request.token, result.OwnerTokenSha256()))
	{
		result.ReasonCode() = "PAPER_FINALIZATION_OWNER_HASH_FAILED";
		return false;
	}
	if (m_leaseStore == nullptr)
	{
		result.ReasonCode() = "SUPERVISOR_DURABLE_LEASE_STORE_REQUIRED";
		return false;
	}
	SessionSupervisorPaperFinalizationAck existingAck;
	if (m_leaseStore->GetPaperFinalizationAck(
			request.finalizationId, existingAck))
	{
		result.ReasonCode() = "PAPER_TERMINAL_WITNESS_ALREADY_ACKED";
		return false;
	}

	SessionSupervisorLeaseRecord requestOwner;
	if (!m_leaseStore->Get(request.token, requestOwner))
	{
		result.ReasonCode() = "SESSION_LEASE_NOT_FOUND";
		return false;
	}
	result.PaperFinalizationState() =
		SessionSupervisorPaperFinalizationStateName(
			requestOwner.paperFinalizationState);
	if (requestOwner.paperFinalizationState ==
		SessionSupervisorPaperFinalizationState::AuditSealed)
	{
		result.PreliminaryFinalizationReceiptSha256() =
			requestOwner.finalizationReceiptSha256;
		result.FinalizationReceiptSha256() =
			requestOwner.finalizationReceiptSha256;
		result.FinalizationReceipt() = requestOwner.finalizationReceipt;
		if (!PopulateAuditFromReceipt(requestOwner.finalizationReceipt,
				result, result.ReasonCode())) return false;
	}
	if (requestOwner.leaseGeneration != request.expectedGeneration ||
		!requestOwner.paperFinalizationRequired ||
		requestOwner.ownerTokenSha256 != result.OwnerTokenSha256() ||
		requestOwner.paperFinalizationState !=
			SessionSupervisorPaperFinalizationState::AuditSealed ||
		!SameFinalizationGroup(requestOwner, request) ||
		requestOwner.finalizationReceiptSha256 != request.receiptSha256)
	{
		result.ReasonCode() =
			"PAPER_TERMINAL_WITNESS_PREPARE_BINDING_MISMATCH";
		return false;
	}

	std::vector<SessionSupervisorLeaseRecord> paperRecords;
	const std::vector<SessionSupervisorLeaseRecord> records =
		m_leaseStore->List();
	std::string account;
	std::string domain;
	std::string canonical;
	std::string ownerSetSha256;
	for (std::size_t i = 0; i < records.size(); ++i)
	{
		if (records[i].templateId != "paper") continue;
		if (!records[i].paperFinalizationRequired ||
			records[i].paperFinalizationState !=
				SessionSupervisorPaperFinalizationState::AuditSealed ||
			!SameFinalizationGroup(records[i], request) ||
			records[i].finalizationReceiptSha256 != request.receiptSha256 ||
			records[i].finalizationReceipt != requestOwner.finalizationReceipt)
		{
			result.ReasonCode() =
				"PAPER_TERMINAL_WITNESS_PREPARE_BINDING_MISMATCH";
			return false;
		}
		paperRecords.push_back(records[i]);
	}
	if (paperRecords.size() != request.expectedOwnerCount ||
		!CanonicalPaperOwnerSet(paperRecords, canonical, ownerSetSha256,
			result.ReasonCode()) ||
		ownerSetSha256 != request.expectedOwnerSetSha256 ||
		!CommonPaperScope(paperRecords, account, domain,
			result.ReasonCode()))
	{
		if (result.ReasonCode().empty())
			result.ReasonCode() =
				"PAPER_TERMINAL_WITNESS_PREPARE_BINDING_MISMATCH";
		return false;
	}
	std::sort(paperRecords.begin(), paperRecords.end(),
		[](const SessionSupervisorLeaseRecord& left,
			const SessionSupervisorLeaseRecord& right) {
			if (left.ownerTokenSha256 != right.ownerTokenSha256)
				return left.ownerTokenSha256 < right.ownerTokenSha256;
			return left.leaseGeneration < right.leaseGeneration;
		});
	const SessionSupervisorLeaseRecord& terminalOwner = paperRecords.front();
	if (terminalOwner.ownerTokenSha256 != result.OwnerTokenSha256() ||
		terminalOwner.leaseGeneration != request.expectedGeneration)
	{
		result.ReasonCode() =
			"PAPER_TERMINAL_WITNESS_PREPARE_OWNER_NOT_DETERMINISTIC";
		return false;
	}
	if (result.OwnerAccount() != account ||
		result.OwnerExecutionDomain() != domain)
	{
		result.ReasonCode() =
			"PAPER_FINALIZATION_RECEIPT_SCOPE_MISMATCH";
		return false;
	}

	ExecutionControlResult terminal;
	std::string terminalReason;
	const bool localTerminal =
		m_controlPlane.TerminalizeFinalizedRecoveryOwner(
			terminalOwner.issuer, terminalOwner, request.receiptSha256,
			terminal, terminalReason);
	if (localTerminal)
	{
		result.ReasonCode() =
			"PAPER_TERMINAL_WITNESS_PREPARE_UNEXPECTED_LOCAL_TERMINAL";
		return false;
	}
	if (terminal.status != ExecutionCommandStatus::Rejected ||
		terminal.reasonCode != terminalReason ||
		terminal.targetCommandId != request.finalizationId ||
		terminal.ownerAccount != account ||
		terminal.ownerExecutionDomain != domain)
	{
		result.ReasonCode() =
			"PAPER_TERMINAL_WITNESS_PREPARE_RUNTIME_RESULT_INVALID";
		return false;
	}
	if (terminalReason == "IB_PAPER_TERMINALIZATION_INCOMPLETE")
	{
		// A bare TERMINALIZING latch proves intent only. Root must stop the
		// runtime and establish a fresh zero-boundary cutoff before attesting.
		result.ReasonCode() =
			"PAPER_TERMINAL_WITNESS_PREPARE_INTENT_PENDING";
		return false;
	}
	if (terminalReason != "POST_CUTOFF_SIGNED_WITNESS_REQUIRED")
	{
		result.ReasonCode() = terminalReason.empty() ?
			"PAPER_TERMINAL_WITNESS_PREPARE_FAILED" : terminalReason;
		return false;
	}
	if (m_crashPointHook && m_crashPointHook(
			"after_paper_terminal_witness_prepare_before_reply"))
	{
		result.ReasonCode() =
			"SUPERVISOR_FAULT_INJECTED:"
			"after_paper_terminal_witness_prepare_before_reply";
		return false;
	}
	result.accepted = true;
	result.ReasonCode() = "PAPER_TERMINAL_WITNESS_PREPARED";
	return true;
}

bool UnixSessionSupervisorServer::HandlePaperTerminalWitnessAck(
	const SessionSupervisorRequest& request,
	SessionSupervisorResult& result)
{
	result.leaseGeneration = request.expectedGeneration;
	result.paperFinalizationRequired = true;
	result.PaperFinalizationState() = "NONE";
	result.RecoveryId() = request.recoveryId;
	result.FinalizationId() = request.finalizationId;
	result.ExpectedOwnerSetSha256() = request.expectedOwnerSetSha256;
	result.expectedOwnerCount = request.expectedOwnerCount;
	result.OwnerAccount() = "unavailable";
	result.OwnerExecutionDomain() = "unavailable";
	result.ExecutionServiceEpoch() = "unavailable";
	if (!PaperOwnerTokenSha256(request.token, result.OwnerTokenSha256()))
	{
		result.ReasonCode() = "PAPER_FINALIZATION_OWNER_HASH_FAILED";
		return false;
	}
	if (m_leaseStore == nullptr)
	{
		result.ReasonCode() = "SUPERVISOR_DURABLE_LEASE_STORE_REQUIRED";
		return false;
	}

	SessionSupervisorPaperFinalizationAck existingAck;
	if (m_leaseStore->GetPaperFinalizationAck(
			request.finalizationId, existingAck))
	{
		if (existingAck.recoveryId != request.recoveryId ||
			existingAck.expectedOwnerSetSha256 !=
				request.expectedOwnerSetSha256 ||
			existingAck.expectedOwnerCount != request.expectedOwnerCount ||
			existingAck.receiptSha256 != request.receiptSha256 ||
			existingAck.acknowledgingOwnerTokenSha256 !=
				result.OwnerTokenSha256() ||
			existingAck.acknowledgingOwnerGeneration !=
				request.expectedGeneration)
		{
			result.ReasonCode() =
				"PAPER_TERMINAL_ACK_BINDING_MISMATCH";
			return false;
		}
		SessionSupervisorResult preliminary;
		preliminary.RecoveryId() = existingAck.recoveryId;
		preliminary.FinalizationId() = existingAck.finalizationId;
		preliminary.ExpectedOwnerSetSha256() =
			existingAck.expectedOwnerSetSha256;
		preliminary.expectedOwnerCount = existingAck.expectedOwnerCount;
		if (!PopulateAuditFromReceipt(existingAck.receipt, preliminary,
				result.ReasonCode())) return false;
		SessionSupervisorLeaseRecord replayOwner;
		replayOwner.templateId = "paper";
		replayOwner.issuer = existingAck.acknowledgingOwnerIssuer;
		replayOwner.token = request.token;
		replayOwner.agentId = existingAck.terminalizingOwnerAgentId;
		replayOwner.sessionId = existingAck.terminalizingOwnerSessionId;
		replayOwner.ownerAccount = existingAck.terminalizingOwnerAccount;
		replayOwner.ownerExecutionDomain =
			existingAck.terminalizingOwnerExecutionDomain;
		replayOwner.leaseGeneration =
			existingAck.acknowledgingOwnerGeneration;
		std::map<std::string, std::string> evidence;
		if (!ValidateTerminalEvidence(request, replayOwner, preliminary,
				replayOwner.ownerAccount, replayOwner.ownerExecutionDomain,
				evidence, result.ReasonCode())) return false;
		const std::string rebuilt = BuildPaperTerminalWitnessAckReceipt(
			evidence, request.terminalEvidenceSha256);
		std::string rebuiltSha256;
		if (!Sha256(rebuilt, rebuiltSha256) ||
			rebuilt != existingAck.terminalReceipt ||
			rebuiltSha256 != existingAck.terminalReceiptSha256)
		{
			result.ReasonCode() =
				"PAPER_TERMINAL_ACK_REPLAY_EVIDENCE_MISMATCH";
			return false;
		}
		CopyTerminalEvidenceResult(evidence,
			request.terminalEvidenceSha256, true, result);
		result.PreliminaryFinalizationReceiptSha256() =
			existingAck.receiptSha256;
		result.PaperFinalizationState() = "ACKED";
		result.FinalizationReceiptSha256() =
			existingAck.terminalReceiptSha256;
		result.FinalizationReceipt() = existingAck.terminalReceipt;
		result.accepted = true;
		result.ReasonCode() = "PAPER_FINALIZATION_TERMINAL_ACKED";
		return true;
	}

	SessionSupervisorLeaseRecord requestOwner;
	if (!m_leaseStore->Get(request.token, requestOwner))
	{
		result.ReasonCode() = "SESSION_LEASE_NOT_FOUND";
		return false;
	}
	result.PaperFinalizationState() =
		SessionSupervisorPaperFinalizationStateName(
			requestOwner.paperFinalizationState);
	if (requestOwner.paperFinalizationState ==
		SessionSupervisorPaperFinalizationState::AuditSealed)
	{
		result.PreliminaryFinalizationReceiptSha256() =
			requestOwner.finalizationReceiptSha256;
		result.FinalizationReceiptSha256() =
			requestOwner.finalizationReceiptSha256;
		result.FinalizationReceipt() = requestOwner.finalizationReceipt;
		if (!PopulateAuditFromReceipt(requestOwner.finalizationReceipt,
				result, result.ReasonCode())) return false;
	}
	if (requestOwner.leaseGeneration != request.expectedGeneration ||
		!requestOwner.paperFinalizationRequired ||
		requestOwner.ownerTokenSha256 != result.OwnerTokenSha256() ||
		requestOwner.paperFinalizationState !=
			SessionSupervisorPaperFinalizationState::AuditSealed ||
		!SameFinalizationGroup(requestOwner, request) ||
		requestOwner.finalizationReceiptSha256 != request.receiptSha256)
	{
		result.ReasonCode() = "PAPER_TERMINAL_ACK_BINDING_MISMATCH";
		return false;
	}

	std::vector<SessionSupervisorLeaseRecord> paperRecords;
	const std::vector<SessionSupervisorLeaseRecord> records =
		m_leaseStore->List();
	std::string account;
	std::string domain;
	std::string canonical;
	std::string ownerSetSha256;
	for (std::size_t i = 0; i < records.size(); ++i)
	{
		if (records[i].templateId != "paper") continue;
		if (!records[i].paperFinalizationRequired ||
			records[i].paperFinalizationState !=
				SessionSupervisorPaperFinalizationState::AuditSealed ||
			!SameFinalizationGroup(records[i], request) ||
			records[i].finalizationReceiptSha256 != request.receiptSha256 ||
			records[i].finalizationReceipt != requestOwner.finalizationReceipt)
		{
			result.ReasonCode() =
				"PAPER_TERMINAL_ACK_BINDING_MISMATCH";
			return false;
		}
		paperRecords.push_back(records[i]);
	}
	if (paperRecords.size() != request.expectedOwnerCount ||
		!CanonicalPaperOwnerSet(paperRecords, canonical, ownerSetSha256,
			result.ReasonCode()) ||
		ownerSetSha256 != request.expectedOwnerSetSha256 ||
		!CommonPaperScope(paperRecords, account, domain,
			result.ReasonCode()))
	{
		if (result.ReasonCode().empty())
			result.ReasonCode() = "PAPER_TERMINAL_ACK_BINDING_MISMATCH";
		return false;
	}
	std::sort(paperRecords.begin(), paperRecords.end(),
		[](const SessionSupervisorLeaseRecord& left,
			const SessionSupervisorLeaseRecord& right) {
			if (left.ownerTokenSha256 != right.ownerTokenSha256)
				return left.ownerTokenSha256 < right.ownerTokenSha256;
			return left.leaseGeneration < right.leaseGeneration;
		});
	const SessionSupervisorLeaseRecord& terminalOwner = paperRecords.front();
	if (terminalOwner.ownerTokenSha256 != result.OwnerTokenSha256() ||
		terminalOwner.leaseGeneration != request.expectedGeneration)
	{
		result.ReasonCode() =
			"PAPER_TERMINAL_ACK_OWNER_NOT_DETERMINISTIC";
		return false;
	}
	SessionSupervisorResult preliminary;
	preliminary.RecoveryId() = request.recoveryId;
	preliminary.FinalizationId() = request.finalizationId;
	preliminary.ExpectedOwnerSetSha256() = request.expectedOwnerSetSha256;
	preliminary.expectedOwnerCount = request.expectedOwnerCount;
	if (!PopulateAuditFromReceipt(requestOwner.finalizationReceipt,
			preliminary, result.ReasonCode()) ||
		preliminary.OwnerAccount() != account ||
		preliminary.OwnerExecutionDomain() != domain)
	{
		if (result.ReasonCode().empty())
			result.ReasonCode() =
				"PAPER_FINALIZATION_RECEIPT_SCOPE_MISMATCH";
		return false;
	}
	std::map<std::string, std::string> evidence;
	if (!ValidateTerminalEvidence(request, terminalOwner, preliminary,
			account, domain, evidence, result.ReasonCode())) return false;
	const std::string terminalReceipt =
		BuildPaperTerminalWitnessAckReceipt(
			evidence, request.terminalEvidenceSha256);
	std::string terminalReceiptSha256;
	if (terminalReceipt.empty() || terminalReceipt.size() > 12288 ||
		!Sha256(terminalReceipt, terminalReceiptSha256))
	{
		result.ReasonCode() = "PAPER_TERMINAL_ACK_RECEIPT_HASH_FAILED";
		return false;
	}
	if (m_crashPointHook && m_crashPointHook(
			"after_paper_terminal_witness_evidence_before_bearer_purge"))
	{
		result.ReasonCode() =
			"SUPERVISOR_FAULT_INJECTED:"
			"after_paper_terminal_witness_evidence_before_bearer_purge";
		return false;
	}
	for (std::size_t i = 0; i < paperRecords.size(); ++i)
	{
		if (!m_controlPlane.PurgeFinalizedRecoveryOwner(
				paperRecords[i].issuer, paperRecords[i],
				result.ReasonCode())) return false;
		if (i + 1 < paperRecords.size() && m_crashPointHook &&
			m_crashPointHook(
				"after_paper_terminal_witness_partial_bearer_purge"))
		{
			result.ReasonCode() =
				"SUPERVISOR_FAULT_INJECTED:"
				"after_paper_terminal_witness_partial_bearer_purge";
			return false;
		}
	}
	if (m_crashPointHook && m_crashPointHook(
			"after_paper_terminal_witness_bearer_purge_before_ack_commit"))
	{
		result.ReasonCode() =
			"SUPERVISOR_FAULT_INJECTED:"
			"after_paper_terminal_witness_bearer_purge_before_ack_commit";
		return false;
	}
	SessionSupervisorPaperFinalizationAck acknowledgement;
	bool alreadyAcknowledged = false;
	if (!m_leaseStore->AcknowledgeAndPurgePaperFinalizationGroup(
			request.recoveryId, request.finalizationId,
			request.expectedOwnerSetSha256, request.expectedOwnerCount,
			request.receiptSha256, terminalReceiptSha256, terminalReceipt,
			result.OwnerTokenSha256(), request.expectedGeneration,
			terminalOwner.issuer, terminalOwner.agentId,
			terminalOwner.sessionId, terminalOwner.ownerAccount,
			terminalOwner.ownerExecutionDomain, acknowledgement,
			alreadyAcknowledged, result.ReasonCode())) return false;
	(void)alreadyAcknowledged;
	if (acknowledgement.terminalReceiptSha256 != terminalReceiptSha256 ||
		acknowledgement.terminalReceipt != terminalReceipt)
	{
		result.ReasonCode() = "PAPER_TERMINAL_ACK_LEDGER_MISMATCH";
		return false;
	}
	CopyTerminalEvidenceResult(evidence,
		request.terminalEvidenceSha256, false, result);
	result.PreliminaryFinalizationReceiptSha256() = request.receiptSha256;
	result.PaperFinalizationState() = "ACKED";
	result.FinalizationReceiptSha256() =
		acknowledgement.terminalReceiptSha256;
	result.FinalizationReceipt() = acknowledgement.terminalReceipt;
	if (m_crashPointHook &&
		m_crashPointHook("after_paper_terminal_witness_ack_commit"))
	{
		result.ReasonCode() =
			"SUPERVISOR_FAULT_INJECTED:"
			"after_paper_terminal_witness_ack_commit";
		return false;
	}
	result.accepted = true;
	result.ReasonCode() = "PAPER_FINALIZATION_TERMINAL_ACKED";
	return true;
}
