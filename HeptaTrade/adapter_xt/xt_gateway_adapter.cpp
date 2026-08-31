#include "xt_gateway_adapter.h"

#include <chrono>
#include <cmath>

namespace {

std::int64_t NowMs()
{
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
}

bool IsFinitePositive(double value)
{
    return std::isfinite(value) && value > 0.0;
}

} // namespace

HeptaXTGatewayAdapter::HeptaXTGatewayAdapter() = default;
HeptaXTGatewayAdapter::~HeptaXTGatewayAdapter() = default;

bool HeptaXTGatewayAdapter::Init(const HeptaXTConfig& cfg)
{
    m_cfg = cfg;
    m_inited = false;
    m_connected = false;
    m_status = "XT_TRANSPORT_UNAVAILABLE";
    m_lastRejectReason = "XT_TRANSPORT_UNAVAILABLE";
    PushEvent(MakeEvent(XTEventType::Error, 0, "XT_TRANSPORT_UNAVAILABLE",
                        "vendor transport is not present in this build", 0.0,
                        "xt.init"));
    return false;
}

bool HeptaXTGatewayAdapter::Connect()
{
    return RejectUnavailable("connect");
}

void HeptaXTGatewayAdapter::Disconnect()
{
    m_connected = false;
    m_status = "XT_TRANSPORT_UNAVAILABLE";
}

bool HeptaXTGatewayAdapter::PollOnce(int /*timeoutMs*/)
{
    return false;
}

bool HeptaXTGatewayAdapter::TryDequeueEvent(XTEvent& outEvent)
{
    if (m_events.empty()) return false;
    outEvent = m_events.front();
    m_events.pop();
    return true;
}

bool HeptaXTGatewayAdapter::ReqAccountSummary()
{
    return RejectUnavailable("query_account");
}

bool HeptaXTGatewayAdapter::ReqPositions()
{
    return RejectUnavailable("query_positions");
}

bool HeptaXTGatewayAdapter::ReqMktData(const std::string& /*instrument*/)
{
    return RejectUnavailable("query_market_data");
}

bool HeptaXTGatewayAdapter::PlaceOrder(const std::string& instrument,
                                       const std::string& side,
                                       double qty,
                                       double price,
                                       long long* outOrderId)
{
    if (outOrderId) *outOrderId = 0;
    if (instrument.empty() || (side != "BUY" && side != "SELL") ||
        !IsFinitePositive(qty) || !std::isfinite(price) || price < 0.0)
    {
        m_lastRejectReason = "XT_ORDER_ARGUMENT_INVALID";
        PushEvent(MakeEvent(XTEventType::Error, 0,
                            "XT_ORDER_ARGUMENT_INVALID", "place_order", 0.0,
                            "xt.place"));
        return false;
    }

    std::string riskReason;
    if (!RunPreflightChecks(riskReason))
    {
        m_lastRejectReason = riskReason;
        PushEvent(MakeEvent(XTEventType::Error, 0, "RISK_BLOCK", riskReason,
                            0.0, "xt.place.preflight"));
        return false;
    }
    return RejectUnavailable("place_order");
}

bool HeptaXTGatewayAdapter::CancelOrder(long long orderId)
{
    if (orderId <= 0)
    {
        m_lastRejectReason = "XT_ORDER_ID_INVALID";
        PushEvent(MakeEvent(XTEventType::Error, orderId,
                            "XT_ORDER_ID_INVALID", "cancel_order", 0.0,
                            "xt.cancel"));
        return false;
    }
    return RejectUnavailable("cancel_order", orderId);
}

const char* HeptaXTGatewayAdapter::GetStatusString() const
{
    return m_status.c_str();
}

bool HeptaXTGatewayAdapter::RunPreflightChecks(std::string& reason) const
{
    if (m_cfg.risk.globalKillSwitch)
    {
        reason = "XT_GLOBAL_KILL_SWITCH";
        return false;
    }
    if (m_cfg.risk.flattenOnly)
    {
        reason = "XT_FLATTEN_ONLY_REQUIRES_AUTHORITATIVE_POSITION";
        return false;
    }
    if (m_cfg.readOnly || !m_cfg.risk.enableOrderSubmission)
    {
        reason = "XT_ORDER_GATE_CLOSED";
        return false;
    }
    reason = "XT_TRANSPORT_UNAVAILABLE";
    return false;
}

void HeptaXTGatewayAdapter::OnXtConnected()
{
    m_connected = true;
    m_status = "XT_INBOUND_CALLBACK_CONNECTED_OUTBOUND_DISABLED";
    PushEvent(MakeEvent(XTEventType::Connected, 0, "xt", "connected", 0.0,
                        "xt.cb.on_connected"));
}

void HeptaXTGatewayAdapter::OnXtDisconnected(const std::string& reason)
{
    m_connected = false;
    m_status = "XT_TRANSPORT_UNAVAILABLE";
    PushEvent(MakeEvent(XTEventType::Disconnected, 0, "xt",
                        reason.empty() ? "disconnected" : reason, 0.0,
                        "xt.cb.on_disconnected"));
}

