#include "xt_gateway_adapter.h"

#include <cctype>

namespace {
XTReadOnlyBarrierSnapshot PublicSlot(
    std::uint64_t generation, bool complete, std::uint64_t rowCount,
    const std::string& digest, const std::string& reason)
{
    XTReadOnlyBarrierSnapshot out;
    out.generation = generation;
    out.complete = complete;
    out.rowCount = rowCount;
    out.snapshotSha256 = digest;
    out.reasonCode = reason;
    return out;
}
}

bool HeptaXTGatewayAdapter::Init(const HeptaXTConfig& cfg)
{
    m_initialized = cfg.mode == "XT";
    m_identityBound = false;
    m_serviceEpoch.clear();
    m_transportEpoch = 0;
    m_sidecarInstanceId.clear();
    m_connectionEpoch = 0;
    m_account.clear();
    ResetReadOnlyBarriers("XT_HXQ1_IDENTITY_REQUIRED");
    m_lastRejectReason = m_initialized ?
        "XT_TRANSPORT_NOT_IMPLEMENTED" : "XT_MODE_INVALID";
    return m_initialized;
}

bool HeptaXTGatewayAdapter::RejectUnsupported()
{
    m_lastRejectReason = m_initialized ?
        "XT_TRANSPORT_NOT_IMPLEMENTED" : "XT_NOT_INITIALIZED";
    return false;
}

bool HeptaXTGatewayAdapter::RejectReadOnly(const std::string& reason)
{
    m_readOnlyReason = reason;
    m_lastRejectReason = reason;
    return false;
}

bool HeptaXTGatewayAdapter::ValidToken(const std::string& value, std::size_t maximum)
{
    if (value.empty() || value.size() > maximum) return false;
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
    {
        const unsigned char c = static_cast<unsigned char>(*it);
        if (c < 0x21 || c > 0x7e || c == '\\' || c == '"') return false;
    }
    return true;
}

bool HeptaXTGatewayAdapter::ValidDigest(const std::string& value)
{
    if (value.size() != 64U) return false;
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
        if (!((*it >= '0' && *it <= '9') || (*it >= 'a' && *it <= 'f')))
            return false;
    return true;
}

void HeptaXTGatewayAdapter::ResetReadOnlyBarriers(const std::string& reason)
{
    m_accountSnapshot = SnapshotSlot();
    m_positionSnapshot = SnapshotSlot();
    m_orderSnapshot = SnapshotSlot();
    m_tradeSnapshot = SnapshotSlot();
    m_readOnlyReason = reason;
}

bool HeptaXTGatewayAdapter::BindIdentity(const XTHXQ1ReadEnvelope& envelope)
{
    if (!envelope.complete || envelope.generation != 0 || envelope.rowCount != 0 ||
        !envelope.snapshotSha256.empty() || !envelope.reasonCode.empty())
        return RejectReadOnly("XT_HXQ1_IDENTITY_INVALID");

    if (!m_identityBound)
    {
        m_identityBound = true;
        m_serviceEpoch = envelope.serviceEpoch;
        m_transportEpoch = envelope.transportEpoch;
        m_sidecarInstanceId = envelope.sidecarInstanceId;
        m_connectionEpoch = envelope.connectionEpoch;
        m_account = envelope.account;
        ResetReadOnlyBarriers("XT_HXQ1_REFRESH_REQUIRED");
        m_lastRejectReason = "XT_TRANSPORT_NOT_IMPLEMENTED";
        return true;
    }

    if (envelope.transportEpoch < m_transportEpoch ||
        envelope.connectionEpoch < m_connectionEpoch)
        return RejectReadOnly("XT_HXQ1_STALE_EPOCH");

    if (envelope.transportEpoch == m_transportEpoch &&
        envelope.connectionEpoch == m_connectionEpoch)
    {
        if (envelope.serviceEpoch != m_serviceEpoch ||
            envelope.sidecarInstanceId != m_sidecarInstanceId ||
            envelope.account != m_account)
            return RejectReadOnly("XT_HXQ1_IDENTITY_CONFLICT");
        m_lastRejectReason = "XT_TRANSPORT_NOT_IMPLEMENTED";
        return true;
    }

    // A newer authenticated transport or venue connection is an authority
    // boundary.  Service/account identity cannot silently change on it.
    if (envelope.serviceEpoch != m_serviceEpoch || envelope.account != m_account)
        return RejectReadOnly("XT_HXQ1_BINDING_CONFLICT");
    m_transportEpoch = envelope.transportEpoch;
    m_sidecarInstanceId = envelope.sidecarInstanceId;
    m_connectionEpoch = envelope.connectionEpoch;
    ResetReadOnlyBarriers("XT_HXQ1_REFRESH_REQUIRED");
    m_lastRejectReason = "XT_TRANSPORT_NOT_IMPLEMENTED";
    return true;
}

