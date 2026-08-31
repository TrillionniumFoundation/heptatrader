#include "trading_tool_host.h"
#include "session_supervisor_lease_store.h"
#include "../tools/trading_tool_wire_contract.h"
#include <cmath>
#include <chrono>
#include <cstring>
#include <exception>

namespace
{
std::uint64_t EpochNowMs()
{
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count());
}

bool IsDiscoveryToolName(const std::string& toolName)
{
    return toolName == "system.tools.list" ||
        toolName == "system.tools.describe";
}

const std::uint32_t kMaximumCancelCallsPerMinute = 4;
const std::uint32_t kMaximumFlattenCallsPerMinute = 2;
const std::size_t kMaximumMutationReplayRecords = 2048;
const std::chrono::hours kMutationReplayTtl(24);

TradingToolResult MutationDispatchExceptionResult(const std::string& toolName)
{
    TradingToolResult result;
    result.status = TradingToolCallStatus::Uncertain;
    result.toolName = toolName;
    // The registry and Execution Service use this code for an exception at
    // their mutation boundary. Keep the host fallback identical so callers
    // always enter the same recovery/idempotency path even if an unexpected
    // implementation exception escapes the registry.
    result.reasonCode = "EXECUTION_AUTHORITY_EXCEPTION";
    result.detail = "execution authority outcome is uncertain";
    return result;
}

std::string BoundedHostDetail(const std::string& value,
                              const char* fallback)
{
    if (value.size() > 65536u ||
        value.find('\0') != std::string::npos)
        return fallback;
    return value;
}

TradingToolResult ReadDispatchExceptionResult(const std::string& toolName)
{
    TradingToolResult result;
    result.status = TradingToolCallStatus::Error;
    result.toolName = toolName;
    result.reasonCode = "TOOL_DISPATCH_EXCEPTION";
    result.detail = "tool dispatch failed";
    return result;
}

// Recovery-only owners retain read and explicitly owned risk-reduction
// authority, but must not create a new entry path.  Target-position apply is
// intentionally included alongside the raw place tool: a target can be
// either an increase or a reduction, and the host cannot safely infer which
// from the untrusted request without re-running the full authoritative plan.
bool IsRecoveryBlockedEntryTool(const std::string& toolName)
{
    return toolName == "trade.place_order" ||
        toolName == "intent.apply_target_position";
}

bool IsRecoveryBlockedEntryPreview(const std::string& toolName)
{
    return toolName == "risk.preview_order" ||
        toolName == "intent.preview_target_position";
}

bool SameDoubleBits(double left, double right)
{
    return std::memcmp(&left, &right, sizeof(left)) == 0;
}

bool SameContract(const InstrumentRef& left, const InstrumentRef& right)
{
    return left.symbol == right.symbol &&
        left.secType == right.secType &&
        left.exchange == right.exchange &&
        left.primaryExchange == right.primaryExchange &&
        left.currency == right.currency &&
        left.lastTradeDateOrContractMonth ==
            right.lastTradeDateOrContractMonth &&
        left.right == right.right &&
        SameDoubleBits(left.strike, right.strike) &&
        left.multiplier == right.multiplier &&
        left.tradingClass == right.tradingClass &&
        left.localSymbol == right.localSymbol;
}

bool SameOrder(const OrderIntent& left, const OrderIntent& right)
{
    return left.action == right.action &&
        left.orderType == right.orderType &&
        SameDoubleBits(left.totalQuantity, right.totalQuantity) &&
        SameDoubleBits(left.lmtPrice, right.lmtPrice) &&
        SameDoubleBits(left.auxPrice, right.auxPrice) &&
        left.outsideRth == right.outsideRth &&
        left.orderRef == right.orderRef;
}

bool SameExecutionContext(const TradingToolSession& left,
                          const TradingToolSession& right)
{
    return left.executionContext.strategy ==
            right.executionContext.strategy &&
        left.executionContext.account == right.executionContext.account &&
        left.executionContext.venue == right.executionContext.venue &&
        left.executionContext.executionDomain ==
            right.executionContext.executionDomain;
}

bool SameMutationPayload(const TradingToolSession& leftSession,
                         const TradingToolCall& left,
                         const TradingToolSession& rightSession,
                         const TradingToolCall& right)
{
    if (left.name != right.name ||
        !SameExecutionContext(leftSession, rightSession))
        return false;
    if (left.name == "trade.cancel_order")
        return left.orderId == right.orderId;
    if (left.name == "trade.flatten_position")
        return left.instrument == right.instrument &&
            SameContract(left.ibContract, right.ibContract);
    if (left.name == "trade.place_order")
        return left.instrument == right.instrument &&
            SameDoubleBits(left.referencePrice, right.referencePrice) &&
            left.expiresAtMs == right.expiresAtMs &&
            left.timeInForce == right.timeInForce &&
            SameContract(left.ibContract, right.ibContract) &&
            SameOrder(left.ibOrder, right.ibOrder);
    if (left.name == "intent.apply_target_position")
        // The target permit is a one-time credential and is intentionally not
        // part of the idempotency payload.  Retries compare the normalized
        // target request so the registry can return its durable replay result.
        return left.instrument == right.instrument &&
            left.targetCommandId == right.targetCommandId &&
            SameDoubleBits(left.ibOrder.totalQuantity,
                           right.ibOrder.totalQuantity) &&
            SameDoubleBits(left.referencePrice, right.referencePrice) &&
            left.expiresAtMs == right.expiresAtMs;
    return false;
}

bool ExactZeroOwnerAudit(
    const TradingToolHostSessionBinding& binding,
    const ExecutionControlResult& audit,
    std::string& reason)
{
    if (audit.status != ExecutionCommandStatus::Accepted ||
        !audit.ownerAuditAuthoritative || !audit.ownerAuditComplete ||
        audit.ownerAccount != binding.session.executionContext.account ||
        audit.ownerExecutionDomain != binding.executionDomain ||
        audit.brokerConnectionEpoch == 0 ||
        audit.brokerActiveGeneration == 0 ||
        audit.brokerTerminalGeneration == 0)
    {
        reason = audit.reasonCode.empty() ?
            "SESSION_OWNER_AUDIT_INCOMPLETE" : audit.reasonCode;
        return false;
    }
    if (audit.ownerActiveOrderCount != 0 ||
        audit.ownerUncertainCommandCount != 0)
    {
        reason = "SESSION_OWNER_RECOVERY_REQUIRED";
        return false;
    }
    reason.clear();
    return true;
}
}

TradingToolHost::TradingToolHost(TradingToolRegistry& registry)
    : m_registry(registry), m_decisionLeases(m_ownedDecisionLeases)
{
}

TradingToolHost::TradingToolHost(TradingToolRegistry& registry,
                                 DecisionLeaseManager& decisionLeases,
                                 const TradingToolMutationReadiness& mutationReadiness)
    : m_registry(registry), m_decisionLeases(decisionLeases), m_mutationReadiness(mutationReadiness)
{
}

TradingToolResult TradingToolHost::Reject(const std::string& toolName,
                                          TradingToolCallStatus status,
                                          const std::string& reasonCode,
                                          const std::string& detail)
{
    TradingToolResult result;
    result.toolName = toolName;
    result.status = status;
    result.reasonCode = reasonCode;
    result.detail = detail;
    return result;
}

std::string TradingToolHost::SessionLeaseKey(const std::string& token, const std::string& instrument)
{
    return std::to_string(token.size()) + ":" + token + instrument;
}

