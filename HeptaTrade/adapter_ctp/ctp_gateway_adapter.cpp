#include "ctp_gateway_adapter.h"

HeptaCTPGatewayAdapter::HeptaCTPGatewayAdapter() = default;
HeptaCTPGatewayAdapter::~HeptaCTPGatewayAdapter() = default;

bool HeptaCTPGatewayAdapter::Init(const HeptaCTPConfig& cfg) {
    if (cfg.mode != "CTP") {
        m_initialized = false;
        m_lastError = "CTP_MODE_INVALID";
        return false;
    }
    m_initialized = true;
    m_lastError = "CTP_TRANSPORT_NOT_IMPLEMENTED";
    return true;
}

bool HeptaCTPGatewayAdapter::Connect() {
    m_lastError = m_initialized ?
        "CTP_TRANSPORT_NOT_IMPLEMENTED" : "CTP_NOT_INITIALIZED";
    return false;
}

void HeptaCTPGatewayAdapter::Disconnect() {
}

const char* HeptaCTPGatewayAdapter::CapabilityStatus() const {
    return "EXPERIMENTAL_NO_TRANSPORT";
}
