#include "ctp_gateway_adapter.h"

HeptaCTPGatewayAdapter::HeptaCTPGatewayAdapter() = default;
HeptaCTPGatewayAdapter::~HeptaCTPGatewayAdapter() = default;

bool HeptaCTPGatewayAdapter::Init(const HeptaCTPConfig& cfg)
{
    m_cfg = cfg;
    m_connected = false;
    // The distributable repository intentionally has no usable CTP transport.
    return false;
}

bool HeptaCTPGatewayAdapter::Connect()
{
    m_connected = false;
    return false;
}

void HeptaCTPGatewayAdapter::Disconnect()
{
    m_connected = false;
}
