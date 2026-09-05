#include "management/module_lifecycle.h"

#include <atomic>
#include <condition_variable>
#include <cstdlib>
#include <iostream>
#include <new>
#include <sstream>
#include <thread>
#include <vector>

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
using State = ModuleLifecycleState;
std::string Digest(char c) { return "sha256:" + std::string(64, c); }
ModuleArtifactIdentity Identity(char artifact = 'a')
{
    ModuleArtifactIdentity i;
    i.moduleId = "hepta.lifecycle.transaction-fixture";
    i.version = artifact == 'a' ? "1.0.0" : "2.0.0";
    i.artifactDigest = Digest(artifact); i.configDigest = Digest('c');
    i.modelDigest = Digest('d');
    return i;
}
ModuleHealthEvidence Health(std::uint64_t time)
{
    ModuleHealthEvidence h; h.healthy = true; h.observedAtMs = time;
    h.evidenceDigest = Digest('e'); return h;
}
std::string Fingerprint(const ModuleLifecycleSnapshot& s)
{
    std::ostringstream out;
    out << s.found << '|' << s.identity.moduleId << '|' << s.identity.version << '|'
        << s.identity.artifactDigest << '|' << s.identity.configDigest << '|'
        << s.identity.modelDigest << '|' << static_cast<int>(s.state) << '|'
        << s.generation << '|' << s.updatedAtMs << '|' << s.health.healthy << '|'
        << s.health.observedAtMs << '|' << s.health.evidenceDigest << '|' << s.reasonCode;
    return out.str();
}
ModuleLifecycleSnapshot Snapshot(ModuleLifecycleRegistry& r, const std::string& id)
{
    ModuleLifecycleSnapshot s; r.Get(id, s); return s;
}
std::string StateFingerprint(ModuleLifecycleRegistry& r, const std::string& id)
{
    ModuleLifecycleSnapshot s;
    const bool exists = r.Get(id, s);
    // Detect a ghost map entry even if its stored found flag is still false.
    return (exists ? "present|" : "absent|") + Fingerprint(s);
}
void Setup(ModuleLifecycleRegistry& r, int operation, const ModuleArtifactIdentity& a,
           const ModuleArtifactIdentity& b)
{
    if (operation == 0) return;
    REQUIRE(r.Register(a, 100).accepted);
    if (operation == 1) return;
    REQUIRE(r.Transition(a.moduleId, 1, State::Warming, Health(110), 110).accepted);
    if (operation == 2) return;
    REQUIRE(r.Transition(a.moduleId, 2, State::Shadow, Health(120), 120).accepted);
    REQUIRE(r.Transition(a.moduleId, 3, State::Active, Health(130), 130).accepted);
    if (operation == 4 || operation == 5)
        REQUIRE(r.StageUpgrade(b, 4, 140).accepted);
    if (operation >= 7)
        REQUIRE(r.Transition(a.moduleId, 4, State::Draining, Health(140), 140).accepted);
    if (operation >= 8)
        REQUIRE(r.Transition(a.moduleId, 5, State::Stopped, Health(150), 150).accepted);
}
ModuleLifecycleResult Mutate(ModuleLifecycleRegistry& r, int operation,
                              const ModuleArtifactIdentity& a,
                              const ModuleArtifactIdentity& b,
                              const ModuleHealthEvidence& h,
                              const std::string& reason)
{
    switch (operation)
    {
    case 0: return r.Register(a, 100);
    case 1: return r.Transition(a.moduleId, 1, State::Warming, h, 160);
    case 2: return r.Transition(a.moduleId, 2, State::Shadow, h, 160);
    case 3: return r.StageUpgrade(b, 4, 160);
    case 4: return r.Quarantine(a.moduleId, 5, reason, 160);
    case 5: return r.Rollback(a.moduleId, 5, h, 160);
    case 6: return r.Transition(a.moduleId, 4, State::Draining, h, 160);
    case 7: return r.Transition(a.moduleId, 5, State::Stopped, h, 160);
    default: return r.Transition(a.moduleId, 6, State::Warming, h, 160);
    }
}

