#include "xt_gateway_adapter.h"

#include <chrono>
#include <cmath>

namespace {
std::int64_t NowMs()
{
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
}
}

HeptaXTGatewayAdapter::HeptaXTGatewayAdapter() = default;
HeptaXTGatewayAdapter::~HeptaXTGatewayAdapter() = default;

bool HeptaXTGatewayAdapter::Init(const HeptaXTConfig& cfg)
{
    m_cfg = cfg;
    m_connected = false;
    if (m_cfg.mode != "XT")
    {
        m_inited = false;
        m_status = "XT_MODE_INVALID";
        m_lastRejectReason = m_status;
        return false;
    }
    m_inited = true;
    m_status = "XT_EXPERIMENTAL_NO_TRANSPORT";
    m_lastRejectReason = "XT_TRANSPORT_NOT_IMPLEMENTED";
    return true;
}

bool HeptaXTGatewayAdapter::Connect()
{
    if (!m_inited)
    {
        m_status = "XT_CONNECT_FAIL_NOT_INIT";
        m_lastRejectReason = "XT_NOT_INITIALIZED";
        return false;
    }
    return RejectUnsupported("connect");
}

void HeptaXTGatewayAdapter::Disconnect()
{
    m_connected = false;
    m_status = m_inited ? "XT_EXPERIMENTAL_NO_TRANSPORT" : "XT_NOT_INIT";
}

bool HeptaXTGatewayAdapter::PollOnce(int /*timeoutMs*/)
{
    if (!TransportImplemented()) return false;
    return m_connected;
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
    return RejectUnsupported("account_summary");
}

bool HeptaXTGatewayAdapter::ReqPositions()
{
    return RejectUnsupported("positions");
}

bool HeptaXTGatewayAdapter::ReqMktData(const std::string& /*instrument*/)
{
    return RejectUnsupported("market_data");
}

bool HeptaXTGatewayAdapter::PlaceOrder(const std::string& /*instrument*/,
                                       const std::string& /*side*/,
                                       double qty,
                                       double price,
                                       long long* outOrderId)
{
    if (outOrderId) *outOrderId = 0;
    if (!std::isfinite(qty) || qty <= 0.0 ||
        !std::isfinite(price) || price < 0.0)
    {
        m_lastRejectReason = "XT_ORDER_ARGUMENT_INVALID";
        PushEvent(MakeEvent(XTEventType::Error, 0, "XT_ORDER_ARGUMENT_INVALID",
                            "", 0.0, "xt.place"));
        return false;
    }
    std::string reason;
    if (!RunPreflightChecks(reason))
    {
        m_lastRejectReason = reason;
        PushEvent(MakeEvent(XTEventType::Error, 0, "RISK_BLOCK", reason,
                            0.0, "xt.place.preflight"));
        return false;
    }
    return RejectUnsupported("place_order");
}

bool HeptaXTGatewayAdapter::CancelOrder(long long orderId)
{
    if (orderId < 0)
    {
        m_lastRejectReason = "XT_ORDER_ID_INVALID";
        return false;
    }
    return RejectUnsupported("cancel_order", orderId);
}

const char* HeptaXTGatewayAdapter::GetStatusString() const
{
    return m_status.c_str();
}

const char* HeptaXTGatewayAdapter::CapabilityStatus() const
{
    return "EXPERIMENTAL_NO_TRANSPORT";
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
        reason = "XT_FLATTEN_ONLY";
        return false;
    }
    if (!m_cfg.risk.enableOrderSubmission)
    {
        reason = "XT_ORDER_GATE_CLOSED";
        return false;
    }
    reason.clear();
    return true;
}

bool HeptaXTGatewayAdapter::RejectUnsupported(const char* operation, long long id)
{
    m_connected = false;
    m_status = "XT_TRANSPORT_NOT_IMPLEMENTED";
    m_lastRejectReason = "XT_TRANSPORT_NOT_IMPLEMENTED";
    PushEvent(MakeEvent(XTEventType::Error, id,
                        operation ? operation : "operation",
                        m_lastRejectReason, 0.0,
                        "xt.experimental.no_transport"));
    return false;
}

void HeptaXTGatewayAdapter::OnXtConnected()
{
    if (!TransportImplemented())
    {
        RejectUnsupported("callback_connected");
        return;
    }
    m_connected = true;
    m_status = "XT_CONNECTED";
    PushEvent(MakeEvent(XTEventType::Connected, 0, "xt", "connected", 0.0,
                        "xt.cb.on_connected"));
}

void HeptaXTGatewayAdapter::OnXtDisconnected(const std::string& reason)
{
    m_connected = false;
    m_status = "XT_DISCONNECTED";
    PushEvent(MakeEvent(XTEventType::Disconnected, 0, "xt",
                        reason.empty() ? "disconnected" : reason,
                        0.0, "xt.cb.on_disconnected"));
}

