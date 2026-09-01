#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def replace(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        if new in text:
            return
        raise RuntimeError(f"missing anchor in {path}: {old[:80]!r}")
    if text.count(old) != 1:
        raise RuntimeError(f"non-unique anchor in {path}: {old[:80]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


write("HeptaTrade/numeric/fixed_decimal.h", r'''#pragma once

#include <cmath>
#include <cstdint>
#include <string>

class HeptaFixedDecimal
{
public:
    typedef std::int64_t Rep;
    static const Rep kScale = 1000000;
    // Fixed raw units are authoritative. Compatibility conversion to binary64
    // is explicitly fallible and must round-trip to the identical raw value.
    static const Rep kMaximumRaw = 9000000000000000LL;

    HeptaFixedDecimal() noexcept : m_raw(0) {}

    static bool ParseCanonical(
        const std::string& text,
        HeptaFixedDecimal& out,
        std::string& reason) noexcept;

    static bool FromRawExact(
        Rep raw,
        HeptaFixedDecimal& out,
        std::string& reason) noexcept;

    static bool FromDoubleExact(
        double value,
        HeptaFixedDecimal& out,
        std::string& reason) noexcept;

    static bool IsExactlyRepresentable(double value) noexcept;

    static bool CheckedAdd(
        HeptaFixedDecimal left,
        HeptaFixedDecimal right,
        HeptaFixedDecimal& out) noexcept;

    static bool CheckedSubtract(
        HeptaFixedDecimal left,
        HeptaFixedDecimal right,
        HeptaFixedDecimal& out) noexcept;

    bool IsValid() const noexcept
    {
        return m_raw >= -kMaximumRaw && m_raw <= kMaximumRaw;
    }
    Rep Raw() const noexcept { return m_raw; }

    // Produce a compatibility binary64 value only when it maps back to the
    // exact same microunit. This prevents adjacent high-magnitude fixed values
    // from collapsing onto one double at a wire/venue seam.
    bool ToDoubleExact(double& out, std::string& reason) const noexcept;
    std::string ToCanonicalString() const;

    friend bool operator==(HeptaFixedDecimal left, HeptaFixedDecimal right)
    {
        return left.m_raw == right.m_raw;
    }
    friend bool operator!=(HeptaFixedDecimal left, HeptaFixedDecimal right)
    {
        return !(left == right);
    }
    friend bool operator<(HeptaFixedDecimal left, HeptaFixedDecimal right)
    {
        return left.m_raw < right.m_raw;
    }

private:
    explicit HeptaFixedDecimal(Rep raw, bool) noexcept : m_raw(raw) {}
    Rep m_raw;
};
''')

replace(
    "HeptaTrade/numeric/fixed_decimal.cpp",
    "    out = HeptaFixedDecimal(raw);\n    return true;\n}\n\nbool HeptaFixedDecimal::FromDoubleExact(",
    "    out = HeptaFixedDecimal(raw, true);\n    return true;\n}\n\n"
    "bool HeptaFixedDecimal::FromRawExact(\n"
    "    Rep raw,\n"
    "    HeptaFixedDecimal& out,\n"
    "    std::string& reason) noexcept\n"
    "{\n"
    "    out = HeptaFixedDecimal();\n"
    "    if (raw < -kMaximumRaw || raw > kMaximumRaw)\n"
    "    {\n"
    "        reason = \"NUMERIC_RANGE_EXCEEDED\";\n"
    "        return false;\n"
    "    }\n"
    "    out = HeptaFixedDecimal(raw == 0 ? 0 : raw, true);\n"
    "    reason.clear();\n"
    "    return true;\n"
    "}\n\n"
    "bool HeptaFixedDecimal::IsExactlyRepresentable(double value) noexcept\n"
    "{\n"
    "    HeptaFixedDecimal out;\n"
    "    std::string reason;\n"
    "    return FromDoubleExact(value, out, reason);\n"
    "}\n\n"
    "bool HeptaFixedDecimal::FromDoubleExact("
)
replace(
    "HeptaTrade/numeric/fixed_decimal.cpp",
    "    out = HeptaFixedDecimal(raw == 0 ? 0 : raw);\n    reason.clear();\n    return true;\n}\n\nbool HeptaFixedDecimal::CheckedAdd(",
    "    out = HeptaFixedDecimal(raw == 0 ? 0 : raw, true);\n"
    "    reason.clear();\n"
    "    return true;\n"
    "}\n\n"
    "bool HeptaFixedDecimal::ToDoubleExact(\n"
    "    double& out, std::string& reason) const noexcept\n"
    "{\n"
    "    out = 0.0;\n"
    "    if (!IsValid())\n"
    "    {\n"
    "        reason = \"NUMERIC_RANGE_EXCEEDED\";\n"
    "        return false;\n"
    "    }\n"
    "    const double candidate = static_cast<double>(m_raw) /\n"
    "        static_cast<double>(kScale);\n"
    "    HeptaFixedDecimal recovered;\n"
    "    std::string recoveredReason;\n"
    "    if (!FromDoubleExact(candidate, recovered, recoveredReason) ||\n"
    "        recovered.m_raw != m_raw)\n"
    "    {\n"
    "        reason = \"NUMERIC_DOUBLE_PROJECTION_LOSS\";\n"
    "        return false;\n"
    "    }\n"
    "    out = candidate;\n"
    "    reason.clear();\n"
    "    return true;\n"
    "}\n\n"
    "bool HeptaFixedDecimal::CheckedAdd("
)
replace(
    "HeptaTrade/numeric/fixed_decimal.cpp",
    "    out = HeptaFixedDecimal(left.m_raw + right.m_raw);",
    "    out = HeptaFixedDecimal(left.m_raw + right.m_raw, true);"
)
replace(
    "HeptaTrade/numeric/fixed_decimal.cpp",
    "    out = HeptaFixedDecimal(left.m_raw - right.m_raw);",
    "    out = HeptaFixedDecimal(left.m_raw - right.m_raw, true);"
)
replace(
    "HeptaTrade/tool_host/typed_tool_protocol.cpp",
    "    out = fixed.ToDouble();\n    return true;",
    "    return fixed.ToDoubleExact(out, reason);"
)

replace(
    "HeptaTrade/marketdata/sharded_market_data.h",
    "#include <cstdint>\n#include <map>",
    "#include <cstdint>\n#include <functional>\n#include <map>"
)
replace(
    "HeptaTrade/marketdata/sharded_market_data.h",
    "    static std::string EventDigest(const MarketDataEvent& event);\n\nprivate:",
    "    static std::string EventDigest(const MarketDataEvent& event);\n"
    "    static bool ValidateSnapshot(const MarketDataSnapshot& snapshot,\n"
    "                                 std::string& reason);\n"
    "    void SetReadVectorLocksAcquiredHookForTesting(\n"
    "        const std::function<void()>& hook);\n\nprivate:"
)
replace(
    "HeptaTrade/marketdata/sharded_market_data.h",
    "    const std::size_t m_maximumKeys;\n};",
    "    const std::size_t m_maximumKeys;\n"
    "    mutable std::mutex m_vectorHookMutex;\n"
    "    std::function<void()> m_vectorLocksAcquiredHook;\n};"
)
replace(
    "HeptaTrade/marketdata/sharded_market_data.cpp",
    "    if (event.ask < event.bid || event.bidSize.Raw() < 0 ||\n        event.askSize.Raw() < 0)",
    "    if (!event.bid.IsValid() || !event.ask.IsValid() ||\n"
    "        !event.last.IsValid() || !event.bidSize.IsValid() ||\n"
    "        !event.askSize.IsValid() || event.ask < event.bid ||\n"
    "        event.bidSize.Raw() < 0 || event.askSize.Raw() < 0)"
)
replace(
    "HeptaTrade/marketdata/sharded_market_data.cpp",
    "    return Sha256(canonical);\n}\n\nbool ShardedMarketDataStore::ReserveKey()",
    "    return Sha256(canonical);\n"
    "}\n\n"
    "bool ShardedMarketDataStore::ValidateSnapshot(\n"
    "    const MarketDataSnapshot& snapshot, std::string& reason)\n"
    "{\n"
    "    if (!snapshot.found || snapshot.generation == 0 ||\n"
    "        !CanonicalDigest(snapshot.digest))\n"
    "    {\n"
    "        reason = \"MARKET_SNAPSHOT_INCOMPLETE\";\n"
    "        return false;\n"
    "    }\n"
    "    MarketDataEvent event;\n"
    "    event.eventId = snapshot.eventId;\n"
    "    event.producer = snapshot.producer;\n"
    "    event.venue = snapshot.key.venue;\n"
    "    event.instrument = snapshot.key.instrument;\n"
    "    event.sourceDigest = snapshot.sourceDigest;\n"
    "    event.producerEpoch = snapshot.producerEpoch;\n"
    "    event.sequence = snapshot.sequence;\n"
    "    event.observedAtMs = snapshot.observedAtMs;\n"
    "    event.capturedAtMs = snapshot.capturedAtMs;\n"
    "    event.freshUntilMs = snapshot.freshUntilMs;\n"
    "    event.bid = snapshot.bid;\n"
    "    event.ask = snapshot.ask;\n"
    "    event.last = snapshot.last;\n"
    "    event.bidSize = snapshot.bidSize;\n"
    "    event.askSize = snapshot.askSize;\n"
    "    if (!ValidateEvent(event, reason)) return false;\n"
    "    const std::string expected = EventDigest(event);\n"
    "    if (expected.empty() || expected != snapshot.digest)\n"
    "    {\n"
    "        reason = \"MARKET_SNAPSHOT_DIGEST_MISMATCH\";\n"
    "        return false;\n"
    "    }\n"
    "    reason.clear();\n"
    "    return true;\n"
    "}\n\n"
    "void ShardedMarketDataStore::SetReadVectorLocksAcquiredHookForTesting(\n"
    "    const std::function<void()>& hook)\n"
    "{\n"
    "    std::lock_guard<std::mutex> lock(m_vectorHookMutex);\n"
    "    m_vectorLocksAcquiredHook = hook;\n"
    "}\n\n"
    "bool ShardedMarketDataStore::ReserveKey()"
)
replace(
    "HeptaTrade/marketdata/sharded_market_data.cpp",
    "    if (out.sequenceGap)\n    {",
    "    if (!ValidateSnapshot(out, reason)) return false;\n"
    "    if (out.sequenceGap)\n    {"
)
market_path = ROOT / "HeptaTrade/marketdata/sharded_market_data.cpp"
market = market_path.read_text(encoding="utf-8")
market = re.sub(
    r"bool ShardedMarketDataStore::ReadVector\(.*\Z",
    r'''bool ShardedMarketDataStore::ReadVector(
    const std::vector<MarketDataKey>& keys,
    std::uint64_t nowMs,
    MarketDataSnapshotVector& out,
    std::string& reason) const
{
    out = MarketDataSnapshotVector();
    if (keys.empty() || keys.size() > 256u)
    {
        reason = "MARKET_VECTOR_SIZE_INVALID";
        return false;
    }
    std::vector<MarketDataKey> ordered = keys;
    std::sort(ordered.begin(), ordered.end());
    for (std::size_t i = 1; i < ordered.size(); ++i)
    {
        if (ordered[i] == ordered[i - 1])
        {
            reason = "MARKET_VECTOR_DUPLICATE_KEY";
            return false;
        }
    }

    std::vector<std::size_t> shardIds;
    shardIds.reserve(ordered.size());
    for (std::size_t i = 0; i < ordered.size(); ++i)
        shardIds.push_back(ShardFor(ordered[i]));
    std::sort(shardIds.begin(), shardIds.end());
    shardIds.erase(std::unique(shardIds.begin(), shardIds.end()),
                   shardIds.end());

    // Lock the complete target shard set in canonical order. No writer can
    // advance one component while the vector is being assembled, so the
    // resulting digest identifies one coherent store cut.
    std::vector<std::unique_lock<std::mutex> > locks;
    locks.reserve(shardIds.size());
    for (std::size_t i = 0; i < shardIds.size(); ++i)
        locks.push_back(std::unique_lock<std::mutex>(
            m_shards[shardIds[i]].mutex));

    std::function<void()> hook;
    {
        std::lock_guard<std::mutex> hookLock(m_vectorHookMutex);
        hook = m_vectorLocksAcquiredHook;
    }
    if (hook) hook();

    out.components.reserve(ordered.size());
    for (std::size_t i = 0; i < ordered.size(); ++i)
    {
        const Shard& shard = m_shards[ShardFor(ordered[i])];
        const std::map<MarketDataKey, Entry>::const_iterator found =
            shard.entries.find(ordered[i]);
        if (found == shard.entries.end())
        {
            out = MarketDataSnapshotVector();
            reason = "MARKET_SNAPSHOT_MISSING";
            return false;
        }
        MarketDataSnapshot snapshot = Snapshot(found->second);
        if (!ValidateSnapshot(snapshot, reason))
        {
            out = MarketDataSnapshotVector();
            return false;
        }
        if (snapshot.sequenceGap)
        {
            out = MarketDataSnapshotVector();
            reason = "MARKET_SEQUENCE_GAP";
            return false;
        }
        if (nowMs < snapshot.capturedAtMs)
        {
            out = MarketDataSnapshotVector();
            reason = "MARKET_CLOCK_REGRESSION";
            return false;
        }
        if (nowMs > snapshot.freshUntilMs)
        {
            out = MarketDataSnapshotVector();
            reason = "MARKET_SNAPSHOT_STALE";
            return false;
        }
        out.components.push_back(snapshot);
    }
    out.digest = VectorDigest(out.components);
    if (out.digest.empty())
    {
        out = MarketDataSnapshotVector();
        reason = "MARKET_VECTOR_DIGEST_FAILED";
        return false;
    }
    reason.clear();
    return true;
}
''',
    market,
    flags=re.S,
)
market_path.write_text(market, encoding="utf-8")

replace(
    "HeptaTrade/features/feature_generation.cpp",
    "    if (input.sequenceGap)\n    {",
    "    std::string marketReason;\n"
    "    if (!ShardedMarketDataStore::ValidateSnapshot(input, marketReason))\n"
    "    {\n"
    "        result.reasonCode = \"FEATURE_INPUT_INVALID\";\n"
    "        return result;\n"
    "    }\n"
    "    if (input.sequenceGap)\n    {"
)
replace(
    "HeptaTrade/features/feature_generation.cpp",
    "    snapshot.mid = HeptaFixedDecimal(bidAskSum.Raw() / 2);\n    snapshot.spread = spread;",
    "    std::string numericReason;\n"
    "    if (!HeptaFixedDecimal::FromRawExact(\n"
    "            bidAskSum.Raw() / 2, snapshot.mid, numericReason))\n"
    "    {\n"
    "        if (found == shard.entries.end()) --m_size;\n"
    "        result.reasonCode = \"FEATURE_NUMERIC_OVERFLOW\";\n"
    "        return result;\n"
    "    }\n"
    "    snapshot.spread = spread;"
)

write("tests/fixed_decimal_tests.cpp", r'''#include "../HeptaTrade/numeric/fixed_decimal.h"

#include <cassert>
#include <limits>
#include <string>
#include <type_traits>

namespace
{
HeptaFixedDecimal Parse(const char* value)
{
    HeptaFixedDecimal out;
    std::string reason;
    assert(HeptaFixedDecimal::ParseCanonical(value, out, reason));
    assert(reason.empty());
    return out;
}

HeptaFixedDecimal Raw(HeptaFixedDecimal::Rep value)
{
    HeptaFixedDecimal out;
    std::string reason;
    assert(HeptaFixedDecimal::FromRawExact(value, out, reason));
    return out;
}

void TestCanonicalVectors()
{
    static_assert(!std::is_constructible<
        HeptaFixedDecimal, HeptaFixedDecimal::Rep>::value,
        "raw construction must remain checked");
    assert(Parse("0").Raw() == 0);
    assert(Parse("1").Raw() == 1000000);
    assert(Parse("-1.25").Raw() == -1250000);
    assert(Parse("0.000001").Raw() == 1);
    assert(Parse("1e-6").Raw() == 1);
    assert(Parse("12.340000").ToCanonicalString() == "12.34");
    assert(Parse("-0.000001").ToCanonicalString() == "-0.000001");
}

void TestInvalidVectors()
{
    const char* invalid[] = {
        "", "+1", "01", ".1", "1.", "-0", "-0.0", "nan", "inf",
        "1e", "1.0000001", "9000000000.000001"
    };
    for (std::size_t i = 0; i < sizeof(invalid) / sizeof(invalid[0]); ++i)
    {
        HeptaFixedDecimal out;
        std::string reason;
        assert(!HeptaFixedDecimal::ParseCanonical(invalid[i], out, reason));
        assert(!reason.empty());
        assert(out.Raw() == 0);
    }
    HeptaFixedDecimal out;
    std::string reason;
    assert(!HeptaFixedDecimal::FromRawExact(
        HeptaFixedDecimal::kMaximumRaw + 1, out, reason));
    assert(reason == "NUMERIC_RANGE_EXCEEDED");
}

void TestDoubleBoundary()
{
    HeptaFixedDecimal out;
    std::string reason;
    assert(HeptaFixedDecimal::FromDoubleExact(0.1, out, reason));
    assert(out.Raw() == 100000);
    assert(out.ToCanonicalString() == "0.1");
    assert(!HeptaFixedDecimal::FromDoubleExact(0.1234567, out, reason));
    assert(reason == "NUMERIC_SCALE_MISMATCH");
    assert(!HeptaFixedDecimal::FromDoubleExact(
        std::numeric_limits<double>::infinity(), out, reason));
    assert(!HeptaFixedDecimal::FromDoubleExact(-0.0, out, reason));

    bool sawProjectionLoss = false;
    bool havePrevious = false;
    double previous = 0.0;
    for (HeptaFixedDecimal::Rep raw =
             HeptaFixedDecimal::kMaximumRaw - 4096;
         raw <= HeptaFixedDecimal::kMaximumRaw; ++raw)
    {
        const HeptaFixedDecimal fixed = Raw(raw);
        double projected = 0.0;
        if (!fixed.ToDoubleExact(projected, reason))
        {
            assert(reason == "NUMERIC_DOUBLE_PROJECTION_LOSS");
            sawProjectionLoss = true;
            continue;
        }
        HeptaFixedDecimal recovered;
        assert(HeptaFixedDecimal::FromDoubleExact(
            projected, recovered, reason));
        assert(recovered == fixed);
        if (havePrevious) assert(projected > previous);
        previous = projected;
        havePrevious = true;
    }
    assert(sawProjectionLoss);
}

void TestCheckedArithmetic()
{
    HeptaFixedDecimal result;
    assert(HeptaFixedDecimal::CheckedAdd(
        Parse("1.25"), Parse("2.75"), result));
    assert(result.ToCanonicalString() == "4");
    assert(HeptaFixedDecimal::CheckedSubtract(
        Parse("1.25"), Parse("2.75"), result));
    assert(result.ToCanonicalString() == "-1.5");

    const HeptaFixedDecimal maximum = Raw(HeptaFixedDecimal::kMaximumRaw);
    assert(!HeptaFixedDecimal::CheckedAdd(maximum, Raw(1), result));
    const HeptaFixedDecimal minimum = Raw(-HeptaFixedDecimal::kMaximumRaw);
    assert(!HeptaFixedDecimal::CheckedSubtract(minimum, Raw(1), result));
}
}

int main()
{
    TestCanonicalVectors();
    TestInvalidVectors();
    TestDoubleBoundary();
    TestCheckedArithmetic();
    return 0;
}
''')

feature_path = ROOT / "tests/feature_generation_tests.cpp"
feature = feature_path.read_text(encoding="utf-8")
feature = feature.replace(
    '    assert(features.Compute(odd, 1200).reasonCode ==\n           "FEATURE_NUMERIC_SCALE_MISMATCH");',
    '    assert(features.Compute(odd, 1200).reasonCode ==\n           "FEATURE_INPUT_INVALID");\n'
    '    MarketDataSnapshot forged = stale;\n'
    '    forged.digest = std::string("sha256:") + std::string(64, \'f\');\n'
    '    assert(features.Compute(forged, 1200).reasonCode ==\n'
    '           "FEATURE_INPUT_INVALID");'
)
feature_path.write_text(feature, encoding="utf-8")

market_test = ROOT / "tests/sharded_market_data_tests.cpp"
text = market_test.read_text(encoding="utf-8")
text = text.replace(
    "#include <cassert>\n#include <string>",
    "#include <atomic>\n#include <cassert>\n#include <chrono>\n"
    "#include <condition_variable>\n#include <mutex>\n#include <string>"
)
anchor = "void TestIndependentShardProgress()\n{"
coherence = r'''void TestVectorIsOneCoherentCut()
{
    ShardedMarketDataStore store(8);
    MarketDataEvent first = Event(1, 1);
    MarketDataEvent second = Event(1, 1);
    second.instrument = "VECTOR.B";
    while (ShardedMarketDataStore::ShardFor(
               {second.venue, second.instrument}) ==
           ShardedMarketDataStore::ShardFor(
               {first.venue, first.instrument}))
        second.instrument.push_back('X');
    second.eventId = "vector-second";
    assert(store.Apply(first).accepted);
    assert(store.Apply(second).accepted);

    std::mutex gateMutex;
    std::condition_variable gate;
    bool locked = false;
    bool release = false;
    store.SetReadVectorLocksAcquiredHookForTesting([&]() {
        std::unique_lock<std::mutex> lock(gateMutex);
        locked = true;
        gate.notify_all();
        gate.wait(lock, [&]() { return release; });
    });

    MarketDataSnapshotVector vector;
    std::string reason;
    std::thread reader([&]() {
        assert(store.ReadVector(
            {{first.venue, first.instrument},
             {second.venue, second.instrument}},
            1200, vector, reason));
    });
    {
        std::unique_lock<std::mutex> lock(gateMutex);
        gate.wait(lock, [&]() { return locked; });
    }

    std::atomic<bool> writerFinished(false);
    std::thread writer([&]() {
        MarketDataEvent update = first;
        update.eventId = "event-1-2";
        update.sequence = 2;
        update.observedAtMs = 1002;
        update.capturedAtMs = 1102;
        update.freshUntilMs = 5002;
        assert(store.Apply(update).accepted);
        writerFinished.store(true);
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    assert(!writerFinished.load());
    {
        std::lock_guard<std::mutex> lock(gateMutex);
        release = true;
    }
    gate.notify_all();
    reader.join();
    writer.join();
    store.SetReadVectorLocksAcquiredHookForTesting(std::function<void()>());

    assert(vector.components.size() == 2);
    for (std::size_t i = 0; i < vector.components.size(); ++i)
        assert(vector.components[i].sequence == 1);

    MarketDataSnapshotVector after;
    assert(store.ReadVector(
        {{first.venue, first.instrument},
         {second.venue, second.instrument}},
        1200, after, reason));
    bool sawUpdated = false;
    for (std::size_t i = 0; i < after.components.size(); ++i)
        if (after.components[i].key.instrument == first.instrument)
            sawUpdated = after.components[i].sequence == 2;
    assert(sawUpdated);
}

void TestIndependentShardProgress()
{'''
if anchor not in text:
    raise RuntimeError("market coherence anchor missing")
text = text.replace(anchor, coherence, 1)
text = text.replace(
    "    TestCapacityAndVector();\n    TestIndependentShardProgress();",
    "    TestCapacityAndVector();\n    TestVectorIsOneCoherentCut();\n"
    "    TestIndependentShardProgress();"
)
market_test.write_text(text, encoding="utf-8")
