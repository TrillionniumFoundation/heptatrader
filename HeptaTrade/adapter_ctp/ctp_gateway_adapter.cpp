#include "ctp_gateway_adapter.h"

HeptaCTPGatewayAdapter::HeptaCTPGatewayAdapter() = default;
HeptaCTPGatewayAdapter::~HeptaCTPGatewayAdapter() = default;

bool HeptaCTPGatewayAdapter::Init(const HeptaCTPConfig& cfg)
{
    m_cfg = cfg;
    m_connected = false;
    // No reviewed CTP transport is bound to this adapter.  Returning success
    // here previously allowed the scaffold to be mistaken for a live venue.
    return false;
}

bool HeptaCTPGatewayAdapter::Connect()
{
    // Fail closed until a real transport, authoritative state projection and
    // recovery contract are implemented.
    m_connected = false;
    return false;
}

void HeptaCTPGatewayAdapter::Disconnect()
{
    m_connected = false;
}