bool HeptaXTGatewayAdapter::ApplySnapshot(
    const XTHXQ1ReadEnvelope& envelope, SnapshotSlot& slot)
{
    if (!m_identityBound)
        return RejectReadOnly("XT_HXQ1_IDENTITY_REQUIRED");
    if (envelope.serviceEpoch != m_serviceEpoch ||
        envelope.transportEpoch != m_transportEpoch ||
        envelope.sidecarInstanceId != m_sidecarInstanceId ||
        envelope.connectionEpoch != m_connectionEpoch ||
        envelope.account != m_account)
        return RejectReadOnly("XT_HXQ1_STALE_OR_FOREIGN_BINDING");
    if (envelope.generation == 0 || !ValidDigest(envelope.snapshotSha256) ||
        (envelope.complete && !envelope.reasonCode.empty()) ||
        (!envelope.complete && envelope.reasonCode.empty()))
        return RejectReadOnly("XT_HXQ1_SNAPSHOT_INVALID");
    if (!envelope.reasonCode.empty() && !ValidToken(envelope.reasonCode, 96))
        return RejectReadOnly("XT_HXQ1_SNAPSHOT_INVALID");

    if (envelope.generation < slot.generation)
        return RejectReadOnly("XT_HXQ1_STALE_GENERATION");
    if (envelope.generation == slot.generation && slot.generation != 0)
    {
        if (envelope.complete != slot.complete ||
            envelope.rowCount != slot.rowCount ||
            envelope.snapshotSha256 != slot.snapshotSha256 ||
            envelope.reasonCode != slot.reasonCode)
        {
            slot.complete = false;
            slot.reasonCode = "XT_HXQ1_SNAPSHOT_CONFLICT";
            m_readOnlyReason = slot.reasonCode;
            return RejectReadOnly(slot.reasonCode);
        }
        return true;
    }

    slot.generation = envelope.generation;
    slot.complete = envelope.complete;
    slot.rowCount = envelope.rowCount;
    slot.snapshotSha256 = envelope.snapshotSha256;
    slot.reasonCode = envelope.reasonCode;
    m_readOnlyReason = ReadOnlyReady() ? std::string() :
        (envelope.complete ? "XT_HXQ1_REFRESH_REQUIRED" : envelope.reasonCode);
    return true;
}

bool HeptaXTGatewayAdapter::ReadOnlyReady() const
{
    return m_identityBound &&
        m_accountSnapshot.generation != 0 && m_accountSnapshot.complete &&
        m_positionSnapshot.generation != 0 && m_positionSnapshot.complete &&
        m_orderSnapshot.generation != 0 && m_orderSnapshot.complete &&
        m_tradeSnapshot.generation != 0 && m_tradeSnapshot.complete;
}

