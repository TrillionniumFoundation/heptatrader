#pragma once

#include "../execution/execution_authority.h"
#include "../intent/target_position_intent.h"

#include <atomic>
#include <chrono>
#include <mutex>
#include <functional>
#include <cstddef>
#include <cstdint>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

enum class TradingToolEffect
{
    Read = 0,
    Trade
};

enum class TradingToolCallStatus
{
    Ok = 0,
    PermissionDenied,
    InvalidTool,
    Rejected,
    Duplicate,
    Uncertain,
    Error
};

// Shared by the privileged registry, Unix result codec, and installed native
// client SDK.  Keep the transport ceiling in this installed dependency rather
// than making SDK consumers depend on a private host-only header.
struct TradingToolWireLimits
{
    static std::size_t MaximumResultEnvelopeBytes()
    {
        return 1024u * 1024u;
    }
};

struct TradingToolDescriptor
{
    std::string name;
    std::string description;
    std::string requiredCapability;
    TradingToolEffect effect = TradingToolEffect::Read;
    int timeoutMs = 1000;
    std::string inputSchema;
    std::string resultSchema;
};

struct TradingToolSession
{
    AgentExecutionContext executionContext;
    std::string environment; // Bound by the OS: WATCH or PAPER only.
    std::unordered_set<std::string> capabilities;
    // Server-derived visibility scope. The client cannot populate or widen it.
    std::unordered_set<std::string> visibleInstruments;
    std::unordered_map<std::string, InstrumentRef> boundInstrumentContracts;
    // Server-derived per-order quantity ceiling.  Zero means that an
    // in-process registry caller did not provide a session limit (the host
    // always populates this for mutation-capable sessions).  Target-position
    // planning uses this value for the derived delta, so an intent cannot
    // bypass the same ceiling enforced on raw order tools.
    double maxOrderQuantity = 0.0;
};

struct TradingToolCall
{
    std::string name;
    std::string targetToolName;
    std::string targetCommandId;
    std::string instrument;
    long orderId = -1;
    InstrumentRef ibContract;
    OrderIntent ibOrder;
    // The current supported venue profiles fix TIF to DAY. Keep the Agent-visible
    // value explicit so a model cannot request one policy while another is
    // silently sent to the venue.
    std::string timeInForce;
    double referencePrice = 0.0;
    std::int64_t expiresAtMs = 0;
    std::string previewPermit;
    int waitTimeoutMs = 0;
    std::uint64_t afterEventSequence = 0;
};

struct TradingToolResult
{
    TradingToolCallStatus status = TradingToolCallStatus::Error;
    std::string toolName;
    std::string reasonCode;
    std::string detail;
    std::string payloadJson;
    long orderId = -1;
};

struct TradingToolReadCallbacks
{
    // The callback must return a bounded JSON object generated from live C++ state.
    typedef std::function<bool(const TradingToolSession&, const TradingToolCall&, std::string&, std::string&)> Handler;
    Handler marketGetQuote;
    Handler accountGetSummary;
    Handler portfolioListPositions;
    Handler ordersList;
    Handler executionGetCommandStatus;
    Handler riskGetLimits;
    Handler riskPreviewOrder;
    Handler riskPreviewFlatten;
    Handler eventsWait;
    Handler systemGetHealth;
};

struct TradingToolTradeCallbacks
{
    std::function<ExecutionCommandResult(
        const TradingToolSession&, const TradingToolCall&)>
        flattenPosition;
};

class TradingToolRegistry
{
public:
    TradingToolRegistry(ExecutionAuthority& execution,
                        const TradingToolReadCallbacks& readCallbacks = TradingToolReadCallbacks(),
                        const TradingToolTradeCallbacks& tradeCallbacks = TradingToolTradeCallbacks());

    std::vector<TradingToolDescriptor> ListTools(const TradingToolSession& session) const;
    bool GetDescriptor(const std::string& name, TradingToolDescriptor& out) const;
    static std::string DescriptorSchemaHash(const TradingToolDescriptor& descriptor);
    static unsigned int DiscoverySchemaVersion();
    std::string CatalogSchemaHash(const TradingToolSession& session) const;
    TradingToolResult Invoke(const TradingToolSession& session, const TradingToolCall& call);

    // Invalidate all Agent-facing target permits and their local replay
    // witnesses for an owner whose host session has been fenced or rotated.
    // Target permits are intentionally kept outside the host's bearer-token
    // replay map so the registry can enforce the one-time transition itself;
    // the host must therefore explicitly revoke this state when the session
    // lifecycle closes or replaces an owner.  The exact-owner overload is
    // used while the full binding is still available.  The identity overload
    // is for recovery/purge paths where only the durable agent/session
    // identity remains and must invalidate every bound strategy/account.
    void RevokeTargetPermitsForOwner(const TradingToolSession& owner) const;
    void RevokeTargetPermitsForIdentity(const std::string& agentId,
                                        const std::string& sessionId) const;

