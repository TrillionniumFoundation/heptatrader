#include "execution_coordinator.h"
#include <cstring>
#include <exception>
#include <iomanip>
#include <openssl/evp.h>
#include <sstream>
#include <utility>
#include <vector>
namespace {
const char* kAgentSourcePrefix = "agent.tool:";
bool IsBuyOrSell(const std::string& side)
{
    return side == "BUY" || side == "SELL";
}
bool IsKnownIbTerminalStatus(const std::string& status)
{
    return status == "Filled" || status == "Cancelled" ||
        status == "ApiCancelled" || status == "Inactive" ||
        status == "Rejected";
}

bool IsSuccessfulCancelTerminalStatus(const std::string& status)
{
    return status == "Cancelled" || status == "ApiCancelled";
}

// A pre-ACK cancel is durably recorded with its more specific adapter reason.
// It is still an unresolved mutation after restart and may only be resolved
// from the same positive terminal/execution evidence as the generic recovery
// reason.  Keep this allow-list explicit; arbitrary risk codes must not gain
// reconciliation authority merely because they belong to a cancel record.
bool IsRecoverableCancelReason(const std::string& reason)
{
    return reason == "RECOVERY_RECONCILE_REQUIRED" ||
        reason == "IB_CANCEL_DEFERRED_UNTIL_BROKER_ACK";
}

void AppendCanonicalField(std::string& out, const char* name, const std::string& value)
{
    out.append(name);
    out.push_back('=');
    out.append(std::to_string(value.size()));
    out.push_back(':');
    out.append(value);
    out.push_back(';');
}

std::string CanonicalDouble(double value)
{
    static_assert(sizeof(double) == sizeof(std::uint64_t), "unsupported double representation");
    std::uint64_t bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    std::ostringstream out;
    out << std::hex << std::setw(16) << std::setfill('0') << bits;
    return out.str();
}

void AppendCanonicalContext(std::string& out, const AgentExecutionContext& context)
{
    // Agent/session/toolCallId already scope the idempotency key. Decision lease
    // credentials authorize dispatch but are intentionally not execution
    // payload: an exact retry remains exact after a lease refresh.
    AppendCanonicalField(out, "strategy", context.strategy);
    AppendCanonicalField(out, "account", context.account);
    AppendCanonicalField(out, "venue", context.venue);
    AppendCanonicalField(out, "execution_domain", context.executionDomain);
}

std::string Sha256(const std::string& value)
{
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int length = 0;
    EVP_MD_CTX* context = EVP_MD_CTX_new();
    if (context == nullptr) return std::string();
    const bool ok = EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1 &&
        EVP_DigestUpdate(context, value.data(), value.size()) == 1 &&
        EVP_DigestFinal_ex(context, digest, &length) == 1;
    EVP_MD_CTX_free(context);
    if (!ok) return std::string();

    std::ostringstream out;
    out << "sha256:" << std::hex << std::setfill('0');
    for (unsigned int i = 0; i < length; ++i)
        out << std::setw(2) << static_cast<unsigned int>(digest[i]);
    return out.str();
}

std::string PlaceRequestHash(const IbPlaceOrderCommand& command)
{
    std::string canonical;
    AppendCanonicalField(canonical, "operation", "place_ib_order");
    AppendCanonicalContext(canonical, command.context);
    AppendCanonicalField(canonical, "instrument", command.instrument);
    AppendCanonicalField(canonical, "reference_price", CanonicalDouble(command.referencePrice));
    AppendCanonicalField(canonical, "expires_at_ms", std::to_string(command.expiresAtMs));
    AppendCanonicalField(canonical, "time_in_force", command.timeInForce);
    AppendCanonicalField(canonical, "contract.symbol", command.contract.symbol);
    AppendCanonicalField(canonical, "contract.sec_type", command.contract.secType);
    AppendCanonicalField(canonical, "contract.exchange", command.contract.exchange);
    AppendCanonicalField(canonical, "contract.primary_exchange", command.contract.primaryExchange);
    AppendCanonicalField(canonical, "contract.currency", command.contract.currency);
    AppendCanonicalField(canonical, "contract.contract_month",
                         command.contract.lastTradeDateOrContractMonth);
    AppendCanonicalField(canonical, "contract.right", command.contract.right);
    AppendCanonicalField(canonical, "contract.strike", CanonicalDouble(command.contract.strike));
    AppendCanonicalField(canonical, "contract.multiplier", command.contract.multiplier);
    AppendCanonicalField(canonical, "contract.trading_class", command.contract.tradingClass);
    AppendCanonicalField(canonical, "contract.local_symbol", command.contract.localSymbol);
    AppendCanonicalField(canonical, "order.action", command.order.action);
    AppendCanonicalField(canonical, "order.order_type", command.order.orderType);
    AppendCanonicalField(canonical, "order.quantity", CanonicalDouble(command.order.totalQuantity));
    AppendCanonicalField(canonical, "order.limit_price", CanonicalDouble(command.order.lmtPrice));
    AppendCanonicalField(canonical, "order.aux_price", CanonicalDouble(command.order.auxPrice));
    AppendCanonicalField(canonical, "order.outside_rth", command.order.outsideRth ? "1" : "0");
    AppendCanonicalField(canonical, "order.order_ref", command.order.orderRef);
    return Sha256(canonical);
}

} // namespace

std::string CancelRequestHash(const IbCancelOrderCommand& command)
{
    std::string canonical;
    AppendCanonicalField(canonical, "operation", "cancel_ib_order");
    AppendCanonicalContext(canonical, command.context);
    AppendCanonicalField(canonical, "order_id", std::to_string(command.orderId));
    AppendCanonicalField(canonical, "instrument", command.instrument);
    AppendCanonicalField(canonical, "side", command.side);
    return Sha256(canonical);
}

ExecutionCoordinator::ExecutionCoordinator(OmsJournal& journal,
                                           const ExecutionCoordinatorCallbacks& callbacks)
    : m_journal(journal), m_callbacks(callbacks)
{
}

