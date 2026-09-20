#include "hepta/research/native_strategy_client.h"
#include "test_support.h"
#include <tools/trading_tool_wire_contract.h>
#include <tool_host/typed_tool_protocol.h>
#include <cmath>
#include <limits>
#include <type_traits>

using namespace hepta::research;

namespace {

// Enforce the borrowed-client contract without executing undefined behaviour.
static_assert(std::is_constructible<NativeStrategyClient, NativeToolClient&>::value,
              "a borrowed mutable lvalue must remain supported");
static_assert(std::is_constructible<NativeStrategyClient, const NativeToolClient&>::value,
              "a borrowed const lvalue must remain supported");
static_assert(!std::is_constructible<NativeStrategyClient, NativeToolClient&&>::value,
              "must reject a temporary client that expires after construction");
static_assert(!std::is_constructible<NativeStrategyClient, const NativeToolClient&&>::value,
              "must also reject a const temporary client");

std::string Wire(TradingToolHostRequest request) {
    Check(request.sessionToken.empty(), "proposal must not assign the session");
    request.sessionToken = "research-exit-codec-token";
    std::string body, reason;
    Check(TypedToolProtocol::EncodeRequest(request, body, reason), "exit encoding failed");
    TradingToolHostRequest decoded;
    Check(TypedToolProtocol::DecodeRequest(body, decoded, reason), "exit decoding failed");
    std::string roundTrip;
    Check(TypedToolProtocol::EncodeRequest(decoded, roundTrip, reason), "exit re-encoding failed");
    Check(body == roundTrip, "exit wire round trip changed fields");
    return body;
}
void ExitRequests() {
    const std::string cancelId = "research-cancel-command-001";
    // Exercise the actual wire's full nonnegative long domain, including zero.
    for (long orderId : {0L, 42L, std::numeric_limits<long>::max()}) {
        PreparedCancellation cancellation(orderId);
        auto request = cancellation.SubmissionRequest(cancelId);
        TradingToolHostRequest expected;
        expected.toolCallId = cancelId; expected.call.name = "trade.cancel_order";
        expected.call.orderId = orderId;
        Check(Wire(request) == Wire(expected), "cancel added authority or changed order identity");
        Check(Wire(request) == Wire(cancellation.SubmissionRequest(cancelId)), "cancel retry drifted");
        request.call.orderId = 17; // Modifying a returned copy cannot change the proposal.
        Check(cancellation.SubmissionRequest(cancelId).call.orderId == orderId, "cancel proposal mutated");
        Throws([&] { cancellation.SubmissionRequest("short"); });
    }
    Throws([] { PreparedCancellation(-1); });
    Throws([] { (void)PreparedCancellation(std::numeric_limits<long>::min()); });
    Throws([] { PreparedFlatten(""); });
    Throws([] { PreparedFlatten("EUR USD"); });
    PreparedFlatten flatten("EUR.USD");
    const std::string previewId = "research-flatten-preview-001";
    const std::string flattenId = "execution-flatten-command-001";
    const std::string permit = "sha256:" + std::string(64, 'b');
    const auto preview = flatten.PreviewRequest(previewId);
    const auto submit = flatten.SubmissionRequest(flattenId, permit);
    TradingToolHostRequest expected;
    expected.toolCallId = previewId; expected.call.name = "risk.preview_flatten";
    expected.call.instrument = "EUR.USD";
    Check(Wire(preview) == Wire(expected), "flatten preview carried client position or price");
    expected.toolCallId = flattenId; expected.call.name = "trade.flatten_position";
    expected.call.previewPermit = permit;
    Check(Wire(submit) == Wire(expected), "flatten submission widened the proposal");
    Check(Wire(submit) == Wire(flatten.SubmissionRequest(flattenId, permit)), "flatten retry drifted");
    auto normalized = submit;
    normalized.toolCallId = previewId; normalized.call.name = preview.call.name;
    normalized.call.previewPermit.clear();
    Check(Wire(normalized) == Wire(preview), "flatten preview/submission binding drift");
    normalized.call.instrument = "OTHER.FUT";
    Check(flatten.PreviewRequest(previewId).call.instrument == "EUR.USD", "flatten proposal mutated");
    Throws([&] { flatten.PreviewRequest("short"); });
    Throws([&] { flatten.SubmissionRequest("short", permit); });
    Throws([&] { flatten.SubmissionRequest(flattenId, ""); });
    Throws([&] { flatten.SubmissionRequest(flattenId, "not-an-execution-permit"); });
    Throws([&] { flatten.SubmissionRequest(flattenId, std::string(4097, 'x')); });

    NativeToolClientConfig config;
    config.socketPath = "/dev/null/hepta-research-no-socket";
    config.sessionToken = "research-test-session-token"; config.timeoutMs = 100;
    NativeToolClient native(config); NativeStrategyClient client(native);
    PreparedCancellation cancellation(42);
    // Every exit wrapper must clear prior success both on local validation
    // failure and on real missing-socket transport failure. No test transport.
    for (int which = 0; which != 6; ++which) {
        NativeToolClientResult result;
        result.envelope.status = "ok"; result.envelope.orderId = 42;
        result.envelope.payloadJson = "old-payload"; result.responseJson = "old-success";
        std::string reason = "old-reason";
        bool transported = false;
        switch (which) {
        case 0: transported = client.Cancel(cancellation, cancelId, result, reason); break;
        case 1: transported = client.Cancel(cancellation, "short", result, reason); break;
        case 2: transported = client.PreviewFlatten(flatten, previewId, result, reason); break;
        case 3: transported = client.PreviewFlatten(flatten, "short", result, reason); break;
        case 4: transported = client.Flatten(flatten, flattenId, permit, result, reason); break;
        default: transported = client.Flatten(flatten, flattenId, "", result, reason); break;
        }
        Check(!transported && !reason.empty() && reason != "old-reason", "exit failure lost its diagnostic");
        Check(result.envelope.status.empty() && result.envelope.orderId == -1 &&
              result.envelope.payloadJson.empty() && result.responseJson.empty(), "exit failure leaked old success");
    }
}

void Tests() {
    ExitRequests();
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
