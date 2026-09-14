#pragma once

#include <algorithm>
#include <cstdint>
#include <limits>
#include <map>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

// Secondary time projection only. The coordinator and journal, not this
// index, own durable command identities and deduplication. Never expire a send.
template <typename Record>
class SendAttemptTimeIndex
{
public:
    void push_back(const Record& record)
    {
        if (m_size == std::numeric_limits<std::size_t>::max())
            throw std::overflow_error("send-attempt index size overflow");
        const Scope scope(record.account, record.executionDomain);
        const auto inserted = m_byScope.emplace(scope, Timeline());
        try
        {
            // Store time and insertion ordinal once; copying the whole Record
            // would duplicate request keys and scope strings for every send.
            inserted.first->second.emplace(record.tsMs, m_size);
        }
        catch (...)
        {
            if (inserted.second) m_byScope.erase(inserted.first);
            throw;
        }
        ++m_size;
    }

    void clear()
    {
        m_byScope.clear();
        m_size = 0;
    }

    std::size_t size() const { return m_size; }

    void ReadTimes(const std::string& account, const std::string& domain,
                   std::int64_t cutoffMs, std::vector<std::int64_t>& out) const
    {
        out.clear();
        const auto scope = m_byScope.find(Scope(account, domain));
        if (scope == m_byScope.end()) return;
        const Timeline& timeline = scope->second;
        std::vector<std::pair<std::size_t, std::int64_t>> selected;
        for (auto it = timeline.upper_bound(cutoffMs); it != timeline.end(); ++it)
            selected.emplace_back(it->second, it->first);
        // Preserve insertion order under equal/backwards wall clocks. Only the
        // matching window is sorted; queries never delete historical entries.
        std::sort(selected.begin(), selected.end());
        out.reserve(selected.size());
        for (const auto& entry : selected) out.push_back(entry.second);
    }

private:
    using Scope = std::pair<std::string, std::string>;
    using Timeline = std::multimap<std::int64_t, std::size_t>;
    std::map<Scope, Timeline> m_byScope;
    std::size_t m_size = 0;
};
