#pragma once
#include "zero_dte.h"

namespace hepta { namespace options {

// Normalized vendor identity, not a Snowball wire format or an IB conId.
// Only standard XSP/SPXW European PM cash-settled products are supported.
struct CanonicalOptionIdentity {
    std::string symbol, underlyingType = "IND", currency = "USD";
    std::string expiry, right, tradingClass, localSymbol;
    std::int64_t strikeMilli = 0;
    int multiplier = 0;
    std::string Key() const;
};
bool ParseCanonicalOcc(const std::string& localSymbol, CanonicalOptionIdentity&, std::string& reason);
std::string ValidateCanonicalOption(const CanonicalOptionIdentity&);
std::string MatchBrokerOption(const CanonicalOptionIdentity&, const Contract&);

// Trusted transport boundary input. A UI subscription, a config flag, or a
// client-authored realtime=true is NOT an authorization witness. The real
// Snowball transport must obtain/verify these facts before calling BeginEpoch.
// No such transport or live entitlement has been verified in this delivery.
struct SnowballFeedAuthority {
    std::string provider = "SNOWBALL", receiptId, endpointIdentity;
    std::string accountSubject, productClass, underlyingSymbol;
    bool apiUseVerified = false, realtimeOptions = false;
    bool productCoverage = false, realtimeUnderlying = false;
    std::int64_t verifiedAtMs = 0, validUntilMs = 0;
};
struct SourceStamp {
    std::int64_t sourceMs = 0, receivedMs = 0, receivedMonotonicMs = 0;
};
struct SnowballOptionFrame {
    std::string vendorContractId;
    CanonicalOptionIdentity identity;
    std::uint64_t feedEpoch = 0, sequence = 0;
    bool realtime = false, fullSnapshot = false;
    Book option;
    Tick underlying;
    Greeks greeks;
    // Independent source and local receive timestamps for every field.
    SourceStamp bid, ask, bidSize, askSize, underlyingStamp, greeksStamp;
};
struct BrokerOptionBinding {
    Contract contract;
    std::uint64_t brokerEpoch = 0;
    bool qualified = false;
};

// No socket, SDK credentials, order API, or mutation permit. This is the native
// normalized-feed admission/cache layer, not a claimed Snowball HTTP client.
class SnowballOptionFeed {
public:
    bool BeginEpoch(std::uint64_t epoch, const std::string& sessionIdentity,
                    const SnowballFeedAuthority&, std::string& reason);
    void Disconnect();
    bool Observe(const SnowballOptionFrame&, std::string& reason);
    Decision Evaluate(const Policy&, const BrokerOptionBinding&, const Snapshot& broker,
                      std::int64_t nowMonotonicMs, bool reduceOnly = false) const;
    std::uint64_t FeedEpoch() const { return epoch_; }
    bool Connected() const { return connected_; }
private:
    std::uint64_t epoch_ = 0;
    std::string sessionIdentity_, fault_;
    bool connected_ = false, haveFrame_ = false;
    SnowballFeedAuthority authority_;
    SnowballOptionFrame frame_;
};

} }
