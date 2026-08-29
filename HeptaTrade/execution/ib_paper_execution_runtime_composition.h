#pragma once

#include "execution_authority.h"
#include "ib_paper_execution_runtime_config.h"
#include "paper_terminal_mutation_manifest.h"
#include "../adapter_ib/ib_gateway_adapter.h"
#include "../events/execution_event_hub.h"
#include "../oms_journal.h"
#include "../state/authoritative_trading_snapshot_store.h"
#include "../state/ib_authoritative_quote_subscription_set.h"

#include <atomic>
#include <chrono>
#include <cstdint>
#include <functional>
#include <memory>
#include <map>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

class ExecutionCoordinator;
class ExecutionDecisionLeaseAuthority;
class IbPaperExecutionPolicyAuthority;
struct IbPaperExecutionPolicyCallbacks;
class UnixExecutionServiceServer;
class UnixExecutionEventFeedServer;
class IbPaperExecutionHookAuthority;
struct ExecutionOrderOwner;
struct IbPaperAuthoritativeRiskSnapshot;
struct IbPaperAuthoritativePositionSnapshot;

// Process-level fault injection seam for offline certification only. The
// production daemon never supplies this object and no environment/argv path
// can enable it.
struct IbPaperExecutionRuntimeTestHooks
{
    std::function<void(const char*)> onStage;
    std::function<bool(const std::string&,
                       std::shared_ptr<IbPaperKillSwitchReader>&,
                       std::string&)> openKillSwitch;
    // Offline-only wrapper factory used after the injected startup wrapper has
    // observed a transport close. Production leaves this empty and always
    // rebuilds the real IB wrapper.
    std::function<std::unique_ptr<IIBApiWrapper>()> reconnectApiFactory;
};

class IbPaperExecutionRuntimeComposition
{
public:
    explicit IbPaperExecutionRuntimeComposition(
        const IbPaperExecutionRuntimeConfig& config,
        std::unique_ptr<IIBApiWrapper> injectedApi = std::unique_ptr<IIBApiWrapper>(),
        const IbPaperExecutionRuntimeTestHooks& testHooks =
            IbPaperExecutionRuntimeTestHooks(),
        const std::shared_ptr<IbPaperKillSwitchReader>& injectedKillSwitch =
            std::shared_ptr<IbPaperKillSwitchReader>());
    ~IbPaperExecutionRuntimeComposition();

    // Install a non-blocking owner supplied cancellation probe before Start.
    // The broker-facing daemon uses this to consume a pending systemd
    // SIGTERM while startup is still waiting on IB readiness; without a
    // startup-time probe a blocked signal can leave systemd waiting for the
    // full stop timeout before it kills the process.  The probe must not
    // perform runtime mutation and is called only by the Start owner thread.
    void SetStartupCancellationProbe(
        const std::function<bool()>& cancellationProbe);
    bool Start(std::string& reason);
    void Stop();
    bool IsRunning() const;
    bool IsMutationBlocked(std::string* reason = nullptr) const;
    bool HasFatalRuntimeError(std::string* reason = nullptr) const;
    const std::string& RecoveryReason() const;

