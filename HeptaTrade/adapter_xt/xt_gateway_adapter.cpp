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
    // -1 is the invalid/sentinel value used by the adapter API.  In
    // particular, zero must not look like a broker-issued order id after an
    // unsupported request.
    if (outOrderId) *outOrderId = -1;
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

// The callback bridge remains a future real-transport seam.  Until a real XT
// binding is linked, every callback is treated as untrusted input and is
// rejected.  In particular, an externally invoked callback must not mutate
// lifecycle state or synthesize account/position/order/fill/ACK events.
void HeptaXTGatewayAdapter::RejectUnsupportedCallback(const char* source,
                                                      long long id)
{
    m_inited = false;
    m_connected = false;
    m_status = kTransportUnavailable;
    m_lastRejectReason = kTransportUnavailable;
    PushEvent(MakeEvent(XTEventType::Error, id, "xt_callback",
                        kTransportUnavailable, 0.0, source));
}

void HeptaXTGatewayAdapter::OnXtConnected()
{
    RejectUnsupportedCallback("xt.cb.on_connected");
}

void HeptaXTGatewayAdapter::OnXtDisconnected(const std::string& /*reason*/)
{
    RejectUnsupportedCallback("xt.cb.on_disconnected");
}

void HeptaXTGatewayAdapter::OnXtAccountStatus(const std::string& /*status*/)
{
    RejectUnsupportedCallback("xt.cb.on_account_status");
}

void HeptaXTGatewayAdapter::OnXtAsset(double /*totalAsset*/, double /*cash*/)
{
    RejectUnsupportedCallback("xt.cb.on_stock_asset");
}

void HeptaXTGatewayAdapter::OnXtPosition(const std::string& /*instrument*/,
                                         double /*volume*/)
{
    RejectUnsupportedCallback("xt.cb.on_stock_position");
}

void HeptaXTGatewayAdapter::OnXtOrderStatus(long long orderId,
                                            const std::string& /*status*/,
                                            const std::string& /*detail*/)
{
    RejectUnsupportedCallback("xt.cb.on_stock_order", orderId);
}

void HeptaXTGatewayAdapter::OnXtTrade(long long orderId,
                                      const std::string& /*instrument*/,
                                      const std::string& /*side*/,
                                      double /*qty*/,
                                      double /*price*/)
{
    RejectUnsupportedCallback("xt.cb.on_stock_trade", orderId);
}

void HeptaXTGatewayAdapter::OnXtOrderError(long long orderId,
                                           const std::string& /*errorCode*/,
                                           const std::string& /*detail*/)
{
    RejectUnsupportedCallback("xt.cb.on_order_error", orderId);
}

void HeptaXTGatewayAdapter::OnXtCancelError(long long orderId,
                                            const std::string& /*errorCode*/,
                                            const std::string& /*detail*/)
{
    RejectUnsupportedCallback("xt.cb.on_cancel_error", orderId);
}

void HeptaXTGatewayAdapter::OnXtAsyncOrderResponse(
    long long orderId, bool /*ok*/, const std::string& /*detail*/)
{
    RejectUnsupportedCallback("xt.cb.on_order_stock_async_response", orderId);
}

void HeptaXTGatewayAdapter::OnXtAsyncCancelResponse(
    long long orderId, bool /*ok*/, const std::string& /*detail*/)
{
    RejectUnsupportedCallback("xt.cb.on_cancel_order_stock_async_response",
                              orderId);
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
