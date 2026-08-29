#pragma once

#include "execution_authority.h"

#include <chrono>
#include <cstdint>
#include <cstddef>
#include <set>
#include <string>
#include <sys/un.h>

namespace HeptaExecutionServiceInternal {

typedef std::chrono::steady_clock::time_point IoDeadline;

bool ValidIdentity(const ExecutionServiceIdentity& identity);
bool SameIdentity(const ExecutionServiceIdentity& left,
                  const ExecutionServiceIdentity& right);
IoDeadline DeadlineAfter(int timeoutMs);
bool WaitFd(int fd, short events, const IoDeadline& deadline);
bool ReadFrame(int fd,
               std::size_t maxBytes,
               const IoDeadline& deadline,
               std::string& body);
bool WriteFrame(int fd,
                const std::string& body,
                const IoDeadline& deadline);
ExecutionCommandResult TransportFailure(
    const std::string& commandId,
    const std::string& detail);
ExecutionControlResult ControlTransportFailure(
    const std::string& commandId,
    const std::string& detail);
bool BuildAddress(const std::string& socketPath,
                  struct sockaddr_un& address,
                  std::string& reason);
inline bool AllowedServerPeerCredential(
    std::uint32_t peerUid,
    int peerPid,
    const std::set<std::uint32_t>& allowedServerUids)
{
    if (allowedServerUids.find(peerUid) != allowedServerUids.end())
        return true;
    // Root system units use systemd socket activation. The connected Unix
    // endpoint is therefore owned by PID 1 even though the only process that
    // can consume the passed listener is the separately fenced Execution
    // authority. Accept this one explicit activator identity; other root
    // peers remain rejected and protocol epoch/fencing still binds every RPC.
    return peerUid == 0 && peerPid == 1;
}

} // namespace HeptaExecutionServiceInternal
