#pragma once
#include <string>
#include <queue>

// CTP adapter scaffold to eventually replace direct m_TradeChannel/m_mdCollector calls.

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
