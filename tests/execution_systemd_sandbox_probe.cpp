#include "../HeptaTrade/execution/ib_paper_kill_switch.h"

#include <arpa/inet.h>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <fcntl.h>
#include <grp.h>
#include <iostream>
#include <limits>
#include <netinet/in.h>
#include <poll.h>
#include <pthread.h>
#include <pwd.h>
#include <set>
#include <signal.h>
#include <sstream>
#include <string>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/types.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

namespace
{
const char* const kExecutionSocket = "/run/hepta-execution/execution.sock";
const char* const kEventSocket = "/run/hepta-execution/events.sock";
const char* const kControlDirectory = "/run/hepta/ib-paper-control";
const char* const kEvidenceName = "execution-systemd-sandbox-probe.evidence";
const char* const kLoopbackSentinelAddress = "127.0.0.1";
const std::uint16_t kLoopbackSentinelPort = 38081;
const char* const kNonLoopbackSentinelAddress = "192.0.2.1";
const std::uint16_t kNonLoopbackSentinelPort = 38082;
const std::size_t kMaximumEnvironmentBytes = 1024;
const std::size_t kMaximumEvidenceBytes = 4096;

enum class ProbeMode
{
    Simulator = 0,
    IbPaper
};

struct Endpoint
{
    sockaddr_storage address;
    socklen_t length = 0;
    int family = AF_UNSPEC;
};

struct ProbeContext
{
    ProbeMode mode = ProbeMode::Simulator;
    std::string modeName;
    std::string serviceName;
    std::string stateDirectory;
    std::string credentialDirectory;
    std::uint32_t gatewayUid = 0;
    gid_t gatewayGid = 0;
    int executionFd = -1;
    int eventFd = -1;
    std::size_t supplementaryGroupCount = 0;
    std::size_t credentialCount = 0;
    std::string networkEvidence;
    std::string killSwitchEvidence = "not_applicable";
};

bool ReadEnvironment(const char* name, std::string& value)
{
    const char* observed = ::getenv(name);
    if (observed == nullptr || *observed == '\0') return false;
    value = observed;
    return value.size() <= kMaximumEnvironmentBytes &&
        value.find('\n') == std::string::npos &&
        value.find('\r') == std::string::npos &&
        value.find('\0') == std::string::npos;
}

bool ParseUnsigned(const std::string& value, std::uint64_t maximum,
                   std::uint64_t& parsed)
{
    if (value.empty()) return false;
    std::uint64_t result = 0;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char character = static_cast<unsigned char>(value[i]);
        if (character < static_cast<unsigned char>('0') ||
            character > static_cast<unsigned char>('9'))
            return false;
        const std::uint64_t digit = static_cast<std::uint64_t>(character - '0');
        if (result > (maximum - digit) / 10) return false;
        result = result * 10 + digit;
    }
    parsed = result;
    return true;
}

bool SafeAbsolutePath(const std::string& path)
{
    if (path.size() < 2 || path[0] != '/' || path[path.size() - 1] == '/' ||
        path.find(':') != std::string::npos)
        return false;
    std::size_t offset = 1;
    while (offset <= path.size())
    {
        const std::size_t slash = path.find('/', offset);
        const std::string component = path.substr(
            offset, slash == std::string::npos ? slash : slash - offset);
        if (component.empty() || component == "." || component == "..")
            return false;
        if (slash == std::string::npos) break;
        offset = slash + 1;
    }
    return true;
}

