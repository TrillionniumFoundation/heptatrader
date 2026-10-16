#include "execution_coordinator_test_support.h"
using namespace hepta_execution_coordinator_test;

#include "venue_placement_cases.h"
#include "pre_intent_refusal_cases.h"
#include "cancel_uncertainty_cases.h"
#include "flatten_result_cases.h"
#include "oms_recovery_growth_probe.h"

int main(int argc, char** argv)
{
    if (argc == 6 && std::string(argv[1]) == "--generation-inspect")
    {
        char* end = nullptr; const long long expiry = std::strtoll(argv[3], &end, 10);
        if (!end || *end || expiry <= 0) return 2;
        const unsigned long count = std::strtoul(argv[4], &end, 10);
        if (!end || *end || !count || count > 100000) return 2;
        if (std::string(argv[5]) != "sealed" && std::string(argv[5]) != "rebased") return 2;
        return InspectGenerationProbe(argv[2], expiry, static_cast<unsigned>(count), argv[5]);
    }
    if (argc != 1)
    {
        if (argc != 3) return 2;
        const bool generation = std::string(argv[1]) == "--generation-growth";
        if (!generation && std::string(argv[1]) != "--recovery-growth") return 2;
        unsigned count = 0;
        for (const char* p = argv[2]; *p; ++p)
        {
            if (*p < '0' || *p > '9' || count > 10000) return 2;
            count = count * 10 + static_cast<unsigned>(*p - '0');
        }
        if (!count || count > (generation ? 100000U : 20000U)) return 2;
        return generation ? RunGenerationGrowthProbe(count, argv[0]) : RunRecoveryGrowthProbe(count);
    }
    RunGenerationGrowthProbe(16, argv[0]);
    RunRecoveryGrowthProbe(16); // exercise the same producer in normal CTest

    TestCancelUncertaintySurvivesReplayAndRequiresTerminalProof();
    TestCancelPreSendRefusalIsDistinctFromDeferred();
    TestCancelUncertainReceiptFailurePreservesAttempt();
    TestPreIntentRefusalsDoNotRetainCommandIdentities();
    TestBlockedRefusalFloodDoesNotEraseUncertainIdentity();
    TestCoordinatorMeasurementsPreserveExceptionsAndFlattenRejection();
    TestVenuePlacementConstructionAndResultContract();
    TestSlowVenueDispatchDoesNotHoldCoordinatorLock();
    TestCancelEligibilityPreflightDoesNotHoldCoordinatorLock();
    TestReconnectFenceRefusesVenueDispatchInFlight();
    TestAccountNewEntryFencePersistsAcrossSessions();
    TestAccountNewEntryFenceReportsInFlightAndDoesNotResend();
    TestAccountNewEntryFenceJournalFailureFailsClosed();
    TestSessionFenceTracksRiskDispatchOutsideCoordinatorLock();
    TestSessionFenceTracksFlattenDispatchOutsideCoordinatorLock();
    TestCompactTerminalUniverseExceedsLegacyEnumerationLimit();
    TestJournalBeforeSendAndDuplicate();
    TestCashOrderIdentityAndZeroBaseQuantitySurviveRecovery();
    TestPresendSyncFailureNeverSendsOrResetsIdentity();
    TestTwoPhaseActivationDurabilityAndRecovery();
    TestJournalFailurePreventsBrokerSend();
    TestVenueCorrelationBindsCommandIdentity();
    TestPlaceCallbackUncertaintyAndReliableReject();
    TestIdempotencyIsScopedPerAgentSession();
    TestIdempotencyPayloadAndOperationConflict();
    TestIdempotencyConflictSurvivesReplayAndLegacyHashIsCompatible();
    TestOwnershipAndCancel();
    TestRecoveryBlocksUncertainIntent();
    TestProjectionFailureBlocksFurtherMutations();
    TestCancelProjectionFailureIsUncertain();
    TestFlattenLifecycleRecoveryAndRateProjection();
    TestQualificationExternalFlattenIsExactAndAbsolutelyBounded();
    TestFlattenVenueRejectCodeAllowlist();
    TestFlattenOrphanIntentRequiresDurableReconciliation();
    TestFlattenProjectionFailureBlocksAndRecovers();
    TestFlattenCallbackExceptionIsDurablyUncertain();
    TestTypedFlattenResultPreservesDurableUncertainty();
    TestTypedFlattenReasonIsIndependentOfDiagnosticText();
    TestFlattenRejectsSubToleranceOverCloseAndStaleNoop();
    TestFlattenNoopJournalFailureIsUncertain();
    TestRevokedSessionOwnerIsFenced();
    TestRecoveryOnlySessionOwnerIsDurableAndAllowsOwnedCancel();
    TestDurableAuthoritativeCorrelationResolution();
    TestDeferredCancelRecoveryResolution();
    TestDurableAuthoritativeCancelResolution();
    TestCorrelationCancelRecoveryAcceptsCompletedOrderUnknownNumericId();
    TestCorrelationCancelRecoveryIgnoresReusedNumericOrderId();
    std::cout << "execution_coordinator_tests: PASS" << std::endl;
    return 0;
}