void HeptaXTGatewayAdapter::OnXtAccountStatus(const std::string& status)
{
    PushEvent(MakeEvent(XTEventType::Account, 0, "account_status", status, 0.0,
                        "xt.cb.on_account_status"));
}

void HeptaXTGatewayAdapter::OnXtAsset(double totalAsset, double cash)
{
    PushEvent(MakeEvent(XTEventType::Account, 0, "total_asset", "asset_update",
                        totalAsset, "xt.cb.on_stock_asset"));
    PushEvent(MakeEvent(XTEventType::Account, 0, "cash", "asset_update", cash,
                        "xt.cb.on_stock_asset"));
}

void HeptaXTGatewayAdapter::OnXtPosition(const std::string& instrument,
                                         double volume)
{
    PushEvent(MakeEvent(XTEventType::Position, 0, instrument,
                        "position_update", volume,
                        "xt.cb.on_stock_position"));
}

void HeptaXTGatewayAdapter::OnXtOrderStatus(long long orderId,
                                            const std::string& status,
                                            const std::string& detail)
{
    std::string key = "order_status";
    const auto symbol = m_orderSymbol.find(orderId);
    const auto side = m_orderSide.find(orderId);
    if (symbol != m_orderSymbol.end())
    {
        key = symbol->second;
        if (side != m_orderSide.end()) key += ":" + side->second;
    }
    std::string value = status;
    if (!detail.empty()) value += "|" + detail;
    PushEvent(MakeEvent(XTEventType::OrderStatus, orderId, key, value, 0.0,
                        "xt.cb.on_stock_order"));
}

void HeptaXTGatewayAdapter::OnXtTrade(long long orderId,
                                      const std::string& instrument,
                                      const std::string& side,
                                      double qty,
                                      double price)
{
    m_orderSymbol[orderId] = instrument;
    m_orderSide[orderId] = side;
    PushEvent(MakeEvent(XTEventType::OrderStatus, orderId,
                        instrument + ":" + side, "trade", qty,
                        "xt.cb.on_stock_trade"));
    PushEvent(MakeEvent(XTEventType::OrderStatus, orderId,
                        instrument + ":" + side, "trade_price", price,
                        "xt.cb.on_stock_trade"));
}

void HeptaXTGatewayAdapter::OnXtOrderError(long long orderId,
                                           const std::string& errorCode,
                                           const std::string& detail)
{
    std::string value = errorCode;
    if (!detail.empty()) value += "|" + detail;
    PushEvent(MakeEvent(XTEventType::Error, orderId, "order_error", value, 0.0,
                        "xt.cb.on_order_error"));
}

void HeptaXTGatewayAdapter::OnXtCancelError(long long orderId,
                                            const std::string& errorCode,
                                            const std::string& detail)
{
    std::string value = errorCode;
    if (!detail.empty()) value += "|" + detail;
    PushEvent(MakeEvent(XTEventType::Error, orderId, "cancel_error", value, 0.0,
                        "xt.cb.on_cancel_error"));
}

void HeptaXTGatewayAdapter::OnXtAsyncOrderResponse(long long orderId, bool ok,
                                                   const std::string& detail)
{
    PushEvent(MakeEvent(XTEventType::OrderAck, orderId, "order_async",
                        ok ? "ok" : "fail", 0.0,
                        "xt.cb.on_order_stock_async_response"));
    if (!detail.empty())
        PushEvent(MakeEvent(XTEventType::OrderAck, orderId,
                            "order_async_detail", detail, 0.0,
                            "xt.cb.on_order_stock_async_response"));
}

void HeptaXTGatewayAdapter::OnXtAsyncCancelResponse(long long orderId, bool ok,
                                                    const std::string& detail)
{
    PushEvent(MakeEvent(XTEventType::CancelAck, orderId, "cancel_async",
                        ok ? "ok" : "fail", 0.0,
                        "xt.cb.on_cancel_order_stock_async_response"));
    if (!detail.empty())
        PushEvent(MakeEvent(XTEventType::CancelAck, orderId,
                            "cancel_async_detail", detail, 0.0,
                            "xt.cb.on_cancel_order_stock_async_response"));
}

void HeptaXTGatewayAdapter::PushEvent(const XTEvent& event)
{
    m_events.push(event);
}

XTEvent HeptaXTGatewayAdapter::MakeEvent(XTEventType type, long long id,
                                         const std::string& key,
                                         const std::string& value,
                                         double number,
                                         const std::string& source) const
{
    XTEvent event;
    event.type = type;
    event.id = id;
    event.key = key;
    event.value = value;
    event.number = number;
    event.tsMs = NowMs();
    event.source = source;
    return event;
}

bool HeptaXTGatewayAdapter::RejectUnavailable(const std::string& operation,
                                               long long id)
{
    m_connected = false;
    m_status = "XT_TRANSPORT_UNAVAILABLE";
    m_lastRejectReason = "XT_TRANSPORT_UNAVAILABLE";
    PushEvent(MakeEvent(XTEventType::Error, id, "XT_TRANSPORT_UNAVAILABLE",
                        operation, 0.0, "xt.transport"));
    return false;
}
