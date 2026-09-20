#include "hepta/research/native_strategy_client.h"
#include "test_support.h"
#include <tools/trading_tool_wire_contract.h>
#include <tool_host/typed_tool_protocol.h>
#include <cmath>
#include <limits>

using namespace hepta::research;

namespace {
void Tests() {
    InstrumentRef contract;
    contract.symbol = "EUR"; contract.secType = "CASH";
    contract.exchange = "SIM"; contract.currency = "USD";
    const std::int64_t expiry = 1900000000000LL;
    PreparedOrder proposal("EUR.USD", contract, "BUY", 100, 1.10, 1.09, expiry);
    const std::string commandId = "execution-issued-command-001";
    const std::string permit = "sha256:" + std::string(64, 'a');
    auto preview = proposal.PreviewRequest("research-preview-001");
    auto submit = proposal.SubmissionRequest(commandId, permit);
    Check(preview.call.name == "risk.preview_order", "preview must use canonical read path");
    Check(preview.call.previewPermit.empty(), "preview cannot claim its own permit");
    Check(submit.call.name == "trade.place_order", "submit must use canonical mutation tool");
    Check(submit.toolCallId == commandId && submit.call.previewPermit == permit,
          "Execution-issued identity and permit must be forwarded unchanged");
    Check(preview.sessionToken.empty() && submit.sessionToken.empty(), "NativeToolClient owns session binding");
    Check(submit.call.ibOrder.orderRef.empty(), "caller must not assign broker correlation");
    Check(submit.call.ibContract.exchange == "SIM", "contract identity was changed");
    Check(submit.call.timeInForce == "DAY" && submit.call.expiresAtMs == expiry, "expiry or TIF drift");
    Near(submit.call.ibOrder.totalQuantity, 100); Near(submit.call.ibOrder.lmtPrice, 1.10);
    // Session insertion is done by NativeToolClient in production. Supply a
    // synthetic token only for these pure wire-codec round-trip tests.
    preview.sessionToken = submit.sessionToken = "research-codec-test-token";
    // Compare the exact serialized proposals, after removing only the three
    // intentional differences. No lossy field-by-field reconstruction.
    std::string reason, first, second, retry;
    auto normalized = submit;
    normalized.call.name = preview.call.name;
    normalized.call.previewPermit.clear(); normalized.toolCallId = preview.toolCallId;
    Check(TypedToolProtocol::EncodeRequest(preview, first, reason), "preview encoding failed");
    Check(TypedToolProtocol::EncodeRequest(normalized, second, reason), "submission encoding failed");
    Check(first == second, "preview and submission proposal mismatch");
    Check(TypedToolProtocol::EncodeRequest(submit, first, reason), "submit encoding failed");
    auto retried = proposal.SubmissionRequest(commandId, permit);
    retried.sessionToken = submit.sessionToken;
    Check(TypedToolProtocol::EncodeRequest(retried, retry, reason), "retry encoding failed");
    Check(first == retry, "retry changed request identity or order");
    TradingToolHostRequest decoded;
    Check(TypedToolProtocol::DecodeRequest(first, decoded, reason), "request round-trip failed");
    Check(decoded.toolCallId == commandId && decoded.call.previewPermit == permit, "wire identity lost");
    Throws([&] { proposal.PreviewRequest("short"); });
    Throws([&] { proposal.SubmissionRequest(commandId, ""); });
    Throws([&] { proposal.SubmissionRequest(commandId, "not-an-execution-permit"); });
    Throws([&] { PreparedOrder("EUR.USD", contract, "BUY", 0, 1, 1, expiry); });
    Throws([&] { PreparedOrder("EUR.USD", contract, "SELL", 1, std::numeric_limits<double>::quiet_NaN(), 1, expiry); });
    Throws([&] { PreparedOrder("EUR.USD", contract, "CLOSE_TODAY", 1, 1, 1, expiry); });
    Throws([&] { PreparedOrder("EUR.USD", contract, "BUY", 1, 1, 1, 0); });

    InstrumentRef unsupported = contract; unsupported.strike = 100;
    Throws([&] { PreparedOrder("EUR.USD", unsupported, "BUY", 1, 1, 1, expiry); });
    unsupported = contract; unsupported.lastTradeDateOrContractMonth = "202612";
    Throws([&] { PreparedOrder("EUR.USD", unsupported, "BUY", 1, 1, 1, expiry); });

    NativeToolClientConfig config;
    // A nonexistent path below /dev/null cannot accidentally resolve to a
    // real local Gateway. This is a negative transport test, not broker proof.
    config.socketPath = "/dev/null/hepta-research-no-socket";
    config.sessionToken = "research-test-session-token"; config.timeoutMs = 100;
    NativeToolClient native(config); NativeStrategyClient client(native);
    NativeToolClientResult result;
    result.envelope.status = "ok"; result.envelope.orderId = 42; result.responseJson = "old-success";
    Check(!client.Preview(proposal, "research-preview-002", result, reason), "missing Gateway must fail");
    Check(result.envelope.status.empty() && result.envelope.orderId == -1 && result.responseJson.empty(),
          "failed transport leaked a prior success");
    Check(!reason.empty(), "failed transport requires a diagnostic");
    Check(!client.Submit(proposal, commandId, permit, result, reason), "submit must not fake success");
    Check(!client.Status(commandId, "research-query-001", result, reason), "query must not fake success");
    result.envelope.status = "ok";
    Check(!client.Status("short", "research-query-002", result, reason), "invalid identity must fail");
    Check(result.envelope.status.empty(), "local validation failure retained old success");
}
}
int main() { return Run(Tests); }
