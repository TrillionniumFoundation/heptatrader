#include "tool_gateway_runtime_composition.h"

#include <cerrno>
#include <chrono>
#include <iomanip>
#include <sstream>
#include <sys/random.h>
#include <thread>

namespace
{
// Execution and event units are socket-activated independently.  The IB PAPER
// runtime owns a 180-second reconnect budget, while its systemd unit permits
// a 240-second initial start.  Match the larger reviewed bound so IPC
// publication cannot be cut off during a slow first startup; the wait remains
// bounded so a missing peer cannot leave a half-started Gateway accepting
// sessions.
const std::uint64_t kRemoteReadinessTimeoutMs = 240000;
const std::uint64_t kRemoteReadinessPollMs = 100;

std::chrono::steady_clock::time_point ReadinessNow(
    const ToolGatewayRuntimeTestHooks& hooks)
{
    return hooks.readinessNow ? hooks.readinessNow() :
        std::chrono::steady_clock::now();
}

void SleepForReadiness(const ToolGatewayRuntimeTestHooks& hooks,
                       std::chrono::milliseconds duration)
{
    if (hooks.readinessSleep) hooks.readinessSleep(duration);
    else std::this_thread::sleep_for(duration);
}

class FailClosedLocalAuthority : public ExecutionAuthority
{
public:
    ExecutionCommandResult PlaceOrder(const PlaceOrderCommand& command) override
    {
        return Rejected(command.context);
    }

    ExecutionCommandResult CancelOrder(const CancelOrderCommand& command) override
    {
        return Rejected(command.context);
    }

private:
    static ExecutionCommandResult Rejected(const AgentExecutionContext& context)
    {
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Rejected;
        result.commandId = context.toolCallId;
        result.reasonCode = "TOOL_GATEWAY_LOCAL_EXECUTION_FORBIDDEN";
        return result;
    }
};

std::uint64_t NowMs()
{
    return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count());
}

std::string JsonString(const std::string& value)
{
    std::ostringstream output;
    output << '"';
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
    {
        const unsigned char c = static_cast<unsigned char>(*it);
        if (c == '"' || c == '\\') output << '\\' << static_cast<char>(c);
        else if (c == '\b') output << "\\b";
        else if (c == '\f') output << "\\f";
        else if (c == '\n') output << "\\n";
        else if (c == '\r') output << "\\r";
        else if (c == '\t') output << "\\t";
        else if (c < 0x20)
            output << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                   << static_cast<unsigned int>(c) << std::dec;
        else output << static_cast<char>(c);
    }
    output << '"';
    return output.str();
}

bool GenerateToolGatewayEpoch(std::string& epoch)
{
    unsigned char bytes[16];
    std::size_t offset = 0;
    while (offset < sizeof(bytes))
    {
        const ssize_t count = ::getrandom(
            bytes + offset, sizeof(bytes) - offset, 0);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return false;
        offset += static_cast<std::size_t>(count);
    }
    static const char hex[] = "0123456789abcdef";
    epoch = "htgw-v1-";
    for (std::size_t i = 0; i < sizeof(bytes); ++i)
    {
        epoch.push_back(hex[bytes[i] >> 4]);
        epoch.push_back(hex[bytes[i] & 0x0f]);
    }
    return true;
}

const char* CommandStatusName(ExecutionCommandStatus status)
{
    switch (status)
    {
    case ExecutionCommandStatus::Accepted: return "accepted";
    case ExecutionCommandStatus::Rejected: return "rejected";
    case ExecutionCommandStatus::Uncertain: return "uncertain";
    case ExecutionCommandStatus::Duplicate: return nullptr;
    }
    return nullptr;
}

