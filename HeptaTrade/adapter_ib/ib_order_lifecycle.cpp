#include "ib_order_lifecycle.h"

void IbOrderLifecycleTracker::ActivateConnectionEpoch(
    std::uint64_t connectionEpoch)
{
    if (m_connectionEpochActive && m_connectionEpoch == connectionEpoch)
        return;
    m_states.clear();
    m_connectionEpoch = connectionEpoch;
    m_connectionEpochActive = true;
}

void IbOrderLifecycleTracker::InvalidateConnectionEpoch()
{
    m_states.clear();
    m_connectionEpochActive = false;
}

void IbOrderLifecycleTracker::BeginLocalOrderGeneration(long orderId)
{
    if (!m_connectionEpochActive) return;
    // Assignment is intentional: reuse of an order id starts a new local
    // generation and must never inherit broker acknowledgement or terminal
    // evidence from the previous generation.
    State state;
    state.sentToBroker = true;
    state.lastStatus = "LOCAL_SENT";
    m_states[orderId] = state;
}

bool IbOrderLifecycleTracker::RecordBrokerOpenOrder(
    long orderId, const std::string& status)
{
    if (!m_connectionEpochActive) return false;
    const std::unordered_map<long, State>::iterator found =
        m_states.find(orderId);
    if (found != m_states.end() && found->second.finalState)
        return false;
    State& state = m_states[orderId];
    state.sentToBroker = true;
    state.brokerAck = true;
    // An OpenOrder callback can carry terminal broker status when an order
    // was already cancelled/rejected before the snapshot reached us. Keep
    // that terminal evidence sticky. A textual Filled OpenOrder is
    // intentionally not terminal here because economic fill evidence arrives
    // through execution/order-status callbacks separately.
    if (IsFinalStatus(status) && status != "Filled")
        state.finalState = true;
    state.lastStatus = status;
    return true;
}

void IbOrderLifecycleTracker::RecordBrokerStatus(
    long orderId, const std::string& status, bool economicFillEvidence)
{
    if (!m_connectionEpochActive) return;
    State& state = m_states[orderId];
    state.sentToBroker = true;
    if (IsBrokerAckStatus(status)) state.brokerAck = true;
    if (IsFinalStatus(status) &&
        (status != "Filled" || economicFillEvidence))
        state.finalState = true;
    state.lastStatus = status;
}

void IbOrderLifecycleTracker::Forget(long orderId)
{
    m_states.erase(orderId);
}

bool IbOrderLifecycleTracker::CanCancel(
    long orderId, std::string* suppressReason) const
{
    const std::unordered_map<long, State>::const_iterator found =
        m_states.find(orderId);
    if (found == m_states.end() || !found->second.sentToBroker)
    {
        if (suppressReason) *suppressReason = "NO_BROKER_SUBMIT";
        return false;
    }
    if (!found->second.brokerAck)
    {
        if (suppressReason) *suppressReason = "NO_BROKER_ACK";
        return false;
    }
    if (found->second.finalState)
    {
        if (suppressReason) *suppressReason = "ALREADY_FINAL";
        return false;
    }
    return true;
}

bool IbOrderLifecycleTracker::IsBrokerAckStatus(
    const std::string& status)
{
    return status == "ApiPending" ||
        status == "PendingSubmit" ||
        status == "PreSubmitted" ||
        status == "Submitted" ||
        status == "PartiallyFilled" ||
        status == "PendingCancel" ||
        status == "Filled" ||
        status == "Cancelled" ||
        status == "ApiCancelled" ||
        status == "Inactive";
}

bool IbOrderLifecycleTracker::IsFinalStatus(const std::string& status)
{
    return status == "Filled" ||
        status == "Cancelled" ||
        status == "ApiCancelled" ||
        status == "Inactive" ||
        status == "Rejected";
}
