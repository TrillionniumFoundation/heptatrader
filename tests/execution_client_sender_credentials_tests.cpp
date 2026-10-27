#include "../HeptaTrade/execution/unix_execution_service_client.h"
#include "../HeptaTrade/execution/execution_service_protocol.h"

#include <algorithm>
#include <arpa/inet.h>
#include <cassert>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <poll.h>
#include <set>
#include <sstream>
#include <string>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>

namespace {
using Mode = UnixExecutionServiceClient::ProcessBindingMode;
using Clock = std::chrono::steady_clock;

std::uint64_t StartTicks(pid_t pid)
{
    std::ifstream input("/proc/" + std::to_string(pid) + "/stat");
    std::string line, field;
    assert(std::getline(input, line));
    assert(line.rfind(')') != std::string::npos);
    std::istringstream fields(line.substr(line.rfind(')') + 1));
    for (int i = 0; i != 20; ++i) assert(bool(fields >> field));
    return std::stoull(field);
}

bool ReadExact(int fd, void* data, std::size_t size, std::uint64_t* count = nullptr)
{
    auto* bytes = static_cast<char*>(data);
    while (size)
    {
        const ssize_t n = ::read(fd, bytes, size);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return false;
        if (count) *count += static_cast<std::uint64_t>(n);
        bytes += n;
        size -= static_cast<std::size_t>(n);
    }
    return true;
}

bool WriteExact(int fd, const void* data, std::size_t size, bool socket = false)
{
    const auto* bytes = static_cast<const char*>(data);
    while (size)
    {
        const ssize_t n = socket ? ::send(fd, bytes, size, MSG_NOSIGNAL) :
            ::write(fd, bytes, size);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return false;
        bytes += n;
        size -= static_cast<std::size_t>(n);
    }
    return true;
}

int Listener(const std::string& path)
{
    const int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(fd >= 0);
    sockaddr_un address{};
    address.sun_family = AF_UNIX;
    assert(path.size() < sizeof(address.sun_path));
    std::memcpy(address.sun_path, path.c_str(), path.size() + 1);
    assert(::bind(fd, reinterpret_cast<const sockaddr*>(&address), sizeof(address)) == 0);
    assert(::listen(fd, 16) == 0);
    return fd;
}

ExecutionServiceIdentity Identity()
{
    ExecutionServiceIdentity identity;
    identity.serviceEpoch = "sender-credential-fixture-epoch";
    identity.serviceFencingGeneration = 19;
    return identity;
}

AgentExecutionContext Owner(const std::string& id)
{
    AgentExecutionContext context;
    context.agentId = "fixture-agent";
    context.sessionId = "fixture-session";
    context.toolCallId = id;
    context.account = "DU12345";
    context.executionDomain = "PAPER";
    return context;
}

std::string Response(const ExecutionServiceRequest& request)
{
    const auto identity = Identity();
    std::string body, reason;
    if (request.operation == ExecutionServiceOperation::AuditSessionOwner)
    {
        ExecutionControlResult result;
        result.commandId = request.control.context.toolCallId;
        result.status = ExecutionCommandStatus::Accepted;
        result.serviceEpoch = identity.serviceEpoch;
        result.serviceFencingGeneration = identity.serviceFencingGeneration;
        result.ownerAuditAuthoritative = result.ownerAuditComplete = true;
        result.ownerAccount = request.control.context.account;
        result.ownerExecutionDomain = request.control.context.executionDomain;
        assert(ExecutionServiceProtocol::EncodeControlResponse(result, body, reason));
    }
    else
    {
        ExecutionCommandResult result;
        assert(request.operation == ExecutionServiceOperation::GetServiceIdentity ||
            request.operation == ExecutionServiceOperation::ReadAuthoritativeState);
        result.commandId = request.operation == ExecutionServiceOperation::GetServiceIdentity ?
            "__service_identity__" : request.read.context.toolCallId;
        result.status = ExecutionCommandStatus::Accepted;
        result.serviceEpoch = identity.serviceEpoch;
        result.serviceFencingGeneration = identity.serviceFencingGeneration;
        result.detail = "{\"authoritative\":true}";
        assert(ExecutionServiceProtocol::EncodeResponse(result, body, reason));
    }
    const std::uint32_t length = htonl(static_cast<std::uint32_t>(body.size()));
    return std::string(reinterpret_cast<const char*>(&length), sizeof(length)) + body;
}

enum class Reply { Normal, HeaderSenderChanges, BodySenderChanges, MidBodySenderChanges,
                   Oversized, SlowFrame };
struct Report
{
    std::uint64_t requestBytes = 0;
    std::uint64_t requests = 0;
    std::uint64_t zeroByteConnections = 0;
    std::uint64_t operationMask = 0;
    std::uint64_t senderChanges = 0;
};

void WaitForClientClose(int fd)
{
    char byte;
    ssize_t n;
    do { n = ::read(fd, &byte, 1); } while (n < 0 && errno == EINTR);
    assert(n == 0 || (n < 0 && errno == ECONNRESET));
}

void SendResponse(int fd, const std::string& frame, Reply reply, Report& report)
{
    if (reply == Reply::Oversized)
    {
        const std::uint32_t length = htonl(1024 * 1024);
        WriteExact(fd, &length, sizeof(length), true);
    }
    else if (reply == Reply::SlowFrame)
    {
        // Each individual fragment arrives within the I/O timeout; the caller's
        // earlier absolute deadline must still bound the entire response.
        WriteExact(fd, frame.data(), 2, true);
        std::this_thread::sleep_for(std::chrono::milliseconds(65));
        WriteExact(fd, frame.data() + 2, 2, true);
        std::this_thread::sleep_for(std::chrono::milliseconds(65));
        WriteExact(fd, frame.data() + 4, frame.size() - 4, true);
    }
    else if (reply == Reply::Normal)
    {
        // Ordinary writes, not an explicit SCM_CREDENTIALS sendmsg: the kernel
        // must attach credentials even when the frame header is fragmented.
        WriteExact(fd, frame.data(), 1, true);
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
        WriteExact(fd, frame.data() + 1, 1, true);
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
        WriteExact(fd, frame.data() + 2, 2, true);
        for (std::size_t offset = 4; offset < frame.size(); offset += 17)
            if (!WriteExact(fd, frame.data() + offset,
                    std::min<std::size_t>(17, frame.size() - offset), true)) break;
    }
    else
    {
        const std::size_t split = reply == Reply::HeaderSenderChanges ? 2 :
            reply == Reply::BodySenderChanges ? 4 : 12;
        int release[2];
        assert(::pipe(release) == 0);
        // Keep the inherited acceptor and the second writer alive until the
        // client closes: /proc start ticks are available for every fragment.
        const pid_t second = ::fork();
        assert(second >= 0);
        if (second == 0)
        {
            ::alarm(10);
            ::close(release[1]);
            char signal;
            assert(ReadExact(release[0], &signal, 1) && signal == 'S');
            WriteExact(fd, frame.data() + split, frame.size() - split, true);
            assert(ReadExact(release[0], &signal, 1) && signal == 'X');
            ::close(release[0]);
            ::close(fd);
            ::_exit(0);
        }
        ::close(release[0]);
        WriteExact(fd, frame.data(), split, true);
        assert(WriteExact(release[1], "S", 1));
        ++report.senderChanges;
        WaitForClientClose(fd);
        assert(WriteExact(release[1], "X", 1));
        ::close(release[1]);
        int status = 0;
        assert(::waitpid(second, &status, 0) == second);
        assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
        return;
    }
    WaitForClientClose(fd);
}

// A real accept/send process, with either a manager-created inherited listener
// or a direct server-created listener. No native service or Broker is started.
class Server
{
public:
    explicit Server(Reply reply = Reply::Normal, bool inherited = true)
    {
        char directory[] = "/tmp/hepta-sender-native-XXXXXX";
        assert(::mkdtemp(directory));
        dir = directory;
        path = dir + "/execution.sock";
        const int listener = inherited ? Listener(path) : -1;
        int control[2], result[2];
        assert(::pipe(control) == 0 && ::pipe(result) == 0);
        pid = ::fork();
        assert(pid >= 0);
        if (pid == 0)
        {
            ::alarm(20);
            ::close(control[1]);
            ::close(result[0]);
            const int active = inherited ? listener : Listener(path);
            assert(WriteExact(result[1], "R", 1));
            Report report;
            for (;;)
            {
                pollfd wait[2] = {{active, POLLIN, 0}, {control[0], POLLIN, 0}};
                int ready;
                do { ready = ::poll(wait, 2, 4000); } while (ready < 0 && errno == EINTR);
                assert(ready > 0);
                // Drain an already queued connection before accepting finalization.
                if (wait[0].revents & POLLIN)
                {
                    const int fd = ::accept(active, nullptr, nullptr);
                    assert(fd >= 0);
                    const timeval timeout = {3, 0};
                    assert(::setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) == 0);
                    assert(::setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout)) == 0);
                    std::uint32_t length = 0;
                    const std::uint64_t before = report.requestBytes;
                    if (!ReadExact(fd, &length, sizeof(length), &report.requestBytes))
                    {
                        assert(report.requestBytes == before);
                        ++report.zeroByteConnections;
                        ::close(fd);
                        continue;
                    }
                    const std::size_t size = ntohl(length);
                    assert(size && size <= 32768);
                    std::string body(size, '\0');
                    assert(ReadExact(fd, &body[0], size, &report.requestBytes));
                    ExecutionServiceRequest request;
                    std::string reason;
                    assert(ExecutionServiceProtocol::DecodeRequest(body, request, reason));
                    ++report.requests;
                    report.operationMask |= std::uint64_t(1) << static_cast<int>(request.operation);
                    SendResponse(fd, Response(request), reply, report);
                    ::close(fd);
                }
                else if (wait[1].revents & (POLLIN | POLLHUP)) break;
            }
            assert(WriteExact(result[1], &report, sizeof(report)));
            ::close(active);
            ::close(control[0]);
            ::close(result[1]);
            ::_exit(0);
        }
        if (listener >= 0) ::close(listener);
        ::close(control[0]);
        ::close(result[1]);
        stop = control[1];
        readback = result[0];
        char ready;
        assert(ReadExact(readback, &ready, 1) && ready == 'R');
        ticks = StartTicks(pid);
    }
    ~Server()
    {
        assert(finished);
        assert(::unlink(path.c_str()) == 0);
        assert(::rmdir(dir.c_str()) == 0);
    }
    void CheckCreator(pid_t expected) const
    {
        const int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
        assert(fd >= 0);
        sockaddr_un address{};
        address.sun_family = AF_UNIX;
        std::memcpy(address.sun_path, path.c_str(), path.size() + 1);
        assert(::connect(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
        ucred credential{};
        socklen_t size = sizeof(credential);
        assert(::getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &credential, &size) == 0);
        assert(size == sizeof(credential) && credential.pid == expected);
        assert(credential.uid == ::geteuid());
        ::close(fd);
    }
    Report Finish()
    {
        assert(!finished && WriteExact(stop, "X", 1));
        ::close(stop);
        Report report;
        assert(ReadExact(readback, &report, sizeof(report)));
        ::close(readback);
        int status = 0;
        assert(::waitpid(pid, &status, 0) == pid);
        assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
        finished = true;
        return report;
    }
    std::string path;
    pid_t pid;
    std::uint64_t ticks;
private:
    std::string dir;
    int stop, readback;
    bool finished = false;
};