const char* ExecutionCoordinator::StatusName(ExecutionCommandStatus status)
{
    switch (status)
    {
    case ExecutionCommandStatus::Accepted: return "accepted";
    case ExecutionCommandStatus::Rejected: return "rejected";
    case ExecutionCommandStatus::Duplicate: return "duplicate";
    case ExecutionCommandStatus::Uncertain: return "uncertain";
    }
    return "unknown";
}

std::string ExecutionCoordinator::AgentSource(const std::string& agentId)
{
    return std::string(kAgentSourcePrefix) + agentId;
}

std::string ExecutionCoordinator::AgentIdFromSource(const std::string& source)
{
    const std::string prefix(kAgentSourcePrefix);
    if (source.compare(0, prefix.size(), prefix) != 0) return "";
    return source.substr(prefix.size());
}

OmsJournalEvent ExecutionCoordinator::BuildEvent(const AgentExecutionContext& context,
                                                 const std::string& eventType,
                                                 long orderId,
                                                 const std::string& instrument,
                                                 const std::string& side,
                                                 double qty,
                                                 double price,
                                                 const std::string& status,
                                                 const std::string& reason,
                                                 const std::string& riskCode,
                                                 const std::string& requestHash,
                                                 const std::string& venueCorrelationId) const
{
    OmsJournalEvent event;
    event.eventType = eventType;
    event.tsMs = OmsJournal::NowEpochMs();
    event.orderId = orderId;
    event.reqId = context.toolCallId;
    event.clientReqId = context.toolCallId;
    event.traceId = context.sessionId;
    if (!context.toolCallId.empty())
    {
        event.eventId = context.toolCallId + ":" + eventType + ":" + status + ":" +
            std::to_string(orderId);
        if (!riskCode.empty()) event.eventId += ":" + riskCode;
    }
    event.riskCode = riskCode;
    event.venue = context.venue.empty() ? "IB" : context.venue;
    event.strategy = context.strategy;
    event.account = context.account;
    event.executionDomain = context.executionDomain;
    event.requestHash = requestHash;
    event.venueCorrelationId = venueCorrelationId;
    event.instrument = instrument;
    event.side = side;
    event.qty = qty;
    event.price = price;
    event.status = status;
    event.reason = reason;
    event.source = AgentSource(context.agentId);
    return event;
}

std::string ExecutionCoordinator::VenueCorrelationId(
    const AgentExecutionContext& context, const std::string& requestHash)
{
    if (context.agentId.empty() || context.sessionId.empty() ||
        context.toolCallId.empty() || requestHash.empty()) return std::string();
    std::string canonical;
    AppendCanonicalField(canonical, "agent_id", context.agentId);
    AppendCanonicalField(canonical, "session_id", context.sessionId);
    AppendCanonicalField(canonical, "command_id", context.toolCallId);
    AppendCanonicalField(canonical, "request_hash", requestHash);
    return std::string("hepta-v1-") + Sha256(canonical);
}

void ExecutionCoordinator::BlockMutationsLocked(const std::string& reason)
{
    m_mutationBlocked = true;
    m_mutationBlockReason = reason;
}

bool ExecutionCoordinator::AppendOrBlockLocked(const OmsJournalEvent& event,
                                               const std::string& failureCode)
{
    if (m_journal.Append(event)) return true;
    BlockMutationsLocked(failureCode);
    return false;
}

ExecutionCommandResult ExecutionCoordinator::HandleDeferredCancelLocked(
    const CancelOrderCommand& command, const AgentExecutionContext& context,
    const std::string& instrument, const std::string& side,
    const std::string& requestHash, const std::string& requestKey,
    RequestRecord& pending)
{
    const std::string reason = "IB_CANCEL_DEFERRED_UNTIL_BROKER_ACK";
    const OmsJournalEvent deferred = BuildEvent(
        context, "cancel", command.orderId, instrument, side, 0.0, 0.0,
        "cancel_pending", "waiting for broker order acknowledgement",
        reason, requestHash);
    const bool journaled = AppendOrBlockLocked(
        deferred, "OMS_CANCEL_PENDING_RECEIPT_WRITE_FAILED");
    pending.reasonCode = journaled ? reason :
        "OMS_CANCEL_PENDING_RECEIPT_WRITE_FAILED";
    pending.detail = journaled ?
        "cancel intent queued; broker API cancel will dispatch after Submitted/OpenOrder" :
        "cancel intent was queued but its pending receipt could not be persisted; reconcile required";
    m_requests[requestKey] = pending;
    ExecutionCommandResult result;
    result.status = ExecutionCommandStatus::Uncertain;
    result.commandId = context.toolCallId;
    result.orderId = command.orderId;
    result.reasonCode = pending.reasonCode;
    result.detail = pending.detail;
    return result;
}

bool ExecutionCoordinator::TryCancelAtVenueLocked(
    long orderId, std::string& rejectReason)
{
    try
    {
        const bool cancelled = m_callbacks.cancelIbOrder(orderId);
        if (m_callbacks.lastIbRejectReason)
            rejectReason = m_callbacks.lastIbRejectReason();
        return cancelled;
    }
    catch (const std::exception& ex)
    {
        rejectReason = ex.what();
    }
    catch (...)
    {
        rejectReason = "unknown IB cancel exception";
    }
    return false;
}

std::string ExecutionCoordinator::RequestKey(const std::string& agentId,
                                             const std::string& sessionId,
                                             const std::string& toolCallId)
{
    return agentId + "\x1f" + sessionId + "\x1f" + toolCallId;
}

std::string ExecutionCoordinator::OwnerKey(const std::string& agentId,
                                           const std::string& sessionId)
{
    return std::to_string(agentId.size()) + ":" + agentId + sessionId;
}

ExecutionCommandResult ExecutionCoordinator::DuplicateResultLocked(const AgentExecutionContext& context) const
{
    ExecutionCommandResult result;
    result.commandId = context.toolCallId;
    const std::string requestKey = RequestKey(context.agentId, context.sessionId, context.toolCallId);
    const std::unordered_map<std::string, RequestRecord>::const_iterator it = m_requests.find(requestKey);
    if (it != m_requests.end())
    {
        if (it->second.status == ExecutionCommandStatus::Uncertain)
        {
            result.status = ExecutionCommandStatus::Uncertain;
            result.orderId = it->second.orderId;
            result.reasonCode = it->second.reasonCode;
            result.detail = it->second.detail;
            return result;
        }
        result.status = ExecutionCommandStatus::Duplicate;
        result.orderId = it->second.orderId;
        result.reasonCode = "DUPLICATE_TOOL_CALL";
        result.detail = std::string("previous_status=") + StatusName(it->second.status);
    }
    return result;
}

