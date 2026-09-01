#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(relative: str, old: str, new: str) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    if old not in text:
        if new in text:
            return
        raise RuntimeError(f"missing patch anchor in {relative}: {old[:100]!r}")
    if text.count(old) != 1:
        raise RuntimeError(f"non-unique patch anchor in {relative}: {old[:100]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "HeptaTrade/marketdata/sharded_market_data.cpp",
    '''    return out.str();
}

std::string VectorDigest(const std::vector<MarketDataSnapshot>& snapshots)
''',
    '''    return out.str();
}

std::string SnapshotDigest(const std::string& eventDigest,
                           std::uint64_t generation,
                           bool sequenceGap)
{
    if (!CanonicalDigest(eventDigest) || generation == 0)
        return std::string();
    std::string canonical;
    AppendField(canonical, "schema", "hepta.market-snapshot.v1");
    AppendField(canonical, "event_digest", eventDigest);
    AppendField(canonical, "generation", std::to_string(generation));
    AppendField(canonical, "sequence_gap", sequenceGap ? "1" : "0");
    return Sha256(canonical);
}

std::string VectorDigest(const std::vector<MarketDataSnapshot>& snapshots)
''',
)

replace_once(
    "HeptaTrade/marketdata/sharded_market_data.cpp",
    '''        AppendField(canonical, "generation",
                    std::to_string(snapshots[i].generation));
        AppendField(canonical, "digest", snapshots[i].digest);
''',
    '''        AppendField(canonical, "generation",
                    std::to_string(snapshots[i].generation));
        AppendField(canonical, "sequence_gap",
                    snapshots[i].sequenceGap ? "1" : "0");
        AppendField(canonical, "digest", snapshots[i].digest);
''',
)

replace_once(
    "HeptaTrade/marketdata/sharded_market_data.cpp",
    '''    const std::string expected = EventDigest(event);
    if (expected.empty() || expected != snapshot.digest)
''',
    '''    const std::string eventDigest = EventDigest(event);
    const std::string expected = SnapshotDigest(
        eventDigest, snapshot.generation, snapshot.sequenceGap);
    if (eventDigest.empty() || expected.empty() || expected != snapshot.digest)
''',
)

replace_once(
    "HeptaTrade/marketdata/sharded_market_data.cpp",
    '''    const std::string digest = EventDigest(event);
    if (digest.empty())
''',
    '''    const std::string eventDigest = EventDigest(event);
    if (eventDigest.empty())
''',
)

replace_once(
    "HeptaTrade/marketdata/sharded_market_data.cpp",
    '''        entry.generation = 1;
        entry.sequenceGap = event.sequence != 1;
        entry.digest = digest;
        try
''',
    '''        entry.generation = 1;
        entry.sequenceGap = event.sequence != 1;
        entry.eventDigest = eventDigest;
        entry.digest = SnapshotDigest(
            entry.eventDigest, entry.generation, entry.sequenceGap);
        if (entry.digest.empty())
        {
            --m_size;
            result.reasonCode = "MARKET_DIGEST_FAILED";
            return result;
        }
        try
''',
)

replace_once(
    "HeptaTrade/marketdata/sharded_market_data.cpp",
    '''            if (digest == current.digest)
''',
    '''            if (eventDigest == current.eventDigest)
''',
)

replace_once(
    "HeptaTrade/marketdata/sharded_market_data.cpp",
    '''    current.event = event;
    ++current.generation;
    current.sequenceGap = sequenceGap;
    current.digest = digest;
    result.accepted = true;
    result.sequenceGap = sequenceGap;
    result.generation = current.generation;
    result.digest = digest;
''',
    '''    const std::uint64_t nextGeneration = current.generation + 1u;
    const std::string snapshotDigest = SnapshotDigest(
        eventDigest, nextGeneration, sequenceGap);
    if (snapshotDigest.empty())
    {
        result.reasonCode = "MARKET_DIGEST_FAILED";
        return result;
    }
    current.event = event;
    current.generation = nextGeneration;
    current.sequenceGap = sequenceGap;
    current.eventDigest = eventDigest;
    current.digest = snapshotDigest;
    result.accepted = true;
    result.sequenceGap = sequenceGap;
    result.generation = current.generation;
    result.digest = snapshotDigest;
''',
)