void TestDuplicateTimeAndAliasedReads()
{
    const auto a = Identity(); ModuleLifecycleRegistry r;
    REQUIRE(r.Register(a, 100).accepted);
    const auto before = StateFingerprint(r, a.moduleId);
    REQUIRE(r.Register(a, 99).reasonCode == "MODULE_TIME_REGRESSION");
    REQUIRE(!r.Register(a, 0).accepted);
    const auto duplicate = r.Register(a, 101);
    REQUIRE(duplicate.accepted && duplicate.reasonCode == "MODULE_REGISTRATION_DUPLICATE");
    REQUIRE(duplicate.snapshot.updatedAtMs == 100);
    REQUIRE(StateFingerprint(r, a.moduleId) == before);
    auto s = Snapshot(r, a.moduleId);
    REQUIRE(r.Get(s.identity.moduleId, s) && s.found);
    REQUIRE(s.identity.moduleId == a.moduleId);
    s.identity.artifactDigest = Digest('f');
    REQUIRE(Snapshot(r, a.moduleId).identity.artifactDigest == a.artifactDigest);
    s.identity.moduleId = "hepta.missing";
    REQUIRE(!r.Get(s.identity.moduleId, s) && !s.found && s.identity.moduleId.empty());
}

void TestEveryMutationIsExceptionAtomic()
{
    const auto a = Identity(), b = Identity('b');
    const auto h = Health(160);
    const std::string reason = "LIFECYCLE_ALLOCATION_FAULT_QUARANTINE";
    unsigned long totalFaults = 0;
    for (int operation = 0; operation < 9; ++operation)
    {
        bool completed = false;
        unsigned long faults = 0;
        for (long ordinal = 0; ordinal < 512; ++ordinal)
        {
            ModuleLifecycleRegistry r; Setup(r, operation, a, b);
            const auto before = StateFingerprint(r, a.moduleId);
            const auto old = Snapshot(r, a.moduleId);
            ModuleLifecycleResult result;
            bool threw = false;
            allocation_fault::calls = 0; allocation_fault::failAfter = ordinal;
            try { result = Mutate(r, operation, a, b, h, reason); }
            catch (const std::bad_alloc&) { threw = true; }
            allocation_fault::failAfter = -1;
            if (threw)
            {
                ++faults; ++totalFaults;
                REQUIRE(StateFingerprint(r, a.moduleId) == before);
                // Probe private previous-active state, not only the public snapshot.
                if (operation == 4 || operation == 5)
                {
                    const auto recovered = r.Rollback(a.moduleId, old.generation, h, 170);
                    REQUIRE(recovered.accepted);
                    REQUIRE(recovered.snapshot.identity.artifactDigest == a.artifactDigest);
                }
                else if (operation == 3)
                {
                    // Failed staging must not manufacture a rollback checkpoint.
                    REQUIRE(r.Quarantine(a.moduleId, old.generation, "FAULT", 170).accepted);
                    REQUIRE(r.Rollback(a.moduleId, old.generation + 1, h, 180).reasonCode ==
                            "MODULE_ROLLBACK_UNAVAILABLE");
                }
                else
                {
                    const auto retried = Mutate(r, operation, a, b, h, reason);
                    REQUIRE(retried.accepted);
                    REQUIRE(retried.snapshot.generation == old.generation + 1);
                }
            }
            else
            {
                REQUIRE(result.accepted);
                REQUIRE(result.snapshot.generation == old.generation + 1);
                REQUIRE(Fingerprint(result.snapshot) == Fingerprint(Snapshot(r, a.moduleId)));
                completed = true; break;
            }
        }
        REQUIRE(completed && faults > 0);
    }
    std::cout << "lifecycle_injected_allocation_failures=" << totalFaults << '\n';
}

void TestReadFailurePreservesOutput()
{
    const auto a = Identity(); ModuleLifecycleRegistry r;
    REQUIRE(r.Register(a, 100).accepted);
    unsigned long faults = 0;
    bool completed = false;
    for (long ordinal = 0; ordinal < 128; ++ordinal)
    {
        auto out = Snapshot(r, a.moduleId);
        const auto before = Fingerprint(out);
        bool threw = false, found = false;
        allocation_fault::calls = 0; allocation_fault::failAfter = ordinal;
        try { found = r.Get(out.identity.moduleId, out); }
        catch (const std::bad_alloc&) { threw = true; }
        allocation_fault::failAfter = -1;
        if (threw) { ++faults; REQUIRE(Fingerprint(out) == before); }
        else { REQUIRE(found); REQUIRE(Fingerprint(out) == before); completed = true; break; }
    }
    REQUIRE(completed && faults > 0);
}

