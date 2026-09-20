#pragma once
#include "native_tool_client.h"
#include "../../strategies/native/strategy.h"

// This adapter has only the same unprivileged local socket as NativeToolClient.
// No legacy SPI, broker SDK, authoritative-state writer or Execution implementation.
class ResearchPreparedOrder {
public:
    bool Ready() const { return !bytes_.empty(); }
    bool Persisted() const { return persisted_; }
    const std::string& CommandId() const { return request_.toolCallId; }
private:
    friend class ResearchIntentClient;
    TradingToolHostRequest request_;
    std::string bytes_;
    bool persisted_ = false;
};
class ResearchIntentClient {
public:
    ResearchIntentClient(const NativeToolClientConfig& client,
                         const hepta::research::ProposalLimits& limits);
    // Calls risk.preview_order; parses the actual service-issued ID and permit.
    // A successful preview is not a fill, a send, or a local risk approval.
    bool Prepare(const hepta::research::BoundedOrderProposal& proposal,
                 const InstrumentRef& contract, std::int64_t nowMs,
                 ResearchPreparedOrder& prepared, NativeToolClientResult& result,
                 std::string& reason) const;
    // Caller creates a private owned 0700 directory. Immutable 0600 records are
    // fsynced before Submit is permitted; the session credential is never stored.
    static bool Persist(const std::string& directory, ResearchPreparedOrder& prepared,
                        std::string& reason);
    static bool Load(const std::string& directory, const std::string& commandId,
                     ResearchPreparedOrder& prepared, std::string& reason);
    // Exactly one call, including on retries; never creates another ID/expiry.
    bool Submit(const ResearchPreparedOrder& prepared, NativeToolClientResult& result,
                std::string& reason) const;
    bool Status(const std::string& commandId, const std::string& requestId,
                NativeToolClientResult& result, std::string& reason) const;
private:
    NativeToolClient client_;
    hepta::research::ProposalLimits limits_;
};