std::string TradingToolHost::SessionOwnerKey(
    const TradingToolHostSessionBinding& binding)
{
    const std::string& agentId = binding.session.executionContext.agentId;
    const std::string& sessionId = binding.session.executionContext.sessionId;
    return std::to_string(agentId.size()) + ":" + agentId + sessionId;
}

std::string TradingToolHost::MutationReplayKey(
    const TradingToolHostSessionBinding& binding,
    const std::string& toolCallId)
{
    const std::string ownerKey = SessionOwnerKey(binding);
    return std::to_string(ownerKey.size()) + ":" + ownerKey + toolCallId;
}

void TradingToolHost::PruneMutationReplaysLocked(
    std::chrono::steady_clock::time_point now) const
{
    for (std::unordered_map<std::string,
             MutationReplayRecord>::iterator it = m_mutationReplays.begin();
         it != m_mutationReplays.end();)
    {
        if (it->second.steadyExpiresAt <= now)
            it = m_mutationReplays.erase(it);
        else
            ++it;
    }
}

void TradingToolHost::RevokeSession(const std::string& token)
{
    std::string reason;
    RevokeSessionWithReason(token, "session_revoked", reason);
}

bool TradingToolHost::RevokeSession(
    const std::string& token, std::uint64_t expectedGeneration, std::string& reason)
{
    return RevokeSession(token, expectedGeneration, "session_revoked", reason);
}

bool TradingToolHost::RevokeSession(
    const std::string& token,
    std::uint64_t expectedGeneration,
    const std::string& revokeReason,
    std::string& reason)
{
    std::lock_guard<std::mutex> dispatchLock(m_mutationDispatchMutex);
    return RevokeSessionUnderDispatchLock(
        token, expectedGeneration, nullptr, nullptr, nullptr,
        revokeReason, reason);
}

bool TradingToolHost::RevokeCurrentSessionIfOwner(
    const std::string& token,
    const std::string& expectedAgentId,
    const std::string& expectedSessionId,
    const std::string& revokeReason,
    std::string& reason)
{
    if (expectedAgentId.empty() || expectedSessionId.empty())
    {
        reason = "INVALID_SESSION_IDENTITY";
        return false;
    }
    std::lock_guard<std::mutex> dispatchLock(m_mutationDispatchMutex);
    return RevokeSessionUnderDispatchLock(
        token, 0, &expectedAgentId, &expectedSessionId, nullptr,
        revokeReason, reason);
}

TradingToolResult TradingToolHost::EnsureDecisionLease(const TradingToolHostSessionBinding& binding,
                                                        const std::string& sessionToken,
                                                        const TradingToolCall& call,
                                                        DecisionLeaseCredential& credential)
{
    DecisionLeaseKey key;
    key.executionDomain = binding.executionDomain;
    key.account = binding.session.executionContext.account;
    key.instrument = call.instrument;
    DecisionLeaseOwner owner;
    owner.agentId = binding.session.executionContext.agentId;
    owner.sessionId = binding.session.executionContext.sessionId;
    const std::string lookup = SessionLeaseKey(sessionToken, call.instrument);
    DecisionLeaseCredential existing;
    bool haveExisting = false;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        const std::unordered_map<std::string, ActiveDecisionLease>::const_iterator found =
            m_activeDecisionLeases.find(lookup);
        if (found != m_activeDecisionLeases.end())
        {
            existing = found->second.credential;
            haveExisting = true;
        }
    }

    const std::chrono::milliseconds ttl(binding.decisionLeaseTtlMs);
    DecisionLeaseResult lease = haveExisting ? m_decisionLeases.Renew(key, owner, existing, ttl) :
                                               m_decisionLeases.Acquire(key, owner, ttl);
    if (!lease.Succeeded() && haveExisting &&
        (lease.status == DecisionLeaseStatus::Expired || lease.status == DecisionLeaseStatus::NotFound ||
         lease.status == DecisionLeaseStatus::StaleFence))
    {
        lease = m_decisionLeases.Acquire(key, owner, ttl);
    }
    if (!lease.Succeeded())
    {
        const bool busy = lease.status == DecisionLeaseStatus::Busy ||
                          lease.status == DecisionLeaseStatus::OwnerMismatch;
        return Reject(call.name, TradingToolCallStatus::Rejected,
                      busy ? "DECISION_LEASE_BUSY" : "DECISION_LEASE_REJECTED",
                      DecisionLeaseManager::StatusName(lease.status));
    }

    {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (m_sessions.find(sessionToken) == m_sessions.end())
        {
            m_decisionLeases.Release(key, owner, lease.credential);
            return Reject(call.name, TradingToolCallStatus::PermissionDenied,
                          "SESSION_REVOKED", "Agent session was revoked during authorization");
        }
        ActiveDecisionLease active;
        active.sessionToken = sessionToken;
        active.key = key;
        active.owner = owner;
        active.credential = lease.credential;
        m_activeDecisionLeases[lookup] = active;
    }
    credential = lease.credential;
    TradingToolResult ok;
    ok.status = TradingToolCallStatus::Ok;
    ok.toolName = call.name;
    return ok;
}

TradingToolResult TradingToolHost::AuthorizeCommon(
    std::uint32_t peerUid,
    const TradingToolHostRequest& request,
    TradingToolHostSessionBinding& binding,
    TradingToolDescriptor& descriptor)
{
	const std::uint64_t nowMs = EpochNowMs();
    if (request.toolCallId.empty())
        return Reject(request.call.name, TradingToolCallStatus::Rejected,
                      "TOOL_CALL_ID_REQUIRED", "each call requires an idempotency key");
    // Direct in-process callers do not pass through TypedToolProtocol. Keep
    // the host API subject to the same bounded idempotency-key grammar as the
    // binary wire so controls, all-punctuation keys and oversized values
    // cannot enter replay/journal namespaces through an alternate path.
    if (!TradingToolWireContract::IsCanonicalCommandId(request.toolCallId))
        return Reject(request.call.name, TradingToolCallStatus::Rejected,
                      "INVALID_COMMAND_ID",
                      "tool_call_id must be a bounded canonical identifier");

    {
        std::lock_guard<std::mutex> lock(m_mutex);
        const std::unordered_map<std::string, TradingToolHostSessionBinding>::const_iterator found =
            m_sessions.find(request.sessionToken);
        if (found == m_sessions.end())
            return Reject(request.call.name, TradingToolCallStatus::PermissionDenied,
                          "SESSION_NOT_FOUND", "unknown or revoked Agent session");
        if (WatchTransactionPendingLocked(found->second))
            return Reject(request.call.name, TradingToolCallStatus::PermissionDenied,
                          "SESSION_OWNER_FENCE_PENDING",
                          "Agent session owner has a pending WATCH transaction");
        binding = found->second;
    }

    if (!binding.enabled)
        return Reject(request.call.name, TradingToolCallStatus::PermissionDenied,
                      "SESSION_DISABLED", "Agent session is disabled");
    if (binding.peerUid != peerUid)
        return Reject(request.call.name, TradingToolCallStatus::PermissionDenied,
                      "PEER_UID_MISMATCH", "Unix peer identity does not own this session");
    if (binding.expiresAtMs <= nowMs)
    {
        std::string revokeReason;
        RevokeSessionWithReason(request.sessionToken, "session_expired", revokeReason);
        return Reject(request.call.name, TradingToolCallStatus::PermissionDenied,
                      "SESSION_EXPIRED", "Agent session has expired");
    }

	if (!m_registry.GetDescriptor(request.call.name, descriptor))
		return Reject(request.call.name, TradingToolCallStatus::InvalidTool,
			"UNKNOWN_TOOL", "tool is not registered by HeptaTrader");
	if (descriptor.effect == TradingToolEffect::Trade &&
		binding.session.environment == "WATCH")
		return Reject(request.call.name, TradingToolCallStatus::PermissionDenied,
			"WATCH_SESSION_CANNOT_TRADE",
			"WATCH sessions have no mutation authority");
	if (binding.session.capabilities.find(descriptor.requiredCapability) ==
		binding.session.capabilities.end())
		return Reject(request.call.name, TradingToolCallStatus::PermissionDenied,
			"CAPABILITY_REQUIRED", descriptor.requiredCapability);
	const std::string actualSchemaHash =
		TradingToolRegistry::DescriptorSchemaHash(descriptor);
	if (!IsDiscoveryToolName(request.call.name) &&
		request.expectedSchemaHash.empty())
		return Reject(request.call.name, TradingToolCallStatus::Rejected,
			"SCHEMA_HASH_REQUIRED", actualSchemaHash);
	if (!request.expectedSchemaHash.empty() &&
		request.expectedSchemaHash != actualSchemaHash)
		return Reject(request.call.name, TradingToolCallStatus::Rejected,
			"SCHEMA_HASH_MISMATCH", actualSchemaHash);

	TradingToolResult authorized;
	authorized.status = TradingToolCallStatus::Ok;
	authorized.toolName = request.call.name;
	return authorized;
}

