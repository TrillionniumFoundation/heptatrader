#pragma once

#include "../execution/execution_authority.h"

#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <mutex>
#include <string>
#include <unordered_map>

struct ExecutionEvent
{
    std::string streamEpoch;
    std::uint64_t sequence = 0;
    std::string upstreamServiceEpoch;
    std::uint64_t upstreamServiceFencingGeneration = 0;
    std::string upstreamStreamEpoch;
    std::uint64_t upstreamSequence = 0;
    std::uint64_t timestampMs = 0;
    std::string executionDomain;
    std::string agentId;
    std::string sessionId;
    std::string type;
    std::string venue;
    long orderId = -1;
    std::string instrument;
    std::string side;
    std::string status;
    std::string reasonCode;
    double filledQuantity = 0.0;
    double remainingQuantity = 0.0;
    double averageFillPrice = 0.0;
};

enum class ExecutionEventReadStatus
{
    Event = 0,
    Timeout,
    Gap,
    EpochChanged,
    InvalidOwner,
    ServiceIdentity,
    ServiceIdentityMismatch,
    ServiceNotReady,
    ServiceStopping
};

struct ExecutionEventReadResult
{
    ExecutionEventReadStatus status = ExecutionEventReadStatus::Timeout;
    ExecutionServiceIdentity serviceIdentity;
    std::string streamEpoch;
    std::uint64_t droppedThroughSequence = 0;
    std::uint64_t latestSequence = 0;
    std::string reasonCode;
    ExecutionEvent event;
};

// Narrow source seam used by the feed server. Tests can provide a counting
// implementation and prove that identity/lifecycle rejection happens before
// any source read.
class ExecutionEventFeedSource
{
public:
    virtual ~ExecutionEventFeedSource() = default;
    virtual ExecutionEventReadResult ReadNext(
        const std::string& executionDomain,
        const std::string& agentId,
        const std::string& sessionId,
        const std::string& expectedEpoch,
        std::uint64_t afterSequence,
        int timeoutMs) = 0;
    virtual const std::string& StreamEpoch() const = 0;
};

// Bounded, owner-routed event channel used by the Agent-facing events.wait tool.
// Broker callbacks publish once; Agents consume only events addressed to their identity.
class ExecutionEventHub : public ExecutionEventFeedSource
{
public:
    explicit ExecutionEventHub(std::size_t capacityPerAgent = 1024,
                               const std::string& streamEpoch = std::string());

    std::uint64_t Publish(ExecutionEvent event);
    bool WaitNext(const std::string& executionDomain,
                  const std::string& agentId,
                  const std::string& sessionId,
                  std::uint64_t afterSequence,
                  int timeoutMs,
                  ExecutionEvent& out);
    std::size_t Pending(const std::string& executionDomain,
                        const std::string& agentId,
                        const std::string& sessionId,
                        std::uint64_t afterSequence) const;
    ExecutionEventReadResult ReadNext(const std::string& executionDomain,
                                      const std::string& agentId,
                                      const std::string& sessionId,
                                      const std::string& expectedEpoch,
                                      std::uint64_t afterSequence,
                                      int timeoutMs) override;
    const std::string& StreamEpoch() const override;

    static std::string ToJson(const ExecutionEvent& event);

private:
    static std::string QueueKey(const std::string& executionDomain,
                                const std::string& agentId,
                                const std::string& sessionId);
    bool FindNextLocked(const std::string& queueKey,
                        std::uint64_t afterSequence,
                        ExecutionEvent& out) const;

private:
    const std::size_t m_capacityPerAgent;
    const std::string m_streamEpoch;
    mutable std::mutex m_mutex;
    std::condition_variable m_changed;
    std::uint64_t m_nextSequence;
    std::unordered_map<std::string, std::deque<ExecutionEvent> > m_queues;
    std::unordered_map<std::string, std::uint64_t> m_droppedThrough;
};