ExecutionCommandResult ExecutionCoordinator::IdempotencyConflictLocked(
    const AgentExecutionContext& context,
    long orderId) const
{
    ExecutionCommandResult result;
    result.status = ExecutionCommandStatus::Rejected;
    result.commandId = context.toolCallId;
    result.orderId = orderId;
    result.reasonCode = "IDEMPOTENCY_KEY_CONFLICT";
    result.detail = "tool_call_id was already used for a different execution operation or payload";
    return result;
}

ExecutionCommandResult ExecutionCoordinator::RejectLocked(const AgentExecutionContext& context,
                                                          const std::string& reasonCode,
                                                          const std::string& detail,
                                                          long orderId,
                                                          const std::string& requestHash)
{
    ExecutionCommandResult result;
    result.status = ExecutionCommandStatus::Rejected;
    result.commandId = context.toolCallId;
    result.orderId = orderId;
    result.reasonCode = reasonCode;
    result.detail = detail;
    if (!context.toolCallId.empty())
    {
        const std::string key = RequestKey(
            context.agentId, context.sessionId, context.toolCallId);
        RequestRecord record;
        const std::unordered_map<std::string, RequestRecord>::const_iterator
            existing = m_requests.find(key);
        if (existing != m_requests.end() &&
            (requestHash.empty() || existing->second.requestHash.empty() ||
             existing->second.requestHash == requestHash))
            record = existing->second;
        record.status = result.status;
        record.orderId = orderId;
        record.reasonCode = reasonCode;
        record.detail = detail;
        if (!requestHash.empty()) record.requestHash = requestHash;
        if (record.context.agentId.empty()) record.context = context;
        m_requests[key] = record;
    }
    return result;
}

ExecutionCommandResult ExecutionCoordinator::PlaceOrder(const PlaceOrderCommand& command)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const AgentExecutionContext& context = command.context;

    if (context.toolCallId.empty() || context.agentId.empty() || context.sessionId.empty())
        return RejectLocked(context, "INVALID_AGENT_CONTEXT", "agent_id, session_id and tool_call_id are required");
    const std::string requestHash = PlaceRequestHash(command);
    if (requestHash.empty())
        return RejectLocked(context, "REQUEST_HASH_FAILED", "canonical request hashing failed");
    const std::string requestKey = RequestKey(context.agentId, context.sessionId, context.toolCallId);
    const std::unordered_map<std::string, RequestRecord>::const_iterator existing =
        m_requests.find(requestKey);
    if (existing != m_requests.end())
    {
        if (!existing->second.requestHash.empty() && existing->second.requestHash != requestHash)
            return IdempotencyConflictLocked(context, existing->second.orderId);
        return DuplicateResultLocked(context);
    }
    if (m_fencedSessionOwners.find(OwnerKey(context.agentId, context.sessionId)) !=
        m_fencedSessionOwners.end())
        return RejectLocked(context, "SESSION_OWNER_FENCED", "revoked or expired session owner cannot mutate",
                            -1, requestHash);
    if (m_recoveryOnlySessionOwners.find(
            OwnerKey(context.agentId, context.sessionId)) !=
        m_recoveryOnlySessionOwners.end())
        return RejectLocked(context, "SESSION_RECOVERY_ONLY",
            "root custodian disabled new entry for this session owner",
            -1, requestHash);
    if (m_mutationBlocked)
        return RejectLocked(context, "MUTATION_BLOCKED", m_mutationBlockReason, -1, requestHash);
    if (!m_callbacks.placeIbOrder && !m_callbacks.placeIbOrderCorrelated && !m_callbacks.placeIbOrderCommandCorrelated)
        return RejectLocked(context, "IB_PLACE_CALLBACK_MISSING", "IB place callback is not configured",
                            -1, requestHash);
    if (command.expiresAtMs > 0 && OmsJournal::NowEpochMs() > command.expiresAtMs)
        return RejectLocked(context, "TOOL_CALL_EXPIRED", "order command expired before execution",
                            -1, requestHash);
    if (command.contract.symbol.empty() || command.order.totalQuantity <= 0.0 || !IsBuyOrSell(command.order.action))
        return RejectLocked(context, "INVALID_ORDER", "symbol, BUY/SELL action and positive quantity are required",
                            -1, requestHash);

    const std::string instrument = command.instrument.empty() ? command.contract.symbol : command.instrument;
    if (!context.executionDomain.empty())
    {
        if (context.decisionLeaseFencingToken == 0 || context.decisionLeaseGeneration == 0 ||
            !m_callbacks.validateDecisionLease)
            return RejectLocked(context, "DECISION_LEASE_REQUIRED", "Agent mutation lacks a server-validated lease",
                                -1, requestHash);
        std::string leaseReason;
        if (!m_callbacks.validateDecisionLease(context, instrument, &leaseReason))
            return RejectLocked(context, "DECISION_LEASE_INVALID", leaseReason, -1, requestHash);
    }
    const double eventPrice = command.order.lmtPrice > 0.0 ? command.order.lmtPrice : command.referencePrice;
    const std::string venueCorrelationId = VenueCorrelationId(context, requestHash);
    const OmsJournalEvent intent = BuildEvent(context, "order_intent", -1, instrument,
                                              command.order.action, command.order.totalQuantity,
                                              eventPrice, "intent_recorded", "", "", requestHash,
                                              venueCorrelationId);
    if (!AppendOrBlockLocked(intent, "OMS_INTENT_WRITE_FAILED"))
        return RejectLocked(context, "OMS_INTENT_WRITE_FAILED", "broker send was not attempted",
                            -1, requestHash);

    RequestRecord pending;
    pending.status = ExecutionCommandStatus::Uncertain;
    pending.reasonCode = "BROKER_RESULT_PENDING";
    pending.requestHash = requestHash;
    pending.venueCorrelationId = venueCorrelationId;
    pending.operation = "place";
    pending.context = context;
    pending.instrument = instrument;
    pending.side = command.order.action;
    pending.quantity = command.order.totalQuantity;
    pending.price = eventPrice;
    pending.durableMutationIntent = true;
    m_requests[requestKey] = pending;
    PlaceOrderDispatchContext dispatch;
    dispatch.requestKey = requestKey;
    dispatch.requestHash = requestHash;
    dispatch.venueCorrelationId = venueCorrelationId;
    dispatch.instrument = instrument;
    dispatch.eventPrice = eventPrice;
    return DispatchPlaceOrderLocked(command, dispatch);
}

