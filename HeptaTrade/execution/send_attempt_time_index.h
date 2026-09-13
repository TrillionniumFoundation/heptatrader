#pragma once

#include <cstddef>
#include <cstdint>
#include <map>
#include <set>
#include <string>
#include <unordered_set>
#include <utility>
#include <vector>

// A rebuildable projection, synchronized by the owning coordinator. It is not
// a second mutation authority. Keep every first-seen identity and timestamp:
// optimizing a rolling-rate query must never expire durable command history.
class SendAttemptTimeIndex
{
public:
    bool Record(const std::string& requestKey, const std::string& account,
                const std::string& executionDomain, std::int64_t timestampMs)
    {
        const auto identity = m_requestKeys.insert(requestKey);
        if (!identity.second) return false;
        auto scope = m_times.end();
        bool newScope = false;
        try
        {
            const auto inserted = m_times.emplace(
                std::make_pair(account, executionDomain), Times());
            scope = inserted.first;
            newScope = inserted.second;
            scope->second.insert(timestampMs);
        }
        catch (...)
        {
            if (newScope && scope != m_times.end()) m_times.erase(scope);
            m_requestKeys.erase(identity.first);
            throw;
        }
        return true;
    }

    // O(log(scopes) + log(attempts in scope) + returned timestamps), rather
    // than scanning all accounts and all historical attempts under one lock.
    // upper_bound also handles INT64_MAX without cutoff+1 overflow. A multiset
    // preserves distinct sends with equal times and tolerates clock rollback.
    void Query(const std::string& account, const std::string& executionDomain,
               std::int64_t cutoffMs, std::vector<std::int64_t>& out) const
    {
        out.clear();
        const auto scope = m_times.find(std::make_pair(account, executionDomain));
        if (scope == m_times.end()) return;
        out.assign(scope->second.upper_bound(cutoffMs), scope->second.end());
    }

    void Clear()
    {
        m_times.clear();
        m_requestKeys.clear();
    }

    std::size_t Size() const { return m_requestKeys.size(); }

private:
    typedef std::multiset<std::int64_t> Times;
    std::map<std::pair<std::string, std::string>, Times> m_times;
    std::unordered_set<std::string> m_requestKeys;
};
