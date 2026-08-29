#pragma once

#include "execution_event_feed_contract.h"

#include <cstddef>
#include <cstdint>
#include <set>
#include <string>

// Read-only client used by the Agent-facing Gateway.
class UnixExecutionEventFeedClient
{
public:
    explicit UnixExecutionEventFeedClient(
        const std::string& socketPath,
        int ioTimeoutMs = 1000,
        std::size_t maxResponseBytes = 32768,
        const std::set<std::uint32_t>& allowedServerUids =
            std::set<std::uint32_t>());

    ExecutionEventReadResult GetServiceIdentity() const;
    ExecutionEventReadResult Wait(const ExecutionEventFeedRequest& request) const;

private:
    ExecutionEventReadResult Call(
        const ExecutionEventFeedRequest& request) const;

    std::string m_socketPath;
    int m_ioTimeoutMs;
    std::size_t m_maxResponseBytes;
    std::set<std::uint32_t> m_allowedServerUids;
};
