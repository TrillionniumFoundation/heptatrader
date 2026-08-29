#include "xt_gateway_adapter.h"

#include <chrono>

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
    m_inited = true;
    m_status = "XT_INIT_OK";
    return true;
}

bool HeptaXTGatewayAdapter::Connect()
{
    if (!m_inited)
    {
        m_status = "XT_CONNECT_FAIL_NOT_INIT";
        return false;
    }

    // Stage-2 scaffold: lifecycle + callback bridge wired, transport pending.
    OnXtConnected();
    m_status = "XT_CONNECTED_SCAFFOLD";
    return true;
}

void HeptaXTGatewayAdapter::Disconnect()
{
    if (m_connected)
    {
        OnXtDisconnected("manual_disconnect");
    }
}

bool HeptaXTGatewayAdapter::PollOnce(int /*timeoutMs*/)
{
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
    if (!m_connected) return false;
    PushEvent(MakeEvent(XTEventType::Account, 0, "account", m_cfg.account, 0.0, "xt.req.account"));
    return true;
}

bool HeptaXTGatewayAdapter::ReqPositions()
{
    if (!m_connected) return false;
    PushEvent(MakeEvent(XTEventType::Position, 0, "position", "query_sent", 0.0, "xt.req.positions"));
    return true;
}

bool HeptaXTGatewayAdapter::ReqMktData(const std::string& instrument)
{
    if (!m_connected) return false;
    PushEvent(MakeEvent(XTEventType::Tick, 0, instrument, "query_sent", 0.0, "xt.req.mktdata"));
    return true;
}

bool HeptaXTGatewayAdapter::PlaceOrder(const std::string& instrument,
                                       const std::string& side,
                                       double qty,
                                       double price,
                                       long long* outOrderId)
{
    std::string reason;
    if (!RunPreflightChecks(reason))
    {
        m_lastRejectReason = reason;
        PushEvent(MakeEvent(XTEventType::Error, 0, "RISK_BLOCK", reason, 0.0, "xt.place.preflight"));
        return false;
    }

    if (!m_connected)
    {
        m_lastRejectReason = "XT_NOT_CONNECTED";
        PushEvent(MakeEvent(XTEventType::Error, 0, "XT_NOT_CONNECTED", "", 0.0, "xt.place"));
        return false;
    }

    const long long oid = ++m_localOrderSeed;
    if (outOrderId) *outOrderId = oid;

    m_orderSymbol[oid] = instrument;
    m_orderSide[oid] = side;

    // Stage-2: emit deterministic ack/status events; real transport callback will replace source.
    OnXtAsyncOrderResponse(oid, true, "accepted_scaffold");
    OnXtOrderStatus(oid, "submitted", "place_order_scaffold");

    XTEvent px = MakeEvent(XTEventType::OrderStatus, oid, instrument + ":" + side,
                           "reference_price", price > 0.0 ? price : qty, "xt.place.ref");
    PushEvent(px);
    return true;
}

