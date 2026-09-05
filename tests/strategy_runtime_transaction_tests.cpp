#include "strategy_runtime/strategy_runtime_control.h"

#include <atomic>
#include <condition_variable>
#include <cstdlib>
#include <iostream>
#include <new>
#include <sstream>
#include <thread>
#include <vector>

// Deterministic allocation failure, isolated to this test process/thread.
namespace allocation_fault {
thread_local long failAfter = -1;
thread_local long calls = 0;
}
__attribute__((noinline)) void* operator new(std::size_t size)
{
    if (allocation_fault::failAfter >= 0 &&
        allocation_fault::calls++ == allocation_fault::failAfter)
        throw std::bad_alloc();
    void* memory = std::malloc(size == 0 ? 1 : size);
    if (!memory) throw std::bad_alloc();
    return memory;
}
__attribute__((noinline)) void* operator new[](std::size_t size) { return ::operator new(size); }
__attribute__((noinline)) void operator delete(void* memory) noexcept { std::free(memory); }
__attribute__((noinline)) void operator delete[](void* memory) noexcept { std::free(memory); }
#if defined(__cpp_sized_deallocation)
__attribute__((noinline)) void operator delete(void* memory, std::size_t) noexcept { std::free(memory); }
__attribute__((noinline)) void operator delete[](void* memory, std::size_t) noexcept { std::free(memory); }
#endif

