#include "unix_execution_service_server.h"
#include "unix_execution_service_internal.h"
#include "execution_decision_lease_authority.h"
#include "execution_service_protocol.h"
#include <arpa/inet.h>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <exception>
#include <fcntl.h>
#include <iomanip>
#include <poll.h>
#include <sstream>
#include <sys/file.h>
#include <sys/socket.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>
namespace HeptaExecutionServiceInternal
{
bool GenerateServiceEpoch(std::string& epoch)
{
    unsigned char bytes[16];
    std::size_t offset = 0;
    while (offset < sizeof(bytes))
    {
        const ssize_t count = ::getrandom(bytes + offset, sizeof(bytes) - offset, 0);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return false;
        offset += static_cast<std::size_t>(count);
    }
    static const char hex[] = "0123456789abcdef";
    epoch = "hexec-v6-";
    for (std::size_t i = 0; i < sizeof(bytes); ++i)
    {
        epoch.push_back(hex[bytes[i] >> 4]);
        epoch.push_back(hex[bytes[i] & 0x0f]);
    }
    return true;
}
long long EpochNowMs()
{
    return static_cast<long long>(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count());
}
void AppendFingerprintField(std::string& out, const std::string& value)
{
    out.append(std::to_string(value.size()));
    out.push_back(':');
    out.append(value);
    out.push_back('\n');
}
template <typename T>
std::string FingerprintNumber(T value)
{
    std::ostringstream output;
    output << std::setprecision(17) << value;
    return output.str();
}
std::string PreviewFingerprint(const PlaceOrderCommand& command)
{
    std::string value;
    AppendFingerprintField(value, command.context.agentId);
    AppendFingerprintField(value, command.context.sessionId);
    AppendFingerprintField(value, command.context.strategy);
    AppendFingerprintField(value, command.context.account);
    AppendFingerprintField(value, command.context.venue);
    AppendFingerprintField(value, command.context.executionDomain);
    AppendFingerprintField(value, command.context.allowCancelAny ? "1" : "0");
    AppendFingerprintField(value, command.instrument);
    AppendFingerprintField(value, command.contract.symbol);
    AppendFingerprintField(value, command.contract.secType);
    AppendFingerprintField(value, command.contract.exchange);
    AppendFingerprintField(value, command.contract.primaryExchange);
    AppendFingerprintField(value, command.contract.currency);
    AppendFingerprintField(value, command.contract.lastTradeDateOrContractMonth);
    AppendFingerprintField(value, command.contract.right);
    AppendFingerprintField(value, FingerprintNumber(command.contract.strike));
    AppendFingerprintField(value, command.contract.multiplier);
    AppendFingerprintField(value, command.contract.tradingClass);
    AppendFingerprintField(value, command.contract.localSymbol);
    AppendFingerprintField(value, command.order.action);
    AppendFingerprintField(value, command.order.orderType);
    AppendFingerprintField(value, FingerprintNumber(command.order.totalQuantity));
    AppendFingerprintField(value, FingerprintNumber(command.order.lmtPrice));
    AppendFingerprintField(value, FingerprintNumber(command.order.auxPrice));
    AppendFingerprintField(value, command.order.outsideRth ? "1" : "0");
    AppendFingerprintField(value, command.order.orderRef);
    AppendFingerprintField(value, command.timeInForce);
    AppendFingerprintField(value, FingerprintNumber(command.referencePrice));
    AppendFingerprintField(value, std::to_string(command.expiresAtMs));
    return value;
}
std::string PreviewOwnerKey(const PlaceOrderCommand& command)
{
    std::string value;
    AppendFingerprintField(value, command.context.agentId);
    AppendFingerprintField(value, command.context.sessionId);
    return value;
}
const std::size_t kMaxPreviewPermits = 128;
const std::size_t kMaxPreviewPermitsPerOwner = 8;
bool GeneratePreviewPermit(std::string& permit)
{
    unsigned char bytes[32];
    std::size_t offset = 0;
    while (offset < sizeof(bytes))
    {
        const ssize_t count = ::getrandom(bytes + offset, sizeof(bytes) - offset, 0);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return false;
        offset += static_cast<std::size_t>(count);
    }
    static const char hex[] = "0123456789abcdef";
    permit = "sha256:";
    for (std::size_t i = 0; i < sizeof(bytes); ++i)
    {
        permit.push_back(hex[bytes[i] >> 4]);
        permit.push_back(hex[bytes[i] & 0x0f]);
    }
    return true;
}
bool GeneratePreviewMutationCommandId(std::string& commandId)
{
    unsigned char bytes[16];
    std::size_t offset = 0;
    while (offset < sizeof(bytes))
    {
        const ssize_t count =
            ::getrandom(bytes + offset, sizeof(bytes) - offset, 0);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return false;
        offset += static_cast<std::size_t>(count);
    }
    static const char hex[] = "0123456789abcdef";
    commandId = "hexec-command-";
    for (std::size_t i = 0; i < sizeof(bytes); ++i)
    {
        commandId.push_back(hex[bytes[i] >> 4]);
        commandId.push_back(hex[bytes[i] & 0x0f]);
    }
    return true;
}
bool ExistingSocketIsStale(const std::string& socketPath,
                           const struct stat& original,
                           std::string& reason)
{
    if (!S_ISSOCK(original.st_mode))
    {
        reason = "EXECUTION_SOCKET_PATH_NOT_SOCKET";
        return false;
    }
    if (original.st_uid != ::geteuid())
    {
        reason = "EXECUTION_SOCKET_PATH_NOT_OWNED";
        return false;
    }
    struct sockaddr_un address;
    if (!BuildAddress(socketPath, address, reason)) return false;
    const int probe = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (probe < 0)
    {
        reason = "EXECUTION_SOCKET_PROBE_FAILED";
        return false;
    }
    const int rc = ::connect(probe, reinterpret_cast<struct sockaddr*>(&address), sizeof(address));
    const int connectError = rc == 0 ? 0 : errno;
    ::close(probe);
    if (rc == 0 || connectError == EINPROGRESS || connectError == EAGAIN ||
        connectError == EALREADY || connectError == EISCONN)
    {
        reason = "EXECUTION_SOCKET_ALREADY_ACTIVE";
        return false;
    }
    if (connectError != ECONNREFUSED)
    {
        reason = std::string("EXECUTION_SOCKET_PROBE_REJECTED:") + std::strerror(connectError);
        return false;
    }
    struct stat current;
    if (::lstat(socketPath.c_str(), &current) != 0 ||
        current.st_dev != original.st_dev || current.st_ino != original.st_ino ||
        !S_ISSOCK(current.st_mode) || current.st_uid != original.st_uid)
    {
        reason = "EXECUTION_SOCKET_PATH_CHANGED_DURING_PROBE";
        return false;
    }
    reason.clear();
    return true;
}
int LockSocketPath(const std::string& socketPath, std::string& reason)
{
    const std::string lockPath = socketPath + ".lock";
    const int lockFd = ::open(lockPath.c_str(), O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (lockFd < 0)
    {
        reason = "EXECUTION_SOCKET_LOCK_OPEN_FAILED";
        return -1;
    }
    struct stat metadata;
    if (::fstat(lockFd, &metadata) != 0 || !S_ISREG(metadata.st_mode) ||
        metadata.st_uid != ::geteuid() || metadata.st_nlink != 1 ||
        (metadata.st_mode & 0077) != 0 || ::flock(lockFd, LOCK_EX | LOCK_NB) != 0)
    {
        ::close(lockFd);
        reason = "EXECUTION_SOCKET_LOCK_UNAVAILABLE";
        return -1;
    }
    reason.clear();
    return lockFd;
}
void UnlockAndClose(int fd)
{
    if (fd < 0) return;
    ::flock(fd, LOCK_UN);
    ::close(fd);
}
void UnlinkSocketIfIdentityMatches(const std::string& socketPath,
                                   std::uint64_t device,
                                   std::uint64_t inode)
{
    struct stat current;
    if (::lstat(socketPath.c_str(), &current) == 0 && S_ISSOCK(current.st_mode) &&
        static_cast<std::uint64_t>(current.st_dev) == device &&
        static_cast<std::uint64_t>(current.st_ino) == inode)
        ::unlink(socketPath.c_str());
}
bool ValidateActivatedSocket(int listenFd, std::string& reason)
{
    if (listenFd < 0 || ::fcntl(listenFd, F_GETFD) < 0)
    {
        reason = "EXECUTION_ACTIVATED_FD_INVALID";
        return false;
    }
    int socketType = 0;
    socklen_t socketTypeLength = sizeof(socketType);
    int accepting = 0;
    socklen_t acceptingLength = sizeof(accepting);
    struct sockaddr_un address;
    socklen_t addressLength = sizeof(address);
    std::memset(&address, 0, sizeof(address));
    if (::getsockopt(listenFd, SOL_SOCKET, SO_TYPE, &socketType, &socketTypeLength) != 0 ||
        socketType != SOCK_STREAM ||
        ::getsockopt(listenFd, SOL_SOCKET, SO_ACCEPTCONN, &accepting, &acceptingLength) != 0 ||
        accepting != 1 ||
        ::getsockname(listenFd, reinterpret_cast<struct sockaddr*>(&address), &addressLength) != 0 ||
        address.sun_family != AF_UNIX)
    {
        reason = "EXECUTION_ACTIVATED_FD_NOT_LISTENING_UNIX_STREAM";
        return false;
    }
    const int flags = ::fcntl(listenFd, F_GETFD);
    if (flags < 0 || ::fcntl(listenFd, F_SETFD, flags | FD_CLOEXEC) != 0)
    {
        reason = "EXECUTION_ACTIVATED_FD_CLOEXEC_FAILED";
        return false;
    }
    reason.clear();
    return true;
}
bool IsControlOperation(ExecutionServiceOperation operation)
{
    return operation != ExecutionServiceOperation::PlaceIbOrder &&
        operation != ExecutionServiceOperation::CancelIbOrder &&
        operation != ExecutionServiceOperation::FlattenPosition &&
        operation != ExecutionServiceOperation::ReadAuthoritativeState &&
        operation != ExecutionServiceOperation::PreviewOrder &&
        operation != ExecutionServiceOperation::PreviewFlattenPosition &&
        operation != ExecutionServiceOperation::GetServiceIdentity;
}
const AgentExecutionContext* RequestContext(const ExecutionServiceRequest& request)
{
    if (IsControlOperation(request.operation)) return &request.control.context;
    switch (request.operation)
    {
    case ExecutionServiceOperation::PlaceIbOrder:
    case ExecutionServiceOperation::PreviewOrder:
        return &request.place.context;
    case ExecutionServiceOperation::CancelIbOrder:
        return &request.cancel.context;
    case ExecutionServiceOperation::FlattenPosition:
    case ExecutionServiceOperation::PreviewFlattenPosition:
        return &request.flatten.context;
    case ExecutionServiceOperation::ReadAuthoritativeState:
        return &request.read.context;
    case ExecutionServiceOperation::GetServiceIdentity:
        return nullptr;
    default: return nullptr;
    }
}
} // namespace HeptaExecutionServiceInternal
using namespace HeptaExecutionServiceInternal;
bool GenerateExecutionServiceIdentity(
    std::uint64_t serviceFencingGeneration,
    ExecutionServiceIdentity& identity,
    std::string& reason)
{
    identity = ExecutionServiceIdentity();
    if (serviceFencingGeneration == 0)
    {
        reason = "EXECUTION_SERVICE_FENCING_GENERATION_INVALID";
        return false;
    }
    if (!GenerateServiceEpoch(identity.serviceEpoch))
    {
        reason = "EXECUTION_SERVICE_EPOCH_GENERATION_FAILED";
        return false;
    }
    identity.serviceFencingGeneration = serviceFencingGeneration;
    reason.clear();
    return true;
}
UnixExecutionServiceServer::UnixExecutionServiceServer(
    ExecutionAuthority& authority,
    ExecutionControlAuthority* controlAuthority,
    const std::shared_ptr<ExecutionDecisionLeaseAuthority>& decisionLeases)
    : m_authority(authority), m_controlAuthority(controlAuthority),
      m_readAuthority(dynamic_cast<ExecutionReadAuthority*>(controlAuthority)),
      m_decisionLeases(decisionLeases ? decisionLeases :
          std::shared_ptr<ExecutionDecisionLeaseAuthority>(
              new ExecutionDecisionLeaseAuthority())), m_stop(true),
      m_listenFd(-1), m_socketDevice(0),
      m_socketInode(0), m_ownsSocketPath(false), m_socketLockFd(-1),
      m_enforceGatewayContextBinding(false), m_maxRequestBytes(32768),
      m_ioTimeoutMs(3000)
{
}
UnixExecutionServiceServer::~UnixExecutionServiceServer()
{
    Stop();
}
bool UnixExecutionServiceServer::IssuePreviewPermit(
    const PlaceOrderCommand& command,
    std::string& permit,
    std::string& mutationCommandId,
    long long& expiresAtMs,
    std::string& reason)
{
    const long long now = EpochNowMs();
    const std::chrono::steady_clock::time_point steadyNow =
        std::chrono::steady_clock::now();
    expiresAtMs = std::min(command.expiresAtMs, now + 5000);
    if (command.expiresAtMs <= 0 || expiresAtMs <= now)
    {
        reason = "EXECUTION_PREVIEW_EXPIRY_INVALID";
        return false;
    }
    if (!GeneratePreviewPermit(permit) ||
        !GeneratePreviewMutationCommandId(mutationCommandId))
    {
        reason = "EXECUTION_PREVIEW_PERMIT_GENERATION_FAILED";
        return false;
    }
    std::lock_guard<std::mutex> lock(m_previewMutex);
    for (std::unordered_map<std::string, PreviewPermitRecord>::iterator it =
             m_previewPermits.begin(); it != m_previewPermits.end();)
    {
        if (it->second.expiresAtMs <= now ||
            it->second.steadyExpiresAt <= steadyNow)
            it = m_previewPermits.erase(it);
        else ++it;
    }
    const std::string fingerprint = PreviewFingerprint(command);
    const std::string ownerKey = PreviewOwnerKey(command);
    std::size_t ownerCount = 0;
    for (std::unordered_map<std::string, PreviewPermitRecord>::iterator it =
             m_previewPermits.begin(); it != m_previewPermits.end();)
    {
        if (it->second.fingerprint == fingerprint)
        {
            // A newer preview of the exact command deterministically revokes
            // the prior credential instead of growing the store.
            it = m_previewPermits.erase(it);
            continue;
        }
        if (it->second.ownerKey == ownerKey) ++ownerCount;
        ++it;
    }
    if (ownerCount >= kMaxPreviewPermitsPerOwner)
    {
        reason = "EXECUTION_PREVIEW_PERMIT_OWNER_CAPACITY_EXCEEDED";
        return false;
    }
    if (m_previewPermits.size() >= kMaxPreviewPermits)
    {
        reason = "EXECUTION_PREVIEW_PERMIT_CAPACITY_EXCEEDED";
        return false;
    }
    PreviewPermitRecord record;
    record.fingerprint = fingerprint;
    record.ownerKey = ownerKey;
    record.mutationCommandId = mutationCommandId;
    record.expiresAtMs = expiresAtMs;
    record.steadyExpiresAt =
        steadyNow + std::chrono::milliseconds(expiresAtMs - now);
    m_previewPermits[permit] = record;
    reason.clear();
    return true;
}
bool UnixExecutionServiceServer::ConsumePreviewPermit(
    const PlaceOrderCommand& command,
    std::string& reason)
{
    const long long now = EpochNowMs();
    const std::chrono::steady_clock::time_point steadyNow =
        std::chrono::steady_clock::now();
    std::lock_guard<std::mutex> lock(m_previewMutex);
    const std::unordered_map<std::string, PreviewPermitRecord>::iterator found =
        m_previewPermits.find(command.previewPermit);
    if (found == m_previewPermits.end())
    {
        reason = "EXECUTION_PREVIEW_PERMIT_UNKNOWN_OR_CONSUMED";
        return false;
    }
    const PreviewPermitRecord record = found->second;
    m_previewPermits.erase(found);
    if (record.expiresAtMs <= now ||
        record.steadyExpiresAt <= steadyNow)
    {
        reason = "EXECUTION_PREVIEW_PERMIT_EXPIRED";
        return false;
    }
    if (record.fingerprint != PreviewFingerprint(command))
    {
        reason = "EXECUTION_PREVIEW_PERMIT_ORDER_MISMATCH";
        return false;
    }
    if (record.mutationCommandId != command.context.toolCallId)
    {
        reason = "EXECUTION_PREVIEW_PERMIT_COMMAND_ID_MISMATCH";
        return false;
    }
    reason.clear();
    return true;
}
void UnixExecutionServiceServer::RevokePreviewPermitsForOwner(
    const std::string& agentId,
    const std::string& sessionId)
{
    PlaceOrderCommand owner;
    owner.context.agentId = agentId;
    owner.context.sessionId = sessionId;
    const std::string ownerKey = PreviewOwnerKey(owner);
    std::lock_guard<std::mutex> lock(m_previewMutex);
    for (std::unordered_map<std::string, PreviewPermitRecord>::iterator it =
             m_previewPermits.begin(); it != m_previewPermits.end();)
    {
        if (it->second.ownerKey == ownerKey)
            it = m_previewPermits.erase(it);
        else
            ++it;
    }
}
bool UnixExecutionServiceServer::Start(const std::string& socketPath,
                                       const std::set<std::uint32_t>& allowedPeerUids,
                                       std::string& reason, std::size_t maxRequestBytes,
                                       int ioTimeoutMs)
{
    std::lock_guard<std::mutex> lifecycleLock(m_lifecycleMutex);
    if (!m_stop.load() || allowedPeerUids.empty() || maxRequestBytes < 1024 || ioTimeoutMs < 1)
    {
        reason = "EXECUTION_SERVER_INVALID_CONFIG";
        return false;
    }
    ExecutionServiceIdentity identity;
    if (!GenerateExecutionServiceIdentity(1, identity, reason)) return false;
    std::shared_ptr<ExecutionServiceLifecycleGate> lifecycleGate(
        new ExecutionServiceLifecycleGate());
    lifecycleGate->ready.store(true);
    struct sockaddr_un address;
    if (!BuildAddress(socketPath, address, reason)) return false;
    const int socketLockFd = LockSocketPath(socketPath, reason);
    if (socketLockFd < 0) return false;
    const int listenFd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (listenFd < 0)
    {
        UnlockAndClose(socketLockFd);
        reason = "EXECUTION_SOCKET_CREATE_FAILED";
        return false;
    }
    struct stat existing;
    if (::lstat(socketPath.c_str(), &existing) == 0)
    {
        if (!ExistingSocketIsStale(socketPath, existing, reason))
        {
            ::close(listenFd);
            UnlockAndClose(socketLockFd);
            return false;
        }
        if (::unlink(socketPath.c_str()) != 0)
        {
            reason = "EXECUTION_STALE_SOCKET_UNLINK_FAILED";
            ::close(listenFd);
            UnlockAndClose(socketLockFd);
            return false;
        }
    }
    else if (errno != ENOENT)
    {
        reason = "EXECUTION_SOCKET_PATH_INSPECTION_FAILED";
        ::close(listenFd);
        UnlockAndClose(socketLockFd);
        return false;
    }
    if (::bind(listenFd, reinterpret_cast<struct sockaddr*>(&address), sizeof(address)) != 0)
    {
        reason = std::string("EXECUTION_SOCKET_START_FAILED:") + std::strerror(errno);
        ::close(listenFd);
        UnlockAndClose(socketLockFd);
        return false;
    }
    struct stat bound;
    if (::lstat(socketPath.c_str(), &bound) != 0 || !S_ISSOCK(bound.st_mode))
    {
        reason = "EXECUTION_BOUND_SOCKET_INSPECTION_FAILED";
        ::close(listenFd);
        UnlockAndClose(socketLockFd);
        return false;
    }
    const std::uint64_t socketDevice = static_cast<std::uint64_t>(bound.st_dev);
    const std::uint64_t socketInode = static_cast<std::uint64_t>(bound.st_ino);
    if (::chmod(socketPath.c_str(), 0600) != 0 || ::listen(listenFd, 16) != 0)
    {
        reason = std::string("EXECUTION_SOCKET_START_FAILED:") + std::strerror(errno);
        ::close(listenFd);
        UnlinkSocketIfIdentityMatches(socketPath, socketDevice, socketInode);
        UnlockAndClose(socketLockFd);
        return false;
    }
    m_socketPath = socketPath;
    m_socketDevice = socketDevice;
    m_socketInode = socketInode;
    m_ownsSocketPath = true;
    m_socketLockFd = socketLockFd;
    m_allowedPeerUids = allowedPeerUids;
    m_maxRequestBytes = maxRequestBytes;
    m_ioTimeoutMs = ioTimeoutMs;
    m_serviceIdentity = identity;
    m_lifecycleGate = lifecycleGate;
    m_listenFd.store(listenFd);
    m_stop.store(false);
    try
    {
        m_acceptThread = std::thread(&UnixExecutionServiceServer::AcceptLoop, this);
    }
    catch (const std::exception& ex)
    {
        m_stop.store(true);
        m_lifecycleGate->ready.store(false);
        m_listenFd.store(-1);
        ::close(listenFd);
        UnlinkSocketIfIdentityMatches(socketPath, m_socketDevice, m_socketInode);
        UnlockAndClose(m_socketLockFd);
        m_socketLockFd = -1;
        m_ownsSocketPath = false;
        reason = std::string("EXECUTION_ACCEPT_THREAD_START_FAILED:") + ex.what();
        return false;
    }
    reason.clear();
    return true;
}
bool UnixExecutionServiceServer::StartFromFd(
    int listenFd,
    const std::set<std::uint32_t>& allowedPeerUids,
    std::string& reason,
    std::size_t maxRequestBytes,
    int ioTimeoutMs)
{
    ExecutionServiceIdentity identity;
    if (!GenerateExecutionServiceIdentity(1, identity, reason))
    {
        if (listenFd >= 0) ::close(listenFd);
        return false;
    }
    return StartFromFd(listenFd, allowedPeerUids, identity, reason,
        maxRequestBytes, ioTimeoutMs);
}
bool UnixExecutionServiceServer::StartFromFd(
    int listenFd,
    const std::set<std::uint32_t>& allowedPeerUids,
    const ExecutionServiceIdentity& identity,
    std::string& reason,
    std::size_t maxRequestBytes,
    int ioTimeoutMs)
{
    std::shared_ptr<ExecutionServiceLifecycleGate> lifecycleGate(
        new ExecutionServiceLifecycleGate());
    lifecycleGate->ready.store(true);
    return StartFromFd(listenFd, allowedPeerUids, identity, lifecycleGate,
        reason, maxRequestBytes, ioTimeoutMs);
}
bool UnixExecutionServiceServer::StartFromFd(
    int listenFd,
    const std::set<std::uint32_t>& allowedPeerUids,
    const ExecutionServiceIdentity& identity,
    const std::shared_ptr<ExecutionServiceLifecycleGate>& lifecycleGate,
    std::string& reason,
    std::size_t maxRequestBytes,
    int ioTimeoutMs)
{
    return StartFromFdInternal(listenFd, allowedPeerUids, nullptr, identity,
        lifecycleGate, reason, maxRequestBytes, ioTimeoutMs);
}
bool UnixExecutionServiceServer::StartFromFd(
    int listenFd,
    const std::set<std::uint32_t>& allowedPeerUids,
    const ExecutionGatewayContextBinding& gatewayContextBinding,
    const ExecutionServiceIdentity& identity,
    const std::shared_ptr<ExecutionServiceLifecycleGate>& lifecycleGate,
    std::string& reason,
    std::size_t maxRequestBytes,
    int ioTimeoutMs)
{
    return StartFromFdInternal(listenFd, allowedPeerUids,
        &gatewayContextBinding, identity, lifecycleGate, reason,
        maxRequestBytes, ioTimeoutMs);
}
bool UnixExecutionServiceServer::StartFromFdInternal(
    int listenFd,
    const std::set<std::uint32_t>& allowedPeerUids,
    const ExecutionGatewayContextBinding* gatewayContextBinding,
    const ExecutionServiceIdentity& identity,
    const std::shared_ptr<ExecutionServiceLifecycleGate>& lifecycleGate,
    std::string& reason,
    std::size_t maxRequestBytes,
    int ioTimeoutMs)
{
    std::lock_guard<std::mutex> lifecycleLock(m_lifecycleMutex);
    if (!m_stop.load() || allowedPeerUids.empty() || maxRequestBytes < 1024 ||
        ioTimeoutMs < 1 || !ValidIdentity(identity) || !lifecycleGate ||
        (gatewayContextBinding != nullptr &&
         !gatewayContextBinding->Complete()))
    {
        if (listenFd >= 0) ::close(listenFd);
        reason = "EXECUTION_SERVER_INVALID_CONFIG";
        return false;
    }
    if (!ValidateActivatedSocket(listenFd, reason))
    {
        if (listenFd >= 0) ::close(listenFd);
        return false;
    }
    m_socketPath.clear();
    m_socketDevice = 0;
    m_socketInode = 0;
    m_ownsSocketPath = false;
    m_socketLockFd = -1;
    m_allowedPeerUids = allowedPeerUids;
    m_gatewayContextBinding = gatewayContextBinding == nullptr ?
        ExecutionGatewayContextBinding() : *gatewayContextBinding;
    m_enforceGatewayContextBinding = gatewayContextBinding != nullptr;
    m_maxRequestBytes = maxRequestBytes;
    m_ioTimeoutMs = ioTimeoutMs;
    m_serviceIdentity = identity;
    m_lifecycleGate = lifecycleGate;
    m_listenFd.store(listenFd);
    m_stop.store(false);
    try
    {
        m_acceptThread = std::thread(&UnixExecutionServiceServer::AcceptLoop, this);
    }
    catch (const std::exception& ex)
    {
        m_stop.store(true);
        m_lifecycleGate->ready.store(false);
        m_listenFd.store(-1);
        ::close(listenFd);
        reason = std::string("EXECUTION_ACCEPT_THREAD_START_FAILED:") + ex.what();
        return false;
    }
    reason.clear();
    return true;
}
void UnixExecutionServiceServer::Stop()
{
    std::lock_guard<std::mutex> lifecycleLock(m_lifecycleMutex);
    if (m_lifecycleGate) m_lifecycleGate->ready.store(false);
    m_stop.store(true);
    if (m_acceptThread.joinable() &&
        m_acceptThread.get_id() == std::this_thread::get_id())
    {
        // A callback may request shutdown, but final join/close/path cleanup
        // must be performed later by the owning lifecycle thread.
        return;
    }
    if (m_acceptThread.joinable()) m_acceptThread.join();
    const int ownedListenFd = m_listenFd.exchange(-1);
    if (ownedListenFd >= 0) ::close(ownedListenFd);
    if (m_ownsSocketPath && !m_socketPath.empty())
        UnlinkSocketIfIdentityMatches(m_socketPath, m_socketDevice, m_socketInode);
    UnlockAndClose(m_socketLockFd);
    m_socketLockFd = -1;
    m_ownsSocketPath = false;
    m_socketDevice = 0;
    m_socketInode = 0;
    m_socketPath.clear();
    m_serviceIdentity = ExecutionServiceIdentity();
    m_gatewayContextBinding = ExecutionGatewayContextBinding();
    m_enforceGatewayContextBinding = false;
    m_lifecycleGate.reset();
    {
        std::lock_guard<std::mutex> previewLock(m_previewMutex);
        m_previewPermits.clear();
    }
}
bool UnixExecutionServiceServer::IsRunning() const
{
    return !m_stop.load() && m_listenFd.load() >= 0 && m_lifecycleGate &&
        (m_lifecycleGate->ready.load() ||
         m_lifecycleGate->terminalControlOnly.load());
}
std::string UnixExecutionServiceServer::ServiceEpoch() const
{
    std::lock_guard<std::mutex> lifecycleLock(m_lifecycleMutex);
    return m_serviceIdentity.serviceEpoch;
}
ExecutionServiceIdentity UnixExecutionServiceServer::ServiceIdentity() const
{
    std::lock_guard<std::mutex> lifecycleLock(m_lifecycleMutex);
    return m_serviceIdentity;
}
void UnixExecutionServiceServer::AcceptLoop()
{
    while (!m_stop.load())
    {
        const int listenFd = m_listenFd.load();
        if (listenFd < 0) break;
        struct pollfd pending;
        pending.fd = listenFd;
        pending.events = POLLIN;
        pending.revents = 0;
        const int pollResult = ::poll(&pending, 1, 100);
        if (pollResult < 0 && errno == EINTR) continue;
        if (pollResult <= 0) continue;
        if ((pending.revents & POLLIN) == 0)
        {
            if (m_stop.load()) break;
            continue;
        }
        const int clientFd = ::accept4(
            listenFd, nullptr, nullptr, SOCK_CLOEXEC | SOCK_NONBLOCK);
        if (clientFd < 0)
        {
            if (errno == EINTR) continue;
            if (m_stop.load() || errno == EBADF || errno == EINVAL) break;
            continue;
        }
        HandleClient(clientFd);
        ::close(clientFd);
    }
}
bool UnixExecutionServiceServer::ReadAuthorizedRequest(
    int clientFd,
    const std::chrono::steady_clock::time_point& deadline,
    ExecutionServiceRequest& request,
    std::string& reason)
{
    struct ucred credential;
    socklen_t credentialLength = sizeof(credential);
    if (::getsockopt(clientFd, SOL_SOCKET, SO_PEERCRED, &credential, &credentialLength) != 0 ||
        credentialLength != sizeof(credential) ||
        m_allowedPeerUids.find(static_cast<std::uint32_t>(credential.uid)) == m_allowedPeerUids.end())
        return false;
    std::string requestBody;
    if (!ReadFrame(clientFd, m_maxRequestBytes, deadline, requestBody))
        return false;
    return ExecutionServiceProtocol::DecodeRequest(requestBody, request, reason);
}
namespace
{
void RejectExecutionRequest(const ExecutionServiceRequest& request,
                            const std::string& reasonCode,
                            ExecutionCommandResult& result,
                            ExecutionControlResult& controlResult,
                            bool& controlResponse)
{
    controlResponse = IsControlOperation(request.operation);
    const AgentExecutionContext* context = RequestContext(request);
    const std::string commandId = context == nullptr ?
        std::string() : context->toolCallId;
    if (controlResponse)
    {
        controlResult.status = ExecutionCommandStatus::Rejected;
        controlResult.commandId = commandId;
        controlResult.targetCommandId = request.control.targetCommandId;
        controlResult.reasonCode = reasonCode;
    }
    else
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.commandId = commandId;
        result.reasonCode = reasonCode;
    }
}
}
bool UnixExecutionServiceServer::ApplyPreDispatchGate(
    const ExecutionServiceRequest& request,
    ExecutionCommandResult& result,
    ExecutionControlResult& controlResult,
    bool& controlResponse)
{
    if (request.operation == ExecutionServiceOperation::GetServiceIdentity)
    {
        result.commandId = "__service_identity__";
        if (!m_lifecycleGate ||
            (!m_lifecycleGate->ready.load() &&
             !m_lifecycleGate->terminalControlOnly.load()))
        {
            result.status = ExecutionCommandStatus::Rejected;
            result.reasonCode = "EXECUTION_SERVICE_NOT_READY";
        }
        else
        {
            result.status = ExecutionCommandStatus::Accepted;
            result.reasonCode = "EXECUTION_SERVICE_IDENTITY";
        }
        return false;
    }
    if (m_enforceGatewayContextBinding &&
        (RequestContext(request) == nullptr ||
         !m_gatewayContextBinding.Matches(*RequestContext(request))))
    {
        RejectExecutionRequest(request,
            "EXECUTION_GATEWAY_CONTEXT_BINDING_MISMATCH",
            result, controlResult, controlResponse);
        return false;
    }
    if (request.expectedServiceEpoch != m_serviceIdentity.serviceEpoch ||
        request.expectedServiceFencingGeneration !=
            m_serviceIdentity.serviceFencingGeneration)
    {
        RejectExecutionRequest(request, "EXECUTION_SERVICE_EPOCH_MISMATCH",
            result, controlResult, controlResponse);
        return false;
    }
    if (!m_lifecycleGate ||
        (!m_lifecycleGate->ready.load() &&
         !(m_lifecycleGate->terminalControlOnly.load() &&
           request.operation ==
               ExecutionServiceOperation::TerminalizeRecoveryOwner)))
    {
        RejectExecutionRequest(request, "EXECUTION_SERVICE_NOT_READY",
            result, controlResult, controlResponse);
        return false;
    }
    return true;
}
ExecutionCommandResult UnixExecutionServiceServer::DispatchPlaceOrder(
    const IbPlaceOrderCommand& command)
{
    IbPlaceOrderCommand authorized = command;
    std::string permitReason;
    const bool durablePlaceReplay = m_readAuthority != nullptr &&
        m_readAuthority->IsDurablePlaceReplay(authorized);
    if (durablePlaceReplay)
    {
        authorized.previewPermit.clear();
        return m_authority.PlaceOrder(authorized);
    }
    ExecutionCommandResult result;
    if (!ConsumePreviewPermit(authorized, permitReason))
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.commandId = command.context.toolCallId;
        result.reasonCode = permitReason;
        result.detail =
            "Execution Service rejected the missing, expired, replayed, or mismatched preview permit";
        return result;
    }
    const std::string instrument = authorized.instrument.empty() ?
        authorized.contract.symbol : authorized.instrument;
    std::string leaseReason;
    if (!m_decisionLeases->Authorize(authorized.context, instrument, leaseReason))
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.commandId = command.context.toolCallId;
        result.reasonCode = leaseReason;
        result.detail = "Execution Service could not grant the mutation lease";
        return result;
    }
    authorized.previewPermit.clear();
    return m_authority.PlaceOrder(authorized);
}
ExecutionCommandResult UnixExecutionServiceServer::DispatchPreviewOrder(
    const IbPlaceOrderCommand& command)
{
    ExecutionCommandResult result;
    if (m_readAuthority == nullptr)
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.commandId = command.context.toolCallId;
        result.reasonCode = "EXECUTION_PREVIEW_UNAVAILABLE";
        return result;
    }
    result = m_readAuthority->PreviewOrder(command);
    if (result.status != ExecutionCommandStatus::Accepted) return result;
    std::string permit;
    std::string mutationCommandId;
    long long permitExpiry = 0;
    std::string permitReason;
    if (!IssuePreviewPermit(command, permit, mutationCommandId,
                            permitExpiry, permitReason))
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = permitReason;
        result.detail.clear();
        return result;
    }
    const std::string authoritative = result.detail.empty() ? "null" : result.detail;
    std::ostringstream payload;
    payload << "{\"approved\":true,\"preview_permit\":\"" << permit
            << "\",\"command_id\":\"" << mutationCommandId
            << "\",\"permit_expires_at_ms\":" << permitExpiry
            << ",\"single_use\":true,\"service_epoch\":\""
            << m_serviceIdentity.serviceEpoch
            << "\",\"service_fencing_generation\":"
            << m_serviceIdentity.serviceFencingGeneration
            << ",\"authoritative_preview\":" << authoritative << '}';
    result.detail = payload.str();
    return result;
}
ExecutionControlResult UnixExecutionServiceServer::DispatchControl(
    const ExecutionServiceRequest& request)
{
    ExecutionControlResult result;
    if (m_controlAuthority == nullptr)
    {
        result.status = ExecutionCommandStatus::Rejected;
        result.commandId = request.control.context.toolCallId;
        result.reasonCode = "EXECUTION_CONTROL_UNAVAILABLE";
        return result;
    }
    if (request.operation == ExecutionServiceOperation::QueryCommandStatus)
        return m_controlAuthority->QueryCommandStatus(request.control);
    if (request.operation ==
        ExecutionServiceOperation::RecoveryQueryCommandStatus)
    {
        RevokePreviewPermitsForOwner(request.control.context.agentId,
            request.control.context.sessionId);
        return m_controlAuthority->QueryCommandStatus(request.control);
    }
    if (request.operation == ExecutionServiceOperation::FenceSessionOwner)
    {
        RevokePreviewPermitsForOwner(request.control.context.agentId,
                                     request.control.context.sessionId);
        m_decisionLeases->FenceOwner(request.control.context.agentId,
                                     request.control.context.sessionId);
        return m_controlAuthority->FenceSessionOwner(request.control);
    }
    if (request.operation == ExecutionServiceOperation::ReleaseSessionOwnerFence)
        return m_controlAuthority->ReleaseSessionOwnerFence(request.control);
    if (request.operation == ExecutionServiceOperation::RecoveryAuditOwner)
    {
        RevokePreviewPermitsForOwner(request.control.context.agentId,
            request.control.context.sessionId);
        m_decisionLeases->FenceOwner(request.control.context.agentId,
            request.control.context.sessionId);
        return m_controlAuthority->RecoveryAuditOwner(request.control);
    }
    if (request.operation ==
        ExecutionServiceOperation::TerminalizeRecoveryOwner)
    {
        RevokePreviewPermitsForOwner(request.control.context.agentId,
            request.control.context.sessionId);
        m_decisionLeases->FenceOwner(request.control.context.agentId,
            request.control.context.sessionId);
        return m_controlAuthority->TerminalizeRecoveryOwner(
            request.control);
    }
    return m_controlAuthority->ReconcileAuthoritativeState(request.control);
}
void UnixExecutionServiceServer::DispatchRequest(
    const ExecutionServiceRequest& request,
    ExecutionCommandResult& result,
    ExecutionControlResult& controlResult,
    bool& controlResponse)
{
    if (request.operation == ExecutionServiceOperation::PlaceIbOrder)
        result = DispatchPlaceOrder(request.place);
    else if (request.operation == ExecutionServiceOperation::CancelIbOrder)
        result = m_authority.CancelOrder(request.cancel);
    else if (request.operation ==
             ExecutionServiceOperation::FlattenPosition)
        result = DispatchFlattenPosition(request.flatten);
    else if (request.operation == ExecutionServiceOperation::ReadAuthoritativeState)
    {
        if (m_readAuthority == nullptr)
        {
            result.status = ExecutionCommandStatus::Rejected;
            result.commandId = request.read.context.toolCallId;
            result.reasonCode = "EXECUTION_READ_UNAVAILABLE";
        }
        else
            result = m_readAuthority->ReadAuthoritativeState(request.read);
    }
    else if (request.operation == ExecutionServiceOperation::PreviewOrder)
        result = DispatchPreviewOrder(request.place);
    else if (request.operation == ExecutionServiceOperation::PreviewFlattenPosition)
        result = DispatchFlattenPreview(request.flatten);
    else
    {
        controlResponse = true;
        controlResult = DispatchControl(request);
    }
}
void UnixExecutionServiceServer::ValidateAndBindResponse(
    const ExecutionServiceRequest& request,
    ExecutionCommandResult& result,
    ExecutionControlResult& controlResult,
    bool controlResponse) const
{
    const std::string expectedCommandId =
        request.operation == ExecutionServiceOperation::GetServiceIdentity ?
            "__service_identity__" : (controlResponse ?
        request.control.context.toolCallId :
        (RequestContext(request) == nullptr ? std::string() :
            RequestContext(request)->toolCallId));
    if ((!controlResponse && result.commandId != expectedCommandId) ||
        (controlResponse && controlResult.commandId != expectedCommandId))
    {
        if (controlResponse)
            controlResult = ControlTransportFailure(expectedCommandId,
                "EXECUTION_AUTHORITY_RESPONSE_COMMAND_ID_MISMATCH");
        else
            result = TransportFailure(expectedCommandId,
                "EXECUTION_AUTHORITY_RESPONSE_COMMAND_ID_MISMATCH");
    }
    result.serviceEpoch = m_serviceIdentity.serviceEpoch;
    result.serviceFencingGeneration = m_serviceIdentity.serviceFencingGeneration;
    controlResult.serviceEpoch = m_serviceIdentity.serviceEpoch;
    controlResult.serviceFencingGeneration =
        m_serviceIdentity.serviceFencingGeneration;
}
void UnixExecutionServiceServer::HandleClient(int clientFd)
{
    const IoDeadline deadline = DeadlineAfter(m_ioTimeoutMs);
    ExecutionServiceRequest request;
    std::string reason;
    ExecutionCommandResult result;
    ExecutionControlResult controlResult;
    bool controlResponse = false;
    if (!ReadAuthorizedRequest(clientFd, deadline, request, reason))
    {
        if (reason.empty()) return;
        result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = reason;
        result.detail =
            "Execution IPC request was rejected before authority dispatch";
    }
    else
    {
        if (ApplyPreDispatchGate(request, result, controlResult, controlResponse))
            DispatchRequest(request, result, controlResult, controlResponse);
        ValidateAndBindResponse(request, result, controlResult, controlResponse);
    }
    std::string responseBody;
    const bool encoded = controlResponse ?
        ExecutionServiceProtocol::EncodeControlResponse(controlResult, responseBody, reason) :
        ExecutionServiceProtocol::EncodeResponse(result, responseBody, reason);
    if (encoded)
        WriteFrame(clientFd, responseBody, deadline);
}
