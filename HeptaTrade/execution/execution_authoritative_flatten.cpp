#include "execution_coordinator.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <exception>
#include <iomanip>
#include <openssl/evp.h>
#include <sstream>

namespace
{
void AppendField(std::string& output, const char* name,
                 const std::string& value)
{
    output.append(name);
    output.push_back('=');
    output.append(std::to_string(value.size()));
    output.push_back(':');
    output.append(value);
    output.push_back(';');
}

std::string DoubleBits(double value)
{
    std::uint64_t bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    std::ostringstream output;
    output << std::hex << std::setw(16) << std::setfill('0') << bits;
    return output.str();
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
    std::ostringstream output;
    output << "sha256:" << std::hex << std::setfill('0');
    for (unsigned int i = 0; i < length; ++i)
        output << std::setw(2) << static_cast<unsigned int>(digest[i]);
    return output.str();
}

void AppendContract(std::string& value, const InstrumentRef& contract)
{
    AppendField(value, "contract.symbol", contract.symbol);
    AppendField(value, "contract.sec_type", contract.secType);
    AppendField(value, "contract.exchange", contract.exchange);
    AppendField(value, "contract.primary_exchange",
                contract.primaryExchange);
    AppendField(value, "contract.currency", contract.currency);
    AppendField(value, "contract.contract_month",
                contract.lastTradeDateOrContractMonth);
    AppendField(value, "contract.right", contract.right);
    AppendField(value, "contract.strike", DoubleBits(contract.strike));
    AppendField(value, "contract.multiplier", contract.multiplier);
    AppendField(value, "contract.trading_class", contract.tradingClass);
    AppendField(value, "contract.local_symbol", contract.localSymbol);
}

std::string FlattenHash(const FlattenPositionCommand& command)
{
    std::string value;
    AppendField(value, "operation", "flatten_position");
    AppendField(value, "strategy", command.context.strategy);
    AppendField(value, "account", command.context.account);
    AppendField(value, "venue", command.context.venue);
    AppendField(value, "execution_domain",
                command.context.executionDomain);
    AppendField(value, "instrument", command.instrument);
    AppendContract(value, command.contract);
    return Sha256(value);
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
        left.right == right.right && left.strike == right.strike &&
        left.multiplier == right.multiplier &&
        left.tradingClass == right.tradingClass &&
        left.localSymbol == right.localSymbol;
}

bool ExactExternalLimitDayReduceOnly(
    const AuthoritativeFlattenPlan& plan,
    double position,
    double quantity)
{
    return plan.profileOrderMode ==
            "EXTERNAL_P1_CANARY_LMT_DAY" &&
        plan.order.orderType == "LMT" &&
        std::isfinite(plan.quoteBid) && plan.quoteBid > 0.0 &&
        std::isfinite(plan.quoteAsk) &&
        plan.quoteAsk >= plan.quoteBid &&
        plan.order.lmtPrice > 0.0 &&
        plan.referencePrice == plan.order.lmtPrice &&
        ((plan.order.action == "SELL" &&
          plan.order.lmtPrice == plan.quoteBid) ||
         (plan.order.action == "BUY" &&
          plan.order.lmtPrice == plan.quoteAsk)) &&
        quantity == std::fabs(position) && quantity <= 1.0;
}

bool ExactLocalMarketReduceOnly(
    const AuthoritativeFlattenPlan& plan,
    double position,
    double quantity)
{
    return plan.profileOrderMode.empty() &&
        plan.order.orderType == "MKT" &&
        plan.order.lmtPrice == 0.0 &&
        plan.referencePrice > 0.0 &&
        quantity <= std::fabs(position);
}

bool ExactReduceOnly(const FlattenPositionCommand& command,
                     const AuthoritativeFlattenPlan& plan)
{
    if (command.instrument.empty() ||
        command.instrument != plan.instrument ||
        !SameContract(command.contract, plan.contract) ||
        !command.hasAuthoritativePreviewSnapshot ||
        command.authoritativePreviewPlanBinding.empty() ||
        !std::isfinite(command.previewPositionQuantity) ||
        !std::isfinite(plan.expectedPositionQuantity) ||
        command.previewPositionConnectionEpoch == 0 ||
        command.previewPositionGeneration == 0 ||
        plan.positionConnectionEpoch == 0 ||
        plan.positionGeneration == 0 ||
        command.previewPositionConnectionEpoch !=
            plan.positionConnectionEpoch ||
        command.previewPositionGeneration !=
            plan.positionGeneration)
        return false;
    if (command.previewPositionQuantity !=
            plan.expectedPositionQuantity)
        return false;
    if (plan.expectedPositionQuantity == 0.0)
        return plan.order.action.empty() &&
            plan.order.totalQuantity == 0.0;
    if (plan.positionConnectionEpoch == 0 ||
        plan.positionGeneration == 0 ||
        plan.timeInForce != "DAY" ||
        !std::isfinite(plan.order.totalQuantity) ||
        !std::isfinite(plan.order.lmtPrice) ||
        !std::isfinite(plan.referencePrice) ||
        plan.order.totalQuantity <= 0.0)
        return false;
    const double position = plan.expectedPositionQuantity;
    const double quantity = plan.order.totalQuantity;
    const bool validExternalOrder =
        ExactExternalLimitDayReduceOnly(plan, position, quantity);
    const bool validLocalOrder =
        ExactLocalMarketReduceOnly(plan, position, quantity);
    if (!validExternalOrder && !validLocalOrder)
        return false;
    return (position > 0.0 && plan.order.action == "SELL") ||
        (position < 0.0 && plan.order.action == "BUY");
}

std::string SnapshotEvidence(const AuthoritativeFlattenPlan& plan)
{
    std::ostringstream output;
    output << "position_connection_epoch=" << plan.positionConnectionEpoch
           << ";position_generation=" << plan.positionGeneration
           << ";position_quantity=" << std::setprecision(17)
           << plan.expectedPositionQuantity
           << ";quote_subscription_id=" << plan.quoteSubscriptionId
           << ";quote_observed_at_ms=" << plan.quoteObservedAtMs
           << ";quote_stale_after_ms=" << plan.quoteStaleAfterMs
           << ";contract_symbol=" << plan.contract.symbol
           << ";contract_sec_type=" << plan.contract.secType
           << ";contract_exchange=" << plan.contract.exchange
           << ";contract_currency=" << plan.contract.currency;
    if (!plan.profileOrderMode.empty())
        output << ";quote_bid=" << plan.quoteBid
               << ";quote_ask=" << plan.quoteAsk
               << ";profile_order_mode=" << plan.profileOrderMode;
    return output.str();
}

bool IsCanonicalNoopProveRejectCode(const std::string& value)
{
    static const char* const codes[] = {
        "IB_FLATTEN_POSITION_SNAPSHOT_MISMATCH",
        "IB_FLATTEN_POSITION_CHANGED_BEFORE_NOOP",
        "IB_FLATTEN_ACTIVE_ORDER_SNAPSHOT_UNSAFE",
        "IB_PAPER_KILL_SWITCH_READER_REQUIRED",
        "IB_PAPER_KILL_SWITCH_ENGAGED",
        "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN",
        "IB_PAPER_BROKER_CONNECTION_CLOSED",
        "IB_PAPER_EVENT_STREAM_OVERFLOW",
        "IB_PAPER_RUNTIME_FATAL",
        "IB_PAPER_RUNTIME_NOT_READY",
    };
    for (const char* const code : codes)
        if (value == code) return true;
    return false;
}
}