TradingToolResult TradingToolHost::AuthorizeControlRequest(
	std::uint32_t peerUid,
	const TradingToolHostRequest& request,
	TradingToolHostSessionBinding& binding)
{
	TradingToolDescriptor descriptor;
	TradingToolResult authorized =
		AuthorizeCommon(peerUid, request, binding, descriptor);
	if (authorized.status != TradingToolCallStatus::Ok)
		return authorized;
	if (request.call.name != "system.cancel_request")
		return Reject(request.call.name, TradingToolCallStatus::InvalidTool,
			"CONTROL_TOOL_REQUIRED",
			"request is not handled by the Tool Server control plane");
	return authorized;
}

TradingToolResult TradingToolHost::PrepareMutationCall(
	const TradingToolHostSessionBinding& binding,
	TradingToolCall& call) const
{
	if (binding.session.environment == "WATCH")
		return Reject(call.name, TradingToolCallStatus::PermissionDenied,
			"WATCH_SESSION_CANNOT_TRADE", "WATCH sessions have no mutation authority");
	if (binding.recoveryOnly && IsRecoveryBlockedEntryTool(call.name))
		return Reject(call.name, TradingToolCallStatus::PermissionDenied,
			"SESSION_RECOVERY_ONLY",
			"root custodian disabled new entry while command recovery is pending");
	if (binding.session.environment != "PAPER")
		return Reject(call.name, TradingToolCallStatus::PermissionDenied,
			"INVALID_SESSION_ENVIRONMENT", "only PAPER sessions may mutate");
	std::string semanticReason;
	std::string semanticDetail;
	if (!TradingToolRegistry::ValidateCallSemantics(call, semanticReason, semanticDetail))
		return Reject(call.name, TradingToolCallStatus::Rejected, semanticReason, semanticDetail);
	if (call.name != "trade.cancel_order" &&
		(call.instrument.empty() || binding.allowedInstruments.find(call.instrument) ==
			binding.allowedInstruments.end()))
		return Reject(call.name, TradingToolCallStatus::PermissionDenied,
			"INSTRUMENT_NOT_ALLOWED", "instrument is outside the server-bound session allowlist");
	if (call.name == "trade.place_order" || call.name == "trade.flatten_position")
	{
		const std::unordered_map<std::string, IBContractLite>::const_iterator bound =
			binding.instrumentContracts.find(call.instrument);
		if (bound == binding.instrumentContracts.end())
			return Reject(call.name, TradingToolCallStatus::PermissionDenied,
				"SERVER_CONTRACT_BINDING_REQUIRED", "instrument has no server-side broker contract");
		const IBContractLite& supplied = call.ibContract;
		const IBContractLite& expected = bound->second;
		if ((!supplied.symbol.empty() && supplied.symbol != expected.symbol) ||
			(!supplied.currency.empty() && supplied.currency != expected.currency) ||
			(!supplied.secType.empty() && supplied.secType != expected.secType) ||
			(!supplied.exchange.empty() && supplied.exchange != expected.exchange) ||
			(!supplied.primaryExchange.empty() && supplied.primaryExchange != expected.primaryExchange) ||
			(!supplied.lastTradeDateOrContractMonth.empty() &&
				supplied.lastTradeDateOrContractMonth != expected.lastTradeDateOrContractMonth) ||
			(!supplied.right.empty() && supplied.right != expected.right) ||
			(supplied.strike != 0.0 && supplied.strike != expected.strike) ||
			(!supplied.multiplier.empty() && supplied.multiplier != expected.multiplier) ||
			(!supplied.tradingClass.empty() && supplied.tradingClass != expected.tradingClass) ||
			(!supplied.localSymbol.empty() && supplied.localSymbol != expected.localSymbol))
			return Reject(call.name, TradingToolCallStatus::PermissionDenied,
				"CONTRACT_IDENTITY_MISMATCH", "client contract differs from the server-bound instrument");
		call.ibContract = expected;
	}
	if (call.name == "trade.place_order" &&
		(!std::isfinite(call.ibOrder.totalQuantity) ||
		 call.ibOrder.totalQuantity > binding.maxOrderQuantity))
		return Reject(call.name, TradingToolCallStatus::Rejected,
			"AGENT_ORDER_QUANTITY_LIMIT", "order exceeds the Agent session limit");
	TradingToolResult result;
	result.status = TradingToolCallStatus::Ok;
	return result;
}

TradingToolResult TradingToolHost::PrepareReadCall(
	const TradingToolHostSessionBinding& binding,
	TradingToolCall& call) const
{
	if (binding.recoveryOnly && IsRecoveryBlockedEntryPreview(call.name))
		return Reject(call.name, TradingToolCallStatus::PermissionDenied,
			"SESSION_RECOVERY_ONLY",
			"root custodian disabled new entry while command recovery is pending");
	if (call.name == "risk.preview_order" || call.name == "risk.preview_flatten")
	{
		if (call.instrument.empty() || binding.allowedInstruments.find(call.instrument) ==
			binding.allowedInstruments.end())
			return Reject(call.name, TradingToolCallStatus::PermissionDenied,
				"INSTRUMENT_NOT_ALLOWED", "instrument is outside the server-bound session allowlist");
		const std::unordered_map<std::string, IBContractLite>::const_iterator bound =
			binding.instrumentContracts.find(call.instrument);
		if (bound == binding.instrumentContracts.end())
			return Reject(call.name, TradingToolCallStatus::PermissionDenied,
				"SERVER_CONTRACT_BINDING_REQUIRED", "instrument has no server-side broker contract");
		call.ibContract = bound->second;
		if (call.name == "risk.preview_order" &&
			(!std::isfinite(call.ibOrder.totalQuantity) ||
			 call.ibOrder.totalQuantity > binding.maxOrderQuantity))
			return Reject(call.name, TradingToolCallStatus::Rejected,
				"AGENT_ORDER_QUANTITY_LIMIT", "order exceeds the Agent session limit");
	}
	std::string semanticReason;
	std::string semanticDetail;
	if (!TradingToolRegistry::ValidateCallSemantics(call, semanticReason, semanticDetail))
		return Reject(call.name, TradingToolCallStatus::Rejected, semanticReason, semanticDetail);
	TradingToolResult result;
	result.status = TradingToolCallStatus::Ok;
	return result;
}