namespace {
unsigned long assertions = 0;
void Require(bool value, const char* expression, int line)
{
    ++assertions;
    if (value) return;
    std::cerr << "failed at " << line << ": " << expression << '\n';
    std::abort();
}
#define REQUIRE(x) Require(static_cast<bool>(x), #x, __LINE__)

std::string Digest(char c) { return "sha256:" + std::string(64, c); }
StrategyArtifactDescriptor Descriptor()
{
    StrategyArtifactDescriptor d;
    d.moduleId = "hepta.strategy.transaction-fixture";
    d.version = "1.0.0";
    d.artifactDigest = Digest('a'); d.configDigest = Digest('b');
    d.modelDigest = Digest('c');
    d.budget.maxThreads = 2; d.budget.maxFileDescriptors = 32;
    d.budget.maxMemoryBytes = 65536; d.budget.maxCheckpointBytes = 4096;
    return d;
}
std::string Fingerprint(const StrategyRuntimeSnapshot& s)
{
    std::ostringstream out;
    const StrategyArtifactDescriptor& d = s.descriptor;
    out << s.found << '|' << d.moduleId << '|' << d.version << '|'
        << d.artifactDigest << '|' << d.configDigest << '|' << d.modelDigest << '|'
        << d.budget.maxThreads << '|' << d.budget.maxFileDescriptors << '|'
        << d.budget.maxMemoryBytes << '|' << d.budget.maxCheckpointBytes << '|'
        << static_cast<int>(s.phase) << '|' << s.generation << '|' << s.updatedAtMs
        << '|' << s.checkpointSequence << '|' << s.checkpointDigest << '|'
        << s.checkpointBytes << '|' << s.reasonCode;
    return out.str();
}
StrategyRuntimeSnapshot Snapshot(StrategyRuntimeControl& c, const std::string& id)
{
    StrategyRuntimeSnapshot s; c.Get(id, s); return s;
}

void TestTimeAndCheckpointIdentity()
{
    auto d = Descriptor(); StrategyRuntimeControl c;
    REQUIRE(c.Admit(d, 100).accepted);
    const auto first = Snapshot(c, d.moduleId);
    const auto rollback = c.Admit(d, 99);
    REQUIRE(!rollback.accepted && rollback.reasonCode == "STRATEGY_TIME_REGRESSION");
    REQUIRE(Fingerprint(Snapshot(c, d.moduleId)) == Fingerprint(first));
    REQUIRE(!c.Admit(d, 0).accepted);
    const auto duplicate = c.Admit(d, 101);
    REQUIRE(duplicate.accepted && duplicate.duplicate);
    REQUIRE(duplicate.snapshot.updatedAtMs == 100);
    REQUIRE(c.Start(d.moduleId, 1, d.artifactDigest, 110).accepted);
    REQUIRE(c.Checkpoint(d.moduleId, 2, 4, Digest('d'), 1024, 120).accepted);
    const auto committed = Snapshot(c, d.moduleId);
    REQUIRE(committed.checkpointBytes == 1024 && committed.generation == 3);
    const auto bad = c.Checkpoint(d.moduleId, 3, 4, Digest('d'), 1025, 121);
    REQUIRE(!bad.accepted && bad.reasonCode == "STRATEGY_CHECKPOINT_SEQUENCE_CONFLICT");
    REQUIRE(Fingerprint(Snapshot(c, d.moduleId)) == Fingerprint(committed));
    REQUIRE(!c.Checkpoint(d.moduleId, 3, 4, Digest('e'), 1024, 121).accepted);
    REQUIRE(!c.Checkpoint(d.moduleId, 3, 3, Digest('d'), 1024, 121).accepted);
    REQUIRE(!c.Checkpoint(d.moduleId, 3, 5, Digest('d'), 0, 121).accepted);
    REQUIRE(!c.Checkpoint(d.moduleId, 3, 5, Digest('d'), 4097, 121).accepted);
    REQUIRE(!c.Checkpoint(d.moduleId, 3, 5, "sha256:invalid", 1024, 121).accepted);
    REQUIRE(!c.Checkpoint(d.moduleId, 2, 5, Digest('d'), 1024, 121).accepted);
    REQUIRE(!c.Checkpoint(d.moduleId, 3, 5, Digest('d'), 1024, 119).accepted);
    const auto same = c.Checkpoint(d.moduleId, 3, 4, Digest('d'), 1024, 130);
    REQUIRE(same.accepted && same.duplicate);
    REQUIRE(Fingerprint(same.snapshot) == Fingerprint(committed));
    REQUIRE(c.Quarantine(d.moduleId, 3, "HEALTH_DRIFT", 140).accepted);
    d.version = "1.1.0"; d.artifactDigest = Digest('f');
    const auto replaced = c.Replace(d, 4, 150);
    REQUIRE(replaced.accepted && replaced.snapshot.generation == 5);
    REQUIRE(replaced.snapshot.checkpointSequence == 0);
    REQUIRE(replaced.snapshot.checkpointDigest.empty());
    REQUIRE(replaced.snapshot.checkpointBytes == 0);
    REQUIRE(c.Stop(d.moduleId, 5, 160).accepted);
    const auto stopped = Snapshot(c, d.moduleId);
    REQUIRE(!c.Stop(d.moduleId, 6, 159).accepted);
    REQUIRE(c.Stop(d.moduleId, 6, 160).duplicate);
    REQUIRE(!c.Start(d.moduleId, 6, d.artifactDigest, 170).accepted);
    REQUIRE(Fingerprint(Snapshot(c, d.moduleId)) == Fingerprint(stopped));
}

// Each operation is retried from identical initial state, failing every
// allocation ordinal until the first non-failing call. No timing or sleeps.
void TestAllocationAtomicity()
{
    const auto d = Descriptor();
    auto replacement = d;
    replacement.version = "2.0.0"; replacement.artifactDigest = Digest('f');
    const std::string digest = Digest('d');
    const std::string reason = "HEALTH_QUARANTINE_ALLOCATION_TEST";
    unsigned long totalFaults = 0;
    for (int operation = 0; operation < 6; ++operation)
    {
        bool succeeded = false;
        unsigned long faults = 0;
        for (long ordinal = 0; ordinal < 256; ++ordinal)
        {
            StrategyRuntimeControl c(1);
            if (operation > 0) REQUIRE(c.Admit(d, 100).accepted);
            if (operation >= 2) REQUIRE(c.Start(d.moduleId, 1, d.artifactDigest, 110).accepted);
            if (operation == 4)
                REQUIRE(c.Quarantine(d.moduleId, 2, reason, 120).accepted);
            const auto before = Snapshot(c, d.moduleId);
            bool threw = false;
            StrategyRuntimeControlResult result;
            allocation_fault::calls = 0;
            allocation_fault::failAfter = ordinal;
            try
            {
                switch (operation)
                {
                case 0: result = c.Admit(d, 100); break;
                case 1: result = c.Start(d.moduleId, 1, d.artifactDigest, 110); break;
                case 2: result = c.Checkpoint(d.moduleId, 2, 1, digest, 1024, 130); break;
                case 3: result = c.Quarantine(d.moduleId, 2, reason, 130); break;
                case 4: result = c.Replace(replacement, 3, 130); break;
                case 5: result = c.Stop(d.moduleId, 2, 130); break;
                }
            }
            catch (const std::bad_alloc&) { threw = true; }
            allocation_fault::failAfter = -1;
            const auto after = Snapshot(c, d.moduleId);
            if (threw)
            {
                ++faults; ++totalFaults;
                REQUIRE(Fingerprint(after) == Fingerprint(before));
                if (operation == 0) REQUIRE(c.Admit(d, 100).accepted);
            }
            else
            {
                REQUIRE(result.accepted);
                REQUIRE(Fingerprint(result.snapshot) == Fingerprint(after));
                REQUIRE(after.generation == before.generation + 1);
                succeeded = true; break;
            }
        }
        REQUIRE(succeeded && faults != 0);
    }
    std::cout << "allocation_faults=" << totalFaults << '\n';
}

void TestConcurrentGenerationFence()
{
    const auto d = Descriptor(); StrategyRuntimeControl c;
    REQUIRE(c.Admit(d, 100).accepted);
    REQUIRE(c.Start(d.moduleId, 1, d.artifactDigest, 110).accepted);
    std::mutex mutex; std::condition_variable cv;
    unsigned int ready = 0; bool go = false;
    std::atomic<unsigned int> accepted(0), stale(0);
    std::vector<std::thread> workers;
    for (unsigned int i = 0; i < 12; ++i)
        workers.emplace_back([&, i] {
            { std::unique_lock<std::mutex> lock(mutex); ++ready; cv.notify_all();
              cv.wait(lock, [&] { return go; }); }
            const auto result = c.Checkpoint(d.moduleId, 2, i + 1, Digest('d'), i + 1, 120);
            if (result.accepted) ++accepted;
            if (result.reasonCode == "STRATEGY_GENERATION_STALE") ++stale;
        });
    { std::unique_lock<std::mutex> lock(mutex);
      cv.wait(lock, [&] { return ready == 12; }); go = true; cv.notify_all(); }
    for (auto& worker : workers) worker.join();
    REQUIRE(accepted == 1 && stale == 11);
    const auto s = Snapshot(c, d.moduleId);
    REQUIRE(s.generation == 3 && s.checkpointBytes == s.checkpointSequence);
}

void TestCapacityAndIsolation()
{
    const auto d = Descriptor(); StrategyRuntimeControl empty(0), c(1);
    REQUIRE(!empty.Admit(d, 100).accepted);
    REQUIRE(c.Admit(d, 100).accepted);
    auto other = d; other.moduleId = "hepta.strategy.other";
    REQUIRE(c.Admit(other, 100).reasonCode == "STRATEGY_CAPACITY_EXHAUSTED");
    StrategyRuntimeSnapshot s;
    REQUIRE(c.Get(d.moduleId, s));
    REQUIRE(c.Get(s.descriptor.moduleId, s) && s.found);
    s.descriptor.artifactDigest = Digest('e');
    REQUIRE(c.Start(d.moduleId, 1, d.artifactDigest, 110).accepted);
    REQUIRE(!c.Get(other.moduleId, s) && !s.found);
    REQUIRE(c.Stop(other.moduleId, 1, 120).reasonCode == "STRATEGY_NOT_FOUND");
}
}
int main()
{
    TestTimeAndCheckpointIdentity(); TestAllocationAtomicity();
    TestConcurrentGenerationFence(); TestCapacityAndIsolation();
    std::cout << "strategy_transaction_assertions=" << assertions << '\n';
    return 0;
}
