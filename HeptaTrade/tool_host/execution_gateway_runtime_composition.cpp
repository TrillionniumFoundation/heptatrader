#include "execution_gateway_runtime_composition.h"

#include <cctype>

namespace
{
std::string OwnerKey(const AgentExecutionContext& owner)
{
    return std::to_string(owner.executionDomain.size()) + ":" + owner.executionDomain +
        std::to_string(owner.agentId.size()) + ":" + owner.agentId + owner.sessionId;
}

bool ValidIdentity(const ExecutionServiceIdentity& identity)
{
    return !identity.serviceEpoch.empty() && identity.serviceEpoch.size() <= 128 &&
        identity.serviceFencingGeneration != 0;
}

bool SameIdentity(const ExecutionServiceIdentity& left,
                  const ExecutionServiceIdentity& right)
{
    return left.serviceEpoch == right.serviceEpoch &&
        left.serviceFencingGeneration == right.serviceFencingGeneration;
}

ExecutionServiceIdentity EventProvenance(const ExecutionEvent& event)
{
    ExecutionServiceIdentity identity;
    identity.serviceEpoch = event.upstreamServiceEpoch;
    identity.serviceFencingGeneration = event.upstreamServiceFencingGeneration;
    return identity;
}

bool IsResyncControlEvent(const ExecutionEvent& event)
{
    if (event.status != "AuthoritativeResyncRequired") return false;
    if (event.type == "system.execution_service_identity_changed")
        return event.venue == "EXECUTION_SERVICE" &&
            event.reasonCode == "EXECUTION_EVENT_SERVICE_IDENTITY_CHANGED";
    if (event.type == "system.execution_stream_gap")
        return event.venue == "EXECUTION_SERVICE" &&
            event.reasonCode == "EXECUTION_EVENT_GAP";
    if (event.type == "system.execution_stream_epoch_changed")
        return event.venue == "EXECUTION_SERVICE" &&
            event.reasonCode == "EXECUTION_EVENT_STREAM_EPOCH_CHANGED";
    if (event.type == "system.execution_gateway_local_gap")
        return event.venue == "EXECUTION_GATEWAY" &&
            event.reasonCode == "EXECUTION_GATEWAY_LOCAL_EVENT_GAP";
    return false;
}

bool IsIdentityMismatchReason(const std::string& reason)
{
    return reason == "EXECUTION_SERVICE_EPOCH_MISMATCH" ||
        reason == "EXECUTION_SERVICE_EPOCH_CHANGED";
}

bool IsPaperDomain(const std::string& value)
{
    static const std::string prefix = "PAPER:";
    if (value == "PAPER") return true;
    if (value.size() <= prefix.size() ||
        value.compare(0, prefix.size(), prefix) != 0)
        return false;
    const std::string suffix = value.substr(prefix.size());
    if (suffix.empty() || suffix.size() > 32 ||
        suffix[0] < 'a' || suffix[0] > 'z')
        return false;
    for (std::size_t i = 1; i < suffix.size(); ++i)
    {
        const unsigned char byte =
            static_cast<unsigned char>(suffix[i]);
        if (!std::islower(byte) && !std::isdigit(byte) && byte != '-')
            return false;
    }
    return true;
}
}

ExecutionGatewayRuntimeComposition::ExecutionGatewayRuntimeComposition(
    ExecutionAuthority& localAuthority, ExecutionEventHub& localEventHub,
    const ExecutionGatewayRuntimeConfig& config,
    const ExecutionGatewayRuntimeTestHooks& testHooks)
    : m_localAuthority(localAuthority), m_localEventHub(localEventHub),
      m_config(config), m_testHooks(testHooks)
{
}

void ExecutionGatewayRuntimeComposition::NotifyTestStage(const char* stage) const
{
    if (m_testHooks.onStage) m_testHooks.onStage(stage);
}

bool ExecutionGatewayRuntimeComposition::Start(std::string& reason)
{
    if (!m_config.Validate(reason)) return false;
    if (!m_config.Enabled())
    {
        reason.clear();
        return true;
    }
    const std::set<std::uint32_t> serviceUid{m_config.executionServiceUid};
    m_executionClient.reset(new UnixExecutionServiceClient(
        m_config.executionSocket, m_config.ioTimeoutMs,
        m_config.maxResponseBytes, serviceUid));
    m_eventClient.reset(new UnixExecutionEventFeedClient(
        m_config.eventSocket, m_config.ioTimeoutMs,
        m_config.maxResponseBytes, serviceUid));
    m_relay.reset(new ExecutionEventRelay(m_localEventHub,
        [this](const ExecutionEventFeedRequest& request) {
            return m_eventClient->Wait(request);
        }));
    reason.clear();
    return true;
}

