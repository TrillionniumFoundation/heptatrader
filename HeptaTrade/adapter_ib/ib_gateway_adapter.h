#pragma once

#include <string>
#include <memory>
#include <unordered_set>
#include <unordered_map>
#include <chrono>
#include <ctime>
#include <functional>
#include <mutex>
#include <map>
#include <set>
#include <vector>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <deque>

#include "ib_api_wrapper.h"
#include "ib_order_lifecycle.h"
#include "../risk/deterministic_risk_policy.h"

struct HeptaIBRiskConfig {
    bool enableOrderSubmission = false;
    double maxOrderQuantity = 1.0;
    int maxDailyOrders = 1;
    bool enableAutoCircuitBreaker = true;
    int fuseOnErrorCount = 3;

    double maxPriceDeviationBps = 30.0;
    int duplicateOrderWindowSec = 3;
    double duplicatePriceTolerance = 0.0001;
    int circuitBreakerCooldownSec = 15;

    bool enableErrorCodeBlacklist = true;
    std::unordered_set<int> errorCodeBlacklist = {201, 202, 10147, 10148};
    std::unordered_set<int> fuseIgnoreErrorCodes = {2104, 2106, 2107, 2109, 2119, 2158, 399};

    bool requireTwsConnected = true;
    bool requireNextValidId = true;
    bool requireAccountConfigured = true;

    std::unordered_set<std::string> accountWhitelist = {"DU*"};
    bool allowLiveTrading = false;
    bool liveKillSwitch = true;
    bool globalKillSwitch = false;
    bool flattenOnly = false;

    // Common deterministic-risk limits.  The low-level adapter keeps these
    // explicit so an IB PAPER profile can bind the same reviewed budgets it
    // applies before the broker send.  maxOrderQuantity and
    // maxPriceDeviationBps above map one-to-one to the common policy;
    // maxDailyOrders remains this adapter's calendar-day compatibility cap.
    // Optional portfolio/PnL dimensions (maxNetPosition,
    // maxStrategyGrossPosition, maxDailyLoss and maxDrawdown) are deliberately
    // not synthesized here: no generation-bound IB authority supplies them,
    // so they stay disabled in the shared limits rather than receiving a
    // hidden default.
    double maxOrderNotional = 250000.0;
    std::size_t maxOrdersPerMinute = 30;
    std::size_t maxActiveOrders = 50;
    double maxGrossPosition = 100000.0;
    bool requireFreshQuote = true;
    bool requireCompleteSnapshot = true;
};

struct HeptaIBConfig {
    std::string host = "127.0.0.1";
    int port = 7497;
    int clientId = 101;
    std::string account;
    bool readOnly = true;
    std::string observabilityLogPath;
    // CASH FX inventory is held as currency cash rather than an IB security
    // position.  Each configured instrument is projected from the broker's
    // CashBalance for contract.symbol (the base currency).
    std::map<std::string, InstrumentRef> authoritativeCashFxContracts;
    // Durable, operator-reconciled broker CashBalance anchors.  Absolute
    // multi-currency cash can predate this PAPER campaign and is never itself
    // treated as campaign risk.  The owned quantity is current cash minus this
    // baseline.
    std::map<std::string, double> authoritativeCashFxBaselines;
    // Operator-attested live CashBalance observed when the baseline was
    // sealed. This is checked exactly once against the first complete broker
    // account snapshot; later post-execution refreshes use the fixed baseline
    // normally and are not compared to this initial observation.
    std::map<std::string, double>
        authoritativeCashFxStartupObservedBalances;
    HeptaIBRiskConfig risk;
};

struct IBAuthoritativeFxCashExposure {
    std::string instrument;
    std::string baseCurrency;
    std::string quoteCurrency;
    double baselineCashBalance = 0.0;
    double currentCashBalance = 0.0;
    double campaignOwnedQuantity = 0.0;
};

struct IBAuthoritativeCorrelationSnapshot {
    std::uint64_t connectionEpoch = 0;
    std::uint64_t generation = 0;
    bool complete = false;
    std::string reasonCode;
    std::map<std::string, long> activeOrderIdsByCorrelation;
    std::set<long> activeOrderIds;
};

