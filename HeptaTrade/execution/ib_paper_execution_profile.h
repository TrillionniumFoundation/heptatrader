#pragma once

#include "execution_authority.h"
#include "execution_coordinator.h"
#include "ib_paper_kill_switch.h"
#include "../adapter_ib/ib_gateway_adapter.h"

#include <cstddef>
#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <string>
#include <mutex>
#include <vector>

enum class IbPaperOrderMode
{
    LocalMarketDay = 0,
    ExternalLimitDay
};

struct IbPaperExecutionProfileConfig
{
    bool enabled = false;
    std::string account;
    std::string host;
    int port = 0;
    int clientId = 0;
    std::string stateDirectory;
    std::string authorizationCredentialPath;
    std::string controlDirectory;
    double maxOrderQuantity = 0.0;
    double maxOrderNotional = 0.0;
    std::size_t maxOrdersPerMinute = 0;
    std::size_t maxActiveOrders = 0;
    double maxGrossPosition = 0.0;
    IbPaperOrderMode orderMode = IbPaperOrderMode::LocalMarketDay;
    // The legacy local profile leaves this zero and continues to source the
    // quote TTL from the runtime config.  ExternalLimitDay records and binds
    // its reviewed upper bound here so the authorization credential cannot
    // be reused with a looser quote-age policy.
    std::uint64_t externalQuoteMaxAgeMs = 0;

    bool Validate(std::string& reason) const;
    bool VerifyAuthorizationCredential(std::string& reason) const;
    bool BuildAuthorizationCredential(std::string& value,
                                      std::string& reason) const;

    static const char* AuthorizationCredentialName();
    static const char* ControlDirectoryPath();
    static const char* AllowedSecurityTypes();
    const char* AllowedOrderTypes() const;
    static const char* OrderModeName(IbPaperOrderMode mode);
    bool UsesExternalLimitDay() const;
    static bool FromEnvironment(IbPaperExecutionProfileConfig& config,
                                std::string& reason);
    static bool FromValues(const std::map<std::string, std::string>& values,
                           IbPaperExecutionProfileConfig& config,
                           std::string& reason);
};

struct IbPaperAuthoritativeRiskSnapshot
{
    bool complete = false;
    std::size_t activeOrderCount = 0;
    double grossAbsolutePosition = 0.0;
};

struct IbPaperAuthoritativePositionSnapshot
{
    bool complete = false;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t generation = 0;
    double quantity = 0.0;
    std::string reasonCode;
};

// Service-local, fail-closed guard for risk-increasing IB PAPER mutations.
// Cancels deliberately remain available while the kill switch is engaged.
class IbPaperExecutionGuard
{
public:
    IbPaperExecutionGuard(
        const IbPaperExecutionProfileConfig& config,
        const std::shared_ptr<IbPaperKillSwitchReader>& killSwitch);

    bool AllowPlace(const IbPlaceOrderCommand& command,
                    const IbPaperAuthoritativeRiskSnapshot& snapshot,
                    std::int64_t nowMs,
                    std::string& reason);
    bool AllowPlaceAtAuthoritativePrice(
        const IbPlaceOrderCommand& command,
        const IbPaperAuthoritativeRiskSnapshot& snapshot,
        double authoritativePrice,
        std::int64_t nowMs,
        std::string& reason);
    bool AllowCancel(const IbCancelOrderCommand& command,
                     std::string& reason) const;
    bool AllowFlatten(
        const FlattenPositionCommand& command,
        const AuthoritativeFlattenPlan& plan,
        const IbPaperAuthoritativeRiskSnapshot& risk,
        std::int64_t nowMs,
        std::string& reason);
    void ReplaceSendAttemptTimes(const std::vector<std::int64_t>& times,
                                 std::int64_t nowMs);

private:
    void PruneRateWindow(std::int64_t nowMs);

    IbPaperExecutionProfileConfig m_config;
    std::shared_ptr<IbPaperKillSwitchReader> m_killSwitch;
    std::deque<std::int64_t> m_acceptedPlaceTimesMs;
    std::mutex m_mutex;
};

