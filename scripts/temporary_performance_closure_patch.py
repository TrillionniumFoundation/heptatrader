#!/usr/bin/env python3
"""One-shot repository-CI performance evidence closure patch."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DISTRIBUTION = ["p50", "p95", "p99", "p999", "max"]


def load(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def write_json(path: str, value) -> None:
    (ROOT / path).write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def baseline(
    fixture: str,
    p99_us: int,
    regression_percent: int,
    warmup: int,
    samples: int,
    operation_scope: str,
) -> dict:
    return {
        "schema": "heptatrader.latency-baseline.v1",
        "fixture": fixture,
        "operation_scope": operation_scope,
        "scope": "repository-ci",
        "build_type": "Release",
        "runner_class": "github-hosted-ubuntu-24.04",
        "toolchain_binding": (
            "fixture emits compiler family/version and __cplusplus; workflow "
            "and check record bind exact source SHA and runner image"
        ),
        "p99_microseconds": p99_us,
        "maximum_regression_percent": regression_percent,
        "warmup_iterations": warmup,
        "sample_count": samples,
        "required_distribution": DISTRIBUTION,
        "claim_ceiling": (
            "same-fixture repository regression guard only; not a deployed, "
            "PAPER-host, venue-network or product latency SLO"
        ),
    }


def write_baselines() -> None:
    risk_path = "benchmarks/core-latency-baseline-v1.json"
    risk = load(risk_path)
    risk.update(
        {
            "operation_scope": "deterministic fixed-point risk evaluation",
            "scope": "repository-ci",
            "build_type": "Release",
            "runner_class": "github-hosted-ubuntu-24.04",
            "toolchain_binding": (
                "fixture emits numeric policy; workflow and check record bind "
                "exact source SHA, compiler and runner image"
            ),
            "warmup_iterations": 500,
            "sample_count": 10000,
            "claim_ceiling": (
                "same-fixture repository regression guard only; not a deployed "
                "or PAPER-host latency SLO"
            ),
        }
    )
    write_json(risk_path, risk)
    write_json(
        "benchmarks/gateway-control-latency-baseline-v1.json",
        baseline(
            "gateway-validated-read-dispatch-v1",
            50000,
            20,
            1000,
            10000,
            "in-process capability/environment/schema validation, read callback and bounded JSON result validation",
        ),
    )
    write_json(
        "benchmarks/decision-snapshot-latency-baseline-v1.json",
        baseline(
            "decision-snapshot-capture-v1",
            100000,
            20,
            100,
            2000,
            "seven-read authoritative decision snapshot capture, canonical JSON validation, digest and generation publication",
        ),
    )
    write_json(
        "benchmarks/portfolio-compiler-latency-baseline-v1.json",
        baseline(
            "portfolio-compiler-limit-v1",
            200000,
            10,
            100,
            2000,
            "64-strategy by 16-instrument deterministic fixed-point netting and delta compilation",
        ),
    )
    write_json(
        "benchmarks/oms-journal-ci-latency-baseline-v1.json",
        baseline(
            "oms-critical-durable-append-ci-v1",
            500000,
            20,
            8,
            128,
            "critical OMS append through path-identity checks, write and fdatasync on hosted runner temporary storage",
        ),
    )


def write_common_header() -> None:
    content = r'''#pragma once

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
'''
    (ROOT / "tests/latency_fixture_common.h").write_text(content, encoding="utf-8")


def write_gateway_fixture() -> None:
    content = r'''#include "../HeptaTrade/tools/trading_tool_registry.h"
#include "latency_fixture_common.h"

#include <chrono>
#include <string>
#include <vector>

namespace
{
constexpr int kWarmupIterations = 1000;
constexpr int kSampleCount = 10000;

class NoMutationAuthority final : public ExecutionAuthority
{
public:
    ExecutionCommandResult PlaceOrder(const PlaceOrderCommand& command) override
    {
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Rejected;
        result.commandId = command.context.toolCallId;
        result.reasonCode = "PERFORMANCE_FIXTURE_NO_MUTATION";
        return result;
    }

    ExecutionCommandResult CancelOrder(const CancelOrderCommand& command) override
    {
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Rejected;
        result.commandId = command.context.toolCallId;
        result.reasonCode = "PERFORMANCE_FIXTURE_NO_MUTATION";
        return result;
    }
};

bool InvokeHealth(TradingToolRegistry& registry,
                  const TradingToolSession& session,
                  const TradingToolCall& call)
{
    const TradingToolResult result = registry.Invoke(session, call);
    return result.status == TradingToolCallStatus::Ok &&
        result.reasonCode.empty() &&
        result.payloadJson == "{\"gateway_ready\":true}";
}
}

int main()
{
    NoMutationAuthority execution;
    TradingToolReadCallbacks callbacks;
    callbacks.systemGetHealth = [](
        const TradingToolSession&, const TradingToolCall&,
        std::string& payload, std::string& reason) {
        payload = "{\"gateway_ready\":true}";
        reason.clear();
        return true;
    };
    TradingToolRegistry registry(execution, callbacks);
    TradingToolSession session;
    session.environment = "WATCH";
    session.capabilities.insert("system.read");
    TradingToolCall call;
    call.name = "system.get_health";

    for (int i = 0; i < kWarmupIterations; ++i)
        if (!InvokeHealth(registry, session, call)) return 2;

    std::vector<long long> samples;
    samples.reserve(kSampleCount);
    for (int i = 0; i < kSampleCount; ++i)
    {
        const auto start = std::chrono::steady_clock::now();
        const bool ok = InvokeHealth(registry, session, call);
        const auto end = std::chrono::steady_clock::now();
        if (!ok) return 2;
        samples.push_back(
            std::chrono::duration_cast<std::chrono::microseconds>(end - start).count());
    }
    return HeptaLatencyFixture::ReportAndCheck(
        "gateway-validated-read-dispatch-v1",
        "capability/environment/schema validation, read callback and bounded JSON validation",
        kWarmupIterations,
        samples);
}
'''
    (ROOT / "tests/gateway_latency_fixture_tests.cpp").write_text(
        content, encoding="utf-8"
    )


def write_snapshot_fixture() -> None:
    content = r'''#include "../HeptaTrade/tools/trading_tool_registry.h"
#include "latency_fixture_common.h"

#include <chrono>
#include <cstdint>
#include <string>
#include <vector>

namespace
{
constexpr int kWarmupIterations = 100;
constexpr int kSampleCount = 2000;

std::int64_t EpochMilliseconds()
{
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
}

class NoMutationAuthority final : public ExecutionAuthority
{
public:
    ExecutionCommandResult PlaceOrder(const PlaceOrderCommand& command) override
    {
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Rejected;
        result.commandId = command.context.toolCallId;
        result.reasonCode = "PERFORMANCE_FIXTURE_NO_MUTATION";
        return result;
    }

    ExecutionCommandResult CancelOrder(const CancelOrderCommand& command) override
    {
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Rejected;
        result.commandId = command.context.toolCallId;
        result.reasonCode = "PERFORMANCE_FIXTURE_NO_MUTATION";
        return result;
    }
};

bool InvokeSnapshot(TradingToolRegistry& registry,
                    const TradingToolSession& session,
                    const TradingToolCall& call)
{
    const TradingToolResult result = registry.Invoke(session, call);
    return result.status == TradingToolCallStatus::Ok &&
        result.reasonCode.empty() &&
        result.payloadJson.find("\"schema\":\"hepta.decision-snapshot.v1\"") !=
            std::string::npos;
}
}

int main()
{
    NoMutationAuthority execution;
    TradingToolReadCallbacks callbacks;
    callbacks.systemGetHealth = [](
        const TradingToolSession&, const TradingToolCall&,
        std::string& payload, std::string& reason) {
        payload =
            "{\"gateway_ready\":true,\"remote_execution_ready\":true,"
            "\"execution_service_epoch\":\"performance-epoch-1\","
            "\"execution_service_fencing_generation\":7,"
            "\"event_watermark\":42,\"state_generation\":11}";
        reason.clear();
        return true;
    };
    callbacks.marketGetQuote = [](
        const TradingToolSession&, const TradingToolCall& call,
        std::string& payload, std::string& reason) {
        payload = "{\"authoritative\":true,\"instrument\":\"" +
            call.instrument + "\",\"observed_at_ms\":" +
            std::to_string(EpochMilliseconds()) +
            ",\"stale\":false,\"bid\":1.1001,\"ask\":1.1002}";
        reason.clear();
        return true;
    };
    callbacks.accountGetSummary = [](
        const TradingToolSession&, const TradingToolCall&,
        std::string& payload, std::string& reason) {
        payload = "{\"authoritative\":true,\"account_complete\":true}";
        reason.clear();
        return true;
    };
    callbacks.portfolioListPositions = [](
        const TradingToolSession&, const TradingToolCall&,
        std::string& payload, std::string& reason) {
        payload =
            "{\"authoritative\":true,\"positions\":[{"
            "\"instrument\":\"EUR.USD\",\"quantity\":1000}]}";
        reason.clear();
        return true;
    };
    callbacks.ordersList = [](
        const TradingToolSession&, const TradingToolCall&,
        std::string& payload, std::string& reason) {
        payload =
            "{\"authoritative\":true,\"active_order_ids\":[]}";
        reason.clear();
        return true;
    };
    callbacks.riskGetLimits = [](
        const TradingToolSession&, const TradingToolCall&,
        std::string& payload, std::string& reason) {
        payload =
            "{\"authoritative\":true,\"gross_absolute_position\":1000}";
        reason.clear();
        return true;
    };

    TradingToolRegistry registry(execution, callbacks);
    TradingToolSession session;
    session.executionContext.agentId = "performance-agent";
    session.executionContext.sessionId = "performance-session";
    session.executionContext.account = "PERF-ACCOUNT";
    session.executionContext.executionDomain = "SIM-PERFORMANCE";
    session.environment = "WATCH";
    session.capabilities.insert("system.read");
    session.visibleInstruments.insert("EUR.USD");
    TradingToolCall call;
    call.name = "decision.get_snapshot";
    call.instrument = "EUR.USD";

    for (int i = 0; i < kWarmupIterations; ++i)
        if (!InvokeSnapshot(registry, session, call)) return 2;

    std::vector<long long> samples;
    samples.reserve(kSampleCount);
    for (int i = 0; i < kSampleCount; ++i)
    {
        const auto start = std::chrono::steady_clock::now();
        const bool ok = InvokeSnapshot(registry, session, call);
        const auto end = std::chrono::steady_clock::now();
        if (!ok) return 2;
        samples.push_back(
            std::chrono::duration_cast<std::chrono::microseconds>(end - start).count());
    }
    return HeptaLatencyFixture::ReportAndCheck(
        "decision-snapshot-capture-v1",
        "seven-read authoritative decision snapshot, canonical validation, digest and generation publication",
        kWarmupIterations,
        samples);
}
'''
    (ROOT / "tests/snapshot_latency_fixture_tests.cpp").write_text(
        content, encoding="utf-8"
    )


def write_portfolio_fixture() -> None:
    content = r'''#include "../HeptaTrade/portfolio/portfolio_compiler.h"
#include "latency_fixture_common.h"

#include <chrono>
#include <cstdio>
#include <string>
#include <vector>

namespace
{
constexpr int kWarmupIterations = 100;
constexpr int kSampleCount = 2000;
constexpr int kStrategyCount = 64;
constexpr int kInstrumentCount = 16;

std::string PaddedId(const char* prefix, int value)
{
    char buffer[32];
    std::snprintf(buffer, sizeof(buffer), "%s%03d", prefix, value);
    return std::string(buffer);
}

bool CompileOnce(const std::vector<StrategyTargetIntent>& intents,
                 const AuthoritativePortfolioInput& authoritative,
                 const PortfolioCapitalPolicy& policy)
{
    const PortfolioCompileResult result =
        PortfolioCompiler::Compile(intents, authoritative, policy);
    return result.accepted && result.reasonCode == "PORTFOLIO_COMPILED" &&
        result.strategyGrossTargets.size() == kStrategyCount &&
        result.netTargets.size() == kInstrumentCount;
}
}

int main()
{
    const std::uint64_t generation = 77;
    AuthoritativePortfolioInput authoritative;
    authoritative.complete = true;
    authoritative.generation = generation;
    PortfolioCapitalPolicy policy;
    policy.maximumGrossTarget = 1000000000;
    policy.maximumStrategies = kStrategyCount;
    policy.maximumInstruments = kInstrumentCount;

    std::vector<std::string> instruments;
    instruments.reserve(kInstrumentCount);
    for (int instrument = 0; instrument < kInstrumentCount; ++instrument)
    {
        const std::string id = PaddedId("INSTRUMENT-", instrument);
        instruments.push_back(id);
        authoritative.currentPositions[id] = instrument * 100;
    }

    std::vector<StrategyTargetIntent> intents;
    intents.reserve(kStrategyCount * kInstrumentCount);
    for (int strategy = 0; strategy < kStrategyCount; ++strategy)
    {
        const std::string strategyId = PaddedId("STRATEGY-", strategy);
        StrategyCapitalBudget budget;
        budget.strategyId = strategyId;
        budget.maximumGrossTarget = 1000000;
        policy.strategyBudgets[strategyId] = budget;
        for (int instrument = 0; instrument < kInstrumentCount; ++instrument)
        {
            StrategyTargetIntent intent;
            intent.strategyId = strategyId;
            intent.instrument = instruments[instrument];
            const PortfolioMicrounits magnitude =
                static_cast<PortfolioMicrounits>(1000 + (strategy % 5) * 100);
            intent.targetPosition =
                ((strategy + instrument) % 2 == 0) ? magnitude : -magnitude;
            intent.snapshotGeneration = generation;
            intents.push_back(intent);
        }
    }

    for (int i = 0; i < kWarmupIterations; ++i)
        if (!CompileOnce(intents, authoritative, policy)) return 2;

    std::vector<long long> samples;
    samples.reserve(kSampleCount);
    for (int i = 0; i < kSampleCount; ++i)
    {
        const auto start = std::chrono::steady_clock::now();
        const bool ok = CompileOnce(intents, authoritative, policy);
        const auto end = std::chrono::steady_clock::now();
        if (!ok) return 2;
        samples.push_back(
            std::chrono::duration_cast<std::chrono::microseconds>(end - start).count());
    }
    return HeptaLatencyFixture::ReportAndCheck(
        "portfolio-compiler-limit-v1",
        "64-strategy by 16-instrument fixed-point netting, budget and delta compilation",
        kWarmupIterations,
        samples);
}
'''
    (ROOT / "tests/portfolio_latency_fixture_tests.cpp").write_text(
        content, encoding="utf-8"
    )


def write_oms_fixture() -> None:
    content = r'''#include "../HeptaTrade/oms_journal.h"
#include "latency_fixture_common.h"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <unistd.h>
#include <vector>

namespace
{
constexpr int kWarmupIterations = 8;
constexpr int kSampleCount = 128;

OmsJournalEvent Event(int sequence)
{
    OmsJournalEvent event;
    event.eventType = "order_intent";
    event.tsMs = OmsJournal::NowEpochMs();
    event.clientReqId = "performance-request-" + std::to_string(sequence);
    event.reqId = event.clientReqId;
    event.eventId = "performance-event-" + std::to_string(sequence);
    event.instrument = "EUR.USD";
    event.side = "BUY";
    event.qty = 1000.0;
    event.price = 1.1002;
    event.status = "PENDING";
    event.reason = "PERFORMANCE_FIXTURE";
    event.source = "repository-ci";
    event.executionDomain = "SIM-PERFORMANCE";
    return event;
}
}

int main()
{
    if (::setenv("HEPTA_OMS_ASYNC_FLUSH", "0", 1) != 0 ||
        ::setenv("HEPTA_OMS_SYNC_CRITICAL", "1", 1) != 0 ||
        ::setenv("HEPTA_OMS_BATCH_SIZE", "8", 1) != 0 ||
        ::setenv("HEPTA_OMS_FLUSH_INTERVAL_MS", "250", 1) != 0)
        return 2;

    char rawPath[] = "/tmp/hepta-oms-performance-XXXXXX";
    const int fd = ::mkstemp(rawPath);
    if (fd < 0) return 2;
    if (::close(fd) != 0)
    {
        ::unlink(rawPath);
        return 2;
    }
    const std::string path(rawPath);
    std::vector<long long> samples;
    samples.reserve(kSampleCount);
    int resultCode = 0;
    {
        OmsJournal journal;
        if (!journal.Init(path)) resultCode = 2;
        for (int i = 0; resultCode == 0 && i < kWarmupIterations; ++i)
            if (!journal.Append(Event(i))) resultCode = 2;
        for (int i = 0; resultCode == 0 && i < kSampleCount; ++i)
        {
            const OmsJournalEvent event = Event(kWarmupIterations + i);
            const auto start = std::chrono::steady_clock::now();
            const bool ok = journal.Append(event);
            const auto end = std::chrono::steady_clock::now();
            if (!ok)
            {
                resultCode = 2;
                break;
            }
            samples.push_back(
                std::chrono::duration_cast<std::chrono::microseconds>(end - start).count());
        }
        const OmsJournalHealthSnapshot health = journal.GetHealthSnapshot();
        if (resultCode == 0 &&
            (health.writePoisoned || health.durableSyncFailures != 0 ||
             health.durableSyncWrites < kWarmupIterations + kSampleCount))
            resultCode = 2;
    }
    if (::unlink(path.c_str()) != 0 && resultCode == 0) resultCode = 2;
    if (resultCode != 0) return resultCode;
    return HeptaLatencyFixture::ReportAndCheck(
        "oms-critical-durable-append-ci-v1",
        "critical OMS path-identity validation, append and fdatasync on hosted runner temporary storage",
        kWarmupIterations,
        samples);
}
'''
    (ROOT / "tests/oms_journal_latency_fixture_tests.cpp").write_text(
        content, encoding="utf-8"
    )


def patch_tests_cmake() -> None:
    path = ROOT / "tests/CMakeLists.txt"
    text = path.read_text(encoding="utf-8")
    function_anchor = '''    set(HEPTA_CORE_TEST_TARGETS "${HEPTA_CORE_TEST_TARGETS}" PARENT_SCOPE)
endfunction()

add_executable(hepta_trading_contract_tests
'''
    if function_anchor not in text:
        raise SystemExit("tests CMake helper anchor missing")
    loader = r'''    set(HEPTA_CORE_TEST_TARGETS "${HEPTA_CORE_TEST_TARGETS}" PARENT_SCOPE)
endfunction()

function(hepta_load_latency_baseline prefix relative_path expected_fixture)
    set(path "${CMAKE_SOURCE_DIR}/${relative_path}")
    file(READ "${path}" json)
    string(JSON schema ERROR_VARIABLE error GET "${json}" schema)
    if(error OR NOT schema STREQUAL "heptatrader.latency-baseline.v1")
        message(FATAL_ERROR "${relative_path}: invalid latency baseline schema: ${error}")
    endif()
    string(JSON fixture ERROR_VARIABLE error GET "${json}" fixture)
    if(error OR NOT fixture STREQUAL expected_fixture)
        message(FATAL_ERROR "${relative_path}: invalid fixture identity: ${error}")
    endif()
    string(JSON p99 ERROR_VARIABLE error GET "${json}" p99_microseconds)
    if(error OR NOT p99 MATCHES "^[1-9][0-9]*$")
        message(FATAL_ERROR "${relative_path}: invalid p99 baseline: ${error}")
    endif()
    string(JSON regression ERROR_VARIABLE error GET "${json}" maximum_regression_percent)
    if(error OR NOT regression MATCHES "^[0-9]+$" OR regression GREATER 100)
        message(FATAL_ERROR "${relative_path}: invalid regression percentage: ${error}")
    endif()
    set(${prefix}_P99_US "${p99}" PARENT_SCOPE)
    set(${prefix}_REGRESSION_PERCENT "${regression}" PARENT_SCOPE)
endfunction()

add_executable(hepta_trading_contract_tests
'''
    text = text.replace(function_anchor, loader, 1)

    target_anchor = '''add_custom_target(hepta_reliability_test_binaries
    DEPENDS ${HEPTA_RELIABILITY_TEST_TARGETS})
'''
    if target_anchor not in text:
        raise SystemExit("tests CMake reliability target anchor missing")
    targets = r'''hepta_load_latency_baseline(
    HEPTA_GATEWAY
    "benchmarks/gateway-control-latency-baseline-v1.json"
    "gateway-validated-read-dispatch-v1")
add_executable(hepta_gateway_latency_fixture_tests
    gateway_latency_fixture_tests.cpp)
target_link_libraries(hepta_gateway_latency_fixture_tests
    hepta_trading_tool_core)
target_compile_definitions(hepta_gateway_latency_fixture_tests PRIVATE
    HEPTA_P99_BASELINE_US=${HEPTA_GATEWAY_P99_US}
    HEPTA_MAX_REGRESSION_PERCENT=${HEPTA_GATEWAY_REGRESSION_PERCENT}
    HEPTA_PERF_BUILD_TYPE="${CMAKE_BUILD_TYPE}")
hepta_register_reliability_test(hepta_gateway_latency_fixture_tests)
set_tests_properties(hepta_gateway_latency_fixture_tests PROPERTIES
    LABELS "reliability;performance" TIMEOUT 60)

hepta_load_latency_baseline(
    HEPTA_SNAPSHOT
    "benchmarks/decision-snapshot-latency-baseline-v1.json"
    "decision-snapshot-capture-v1")
add_executable(hepta_snapshot_latency_fixture_tests
    snapshot_latency_fixture_tests.cpp)
target_link_libraries(hepta_snapshot_latency_fixture_tests
    hepta_trading_tool_core)
target_compile_definitions(hepta_snapshot_latency_fixture_tests PRIVATE
    HEPTA_P99_BASELINE_US=${HEPTA_SNAPSHOT_P99_US}
    HEPTA_MAX_REGRESSION_PERCENT=${HEPTA_SNAPSHOT_REGRESSION_PERCENT}
    HEPTA_PERF_BUILD_TYPE="${CMAKE_BUILD_TYPE}")
hepta_register_reliability_test(hepta_snapshot_latency_fixture_tests)
set_tests_properties(hepta_snapshot_latency_fixture_tests PROPERTIES
    LABELS "reliability;performance" TIMEOUT 90)

hepta_load_latency_baseline(
    HEPTA_PORTFOLIO
    "benchmarks/portfolio-compiler-latency-baseline-v1.json"
    "portfolio-compiler-limit-v1")
add_executable(hepta_portfolio_latency_fixture_tests
    portfolio_latency_fixture_tests.cpp)
target_link_libraries(hepta_portfolio_latency_fixture_tests
    hepta_portfolio_core)
target_compile_definitions(hepta_portfolio_latency_fixture_tests PRIVATE
    HEPTA_P99_BASELINE_US=${HEPTA_PORTFOLIO_P99_US}
    HEPTA_MAX_REGRESSION_PERCENT=${HEPTA_PORTFOLIO_REGRESSION_PERCENT}
    HEPTA_PERF_BUILD_TYPE="${CMAKE_BUILD_TYPE}")
hepta_register_reliability_test(hepta_portfolio_latency_fixture_tests)
set_tests_properties(hepta_portfolio_latency_fixture_tests PROPERTIES
    LABELS "reliability;performance" TIMEOUT 90)

hepta_load_latency_baseline(
    HEPTA_OMS_JOURNAL_CI
    "benchmarks/oms-journal-ci-latency-baseline-v1.json"
    "oms-critical-durable-append-ci-v1")
add_executable(hepta_oms_journal_latency_fixture_tests
    oms_journal_latency_fixture_tests.cpp)
target_link_libraries(hepta_oms_journal_latency_fixture_tests
    pthread hepta_observability_core hepta_oms_core)
target_compile_definitions(hepta_oms_journal_latency_fixture_tests PRIVATE
    HEPTA_P99_BASELINE_US=${HEPTA_OMS_JOURNAL_CI_P99_US}
    HEPTA_MAX_REGRESSION_PERCENT=${HEPTA_OMS_JOURNAL_CI_REGRESSION_PERCENT}
    HEPTA_PERF_BUILD_TYPE="${CMAKE_BUILD_TYPE}")
hepta_register_reliability_test(hepta_oms_journal_latency_fixture_tests)
set_tests_properties(hepta_oms_journal_latency_fixture_tests PROPERTIES
    LABELS "reliability;performance" RUN_SERIAL TRUE TIMEOUT 120)

add_custom_target(hepta_reliability_test_binaries
    DEPENDS ${HEPTA_RELIABILITY_TEST_TARGETS})
'''
    text = text.replace(target_anchor, targets, 1)
    path.write_text(text, encoding="utf-8")


def update_budget_registry() -> None:
    path = "docs/verification/performance-budgets-v1.json"
    document = load(path)
    policy = document["policy"]
    policy["implemented_scopes"] = ["repository-ci", "bounded-complexity"]
    policy["repository_ci_claim_ceiling"] = (
        "same-fixture regression evidence only; target-host, network and venue "
        "latency require separate qualification"
    )
    by_id = {item["id"]: item for item in document["budgets"]}

    repository_budgets = {
        "gateway-control-v1": {
            "metric": "p99 validated in-process read dispatch latency",
            "regression_percent": 20,
            "baseline": "benchmarks/gateway-control-latency-baseline-v1.json",
            "fixture": "gateway-validated-read-dispatch-v1",
            "fixture_source": "tests/gateway_latency_fixture_tests.cpp",
            "test_target": "hepta_gateway_latency_fixture_tests",
            "scope": "repository-ci",
            "state": "implemented",
        },
        "snapshot-v1": {
            "metric": "p99 authoritative decision snapshot capture latency",
            "regression_percent": 20,
            "baseline": "benchmarks/decision-snapshot-latency-baseline-v1.json",
            "fixture": "decision-snapshot-capture-v1",
            "fixture_source": "tests/snapshot_latency_fixture_tests.cpp",
            "test_target": "hepta_snapshot_latency_fixture_tests",
            "scope": "repository-ci",
            "state": "implemented",
        },
        "risk-policy-v1": {
            "metric": "p99 evaluation latency",
            "regression_percent": 10,
            "baseline": "benchmarks/core-latency-baseline-v1.json",
            "fixture": "risk-evaluate-v1",
            "fixture_source": "tests/risk_latency_fixture_tests.cpp",
            "test_target": "hepta_risk_latency_fixture_tests",
            "numeric_policy": "hepta.numeric.fixed-v1",
            "scope": "repository-ci",
            "state": "implemented",
        },
        "portfolio-compiler-v1": {
            "metric": "p99 limit-profile compile latency",
            "regression_percent": 10,
            "baseline": "benchmarks/portfolio-compiler-latency-baseline-v1.json",
            "fixture": "portfolio-compiler-limit-v1",
            "fixture_source": "tests/portfolio_latency_fixture_tests.cpp",
            "test_target": "hepta_portfolio_latency_fixture_tests",
            "scope": "repository-ci",
            "state": "implemented",
        },
    }
    for budget_id, replacement in repository_budgets.items():
        by_id[budget_id].clear()
        by_id[budget_id].update({"id": budget_id, **replacement})

    execution = by_id["execution-authority-v1"]
    execution["scope"] = "target-host"
    execution["repository_ci_baseline"] = (
        "benchmarks/oms-journal-ci-latency-baseline-v1.json"
    )
    execution["repository_ci_fixture"] = "oms-critical-durable-append-ci-v1"
    execution["repository_ci_fixture_source"] = (
        "tests/oms_journal_latency_fixture_tests.cpp"
    )
    execution["repository_ci_test_target"] = (
        "hepta_oms_journal_latency_fixture_tests"
    )
    execution["missing_evidence"] = (
        "repository CI now guards critical append+fdatasync, but the canonical "
        "journal-durable-to-send target still lacks the qualified PAPER host "
        "filesystem, send handoff, queue/load distribution and absolute threshold"
    )

    allocator = by_id["global-allocator-v1"]
    allocator["scope"] = "bounded-complexity"
    allocator["fixture_source"] = "tests/global_allocator_tests.cpp"
    document["budgets"] = [by_id[item["id"]] for item in document["budgets"]]
    write_json(path, document)


def write_budget_tests() -> None:
    content = r'''from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]


class PerformanceBudgetRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = json.loads(
            (ROOT / "docs/verification/performance-budgets-v1.json").read_text(
                encoding="utf-8"
            )
        )
        self.budgets = {item["id"]: item for item in self.document["budgets"]}

    def _assert_baseline(self, budget: dict, prefix: str = "") -> None:
        baseline_key = prefix + "baseline"
        fixture_key = prefix + "fixture"
        source_key = prefix + "fixture_source"
        target_key = prefix + "test_target"
        baseline_path = ROOT / budget[baseline_key]
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        self.assertEqual("heptatrader.latency-baseline.v1", baseline["schema"])
        self.assertEqual(budget[fixture_key], baseline["fixture"])
        self.assertGreater(baseline["p99_microseconds"], 0)
        self.assertGreaterEqual(baseline["maximum_regression_percent"], 0)
        self.assertLessEqual(baseline["maximum_regression_percent"], 100)
        self.assertEqual(
            self.document["policy"]["distribution_required"],
            baseline["required_distribution"],
        )
        for field in (
            "operation_scope",
            "scope",
            "build_type",
            "runner_class",
            "toolchain_binding",
            "claim_ceiling",
        ):
            self.assertTrue(str(baseline[field]).strip(), baseline_path)
        self.assertEqual("repository-ci", baseline["scope"])
        self.assertGreater(baseline["warmup_iterations"], 0)
        self.assertGreater(baseline["sample_count"], 0)
        source = ROOT / budget[source_key]
        self.assertTrue(source.is_file(), source)
        source_text = source.read_text(encoding="utf-8")
        self.assertIn(baseline["fixture"], source_text)
        for percentile in ("p50_us", "p95_us", "p99_us", "p999_us", "max_us"):
            if source.name == "risk_latency_fixture_tests.cpp":
                self.assertIn(percentile, source_text)
            else:
                self.assertIn("ReportAndCheck", source_text)
        self.assertTrue(budget[target_key].strip())

    def test_budget_ids_are_unique_and_states_have_truthful_claim_ceiling(self) -> None:
        self.assertEqual(
            "heptatrader.performance-budgets.v1", self.document["schema"]
        )
        self.assertEqual(len(self.budgets), len(self.document["budgets"]))
        allowed = set(self.document["policy"]["allowed_states"])
        scopes = set(self.document["policy"]["implemented_scopes"])
        for budget_id, budget in self.budgets.items():
            self.assertIn(budget["state"], allowed, budget_id)
            self.assertGreaterEqual(budget["regression_percent"], 0, budget_id)
            self.assertLessEqual(budget["regression_percent"], 100, budget_id)
            if budget["state"] == "declared":
                self.assertTrue(budget["missing_evidence"].strip(), budget_id)
                self.assertNotIn("baseline", budget, budget_id)
            else:
                self.assertTrue(budget["fixture"].strip(), budget_id)
                self.assertTrue(budget["test_target"].strip(), budget_id)
                self.assertIn(budget["scope"], scopes, budget_id)
                if budget["scope"] == "repository-ci":
                    self.assertIn("baseline", budget, budget_id)

    def test_repository_ci_budgets_use_canonical_baselines(self) -> None:
        expected = {
            "gateway-control-v1",
            "snapshot-v1",
            "risk-policy-v1",
            "portfolio-compiler-v1",
        }
        observed = {
            budget_id
            for budget_id, budget in self.budgets.items()
            if budget.get("scope") == "repository-ci" and
            budget.get("state") == "implemented"
        }
        self.assertEqual(expected, observed)
        for budget_id in sorted(expected):
            budget = self.budgets[budget_id]
            self._assert_baseline(budget)
            baseline = json.loads(
                (ROOT / budget["baseline"]).read_text(encoding="utf-8")
            )
            self.assertEqual(
                budget["regression_percent"],
                baseline["maximum_regression_percent"],
            )

    def test_execution_target_host_budget_is_not_promoted_by_ci_storage(self) -> None:
        budget = self.budgets["execution-authority-v1"]
        self.assertEqual("declared", budget["state"])
        self.assertEqual("target-host", budget["scope"])
        self.assertNotIn("baseline", budget)
        self.assertIn("PAPER host", budget["missing_evidence"])
        auxiliary = {
            "repository_ci_baseline": budget["repository_ci_baseline"],
            "repository_ci_fixture": budget["repository_ci_fixture"],
            "repository_ci_fixture_source": budget[
                "repository_ci_fixture_source"
            ],
            "repository_ci_test_target": budget[
                "repository_ci_test_target"
            ],
        }
        self._assert_baseline(auxiliary, "repository_ci_")

    def test_cmake_loads_every_repository_latency_baseline(self) -> None:
        cmake = (ROOT / "tests/CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("hepta_load_latency_baseline", cmake)
        for budget in self.budgets.values():
            if budget.get("scope") == "repository-ci":
                self.assertIn(Path(budget["baseline"]).name, cmake)
                self.assertIn(budget["test_target"], cmake)
        execution = self.budgets["execution-authority-v1"]
        self.assertIn(Path(execution["repository_ci_baseline"]).name, cmake)
        self.assertIn(execution["repository_ci_test_target"], cmake)

        root_cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("core-latency-baseline-v1.json", root_cmake)
        self.assertIn("string(JSON HEPTA_RISK_P99_BASELINE_US", root_cmake)
        fixture = (ROOT / "tests/risk_latency_fixture_tests.cpp").read_text(
            encoding="utf-8"
        )
        self.assertIn('#include "heptatrader_performance_budget.h"', fixture)
        self.assertNotRegex(fixture, r"#define\s+HEPTA_RISK_P99_BASELINE_US")

    def test_implemented_and_auxiliary_test_targets_exist(self) -> None:
        cmake_text = "\n".join(
            path.read_text(encoding="utf-8-sig")
            for path in (ROOT / "tests").rglob("CMakeLists.txt")
        )
        targets = {
            budget["test_target"]
            for budget in self.budgets.values()
            if budget["state"] == "implemented"
        }
        targets.add(
            self.budgets["execution-authority-v1"][
                "repository_ci_test_target"
            ]
        )
        for target in sorted(targets):
            self.assertRegex(
                cmake_text,
                rf"add_executable\s*\(\s*{re.escape(target)}\b",
                target,
            )

    def test_documentation_does_not_promote_repository_ci_or_declared_budgets(self) -> None:
        documentation = (
            ROOT / "docs/operations/PERFORMANCE-QUALIFICATION.md"
        ).read_text(encoding="utf-8")
        self.assertIn("declared", documentation)
        self.assertIn("implemented", documentation)
        self.assertIn("repository-ci", documentation)
        self.assertIn("不能支持", documentation)
        self.assertIn("不能成为 PAPER host SLA", documentation)


if __name__ == "__main__":
    unittest.main()
'''
    (ROOT / "tests/python/test_performance_budget_registry.py").write_text(
        content, encoding="utf-8"
    )


def write_performance_document() -> None:
    content = r'''# Performance Qualification

Status: current normative
Applies to: runtime latency, throughput, queue and host-tuning claims
Verification: performance budget registry, same-fixture executable gates and target-host observations
Authority: performance-claim policy

性能声明必须绑定 source、binary、compiler/toolchain、build type、fixture、hardware/VM、kernel、CPU governor、affinity、queue load、sample distribution 和 correctness result。只报告平均值不足以支持交易运行时声明，至少记录 p50/p95/p99/p999、max、sample count、drop/backpressure 和 CPU/memory。

## Budget states and evidence scope

`performance-budgets-v1.json` 每个 entry 明确为：

- `declared`：目标和允许回退比例已经定义，但缺少 canonical absolute baseline、representative fixture 或 target-host distribution；**不能支持性能、readiness、release 或 qualification 声明**；
- `implemented`：存在同一 revision 的 executable fixture、机器绑定 threshold/complexity contract 和可记录 source/build/toolchain identity。

`implemented` 还必须解释 scope：

- `repository-ci`：只证明同 fixture、Release build、托管 runner/toolchain 下的回归上界；不能支持部署、PAPER、网络或产品 SLO；
- `bounded-complexity`：证明算法探索上限、deadline/fallback 和 truthful bound，不自动形成 wall-clock SLO；
- target-host 性能只有受保护环境在精确 artifact/config/host 上生成完整分布后才能成立。

当前 repository-ci 已实现：

- `risk-policy-v1`：固定点风险评估；
- `gateway-control-v1`：进程内 capability/environment/schema 校验、read callback 和 bounded JSON 结果校验；
- `snapshot-v1`：七次权威子读取、复合决策快照校验、digest 与 generation 发布；
- `portfolio-compiler-v1`：64 strategy × 16 instrument 的固定点净额、预算和 delta 编译。

`global-allocator-v1` 是 implemented bounded-complexity contract。`execution-authority-v1` 仍为 declared target-host budget；仓库 CI 的 critical OMS append+`fdatasync` fixture 只是回归烟测，不能成为 PAPER host SLA。

## Canonical repository fixtures

每个 wall-clock fixture 的唯一 threshold 来自 `benchmarks/*.json`。CMake 使用 `string(JSON ...)` 读取 exact fixture、p99 和 regression percentage，并把数值编译进对应测试；C++ 不保留第二套手写阈值。Baseline 记录 operation scope、Release build、runner class、warmup、sample count 和 claim ceiling；fixture 输出 compiler family/version、`__cplusplus`、完整 percentile distribution 和 allowed threshold。Workflow/check record 绑定 exact source SHA 与 runner image。

### Risk

`hepta_risk_latency_fixture_tests` 预热 500 次并采集 10,000 次 exact fixed-point evaluation。其 hosted 数值不是生产承诺。

### Gateway validated read dispatch

`hepta_gateway_latency_fixture_tests` 对 `system.get_health` 运行 1,000 次预热和 10,000 次采样，覆盖 registry lookup、capability、environment、typed-call validation、read callback、bounded JSON validation 和 result construction。它不覆盖 AF_UNIX、排队、慢 handler、跨 owner 公平性或下游 Execution RPC；这些必须作为更高层 fixture 单独资格化。

### Authoritative decision snapshot

`hepta_snapshot_latency_fixture_tests` 预热 100 次并采集 2,000 次完整复合快照，验证 before/after health identity、quote currentness、account/positions/orders/risk authoritative flags、owner identity、canonical JSON、digest、watermark 和 generation。它不替代 Market Data 多 shard contention 或目标主机负载测试。

### Portfolio compiler

`hepta_portfolio_latency_fixture_tests` 对 64 strategy × 16 instrument、1,024 intents 的 limit profile 预热 100 次并采集 2,000 次。Correctness tests仍单独覆盖 overflow、duplicate、generation、budget 和 canonical ordering；性能 fixture 不允许通过减少 cardinality 来规避门禁。

### OMS durable append repository smoke

`hepta_oms_journal_latency_fixture_tests` 强制同步 critical event，在每次样本中执行 path identity、write 和 `fdatasync`，并验证 durable-write counters 与 poison/failure 状态。托管 runner 的 `/tmp` filesystem、虚拟化与噪声不是 PAPER 主机，因此该 fixture 只能发现显著仓库回归，不能关闭 `execution-authority-v1` 的 target-host evidence。

## Global allocator budget

Global allocator 的 implemented budget 当前是 bounded complexity/deadline contract，而非通用 wall-clock SLA。Evidence 必须验证：

- exact enumeration只在 `maximumExactCombinations` 内运行；
- 超过上限使用确定性、truthful `feasible_not_proven` 路径；
- `combinationsExplored`、objective、upper bound、absolute gap、exact/status 和 digest一致；
- 不把 heuristic 谎称为 optimal；
- malformed/overflow/invalid bound fail closed。

未来增加 wall-clock target 时必须使用独立 baseline与representative proposal distributions，不能复用 risk fixture。

## Remaining higher-layer budgets

### Gateway end to end

仍需在目标拓扑覆盖 AF_UNIX admission、session lease lookup、tools list/describe、mutation RPC、queue full、slow handler、cross-owner fairness和response encoding；记录 request bytes、concurrency、queue depth、timeout、p50–p999/max 和 rejection/drop。

### Market Data and snapshot contention

仍需分别测量 single shard、coherent multi-shard vector、contention、gap/stale rejection 和 digest validation；记录 shard count、instrument count、reader/writer load、generation和lock wait。

### Execution authority target host

`journal-durable-to-send` 必须在目标 filesystem/durability mode 上测量，明确 append、fdatasync/fsync、queue、send handoff和emergency lane。Hosted tmpfs/ephemeral disk结果不能成为PAPER host SLA。

## Target-host qualification

任何低延迟或吞吐宣传必须在目标 host/profile上重复，并绑定：

```text
exact Git/binary/config digest
+ CPU/model/microcode/NUMA
+ kernel/governor/affinity/IRQ
+ compiler/linker/build flags
+ filesystem/mount/journal durability
+ venue/network mode
+ queue/load/fixture/version
+ full distribution and raw or histogram evidence
```

Host tuning 不能替代正确性、journal、risk、reconciliation 或 qualification。关闭安全检查、改变durability、隐藏drops、减少fault coverage或让safe-exit与普通队列竞争，均不是可接受优化。

## Acceptance and regression response

性能 gate 失败时：

1. 保留 exact failing distribution 和 environment；
2. 先确认 correctness/determinism 未变；
3. 对比相同 fixture/toolchain和原始分布；
4. 定位CPU、allocation、lock、I/O、queue和instrumentation变化；
5. 修复实现或提供独立审查的baseline change evidence；
6. 重新跑 exact head 与 merge candidate。

禁止重跑直到偶然成功、删除outlier、只报平均值、降低sample count、扩大threshold、把different host结果混合或用Simulator延迟替代PAPER证据。
'''
    (ROOT / "docs/operations/PERFORMANCE-QUALIFICATION.md").write_text(
        content, encoding="utf-8"
    )


def update_gap_and_matrix() -> None:
    gaps_path = "docs/program/gap-registry-v2.json"
    gaps = load(gaps_path)
    gap_id = "G-PERF-002"
    if any(item.get("id") == gap_id for item in gaps["gaps"]):
        raise SystemExit(f"{gap_id} already exists")
    gaps["gaps"].append(
        {
            "id": gap_id,
            "priority": "P1",
            "title": (
                "Gateway, authoritative snapshot and portfolio compiler lack "
                "same-revision executable repository performance distributions"
            ),
            "workstream": "WS-REL",
            "milestone": "M7",
            "state": "closed",
            "evidence": ["performance-budgets", "sanitizers"],
        }
    )
    write_json(gaps_path, gaps)

    matrix_path = "docs/verification/test-matrix-v2.json"
    matrix = load(matrix_path)
    check = next(item for item in matrix["checks"] if item["id"] == "performance-budgets")
    check["evidence"] = (
        "same-revision risk, Gateway validated-read, authoritative decision "
        "snapshot, portfolio limit-profile and critical OMS durable-append "
        "distributions plus bounded exact global-allocation complexity; target-host "
        "Execution remains explicitly unpromoted"
    )
    write_json(matrix_path, matrix)


def main() -> int:
    write_baselines()
    write_common_header()
    write_gateway_fixture()
    write_snapshot_fixture()
    write_portfolio_fixture()
    write_oms_fixture()
    patch_tests_cmake()
    update_budget_registry()
    write_budget_tests()
    write_performance_document()
    update_gap_and_matrix()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