    HeptaIBGatewayAdapter& Adapter();
    ExecutionCoordinator& Coordinator();
    ExecutionEventHub& EventHub();

private:
    bool PreparePrivateState(std::string& reason);
    bool LoadFxCashBaselines(std::string& reason);
    bool ValidateFxCashBaselineRecords(
        const std::map<std::string, IbPaperFxCashBaseline>& records) const;
    bool LoadFxCashRestartCheckpoint(std::string& reason);
    bool PersistFxCashRestartCheckpoint(std::string& reason);
    bool LoadPaperTerminalLatch(std::string& reason);
    bool PersistPaperTerminalizingLatch(
        const ExecutionControlCommand& command, std::string& reason);
    bool PersistPaperTerminalHaltedLatch(
        const ExecutionControlCommand& command,
        const ExecutionControlResult& audit,
        ExecutionControlResult& terminal,
        std::string& reason);
    bool LoadFenceCredential(std::string& reason);
    bool ValidateStartupContract(std::string& reason);
    bool PrepareExecutionFoundation(std::string& reason);
    void BuildCoordinator();
    bool BuildPolicyAuthority(std::string& reason);
    bool BuildTerminalControlAuthority(std::string& reason);
    void BindTerminalPolicyCallbacks(
        IbPaperExecutionPolicyCallbacks& callbacks);
    IBAuthoritativeCorrelationSnapshot PolicyCorrelationSnapshot() const;
    IBAuthoritativeRecoveryAuditSnapshot PolicyRecoveryAuditSnapshot();
    bool BeginTerminalRecoveryAudit(
        const ExecutionControlCommand& command,
        IBAuthoritativeRecoveryAuditSnapshot& snapshot,
        ExecutionControlResult& terminalState,
        std::string& reason);
    bool CommitTerminalRecoveryAudit(
        const ExecutionControlCommand& command,
        const ExecutionControlResult& audit,
        ExecutionControlResult& terminalState,
        std::string& reason);
    bool CompleteTerminalTransportAudit(
        IBAuthoritativeRecoveryAuditSnapshot& frozen,
        IBAuthoritativeRecoveryAuditSnapshot& snapshot,
        ExecutionControlResult& terminalState,
        std::string& reason);
    IbPaperAuthoritativeRiskSnapshot PolicyRiskSnapshot() const;
    IbPaperAuthoritativePositionSnapshot PolicyPositionSnapshot(
        const std::string& instrument) const;
    ExecutionCommandResult PolicyAuthoritativeRead(
        const ExecutionReadCommand& command) const;
    bool StartIpcAndPublishReady(std::string& reason);
    bool StartAdapterAndBuildSnapshots(std::string& reason);
    bool StartupCancellationRequested(std::string& reason) const;
    bool AbortStartupIfCancelled(std::string& reason);
    bool InitializeAdapterAndConnect(
        std::string& reason,
        std::chrono::steady_clock::time_point deadline);
    bool WaitForStartupUpstream(
        std::string& reason,
        std::chrono::steady_clock::time_point deadline);
    bool WaitForAuthoritativeSnapshots(
        std::string& reason,
        std::chrono::steady_clock::time_point deadline);
    bool CleanupStartupSnapshotFailure(
        std::string& reason, const std::string& primaryReason);
    bool DispatchStartupRecoveryRisk(
        const IBAuthoritativeRecoveryAuditSnapshot& recovery,
        bool& dispatched, std::string& reason);
    bool StartupSnapshotBarrierReady(
        const IBAuthoritativeRecoveryAuditSnapshot& recovery,
        bool recoveryRiskDispatched) const;
    bool StartQuoteSubscriptions(
        std::string& reason, std::uint64_t expectedConnectionEpoch = 0);
    bool StopQuoteSubscriptions(std::string* reason = nullptr);
    bool BeginBrokerReconnect(std::string& reason,
                              bool recoveryAudit = false);
    bool DriveBrokerReconnect(std::string& reason);
    bool DriveBrokerReconnectConnected(std::string& reason);
    bool AdvanceReconnectMarketDataGate(
        bool requiresCashMarketDataFarm, bool upstreamReady,
        std::chrono::steady_clock::time_point observedNow,
        std::chrono::steady_clock::time_point marketDataWarmupReadyAt,
        std::chrono::milliseconds cashFarmStabilityWindow,
        std::chrono::steady_clock::time_point& cashFarmStableSince,
        std::string& reason, bool& marketDataFarmReady);
    bool DispatchReconnectRefreshIfReady(
        bool upstreamReady, bool marketDataFarmReady,
        std::chrono::steady_clock::time_point observedNow,
        std::chrono::steady_clock::time_point& snapshotDeadline,
        std::string& reason, bool& retryScheduled);
    bool FailBrokerReconnect(std::string& reason,
                             const std::string& primaryReason,
                             bool disconnect = true);
    bool RequestReconnectRiskRefresh(std::string& reason);
    bool ReconnectAuthoritativeStateReady(std::string& reason) const;
    bool AllowsRiskIncrease(std::string& reason) const;
    bool AllowsAuthoritativeFlatten(
        std::string& reason, bool requireSettledQuote = false) const;
    bool AllowsBoundRiskIncreasingPlace(
        const IBFinalOrderSendContext* context,
        const IBContractLite& contract, const IBOrderLite& order,
        std::string& reason) const;
    bool AuthorizeFinalBrokerSend(
        const IBFinalOrderSendContext* context,
        const IBContractLite& contract, const IBOrderLite& order,
        std::string* detail) const;
    MarketQuoteSnapshot AuthoritativeQuote(
        const std::string& instrument) const;
    static bool HasPositiveTradableQuote(
        const IBAuthoritativeQuoteSnapshot& quote);
    static bool FreshCompositeQuote(
        const IBAuthoritativeQuoteSnapshot& quote,
        std::uint64_t nowMs,
        std::uint64_t maxAgeMs,
        std::uint64_t& observedAtMs,
        std::uint64_t& staleAfterMs);
    enum class AdapterControlAction
    {
        Route,
        Consumed,
        ReconnectBoundary
    };
    enum class AdapterRouteAction
    {
        Processed,
        Busy,
        Fatal
    };
    bool DrainAdapterEvents(int pollTimeoutMs = -1);
    bool HasPendingAdapterEvents() const;
    // Collect wrapper ingress into the pending batch without routing it.  The
    // quote-admission fence uses this narrow phase so an EReader callback can
    // never be blocked while the caller routes a control event and joins the
    // reader thread.
    bool CollectAdapterIngressEvents(int pollTimeoutMs = -1);
    bool CheckQuoteAdmissionPreflight(
        std::string& reason, std::uint64_t expectedConnectionEpoch);
    bool PrepareQuoteSubscriptionPlan(
        std::unique_ptr<IBAuthoritativeQuoteSubscriptionSet>& subscriptions,
        IBAuthoritativeQuoteSubscriptionPlan& plan,
        std::string& reason, std::uint64_t expectedConnectionEpoch);
    class QuoteAdmissionTransaction;
    void AbortUnpublishedQuoteCycle(
        std::unique_ptr<IBAuthoritativeQuoteSubscriptionSet>& subscriptions,
        std::uint64_t generation);
    bool PrepareQuoteAdmissionPass(
        QuoteAdmissionTransaction& transaction,
        std::unique_ptr<IBAuthoritativeQuoteSubscriptionSet>& subscriptions,
        const IBAuthoritativeQuoteSubscriptionPlan& plan,
        std::string& reason, std::uint64_t expectedConnectionEpoch);
    bool DispatchQuoteAdmissionPlan(
        QuoteAdmissionTransaction& transaction,
        const IBAuthoritativeQuoteSubscriptionPlan& plan,
        std::vector<int>& acceptedRequestIds, std::string& reason);
    bool FinishQuoteAdmission(
        QuoteAdmissionTransaction& transaction,
        std::string& reason, std::uint64_t expectedConnectionEpoch);
    bool SettleQuoteAdmission(
        std::string& reason, std::uint64_t expectedConnectionEpoch);
    bool RoutePendingAdapterEvents(
        std::vector<IBEvent>& pendingEvents,
        std::uint64_t& pendingQuoteEvents, bool& drained);
    bool RunQuoteAdmissionTransaction(
        std::unique_ptr<IBAuthoritativeQuoteSubscriptionSet>& subscriptions,
        const IBAuthoritativeQuoteSubscriptionPlan& plan,
        std::string& reason, std::uint64_t expectedConnectionEpoch);
    AdapterControlAction HandleMarketDataFarmControl(
        int controlErrorCode, std::uint64_t eventEpoch);
    AdapterControlAction HandleStartupBrokerControl(
        const IBEvent& event, int controlErrorCode);
    AdapterControlAction HandleReconnectBrokerControl(
        const IBEvent& event, int controlErrorCode);
    void PersistAndMarkFatalBrokerControl(
        const IBEvent& event, const std::string& reason);
    AdapterControlAction HandleAdapterControlEvent(const IBEvent& event);
    bool HandleNonPersistentAdapterEvent(const IBEvent& event);
    bool DisconnectAndDrainBoundaryEvents(std::string& reason);
    AdapterRouteAction RoutePersistedAdapterEvent(const IBEvent& event);
    bool ValidateBrokerCallbackIdentity(
        const IBEvent& event, const ExecutionOrderOwner& owner,
        std::string& reason) const;
    bool BeginEventPostFillRiskRefresh(
        const IBEvent& event, const ExecutionOrderOwner& owner,
        long orderId);
    static bool IsTerminalBrokerEvent(const IBEvent& event);
    void RecordTerminalBrokerEvent(long orderId);
    bool BeginPostFillRiskRefresh(
        long orderId, const ExecutionOrderOwner& owner,
        const IBEvent& execution);
    bool PostFillRiskRefreshPending() const;
    void DrivePostFillRiskRefresh();
    bool PersistBrokerCallback(
        const IBEvent& event, const ExecutionOrderOwner* owner);
    void PublishBrokerCallback(
        const IBEvent& event, const ExecutionOrderOwner& owner);
    struct RecentBrokerOrder;
    bool RebuildRecentBrokerOrders(std::string& reason);
    void ProjectRecentBrokerOrder(const OmsJournalEvent& event);
    void ProjectAcceptedRecentBrokerOrder(
        const OmsJournalEvent& event, const std::string& agentId);
    void ApplyRecentBrokerOrderIdentity(
        RecentBrokerOrder& order, const OmsJournalEvent& event,
        const std::string& agentId);
    static void ApplyRecentBrokerExecutionIdentity(
        RecentBrokerOrder& order, const OmsJournalEvent& event);
    static bool HasPositiveEconomicEvidence(
        const OmsJournalEvent& event);
    static void ApplyRecentBrokerEconomicEvidence(
        RecentBrokerOrder& order, const OmsJournalEvent& event,
        bool positiveEconomicEvidence);
    static void ApplyRecentBrokerTerminalEvidence(
        RecentBrokerOrder& order, const OmsJournalEvent& event,
        bool reconciledTerminal, bool positiveEconomicEvidence);
    void TrimRecentBrokerOrders();
    bool ProjectAcceptedBrokerOrder(
        const IbPlaceOrderCommand& command, long orderId,
        std::string& reason);
    std::string RecentBrokerOrdersJson(
        const AgentExecutionContext& context) const;
    std::string OwnedOrdersJson(
        const AgentExecutionContext& context,
        const IBAuthoritativeCorrelationSnapshot& orders) const;
    void MarkFatalRuntimeError(const std::string& reason);
    bool PublishTerminalControlReady(std::string& reason);
    bool DriveRecoveryAuditReconnect();
    void AdapterLoop();
    void CloseUnconsumedListenFds();
    void NotifyTestStage(const char* stage) const;

