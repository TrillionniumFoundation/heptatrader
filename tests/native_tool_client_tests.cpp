#include "../HeptaTrade/client/native_tool_client.h"
#include "../HeptaTrade/client/native_tool_discovery_contract.h"
#include "../HeptaTrade/tool_host/unix_session_supervisor_client.h"
#include "../HeptaTrade/tool_host/unix_tool_client.h"

#include <algorithm>
#include <cassert>
#include <arpa/inet.h>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <poll.h>
#include <string>
#include <vector>
#include <sys/stat.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>

#ifdef HEPTA_TEST_WRAP_SOCKET
namespace { bool smallNextSupervisorSocket = false; }
extern "C" int __real_socket(int domain, int type, int protocol);
extern "C" int __wrap_socket(int domain, int type, int protocol)
{
    const int fd = __real_socket(domain, type, protocol);
    if (fd >= 0 && smallNextSupervisorSocket && domain == AF_UNIX && (type & SOCK_STREAM))
    {
        smallNextSupervisorSocket = false;
        const int bytes = 1024;
        assert(::setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &bytes, sizeof(bytes)) == 0);
    }
    return fd;
}
#endif

namespace
{
enum class SupervisorResponse { Immediate, SlowBody, NoHeader, InvalidLength, BackpressuredRequest };

bool SupervisorCallAgainstResponse(SupervisorResponse scenario,
                                  const SessionSupervisorRequest& request,
                                  SessionSupervisorResult& result,
                                  std::string& reason)
{
    // A local wire peer only decodes the request; it owns no real lease or
    // Broker authority. Slow fragments and send-buffer pressure remain real I/O.
    char directory[] = "/tmp/hepta-supervisor-client-socket-XXXXXX";
    assert(::mkdtemp(directory) != nullptr);
    const std::string socketPath = std::string(directory) + "/supervisor.sock";
    const int listener = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(listener >= 0);
    sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    assert(socketPath.size() < sizeof(address.sun_path));
    std::strncpy(address.sun_path, socketPath.c_str(), sizeof(address.sun_path) - 1);
    assert(::bind(listener, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
    assert(::listen(listener, 1) == 0);

    const pid_t child = ::fork();
    assert(child >= 0);
    if (child == 0)
    {
        const int connection = ::accept(listener, nullptr, nullptr);
        if (connection < 0) _exit(2);
        if (scenario == SupervisorResponse::BackpressuredRequest) ::usleep(400000);
        std::string body, error;
        if (!TypedToolProtocol::ReadFrame(connection, 16384, 2000, body, error))
        {
            // The fixed client can exhaust its write budget and close before
            // its large request reaches this deliberately delayed reader.
            ::close(connection); ::close(listener);
            _exit(scenario == SupervisorResponse::BackpressuredRequest ? 0 : 3);
        }
        SessionSupervisorRequest decoded;
        if (!SessionSupervisorProtocol::DecodeRequest(body, decoded, error) ||
            decoded.operation != request.operation || decoded.token != request.token ||
            decoded.terminalEvidence != request.terminalEvidence) _exit(4);
        if (scenario == SupervisorResponse::NoHeader) ::usleep(400000);
        else if (scenario == SupervisorResponse::InvalidLength)
        {
            const std::uint32_t invalid = htonl(65537);
            if (::send(connection, &invalid, sizeof(invalid), MSG_NOSIGNAL) != sizeof(invalid)) _exit(5);
        }
        else
        {
            SessionSupervisorResult reply;
            reply.accepted = true; reply.leaseGeneration = 2;
            reply.ReasonCode() = "FIXTURE_CONTROL_ACCEPTED";
            const std::string response = SessionSupervisorProtocol::EncodeResult(reply);
            if (scenario == SupervisorResponse::SlowBody)
            {
                const std::uint32_t size = htonl(static_cast<std::uint32_t>(response.size()));
                if (::send(connection, &size, sizeof(size), MSG_NOSIGNAL) != sizeof(size)) _exit(6);
                const std::size_t chunk = (response.size() + 4) / 5;
                for (std::size_t offset = 0; offset < response.size(); offset += chunk)
                {
                    ::usleep(80000);  // Every fragment arrives within 200 ms; the frame does not.
                    const std::size_t count = std::min(chunk, response.size() - offset);
                    if (::send(connection, response.data() + offset, count, MSG_NOSIGNAL) !=
                            static_cast<ssize_t>(count)) break;
                }
            }
            else TypedToolProtocol::WriteFrame(connection, response, 2000, error);
        }
        ::close(connection); ::close(listener);
        _exit(0);
    }
#ifdef HEPTA_TEST_WRAP_SOCKET
    // Only the next client socket changes. Listener and all unrelated tests
    // keep their normal buffers, and the syscall/transport remains unchanged.
    smallNextSupervisorSocket = scenario == SupervisorResponse::BackpressuredRequest;
#endif
    const bool called = UnixSessionSupervisorClient::Call(socketPath, request, result, reason,
        scenario == SupervisorResponse::Immediate ? 2000 : 200, 32768);
#ifdef HEPTA_TEST_WRAP_SOCKET
    assert(!smallNextSupervisorSocket);
#endif
    ::close(listener);
    int status = 0;
    assert(::waitpid(child, &status, 0) == child);
    assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    std::remove(socketPath.c_str());
    assert(::rmdir(directory) == 0);
    return called;
}

void TestSupervisorClientFrameBudgetsAndReasons()
{
    SessionSupervisorRequest request;
    request.operation = SessionSupervisorOperation::Revoke;
    request.token = "supervisor-client-fixture-token";
    request.expectedGeneration = 1;
    SessionSupervisorResult result;
    std::string reason;
    assert(SupervisorCallAgainstResponse(SupervisorResponse::Immediate, request, result, reason));
    assert(result.accepted && result.leaseGeneration == 2);
    assert(!SupervisorCallAgainstResponse(SupervisorResponse::SlowBody, request, result, reason));
    assert(reason == "SUPERVISOR_FRAME_BODY_TIMEOUT");
    assert(!SupervisorCallAgainstResponse(SupervisorResponse::NoHeader, request, result, reason));
    assert(reason == "SUPERVISOR_FRAME_HEADER_TIMEOUT");
    assert(!SupervisorCallAgainstResponse(SupervisorResponse::InvalidLength, request, result, reason));
    assert(reason == "SUPERVISOR_FRAME_LENGTH_REJECTED");
#ifdef HEPTA_TEST_WRAP_SOCKET
    request.operation = SessionSupervisorOperation::PaperTerminalWitnessAck;
    request.recoveryId = "fixture-recovery";
    request.finalizationId = "fixture-finalization";
    request.expectedOwnerSetSha256 = "sha256:" + std::string(64, 'a');
    request.expectedOwnerCount = 1;
    request.receiptSha256 = "sha256:" + std::string(64, 'b');
    request.terminalEvidenceSha256 = "sha256:" + std::string(64, 'c');
    request.terminalEvidence.assign(12288, 'e');
    request.terminalEvidence.replace(0, 5, "HPE1\n");
    request.terminalEvidence.back() = '\n';
    std::string encoded;
    assert(SessionSupervisorProtocol::EncodeRequest(request, encoded, reason));
    assert(encoded.size() > 12288 && encoded.size() <= 16384);
    assert(!SupervisorCallAgainstResponse(SupervisorResponse::BackpressuredRequest, request, result, reason));
    assert(reason == "SUPERVISOR_FRAME_WRITE_TIMEOUT");
#endif
}

#ifdef HEPTA_TEST_WRAP_SOCKET
void TestClientConnectBacklogDeadline(bool supervisor)
{
    char directory[] = "/tmp/hepta-client-backlog-XXXXXX";
    assert(::mkdtemp(directory) != nullptr);
    const std::string socketPath = std::string(directory) + "/full.sock";
    const int listener = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(listener >= 0);
    sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    assert(socketPath.size() < sizeof(address.sun_path));
    std::strncpy(address.sun_path, socketPath.c_str(), sizeof(address.sun_path) - 1);
    assert(::bind(listener, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
    // Linux permits one pending AF_UNIX connection for backlog=0. Occupy it
    // before Call(), so an unbounded blocking connect cannot reach framing.
    assert(::listen(listener, 0) == 0);
    const int filler = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(filler >= 0);
    assert(::connect(filler, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
    const int probe = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    assert(probe >= 0);
    assert(::connect(probe, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == -1);
    assert(errno == EAGAIN || errno == EWOULDBLOCK);
    ::close(probe);
    const pid_t child = ::fork();
    assert(child >= 0);
    if (child == 0)
    {
        ::close(filler);
        ::usleep(400000);  // Longer than the caller's entire connect budget.
        const int first = ::accept(listener, nullptr, nullptr);
        if (first < 0) _exit(2);
        ::close(first);
        // The fixed nonblocking client refused the full queue and never
        // enqueued this second connection. Do not block in accept for it.
        pollfd pending = {listener, POLLIN, 0};
        const int ready = ::poll(&pending, 1, 500);
        if (ready == 0) { ::close(listener); _exit(0); }
        if (ready < 0 || !(pending.revents & POLLIN)) _exit(3);
        const int connection = ::accept(listener, nullptr, nullptr);
        if (connection < 0) _exit(4);
        std::string body, error, response;
        if (!TypedToolProtocol::ReadFrame(connection, 16384, 2000, body, error)) _exit(5);
        if (supervisor)
        {
            SessionSupervisorRequest decoded;
            if (!SessionSupervisorProtocol::DecodeRequest(body, decoded, error)) _exit(6);
            SessionSupervisorResult reply;
            reply.accepted = true; reply.leaseGeneration = 2;
            response = SessionSupervisorProtocol::EncodeResult(reply);
        }
        else
        {
            TradingToolHostRequest decoded;
            if (!TypedToolProtocol::DecodeRequest(body, decoded, error)) _exit(7);
            response = "{\"status\":\"ok\",\"tool\":\"system.get_health\","
                "\"reason_code\":\"\",\"detail\":\"\",\"order_id\":-1,\"payload\":null}";
        }
        if (!TypedToolProtocol::WriteFrame(connection, response, 2000, error)) _exit(8);
        ::close(connection); ::close(listener);
        _exit(0);
    }
    std::string reason;
    bool called;
    if (supervisor)
    {
        SessionSupervisorRequest request;
        request.operation = SessionSupervisorOperation::Revoke;
        request.token = "supervisor-backlog-fixture-token";
        request.expectedGeneration = 1;
        SessionSupervisorResult result;
        called = UnixSessionSupervisorClient::Call(socketPath, request, result, reason, 200, 32768);
    }
    else
    {
        TradingToolHostRequest request;
        request.sessionToken = "tool-backlog-fixture-token";
        request.toolCallId = "backlog-call";
        request.call.name = "system.get_health";
        std::string response;
        called = UnixToolClient::Call(socketPath, request, response, reason, 200, 32768);
    }
    ::close(listener);
    int status = 0;
    assert(::waitpid(child, &status, 0) == child);
    ::close(filler);
    assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    std::remove(socketPath.c_str());
    assert(::rmdir(directory) == 0);
    if (supervisor)
    {
        assert(!called);
        assert(reason == "SUPERVISOR_SOCKET_CONNECT_FAILED");
    }
    else
    {
        assert(!called);
        assert(reason == "SOCKET_CONNECT_FAILED");
    }
}
#endif

bool CallAgainstResponse(const std::string& response,
                         std::size_t maxResponseBytes,
                         NativeToolClientResult& result,
                         std::string& reason)
{
    char directory[] = "/tmp/hepta-native-client-socket-XXXXXX";
    assert(::mkdtemp(directory) != nullptr);
    const std::string socketPath = std::string(directory) + "/tool.sock";

    const int listener = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(listener >= 0);
    sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    assert(socketPath.size() < sizeof(address.sun_path));
    std::strncpy(address.sun_path, socketPath.c_str(), sizeof(address.sun_path) - 1);
    assert(::bind(listener, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
    assert(::listen(listener, 1) == 0);

    const pid_t child = ::fork();
    assert(child >= 0);
    if (child == 0)
    {
        const int connection = ::accept(listener, nullptr, nullptr);
        if (connection < 0) _exit(2);
        std::string requestBody;
        std::string childReason;
        if (!TypedToolProtocol::ReadFrame(
                connection, 65536, 2000, requestBody, childReason))
            _exit(3);
        TradingToolHostRequest request;
        if (!TypedToolProtocol::DecodeRequest(requestBody, request, childReason))
            _exit(4);
        TypedToolProtocol::WriteFrame(connection, response, 2000, childReason);
        ::close(connection);
        ::close(listener);
        _exit(0);
    }

    NativeToolClientConfig config;
    config.socketPath = socketPath;
    config.sessionToken = "native-client-session-token";
    config.timeoutMs = 2000;
    config.maxResponseBytes = maxResponseBytes;
    NativeToolClient client(config);
    TradingToolHostRequest request;
    request.toolCallId = "native-test-001";
    request.call.name = "system.tools.list";
    const bool called = client.Call(request, result, reason);

    ::close(listener);
    int status = 0;
    assert(::waitpid(child, &status, 0) == child);
    assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    std::remove(socketPath.c_str());
    assert(::rmdir(directory) == 0);
    return called;
}

bool DescribeAgainstListedCatalog(
    const std::string& listResponse,
    const std::string& describeResponse,
    const std::string& targetToolName,
    NativeToolClientResult& result,
    std::string& reason)
{
    char directory[] = "/tmp/hepta-native-client-discovery-XXXXXX";
    assert(::mkdtemp(directory) != nullptr);
    const std::string socketPath = std::string(directory) + "/tool.sock";

    const int listener = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(listener >= 0);
    sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    assert(socketPath.size() < sizeof(address.sun_path));
    std::strncpy(address.sun_path, socketPath.c_str(), sizeof(address.sun_path) - 1);
    assert(::bind(listener, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
    assert(::listen(listener, 2) == 0);

    const pid_t child = ::fork();
    assert(child >= 0);
    if (child == 0)
    {
        const std::string responses[] = {listResponse, describeResponse};
        const std::string operations[] = {
            "system.tools.list", "system.tools.describe"
        };
        for (unsigned int index = 0; index < 2; ++index)
        {
            const int connection = ::accept(listener, nullptr, nullptr);
            if (connection < 0) _exit(2);
            std::string requestBody;
            std::string childReason;
            if (!TypedToolProtocol::ReadFrame(
                    connection, 65536, 2000, requestBody, childReason))
                _exit(3);
            TradingToolHostRequest request;
            if (!TypedToolProtocol::DecodeRequest(
                    requestBody, request, childReason) ||
                request.call.name != operations[index] ||
                (index == 1 && request.call.targetToolName != targetToolName))
                _exit(4);
            if (!TypedToolProtocol::WriteFrame(
                    connection, responses[index], 2000, childReason))
                _exit(5);
            ::close(connection);
        }
        ::close(listener);
        _exit(0);
    }

    NativeToolClientConfig config;
    config.socketPath = socketPath;
    config.sessionToken = "native-client-session-token";
    config.timeoutMs = 2000;
    config.maxResponseBytes = 65536;
    NativeToolClient client(config);

    TradingToolHostRequest list;
    list.toolCallId = "native-list-001";
    list.call.name = "system.tools.list";
    NativeToolClientResult listResult;
    assert(client.Call(list, listResult, reason));

    TradingToolHostRequest describe;
    describe.toolCallId = "native-describe-001";
    describe.call.name = "system.tools.describe";
    describe.call.targetToolName = targetToolName;
    const bool called = client.Call(describe, result, reason);

    ::close(listener);
    int status = 0;
    assert(::waitpid(child, &status, 0) == child);
    assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    std::remove(socketPath.c_str());
    assert(::rmdir(directory) == 0);
    return called;
}

enum class DiscoveryScenario { Automatic, BoundReuse, InflightRotation };

void TestDiscoveryCredentialScenario(DiscoveryScenario scenario)
{
    const std::string descriptorHash =
        "sha256:23e27458810abb6b8949d4cae06768582133e9f909235e06828bfda9d78811e0";
    const std::string catalogHash =
        "sha256:a61d046525e879564d25dbf7556033eebbaf1241cfa5fd85fb60c54954df9114";
    const std::string descriptor =
        "{\"name\":\"market.get_quote\","
        "\"description\":\"Read the latest normalized quote for one instrument.\","
        "\"required_capability\":\"market.read\",\"effect\":\"read\","
        "\"timeout_ms\":8000,\"schema_hash\":\"" + descriptorHash +
        "\",\"input_schema\":{\"type\":\"object\",\"required\":[\"instrument\"],"
        "\"additionalProperties\":false},\"result_schema\":"
        "{\"type\":\"object\",\"additionalProperties\":true}}";
    const std::string discovery =
        "{\"protocol\":\"hepta.agent-tools\",\"protocol_version\":1,"
        "\"protocol_min_version\":1,\"protocol_max_version\":1,"
        "\"schema_version\":2,\"catalog_schema_hash\":\"" + catalogHash +
        "\",\"tools\":[" + descriptor + "]}";
    const std::string responses[] = {
        "{\"status\":\"ok\",\"tool\":\"system.tools.list\",\"reason_code\":\"\","
        "\"detail\":\"\",\"order_id\":-1,\"payload\":" + discovery + "}",
        "{\"status\":\"ok\",\"tool\":\"market.get_quote\",\"reason_code\":\"\","
        "\"detail\":\"\",\"order_id\":-1,\"payload\":{\"bid\":1.0,\"ask\":1.1}}"
    };

    const bool reuseBound = scenario == DiscoveryScenario::BoundReuse;
    const bool rotateInflight = scenario == DiscoveryScenario::InflightRotation;
    const std::vector<bool> discoveryRequests = reuseBound ?
        std::vector<bool>{true, false, false, false, true, false, false} :
        (rotateInflight ? std::vector<bool>{true, false, true, false} :
                          std::vector<bool>{true, false});
    char directory[] = "/tmp/hepta-native-client-schema-XXXXXX";
    assert(::mkdtemp(directory) != nullptr);
    const std::string socketPath = std::string(directory) + "/tool.sock";
    const int listener = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(listener >= 0);
    sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    std::strncpy(address.sun_path, socketPath.c_str(),
                 sizeof(address.sun_path) - 1);
    assert(::bind(listener, reinterpret_cast<sockaddr*>(&address),
                  sizeof(address)) == 0);
    assert(::listen(listener, 2) == 0);

    const std::string tokenPath = std::string(directory) + "/token";
    const auto writeToken = [&tokenPath](const std::string& value) {
        const int fd = ::open(tokenPath.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
        assert(fd >= 0);
        assert(::write(fd, value.data(), value.size()) == static_cast<ssize_t>(value.size()));
        assert(::close(fd) == 0);
    };
    writeToken("native-client-session-token");
    const pid_t child = ::fork();
    assert(child >= 0);
    if (child == 0)
    {
        ::alarm(10); // a failed cache expectation must not orphan a listener
        for (std::size_t index = 0; index < discoveryRequests.size(); ++index)
        {
            const int connection = ::accept(listener, nullptr, nullptr);
            if (connection < 0) _exit(2);
            std::string requestBody;
            std::string childReason;
            TradingToolHostRequest observed;
            if (!TypedToolProtocol::ReadFrame(
                    connection, 65536, 2000, requestBody, childReason) ||
                !TypedToolProtocol::DecodeRequest(
                    requestBody, observed, childReason))
                _exit(3);
            if ((discoveryRequests[index] &&
                 (observed.call.name != "system.tools.list" ||
                  !observed.expectedSchemaHash.empty())) ||
                (!discoveryRequests[index] &&
                 (observed.call.name != "market.get_quote" ||
                  observed.call.instrument != "EUR.USD" ||
                  observed.expectedSchemaHash != descriptorHash)))
                _exit(4);
            const bool rotated = (reuseBound && index >= 4) || (rotateInflight && index >= 2);
            if (observed.sessionToken != (rotated ? "rotated-session-token" :
                                                   "native-client-session-token")) _exit(6);
            // Change credentials while discovery is in flight. The next
            // request in THIS call must still use the original principal.
            if (rotateInflight && index == 0) writeToken("rotated-session-token");
            if (!TypedToolProtocol::WriteFrame(
                    connection, responses[discoveryRequests[index] ? 0 : 1], 2000, childReason))
                _exit(5);
            ::close(connection);
        }
        ::close(listener);
        _exit(0);
    }

    NativeToolClientConfig config;
    config.socketPath = socketPath;
    if (reuseBound || rotateInflight) config.tokenFile = tokenPath;
    else config.sessionToken = "native-client-session-token";
    config.timeoutMs = 2000;
    config.maxResponseBytes = 65536;
    NativeToolClient client(config);
    TradingToolHostRequest request;
    request.toolCallId = "native-quote-001";
    request.call.name = "market.get_quote";
    request.call.instrument = "EUR.USD";
    NativeToolClientResult result;
    std::string reason;
    assert(client.Call(request, result, reason));
    assert(result.envelope.status == "ok");
    if (reuseBound)
    {
        std::string binding;
        assert(client.RecoveryBinding(binding, reason));
        request.toolCallId = "bound-quote-001";
        assert(client.CallBound(request, binding, result, reason));
        request.toolCallId = "bound-quote-002";
        assert(client.CallBound(request, binding, result, reason));
        const TradingToolHostRequest validRequest = request;
        request.expectedSchemaHash = "sha256:" + std::string(64, 'a');
        assert(!client.CallBound(request, binding, result, reason));
        assert(reason == "DISCOVERY_REQUEST_SCHEMA_HASH_MISMATCH");
        assert(result.responseJson.empty());
        request.expectedSchemaHash.clear();
        request.call.name = "trade.place_order";
        assert(!client.CallBound(request, binding, result, reason));
        assert(reason == "DISCOVERY_TOOL_NOT_ADVERTISED");
        assert(result.responseJson.empty());
        request = validRequest;
        writeToken("rotated-session-token");
        assert(!client.CallBound(request, binding, result, reason));
        assert(reason == "NATIVE_RECOVERY_BINDING_MISMATCH");
        assert(result.responseJson.empty());
        request.toolCallId = "rotated-quote-001";
        assert(client.Call(request, result, reason));
        assert(client.RecoveryBinding(binding, reason));
        request.toolCallId = "rotated-bound-quote-001";
        assert(client.CallBound(request, binding, result, reason));
    }
    else if (rotateInflight)
    {
        request.toolCallId = "after-inflight-rotation";
        assert(client.Call(request, result, reason));
        assert(result.envelope.status == "ok");
    }

    ::close(listener);
    int status = 0;
    assert(::waitpid(child, &status, 0) == child);
    assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    std::remove(tokenPath.c_str());
    std::remove(socketPath.c_str());
    assert(::rmdir(directory) == 0);
}

void TestAutomaticSchemaDiscoveryAndInjection()
{
    TestDiscoveryCredentialScenario(DiscoveryScenario::Automatic);
}
void TestBoundDiscoveryReuseAndCredentialRotation()
{
    TestDiscoveryCredentialScenario(DiscoveryScenario::BoundReuse);
}
void TestCredentialPinnedAcrossDiscoveryRotation()
{
    TestDiscoveryCredentialScenario(DiscoveryScenario::InflightRotation);
}

void TestTransportResponseBoundary()
{
    NativeToolClientResult result;
    std::string reason;
    const std::string descriptorHash =
        "sha256:90629e163c334feda9597d33154a238775ae8a40951263671c20d1e6d5619a93";
    const std::string catalogHash =
        "sha256:ff93002d63593298639297fa8fff4e002dab07928d9a4c43ef01986796986db7";
    const std::string descriptor =
        "{\"name\":\"system.tools.list\","
        "\"description\":\"List versioned tools visible to this session.\","
        "\"required_capability\":\"system.read\",\"effect\":\"read\","
        "\"timeout_ms\":1000,\"schema_hash\":\"" + descriptorHash +
        "\",\"input_schema\":{\"type\":\"object\","
        "\"additionalProperties\":false},\"result_schema\":"
        "{\"type\":\"object\",\"additionalProperties\":true}}";
    const std::string discovery =
        "{\"protocol\":\"hepta.agent-tools\",\"protocol_version\":1,"
        "\"protocol_min_version\":1,\"protocol_max_version\":1,"
        "\"schema_version\":2,\"catalog_schema_hash\":\"" + catalogHash +
        "\",\"tools\":[" + descriptor + "]}";
    const std::string canonical =
        "{\"status\":\"ok\",\"tool\":\"system.tools.list\",\"reason_code\":\"\","
        "\"detail\":\"\",\"order_id\":-1,\"payload\":" + discovery + "}";
    assert(CallAgainstResponse(canonical, 65536, result, reason));
    assert(result.envelope.toolName == "system.tools.list");
    assert(result.envelope.payloadJson == discovery);

    std::string wrongSchema = canonical;
    const std::size_t schemaVersionOffset =
        wrongSchema.find("\"schema_version\":2");
    assert(schemaVersionOffset != std::string::npos);
    wrongSchema.replace(
                        schemaVersionOffset, std::strlen("\"schema_version\":2"),
                        "\"schema_version\":1");
    assert(!CallAgainstResponse(wrongSchema, 65536, result, reason));
    assert(reason == "DISCOVERY_SCHEMA_VERSION_UNSUPPORTED");

    std::string wrongHash = canonical;
    const std::size_t hashOffset = wrongHash.rfind(descriptorHash);
    assert(hashOffset != std::string::npos);
    wrongHash.replace(
        hashOffset + descriptorHash.size() - 1, 1, "0");
    assert(!CallAgainstResponse(wrongHash, 65536, result, reason));
    assert(reason == "DISCOVERY_DESCRIPTOR_SCHEMA_HASH_MISMATCH");

    std::string wrongCatalog = canonical;
    const std::size_t catalogOffset = wrongCatalog.find(catalogHash);
    assert(catalogOffset != std::string::npos);
    wrongCatalog.replace(catalogOffset + catalogHash.size() - 1, 1, "0");
    assert(!CallAgainstResponse(wrongCatalog, 65536, result, reason));
    assert(reason == "DISCOVERY_CATALOG_SCHEMA_HASH_MISMATCH");

    std::string missingField = canonical;
    const std::string description =
        "\"description\":\"List versioned tools visible to this session.\",";
    const std::size_t descriptionOffset = missingField.find(description);
    assert(descriptionOffset != std::string::npos);
    missingField.erase(descriptionOffset, description.size());
    assert(!CallAgainstResponse(missingField, 65536, result, reason));
    assert(reason == "DISCOVERY_DESCRIPTOR_INVALID");

    std::string duplicateField = canonical;
    const std::string capability = "\"required_capability\":\"system.read\",";
    const std::size_t capabilityOffset = duplicateField.find(capability);
    assert(capabilityOffset != std::string::npos);
    duplicateField.insert(capabilityOffset, capability);
    assert(!CallAgainstResponse(duplicateField, 65536, result, reason));
    assert(reason == "INVALID_RESULT_ENVELOPE");

    std::string duplicateName = canonical;
    const std::string tools = "\"tools\":[" + descriptor + "]";
    const std::size_t toolsOffset = duplicateName.find(tools);
    assert(toolsOffset != std::string::npos);
    duplicateName.replace(
        toolsOffset, tools.size(),
        "\"tools\":[" + descriptor + "," + descriptor + "]");
    assert(!CallAgainstResponse(duplicateName, 65536, result, reason));
    assert(reason == "DISCOVERY_DUPLICATE_TOOL");

    std::string duplicateSchemaKey = canonical;
    const std::string inputSchemaPrefix =
        "\"input_schema\":{\"type\":\"object\",";
    const std::size_t schemaOffset =
        duplicateSchemaKey.find(inputSchemaPrefix);
    assert(schemaOffset != std::string::npos);
    duplicateSchemaKey.replace(
        schemaOffset, inputSchemaPrefix.size(),
        "\"input_schema\":{\"type\":\"object\",\"type\":\"object\",");
    assert(!CallAgainstResponse(
        duplicateSchemaKey, 65536, result, reason));
    assert(reason == "INVALID_RESULT_ENVELOPE");

    NativeToolDiscoveryContract::CatalogSnapshot emptyCatalog;
    NativeToolDiscoveryContract::CatalogSnapshot listedCatalog;
    assert(NativeToolDiscoveryContract::Validate(
        "system.tools.list", discovery, "", emptyCatalog,
        listedCatalog, reason));
    assert(listedCatalog.schemaHash == catalogHash);
    assert(listedCatalog.descriptorSchemaHashes.size() == 1);
    assert(listedCatalog.descriptorSchemaHashes.at(
        "system.tools.list") == descriptorHash);

    NativeToolDiscoveryContract::CatalogSnapshot observedCatalog;
    const std::string describePayload =
        "{\"protocol\":\"hepta.agent-tools\",\"protocol_version\":1,"
        "\"protocol_min_version\":1,\"protocol_max_version\":1,"
        "\"schema_version\":2,\"catalog_schema_hash\":\"" + catalogHash +
        "\",\"tool\":" + descriptor + "}";
    assert(!NativeToolDiscoveryContract::Validate(
        "system.tools.describe", describePayload, "system.tools.list",
        emptyCatalog, observedCatalog, reason));
    assert(reason == "DISCOVERY_CATALOG_CONTEXT_REQUIRED");
    assert(NativeToolDiscoveryContract::Validate(
        "system.tools.describe", describePayload, "system.tools.list",
        listedCatalog, observedCatalog, reason));

    const std::string otherDescriptorHash =
        "sha256:ecd7ff5b961478645e1948eedc272283e57403a00442747de04d6b6fcee3cdec";
    const std::string otherDescriptor =
        "{\"name\":\"system.tools.describe\","
        "\"description\":\"List versioned tools visible to this session.\","
        "\"required_capability\":\"system.read\",\"effect\":\"read\","
        "\"timeout_ms\":1000,\"schema_hash\":\"" + otherDescriptorHash +
        "\",\"input_schema\":{\"type\":\"object\","
        "\"additionalProperties\":false},\"result_schema\":"
        "{\"type\":\"object\",\"additionalProperties\":true}}";
    const std::string wrongTargetPayload =
        "{\"protocol\":\"hepta.agent-tools\",\"protocol_version\":1,"
        "\"protocol_min_version\":1,\"protocol_max_version\":1,"
        "\"schema_version\":2,\"catalog_schema_hash\":\"" + catalogHash +
        "\",\"tool\":" + otherDescriptor + "}";
    assert(!NativeToolDiscoveryContract::Validate(
        "system.tools.describe", wrongTargetPayload, "system.tools.list",
        listedCatalog, observedCatalog, reason));
    assert(reason == "DISCOVERY_TARGET_MISMATCH");

    const std::string substitutedHash =
        "sha256:206b000e605fa7a68070efd7b5ca8514a645da86c13f66a51f42f20c9b7384fe";
    const std::string substitutedDescriptor =
        "{\"name\":\"system.tools.list\","
        "\"description\":\"Substituted descriptor.\","
        "\"required_capability\":\"system.read\",\"effect\":\"read\","
        "\"timeout_ms\":1000,\"schema_hash\":\"" + substitutedHash +
        "\",\"input_schema\":{\"type\":\"object\","
        "\"additionalProperties\":false},\"result_schema\":"
        "{\"type\":\"object\",\"additionalProperties\":true}}";
    const std::string substitutedPayload =
        "{\"protocol\":\"hepta.agent-tools\",\"protocol_version\":1,"
        "\"protocol_min_version\":1,\"protocol_max_version\":1,"
        "\"schema_version\":2,\"catalog_schema_hash\":\"" + catalogHash +
        "\",\"tool\":" + substitutedDescriptor + "}";
    assert(!NativeToolDiscoveryContract::Validate(
        "system.tools.describe", substitutedPayload, "system.tools.list",
        listedCatalog, observedCatalog, reason));
    assert(reason == "DISCOVERY_DESCRIPTOR_CHANGED");

    const std::string validDescribeEnvelope =
        "{\"status\":\"ok\",\"tool\":\"system.tools.describe\","
        "\"reason_code\":\"\",\"detail\":\"\",\"order_id\":-1,"
        "\"payload\":" + describePayload + "}";
    assert(DescribeAgainstListedCatalog(
        canonical, validDescribeEnvelope, "system.tools.list", result, reason));
    const std::string wrongTargetEnvelope =
        "{\"status\":\"ok\",\"tool\":\"system.tools.describe\","
        "\"reason_code\":\"\",\"detail\":\"\",\"order_id\":-1,"
        "\"payload\":" + wrongTargetPayload + "}";
    assert(!DescribeAgainstListedCatalog(
        canonical, wrongTargetEnvelope, "system.tools.list", result, reason));
    assert(reason == "DISCOVERY_TARGET_MISMATCH");
    const std::string substitutedEnvelope =
        "{\"status\":\"ok\",\"tool\":\"system.tools.describe\","
        "\"reason_code\":\"\",\"detail\":\"\",\"order_id\":-1,"
        "\"payload\":" + substitutedPayload + "}";
    assert(!DescribeAgainstListedCatalog(
        canonical, substitutedEnvelope, "system.tools.list", result, reason));
    assert(reason == "DISCOVERY_DESCRIPTOR_CHANGED");

    const std::string mismatched =
        "{\"status\":\"ok\",\"tool\":\"market.get_quote\",\"reason_code\":\"\","
        "\"detail\":\"\",\"order_id\":-1,\"payload\":null}";
    assert(!CallAgainstResponse(mismatched, 65536, result, reason));
    assert(reason == "RESULT_TOOL_MISMATCH");

    const std::string malformed =
        "{\"status\":\"ok\",\"tool\":\"system.tools.list\",\"reason_code\":\"\","
        "\"detail\":\"\",\"order_id\":-1,\"payload\":null} trailing";
    assert(!CallAgainstResponse(malformed, 65536, result, reason));
    assert(reason == "INVALID_RESULT_ENVELOPE");

    const std::string oversized =
        "{\"status\":\"ok\",\"tool\":\"system.tools.list\",\"reason_code\":\"\","
        "\"detail\":\"" + std::string(512, 'x') +
        "\",\"order_id\":-1,\"payload\":null}";
    assert(!CallAgainstResponse(oversized, 128, result, reason));
    assert(reason == "FRAME_LENGTH_REJECTED");
}
}

int main()
{
#ifdef HEPTA_TEST_WRAP_SOCKET
    TestClientConnectBacklogDeadline(true);
    TestClientConnectBacklogDeadline(false);
#endif
    TestSupervisorClientFrameBudgetsAndReasons();
    // Every fragment must share one frame budget. An occasional byte must
    // not keep a Gateway worker occupied by resetting its I/O timeout.
    {
        int sockets[2];
        assert(::socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, sockets) == 0);
        const std::uint32_t length = htonl(5);
        assert(::send(sockets[1], &length, sizeof(length), MSG_NOSIGNAL) == sizeof(length));
        const pid_t child = ::fork();
        assert(child >= 0);
        if (child == 0)
        {
            ::close(sockets[0]);
            for (int i = 0; i < 5; ++i)
            {
                ::usleep(40000);
                if (::send(sockets[1], "x", 1, MSG_NOSIGNAL) != 1) break;
            }
            ::close(sockets[1]);
            _exit(0);
        }
        ::close(sockets[1]);
        std::string body, reason;
        const bool accepted = TypedToolProtocol::ReadFrame(sockets[0], 64, 100, body, reason);
        ::close(sockets[0]);
        int status = 0;
        assert(::waitpid(child, &status, 0) == child);
        assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
        assert(!accepted);
        assert(reason == "FRAME_BODY_TIMEOUT");
    }

    // A writable blocking socket can still block inside send() when the
    // complete frame exceeds its buffer. Backpressure shares the same budget.
    {
        int sockets[2];
        assert(::socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, sockets) == 0);
        const int sendBuffer = 4096;
        assert(::setsockopt(sockets[0], SOL_SOCKET, SO_SNDBUF, &sendBuffer, sizeof(sendBuffer)) == 0);
        const pid_t child = ::fork();
        assert(child >= 0);
        if (child == 0)
        {
            ::close(sockets[0]);
            ::usleep(200000);
            char buffer[4096];
            while (::recv(sockets[1], buffer, sizeof(buffer), 0) > 0) {}
            ::close(sockets[1]);
            _exit(0);
        }
        ::close(sockets[1]);
        std::string reason;
        const bool delivered = TypedToolProtocol::WriteFrame(sockets[0], std::string(524288, 'x'), 100, reason);
        ::close(sockets[0]);
        int status = 0;
        assert(::waitpid(child, &status, 0) == child);
        assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
        assert(!delivered);
        assert(reason == "FRAME_WRITE_TIMEOUT");
    }

    char path[] = "/tmp/hepta-native-client-token-XXXXXX";
    const int fd = ::mkstemp(path);
    assert(fd >= 0);
    const std::string token = "native-client-session-token\n";
    assert(::write(fd, token.data(), token.size()) == static_cast<ssize_t>(token.size()));
    assert(::fchmod(fd, 0600) == 0);
    assert(::close(fd) == 0);

    std::string loaded;
    std::string reason;
    assert(NativeToolClient::ReadSessionToken(path, loaded, reason));
    assert(loaded == "native-client-session-token");

    assert(::chmod(path, 0640) == 0);
    assert(!NativeToolClient::ReadSessionToken(path, loaded, reason));
    assert(reason == "TOKEN_FILE_UNSAFE");
    const mode_t unsafeModes[] = {0400, 0700, 04600};
    for (const mode_t unsafeMode : unsafeModes)
    {
        assert(::chmod(path, unsafeMode) == 0);
        assert(!NativeToolClient::ReadSessionToken(path, loaded, reason));
        assert(reason == "TOKEN_FILE_UNSAFE");
    }
    assert(::chmod(path, 0600) == 0);

    const std::string hardLink = std::string(path) + ".hard";
    assert(::link(path, hardLink.c_str()) == 0);
    assert(!NativeToolClient::ReadSessionToken(path, loaded, reason));
    assert(reason == "TOKEN_FILE_UNSAFE");
    assert(std::remove(hardLink.c_str()) == 0);
    assert(NativeToolClient::ReadSessionToken(path, loaded, reason));

    const std::string link = std::string(path) + ".link";
    assert(::symlink(path, link.c_str()) == 0);
    assert(!NativeToolClient::ReadSessionToken(link, loaded, reason));
    assert(reason == "TOKEN_FILE_UNSAFE");

    std::remove(link.c_str());
    std::remove(path);
    TestAutomaticSchemaDiscoveryAndInjection();
    TestBoundDiscoveryReuseAndCredentialRotation();
    TestCredentialPinnedAcrossDiscoveryRotation();
    TestTransportResponseBoundary();
    return 0;
}