TradingToolSession TradingToolHost::BuildDispatchSession(
	const TradingToolHostSessionBinding& binding,
	const TradingToolHostRequest& request,
	const DecisionLeaseCredential& credential) const
{
	TradingToolSession session = binding.session;
	session.visibleInstruments = binding.allowedInstruments;
	session.boundInstrumentContracts = binding.instrumentContracts;
	// Never trust a client-populated session copy for the quantity ceiling;
	// overwrite it from the supervisor-bound host policy at the authority
	// boundary.  The registry then applies the same limit to target deltas as
	// it does to raw order placement.
	session.maxOrderQuantity = binding.maxOrderQuantity;
	session.executionContext.toolCallId = request.toolCallId;
	session.executionContext.executionDomain = binding.executionDomain;
	session.executionContext.decisionLeaseFencingToken = credential.fencingToken;
	session.executionContext.decisionLeaseGeneration = credential.generation;
	return session;
}

TradingToolResult TradingToolHost::DispatchRead(
	const TradingToolHostSessionBinding& binding,
	const TradingToolHostRequest& request,
	const TradingToolSession& session,
	const TradingToolCall& call)
{
	bool recoveryOnly = false;
	{
		std::lock_guard<std::mutex> dispatchLock(m_mutationDispatchMutex);
		std::lock_guard<std::mutex> lock(m_mutex);
		const std::unordered_map<std::string,
			TradingToolHostSessionBinding>::const_iterator current =
			m_sessions.find(request.sessionToken);
		if (current == m_sessions.end())
			return Reject(call.name, TradingToolCallStatus::PermissionDenied,
				"SESSION_REVOKED", "Agent session was revoked before read dispatch");
		if (current->second.leaseGeneration != binding.leaseGeneration)
			return Reject(call.name, TradingToolCallStatus::PermissionDenied,
				"SESSION_LEASE_GENERATION_CHANGED",
				"Agent session generation changed before read dispatch");
		if (!current->second.enabled ||
			m_pendingOwnerFences.find(SessionOwnerKey(current->second)) !=
				m_pendingOwnerFences.end() ||
			WatchTransactionPendingLocked(current->second))
			return Reject(call.name, TradingToolCallStatus::PermissionDenied,
				"SESSION_OWNER_FENCE_PENDING",
				"Agent session owner is disabled pending remote fence");
		if (current->second.expiresAtMs <= EpochNowMs())
			return Reject(call.name, TradingToolCallStatus::PermissionDenied,
				"SESSION_EXPIRED", "Agent session expired before read dispatch");
		recoveryOnly = current->second.recoveryOnly;
	}
    if (recoveryOnly && IsRecoveryBlockedEntryPreview(call.name))
        return Reject(call.name, TradingToolCallStatus::PermissionDenied,
            "SESSION_RECOVERY_ONLY",
            "root custodian disabled new entry while command recovery is pending");
    try
    {
        return m_registry.Invoke(session, call);
    }
    catch (const std::exception&)
    {
        return ReadDispatchExceptionResult(call.name);
    }
    catch (...)
    {
        return ReadDispatchExceptionResult(call.name);
    }
}

TradingToolResult TradingToolHost::DispatchMutation(
	const TradingToolHostSessionBinding& binding,
	const TradingToolHostRequest& request,
	const TradingToolSession& session,
	const TradingToolCall& call)
{
	std::lock_guard<std::mutex> dispatchLock(m_mutationDispatchMutex);
	{
		std::lock_guard<std::mutex> lock(m_mutex);
		PruneMutationReplaysLocked(std::chrono::steady_clock::now());
		const std::unordered_map<std::string,
			TradingToolHostSessionBinding>::const_iterator current =
			m_sessions.find(request.sessionToken);
		if (current == m_sessions.end())
			return Reject(call.name, TradingToolCallStatus::PermissionDenied,
				"SESSION_REVOKED", "Agent session was revoked before authority dispatch");
		if (current->second.leaseGeneration != binding.leaseGeneration)
			return Reject(call.name, TradingToolCallStatus::PermissionDenied,
				"SESSION_LEASE_GENERATION_CHANGED",
				"Agent session generation changed before authority dispatch");
		if (!current->second.enabled ||
			m_pendingOwnerFences.find(SessionOwnerKey(current->second)) !=
				m_pendingOwnerFences.end() ||
			WatchTransactionPendingLocked(current->second))
			return Reject(call.name, TradingToolCallStatus::PermissionDenied,
				"SESSION_OWNER_FENCE_PENDING",
				"Agent session owner is disabled pending remote fence");
		const std::uint64_t dispatchNowMs = EpochNowMs();
		if (current->second.recoveryOnly &&
			IsRecoveryBlockedEntryTool(call.name))
			return Reject(call.name, TradingToolCallStatus::PermissionDenied,
				"SESSION_RECOVERY_ONLY",
				"root custodian permits only owned cancel or flatten during recovery");
		if (current->second.expiresAtMs <= dispatchNowMs)
			return Reject(call.name, TradingToolCallStatus::PermissionDenied,
				"SESSION_EXPIRED", "Agent session expired before authority dispatch");
		const std::string replayKey = MutationReplayKey(
			current->second, request.toolCallId);
		const std::unordered_map<std::string,
			MutationReplayRecord>::const_iterator replay =
			m_mutationReplays.find(replayKey);
		if (replay != m_mutationReplays.end())
		{
			if (!SameMutationPayload(
					replay->second.session, replay->second.call,
					session, call))
				return Reject(call.name, TradingToolCallStatus::Rejected,
					"IDEMPOTENCY_KEY_CONFLICT",
					"tool_call_id was already used for a different execution operation or payload");
			TradingToolResult duplicate = replay->second.result;
			if (duplicate.status == TradingToolCallStatus::Ok)
			{
				duplicate.status = TradingToolCallStatus::Duplicate;
				duplicate.reasonCode = "DUPLICATE_TOOL_CALL";
				duplicate.detail = "previous_status=accepted";
			}
			return duplicate;
		}
		const bool cancellation = call.name == "trade.cancel_order";
		const bool flatten = call.name == "trade.flatten_position";
		std::unordered_map<std::string, std::uint64_t>& windowStarts =
			cancellation ? m_riskReductionWindowStartMs :
			(flatten ? m_flattenWindowStartMs : m_rateWindowStartMs);
		std::unordered_map<std::string, std::uint32_t>& callsInWindow =
			cancellation ? m_riskReductionCallsInWindow :
			(flatten ? m_flattenCallsInWindow : m_tradeCallsInWindow);
		std::uint64_t& windowStart = windowStarts[request.sessionToken];
		std::uint32_t& count = callsInWindow[request.sessionToken];
		if (dispatchNowMs - windowStart >= 60000)
		{
			windowStart = dispatchNowMs;
			count = 0;
		}
		const std::uint32_t limit = cancellation ?
			kMaximumCancelCallsPerMinute :
			(flatten ? kMaximumFlattenCallsPerMinute :
				binding.maxTradeCallsPerMinute);
		if (count >= limit)
			return Reject(call.name, TradingToolCallStatus::Rejected,
				(cancellation || flatten) ?
					"AGENT_RISK_REDUCTION_RATE_LIMIT" :
					"AGENT_TRADE_RATE_LIMIT",
				(cancellation || flatten) ?
					"Agent session exhausted its emergency risk-reduction budget" :
					"Agent session exhausted its entry trade-call budget");
		++count;
	}
	TradingToolResult result;
	try
	{
		result = m_registry.Invoke(session, call);
	}
	catch (const std::exception&)
	{
		// Dispatch has already passed the host's authorization, rate and lease
		// gates. An escaping registry exception is therefore treated as an
		// uncertain mutation and retained in the replay witness; returning a
		// plain rejection could let an identical retry cross the authority a
		// second time.
		result = MutationDispatchExceptionResult(call.name);
	}
	catch (...)
	{
		result = MutationDispatchExceptionResult(call.name);
	}
	if (result.status == TradingToolCallStatus::Ok ||
		result.status == TradingToolCallStatus::Duplicate ||
		result.status == TradingToolCallStatus::Uncertain)
	{
		std::lock_guard<std::mutex> lock(m_mutex);
		PruneMutationReplaysLocked(std::chrono::steady_clock::now());
		const std::string replayKey = MutationReplayKey(
			binding, request.toolCallId);
		const std::unordered_map<std::string,
			MutationReplayRecord>::iterator existing =
			m_mutationReplays.find(replayKey);
		if (existing == m_mutationReplays.end() &&
			m_mutationReplays.size() >= kMaximumMutationReplayRecords)
		{
			// Keep the local cache bounded.  Entries expire at insertion + TTL,
			// so the earliest expiry is the oldest retained witness.  Eviction
			// only removes a host optimization; the registry/Execution durable
			// ledger remains authoritative for a later retry.
			std::unordered_map<std::string,
				MutationReplayRecord>::iterator oldest =
				m_mutationReplays.begin();
			for (std::unordered_map<std::string,
					 MutationReplayRecord>::iterator it =
					 m_mutationReplays.begin();
				 it != m_mutationReplays.end(); ++it)
			{
				if (oldest == m_mutationReplays.end() ||
					it->second.steadyExpiresAt <
						oldest->second.steadyExpiresAt)
					oldest = it;
			}
			if (oldest != m_mutationReplays.end())
				m_mutationReplays.erase(oldest);
		}
		MutationReplayRecord replay;
		replay.ownerKey = SessionOwnerKey(binding);
		replay.session = session;
		replay.call = call;
		// The permit is a one-time credential and is intentionally excluded
		// from SameMutationPayload. Never retain it in the host replay cache.
		replay.call.previewPermit.clear();
		replay.result = result;
		replay.steadyExpiresAt = std::chrono::steady_clock::now() +
			kMutationReplayTtl;
		m_mutationReplays[replayKey] = replay;
	}
	return result;
}