struct IBAuthoritativeRiskSnapshot {
    std::uint64_t connectionEpoch = 0;
    std::uint64_t generation = 0;
    std::uint64_t accountGeneration = 0;
    std::uint64_t positionsGeneration = 0;
    bool complete = false;
    bool coherentRefreshComplete = false;
    bool accountComplete = false;
    bool positionsComplete = false;
    bool fxCashComplete = false;
    std::uint64_t fxCashGeneration = 0;
    // The latest economic-fill generation that the completed account,
    // positions and FX refresh pair causally follows.
    std::uint64_t riskAbsorbedExposureGeneration = 0;
    double grossAbsolutePosition = 0.0;
    std::string reasonCode;
};

struct IBOrderRiskBaseline {
    std::string instrument;
    std::string side;
    double positionQuantity = 0.0;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t positionGeneration = 0;
    std::uint64_t fxCashGeneration = 0;
};

// Positive-only terminal evidence. A complete empty snapshot means only that
// the broker completed both bounded queries; it is never permission to infer
// that an absent correlation was rejected or never sent.
struct IBAuthoritativeTerminalCorrelationSnapshot {
    std::uint64_t connectionEpoch = 0;
    std::uint64_t generation = 0;
    bool complete = false;
    // Exposure generation observed at the completedOrders+executions End
    // barrier. A risk snapshot predating this value cannot prove flatness.
    std::uint64_t exposureGeneration = 0;
    std::string reasonCode;
    std::map<std::string, long> terminalOrderIdsByCorrelation;
    std::map<std::string, std::string> terminalStatusesByCorrelation;
    std::set<long> executionOrderIds;
};

// One adapter-lock sample used only by PAPER recovery finalization.  It binds
// the active, terminal, risk, position and post-fill views so callers cannot
// compose a false zero from independently sampled generations.
struct IBAuthoritativeRecoveryAuditSnapshot {
    IBAuthoritativeCorrelationSnapshot active;
    IBAuthoritativeTerminalCorrelationSnapshot terminal;
    IBAuthoritativeRiskSnapshot risk;
    std::map<std::string, double> positionQuantities;
    bool postFillRiskReconciliationPending = false;
    std::uint64_t exposureGeneration = 0;
    std::uint64_t terminalExposureGeneration = 0;
    std::uint64_t riskAbsorbedExposureGeneration = 0;
    bool barrierComplete = false;
    bool newConnectionEpochRequired = false;
    std::string reasonCode;
};

struct IBFinalOrderSendContext {
    bool exactReduceOnly = false;
    bool proveFlatOnly = false;
    bool authoritativeQuoteBound = false;
    std::string instrument;
    std::string quoteSubscriptionId;
    double quoteBid = 0.0;
    double quoteAsk = 0.0;
    std::uint64_t quoteObservedAtMs = 0;
    std::uint64_t quoteStaleAfterMs = 0;
};

class HeptaIBGatewayAdapter {
public:
    HeptaIBGatewayAdapter();
    explicit HeptaIBGatewayAdapter(std::unique_ptr<IIBApiWrapper> api);
    HeptaIBGatewayAdapter(
        std::unique_ptr<IIBApiWrapper> api,
        const std::function<std::unique_ptr<IIBApiWrapper>()>& reconnectApiFactory);
    ~HeptaIBGatewayAdapter();