bool ExecutionGatewayRuntimeComposition::Enabled() const
{
    return m_config.Enabled() && m_executionClient && m_eventClient && m_relay;
}

bool ExecutionGatewayRuntimeComposition::ProbeRemoteService(
    ExecutionServiceIdentity& identity,
    std::string& reason)
{
    identity = ExecutionServiceIdentity();
    if (!Enabled())
    {
        reason = "REMOTE_EXECUTION_DISABLED";
        return false;
    }
    return ResolveRemoteIdentity(identity, reason);
}

const char* ExecutionGatewayRuntimeComposition::ModeName() const
{
    return Enabled() ? m_config.ModeName() : "LOCAL";
}

bool ExecutionGatewayRuntimeComposition::ContextAllowed(
    const AgentExecutionContext& context) const
{
    if (m_config.mode == ExecutionGatewayMode::Paper)
        return context.venue == "IB" && IsPaperDomain(context.executionDomain);
    if (m_config.mode == ExecutionGatewayMode::Simulator)
        return context.venue == "SIMULATOR" &&
            context.executionDomain.compare(0, 4, "SIM:") == 0;
    return true;
}

ExecutionCommandResult ExecutionGatewayRuntimeComposition::ContextRejected(
    const AgentExecutionContext& context) const
{
    ExecutionCommandResult result;
    result.status = ExecutionCommandStatus::Rejected;
    result.commandId = context.toolCallId;
    result.reasonCode = "EXECUTION_GATEWAY_CONTEXT_MISMATCH";
    return result;
}

ExecutionControlResult ExecutionGatewayRuntimeComposition::ContextRejected(
    const ExecutionControlCommand& command) const
{
    ExecutionControlResult result;
    result.status = ExecutionCommandStatus::Rejected;
    result.commandId = command.context.toolCallId;
    result.targetCommandId = command.targetCommandId;
    result.reasonCode = "EXECUTION_GATEWAY_CONTEXT_MISMATCH";
    return result;
}

ExecutionAuthority& ExecutionGatewayRuntimeComposition::Authority()
{
    return Enabled() || m_config.mutationToolsEnabled ?
        static_cast<ExecutionAuthority&>(*this) : m_localAuthority;
}

ExecutionCommandResult ExecutionGatewayRuntimeComposition::PlaceOrder(
    const PlaceOrderCommand& command)
{
    if (Enabled() && !ContextAllowed(command.context)) return ContextRejected(command.context);
    if (!Enabled())
    {
        if (m_config.mutationToolsEnabled)
            return RemoteIdentityRejected(
                command.context,
                "EXECUTION_GATEWAY_REMOTE_REQUIRED_FOR_MUTATION_TOOLS");
        return m_localAuthority.PlaceOrder(command);
    }
    ExecutionServiceIdentity identity;
    std::string reason;
    if (!ResolveRemoteIdentity(identity, reason))
        return RemoteIdentityRejected(command.context, reason);
    NotifyTestStage("after_place_identity_resolved");
    const ExecutionCommandResult result =
        m_executionClient->PlaceIbOrderWithIdentity(command, identity);
    if (IsIdentityMismatchReason(result.reasonCode))
        InvalidateRemoteIdentity(identity);
    return result;
}

ExecutionCommandResult ExecutionGatewayRuntimeComposition::CancelOrder(
    const CancelOrderCommand& command)
{
    if (Enabled() && !ContextAllowed(command.context)) return ContextRejected(command.context);
    if (!Enabled())
    {
        if (m_config.mutationToolsEnabled)
            return RemoteIdentityRejected(
                command.context,
                "EXECUTION_GATEWAY_REMOTE_REQUIRED_FOR_MUTATION_TOOLS");
        return m_localAuthority.CancelOrder(command);
    }
    ExecutionServiceIdentity identity;
    std::string reason;
    if (!ResolveRemoteIdentity(identity, reason))
        return RemoteIdentityRejected(command.context, reason);
    const ExecutionCommandResult result =
        m_executionClient->CancelIbOrderWithIdentity(command, identity);
    if (IsIdentityMismatchReason(result.reasonCode))
        InvalidateRemoteIdentity(identity);
    return result;
}