bool ExecutionCoordinator::PrecheckPlaceIbOrder(
    const IbPlaceOrderCommand& command, ExecutionCommandResult& out) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::string requestKey = RequestKey(
        command.context.agentId, command.context.sessionId,
        command.context.toolCallId);
    const std::unordered_map<std::string, RequestRecord>::const_iterator existing =
        m_requests.find(requestKey);
    if (existing == m_requests.end()) return false;
    const std::string requestHash = PlaceRequestHash(command);
    if (!existing->second.requestHash.empty() &&
        existing->second.requestHash != requestHash)
        out = IdempotencyConflictLocked(command.context, existing->second.orderId);
    else
        out = DuplicateResultLocked(command.context);
    return true;
}

bool ExecutionCoordinator::IsDurablePlaceReplay(
    const IbPlaceOrderCommand& command) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::unordered_map<std::string, RequestRecord>::const_iterator existing =
        m_requests.find(RequestKey(command.context.agentId,
            command.context.sessionId, command.context.toolCallId));
    return existing != m_requests.end() &&
        (existing->second.status == ExecutionCommandStatus::Accepted ||
         existing->second.status == ExecutionCommandStatus::Uncertain) &&
        !existing->second.requestHash.empty() &&
        existing->second.requestHash == PlaceRequestHash(command);
}

void ExecutionCoordinator::GetPlaceSendAttemptTimes(
    const std::string& account, const std::string& executionDomain,
    std::int64_t cutoffMs, std::vector<std::int64_t>& out) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    out.clear();
    for (std::vector<PlaceSendAttempt>::const_iterator it =
             m_placeSendAttempts.begin(); it != m_placeSendAttempts.end(); ++it)
    {
        if (it->account == account && it->executionDomain == executionDomain &&
            it->tsMs > cutoffMs)
            out.push_back(it->tsMs);
    }
}

void ExecutionCoordinator::ResetRecoveryProjectionLocked()
{
    m_requests.clear();
    m_orderOwners.clear();
    m_fencedSessionOwners.clear();
    m_recoveryOnlySessionOwners.clear();
    m_placeSendAttempts.clear();
    m_placeSendAttemptKeys.clear();
    m_mutationBlocked = false;
    m_mutationBlockReason.clear();
    m_paperTerminalFencePresent = false;
    m_paperTerminalFenceBinding = PaperTerminalFenceBinding();
}

bool ExecutionCoordinator::ApplyRecoveredPlaceReceiptLocked(
    const OmsJournalEvent& event,
    RequestRecord& record,
    const std::string& agentId)
{
    if (event.eventType != "place_sent" &&
        event.eventType != "flatten_sent")
        return false;
    record.operation = event.eventType == "flatten_sent" ?
        "flatten" : "place";
    record.status = ExecutionCommandStatus::Accepted;
    record.reasonCode.clear();
    ExecutionOrderOwner owner;
    owner.agentId = agentId;
    owner.sessionId = event.traceId;
    owner.strategy = event.strategy;
    owner.account = event.account;
    owner.executionDomain = event.executionDomain;
    owner.instrument = event.instrument;
    owner.side = event.side;
    if (event.orderId >= 0) m_orderOwners[event.orderId] = owner;
    return true;
}

bool ExecutionCoordinator::ApplyRecoveredOutcomeUncertainLocked(
    const OmsJournalEvent& event,
    RequestRecord& record)
{
    if (event.eventType != "place_outcome_uncertain" &&
        event.eventType != "flatten_outcome_uncertain")
        return false;
    const bool flatten =
        event.eventType == "flatten_outcome_uncertain";
    record.operation = flatten ? "flatten" : "place";
    record.status = ExecutionCommandStatus::Uncertain;
    record.reasonCode = event.riskCode.empty() ?
        (flatten ? "IB_FLATTEN_OUTCOME_UNCERTAIN" :
            "IB_PLACE_OUTCOME_UNCERTAIN") : event.riskCode;
    record.detail = event.reason;
    return true;
}

namespace
{
bool IsProjectionFailureReason(const std::string& reason)
{
    return reason == "AUTHORITATIVE_ORDER_PROJECTION_FAILED" ||
        reason == "AUTHORITATIVE_CANCEL_PROJECTION_FAILED" ||
        reason == "AUTHORITATIVE_FLATTEN_PROJECTION_FAILED";
}
}

void ExecutionCoordinator::ApplyRecoveredProjectionResolvedLocked()
{
    for (std::unordered_map<std::string, RequestRecord>::iterator record =
             m_requests.begin(); record != m_requests.end(); ++record)
    {
        if (!IsProjectionFailureReason(record->second.reasonCode)) continue;
        record->second.status = ExecutionCommandStatus::Accepted;
        record->second.reasonCode.clear();
        record->second.detail.clear();
    }
    if (!IsProjectionFailureReason(m_mutationBlockReason)) return;
    m_mutationBlocked = false;
    m_mutationBlockReason.clear();
}

void ExecutionCoordinator::TrackRecoveredSendAttemptLocked(
    const OmsJournalEvent& event, const std::string& requestKey)
{
    const bool sendAttempt = event.eventType == "place_send_attempt" ||
        event.eventType == "place_sent" ||
        event.eventType == "flatten_send_attempt" ||
        event.eventType == "flatten_sent";
    if (!sendAttempt || !m_placeSendAttemptKeys.insert(requestKey).second) return;
    PlaceSendAttempt attempt;
    attempt.requestKey = requestKey;
    attempt.account = event.account;
    attempt.executionDomain = event.executionDomain;
    attempt.tsMs = event.tsMs;
    m_placeSendAttempts.push_back(attempt);
}