bool HeptaXTGatewayAdapter::CancelOrder(long long orderId)
{
    if (!m_connected)
    {
        PushEvent(MakeEvent(XTEventType::Error, orderId, "XT_NOT_CONNECTED", "cancel", 0.0, "xt.cancel"));
        return false;
    }

    OnXtAsyncCancelResponse(orderId, true, "cancel_sent_scaffold");
    OnXtOrderStatus(orderId, "cancelled", "cancel_order_scaffold");
    return true;
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

void HeptaXTGatewayAdapter::OnXtConnected()
{
    m_connected = true;
    m_status = "XT_CONNECTED";
    PushEvent(MakeEvent(XTEventType::Connected, 0, "xt", "connected", 0.0, "xt.cb.on_connected"));
}

void HeptaXTGatewayAdapter::OnXtDisconnected(const std::string& reason)
{
    m_connected = false;
    m_status = "XT_DISCONNECTED";
    PushEvent(MakeEvent(XTEventType::Disconnected, 0, "xt", reason.empty() ? "disconnected" : reason,
                        0.0, "xt.cb.on_disconnected"));
}

void HeptaXTGatewayAdapter::OnXtAccountStatus(const std::string& status)
{
    PushEvent(MakeEvent(XTEventType::Account, 0, "account_status", status, 0.0, "xt.cb.on_account_status"));
}

void HeptaXTGatewayAdapter::OnXtAsset(double totalAsset, double cash)
{
    PushEvent(MakeEvent(XTEventType::Account, 0, "total_asset", "asset_update", totalAsset, "xt.cb.on_stock_asset"));
    PushEvent(MakeEvent(XTEventType::Account, 0, "cash", "asset_update", cash, "xt.cb.on_stock_asset"));
}

void HeptaXTGatewayAdapter::OnXtPosition(const std::string& instrument, double volume)
{
    PushEvent(MakeEvent(XTEventType::Position, 0, instrument, "position_update", volume, "xt.cb.on_stock_position"));
}

void HeptaXTGatewayAdapter::OnXtOrderStatus(long long orderId, const std::string& status, const std::string& detail)
{
    std::string key = "order_status";
    auto itSym = m_orderSymbol.find(orderId);
    auto itSide = m_orderSide.find(orderId);
    if (itSym != m_orderSymbol.end())
    {
        key = itSym->second;
        if (itSide != m_orderSide.end()) key += ":" + itSide->second;
    }

    std::string value = status;
    if (!detail.empty()) value += "|" + detail;
    PushEvent(MakeEvent(XTEventType::OrderStatus, orderId, key, value, 0.0, "xt.cb.on_stock_order"));
}

void HeptaXTGatewayAdapter::OnXtTrade(long long orderId, const std::string& instrument,
                                      const std::string& side, double qty, double price)
{
    m_orderSymbol[orderId] = instrument;
    m_orderSide[orderId] = side;
    PushEvent(MakeEvent(XTEventType::OrderStatus, orderId, instrument + ":" + side, "trade", qty, "xt.cb.on_stock_trade"));
    PushEvent(MakeEvent(XTEventType::OrderStatus, orderId, instrument + ":" + side, "trade_price", price, "xt.cb.on_stock_trade"));
}

void HeptaXTGatewayAdapter::OnXtOrderError(long long orderId, const std::string& errorCode, const std::string& detail)
{
    std::string value = errorCode;
    if (!detail.empty()) value += "|" + detail;
    PushEvent(MakeEvent(XTEventType::Error, orderId, "order_error", value, 0.0, "xt.cb.on_order_error"));
}

void HeptaXTGatewayAdapter::OnXtCancelError(long long orderId, const std::string& errorCode, const std::string& detail)
{
    std::string value = errorCode;
    if (!detail.empty()) value += "|" + detail;
    PushEvent(MakeEvent(XTEventType::Error, orderId, "cancel_error", value, 0.0, "xt.cb.on_cancel_error"));
}

void HeptaXTGatewayAdapter::OnXtAsyncOrderResponse(long long orderId, bool ok, const std::string& detail)
{
    PushEvent(MakeEvent(XTEventType::OrderAck, orderId, "order_async", ok ? "ok" : "fail", 0.0,
                        "xt.cb.on_order_stock_async_response"));
    if (!detail.empty())
    {
        PushEvent(MakeEvent(XTEventType::OrderAck, orderId, "order_async_detail", detail, 0.0,
                            "xt.cb.on_order_stock_async_response"));
    }
}

void HeptaXTGatewayAdapter::OnXtAsyncCancelResponse(long long orderId, bool ok, const std::string& detail)
{
    PushEvent(MakeEvent(XTEventType::CancelAck, orderId, "cancel_async", ok ? "ok" : "fail", 0.0,
                        "xt.cb.on_cancel_order_stock_async_response"));
    if (!detail.empty())
    {
        PushEvent(MakeEvent(XTEventType::CancelAck, orderId, "cancel_async_detail", detail, 0.0,
                            "xt.cb.on_cancel_order_stock_async_response"));
    }
}

void HeptaXTGatewayAdapter::PushEvent(const XTEvent& e)
{
    m_events.push(e);
}

XTEvent HeptaXTGatewayAdapter::MakeEvent(XTEventType type, long long id,
                                         const std::string& key,
                                         const std::string& value,
                                         double number,
                                         const std::string& source) const
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
