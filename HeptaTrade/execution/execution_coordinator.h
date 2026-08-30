#pragma once

#include "execution_authority.h"
#include "paper_terminal_mutation_manifest.h"
#include "../oms_journal.h"

#include <functional>
#include <cstdint>
#include <mutex>
#include <string>
#include <set>
#include <map>
#include <unordered_map>
#include <unordered_set>
#include <vector>

struct ExecutionOrderOwner
{
    std::string agentId;
    std::string sessionId;
    std::string strategy;
    std::string account;
    std::string executionDomain;
    std::string instrument;
    std::string side;
};

// Shared canonical hash used by the split cancel dispatcher.
std::string CancelRequestHash(const IbCancelOrderCommand& command);

enum class ExecutionOrderOwnerLookupStatus
{
    Found,
    Missing,
    Busy
};

struct ExecutionOwnedActiveOrderProjection
{
    bool complete = false;
    std::set<long> ownedOrderIds;
    std::set<long> unmappedOrderIds;
};

struct ExecutionCoordinatorCallbacks
{
    std::function<bool(const InstrumentRef&, const OrderIntent&, long*)> placeIbOrder;
    std::function<bool(const InstrumentRef&, const OrderIntent&,
                       const std::string&, long*)> placeIbOrderCorrelated;
    // PAPER-only full-command dispatch preserves the privileged quote binding
    // across the durable intent/send-attempt writes. Generic and simulator
    // venues continue to use the narrower callbacks above.
    std::function<bool(const IbPlaceOrderCommand&, const std::string&, long*)>
        placeIbOrderCommandCorrelated;
    // Durable pre-adapter risk-increase check after the send-attempt marker.
    // PAPER installs a second check inside the adapter send lock immediately
    // before broker IO, so lock wait and adapter preflight cannot stale this
    // earlier observation.
    std::function<bool(const IbPlaceOrderCommand&, std::string*)>
        preVenuePlaceCheck;
    std::function<bool(const FlattenPositionCommand&,
                       const AuthoritativeFlattenPlan&, std::string*)>
        preVenueFlattenCheck;
    // Final zero-position proof performed by the venue adapter while holding
    // the same lock that serializes authoritative position/order updates.
    // A zero-position flatten may be journaled as a no-op only after this
    // callback proves the position is still flat, there are no active orders,
    // and the final PAPER runtime/kill-switch check still permits flattening.
    std::function<bool(
        const AuthoritativeFlattenPlan&,
        const std::function<bool()>&,
        bool*, std::string*)> proveAndCommitIbFlatNoop;
    std::function<bool(long)> cancelIbOrder;
    std::function<bool(const AuthoritativeFlattenPlan&, const std::string&,
                       long*)> placeIbReduceOnlyOrderCorrelated;
    std::function<bool(long, std::string*)> canCancelIbOrder;
    std::function<std::string()> lastIbRejectReason;
    std::function<void(const std::string&, long, const std::string&, const std::string&, const std::string&, const std::string&)> trackOrder;
    std::function<bool(const AgentExecutionContext&, const std::string&, std::string*)> validateDecisionLease;
    std::function<bool(const IbPlaceOrderCommand&, long, std::string*)> onIbOrderPlaced;
    std::function<bool(const IbCancelOrderCommand&, std::string*)> onIbCancelSent;
};

// The only mutation entry point used by Agent-facing trading tools.
// It deliberately owns no broker credentials and delegates venue IO through
// callbacks supplied by the HeptaTrader process.
class ExecutionCoordinator : public ExecutionAuthority
{
public:
    ExecutionCoordinator(OmsJournal& journal, const ExecutionCoordinatorCallbacks& callbacks);

