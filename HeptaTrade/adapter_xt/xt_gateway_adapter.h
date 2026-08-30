#pragma once

#include <string>
#include <queue>
#include <unordered_map>
#include <cstdint>

// XT (MiniQMT/xtquant) adapter scaffold.
// Stage-2 goal: stable event semantics for future real SDK binding.

enum class XTEventType {
    None = 0,
    Connected,
    Disconnected,
    Account,
    Position,
    Tick,
    OrderStatus,
    Error,
    OrderAck,
    CancelAck,
};

struct XTEvent {
    XTEventType type = XTEventType::None;
    long long id = 0;
    std::string key;
    std::string value;
    double number = 0.0;
    std::int64_t tsMs = 0;
    std::string source;
};

struct HeptaXTRiskConfig {
    bool enableOrderSubmission = false;
    double maxOrderQuantity = 1.0;
    int maxDailyOrders = 20;
    bool globalKillSwitch = false;
    bool flattenOnly = false;
    double maxPriceDeviationBps = 30.0;
};

struct HeptaXTConfig {
    std::string mode = "XT";
    std::string path;         // miniqmt userdata_mini path
    long long sessionId = 88888;
    std::string account;
    std::string accountType = "STOCK";
    bool readOnly = true;
    HeptaXTRiskConfig risk;
};

class HeptaXTGatewayAdapter {
public:
    HeptaXTGatewayAdapter();
    ~HeptaXTGatewayAdapter();

    bool Init(const HeptaXTConfig& cfg);
    bool Connect();
    void Disconnect();
    bool PollOnce(int timeoutMs);
    bool TryDequeueEvent(XTEvent& outEvent);

    bool ReqAccountSummary();
    bool ReqPositions();
    bool ReqMktData(const std::string& instrument);

    bool PlaceOrder(const std::string& instrument,
                    const std::string& side,
                    double qty,
                    double price,
                    long long* outOrderId = nullptr);
    bool CancelOrder(long long orderId);

    const char* GetStatusString() const;
    bool RunPreflightChecks(std::string& reason) const;

    // Stage-2 callback bridge API (to be called by real XT transport binding)
    void OnXtConnected();
    void OnXtDisconnected(const std::string& reason = "");
    void OnXtAccountStatus(const std::string& status);
    void OnXtAsset(double totalAsset, double cash);
    void OnXtPosition(const std::string& instrument, double volume);
    void OnXtOrderStatus(long long orderId, const std::string& status, const std::string& detail = "");
    void OnXtTrade(long long orderId, const std::string& instrument, const std::string& side, double qty, double price);
    void OnXtOrderError(long long orderId, const std::string& errorCode, const std::string& detail);
    void OnXtCancelError(long long orderId, const std::string& errorCode, const std::string& detail);
    void OnXtAsyncOrderResponse(long long orderId, bool ok, const std::string& detail = "");
    void OnXtAsyncCancelResponse(long long orderId, bool ok, const std::string& detail = "");

private:
    // The callback bridge is intentionally inert until a real XT transport is
    // linked.  Public callback entry points must never be able to turn this
    // unsupported scaffold into an apparent connected/acknowledged venue.
    void RejectUnsupportedCallback(const char* source, long long id = 0);
    void PushEvent(const XTEvent& e);
    XTEvent MakeEvent(XTEventType type, long long id, const std::string& key,
                      const std::string& value, double number, const std::string& source) const;

    HeptaXTConfig m_cfg;
    bool m_inited = false;
    bool m_connected = false;
    std::string m_status = "XT_NOT_INIT";
    mutable std::string m_lastRejectReason;
    long long m_localOrderSeed = 100000;
    std::queue<XTEvent> m_events;

    // request correlation for event enrichment
    std::unordered_map<long long, std::string> m_orderSymbol;
    std::unordered_map<long long, std::string> m_orderSide;
};