ExecutionCommandResult ExecutionCoordinator::FlattenPosition(
    const FlattenPositionCommand& command)
{
    ExecutionCommandResult result;
    result.commandId = command.context.toolCallId;
    result.reasonCode = "AUTHORITATIVE_FLATTEN_PLAN_REQUIRED";
    return result;
}

ExecutionCommandResult
ExecutionCoordinator::HandleCancelProjectionFailureLocked(
    const CancelOrderCommand& command,
    const std::string& instrument,
    const std::string& side,
    const std::string& requestHash,
    const std::string& requestKey,
    const std::string& projectionReason)
{
    const AgentExecutionContext& context = command.context;
    const std::string failureCode =
        "AUTHORITATIVE_CANCEL_PROJECTION_FAILED";
    const OmsJournalEvent projectionFailure = BuildEvent(
        context, "execution_projection_failed", command.orderId,
        instrument, side, 0.0, 0.0, "cancel_projection_failed",
        projectionReason, failureCode, requestHash);
    if (AppendOrBlockLocked(
            projectionFailure,
            "OMS_EXECUTION_PROJECTION_FAILURE_WRITE_FAILED"))
        BlockMutationsLocked(failureCode);
    RequestRecord record;
    record.status = ExecutionCommandStatus::Uncertain;
    record.orderId = command.orderId;
    record.reasonCode = failureCode;
    record.detail = projectionReason;
    record.requestHash = requestHash;
    record.operation = "cancel";
    record.context = context;
    record.instrument = instrument;
    record.side = side;
    record.durableMutationIntent = true;
    m_requests[requestKey] = record;

    ExecutionCommandResult result;
    result.status = ExecutionCommandStatus::Uncertain;
    result.commandId = context.toolCallId;
    result.orderId = command.orderId;
    result.reasonCode = record.reasonCode;
    result.detail = projectionReason;
    return result;
}