bool HeptaXTGatewayAdapter::ApplyAuthenticatedReadOnlyEnvelope(
    const XTHXQ1ReadEnvelope& envelope)
{
    if (!m_initialized)
        return RejectReadOnly("XT_NOT_INITIALIZED");
    if (envelope.protocol != "HXQ1" || envelope.version != 1 ||
        !ValidToken(envelope.requestId, 96) ||
        !ValidToken(envelope.serviceEpoch, 128) || envelope.transportEpoch == 0 ||
        !ValidToken(envelope.sidecarInstanceId, 128) || envelope.connectionEpoch == 0 ||
        !ValidToken(envelope.account, 128) || !ValidToken(envelope.operation, 64))
        return RejectReadOnly("XT_HXQ1_PROTOCOL_INVALID");
    if (envelope.operation == "place" || envelope.operation == "cancel")
        return RejectReadOnly("XT_HXQ1_MUTATION_FORBIDDEN");
    if (envelope.operation == "identity")
        return BindIdentity(envelope);
    if (envelope.operation == "account_snapshot")
        return ApplySnapshot(envelope, m_accountSnapshot);
    if (envelope.operation == "position_snapshot")
        return ApplySnapshot(envelope, m_positionSnapshot);
    if (envelope.operation == "order_snapshot")
        return ApplySnapshot(envelope, m_orderSnapshot);
    if (envelope.operation == "trade_snapshot")
        return ApplySnapshot(envelope, m_tradeSnapshot);
    return RejectReadOnly("XT_HXQ1_READ_OPERATION_UNSUPPORTED");
}

XTReadOnlyAuthoritySnapshot HeptaXTGatewayAdapter::ReadOnlyAuthoritySnapshot() const
{
    XTReadOnlyAuthoritySnapshot out;
    out.identityBound = m_identityBound;
    out.serviceEpoch = m_serviceEpoch;
    out.transportEpoch = m_transportEpoch;
    out.sidecarInstanceId = m_sidecarInstanceId;
    out.connectionEpoch = m_connectionEpoch;
    out.account = m_account;
    out.accountSnapshot = PublicSlot(
        m_accountSnapshot.generation, m_accountSnapshot.complete,
        m_accountSnapshot.rowCount, m_accountSnapshot.snapshotSha256,
        m_accountSnapshot.reasonCode);
    out.positionSnapshot = PublicSlot(
        m_positionSnapshot.generation, m_positionSnapshot.complete,
        m_positionSnapshot.rowCount, m_positionSnapshot.snapshotSha256,
        m_positionSnapshot.reasonCode);
    out.orderSnapshot = PublicSlot(
        m_orderSnapshot.generation, m_orderSnapshot.complete,
        m_orderSnapshot.rowCount, m_orderSnapshot.snapshotSha256,
        m_orderSnapshot.reasonCode);
    out.tradeSnapshot = PublicSlot(
        m_tradeSnapshot.generation, m_tradeSnapshot.complete,
        m_tradeSnapshot.rowCount, m_tradeSnapshot.snapshotSha256,
        m_tradeSnapshot.reasonCode);
    out.ready = ReadOnlyReady();
    out.reasonCode = out.ready ? std::string() : m_readOnlyReason;
    return out;
}

bool HeptaXTGatewayAdapter::Connect() { return RejectUnsupported(); }
void HeptaXTGatewayAdapter::Disconnect()
{
    m_identityBound = false;
    m_serviceEpoch.clear();
    m_transportEpoch = 0;
    m_sidecarInstanceId.clear();
    m_connectionEpoch = 0;
    m_account.clear();
    ResetReadOnlyBarriers("XT_HXQ1_IDENTITY_REQUIRED");
    RejectUnsupported();
}
bool HeptaXTGatewayAdapter::ReqAccountSummary() { return RejectUnsupported(); }
bool HeptaXTGatewayAdapter::ReqPositions() { return RejectUnsupported(); }
bool HeptaXTGatewayAdapter::ReqMktData(const std::string&) { return RejectUnsupported(); }
bool HeptaXTGatewayAdapter::CancelOrder(long long) { return RejectUnsupported(); }

bool HeptaXTGatewayAdapter::PlaceOrder(const std::string&, const std::string&,
                                      double, double, long long* outOrderId)
{
    // No argument or read-only projection can turn an absent transport into an
    // executable order.
    if (outOrderId) *outOrderId = 0;
    return RejectUnsupported();
}
