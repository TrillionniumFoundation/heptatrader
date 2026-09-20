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

// This forward-only library links only NativeToolClient and its wire closure.
// The referenced NativeToolClient must outlive this object. No automatic retry,
// no locally invented execution command IDs, no journal or broker networking.
// A true return means a response was transported, NOT that an order succeeded.
// Always inspect the envelope status; rejected/uncertain are preserved unchanged.
class NativeStrategyClient {
public:
    explicit NativeStrategyClient(const NativeToolClient& client) : client_(client) {}
    bool Preview(const PreparedOrder& order, const std::string& previewCallId,
                 NativeToolClientResult& result, std::string& reason) const;
    // ID and permit MUST come from the matching successful preview response.
    // Persist that response and proposal before sending. Reuse the exact ID and
    // proposal on retry; an uncertain response is not permission for a new ID.
    bool Submit(const PreparedOrder& order, const std::string& executionCommandId,
                const std::string& previewPermit, NativeToolClientResult& result,
                std::string& reason) const;
    bool Status(const std::string& executionCommandId, const std::string& queryCallId,
                NativeToolClientResult& result, std::string& reason) const;
private:
    bool Forward(const TradingToolHostRequest& request, NativeToolClientResult& result,
                 std::string& reason) const;
    const NativeToolClient& client_;
};
}} // namespace hepta::research