bool Observe(UnixExecutionServiceClient& client, int operation, std::string& reason)
{
    if (operation == 7)
    {
        ExecutionServiceIdentity identity;
        const bool ok = client.GetServiceIdentity(identity, reason);
        if (ok) assert(identity.serviceEpoch == Identity().serviceEpoch &&
            identity.serviceFencingGeneration == Identity().serviceFencingGeneration);
        return ok;
    }
    if (operation == 8)
    {
        ExecutionReadCommand command;
        command.context = Owner("read-authoritative");
        command.query = "system.get_health";
        const auto result = client.ReadAuthoritativeStateWithIdentity(command, Identity());
        reason = result.detail;
        if (result.status == ExecutionCommandStatus::Accepted)
            assert(result.detail == "{\"authoritative\":true}");
        return result.status == ExecutionCommandStatus::Accepted;
    }
    assert(operation == 15);
    ExecutionControlCommand command;
    command.context = Owner("audit-owner");
    const auto result = client.AuditSessionOwnerWithIdentity(command, Identity());
    reason = result.detail;
    if (result.status == ExecutionCommandStatus::Accepted)
        assert(result.ownerAuditAuthoritative && result.ownerAuditComplete);
    return result.status == ExecutionCommandStatus::Accepted;
}

void TestInheritedListenerObservations()
{
    Server server;
    server.CheckCreator(::getpid());
    UnixExecutionServiceClient client(server.path, 1000, 32768, {}, 1000,
        server.pid, server.ticks, Mode::InactiveOwnerAuditResponse);
    std::string reason;
    for (int operation : {7, 8, 15}) assert(Observe(client, operation, reason));
    const auto report = server.Finish();
    assert(report.requests == 3 && report.requestBytes > 0);
    assert(report.operationMask == ((1u << 7) | (1u << 8) | (1u << 15)));
    assert(report.zeroByteConnections == 1);
}

