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

// Opaque request lifecycle for explicitly migrated clients. Ready means an
// immutable request exists, not service acceptance; Durable means Persist or
// Restore succeeded, not that a send/fill occurred. Session-token bytes are
// never retained here. Existing HRO1/JSON outboxes are NOT accepted or converted.
class PreparedStrategyCommand {
public:
    PreparedStrategyCommand() : durable_(false) {}
    bool Ready() const { return !request_.toolCallId.empty(); }
    bool Durable() const { return durable_; }
    const std::string& CommandId() const { return request_.toolCallId; }
    const std::string& ToolName() const { return request_.call.name; }
private:
    friend class NativeStrategyClient;
    TradingToolHostRequest request_;
    std::string binding_;
    std::string directory_;
    bool durable_;
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
    // One ordinary preview call followed by strict typed response decoding.
    // Unlike raw Preview, true means a structurally approved matching preview
    // was received, NOT that a mutation ran or the permit remains usable. False
    // clears authorization, preserves a transported rejection/uncertainty in
    // result, and returns a diagnostic. No mutation, persistence or retry here.
    bool PreviewAuthorized(const PreparedOrder& order, const std::string& previewCallId,
                           TypedPreviewAuthorization& authorization,
                           NativeToolClientResult& result, std::string& reason) const;
    bool PreviewAuthorized(const PreparedFlatten& flatten, const std::string& previewCallId,
                           TypedPreviewAuthorization& authorization,
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
    // Explicit Prepare -> Persist -> Submit workflow for new/migrated callers.
    // Prepare makes exactly one bound preview for order/flatten; cancellation
    // uses a caller-selected canonical ID without a preview or network call.
    // No generated ID, retry, expiry refresh, historical-record conversion, or
    // send occurs here. A false preview retains its transported envelope.
    bool Prepare(const PreparedOrder& order, const std::string& previewCallId,
                 PreparedStrategyCommand& prepared, NativeToolClientResult& result,
                 std::string& reason) const;
    bool Prepare(const PreparedFlatten& flatten, const std::string& previewCallId,
                 PreparedStrategyCommand& prepared, NativeToolClientResult& result,
                 std::string& reason) const;
    bool Prepare(const PreparedCancellation& cancellation, const std::string& executionCommandId,
                 PreparedStrategyCommand& prepared, std::string& reason) const;
    // Failure keeps the original prepared request/ID for explicit recovery,
    // but clears Durable. Do not obtain a replacement preview to retry a write.
    // Even on sync failure an immutable disk record may already exist.
    bool Persist(const std::string& directory, PreparedStrategyCommand& prepared,
                 std::string& reason) const;
    // Restore accepts only canonical HSR1 records, checks the original binding,
    // and clears prepared on failure. All requests are reread at submission;
    // neither a mutable diagnostic copy nor a cached durable flag is authority.
    bool Restore(const std::string& directory, const std::string& executionCommandId,
                 PreparedStrategyCommand& prepared, std::string& reason) const;
    // Validate a durable opaque LIMIT/DAY request against an application intent
    // and the original NativeToolClient recovery binding. No socket call or
    // record write; Submit/Inspect still reread the same opaque snapshot.
    bool MatchesOrder(const PreparedStrategyCommand& prepared, const PreparedOrder& order,
                      const std::string& expectedBinding, std::string& reason) const;
    bool Submit(const PreparedStrategyCommand& prepared, NativeToolClientResult& result,
                std::string& reason) const;
    // Read-only recovery for callers whose policy forbids another mutation
    // after any possible send. Verify the ORIGINAL HSR1 record/binding, then
    // send only execution.get_command_status. No preview, submit, new mutation
    // ID, acknowledgement, outbox rewrite or automatic retry occurs. Unknown,
    // rejected and uncertain responses are preserved, never permission to send.
    // The query ID is caller-owned and must differ from the original command.
    // Inspect additionally checks the prepared snapshot against current bytes.
    bool InspectStored(const std::string& directory, const std::string& executionCommandId,
                       const std::string& queryCallId, NativeToolClientResult& result,
                       std::string& reason) const;
    bool Inspect(const PreparedStrategyCommand& prepared, const std::string& queryCallId,
                 NativeToolClientResult& result, std::string& reason) const;
    bool Status(const std::string& executionCommandId, const std::string& queryCallId,
                NativeToolClientResult& result, std::string& reason) const;
private:
    bool PersistRequest(const std::string& directory, TradingToolHostRequest request,
                        std::string& reason, const std::string& expectedBinding = "") const;
    bool PrepareRequest(TradingToolHostRequest request, const std::string& mutationTool,
                        PreparedStrategyCommand& prepared, NativeToolClientResult& result,
                        std::string& reason) const;
    bool LoadPrepared(const PreparedStrategyCommand& prepared, TradingToolHostRequest& request,
                      std::string& binding, std::string& reason) const;
    bool InspectBound(const std::string& executionCommandId, const std::string& queryCallId,
                      const std::string& binding, NativeToolClientResult& result,
                      std::string& reason) const;
    bool Forward(const TradingToolHostRequest& request, NativeToolClientResult& result,
                 std::string& reason) const;
    const NativeToolClient& client_;
};
}} // namespace hepta::research
