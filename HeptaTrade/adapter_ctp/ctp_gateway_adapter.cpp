#include "ctp_gateway_adapter.h"

HeptaCTPGatewayAdapter::HeptaCTPGatewayAdapter() = default;
HeptaCTPGatewayAdapter::~HeptaCTPGatewayAdapter() = default;

bool HeptaCTPGatewayAdapter::Init(const HeptaCTPConfig& cfg) {
    m_cfg = cfg;
    return true;
}

bool HeptaCTPGatewayAdapter::Connect() {
    m_connected = true;
    return true;
}

void HeptaCTPGatewayAdapter::Disconnect() {
    m_connected = false;
}
