#ifndef HEPTA_STATE_IB_CONNECTION_LIFECYCLE_STATE_MACHINE_H
#define HEPTA_STATE_IB_CONNECTION_LIFECYCLE_STATE_MACHINE_H

#include <cstdint>
#include <string>

namespace hepta {

enum class IBConnectionTransition
{
	None,
	Restored,
	Lost
};

enum class IBLivenessAction
{
	None,
	ForceReconnectNextValidIdStale,
	ForceReconnectMarketDataStale,
	WarnMarketDataStaleSuppressed
};

struct IBLivenessPolicy
{
	std::uint64_t graceSec = 0;
	std::uint64_t nextValidIdStaleSec = 0;
	std::uint64_t marketDataStaleSec = 0;
	bool marketDataRequireActivity = true;
};

struct IBLivenessState
{
	std::uint64_t connectedSinceSec = 0;
	std::uint64_t lastNextValidIdSec = 0;
	std::uint64_t lastMarketDataSec = 0;
	long lastValidOrderId = 0;
	bool hasBrokerExposure = false;
	bool hasExecutionWork = false;
};

struct IBConnectionLifecycleSnapshot
{
	bool connected = false;
	std::uint64_t connectionEpoch = 0;
	std::uint64_t revision = 0;
	std::uint64_t lastTransitionSec = 0;
	std::uint64_t lastReconnectAttemptSec = 0;
	std::string transitionReason;
};

class IBConnectionLifecycleStateMachine
{
public:
	IBConnectionLifecycleStateMachine(bool connected,
		std::uint64_t connectionEpoch, std::uint64_t nowSec);

	IBConnectionTransition Observe(bool connected,
		std::uint64_t connectionEpoch, std::uint64_t nowSec,
		const std::string& reason);

	bool ShouldAttemptReconnect(std::uint64_t nowSec,
		std::uint64_t retryIntervalSec) const;
	void RecordReconnectAttempt(std::uint64_t nowSec);

	IBConnectionLifecycleSnapshot GetSnapshot() const;

	static IBLivenessAction EvaluateLiveness(std::uint64_t nowSec,
		const IBLivenessPolicy& policy, const IBLivenessState& state);

private:
	IBConnectionLifecycleSnapshot m_snapshot;
};

} // namespace hepta

#endif
