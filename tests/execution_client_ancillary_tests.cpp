#include "../HeptaTrade/execution/unix_execution_service_client.h"
#include "../HeptaTrade/execution/execution_service_protocol.h"
#include "../HeptaTrade/execution/unix_execution_service_internal.h"

#include <arpa/inet.h>
#include <cerrno>
#include <cstring>
#include <dirent.h>
#include <fcntl.h>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/socket.h>
#include <sys/un.h>
#include <thread>
#include <unistd.h>
#include <vector>

namespace {
enum class Fault { None, Missing, Duplicate, Malformed, WrongUid, RootActivator,
                   Unexpected, Truncated, EmptyBuffer };
thread_local Fault fault = Fault::None;
thread_local unsigned int receiveCount = 0;
thread_local unsigned int faultAt = 0;

void Require(bool value, const std::string& message)
{
    if (!value) throw std::runtime_error("ancillary fixture: " + message);
}

std::uint64_t StartTicks()
{
    std::ifstream input("/proc/self/stat"); std::string line, field;
    Require(bool(std::getline(input, line)), "proc stat");
    std::istringstream fields(line.substr(line.rfind(')') + 1));
    for (int i = 0; i != 20; ++i) Require(bool(fields >> field), "start ticks");
    return std::stoull(field);
}

std::size_t OpenDescriptors()
{
    DIR* directory = ::opendir("/proc/self/fd");
    Require(directory != nullptr, "fd census");
    std::size_t count = 0;
    while (auto* entry = ::readdir(directory))
        if (std::strcmp(entry->d_name, ".") && std::strcmp(entry->d_name, "..")) ++count;
    Require(::closedir(directory) == 0, "fd census close");
    return count;
}

struct Listener
{
    std::string directory, path;
    int fd = -1;
    Listener()
    {
        char pattern[] = "/tmp/hepta-sender-ancillary-XXXXXX";
        Require(::mkdtemp(pattern) != nullptr, "tempdir"); directory = pattern;
        path = directory + "/socket";
        fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
        Require(fd >= 0, "socket");
        struct sockaddr_un address = {}; address.sun_family = AF_UNIX;
        std::memcpy(address.sun_path, path.c_str(), path.size() + 1);
        Require(::bind(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0 &&
                ::listen(fd, 2) == 0, "bind/listen");
    }
    ~Listener() { if (fd >= 0) ::close(fd); ::unlink(path.c_str()); ::rmdir(directory.c_str()); }
};

std::string Response()
{
    ExecutionCommandResult response;
    response.commandId = "__service_identity__";
    response.status = ExecutionCommandStatus::Accepted;
    response.serviceEpoch = "sender-ancillary-epoch";
    response.serviceFencingGeneration = 19;
    std::string body, reason;
    Require(ExecutionServiceProtocol::EncodeResponse(response, body, reason), reason);
    return body;
}

void RejectAncillary(Fault injected, unsigned int at, const std::string& expected,
                    unsigned int rights = 0)
{
    Listener listener;
    const int sourceFd = rights ? ::open("/dev/null", O_RDONLY | O_CLOEXEC) : -1;
    Require(!rights || sourceFd >= 0, "rights source");
    const std::size_t descriptors = OpenDescriptors();
    std::string serverError;
    std::thread server([&]() {
        const int fd = ::accept4(listener.fd, nullptr, nullptr, SOCK_CLOEXEC);
        if (fd < 0) { serverError = "accept"; return; }
        std::string request;
        const auto deadline = HeptaExecutionServiceInternal::DeadlineAfter(2000);
        if (!HeptaExecutionServiceInternal::ReadFrame(fd, 32768, deadline, request))
            serverError = "request";
        else
        {
            const std::string body = Response();
            const std::uint32_t length = htonl(body.size());
            std::string frame(reinterpret_cast<const char*>(&length), sizeof(length));
            frame += body;
            if (rights)
            {
                std::vector<char> control(CMSG_SPACE(rights * sizeof(int)));
                struct iovec data = {&frame[0], frame.size()};
                struct msghdr message = {};
                message.msg_iov = &data; message.msg_iovlen = 1;
                message.msg_control = control.data(); message.msg_controllen = control.size();
                struct cmsghdr* item = CMSG_FIRSTHDR(&message);
                item->cmsg_level = SOL_SOCKET; item->cmsg_type = SCM_RIGHTS;
                item->cmsg_len = CMSG_LEN(rights * sizeof(int));
                for (unsigned int i = 0; i != rights; ++i)
                    std::memcpy(CMSG_DATA(item) + i * sizeof(int), &sourceFd, sizeof(int));
                if (::sendmsg(fd, &message, MSG_NOSIGNAL) != static_cast<ssize_t>(frame.size()))
                    serverError = "rights send";
            }
            else if (::send(fd, frame.data(), frame.size(), MSG_NOSIGNAL) !=
                     static_cast<ssize_t>(frame.size())) serverError = "response send";
            // Keep the sender alive through every /proc identity check.
            char ignored;
            while (::recv(fd, &ignored, 1, 0) > 0) {}
        }
        ::close(fd);
    });
    UnixExecutionServiceClient client(listener.path, 1500, 32768,
        {static_cast<std::uint32_t>(::geteuid())}, 1500, ::getpid(), StartTicks(),
        UnixExecutionServiceClient::ProcessBindingMode::InactiveOwnerAuditResponse);
    fault = injected; faultAt = at; receiveCount = 0;
    ExecutionServiceIdentity identity; std::string reason;
    const bool accepted = client.GetServiceIdentity(identity, reason);
    fault = Fault::None;
    server.join();
    const std::size_t after = OpenDescriptors();
    if (sourceFd >= 0) ::close(sourceFd);
    Require(serverError.empty(), serverError);
    Require(!accepted && reason == expected, "rejection: " + reason);
    Require(receiveCount >= at, "injection point did not execute");
    Require(after == descriptors, "received descriptors leaked on rejection");
}
} // namespace

extern "C" ssize_t __real_recvmsg(int, struct msghdr*, int);
extern "C" ssize_t __wrap_recvmsg(int fd, struct msghdr* message, int flags)
{
    const bool inject = fault != Fault::None && receiveCount + 1 == faultAt;
    const std::size_t capacity = message->msg_controllen;
    if (inject && fault == Fault::EmptyBuffer) message->msg_controllen = 0;
    const ssize_t count = __real_recvmsg(fd, message, flags);
    if (count <= 0) return count;
    ++receiveCount;
    if (!inject || fault == Fault::EmptyBuffer) return count;
    struct cmsghdr* item = CMSG_FIRSTHDR(message);
    Require(item && item->cmsg_level == SOL_SOCKET && item->cmsg_type == SCM_CREDENTIALS &&
            item->cmsg_len == CMSG_LEN(sizeof(struct ucred)), "real kernel credential missing");
    if (fault == Fault::Missing) message->msg_controllen = 0;
    else if (fault == Fault::Duplicate)
    {
        const std::size_t space = CMSG_SPACE(sizeof(struct ucred));
        Require(capacity >= 2 * space, "duplicate buffer capacity");
        std::memcpy(static_cast<char*>(message->msg_control) + space, item, space);
        message->msg_controllen = 2 * space;
    }
    else if (fault == Fault::Malformed) --item->cmsg_len;
    else if (fault == Fault::Unexpected) item->cmsg_type = 0x6fff;
    else if (fault == Fault::Truncated) message->msg_flags |= MSG_CTRUNC;
    else
    {
        struct ucred credential;
        std::memcpy(&credential, CMSG_DATA(item), sizeof(credential));
        credential.uid = ::geteuid() + 1;
        if (fault == Fault::RootActivator) { credential.uid = 0; credential.pid = 1; }
        std::memcpy(CMSG_DATA(item), &credential, sizeof(credential));
    }
    return count;
}

void RunExecutionClientAncillaryTests()
{
    // Header and body are distinct recvmsg calls even when the server writes
    // the complete frame once. Faults cover both, after real kernel receipt.
    for (unsigned int at : {1u, 2u})
    {
        RejectAncillary(Fault::Missing, at, "EXECUTION_SERVICE_RESPONSE_CREDENTIALS_MISSING");
        RejectAncillary(Fault::Duplicate, at, "EXECUTION_SERVICE_RESPONSE_CREDENTIALS_INVALID");
        RejectAncillary(Fault::Malformed, at, "EXECUTION_SERVICE_RESPONSE_CREDENTIALS_INVALID");
        RejectAncillary(Fault::Unexpected, at, "EXECUTION_SERVICE_RESPONSE_CREDENTIALS_INVALID");
        RejectAncillary(Fault::Truncated, at, "EXECUTION_SERVICE_RESPONSE_CREDENTIALS_TRUNCATED");
        RejectAncillary(Fault::EmptyBuffer, at, "EXECUTION_SERVICE_RESPONSE_CREDENTIALS_TRUNCATED");
        RejectAncillary(Fault::WrongUid, at, "EXECUTION_SERVICE_RESPONSE_SENDER_UID_REJECTED");
        RejectAncillary(Fault::RootActivator, at, "EXECUTION_SERVICE_RESPONSE_SENDER_UID_REJECTED");
    }
    // Real SCM_RIGHTS delivery, including a kernel-truncated control buffer,
    // must leave the descriptor census unchanged after rejection.
    RejectAncillary(Fault::None, 1, "EXECUTION_SERVICE_RESPONSE_CREDENTIALS_INVALID", 2);
    RejectAncillary(Fault::None, 1, "EXECUTION_SERVICE_RESPONSE_CREDENTIALS_TRUNCATED", 64);
}
