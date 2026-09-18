#pragma once

#include <cstdint>
#include <functional>
#include <map>
#include <set>
#include <string>
#include <vector>

struct HeptaXTAccountSnapshot {
    bool complete = false;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t generation = 0;
    std::string currency;
    double cash = 0.0;
    double totalAsset = 0.0;
    double availableCash = 0.0;
};

struct HeptaXTPosition {
    std::string instrument;
    double quantity = 0.0;
    double sellableQuantity = 0.0;
    double cost = 0.0;
};

struct HeptaXTPositionSnapshot {
    bool complete = false;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t generation = 0;
    std::vector<HeptaXTPosition> positions;
};

struct HeptaXTOrder {
    std::string orderId;
    std::string instrument;
    std::string side;
    std::string status;
    double quantity = 0.0;
    double filledQuantity = 0.0;
    double limitPrice = 0.0;
};

struct HeptaXTOrderSnapshot {
    bool complete = false;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t generation = 0;
    std::vector<HeptaXTOrder> orders;
};

struct HeptaXTTrade {
    std::string tradeId;
    std::string orderId;
    std::string instrument;
    std::string side;
    double quantity = 0.0;
    double price = 0.0;
    std::uint64_t occurredAtMs = 0;
};

struct HeptaXTTradeSnapshot {
    bool complete = false;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t generation = 0;
    std::vector<HeptaXTTrade> trades;
};

struct HeptaXTQuoteSnapshot {
    bool complete = false;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t generation = 0;
    std::string instrument;
    double bid = 0.0;
    double ask = 0.0;
    std::uint64_t observedAtMs = 0;
};

// HXQ1 v1 read-only client boundary. The injected exchange is deliberately
// narrower than a socket: production wiring must supply an already-admitted,
// peer-pinned mTLS channel owned by the Execution identity. No callback here can
// authorize place/cancel and no Agent credential crosses this interface.
struct HeptaXTConfig {
    std::string mode = "XT";
    std::string account;
    std::string serviceEpoch;
    std::uint64_t connectionEpoch = 0;
    // Trusted qualification-profile bindings. Read-side authority is accepted
    // only for this account currency and this finite normalized instrument set.
    std::string accountCurrency;
    std::set<std::string> authorizedInstruments;
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
    bool ReqOrders();
    bool ReqTrades();
    bool ReqMktData(const std::string& instrument);
    bool GetAccountSnapshot(HeptaXTAccountSnapshot& out) const;
    bool GetPositionSnapshot(HeptaXTPositionSnapshot& out) const;
    bool GetOrderSnapshot(HeptaXTOrderSnapshot& out) const;
    bool GetTradeSnapshot(HeptaXTTradeSnapshot& out) const;
    bool GetQuoteSnapshot(const std::string& instrument, HeptaXTQuoteSnapshot& out) const;
    bool AccountPositionReadReady() const;
    bool AccountPositionOrderTradeReadReady() const;
    bool QuoteFresh(const std::string& instrument,
                    std::uint64_t evaluationAtMs,
                    std::uint64_t maximumAgeMs) const;
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
                          const std::string& payloadJson,
                          std::string* responsePayloadJson = nullptr);
    static bool ParseAccountSnapshot(const std::string& payload,
                                     std::uint64_t connectionEpoch,
                                     HeptaXTAccountSnapshot& out);
    static bool ParsePositionSnapshot(const std::string& payload,
                                      std::uint64_t connectionEpoch,
                                      HeptaXTPositionSnapshot& out);
    static bool ParseOrderSnapshot(const std::string& payload,
                                   std::uint64_t connectionEpoch,
                                   HeptaXTOrderSnapshot& out);
    static bool ParseTradeSnapshot(const std::string& payload,
                                   std::uint64_t connectionEpoch,
                                   HeptaXTTradeSnapshot& out);
    static bool ParseQuoteSnapshot(const std::string& payload,
                                   std::uint64_t connectionEpoch,
                                   const std::string& expectedInstrument,
                                   HeptaXTQuoteSnapshot& out);
    static bool SafeToken(const std::string& value, std::size_t maximum);
    bool InstrumentAuthorized(const std::string& instrument) const;
    static bool LowerHexSha256(const std::string& value);
    static std::string EscapeJson(const std::string& value);
    void ResetReadState();
    bool FailReadOnlyConnection(const char* reason);

    bool m_initialized = false;
    bool m_connected = false;
    bool m_readOnlyTransportConfigured = false;
    std::uint64_t m_nextRequestId = 1;
    HeptaXTConfig m_config;
    HeptaXTAccountSnapshot m_accountSnapshot;
    HeptaXTPositionSnapshot m_positionSnapshot;
    HeptaXTOrderSnapshot m_orderSnapshot;
    HeptaXTTradeSnapshot m_tradeSnapshot;
    std::map<std::string, HeptaXTQuoteSnapshot> m_quoteSnapshots;
    std::string m_lastRejectReason = "XT_NOT_INITIALIZED";
};
