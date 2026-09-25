#include "tool_decision_audit.h"

#include <iomanip>
#include <openssl/evp.h>
#include <sstream>

namespace
{
void AppendValue(std::ostringstream& output, const char* name, const std::string& value)
{
    output << name << '=' << value.size() << ':' << value << '\n';
}

template <typename T>
std::string Number(T value)
{
    std::ostringstream output;
    output << std::setprecision(17) << value;
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
    static const char digits[] = "0123456789abcdef";
    std::string encoded;
    encoded.reserve(length * 2);
    for (unsigned int i = 0; i < length; ++i)
    {
        encoded.push_back(digits[digest[i] >> 4]);
        encoded.push_back(digits[digest[i] & 15]);
    }
    return "sha256:" + encoded;
}
}

ToolDecisionAudit::ToolDecisionAudit()
    : m_journal(nullptr), m_allowMissingForTests(false)
{
}

void ToolDecisionAudit::SetJournal(SessionSupervisorAuditJournal* journal)
{
    m_journal = journal;
}

void ToolDecisionAudit::AllowMissingForTests()
{
    m_allowMissingForTests = true;
}

bool ToolDecisionAudit::Ready() const
{
    return m_journal != nullptr || m_allowMissingForTests;
}

bool ToolDecisionAudit::AppendIntent(
    bool peerCredentialAvailable, std::uint32_t peerUid,
    const TradingToolHostRequest& request,
    const TradingToolHostSessionBinding* binding,
    bool mutation,
    std::string& reason) const
{
    // Only mutation intents need a durable pre-dispatch barrier. Read and
    // control calls are captured once, with their final authorization result.
    if (!mutation)
    {
        reason.clear();
        return true;
    }
    return Append(peerCredentialAvailable, peerUid, &request, binding, mutation,
        "intent", "pending", std::string(), reason);
}

void ToolDecisionAudit::AppendOutcome(
    bool peerCredentialAvailable, std::uint32_t peerUid,
    const TradingToolHostRequest* request,
    const TradingToolHostSessionBinding* binding,
    bool mutation,
    TradingToolResult& result) const
{
    std::string auditReason;
    if (Append(peerCredentialAvailable, peerUid, request, binding, mutation,
        "outcome", TradingToolRegistry::StatusName(result.status),
        result.reasonCode, auditReason))
        return;
    // A mutation crossed a durable intent barrier before dispatch. If only
    // its outcome record fails, never misreport it as a definite rejection:
    // the OMS/idempotency record remains authoritative for reconciliation.
    result.status = mutation ?
        TradingToolCallStatus::Uncertain : TradingToolCallStatus::Rejected;
    result.reasonCode = mutation ?
        "DECISION_AUDIT_OUTCOME_UNCERTAIN" : "DECISION_AUDIT_WRITE_FAILED";
    result.detail = "Tool decision audit is unavailable";
    result.payloadJson.clear();
    if (!mutation) result.orderId = -1;
}

bool ToolDecisionAudit::Append(
    bool peerCredentialAvailable, std::uint32_t peerUid,
    const TradingToolHostRequest* request,
    const TradingToolHostSessionBinding* binding,
    bool mutation,
    const std::string& phase,
    const std::string& outcome,
    const std::string& reasonCode,
    std::string& reason) const
{
    if (m_journal == nullptr)
    {
        reason = "TOOL_DECISION_AUDIT_NOT_CONFIGURED";
        return m_allowMissingForTests;
    }
    ToolDecisionAuditRecord record;
    record.observational = !mutation;
    record.peerCredentialAvailable = peerCredentialAvailable;
    record.peerUid = peerUid;
    record.daemonIdentity = "hepta-unix-tool-gateway/v1";
    if (binding != nullptr)
    {
        // Preserve the observed peer and the presented server-side owner even
        // on a denied call. Do not erase audit identity to encode privilege.
        record.executionDomain = binding->executionDomain;
        record.agentId = binding->session.executionContext.agentId;
        record.sessionId = binding->session.executionContext.sessionId;
        record.account = binding->session.executionContext.account;
        record.venue = binding->session.executionContext.venue;
        record.environment = binding->session.environment;
    }
    if (request != nullptr)
    {
        record.toolCallId = request->toolCallId;
        record.toolName = request->call.name;
        record.expectedSchemaHash = request->expectedSchemaHash;
        record.requestFingerprint = RequestFingerprint(*request);
        if (record.requestFingerprint.empty())
        {
            reason = "TOOL_DECISION_FINGERPRINT_FAILED";
            return false;
        }
    }
    if (mutation && request != nullptr && binding != nullptr &&
        peerCredentialAvailable && binding->peerUid == peerUid && binding->enabled)
    {
        const auto& session = binding->session;
        const bool exitProfile = session.environment == "PAPER" ||
            session.environment == "LIVE_CAPPED" ||
            session.environment == "LIVE_REDUCE_ONLY";
        const char* capability = request->call.name == "trade.cancel_order" ?
            "trade.cancel" : (request->call.name == "trade.flatten_position" ?
            "trade.flatten" : nullptr);
        record.safetyReserveEligible = exitProfile && capability != nullptr &&
            session.capabilities.count(capability) != 0;
        // Use the request's captured binding for both intent and outcome.
        // Recovery-only exits retain capacity, and expiry/revocation while an
        // admitted effect runs cannot erase its outcome audit. Currentness,
        // schema, ownership and final risk remain the Host/Execution checks.
    }
    record.phase = phase;
    record.outcome = outcome;
    record.reasonCode = reasonCode;
    return m_journal->AppendToolDecision(record, reason);
}