bool LoadModeAndPaths(ProbeContext& context, std::string& reason)
{
    const char* simulator = ::getenv("HEPTA_EXECUTION_SERVICE_MODE");
    const char* paper = ::getenv("HEPTA_IB_EXECUTION_MODE");
    const bool simulatorMode = simulator != nullptr &&
        std::string(simulator) == "SIMULATOR";
    const bool paperMode = paper != nullptr && std::string(paper) == "PAPER";
    if (simulatorMode == paperMode)
    {
        reason = "exactly one canonical execution mode is required";
        return false;
    }
    context.mode = simulatorMode ? ProbeMode::Simulator : ProbeMode::IbPaper;
    context.modeName = simulatorMode ? "SIMULATOR" : "IB_PAPER";
    context.serviceName = simulatorMode ? "hepta-exec" : "hepta-ib-exec";

    if (!ReadEnvironment("STATE_DIRECTORY", context.stateDirectory) ||
        !SafeAbsolutePath(context.stateDirectory) ||
        !ReadEnvironment("CREDENTIALS_DIRECTORY", context.credentialDirectory) ||
        !SafeAbsolutePath(context.credentialDirectory))
    {
        reason = "safe STATE_DIRECTORY and CREDENTIALS_DIRECTORY are required";
        return false;
    }
    reason.clear();
    return true;
}

bool ValidateIdentities(ProbeContext& context, std::string& reason)
{
    const char* gatewayKey = context.mode == ProbeMode::Simulator ?
        "HEPTA_EXECUTION_GATEWAY_UID" : "HEPTA_IB_EXECUTION_GATEWAY_UID";
    const char* gatewayAgentKey = context.mode == ProbeMode::Simulator ?
        "HEPTA_EXECUTION_GATEWAY_AGENT_ID" :
        "HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID";
    std::string gatewayValue;
    std::uint64_t gatewayUid = 0;
    if (!ReadEnvironment(gatewayKey, gatewayValue) ||
        !ParseUnsigned(gatewayValue,
            std::numeric_limits<std::uint32_t>::max(), gatewayUid) ||
        gatewayUid == 0)
    {
        reason = "canonical nonzero gateway UID is required";
        return false;
    }
    context.gatewayUid = static_cast<std::uint32_t>(gatewayUid);
    std::string gatewayAgentId;
    if (!ReadEnvironment(gatewayAgentKey, gatewayAgentId) ||
        gatewayAgentId != "codex-agent-os-e2e")
    {
        reason = "canonical gateway Agent binding is required";
        return false;
    }

    const struct passwd* service = ::getpwnam(context.serviceName.c_str());
    if (service == nullptr)
    {
        reason = "service account is missing";
        return false;
    }
    const uid_t expectedUid = service->pw_uid;
    const gid_t expectedGid = service->pw_gid;
    if (expectedUid == 0 || expectedGid == 0 || ::getuid() != expectedUid ||
        ::geteuid() != expectedUid || ::getgid() != expectedGid ||
        ::getegid() != expectedGid || expectedUid == context.gatewayUid)
    {
        reason = "service process UID/GID isolation mismatch";
        return false;
    }

    const struct passwd* gateway = ::getpwuid(
        static_cast<uid_t>(context.gatewayUid));
    if (gateway == nullptr || std::string(gateway->pw_name) != "hepta-gateway" ||
        gateway->pw_uid == 0 || gateway->pw_gid == 0 ||
        gateway->pw_gid == expectedGid)
    {
        reason = "gateway UID does not resolve to an isolated hepta-gateway account";
        return false;
    }
    context.gatewayGid = gateway->pw_gid;

    const int groupCount = ::getgroups(0, nullptr);
    if (groupCount < 0 || groupCount > 64)
    {
        reason = "supplementary group inspection failed";
        return false;
    }
    std::vector<gid_t> groups(static_cast<std::size_t>(groupCount));
    if (groupCount > 0 && ::getgroups(groupCount, &groups[0]) != groupCount)
    {
        reason = "supplementary group read failed";
        return false;
    }
    for (std::size_t i = 0; i < groups.size(); ++i)
    {
        if (groups[i] != expectedGid)
        {
            reason = "unexpected supplementary group inherited";
            return false;
        }
    }
    context.supplementaryGroupCount = groups.size();
    reason.clear();
    return true;
}