    bool Init(const HeptaIBConfig& cfg);
    // Re-arm the one-shot CASH balance attestation before an in-process
    // transport reconnect. Immutable campaign baselines remain unchanged;
    // only the durable post-fill checkpoint becomes the expected first account
    // snapshot for the next connection epoch.
    bool PrepareReconnectCashAttestation(
        const std::map<std::string, double>& observedBalances,
        std::string& reason);
    bool Connect();
    void Disconnect();
    bool PollOnce(int timeoutMs);
    bool TryDequeueEvent(IBEvent& outEvent);
    long GetLastValidOrderId() const;
    bool ReqAccountSummary();
    bool ReqPositions();
    // Atomically starts a coherent account+positions generation. It rejects
    // while either leg is still in flight so post-fill retry cannot supersede
    // only half of the authoritative risk snapshot.
    bool ReqRiskRefresh();
    // Final leg of a strict recovery barrier. The caller must first complete
    // fresh account-wide open-order and terminal-correlation requests in this
    // broker epoch. Unlike an ordinary post-fill refresh, successful
    // completion can publish a recovery-audit barrier.
    bool ReqRecoveryAuditRiskRefresh();
    // Economic fill callbacks immediately close the final broker-send gate,
    // before the runtime can consume and reconcile the callback.  The gate is
    // released only after a newer coherent account+positions snapshot proves
    // the exact post-fill position.
    bool HasPendingPostFillRiskReconciliation() const;
    bool HasPendingLivePostFillRiskReconciliation() const;
    bool AcknowledgePostFillRiskReconciled(long orderId);
    bool GetOrderRiskBaseline(long orderId, IBOrderRiskBaseline& out) const;
    bool ReqOpenOrders();
    bool ReqAuthoritativeOpenOrders();
    bool ReqTerminalCorrelations();
    bool ReqMktData(int reqId, const IBContractLite& c);
    bool CancelMktData(int reqId);
    // Formal quote dispatch must hold this fence while draining ingress and
    // issuing ReqMktData; callback wrappers use the admission state at ingress.
    std::recursive_mutex& EventIngressFence();
    void BeginEventIngressAdmission();
    void EndEventIngressAdmission();
    void FlushEventIngressAdmission();
    void CompleteEventIngressAdmission();
    bool EventIngressAdmissionFailed() const;
    bool IsConnected() const;
    std::uint64_t GetConnectionEpoch() const;
    bool GetBrokerConnectionIdentity(
        IBBrokerConnectionIdentity& identity, std::string& reason) const;
    bool PlaceOrder(const IBContractLite& c, const IBOrderLite& o, long* outOrderId = nullptr);
    bool PlaceOrderCorrelated(const IBContractLite& c, const IBOrderLite& o,
                              const std::string& venueCorrelationId,
                              long* outOrderId = nullptr,
                              const IBFinalOrderSendContext* context = nullptr);
    bool PlaceReduceOnlyOrderCorrelated(
        const IBContractLite& contract,
        const IBOrderLite& order,
        const std::string& instrument,
        double expectedPositionQuantity,
        std::uint64_t expectedConnectionEpoch,
        std::uint64_t expectedPositionGeneration,
        const std::string& expectedQuoteSubscriptionId,
        std::uint64_t expectedQuoteObservedAtMs,
        std::uint64_t expectedQuoteStaleAfterMs,
        const std::string& venueCorrelationId,
        long* outOrderId = nullptr,
        double expectedQuoteBid = 0.0,
        double expectedQuoteAsk = 0.0);
    bool ProveAndCommitFlatNoop(
        const std::string& instrument,
        std::uint64_t expectedConnectionEpoch,
        std::uint64_t expectedPositionGeneration,
        const std::function<bool()>& durableCommit,
        bool* commitAttempted,
        std::string* reason = nullptr);
    bool CancelOrder(long orderId);
    bool CanCancelOrder(long orderId, std::string* suppressReason = nullptr) const;
    const char* GetStatusString() const;
    std::string GetPositionSummary() const;

    void UpdateReferencePrice(double price);
    void SetRuntimeFlattenOnly(bool enabled, const std::string& reason = "");
    void SetPrePlaceOrderSendCheck(
        const std::function<bool(
            const IBFinalOrderSendContext*,
            const IBContractLite&, const IBOrderLite&,
            std::string*)>& check);