void HeptaXTGatewayAdapter::OnXtAccountStatus(const std::string& status)
{
    if (!TransportImplemented()) return;
    PushEvent(MakeEvent(XTEventType::Account, 0, "account_status", status,
                        0.0, "xt.cb.on_account_status"));
}

void HeptaXTGatewayAdapter::OnXtAsset(double totalAsset, double cash)
{
    if (!TransportImplemented()) return;
    PushEvent(MakeEvent(XTEventType::Account, 0, "total_asset", "asset_update",
                        totalAsset, "xt.cb.on_stock_asset"));
    PushEvent(MakeEvent(XTEventType::Account, 0, "cash", "asset_update", cash,
                        "xt.cb.on_stock_asset"));
}

void HeptaXTGatewayAdapter::OnXtPosition(const std::string& instrument,
                                         double volume)
{
    if (!TransportImplemented()) return;
    PushEvent(MakeEvent(XTEventType::Position, 0, instrument, "position_update",
                        volume, "xt.cb.on_stock_position"));
}

void HeptaXTGatewayAdapter::OnXtOrderStatus(
    long long orderId, const std::string& status, const std::string& detail)
{
    if (!TransportImplemented()) return;
    std::string key = "order_status";
    const auto itSym = m_orderSymbol.find(orderId);
    const auto itSide = m_orderSide.find(orderId);
    if (itSym != m_orderSymbol.end())
    {
        key = itSym->second;
        if (itSide != m_orderSide.end()) key += ":" + itSide->second;
    }
    std::string value = status;
    if (!detail.empty()) value += "|" + detail;
    PushEvent(MakeEvent(XTEventType::OrderStatus, orderId, key, value, 0.0,
                        "xt.cb.on_stock_order"));
}

void HeptaXTGatewayAdapter::OnXtTrade(
    long long orderId, const std::string& instrument,
    const std::string& side, double qty, double price)
{
    if (!TransportImplemented()) return;
    m_orderSymbol[orderId] = instrument;
    m_orderSide[orderId] = side;
    PushEvent(MakeEvent(XTEventType::OrderStatus, orderId,
                        instrument + ":" + side, "trade", qty,
                        "xt.cb.on_stock_trade"));
    PushEvent(MakeEvent(XTEventType::OrderStatus, orderId,
                        instrument + ":" + side, "trade_price", price,
                        "xt.cb.on_stock_trade"));
}

void HeptaXTGatewayAdapter::OnXtOrderError(
    long long orderId, const std::string& errorCode,
    const std::string& detail)
{
    if (!TransportImplemented()) return;
    std::string value = errorCode;
    if (!detail.empty()) value += "|" + detail;
    PushEvent(MakeEvent(XTEventType::Error, orderId, "order_error", value,
                        0.0, "xt.cb.on_order_error"));
}

void HeptaXTGatewayAdapter::OnXtCancelError(
    long long orderId, const std::string& errorCode,
    const std::string& detail)
{
    if (!TransportImplemented()) return;
    std::string value = errorCode;
    if (!detail.empty()) value += "|" + detail;
    PushEvent(MakeEvent(XTEventType::Error, orderId, "cancel_error", value,
                        0.0, "xt.cb.on_cancel_error"));
}

void HeptaXTGatewayAdapter::OnXtAsyncOrderResponse(
    long long orderId, bool ok, const std::string& detail)
{
    if (!TransportImplemented()) return;
    PushEvent(MakeEvent(XTEventType::OrderAck, orderId, "order_async",
                        ok ? "ok" : "fail", 0.0,
                        "xt.cb.on_order_stock_async_response"));
    if (!detail.empty())
        PushEvent(MakeEvent(XTEventType::OrderAck, orderId,
                            "order_async_detail", detail, 0.0,
                            "xt.cb.on_order_stock_async_response"));
}

void HeptaXTGatewayAdapter::OnXtAsyncCancelResponse(
    long long orderId, bool ok, const std::string& detail)
{
    if (!TransportImplemented()) return;
    PushEvent(MakeEvent(XTEventType::CancelAck, orderId, "cancel_async",
                        ok ? "ok" : "fail", 0.0,
                        "xt.cb.on_cancel_order_stock_async_response"));
    if (!detail.empty())
        PushEvent(MakeEvent(XTEventType::CancelAck, orderId,
                            "cancel_async_detail", detail, 0.0,
                            "xt.cb.on_cancel_order_stock_async_response"));
}

void HeptaXTGatewayAdapter::PushEvent(const XTEvent& e)
{
    m_events.push(e);
}

XTEvent HeptaXTGatewayAdapter::MakeEvent(
    XTEventType type, long long id, const std::string& key,
    const std::string& value, double number, const std::string& source) const
{
    XTEvent e;
    e.type = type;
    e.id = id;
    e.key = key;
    e.value = value;
    e.number = number;
    e.tsMs = NowMs();
    e.source = source;
    return e;
}
