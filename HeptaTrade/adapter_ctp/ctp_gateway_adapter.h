#pragma once

#include <string>

// CTP adapter interface scaffold. No real vendor transport is linked in the
// canonical runtime; every connection attempt fails closed until implemented.
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

    bool IsConnected() const { return false; }
    const char* CapabilityStatus() const;
    const std::string& LastError() const { return m_lastError; }

private:
    bool m_initialized = false;
    std::string m_lastError = "CTP_NOT_INITIALIZED";
};