void TestDefaultPinStillRejectsBeforeRequest()
{
    Server server;
    // No opt-in: even the three observation operations retain the old contract.
    UnixExecutionServiceClient client(server.path, 1000, 32768, {}, 1000,
        server.pid, server.ticks);
    std::string reason;
    for (int operation : {7, 8, 15})
    {
        assert(!Observe(client, operation, reason));
        assert(reason == "EXECUTION_SERVICE_PEER_PROCESS_MISMATCH");
    }
    const auto report = server.Finish();
    assert(report.requests == 0 && report.requestBytes == 0 && report.zeroByteConnections == 3);
}

template<typename Result> void Unsent(const Result& result)
{
    assert(result.status == ExecutionCommandStatus::Uncertain);
    assert(result.detail == "EXECUTION_SERVICE_PEER_PROCESS_MISMATCH");
}

void TestOptInDoesNotAuthorizeOtherOperations()
{
    Server server;
    UnixExecutionServiceClient client(server.path, 1000, 32768, {}, 1000,
        server.pid, server.ticks, Mode::InactiveOwnerAuditResponse);
    IbPlaceOrderCommand place;
    place.context = Owner("place");
    Unsent(client.PlaceIbOrderWithIdentity(place, Identity()));
    Unsent(client.PreviewOrderWithIdentity(place, Identity()));
    IbCancelOrderCommand cancel;
    cancel.context = Owner("cancel");
    cancel.orderId = 17;
    Unsent(client.CancelIbOrderWithIdentity(cancel, Identity()));
    FlattenPositionCommand flatten;
    flatten.context = Owner("flatten");
    Unsent(client.FlattenPositionWithIdentity(flatten, Identity()));
    Unsent(client.PreviewFlattenPositionWithIdentity(flatten, Identity()));
    ExecutionControlCommand command;
    command.context = Owner("control");
    command.targetCommandId = "known-command";
    Unsent(client.FenceSessionOwnerWithIdentity(command, Identity()));
    Unsent(client.ReleaseSessionOwnerFenceWithIdentity(command, Identity()));
    Unsent(client.ReconcileAuthoritativeStateWithIdentity(command, Identity()));
    Unsent(client.QueryCommandStatusWithIdentity(command, Identity()));
    Unsent(client.AuditSessionLeaseWithIdentity(command, Identity()));
    command.recoveryIngressFence = 13;
    Unsent(client.QueryCommandStatusWithIdentity(command, Identity()));
    Unsent(client.RecoveryAuditOwnerWithIdentity(command, Identity()));
    command.terminalPreliminaryReceiptSha256 = "sha256:" + std::string(64, 'a');
    Unsent(client.TerminalizeRecoveryOwnerWithIdentity(command, Identity()));
    const auto report = server.Finish();
    assert(report.requests == 0 && report.requestBytes == 0 && report.zeroByteConnections == 13);
}