ExecutionCommandResult ExecutionGatewayRuntimeComposition::FlattenPosition(
    const FlattenPositionCommand& command)
{
    if (Enabled() && !ContextAllowed(command.context))
        return ContextRejected(command.context);
    if (!Enabled())
        return RemoteIdentityRejected(
            command.context,
            "EXECUTION_GATEWAY_REMOTE_REQUIRED_FOR_MUTATION_TOOLS");
    ExecutionServiceIdentity identity;
    std::string reason;
    if (!ResolveRemoteIdentity(identity, reason))
        return RemoteIdentityRejected(command.context, reason);
    const ExecutionCommandResult result =
        m_executionClient->FlattenPositionWithIdentity(
            command, identity);
    if (IsIdentityMismatchReason(result.reasonCode))
        InvalidateRemoteIdentity(identity);
    return result;
}

ExecutionCommandResult ExecutionGatewayRuntimeComposition::RemoteIdentityRejected(
    const AgentExecutionContext& context,
    const std::string& reason) const
{
    ExecutionCommandResult result;
    result.status = ExecutionCommandStatus::Rejected;
    result.commandId = context.toolCallId;
    result.reasonCode = reason.empty() ?
        "EXECUTION_GATEWAY_DAEMON_IDENTITY_UNAVAILABLE" : reason;
    return result;
}

ExecutionControlResult ExecutionGatewayRuntimeComposition::RemoteIdentityRejected(
    const ExecutionControlCommand& command,
    const std::string& reason) const
{
    ExecutionControlResult result;
    result.status = ExecutionCommandStatus::Rejected;
    result.commandId = command.context.toolCallId;
    result.targetCommandId = command.targetCommandId;
    result.reasonCode = reason.empty() ?
        "EXECUTION_GATEWAY_DAEMON_IDENTITY_UNAVAILABLE" : reason;
    return result;
}

bool ExecutionGatewayRuntimeComposition::ResolveRemoteIdentity(
    ExecutionServiceIdentity& identity,
    std::string& reason)
{
    std::lock_guard<std::mutex> identityLock(m_remoteIdentityMutex);
    identity = ExecutionServiceIdentity();
    if (!m_executionClient || !m_eventClient)
    {
        reason = "EXECUTION_GATEWAY_DAEMON_IDENTITY_UNAVAILABLE";
        return false;
    }
    ExecutionServiceIdentity mutationIdentity;
    if (!m_executionClient->GetServiceIdentity(mutationIdentity, reason))
    {
        m_remoteIdentity = ExecutionServiceIdentity();
        return false;
    }
    const ExecutionEventReadResult eventIdentity = m_eventClient->GetServiceIdentity();
    if (eventIdentity.status != ExecutionEventReadStatus::ServiceIdentity ||
        !ValidIdentity(eventIdentity.serviceIdentity))
    {
        m_remoteIdentity = ExecutionServiceIdentity();
        reason = eventIdentity.reasonCode.empty() ?
            "EXECUTION_GATEWAY_EVENT_IDENTITY_INVALID" : eventIdentity.reasonCode;
        return false;
    }
    if (!SameIdentity(mutationIdentity, eventIdentity.serviceIdentity))
    {
        // Compare-and-invalidate only the pair observed by this call. A
        // concurrent thread may already have installed the next identity.
        m_executionClient->InvalidateServiceIdentity(mutationIdentity);
        m_remoteIdentity = ExecutionServiceIdentity();
        reason = "EXECUTION_GATEWAY_DAEMON_IDENTITY_MISMATCH";
        return false;
    }
    m_remoteIdentity = mutationIdentity;
    identity = mutationIdentity;
    reason.clear();
    return true;
}

void ExecutionGatewayRuntimeComposition::InvalidateRemoteIdentity(
    const ExecutionServiceIdentity& identity)
{
    {
        std::lock_guard<std::mutex> lock(m_remoteIdentityMutex);
        if (SameIdentity(m_remoteIdentity, identity))
            m_remoteIdentity = ExecutionServiceIdentity();
    }
    if (m_executionClient)
        m_executionClient->InvalidateServiceIdentity(identity);
}

ExecutionControlResult ExecutionGatewayRuntimeComposition::RemoteDisabled(
    const ExecutionControlCommand& command) const
{
    ExecutionControlResult result;
    result.status = ExecutionCommandStatus::Rejected;
    result.commandId = command.context.toolCallId;
    result.targetCommandId = command.targetCommandId;
    result.reasonCode = "EXECUTION_GATEWAY_REMOTE_DISABLED";
    return result;
}

