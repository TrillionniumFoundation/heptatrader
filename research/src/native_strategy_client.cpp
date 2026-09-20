#include "hepta/research/native_strategy_client.h"
#include <tools/trading_tool_wire_contract.h>
#include <cmath>
#include <stdexcept>

namespace hepta { namespace research {
namespace {
void CheckId(const std::string& id) {
    if (!TradingToolWireContract::IsCanonicalCommandId(id))
        throw std::invalid_argument("RESEARCH_TOOL_CALL_ID_INVALID");
}
void Validate(const TradingToolCall& call) {
    std::string reason, detail;
    if (!TradingToolWireContract::ValidateCallSemantics(call, reason, detail))
        throw std::invalid_argument(reason + ": " + detail);
}
}
PreparedOrder::PreparedOrder(const std::string& instrument, const InstrumentRef& contract,
                             const std::string& side, double quantity, double limit,
                             double reference, std::int64_t expiry) {
    // This version explicitly supports only the existing LMT/DAY profile.
    // SHFE close-today/yesterday and legacy auto-open/close are not inferred.
    if (expiry <= 0 || !std::isfinite(quantity) || quantity <= 0 ||
        !std::isfinite(limit) || limit <= 0 || !std::isfinite(reference) || reference <= 0 ||
        (side != "BUY" && side != "SELL"))
        throw std::invalid_argument("RESEARCH_ORDER_PROPOSAL_INVALID");
    // HTT1 carries only these four contract fields. Do not silently discard
    // an option expiry, strike, multiplier or other unsupported identity.
    if (contract.symbol.empty() || contract.currency.empty() || contract.secType.empty() ||
        contract.exchange.empty() || !contract.primaryExchange.empty() ||
        !contract.lastTradeDateOrContractMonth.empty() || !contract.right.empty() ||
        contract.strike != 0 || !contract.multiplier.empty() ||
        !contract.tradingClass.empty() || !contract.localSymbol.empty())
        throw std::invalid_argument("RESEARCH_CONTRACT_NOT_REPRESENTABLE_BY_HTT1");
    call_.name = "risk.preview_order"; call_.instrument = instrument;
    call_.ibContract = contract; call_.ibOrder.action = side;
    call_.ibOrder.orderType = "LMT"; call_.ibOrder.totalQuantity = quantity;
    call_.ibOrder.lmtPrice = limit; call_.timeInForce = "DAY";
    call_.referencePrice = reference; call_.expiresAtMs = expiry;
    Validate(call_);
}
TradingToolHostRequest PreparedOrder::PreviewRequest(const std::string& id) const {
    CheckId(id);
    TradingToolHostRequest request;
    request.toolCallId = id; request.call = call_;
    return request;
}
TradingToolHostRequest PreparedOrder::SubmissionRequest(const std::string& id, const std::string& permit) const {
    CheckId(id);
    if (permit.empty() || permit.size() > 4096)
        throw std::invalid_argument("RESEARCH_PREVIEW_PERMIT_REQUIRED");
    TradingToolHostRequest request;
    request.toolCallId = id; request.call = call_;
    request.call.name = "trade.place_order"; request.call.previewPermit = permit;
    Validate(request.call);
    return request;
}
PreparedCancellation::PreparedCancellation(long serverOrderId) {
    call_.name = "trade.cancel_order"; call_.orderId = serverOrderId;
    Validate(call_);
}
TradingToolHostRequest PreparedCancellation::SubmissionRequest(const std::string& id) const {
    CheckId(id);
    TradingToolHostRequest request;
    request.toolCallId = id; request.call = call_;
    return request;
}
PreparedFlatten::PreparedFlatten(const std::string& instrument) {
    call_.name = "risk.preview_flatten"; call_.instrument = instrument;
    Validate(call_);
}
TradingToolHostRequest PreparedFlatten::PreviewRequest(const std::string& id) const {
    CheckId(id);
    TradingToolHostRequest request;
    request.toolCallId = id; request.call = call_;
    return request;
}
TradingToolHostRequest PreparedFlatten::SubmissionRequest(const std::string& id,
                                                          const std::string& permit) const {
    CheckId(id);
    if (permit.empty() || permit.size() > 4096)
        throw std::invalid_argument("RESEARCH_PREVIEW_PERMIT_REQUIRED");
    TradingToolHostRequest request;
    request.toolCallId = id; request.call = call_;
    request.call.name = "trade.flatten_position"; request.call.previewPermit = permit;
    Validate(request.call);
    return request;
}
bool NativeStrategyClient::Forward(const TradingToolHostRequest& request, NativeToolClientResult& result,
                                   std::string& reason) const {
    // Never allow a previous success envelope to survive a failed transport.
    result = NativeToolClientResult(); reason.clear();
    const bool transported = client_.Call(request, result, reason);
    if (!transported) result = NativeToolClientResult();
    return transported;
}
bool NativeStrategyClient::Preview(const PreparedOrder& order, const std::string& id,
                                   NativeToolClientResult& result, std::string& reason) const {
    result = NativeToolClientResult(); reason.clear();
    try { return Forward(order.PreviewRequest(id), result, reason); }
    catch (const std::invalid_argument& e) { reason = e.what(); return false; }
}
bool NativeStrategyClient::Submit(const PreparedOrder& order, const std::string& id,
                                  const std::string& permit, NativeToolClientResult& result,
                                  std::string& reason) const {
    result = NativeToolClientResult(); reason.clear();
    try { return Forward(order.SubmissionRequest(id, permit), result, reason); }
    catch (const std::invalid_argument& e) { reason = e.what(); return false; }
}
bool NativeStrategyClient::Cancel(const PreparedCancellation& cancellation, const std::string& id,
                                  NativeToolClientResult& result, std::string& reason) const {
    result = NativeToolClientResult(); reason.clear();
    try { return Forward(cancellation.SubmissionRequest(id), result, reason); }
    catch (const std::invalid_argument& e) { reason = e.what(); return false; }
}
bool NativeStrategyClient::PreviewFlatten(const PreparedFlatten& flatten, const std::string& id,
                                          NativeToolClientResult& result, std::string& reason) const {
    result = NativeToolClientResult(); reason.clear();
    try { return Forward(flatten.PreviewRequest(id), result, reason); }
    catch (const std::invalid_argument& e) { reason = e.what(); return false; }
}
bool NativeStrategyClient::Flatten(const PreparedFlatten& flatten, const std::string& id,
                                   const std::string& permit, NativeToolClientResult& result,
                                   std::string& reason) const {
    result = NativeToolClientResult(); reason.clear();
    try { return Forward(flatten.SubmissionRequest(id, permit), result, reason); }
    catch (const std::invalid_argument& e) { reason = e.what(); return false; }
}
bool NativeStrategyClient::Status(const std::string& id, const std::string& queryId,
                                  NativeToolClientResult& result, std::string& reason) const {
    result = NativeToolClientResult(); reason.clear();
    try {
        CheckId(id); CheckId(queryId);
        TradingToolHostRequest request;
        request.toolCallId = queryId; request.call.name = "execution.get_command_status";
        request.call.targetCommandId = id; Validate(request.call);
        return Forward(request, result, reason);
    } catch (const std::invalid_argument& e) { reason = e.what(); return false; }
}
}} // namespace hepta::research
