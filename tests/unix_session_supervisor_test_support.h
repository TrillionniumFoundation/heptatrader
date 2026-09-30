#pragma once

#include <cstdlib>
#include "../HeptaTrade/tool_host/session_supervisor_protocol.h"
#include "../HeptaTrade/execution/execution_coordinator.h"
#include "../HeptaTrade/tool_host/agent_os_runtime_config.h"
#include "../HeptaTrade/tool_host/execution_gateway_runtime_config.h"
#include "../HeptaTrade/tool_host/tool_gateway_session_policy.h"
#include "../HeptaTrade/tool_host/typed_tool_protocol.h"
#include "../HeptaTrade/tool_host/unix_session_supervisor_server.h"

#include <atomic>
#include <cassert>
#include <arpa/inet.h>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <future>
#include <iostream>
#include <limits>
#include <map>
#include <openssl/evp.h>
#include <sstream>
#include <string>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>
#include <functional>

namespace hepta_supervisor_test {

struct SessionCtlProcessResult
{
    int exitCode = -1;
    std::string standardOutput;
};

class RecoveryControlAuthority : public ExecutionControlAuthority
{
public:
	std::function<ExecutionControlResult(const ExecutionControlCommand&)>
		query;
	std::function<ExecutionControlResult(const ExecutionControlCommand&)>
		ownerAudit;
	std::function<ExecutionControlResult(const ExecutionControlCommand&)>
		fence;
	std::function<ExecutionControlResult(const ExecutionControlCommand&)>
		terminalize;

	ExecutionControlStatusResult QueryCommandStatus(
		const ExecutionControlCommand& command) override
	{
		assert(query);
		return query(command);
	}
	ExecutionControlStatusResult FenceSessionOwner(
		const ExecutionControlCommand& command) override
	{
		return fence ? fence(command) : Rejected(command);
	}
    std::function<ExecutionControlStatusResult(const ExecutionControlCommand&)> release;
	ExecutionControlStatusResult ReleaseSessionOwnerFence(
		const ExecutionControlCommand& command) override
	{
        return release ? release(command) : Rejected(command);
	}
	ExecutionControlStatusResult ReconcileAuthoritativeState(
		const ExecutionControlCommand& command) override
	{
		return Rejected(command);
	}
    ExecutionOwnerAuditResult AuditSessionOwner(
        const ExecutionControlCommand& command) override
    {
        // Normal renewal must not request a recovery ingress transition.
        assert(command.recoveryIngressFence == 0);
        return ownerAudit ? ownerAudit(command) : Rejected(command);
    }
	ExecutionOwnerAuditResult RecoveryAuditOwner(
		const ExecutionControlCommand& command) override
	{
		return ownerAudit ? ownerAudit(command) : Rejected(command);
	}
	ExecutionTerminalResult TerminalizeRecoveryOwner(
		const ExecutionControlCommand& command) override
	{
		return NarrowTerminalResult(
			terminalize ? terminalize(command) : Rejected(command));
	}

private:
	static ExecutionControlResult Rejected(
		const ExecutionControlCommand& command)
	{
		ExecutionControlResult result;
		result.commandId = command.context.toolCallId;
		result.reasonCode = "TEST_CONTROL_OPERATION_UNAVAILABLE";
		return result;
	}
};


std::string Sha256Prefixed(const std::string& value);
std::string HexEncodeTest(const std::string& value);
std::string TerminalOwnerCanonical(const std::vector<SessionSupervisorLeaseRecord>& records);
std::string BuildTerminalEvidence(const std::vector<SessionSupervisorLeaseRecord>& records,
    const SessionSupervisorLeaseRecord& terminalOwner,
    const SessionSupervisorRequest& request, const SessionSupervisorResult& sealed);
std::string RewriteTerminalEvidenceField(const std::string& evidence,
    const std::string& name, const std::string& value);
std::string JsonEncodedStringField(const std::string& json, const std::string& name);
SessionCtlProcessResult RunSessionCtl(const std::vector<std::string>& arguments);
std::string WriteSessionCtlTokenFile(const std::string& token);
std::string WriteSessionCtlEvidenceFile(const std::string& evidence, std::string& directory);
std::string TempPath(const char* pattern);
std::string TempKeyPath();
bool HexNibble(char value, unsigned char& nibble);
std::vector<std::string> AuditPayloads(const std::string& path);
SessionSupervisorResult Call(const std::string& socketPath,
    const SessionSupervisorRequest& request);
int Connect(const std::string& socketPath);
void ExerciseDisconnectPhases(const std::string& socketPath);
void ExerciseSlowIngressIsolation(const std::string& socketPath);

void TestPaperFinalizationProtocol();
void TestAuditJournalSecurity();
void TestAuditJournalCacheAndGrowthBounds();
void TestSupervisorPeerCredentialAndLifecycle();
void TestAuditReserveRejectsRenewalBeforeDurableLeaseMutation();
void TestDurableLiveExpiryReap();
void TestWriteAheadActivationFailureFencesOwner();
void TestRejectedRenewFenceRetryUsesExactPredecessorScope();
void TestReapSerializesWithRenewCommit();
void TestDurablePendingFenceRestartRecoveryWithReviewedPolicy();
void TestDeterministicGenerationSchedules();
void TestRestartAtEveryPersistedGeneration();
void TestUserPaperResumeAuditsBeforeActivation();
void TestWatchLeaseRestartFailsClosed();
void TestSameHostWatchRestartFencesExactLocalSession();
void TestWatchTransactionReservationRaces();
void TestLeaseCommitCrashConvergenceMatrix();
void TestPaperRecoveryRetriesEventServiceNotReady();
void TestWatchProvisionRejectsResolverScopeDrift();
void TestRootRecoveryQueryIsDurableAndRestartClosed();
void TestPaperFinalizationMultiOwnerStateMachineAndCrashReplay();
void TestReapPagesAdvancePastUnexpiredOwners();
void TestSlowRecoveryOwnerDoesNotBlockUnrelatedOwner();
void TestFailedPaperActivationRetainsOriginalWorkDeadline();
void TestExistingSupervisorSocketIsNeverUnlinked();

} // namespace hepta_supervisor_test
