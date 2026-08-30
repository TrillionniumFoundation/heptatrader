#include "execution_service_runtime_composition.h"
#include "execution_coordinator.h"
#include "execution_decision_lease_authority.h"
#include "execution_event_feed_server.h"
#include "unix_execution_service_server.h"
#include "../risk/deterministic_risk_policy.h"
#include <cerrno>
#include <chrono>
#include <climits>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <limits>
#include <locale>
#include <set>
#include <sstream>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>
namespace
{
std::string EscapeJson(const std::string& value)
{
    std::string escaped;
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
    {
        const unsigned char byte = static_cast<unsigned char>(*it);
        if (byte == '"' || byte == '\\') escaped.push_back('\\');
        escaped.push_back(byte < 0x20 ? '?' : static_cast<char>(byte));
    }
    return escaped;
}
ExecutionCommandResult Reject(const AgentExecutionContext& context,
                              const std::string& reasonCode,
                              const std::string& detail,
                              long orderId = -1)
{
    ExecutionCommandResult result;
    result.status = ExecutionCommandStatus::Rejected;
    result.commandId = context.toolCallId;
    result.orderId = orderId;
    result.reasonCode = reasonCode;
    result.detail = detail;
    return result;
}
bool ParsePositiveUnsigned(const std::string& value, std::uint64_t& parsed)
{
    if (value.empty() || (value.size() > 1 && value[0] == '0')) return false;
    std::uint64_t number = 0;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char character = static_cast<unsigned char>(value[i]);
        if (character < static_cast<unsigned char>('0') ||
            character > static_cast<unsigned char>('9'))
            return false;
        const std::uint64_t digit = static_cast<std::uint64_t>(character - '0');
        if (number > (std::numeric_limits<std::uint64_t>::max() - digit) / 10)
            return false;
        number = number * 10 + digit;
    }
    if (number == 0) return false;
    parsed = number;
    return true;
}
bool ReadSmallRegularFile(const std::string& path, std::string& contents, std::string& reason)
{
    const int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0)
    {
        reason = "EXECUTION_FENCE_CREDENTIAL_OPEN_FAILED";
        return false;
    }
    struct stat metadata;
    if (::fstat(fd, &metadata) != 0)
    {
        ::close(fd);
        reason = "EXECUTION_FENCE_CREDENTIAL_UNSAFE";
        return false;
    }
    const bool privateMode = (metadata.st_mode & 07777) == 0400;
    const bool systemdCredentialMode =
        (metadata.st_mode & 07777) == 0440 && metadata.st_uid == 0 && metadata.st_gid == 0;
    if (!S_ISREG(metadata.st_mode) || metadata.st_size <= 0 ||
        metadata.st_size > 256 || (!privateMode && !systemdCredentialMode) ||
        metadata.st_nlink != 1 ||
        (metadata.st_uid != 0 && metadata.st_uid != ::geteuid()))
    {
        ::close(fd);
        reason = "EXECUTION_FENCE_CREDENTIAL_UNSAFE";
        return false;
    }
    contents.assign(static_cast<std::size_t>(metadata.st_size), '\0');
    std::size_t offset = 0;
    while (offset < contents.size())
    {
        const ssize_t count = ::read(fd, &contents[offset], contents.size() - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0)
        {
            ::close(fd);
            reason = "EXECUTION_FENCE_CREDENTIAL_READ_FAILED";
            return false;
        }
        offset += static_cast<std::size_t>(count);
    }
    char extra = 0;
    const ssize_t extraCount = ::read(fd, &extra, 1);
    const int closeResult = ::close(fd);
    if (extraCount != 0 || closeResult != 0)
    {
        reason = "EXECUTION_FENCE_CREDENTIAL_READ_FAILED";
        return false;
    }
    reason.clear();
    return true;
}
bool ValidateOrCreatePrivateFile(const std::string& path, std::string& reason)
{
    struct stat existing;
    const bool exists = ::lstat(path.c_str(), &existing) == 0;
    if (exists && (!S_ISREG(existing.st_mode) || existing.st_uid != ::geteuid() ||
                   (existing.st_mode & 0077) != 0))
    {
        reason = "EXECUTION_JOURNAL_UNSAFE";
        return false;
    }
    if (!exists && errno != ENOENT)
    {
        reason = "EXECUTION_JOURNAL_INSPECTION_FAILED";
        return false;
    }
    const int flags = O_WRONLY | O_APPEND | O_CLOEXEC | O_NOFOLLOW |
        (exists ? 0 : (O_CREAT | O_EXCL));
    const int fd = ::open(path.c_str(), flags, 0600);
    if (fd < 0)
    {
        reason = "EXECUTION_JOURNAL_OPEN_FAILED";
        return false;
    }
    struct stat opened;
    const bool safe = ::fstat(fd, &opened) == 0 && S_ISREG(opened.st_mode) &&
        opened.st_uid == ::geteuid() && (opened.st_mode & 0077) == 0 &&
        ::fchmod(fd, 0600) == 0;
    const int closeResult = ::close(fd);
    if (!safe || closeResult != 0)
    {
        reason = "EXECUTION_JOURNAL_UNSAFE";
        return false;
    }
    reason.clear();
    return true;
}
}
class ExecutionServiceRuntimeComposition::SimulatorPolicyAuthority :
    public ExecutionAuthority,
    public ExecutionControlAuthority,
    public ExecutionReadAuthority
{
public:
    SimulatorPolicyAuthority(ExecutionCoordinator& coordinator,
                             DeterministicExecutionVenue& venue,
                             const ExecutionServiceRuntimeConfig& config)
        : m_coordinator(coordinator), m_venue(venue), m_config(config)
    {
    }
    ExecutionCommandResult PlaceOrder(const PlaceOrderCommand& command) override
    {
        const ExecutionCommandResult rejected = Validate(command.context, -1);
        if (rejected.status == ExecutionCommandStatus::Rejected) return rejected;
        ExecutionCommandResult replay;
        if (m_coordinator.PrecheckPlaceIbOrder(command, replay)) return replay;
        const ExecutionCommandResult eligibility = ValidatePlaceEligibility(command);
        if (eligibility.status == ExecutionCommandStatus::Rejected) return eligibility;
        return m_coordinator.PlaceOrder(command);
    }
    ExecutionCommandResult CancelOrder(const CancelOrderCommand& command) override
    {
        const ExecutionCommandResult rejected = Validate(command.context, command.orderId);
        if (rejected.status == ExecutionCommandStatus::Rejected) return rejected;
        return m_coordinator.CancelOrder(command);
    }
    ExecutionControlResult QueryCommandStatus(
        const ExecutionControlCommand& command) override
    {
        ExecutionControlResult result = BeginControl(command);
        if (result.status == ExecutionCommandStatus::Rejected) return result;
        result.targetCommandId = command.targetCommandId;
        if (!m_coordinator.EnterRecoveryOnlyForControl(command, result))
            return result;
        ExecutionCommandResult target;
        if (!m_coordinator.GetCommandStatus(command.context.agentId,
                command.context.sessionId, command.targetCommandId, target))
        {
            result.status = ExecutionCommandStatus::Rejected;
            result.reasonCode = "EXECUTION_COMMAND_NOT_FOUND";
            return result;
        }
        result.status = ExecutionCommandStatus::Accepted;
        result.targetStatus = target.status;
        result.orderId = target.orderId;
        result.reasonCode = target.reasonCode;
        result.detail = target.detail;
        std::string blockReason;
        result.mutationBlocked = m_coordinator.IsMutationBlocked(&blockReason);
        return result;
    }
    ExecutionControlResult FenceSessionOwner(
        const ExecutionControlCommand& command) override
    {
        ExecutionControlResult result = BeginControl(command);
        if (result.status == ExecutionCommandStatus::Rejected) return result;
        result.affectedCount = m_coordinator.FenceSessionOwner(
            command.context.agentId, command.context.sessionId);
        std::string blockReason;
        result.mutationBlocked = m_coordinator.IsMutationBlocked(&blockReason);
        if (result.mutationBlocked && blockReason == "OMS_SESSION_FENCE_JOURNAL_FAILED")
        {
            result.status = ExecutionCommandStatus::Uncertain;
            result.reasonCode = blockReason;
            return result;
        }
        result.status = ExecutionCommandStatus::Accepted;
        return result;
    }
    ExecutionControlResult RecoveryAuditOwner(
        const ExecutionControlCommand& command) override
    {
        ExecutionControlResult result = BeginControl(command);
        result.ownerAccount = command.context.account; result.ownerExecutionDomain = command.context.executionDomain;
        if (result.status == ExecutionCommandStatus::Rejected) return result;
        if (!m_coordinator.EnterRecoveryOnlyForControl(command, result)) return result;
        const SimulatedRecoveryAuditSnapshot snapshot = m_venue.RecoveryAuditSnapshot();
        result.brokerConnectionEpoch = snapshot.connectionEpoch; result.brokerActiveGeneration = snapshot.generation;
        result.brokerTerminalGeneration = snapshot.generation;
        if (!snapshot.complete)
        {
            result.status = ExecutionCommandStatus::Rejected; result.reasonCode = "SIM_RECOVERY_OWNER_SNAPSHOT_INCOMPLETE";
            return result;
        }
        std::map<std::string, long> correlations = snapshot.activeCorrelations;
        for (std::map<std::string, long>::const_iterator item =
                 snapshot.terminalCorrelations.begin();
             item != snapshot.terminalCorrelations.end(); ++item)
            if (!correlations.insert(*item).second)
            {
                result.status = ExecutionCommandStatus::Rejected; result.reasonCode = "SIM_RECOVERY_OWNER_CORRELATION_CONFLICT";
                return result;
            }
        std::size_t places = 0, cancels = 0, removed = 0;
        std::string reason;
        if (!m_coordinator.ResolveUncertainPlaceCommands(
                correlations, true, places, reason, true) ||
            !m_coordinator.ResolveUncertainCancelCommands(
                snapshot.activeOrderIds, true, snapshot.terminalStatuses,
                snapshot.executionOrderIds, true, cancels, reason) ||
            !m_coordinator.ReconcileOrderOwners(
                snapshot.activeOrderIds, true, removed, reason) ||
            !m_coordinator.AuditRecoveryOwner(
                snapshot.activeOrderIds, true, command.context,
                result.ownerActiveOrderCount,
                result.ownerUncertainCommandCount, reason))
        {
            result.status = ExecutionCommandStatus::Rejected; result.reasonCode = reason.empty() ?
                "SIM_RECOVERY_OWNER_AUDIT_FAILED" : reason;
            return result;
        }
        result.status = ExecutionCommandStatus::Accepted; result.ownerAuditAuthoritative = true;
        result.ownerAuditComplete = true; result.affectedCount = result.ownerActiveOrderCount;
        result.reasonCode = result.ownerActiveOrderCount == 0 ? "RECOVERY_OWNER_ZERO_CONFIRMED" :
            "RECOVERY_OWNER_ACTIVE_ORDERS";
        result.mutationBlocked = m_coordinator.IsMutationBlocked(nullptr);
        return result;
    }
    ExecutionControlResult ReleaseSessionOwnerFence(
        const ExecutionControlCommand& command) override
    {
        ExecutionControlResult result = BeginControl(command);
        if (result.status == ExecutionCommandStatus::Rejected) return result;
        std::size_t removedOwners = 0;
        std::string reason;
        if (!m_coordinator.ReconcileOrderOwners(m_venue.ActiveOrderIds(), true,
                removedOwners, reason) ||
            !m_coordinator.AuditAndReleaseSessionOwnerFence(command.context.agentId,
                command.context.sessionId, true, reason))
        {
            result.status = ExecutionCommandStatus::Rejected;
            result.reasonCode = reason;
            result.affectedCount = removedOwners;
            result.mutationBlocked = m_coordinator.IsMutationBlocked(nullptr);
            return result;
        }
        result.status = ExecutionCommandStatus::Accepted;
        result.affectedCount = removedOwners;
        result.mutationBlocked = m_coordinator.IsMutationBlocked(nullptr);
        return result;
    }
    ExecutionControlResult ReconcileAuthoritativeState(
        const ExecutionControlCommand& command) override
    {
        ExecutionControlResult result = BeginControl(command);
        if (result.status == ExecutionCommandStatus::Rejected) return result;
        std::string reason;
        std::size_t removedOwners = 0;
        std::size_t resolvedCommands = 0;
        if (!m_coordinator.ResolveUncertainPlaceCommands(
                m_venue.ActiveOrderCorrelations(), true, resolvedCommands, reason))
        {
            result.status = ExecutionCommandStatus::Rejected;
            result.reasonCode = reason;
            result.mutationBlocked = true;
            return result;
        }
        if (!m_coordinator.ReconcileOrderOwners(m_venue.ActiveOrderIds(), true,
                removedOwners, reason))
        {
            result.status = ExecutionCommandStatus::Rejected;
            result.reasonCode = reason;
            result.mutationBlocked = true;
            return result;
        }
        m_coordinator.ResolveProjectionBlockAfterAuthoritativeResync();
        result.status = ExecutionCommandStatus::Accepted;
        result.affectedCount = removedOwners + resolvedCommands;
        result.mutationBlocked = m_coordinator.IsMutationBlocked(&reason);
        result.reasonCode = result.mutationBlocked ? reason : std::string();
        return result;
    }
    ExecutionCommandResult PreviewOrder(
        const PlaceOrderCommand& command) override
    {
        const ExecutionCommandResult rejected = Validate(command.context, -1);
        if (rejected.status == ExecutionCommandStatus::Rejected) return rejected;
        const ExecutionCommandResult eligibility = ValidatePlaceEligibility(command);
        if (eligibility.status == ExecutionCommandStatus::Rejected) return eligibility;
        const std::uint64_t now = static_cast<std::uint64_t>(OmsJournal::NowEpochMs());
        const MarketQuoteSnapshot quote =
            m_venue.GetQuoteSnapshot(command.instrument, now);
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        std::ostringstream output;
        output.imbue(std::locale::classic());
        output << "{\"source\":\"SIMULATOR\",\"authoritative\":true,"
               << "\"instrument\":\"" << EscapeJson(command.instrument) << "\","
               << "\"subscription_id\":\"" << EscapeJson(quote.subscriptionId) << "\","
               << "\"observed_at_ms\":" << quote.observedAtMs << ','
               << "\"stale_after_ms\":" << quote.staleAfterMs << ','
               << "\"stale\":false,\"risk_approved\":true}";
        result.detail = output.str();
        return result;
    }
    bool IsDurablePlaceReplay(
        const PlaceOrderCommand& command) const override
    {
        return m_coordinator.IsDurablePlaceReplay(command);
    }
    ExecutionCommandResult ReadAuthoritativeState(
        const ExecutionReadCommand& command) override
    {
        const ExecutionCommandResult rejected = Validate(command.context, -1);
        if (rejected.status == ExecutionCommandStatus::Rejected) return rejected;
        ExecutionCommandResult result;
        result.commandId = command.context.toolCallId;
        result.status = ExecutionCommandStatus::Accepted;
        std::ostringstream output;
        output.imbue(std::locale::classic());
        if (command.query == "market.get_quote")
        {
            const std::uint64_t now = static_cast<std::uint64_t>(OmsJournal::NowEpochMs());
            const MarketQuoteSnapshot quote =
                m_venue.GetQuoteSnapshot(command.instrument, now);
            if (command.instrument.empty() || quote.state == MarketSubscriptionState::Unavailable)
            {
                result.status = ExecutionCommandStatus::Rejected;
                result.reasonCode = "AUTHORITATIVE_QUOTE_UNAVAILABLE";
                return result;
            }
            if (!quote.IsFresh(now))
            {
                result.status = ExecutionCommandStatus::Rejected;
                result.reasonCode = "AUTHORITATIVE_QUOTE_STALE";
                return result;
            }
            output << "{\"source\":\"SIMULATOR\",\"authoritative\":true,"
                   << "\"instrument\":\"" << EscapeJson(command.instrument) << "\","
                   << "\"subscription_id\":\"" << EscapeJson(quote.subscriptionId) << "\","
                   << "\"subscription_state\":\"active\","
                   << "\"observed_at_ms\":" << quote.observedAtMs << ','
                   << "\"stale_after_ms\":" << quote.staleAfterMs << ','
                   << "\"stale\":false,\"bid\":" << quote.bid
                   << ",\"ask\":" << quote.ask << "}";
        }
        else if (command.query == "account.get_summary")
            output << "{\"source\":\"SIMULATOR\",\"authoritative\":true,"
                   << "\"account_complete\":true,\"account\":\"SIM\","
                   << "\"availability\":\"not_applicable\"}";
        else if (command.query == "portfolio.list_positions")
        {
            const std::map<std::string, double> positions = m_venue.Positions();
            output << "{\"source\":\"SIMULATOR\",\"authoritative\":true,\"positions\":[";
            std::size_t count = 0;
            for (std::map<std::string, double>::const_iterator it = positions.begin();
                 it != positions.end() && count < 64; ++it, ++count)
            {
                if (count != 0) output << ',';
                output << "{\"instrument\":\"" << EscapeJson(it->first)
                       << "\",\"quantity\":" << it->second << "}";
            }
            output << "]}";
        }
        else if (command.query == "orders.list")
        {
            const std::set<long> orders = m_venue.ActiveOrderIds();
            output << "{\"source\":\"SIMULATOR\",\"authoritative\":true,\"active_order_ids\":[";
            std::size_t count = 0;
            for (std::set<long>::const_iterator it = orders.begin();
                 it != orders.end() && count < 64; ++it, ++count)
            {
                if (count != 0) output << ',';
                output << *it;
            }
            output << "]}";
        }
        else if (command.query == "risk.get_limits")
        {
            std::string blockReason;
            const bool blocked = m_coordinator.IsMutationBlocked(&blockReason);
            const std::map<std::string, double> positions = m_venue.Positions();
            double grossAbsolutePosition = 0.0;
            for (std::map<std::string, double>::const_iterator it =
                     positions.begin(); it != positions.end(); ++it)
                grossAbsolutePosition += std::fabs(it->second);
            output << "{\"source\":\"SIMULATOR\",\"authoritative\":true,"
                   << "\"mutation_blocked\":" << (blocked ? "true" : "false")
                   << ",\"reason\":\"" << EscapeJson(blockReason) << "\","
                   << "\"order_submission_enabled\":"
                   << (m_config.simulatorOrderSubmissionEnabled ? "true" : "false")
                   << ",\"global_kill_switch\":"
                   << (m_config.simulatorGlobalKillSwitch ? "true" : "false")
                   << ",\"flatten_only\":"
                   << (m_config.simulatorFlattenOnly ? "true" : "false")
                   << ",\"max_order_quantity\":"
                   << m_config.simulatorMaxOrderQuantity
                   << ",\"max_order_notional\":"
                   << m_config.simulatorMaxOrderNotional
                   << ",\"max_orders_per_minute\":"
                   << m_config.simulatorMaxOrdersPerMinute
                   << ",\"max_active_orders\":"
                   << m_config.simulatorMaxActiveOrders
                   << ",\"max_gross_position\":"
                   << m_config.simulatorMaxGrossPosition
                   << ",\"max_price_deviation_bps\":"
                   << m_config.simulatorMaxPriceDeviationBps
                   << ",\"gross_absolute_position\":"
                   << grossAbsolutePosition
                   << ",\"active_order_count\":"
                   << m_venue.ActiveOrderIds().size() << "}";
        }
        else
        {
            result.status = ExecutionCommandStatus::Rejected;
            result.reasonCode = "AUTHORITATIVE_READ_QUERY_UNSUPPORTED";
            return result;
        }
        result.detail = output.str();
        return result;
    }
private:
    ExecutionCommandResult ValidatePlaceEligibility(
        const PlaceOrderCommand& command) const
    {
        if (command.expiresAtMs <= 0 ||
            OmsJournal::NowEpochMs() >= command.expiresAtMs)
            return Reject(command.context, "TOOL_CALL_EXPIRED",
                "order command expired before authoritative preview/place", -1);
        if (command.instrument.empty() || command.contract.symbol.empty() ||
            command.timeInForce != "DAY" ||
            (command.order.action != "BUY" && command.order.action != "SELL") ||
            (command.order.orderType != "MKT" &&
             command.order.orderType != "LMT") ||
            !std::isfinite(command.order.totalQuantity) ||
            command.order.totalQuantity <= 0.0)
            return Reject(command.context, "INVALID_ORDER",
                "normalized order intent is invalid", -1);
        std::string blockReason;
        if (m_coordinator.IsMutationBlocked(&blockReason))
            return Reject(command.context, "MUTATION_BLOCKED", blockReason, -1);
        if (m_coordinator.IsSessionOwnerFenced(
                command.context.agentId, command.context.sessionId))
            return Reject(command.context, "SESSION_OWNER_FENCED",
                "revoked or expired session owner cannot mutate", -1);
        if (m_coordinator.IsSessionOwnerRecoveryOnly(
                command.context.agentId, command.context.sessionId))
            return Reject(command.context, "SESSION_RECOVERY_ONLY",
                "root custodian disabled new entry for this session owner", -1);

        const std::uint64_t now =
            static_cast<std::uint64_t>(OmsJournal::NowEpochMs());
        const MarketQuoteSnapshot quote =
            m_venue.GetQuoteSnapshot(command.instrument, now);
        if (quote.state == MarketSubscriptionState::Unavailable)
            return Reject(command.context, "AUTHORITATIVE_QUOTE_UNAVAILABLE",
                "Execution-owned quote subscription is unavailable", -1);
        if (!quote.IsFresh(now))
            return Reject(command.context, "AUTHORITATIVE_QUOTE_STALE",
                "Execution-owned quote is not fresh", -1);

        const std::map<std::string, double> positions = m_venue.Positions();
        double gross = 0.0;
        for (std::map<std::string, double>::const_iterator it = positions.begin();
             it != positions.end(); ++it) gross += std::fabs(it->second);
        const double current = m_venue.Position(command.instrument);
        const double signedQuantity = command.order.action == "BUY" ?
            command.order.totalQuantity : -command.order.totalQuantity;
        const double projected = gross - std::fabs(current) +
            std::fabs(current + signedQuantity);
        const double authoritativePrice = command.order.action == "BUY" ?
            quote.ask : quote.bid;

        std::vector<std::int64_t> attempts;
        m_coordinator.GetPlaceSendAttemptTimes(
            command.context.account, command.context.executionDomain,
            static_cast<std::int64_t>(now) - 60000, attempts);

        DeterministicRiskLimits limits;
        limits.orderSubmissionEnabled = m_config.simulatorOrderSubmissionEnabled;
        limits.globalKillSwitch = m_config.simulatorGlobalKillSwitch;
        limits.flattenOnly = m_config.simulatorFlattenOnly;
        limits.maxOrderQuantity = m_config.simulatorMaxOrderQuantity;
        limits.maxOrderNotional = m_config.simulatorMaxOrderNotional;
        limits.maxOrdersPerMinute = m_config.simulatorMaxOrdersPerMinute;
        limits.maxActiveOrders = m_config.simulatorMaxActiveOrders;
        limits.maxGrossPosition = m_config.simulatorMaxGrossPosition;
        limits.maxPriceDeviationBps = m_config.simulatorMaxPriceDeviationBps;

        DeterministicRiskContext risk;
        risk.action = command.order.action;
        risk.orderType = command.order.orderType;
        risk.quantity = command.order.totalQuantity;
        risk.valuationPrice = command.order.orderType == "LMT" ?
            command.order.lmtPrice : authoritativePrice;
        risk.submittedPrice = command.order.lmtPrice;
        risk.referencePrice = authoritativePrice;
        risk.ordersInLastMinute = attempts.size();
        risk.activeOrderCount = m_venue.ActiveOrderIds().size();
        risk.grossAbsolutePosition = gross;
        risk.projectedGrossAbsolutePosition = projected;
        risk.exposureReducing = projected < gross;
        risk.quoteFresh = quote.IsFresh(now);
        risk.portfolioSnapshotComplete = true;
        const DeterministicRiskDecision decision =
            DeterministicRiskPolicy::Evaluate(limits, risk);
        if (!decision.allow)
            return Reject(command.context, decision.reasonCode,
                decision.detail, -1);

        ExecutionCommandResult accepted;
        accepted.status = ExecutionCommandStatus::Accepted;
        accepted.commandId = command.context.toolCallId;
        return accepted;
    }
    ExecutionControlResult BeginControl(const ExecutionControlCommand& command) const
    {
        ExecutionControlResult result;
        result.commandId = command.context.toolCallId;
        if (command.context.agentId.empty() || command.context.sessionId.empty() ||
            command.context.toolCallId.empty() || command.context.venue != "SIMULATOR" ||
            command.context.account != "SIM" ||
            command.context.executionDomain.compare(0, 4, "SIM:") != 0 ||
            command.context.allowCancelAny)
        {
            result.status = ExecutionCommandStatus::Rejected;
            result.reasonCode = "SIMULATOR_CONTROL_CONTEXT_REQUIRED";
            return result;
        }
        result.status = ExecutionCommandStatus::Accepted;
        return result;
    }
    ExecutionCommandResult Validate(const AgentExecutionContext& context, long orderId) const
    {
        if (context.agentId.empty() || context.sessionId.empty() ||
            context.toolCallId.empty() || context.venue != "SIMULATOR" ||
            context.account != "SIM" ||
            context.executionDomain.compare(0, 4, "SIM:") != 0)
            return Reject(context, "SIMULATOR_CONTEXT_REQUIRED",
                "execution daemon accepts only the fixed SIMULATOR/SIM domain", orderId);
        if (context.allowCancelAny)
            return Reject(context, "EXECUTION_CANCEL_ANY_FORBIDDEN",
                "cancel-any authority is never accepted over execution IPC", orderId);
        ExecutionCommandResult valid;
        valid.status = ExecutionCommandStatus::Accepted;
        return valid;
    }
    ExecutionCoordinator& m_coordinator;
    DeterministicExecutionVenue& m_venue;
    const ExecutionServiceRuntimeConfig m_config;
};
ExecutionServiceRuntimeComposition::ExecutionServiceRuntimeComposition(
    const ExecutionServiceRuntimeConfig& config)
    : m_config(config), m_ownedListenFd(config.listenFd),
      m_ownedEventListenFd(config.eventListenFd), m_stateLockFd(-1),
      m_fencingToken(0), m_fencingGeneration(0), m_startAttempted(false),
      m_started(false), m_quoteFeedRunning(false), m_quoteFeedStop(true)
{
    m_config.listenFd = -1;
    m_config.eventListenFd = -1;
}
ExecutionServiceRuntimeComposition::~ExecutionServiceRuntimeComposition()
{
    Stop();
}
void ExecutionServiceRuntimeComposition::CloseUnconsumedListenFd()
{
    if (m_ownedListenFd >= 0)
    {
        ::close(m_ownedListenFd);
        m_ownedListenFd = -1;
    }
    if (m_ownedEventListenFd >= 0)
    {
        ::close(m_ownedEventListenFd);
        m_ownedEventListenFd = -1;
    }
}
bool ExecutionServiceRuntimeComposition::PreparePrivateState(std::string& reason)
{
    struct stat directory;
    if (::lstat(m_config.stateDirectory.c_str(), &directory) != 0 ||
        !S_ISDIR(directory.st_mode) || directory.st_uid != ::geteuid() ||
        (directory.st_mode & 0777) != 0700)
    {
        reason = "EXECUTION_STATE_DIRECTORY_UNSAFE";
        return false;
    }
    const std::string lockPath = m_config.stateDirectory + "/execution-runtime.lock";
    m_stateLockFd = ::open(lockPath.c_str(), O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (m_stateLockFd < 0 || ::fchmod(m_stateLockFd, 0600) != 0 ||
        ::flock(m_stateLockFd, LOCK_EX | LOCK_NB) != 0)
    {
        if (m_stateLockFd >= 0) ::close(m_stateLockFd);
        m_stateLockFd = -1;
        reason = "EXECUTION_STATE_LOCK_UNAVAILABLE";
        return false;
    }
    return ValidateOrCreatePrivateFile(m_config.journalPath, reason);
}
bool ExecutionServiceRuntimeComposition::LoadFenceCredential(std::string& reason)
{
    std::string contents;
    if (!ReadSmallRegularFile(m_config.fenceCredentialPath, contents, reason)) return false;
    std::istringstream input(contents);
    std::string header;
    std::string tokenLine;
    std::string generationLine;
    std::string extra;
    if (!std::getline(input, header) || header != "HFC1" ||
        !std::getline(input, tokenLine) || !std::getline(input, generationLine) ||
        std::getline(input, extra) ||
        tokenLine.compare(0, 14, "fencing_token=") != 0 ||
        generationLine.compare(0, 11, "generation=") != 0 ||
        !ParsePositiveUnsigned(tokenLine.substr(14), m_fencingToken) ||
        !ParsePositiveUnsigned(generationLine.substr(11), m_fencingGeneration))
    {
        reason = "EXECUTION_FENCE_CREDENTIAL_INVALID";
        return false;
    }
    reason.clear();
    return true;
}
bool ExecutionServiceRuntimeComposition::RestoreSimulatorState(std::string& reason)
{
    long maximumOrderId = 999999;
    const int replayed = m_journal.Replay([&maximumOrderId](const OmsJournalEvent& event) {
        if (event.orderId > maximumOrderId) maximumOrderId = event.orderId;
    });
    if (replayed < 0 || maximumOrderId == std::numeric_limits<long>::max())
    {
        reason = replayed < 0 ? "EXECUTION_OMS_REPLAY_FAILED" :
            "EXECUTION_ORDER_ID_WATERMARK_EXHAUSTED";
        return false;
    }
    m_venue.RestoreNextOrderIdAtLeast(maximumOrderId + 1);
    reason.clear();
    return true;
}
void ExecutionServiceRuntimeComposition::RefreshSimulatorQuotes()
{
    const std::uint64_t observedAtMs =
        static_cast<std::uint64_t>(OmsJournal::NowEpochMs());
    const std::uint64_t staleAfterMs =
        observedAtMs > std::numeric_limits<std::uint64_t>::max() -
            m_config.simulatorQuoteTtlMs ?
        std::numeric_limits<std::uint64_t>::max() :
        observedAtMs + m_config.simulatorQuoteTtlMs;
    m_venue.SetQuoteObserved(
        "EUR.USD", 1.1000, 1.1002, observedAtMs, staleAfterMs);
    m_venue.SetQuoteObserved(
        "GBP.USD", 1.2500, 1.2502, observedAtMs, staleAfterMs);
}
void ExecutionServiceRuntimeComposition::SimulatorQuoteFeedLoop()
{
    std::unique_lock<std::mutex> lock(m_quoteFeedMutex);
    while (!m_quoteFeedStop)
    {
        if (m_quoteFeedChanged.wait_for(lock,
                std::chrono::milliseconds(
                    m_config.simulatorQuoteRefreshIntervalMs),
                [this]() { return m_quoteFeedStop; }))
            break;
        lock.unlock();
        RefreshSimulatorQuotes();
        lock.lock();
    }
    m_quoteFeedRunning.store(false);
}
bool ExecutionServiceRuntimeComposition::StartSimulatorQuoteFeed(
    std::string& reason)
{
    RefreshSimulatorQuotes();
    {
        std::lock_guard<std::mutex> lock(m_quoteFeedMutex);
        m_quoteFeedStop = false;
        m_quoteFeedRunning.store(true);
    }
    try
    {
        m_quoteFeedThread =
            std::thread(&ExecutionServiceRuntimeComposition::SimulatorQuoteFeedLoop,
                        this);
    }
    catch (...)
    {
        std::lock_guard<std::mutex> lock(m_quoteFeedMutex);
        m_quoteFeedStop = true;
        m_quoteFeedRunning.store(false);
        reason = "EXECUTION_SIMULATOR_QUOTE_FEED_START_FAILED";
        return false;
    }
    reason.clear();
    return true;
}
void ExecutionServiceRuntimeComposition::StopSimulatorQuoteFeed()
{
    {
        std::lock_guard<std::mutex> lock(m_quoteFeedMutex);
        m_quoteFeedStop = true;
    }
    m_quoteFeedChanged.notify_all();
    if (m_quoteFeedThread.joinable()) m_quoteFeedThread.join();
    m_quoteFeedRunning.store(false);
}
bool ExecutionServiceRuntimeComposition::Start(std::string& reason)
{
    if (m_startAttempted)
    {
        reason = "EXECUTION_RUNTIME_START_ALREADY_ATTEMPTED";
        return false;
    }
    m_startAttempted = true;
    ExecutionServiceRuntimeConfig validationConfig = m_config;
    validationConfig.listenFd = m_ownedListenFd;
    validationConfig.eventListenFd = m_ownedEventListenFd;
    if (!validationConfig.Validate(reason) || !validationConfig.Enabled())
    {
        if (reason.empty()) reason = "EXECUTION_RUNTIME_DISABLED";
        CloseUnconsumedListenFd();
        return false;
    }
    if (!PreparePrivateState(reason) || !LoadFenceCredential(reason))
    {
        CloseUnconsumedListenFd();
        return false;
    }
    if (!GenerateExecutionServiceIdentity(
            m_fencingGeneration, m_serviceIdentity, reason))
    {
        CloseUnconsumedListenFd();
        return false;
    }
    m_lifecycleGate.reset(new ExecutionServiceLifecycleGate());
    m_eventHub.reset(new ExecutionEventHub(1024, m_serviceIdentity.serviceEpoch));
    m_decisionLeases.reset(new ExecutionDecisionLeaseAuthority());
    // A dedicated execution daemon must never inherit performance-oriented OMS
    // buffering knobs from an interactive parent environment.
    ::setenv("HEPTA_OMS_ASYNC_FLUSH", "0", 1);
    ::setenv("HEPTA_OMS_SYNC_CRITICAL", "1", 1);
    ::setenv("HEPTA_OMS_BATCH_SIZE", "1", 1);
    ::setenv("HEPTA_OMS_FLUSH_INTERVAL_MS", "0", 1);
    if (!m_journal.Init(m_config.journalPath))
    {
        reason = "EXECUTION_OMS_INIT_FAILED";
        CloseUnconsumedListenFd();
        return false;
    }
    if (!RestoreSimulatorState(reason))
    {
        CloseUnconsumedListenFd();
        return false;
    }
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placeIbOrderCorrelated = [this](const InstrumentRef& contract,
                                              const OrderIntent& order,
                                              const std::string& correlationId,
                                              long* orderId) {
        return m_venue.PlaceOrderCorrelated(contract, order, correlationId, orderId);
    };
    callbacks.cancelIbOrder = [this](long orderId) { return m_venue.CancelOrder(orderId); };
    callbacks.canCancelIbOrder = [this](long orderId, std::string* detail) {
        return m_venue.CanCancelOrder(orderId, detail);
    };
    callbacks.lastIbRejectReason = [this]() { return m_venue.LastRejectReason(); };
    callbacks.onIbOrderPlaced = [this](const IbPlaceOrderCommand& command,
                                       long orderId, std::string*) {
        ExecutionEvent event;
        event.executionDomain = command.context.executionDomain;
        event.agentId = command.context.agentId;
        event.sessionId = command.context.sessionId;
        event.type = "order.accepted";
        event.venue = "SIMULATOR";
        event.orderId = orderId;
        event.instrument = command.instrument;
        event.side = command.order.action;
        event.status = "Accepted";
        event.remainingQuantity = command.order.totalQuantity;
        return m_eventHub->Publish(event) != 0;
    };
    callbacks.onIbCancelSent = [this](const IbCancelOrderCommand& command, std::string*) {
        ExecutionEvent event;
        event.executionDomain = command.context.executionDomain;
        event.agentId = command.context.agentId;
        event.sessionId = command.context.sessionId;
        event.type = "order.cancel_requested";
        event.venue = "SIMULATOR";
        event.orderId = command.orderId;
        event.instrument = command.instrument;
        event.side = command.side;
        event.status = "CancelRequested";
        return m_eventHub->Publish(event) != 0;
    };
    callbacks.validateDecisionLease = [this](const AgentExecutionContext& context,
                                             const std::string& instrument,
                                             std::string* detail) {
        return m_decisionLeases->Validate(context, instrument, detail);
    };
    m_coordinator.reset(new ExecutionCoordinator(m_journal, callbacks));
    std::string recoveryReason;
    if (!m_coordinator->RecoverFromJournal(recoveryReason)) m_recoveryReason = recoveryReason;
    else m_recoveryReason.clear();
    // The Simulator venue is process-local and deliberately restores no live
    // orders. Its complete authoritative state after restart is therefore an
    // empty active-order set. Persistently terminate replayed owners so the
    // coordinator cannot retain ownership for orders absent from the venue.
    // This does not reset any UNCERTAIN mutation block discovered by recovery.
    std::size_t removedOwners = 0;
    std::string reconcileReason;
    if (!m_coordinator->ReconcileOrderOwners(std::set<long>(), true,
            removedOwners, reconcileReason) && m_recoveryReason.empty())
        m_recoveryReason = reconcileReason;
    m_policyAuthority.reset(new SimulatorPolicyAuthority(
        *m_coordinator, m_venue, m_config));
    if (!StartSimulatorQuoteFeed(reason))
    {
        m_policyAuthority.reset();
        m_coordinator.reset();
        CloseUnconsumedListenFd();
        return false;
    }
    m_eventServer.reset(new UnixExecutionEventFeedServer(
        *m_eventHub, m_serviceIdentity, m_lifecycleGate));
    const int activatedEventFd = m_ownedEventListenFd;
    m_ownedEventListenFd = -1;
    if (!m_eventServer->StartFromFd(activatedEventFd, m_config.allowedGatewayUids,
            m_config.gatewayContextBinding, reason, 8192,
            m_config.ioTimeoutMs, 4, 32))
    {
        m_eventServer.reset();
        StopSimulatorQuoteFeed();
        m_policyAuthority.reset();
        m_coordinator.reset();
        CloseUnconsumedListenFd();
        return false;
    }
    m_server.reset(new UnixExecutionServiceServer(
        *m_policyAuthority, m_policyAuthority.get(), m_decisionLeases));
    const int activatedFd = m_ownedListenFd;
    m_ownedListenFd = -1;
    if (!m_server->StartFromFd(activatedFd, m_config.allowedGatewayUids,
            m_config.gatewayContextBinding, m_serviceIdentity,
            m_lifecycleGate, reason,
            m_config.maxRequestBytes, m_config.ioTimeoutMs))
    {
        m_server.reset();
        m_eventServer->Stop();
        m_eventServer.reset();
        StopSimulatorQuoteFeed();
        m_policyAuthority.reset();
        m_coordinator.reset();
        return false;
    }
    m_lifecycleGate->ready.store(true);
    m_started = true;
    reason.clear();
    return true;
}
void ExecutionServiceRuntimeComposition::Stop()
{
    if (m_lifecycleGate) m_lifecycleGate->ready.store(false);
    m_started = false;
    StopSimulatorQuoteFeed();
    if (m_server) m_server->Stop();
    if (m_eventServer) m_eventServer->Stop();
    CloseUnconsumedListenFd();
    if (m_stateLockFd >= 0)
    {
        ::flock(m_stateLockFd, LOCK_UN);
        ::close(m_stateLockFd);
        m_stateLockFd = -1;
    }
}
bool ExecutionServiceRuntimeComposition::IsRunning() const
{
    return m_started && m_server && m_server->IsRunning() &&
        m_eventServer && m_eventServer->IsRunning() &&
        m_quoteFeedRunning.load();
}
bool ExecutionServiceRuntimeComposition::IsMutationBlocked(std::string* reason) const
{
    if (!m_coordinator)
    {
        if (reason != nullptr) *reason = "EXECUTION_RUNTIME_NOT_STARTED";
        return true;
    }
    return m_coordinator->IsMutationBlocked(reason);
}
const std::string& ExecutionServiceRuntimeComposition::RecoveryReason() const
{
    return m_recoveryReason;
}
ExecutionCoordinator& ExecutionServiceRuntimeComposition::Coordinator()
{
    return *m_coordinator;
}
DeterministicExecutionVenue& ExecutionServiceRuntimeComposition::Venue()
{
    return m_venue;
}
ExecutionEventHub& ExecutionServiceRuntimeComposition::EventHub()
{
    return *m_eventHub;
}