bool ValidateSocketFd(int fd, const std::string& expectedPath,
                      const ProbeContext& context, std::string& reason)
{
    if (::fcntl(fd, F_GETFD) < 0)
    {
        reason = "activated descriptor is not open";
        return false;
    }
    int socketType = 0;
    socklen_t optionLength = sizeof(socketType);
    if (::getsockopt(fd, SOL_SOCKET, SO_TYPE, &socketType, &optionLength) != 0 ||
        optionLength != sizeof(socketType) || socketType != SOCK_STREAM)
    {
        reason = "activated descriptor is not SOCK_STREAM";
        return false;
    }
    int accepting = 0;
    optionLength = sizeof(accepting);
    if (::getsockopt(fd, SOL_SOCKET, SO_ACCEPTCONN, &accepting, &optionLength) != 0 ||
        accepting != 1)
    {
        reason = "activated descriptor is not listening";
        return false;
    }
    struct sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    socklen_t addressLength = sizeof(address);
    if (::getsockname(fd, reinterpret_cast<struct sockaddr*>(&address),
            &addressLength) != 0 || address.sun_family != AF_UNIX)
    {
        reason = "activated descriptor is not AF_UNIX";
        return false;
    }
    const std::size_t pathLength = ::strnlen(address.sun_path,
        sizeof(address.sun_path));
    if (pathLength == sizeof(address.sun_path) ||
        std::string(address.sun_path, pathLength) != expectedPath)
    {
        reason = "activated descriptor pathname mismatch";
        return false;
    }

    struct stat socketMetadata;
    if (::lstat(expectedPath.c_str(), &socketMetadata) != 0 ||
        !S_ISSOCK(socketMetadata.st_mode) ||
        (socketMetadata.st_mode & 07777) != 0600 ||
        socketMetadata.st_uid != static_cast<uid_t>(context.gatewayUid) ||
        socketMetadata.st_gid != context.gatewayGid)
    {
        reason = "activated socket inode ownership or mode mismatch";
        return false;
    }
    reason.clear();
    return true;
}

bool ValidateActivatedFds(ProbeContext& context, std::string& reason)
{
    std::string pidValue;
    std::string countValue;
    std::string namesValue;
    std::uint64_t parsedPid = 0;
    std::uint64_t parsedCount = 0;
    if (!ReadEnvironment("LISTEN_PID", pidValue) ||
        !ParseUnsigned(pidValue,
            static_cast<std::uint64_t>(std::numeric_limits<pid_t>::max()),
            parsedPid) || parsedPid != static_cast<std::uint64_t>(::getpid()) ||
        !ReadEnvironment("LISTEN_FDS", countValue) ||
        !ParseUnsigned(countValue, 16, parsedCount) || parsedCount != 2 ||
        !ReadEnvironment("LISTEN_FDNAMES", namesValue))
    {
        reason = "exact systemd activation environment is required";
        return false;
    }

    std::vector<std::string> names;
    std::size_t offset = 0;
    while (offset <= namesValue.size())
    {
        const std::size_t colon = namesValue.find(':', offset);
        names.push_back(namesValue.substr(offset,
            colon == std::string::npos ? colon : colon - offset));
        if (colon == std::string::npos) break;
        offset = colon + 1;
    }
    if (names.size() != 2 || names[0] == names[1])
    {
        reason = "exactly two unique activated FD names are required";
        return false;
    }
    for (std::size_t i = 0; i < names.size(); ++i)
    {
        const int fd = 3 + static_cast<int>(i);
        if (names[i] == "execution") context.executionFd = fd;
        else if (names[i] == "events") context.eventFd = fd;
        else
        {
            reason = "unknown activated FD name";
            return false;
        }
    }
    if (context.executionFd < 3 || context.eventFd < 3 ||
        context.executionFd == context.eventFd ||
        !ValidateSocketFd(context.executionFd, kExecutionSocket, context, reason) ||
        !ValidateSocketFd(context.eventFd, kEventSocket, context, reason))
        return false;
    reason.clear();
    return true;
}