ExecutionControlResult ExecutionGatewayRuntimeComposition::QueryCommandStatus(
    const ExecutionControlCommand& command)
{
    if (Enabled() && !ContextAllowed(command.context)) return ContextRejected(command);
    if (!Enabled()) return RemoteDisabled(command);
    ExecutionServiceIdentity identity;
    std::string reason;
    if (!ResolveRemoteIdentity(identity, reason))
        return RemoteIdentityRejected(command, reason);
    const ExecutionControlResult result =
        m_executionClient->QueryCommandStatusWithIdentity(command, identity);
    if (IsIdentityMismatchReason(result.reasonCode))
        InvalidateRemoteIdentity(identity);
    return result;
}

ExecutionControlResult ExecutionGatewayRuntimeComposition::RecoveryAuditOwner(
    const ExecutionControlCommand& command)
{
    if (Enabled() && !ContextAllowed(command.context))
        return ContextRejected(command);
    if (!Enabled()) return RemoteDisabled(command);
    ExecutionServiceIdentity identity;
    std::string reason;
    if (!ResolveRemoteIdentity(identity, reason))
        return RemoteIdentityRejected(command, reason);
    const ExecutionControlResult result =
        m_executionClient->RecoveryAuditOwnerWithIdentity(command, identity);
    if (IsIdentityMismatchReason(result.reasonCode))
        InvalidateRemoteIdentity(identity);
    return result;
}

ExecutionControlResult
ExecutionGatewayRuntimeComposition::TerminalizeRecoveryOwner(
    const ExecutionControlCommand& command)
{
    if (Enabled() && !ContextAllowed(command.context))
        return ContextRejected(command);
    if (!Enabled()) return RemoteDisabled(command);
    ExecutionServiceIdentity identity;
    std::string reason;
    if (!ResolveRemoteIdentity(identity, reason))
        return RemoteIdentityRejected(command, reason);
    const ExecutionControlResult result =
        m_executionClient->TerminalizeRecoveryOwnerWithIdentity(
            command, identity);
    if (IsIdentityMismatchReason(result.reasonCode))
        InvalidateRemoteIdentity(identity);
    return result;
}

ExecutionControlResult ExecutionGatewayRuntimeComposition::FenceSessionOwner(
    const ExecutionControlCommand& command)
{
    if (Enabled() && !ContextAllowed(command.context)) return ContextRejected(command);
    if (!Enabled()) return RemoteDisabled(command);
    ExecutionServiceIdentity identity;
    std::string reason;
    if (!ResolveRemoteIdentity(identity, reason))
        return RemoteIdentityRejected(command, reason);
    const ExecutionControlResult result =
        m_executionClient->FenceSessionOwnerWithIdentity(command, identity);
    if (IsIdentityMismatchReason(result.reasonCode))
        InvalidateRemoteIdentity(identity);
    return result;
}

ExecutionControlResult ExecutionGatewayRuntimeComposition::ReleaseSessionOwnerFence(
    const ExecutionControlCommand& command)
{
    if (Enabled() && !ContextAllowed(command.context)) return ContextRejected(command);
    if (!Enabled()) return RemoteDisabled(command);
    ExecutionServiceIdentity identity;
    std::string reason;
    if (!ResolveRemoteIdentity(identity, reason))
        return RemoteIdentityRejected(command, reason);
    const ExecutionControlResult result =
        m_executionClient->ReleaseSessionOwnerFenceWithIdentity(command, identity);
    if (IsIdentityMismatchReason(result.reasonCode))
        InvalidateRemoteIdentity(identity);
    return result;
}

ExecutionControlResult ExecutionGatewayRuntimeComposition::ReconcileAuthoritativeState(
    const ExecutionControlCommand& command)
{
    if (Enabled() && !ContextAllowed(command.context)) return ContextRejected(command);
    if (!Enabled()) return RemoteDisabled(command);
    const std::shared_ptr<RelayState> state = StateFor(command.context);
    std::lock_guard<std::mutex> lock(state->mutex);
    ExecutionServiceIdentity identity;
    std::string reason;
    if (!ResolveRemoteIdentity(identity, reason))
        return RemoteIdentityRejected(command, reason);
    const ExecutionControlResult result =
        m_executionClient->ReconcileAuthoritativeStateWithIdentity(command, identity);
    if (IsIdentityMismatchReason(result.reasonCode))
        InvalidateRemoteIdentity(identity);
    if (result.status == ExecutionCommandStatus::Accepted && !result.mutationBlocked)
        m_relay->AcknowledgeAuthoritativeResync(state->cursor, identity);
    return result;
}