bool TradingToolHost::TryReplayMutationBeforeLease(
    const TradingToolHostSessionBinding& binding,
    const TradingToolHostRequest& request,
    const TradingToolSession& session,
    const TradingToolCall& call,
    TradingToolResult& result) const
{
    // Serialize this probe with final mutation dispatch and session-control
    // transitions.  The normal authorization path has already checked the
    // peer, capability, schema and semantic field set; these checks only make
    // sure a replay cannot outlive a revoked/fenced/expired binding.
    std::lock_guard<std::mutex> dispatchLock(m_mutationDispatchMutex);
    std::lock_guard<std::mutex> lock(m_mutex);
    PruneMutationReplaysLocked(std::chrono::steady_clock::now());
    const std::unordered_map<std::string,
        TradingToolHostSessionBinding>::const_iterator current =
        m_sessions.find(request.sessionToken);
    if (current == m_sessions.end() ||
        current->second.leaseGeneration != binding.leaseGeneration ||
        !current->second.enabled ||
        m_pendingOwnerFences.find(SessionOwnerKey(current->second)) !=
            m_pendingOwnerFences.end() ||
        WatchTransactionPendingLocked(current->second) ||
        current->second.expiresAtMs <= EpochNowMs())
        return false;

    const std::string replayKey = MutationReplayKey(
        current->second, request.toolCallId);
    const std::unordered_map<std::string, MutationReplayRecord>::const_iterator
        replay = m_mutationReplays.find(replayKey);
    if (replay == m_mutationReplays.end()) return false;
    if (!SameMutationPayload(
            replay->second.session, replay->second.call,
            session, call))
    {
        result = Reject(call.name, TradingToolCallStatus::Rejected,
            "IDEMPOTENCY_KEY_CONFLICT",
            "tool_call_id was already used for a different execution operation or payload");
        return true;
    }
    result = replay->second.result;
    if (result.status == TradingToolCallStatus::Ok)
    {
        result.status = TradingToolCallStatus::Duplicate;
        result.reasonCode = "DUPLICATE_TOOL_CALL";
        result.detail = "previous_status=accepted";
    }
    return true;
}

TradingToolResult TradingToolHost::Invoke(std::uint32_t peerUid, const TradingToolHostRequest& request)
{
	TradingToolHostSessionBinding binding;
	TradingToolDescriptor descriptor;
	TradingToolResult authorized =
		AuthorizeCommon(peerUid, request, binding, descriptor);
	if (authorized.status != TradingToolCallStatus::Ok)
		return authorized;
	// ``system.cancel_request`` is a queue-control operation.  It must be
	// handled by UnixToolServer, which owns the pending-request queues and can
	// atomically remove a request before dispatch.  A direct host invocation
	// has no queue context; routing it through the read registry would return a
	// misleading handler-unavailable result and could make callers believe a
	// cancellation was applied.  Keep the boundary explicit and fail closed.
	if (request.call.name == "system.cancel_request")
		return Reject(request.call.name, TradingToolCallStatus::InvalidTool,
			"CONTROL_TOOL_REQUIRED",
			"request is handled only by the Tool Server control plane");
	TradingToolCall authorizedCall = request.call;
	if (descriptor.effect == TradingToolEffect::Trade)
	{
		const TradingToolResult prepared = PrepareMutationCall(binding, authorizedCall);
		if (prepared.status != TradingToolCallStatus::Ok) return prepared;
	}

	if (descriptor.effect == TradingToolEffect::Read)
	{
		const TradingToolResult prepared = PrepareReadCall(binding, authorizedCall);
		if (prepared.status != TradingToolCallStatus::Ok) return prepared;
	}

    // Resolve a completed mutation before any liveness/readiness probe or
    // fresh decision-lease acquisition. Readiness is a gate for *new* side
    // effects; it must not turn an exact retry of an already accepted command
    // into TRADING_STATE_NOT_READY (or DECISION_LEASE_BUSY). Authorization,
    // visibility, semantic validation and the current session fence were
    // already checked above and again inside TryReplayMutationBeforeLease.
    if (descriptor.effect == TradingToolEffect::Trade)
    {
        TradingToolSession replaySession = binding.session;
        replaySession.visibleInstruments = binding.allowedInstruments;
        replaySession.boundInstrumentContracts = binding.instrumentContracts;
        replaySession.maxOrderQuantity = binding.maxOrderQuantity;
        replaySession.executionContext.toolCallId = request.toolCallId;
        replaySession.executionContext.executionDomain = binding.executionDomain;
        TradingToolResult replay;
        if (TryReplayMutationBeforeLease(
                binding, request, replaySession, authorizedCall, replay))
            return replay;
    }

    if (descriptor.effect == TradingToolEffect::Trade && m_mutationReadiness)
    {
        TradingToolSession readinessSession = binding.session;
        readinessSession.maxOrderQuantity = binding.maxOrderQuantity;
        readinessSession.executionContext.executionDomain = binding.executionDomain;
        readinessSession.executionContext.toolCallId = request.toolCallId;
        std::string readinessReason;
        bool ready = false;
        try
        {
            ready = m_mutationReadiness(
                readinessSession, authorizedCall, readinessReason);
        }
        catch (const std::exception&)
        {
            // A readiness callback is a pre-dispatch gate, so an exception
            // cannot prove that a mutation was sent. Fail closed as a normal
            // not-ready response and do not retain a replay witness.
            return Reject(authorizedCall.name, TradingToolCallStatus::Rejected,
                          "TRADING_STATE_NOT_READY",
                          "mutation readiness check failed");
        }
        catch (...)
        {
            return Reject(authorizedCall.name, TradingToolCallStatus::Rejected,
                          "TRADING_STATE_NOT_READY",
                          "mutation readiness check failed");
        }
        if (!ready)
            return Reject(authorizedCall.name, TradingToolCallStatus::Rejected,
                          "TRADING_STATE_NOT_READY",
                          BoundedHostDetail(
                              readinessReason,
                              "mutation readiness check failed"));
    }

    DecisionLeaseCredential leaseCredential;
    if (descriptor.effect == TradingToolEffect::Trade && authorizedCall.name != "trade.cancel_order")
    {
        const TradingToolResult lease = EnsureDecisionLease(binding, request.sessionToken,
                                                            authorizedCall, leaseCredential);
        if (lease.status != TradingToolCallStatus::Ok) return lease;
    }

	const TradingToolSession session =
		BuildDispatchSession(binding, request, leaseCredential);

	if (descriptor.effect == TradingToolEffect::Trade)
		return DispatchMutation(binding, request, session, authorizedCall);
	return DispatchRead(binding, request, session, authorizedCall);
}