bool ReadCredential(const std::string& directory, const char* name,
                    uid_t effectiveUid, std::string& reason)
{
    const std::string path = directory + "/" + name;
    const int descriptor = ::open(
        path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (descriptor < 0)
    {
        reason = std::string("credential open failed: ") + name;
        return false;
    }
    struct stat metadata;
    if (::fstat(descriptor, &metadata) != 0 || !S_ISREG(metadata.st_mode) ||
        (metadata.st_mode & 07777) != 0400 || metadata.st_nlink != 1 ||
        (metadata.st_uid != 0 && metadata.st_uid != effectiveUid) ||
        metadata.st_size < 1 || metadata.st_size > 256)
    {
        ::close(descriptor);
        reason = std::string("credential metadata unsafe: ") + name;
        return false;
    }
    char buffer[256];
    std::size_t total = 0;
    while (total < static_cast<std::size_t>(metadata.st_size))
    {
        const ssize_t count = ::read(descriptor, buffer + total,
            static_cast<std::size_t>(metadata.st_size) - total);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0)
        {
            ::close(descriptor);
            reason = std::string("credential read failed: ") + name;
            return false;
        }
        total += static_cast<std::size_t>(count);
    }
    const int closeResult = ::close(descriptor);
    if (closeResult != 0)
    {
        reason = std::string("credential close failed: ") + name;
        return false;
    }

    errno = 0;
    const int writable = ::open(path.c_str(), O_WRONLY | O_CLOEXEC | O_NOFOLLOW);
    const int writeError = errno;
    if (writable >= 0)
    {
        ::close(writable);
        reason = std::string("credential unexpectedly writable: ") + name;
        return false;
    }
    if (writeError != EACCES && writeError != EPERM && writeError != EROFS)
    {
        reason = std::string("credential write failed for unexpected reason: ") + name;
        return false;
    }
    reason.clear();
    return true;
}

bool ValidateCredentials(ProbeContext& context, std::string& reason)
{
    struct stat directory;
    struct statvfs filesystem;
    if (::lstat(context.credentialDirectory.c_str(), &directory) != 0 ||
        !S_ISDIR(directory.st_mode) ||
        ::statvfs(context.credentialDirectory.c_str(), &filesystem) != 0 ||
        (filesystem.f_flag & ST_RDONLY) == 0)
    {
        reason = "credential directory must be a read-only mounted directory";
        return false;
    }
    static const char* const simulatorCredentials[] = {
        "hepta-execution-fence"
    };
    static const char* const paperCredentials[] = {
        "hepta-execution-fence", "hepta-ib-paper-authorization"
    };
    const char* const* names = context.mode == ProbeMode::Simulator ?
        simulatorCredentials : paperCredentials;
    const std::size_t count = context.mode == ProbeMode::Simulator ? 1 : 2;
    for (std::size_t i = 0; i < count; ++i)
    {
        if (!ReadCredential(context.credentialDirectory, names[i], ::geteuid(), reason))
            return false;
    }
    context.credentialCount = count;
    reason.clear();
    return true;
}

