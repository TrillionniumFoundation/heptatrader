#pragma once

#include <cstddef>
#include <cstdint>
#include <map>
#include <string>

struct AgentOsRuntimeConfig
{
    std::string toolSocket;
    int toolListenFd = -1;
    std::size_t toolExecutionWorkers = 4;
    std::size_t toolMaxPending = 32;
    std::size_t toolMaxConcurrentPerOwner = 1;
    std::size_t toolMaxPendingPerOwner = 8;
    std::size_t toolIngressWorkers = 2;

    std::string supervisorSocket;
    int supervisorListenFd = -1;
    std::string supervisorLeaseStorePath;
    std::string supervisorLeaseKeyPath;
    std::string supervisorLeaseCleanupLockPath;
    std::uint32_t supervisorLeaseCleanupLockUid = 0;
    std::uint32_t supervisorLeaseCleanupLockGid = 0;
    std::string supervisorAuditJournalPath;
    std::uint32_t supervisorUid = 0;
    std::uint32_t agentUid = 0;
    std::uint64_t supervisorMaxTtlMs = 86400000;
    // A configuration assembled by hand (for example in a unit test) is
    // valid by default.  Values read from the environment are marked
    // invalid, rather than silently replaced or clamped, when a configured
    // numeric field is malformed or outside its contract.
    bool valid = true;
    std::string invalidReason;
    // Never populated from environment. Unit tests that intentionally exercise
    // a server without durable audit must opt in in C++.
    bool allowMissingAuditForTests = false;

    bool ToolServerEnabled() const;
    bool SupervisorEnabled() const;
    bool Validate(std::string& reason) const;

    static AgentOsRuntimeConfig FromEnvironment(int currentPid, std::uint32_t currentUid);
    static AgentOsRuntimeConfig FromValues(const std::map<std::string, std::string>& values,
                                           int currentPid,
                                           std::uint32_t currentUid);
};