bool ExecutionCoordinator::PrecheckFlattenPosition(
    const FlattenPositionCommand& command,
    ExecutionCommandResult& out) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::unordered_map<std::string, RequestRecord>::const_iterator found =
        m_requests.find(RequestKey(command.context.agentId,
            command.context.sessionId, command.context.toolCallId));
    if (found == m_requests.end()) return false;
    const std::string requestHash = FlattenHash(command);
    if (!found->second.requestHash.empty() &&
        found->second.requestHash != requestHash)
        out = IdempotencyConflictLocked(
            command.context, found->second.orderId);
    else
        out = DuplicateResultLocked(command.context);
    return true;
}

bool ExecutionCoordinator::IsDurableFlattenReplay(
    const FlattenPositionCommand& command) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::unordered_map<std::string, RequestRecord>::const_iterator found =
        m_requests.find(RequestKey(command.context.agentId,
            command.context.sessionId, command.context.toolCallId));
    return found != m_requests.end() &&
        (found->second.status == ExecutionCommandStatus::Accepted ||
         found->second.status == ExecutionCommandStatus::Rejected ||
         found->second.status == ExecutionCommandStatus::Uncertain) &&
        found->second.operation == "flatten" &&
        !found->second.requestHash.empty() &&
        found->second.requestHash == FlattenHash(command);
}

ExecutionCommandResult ExecutionCoordinator::ExecuteAuthoritativeFlatten(
    const FlattenPositionCommand& command,
    const AuthoritativeFlattenPlan& plan)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const AgentExecutionContext& context = command.context;
    if (context.toolCallId.empty() || context.agentId.empty() ||
        context.sessionId.empty())
        return RejectLocked(context, "INVALID_AGENT_CONTEXT",
            "agent_id, session_id and tool_call_id are required");
    const std::string requestHash = FlattenHash(command);
    if (requestHash.empty())
        return RejectLocked(context, "REQUEST_HASH_FAILED",
            "canonical flatten request hashing failed");
    const std::string requestKey = RequestKey(
        context.agentId, context.sessionId, context.toolCallId);
    const std::unordered_map<std::string, RequestRecord>::const_iterator
        existing = m_requests.find(requestKey);
    if (existing != m_requests.end())
    {
        if (!existing->second.requestHash.empty() &&
            existing->second.requestHash != requestHash)
            return IdempotencyConflictLocked(
                context, existing->second.orderId);
        return DuplicateResultLocked(context);
    }
    const std::string instrument = command.instrument;
    const std::string side = plan.order.action;
    const double quantity = plan.order.totalQuantity;
    const double price = plan.order.lmtPrice > 0.0 ?
        plan.order.lmtPrice : plan.referencePrice;
    AuthoritativeFlattenDispatchContext dispatch;
    dispatch.requestKey = requestKey;
    dispatch.requestHash = requestHash;
    dispatch.venueCorrelationId =
        VenueCorrelationId(context, requestHash);
    dispatch.snapshotEvidence = SnapshotEvidence(plan);
    if (m_fencedSessionOwners.find(
            OwnerKey(context.agentId, context.sessionId)) !=
        m_fencedSessionOwners.end())
        return RejectAuthoritativeFlattenLocked(
            command, plan, dispatch, "SESSION_OWNER_FENCED",
            "revoked or expired session owner cannot mutate");
    if (m_mutationBlocked)
        return RejectAuthoritativeFlattenLocked(
            command, plan, dispatch, "MUTATION_BLOCKED",
            m_mutationBlockReason);
    if (!ExactReduceOnly(command, plan))
        return RejectAuthoritativeFlattenLocked(
            command, plan, dispatch,
            "AUTHORITATIVE_FLATTEN_PLAN_INVALID",
            "flatten plan is not a bounded strict reduce-only order");
    if (!context.executionDomain.empty())
    {
        if (context.decisionLeaseFencingToken == 0 ||
            context.decisionLeaseGeneration == 0 ||
            !m_callbacks.validateDecisionLease)
            return RejectAuthoritativeFlattenLocked(
                command, plan, dispatch, "DECISION_LEASE_REQUIRED",
                "flatten mutation lacks a server-validated lease");
        std::string leaseReason;
        if (!m_callbacks.validateDecisionLease(
                context, instrument, &leaseReason))
            return RejectAuthoritativeFlattenLocked(
                command, plan, dispatch, "DECISION_LEASE_INVALID",
                leaseReason);
    }

    OmsJournalEvent intent = BuildEvent(
        context, "flatten_intent", -1, instrument, side, quantity, price,
        "intent_recorded", dispatch.snapshotEvidence, "", requestHash,
        dispatch.venueCorrelationId);
    if (!AppendOrBlockLocked(intent, "OMS_FLATTEN_INTENT_WRITE_FAILED"))
        return RejectLocked(context, "OMS_FLATTEN_INTENT_WRITE_FAILED",
            "flatten broker send was not attempted", -1, requestHash);

    RequestRecord pending;
    pending.status = ExecutionCommandStatus::Uncertain;
    pending.reasonCode = "BROKER_RESULT_PENDING";
    pending.requestHash = requestHash;
    pending.venueCorrelationId = dispatch.venueCorrelationId;
    pending.operation = "flatten";
    pending.context = context;
    pending.instrument = instrument;
    pending.side = side;
    pending.quantity = quantity;
    pending.price = price;
    pending.durableMutationIntent = true;
    m_requests[requestKey] = pending;
    return DispatchAuthoritativeFlattenLocked(
        command, plan, dispatch);
}