bool EncodeCommandStatus(const ExecutionControlResult& result,
                         const std::string& targetCommandId,
                         std::string& payload,
                         std::string& reason)
{
    if (result.targetCommandId != targetCommandId ||
        result.serviceEpoch.empty() || result.serviceEpoch.size() > 128 ||
        result.serviceFencingGeneration == 0)
    {
        reason = "EXECUTION_COMMAND_STATUS_IDENTITY_MISMATCH";
        return false;
    }
    const char* status = CommandStatusName(result.targetStatus);
    if (status == nullptr)
    {
        reason = "EXECUTION_COMMAND_STATUS_INVALID";
        return false;
    }
    std::ostringstream output;
    output << "{\"authoritative\":true,\"command_id\":"
           << JsonString(result.targetCommandId)
           << ",\"command_status\":" << JsonString(status)
           << ",\"order_id\":" << result.orderId
           << ",\"reason_code\":" << JsonString(result.reasonCode)
           << ",\"execution_service_epoch\":"
           << JsonString(result.serviceEpoch)
           << ",\"execution_service_fencing_generation\":"
           << result.serviceFencingGeneration << '}';
    payload = output.str();
    if (payload.size() > 1024)
    {
        payload.clear();
        reason = "EXECUTION_COMMAND_STATUS_RESPONSE_TOO_LARGE";
        return false;
    }
    reason.clear();
    return true;
}
}

bool ToolGatewayRuntimeComposition::ValidateExternalAuthoritativeHealth(
    const std::string& payload,
    std::uint32_t& authorizedConnectorCount)
{
    static const std::string prefix =
        "{\"source\":\"IB\",\"authoritative\":true,"
        "\"paper_order_mode\":\"EXTERNAL_P1_CANARY_LMT_DAY\","
        "\"authorized_connector_count\":";
    if (payload.size() != prefix.size() + 2 ||
        payload.compare(0, prefix.size(), prefix) != 0 ||
        payload[payload.size() - 1] != '}' ||
        (payload[prefix.size()] != '0' && payload[prefix.size()] != '1'))
        return false;
    authorizedConnectorCount = static_cast<std::uint32_t>(
        payload[prefix.size()] - '0');
    return true;
}

ToolGatewayRuntimeComposition::ToolGatewayRuntimeComposition(
    const ExecutionGatewayRuntimeConfig& execution,
    const AgentOsRuntimeConfig& agentOs,
    const ToolGatewaySessionPolicy& sessionPolicy,
    const ToolGatewayRuntimeTestHooks& testHooks)
    : m_executionConfig(execution),
      m_agentOsConfig(agentOs),
      m_sessionPolicy(sessionPolicy),
      m_testHooks(testHooks),
      m_startupCancellationProbe(testHooks.startupCancellationProbe),
      m_localEvents(1024),
      m_fenceSequence(0)
{
}

ToolGatewayRuntimeComposition::~ToolGatewayRuntimeComposition()
{
    Stop();
}

void ToolGatewayRuntimeComposition::SetStartupCancellationProbe(
    const std::function<bool()>& cancellationProbe)
{
    // This setter is intentionally a pre-Start configuration boundary. A
    // steady-state runtime has a separate shutdown path (the owner loop
    // consumes signals), and replacing the callback concurrently with the
    // bounded readiness state machine would race startup. Callers therefore
    // install it before invoking Start, on the same owner thread.
    if (m_startAttempted || IsRunning()) return;
    m_startupCancellationProbe = cancellationProbe;
}

bool ToolGatewayRuntimeComposition::StartupCancellationRequested(
    std::string& reason) const
{
    if (!m_startupCancellationProbe) return false;
    bool requested = false;
    try
    {
        requested = m_startupCancellationProbe();
    }
    catch (...)
    {
        reason = "TOOL_GATEWAY_STARTUP_CANCEL_PROBE_FAILED";
        return true;
    }
    if (!requested) return false;
    reason = "TOOL_GATEWAY_STARTUP_CANCELLED";
    return true;
}

bool ToolGatewayRuntimeComposition::AbortStartupIfCancelled(
    std::string& reason)
{
    if (!StartupCancellationRequested(reason)) return false;
    // Agent OS is not started until the remote readiness barrier succeeds, so
    // Stop() here only tears down the authenticated IPC clients/relay and any
    // partially-built local composition. It cannot create orders or sessions.
    Stop();
    return true;
}

