#include "unix_session_supervisor_test_support.h"

using namespace hepta_supervisor_test;

int main(int argc, char** argv)
{
    if (argc == 2 && std::string(argv[1]) == "--lifecycle-audit-only")
    { TestDurableAuditBindsOwnerBeforeMutation(); TestAutomaticPaperFinalizationAuditFailures();
      TestUserZeroRecoverySurvivesRestartAndMaintenance(); return 0; }
    if (argc == 2 && std::string(argv[1]) == "--user-recovery-only")
    { TestUserZeroRecoverySurvivesRestartAndMaintenance(); TestConfiguredFxResumeKeepsExactPolicyScope();
      TestUserPaperResumeAuditsBeforeActivation(); TestRootRecoveryQueryIsDurableAndRestartClosed(); return 0; }
    if (argc == 2 && std::string(argv[1]) == "--activation-budget-only")
    { TestFailedPaperActivationRetainsOriginalWorkDeadline(); return 0; }
    if (argc == 2 && std::string(argv[1]) == "--work-budget-only")
    { TestReapPagesAdvancePastUnexpiredOwners(); TestSlowRecoveryOwnerDoesNotBlockUnrelatedOwner();
      TestFailedPaperActivationRetainsOriginalWorkDeadline(); return 0; }
    if (argc == 2 && std::string(argv[1]) == "--audit-reserve-only")
    { TestAuditReserveRejectsRenewalBeforeDurableLeaseMutation(); return 0; }
    assert(argc == 1);
    TestUserPaperResumeAuditsBeforeActivation();
    TestUserRecoveryRestoreSurvivesHeldFxAndExplicitCryptoResume();
    TestUserZeroRecoverySurvivesRestartAndMaintenance();
    TestConfiguredFxResumeKeepsExactPolicyScope();
    TestCryptoAndFxSessionContractScopesRemainDisjoint();
    TestAuditReserveRejectsRenewalBeforeDurableLeaseMutation();
    TestDurableAuditBindsOwnerBeforeMutation();
    TestAutomaticPaperFinalizationAuditFailures();
    TestFailedPaperActivationRetainsOriginalWorkDeadline();
    TestReapPagesAdvancePastUnexpiredOwners();
    TestSlowRecoveryOwnerDoesNotBlockUnrelatedOwner();
	TestPaperFinalizationProtocol();
	TestAuditJournalSecurity();
	TestAuditJournalCacheAndGrowthBounds();
	TestSupervisorPeerCredentialAndLifecycle();
	TestWatchLeaseRestartFailsClosed();
	TestSameHostWatchRestartFencesExactLocalSession();
	TestWatchTransactionReservationRaces();
	TestLeaseCommitCrashConvergenceMatrix();
	TestPaperRecoveryRetriesEventServiceNotReady();
	TestDurableLiveExpiryReap();
	TestWriteAheadActivationFailureFencesOwner();
	TestRejectedRenewFenceRetryUsesExactPredecessorScope();
	TestWatchProvisionRejectsResolverScopeDrift();
	TestRootRecoveryQueryIsDurableAndRestartClosed();
	TestPaperFinalizationMultiOwnerStateMachineAndCrashReplay();
	TestReapSerializesWithRenewCommit();
	TestDurablePendingFenceRestartRecoveryWithReviewedPolicy();
	TestDeterministicGenerationSchedules();
	TestRestartAtEveryPersistedGeneration();
	TestExistingSupervisorSocketIsNeverUnlinked();
	std::cout << "unix_session_supervisor_server_tests: PASS" << std::endl;
	return 0;
}