    ExecutionCommandResult PlaceOrder(const PlaceOrderCommand& command) override;
    ExecutionCommandResult CancelOrder(const CancelOrderCommand& command) override;
    // Planless flatten is deliberately unavailable. Only the privileged
    // policy authority can construct a plan from service-owned state.
    ExecutionCommandResult FlattenPosition(
        const FlattenPositionCommand& command) override;
    ExecutionCommandResult ExecuteAuthoritativeFlatten(
        const FlattenPositionCommand& command,
        const AuthoritativeFlattenPlan& plan);
    // Returns true when this command identity already exists and fills out
    // with Duplicate/Uncertain/conflict. New commands return false. Policy
    // layers call this before dynamic risk gates so exact retries remain stable.
    bool PrecheckPlaceIbOrder(const IbPlaceOrderCommand& command,
                              ExecutionCommandResult& out) const;
    bool IsDurablePlaceReplay(const IbPlaceOrderCommand& command) const;
    bool PrecheckFlattenPosition(
        const FlattenPositionCommand& command,
        ExecutionCommandResult& out) const;
    bool IsDurableFlattenReplay(
        const FlattenPositionCommand& command) const;
    // Returns durable broker-send attempts in the requested account/domain.
    // The timestamp is persisted before venue IO, so a crash after the send
    // cannot reset a service-local rolling rate budget.
    void GetPlaceSendAttemptTimes(const std::string& account,
                                  const std::string& executionDomain,
                                  std::int64_t cutoffMs,
                                  std::vector<std::int64_t>& out) const;

    // Rebuild idempotency and ownership projections from durable OMS events.
    // An intent without a terminal/send receipt is UNCERTAIN and blocks new
    // mutations until the caller completes broker reconciliation.
    bool RecoverFromJournal(std::string& reason);

    bool IsMutationBlocked(std::string* reason = nullptr) const;
    bool BeginBrokerReconnectFence(std::string& reason);
    bool EndBrokerReconnectFence(std::string& reason);
    bool EnterPaperTerminalFence(
        const AgentExecutionContext& context,
        const std::string& finalizationId,
        std::string& reason);
    // Atomically persists and verifies the irreversible v2 terminal fence,
    // closes every mutation path, and projects the complete durable mutation
    // universe for one account/domain while holding the same coordinator
    // mutex.  The caller persists that immutable projection as HPM1.
    bool EnterPaperTerminalFenceAndProject(
        const PaperTerminalFenceBinding& binding,
        PaperTerminalMutationUniverse& universe,
        std::string& reason);
    // Retained for source compatibility with the legacy monolith, but it is
    // deliberately fail-closed in the standalone authority.  A mutation
    // block can only be cleared by a method that records/validates the
    // corresponding authoritative reconciliation proof (for example
    // ResolveUncertain* or ResolveProjectionBlockAfterAuthoritativeResync).
    void ResetMutationBlockAfterReconcile();
    bool ResolveProjectionBlockAfterAuthoritativeResync();
    void RecordOrderTerminal(long orderId);
    bool GetOrderOwner(long orderId, ExecutionOrderOwner& out) const;
    ExecutionOrderOwnerLookupStatus TryGetOrderOwner(
        long orderId, ExecutionOrderOwner& out) const;
    // Projects a complete broker-global active-order snapshot through the
    // durable coordinator owner map while holding one coordinator lock. A
    // missing owner makes the projection incomplete; known foreign owners
    // remain visible in the caller's global snapshot but are not selected.
    ExecutionOwnedActiveOrderProjection ProjectOwnedActiveOrders(
        const std::set<long>& authoritativeActiveOrderIds,
        const AgentExecutionContext& ownerScope) const;
    bool AuditRecoveryOwner(
        const std::set<long>& authoritativeActiveOrderIds,
        bool authoritativeOpenOrdersComplete,
        const AgentExecutionContext& ownerScope,
        std::uint64_t& activeOrderCount,
        std::uint64_t& uncertainCommandCount,
        std::string& reason) const;
    bool GetCommandStatus(const std::string& agentId,
                          const std::string& sessionId,
                          const std::string& commandId,
                          ExecutionCommandResult& out) const;
    bool EnterRecoveryOnlyOwner(const std::string& agentId,
                                const std::string& sessionId,
                                std::uint64_t ingressFence,
                                std::string& reason);
    bool EnterRecoveryOnlyForControl(const ExecutionControlCommand& command,
                                     ExecutionControlResult& result);
    std::size_t FenceSessionOwner(const std::string& agentId, const std::string& sessionId);
    bool IsSessionOwnerFenced(const std::string& agentId, const std::string& sessionId) const;
    bool IsSessionOwnerRecoveryOnly(const std::string& agentId,
                                    const std::string& sessionId) const;
    bool AuditAndReleaseSessionOwnerFence(const std::string& agentId,
                                          const std::string& sessionId,
                                          bool authoritativeOpenOrdersComplete,
                                          std::string& reason);
    bool ReconcileOrderOwners(const std::set<long>& authoritativeActiveOrderIds,
                              bool authoritativeOpenOrdersComplete,
                              std::size_t& removedOwners,
                              std::string& reason);
    // Resolve replayed UNCERTAIN place intents from a complete, service-owned
    // venue correlation snapshot. Missing correlations are durably rejected;
    // present correlations are durably accepted with the authoritative ID.
    bool ResolveUncertainPlaceCommands(
        const std::map<std::string, long>& authoritativeCorrelations,
        bool authoritativeSnapshotComplete,
        std::size_t& resolvedCommands,
        std::string& reason,
        bool resolveMissingAsRejected = true);
    // Resolve replayed UNCERTAIN cancel requests only from positive broker
    // evidence. An active order remains uncertain; a broker terminal status
    // or execution resolves the command without ever resending the cancel.
    bool ResolveUncertainCancelCommands(
        const std::set<long>& authoritativeActiveOrderIds,
        bool authoritativeActiveSnapshotComplete,
        const std::map<long, std::string>& authoritativeTerminalStatuses,
        const std::set<long>& authoritativeExecutionOrderIds,
        bool authoritativeTerminalSnapshotComplete,
        std::size_t& resolvedCommands,
        std::string& reason);

