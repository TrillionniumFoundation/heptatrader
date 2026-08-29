#pragma once

#include "trading_contract.h"

#include <atomic>
#include <cstdint>
#include <string>

enum class ExecutionCommandStatus
{
    Accepted = 0,
    Rejected,
    Duplicate,
    Uncertain
};

// Immutable identity for one Execution Service daemon incarnation.  The epoch
// changes on every process start; the fencing generation is loaded from the
// pinned HFC credential.  It is deliberately distinct from a per-key
// DecisionLease generation carried by AgentExecutionContext.
struct ExecutionServiceIdentity
{
    std::string serviceEpoch;
    std::uint64_t serviceFencingGeneration = 0;
};

struct ExecutionServiceLifecycleGate
{
    ExecutionServiceLifecycleGate() : ready(false), terminalControlOnly(false) {}
    std::atomic<bool> ready;
    // A durable PAPER terminal latch keeps the process available only for
    // identity and idempotent terminal-witness queries.  It never reopens any
    // read, preview, cancel, flatten, or placement surface.
    std::atomic<bool> terminalControlOnly;
};

struct AgentExecutionContext
{
    std::string agentId;
    std::string sessionId;
    std::string toolCallId;
    std::string strategy;
    std::string account;
    std::string venue = "IB";
    std::string executionDomain;
    std::uint64_t decisionLeaseFencingToken = 0;
    std::uint64_t decisionLeaseGeneration = 0;
    bool allowCancelAny = false;
};

// Privileged, in-process-only binding for the exact authoritative quote that
// approved one new risk-increasing PAPER mutation.  For IB, subscriptionId is
// the opaque connection-epoch/subscription-generation/request-id identity.
// The Execution wire codec, preview fingerprint, and durable request hash
// deliberately exclude this structure; the PAPER policy overwrites it after
// validating service-owned quote state and before entering the coordinator.
struct AuthoritativePlaceQuoteBinding
{
    bool valid = false;
    std::string instrument;
    std::string subscriptionId;
    double bid = 0.0;
    double ask = 0.0;
    std::uint64_t observedAtMs = 0;
    std::uint64_t staleAfterMs = 0;
};

struct PlaceOrderCommand
{
    AgentExecutionContext context;
    InstrumentRef contract;
    OrderIntent order;
    std::string instrument;
    std::string timeInForce;
    double referencePrice = 0.0;
    long long expiresAtMs = 0;
    // Issued and consumed only by the authoritative Execution Service.  The
    // unprivileged Gateway merely relays this opaque, single-use credential.
    std::string previewPermit;
    AuthoritativePlaceQuoteBinding authoritativeQuoteBinding;
};

struct CancelOrderCommand
{
    AgentExecutionContext context;
    long orderId = -1;
    std::string instrument;
    std::string side;
};

// Agent callers select only a server-bound instrument.  Side, quantity and
// price are derived by the privileged Execution authority from one
// authoritative position/quote snapshot and are never accepted over the
// Agent-facing tool protocol.
struct FlattenPositionCommand
{
    AgentExecutionContext context;
    InstrumentRef contract;
    std::string instrument;
    // Opaque, single-use credential issued by PreviewFlattenPosition.  It is
    // consumed by the Execution Service and cleared before durable hashing.
    std::string previewPermit;
    // Execution-server-owned snapshot binding. These fields are never
    // serialized on the Gateway IPC request; permit consumption injects them
    // immediately before authority dispatch so Agent input cannot select a
    // different position, epoch or generation than the approved preview.
    bool hasAuthoritativePreviewSnapshot = false;
    double previewPositionQuantity = 0.0;
    std::uint64_t previewPositionConnectionEpoch = 0;
    std::uint64_t previewPositionGeneration = 0;
    // Opaque service-local canonical binding for the complete approved plan.
    // It is stored in the permit record and never encoded on the IPC wire.
    std::string authoritativePreviewPlanBinding;
};

// Privileged, normalized plan produced from service-owned state.  The venue
// adapter revalidates the exact position epoch/generation/quantity under its
// send lock immediately before broker I/O.
struct AuthoritativeFlattenPlan
{
    InstrumentRef contract;
    OrderIntent order;
    std::string instrument;
    std::string timeInForce;
    double referencePrice = 0.0;
    double expectedPositionQuantity = 0.0;
    std::uint64_t positionConnectionEpoch = 0;
    std::uint64_t positionGeneration = 0;
    std::string quoteSubscriptionId;
    std::uint64_t quoteObservedAtMs = 0;
    std::uint64_t quoteStaleAfterMs = 0;
    double quoteBid = 0.0;
    double quoteAsk = 0.0;
    // Service-owned PAPER profile discriminator.  It is empty for the
    // byte-compatible legacy local MKT plan and explicitly bound for the
    // externally finalized atomic LMT plan.
    std::string profileOrderMode;
};