ExecutionCommandResult ExecutionGatewayRuntimeComposition::ReadAuthoritativeState(
    const ExecutionReadCommand& command)
{
    if (Enabled() && !ContextAllowed(command.context))
        return ContextRejected(command.context);
    if (!Enabled())
        return RemoteIdentityRejected(command.context, "REMOTE_EXECUTION_DISABLED");
    ExecutionServiceIdentity identity;
    std::string reason;
    if (!ResolveRemoteIdentity(identity, reason))
        return RemoteIdentityRejected(command.context, reason);
    const ExecutionCommandResult result =
        m_executionClient->ReadAuthoritativeStateWithIdentity(command, identity);
    if (IsIdentityMismatchReason(result.reasonCode))
        InvalidateRemoteIdentity(identity);
    return result;
}

ExecutionCommandResult ExecutionGatewayRuntimeComposition::PreviewOrder(
    const PlaceOrderCommand& command)
{
    if (Enabled() && !ContextAllowed(command.context))
        return ContextRejected(command.context);
    if (!Enabled())
        return RemoteIdentityRejected(command.context, "REMOTE_EXECUTION_DISABLED");
    ExecutionServiceIdentity identity;
    std::string reason;
    if (!ResolveRemoteIdentity(identity, reason))
        return RemoteIdentityRejected(command.context, reason);
    const ExecutionCommandResult result =
        m_executionClient->PreviewOrderWithIdentity(command, identity);
    if (IsIdentityMismatchReason(result.reasonCode))
        InvalidateRemoteIdentity(identity);
    return result;
}

ExecutionCommandResult
ExecutionGatewayRuntimeComposition::PreviewFlattenPosition(
    const FlattenPositionCommand& command)
{
    if (Enabled() && !ContextAllowed(command.context))
        return ContextRejected(command.context);
    if (!Enabled())
        return RemoteIdentityRejected(
            command.context, "REMOTE_EXECUTION_DISABLED");
    ExecutionServiceIdentity identity;
    std::string reason;
    if (!ResolveRemoteIdentity(identity, reason))
        return RemoteIdentityRejected(command.context, reason);
    const ExecutionCommandResult result =
        m_executionClient->PreviewFlattenPositionWithIdentity(
            command, identity);
    if (IsIdentityMismatchReason(result.reasonCode))
        InvalidateRemoteIdentity(identity);
    return result;
}

std::shared_ptr<ExecutionGatewayRuntimeComposition::RelayState>
ExecutionGatewayRuntimeComposition::StateFor(const AgentExecutionContext& owner)
{
    const std::string key = OwnerKey(owner);
    std::lock_guard<std::mutex> lock(m_statesMutex);
    std::shared_ptr<RelayState>& state = m_states[key];
    if (!state) state.reset(new RelayState());
    return state;
}

