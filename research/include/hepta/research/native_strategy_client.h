#pragma once
#include <client/native_tool_client.h>
#include <cstdint>
#include <string>

namespace hepta { namespace research {
// Immutable proposal. Contract identity comes from the supported tool contract,
// never from a CTP/IB SDK type. The service still authenticates it against the
// server-side session binding. No account, owner, risk approval or fill fields.
// Extra InstrumentRef fields not represented by the current HTT1 wire fail closed.
class PreparedOrder {
public:
    PreparedOrder(const std::string& instrument, const InstrumentRef& contract,
                  const std::string& side, double quantity, double limitPrice,
                  double referencePrice, std::int64_t expiresAtMs);
    TradingToolHostRequest PreviewRequest(const std::string& previewCallId) const;
    TradingToolHostRequest SubmissionRequest(const std::string& executionCommandId,
                                             const std::string& previewPermit) const;
private:
    TradingToolCall call_;
};

// Select an order ID obtained from the canonical service, never a replay order
// or locally invented broker ID. The service alone checks ownership and cancel
// eligibility. Construction does not establish either. There is no cancel-any
// flag, owner override, local order map or fallback to a broker transport.
class PreparedCancellation {
public:
    explicit PreparedCancellation(long serverOrderId);
    TradingToolHostRequest SubmissionRequest(const std::string& executionCommandId) const;
private:
    TradingToolCall call_;
};

// Select only a server-bound instrument. The Execution Service derives side,
// quantity, price and snapshot binding from its authoritative state. Research
// positions, marks and ledger balances cannot be attached to this proposal.
class PreparedFlatten {
public:
    explicit PreparedFlatten(const std::string& instrument);
    TradingToolHostRequest PreviewRequest(const std::string& previewCallId) const;
    TradingToolHostRequest SubmissionRequest(const std::string& executionCommandId,
                                             const std::string& previewPermit) const;
private:
    TradingToolCall call_;
};

// This forward-only library links only NativeToolClient and its wire closure.
// The referenced NativeToolClient must outlive this object. No automatic retry,
// no locally invented execution command IDs, no execution journal or broker networking.
// A true return means a response was transported, NOT that an order succeeded.
// Always inspect the envelope status; rejected/uncertain are preserved unchanged.
class NativeStrategyClient {
public:
    explicit NativeStrategyClient(const NativeToolClient& client) : client_(client) {}
    // Borrow only a named, longer-lived client. Binding a temporary would leave
    // client_ dangling at the end of the constructing full expression.
    NativeStrategyClient(NativeToolClient&&) = delete;
    NativeStrategyClient(const NativeToolClient&&) = delete;
    bool Preview(const PreparedOrder& order, const std::string& previewCallId,
                 NativeToolClientResult& result, std::string& reason) const;
    // ID and permit MUST come from the matching successful preview response.
    // Persist that response and proposal before sending. Reuse the exact ID and
    // proposal on retry; an uncertain response is not permission for a new ID.
    bool Submit(const PreparedOrder& order, const std::string& executionCommandId,
                const std::string& previewPermit, NativeToolClientResult& result,
                std::string& reason) const;
    // Cancel has no preview tool. Persist one caller-selected canonical command
    // ID with the service order ID before sending; reuse both after uncertainty.
    bool Cancel(const PreparedCancellation& cancellation, const std::string& executionCommandId,
                NativeToolClientResult& result, std::string& reason) const;
    bool PreviewFlatten(const PreparedFlatten& flatten, const std::string& previewCallId,
                        NativeToolClientResult& result, std::string& reason) const;
    // As for Submit, forward the ID and permit from the matching preview. This
    // does not turn flatten into an unrestricted or automatically retried exit.
    bool Flatten(const PreparedFlatten& flatten, const std::string& executionCommandId,
                 const std::string& previewPermit, NativeToolClientResult& result,
                 std::string& reason) const;
    // Private POSIX outbox, not an OMS: caller creates an owned 0700 absolute
    // directory. Persist fsyncs an immutable 0600 record before returning true.
    // There is no send here, and no automatic retry, ID allocation or expiry
    // refresh. Order/flatten IDs and permits must be from the matching preview.
    bool Persist(const std::string& directory, const PreparedOrder& order,
                 const std::string& executionCommandId, const std::string& previewPermit,
                 std::string& reason) const;
    bool Persist(const std::string& directory, const PreparedCancellation& cancellation,
                 const std::string& executionCommandId, std::string& reason) const;
    bool Persist(const std::string& directory, const PreparedFlatten& flatten,
                 const std::string& executionCommandId, const std::string& previewPermit,
                 std::string& reason) const;
    // Diagnostic copy only, with an empty sessionToken. Failure clears output.
    // SubmitStored always rereads the disk record; it never trusts this copy.
    bool LoadStored(const std::string& directory, const std::string& executionCommandId,
                    TradingToolHostRequest& request, std::string& reason) const;
    // One bound call through NativeToolClient after verified durable load.
    // Same UID/socket/credential, exact original ID/expiry/payload/permit.
    bool SubmitStored(const std::string& directory, const std::string& executionCommandId,
                      NativeToolClientResult& result, std::string& reason) const;
    bool Status(const std::string& executionCommandId, const std::string& queryCallId,
                NativeToolClientResult& result, std::string& reason) const;
private:
    bool PersistRequest(const std::string& directory, TradingToolHostRequest request,
                        std::string& reason) const;
    bool Forward(const TradingToolHostRequest& request, NativeToolClientResult& result,
                 std::string& reason) const;
    const NativeToolClient& client_;
};
}} // namespace hepta::research
