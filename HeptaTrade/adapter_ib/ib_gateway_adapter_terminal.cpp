#include "ib_gateway_adapter.h"

bool HeptaIBGatewayAdapter::HaltTransportForTerminalAudit(
    std::vector<IBEvent>& drainedEvents,
    IBAuthoritativeRecoveryAuditSnapshot& frozenSnapshot,
    std::string& reason)
{
    std::lock_guard<std::recursive_mutex> lock(m_apiMutex);
    return HaltTransportForTerminalAuditLocked(
        drainedEvents, frozenSnapshot, reason);
}

bool HeptaIBGatewayAdapter::IsTerminalTransportHalted() const
{
    std::lock_guard<std::recursive_mutex> lock(m_apiMutex);
    return m_terminalTransportHalted;
}

bool HeptaIBGatewayAdapter::IsTerminalTransportDrainVerified() const
{
    std::lock_guard<std::recursive_mutex> lock(m_apiMutex);
    return m_terminalTransportHalted && m_terminalTransportDrainVerified;
}

std::uint64_t HeptaIBGatewayAdapter::TerminalCallbacksInFlight() const
{
    std::lock_guard<std::recursive_mutex> lock(m_apiMutex);
    return m_terminalCallbacksInFlight;
}