struct ExecutionCommandResult
{
    ExecutionCommandStatus status = ExecutionCommandStatus::Rejected;
    std::string commandId;
    long orderId = -1;
    std::string reasonCode;
    std::string detail;
    std::string serviceEpoch;
    std::uint64_t serviceFencingGeneration = 0;
    // In-process-only binding returned by the privileged flatten preview
    // authority. The response codec deliberately does not serialize it.
    bool hasAuthoritativeFlattenSnapshot = false;
    double authoritativeFlattenPositionQuantity = 0.0;
    std::uint64_t authoritativeFlattenConnectionEpoch = 0;
    std::uint64_t authoritativeFlattenPositionGeneration = 0;
    std::string authoritativeFlattenPlanBinding;
};

struct ExecutionControlCommand
{
    AgentExecutionContext context;
    std::string targetCommandId;
    std::uint64_t recoveryIngressFence = 0;
    // The one-way Execution terminalization happens before the supervisor
    // may persist its HSL8 terminal receipt/ACK ledger. It is bound to the
    // preliminary HSL7 AUDIT_SEALED diagnostic receipt; targetCommandId is
    // the finalization id for this operation.
    std::string terminalPreliminaryReceiptSha256;
};

struct ExecutionControlResult
{
    ExecutionCommandStatus status = ExecutionCommandStatus::Rejected;
    std::string commandId;
    std::string targetCommandId;
    ExecutionCommandStatus targetStatus = ExecutionCommandStatus::Rejected;
    long orderId = -1;
    std::uint64_t affectedCount = 0;
    bool mutationBlocked = false;
    // Typed evidence for one exact Execution owner audit.  The counts are
    // meaningful only when both flags are true and the returned account and
    // domain exactly match the requested server-bound owner context.
    bool ownerAuditAuthoritative = false;
    bool ownerAuditComplete = false;
    std::uint64_t ownerActiveOrderCount = 0;
    std::uint64_t ownerUncertainCommandCount = 0;
    std::uint64_t brokerConnectionEpoch = 0;
    std::uint64_t brokerActiveGeneration = 0;
    std::uint64_t brokerTerminalGeneration = 0;
    std::uint64_t brokerRiskGeneration = 0;
    std::uint64_t brokerAccountGeneration = 0;
    std::uint64_t brokerPositionGeneration = 0;
    std::uint64_t brokerFxCashGeneration = 0;
    std::uint64_t brokerExposureGeneration = 0;
    std::uint64_t brokerTerminalExposureGeneration = 0;
    std::uint64_t brokerRiskAbsorbedExposureGeneration = 0;
    std::uint64_t brokerGlobalActiveOrderCount = 0;
    bool brokerPostFillRiskReconciliationPending = false;
    bool brokerRecoveryAuditBarrierComplete = false;
    bool brokerRecoveryAuditNewConnectionEpochRequired = false;
    // Canonical non-exponent decimal strings. Zero is exactly "0"; -0,
    // floating JSON numbers and exponent notation are never emitted.
    std::string brokerPositionQuantity;
    std::string brokerGrossAbsolutePosition;
    // Typed one-way terminal witness.  These fields are meaningful only for
    // TerminalizeRecoveryOwner and are all fail-closed defaults elsewhere.
    std::string terminalizationServiceEpoch;
    std::uint64_t terminalizationServiceFencingGeneration = 0;
    std::uint64_t terminalizationGeneration = 0;
    std::string terminalLatchSha256;
    std::uint64_t terminalServiceProcessId = 0;
    std::uint64_t terminalServiceProcessStartTicks = 0;
    std::string terminalBrokerSocketIdentitySha256;
    std::string terminalMutationManifestFile;
    std::string terminalMutationManifestFileSha256;
    std::string terminalMutationManifestBodySha256;
    std::string terminalKnownMutationCommandSetSha256;
    std::uint64_t terminalKnownMutationCommandCount = 0;
    std::string terminalKnownCorrelationSetSha256;
    std::uint64_t terminalKnownCorrelationCount = 0;
    bool terminalMutationGateClosed = false;
    bool terminalBrokerTransportConnected = true;
    bool terminalBrokerEventIngressHalted = false;
    bool terminalBrokerCallbackQueueDrained = false;
    std::uint64_t terminalBrokerCallbacksInFlight = 0;
    bool terminalBrokerReconnectPermitted = true;
    bool terminalLatchDurable = false;
    bool terminalRuntimeLatchLoaded = false;
    bool terminalRuntimeVerified = false;
    bool terminalReplay = false;
    std::string ownerAccount;
    std::string ownerExecutionDomain;
    std::string reasonCode;
    std::string detail;
    std::string serviceEpoch;
    std::uint64_t serviceFencingGeneration = 0;
};