bool TradingToolHost::IsMutationTool(const std::string& toolName) const
{
    TradingToolDescriptor descriptor;
    return m_registry.GetDescriptor(toolName, descriptor) &&
        descriptor.effect == TradingToolEffect::Trade;
}

bool TradingToolHost::ValidateSchemaHash(const std::string& toolName,
                                         const std::string& expectedSchemaHash,
                                         std::string& actualSchemaHash) const
{
    TradingToolDescriptor descriptor;
    actualSchemaHash.clear();
    if (!m_registry.GetDescriptor(toolName, descriptor)) return false;
    actualSchemaHash = TradingToolRegistry::DescriptorSchemaHash(descriptor);
    if (IsDiscoveryToolName(toolName) && expectedSchemaHash.empty())
        return true;
    return !expectedSchemaHash.empty() &&
        expectedSchemaHash == actualSchemaHash;
}

std::size_t TradingToolHost::SessionCount() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_sessions.size();
}

std::vector<TradingToolHostSessionBinding> TradingToolHost::ListSessions() const
{
    std::vector<TradingToolHostSessionBinding> sessions;
    std::lock_guard<std::mutex> lock(m_mutex);
    sessions.reserve(m_sessions.size());
    for (std::unordered_map<std::string, TradingToolHostSessionBinding>::const_iterator it =
             m_sessions.begin(); it != m_sessions.end(); ++it)
        sessions.push_back(it->second);
    return sessions;
}

TradingToolSessionContractCatalogSnapshot TradingToolHost::GetContractCatalogSnapshot() const
{
    return m_contractCatalog.GetSnapshot();
}

void TradingToolHost::SetContractCatalogObserver(
    const TradingToolSessionContractCatalog::Observer& observer)
{
    m_contractCatalog.SetObserver(observer);
}

void TradingToolHost::SetSessionRevokedObserver(
    const TradingToolSessionRevokedObserver& observer)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_sessionRevokedObserver = observer;
}

void TradingToolHost::MoveSessionRateBudgetsLocked(
    const std::string& currentToken, const std::string& replacementToken)
{
    const std::uint64_t tradeStart = m_rateWindowStartMs[currentToken];
    const std::uint32_t tradeCalls = m_tradeCallsInWindow[currentToken];
    const std::uint64_t reductionStart =
        m_riskReductionWindowStartMs[currentToken];
    const std::uint32_t reductionCalls =
        m_riskReductionCallsInWindow[currentToken];
    const std::uint64_t flattenStart = m_flattenWindowStartMs[currentToken];
    const std::uint32_t flattenCalls = m_flattenCallsInWindow[currentToken];
    m_rateWindowStartMs.erase(currentToken);
    m_tradeCallsInWindow.erase(currentToken);
    m_riskReductionWindowStartMs.erase(currentToken);
    m_riskReductionCallsInWindow.erase(currentToken);
    m_flattenWindowStartMs.erase(currentToken);
    m_flattenCallsInWindow.erase(currentToken);
    m_rateWindowStartMs[replacementToken] = tradeStart;
    m_tradeCallsInWindow[replacementToken] = tradeCalls;
    m_riskReductionWindowStartMs[replacementToken] = reductionStart;
    m_riskReductionCallsInWindow[replacementToken] = reductionCalls;
    m_flattenWindowStartMs[replacementToken] = flattenStart;
    m_flattenCallsInWindow[replacementToken] = flattenCalls;
}

void TradingToolHost::EraseSessionLocked(const std::string& token)
{
    std::string ownerKey;
    const std::unordered_map<std::string,
        TradingToolHostSessionBinding>::const_iterator session =
        m_sessions.find(token);
    if (session != m_sessions.end()) ownerKey = SessionOwnerKey(session->second);
    m_sessions.erase(token);
    m_rateWindowStartMs.erase(token);
    m_tradeCallsInWindow.erase(token);
    m_riskReductionWindowStartMs.erase(token);
    m_riskReductionCallsInWindow.erase(token);
    m_flattenWindowStartMs.erase(token);
    m_flattenCallsInWindow.erase(token);
    for (std::unordered_map<std::string, MutationReplayRecord>::iterator replay =
             m_mutationReplays.begin();
         !ownerKey.empty() && replay != m_mutationReplays.end();)
    {
        if (replay->second.ownerKey == ownerKey)
            replay = m_mutationReplays.erase(replay);
        else
            ++replay;
    }
    for (std::unordered_map<std::string, ActiveDecisionLease>::iterator lease =
             m_activeDecisionLeases.begin();
         lease != m_activeDecisionLeases.end();)
    {
        if (lease->second.sessionToken == token)
            lease = m_activeDecisionLeases.erase(lease);
        else
            ++lease;
    }
}

