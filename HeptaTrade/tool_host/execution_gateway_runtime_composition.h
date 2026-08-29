#pragma once

#include "execution_gateway_runtime_config.h"
#include "execution_event_relay.h"
#include "../execution/execution_authority.h"
#include "../execution/execution_event_feed_client.h"
#include "../execution/unix_execution_service_client.h"

#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <unordered_map>
#include <functional>

// Offline concurrency-test seam only. No configuration, environment, or IPC
// surface can install these hooks in the production Gateway.
struct ExecutionGatewayRuntimeTestHooks
{
    std::function<void(const char*)> onStage;
};

class ExecutionGatewayRuntimeComposition : public ExecutionAuthority,
                                           public ExecutionControlAuthority,
                                           public ExecutionReadAuthority
{
public:
    ExecutionGatewayRuntimeComposition(ExecutionAuthority& localAuthority,
                                       ExecutionEventHub& localEventHub,
                                       const ExecutionGatewayRuntimeConfig& config,
                                       const ExecutionGatewayRuntimeTestHooks& testHooks =
                                           ExecutionGatewayRuntimeTestHooks());

    bool Start(std::string& reason);
    bool Enabled() const;
    bool ProbeRemoteService(ExecutionServiceIdentity& identity,
                            std::string& reason);
    const char* ModeName() const;
    ExecutionAuthority& Authority();

    ExecutionCommandResult PlaceOrder(const PlaceOrderCommand& command) override;
    ExecutionCommandResult CancelOrder(const CancelOrderCommand& command) override;
    ExecutionCommandResult FlattenPosition(
        const FlattenPositionCommand& command) override;
    ExecutionControlResult QueryCommandStatus(const ExecutionControlCommand& command) override;
    ExecutionControlResult FenceSessionOwner(const ExecutionControlCommand& command) override;
    ExecutionControlResult ReleaseSessionOwnerFence(const ExecutionControlCommand& command) override;
    ExecutionControlResult ReconcileAuthoritativeState(const ExecutionControlCommand& command) override;
    ExecutionControlResult RecoveryAuditOwner(
        const ExecutionControlCommand& command) override;
    ExecutionControlResult TerminalizeRecoveryOwner(
        const ExecutionControlCommand& command) override;
    ExecutionCommandResult PreviewOrder(
        const PlaceOrderCommand& command) override;
    ExecutionCommandResult PreviewFlattenPosition(
        const FlattenPositionCommand& command) override;
    ExecutionCommandResult ReadAuthoritativeState(
        const ExecutionReadCommand& command) override;

    bool WaitNext(const AgentExecutionContext& owner, std::uint64_t afterLocalSequence,
                  int timeoutMs, ExecutionEvent& event, std::string& reason);

private:
    struct RelayState
    {
        std::mutex mutex;
        ExecutionEventRelayCursor cursor;
    };
    std::shared_ptr<RelayState> StateFor(const AgentExecutionContext& owner);
    ExecutionControlResult RemoteDisabled(const ExecutionControlCommand& command) const;
    ExecutionCommandResult RemoteIdentityRejected(
        const AgentExecutionContext& context, const std::string& reason) const;
    ExecutionControlResult RemoteIdentityRejected(
        const ExecutionControlCommand& command, const std::string& reason) const;
    bool ResolveRemoteIdentity(ExecutionServiceIdentity& identity,
                               std::string& reason);
    void InvalidateRemoteIdentity(const ExecutionServiceIdentity& identity);
    void NotifyTestStage(const char* stage) const;
    bool ReadEligibleLocalEvent(const AgentExecutionContext& owner,
                                std::uint64_t afterLocalSequence,
                                const ExecutionServiceIdentity& identity,
                                RelayState& state,
                                ExecutionEvent& event,
                                std::string& reason);
    bool ContextAllowed(const AgentExecutionContext& context) const;
    ExecutionCommandResult ContextRejected(const AgentExecutionContext& context) const;
    ExecutionControlResult ContextRejected(const ExecutionControlCommand& command) const;

    ExecutionAuthority& m_localAuthority;
    ExecutionEventHub& m_localEventHub;
    ExecutionGatewayRuntimeConfig m_config;
    ExecutionGatewayRuntimeTestHooks m_testHooks;
    std::unique_ptr<UnixExecutionServiceClient> m_executionClient;
    std::unique_ptr<UnixExecutionEventFeedClient> m_eventClient;
    std::unique_ptr<ExecutionEventRelay> m_relay;
    std::mutex m_remoteIdentityMutex;
    ExecutionServiceIdentity m_remoteIdentity;
    std::mutex m_statesMutex;
    std::unordered_map<std::string, std::shared_ptr<RelayState> > m_states;
};
