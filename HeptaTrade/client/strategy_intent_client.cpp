#include "strategy_intent_client.h"
#include <chrono>
#include <exception>
#include "../tools/trading_tool_wire_contract.h"
#include <utility>
StrategyIntentClient::StrategyIntentClient(const NativeToolClientConfig& config) : client_(config) {}
bool StrategyIntentClient::BuildRequest(const hepta::research::BoundedIntent& intent,
    const std::string& id, const std::string& permit, bool submit, std::int64_t now,
    TradingToolHostRequest& request, std::string& reason) {
    request = TradingToolHostRequest(); reason.clear();
    if (!TradingToolWireContract::IsCanonicalCommandId(id)) { reason = "STRATEGY_COMMAND_ID_REQUIRED"; return false; }
    if (submit && (permit.size() != 71 || permit.compare(0, 7, "sha256:") != 0)) {
        reason = "STRATEGY_PREVIEW_PERMIT_REQUIRED"; return false;
    }
    try { hepta::research::ValidateIntent(intent, now); }
    catch (const std::exception& e) { reason = e.what(); return false; }
    request.toolCallId = id;
    request.call.name = submit ? "trade.place_order" : "risk.preview_order";
    request.call.instrument = intent.instrument;
    // Canonical contract/account/session are bound by the Gateway, not selected
    // by legacy strategy state. No last quote is asserted as an approval.
    request.call.ibOrder.action = intent.action;
    request.call.ibOrder.orderType = "LMT";
    request.call.ibOrder.totalQuantity = intent.quantity;
    request.call.ibOrder.lmtPrice = intent.limitPrice;
    request.call.timeInForce = "DAY";
    request.call.expiresAtMs = intent.expiresAtMs;
    request.call.previewPermit = submit ? permit : std::string();
    std::string detail;
    return TradingToolWireContract::ValidateCallSemantics(request.call, reason, detail);
}
bool StrategyIntentClient::Call(TradingToolHostRequest request, const std::string& id,
    StrategyCallResult& result, std::string& reason) const {
    result = StrategyCallResult(); result.commandId = id;
    const auto start = std::chrono::steady_clock::now();
    result.transportComplete = client_.Call(std::move(request), result.native, reason);
    result.elapsedUs = static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now()-start).count());
    // Never manufacture an acknowledgement or generate a new command ID after
    // an uncertain response. Query/reconcile the Execution-issued, caller-persisted ID instead.
    return result.transportComplete;
}
bool StrategyIntentClient::Preview(const hepta::research::BoundedIntent& intent,
    const std::string& id, std::int64_t now, StrategyCallResult& result, std::string& reason) const {
    result = StrategyCallResult(); result.commandId = id;
    TradingToolHostRequest request;
    if (!BuildRequest(intent, id, "", false, now, request, reason)) return false;
    return Call(request, id, result, reason);
}
bool StrategyIntentClient::Submit(const hepta::research::BoundedIntent& intent,
    const std::string& id, const std::string& permit, std::int64_t now,
    StrategyCallResult& result, std::string& reason) const {
    result = StrategyCallResult(); result.commandId = id;
    TradingToolHostRequest request;
    if (!BuildRequest(intent, id, permit, true, now, request, reason)) return false;
    return Call(request, id, result, reason);
}
bool StrategyIntentClient::Query(const std::string& id, const std::string& queryId,
    StrategyCallResult& result, std::string& reason) const {
    result = StrategyCallResult(); result.commandId = id; reason.clear();
    if (!TradingToolWireContract::IsCanonicalCommandId(id) || !TradingToolWireContract::IsCanonicalCommandId(queryId)) { reason = "STRATEGY_COMMAND_ID_REQUIRED"; return false; }
    TradingToolHostRequest request; request.toolCallId = queryId;
    request.call.name = "execution.get_command_status"; request.call.targetCommandId = id;
    return Call(request, id, result, reason);
}