void TestTransitionAndRollbackGuards()
{
    const auto a = Identity(), b = Identity('b'); ModuleLifecycleRegistry r;
    Setup(r, 4, a, b); // B Warming, A is previous active
    const auto before = StateFingerprint(r, a.moduleId);
    REQUIRE(r.Rollback(a.moduleId, 4, Health(160), 160).reasonCode == "MODULE_GENERATION_STALE");
    REQUIRE(r.Rollback(a.moduleId, 5, Health(139), 139).reasonCode == "MODULE_TIME_REGRESSION");
    for (int bad = 0; bad < 5; ++bad)
    {
        auto health = Health(160);
        if (bad == 0) health.healthy = false;
        if (bad == 1) health.observedAtMs = 0;
        if (bad == 2) health.observedAtMs = 40000;
        if (bad == 3) health.evidenceDigest = "invalid";
        if (bad == 4) health.observedAtMs = 1;
        const std::uint64_t now = bad == 4 ? 40000 : 160;
        REQUIRE(r.Rollback(a.moduleId, 5, health, now).reasonCode == "MODULE_HEALTH_EVIDENCE_INVALID");
    }
    REQUIRE(r.Transition(a.moduleId, 5, State::Active, Health(160), 160).reasonCode ==
            "MODULE_TRANSITION_INVALID");
    REQUIRE(!r.Quarantine(a.moduleId, 5, "bad reason", 160).accepted);
    REQUIRE(StateFingerprint(r, a.moduleId) == before);
    REQUIRE(r.Quarantine(a.moduleId, 5, "FAULT", 160).accepted);
    const auto rollback = r.Rollback(a.moduleId, 6, Health(170), 170);
    REQUIRE(rollback.accepted && rollback.snapshot.generation == 7);
    REQUIRE(rollback.snapshot.identity.artifactDigest == a.artifactDigest);
    REQUIRE(r.Quarantine(a.moduleId, 7, "FAULT", 180).accepted);
    REQUIRE(r.Rollback(a.moduleId, 8, Health(180), 180).reasonCode == "MODULE_ROLLBACK_UNAVAILABLE");
    REQUIRE(r.ListActive().empty());
}

void Reach(ModuleLifecycleRegistry& r, State desired, const ModuleArtifactIdentity& a)
{
    REQUIRE(r.Register(a, 100).accepted);
    if (desired == State::Registered) return;
    if (desired == State::Quarantined)
    {
        REQUIRE(r.Quarantine(a.moduleId, 1, "FAULT", 110).accepted);
        return;
    }
    const State path[] = {State::Warming, State::Shadow, State::Active,
                          State::Draining, State::Stopped};
    std::uint64_t generation = 1;
    for (State next : path)
    {
        const auto time = 100 + 10 * generation;
        REQUIRE(r.Transition(a.moduleId, generation, next, Health(time), time).accepted);
        ++generation;
        if (next == desired) return;
    }
}

void TestCompleteTransitionMatrixAndHealthBoundary()
{
    const auto a = Identity();
    const State states[] = {State::Registered, State::Warming, State::Shadow,
        State::Active, State::Quarantined, State::Draining, State::Stopped};
    const std::pair<State, State> edges[] = {
        {State::Registered, State::Warming}, {State::Warming, State::Shadow},
        {State::Shadow, State::Active}, {State::Active, State::Draining},
        {State::Draining, State::Stopped}, {State::Quarantined, State::Stopped},
        {State::Stopped, State::Warming}};
    for (State from : states)
        for (State to : states)
        {
            ModuleLifecycleRegistry r; Reach(r, from, a);
            const auto before = Snapshot(r, a.moduleId);
            bool expected = false;
            for (const auto& edge : edges)
                if (edge.first == from && edge.second == to) expected = true;
            const auto result = r.Transition(a.moduleId, before.generation, to, Health(1000), 1000);
            REQUIRE(result.accepted == expected);
            const auto after = Snapshot(r, a.moduleId);
            if (expected)
            {
                REQUIRE(after.generation == before.generation + 1 && after.state == to);
                REQUIRE(after.health.healthy == (to == State::Shadow || to == State::Active));
            }
            else REQUIRE(Fingerprint(before) == Fingerprint(after));
        }
    for (std::uint64_t age : {30000u, 30001u})
    {
        ModuleLifecycleRegistry r; Reach(r, State::Warming, a);
        const auto before = Snapshot(r, a.moduleId);
        const auto result = r.Transition(a.moduleId, before.generation,
            State::Shadow, Health(1000), 1000 + age);
        REQUIRE(result.accepted == (age == 30000u));
        if (!result.accepted) REQUIRE(Fingerprint(before) == Fingerprint(Snapshot(r, a.moduleId)));
    }
    ModuleLifecycleRegistry r; Reach(r, State::Warming, a);
    const auto before = Snapshot(r, a.moduleId);
    REQUIRE(!r.Transition(a.moduleId, before.generation,
                         static_cast<State>(255), Health(1000), 1000).accepted);
    REQUIRE(Fingerprint(before) == Fingerprint(Snapshot(r, a.moduleId)));
}

