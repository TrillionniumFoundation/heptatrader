#pragma once

#include <algorithm>
#include <cstdint>
#include <iomanip>
#include <limits>
#include <set>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

struct MultiAgentScenarioEvent
{
    std::uint64_t atMs = 0;
    std::uint64_t sequence = 0;
    std::string targetModule;
    std::string eventType;
    std::string payloadDigest;
};

struct MultiAgentScenarioSnapshot
{
    std::string scenarioDigest;
    std::uint64_t clockMs = 0;
    std::size_t nextEvent = 0;
};

struct MultiAgentScenarioResult
{
    bool accepted = false;
    std::string reasonCode;
    std::string scenarioDigest;
    MultiAgentScenarioSnapshot snapshot;
    std::vector<MultiAgentScenarioEvent> emitted;
};

// Deterministic single-owner virtual-clock scenario runner. It supplies
// reproducible scheduling and restart snapshots; it deliberately models
// neither exchange microstructure nor stochastic fills.
class MultiAgentAllocationScenario
{
public:
    explicit MultiAgentAllocationScenario(std::uint64_t startMs = 1,
                                          std::size_t maximumEvents = 4096)
        : m_startMs(startMs), m_clockMs(startMs), m_maximumEvents(maximumEvents)
    {
    }

    static const char* Version() noexcept
    {
        return "hepta.multi-agent-allocation-scenario.v1";
    }

    MultiAgentScenarioResult Add(const MultiAgentScenarioEvent& event)
    {
        if (m_sealed) return Reject("SCENARIO_ALREADY_SEALED");
        if (!ValidEvent(event) || event.atMs < m_startMs)
            return Reject("SCENARIO_EVENT_INVALID");
        if (m_events.size() >= m_maximumEvents)
            return Reject("SCENARIO_EVENT_LIMIT");
        m_events.push_back(event);
        return Accept("SCENARIO_EVENT_ADDED");
    }

    MultiAgentScenarioResult Seal()
    {
        if (m_sealed)
        {
            MultiAgentScenarioResult duplicate = Accept("SCENARIO_SEAL_DUPLICATE");
            duplicate.scenarioDigest = m_digest;
            duplicate.snapshot = Snapshot();
            return duplicate;
        }
        std::sort(m_events.begin(), m_events.end(), EventLess);
        std::set<std::pair<std::uint64_t, std::uint64_t>> orderKeys;
        for (const MultiAgentScenarioEvent& event : m_events)
        {
            if (!orderKeys.emplace(event.atMs, event.sequence).second)
                return Reject("SCENARIO_ORDER_KEY_DUPLICATE");
        }
        m_digest = Digest(m_events, m_startMs);
        if (m_digest.empty()) return Reject("SCENARIO_DIGEST_FAILED");
        m_sealed = true;
        MultiAgentScenarioResult result = Accept("SCENARIO_SEALED");
        result.scenarioDigest = m_digest;
        result.snapshot = Snapshot();
        return result;
    }

    MultiAgentScenarioResult AdvanceTo(std::uint64_t targetMs)
    {
        if (!m_sealed) return Reject("SCENARIO_NOT_SEALED");
        if (targetMs < m_clockMs) return Reject("SCENARIO_TIME_REGRESSION");

        MultiAgentScenarioResult result = Accept("SCENARIO_ADVANCED");
        while (m_nextEvent < m_events.size() &&
               m_events[m_nextEvent].atMs <= targetMs)
        {
            result.emitted.push_back(m_events[m_nextEvent]);
            ++m_nextEvent;
        }
        m_clockMs = targetMs;
        result.scenarioDigest = m_digest;
        result.snapshot = Snapshot();
        return result;
    }

    MultiAgentScenarioResult Restore(const MultiAgentScenarioSnapshot& snapshot)
    {
        if (!m_sealed) return Reject("SCENARIO_NOT_SEALED");
        if (snapshot.scenarioDigest != m_digest ||
            snapshot.clockMs < m_startMs ||
            snapshot.nextEvent > m_events.size())
            return Reject("SCENARIO_SNAPSHOT_INVALID");
        for (std::size_t i = 0; i < snapshot.nextEvent; ++i)
        {
            if (m_events[i].atMs > snapshot.clockMs)
                return Reject("SCENARIO_SNAPSHOT_CURSOR_INVALID");
        }
        if (snapshot.nextEvent < m_events.size() &&
            m_events[snapshot.nextEvent].atMs <= snapshot.clockMs)
            return Reject("SCENARIO_SNAPSHOT_CURSOR_INVALID");
        m_clockMs = snapshot.clockMs;
        m_nextEvent = snapshot.nextEvent;
        MultiAgentScenarioResult result = Accept("SCENARIO_RESTORED");
        result.scenarioDigest = m_digest;
        result.snapshot = Snapshot();
        return result;
    }

