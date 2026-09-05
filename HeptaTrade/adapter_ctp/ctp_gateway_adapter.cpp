#include "ctp_gateway_adapter.h"

namespace
{
const char* const kUnsupported = "VENUE_NOT_IMPLEMENTED";
}

HeptaCTPGatewayAdapter::HeptaCTPGatewayAdapter() = default;
HeptaCTPGatewayAdapter::~HeptaCTPGatewayAdapter() = default;

bool HeptaCTPGatewayAdapter::Init(const HeptaCTPConfig& cfg)
{
    m_cfg = cfg;
    m_connected = false;
    m_status = kUnsupported;
    return false;
}

bool HeptaCTPGatewayAdapter::Connect()
{
    m_connected = false;
    m_status = kUnsupported;
    return false;
}

void HeptaCTPGatewayAdapter::Disconnect()
{
    m_connected = false;
    m_status = kUnsupported;
}

const char* HeptaCTPGatewayAdapter::GetStatusString() const
{
    return m_status.c_str();
}