struct ExecutionReadCommand
{
    AgentExecutionContext context;
    std::string query;
    std::string instrument;
};

class ExecutionAuthority
{
public:
    virtual ~ExecutionAuthority() = default;
    virtual ExecutionCommandResult PlaceOrder(const PlaceOrderCommand& command) = 0;
    virtual ExecutionCommandResult CancelOrder(const CancelOrderCommand& command) = 0;
    virtual ExecutionCommandResult FlattenPosition(
        const FlattenPositionCommand& command)
    {
        ExecutionCommandResult result;
        result.commandId = command.context.toolCallId;
        result.reasonCode = "EXECUTION_FLATTEN_UNAVAILABLE";
        return result;
    }

    ExecutionCommandResult PlaceIbOrder(const PlaceOrderCommand& command)
    {
        return PlaceOrder(command);
    }

    ExecutionCommandResult CancelIbOrder(const CancelOrderCommand& command)
    {
        return CancelOrder(command);
    }
};

using IbPlaceOrderCommand = PlaceOrderCommand;
using IbCancelOrderCommand = CancelOrderCommand;

// Short, bounded control operations share the authenticated mutation socket.
// They never accept an Agent-supplied authoritative venue snapshot: the
// concrete Execution Service must obtain that state from its owned venue.
class ExecutionControlAuthority
{
public:
    virtual ~ExecutionControlAuthority() = default;
    virtual ExecutionControlResult QueryCommandStatus(
        const ExecutionControlCommand& command) = 0;
    virtual ExecutionControlResult FenceSessionOwner(
        const ExecutionControlCommand& command) = 0;
    virtual ExecutionControlResult ReleaseSessionOwnerFence(
        const ExecutionControlCommand& command) = 0;
    virtual ExecutionControlResult ReconcileAuthoritativeState(
        const ExecutionControlCommand& command) = 0;
    virtual ExecutionControlResult RecoveryAuditOwner(
        const ExecutionControlCommand& command)
    {
        ExecutionControlResult result;
        result.commandId = command.context.toolCallId;
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = "EXECUTION_OWNER_AUDIT_UNAVAILABLE";
        return result;
    }
    virtual ExecutionControlResult TerminalizeRecoveryOwner(
        const ExecutionControlCommand& command)
    {
        ExecutionControlResult result;
        result.commandId = command.context.toolCallId;
        result.targetCommandId = command.targetCommandId;
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = "EXECUTION_OWNER_TERMINALIZATION_UNAVAILABLE";
        return result;
    }
};

class ExecutionReadAuthority
{
public:
    virtual ~ExecutionReadAuthority() = default;
    // Evaluates the exact normalized order against current authoritative
    // venue/risk state without recording an intent, consuming a rate budget,
    // or performing venue I/O.
    virtual ExecutionCommandResult PreviewOrder(
        const PlaceOrderCommand& command) = 0;
    virtual ExecutionCommandResult PreviewFlattenPosition(
        const FlattenPositionCommand& command)
    {
        ExecutionCommandResult result;
        result.commandId = command.context.toolCallId;
        result.reasonCode = "EXECUTION_FLATTEN_PREVIEW_UNAVAILABLE";
        return result;
    }
    // Local Execution authority hook used only by the server to preserve
    // durable exactly-once/uncertain retry semantics without weakening permit
    // checks for new, rejected, or changed commands.  Implementations must
    // compare the full normalized place fingerprint.
    virtual bool IsDurablePlaceReplay(
        const PlaceOrderCommand&) const { return false; }
    virtual bool IsDurableFlattenReplay(
        const FlattenPositionCommand&) const { return false; }
    virtual ExecutionCommandResult ReadAuthoritativeState(
        const ExecutionReadCommand& command) = 0;
};