bool ToolGatewayRuntimeComposition::Start(std::string& reason)
{
    if (IsRunning())
    {
        reason = "TOOL_GATEWAY_ALREADY_RUNNING";
        return false;
    }
    if (m_startAttempted)
    {
        reason = "TOOL_GATEWAY_START_ALREADY_ATTEMPTED";
        return false;
    }
    // Freeze the owner-supplied startup probe before any startup work begins.
    // Stop() clears this boundary after a failed/cancelled attempt, allowing
    // the normal explicit restart path to configure a fresh probe.
    m_startAttempted = true;
    if (StartupCancellationRequested(reason))
    {
        Stop();
        return false;
    }
    if (!GenerateToolGatewayEpoch(m_gatewayEpoch))
    {
        reason = "TOOL_GATEWAY_EPOCH_GENERATION_FAILED";
        Stop();
        return false;
    }
    if (!m_executionConfig.Enabled())
    {
        reason = "TOOL_GATEWAY_REMOTE_EXECUTION_REQUIRED";
        Stop();
        return false;
    }

    m_failClosedLocalAuthority.reset(new FailClosedLocalAuthority());
    m_executionGateway.reset(new ExecutionGatewayRuntimeComposition(
        *m_failClosedLocalAuthority, m_localEvents, m_executionConfig));
    if (!m_executionGateway->Start(reason))
    {
        Stop();
        return false;
    }
    if (AbortStartupIfCancelled(reason))
    {
        Stop();
        return false;
    }
    if (!WaitForRemoteReadiness(reason))
    {
        Stop();
        return false;
    }
    if (AbortStartupIfCancelled(reason))
    {
        Stop();
        return false;
    }

    TradingToolReadCallbacks reads;
    const std::function<bool(const std::string&, const TradingToolSession&,
        const TradingToolCall&, std::string&, std::string&)> remoteRead =
        [this](const std::string& query, const TradingToolSession& session,
               const TradingToolCall& call, std::string& payload,
               std::string& callbackReason) {
            ExecutionReadCommand command;
            command.context = session.executionContext;
            command.query = query;
            command.instrument = call.instrument;
            const ExecutionCommandResult result =
                m_executionGateway->ReadAuthoritativeState(command);
            if (result.status != ExecutionCommandStatus::Accepted)
            {
                callbackReason = result.detail.empty() ? result.reasonCode : result.detail;
                return false;
            }
            if (result.detail.empty())
            {
                callbackReason = "AUTHORITATIVE_READ_EMPTY";
                return false;
            }
            payload = result.detail;
            callbackReason.clear();
            return true;
        };
    reads.marketGetQuote = [remoteRead](const TradingToolSession& session,
        const TradingToolCall& call, std::string& payload, std::string& reason) {
        return remoteRead("market.get_quote", session, call, payload, reason);
    };
    reads.accountGetSummary = [remoteRead](const TradingToolSession& session,
        const TradingToolCall& call, std::string& payload, std::string& reason) {
        return remoteRead("account.get_summary", session, call, payload, reason);
    };
    reads.portfolioListPositions = [remoteRead](const TradingToolSession& session,
        const TradingToolCall& call, std::string& payload, std::string& reason) {
        return remoteRead("portfolio.list_positions", session, call, payload, reason);
    };
    reads.ordersList = [remoteRead](const TradingToolSession& session,
        const TradingToolCall& call, std::string& payload, std::string& reason) {
        return remoteRead("orders.list", session, call, payload, reason);
    };
    reads.executionGetCommandStatus =
        [this](const TradingToolSession& session,
               const TradingToolCall& call,
               std::string& payload,
               std::string& callbackReason) {
            ExecutionControlCommand command;
            command.context = session.executionContext;
            command.targetCommandId = call.targetCommandId;
            const ExecutionControlResult result =
                m_executionGateway->QueryCommandStatus(command);
            if (result.status != ExecutionCommandStatus::Accepted)
            {
                callbackReason = result.reasonCode.empty() ?
                    "EXECUTION_COMMAND_STATUS_UNAVAILABLE" :
                    result.reasonCode;
                return false;
            }
            return EncodeCommandStatus(
                result, call.targetCommandId, payload, callbackReason);
        };
    reads.riskGetLimits = [remoteRead](const TradingToolSession& session,
        const TradingToolCall& call, std::string& payload, std::string& reason) {
        return remoteRead("risk.get_limits", session, call, payload, reason);
    };
    reads.riskPreviewOrder = [this](const TradingToolSession& session,
        const TradingToolCall& call, std::string& payload, std::string& reason) {
        PlaceOrderCommand command;
        command.context = session.executionContext;
        command.contract = call.ibContract;
        command.order = call.ibOrder;
        command.instrument = call.instrument;
        command.timeInForce = call.timeInForce;
        command.referencePrice = call.referencePrice;
        command.expiresAtMs = call.expiresAtMs;
        const ExecutionCommandResult result =
            m_executionGateway->PreviewOrder(command);
        if (result.status != ExecutionCommandStatus::Accepted)
        {
            reason = result.detail.empty() ? result.reasonCode : result.detail;
            return false;
        }
        if (result.detail.empty())
        {
            reason = "AUTHORITATIVE_PREVIEW_EMPTY";
            return false;
        }
        payload = result.detail;
        reason.clear();
        return true;
    };
    reads.riskPreviewFlatten =
        [this](const TradingToolSession& session,
               const TradingToolCall& call, std::string& payload,
               std::string& reason) {
            FlattenPositionCommand command;
            command.context = session.executionContext;
            command.contract = call.ibContract;
            command.instrument = call.instrument;
            const ExecutionCommandResult result =
                m_executionGateway->PreviewFlattenPosition(command);
            if (result.status != ExecutionCommandStatus::Accepted)
            {
                reason = result.detail.empty() ?
                    result.reasonCode : result.detail;
                return false;
            }
            if (result.detail.empty())
            {
                reason = "AUTHORITATIVE_PREVIEW_EMPTY";
                return false;
            }
            payload = result.detail;
            reason.clear();
            return true;
        };
    reads.eventsWait = [this](const TradingToolSession& session,
                              const TradingToolCall& call,
                              std::string& payload,
                              std::string& callbackReason) {
        ExecutionEvent event;
        if (!m_executionGateway->WaitNext(session.executionContext,
                call.afterEventSequence, call.waitTimeoutMs, event, callbackReason))
            return false;
        payload = ExecutionEventHub::ToJson(event);
        return true;
    };
    reads.systemGetHealth = [this](const TradingToolSession& session,
                                   const TradingToolCall&,
                                   std::string& payload,
                                   std::string& callbackReason) {
        ExecutionServiceIdentity identity;
        std::string probeReason;
        const bool configured =
            m_executionGateway && m_executionGateway->Enabled();
        const bool ready = configured &&
            m_executionGateway->ProbeRemoteService(identity, probeReason);
        std::string authoritativeHealth;
        std::uint32_t authorizedConnectorCount = 0;
        if (m_executionConfig.externalP1CanaryLimitDay)
        {
            if (!ready)
            {
                callbackReason = probeReason.empty() ?
                    "EXECUTION_AUTHORITATIVE_HEALTH_UNAVAILABLE" :
                    probeReason;
                return false;
            }
            ExecutionReadCommand command;
            command.context = session.executionContext;
            command.query = "system.get_health";
            const ExecutionCommandResult result =
                m_executionGateway->ReadAuthoritativeState(command);
            if (result.status != ExecutionCommandStatus::Accepted)
            {
                callbackReason = result.reasonCode.empty() ?
                    "EXECUTION_AUTHORITATIVE_HEALTH_UNAVAILABLE" :
                    result.reasonCode;
                return false;
            }
            authoritativeHealth = result.detail;
            if (authoritativeHealth.empty())
            {
                callbackReason = "EXECUTION_AUTHORITATIVE_HEALTH_EMPTY";
                return false;
            }
            if (!ValidateExternalAuthoritativeHealth(
                    authoritativeHealth, authorizedConnectorCount))
            {
                callbackReason = "EXECUTION_AUTHORITATIVE_HEALTH_INVALID";
                return false;
            }
        }
        std::ostringstream output;
        output << "{\"gateway_ready\":" << (IsRunning() ? "true" : "false")
               << ",\"tool_gateway_epoch\":"
               << JsonString(m_gatewayEpoch)
               << ",\"remote_execution\":" << (configured ? "true" : "false")
               << ",\"remote_execution_configured\":"
               << (configured ? "true" : "false")
               << ",\"remote_execution_ready\":" << (ready ? "true" : "false")
               << ",\"execution_mode\":\""
               << (m_executionGateway ? m_executionGateway->ModeName() : "DISABLED")
               << "\",\"execution_service_epoch\":"
               << JsonString(ready ? identity.serviceEpoch : std::string())
               << ",\"execution_service_fencing_generation\":"
               << (ready ? identity.serviceFencingGeneration : 0)
               << ",\"remote_execution_reason\":"
               << JsonString(ready ? std::string() :
                    (probeReason.empty() ? "REMOTE_EXECUTION_NOT_READY" : probeReason))
               << ",\"read_model\":\"execution_authoritative_v1\",\"paper_template_enabled\":"
               << (m_sessionPolicy.PaperEnabled() ? "true" : "false");
        if (m_executionConfig.externalP1CanaryLimitDay)
            output << ",\"authorized_connector_count\":"
                   << authorizedConnectorCount
                   << ",\"execution_authoritative_health\":"
                   << authoritativeHealth;
        output << "}";
        payload = output.str();
        callbackReason.clear();
        return true;
    };

    if (AbortStartupIfCancelled(reason))
    {
        Stop();
        return false;
    }

    TradingToolTradeCallbacks trades;
    trades.flattenPosition =
        [this](const TradingToolSession& session,
               const TradingToolCall& call) {
            FlattenPositionCommand command;
            command.context = session.executionContext;
            command.contract = call.ibContract;
            command.instrument = call.instrument;
            command.previewPermit = call.previewPermit;
            return m_executionGateway->FlattenPosition(command);
        };
    m_registry.reset(new TradingToolRegistry(
        m_executionGateway->Authority(), reads, trades));
    m_host.reset(new TradingToolHost(
        *m_registry, m_localDecisionLeases,
        [this](const TradingToolSession& session, const TradingToolCall&,
               std::string& readinessReason) {
            if (!m_executionGateway || !m_executionGateway->Enabled() ||
                session.environment != "PAPER" || !m_sessionPolicy.PaperEnabled())
            {
                readinessReason = "REMOTE_EXECUTION_NOT_READY";
                return false;
            }
            ExecutionServiceIdentity identity;
            if (!m_executionGateway->ProbeRemoteService(identity, readinessReason))
            {
                if (readinessReason.empty())
                    readinessReason = "REMOTE_EXECUTION_NOT_READY";
                return false;
            }
            return true;
        }));
    m_host->SetSessionRevokedObserver(
        [this](const TradingToolHostSessionBinding& binding,
               const std::string& revokeReason,
               std::string& failureReason) {
            return FenceRevokedOwner(binding, revokeReason, failureReason);
        });
    m_host->SetRecoveryControlAuthority(m_executionGateway.get());

    m_agentOs.reset(new AgentOsRuntimeComposition(
        *m_host, m_agentOsConfig,
        [this](const std::string& issuer,
               const TradingToolHostSessionBinding& binding,
               std::string& authorizationReason) {
            return m_sessionPolicy.Authorize(issuer, binding, authorizationReason);
        }));
    if (m_rootCustodianUidTestOverride)
        m_agentOs->Supervisor().SetRootCustodianUidForTests(
            m_rootCustodianUidForTests);
    if (AbortStartupIfCancelled(reason))
    {
        Stop();
        return false;
    }
    if (!m_agentOs->StartToolServer(reason))
    {
        Stop();
        return false;
    }
    if (AbortStartupIfCancelled(reason))
    {
        Stop();
        return false;
    }
    if (!m_agentOs->StartSupervisor(
            [this](const SessionSupervisorRequest& request,
                   TradingToolHostSessionBinding& binding,
                   std::string& resolveReason) {
                return m_sessionPolicy.Resolve(request, binding, resolveReason);
            }, reason))
    {
        Stop();
        return false;
    }
    if (AbortStartupIfCancelled(reason))
    {
        Stop();
        return false;
    }
    reason.clear();
    return true;
}

