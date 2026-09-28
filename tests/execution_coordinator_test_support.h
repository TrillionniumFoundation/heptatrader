#pragma once

#include <cstdlib>
#include "../HeptaTrade/execution/execution_coordinator.h"
#include "compat/oms_recover.h"

#include <atomic>
#include <cassert>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdio>
#include <fstream>
#include <future>
#include <iostream>
#include <limits>
#include <map>
#include <mutex>
#include <set>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include <cerrno>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

namespace CoordinatorSyncFault {
extern std::atomic<bool> armed;
extern dev_t device;
extern ino_t inode;
}

namespace hepta_execution_coordinator_test {
std::string TempJournalPath();
IbPlaceOrderCommand MakePlace(
    const std::string& callId, const std::string& agentId = "agent-a");
FlattenPositionCommand MakeFlatten(
    const std::string& callId, double position = 100.0);
AuthoritativeFlattenPlan MakeFlattenPlan(
    const FlattenPositionCommand& command, double position = 100.0);
void TestJournalBeforeSendAndDuplicate();
void TestPresendSyncFailureNeverSendsOrResetsIdentity();
void TestDurableAuthoritativeCorrelationResolution();
void TestDeferredCancelRecoveryResolution();
void TestDurableAuthoritativeCancelResolution();
void TestJournalFailurePreventsBrokerSend();
void TestVenueCorrelationBindsCommandIdentity();
void TestPlaceCallbackUncertaintyAndReliableReject();
void TestIdempotencyIsScopedPerAgentSession();
void TestIdempotencyPayloadAndOperationConflict();
void TestIdempotencyConflictSurvivesReplayAndLegacyHashIsCompatible();
void TestOwnershipAndCancel();
void TestRecoveryBlocksUncertainIntent();
void TestProjectionFailureBlocksFurtherMutations();
void TestCancelProjectionFailureIsUncertain();
void TestFlattenLifecycleRecoveryAndRateProjection();
void TestQualificationExternalFlattenIsExactAndAbsolutelyBounded();
void TestFlattenRejectsSubToleranceOverCloseAndStaleNoop();
void TestFlattenNoopJournalFailureIsUncertain();
void TestFlattenVenueRejectCodeAllowlist();
void TestFlattenOrphanIntentRequiresDurableReconciliation();
void TestFlattenProjectionFailureBlocksAndRecovers();
void TestFlattenCallbackExceptionIsDurablyUncertain();
void TestRevokedSessionOwnerIsFenced();
void TestRecoveryOnlySessionOwnerIsDurableAndAllowsOwnedCancel();
void TestTwoPhaseActivationDurabilityAndRecovery();
void TestSlowVenueDispatchDoesNotHoldCoordinatorLock();
void TestCancelEligibilityPreflightDoesNotHoldCoordinatorLock();
void TestReconnectFenceRefusesVenueDispatchInFlight();
void TestSessionFenceTracksRiskDispatchOutsideCoordinatorLock();
void TestSessionFenceTracksFlattenDispatchOutsideCoordinatorLock();
void TestCompactTerminalUniverseExceedsLegacyEnumerationLimit();
} // namespace hepta_execution_coordinator_test
