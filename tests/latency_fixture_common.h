#pragma once

#ifndef HEPTA_P99_BASELINE_US
#error "HEPTA_P99_BASELINE_US must come from one canonical JSON baseline"
#endif
#ifndef HEPTA_MAX_REGRESSION_PERCENT
#error "HEPTA_MAX_REGRESSION_PERCENT must come from one canonical JSON baseline"
#endif
#ifndef HEPTA_PERF_BUILD_TYPE
#define HEPTA_PERF_BUILD_TYPE "unknown"
#endif

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <limits>
#include <string>
#include <vector>

#define HEPTA_STRINGIFY_INNER(value) #value
#define HEPTA_STRINGIFY(value) HEPTA_STRINGIFY_INNER(value)

namespace HeptaLatencyFixture
{
inline const char* CompilerFamily()
{
#if defined(__clang__)
    return "clang";
#elif defined(__GNUC__)
    return "gcc";
#elif defined(_MSC_VER)
    return "msvc";
#else
    return "unknown";
#endif
}

inline const char* CompilerVersion()
{
#if defined(__clang__)
    return __clang_version__;
#elif defined(__GNUC__)
    return HEPTA_STRINGIFY(__GNUC__) "." HEPTA_STRINGIFY(__GNUC_MINOR__) "." HEPTA_STRINGIFY(__GNUC_PATCHLEVEL__);
#elif defined(_MSC_VER)
    return HEPTA_STRINGIFY(_MSC_VER);
#else
    return "unknown";
#endif
}

inline long long Percentile(const std::vector<long long>& samples,
                            std::size_t permille)
{
    const std::size_t index =
        ((samples.size() - 1u) * permille + 999u) / 1000u;
    return samples[index];
}

inline int ReportAndCheck(const char* fixture,
                          const char* operationScope,
                          std::size_t warmupIterations,
                          std::vector<long long> samples)
{
    if (fixture == nullptr || *fixture == '\0' ||
        operationScope == nullptr || *operationScope == '\0' ||
        samples.empty())
        return 4;
    std::sort(samples.begin(), samples.end());
    const long long baseline = static_cast<long long>(HEPTA_P99_BASELINE_US);
    const long long regression =
        static_cast<long long>(HEPTA_MAX_REGRESSION_PERCENT);
    if (baseline <= 0 || regression < 0 || regression > 100 ||
        baseline > std::numeric_limits<long long>::max() / (100 + regression))
        return 4;
    const long long allowed = baseline * (100 + regression) / 100;
    const long long p50 = Percentile(samples, 500u);
    const long long p95 = Percentile(samples, 950u);
    const long long p99 = Percentile(samples, 990u);
    const long long p999 = Percentile(samples, 999u);
    const long long maximum = samples.back();

    std::cout
        << "{\"fixture\":\"" << fixture << "\","
        << "\"operation_scope\":\"" << operationScope << "\","
        << "\"evidence_scope\":\"repository-ci\","
        << "\"build_type\":\"" << HEPTA_PERF_BUILD_TYPE << "\","
        << "\"compiler_family\":\"" << CompilerFamily() << "\","
        << "\"compiler_version\":\"" << CompilerVersion() << "\","
        << "\"cplusplus\":" << static_cast<long long>(__cplusplus) << ','
        << "\"warmup_iterations\":" << warmupIterations << ','
        << "\"samples\":" << samples.size() << ','
        << "\"p50_us\":" << p50 << ','
        << "\"p95_us\":" << p95 << ','
        << "\"p99_us\":" << p99 << ','
        << "\"p999_us\":" << p999 << ','
        << "\"max_us\":" << maximum << ','
        << "\"baseline_p99_us\":" << baseline << ','
        << "\"maximum_regression_percent\":" << regression << ','
        << "\"allowed_p99_us\":" << allowed << "}\n";
    return p99 <= allowed ? 0 : 3;
}
}

#undef HEPTA_STRINGIFY
#undef HEPTA_STRINGIFY_INNER