bool ToolGatewayRuntimeComposition::WaitForRemoteReadiness(
    std::string& reason)
{
    if (AbortStartupIfCancelled(reason)) return false;
    if (!m_executionGateway || !m_executionGateway->Enabled())
    {
        reason = "TOOL_GATEWAY_REMOTE_EXECUTION_REQUIRED";
        return false;
    }
    const std::chrono::steady_clock::time_point deadline =
        ReadinessNow(m_testHooks) +
        std::chrono::milliseconds(kRemoteReadinessTimeoutMs);
    std::string lastReason;
    for (;;)
    {
        if (AbortStartupIfCancelled(reason)) return false;
        ExecutionServiceIdentity identity;
        if (m_executionGateway->ProbeRemoteService(identity, lastReason))
        {
            if (AbortStartupIfCancelled(reason)) return false;
            reason.clear();
            return true;
        }
        if (AbortStartupIfCancelled(reason)) return false;
        if (ReadinessNow(m_testHooks) >= deadline) break;
        SleepForReadiness(m_testHooks,
            std::chrono::milliseconds(kRemoteReadinessPollMs));
        if (AbortStartupIfCancelled(reason)) return false;
    }
    reason = "TOOL_GATEWAY_EXECUTION_EVENT_READINESS_TIMEOUT";
    if (!lastReason.empty()) reason += ":" + lastReason;
    return false;
}