struct IbPaperExecutionPolicyCallbacks
{
    std::function<IbPaperAuthoritativeRiskSnapshot()> riskSnapshot;
    std::function<IBAuthoritativeCorrelationSnapshot()> correlationSnapshot;
    std::function<IBAuthoritativeTerminalCorrelationSnapshot()>
        terminalCorrelationSnapshot;
    // Recovery finalization must use this single composite callback. Runtime
    // composition binds it to a fresh broker barrier and one adapter-lock
    // sample; the older callbacks remain for non-terminal reconciliation.
    std::function<IBAuthoritativeRecoveryAuditSnapshot()>
        recoveryAuditSnapshot;
    // One-way runtime terminal boundary.  The first callback durably enters
    // TERMINALIZING, closes broker ingress, drains callbacks, and returns the
    // frozen snapshot.  The second fsyncs the exact terminal witness.  Replay
    // returns the already-durable witness without broker I/O.
    std::function<bool(const ExecutionControlCommand&,
                       IBAuthoritativeRecoveryAuditSnapshot&,
                       ExecutionControlResult&, std::string&)>
        beginTerminalRecoveryAudit;
    std::function<bool(const ExecutionControlCommand&,
                       const ExecutionControlResult&,
                       ExecutionControlResult&, std::string&)>
        commitTerminalRecoveryAudit;
    std::function<std::int64_t()> nowMs;
    std::function<MarketQuoteSnapshot(const std::string&)> authoritativeQuote;
    std::function<bool(const std::string&, InstrumentRef&)>
        authoritativeContract;
    std::function<IbPaperAuthoritativePositionSnapshot(
        const std::string&)> authoritativePosition;
    std::function<ExecutionCommandResult(const ExecutionReadCommand&)> authoritativeRead;
};

class IbPaperExecutionPolicyAuthority : public ExecutionAuthority,
                                        public ExecutionControlAuthority,
                                        public ExecutionReadAuthority
{
public:
    IbPaperExecutionPolicyAuthority(
        ExecutionCoordinator& coordinator,
        const IbPaperExecutionProfileConfig& config,
        const IbPaperExecutionPolicyCallbacks& callbacks,
        const std::shared_ptr<IbPaperKillSwitchReader>& killSwitch);

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
    bool IsDurablePlaceReplay(
        const PlaceOrderCommand& command) const override;
    bool IsDurableFlattenReplay(
        const FlattenPositionCommand& command) const override;
    ExecutionCommandResult ReadAuthoritativeState(
        const ExecutionReadCommand& command) override;
    bool ReconcileAuthoritativeState(std::size_t& affectedCount,
                                     std::string& reason);

private:
    ExecutionCommandResult Reject(const AgentExecutionContext& context,
                                  long orderId,
                                  const std::string& reason) const;
    bool ValidContext(const AgentExecutionContext& context) const;
    ExecutionControlResult BeginControl(const ExecutionControlCommand& command) const;
    ExecutionControlResult AuditRecoveryOwner(
        const ExecutionControlCommand& command,
        const IBAuthoritativeRecoveryAuditSnapshot& recovery);
    void RefreshRateBudget(
        std::int64_t nowMs,
        const std::string& executionDomain = "PAPER");
    bool ValidateFreshQuote(const PlaceOrderCommand& command,
                            std::int64_t nowMs,
                            MarketQuoteSnapshot& quote,
                            std::string& reason) const;
    bool BuildAuthoritativeFlattenPlan(
        const FlattenPositionCommand& command,
        std::int64_t nowMs,
        AuthoritativeFlattenPlan& plan,
        std::string& reason);

    ExecutionCoordinator& m_coordinator;
    const IbPaperExecutionProfileConfig m_config;
    IbPaperExecutionGuard m_guard;
    IbPaperExecutionPolicyCallbacks m_callbacks;
    const std::string m_account;
    const double m_maxOrderQuantity;
    std::mutex m_mutex;
};
