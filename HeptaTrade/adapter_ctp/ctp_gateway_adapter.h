#pragma once

#include <string>

// Fail-closed placeholder for the separately controlled CTP integration.
// The public source tree does not contain an authorized, complete CTP
// transport. Callers must treat Init/Connect=false as unsupported, not as a
// transient broker outage.
struct HeptaCTPConfig {
    std::string mode = "CTP";
};

class HeptaCTPGatewayAdapter {
public:
    HeptaCTPGatewayAdapter();
    ~HeptaCTPGatewayAdapter();

    bool Init(const HeptaCTPConfig& cfg);
    bool Connect();
    void Disconnect();

private:
    HeptaCTPConfig m_cfg;
    bool m_connected = false;
};
