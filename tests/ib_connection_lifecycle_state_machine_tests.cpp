#include "state/ib_connection_lifecycle_state_machine.h"

#include <cassert>
#include <iostream>

using namespace hepta;

int main()
{
	IBConnectionLifecycleStateMachine lifecycle(false, 3, 100);
	assert(lifecycle.ShouldAttemptReconnect(100, 10));
	lifecycle.RecordReconnectAttempt(100);
	assert(!lifecycle.ShouldAttemptReconnect(109, 10));
	assert(lifecycle.ShouldAttemptReconnect(110, 10));

	assert(lifecycle.Observe(true, 4, 111, "reconnect") ==
		IBConnectionTransition::Restored);
	assert(!lifecycle.ShouldAttemptReconnect(200, 10));
	assert(lifecycle.Observe(true, 4, 112, "duplicate") ==
		IBConnectionTransition::None);
	assert(lifecycle.Observe(true, 5, 113, "new_epoch") ==
		IBConnectionTransition::Restored);
	assert(lifecycle.Observe(false, 5, 114, "closed") ==
		IBConnectionTransition::Lost);
	assert(lifecycle.Observe(false, 5, 115, "duplicate_closed") ==
		IBConnectionTransition::None);

	IBLivenessPolicy policy;
	policy.graceSec = 20;
	policy.nextValidIdStaleSec = 30;
	policy.marketDataStaleSec = 90;
	policy.marketDataRequireActivity = true;

	IBLivenessState state;
	state.connectedSinceSec = 100;
	assert(IBConnectionLifecycleStateMachine::EvaluateLiveness(129, policy, state) ==
		IBLivenessAction::None);
	assert(IBConnectionLifecycleStateMachine::EvaluateLiveness(130, policy, state) ==
		IBLivenessAction::ForceReconnectNextValidIdStale);

	state.lastValidOrderId = 42;
	assert(IBConnectionLifecycleStateMachine::EvaluateLiveness(190, policy, state) ==
		IBLivenessAction::WarnMarketDataStaleSuppressed);
	state.hasExecutionWork = true;
	assert(IBConnectionLifecycleStateMachine::EvaluateLiveness(190, policy, state) ==
		IBLivenessAction::ForceReconnectMarketDataStale);
	state.lastMarketDataSec = 189;
	assert(IBConnectionLifecycleStateMachine::EvaluateLiveness(190, policy, state) ==
		IBLivenessAction::None);

	const IBConnectionLifecycleSnapshot snapshot = lifecycle.GetSnapshot();
	assert(!snapshot.connected);
	assert(snapshot.connectionEpoch == 5);
	assert(snapshot.revision == 3);
	assert(snapshot.transitionReason == "closed");

	std::cout << "IB connection lifecycle state machine tests passed\n";
	return 0;
}
