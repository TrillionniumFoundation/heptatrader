#pragma once

#include "../execution/execution_event_feed_contract.h"

#include <cstdint>
#include <functional>
#include <string>

struct ExecutionEventRelayOwner
{
    std::string executionDomain;
    std::string agentId;
    std::string sessionId;
    ExecutionServiceIdentity serviceIdentity;
};

struct ExecutionEventRelayCursor
{
    ExecutionServiceIdentity upstreamServiceIdentity;
    std::string upstreamEpoch;
    std::uint64_t upstreamSequence = 0;
    bool authoritativeResyncRequired = false;
};

enum class ExecutionEventRelayStatus
{
    Published = 0,
    Timeout,
    Gap,
    EpochChanged,
    ServiceIdentityChanged,
    ServiceIdentityMismatch,
    ResyncRequired,
    InvalidOwner,
    TransportFailure
};

// Gateway-side relay for the dedicated read-only Execution Service feed. It
// republishes remote events into the local owner-scoped hub while preserving
// upstream epoch/sequence provenance. Gap/epoch notifications request a real
// authoritative refresh; the relay never fabricates a complete snapshot.
class ExecutionEventRelay
{
public:
    typedef std::function<ExecutionEventReadResult(const ExecutionEventFeedRequest&)> Reader;

    ExecutionEventRelay(ExecutionEventHub& localHub, const Reader& reader);

    ExecutionEventRelayStatus Poll(const ExecutionEventRelayOwner& owner,
                                   ExecutionEventRelayCursor& cursor,
                                   int timeoutMs,
                                   std::string& reason);

    bool AcknowledgeAuthoritativeResync(
        ExecutionEventRelayCursor& cursor,
        const ExecutionServiceIdentity& reconciledIdentity);

private:
    std::uint64_t PublishControlEvent(const ExecutionEventRelayOwner& owner,
                                      const std::string& type,
                                      const std::string& reasonCode,
                                      const ExecutionEventReadResult& upstream);

    ExecutionEventHub& m_localHub;
    Reader m_reader;
};
