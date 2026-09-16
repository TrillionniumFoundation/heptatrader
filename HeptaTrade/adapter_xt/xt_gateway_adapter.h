#pragma once

#include <cstdint>
#include <string>

// The vendor transport is still intentionally absent.  These types model the
// already-authenticated read-only HXQ1 envelope that a future mTLS transport
// may hand to the projection layer.  They do not provide a socket, credentials,
// vendor calls, or any mutation authority.
struct HeptaXTConfig {
    std::string mode = "XT";
};

struct XTHXQ1ReadEnvelope {
    std::string protocol = "HXQ1";
    std::uint32_t version = 1;
    std::string requestId;
    std::string serviceEpoch;
    std::uint64_t transportEpoch = 0;
    std::string sidecarInstanceId;
    std::uint64_t connectionEpoch = 0;
    std::string operation;
    std::string account;

    // Snapshot fields are ignored for identity and required for the four
    // authoritative read barriers.  The digest binds the normalized, validated
    // row set without putting account/order/instrument identities into metrics.
    std::uint64_t generation = 0;
    bool complete = false;
    std::uint64_t rowCount = 0;
    std::string snapshotSha256;
    std::string reasonCode;
};

struct XTReadOnlyBarrierSnapshot {
    std::uint64_t generation = 0;
    bool complete = false;
    std::uint64_t rowCount = 0;
    std::string snapshotSha256;
    std::string reasonCode;
};

struct XTReadOnlyAuthoritySnapshot {
    bool identityBound = false;
    std::string serviceEpoch;
    std::uint64_t transportEpoch = 0;
    std::string sidecarInstanceId;
    std::uint64_t connectionEpoch = 0;
    std::string account;
    XTReadOnlyBarrierSnapshot accountSnapshot;
    XTReadOnlyBarrierSnapshot positionSnapshot;
    XTReadOnlyBarrierSnapshot orderSnapshot;
    XTReadOnlyBarrierSnapshot tradeSnapshot;
    bool ready = false;
    std::string reasonCode;
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

    // Projection-only seam.  The caller must already have authenticated and
    // decoded an HXQ1 frame according to the reviewed transport contract.  This
    // method validates application identity/epoch/snapshot semantics only.  It
    // never marks the adapter transport-connected and cannot send a mutation.
    bool ApplyAuthenticatedReadOnlyEnvelope(const XTHXQ1ReadEnvelope& envelope);
    XTReadOnlyAuthoritySnapshot ReadOnlyAuthoritySnapshot() const;

    const char* GetStatusString() const { return m_lastRejectReason.c_str(); }
    const char* CapabilityStatus() const { return "EXPERIMENTAL_NO_TRANSPORT"; }
    const std::string& LastRejectReason() const { return m_lastRejectReason; }

private:
    struct SnapshotSlot {
        std::uint64_t generation = 0;
        bool complete = false;
        std::uint64_t rowCount = 0;
        std::string snapshotSha256;
        std::string reasonCode;
    };

    bool RejectUnsupported();
    bool RejectReadOnly(const std::string& reason);
    bool BindIdentity(const XTHXQ1ReadEnvelope& envelope);
    bool ApplySnapshot(const XTHXQ1ReadEnvelope& envelope, SnapshotSlot& slot);
    void ResetReadOnlyBarriers(const std::string& reason);
    bool ReadOnlyReady() const;
    static bool ValidToken(const std::string& value, std::size_t maximum);
    static bool ValidDigest(const std::string& value);

    bool m_initialized = false;
    std::string m_lastRejectReason = "XT_NOT_INITIALIZED";

    bool m_identityBound = false;
    std::string m_serviceEpoch;
    std::uint64_t m_transportEpoch = 0;
    std::string m_sidecarInstanceId;
    std::uint64_t m_connectionEpoch = 0;
    std::string m_account;
    SnapshotSlot m_accountSnapshot;
    SnapshotSlot m_positionSnapshot;
    SnapshotSlot m_orderSnapshot;
    SnapshotSlot m_tradeSnapshot;
    std::string m_readOnlyReason = "XT_HXQ1_IDENTITY_REQUIRED";
};