void ToolGatewayRuntimeComposition::Stop()
{
    if (m_agentOs) m_agentOs->Stop();
    m_agentOs.reset();
    m_host.reset();
    m_registry.reset();
    m_executionGateway.reset();
    m_failClosedLocalAuthority.reset();
    m_startAttempted = false;
}

bool ToolGatewayRuntimeComposition::IsRunning() const
{
    return m_agentOs && m_agentOs->ToolServer().IsRunning() &&
        m_agentOs->Supervisor().IsRunning();
}

bool ToolGatewayRuntimeComposition::ReapExpired(
    std::uint64_t nowMs,
    std::size_t& reaped,
    std::string& reason)
{
    if (!m_agentOs || !m_agentOs->Supervisor().IsRunning())
    {
        reaped = 0;
        reason = "TOOL_GATEWAY_SUPERVISOR_NOT_RUNNING";
        return false;
    }
    return m_agentOs->Supervisor().ReapExpired(nowMs, reaped, reason);
}

void ToolGatewayRuntimeComposition::SetRootCustodianUidForTests(
    std::uint32_t uid)
{
    if (IsRunning()) return;
    m_rootCustodianUidForTests = uid;
    m_rootCustodianUidTestOverride = true;
}

bool ToolGatewayRuntimeComposition::FenceRevokedOwner(
    const TradingToolHostSessionBinding& binding,
    const std::string& reasonCode,
    std::string& failureReason)
{
    if (!m_executionGateway || !m_executionGateway->Enabled())
    {
        failureReason = "REMOTE_EXECUTION_NOT_READY";
        return false;
    }
    ExecutionControlCommand command;
    command.context = binding.session.executionContext;
    command.context.executionDomain = binding.executionDomain;
    std::ostringstream callId;
    callId << "gateway-owner-fence-" << NowMs() << '-'
           << m_fenceSequence.fetch_add(1) << '-' << reasonCode.size();
    command.context.toolCallId = callId.str();
    const ExecutionControlResult result =
        m_executionGateway->FenceSessionOwner(command);
    if (result.status != ExecutionCommandStatus::Accepted)
    {
        failureReason = result.reasonCode.empty() ?
            "SESSION_REMOTE_FENCE_PENDING" : result.reasonCode;
        return false;
    }
    failureReason.clear();
    return true;
}
