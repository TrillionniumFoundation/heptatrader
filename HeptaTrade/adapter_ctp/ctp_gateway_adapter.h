#pragma once

#include <string>

// Unsupported CTP seam. A real implementation must add transport, lifecycle,
// order correlation, authoritative projection, recovery and reconciliation.
struct HeptaCTPConfig
{
    std::string mode = "CTP";
};

class HeptaCTPGatewayAdapter
{
public:
    HeptaCTPGatewayAdapter();
    ~HeptaCTPGatewayAdapter();

    bool Init(const HeptaCTPConfig& cfg);
    bool Connect();
    void Disconnect();
    const char* GetStatusString() const;

private:
    HeptaCTPConfig m_cfg;
    bool m_connected = false;
    std::string m_status = "VENUE_NOT_IMPLEMENTED";
};