replace_once(
    "tests/sharded_market_data_tests.cpp",
    '''void TestFreshnessAndValidation()
{
    ShardedMarketDataStore store;
    MarketDataEvent event = Event(1, 1);
    assert(store.Apply(event).accepted);
    MarketDataSnapshot snapshot;
    std::string reason;
    assert(!store.GetRiskReady({"SIM", "EUR.USD"}, 1000, snapshot, reason));
    assert(reason == "MARKET_CLOCK_REGRESSION");
    assert(!store.GetRiskReady({"SIM", "EUR.USD"}, 6000, snapshot, reason));
    assert(reason == "MARKET_SNAPSHOT_STALE");

    event = Event(1, 2);
    event.sourceDigest = "bad";
    assert(store.Apply(event).reasonCode == "MARKET_SOURCE_DIGEST_INVALID");
    event = Event(1, 2);
    event.capturedAtMs = event.observedAtMs - 1;
    assert(store.Apply(event).reasonCode == "MARKET_TIME_ENVELOPE_INVALID");
    event = Event(1, 2);
    event.ask = Fixed("1.0");
    assert(store.Apply(event).reasonCode == "MARKET_QUOTE_INVALID");
}
''',
    '''void TestFreshnessAndValidation()
{
    ShardedMarketDataStore store;
    MarketDataEvent event = Event(1, 1);
    const MarketDataWriteResult written = store.Apply(event);
    assert(written.accepted);
    MarketDataSnapshot snapshot;
    std::string reason;
    assert(store.Get({"SIM", "EUR.USD"}, snapshot));
    assert(written.digest == snapshot.digest);
    assert(snapshot.digest != ShardedMarketDataStore::EventDigest(event));
    assert(ShardedMarketDataStore::ValidateSnapshot(snapshot, reason));

    MarketDataSnapshot forged = snapshot;
    ++forged.generation;
    assert(!ShardedMarketDataStore::ValidateSnapshot(forged, reason));
    assert(reason == "MARKET_SNAPSHOT_DIGEST_MISMATCH");
    forged = snapshot;
    forged.sequenceGap = !forged.sequenceGap;
    assert(!ShardedMarketDataStore::ValidateSnapshot(forged, reason));
    assert(reason == "MARKET_SNAPSHOT_DIGEST_MISMATCH");
    forged = snapshot;
    forged.eventId = "field-after-digest-mutation";
    assert(!ShardedMarketDataStore::ValidateSnapshot(forged, reason));
    assert(reason == "MARKET_SNAPSHOT_DIGEST_MISMATCH");

    assert(!store.GetRiskReady({"SIM", "EUR.USD"}, 1000, snapshot, reason));
    assert(reason == "MARKET_CLOCK_REGRESSION");
    assert(!store.GetRiskReady({"SIM", "EUR.USD"}, 6000, snapshot, reason));
    assert(reason == "MARKET_SNAPSHOT_STALE");

    event = Event(1, 2);
    event.sourceDigest = "bad";
    assert(store.Apply(event).reasonCode == "MARKET_SOURCE_DIGEST_INVALID");
    event = Event(1, 2);
    event.capturedAtMs = event.observedAtMs - 1;
    assert(store.Apply(event).reasonCode == "MARKET_TIME_ENVELOPE_INVALID");
    event = Event(1, 2);
    event.ask = Fixed("1.0");
    assert(store.Apply(event).reasonCode == "MARKET_QUOTE_INVALID");
}
''',
)

replace_once(
    "tests/feature_generation_tests.cpp",
    '''    MarketDataSnapshot forged = stale;
    forged.digest = std::string("sha256:") + std::string(64, 'f');
    assert(features.Compute(forged, 1200).reasonCode ==
           "FEATURE_INPUT_INVALID");
    assert(features.Compute(stale, 1200, "unknown").reasonCode ==
''',
    '''    MarketDataSnapshot forged = stale;
    forged.digest = std::string("sha256:") + std::string(64, 'f');
    assert(features.Compute(forged, 1200).reasonCode ==
           "FEATURE_INPUT_INVALID");
    forged = stale;
    ++forged.generation;
    assert(features.Compute(forged, 1200).reasonCode ==
           "FEATURE_INPUT_INVALID");
    forged = stale;
    forged.sequenceGap = !forged.sequenceGap;
    assert(features.Compute(forged, 1200).reasonCode ==
           "FEATURE_INPUT_INVALID");
    assert(features.Compute(stale, 1200, "unknown").reasonCode ==
''',
)

replace_once(
    "docs/architecture/DATAFLOW-AND-CONSISTENCY.md",
    '''4. 重算 canonical event digest，并与 snapshot digest 精确比较。
''',
    '''4. 重算 canonical event digest，再把该 digest、store generation 与 sequence-gap 状态绑定成 snapshot-level digest，并精确比较。
''',
)

replace_once(
    "docs/research/POINT-IN-TIME-DATA-CONTRACT.md",
    '''运行时 `MarketDataSnapshot` 是不受信输入载体：feature 消费前必须重建 market event、验证 fixed-point/quote/time invariant 并重算 digest。多 instrument 决策通过 canonical-order shard locking 读取一个 coherent participating-shard cut；逐 key 释放锁后再拼接的混合 cut 不得签发 authoritative vector digest。每个 vector 仍携带各组件 epoch、sequence、generation 与 fresh-until，调用方不能把 coherent read 误述为未实现的全系统 global generation。
''',
    '''运行时 `MarketDataSnapshot` 是不受信输入载体：feature 消费前必须重建 market event、验证 fixed-point/quote/time invariant，重算 event digest，并验证绑定 event digest、store generation 与 sequence-gap 的 snapshot-level digest。多 instrument 决策通过 canonical-order shard locking 读取一个 coherent participating-shard cut；逐 key 释放锁后再拼接的混合 cut 不得签发 authoritative vector digest。每个 vector 仍携带各组件 epoch、sequence、generation 与 fresh-until，调用方不能把 coherent read 误述为未实现的全系统 global generation。
''',
)

print("[SNAPSHOT-DIGEST-PATCH] PASS")
