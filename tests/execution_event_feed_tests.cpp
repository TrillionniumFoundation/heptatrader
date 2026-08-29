#include "../HeptaTrade/execution/execution_event_feed.h"
#include "../HeptaTrade/tool_host/execution_event_relay.h"

#include <arpa/inet.h>
#include <atomic>
#include <cassert>
#include <chrono>
#include <condition_variable>
#include <cstring>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <mutex>
#include <poll.h>
#include <set>
#include <string>
#include <sys/socket.h>
#include <sys/un.h>
#include <thread>
#include <unistd.h>

namespace
{
ExecutionServiceIdentity Identity(const std::string& epoch,
                                  std::uint64_t generation)
{
    ExecutionServiceIdentity identity;
    identity.serviceEpoch = epoch;
    identity.serviceFencingGeneration = generation;
    return identity;
}

bool SameIdentity(const ExecutionServiceIdentity& left,
                  const ExecutionServiceIdentity& right)
{
    return left.serviceEpoch == right.serviceEpoch &&
        left.serviceFencingGeneration == right.serviceFencingGeneration;
}

std::shared_ptr<ExecutionServiceLifecycleGate> ReadyGate()
{
    std::shared_ptr<ExecutionServiceLifecycleGate> gate(
        new ExecutionServiceLifecycleGate());
    gate->ready.store(true);
    return gate;
}

std::uint32_t WrongEffectiveUid()
{
    const std::uint32_t current = static_cast<std::uint32_t>(::geteuid());
    return current == std::numeric_limits<std::uint32_t>::max() ?
        current - 1 : current + 1;
}

int ActivatedSocket(const std::string& path)
{
    ::unlink(path.c_str());
    const int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(fd >= 0);
    struct sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    assert(path.size() < sizeof(address.sun_path));
    std::memcpy(address.sun_path, path.c_str(), path.size() + 1);
    assert(::bind(fd, reinterpret_cast<struct sockaddr*>(&address), sizeof(address)) == 0);
    assert(::listen(fd, 16) == 0);
    return fd;
}

int ConnectSocket(const std::string& path)
{
    const int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(fd >= 0);
    struct sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    assert(path.size() < sizeof(address.sun_path));
    std::memcpy(address.sun_path, path.c_str(), path.size() + 1);
    assert(::connect(fd, reinterpret_cast<struct sockaddr*>(&address), sizeof(address)) == 0);
    return fd;
}

void WriteAll(int fd, const char* data, std::size_t size)
{
    std::size_t offset = 0;
    while (offset < size)
    {
        const ssize_t count = ::send(fd, data + offset, size - offset, MSG_NOSIGNAL);
        if (count < 0 && errno == EINTR) continue;
        assert(count > 0);
        offset += static_cast<std::size_t>(count);
    }
}

void WriteFrame(int fd, const std::string& body)
{
    const std::uint32_t length = htonl(static_cast<std::uint32_t>(body.size()));
    WriteAll(fd, reinterpret_cast<const char*>(&length), sizeof(length));
    WriteAll(fd, body.data(), body.size());
}

bool ReadAllWithTimeout(int fd, char* data, std::size_t size, int timeoutMs)
{
    std::size_t offset = 0;
    while (offset < size)
    {
        struct pollfd descriptor;
        descriptor.fd = fd;
        descriptor.events = POLLIN;
        descriptor.revents = 0;
        int rc = -1;
        do { rc = ::poll(&descriptor, 1, timeoutMs); }
        while (rc < 0 && errno == EINTR);
        if (rc <= 0 || (descriptor.revents & (POLLIN | POLLHUP)) == 0) return false;
        const ssize_t count = ::recv(fd, data + offset, size - offset, 0);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return false;
        offset += static_cast<std::size_t>(count);
    }
    return true;
}

bool ReadFrameWithTimeout(int fd, std::string& body, int timeoutMs = 1500)
{
    std::uint32_t networkLength = 0;
    if (!ReadAllWithTimeout(fd, reinterpret_cast<char*>(&networkLength),
            sizeof(networkLength), timeoutMs))
        return false;
    const std::size_t length = ntohl(networkLength);
    if (length == 0 || length > 32768) return false;
    body.assign(length, '\0');
    return ReadAllWithTimeout(fd, &body[0], body.size(), timeoutMs);
}

unsigned int U16At(const std::string& value, std::size_t offset)
{
    assert(offset + 2 <= value.size());
    return (static_cast<unsigned char>(value[offset]) << 8) |
        static_cast<unsigned char>(value[offset + 1]);
}

std::size_t U32At(const std::string& value, std::size_t offset)
{
    assert(offset + 4 <= value.size());
    return (static_cast<std::size_t>(static_cast<unsigned char>(value[offset])) << 24) |
        (static_cast<std::size_t>(static_cast<unsigned char>(value[offset + 1])) << 16) |
        (static_cast<std::size_t>(static_cast<unsigned char>(value[offset + 2])) << 8) |
        static_cast<unsigned char>(value[offset + 3]);
}

void AppendU16(std::string& value, unsigned int number)
{
    value.push_back(static_cast<char>((number >> 8) & 0xff));
    value.push_back(static_cast<char>(number & 0xff));
}

void AppendU32(std::string& value, std::size_t number)
{
    value.push_back(static_cast<char>((number >> 24) & 0xff));
    value.push_back(static_cast<char>((number >> 16) & 0xff));
    value.push_back(static_cast<char>((number >> 8) & 0xff));
    value.push_back(static_cast<char>(number & 0xff));
}

std::string RemoveField(const std::string& body, unsigned int removedTag)
{
    assert(body.size() >= 8);
    std::string result = body.substr(0, 8);
    std::size_t offset = 8;
    bool removed = false;
    while (offset < body.size())
    {
        const std::size_t fieldStart = offset;
        const unsigned int tag = U16At(body, offset);
        offset += 2;
        const std::size_t length = U32At(body, offset);
        offset += 4;
        assert(offset + length <= body.size());
        offset += length;
        if (tag == removedTag) removed = true;
        else result.append(body, fieldStart, offset - fieldStart);
    }
    assert(removed);
    return result;
}

std::string WithUnknownField(std::string body)
{
    AppendU16(body, 65000);
    AppendU32(body, 1);
    body.push_back('x');
    return body;
}

ExecutionEventFeedRequest Request(const ExecutionServiceIdentity& identity,
                                  const std::string& sessionId,
                                  std::uint64_t afterSequence = 0,
                                  int timeoutMs = 0)
{
    ExecutionEventFeedRequest request;
    request.operation = ExecutionEventFeedOperation::Wait;
    request.executionDomain = "SIM:EURUSD";
    request.agentId = "feed-agent";
    request.sessionId = sessionId;
    request.expectedServiceIdentity = identity;
    request.afterSequence = afterSequence;
    request.timeoutMs = timeoutMs;
    return request;
}

ExecutionEvent Event(const std::string& sessionId, long orderId)
{
    ExecutionEvent event;
    event.executionDomain = "SIM:EURUSD";
    event.agentId = "feed-agent";
    event.sessionId = sessionId;
    event.type = "order.status";
    event.venue = "SIMULATOR";
    event.orderId = orderId;
    event.instrument = "EUR.USD";
    event.side = "BUY";
    event.status = "Submitted";
    event.remainingQuantity = 1000.0;
    return event;
}

class CountingSource : public ExecutionEventFeedSource
{
public:
    CountingSource(std::size_t capacity, const std::string& epoch)
        : m_hub(capacity, epoch), m_epoch(epoch)
    {
    }

