#include "../HeptaTrade/execution/send_attempt_time_index.h"
#include "../HeptaTrade/execution/venue_submission_result.h"

#include <algorithm>
#include <chrono>
#include <iostream>
#include <limits>
#include <stdexcept>

namespace
{
void Require(bool value, const char* message)
{
    if (!value) throw std::runtime_error(message);
}

struct Attempt
{
    std::string key, account, domain;
    std::int64_t timestamp;
};

std::vector<std::int64_t> Reference(const std::vector<Attempt>& attempts,
                                   const std::string& account,
                                   const std::string& domain,
                                   std::int64_t cutoff)
{
    std::unordered_set<std::string> seen;
    std::vector<std::int64_t> result;
    for (const auto& attempt : attempts)
        if (seen.insert(attempt.key).second && attempt.account == account &&
            attempt.domain == domain && attempt.timestamp > cutoff)
            result.push_back(attempt.timestamp);
    std::sort(result.begin(), result.end());
    return result;
}

void Correctness()
{
    SendAttemptTimeIndex index;
    std::vector<std::int64_t> result(1, 99);
    index.Query("missing", "domain", 0, result);
    Require(result.empty(), "missing scope must clear reused output");
    Require(index.Record("one", "a", "bc", 10), "first identity");
    Require(index.Record("two", "a", "bc", 10), "equal timestamp, distinct identity");
    Require(index.Record("three", "ab", "c", 11), "scope must not concatenate ambiguously");
    Require(!index.Record("one", "other", "other", 999), "first-seen identity is not replaced");
    index.Query("a", "bc", 9, result);
    Require(result == std::vector<std::int64_t>({10, 10}), "equal times preserve send count");
    index.Query("a", "bc", 10, result);
    Require(result.empty(), "cutoff is exclusive");
    Require(index.Size() == 3, "duplicate does not increase retained identity count");
    Require(index.Record("clock-back", "a", "bc", -5), "out-of-order time");
    Require(index.Record("max", "a", "bc", std::numeric_limits<std::int64_t>::max()), "max time");
    index.Query("a", "bc", std::numeric_limits<std::int64_t>::max(), result);
    Require(result.empty(), "maximum cutoff must not overflow");
    index.Query("a", "bc", std::numeric_limits<std::int64_t>::min(), result);
    Require(result.size() == 4 && result.front() == -5, "clock rollback preserves all history");
    index.Clear();
    Require(index.Size() == 0, "recovery clears projection");
    std::vector<Attempt> attempts;
    for (std::size_t i = 0; i < 4096; ++i)
    {
        Attempt attempt;
        attempt.key = "request-" + std::to_string(i % 3001);
        attempt.account = "account-" + std::to_string(i % 7);
        attempt.domain = "domain-" + std::to_string(i % 5);
        attempt.timestamp = static_cast<std::int64_t>((i * 7919) % 100003) - 40000;
        attempts.push_back(attempt);
        index.Record(attempt.key, attempt.account, attempt.domain, attempt.timestamp);
    }
    Require(index.Size() == 3001, "all first identities survive unrelated queries");
    for (int a = 0; a < 8; ++a)
        for (int d = 0; d < 6; ++d)
            for (const std::int64_t cutoff : {-40001LL, -1LL, 0LL, 20000LL, 60003LL})
            {
                const auto account = "account-" + std::to_string(a);
                const auto domain = "domain-" + std::to_string(d);
                index.Query(account, domain, cutoff, result);
                Require(result == Reference(attempts, account, domain, cutoff), "indexed/reference parity");
            }
    // Use the same durable order, including duplicate receipt records: restart
    // must reconstruct the original attempt time, not the later receipt time.
    index.Clear();
    for (const auto& attempt : attempts)
        index.Record(attempt.key, attempt.account, attempt.domain, attempt.timestamp);
    index.Query("account-0", "domain-0", -40001, result);
    Require(result == Reference(attempts, "account-0", "domain-0", -40001), "recovery rebuild parity");
    std::cout << "send-attempt-index correctness PASS\n";
}

void SubmissionOutcomes()
{
    for (int mode = 0; mode < 9; ++mode)
    {
        int sends = 0, reasonReads = 0;
        std::function<std::string()> reason;
        if (mode != 3)
            reason = [&]() -> std::string {
                ++reasonReads;
                if (mode == 5) throw std::runtime_error("reason failed");
                if (mode == 6) throw 6;
                return mode == 4 ? "" : "EXPLICIT_VENUE_REJECTION";
            };
        const auto result = ObserveVenueSubmission([&](long* orderId) {
            ++sends;
            if (mode == 7) { *orderId = 71; throw std::runtime_error("possible send"); }
            if (mode == 8) throw 8;
            if (mode == 0) { *orderId = 17; return true; }
            if (mode == 1) return true; // accepted without ID is ambiguous
            return false;
        }, reason);
        Require(sends == 1, "submission must never retry");
        if (mode == 0)
        {
            Require(result.status == VenueSubmissionStatus::Accepted && result.orderId == 17,
                    "valid adapter acceptance");
            Require(reasonReads == 0, "acceptance must not read stale rejection");
        }
        else if (mode == 2)
        {
            Require(result.status == VenueSubmissionStatus::Rejected &&
                    result.reason == "EXPLICIT_VENUE_REJECTION", "explicit reliable rejection");
            Require(reasonReads == 1, "read rejection once");
        }
        else
        {
            Require(result.status == VenueSubmissionStatus::Uncertain,
                    "ambiguous transport must remain uncertain");
            Require(!result.reason.empty(), "uncertainty carries diagnostic");
            if (mode == 1 || mode >= 7)
                Require(reasonReads == 0, "exception/missing-ID must not consult rejection");
            if (mode == 7) Require(result.orderId == 71, "preserve possible-send correlation");
        }
    }
    std::cout << "venue submission outcomes PASS\n";
}

void Measurements()
{
    std::cout << "[";
    bool first = true;
    for (const std::size_t history : {1000U, 10000U, 200000U})
    {
        SendAttemptTimeIndex index;
        for (std::size_t i = 0; i < history; ++i)
            index.Record("history-" + std::to_string(i), "account", "domain",
                         static_cast<std::int64_t>(i));
        std::vector<std::int64_t> result;
        const auto cutoff = static_cast<std::int64_t>(history) - 7;
        for (int warm = 0; warm < 20; ++warm) index.Query("account", "domain", cutoff, result);
        std::vector<long long> timings;
        for (int repeat = 0; repeat < 1000; ++repeat)
        {
            const auto start = std::chrono::steady_clock::now();
            index.Query("account", "domain", cutoff, result);
            const auto duration = std::chrono::duration_cast<std::chrono::nanoseconds>(
                std::chrono::steady_clock::now() - start).count();
            Require(result.size() == 6, "fixed query window");
            timings.push_back(duration);
        }
        Require(index.Size() == history, "measurements must not prune historical identities");
        index.Query("account", "domain", -1, result);
        Require(result.size() == history, "complete historical query remains available");
        std::sort(timings.begin(), timings.end());
        if (!first) std::cout << ",";
        first = false;
        std::cout << "{\"retained_attempts\":" << history
                  << ",\"samples\":1000,\"window_results\":6,\"p50_ns\":" << timings[499]
                  << ",\"p95_ns\":" << timings[949] << ",\"p99_ns\":" << timings[989]
                  << ",\"max_ns\":" << timings.back() << "}";
    }
    std::cout << "]\n";
}
}

int main(int argc, char** argv)
{
    try
    {
        if (argc == 2 && std::string(argv[1]) == "--measure") Measurements();
        else if (argc == 2 && std::string(argv[1]) == "--outcomes") SubmissionOutcomes();
        else if (argc == 1) Correctness();
        else throw std::runtime_error("unsupported argument");
    }
    catch (const std::exception& error)
    {
        std::cerr << error.what() << '\n';
        return 1;
    }
    return 0;
}