    bool IsOrderGateOpen() const;
    bool IsCircuitBreakerTripped() const;
    bool IsEventStreamAuthoritative() const;
    std::uint64_t GetLastEventOverflowGeneration() const;
    bool MarkAuthoritativeResyncComplete(std::uint64_t overflowGeneration);
    int GetTodayOrderCount() const;
    bool RunPreflightChecks(std::string& reason) const;
    bool RunPreflightChecksDetailed(std::string& reasonCode, std::string& detail) const;
    void NotifyErrorEvent(int ibErrorCode = 0);
    std::string GetLastRejectReason() const;
    IBAuthoritativeCorrelationSnapshot GetAuthoritativeCorrelationSnapshot() const;
    IBAuthoritativeTerminalCorrelationSnapshot
        GetAuthoritativeTerminalCorrelationSnapshot() const;
    IBAuthoritativeRiskSnapshot GetAuthoritativeRiskSnapshot() const;
    IBAuthoritativeRecoveryAuditSnapshot
        GetAuthoritativeRecoveryAuditSnapshot() const;
    // Idempotently requires a broker epoch newer than the first recovery-audit
    // attempt. Runtime composition consumes newConnectionEpochRequired by
    // scheduling its in-process reconnect state machine; no service manager or
    // external broker mutation is performed here.
    IBAuthoritativeRecoveryAuditSnapshot BeginRecoveryAuditBarrier();
    // Irreversible, one-way transport boundary for PAPER terminalization.
    // Under the adapter API mutex it polls once, applies/dequeues every queued
    // callback, disables all future Connect/send paths, disconnects the
    // wrapper, drains callbacks queued by Disconnect itself, and returns the
    // final frozen composite snapshot.  Unlike ordinary Disconnect(), it does
    // not erase the snapshot being certified.
    bool HaltTransportForTerminalAudit(
        std::vector<IBEvent>& drainedEvents,
        IBAuthoritativeRecoveryAuditSnapshot& frozenSnapshot,
        std::string& reason);
    bool IsTerminalTransportHalted() const;
    bool IsTerminalTransportDrainVerified() const;
    std::uint64_t TerminalCallbacksInFlight() const;
    std::map<std::string, double> GetAuthoritativePositionQuantities() const;
    std::map<std::string, double>
        GetAuthoritativeFxCashPositionQuantities() const;
    std::map<std::string, IBAuthoritativeFxCashExposure>
        GetAuthoritativeFxCashExposures() const;
    bool ResolveAuthoritativePositionQuantity(
        const std::string& instrument,
        const InstrumentRef& contract,
        double& quantity,
        std::string& reason) const;

private:
    long NextOrderId();
    bool IsSameTradingDay() const;
    bool IsDuplicateOrder(const IBContractLite& c, const IBOrderLite& o, std::time_t nowTs) const;
    void RememberLastOrder(const IBContractLite& c, const IBOrderLite& o, std::time_t nowTs);
    bool IsPaperAccount(const std::string& account) const;
    bool IsAccountWhitelisted(const std::string& account) const;
    bool PlaceOrderInternal(const IBContractLite& c, const IBOrderLite& o,
                            long* outOrderId,
                            const IBFinalOrderSendContext* context = nullptr);
    bool RejectOrder(
        const IBContractLite& contract,
        const std::chrono::steady_clock::time_point& startedAt,
        const std::string& reason, const std::string& extraJson);
    bool ValidateOrderRequest(
        const IBContractLite& contract, const IBOrderLite& order,
        std::string& reason, std::string& detail) const;
    void ResetDailyRiskStateIfNeeded();
    bool CircuitBreakerAllowsOrder(std::time_t nowTs);
    void PruneOrderAttemptTimes();
    void PopulateDeterministicRiskContext(
        const IBContractLite& contract, const IBOrderLite& order);
    bool RunFinalOrderSendCheck(
        const IBFinalOrderSendContext* context,
        const IBContractLite& contract, const IBOrderLite& order,
        std::string& reason);
    bool ResolveCashFxInstrumentForOrder(
        const IBFinalOrderSendContext* context,
        const IBContractLite& contract, std::string& instrument,
        std::string& reason) const;
    bool BuildOrderRiskBaseline(
        const IBFinalOrderSendContext* context,
        const IBContractLite& contract, const IBOrderLite& order,
        IBOrderRiskBaseline& baseline, bool& hasBaseline,
        std::string& reason, std::string& detail);
    bool SubmitValidatedOrder(
        long orderId, const IBContractLite& contract,
        const IBOrderLite& order, std::time_t nowTs,
        const IBOrderRiskBaseline* baseline, long* outOrderId,
        const std::chrono::steady_clock::time_point& startedAt);
    bool BeginOpenOrderRefresh(bool accountWide);
    bool EncodeVenueOrderRef(
        const std::string& correlationId, std::string& orderRef,
        std::string& reason) const;
    bool DecodeVenueOrderRef(
        const std::string& orderRef, std::string& correlationId,
        std::string& reason) const;
    bool DequeueCurrentEpochEvent(IBEvent& event);
    bool CorrelateOpenOrderEvent(const IBEvent& event);
    void ApplyEventStateTransition(const IBEvent& event);
    void ApplyOrderStatusTransition(const IBEvent& event);
    void DispatchPendingCancelIfAcknowledged(long orderId,
                                             const std::string& status,
                                             bool economicTerminal,
                                             bool authoritativeOpenOrderAck = false);
    void ApplyExecutionDetailsTransition(const IBEvent& event);
    void ApplyBrokerErrorTransition(const IBEvent& event);
    void ApplyEventQueueOverflow(const IBEvent& event);
    void RetireTerminalActiveOrder(long orderId, bool eraseRiskBaseline);
    void ApplyRiskSnapshotEvent(const IBEvent& event);
    void ApplyAccountValueRiskEvent(const IBEvent& event);
    void CompleteAccountRiskRefresh();
    bool HasCompletePendingFxCashSnapshot() const;
    bool InitialFxCashAttestationMatches() const;
    void CommitAccountRiskRefresh();
    void RejectAccountRiskRefresh(bool initialAttestationMatches);
    void ApplyPositionSnapshotItem(const IBEvent& event);
    void ApplyPositionMonitorUpdate(const IBEvent& event);
    void CompletePositionRiskRefresh();
    void ApplyActiveCorrelationEvent(const IBEvent& event,
                                     bool acceptedBrokerOpenOrder);
    void ApplyTerminalCorrelationEvent(const IBEvent& event);
    void PublishPositionEvent(const IBEvent& event);
    bool MergeIncrementalActiveOrder(long orderId,
                                     const std::string& correlationId);
    void InvalidateCorrelationSnapshot(const std::string& reason);
    void InvalidateTerminalCorrelationSnapshot(const std::string& reason);
    void FinalizeTerminalCorrelationSnapshot();
    void InvalidateRiskSnapshot(const std::string& reason);
    bool BeginCoherentRiskRefresh(bool recoveryAuditBarrier);
    void CompleteCoherentRiskRefreshIfReady();
    bool ObserveEconomicFill(const IBEvent& event);
    bool BeginBrokerMutation(const std::string& reason);
    void InvalidateRecoveryAuditBarrier(const std::string& reason);
    bool HaltTransportForTerminalAuditLocked(
        std::vector<IBEvent>& drainedEvents,
        IBAuthoritativeRecoveryAuditSnapshot& frozenSnapshot,
        std::string& reason);
    IBAuthoritativeRecoveryAuditSnapshot
        BuildRecoveryAuditSnapshotLocked() const;
    void RefreshGrossAbsolutePosition();
    void RefreshAuthoritativeFxCashPositions();
    bool ConsumeFxCashAccountValue(const IBEvent& event,
                                   bool initialSnapshot);
    void BindEventIngressFence();

