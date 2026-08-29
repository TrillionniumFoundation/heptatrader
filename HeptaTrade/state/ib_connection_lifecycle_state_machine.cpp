#include "ib_connection_lifecycle_state_machine.h"

#include <algorithm>

namespace hepta {

IBConnectionLifecycleStateMachine::IBConnectionLifecycleStateMachine(
	bool connected, std::uint64_t connectionEpoch, std::uint64_t nowSec)
{
	m_snapshot.connected = connected;
	m_snapshot.connectionEpoch = connectionEpoch;
	m_snapshot.lastTransitionSec = nowSec;
	m_snapshot.transitionReason = connected ? "initial_connected" : "initial_disconnected";
}

IBConnectionTransition IBConnectionLifecycleStateMachine::Observe(
	bool connected, std::uint64_t connectionEpoch, std::uint64_t nowSec,
	const std::string& reason)
{
	const bool epochChanged = connected &&
		connectionEpoch != m_snapshot.connectionEpoch;
	IBConnectionTransition transition = IBConnectionTransition::None;
	if (connected && (!m_snapshot.connected || epochChanged))
		transition = IBConnectionTransition::Restored;
	else if (!connected && m_snapshot.connected)
		transition = IBConnectionTransition::Lost;

	m_snapshot.connected = connected;
	if (connectionEpoch != 0)
		m_snapshot.connectionEpoch = connectionEpoch;
	if (transition != IBConnectionTransition::None)
	{
		++m_snapshot.revision;
		m_snapshot.lastTransitionSec = nowSec;
		m_snapshot.transitionReason = reason;
		if (transition == IBConnectionTransition::Restored)
			m_snapshot.lastReconnectAttemptSec = 0;
	}
	return transition;
}

bool IBConnectionLifecycleStateMachine::ShouldAttemptReconnect(
	std::uint64_t nowSec, std::uint64_t retryIntervalSec) const
{
	if (m_snapshot.connected || retryIntervalSec == 0)
		return false;
	return m_snapshot.lastReconnectAttemptSec == 0 ||
		nowSec >= m_snapshot.lastReconnectAttemptSec + retryIntervalSec;
}

void IBConnectionLifecycleStateMachine::RecordReconnectAttempt(
	std::uint64_t nowSec)
{
	m_snapshot.lastReconnectAttemptSec = nowSec;
}

IBConnectionLifecycleSnapshot
IBConnectionLifecycleStateMachine::GetSnapshot() const
{
	return m_snapshot;
}

IBLivenessAction IBConnectionLifecycleStateMachine::EvaluateLiveness(
	std::uint64_t nowSec, const IBLivenessPolicy& policy,
	const IBLivenessState& state)
{
	if (state.connectedSinceSec == 0 || nowSec < state.connectedSinceSec)
		return IBLivenessAction::None;

	const std::uint64_t connectedAge = nowSec - state.connectedSinceSec;
	if (policy.nextValidIdStaleSec > 0 &&
		connectedAge >= policy.nextValidIdStaleSec &&
		state.lastValidOrderId <= 0)
	{
		return IBLivenessAction::ForceReconnectNextValidIdStale;
	}

	if (policy.marketDataStaleSec == 0 ||
		connectedAge < std::max<std::uint64_t>(policy.graceSec, 1))
	{
		return IBLivenessAction::None;
	}

	const bool marketDataStale = state.lastMarketDataSec == 0 ?
		connectedAge >= policy.marketDataStaleSec :
		nowSec >= state.lastMarketDataSec + policy.marketDataStaleSec;
	if (!marketDataStale)
		return IBLivenessAction::None;

	const bool activity = state.hasBrokerExposure || state.hasExecutionWork;
	if (!policy.marketDataRequireActivity || activity)
		return IBLivenessAction::ForceReconnectMarketDataStale;
	return IBLivenessAction::WarnMarketDataStaleSuppressed;
}

} // namespace hepta
