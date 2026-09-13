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
    m_status = m_inited ? "XT_EXPERIMENTAL_NO_TRANSPORT" : "XT_NOT_INIT";
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
    m_status = "XT_TRANSPORT_NOT_IMPLEMENTED";
    m_lastRejectReason = "XT_TRANSPORT_NOT_IMPLEMENTED";
    PushEvent(MakeEvent(XTEventType::Error, id,
                        operation ? operation : "operation",
                        m_lastRejectReason, 0.0,
                        "xt.experimental.no_transport"));
    return false;
}

// Retain callback signatures for source compatibility, not unreachable fake
// transport branches. Legacy callers and capability tests remain fail-closed.
void HeptaXTGatewayAdapter::OnXtConnected()
{
    RejectUnsupported("callback_connected");
}

void HeptaXTGatewayAdapter::OnXtDisconnected(const std::string&)
{
    Disconnect();
}

void HeptaXTGatewayAdapter::OnXtAccountStatus(const std::string&) {}
void HeptaXTGatewayAdapter::OnXtAsset(double, double) {}
void HeptaXTGatewayAdapter::OnXtPosition(const std::string&, double) {}
void HeptaXTGatewayAdapter::OnXtOrderStatus(long long, const std::string&, const std::string&) {}
void HeptaXTGatewayAdapter::OnXtTrade(long long, const std::string&, const std::string&, double, double) {}
void HeptaXTGatewayAdapter::OnXtOrderError(long long, const std::string&, const std::string&) {}
void HeptaXTGatewayAdapter::OnXtCancelError(long long, const std::string&, const std::string&) {}
void HeptaXTGatewayAdapter::OnXtAsyncOrderResponse(long long, bool, const std::string&) {}
void HeptaXTGatewayAdapter::OnXtAsyncCancelResponse(long long, bool, const std::string&) {}

void HeptaXTGatewayAdapter::PushEvent(const XTEvent& e)
{
    // Only diagnostic errors exist in this unsupported adapter. Keep the
    // newest bounded window, not an unbounded queue under repeated calls.
    if (m_events.size() >= 64U) m_events.pop();
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