std::string ToolDecisionAudit::RequestFingerprint(
    const TradingToolHostRequest& request)
{
    // Intentionally omit sessionToken and previewPermit.  Both are
    // credentials, not request identity suitable for an audit stream.
    std::ostringstream canonical;
    AppendValue(canonical, "tool_call_id", request.toolCallId);
    AppendValue(canonical, "tool_name", request.call.name);
    AppendValue(canonical, "protocol_min", Number(request.protocolMinVersion));
    AppendValue(canonical, "protocol_max", Number(request.protocolMaxVersion));
    AppendValue(canonical, "schema_hash", request.expectedSchemaHash);
    AppendValue(canonical, "queue_deadline", Number(request.queueDeadlineAtMs));
    AppendValue(canonical, "cancel_tool_call_id", request.cancelToolCallId);
    AppendValue(canonical, "target_tool_name", request.call.targetToolName);
    AppendValue(canonical, "instrument", request.call.instrument);
    AppendValue(canonical, "order_id", Number(request.call.orderId));
    AppendValue(canonical, "symbol", request.call.ibContract.symbol);
    AppendValue(canonical, "currency", request.call.ibContract.currency);
    AppendValue(canonical, "security_type", request.call.ibContract.secType);
    AppendValue(canonical, "exchange", request.call.ibContract.exchange);
    AppendValue(canonical, "primary_exchange", request.call.ibContract.primaryExchange);
    AppendValue(canonical, "contract_month", request.call.ibContract.lastTradeDateOrContractMonth);
    AppendValue(canonical, "right", request.call.ibContract.right);
    AppendValue(canonical, "strike", Number(request.call.ibContract.strike));
    AppendValue(canonical, "multiplier", request.call.ibContract.multiplier);
    AppendValue(canonical, "trading_class", request.call.ibContract.tradingClass);
    AppendValue(canonical, "local_symbol", request.call.ibContract.localSymbol);
    AppendValue(canonical, "action", request.call.ibOrder.action);
    AppendValue(canonical, "order_type", request.call.ibOrder.orderType);
    AppendValue(canonical, "position_effect", request.call.ibOrder.positionEffect);
    AppendValue(canonical, "quantity", Number(request.call.ibOrder.totalQuantity));
    AppendValue(canonical, "limit_price", Number(request.call.ibOrder.lmtPrice));
    AppendValue(canonical, "aux_price", Number(request.call.ibOrder.auxPrice));
    AppendValue(canonical, "outside_rth", request.call.ibOrder.outsideRth ? "1" : "0");
    AppendValue(canonical, "order_ref", request.call.ibOrder.orderRef);
    AppendValue(canonical, "reference_price", Number(request.call.referencePrice));
    AppendValue(canonical, "expires_at_ms", Number(request.call.expiresAtMs));
    AppendValue(canonical, "time_in_force", request.call.timeInForce);
    AppendValue(canonical, "wait_timeout_ms", Number(request.call.waitTimeoutMs));
    AppendValue(canonical, "after_event_sequence", Number(request.call.afterEventSequence));
    return Sha256(canonical.str());
}