bool ExecutionCoordinator::HydrateRecoveredRecordLocked(
    const OmsJournalEvent& event, const std::string& agentId,
    const std::string& commandId, RequestRecord& record)
{
    if (!record.requestHash.empty() && !event.requestHash.empty() &&
        record.requestHash != event.requestHash)
    {
        BlockMutationsLocked("OMS_REQUEST_HASH_CONFLICT");
        return false;
    }
    if (record.requestHash.empty()) record.requestHash = event.requestHash;
    if (record.venueCorrelationId.empty())
        record.venueCorrelationId = event.venueCorrelationId;
    record.context.agentId = agentId;
    record.context.sessionId = event.traceId;
    record.context.toolCallId = commandId;
    record.context.strategy = event.strategy;
    record.context.account = event.account;
    record.context.venue = event.venue;
    record.context.executionDomain = event.executionDomain;
    record.instrument = event.instrument;
    record.side = event.side;
    record.quantity = event.qty;
    record.price = event.price;
    record.orderId = event.orderId;
    return true;
}

bool ExecutionCoordinator::ApplyRecoveredCommandStateLocked(
    const OmsJournalEvent& event, RequestRecord& record,
    const std::string& agentId)
{
    const bool durableIntent = event.eventType == "order_intent" ||
        event.eventType == "flatten_intent" ||
        (event.eventType == "cancel" &&
         event.status == "intent_recorded");
    if (durableIntent) record.durableMutationIntent = true;
    if (ApplyRecoveredPlaceReceiptLocked(event, record, agentId) ||
        ApplyRecoveredOutcomeUncertainLocked(event, record)) return true;
    const bool pendingPlace = event.eventType == "order_intent" ||
        event.eventType == "flatten_intent" ||
        event.eventType == "flatten_send_attempt";
    if (pendingPlace)
    {
        record.operation = event.eventType == "order_intent" ? "place" : "flatten";
        record.status = ExecutionCommandStatus::Uncertain;
        record.reasonCode = "RECOVERY_RECONCILE_REQUIRED";
        return true;
    }
    if (event.eventType == "reject" || event.eventType == "flatten_reject")
    {
        if (record.operation.empty())
            record.operation = event.eventType == "flatten_reject" ?
                "flatten" : "place";
        record.status = ExecutionCommandStatus::Rejected;
        record.reasonCode = event.riskCode;
        record.detail = event.reason;
        return true;
    }
    const bool recoveredCancel =
        (event.eventType == "cancel" &&
         (event.status == "cancel_sent" || event.status == "cancel_pending" ||
          event.status == "intent_recorded")) ||
        event.eventType == "cancel_send_attempt";
    if (recoveredCancel)
    {
        record.operation = "cancel";
        const bool sent = event.eventType == "cancel" && event.status == "cancel_sent";
        record.status = sent ? ExecutionCommandStatus::Accepted : ExecutionCommandStatus::Uncertain;
        record.reasonCode = sent ? std::string() :
            (event.status == "cancel_pending" && !event.riskCode.empty() ?
                event.riskCode : "RECOVERY_RECONCILE_REQUIRED");
        record.detail = event.reason;
        return true;
    }
    if (event.eventType == "flatten_noop")
    {
        record.operation = "flatten";
        record.status = ExecutionCommandStatus::Accepted;
        record.reasonCode = event.riskCode;
        return true;
    }
    if (event.eventType == "execution_projection_failed")
    {
        record.status = ExecutionCommandStatus::Uncertain;
        record.reasonCode = event.riskCode.empty() ?
            "AUTHORITATIVE_EXECUTION_PROJECTION_FAILED" : event.riskCode;
        record.detail = event.reason;
        BlockMutationsLocked(record.reasonCode);
        return true;
    }
    if (event.eventType == "execution_command_resolved")
    {
        if (record.operation.empty()) record.operation = "place";
        record.status = event.status == "accepted" ?
            ExecutionCommandStatus::Accepted : ExecutionCommandStatus::Rejected;
        record.reasonCode = event.riskCode;
        record.detail = event.reason;
        if (record.status == ExecutionCommandStatus::Accepted && event.orderId >= 0)
        {
            ExecutionOrderOwner owner;
            owner.agentId = agentId;
            owner.sessionId = event.traceId;
            owner.strategy = event.strategy;
            owner.account = event.account;
            owner.executionDomain = event.executionDomain;
            owner.instrument = event.instrument;
            owner.side = event.side;
            m_orderOwners[event.orderId] = owner;
        }
        return true;
    }
    if (event.eventType != "cancel_command_resolved") return false;
    record.operation = "cancel";
    record.status = event.status == "accepted" ?
        ExecutionCommandStatus::Accepted : ExecutionCommandStatus::Rejected;
    record.reasonCode = event.riskCode;
    record.detail = event.reason;
    if (event.orderId >= 0) m_orderOwners.erase(event.orderId);
    return true;
}

void ExecutionCoordinator::ApplyRecoveredEventLocked(
    const OmsJournalEvent& event)
{
    if (event.eventType == "execution_projection_resolved")
    {
        ApplyRecoveredProjectionResolvedLocked();
        return;
    }

    const std::string agentId = AgentIdFromSource(event.source);
    const std::string commandId = !event.reqId.empty() ? event.reqId :
        (!event.clientReqId.empty() ? event.clientReqId : event.eventId);
    if (commandId.empty() || agentId.empty()) return;
    if (ApplyRecoveredOwnershipEventLocked(event, agentId)) return;

    const std::string requestKey =
        RequestKey(agentId, event.traceId, commandId);
    TrackRecoveredSendAttemptLocked(event, requestKey);
    RequestRecord& record = m_requests[requestKey];
    if (!HydrateRecoveredRecordLocked(event, agentId, commandId, record)) return;
    ApplyRecoveredCommandStateLocked(event, record, agentId);
}

