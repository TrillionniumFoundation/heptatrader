#pragma once
#include "../HeptaTrade/execution/send_attempt_time_index.h"
#include <cstdint>
#include <limits>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

namespace hepta_send_index_test {
struct Attempt {
    std::string requestKey, account, executionDomain;
    std::int64_t tsMs;
};
inline void Check(bool value, const char* what) {
    if (!value) throw std::runtime_error(what);
}
inline std::vector<std::int64_t> Reference(const std::vector<Attempt>& history,
        const std::string& account, const std::string& domain, std::int64_t cutoff) {
    std::vector<std::int64_t> result;
    for (const auto& entry : history)
        if (entry.account == account && entry.executionDomain == domain && entry.tsMs > cutoff)
            result.push_back(entry.tsMs);
    return result;
}
inline void ExactBoundariesAndOrder() {
    SendAttemptTimeIndex<Attempt> index;
    const auto low=std::numeric_limits<std::int64_t>::min();
    const auto high=std::numeric_limits<std::int64_t>::max();
    std::vector<Attempt> history = {
        {"a", "account", "domain", 100}, {"b", "account", "domain", 50},
        {"c", "account", "domain", 100}, {"d", "other", "domain", 100},
        {"e", "account", "other", 100}, {"f", "account", "domain", low},
        {"g", "account", "domain", high}};
    std::vector<std::int64_t> out;
    for (const auto& event : history) index.push_back(event);
    for (auto cutoff : {low, std::int64_t(49), std::int64_t(50), std::int64_t(100), high}) {
        index.ReadTimes("account", "domain", cutoff, out);
        Check(out==Reference(history, "account", "domain", cutoff), "exact bound/insertion order");
    }
    index.ReadTimes("missing", "domain", low, out); Check(out.empty(), "missing scope clears output");
    Check(index.size()==history.size(), "all history retained");
    index.clear(); index.ReadTimes("account", "domain", low, out);
    Check(index.size()==0 && out.empty(), "recovery resets both projections");
    for (const auto& event : history) index.push_back(event);
    index.ReadTimes("account", "domain", 50, out);
    Check(out==Reference(history, "account", "domain", 50), "rebuild preserves exact result");
}
inline void ScopePairsCannotCollide() {
    SendAttemptTimeIndex<Attempt> index;
    index.push_back({"a", "a:b", "c", 10});
    index.push_back({"b", "a", "b:c", 20});
    index.push_back({"c", "", "", 30});
    std::vector<std::int64_t> out;
    index.ReadTimes("a:b", "c", 0, out); Check(out==std::vector<std::int64_t>({10}), "scope key 1");
    index.ReadTimes("a", "b:c", 0, out); Check(out==std::vector<std::int64_t>({20}), "scope key 2");
    index.ReadTimes("", "", 0, out); Check(out==std::vector<std::int64_t>({30}), "legacy empty scope");
}
inline void RandomizedReferenceParity() {
    std::mt19937 random(20260913);
    SendAttemptTimeIndex<Attempt> index;
    std::vector<Attempt> history;
    for (int i=0; i<12000; ++i) {
        Attempt event{std::to_string(i), std::to_string(random()%4), std::to_string(random()%3),
            static_cast<std::int64_t>(random()%20001)-10000};
        index.push_back(event); history.push_back(event);
    }
    for (int q=0; q<1000; ++q) {
        const auto account=std::to_string(random()%5), domain=std::to_string(random()%4);
        const auto cutoff=static_cast<std::int64_t>(random()%25001)-12500;
        std::vector<std::int64_t> out;
        index.ReadTimes(account, domain, cutoff, out);
        Check(out==Reference(history, account, domain, cutoff), "random history parity");
    }
    Check(index.size()==history.size(), "queries never discard durable history");
}
inline void Run() { ExactBoundariesAndOrder(); ScopePairsCannotCollide(); RandomizedReferenceParity(); }
} // namespace hepta_send_index_test