    static const char* StatusName(TradingToolCallStatus status);
    static bool ValidateCallSemantics(const TradingToolCall& call,
                                      std::string& reasonCode,
                                      std::string& detail);

private:
    struct TargetPreviewRecord
    {
        std::string ownerKey;
        // Retain the primary owner identity separately from the composite
        // key so recovery can revoke permits even when strategy/venue fields
        // are no longer present in the restored host binding.
        std::string ownerAgentId;
        std::string ownerSessionId;
        std::string mutationCommandId;
        std::string rawExecutionPermit;
        std::int64_t expiresAtMs = 0;
        std::chrono::steady_clock::time_point steadyExpiresAt;
        TargetPositionDecisionSnapshot snapshot;
        TargetPositionIntentRequest request;
        TargetPositionIntentPolicy policy;
        TargetPositionExecutionPlan plan;
        // Set while the exact bound mutation is outside the registry lock and
        // inside the Execution authority.  Keeping the record until the
        // authority reports an accepted/uncertain outcome prevents a
        // concurrent retry from racing a second dispatch, while a clear
        // pre-dispatch rejection can reset this flag and remain retryable.
        bool applyInFlight = false;
    };

    // A target-position apply has two credentials: the Agent-facing target
    // permit and the raw Execution preview permit.  Once the exact command
    // has crossed the authority boundary, retain its durable result so a
    // caller retrying after an IPC timeout receives the same outcome instead
    // of an opaque "permit unknown" error.  The replay key is server-scoped
    // owner + mutation command id; payload comparison below rejects reuse of
    // one id for a changed target request.
    struct TargetApplyReplayRecord
    {
        std::string ownerKey;
        std::string ownerAgentId;
        std::string ownerSessionId;
        TradingToolCall call;
        TradingToolResult result;
        std::chrono::steady_clock::time_point steadyExpiresAt;
    };

    struct SnapshotGenerationRecord
    {
        std::string fingerprint;
        std::uint64_t generation = 0;
        // The quote timestamp is part of the attested snapshot identity.  A
        // later permit re-read may legitimately reuse this stable cached
        // quote even though the wall-clock collection start has advanced.
        std::int64_t quoteObservedAtMs = 0;
        // Keep generated collection windows monotonic when a cached quote is
        // reused by a later preview/apply re-read.
        std::int64_t collectionStartedAtMs = 0;
    };

    typedef TradingToolReadCallbacks::Handler ReadHandler;

    void RegisterDefaults();
    void RegisterReadTool(const std::string& name,
                          const std::string& description,
                          const std::string& capability,
                          int timeoutMs,
                          const std::string& inputSchema,
                          const ReadHandler& handler);
    bool HasCapability(const TradingToolSession& session, const std::string& capability) const;
    static bool EnvironmentAllows(const TradingToolSession& session,
                                  const TradingToolDescriptor& descriptor,
                                  std::string& reasonCode);
    bool HasRequiredCapabilities(const TradingToolSession& session,
                                 const TradingToolDescriptor& descriptor,
                                 std::string& missingCapability) const;
    TradingToolResult InvokeRead(const TradingToolSession& session,
                                 const TradingToolDescriptor& descriptor,
                                 const TradingToolCall& call) const;
    TradingToolResult InvokeWatchSnapshot(const TradingToolSession& session,
                                          const TradingToolCall& call) const;
    TradingToolResult InvokeDiscovery(const TradingToolSession& session,
                                      const TradingToolCall& call) const;
    TradingToolResult InvokeDecisionSnapshot(
        const TradingToolSession& session,
        const TradingToolCall& call) const;
    TradingToolResult InvokeTargetPreview(
        const TradingToolSession& session,
        const TradingToolCall& call) const;
    TradingToolResult InvokeTargetApply(
        const TradingToolSession& session,
        const TradingToolCall& call) const;
    bool BuildDecisionSnapshot(
        const TradingToolSession& session,
        const TradingToolCall& call,
        TargetPositionDecisionSnapshot& snapshot,
        std::string& outputJson,
        std::string& reasonCode,
        std::string& detail,
        // Optional previously attested collection-start floor used by target
        // apply revalidation when an unchanged cached quote is reread.
        std::int64_t collectionStartedAtMsFloor = 0) const;
    static TradingToolResult FromExecution(const std::string& toolName, const ExecutionCommandResult& execution);

private:
    ExecutionAuthority& m_execution;
    TradingToolReadCallbacks m_readCallbacks;
    TradingToolTradeCallbacks m_tradeCallbacks;
    std::unordered_map<std::string, TradingToolDescriptor> m_descriptors;
    std::unordered_map<std::string, ReadHandler> m_readHandlers;
    mutable std::atomic<std::uint64_t> m_snapshotWatermark{0};
    // A collection watermark is a stable identity for the authoritative
    // component set, not a request counter.  Keep the last bounded
    // fingerprint per owner/instrument so a second read of unchanged state
    // can revalidate a permit while any component change advances the
    // generation and invalidates it.
    mutable std::mutex m_snapshotGenerationMutex;
    mutable std::unordered_map<std::string, SnapshotGenerationRecord>
        m_snapshotGenerations;
    mutable std::mutex m_targetPreviewMutex;
    mutable std::unordered_map<std::string, TargetPreviewRecord> m_targetPreviews;
    mutable std::unordered_map<std::string, TargetApplyReplayRecord>
        m_targetApplyReplays;
};