void TestCachedIdentityDoesNotBypassMutationPeerCheck()
{
    Server server;
    UnixExecutionServiceClient client(server.path, 1000, 32768, {}, 1000,
        server.pid, server.ticks, Mode::InactiveOwnerAuditResponse);
    std::string reason;
    assert(Observe(client, 7, reason)); // Populate the actual service identity cache.
    ExecutionControlCommand fence;
    fence.context = Owner("account-fence-with-cached-identity");
    Unsent(client.FenceAccountNewEntries(fence));
    IbPlaceOrderCommand place;
    place.context = Owner("public-place-with-cached-identity");
    Unsent(client.PlaceOrder(place));
    const auto report = server.Finish();
    assert(report.requests == 1 && report.operationMask == (1u << 7));
    assert(report.zeroByteConnections == 2);
}

void TestWrongIdentityAndIncompleteBinding()
{
    for (int variant = 0; variant != 5; ++variant)
    {
        Server server;
        const std::uint32_t pid = variant == 0 ? ::getpid() :
            variant == 2 || variant == 4 ? 0 : server.pid;
        const std::uint64_t ticks = variant == 0 ? StartTicks(::getpid()) :
            variant == 1 ? server.ticks + 1 : variant == 3 || variant == 4 ? 0 : server.ticks;
        UnixExecutionServiceClient client(server.path, 1000, 32768, {}, 1000,
            pid, ticks, Mode::InactiveOwnerAuditResponse);
        std::string reason;
        assert(!Observe(client, 7, reason));
        assert(reason == "EXECUTION_SERVICE_PEER_PROCESS_MISMATCH");
        const auto report = server.Finish();
        // An existing wrong PID/start pair is rejected on the actual sender;
        // incomplete bindings and stale start ticks are rejected before send.
        assert(report.requests == (variant == 0 ? 1u : 0u));
        if (variant != 0) assert(report.requestBytes == 0);
    }
}