    ExecutionEventReadResult ReadNext(const std::string& executionDomain,
                                      const std::string& agentId,
                                      const std::string& sessionId,
                                      const std::string& expectedEpoch,
                                      std::uint64_t afterSequence,
                                      int timeoutMs) override
    {
        {
            std::lock_guard<std::mutex> lock(m_mutex);
            ++m_reads[sessionId];
        }
        m_readsChanged.notify_all();
        return m_hub.ReadNext(executionDomain, agentId, sessionId, expectedEpoch,
            afterSequence, timeoutMs);
    }

    const std::string& StreamEpoch() const override { return m_epoch; }

    std::uint64_t Publish(const ExecutionEvent& event) { return m_hub.Publish(event); }

    std::size_t Pending(const std::string& sessionId,
                        std::uint64_t afterSequence = 0) const
    {
        return m_hub.Pending("SIM:EURUSD", "feed-agent", sessionId, afterSequence);
    }

    std::uint64_t ReadsFor(const std::string& sessionId) const
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        const std::map<std::string, std::uint64_t>::const_iterator found =
            m_reads.find(sessionId);
        return found == m_reads.end() ? 0 : found->second;
    }

    bool WaitForRead(const std::string& sessionId,
                     std::chrono::milliseconds timeout)
    {
        std::unique_lock<std::mutex> lock(m_mutex);
        return m_readsChanged.wait_for(lock, timeout, [&]() {
            const std::map<std::string, std::uint64_t>::const_iterator found =
                m_reads.find(sessionId);
            return found != m_reads.end() && found->second > 0;
        });
    }

private:
    ExecutionEventHub m_hub;
    const std::string m_epoch;
    mutable std::mutex m_mutex;
    std::condition_variable m_readsChanged;
    std::map<std::string, std::uint64_t> m_reads;
};