    void EmitObsEvent(const char* eventName, const std::string& fieldsJson = "") const;
    void EmitLatency(const char* path, const char* stage, long latencyMs, bool ok, const std::string& fieldsJson = "") const;

    HeptaIBConfig m_cfg;
    bool m_connected;
    bool m_apiConnectAttempted = false;
    std::unique_ptr<IIBApiWrapper> m_api;
    std::function<std::unique_ptr<IIBApiWrapper>()> m_reconnectApiFactory;
    std::shared_ptr<std::recursive_mutex> m_eventIngressFence;

    bool m_circuitBreakerTripped = false;
    int m_todayOrderCount = 0;
    int m_consecutiveErrorCount = 0;
    int m_errorFuseScore = 0;
    std::time_t m_circuitBreakerTripTs = 0;
    int m_dayOfYear = -1;
    long m_localOrderSeed = 1;

    double m_lastReferencePrice = 0.0;
    std::time_t m_lastReferencePriceTs = 0;

    std::string m_lastOrderSig;
    std::time_t m_lastOrderTs = 0;

    std::unordered_map<long, std::chrono::steady_clock::time_point> m_orderSubmitTs;
    std::deque<std::chrono::steady_clock::time_point> m_orderAttemptTimes;
    std::unordered_map<long, std::chrono::steady_clock::time_point> m_cancelSubmitTs;
    // A cancel may arrive before IB emits Submitted/OpenOrder.  Keep the
    // intent local and dispatch exactly once when broker acknowledgement
    // establishes that the order exists at the venue.
    std::unordered_set<long> m_pendingCancelOrderIds;
    std::string m_lastRejectReason;
    std::unordered_map<std::string, double> m_symbolNetPosition;
    IbOrderLifecycleTracker m_orderLifecycle;
    std::unordered_map<int, int> m_errorCodeCounts;
    // Hot-path risk evaluation cache/scratch to reduce per-order allocations.
    // The adapter uses the same venue-independent policy as Simulator and
    // the PAPER execution guard; transport checks remain in this class.
    DeterministicRiskLimits m_cachedRiskLimits;
    DeterministicRiskContext m_riskCtxScratch;
    bool m_eventStreamAuthoritative = true;
    std::uint64_t m_lastEventOverflowGeneration = 0;
    std::uint64_t m_connectionEpoch = 0;
    std::uint64_t m_correlationGeneration = 0;
    bool m_correlationRefreshPending = false;
    bool m_correlationRefreshConflict = false;
    IBAuthoritativeCorrelationSnapshot m_correlationSnapshot;
    std::map<std::string, long> m_pendingCorrelationOrderIds;
    std::set<long> m_pendingActiveOrderIds;
    std::set<long> m_postFillReconciliationOrderIds;
    std::map<long, std::uint64_t> m_postFillExposureGenerationByOrderId;
    std::map<long, double> m_observedEconomicFillQuantityByOrderId;
    std::map<long, IBOrderRiskBaseline> m_orderRiskBaselines;
    std::uint64_t m_terminalCorrelationGeneration = 0;
    bool m_terminalCorrelationRequestIssuedForEpoch = false;
    int m_terminalExecutionRequestId = 0;
    bool m_completedOrdersRefreshPending = false;
    bool m_executionsRefreshPending = false;
    bool m_terminalCorrelationRefreshConflict = false;
    IBAuthoritativeTerminalCorrelationSnapshot m_terminalCorrelationSnapshot;
    std::map<std::string, long> m_pendingTerminalOrderIds;
    std::map<std::string, std::string> m_pendingTerminalStatuses;
    std::map<long, std::string> m_pendingTerminalCorrelationsByOrderId;
    std::set<long> m_pendingExecutionOrderIds;
    std::uint64_t m_exposureGeneration = 0;
    std::uint64_t m_riskGeneration = 0;
    bool m_coherentRiskRefreshDispatching = false;
    bool m_coherentRiskRefreshPending = false;
    bool m_coherentRiskRefreshForRecoveryAudit = false;
    std::uint64_t m_coherentRiskRefreshConnectionEpoch = 0;
    std::uint64_t m_coherentRiskRefreshAccountGeneration = 0;
    std::uint64_t m_coherentRiskRefreshPositionGeneration = 0;
    std::uint64_t m_coherentRiskRefreshExposureGeneration = 0;
    std::uint64_t m_coherentRiskRefreshActiveGeneration = 0;
    std::uint64_t m_coherentRiskRefreshTerminalGeneration = 0;
    std::uint64_t m_coherentRiskRefreshMutationGeneration = 0;
    bool m_accountRefreshPending = false;
    bool m_positionsRefreshPending = false;
    bool m_accountRefreshObserved = false;
    bool m_accountReadyObserved = false;
    bool m_accountReady = false;
    bool m_fxCashRefreshConflict = false;
    bool m_fxCashInitialAttestationPending = false;
    bool m_positionsRefreshConflict = false;
    IBAuthoritativeRiskSnapshot m_riskSnapshot;
    std::map<std::string, double> m_pendingPositionQuantities;
    std::map<std::string, InstrumentRef> m_pendingPositionContracts;
    std::map<std::string, double> m_authoritativePositionQuantities;
    std::map<std::string, InstrumentRef> m_authoritativePositionContracts;
    std::map<std::string, std::string> m_fxInstrumentByBaseCurrency;
    std::map<std::string, double> m_pendingFxCashBalances;
    std::map<std::string, double> m_authoritativeFxCashBalances;
    std::map<std::string, double> m_authoritativeFxPositionQuantities;
    std::map<std::string, IBAuthoritativeFxCashExposure>
        m_authoritativeFxCashExposures;
    std::uint64_t m_brokerMutationGeneration = 0;
    std::uint64_t m_recoveryAuditMinimumConnectionEpoch = 0;
    bool m_recoveryAuditBarrierComplete = false;
    std::uint64_t m_recoveryAuditBarrierConnectionEpoch = 0;
    std::uint64_t m_recoveryAuditBarrierAttemptedConnectionEpoch = 0;
    std::uint64_t m_recoveryAuditBarrierMutationGeneration = 0;
    std::string m_recoveryAuditBarrierReason;
    // The PAPER runtime installs this process-local guard. It is evaluated
    // under m_apiMutex after all adapter risk/preflight work and immediately
    // before the broker API call. Production configuration cannot replace it.
    std::function<bool(
        const IBFinalOrderSendContext*,
        const IBContractLite&, const IBOrderLite&,
        std::string*)> m_prePlaceOrderSendCheck;
    mutable std::recursive_mutex m_apiMutex;
    bool m_terminalTransportHalted = false;
    bool m_terminalTransportDrainVerified = false;
    std::uint64_t m_terminalCallbacksInFlight = 0;
};