bool ExecutionCoordinator::ValidateRecoveredProjectionLocked(
    std::string& reason)
{
    for (std::unordered_map<std::string, RequestRecord>::const_iterator it = m_requests.begin();
         it != m_requests.end(); ++it)
    {
        if (it->second.status == ExecutionCommandStatus::Uncertain)
        {
            if (!m_mutationBlocked) BlockMutationsLocked("RECOVERY_RECONCILE_REQUIRED");
            reason = m_mutationBlockReason;
            return false;
        }
    }

    if (m_mutationBlocked)
    {
        reason = m_mutationBlockReason;
        return false;
    }

    reason.clear();
    return true;
}

bool ExecutionCoordinator::RecoverFromJournal(std::string& reason)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    ResetRecoveryProjectionLocked();

    std::vector<OmsJournalEvent> events;
    const int replayed = m_journal.Replay(
        [&events](const OmsJournalEvent& event) {
            events.push_back(event);
        });
    if (replayed < 0)
    {
        reason = "OMS_REPLAY_FAILED";
        BlockMutationsLocked(reason);
        return false;
    }
    for (std::vector<OmsJournalEvent>::const_iterator it = events.begin();
         it != events.end(); ++it)
        ApplyRecoveredEventLocked(*it);
    return ValidateRecoveredProjectionLocked(reason);
}

bool ExecutionCoordinator::ResolveUncertainPlaceCommands(
    const std::map<std::string, long>& authoritativeCorrelations,
    bool authoritativeSnapshotComplete,
    std::size_t& resolvedCommands,
    std::string& reason,
    bool resolveMissingAsRejected)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    resolvedCommands = 0;
    if (!authoritativeSnapshotComplete)
    {
        reason = "AUTHORITATIVE_CORRELATION_SNAPSHOT_INCOMPLETE";
        return false;
    }
    for (std::unordered_map<std::string, RequestRecord>::iterator it = m_requests.begin();
         it != m_requests.end(); ++it)
    {
        RequestRecord& record = it->second;
        if (record.status != ExecutionCommandStatus::Uncertain ||
            (record.operation != "place" &&
             record.operation != "flatten") ||
            (record.reasonCode != "RECOVERY_RECONCILE_REQUIRED" &&
             !(record.operation == "place" &&
               record.reasonCode ==
                   "IB_PLACE_OUTCOME_UNCERTAIN") &&
             !(record.operation == "flatten" &&
               record.reasonCode ==
                   "IB_FLATTEN_OUTCOME_UNCERTAIN")) ||
            record.venueCorrelationId.empty())
            continue;
        const std::map<std::string, long>::const_iterator venue =
            authoritativeCorrelations.find(record.venueCorrelationId);
        if (venue == authoritativeCorrelations.end() && !resolveMissingAsRejected)
            continue;
        const bool accepted = venue != authoritativeCorrelations.end() && venue->second >= 0;
        const long orderId = accepted ? venue->second : -1;
        const std::string resolutionCode = accepted ?
            "AUTHORITATIVE_CORRELATION_CONFIRMED" :
            "AUTHORITATIVE_CORRELATION_NOT_FOUND";
        const OmsJournalEvent resolution = BuildEvent(
            record.context, "execution_command_resolved", orderId,
            record.instrument, record.side, record.quantity, record.price,
            accepted ? "accepted" : "rejected", resolutionCode, resolutionCode,
            record.requestHash, record.venueCorrelationId);
        if (!AppendOrBlockLocked(resolution, "OMS_EXECUTION_RESOLUTION_WRITE_FAILED"))
        {
            reason = "OMS_EXECUTION_RESOLUTION_WRITE_FAILED";
            return false;
        }
        record.status = accepted ? ExecutionCommandStatus::Accepted :
            ExecutionCommandStatus::Rejected;
        record.orderId = orderId;
        record.reasonCode = resolutionCode;
        record.detail = resolutionCode;
        if (accepted)
        {
            ExecutionOrderOwner owner;
            owner.agentId = record.context.agentId;
            owner.sessionId = record.context.sessionId;
            owner.strategy = record.context.strategy;
            owner.account = record.context.account;
            owner.executionDomain = record.context.executionDomain;
            owner.instrument = record.instrument;
            owner.side = record.side;
            m_orderOwners[orderId] = owner;
        }
        ++resolvedCommands;
    }
    bool uncertainRemains = false;
    for (std::unordered_map<std::string, RequestRecord>::const_iterator it = m_requests.begin();
         it != m_requests.end(); ++it)
        if (it->second.status == ExecutionCommandStatus::Uncertain) uncertainRemains = true;
    if (!uncertainRemains && m_mutationBlockReason == "RECOVERY_RECONCILE_REQUIRED")
    {
        m_mutationBlocked = false;
        m_mutationBlockReason.clear();
    }
    reason.clear();
    return true;
}