bool TradingToolHost::RestorePaperFinalizationTombstone(
    const TradingToolHostSessionBinding& binding,
    const SessionSupervisorLeaseRecord& durableRecord,
    std::string& reason)
{
    if (durableRecord.templateId != "paper" ||
        !durableRecord.recoveryOnly ||
        !durableRecord.paperFinalizationRequired ||
        durableRecord.paperFinalizationState ==
            SessionSupervisorPaperFinalizationState::None ||
        binding.token != durableRecord.token ||
        binding.leaseGeneration != durableRecord.leaseGeneration ||
        binding.peerUid != durableRecord.peerUid ||
        binding.session.environment != "PAPER" ||
        binding.session.executionContext.agentId != durableRecord.agentId ||
        binding.session.executionContext.sessionId !=
            durableRecord.sessionId ||
        binding.session.executionContext.account !=
            durableRecord.ownerAccount ||
        binding.executionDomain != durableRecord.ownerExecutionDomain ||
        !ValidateSessionTradePolicy(binding, reason))
    {
        if (reason.empty())
            reason = "SESSION_FINALIZATION_TOMBSTONE_BINDING_MISMATCH";
        return false;
    }
    TradingToolHostSessionBinding tombstone = binding;
    tombstone.enabled = false;
    tombstone.recoveryOnly = true;
    tombstone.expiresAtMs = durableRecord.expiresAtMs;
    std::lock_guard<std::mutex> dispatchLock(m_mutationDispatchMutex);
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (m_sessions.find(tombstone.token) != m_sessions.end())
        {
            reason = "SESSION_TOKEN_EXISTS";
            return false;
        }
    }
    TradingToolSessionContractRegistration registration;
    registration.token = tombstone.token;
    registration.agentId = tombstone.session.executionContext.agentId;
    registration.sessionId = tombstone.session.executionContext.sessionId;
    registration.expiresAtMs = tombstone.expiresAtMs;
    for (std::unordered_map<std::string, IBContractLite>::const_iterator
             contract = tombstone.instrumentContracts.begin();
         contract != tombstone.instrumentContracts.end(); ++contract)
        registration.contracts[contract->first] = contract->second;
    if (!m_contractCatalog.Register(registration, reason)) return false;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_sessions[tombstone.token] = tombstone;
        m_rateWindowStartMs[tombstone.token] = 0;
        m_tradeCallsInWindow[tombstone.token] = 0;
        m_riskReductionWindowStartMs[tombstone.token] = 0;
        m_riskReductionCallsInWindow[tombstone.token] = 0;
        m_flattenWindowStartMs[tombstone.token] = 0;
        m_flattenCallsInWindow[tombstone.token] = 0;
        m_pendingOwnerFences.insert(SessionOwnerKey(tombstone));
    }
    TradingToolSession targetOwner = tombstone.session;
    targetOwner.executionContext.executionDomain = tombstone.executionDomain;
    m_registry.RevokeTargetPermitsForOwner(targetOwner);
    DecisionLeaseOwner owner;
    owner.agentId = durableRecord.agentId;
    owner.sessionId = durableRecord.sessionId;
    m_decisionLeases.FenceOwner(owner);
    reason.clear();
    return true;
}

bool TradingToolHost::FinalizeRecoveryOnlyOwner(
    const std::string& token,
    std::uint64_t expectedGeneration,
    const SessionSupervisorLeaseRecord& durableRecord,
    ExecutionControlResult& ownerAudit,
    std::string& reason)
{
    std::lock_guard<std::mutex> dispatchLock(m_mutationDispatchMutex);
    TradingToolHostSessionBinding binding;
    ExecutionControlAuthority* authority = nullptr;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        const std::unordered_map<std::string,
            TradingToolHostSessionBinding>::const_iterator found =
            m_sessions.find(token);
        if (found == m_sessions.end() ||
            found->second.leaseGeneration != expectedGeneration)
        {
            reason = "SESSION_LEASE_GENERATION_MISMATCH";
            return false;
        }
        binding = found->second;
        authority = m_recoveryControlAuthority;
    }
    const AgentExecutionContext& context = binding.session.executionContext;
    if (authority == nullptr || !binding.enabled || !binding.recoveryOnly ||
        binding.session.environment != "PAPER" ||
        durableRecord.templateId != "paper" ||
        !durableRecord.recoveryOnly ||
        durableRecord.fencePending ||
        durableRecord.paperFinalizationRequired ||
        durableRecord.paperFinalizationState !=
            SessionSupervisorPaperFinalizationState::None ||
        durableRecord.token != token ||
        durableRecord.leaseGeneration != expectedGeneration ||
        durableRecord.agentId != context.agentId ||
        durableRecord.sessionId != context.sessionId ||
        durableRecord.ownerAccount != context.account ||
        durableRecord.ownerExecutionDomain != binding.executionDomain)
    {
        reason = authority == nullptr ?
            "SESSION_RECOVERY_QUERY_UNAVAILABLE" :
            (durableRecord.paperFinalizationRequired ?
                "PAPER_FINALIZATION_OPERATION_REQUIRED" :
                "SESSION_RECOVERY_FINALIZE_BINDING_MISMATCH");
        return false;
    }
    ExecutionControlCommand command;
    command.context = context;
    command.context.executionDomain = binding.executionDomain;
    command.context.toolCallId = "recovery-owner-finalize-audit-" +
        std::to_string(expectedGeneration);
    command.recoveryIngressFence = expectedGeneration;
    ownerAudit = authority->RecoveryAuditOwner(command);
    if (!ExactZeroOwnerAudit(binding, ownerAudit, reason)) return false;
    command.context.toolCallId = "recovery-owner-finalize-fence-" +
        std::to_string(expectedGeneration);
    const ExecutionControlResult fenced =
        authority->FenceSessionOwner(command);
    if (fenced.status != ExecutionCommandStatus::Accepted ||
        fenced.affectedCount != 0)
    {
        reason = fenced.reasonCode.empty() ?
            "SESSION_REMOTE_FENCE_PENDING" : fenced.reasonCode;
        return false;
    }
    DecisionLeaseOwner owner;
    owner.agentId = context.agentId;
    owner.sessionId = context.sessionId;
    m_decisionLeases.FenceOwner(owner);
    TradingToolSession targetOwner = binding.session;
    targetOwner.executionContext.executionDomain = binding.executionDomain;
    // Finalization has completed the remote fence. Remove target permits
    // before deleting the local bearer so a stale permit cannot survive a
    // later registration that reuses the same agent/session identity.
    m_registry.RevokeTargetPermitsForOwner(targetOwner);
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        EraseSessionLocked(token);
    }
    if (!m_contractCatalog.Revoke(token))
    {
        reason = "SESSION_CONTRACT_CATALOG_REVOKE_FAILED";
        return false;
    }
    reason.clear();
    return true;
}

