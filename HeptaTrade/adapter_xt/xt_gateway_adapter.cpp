#include "xt_gateway_adapter.h"

#include <chrono>

namespace {
const char* const kTransportUnavailable = "XT_TRANSPORT_NOT_BUILT";

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
    m_inited = false;
    m_connected = false;
    m_status = kTransportUnavailable;
    m_lastRejectReason = kTransportUnavailable;
    // Configuration storage is not transport initialization.  The repository
    // currently contains no real xtquant/MiniQMT binding, so initialization
    // must not advertise readiness.
    return false;
}

bool HeptaXTGatewayAdapter::Connect()
{
    m_connected = false;
    m_status = kTransportUnavailable;
    m_lastRejectReason = kTransportUnavailable;
    PushEvent(MakeEvent(XTEventType::Error, 0, kTransportUnavailable,
                        "real XT transport is not linked", 0.0,
                        "xt.connect"));
    return false;
}

void HeptaXTGatewayAdapter::Disconnect()
{
    if (m_connected)
        OnXtDisconnected("manual_disconnect");
    else
        m_status = kTransportUnavailable;
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
    m_lastRejectReason = kTransportUnavailable;
    PushEvent(MakeEvent(XTEventType::Error, 0, kTransportUnavailable,
                        "account query unavailable", 0.0,
                        "xt.req.account"));
    return false;
}

bool HeptaXTGatewayAdapter::ReqPositions()
{
    m_lastRejectReason = kTransportUnavailable;
    PushEvent(MakeEvent(XTEventType::Error, 0, kTransportUnavailable,
                        "position query unavailable", 0.0,
                        "xt.req.positions"));
    return false;
}

bool HeptaXTGatewayAdapter::ReqMktData(const std::string& instrument)
{
    m_lastRejectReason = kTransportUnavailable;
    PushEvent(MakeEvent(XTEventType::Error, 0, instrument,
                        kTransportUnavailable, 0.0,
                        "xt.req.mktdata"));
    return false;
}

bool HeptaXTGatewayAdapter::PlaceOrder(const std::string& instrument,
                                       const std::string& side,
                                       double /*qty*/,
                                       double /*price*/,
                                       long long* outOrderId)
{
    if (outOrderId) *outOrderId = 0;
    m_lastRejectReason = kTransportUnavailable;
    PushEvent(MakeEvent(XTEventType::Error, 0,
                        instrument + ":" + side,
                        kTransportUnavailable, 0.0,
                        "xt.place"));
    // Never mint a local order id or synthesize broker ACK/status events.
    return false;
}

bool HeptaXTGatewayAdapter::CancelOrder(long long orderId)
{
    m_lastRejectReason = kTransportUnavailable;
    PushEvent(MakeEvent(XTEventType::Error, orderId,
                        kTransportUnavailable, "cancel unavailable", 0.0,
                        "xt.cancel"));
    return false;
}

const char* HeptaXTGatewayAdapter::GetStatusString() const
{
    return m_status.c_str();
}

bool HeptaXTGatewayAdapter::RunPreflightChecks(std::string& reason) const
{
    reason = kTransportUnavailable;
    return false;
}

// The callback bridge remains as the future real transport seam.  These
// methods do not run from Init/Connect/PlaceOrder in the scaffold build.
void HeptaXTGatewayAdapter::OnXtConnected()
{
    m_connected = true;
    m_inited = true;
    m_status = "XT_CONNECTED";
    m_lastRejectReason.clear();
    PushEvent(MakeEvent(XTEventType::Connected, 0, "xt", "connected", 0.0,
                        "xt.cb.on_connected"));
}

void HeptaXTGatewayAdapter::OnXtDisconnected(const std::string& reason)
{
    m_connected = false;
    m_status = "XT_DISCONNECTED";
    PushEvent(MakeEvent(XTEventType::Disconnected, 0, "xt",
                        reason.empty() ? "disconnected" : reason, 0.0,
                        "xt.cb.on_disconnected"));
}

void HeptaXTGatewayAdapter::OnXtAccountStatus(const std::string& status)
{
    PushEvent(MakeEvent(XTEventType::Account, 0, "account_status", status,
                        0.0, "xt.cb.on_account_status"));
}

void HeptaXTGatewayAdapter::OnXtAsset(double totalAsset, double cash)
{
    PushEvent(MakeEvent(XTEventType::Account, 0, "total_asset", "asset_update",
                        totalAsset, "xt.cb.on_stock_asset"));
    PushEvent(MakeEvent(XTEventType::Account, 0, "cash", "asset_update",
                        cash, "xt.cb.on_stock_asset"));
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
    const std::unordered_map<long long, std::string>::const_iterator symbol =
        m_orderSymbol.find(orderId);
    const std::unordered_map<long long, std::string>::const_iterator side =
        m_orderSide.find(orderId);
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
    PushEvent(MakeEvent(XTEventType::Error, orderId, "order_error", value,
                        0.0, "xt.cb.on_order_error"));
}

void HeptaXTGatewayAdapter::OnXtCancelError(long long orderId,
                                            const std::string& errorCode,
                                            const std::string& detail)
{
    std::string value = errorCode;
    if (!detail.empty()) value += "|" + detail;
    PushEvent(MakeEvent(XTEventType::Error, orderId, "cancel_error", value,
                        0.0, "xt.cb.on_cancel_error"));
}

void HeptaXTGatewayAdapter::OnXtAsyncOrderResponse(long long orderId,
                                                    bool ok,
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

void HeptaXTGatewayAdapter::OnXtAsyncCancelResponse(long long orderId,
                                                     bool ok,
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

XTEvent HeptaXTGatewayAdapter::MakeEvent(XTEventType type,
                                         long long id,
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