void TestProtocolV2Strictness()
{
    const ExecutionServiceIdentity identity = Identity("feed-epoch", 9);
    ExecutionEventFeedRequest request = Request(identity, "protocol-session", 42, 123);
    std::string body;
    std::string reason;
    assert(ExecutionEventFeedProtocol::EncodeRequest(request, body, reason));
    ExecutionEventFeedRequest decoded;
    assert(ExecutionEventFeedProtocol::DecodeRequest(body, decoded, reason));
    assert(decoded.operation == ExecutionEventFeedOperation::Wait);
    assert(SameIdentity(decoded.expectedServiceIdentity, identity));
    assert(decoded.afterSequence == 42);
    assert(decoded.timeoutMs == 123);

    ExecutionEventFeedRequest identityRequest;
    identityRequest.operation = ExecutionEventFeedOperation::GetServiceIdentity;
    assert(ExecutionEventFeedProtocol::EncodeRequest(identityRequest, body, reason));
    assert(ExecutionEventFeedProtocol::DecodeRequest(body, decoded, reason));
    assert(decoded.operation == ExecutionEventFeedOperation::GetServiceIdentity);

    ExecutionEventFeedRequest missingIdentity = Request(
        ExecutionServiceIdentity(), "missing-identity");
    assert(!ExecutionEventFeedProtocol::EncodeRequest(missingIdentity, body, reason));

    assert(ExecutionEventFeedProtocol::EncodeRequest(request, body, reason));
    // Tag 7 is the mandatory expected service fencing generation.
    assert(!ExecutionEventFeedProtocol::DecodeRequest(RemoveField(body, 7), decoded, reason));
    std::string hev1 = body;
    hev1[3] = '1';
    assert(!ExecutionEventFeedProtocol::DecodeRequest(hev1, decoded, reason));
    assert(!ExecutionEventFeedProtocol::DecodeRequest(
        WithUnknownField(body), decoded, reason));

    ExecutionEventReadResult gap;
    gap.status = ExecutionEventReadStatus::Gap;
    gap.serviceIdentity = identity;
    gap.streamEpoch = identity.serviceEpoch;
    gap.droppedThroughSequence = 41;
    gap.latestSequence = 50;
    gap.reasonCode = "EXECUTION_EVENT_GAP";
    assert(ExecutionEventFeedProtocol::EncodeResponse(gap, body, reason));
    ExecutionEventReadResult decodedResponse;
    assert(ExecutionEventFeedProtocol::DecodeResponse(body, decodedResponse, reason));
    assert(decodedResponse.status == ExecutionEventReadStatus::Gap);
    assert(SameIdentity(decodedResponse.serviceIdentity, identity));
    assert(!ExecutionEventFeedProtocol::DecodeResponse(
        WithUnknownField(body), decodedResponse, reason));
    gap.droppedThroughSequence = 0;
    assert(!ExecutionEventFeedProtocol::EncodeResponse(gap, body, reason));
}