inline bool HeptaIBGatewayAdapter::ResolveAuthoritativePositionQuantity(
    const std::string& instrument, const InstrumentRef& contract,
    double& quantity, std::string& reason) const
{
    const auto matchesContract = [](const InstrumentRef& expected,
                                    const InstrumentRef& observed) {
        return !expected.symbol.empty() && !expected.secType.empty() &&
            !expected.currency.empty() && observed.symbol == expected.symbol &&
            observed.secType == expected.secType &&
            observed.currency == expected.currency &&
            (expected.exchange.empty() || observed.exchange.empty() ||
             observed.exchange == expected.exchange) &&
            (expected.primaryExchange.empty() ||
             observed.primaryExchange == expected.primaryExchange) &&
            (expected.lastTradeDateOrContractMonth.empty() ||
             observed.lastTradeDateOrContractMonth ==
                 expected.lastTradeDateOrContractMonth) &&
            (expected.right.empty() || observed.right == expected.right) &&
            (expected.strike == 0.0 || observed.strike == expected.strike) &&
            (expected.multiplier.empty() ||
             observed.multiplier == expected.multiplier) &&
            (expected.tradingClass.empty() ||
             observed.tradingClass == expected.tradingClass) &&
            (expected.localSymbol.empty() ||
             observed.localSymbol == expected.localSymbol);
    };
    std::lock_guard<std::recursive_mutex> lock(m_apiMutex);
    quantity = 0.0;
    // The baseline-delta model is explicit configuration, not a heuristic on
    // secType alone. Generic adapter consumers without configured PAPER FX
    // baselines retain the ordinary position-snapshot path; the PAPER runtime
    // always configures every allowed CASH instrument and therefore cannot
    // fall through to reqPositions for EURUSD.
    if (contract.secType == "CASH" &&
        !m_cfg.authoritativeCashFxContracts.empty()) {
        const auto configured =
            m_cfg.authoritativeCashFxContracts.find(instrument);
        if (configured == m_cfg.authoritativeCashFxContracts.end() ||
            configured->second.symbol != contract.symbol ||
            configured->second.currency != contract.currency) {
            reason = "IB_FX_CASH_CONTRACT_UNRESOLVED";
            return false;
        }
        if (!m_riskSnapshot.accountComplete ||
            !m_riskSnapshot.fxCashComplete) {
            reason = m_riskSnapshot.reasonCode.empty() ?
                "IB_FX_CASH_BALANCE_NOT_COMPLETE" :
                m_riskSnapshot.reasonCode;
            return false;
        }
        const auto cash = m_authoritativeFxCashExposures.find(instrument);
        if (cash == m_authoritativeFxCashExposures.end() ||
            !std::isfinite(cash->second.campaignOwnedQuantity)) {
            reason = "IB_FX_CASH_BALANCE_MISSING";
            return false;
        }
        quantity = std::fabs(cash->second.campaignOwnedQuantity) <= 1e-6 ?
            0.0 : cash->second.campaignOwnedQuantity;
        reason.clear();
        return true;
    }
    const auto exact = m_authoritativePositionQuantities.find(instrument);
    if (exact != m_authoritativePositionQuantities.end()) {
        quantity = exact->second;
        reason.clear();
        return std::isfinite(quantity);
    }
    std::size_t matches = 0;
    for (const auto& entry : m_authoritativePositionContracts) {
        if (!matchesContract(contract, entry.second)) continue;
        const auto position = m_authoritativePositionQuantities.find(entry.first);
        if (position == m_authoritativePositionQuantities.end() ||
            !std::isfinite(position->second)) {
            reason = "IB_POSITION_CONTRACT_STATE_INVALID";
            return false;
        }
        quantity = position->second;
        ++matches;
    }
    if (matches > 1) {
        quantity = 0.0;
        reason = "IB_POSITION_CONTRACT_AMBIGUOUS";
        return false;
    }
    reason.clear();
    return true;
}