    IbPaperExecutionRuntimeConfig m_config;
    int m_ownedListenFd;
    int m_ownedEventListenFd;
    int m_stateLockFd;
    std::uint64_t m_fencingToken;
    std::uint64_t m_fencingGeneration;
    ExecutionServiceIdentity m_serviceIdentity;
    std::shared_ptr<ExecutionServiceLifecycleGate> m_lifecycleGate;
    bool m_startAttempted;
    bool m_started;
    bool m_injectedApi;
    IbPaperExecutionRuntimeTestHooks m_testHooks;
    std::string m_recoveryReason;
    std::unique_ptr<IIBApiWrapper> m_initialApi;
    std::function<bool()> m_startupCancellationProbe;
    std::shared_ptr<IbPaperKillSwitchReader> m_killSwitch;
    std::unique_ptr<HeptaIBGatewayAdapter> m_adapter;
    AuthoritativeTradingSnapshotStore m_authoritativeSnapshots;
    std::unique_ptr<IBAuthoritativeQuoteSubscriptionSet> m_quoteSubscriptions;
    OmsJournal m_journal;
    std::unique_ptr<ExecutionEventHub> m_eventHub;
    std::shared_ptr<ExecutionDecisionLeaseAuthority> m_decisionLeases;
    std::unique_ptr<ExecutionCoordinator> m_coordinator;
    std::unique_ptr<IbPaperExecutionPolicyAuthority> m_policyAuthority;
    std::unique_ptr<IbPaperExecutionHookAuthority> m_hookAuthority;
    std::unique_ptr<UnixExecutionServiceServer> m_server;
    std::unique_ptr<UnixExecutionEventFeedServer> m_eventServer;
    std::atomic<bool> m_polling;
    std::atomic<bool> m_fatalRuntimeError;
    // Startup broker admission is a separate state machine from steady-state
    // reconnect: policy/lifecycle authority does not exist yet while the first
    // upstream session and authoritative snapshots are being established.
    std::atomic<bool> m_startupBrokerPhase;
    std::atomic<bool> m_startupWaitingForUpstream;
    std::atomic<bool> m_startupUpstreamUnavailable;
    std::atomic<bool> m_startupUpstreamRestored;
    std::atomic<bool> m_startupMarketDataFarmWaiting;
    std::atomic<bool> m_startupMarketDataFarmRestored;
    // Epoch of the positive CASH-farm 2104 witness.  A readiness bit without
    // this binding could leak across a reconnect boundary.
    std::atomic<std::uint64_t> m_startupMarketDataFarmEpoch;
    std::atomic<bool> m_reconnectPending;
    std::atomic<bool> m_reconnectTransportConnected;
    std::atomic<bool> m_reconnectUpstreamUnavailable;
    std::atomic<bool> m_reconnectUpstreamRestored;
    // These flags are observed by the poll thread and may be cleared by the
    // owner thread while it requests shutdown.  Keep the hand-off explicit;
    // a plain bool here made Stop() race a reconnect failure that was still
    // unwinding on the poll thread (caught by ThreadSanitizer).
    std::atomic<bool> m_reconnectRefreshDispatched{false};
    std::atomic<bool> m_reconnectRiskRefreshDispatched{false};
    std::atomic<bool> m_recoveryAuditReconnectRequested;
    std::atomic<std::uint64_t> m_reconnectConnectionEpoch{0};
    int m_reconnectAttempt = 0;
    std::chrono::steady_clock::time_point m_reconnectDeadline;
    std::chrono::steady_clock::time_point m_reconnectNextAttemptAt;
    std::chrono::steady_clock::time_point m_reconnectFastPathReadyAt;
    std::atomic<bool> m_postFillRiskRefreshPending;
    long m_postFillOrderId = -1;
    std::string m_postFillInstrument;
    std::string m_postFillSide;
    double m_postFillBaselinePosition = 0.0;
    double m_postFillExpectedPosition = 0.0;
    bool m_postFillExecutionComplete = false;
    bool m_postFillTerminalObserved = false;
    std::uint64_t m_postFillBaselinePositionGeneration = 0;
    std::uint64_t m_postFillBaselineFxCashGeneration = 0;
    int m_postFillRiskRefreshAttempts = 0;
    std::chrono::steady_clock::time_point m_postFillNextRetryAt;
    std::chrono::steady_clock::time_point m_postFillDeadline;
    std::chrono::steady_clock::time_point m_postFillStableSince;
    struct RecentBrokerOrder
    {
        long orderId = -1;
        std::uint64_t observedAtMs = 0;
        std::string agentId;
        std::string sessionId;
        std::string executionDomain;
        std::string account;
        std::string instrument;
        std::string side;
        std::string brokerExecutionId;
        bool brokerExecutionAmbiguous = false;
        double brokerExecutionQuantity = 0.0;
        double brokerExecutionPrice = 0.0;
        std::string status;
        std::string reasonCode;
        std::string serviceEpoch;
        std::uint64_t connectionEpoch = 0;
        bool terminal = false;
        bool economicFill = false;
        double filledQuantity = 0.0;
        double remainingQuantity = 0.0;
        double averageFillPrice = 0.0;
    };
    mutable std::mutex m_recentBrokerOrdersMutex;
    // Full callback routing serialization. The terminal RPC stops and joins
    // the poll owner before taking this mutex, then becomes the sole drainer.
    mutable std::mutex m_adapterEventDrainMutex;
    mutable std::mutex m_terminalizationMutex;
    bool m_terminalLatchPresent = false;
    bool m_terminalLatchPreparing = false;
    bool m_terminalLatchHalted = false;
    std::string m_terminalFinalizationId;
    std::string m_terminalPreliminaryReceiptSha256;
    std::string m_terminalOwnerAgentId;
    std::string m_terminalOwnerSessionId;
    std::string m_terminalOwnerAccount;
    std::string m_terminalOwnerExecutionDomain;
    std::uint64_t m_terminalRecoveryIngressFence = 0;
    PaperTerminalFenceBinding m_terminalFenceBinding;
    PaperTerminalMutationManifest m_terminalMutationManifest;
    ExecutionControlResult m_terminalResult;
    std::map<long, RecentBrokerOrder> m_recentBrokerOrders;
    mutable std::mutex m_lifecycleStateMutex;
    mutable std::mutex m_fatalMutex;
    std::string m_fatalReason;
    // Linearizes authoritative quote dequeue/apply with the final adapter
    // quote check and broker API send. The recursive form lets the final
    // callback verify the pending counter while its caller holds the gate
    // across the complete adapter operation.
    mutable std::recursive_mutex m_authoritativeQuoteSendMutex;
    // Serializes quote-admission transactions themselves.  This is separate
    // from the quote-send mutex so admission can release the event-drain
    // boundary before routing callbacks, avoiding an eventDrain->quote versus
    // quote->eventDrain lock inversion with the poll owner.
    mutable std::recursive_mutex m_quoteAdmissionMutex;
    std::atomic<std::uint64_t> m_pendingAuthoritativeQuoteEvents{0};
    std::vector<IBEvent> m_pendingAdapterEvents;
    std::thread m_pollThread;
};
