#include "../HeptaTrade/cli/heptactl_pnl_stream.h"
#include "../HeptaTrade/cli/heptactl_exit_codes.h"
#include <cassert>
#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <sstream>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>

namespace {
const std::string kHash = "sha256:23e27458810abb6b8949d4cae06768582133e9f909235e06828bfda9d78811e0";
std::string Catalog() {
    const std::string descriptor = "{\"name\":\"market.get_quote\","
        "\"description\":\"Read the latest normalized quote for one instrument.\","
        "\"required_capability\":\"market.read\",\"effect\":\"read\","
        "\"timeout_ms\":8000,\"schema_hash\":\"" + kHash + "\","
        "\"input_schema\":{\"type\":\"object\",\"required\":[\"instrument\"],"
        "\"additionalProperties\":false},\"result_schema\":{\"type\":\"object\",\"additionalProperties\":true}}";
    return "{\"status\":\"ok\",\"tool\":\"system.tools.list\",\"reason_code\":\"\",\"detail\":\"\",\"order_id\":-1,\"payload\":{"
        "\"protocol\":\"hepta.agent-tools\",\"protocol_version\":1,\"protocol_min_version\":1,\"protocol_max_version\":1,"
        "\"schema_version\":2,\"catalog_schema_hash\":\"sha256:a61d046525e879564d25dbf7556033eebbaf1241cfa5fd85fb60c54954df9114\",\"tools\":[" + descriptor + "]}}";
}
void InvalidInputNeverCallsNative() {
    HeptaCtlCommand command; command.request.toolCallId = "pnl-test";
    NativeToolClient client{NativeToolClientConfig()};
    for (const std::string& line : {"trade.place_order\n", "trade.cancel_order\n", "trade.flatten_position\n",
        "risk.preview_order\n", "execution.reconcile\n", "system.tools.list\n", "market.get_quote SPY\n",
        "orders.list extra\n", "\n", "orders.list", "market.get_quote\n", "orders.list\r\n"}) {
        std::istringstream input(line); std::ostringstream output, errors;
        assert(RunPnlReadStream(command, client, input, output, errors) == HeptaCtlUsageOrCredential);
        assert(output.str().empty());
        assert(errors.str().find("PNL_READ_STREAM_") == 0);
    }
    std::istringstream huge(std::string(10000, 'x') + "\n"); std::ostringstream output, errors;
    assert(RunPnlReadStream(command, client, huge, output, errors) == HeptaCtlUsageOrCredential);
    assert(errors.str() == "PNL_READ_STREAM_LINE_TOO_LARGE\n");
}
void CachedNativeDiscoveryStillChecksEveryRequest(bool rejectSecond) {
    char directory[] = "/tmp/hepta-pnl-cli-XXXXXX"; assert(::mkdtemp(directory));
    const std::string path = std::string(directory) + "/gateway.sock";
    int listener = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0); assert(listener >= 0);
    sockaddr_un address{}; address.sun_family = AF_UNIX;
    std::strncpy(address.sun_path, path.c_str(), sizeof(address.sun_path)-1);
    assert(::bind(listener, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
    assert(::listen(listener, 4) == 0);
    const pid_t child = ::fork(); assert(child >= 0);
    if (child == 0) {
        ::alarm(10);
        for (int index = 0; index != 3; ++index) {
            const int fd = ::accept(listener, nullptr, nullptr); if (fd < 0) _exit(2);
            std::string body, reason; TradingToolHostRequest request;
            if (!TypedToolProtocol::ReadFrame(fd, 65536, 1000, body, reason) ||
                !TypedToolProtocol::DecodeRequest(body, request, reason)) _exit(3);
            if (request.sessionToken != "pnl-test-token") _exit(4);
            if (index == 0) {
                if (request.call.name != "system.tools.list") _exit(5);
            } else {
                if (request.call.name != "market.get_quote" || request.expectedSchemaHash != kHash ||
                    request.call.instrument != (index == 1 ? "EUR.USD" : "BTC.USD") ||
                    request.toolCallId != "pnl-test-pnl-" + std::to_string(index)) _exit(6);
            }
            const bool reject = rejectSecond && index == 2;
            const std::string response = index == 0 ? Catalog() :
                "{\"status\":\"" + std::string(reject ? "rejected" : "ok") + "\",\"tool\":\"market.get_quote\","
                "\"reason_code\":\"" + (reject ? "SCHEMA_HASH_MISMATCH" : "") + "\",\"detail\":\"\",\"order_id\":-1,\"payload\":{\"bid\":1.0,\"ask\":1.1}}";
            if (!TypedToolProtocol::WriteFrame(fd, response, 1000, reason)) _exit(7);
            ::close(fd);
        }
        ::close(listener); _exit(0);
    }
    NativeToolClientConfig config; config.socketPath = path; config.sessionToken = "pnl-test-token"; config.timeoutMs = 1000;
    NativeToolClient client(config); HeptaCtlCommand command; command.request.toolCallId = "pnl-test";
    std::istringstream input("market.get_quote EUR.USD\nmarket.get_quote BTC.USD\n" +
        std::string(rejectSecond ? "market.get_quote EUR.USD\n" : ""));
    std::ostringstream output, errors;
    assert(RunPnlReadStream(command, client, input, output, errors) == (rejectSecond ? HeptaCtlRejected : HeptaCtlSuccess));
    assert(errors.str().empty());
    const std::string raw = output.str();
    assert(std::count(raw.begin(), raw.end(), '\n') == 2);
    int status = 0; assert(::waitpid(child, &status, 0) == child);
    assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    ::close(listener); ::unlink(path.c_str()); ::rmdir(directory);
}
}
int main() {
    InvalidInputNeverCallsNative();
    CachedNativeDiscoveryStillChecksEveryRequest(false);
    CachedNativeDiscoveryStillChecksEveryRequest(true);
}