bool ValidateKillSwitch(ProbeContext& context, std::string& reason)
{
    if (context.mode != ProbeMode::IbPaper)
    {
        reason.clear();
        return true;
    }
    std::string configuredControl;
    if (!ReadEnvironment("HEPTA_IB_PAPER_CONTROL_DIRECTORY", configuredControl) ||
        configuredControl != kControlDirectory)
    {
        reason = "fixed IB PAPER control directory is required";
        return false;
    }
    struct statvfs filesystem;
    if (::statvfs(kControlDirectory, &filesystem) != 0 ||
        (filesystem.f_flag & ST_RDONLY) == 0)
    {
        reason = "IB PAPER control directory is not mounted read-only";
        return false;
    }
    std::shared_ptr<IbPaperKillSwitch> killSwitch;
    if (!IbPaperKillSwitch::OpenAndPinProduction(
            kControlDirectory, killSwitch, reason) || !killSwitch)
        return false;
    const IbPaperKillSwitchObservation observation = killSwitch->Observe();
    if (observation.state != IbPaperKillSwitchState::Engaged ||
        observation.reasonCode != "IB_PAPER_KILL_SWITCH_ENGAGED")
    {
        reason = "production kill-switch observation is not engaged";
        return false;
    }
    const std::string marker = std::string(kControlDirectory) + "/" +
        IbPaperKillSwitch::MarkerName();
    errno = 0;
    const int writable = ::open(
        marker.c_str(), O_WRONLY | O_CLOEXEC | O_NOFOLLOW);
    const int writeError = errno;
    if (writable >= 0)
    {
        ::close(writable);
        reason = "kill-switch marker unexpectedly writable";
        return false;
    }
    if (writeError != EACCES && writeError != EPERM && writeError != EROFS)
    {
        reason = "kill-switch marker write failed for unexpected reason";
        return false;
    }
    context.killSwitchEvidence = "engaged";
    reason.clear();
    return true;
}

bool ExpectedAddressFamilyDenial(int error)
{
    return error == EACCES || error == EPERM || error == EAFNOSUPPORT;
}

bool ValidateSimulatorNetwork(ProbeContext& context, std::string& reason)
{
    errno = 0;
    const int ipv4 = ::socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
    const int ipv4Error = errno;
    if (ipv4 >= 0)
    {
        ::close(ipv4);
        reason = "Simulator unexpectedly created an AF_INET socket";
        return false;
    }
    if (!ExpectedAddressFamilyDenial(ipv4Error))
    {
        reason = "Simulator AF_INET socket failed for an unexpected reason";
        return false;
    }
    errno = 0;
    const int ipv6 = ::socket(AF_INET6, SOCK_STREAM | SOCK_CLOEXEC, 0);
    const int ipv6Error = errno;
    if (ipv6 >= 0)
    {
        ::close(ipv6);
        reason = "Simulator unexpectedly created an AF_INET6 socket";
        return false;
    }
    if (!ExpectedAddressFamilyDenial(ipv6Error))
    {
        reason = "Simulator AF_INET6 socket failed for an unexpected reason";
        return false;
    }
    context.networkEvidence = "af_inet_and_af_inet6_denied";
    reason.clear();
    return true;
}

bool BuildEndpoint(const std::string& text, std::uint16_t port,
                   Endpoint& endpoint, bool& loopback)
{
    std::memset(&endpoint.address, 0, sizeof(endpoint.address));
    struct sockaddr_in* ipv4 = reinterpret_cast<struct sockaddr_in*>(
        &endpoint.address);
    if (::inet_pton(AF_INET, text.c_str(), &ipv4->sin_addr) == 1)
    {
        ipv4->sin_family = AF_INET;
        ipv4->sin_port = htons(port);
        endpoint.family = AF_INET;
        endpoint.length = sizeof(*ipv4);
        const std::uint32_t host = ntohl(ipv4->sin_addr.s_addr);
        loopback = (host & 0xff000000U) == 0x7f000000U;
        return true;
    }
    struct sockaddr_in6* ipv6 = reinterpret_cast<struct sockaddr_in6*>(
        &endpoint.address);
    if (::inet_pton(AF_INET6, text.c_str(), &ipv6->sin6_addr) == 1)
    {
        ipv6->sin6_family = AF_INET6;
        ipv6->sin6_port = htons(port);
        endpoint.family = AF_INET6;
        endpoint.length = sizeof(*ipv6);
        loopback = IN6_IS_ADDR_LOOPBACK(&ipv6->sin6_addr) != 0;
        return true;
    }
    return false;
}

