#include "hepta/research/native_strategy_client.h"
#include "test_support.h"
#include <tools/trading_tool_wire_contract.h>
#include <tool_host/typed_tool_protocol.h>
#include <cmath>
#include <algorithm>
#include <utility>
#include <limits>
#include <type_traits>
#include <cerrno>
#include <dirent.h>
#include <fstream>
#include <iterator>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

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


class OutboxTestRoot {
public:
    OutboxTestRoot() {
        char name[] = "/tmp/hepta-outbox-XXXXXX";
        const char* made = ::mkdtemp(name);
        Check(made != nullptr, "outbox fixture directory"); path = made;
    }
    ~OutboxTestRoot() {
        DIR* directory = ::opendir(path.c_str());
        if (directory) {
            while (dirent* entry = ::readdir(directory)) {
                const std::string name(entry->d_name);
                if (name != "." && name != "..") {
                    ::unlink((path + "/" + name).c_str());
                    ::rmdir((path + "/" + name).c_str());
                }
            }
            ::closedir(directory);
        }
        ::rmdir(path.c_str());
    }
    std::string path;
};
std::string ReadBytes(const std::string& path) {
    std::ifstream input(path, std::ios::binary);
    Check(input.good(), "fixture read open");
    return std::string(std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>());
}
void WriteBytes(const std::string& path, const std::string& bytes) {
    std::ofstream output(path, std::ios::binary | std::ios::trunc);
    output.write(bytes.data(), static_cast<std::streamsize>(bytes.size())); output.close();
    Check(output.good() && ::chmod(path.c_str(), 0600) == 0, "fixture write");
}
NativeToolClientConfig OutboxConfig() {
    NativeToolClientConfig config;
    config.socketPath = "/tmp/nonexistent-hepta-outbox-socket";
    config.sessionToken = "outbox-synthetic-session-token-never-persist";
    config.timeoutMs = 100;
    return config;
}
void OutboxTests() {
    Check(NativeToolDiscoveryContract::ContentDigest("") ==
        "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "SHA empty oracle");
    Check(NativeToolDiscoveryContract::ContentDigest("abc") ==
        "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", "SHA abc oracle");
    Check(NativeToolDiscoveryContract::ContentDigest(std::string(1000000, 'a')) ==
        "sha256:cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0", "SHA million-a oracle");
    OutboxTestRoot root;
    auto config = OutboxConfig();
    NativeToolClient native(config); NativeStrategyClient client(native);
    InstrumentRef contract; contract.symbol = "EUR"; contract.secType = "CASH";
    contract.exchange = "SIM"; contract.currency = "USD";
    PreparedOrder order("EUR.USD", contract, "BUY", 10, 1.1, 1.09, 1900000000000LL);
    const std::string id = "outbox-order-0001", permit = "sha256:" + std::string(64, 'a');
    std::string reason;
    Check(client.Persist(root.path, order, id, permit, reason) && reason.empty(), "durable order store");
    const auto path = root.path + "/" + id + ".hsr", original = ReadBytes(path);
    Check(original.find(config.sessionToken) == std::string::npos, "outbox leaked session credential");
    Check(original.find(permit) != std::string::npos, "outbox lost preview credential");
    Check(original.size() < 65536 + 149 && original.compare(0, 5, "HSR1\n") == 0, "outbox framing");
    struct stat info;
    Check(::stat(path.c_str(), &info) == 0 && (info.st_mode & 07777) == 0600 && info.st_nlink == 1,
          "outbox publication mode/link count");
    TradingToolHostRequest loaded;
    loaded.sessionToken = "stale"; loaded.call.name = "stale";
    Check(client.LoadStored(root.path, id, loaded, reason), "load original store");
    Check(Wire(loaded) == Wire(order.SubmissionRequest(id, permit)), "durable request byte identity");
    loaded.call.ibOrder.totalQuantity = 500;
    Check(client.LoadStored(root.path, id, loaded, reason) && loaded.call.ibOrder.totalQuantity == 10,
          "diagnostic request copy changed disk identity");
    Check(client.Persist(root.path, order, id, permit, reason) && ReadBytes(path) == original,
          "idempotent persistence changed bytes");
    PreparedOrder changed("EUR.USD", contract, "BUY", 11, 1.1, 1.09, 1900000000000LL);
    Check(!client.Persist(root.path, changed, id, permit, reason) && ReadBytes(path) == original,
          "conflicting order overwrote identity");
    Check(!client.Persist(root.path, order, id, "sha256:" + std::string(64, 'b'), reason) &&
          ReadBytes(path) == original, "permit overwrite");
    PreparedCancellation cancellation(42);
    Check(client.Persist(root.path, cancellation, "outbox-cancel-001", reason), "cancel store");
    Check(client.LoadStored(root.path, "outbox-cancel-001", loaded, reason) &&
          Wire(loaded) == Wire(cancellation.SubmissionRequest("outbox-cancel-001")), "cancel load identity");
    PreparedFlatten flatten("EUR.USD");
    Check(client.Persist(root.path, flatten, "outbox-flatten-01", permit, reason), "flatten store");
    Check(client.LoadStored(root.path, "outbox-flatten-01", loaded, reason) &&
          Wire(loaded) == Wire(flatten.SubmissionRequest("outbox-flatten-01", permit)), "flatten load identity");
    Check(!client.Persist(root.path, cancellation, id, reason) && ReadBytes(path) == original, "cross-operation overwrite");
    // A missing socket must remain a transport failure; the record is retained
    // for explicit status/retry handling. No sent/success bit is invented.
    NativeToolClientResult result;
    result.envelope.status = "ok"; result.envelope.orderId = 42; result.responseJson = "stale";
    Check(!client.SubmitStored(root.path, id, result, reason) && !reason.empty() &&
          result.envelope.status.empty() && result.envelope.orderId == -1 && result.responseJson.empty() &&
          ReadBytes(path) == original, "failed stored submission left stale success");
    auto otherConfig = config; otherConfig.sessionToken += "-different";
    NativeToolClient other(otherConfig); NativeStrategyClient otherClient(other);
    Check(!otherClient.LoadStored(root.path, id, loaded, reason) && loaded.call.name.empty() &&
          reason == "NATIVE_RECOVERY_BINDING_MISMATCH", "cross-session load accepted");
    Check(!otherClient.SubmitStored(root.path, id, result, reason) &&
          reason == "NATIVE_RECOVERY_BINDING_MISMATCH", "cross-session send attempted");
    otherConfig = config; otherConfig.socketPath += "-different";
    NativeToolClient moved(otherConfig); NativeStrategyClient movedClient(moved);
    Check(!movedClient.SubmitStored(root.path, id, result, reason) &&
          reason == "NATIVE_RECOVERY_BINDING_MISMATCH", "cross-endpoint send attempted");
    const auto tokenPath = root.path + "/session.token";
    WriteBytes(tokenPath, config.sessionToken + "\n");
    auto fileConfig = config; fileConfig.sessionToken.clear(); fileConfig.tokenFile = tokenPath;
    NativeToolClient fileNative(fileConfig); NativeStrategyClient fileClient(fileNative);
    Check(fileClient.LoadStored(root.path, id, loaded, reason), "same token-file credential changed binding");
    WriteBytes(tokenPath, "rotated-synthetic-token");
    Check(!fileClient.SubmitStored(root.path, id, result, reason) &&
          reason == "NATIVE_RECOVERY_BINDING_MISMATCH", "rotated token replay");
    Check(!client.Persist(root.path, order, "short", permit, reason), "bad ID store");
    Check(!client.LoadStored(root.path, "../../bad-id", loaded, reason), "path traversal ID");
    Check(!client.LoadStored(root.path, "outbox-missing-id", loaded, reason) && loaded.call.name.empty(), "missing record");
    for (const auto& unsafe : {std::string("relative"), root.path + "/", root.path + "/.",
                               root.path + "/../" + root.path.substr(5), root.path + std::string("\0tail", 5)})
        Check(!client.Persist(unsafe, cancellation, "unsafe-path-id", reason), "unsafe directory accepted");
    Check(::chmod(root.path.c_str(), 0750) == 0, "fixture directory chmod");
    Check(!client.LoadStored(root.path, id, loaded, reason) && !client.Persist(root.path, order, id, permit, reason),
          "nonprivate directory accepted");
    Check(::chmod(root.path.c_str(), 0700) == 0, "restore directory");
    for (mode_t mode : {mode_t(0640), mode_t(0400), mode_t(0666), mode_t(04600)}) {
        Check(::chmod(path.c_str(), mode) == 0, "fixture chmod");
        Check(!client.LoadStored(root.path, id, loaded, reason), "unsafe file mode accepted");
        Check(::chmod(path.c_str(), 0600) == 0, "fixture restore mode");
    }
    const auto link = root.path + "/alias.hsr";
    Check(::link(path.c_str(), link.c_str()) == 0, "fixture hardlink");
    Check(!client.LoadStored(root.path, id, loaded, reason), "hardlinked record accepted");
    Check(::unlink(link.c_str()) == 0, "fixture unlink hardlink");
    const auto saved = root.path + "/saved.hsr";
    Check(::rename(path.c_str(), saved.c_str()) == 0 && ::symlink(saved.c_str(), path.c_str()) == 0,
          "fixture symlink");
    Check(!client.LoadStored(root.path, id, loaded, reason) && !client.Persist(root.path, order, id, permit, reason),
          "record symlink followed");
    Check(::unlink(path.c_str()) == 0 && ::mkfifo(path.c_str(), 0600) == 0, "fixture fifo");
    Check(!client.LoadStored(root.path, id, loaded, reason), "FIFO accepted or blocked");
    Check(::unlink(path.c_str()) == 0 && ::rename(saved.c_str(), path.c_str()) == 0, "restore record");
    OutboxTestRoot aliasRoot;
    const auto dirAlias = aliasRoot.path + "/link";
    Check(::symlink(root.path.c_str(), dirAlias.c_str()) == 0, "fixture directory link");
    Check(!client.LoadStored(dirAlias, id, loaded, reason), "directory symlink followed");
    Check(::mkdir((root.path + "/child").c_str(), 0700) == 0, "fixture child directory");
    Check(!client.Persist(dirAlias + "/child", cancellation, "alias-parent-id", reason), "ancestor symlink followed");
    Check(::unlink(dirAlias.c_str()) == 0, "remove directory link");
    for (const auto& corrupt : {original.substr(0, 148), original + "x", std::string(70000, 'x')}) {
        WriteBytes(path, corrupt);
        Check(!client.LoadStored(root.path, id, loaded, reason) && loaded.call.name.empty(), "corrupt record accepted");
    }
    auto corrupt = original; corrupt[corrupt.size() - 1] ^= 1;
    WriteBytes(path, corrupt);
    Check(!client.LoadStored(root.path, id, loaded, reason) && reason == "RESEARCH_OUTBOX_DIGEST_MISMATCH",
          "changed body passed checksum");
    WriteBytes(path, original);
    // A valid checksum is not authorization: still enforce tool allowlist and
    // filename/command correlation when the complete wire itself is malformed.
    auto unauthorized = order.PreviewRequest(id); unauthorized.sessionToken = "hepta-strategy-outbox-v1-not-a-session-token";
    std::string unauthorizedWire;
    Check(TypedToolProtocol::EncodeRequest(unauthorized, unauthorizedWire, reason), "fixture preview wire");
    const auto binding = original.substr(5, 71);
    WriteBytes(path, "HSR1\n" + binding + "\n" + NativeToolDiscoveryContract::ContentDigest(
        "HSR1\n" + binding + "\n" + unauthorizedWire) + "\n" + unauthorizedWire);
    Check(!client.LoadStored(root.path, id, loaded, reason) && reason == "RESEARCH_OUTBOX_REQUEST_BINDING_INVALID",
          "nonmutation replay allowed");
    WriteBytes(path, original);
    // Cooperating processes serialize publication; exactly equal requests all
    // succeed, while conflicting requests cannot replace the winning record.
    for (bool conflict : {false, true}) {
        const std::string concurrentId = conflict ? "concurrent-conflict" : "concurrent-identical";
        std::vector<pid_t> children;
        for (int index = 0; index < 6; ++index) {
            const pid_t child = ::fork(); Check(child >= 0, "outbox publisher fork");
            if (child == 0) {
                NativeToolClient fresh(config); NativeStrategyClient writer(fresh);
                PreparedCancellation candidate(conflict ? 100 + index : 100);
                std::string why;
                ::_exit(writer.Persist(root.path, candidate, concurrentId, why) ? 0 :
                    (why == "RESEARCH_OUTBOX_CONFLICT_OR_UNSAFE" ? 2 : 3));
            }
            children.push_back(child);
        }
        unsigned int successes = 0, conflicts = 0;
        for (const auto child : children) {
            int status;
            pid_t waited;
            do { waited = ::waitpid(child, &status, 0); } while (waited < 0 && errno == EINTR);
            Check(waited == child && WIFEXITED(status), "outbox publisher wait");
            successes += WEXITSTATUS(status) == 0; conflicts += WEXITSTATUS(status) == 2;
            Check(WEXITSTATUS(status) == 0 || WEXITSTATUS(status) == 2, "outbox publisher unexpected error");
        }
        Check(successes == (conflict ? 1U : 6U) && conflicts == (conflict ? 5U : 0U),
              "atomic conflict/idempotence count");
        Check(client.LoadStored(root.path, concurrentId, loaded, reason) &&
              loaded.call.orderId >= 100 && loaded.call.orderId <= (conflict ? 105 : 100), "concurrent publication corrupted");
    }
    // Restrictive umask must not result in a success-shaped unreadable record.
    const mode_t previousMask = ::umask(0777);
    const bool stored = client.Persist(root.path, cancellation, "umask-request-001", reason);
    ::umask(previousMask);
    Check(stored && client.LoadStored(root.path, "umask-request-001", loaded, reason), "restrictive umask persistence");
    std::cout << "outbox: immutable place/cancel/flatten, credential binding, unsafe-file rejection,"
              << " checksum oracles and 12 concurrent publishers passed\n";
}

// Inputs may be borrowed from the previous result/request or error string.
// Output objects themselves remain distinct. Compare missing-socket failures
// with the nonaliased call so validation cannot silently eat the original ID.
void BorrowedInputTests() {
    OutboxTestRoot root;
    const auto config = OutboxConfig();
    NativeToolClient native(config); NativeStrategyClient client(native);
    InstrumentRef contract; contract.symbol = "EUR"; contract.secType = "CASH";
    contract.exchange = "SIM"; contract.currency = "USD";
    PreparedOrder order("EUR.USD", contract, "BUY", 10, 1.1, 1.09, 1900000000000LL);
    PreparedCancellation cancellation(42); PreparedFlatten flatten("EUR.USD");
    const std::string id = "borrowed-command-001", query = "borrowed-query-001";
    const std::string permit = "sha256:" + std::string(64, 'c');
    auto direct = [&](int operation, const std::string& callId, const std::string& credential,
                      const std::string& queryId, NativeToolClientResult& result, std::string& why) {
        switch (operation) {
        case 0: return client.Preview(order, callId, result, why);
        case 1: return client.Submit(order, callId, credential, result, why);
        case 2: return client.Cancel(cancellation, callId, result, why);
        case 3: return client.PreviewFlatten(flatten, callId, result, why);
        case 4: return client.Flatten(flatten, callId, credential, result, why);
        default: return client.Status(callId, queryId, result, why);
        }
    };
    auto cleared = [](const NativeToolClientResult& result) {
        return result.envelope.status.empty() && result.envelope.orderId == -1 &&
            result.envelope.detail.empty() && result.envelope.payloadJson.empty() && result.responseJson.empty();
    };
    for (int operation = 0; operation < 6; ++operation) {
        NativeToolClientResult baseline;
        std::string expected;
        Check(!direct(operation, id, permit, query, baseline, expected) && !expected.empty(),
              "borrowed fixture requires real missing-socket failure");
        for (int mode = 0; mode < 4; ++mode) {
            NativeToolClientResult result;
            result.envelope.status = "ok"; result.envelope.orderId = 42;
            result.envelope.detail = id; result.envelope.payloadJson = permit; result.responseJson = query;
            std::string reason = mode == 1 ? id : (mode == 2 ? permit : query);
            const std::string& borrowedId = mode == 1 ? reason : result.envelope.detail;
            const std::string& borrowedPermit = mode == 2 ? reason : result.envelope.payloadJson;
            const std::string& borrowedQuery = mode == 3 ? reason : result.responseJson;
            Check(!direct(operation, borrowedId, borrowedPermit, borrowedQuery, result, reason) &&
                  reason == expected && cleared(result), "borrowed direct inputs changed failure or leaked success");
        }
        NativeToolClientResult invalid;
        invalid.envelope.status = "ok"; invalid.envelope.detail = "short";
        std::string reason;
        Check(!direct(operation, invalid.envelope.detail, permit, query, invalid, reason) &&
              reason == "RESEARCH_TOOL_CALL_ID_INVALID" && cleared(invalid), "invalid borrowed ID retained success");
    }
    auto persist = [&](int operation, const std::string& directory, const std::string& callId,
                       const std::string& credential, std::string& why) {
        if (operation == 0) return client.Persist(directory, order, callId, credential, why);
        if (operation == 1) return client.Persist(directory, cancellation, callId, why);
        return client.Persist(directory, flatten, callId, credential, why);
    };
    for (int operation = 0; operation < 3; ++operation) {
        const auto command = id + "-" + std::to_string(operation);
        std::string reason;
        Check(persist(operation, root.path, command, permit, reason), "borrowed fixture seed");
        const auto file = root.path + "/" + command + ".hsr", original = ReadBytes(file);
        reason = root.path;
        Check(persist(operation, reason, command, permit, reason), "persist cleared its directory input");
        reason = command;
        Check(persist(operation, root.path, reason, permit, reason), "persist cleared its ID input");
        reason = permit;
        Check(persist(operation, root.path, command, reason, reason), "persist cleared its permit input");
        TradingToolHostRequest loaded;
        Check(client.LoadStored(root.path, command, loaded, reason), "borrowed load baseline");
        const auto expectedWire = Wire(loaded);
        loaded.call.instrument = root.path;
        Check(client.LoadStored(loaded.call.instrument, loaded.toolCallId, loaded, reason) &&
              Wire(loaded) == expectedWire, "load cleared borrowed request inputs");
        reason = command;
        Check(client.LoadStored(root.path, reason, loaded, reason) && Wire(loaded) == expectedWire,
              "load cleared borrowed error/ID input");
        reason = root.path;
        Check(client.LoadStored(reason, command, loaded, reason) && Wire(loaded) == expectedWire,
              "load cleared borrowed error/directory input");
        NativeToolClientResult baseline;
        std::string expected;
        Check(!client.SubmitStored(root.path, command, baseline, expected), "missing socket submitted stored request");
        for (int mode = 0; mode < 3; ++mode) {
            NativeToolClientResult result;
            result.envelope.status = "ok"; result.envelope.orderId = 42;
            result.envelope.detail = command; result.responseJson = root.path;
            reason = mode == 1 ? command : root.path;
            const std::string& borrowedId = mode == 1 ? reason : result.envelope.detail;
            const std::string& directory = mode == 2 ? reason : result.responseJson;
            Check(!client.SubmitStored(directory, borrowedId, result, reason) &&
                  reason == expected && cleared(result), "stored submit lost borrowed inputs");
        }
        loaded.toolCallId = "short";
        Check(!client.LoadStored(root.path, loaded.toolCallId, loaded, reason) &&
              reason == "RESEARCH_OUTBOX_ID_INVALID" && loaded.call.name.empty(), "invalid borrowed stored ID");
        Check(ReadBytes(file) == original, "borrowed calls rewrote immutable request bytes");
    }
    std::string binding, reason;
    Check(native.RecoveryBinding(binding, reason), "borrowed binding baseline");
    const auto request = order.SubmissionRequest(id, permit);
    NativeToolClientResult baseline;
    std::string expected;
    Check(!native.CallBound(request, binding, baseline, expected), "bound fixture needs absent socket");
    NativeToolClientResult result;
    result.responseJson = binding;
    Check(!native.CallBound(request, result.responseJson, result, reason) && reason == expected && cleared(result),
          "bound call cleared result-borrowed binding");
    reason = binding;
    Check(!native.CallBound(request, reason, result, reason) && reason == expected && cleared(result),
          "bound call cleared error-borrowed binding");
    reason = "invalid-binding";
    Check(!native.CallBound(request, reason, result, reason) &&
          reason == "NATIVE_RECOVERY_BINDING_MISMATCH" && cleared(result), "borrowed binding widened authority");
    const auto tokenPath = root.path + "/session.token";
    WriteBytes(tokenPath, config.sessionToken + "\n");
    std::string token = tokenPath;
    Check(NativeToolClient::ReadSessionToken(token, token, reason) && token == config.sessionToken && reason.empty(),
          "token reader cleared its borrowed path");
    token = tokenPath + ".absent";
    Check(!NativeToolClient::ReadSessionToken(token, token, reason) && token.empty() && reason == "TOKEN_FILE_UNSAFE",
          "token read failure changed borrowed-path semantics");
    std::cout << "borrowed_inputs=PASS direct=24 persisted_operations=3 immutable_records=3 bound_call=3 token_path=2\n";
}


// Synthetic codec fixtures only: these bytes do not authorize an actual service.
// The real simulator/Gateway fixture separately consumes service-issued values.
std::string PreviewPayload(const std::vector<std::pair<std::string, std::string>>& fields) {
    std::string payload = "{";
    for (const auto& field : fields) {
        if (payload.size() > 1) payload += ',';
        payload += '"' + field.first + "\":" + field.second;
    }
    return payload + '}';
}
std::string PreviewEnvelope(const std::string& payload, const std::string& tool = "risk.preview_order",
                            TradingToolCallStatus status = TradingToolCallStatus::Ok, long orderId = -1) {
    TradingToolResult result;
    result.toolName = tool; result.status = status; result.payloadJson = payload; result.orderId = orderId;
    return TypedToolProtocol::EncodeResultJson(result);
}
void TypedPreviews() {
    const std::string permit = "sha256:" + std::string(64, 'b');
    const std::vector<std::pair<std::string, std::string>> fields{
        {"approved", "true"}, {"preview_permit", '"' + permit + '"'},
        {"command_id", "\"execution-preview-command-001\""},
        {"permit_expires_at_ms", "9007199254740993"}, {"single_use", "true"},
        {"service_epoch", "\"service-epoch-001\""},
        {"service_fencing_generation", "18446744073709551615"},
        {"authoritative_preview", "{\"note\":\"\\uD83D\\uDE80\",\"values\":[true,false,null,1.25]}"}};
    const auto payload = PreviewPayload(fields), json = PreviewEnvelope(payload);
    TypedPreviewAuthorization authorization; std::string reason;
    auto DecodeFor = [&](const std::string& raw, const std::string& expected) {
        return TypedToolProtocol::DecodePreviewAuthorization(raw, expected, authorization, reason);
    };
    auto Decode = [&](const std::string& raw) { return DecodeFor(raw, "risk.preview_order"); };
    Check(Decode(json) && reason.empty(), "approved preview decoding");
    Check(authorization.toolName == "risk.preview_order" && authorization.commandId == "execution-preview-command-001" &&
          authorization.previewPermit == permit && authorization.permitExpiresAtMs == 9007199254740993LL &&
          authorization.serviceEpoch == "service-epoch-001" &&
          authorization.serviceFencingGeneration == std::numeric_limits<std::uint64_t>::max(),
          "preview integer or identity rounded/discarded");
    auto RejectFor = [&](const std::string& raw, const std::string& expected) {
        Check(Decode(json), "reseed stale authorization");
        Check(!DecodeFor(raw, expected) && !reason.empty() && authorization.toolName.empty() &&
              authorization.commandId.empty() && authorization.previewPermit.empty() &&
              authorization.permitExpiresAtMs == 0 && authorization.serviceEpoch.empty() &&
              authorization.serviceFencingGeneration == 0, "rejected preview retained authorization");
    };
    auto Reject = [&](const std::string& raw) { RejectFor(raw, "risk.preview_order"); };
    for (const auto& tool : {std::string("risk.preview_order"), std::string("risk.preview_flatten")}) {
        Check(DecodeFor(PreviewEnvelope(payload, tool), tool) && authorization.toolName == tool,
              "both canonical preview operations decode");
    }
    RejectFor(json, "risk.preview_flatten"); RejectFor(json, "trade.place_order");
    Reject(PreviewEnvelope(payload, "trade.place_order")); Reject(PreviewEnvelope(payload, "risk.preview_order", TradingToolCallStatus::Ok, 0));
    for (auto status : {TradingToolCallStatus::Rejected, TradingToolCallStatus::Uncertain,
                       TradingToolCallStatus::Duplicate, TradingToolCallStatus::PermissionDenied,
                       TradingToolCallStatus::InvalidTool, TradingToolCallStatus::Error})
        Reject(PreviewEnvelope(payload, "risk.preview_order", status));
    for (std::size_t i = 0; i < fields.size(); ++i) {
        auto missing = fields; missing.erase(missing.begin() + i); Reject(PreviewEnvelope(PreviewPayload(missing)));
        auto duplicate = fields; duplicate.push_back(fields[i]); Reject(PreviewEnvelope(PreviewPayload(duplicate)));
    }
    auto extra = fields; extra.push_back({"future_field", "true"}); Reject(PreviewEnvelope(PreviewPayload(extra)));
    extra = fields; extra.push_back({"\\u0061pproved", "true"}); Reject(PreviewEnvelope(PreviewPayload(extra)));
    const std::vector<std::pair<std::size_t, std::string>> invalid{
        {0,"false"}, {0,"\"true\""}, {0,"1"}, {4,"false"}, {4,"null"},
        {1,"\"sha256:" + std::string(64, 'g') + "\""}, {1,"\"sha256:" + std::string(64, 'A') + "\""},
        {1,"\"sha256:" + std::string(63, 'b') + "\""}, {1,"null"},
        {2,"\"short\""}, {2,"\"execution/command\""}, {2,"\"execution\\u0000command\""},
        {3,"0"}, {3,"-1"}, {3,"1.0"}, {3,"1e3"}, {3,"01"}, {3,"\"123\""}, {3,"9223372036854775808"},
        {5,"\"\""}, {5,"\"" + std::string(129,'x') + "\""}, {5,"\"bad\\nservice\""}, {5,"\"bad\\u0000service\""},
        {6,"0"}, {6,"-1"}, {6,"1.0"}, {6,"1e3"}, {6,"\"1\""}, {6,"18446744073709551616"},
        {7,"[]"}, {7,"true"}, {7,"\"opaque\""}, {7,"{\"x\":1,\"\\u0078\":2}"}};
    for (const auto& bad : invalid) {
        auto changed = fields; changed.at(bad.first).second = bad.second;
        Reject(PreviewEnvelope(PreviewPayload(changed)));
    }
    for (const auto expiry : {1LL, 9007199254740993LL, std::numeric_limits<long long>::max()}) {
        auto changed = fields; changed[3].second = std::to_string(expiry); changed[6].second = "1";
        changed[7].second = "null";
        Check(Decode(PreviewEnvelope(PreviewPayload(changed))) && authorization.permitExpiresAtMs == expiry &&
              authorization.serviceFencingGeneration == 1, "exact signed expiry/unsigned generation bounds");
    }
    auto reversed = fields; std::reverse(reversed.begin(), reversed.end());
    Check(Decode(" \n\t" + PreviewEnvelope(PreviewPayload(reversed)) + "\r\n"), "JSON field order/whitespace");
    reversed[5].first = "\\u0063ommand_id"; // Index 5 is original command_id after reversal.
    Check(Decode(PreviewEnvelope(PreviewPayload(reversed))), "escaped canonical key decoding");
    for (std::size_t i = 0; i < json.size(); ++i) Reject(json.substr(0, i));
    Reject(json + "garbage"); Reject(PreviewEnvelope("null")); Reject(PreviewEnvelope("{}"));
    Reject(std::string(TradingToolWireLimits::MaximumResultEnvelopeBytes() + 1, ' '));
    auto deep = fields; std::string nested = "null";
    for (int i = 0; i < 70; ++i) nested = "{\"x\":" + nested + '}';
    deep[7].second = nested; Reject(PreviewEnvelope(PreviewPayload(deep)));
    // Borrowed input must be captured before clearing the same output object.
    authorization.commandId = json; authorization.toolName = "risk.preview_order";
    Check(TypedToolProtocol::DecodePreviewAuthorization(authorization.commandId, authorization.toolName,
          authorization, reason) && authorization.previewPermit == permit, "borrowed decoder inputs");
    reason = json;
    Check(TypedToolProtocol::DecodePreviewAuthorization(reason, "risk.preview_order", authorization, reason) && reason.empty(),
          "borrowed diagnostic JSON");
    reason = "risk.preview_order";
    Check(TypedToolProtocol::DecodePreviewAuthorization(json, reason, authorization, reason) && reason.empty(),
          "borrowed expected tool");

    auto config = OutboxConfig(); NativeToolClient native(config); NativeStrategyClient client(native);
    InstrumentRef contract; contract.symbol = "EUR"; contract.secType = "CASH";
    contract.exchange = "SIM"; contract.currency = "USD";
    PreparedOrder order("EUR.USD", contract, "BUY", 1, 1.1, 1.09, 1900000000000LL);
    PreparedFlatten flatten("EUR.USD"); NativeToolClientResult result;
    authorization.commandId = "typed-preview-read-001"; authorization.previewPermit = permit;
    Check(!client.PreviewAuthorized(order, authorization.commandId, authorization, result, reason) &&
          authorization.commandId.empty() && authorization.previewPermit.empty() && result.responseJson.empty() &&
          !reason.empty() && reason != "RESEARCH_TOOL_CALL_ID_INVALID", "typed order transport failure/input alias");
    authorization.commandId = "typed-flatten-read-001"; authorization.previewPermit = permit;
    Check(!client.PreviewAuthorized(flatten, authorization.commandId, authorization, result, reason) &&
          authorization.commandId.empty() && result.responseJson.empty() &&
          !reason.empty() && reason != "RESEARCH_TOOL_CALL_ID_INVALID", "typed flatten transport failure/input alias");
    authorization.previewPermit = permit; result.responseJson = "stale";
    Check(!client.PreviewAuthorized(order, "short", authorization, result, reason) &&
          authorization.previewPermit.empty() && result.responseJson.empty() &&
          reason == "RESEARCH_TOOL_CALL_ID_INVALID", "typed local rejection clears prior outputs");
    std::cout << "typed preview codec: exact 64-bit identities, strict schema, truncations, stale-output and alias rejection\n";
}

void PreparedCommandLifecycle() {
    OutboxTestRoot root;
    const auto config = OutboxConfig();
    NativeToolClient native(config); NativeStrategyClient client(native);
    const std::string id = "prepared-cancel-command-001";
    PreparedCancellation cancel(42);
    PreparedStrategyCommand prepared;
    NativeToolClientResult result; std::string reason;
    Check(!prepared.Ready() && !prepared.Durable() && prepared.CommandId().empty(), "fresh prepared state");
    result.envelope.status = "ok"; result.responseJson = "stale";
    Check(!client.Submit(prepared, result, reason) && reason == "RESEARCH_OUTBOX_NOT_DURABLE" &&
          result.envelope.status.empty() && result.responseJson.empty(), "unprepared submission");
    // The nonexistent socket proves cancellation preparation performs no call.
    Check(client.Prepare(cancel, id, prepared, reason) && prepared.Ready() && !prepared.Durable() &&
          prepared.CommandId() == id && prepared.ToolName() == "trade.cancel_order", "cancel preparation");
    Check(!client.Submit(prepared, result, reason) && reason == "RESEARCH_OUTBOX_NOT_DURABLE", "prepare sent before persist");
    Check(!client.Persist("relative", prepared, reason) && prepared.Ready() && !prepared.Durable() &&
          prepared.CommandId() == id, "failed persistence erased original command");
    reason = root.path;
    Check(client.Persist(reason, prepared, reason) && prepared.Durable() && reason.empty(), "borrowed persist path");
    const std::string path = root.path + "/" + id + ".hsr", bytes = ReadBytes(path);
    Check(client.Persist(root.path, cancel, id, reason) && ReadBytes(path) == bytes,
          "prepared facade introduced a different outbox encoding");
    Check(client.Persist(root.path, prepared, reason) && ReadBytes(path) == bytes, "repeated prepared persistence drift");
    Check(client.Restore(root.path, prepared.CommandId(), prepared, reason) && prepared.Durable() &&
          prepared.CommandId() == id, "borrowed restore identity");
    PreparedStrategyCommand copied = prepared;
    result.envelope.status = "ok"; result.envelope.orderId = 42; result.responseJson = "stale";
    Check(!client.Submit(copied, result, reason) && !reason.empty() && result.envelope.status.empty() &&
          result.envelope.orderId == -1 && result.responseJson.empty() && ReadBytes(path) == bytes,
          "prepared transport failure changed record or retained success");
    auto different = config; different.sessionToken += "-rotated";
    NativeToolClient otherNative(different); NativeStrategyClient other(otherNative);
    Check(!other.Submit(copied, result, reason) && reason == "NATIVE_RECOVERY_BINDING_MISMATCH", "prepared cross-token send");
    Check(!other.Persist(root.path, copied, reason) && reason == "NATIVE_RECOVERY_BINDING_MISMATCH" &&
          copied.Ready() && !copied.Durable() && copied.CommandId() == id && ReadBytes(path) == bytes,
          "prepare/persist token rotation rebound the request");
    Check(!other.Restore(root.path, id, copied, reason) && !copied.Ready() && !copied.Durable(), "failed restore leaked prior state");
    Check(client.Restore(root.path, id, copied, reason), "restore after binding failure");
    Check(::chmod(path.c_str(), 0640) == 0, "prepared unsafe fixture mode");
    Check(!client.Submit(copied, result, reason), "cached durable bit bypassed disk permissions");
    Check(::chmod(path.c_str(), 0600) == 0, "prepared restore fixture mode");
    // An owner can replace a file with another VALID checksummed record. A
    // cached object must still refuse changed request bytes, not silently send.
    Check(::unlink(path.c_str()) == 0, "prepared replacement fixture unlink");
    Check(client.Persist(root.path, PreparedCancellation(43), id, reason), "prepared replacement fixture store");
    Check(!client.Submit(copied, result, reason) && reason == "RESEARCH_OUTBOX_PREPARED_MISMATCH", "prepared snapshot silently changed");
    WriteBytes(path, bytes);
    Check(client.Restore(root.path, id, copied, reason), "restore original prepared bytes");
    Check(::unlink(path.c_str()) == 0, "prepared missing file fixture");
    Check(!client.Submit(copied, result, reason) && result.responseJson.empty(), "deleted durable record still sent");
    for (const auto& legacy : {std::string("HRO1not-a-canonical-record"),
                              std::string("{\"schema\":\"hepta.research.outbox.v1\"}")}) {
        WriteBytes(path, legacy);
        Check(!client.Restore(root.path, id, copied, reason) && !copied.Ready(), "legacy outbox silently converted");
    }
    WriteBytes(path, bytes);
    Check(client.Prepare(cancel, prepared.CommandId(), prepared, reason) && prepared.CommandId() == id &&
          !prepared.Durable(), "borrowed prepare identity or implicit durability");
    Check(!client.Prepare(cancel, "short", prepared, reason) && !prepared.Ready(), "invalid prepared cancel identity");
    InstrumentRef contract; contract.symbol = "EUR"; contract.secType = "CASH";
    contract.exchange = "SIM"; contract.currency = "USD";
    PreparedOrder order("EUR.USD", contract, "BUY", 10, 1.1, 1.09, 1900000000000LL);
    PreparedFlatten flatten("EUR.USD");
    for (int which = 0; which != 4; ++which) {
        Check(client.Prepare(cancel, id, prepared, reason), "prepare stale-output fixture");
        result.envelope.status = "ok"; result.responseJson = "stale";
        const std::string preview = which < 2 ? "prepared-preview-001" : "short";
        const bool ok = which % 2 ? client.Prepare(flatten, preview, prepared, result, reason)
                                  : client.Prepare(order, preview, prepared, result, reason);
        Check(!ok && !prepared.Ready() && !prepared.Durable() && result.envelope.status.empty() &&
              result.responseJson.empty() && !reason.empty(), "prepared preview failure leaked authority");
    }
    // Existing direct API records for all operations are restorable without
    // inventing their permits, changing their schema or gaining trading rights.
    const std::string permit = "sha256:" + std::string(64, 'a');
    Check(client.Persist(root.path, order, "prepared-order-001", permit, reason), "prepared order fixture");
    Check(client.Restore(root.path, "prepared-order-001", prepared, reason) &&
          prepared.ToolName() == "trade.place_order", "existing order not restorable");
    std::string applicationBinding;
    Check(native.RecoveryBinding(applicationBinding, reason), "application binding");
    const std::string orderPath = root.path + "/prepared-order-001.hsr";
    const std::string orderBytes = ReadBytes(orderPath);
    Check(client.MatchesOrder(prepared, order, applicationBinding, reason) && reason.empty(), "application intent match");
    PreparedOrder changedOrder("EUR.USD", contract, "BUY", 11, 1.1, 1.09, 1900000000000LL);
    Check(!client.MatchesOrder(prepared, changedOrder, applicationBinding, reason) &&
          reason == "RESEARCH_APPLICATION_INTENT_MISMATCH", "application quantity conflict");
    Check(!client.MatchesOrder(prepared, order, "sha256:" + std::string(64, 'f'), reason) &&
          reason == "NATIVE_RECOVERY_BINDING_MISMATCH", "application foreign binding");
    Check(!other.MatchesOrder(prepared, order, applicationBinding, reason) &&
          reason == "NATIVE_RECOVERY_BINDING_MISMATCH", "application credential rotation");
    Check(ReadBytes(orderPath) == orderBytes, "application validation rewrote HSR1");
    Check(::unlink(orderPath.c_str()) == 0 &&
          client.Persist(root.path, changedOrder, "prepared-order-001", permit, reason), "application changed record fixture");
    Check(!client.MatchesOrder(prepared, changedOrder, applicationBinding, reason) &&
          reason == "RESEARCH_OUTBOX_PREPARED_MISMATCH", "application opaque snapshot replacement");
    Check(client.Restore(root.path, "prepared-order-001", prepared, reason) &&
          !client.MatchesOrder(prepared, order, applicationBinding, reason), "application valid wrong request accepted");
    WriteBytes(orderPath, orderBytes);
    Check(client.Persist(root.path, flatten, "prepared-flatten-001", permit, reason), "prepared flatten fixture");
    Check(client.Restore(root.path, "prepared-flatten-001", prepared, reason) &&
          prepared.ToolName() == "trade.flatten_position", "existing flatten not restorable");
}


void StoredInspectionLifecycle() {
    OutboxTestRoot root;
    const auto config = OutboxConfig();
    NativeToolClient native(config); NativeStrategyClient client(native);
    InstrumentRef contract; contract.symbol = "EUR"; contract.secType = "CASH";
    contract.exchange = "SIM"; contract.currency = "USD";
    PreparedOrder order("EUR.USD", contract, "BUY", 10, 1.1, 1.09, 1900000000000LL);
    PreparedCancellation cancellation(42); PreparedFlatten flatten("EUR.USD");
    const std::string permit = "sha256:" + std::string(64, 'a');
    NativeToolClientResult result; std::string reason;
    PreparedStrategyCommand prepared;
    Check(!client.Inspect(prepared, "inspection-query-001", result, reason) &&
          reason == "RESEARCH_OUTBOX_NOT_DURABLE", "unprepared inspection used cached state");
    for (int operation = 0; operation != 3; ++operation) {
        const std::string id = "inspection-command-00" + std::to_string(operation);
        const std::string query = "inspection-query-00" + std::to_string(operation);
        const bool persisted = operation == 0 ? client.Persist(root.path, order, id, permit, reason) :
            operation == 1 ? client.Persist(root.path, cancellation, id, reason) :
                             client.Persist(root.path, flatten, id, permit, reason);
        Check(persisted && client.Restore(root.path, id, prepared, reason), "inspection fixture persistence");
        const auto path = root.path + "/" + id + ".hsr", bytes = ReadBytes(path);
        for (int facade = 0; facade != 2; ++facade) {
            const auto inspect = [&](const std::string& queryId) {
                return facade ? client.Inspect(prepared, queryId, result, reason) :
                    client.InspectStored(root.path, id, queryId, result, reason);
            };
            result.envelope.status = "ok"; result.responseJson = "stale-success";
            Check(!inspect(id) && reason == "RESEARCH_INSPECTION_QUERY_ID_REUSED" &&
                  result.envelope.status.empty() && result.responseJson.empty(), "inspection reused mutation ID");
            Check(!inspect("short") && reason == "RESEARCH_TOOL_CALL_ID_INVALID", "invalid inspection ID");
            // Existing missing endpoint is real; no test transport is injected.
            result.responseJson = query;
            Check(!inspect(result.responseJson) && !reason.empty() &&
                  reason != "RESEARCH_TOOL_CALL_ID_INVALID" && result.responseJson.empty() &&
                  result.envelope.status.empty(), "borrowed inspection ID or stale transport output");
            reason = query;
            Check(!inspect(reason) && !reason.empty() && reason != "RESEARCH_TOOL_CALL_ID_INVALID",
                  "borrowed inspection diagnostic");
            Check(ReadBytes(path) == bytes && prepared.Durable(), "inspection rewrote original request");
            for (int change = 0; change != 2; ++change) {
                auto otherConfig = config;
                if (change) otherConfig.socketPath += "-other";
                else otherConfig.sessionToken += "-rotated";
                NativeToolClient otherNative(otherConfig); NativeStrategyClient other(otherNative);
                const bool called = facade ? other.Inspect(prepared, query, result, reason) :
                    other.InspectStored(root.path, id, query, result, reason);
                Check(!called && reason == "NATIVE_RECOVERY_BINDING_MISMATCH" &&
                      result.responseJson.empty(), "inspection queried another endpoint/credential");
            }
            Check(::chmod(path.c_str(), 0640) == 0, "inspection unsafe fixture");
            Check(!inspect(query) && reason == "RESEARCH_OUTBOX_RECORD_UNSAFE", "inspection ignored unsafe record");
            Check(::chmod(path.c_str(), 0600) == 0, "inspection restore mode");
            auto corrupt = bytes; corrupt.back() ^= 1; WriteBytes(path, corrupt);
            Check(!inspect(query) && reason == "RESEARCH_OUTBOX_DIGEST_MISMATCH", "inspection ignored corrupt record");
            WriteBytes(path, bytes);
        }
        // Same-UID replacement is not authentication: the opaque snapshot must
        // nevertheless detect a different, valid checksummed request at its path.
        Check(::unlink(path.c_str()) == 0 && client.Persist(root.path, PreparedCancellation(43), id, reason),
              "inspection replacement fixture");
        Check(!client.Inspect(prepared, query, result, reason) &&
              reason == "RESEARCH_OUTBOX_PREPARED_MISMATCH", "inspection accepted changed prepared bytes");
        WriteBytes(path, bytes);
        Check(::unlink(path.c_str()) == 0, "inspection missing fixture");
        Check(!client.InspectStored(root.path, id, query, result, reason) &&
              reason == "RESEARCH_OUTBOX_RECORD_UNSAFE", "missing record became status fallback");
        Check(!client.Inspect(prepared, query, result, reason), "missing prepared record became status fallback");
        WriteBytes(path, bytes);
    }
    // Capture ALL borrowed string inputs before clearing outputs.
    const std::string id = "inspection-command-000", query = "inspection-query-final";
    result.responseJson = root.path; reason = id; result.envelope.detail = query;
    Check(!client.InspectStored(result.responseJson, reason, result.envelope.detail, result, reason) &&
          reason != "RESEARCH_OUTBOX_DIRECTORY_UNSAFE" && reason != "RESEARCH_OUTBOX_ID_INVALID" &&
          reason != "RESEARCH_TOOL_CALL_ID_INVALID" && result.responseJson.empty(), "borrowed inspection inputs lost");
    Check(!client.InspectStored(root.path, "short", query, result, reason) &&
          reason == "RESEARCH_OUTBOX_ID_INVALID", "invalid original command accepted");
    std::cout << "stored_inspection=PASS three_operations both_facades binding snapshot immutable_records\n";
}

void Tests() {
    StoredInspectionLifecycle();
    PreparedCommandLifecycle();
    TypedPreviews();
    OutboxTests();
    BorrowedInputTests();
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