ExecutionCommandResult
ExecutionCoordinator::CompleteAuthoritativeFlattenNoopLocked(
    const FlattenPositionCommand& command,
    const AuthoritativeFlattenPlan& plan,
    const AuthoritativeFlattenDispatchContext& dispatch)
{
    if (!m_callbacks.proveAndCommitIbFlatNoop)
        return RejectAuthoritativeFlattenLocked(
            command, plan, dispatch,
            "IB_FLATTEN_PROVE_FLAT_CALLBACK_MISSING",
            "adapter-lock prove-and-commit callback is not configured");
    const AgentExecutionContext& context = command.context;
    const OmsJournalEvent noPosition = BuildEvent(
        context, "flatten_noop", -1, command.instrument, "", 0.0,
        0.0, "accepted", dispatch.snapshotEvidence,
        "POSITION_ALREADY_FLAT", dispatch.requestHash,
        dispatch.venueCorrelationId);
    std::string proveReason;
    bool provedFlat = false;
    bool commitAttempted = false;
    try
    {
        provedFlat = m_callbacks.proveAndCommitIbFlatNoop(
            plan,
            [&]() {
                return AppendOrBlockLocked(
                    noPosition,
                    "OMS_FLATTEN_NOOP_WRITE_FAILED");
            },
            &commitAttempted, &proveReason);
    }
    catch (const std::exception& error)
    {
        proveReason = error.what();
    }
    catch (...)
    {
        proveReason = "adapter-lock prove-flat callback threw";
    }
    if (!provedFlat)
    {
        if (commitAttempted)
        {
            RequestRecord& record =
                m_requests[dispatch.requestKey];
            record.reasonCode =
                "OMS_FLATTEN_NOOP_WRITE_FAILED";
            ExecutionCommandResult uncertain;
            uncertain.status =
                ExecutionCommandStatus::Uncertain;
            uncertain.commandId = context.toolCallId;
            uncertain.reasonCode = record.reasonCode;
            return uncertain;
        }
        if (proveReason.empty())
            proveReason =
                "IB_FLATTEN_POSITION_SNAPSHOT_MISMATCH";
        return RejectAuthoritativeFlattenLocked(
            command, plan, dispatch,
            IsCanonicalNoopProveRejectCode(proveReason) ?
                proveReason : "IB_FLATTEN_PROVE_FLAT_REJECT",
            proveReason);
    }
    if (!commitAttempted)
        return RejectAuthoritativeFlattenLocked(
            command, plan, dispatch,
            "IB_FLATTEN_NOOP_COMMIT_CALLBACK_INVALID",
            "adapter accepted no-op without durable commit");
    RequestRecord& record = m_requests[dispatch.requestKey];
    record.status = ExecutionCommandStatus::Accepted;
    record.reasonCode = "POSITION_ALREADY_FLAT";
    ExecutionCommandResult accepted;
    accepted.status = ExecutionCommandStatus::Accepted;
    accepted.commandId = context.toolCallId;
    accepted.reasonCode = "POSITION_ALREADY_FLAT";
    return accepted;
}