void TestConcurrentGenerationFence()
{
    const auto a = Identity(); ModuleLifecycleRegistry r;
    REQUIRE(r.Register(a, 100).accepted);
    std::mutex mutex; std::condition_variable cv;
    unsigned int ready = 0; bool go = false;
    std::atomic<unsigned int> accepted(0), stale(0);
    std::vector<std::thread> workers;
    for (unsigned int i = 0; i < 12; ++i)
        workers.emplace_back([&] {
            { std::unique_lock<std::mutex> lock(mutex); ++ready; cv.notify_all();
              cv.wait(lock, [&] { return go; }); }
            const auto result = r.Transition(a.moduleId, 1, State::Warming, Health(110), 110);
            if (result.accepted) ++accepted;
            if (result.reasonCode == "MODULE_GENERATION_STALE") ++stale;
        });
    { std::unique_lock<std::mutex> lock(mutex);
      cv.wait(lock, [&] { return ready == 12; }); go = true; cv.notify_all(); }
    for (auto& worker : workers) worker.join();
    REQUIRE(accepted == 1 && stale == 11);
    REQUIRE(Snapshot(r, a.moduleId).generation == 2);
}

int BaselineProbe()
{
    const auto a = Identity(), b = Identity('b'); const auto h = Health(160);
    const std::string reason = "LIFECYCLE_ALLOCATION_FAULT_QUARANTINE";
    ModuleLifecycleRegistry r; r.Register(a, 100);
    const bool regression = r.Register(a, 99).accepted;
    auto s = Snapshot(r, a.moduleId); const bool aliasFailure = !r.Get(s.identity.moduleId, s);
    unsigned long partial = 0;
    for (int operation = 0; operation < 9; ++operation)
    {
        unsigned long localPartial = 0;
        for (long ordinal = 0; ordinal < 512; ++ordinal)
        {
            ModuleLifecycleRegistry candidate; Setup(candidate, operation, a, b);
            const auto before = StateFingerprint(candidate, a.moduleId);
            bool threw = false; allocation_fault::calls = 0; allocation_fault::failAfter = ordinal;
            try { (void)Mutate(candidate, operation, a, b, h, reason); }
            catch (const std::bad_alloc&) { threw = true; }
            allocation_fault::failAfter = -1;
            if (!threw) break;
            if (StateFingerprint(candidate, a.moduleId) != before) { ++partial; ++localPartial; }
        }
        std::cout << "operation=" << operation << " partial_mutation_failures=" << localPartial << '\n';
    }
    std::cout << "regressed_duplicate_accepted=" << regression << " aliased_read_failed="
              << aliasFailure << " partial_mutation_failures=" << partial << '\n';
    return regression || aliasFailure || partial ? 1 : 0;
}
}
int main(int argc, char** argv)
{
    if (argc == 2 && std::string(argv[1]) == "--probe") return BaselineProbe();
    TestDuplicateTimeAndAliasedReads(); TestEveryMutationIsExceptionAtomic();
    TestReadFailurePreservesOutput(); TestTransitionAndRollbackGuards();
    TestCompleteTransitionMatrixAndHealthBoundary(); TestConcurrentGenerationFence();
    std::cout << "lifecycle_transaction_assertions=" << assertions << '\n';
    return 0;
}
