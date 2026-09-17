#pragma once

#include <cstdint>
#include <functional>
#include <string>

// HXQ1 v1 read-only client boundary. The injected exchange is deliberately
// narrower than a socket: production wiring must supply an already-admitted,
// peer-pinned mTLS channel owned by the Execution identity. No callback here can
// authorize place/cancel and no Agent credential crosses this interface.
struct HeptaXTConfig {
    std::string mode = "XT";
    std::string account;
    std::string serviceEpoch;
    std::uint64_t connectionEpoch = 0;
    std::string peerProfileSha256;
    std::function<bool(const std::string&, std::string&)> admittedReadOnlyExchange;
};

class HeptaXTGatewayAdapter {
public:
    bool Init(const HeptaXTConfig& cfg);
    bool Connect();
    void Disconnect();
    bool IsConnected() const { return m_connected; }
    bool ReqAccountSummary();
    bool ReqPositions();
    bool ReqMktData(const std::string& instrument);
    bool PlaceOrder(const std::string& instrument, const std::string& side,
                    double qty, double price, long long* outOrderId = nullptr);
    bool CancelOrder(long long orderId);
    const char* GetStatusString() const { return m_lastRejectReason.c_str(); }
    const char* CapabilityStatus() const {
        return m_readOnlyTransportConfigured ?
            "EXPERIMENTAL_READ_ONLY_HXQ1" : "EXPERIMENTAL_NO_TRANSPORT";
    }
    const std::string& LastRejectReason() const { return m_lastRejectReason; }

    // Protocol helpers are public so the Windows sidecar qualification fixture
    // can cross-check exact bytes without reimplementing a second parser.
    static bool EncodeFrame(const std::string& canonicalJson, std::string& frame);
    static bool DecodeFrame(const std::string& frame, std::string& canonicalJson);

private:
    bool RejectUnsupportedMutation();
    bool ExchangeReadOnly(const std::string& operation,
                          const std::string& payloadJson);
    static bool SafeToken(const std::string& value, std::size_t maximum);
    static bool LowerHexSha256(const std::string& value);
    static std::string EscapeJson(const std::string& value);

    bool m_initialized = false;
    bool m_connected = false;
    bool m_readOnlyTransportConfigured = false;
    std::uint64_t m_nextRequestId = 1;
    HeptaXTConfig m_config;
    std::string m_lastRejectReason = "XT_NOT_INITIALIZED";
};