bool ExecutionGatewayRuntimeComposition::ReadEligibleLocalEvent(
    const AgentExecutionContext& owner,
    std::uint64_t afterLocalSequence,
    const ExecutionServiceIdentity& identity,
    RelayState& state,
    ExecutionEvent& event,
    std::string& reason)
{
    std::uint64_t scanSequence = afterLocalSequence;
    for (std::size_t inspected = 0; inspected < 4096; ++inspected)
    {
        const ExecutionEventReadResult local = m_localEventHub.ReadNext(
            owner.executionDomain, owner.agentId, owner.sessionId,
            m_localEventHub.StreamEpoch(), scanSequence, 0);
        if (local.status == ExecutionEventReadStatus::Timeout)
        {
            reason = "EVENT_WAIT_TIMEOUT";
            return false;
        }
        if (local.status == ExecutionEventReadStatus::Gap)
        {
            if (!state.cursor.authoritativeResyncRequired)
            {
                state.cursor.upstreamServiceIdentity = identity;
                state.cursor.upstreamEpoch = identity.serviceEpoch;
                state.cursor.authoritativeResyncRequired = true;
                ExecutionEvent control;
                control.executionDomain = owner.executionDomain;
                control.agentId = owner.agentId;
                control.sessionId = owner.sessionId;
                control.type = "system.execution_gateway_local_gap";
                control.venue = "EXECUTION_GATEWAY";
                control.status = "AuthoritativeResyncRequired";
                control.reasonCode = "EXECUTION_GATEWAY_LOCAL_EVENT_GAP";
                control.upstreamServiceEpoch = identity.serviceEpoch;
                control.upstreamServiceFencingGeneration =
                    identity.serviceFencingGeneration;
                control.upstreamStreamEpoch = identity.serviceEpoch;
                control.upstreamSequence = local.droppedThroughSequence;
                if (m_localEventHub.Publish(control) == 0)
                {
                    reason = "EXECUTION_EVENT_RELAY_PUBLISH_FAILED";
                    return false;
                }
            }
            scanSequence = local.droppedThroughSequence;
            continue;
        }
        if (local.status != ExecutionEventReadStatus::Event)
        {
            reason = local.reasonCode.empty() ?
                "EXECUTION_GATEWAY_LOCAL_EVENT_INVALID" : local.reasonCode;
            return false;
        }

        const ExecutionServiceIdentity provenance = EventProvenance(local.event);
        if (!ValidIdentity(provenance))
        {
            event = local.event;
            reason.clear();
            return true;
        }
        if (!SameIdentity(provenance, identity))
        {
            if (!ValidIdentity(state.cursor.upstreamServiceIdentity))
            {
                state.cursor.upstreamServiceIdentity = provenance;
                state.cursor.upstreamEpoch = provenance.serviceEpoch;
                state.cursor.upstreamSequence = local.event.upstreamSequence;
            }
            scanSequence = local.event.sequence;
            continue;
        }
        if (state.cursor.authoritativeResyncRequired &&
            !IsResyncControlEvent(local.event))
        {
            scanSequence = local.event.sequence;
            continue;
        }
        event = local.event;
        reason.clear();
        return true;
    }
    reason = "EXECUTION_GATEWAY_LOCAL_EVENT_SCAN_LIMIT";
    return false;
}

bool ExecutionGatewayRuntimeComposition::WaitNext(
    const AgentExecutionContext& owner, std::uint64_t afterLocalSequence,
    int timeoutMs, ExecutionEvent& event, std::string& reason)
{
    if (Enabled() && !ContextAllowed(owner))
    {
        reason = "EXECUTION_GATEWAY_CONTEXT_MISMATCH";
        return false;
    }
    if (!Enabled())
    {
        if (m_localEventHub.WaitNext(owner.executionDomain, owner.agentId,
                owner.sessionId, afterLocalSequence, timeoutMs, event))
        {
            reason.clear();
            return true;
        }
        reason = "EVENT_WAIT_TIMEOUT";
        return false;
    }
    const std::shared_ptr<RelayState> state = StateFor(owner);
    std::lock_guard<std::mutex> lock(state->mutex);
    ExecutionServiceIdentity identity;
    if (!ResolveRemoteIdentity(identity, reason)) return false;
    NotifyTestStage("after_wait_identity_resolved");
    if (ReadEligibleLocalEvent(owner, afterLocalSequence, identity, *state,
            event, reason))
        return true;
    ExecutionEventRelayOwner relayOwner;
    relayOwner.executionDomain = owner.executionDomain;
    relayOwner.agentId = owner.agentId;
    relayOwner.sessionId = owner.sessionId;
    relayOwner.serviceIdentity = identity;
    const ExecutionEventRelayStatus status = m_relay->Poll(
        relayOwner, state->cursor, timeoutMs, reason);
    if (status == ExecutionEventRelayStatus::Published ||
        status == ExecutionEventRelayStatus::Gap ||
        status == ExecutionEventRelayStatus::EpochChanged ||
        status == ExecutionEventRelayStatus::ServiceIdentityChanged)
    {
        if (ReadEligibleLocalEvent(owner, afterLocalSequence, identity, *state,
                event, reason)) return true;
        reason = "EXECUTION_EVENT_RELAY_PUBLISH_MISSING";
    }
    else if (status == ExecutionEventRelayStatus::ServiceIdentityMismatch)
    {
        InvalidateRemoteIdentity(identity);
        reason = "EXECUTION_EVENT_SERVICE_IDENTITY_MISMATCH";
    }
    else if (status == ExecutionEventRelayStatus::ResyncRequired)
        reason = "EXECUTION_EVENT_AUTHORITATIVE_RESYNC_REQUIRED";
    else if (status == ExecutionEventRelayStatus::Timeout)
        reason = "EVENT_WAIT_TIMEOUT";
    return false;
}