bool SafeNonLoopbackEndpoint(const Endpoint& endpoint)
{
    if (endpoint.family == AF_INET)
    {
        const struct sockaddr_in* ipv4 = reinterpret_cast<const struct sockaddr_in*>(
            &endpoint.address);
        const std::uint32_t host = ntohl(ipv4->sin_addr.s_addr);
        const std::uint32_t firstOctet = host >> 24;
        return host != 0 && host != 0xffffffffU && firstOctet != 0 &&
            firstOctet != 127 && firstOctet < 224;
    }
    if (endpoint.family == AF_INET6)
    {
        const struct sockaddr_in6* ipv6 =
            reinterpret_cast<const struct sockaddr_in6*>(&endpoint.address);
        return IN6_IS_ADDR_LOOPBACK(&ipv6->sin6_addr) == 0 &&
            IN6_IS_ADDR_UNSPECIFIED(&ipv6->sin6_addr) == 0 &&
            IN6_IS_ADDR_MULTICAST(&ipv6->sin6_addr) == 0;
    }
    return false;
}

int ConnectEndpoint(const Endpoint& endpoint)
{
    const int descriptor = ::socket(
        endpoint.family, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (descriptor < 0) return errno;
    int result = ::connect(descriptor,
        reinterpret_cast<const struct sockaddr*>(&endpoint.address),
        endpoint.length);
    int connectionError = 0;
    if (result != 0 && errno == EINPROGRESS)
    {
        struct pollfd pending;
        pending.fd = descriptor;
        pending.events = POLLOUT;
        pending.revents = 0;
        do
        {
            result = ::poll(&pending, 1, 2000);
        } while (result < 0 && errno == EINTR);
        if (result == 0) connectionError = ETIMEDOUT;
        else if (result < 0) connectionError = errno;
        else
        {
            socklen_t length = sizeof(connectionError);
            if (::getsockopt(descriptor, SOL_SOCKET, SO_ERROR,
                    &connectionError, &length) != 0)
                connectionError = errno;
        }
    }
    else if (result != 0)
        connectionError = errno;
    const int closeResult = ::close(descriptor);
    if (connectionError == 0 && closeResult != 0) return errno;
    return connectionError;
}

bool ValidateIbNetwork(ProbeContext& context, std::string& reason)
{
    Endpoint allowed;
    Endpoint denied;
    bool allowedLoopback = false;
    bool deniedLoopback = false;
    if (!BuildEndpoint(kLoopbackSentinelAddress, kLoopbackSentinelPort,
            allowed, allowedLoopback) ||
        !allowedLoopback ||
        !BuildEndpoint(kNonLoopbackSentinelAddress,
            kNonLoopbackSentinelPort, denied, deniedLoopback) ||
        deniedLoopback || !SafeNonLoopbackEndpoint(denied))
    {
        reason = "network sentinel address classification failed";
        return false;
    }
    const int allowedError = ConnectEndpoint(allowed);
    if (allowedError != 0)
    {
        reason = "loopback sentinel was not reachable";
        return false;
    }
    const int deniedError = ConnectEndpoint(denied);
    if (deniedError != EACCES && deniedError != EPERM)
    {
        reason = "non-loopback sentinel was not denied by systemd IP policy";
        return false;
    }
    context.networkEvidence = "loopback_allowed_nonloopback_denied";
    reason.clear();
    return true;
}

bool ValidateNetwork(ProbeContext& context, std::string& reason)
{
    return context.mode == ProbeMode::Simulator ?
        ValidateSimulatorNetwork(context, reason) :
        ValidateIbNetwork(context, reason);
}

bool WriteAll(int descriptor, const char* data, std::size_t size)
{
    std::size_t offset = 0;
    while (offset < size)
    {
        const ssize_t count = ::write(descriptor, data + offset, size - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return false;
        offset += static_cast<std::size_t>(count);
    }
    return true;
}

bool WriteEvidence(const ProbeContext& context, pid_t descendant,
                   std::string& reason)
{
    struct stat stateMetadata;
    if (::lstat(context.stateDirectory.c_str(), &stateMetadata) != 0 ||
        !S_ISDIR(stateMetadata.st_mode) || stateMetadata.st_uid != ::geteuid() ||
        (stateMetadata.st_mode & 07777) != 0700)
    {
        reason = "StateDirectory ownership or mode is unsafe";
        return false;
    }
    const int directory = ::open(context.stateDirectory.c_str(),
        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (directory < 0)
    {
        reason = "StateDirectory open failed";
        return false;
    }
    struct stat existing;
    if (::fstatat(directory, kEvidenceName, &existing, AT_SYMLINK_NOFOLLOW) == 0 ||
        errno != ENOENT)
    {
        ::close(directory);
        reason = "evidence pathname must not already exist";
        return false;
    }

    std::ostringstream output;
    output << "schema=hepta.execution-systemd-sandbox-probe.v1\n"
           << "mode=" << context.modeName << '\n'
           << "pid=" << ::getpid() << '\n'
           << "euid=" << ::geteuid() << '\n'
           << "egid=" << ::getegid() << '\n'
           << "gateway_uid=" << context.gatewayUid << '\n'
           << "supplementary_groups_safe=true\n"
           << "supplementary_group_count="
           << context.supplementaryGroupCount << '\n'
           << "listen_fds=2\n"
           << "execution_fd=" << context.executionFd << '\n'
           << "event_fd=" << context.eventFd << '\n'
           << "socket_contract=verified\n"
           << "credential_count=" << context.credentialCount << '\n'
           << "credentials_readable=true\n"
           << "credentials_read_only=true\n"
           << "kill_switch=" << context.killSwitchEvidence << '\n'
           << "network=" << context.networkEvidence << '\n'
           << "descendant_pid=" << descendant << '\n'
           << "descendant_sigterm=ignored\n"
           << "real_ibapi_linked=false\n"
           << "mutation_requests=0\n";
    const std::string contents = output.str();
    if (contents.empty() || contents.size() > kMaximumEvidenceBytes)
    {
        ::close(directory);
        reason = "evidence exceeds the fixed size bound";
        return false;
    }

    const std::string temporary = std::string(kEvidenceName) + ".tmp." +
        std::to_string(static_cast<long long>(::getpid()));
    const int descriptor = ::openat(directory, temporary.c_str(),
        O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (descriptor < 0)
    {
        ::close(directory);
        reason = "evidence temporary file creation failed";
        return false;
    }
    const bool wrote = ::fchmod(descriptor, 0600) == 0 &&
        WriteAll(descriptor, contents.data(), contents.size()) &&
        ::fsync(descriptor) == 0;
    const int closeResult = ::close(descriptor);
    if (!wrote || closeResult != 0 ||
        ::linkat(directory, temporary.c_str(), directory, kEvidenceName, 0) != 0)
    {
        ::unlinkat(directory, temporary.c_str(), 0);
        ::close(directory);
        reason = "durable evidence publication failed";
        return false;
    }
    const bool unlinked = ::unlinkat(directory, temporary.c_str(), 0) == 0;
    const bool synced = unlinked && ::fsync(directory) == 0;
    const int directoryClose = ::close(directory);
    if (!synced || directoryClose != 0)
    {
        reason = "evidence directory durability failed";
        return false;
    }
    reason.clear();
    return true;
}

void KillAndReap(pid_t child)
{
    if (child <= 0) return;
    ::kill(child, SIGKILL);
    int status = 0;
    while (::waitpid(child, &status, 0) < 0 && errno == EINTR) {}
}

bool SpawnSigtermIgnoringDescendant(const sigset_t& terminationSignals,
                                    const ProbeContext& context,
                                    pid_t& child, std::string& reason)
{
    int readiness[2];
    if (::pipe(readiness) != 0)
    {
        reason = "descendant readiness pipe failed";
        return false;
    }
    child = ::fork();
    if (child < 0)
    {
        ::close(readiness[0]);
        ::close(readiness[1]);
        reason = "descendant fork failed";
        return false;
    }
    if (child == 0)
    {
        ::close(readiness[0]);
        ::close(context.executionFd);
        ::close(context.eventFd);
        struct sigaction ignore;
        std::memset(&ignore, 0, sizeof(ignore));
        ignore.sa_handler = SIG_IGN;
        ::sigemptyset(&ignore.sa_mask);
        if (::sigaction(SIGTERM, &ignore, nullptr) != 0 ||
            ::pthread_sigmask(SIG_UNBLOCK, &terminationSignals, nullptr) != 0)
            ::_exit(111);
        const char ready = 'R';
        if (!WriteAll(readiness[1], &ready, 1)) ::_exit(112);
        ::close(readiness[1]);
        for (;;) ::pause();
    }

    ::close(readiness[1]);
    char ready = 0;
    ssize_t count = 0;
    do
    {
        count = ::read(readiness[0], &ready, 1);
    } while (count < 0 && errno == EINTR);
    ::close(readiness[0]);
    if (count != 1 || ready != 'R')
    {
        KillAndReap(child);
        child = -1;
        reason = "descendant did not confirm SIGTERM-ignore state";
        return false;
    }
    reason.clear();
    return true;
}
}

int main(int argc, char**)
{
    if (argc != 1)
    {
        std::cerr << "execution_systemd_sandbox_probe: FAIL: accepts no argv\n";
        return 2;
    }

    ProbeContext context;
    std::string reason;
    if (!LoadModeAndPaths(context, reason) ||
        !ValidateIdentities(context, reason))
    {
        std::cerr << "execution_systemd_sandbox_probe: FAIL: " << reason << '\n';
        return 2;
    }
    if (!ValidateActivatedFds(context, reason))
    {
        std::cerr << "execution_systemd_sandbox_probe: FAIL: " << reason << '\n';
        return 3;
    }
    if (!ValidateCredentials(context, reason))
    {
        std::cerr << "execution_systemd_sandbox_probe: FAIL: " << reason << '\n';
        return 4;
    }
    if (!ValidateKillSwitch(context, reason))
    {
        std::cerr << "execution_systemd_sandbox_probe: FAIL: " << reason << '\n';
        return 5;
    }
    if (!ValidateNetwork(context, reason))
    {
        std::cerr << "execution_systemd_sandbox_probe: FAIL: " << reason << '\n';
        return 6;
    }

    sigset_t terminationSignals;
    ::sigemptyset(&terminationSignals);
    ::sigaddset(&terminationSignals, SIGTERM);
    ::sigaddset(&terminationSignals, SIGINT);
    if (::pthread_sigmask(SIG_BLOCK, &terminationSignals, nullptr) != 0)
    {
        std::cerr << "execution_systemd_sandbox_probe: FAIL: signal block failed\n";
        return 7;
    }
    pid_t descendant = -1;
    if (!SpawnSigtermIgnoringDescendant(
            terminationSignals, context, descendant, reason))
    {
        std::cerr << "execution_systemd_sandbox_probe: FAIL: " << reason << '\n';
        return 7;
    }
    if (!WriteEvidence(context, descendant, reason))
    {
        KillAndReap(descendant);
        std::cerr << "execution_systemd_sandbox_probe: FAIL: " << reason << '\n';
        return 8;
    }

    std::cerr << "execution_systemd_sandbox_probe: READY"
              << " mode=" << context.modeName
              << " evidence=" << context.stateDirectory << '/' << kEvidenceName
              << '\n';
    int received = 0;
    if (::sigwait(&terminationSignals, &received) != 0 ||
        (received != SIGTERM && received != SIGINT))
    {
        KillAndReap(descendant);
        std::cerr << "execution_systemd_sandbox_probe: FAIL: signal wait failed\n";
        return 9;
    }

    // Deliberately do not reap or signal the descendant here. The rootful gate
    // verifies that systemd KillMode=control-group removes it after this main
    // process exits, even though the descendant ignores SIGTERM.
    return 0;
}