bool ExecutionCoordinator::ResolveUncertainCancelCommands(
    const std::set<long>& authoritativeActiveOrderIds,
    bool authoritativeActiveSnapshotComplete,
    const std::map<long, std::string>& authoritativeTerminalStatuses,
    const std::set<long>& authoritativeExecutionOrderIds,
    bool authoritativeTerminalSnapshotComplete,
    std::size_t& resolvedCommands,
    std::string& reason)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    resolvedCommands = 0;
    if (!authoritativeActiveSnapshotComplete)
    {
        reason = "AUTHORITATIVE_ACTIVE_ORDER_SNAPSHOT_INCOMPLETE";
        return false;
    }
    if (!authoritativeTerminalSnapshotComplete)
    {
        reason = "AUTHORITATIVE_TERMINAL_ORDER_SNAPSHOT_INCOMPLETE";
        return false;
    }

    for (std::map<long, std::string>::const_iterator terminal =
             authoritativeTerminalStatuses.begin();
         terminal != authoritativeTerminalStatuses.end(); ++terminal)
    {
        if (terminal->first < 0 || !IsKnownIbTerminalStatus(terminal->second))
        {
            reason = "AUTHORITATIVE_CANCEL_TERMINAL_STATUS_INVALID";
            return false;
        }
        if (authoritativeActiveOrderIds.find(terminal->first) !=
            authoritativeActiveOrderIds.end())
        {
            reason = "AUTHORITATIVE_CANCEL_ACTIVE_TERMINAL_CONFLICT";
            return false;
        }
    }
    for (std::set<long>::const_iterator execution =
             authoritativeExecutionOrderIds.begin();
         execution != authoritativeExecutionOrderIds.end(); ++execution)
    {
        // Partial fills may have executions while the active snapshot remains
        // authoritative; only malformed execution IDs invalidate it.
        if (*execution < 0)
        {
            reason = "AUTHORITATIVE_CANCEL_ACTIVE_EXECUTION_CONFLICT";
            return false;
        }
    }

    for (std::unordered_map<std::string, RequestRecord>::iterator it =
             m_requests.begin(); it != m_requests.end(); ++it)
    {
        RequestRecord& record = it->second;
        if (record.status != ExecutionCommandStatus::Uncertain ||
            record.operation != "cancel" ||
            !IsRecoverableCancelReason(record.reasonCode) ||
            record.orderId < 0)
            continue;
        if (authoritativeActiveOrderIds.find(record.orderId) !=
            authoritativeActiveOrderIds.end())
            continue;

        // IB order ID zero is not unique across all completed/manual
        // evidence.  It cannot safely identify the target of a cancel, even
        // if a terminal status or economic execution also reports zero.
        if (record.orderId == 0)
            continue;

        const std::map<long, std::string>::const_iterator terminal =
            authoritativeTerminalStatuses.find(record.orderId);
        const bool executed = authoritativeExecutionOrderIds.find(record.orderId) !=
            authoritativeExecutionOrderIds.end();
        if (terminal == authoritativeTerminalStatuses.end() && !executed)
            continue;

        const bool accepted = terminal != authoritativeTerminalStatuses.end() &&
            IsSuccessfulCancelTerminalStatus(terminal->second) && !executed;
        const std::string resolutionCode = accepted ?
            "AUTHORITATIVE_CANCEL_TERMINAL_CONFIRMED" :
            (executed || (terminal != authoritativeTerminalStatuses.end() &&
                          terminal->second == "Filled") ?
                "AUTHORITATIVE_CANCEL_TARGET_FILLED" :
                "AUTHORITATIVE_CANCEL_TARGET_TERMINAL");
        const OmsJournalEvent resolution = BuildEvent(
            record.context, "cancel_command_resolved", record.orderId,
            record.instrument, record.side, 0.0, 0.0,
            accepted ? "accepted" : "rejected", resolutionCode,
            resolutionCode, record.requestHash);
        if (!AppendOrBlockLocked(resolution, "OMS_CANCEL_RESOLUTION_WRITE_FAILED"))
        {
            reason = "OMS_CANCEL_RESOLUTION_WRITE_FAILED";
            return false;
        }
        record.status = accepted ? ExecutionCommandStatus::Accepted :
            ExecutionCommandStatus::Rejected;
        record.reasonCode = resolutionCode;
        record.detail = resolutionCode;
        m_orderOwners.erase(record.orderId);
        ++resolvedCommands;
    }

    bool uncertainRemains = false;
    for (std::unordered_map<std::string, RequestRecord>::const_iterator it =
             m_requests.begin(); it != m_requests.end(); ++it)
        if (it->second.status == ExecutionCommandStatus::Uncertain)
            uncertainRemains = true;
    if (!uncertainRemains && m_mutationBlockReason == "RECOVERY_RECONCILE_REQUIRED")
    {
        m_mutationBlocked = false;
        m_mutationBlockReason.clear();
    }
    reason.clear();
    return true;
}

bool ExecutionCoordinator::IsMutationBlocked(std::string* reason) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (reason) *reason = m_mutationBlockReason;
    return m_mutationBlocked;
}

void ExecutionCoordinator::ResetMutationBlockAfterReconcile()
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_mutationBlockReason == "IB_PAPER_TERMINAL_HALTED") return;
    m_mutationBlocked = false;
    m_mutationBlockReason.clear();
}

bool ExecutionCoordinator::ResolveProjectionBlockAfterAuthoritativeResync()
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (!m_mutationBlocked) return true;
    if (m_mutationBlockReason != "AUTHORITATIVE_ORDER_PROJECTION_FAILED" &&
        m_mutationBlockReason != "AUTHORITATIVE_CANCEL_PROJECTION_FAILED" &&
        m_mutationBlockReason != "AUTHORITATIVE_FLATTEN_PROJECTION_FAILED")
        return false;

    OmsJournalEvent resolved;
    resolved.schemaVersion = OmsJournal::kSchemaVersion;
    resolved.eventType = "execution_projection_resolved";
    resolved.tsMs = OmsJournal::NowEpochMs();
    resolved.eventId = std::string("execution-projection-resolved:") +
        std::to_string(resolved.tsMs);
    resolved.status = "authoritative_resync_complete";
    resolved.reason = m_mutationBlockReason;
    resolved.source = "execution.coordinator";
    if (!AppendOrBlockLocked(resolved, "OMS_EXECUTION_PROJECTION_RESOLUTION_WRITE_FAILED"))
        return false;

    for (std::unordered_map<std::string, RequestRecord>::iterator record =
             m_requests.begin(); record != m_requests.end(); ++record)
    {
        if (record->second.reasonCode == "AUTHORITATIVE_ORDER_PROJECTION_FAILED" ||
            record->second.reasonCode == "AUTHORITATIVE_CANCEL_PROJECTION_FAILED" ||
            record->second.reasonCode == "AUTHORITATIVE_FLATTEN_PROJECTION_FAILED")
        {
            record->second.status = ExecutionCommandStatus::Accepted;
            record->second.reasonCode.clear();
            record->second.detail.clear();
        }
    }
    m_mutationBlocked = false;
    m_mutationBlockReason.clear();
    return true;
}

void ExecutionCoordinator::RecordOrderTerminal(long orderId)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_orderOwners.erase(orderId);
}

bool ExecutionCoordinator::GetOrderOwner(long orderId, ExecutionOrderOwner& out) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::unordered_map<long, ExecutionOrderOwner>::const_iterator it = m_orderOwners.find(orderId);
    if (it == m_orderOwners.end()) return false;
    out = it->second;
    return true;
}