    MultiAgentScenarioSnapshot Snapshot() const
    {
        MultiAgentScenarioSnapshot snapshot;
        snapshot.scenarioDigest = m_digest;
        snapshot.clockMs = m_clockMs;
        snapshot.nextEvent = m_nextEvent;
        return snapshot;
    }

    const std::vector<MultiAgentScenarioEvent>& Events() const noexcept
    {
        return m_events;
    }

private:
    static bool CanonicalId(const std::string& value, std::size_t maximum)
    {
        if (value.empty() || value.size() > maximum) return false;
        for (unsigned char c : value)
        {
            const bool alnum = (c >= 'A' && c <= 'Z') ||
                (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9');
            if (!(alnum || c == '-' || c == '_' || c == '.' || c == ':'))
                return false;
        }
        return true;
    }

    static bool CanonicalDigest(const std::string& value)
    {
        if (value.size() != 71 || value.compare(0, 7, "sha256:") != 0)
            return false;
        for (std::size_t i = 7; i < value.size(); ++i)
        {
            const char c = value[i];
            if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')))
                return false;
        }
        return true;
    }

    static bool ValidEvent(const MultiAgentScenarioEvent& event)
    {
        return event.atMs != 0 && event.sequence != 0 &&
            CanonicalId(event.targetModule, 128) &&
            event.targetModule.compare(0, 6, "hepta.") == 0 &&
            CanonicalId(event.eventType, 64) &&
            CanonicalDigest(event.payloadDigest);
    }

    static bool EventLess(const MultiAgentScenarioEvent& left,
                          const MultiAgentScenarioEvent& right)
    {
        if (left.atMs != right.atMs) return left.atMs < right.atMs;
        if (left.sequence != right.sequence)
            return left.sequence < right.sequence;
        if (left.targetModule != right.targetModule)
            return left.targetModule < right.targetModule;
        if (left.eventType != right.eventType)
            return left.eventType < right.eventType;
        return left.payloadDigest < right.payloadDigest;
    }

    static std::uint64_t Fnv1a(const std::string& value)
    {
        std::uint64_t hash = 1469598103934665603ULL;
        for (unsigned char c : value)
        {
            hash ^= static_cast<std::uint64_t>(c);
            hash *= 1099511628211ULL;
        }
        return hash;
    }

    static void Append(std::string& output, const std::string& value)
    {
        output.append(std::to_string(value.size()));
        output.push_back(':');
        output.append(value);
        output.push_back(';');
    }

    static std::string Digest(
        const std::vector<MultiAgentScenarioEvent>& events,
        std::uint64_t startMs)
    {
        std::string canonical = Version();
        canonical.push_back(';');
        canonical.append(std::to_string(startMs));
        canonical.push_back(';');
        for (const MultiAgentScenarioEvent& event : events)
        {
            canonical.append(std::to_string(event.atMs));
            canonical.push_back(';');
            canonical.append(std::to_string(event.sequence));
            canonical.push_back(';');
            Append(canonical, event.targetModule);
            Append(canonical, event.eventType);
            Append(canonical, event.payloadDigest);
        }
        std::ostringstream output;
        output << "fnv1a64:" << std::hex << std::setfill('0')
               << std::setw(16) << Fnv1a(canonical);
        return output.str();
    }

    MultiAgentScenarioResult Accept(const char* code) const
    {
        MultiAgentScenarioResult result;
        result.accepted = true;
        result.reasonCode = code;
        return result;
    }

    MultiAgentScenarioResult Reject(const char* code) const
    {
        MultiAgentScenarioResult result;
        result.reasonCode = code;
        return result;
    }

    std::uint64_t m_startMs;
    std::uint64_t m_clockMs;
    std::size_t m_maximumEvents;
    std::size_t m_nextEvent = 0;
    bool m_sealed = false;
    std::string m_digest;
    std::vector<MultiAgentScenarioEvent> m_events;
};