void TestUnixFeedIsolationGapIdentityAndWorkers()
{
    const std::string socketPath = "/tmp/hepta-execution-events-" +
        std::to_string(::getpid()) + ".sock";
    const ExecutionServiceIdentity identity = Identity("feed-epoch", 9);
    CountingSource source(2, identity.serviceEpoch);
    const std::shared_ptr<ExecutionServiceLifecycleGate> gate = ReadyGate();
    UnixExecutionEventFeedServer server(source, identity, gate);
    std::string reason;
    assert(server.StartFromFd(ActivatedSocket(socketPath),
        std::set<std::uint32_t>{static_cast<std::uint32_t>(::geteuid())},
        reason, 8192, 500, 2, 8));
    assert(server.IsRunning());
    UnixExecutionEventFeedClient client(socketPath, 500);

    const ExecutionEventReadResult identityResult = client.GetServiceIdentity();
    assert(identityResult.status == ExecutionEventReadStatus::ServiceIdentity);
    assert(SameIdentity(identityResult.serviceIdentity, identity));
    assert(source.ReadsFor("session-a") == 0);

    const ExecutionEventReadResult timeout = client.Wait(Request(identity, "session-a"));
    assert(timeout.status == ExecutionEventReadStatus::Timeout);
    assert(timeout.reasonCode == "EXECUTION_EVENT_TIMEOUT");

    source.Publish(Event("session-a", 100));
    source.Publish(Event("session-b", 200));
    const ExecutionEventReadResult ownerA = client.Wait(Request(identity, "session-a"));
    assert(ownerA.status == ExecutionEventReadStatus::Event);
    assert(ownerA.event.orderId == 100);
    assert(ownerA.event.sessionId == "session-a");

    const std::uint64_t readsBeforeStale = source.ReadsFor("session-a");
    const ExecutionEventReadResult oldEpoch = client.Wait(Request(
        Identity("old-epoch", identity.serviceFencingGeneration),
        "session-a", ownerA.event.sequence));
    assert(oldEpoch.status == ExecutionEventReadStatus::ServiceIdentityMismatch);
    assert(source.ReadsFor("session-a") == readsBeforeStale);
    const ExecutionEventReadResult oldGeneration = client.Wait(Request(
        Identity(identity.serviceEpoch, identity.serviceFencingGeneration - 1),
        "session-a", ownerA.event.sequence));
    assert(oldGeneration.status == ExecutionEventReadStatus::ServiceIdentityMismatch);
    assert(source.ReadsFor("session-a") == readsBeforeStale);

    source.Publish(Event("session-a", 101));
    source.Publish(Event("session-a", 102));
    const ExecutionEventReadResult gap = client.Wait(Request(identity, "session-a", 0));
    assert(gap.status == ExecutionEventReadStatus::Gap);
    assert(gap.reasonCode == "EXECUTION_EVENT_GAP");
    assert(gap.droppedThroughSequence == ownerA.event.sequence);
    assert(gap.latestSequence >= gap.droppedThroughSequence + 2);
    const ExecutionEventReadResult resumed = client.Wait(Request(
        identity, "session-a", gap.droppedThroughSequence));
    assert(resumed.status == ExecutionEventReadStatus::Event);
    assert(resumed.event.orderId == 101);

    ExecutionEventReadResult waitA;
    ExecutionEventReadResult waitB;
    std::thread first([&]() {
        waitA = client.Wait(Request(identity, "wait-a", 0, 1000));
    });
    std::thread second([&]() {
        waitB = client.Wait(Request(identity, "wait-b", 0, 1000));
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    source.Publish(Event("wait-a", 301));
    source.Publish(Event("wait-b", 302));
    first.join();
    second.join();
    assert(waitA.status == ExecutionEventReadStatus::Event);
    assert(waitB.status == ExecutionEventReadStatus::Event);
    assert(waitA.event.orderId == 301);
    assert(waitB.event.orderId == 302);

    server.Stop();
    server.Stop();
    assert(!server.IsRunning());
    ::unlink(socketPath.c_str());
}

void TestEventFeedPeerCredentialRejection()
{
    const std::uint32_t currentUid = static_cast<std::uint32_t>(::geteuid());
    const std::uint32_t wrongUid = WrongEffectiveUid();
    const ExecutionServiceIdentity identity = Identity("peer-epoch", 11);
    std::string reason;

    const std::string serverRejectPath = "/tmp/hepta-events-peer-server-" +
        std::to_string(::getpid()) + ".sock";
    CountingSource serverRejectSource(8, identity.serviceEpoch);
    serverRejectSource.Publish(Event("rejected-reader", 801));
    const std::shared_ptr<ExecutionServiceLifecycleGate> rejectGate = ReadyGate();
    UnixExecutionEventFeedServer serverReject(
        serverRejectSource, identity, rejectGate);
    assert(serverReject.StartFromFd(ActivatedSocket(serverRejectPath),
        std::set<std::uint32_t>{wrongUid}, reason, 8192, 250, 1, 4));
    UnixExecutionEventFeedClient normalClient(serverRejectPath, 250, 32768,
        std::set<std::uint32_t>{currentUid});
    const ExecutionEventReadResult rejectedReader =
        normalClient.Wait(Request(identity, "rejected-reader"));
    assert(rejectedReader.status == ExecutionEventReadStatus::Timeout);
    // The server deliberately closes an unauthorized peer without a
    // response. Depending on scheduling, the client can observe that close
    // while writing the request or while reading the response.
    assert(rejectedReader.reasonCode == "EXECUTION_EVENT_REQUEST_WRITE_FAILED" ||
           rejectedReader.reasonCode == "EXECUTION_EVENT_RESPONSE_READ_FAILED");
    assert(serverRejectSource.ReadsFor("rejected-reader") == 0);
    assert(serverRejectSource.Pending("rejected-reader") == 1);
    serverReject.Stop();
    ::unlink(serverRejectPath.c_str());

    const std::string clientRejectPath = "/tmp/hepta-events-peer-client-" +
        std::to_string(::getpid()) + ".sock";
    CountingSource clientRejectSource(8, identity.serviceEpoch);
    clientRejectSource.Publish(Event("rejected-daemon", 802));
    const std::shared_ptr<ExecutionServiceLifecycleGate> normalGate = ReadyGate();
    UnixExecutionEventFeedServer normalServer(
        clientRejectSource, identity, normalGate);
    assert(normalServer.StartFromFd(ActivatedSocket(clientRejectPath),
        std::set<std::uint32_t>{currentUid}, reason, 8192, 250, 1, 4));
    UnixExecutionEventFeedClient rejectingClient(clientRejectPath, 250, 32768,
        std::set<std::uint32_t>{wrongUid});
    const ExecutionEventReadResult rejectedDaemon =
        rejectingClient.Wait(Request(identity, "rejected-daemon"));
    assert(rejectedDaemon.status == ExecutionEventReadStatus::Timeout);
    assert(rejectedDaemon.reasonCode == "EXECUTION_EVENT_PEER_UID_REJECTED");
    assert(clientRejectSource.ReadsFor("rejected-daemon") == 0);
    assert(clientRejectSource.Pending("rejected-daemon") == 1);
    normalServer.Stop();
    ::unlink(clientRejectPath.c_str());
}

void TestActivatedBacklogAndNotReadyNeverReadSource()
{
    const std::string socketPath = "/tmp/hepta-events-activated-backlog-" +
        std::to_string(::getpid()) + ".sock";
    const int managerFd = ActivatedSocket(socketPath);
    const std::set<std::uint32_t> uid{
        static_cast<std::uint32_t>(::geteuid())};
    const ExecutionServiceIdentity firstIdentity = Identity("event-service-a", 20);
    CountingSource firstSource(8, firstIdentity.serviceEpoch);
    const std::shared_ptr<ExecutionServiceLifecycleGate> firstGate = ReadyGate();
    UnixExecutionEventFeedServer first(firstSource, firstIdentity, firstGate);
    std::string reason;
    assert(first.StartFromFd(::dup(managerFd), uid, reason, 8192, 500, 1, 4));
    UnixExecutionEventFeedClient firstClient(socketPath, 500, 32768, uid);
    assert(firstClient.GetServiceIdentity().status ==
        ExecutionEventReadStatus::ServiceIdentity);
    first.Stop();

    ExecutionEventFeedRequest staleRequest = Request(
        firstIdentity, "activated-stale");
    std::string staleBody;
    assert(ExecutionEventFeedProtocol::EncodeRequest(staleRequest, staleBody, reason));
    const int queuedFd = ConnectSocket(socketPath);
    WriteFrame(queuedFd, staleBody);

    const ExecutionServiceIdentity secondIdentity = Identity("event-service-b", 21);
    CountingSource secondSource(8, secondIdentity.serviceEpoch);
    secondSource.Publish(Event("activated-stale", 901));
    const std::shared_ptr<ExecutionServiceLifecycleGate> secondGate(
        new ExecutionServiceLifecycleGate());
    UnixExecutionEventFeedServer second(secondSource, secondIdentity, secondGate);
    assert(second.StartFromFd(::dup(managerFd), uid, reason, 8192, 500, 1, 4));
    secondGate->ready.store(true);

    std::string responseBody;
    assert(ReadFrameWithTimeout(queuedFd, responseBody));
    ExecutionEventReadResult staleResult;
    assert(ExecutionEventFeedProtocol::DecodeResponse(
        responseBody, staleResult, reason));
    assert(staleResult.status ==
        ExecutionEventReadStatus::ServiceIdentityMismatch);
    assert(SameIdentity(staleResult.serviceIdentity, secondIdentity));
    assert(secondSource.ReadsFor("activated-stale") == 0);
    assert(secondSource.Pending("activated-stale") == 1);
    ::close(queuedFd);

    UnixExecutionEventFeedClient secondClient(socketPath, 500, 32768, uid);
    const ExecutionEventReadResult wrongGeneration = secondClient.Wait(Request(
        Identity(secondIdentity.serviceEpoch,
            secondIdentity.serviceFencingGeneration - 1),
        "wrong-generation"));
    assert(wrongGeneration.status ==
        ExecutionEventReadStatus::ServiceIdentityMismatch);
    assert(secondSource.ReadsFor("wrong-generation") == 0);

    secondGate->ready.store(false);
    const ExecutionEventReadResult notReady = secondClient.Wait(Request(
        secondIdentity, "not-ready"));
    assert(notReady.status == ExecutionEventReadStatus::ServiceNotReady);
    assert(secondSource.ReadsFor("not-ready") == 0);
    secondGate->ready.store(true);
    assert(secondClient.GetServiceIdentity().status ==
        ExecutionEventReadStatus::ServiceIdentity);
    assert(secondSource.ReadsFor("not-ready") == 0);

    second.Stop();
    ::close(managerFd);
    ::unlink(socketPath.c_str());
}

void TestStopRejectsAcceptedWorkerBacklog()
{
    const std::string socketPath = "/tmp/hepta-events-stop-backlog-" +
        std::to_string(::getpid()) + ".sock";
    const ExecutionServiceIdentity identity = Identity("stop-epoch", 30);
    CountingSource source(8, identity.serviceEpoch);
    source.Publish(Event("queued-stop", 1001));
    const std::shared_ptr<ExecutionServiceLifecycleGate> gate = ReadyGate();
    UnixExecutionEventFeedServer server(source, identity, gate);
    std::string reason;
    assert(server.StartFromFd(ActivatedSocket(socketPath),
        std::set<std::uint32_t>{static_cast<std::uint32_t>(::geteuid())},
        reason, 8192, 500, 1, 8));
    UnixExecutionEventFeedClient client(socketPath, 500);

    ExecutionEventReadResult blockingResult;
    std::thread blocking([&]() {
        blockingResult = client.Wait(Request(identity, "blocking-stop", 0, 1000));
    });
    assert(source.WaitForRead(
        "blocking-stop", std::chrono::seconds(5)));

    std::string queuedBody;
    assert(ExecutionEventFeedProtocol::EncodeRequest(
        Request(identity, "queued-stop"), queuedBody, reason));
    const int queuedFd = ConnectSocket(socketPath);
    WriteFrame(queuedFd, queuedBody);
    std::this_thread::sleep_for(std::chrono::milliseconds(30));

    server.Stop();
    blocking.join();
    assert(blockingResult.status != ExecutionEventReadStatus::Event);
    assert(source.ReadsFor("queued-stop") == 0);
    assert(source.Pending("queued-stop") == 1);

    std::string responseBody;
    if (ReadFrameWithTimeout(queuedFd, responseBody, 500))
    {
        ExecutionEventReadResult queuedResult;
        assert(ExecutionEventFeedProtocol::DecodeResponse(
            responseBody, queuedResult, reason));
        assert(queuedResult.status != ExecutionEventReadStatus::Event);
    }
    ::close(queuedFd);
    ::unlink(socketPath.c_str());
}

void TestRelayIdentityMismatchAndResyncLatch()
{
    const ExecutionServiceIdentity firstIdentity = Identity("relay-service-a", 40);
    const ExecutionServiceIdentity secondIdentity = Identity("relay-service-b", 41);
    ExecutionEventHub localHub(16, "gateway-epoch");
    int calls = 0;
    ExecutionEventRelay relay(localHub,
        [&](const ExecutionEventFeedRequest& request) {
            ++calls;
            ExecutionEventReadResult result;
            if (calls == 1)
            {
                assert(SameIdentity(request.expectedServiceIdentity, firstIdentity));
                result.status = ExecutionEventReadStatus::ServiceIdentityMismatch;
                result.serviceIdentity = secondIdentity;
                result.streamEpoch = secondIdentity.serviceEpoch;
                result.reasonCode = "EXECUTION_EVENT_SERVICE_IDENTITY_MISMATCH";
            }
            else if (calls == 2)
            {
                assert(SameIdentity(request.expectedServiceIdentity, secondIdentity));
                result.status = ExecutionEventReadStatus::Event;
                result.serviceIdentity = secondIdentity;
                result.streamEpoch = secondIdentity.serviceEpoch;
                result.latestSequence = 1;
                result.event = Event("relay-session", 1100);
                result.event.streamEpoch = secondIdentity.serviceEpoch;
                result.event.sequence = 1;
                result.event.timestampMs = 1;
            }
            else
            {
                result.status = ExecutionEventReadStatus::Gap;
                result.serviceIdentity = secondIdentity;
                result.streamEpoch = secondIdentity.serviceEpoch;
                result.droppedThroughSequence = 3;
                result.latestSequence = 3;
                result.reasonCode = "EXECUTION_EVENT_GAP";
            }
            return result;
        });

    ExecutionEventRelayOwner owner;
    owner.executionDomain = "SIM:EURUSD";
    owner.agentId = "feed-agent";
    owner.sessionId = "relay-session";
    owner.serviceIdentity = firstIdentity;
    ExecutionEventRelayCursor cursor;
    cursor.upstreamServiceIdentity = firstIdentity;
    cursor.upstreamEpoch = firstIdentity.serviceEpoch;
    cursor.upstreamSequence = 5;
    std::string reason;

    assert(relay.Poll(owner, cursor, 0, reason) ==
        ExecutionEventRelayStatus::ServiceIdentityMismatch);
    assert(calls == 1);
    assert(SameIdentity(cursor.upstreamServiceIdentity, firstIdentity));
    assert(cursor.upstreamSequence == 5);
    assert(!cursor.authoritativeResyncRequired);
    assert(localHub.Pending(owner.executionDomain, owner.agentId,
        owner.sessionId, 0) == 0);

    owner.serviceIdentity = secondIdentity;
    assert(relay.Poll(owner, cursor, 0, reason) ==
        ExecutionEventRelayStatus::ServiceIdentityChanged);
    assert(calls == 1);
    assert(SameIdentity(cursor.upstreamServiceIdentity, secondIdentity));
    assert(cursor.upstreamEpoch == secondIdentity.serviceEpoch);
    assert(cursor.upstreamSequence == 0);
    assert(cursor.authoritativeResyncRequired);
    ExecutionEvent control;
    assert(localHub.WaitNext(owner.executionDomain, owner.agentId, owner.sessionId,
        0, 0, control));
    assert(control.type == "system.execution_service_identity_changed");
    assert(control.status == "AuthoritativeResyncRequired");
    assert(control.upstreamServiceEpoch == secondIdentity.serviceEpoch);
    const std::uint64_t controlCursor = control.sequence;

    assert(relay.Poll(owner, cursor, 0, reason) ==
        ExecutionEventRelayStatus::ResyncRequired);
    assert(calls == 1);
    assert(!relay.AcknowledgeAuthoritativeResync(cursor, firstIdentity));
    assert(cursor.authoritativeResyncRequired);
    assert(relay.AcknowledgeAuthoritativeResync(cursor, secondIdentity));
    assert(!cursor.authoritativeResyncRequired);

    assert(relay.Poll(owner, cursor, 0, reason) ==
        ExecutionEventRelayStatus::Published);
    assert(calls == 2);
    ExecutionEvent published;
    assert(localHub.WaitNext(owner.executionDomain, owner.agentId, owner.sessionId,
        controlCursor, 0, published));
    assert(published.orderId == 1100);
    assert(published.upstreamServiceEpoch == secondIdentity.serviceEpoch);
    assert(published.upstreamServiceFencingGeneration ==
        secondIdentity.serviceFencingGeneration);
    assert(published.upstreamSequence == 1);
    const std::uint64_t publishedCursor = published.sequence;

    assert(relay.Poll(owner, cursor, 0, reason) == ExecutionEventRelayStatus::Gap);
    assert(cursor.authoritativeResyncRequired);
    ExecutionEvent gap;
    assert(localHub.WaitNext(owner.executionDomain, owner.agentId, owner.sessionId,
        publishedCursor, 0, gap));
    assert(gap.type == "system.execution_stream_gap");
    assert(gap.upstreamServiceEpoch == secondIdentity.serviceEpoch);
    assert(relay.Poll(owner, cursor, 0, reason) ==
        ExecutionEventRelayStatus::ResyncRequired);
    assert(calls == 3);
    assert(relay.AcknowledgeAuthoritativeResync(cursor, secondIdentity));
}
}

int main()
{
    TestProtocolV2Strictness();
    TestUnixFeedIsolationGapIdentityAndWorkers();
    TestEventFeedPeerCredentialRejection();
    TestActivatedBacklogAndNotReadyNeverReadSource();
    TestStopRejectsAcceptedWorkerBacklog();
    TestRelayIdentityMismatchAndResyncLatch();
    std::cout << "execution_event_fault_matrix_evidence:"
              << " server_identity_change=verified"
              << " ring_backpressure_gap=verified"
              << " gap_cursor_resume=verified"
              << " relay_gap_resync_latch=verified"
              << " relay_identity_reset=verified"
              << " peer_rejection_no_consume=verified"
              << " stale_event_identity_no_read=verified"
              << " identity_reject_no_cursor_publish=verified"
              << std::endl;
    std::cout << "execution_event_feed_tests: PASS" << std::endl;
    return 0;
}