    static const char* StatusName(ExecutionCommandStatus status);

private:
    struct RequestRecord
    {
        ExecutionCommandStatus status = ExecutionCommandStatus::Uncertain;
        long orderId = -1;
        std::string reasonCode;
        std::string detail;
        std::string requestHash;
        std::string venueCorrelationId;
        std::string operation;
        AgentExecutionContext context;
        std::string instrument;
        std::string side;
        double quantity = 0.0;
        double price = 0.0;
        bool durableMutationIntent = false;
    };

    struct PlaceSendAttempt
    {
        std::string requestKey;
        std::string account;
        std::string executionDomain;
        std::int64_t tsMs = 0;
    };

    struct PlaceOrderDispatchContext
    {
        std::string requestKey;
        std::string requestHash;
        std::string venueCorrelationId;
        std::string instrument;
        double eventPrice = 0.0;
    };

    struct AuthoritativeFlattenDispatchContext
    {
        std::string requestKey;
        std::string requestHash;
        std::string venueCorrelationId;
        std::string snapshotEvidence;
    };

    ExecutionCommandResult DuplicateResultLocked(const AgentExecutionContext& context) const;
    ExecutionCommandResult IdempotencyConflictLocked(const AgentExecutionContext& context,
                                                      long orderId) const;
    ExecutionCommandResult RejectLocked(const AgentExecutionContext& context,
                                        const std::string& reasonCode,
                                        const std::string& detail,
                                        long orderId = -1,
                                        const std::string& requestHash = std::string());
    ExecutionCommandResult UncertainPlaceOutcomeLocked(
        const IbPlaceOrderCommand& command,
        const PlaceOrderDispatchContext& dispatch,
        long orderId,
        const std::string& detail);
    ExecutionCommandResult CompletePlaceOrderLocked(
        const IbPlaceOrderCommand& command,
        const PlaceOrderDispatchContext& dispatch,
        long orderId);
    bool PreVenuePlaceAllowedLocked(
        const IbPlaceOrderCommand& command,
        const PlaceOrderDispatchContext& dispatch,
        ExecutionCommandResult& rejection);
    ExecutionCommandResult DispatchPlaceOrderLocked(
        const IbPlaceOrderCommand& command,
        const PlaceOrderDispatchContext& dispatch);
    ExecutionCommandResult RejectAuthoritativeFlattenLocked(
        const FlattenPositionCommand& command,
        const AuthoritativeFlattenPlan& plan,
        const AuthoritativeFlattenDispatchContext& dispatch,
        const std::string& reasonCode,
        const std::string& detail);
    ExecutionCommandResult UncertainAuthoritativeFlattenLocked(
        const FlattenPositionCommand& command,
        const AuthoritativeFlattenPlan& plan,
        const AuthoritativeFlattenDispatchContext& dispatch,
        long orderId,
        const std::string& detail);
    ExecutionCommandResult CompleteAuthoritativeFlattenLocked(
        const FlattenPositionCommand& command,
        const AuthoritativeFlattenPlan& plan,
        const AuthoritativeFlattenDispatchContext& dispatch,
        long orderId);
    ExecutionCommandResult DispatchAuthoritativeFlattenLocked(
        const FlattenPositionCommand& command,
        const AuthoritativeFlattenPlan& plan,
        const AuthoritativeFlattenDispatchContext& dispatch);
    ExecutionCommandResult HandleCancelProjectionFailureLocked(
        const CancelOrderCommand& command,
        const std::string& instrument,
        const std::string& side,
        const std::string& requestHash,
        const std::string& requestKey,
        const std::string& projectionReason);
    ExecutionCommandResult HandleDeferredCancelLocked(
        const CancelOrderCommand& command,
        const AgentExecutionContext& context,
        const std::string& instrument,
        const std::string& side,
        const std::string& requestHash,
        const std::string& requestKey,
        RequestRecord& pending);
    bool TryCancelAtVenueLocked(long orderId, std::string& rejectReason);
    ExecutionCommandResult CompleteAuthoritativeFlattenNoopLocked(
        const FlattenPositionCommand& command,
        const AuthoritativeFlattenPlan& plan,
        const AuthoritativeFlattenDispatchContext& dispatch);
    OmsJournalEvent BuildEvent(const AgentExecutionContext& context,
                               const std::string& eventType,
                               long orderId,
                               const std::string& instrument,
                               const std::string& side,
                               double qty,
                               double price,
                               const std::string& status,
                               const std::string& reason,
                               const std::string& riskCode,
                               const std::string& requestHash = std::string(),
                               const std::string& venueCorrelationId = std::string()) const;
    bool AppendOrBlockLocked(const OmsJournalEvent& event, const std::string& failureCode);
    void BlockMutationsLocked(const std::string& reason);
    void ResetRecoveryProjectionLocked();
    bool ApplyRecoveredPlaceReceiptLocked(
        const OmsJournalEvent& event,
        RequestRecord& record,
        const std::string& agentId);
    bool ApplyRecoveredOutcomeUncertainLocked(
        const OmsJournalEvent& event,
        RequestRecord& record);
    void ApplyRecoveredProjectionResolvedLocked();
    bool ApplyRecoveredOwnershipEventLocked(
        const OmsJournalEvent& event,
        const std::string& agentId);
    bool ApplyRecoveredPaperTerminalFenceLocked(
        const OmsJournalEvent& event,
        const std::string& agentId);
    bool EnterPaperTerminalFenceAndProjectLocked(
        const PaperTerminalFenceBinding& binding,
        PaperTerminalMutationUniverse& universe,
        std::string& reason);
    void TrackRecoveredSendAttemptLocked(
        const OmsJournalEvent& event,
        const std::string& requestKey);
    bool HydrateRecoveredRecordLocked(
        const OmsJournalEvent& event,
        const std::string& agentId,
        const std::string& commandId,
        RequestRecord& record);
    bool ApplyRecoveredCommandStateLocked(
        const OmsJournalEvent& event,
        RequestRecord& record,
        const std::string& agentId);
    void ApplyRecoveredEventLocked(const OmsJournalEvent& event);
    bool ValidateRecoveredProjectionLocked(std::string& reason);
    static std::string AgentSource(const std::string& agentId);
    static std::string AgentIdFromSource(const std::string& source);
    static std::string RequestKey(const std::string& agentId,
                                  const std::string& sessionId,
                                  const std::string& toolCallId);
    static std::string OwnerKey(const std::string& agentId, const std::string& sessionId);
    static std::string VenueCorrelationId(const AgentExecutionContext& context,
                                          const std::string& requestHash);

private:
    OmsJournal& m_journal;
    ExecutionCoordinatorCallbacks m_callbacks;
    mutable std::mutex m_mutex;
    std::unordered_map<std::string, RequestRecord> m_requests;
    std::unordered_map<long, ExecutionOrderOwner> m_orderOwners;
    std::unordered_set<std::string> m_fencedSessionOwners;
    std::unordered_map<std::string, std::uint64_t>
        m_recoveryOnlySessionOwners;
    std::vector<PlaceSendAttempt> m_placeSendAttempts;
    std::unordered_set<std::string> m_placeSendAttemptKeys;
    bool m_mutationBlocked = false;
    std::string m_mutationBlockReason;
    bool m_paperTerminalFencePresent = false;
    PaperTerminalFenceBinding m_paperTerminalFenceBinding;
};