void TestEveryResponseSegmentIsBound()
{
    for (Reply reply : {Reply::HeaderSenderChanges, Reply::BodySenderChanges,
            Reply::MidBodySenderChanges})
        for (int operation : {7, 8, 15})
        {
            Server server(reply);
            UnixExecutionServiceClient client(server.path, 1000, 32768, {}, 1000,
                server.pid, server.ticks, Mode::InactiveOwnerAuditResponse);
            std::string reason;
            assert(!Observe(client, operation, reason));
            assert(reason == "EXECUTION_SERVICE_PEER_PROCESS_MISMATCH");
            const auto report = server.Finish();
            assert(report.requests == 1 && report.senderChanges == 1);
        }
}

void TestFrameBoundsAndAbsoluteDeadline()
{
    {
        Server server(Reply::Oversized);
        UnixExecutionServiceClient client(server.path, 1000, 32768, {}, 1000,
            server.pid, server.ticks, Mode::InactiveOwnerAuditResponse);
        std::string reason;
        assert(!Observe(client, 7, reason));
        assert(!reason.empty());
        assert(server.Finish().requests == 1);
    }
    {
        Server server(Reply::SlowFrame);
        UnixExecutionServiceClient client(server.path, 1000, 32768, {}, 1000,
            server.pid, server.ticks, Mode::InactiveOwnerAuditResponse);
        ExecutionServiceIdentity identity;
        std::string reason;
        const auto started = Clock::now();
        assert(!client.GetServiceIdentity(identity, reason,
            started + std::chrono::milliseconds(100)));
        const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
            Clock::now() - started).count();
        assert(elapsed >= 70 && elapsed < 500);
        assert(server.Finish().requests == 1);
    }
}

void TestOrdinaryClientsRemainCompatible()
{
    {
        Server server;
        UnixExecutionServiceClient client(server.path); // Original UID-only contract.
        std::string reason;
        for (int operation : {7, 8, 15}) assert(Observe(client, operation, reason));
        assert(server.Finish().requests == 3);
    }
    {
        Server server(Reply::Normal, false); // Actual server owns its own listener.
        server.CheckCreator(server.pid);
        UnixExecutionServiceClient client(server.path, 1000, 32768, {}, 1000,
            server.pid, server.ticks); // Original process binding, no opt-in.
        std::string reason;
        for (int operation : {7, 8, 15}) assert(Observe(client, operation, reason));
        assert(server.Finish().requests == 3);
    }
    {
        Server server;
        UnixExecutionServiceClient client(server.path, 1000, 32768,
            {static_cast<std::uint32_t>(::geteuid() + 1)}, 1000,
            server.pid, server.ticks, Mode::InactiveOwnerAuditResponse);
        std::string reason;
        assert(!Observe(client, 7, reason));
        assert(reason == "execution service peer uid rejected");
        assert(server.Finish().requestBytes == 0);
    }
}
} // namespace

void RunExecutionClientSenderCredentialTests()
{
    const unsigned int previousAlarm = ::alarm(20);
    TestInheritedListenerObservations();
    TestDefaultPinStillRejectsBeforeRequest();
    TestOptInDoesNotAuthorizeOtherOperations();
    TestCachedIdentityDoesNotBypassMutationPeerCheck();
    TestWrongIdentityAndIncompleteBinding();
    TestEveryResponseSegmentIsBound();
    TestFrameBoundsAndAbsoluteDeadline();
    TestOrdinaryClientsRemainCompatible();
    ::alarm(previousAlarm);
    std::cout << "execution client sender credentials: 8 groups passed; uid=" << ::geteuid() << '\n';
}