bool TradingToolHost::FenceRecoveryOnlyOwner(
    const std::string& token,
    std::uint64_t expectedGeneration,
    const SessionSupervisorLeaseRecord& durableRecord,
    std::string& reason)
{
    std::lock_guard<std::mutex> dispatchLock(m_mutationDispatchMutex);
    TradingToolHostSessionBinding binding;
    bool localExists = false;
    ExecutionControlAuthority* authority = nullptr;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        const std::unordered_map<std::string,
            TradingToolHostSessionBinding>::const_iterator found =
            m_sessions.find(token);
        if (found != m_sessions.end())
        {
            binding = found->second;
            localExists = true;
        }
        authority = m_recoveryControlAuthority;
    }
    if (authority == nullptr || !localExists ||
        durableRecord.templateId != "paper" ||
        !durableRecord.recoveryOnly ||
        !durableRecord.paperFinalizationRequired ||
        durableRecord.fencePending ||
        durableRecord.fenceComplete ||
        durableRecord.paperFinalizationState !=
            SessionSupervisorPaperFinalizationState::FencePending ||
        durableRecord.token != token ||
        durableRecord.leaseGeneration != expectedGeneration ||
        durableRecord.agentId.empty() || durableRecord.sessionId.empty() ||
        durableRecord.ownerAccount.empty() ||
        durableRecord.ownerExecutionDomain.empty())
    {
        reason = authority == nullptr ?
            "SESSION_RECOVERY_QUERY_UNAVAILABLE" :
            (!localExists ?
                "SESSION_FINALIZATION_TOMBSTONE_REQUIRED" :
                "SESSION_RECOVERY_FINALIZE_BINDING_MISMATCH");
        return false;
    }
    if (localExists)
    {
        const AgentExecutionContext& localContext =
            binding.session.executionContext;
        if (!binding.recoveryOnly ||
            binding.session.environment != "PAPER" ||
            binding.leaseGeneration != expectedGeneration ||
            durableRecord.agentId != localContext.agentId ||
            durableRecord.sessionId != localContext.sessionId ||
            durableRecord.ownerAccount != localContext.account ||
            durableRecord.ownerExecutionDomain != binding.executionDomain)
        {
            reason = "SESSION_RECOVERY_FINALIZE_BINDING_MISMATCH";
            return false;
        }
    }
    ExecutionControlCommand command;
    command.context.agentId = durableRecord.agentId;
    command.context.sessionId = durableRecord.sessionId;
    command.context.account = durableRecord.ownerAccount;
    command.context.executionDomain = durableRecord.ownerExecutionDomain;
    command.context.toolCallId = "paper-finalize-fence-" +
        durableRecord.finalizationId + "-" +
        std::to_string(expectedGeneration);
    command.recoveryIngressFence = expectedGeneration;
    const ExecutionControlResult fenced =
        authority->FenceSessionOwner(command);
    if (fenced.status != ExecutionCommandStatus::Accepted ||
        fenced.affectedCount != 0)
    {
        reason = fenced.reasonCode.empty() ?
            "SESSION_REMOTE_FENCE_PENDING" : fenced.reasonCode;
        return false;
    }
    DecisionLeaseOwner owner;
    owner.agentId = durableRecord.agentId;
    owner.sessionId = durableRecord.sessionId;
    m_decisionLeases.FenceOwner(owner);
    // This path may run with a recovery tombstone or a binding restored from
    // durable state. Use the durable primary identity so permits from any
    // prior strategy/account binding are invalidated too.
    m_registry.RevokeTargetPermitsForIdentity(
        durableRecord.agentId, durableRecord.sessionId);
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        const std::unordered_map<std::string,
            TradingToolHostSessionBinding>::iterator found =
                m_sessions.find(token);
        if (found == m_sessions.end() ||
            found->second.leaseGeneration != expectedGeneration)
        {
            reason = "SESSION_FINALIZATION_TOMBSTONE_REQUIRED";
            return false;
        }
        // Preserve the exact bearer/catalog correlation through the final
        // broker audit. It is a tombstone, not authority: dispatch observes
        // enabled=false, and both local and remote decision/execution owners
        // are fenced. Only the later ACK/Purge phase deletes it.
        found->second.enabled = false;
        found->second.recoveryOnly = true;
        m_pendingOwnerFences.insert(SessionOwnerKey(found->second));
    }
    reason.clear();
    return true;
}

bool TradingToolHost::AuditFinalizedRecoveryOwner(
    const SessionSupervisorLeaseRecord& durableRecord,
    ExecutionControlResult& ownerAudit,
    std::string& reason)
{
    ownerAudit = ExecutionControlResult();
    std::lock_guard<std::mutex> dispatchLock(m_mutationDispatchMutex);
    ExecutionControlAuthority* authority = nullptr;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        authority = m_recoveryControlAuthority;
        const std::unordered_map<std::string,
            TradingToolHostSessionBinding>::const_iterator found =
                m_sessions.find(durableRecord.token);
        if (found == m_sessions.end())
        {
            reason = "SESSION_FINALIZATION_TOMBSTONE_REQUIRED";
            return false;
        }
        const AgentExecutionContext& context =
            found->second.session.executionContext;
        if (found->second.enabled || !found->second.recoveryOnly ||
            found->second.session.environment != "PAPER" ||
            found->second.leaseGeneration != durableRecord.leaseGeneration ||
            context.agentId != durableRecord.agentId ||
            context.sessionId != durableRecord.sessionId ||
            context.account != durableRecord.ownerAccount ||
            found->second.executionDomain !=
                durableRecord.ownerExecutionDomain)
        {
            reason = "SESSION_FINALIZATION_TOMBSTONE_BINDING_MISMATCH";
            return false;
        }
    }
    if (authority == nullptr || durableRecord.templateId != "paper" ||
        !durableRecord.recoveryOnly ||
        !durableRecord.paperFinalizationRequired ||
        durableRecord.fencePending ||
        durableRecord.fenceComplete ||
        (durableRecord.paperFinalizationState !=
             SessionSupervisorPaperFinalizationState::FenceComplete &&
         durableRecord.paperFinalizationState !=
             SessionSupervisorPaperFinalizationState::AuditSealed) ||
        durableRecord.leaseGeneration == 0 ||
        durableRecord.agentId.empty() || durableRecord.sessionId.empty() ||
        durableRecord.ownerAccount.empty() ||
        durableRecord.ownerExecutionDomain.empty())
    {
        reason = authority == nullptr ?
            "SESSION_RECOVERY_QUERY_UNAVAILABLE" :
            "SESSION_RECOVERY_FINALIZE_BINDING_MISMATCH";
        return false;
    }
    ExecutionControlCommand command;
    command.context.agentId = durableRecord.agentId;
    command.context.sessionId = durableRecord.sessionId;
    command.context.account = durableRecord.ownerAccount;
    command.context.executionDomain = durableRecord.ownerExecutionDomain;
    command.context.toolCallId = "paper-finalize-audit-" +
        durableRecord.finalizationId + "-" +
        std::to_string(durableRecord.leaseGeneration);
    command.recoveryIngressFence = durableRecord.leaseGeneration;
    ownerAudit = authority->RecoveryAuditOwner(command);
    if (ownerAudit.ownerAccount != durableRecord.ownerAccount ||
        ownerAudit.ownerExecutionDomain !=
            durableRecord.ownerExecutionDomain)
    {
        reason = "SESSION_RECOVERY_FINALIZE_AUDIT_BINDING_MISMATCH";
        return false;
    }
    if (ownerAudit.status != ExecutionCommandStatus::Accepted)
    {
        reason = ownerAudit.reasonCode.empty() ?
            "SESSION_OWNER_AUDIT_INCOMPLETE" : ownerAudit.reasonCode;
        return false;
    }
    reason.clear();
    return true;
}

bool TradingToolHost::UpdatePaperSessionLeaseAfterAudit(
    const std::string& currentToken,
    const std::string& replacementToken,
    std::uint64_t expectedGeneration,
    std::uint64_t expiresAtMs,
    std::uint64_t& newGeneration,
    ExecutionControlResult& ownerAudit,
    std::string& reason)
{
    std::lock_guard<std::mutex> dispatchLock(m_mutationDispatchMutex);
    TradingToolHostSessionBinding binding;
    ExecutionControlAuthority* authority = nullptr;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        const std::unordered_map<std::string,
            TradingToolHostSessionBinding>::const_iterator found =
            m_sessions.find(currentToken);
        if (found == m_sessions.end() ||
            found->second.leaseGeneration != expectedGeneration)
        {
            reason = "SESSION_LEASE_GENERATION_MISMATCH";
            return false;
        }
        binding = found->second;
        authority = m_recoveryControlAuthority;
    }
    if (authority == nullptr || binding.session.environment != "PAPER" ||
        !binding.enabled || binding.recoveryOnly)
    {
        reason = authority == nullptr ?
            "SESSION_RECOVERY_QUERY_UNAVAILABLE" :
            "SESSION_OWNER_RECOVERY_REQUIRED";
        return false;
    }
    ExecutionControlCommand command;
    command.context = binding.session.executionContext;
    command.context.executionDomain = binding.executionDomain;
    command.context.toolCallId = "paper-renew-owner-audit-" +
        std::to_string(expectedGeneration);
    ownerAudit = authority->RecoveryAuditOwner(command);
    if (!ExactZeroOwnerAudit(binding, ownerAudit, reason)) return false;
    return UpdateSessionLeaseImpl(
        currentToken, replacementToken, expectedGeneration, expiresAtMs,
        newGeneration, nullptr, nullptr, reason, true);
}
