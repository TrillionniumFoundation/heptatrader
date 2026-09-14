#include "xt_gateway_adapter.h"

bool HeptaXTGatewayAdapter::Init(const HeptaXTConfig& cfg)
{
    m_initialized = cfg.mode == "XT";
    m_lastRejectReason = m_initialized ?
        "XT_TRANSPORT_NOT_IMPLEMENTED" : "XT_MODE_INVALID";
    return m_initialized;
}

bool HeptaXTGatewayAdapter::RejectUnsupported()
{
    m_lastRejectReason = m_initialized ?
        "XT_TRANSPORT_NOT_IMPLEMENTED" : "XT_NOT_INITIALIZED";
    return false;
}

bool HeptaXTGatewayAdapter::Connect() { return RejectUnsupported(); }
void HeptaXTGatewayAdapter::Disconnect() { RejectUnsupported(); }
bool HeptaXTGatewayAdapter::ReqAccountSummary() { return RejectUnsupported(); }
bool HeptaXTGatewayAdapter::ReqPositions() { return RejectUnsupported(); }
bool HeptaXTGatewayAdapter::ReqMktData(const std::string&) { return RejectUnsupported(); }
bool HeptaXTGatewayAdapter::CancelOrder(long long) { return RejectUnsupported(); }

bool HeptaXTGatewayAdapter::PlaceOrder(const std::string&, const std::string&,
                                      double, double, long long* outOrderId)
{
    // No argument can turn an absent transport into an executable order.
    if (outOrderId) *outOrderId = 0;
    return RejectUnsupported();
}
