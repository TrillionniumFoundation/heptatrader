#pragma once

#include <string>

// Negative capability only: there is no XT transport, event stream or risk
// engine. A future transport needs a new reviewed contract, not empty hooks.
struct HeptaXTConfig {
    std::string mode = "XT";
};

class HeptaXTGatewayAdapter {
public:
    bool Init(const HeptaXTConfig& cfg);
    bool Connect();
    void Disconnect();
    bool IsConnected() const { return false; }
    bool ReqAccountSummary();
    bool ReqPositions();
    bool ReqMktData(const std::string& instrument);
    bool PlaceOrder(const std::string& instrument, const std::string& side,
                    double qty, double price, long long* outOrderId = nullptr);
    bool CancelOrder(long long orderId);
    const char* GetStatusString() const { return m_lastRejectReason.c_str(); }
    const char* CapabilityStatus() const { return "EXPERIMENTAL_NO_TRANSPORT"; }
    const std::string& LastRejectReason() const { return m_lastRejectReason; }

private:
    bool RejectUnsupported();
    bool m_initialized = false;
    std::string m_lastRejectReason = "XT_NOT_INITIALIZED";
};
