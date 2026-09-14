#pragma once

#include <algorithm>
#include <cstdint>
#include <map>
#include <string>
#include <utility>
#include <vector>

// In-memory secondary index only. The coordinator still owns synchronization,
// durable identity and deduplication. Neither insertion nor querying expires a
// request or changes the journal. Out-of-order wall clocks are supported.
template <typename Record>
class SendAttemptTimeIndex
{
public:
    void push_back(const Record& record)
    {
        const Scope scope(record.account, record.executionDomain);
        const auto inserted = m_byScope.emplace(scope, Timeline());
        try
        {
            m_records.push_back(record);
        }
        catch (...)
        {
            if (inserted.second) m_byScope.erase(inserted.first);
            throw;
        }
        try
        {
            inserted.first->second.emplace(record.tsMs, m_records.size() - 1);
        }
        catch (...)
        {
            m_records.pop_back();
            if (inserted.second) m_byScope.erase(inserted.first);
            throw;
        }
    }

    void clear()
    {
        m_byScope.clear();
        m_records.clear();
    }

    std::size_t size() const { return m_records.size(); }

    void ReadTimes(const std::string& account, const std::string& domain,
                   std::int64_t cutoffMs, std::vector<std::int64_t>& out) const
    {
        out.clear();
        const auto scope = m_byScope.find(Scope(account, domain));
        if (scope == m_byScope.end()) return;
        const Timeline& timeline = scope->second;
        std::vector<std::size_t> selected;
        for (auto it = timeline.upper_bound(cutoffMs); it != timeline.end(); ++it)
            selected.push_back(it->second);
        // Preserve the old vector API's insertion order, including equal or
        // backwards timestamps. Sorting only the matching window avoids an
        // all-history scan and does not presume a monotonic wall clock.
        std::sort(selected.begin(), selected.end());
        out.reserve(selected.size());
        for (const auto index : selected) out.push_back(m_records[index].tsMs);
    }

private:
    using Scope = std::pair<std::string, std::string>;
    using Timeline = std::multimap<std::int64_t, std::size_t>;
    std::vector<Record> m_records;
    std::map<Scope, Timeline> m_byScope;
};