bool ExecutionCoordinator::GetCommandStatus(
    const std::string& agentId,
    const std::string& sessionId,
    const std::string& commandId,
    ExecutionCommandResult& out) const
{
    if (agentId.empty() || sessionId.empty() || commandId.empty()) return false;
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::unordered_map<std::string, RequestRecord>::const_iterator found =
        m_requests.find(RequestKey(agentId, sessionId, commandId));
    if (found == m_requests.end()) return false;
    out = ExecutionCommandResult();
    out.status = found->second.status;
    out.commandId = commandId;
    out.orderId = found->second.orderId;
    out.reasonCode = found->second.reasonCode;
    out.detail = found->second.detail;
    return true;
}

std::size_t ExecutionCoordinator::FenceSessionOwner(
    const std::string& agentId,
    const std::string& sessionId)
{
    if (agentId.empty() || sessionId.empty()) return 0;
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::string ownerKey = OwnerKey(agentId, sessionId);
    if (m_fencedSessionOwners.insert(ownerKey).second)
    {
        AgentExecutionContext context;
        context.agentId = agentId;
        context.sessionId = sessionId;
        context.toolCallId = std::string("session-owner-fenced-") +
            std::to_string(OmsJournal::NowEpochMs());
        AppendOrBlockLocked(BuildEvent(context, "session_owner_fenced", -1,
            "", "", 0.0, 0.0, "fenced", "session revoked or expired",
            "SESSION_OWNER_FENCED"), "OMS_SESSION_FENCE_JOURNAL_FAILED");
    }
    std::size_t activeOrders = 0;
    for (std::unordered_map<long, ExecutionOrderOwner>::const_iterator it = m_orderOwners.begin();
         it != m_orderOwners.end(); ++it)
        if (it->second.agentId == agentId && it->second.sessionId == sessionId) ++activeOrders;
    return activeOrders;
}

bool ExecutionCoordinator::IsSessionOwnerFenced(
    const std::string& agentId,
    const std::string& sessionId) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_fencedSessionOwners.find(OwnerKey(agentId, sessionId)) !=
        m_fencedSessionOwners.end();
}

bool ExecutionCoordinator::AuditAndReleaseSessionOwnerFence(
    const std::string& agentId,
    const std::string& sessionId,
    bool authoritativeOpenOrdersComplete,
    std::string& reason)
{
    if (agentId.empty() || sessionId.empty())
    {
        reason = "SESSION_OWNER_IDENTITY_REQUIRED";
        return false;
    }
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::string ownerKey = OwnerKey(agentId, sessionId);
    if (m_fencedSessionOwners.find(ownerKey) == m_fencedSessionOwners.end())
    {
        reason = "SESSION_OWNER_NOT_FENCED";
        return false;
    }
    if (!authoritativeOpenOrdersComplete)
    {
        reason = "AUTHORITATIVE_OPEN_ORDERS_INCOMPLETE";
        return false;
    }
    for (std::unordered_map<long, ExecutionOrderOwner>::const_iterator it = m_orderOwners.begin();
         it != m_orderOwners.end(); ++it)
    {
        if (it->second.agentId == agentId && it->second.sessionId == sessionId)
        {
            reason = "FENCED_OWNER_ACTIVE_ORDERS_REMAIN";
            return false;
        }
    }
    AgentExecutionContext context;
    context.agentId = agentId;
    context.sessionId = sessionId;
    context.toolCallId = std::string("session-owner-fence-release-") +
        std::to_string(OmsJournal::NowEpochMs());
    if (!AppendOrBlockLocked(BuildEvent(context, "session_owner_fence_release", -1,
        "", "", 0.0, 0.0, "released",
        "authoritative open orders complete and owner has no active orders",
        "SESSION_OWNER_FENCE_RELEASED"), "OMS_SESSION_FENCE_RELEASE_JOURNAL_FAILED"))
    {
        reason = "OMS_SESSION_FENCE_RELEASE_JOURNAL_FAILED";
        return false;
    }
    m_fencedSessionOwners.erase(ownerKey);
    reason.clear();
    return true;
}

bool ExecutionCoordinator::ReconcileOrderOwners(
    const std::set<long>& authoritativeActiveOrderIds,
    bool authoritativeOpenOrdersComplete,
    std::size_t& removedOwners,
    std::string& reason)
{
    removedOwners = 0;
    if (!authoritativeOpenOrdersComplete)
    {
        reason = "AUTHORITATIVE_OPEN_ORDERS_INCOMPLETE";
        return false;
    }
    std::lock_guard<std::mutex> lock(m_mutex);
    std::vector<long> terminalOrderIds;
    for (std::unordered_map<long, ExecutionOrderOwner>::const_iterator it = m_orderOwners.begin();
         it != m_orderOwners.end(); ++it)
        if (authoritativeActiveOrderIds.find(it->first) == authoritativeActiveOrderIds.end())
            terminalOrderIds.push_back(it->first);

    for (std::size_t i = 0; i < terminalOrderIds.size(); ++i)
    {
        const long orderId = terminalOrderIds[i];
        const std::unordered_map<long, ExecutionOrderOwner>::const_iterator owner =
            m_orderOwners.find(orderId);
        if (owner == m_orderOwners.end()) continue;
        AgentExecutionContext context;
        context.agentId = owner->second.agentId;
        context.sessionId = owner->second.sessionId;
        context.strategy = owner->second.strategy;
        context.account = owner->second.account;
        context.executionDomain = owner->second.executionDomain;
        context.toolCallId = std::string("order-owner-reconciled-") +
            std::to_string(orderId) + "-" + std::to_string(OmsJournal::NowEpochMs());
        if (!AppendOrBlockLocked(BuildEvent(context, "order_owner_reconciled_terminal",
            orderId, owner->second.instrument, owner->second.side, 0.0, 0.0,
            "terminal", "absent from complete authoritative active-order snapshot",
            "ORDER_OWNER_RECONCILED_TERMINAL"), "OMS_OWNER_RECONCILE_JOURNAL_FAILED"))
        {
            reason = "OMS_OWNER_RECONCILE_JOURNAL_FAILED";
            return false;
        }
        m_orderOwners.erase(orderId);
        ++removedOwners;
    }
    reason.clear();
    return true;
}
