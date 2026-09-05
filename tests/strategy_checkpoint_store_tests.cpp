#include "strategy_runtime/strategy_checkpoint_store.h"

#include <atomic>
#include <condition_variable>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <new>
#include <openssl/evp.h>
#include <sstream>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <thread>
#include <fcntl.h>
#include <unistd.h>
#include <vector>

namespace faults {
thread_local long allocation = -1, calls = 0;
thread_local int syncAt = 0, syncCalls = 0;
thread_local bool rename = false, partialWrite = false, partialRead = false;
thread_local int writes = 0, reads = 0;
thread_local bool exitAfterWrite = false;
}
__attribute__((noinline)) void* operator new(std::size_t size)
{
    if (faults::allocation >= 0 && faults::calls++ == faults::allocation) throw std::bad_alloc();
    void* ptr = std::malloc(size == 0 ? 1 : size);
    if (!ptr) throw std::bad_alloc();
    return ptr;
}
__attribute__((noinline)) void* operator new[](std::size_t size) { return ::operator new(size); }
__attribute__((noinline)) void operator delete(void* ptr) noexcept { std::free(ptr); }
__attribute__((noinline)) void operator delete[](void* ptr) noexcept { std::free(ptr); }
#if defined(__cpp_sized_deallocation)
__attribute__((noinline)) void operator delete(void* ptr, std::size_t) noexcept { std::free(ptr); }
__attribute__((noinline)) void operator delete[](void* ptr, std::size_t) noexcept { std::free(ptr); }
#endif
extern "C" {
int __real_fsync(int);
int __real_renameat(int, const char*, int, const char*);
ssize_t __real_write(int, const void*, size_t);
ssize_t __real_pread(int, void*, size_t, off_t);
int __wrap_fsync(int fd)
{
    if (faults::syncAt && ++faults::syncCalls == faults::syncAt) { errno = EIO; return -1; }
    return __real_fsync(fd);
}
int __wrap_renameat(int a, const char* b, int c, const char* d)
{
    if (faults::rename) { errno = EIO; return -1; }
    return __real_renameat(a, b, c, d);
}
ssize_t __wrap_write(int fd, const void* bytes, size_t size)
{
    if (faults::partialWrite)
    {
        if (++faults::writes == 1) { errno = EINTR; return -1; }
        size = std::min(size, size_t(11));
    }
    const auto result = __real_write(fd, bytes, size);
    if (faults::exitAfterWrite && result > 0) ::_exit(23);
    return result;
}
ssize_t __wrap_pread(int fd, void* bytes, size_t size, off_t offset)
{
    if (faults::partialRead)
    {
        if (++faults::reads == 1) { errno = EINTR; return -1; }
        size = std::min(size, size_t(7));
    }
    return __real_pread(fd, bytes, size, offset);
}
}
namespace {
unsigned long assertions = 0;
void Require(bool condition, const char* expression, int line)
{
    ++assertions;
    if (condition) return;
    std::cerr << "failure at " << line << ": " << expression << '\n'; std::abort();
}
#define REQUIRE(x) Require(static_cast<bool>(x), #x, __LINE__)
std::string Digest(char c) { return "sha256:" + std::string(64, c); }
std::string Hash(const std::string& bytes)
{
    unsigned char digest[EVP_MAX_MD_SIZE]; unsigned int size = 0;
    REQUIRE(EVP_Digest(bytes.data(), bytes.size(), digest, &size, EVP_sha256(), nullptr) == 1);
    REQUIRE(size == 32);
    const char* digits = "0123456789abcdef";
    std::string out = "sha256:";
    for (unsigned int i = 0; i < size; ++i) { out += digits[digest[i] >> 4]; out += digits[digest[i] & 15]; }
    return out;
}
StrategyArtifactDescriptor Descriptor()
{
    StrategyArtifactDescriptor d; d.moduleId = "hepta.strategy.checkpoint-fixture"; d.version = "1.0.0";
    d.artifactDigest = Digest('a'); d.configDigest = Digest('b'); d.modelDigest = Digest('c');
    d.budget.maxThreads = 2; d.budget.maxFileDescriptors = 32;
    d.budget.maxMemoryBytes = 4096; d.budget.maxCheckpointBytes = 1024; return d;
}
struct Temp
{
    std::string path;
    Temp() { char pattern[] = "/tmp/hepta-checkpoint-test-XXXXXX"; char* p = ::mkdtemp(pattern);
             REQUIRE(p); path = p; }
    ~Temp() { std::error_code e; std::filesystem::remove_all(path, e); }
    std::string File() const { return path + "/state"; }
};
std::string Read(const std::string& path)
{ std::ifstream in(path, std::ios::binary); REQUIRE(in); return {std::istreambuf_iterator<char>(in), {}}; }
void Write(const std::string& path, const std::string& data)
{
    std::ofstream out(path, std::ios::binary | std::ios::trunc); REQUIRE(out);
    out.write(data.data(), data.size()); out.close(); REQUIRE(out);
    REQUIRE(::chmod(path.c_str(), 0600) == 0);
}
std::size_t Entries(const std::string& path)
{ return std::distance(std::filesystem::directory_iterator(path), std::filesystem::directory_iterator()); }
std::string Fingerprint(const StrategyRuntimeSnapshot& s)
{
    std::ostringstream out;
    out << s.found << '|' << s.generation << '|' << s.updatedAtMs << '|' << int(s.phase) << '|'
        << s.checkpointSequence << '|' << s.checkpointDigest << '|' << s.checkpointBytes << '|'
        << s.descriptor.moduleId << '|' << s.descriptor.artifactDigest << '|' << s.reasonCode;
    return out.str();
}
void SetU64(std::string& bytes, std::size_t offset, std::uint64_t value)
{
    REQUIRE(offset + 8 <= bytes.size());
    for (int i = 7; i >= 0; --i) { bytes[offset + i] = char(value & 255u); value >>= 8; }
}
std::size_t MetadataOffset(const StrategyArtifactDescriptor& d)
{
    return std::string("HEPTA_STRATEGY_CHECKPOINT_V1\n").size() + 5 * 8 +
        d.moduleId.size() + d.version.size() + d.artifactDigest.size() +
        d.configDigest.size() + d.modelDigest.size() + 4 * 8;
}

void TestBinaryRoundTripAndIdentity()
{
    Temp t; const auto d = Descriptor();
    StrategyCheckpointStore s(t.path, "state", d, 1024);
    REQUIRE(!s.Save(1, 2, "payload", 120).accepted);
    REQUIRE(s.Load("").accepted && s.IsReady());
    std::string payload; for (int i = 0; i < 256; ++i) payload.push_back(char(i));
    auto saved = s.Save(1, 2, payload, 120);
    REQUIRE(saved.accepted && !saved.duplicate && !saved.uncertain);
    REQUIRE(saved.checkpoint.IsValid()); REQUIRE(saved.checkpoint.Payload() == payload);
    REQUIRE(saved.checkpoint.PayloadDigest() == Hash(payload));
    REQUIRE(saved.checkpoint.RecordDigest() == Hash(Read(t.File())));
    REQUIRE(saved.checkpoint.Sequence() == 1 && saved.checkpoint.SourceGeneration() == 2);
    REQUIRE(saved.checkpoint.SavedAtMs() == 120 && Entries(t.path) == 2);
    const std::string physical = Read(t.File());
    REQUIRE(physical.size() == MetadataOffset(d) + 4 * 8 + payload.size());
    REQUIRE(physical.substr(physical.size() - payload.size()) == payload);
    REQUIRE(s.Save(1, 2, payload, 120).duplicate);
    for (int mode = 0; mode < 4; ++mode)
    {
        auto result = s.Save(1, mode == 0 ? 3 : 2, mode == 1 ? "other" : payload,
                             mode == 2 ? 121 : mode == 3 ? 119 : 120);
        REQUIRE(!result.accepted && !result.checkpoint.IsValid());
        REQUIRE(s.IsReady() && Read(t.File()) == physical);
    }
    REQUIRE(!s.Save(3, 2, payload, 130).accepted);
    REQUIRE(!s.Save(2, 0, payload, 130).accepted);
    REQUIRE(!s.Save(2, 3, "", 130).accepted);
    REQUIRE(!s.Save(2, 3, std::string(1025, 'x'), 130).accepted);
    StrategyCheckpointStore recovered(t.path, "state", d, 1024);
    REQUIRE(!recovered.Load("").accepted); // No trust-on-first-use.
    REQUIRE(!recovered.Load(Digest('f')).accepted);
    auto loaded = recovered.Load(saved.checkpoint.RecordDigest());
    REQUIRE(loaded.accepted && loaded.checkpoint.Payload() == payload);
    for (int i = 0; i < 9; ++i)
    {
        auto wrong = d;
        switch (i) {
        case 0: wrong.moduleId += ".other"; break; case 1: wrong.version = "2.0.0"; break;
        case 2: wrong.artifactDigest = Digest('e'); break; case 3: wrong.configDigest = Digest('e'); break;
        case 4: wrong.modelDigest.clear(); break; case 5: wrong.budget.maxThreads = 3; break;
        case 6: wrong.budget.maxFileDescriptors = 33; break; case 7: wrong.budget.maxMemoryBytes = 8192; break;
        case 8: wrong.budget.maxCheckpointBytes = 512; break; }
        StrategyCheckpointStore mismatch(t.path, "state", wrong);
        REQUIRE(!mismatch.Load(saved.checkpoint.RecordDigest()).accepted);
    }
}

void TestRestoreAndNonAtomicControllerHandoff()
{
    Temp t; const auto d = Descriptor(); StrategyRuntimeControl control;
    REQUIRE(control.Admit(d, 100).accepted);
    REQUIRE(control.Start(d.moduleId, 1, d.artifactDigest, 110).accepted);
    StrategyCheckpointStore s(t.path, "state", d); REQUIRE(s.Load("").accepted);
    const auto saved = s.Save(1, 2, "opaque payload", 120); REQUIRE(saved.accepted);
    REQUIRE(control.Checkpoint(d.moduleId, 2, 1, saved.checkpoint.PayloadDigest(),
                               saved.checkpoint.Payload().size(), 120).accepted);
    // A new process needs the independently retained record digest.
    StrategyRuntimeControl restarted; REQUIRE(restarted.Admit(d, 130).accepted);
    REQUIRE(!restarted.RestoreCheckpoint(d.moduleId, 1, VerifiedStrategyCheckpoint(), 140).accepted);
    REQUIRE(!restarted.RestoreCheckpoint(d.moduleId, 2, saved.checkpoint, 140).accepted);
    REQUIRE(!restarted.RestoreCheckpoint(d.moduleId, 1, saved.checkpoint, 129).accepted);
    const auto restore = restarted.RestoreCheckpoint(d.moduleId, 1, saved.checkpoint, 140);
    REQUIRE(restore.accepted && restore.snapshot.phase == StrategyRuntimePhase::Admitted);
    REQUIRE(restore.snapshot.generation == 2 && restore.snapshot.checkpointSequence == 1);
    REQUIRE(restarted.RestoreCheckpoint(d.moduleId, 2, saved.checkpoint, 141).duplicate);
    const auto next = s.Save(2, 3, "next", 150); REQUIRE(next.accepted);
    REQUIRE(!restarted.RestoreCheckpoint(d.moduleId, 2, next.checkpoint, 160).accepted);
    REQUIRE(restarted.Start(d.moduleId, 2, d.artifactDigest, 160).accepted);
    REQUIRE(!restarted.RestoreCheckpoint(d.moduleId, 3, saved.checkpoint, 170).accepted);
    auto other = d; other.configDigest = Digest('e'); StrategyRuntimeControl mismatch;
    REQUIRE(mismatch.Admit(other, 100).accepted);
    REQUIRE(!mismatch.RestoreCheckpoint(other.moduleId, 1, saved.checkpoint, 150).accepted);
    StrategyRuntimeControl future; REQUIRE(future.Admit(d, 100).accepted);
    REQUIRE(!future.RestoreCheckpoint(d.moduleId, 1, saved.checkpoint, 119).accepted);
    // File commit and metadata checkpoint are intentionally not one transaction.
    REQUIRE(control.Quarantine(d.moduleId, 3, "FAULT", 150).accepted);
    REQUIRE(!control.Checkpoint(d.moduleId, 3, 2, next.checkpoint.PayloadDigest(), 4, 160).accepted);
    REQUIRE(next.checkpoint.Payload() == "next");
    StrategyRuntimeControl recovery; REQUIRE(recovery.Admit(d, 170).accepted);
    REQUIRE(recovery.RestoreCheckpoint(d.moduleId, 1, next.checkpoint, 180).accepted);
}

void TestCorruptionTruncationAndCanonicalEnvelope()
{
    Temp t; const auto d = Descriptor(); StrategyCheckpointStore s(t.path, "state", d);
    REQUIRE(s.Load("").accepted); auto saved = s.Save(1, 2, "abc", 120); REQUIRE(saved.accepted);
    const auto original = Read(t.File());
    for (std::size_t n = 0; n < original.size(); ++n)
    {
        const std::string cut = original.substr(0, n); Write(t.File(), cut);
        StrategyCheckpointStore c(t.path, "state", d);
        REQUIRE(!c.Load(Hash(cut)).accepted && !c.IsReady());
    }
    for (std::size_t n = 0; n < original.size(); ++n)
    {
        auto changed = original; changed[n] ^= 1; Write(t.File(), changed);
        StrategyCheckpointStore c(t.path, "state", d);
        REQUIRE(!c.Load(saved.checkpoint.RecordDigest()).accepted);
    }
    for (int field = 0; field < 4; ++field)
    {
        auto changed = original; SetU64(changed, MetadataOffset(d) + field * 8, 0);
        Write(t.File(), changed); StrategyCheckpointStore c(t.path, "state", d);
        REQUIRE(!c.Load(Hash(changed)).accepted);
    }
    for (std::uint64_t length : {std::uint64_t(2), std::uint64_t(4), std::uint64_t(-1)})
    {
        auto changed = original; SetU64(changed, MetadataOffset(d) + 24, length);
        Write(t.File(), changed); StrategyCheckpointStore c(t.path, "state", d);
        REQUIRE(!c.Load(Hash(changed)).accepted);
    }
    auto trailing = original + "x"; Write(t.File(), trailing);
    StrategyCheckpointStore c(t.path, "state", d); REQUIRE(!c.Load(Hash(trailing)).accepted);
    Write(t.File(), std::string(2049, 'x')); StrategyCheckpointStore bounded(t.path, "state", d, 1024);
    REQUIRE(!bounded.Load(Hash(std::string(2049, 'x'))).accepted);
    std::cout << "checkpoint_truncated_prefixes=" << original.size() << '\n';
}

void TestFilesystemAndConfigurationRejections()
{
    Temp t; const auto d = Descriptor();
    for (const std::string filename : {"..", ".", "../other", "bad/name", "", "bad name"})
    { StrategyCheckpointStore s(t.path, filename, d); REQUIRE(!s.Load("").accepted); }
    for (const auto& path : {t.path + "/../other", std::string("relative"), t.path + "/missing"})
    { StrategyCheckpointStore s(path, "state", d); REQUIRE(!s.Load("").accepted); }
    for (std::size_t limit : {std::size_t(0), std::size_t(16 * 1024 * 1024 + 1)})
    { StrategyCheckpointStore s(t.path, "state", d, limit); REQUIRE(!s.Load("").accepted); }
    auto bad = d; bad.budget.maxThreads = 65; StrategyCheckpointStore invalid(t.path, "state", bad);
    REQUIRE(!invalid.Load("").accepted);
    REQUIRE(::chmod(t.path.c_str(), 0755) == 0);
    StrategyCheckpointStore looseDir(t.path, "state", d); REQUIRE(!looseDir.Load("").accepted);
    REQUIRE(::chmod(t.path.c_str(), 0700) == 0);
    Write(t.File(), "x"); REQUIRE(::chmod(t.File().c_str(), 0644) == 0);
    StrategyCheckpointStore permissions(t.path, "state", d); REQUIRE(!permissions.Load(Hash("x")).accepted);
    REQUIRE(::chmod(t.File().c_str(), 0600) == 0);
    REQUIRE(::link(t.File().c_str(), (t.path + "/hard").c_str()) == 0);
    StrategyCheckpointStore hard(t.path, "state", d); REQUIRE(!hard.Load(Hash("x")).accepted);
    REQUIRE(::unlink(t.File().c_str()) == 0);
    REQUIRE(::symlink("hard", t.File().c_str()) == 0);
    StrategyCheckpointStore symlink(t.path, "state", d); REQUIRE(!symlink.Load(Hash("x")).accepted);
    REQUIRE(::unlink(t.File().c_str()) == 0); REQUIRE(::mkfifo(t.File().c_str(), 0600) == 0);
    StrategyCheckpointStore fifo(t.path, "state", d); REQUIRE(!fifo.Load("").accepted);
    REQUIRE(::unlink(t.File().c_str()) == 0);
    Temp parent; REQUIRE(::symlink(t.path.c_str(), (parent.path + "/alias").c_str()) == 0);
    StrategyCheckpointStore ancestor(parent.path + "/alias", "state", d); REQUIRE(!ancestor.Load("").accepted);
    REQUIRE(::unlink((t.path + "/state.lock").c_str()) == 0);
    REQUIRE(::symlink("hard", (t.path + "/state.lock").c_str()) == 0);
    StrategyCheckpointStore lock(t.path, "state", d); REQUIRE(!lock.Load("").accepted);
}

void TestStaleWritersRollbackAndLockContention()
{
    Temp t; const auto d = Descriptor(); StrategyCheckpointStore a(t.path, "state", d), b(t.path, "state", d);
    REQUIRE(a.Load("").accepted); REQUIRE(b.Load("").accepted);
    const auto first = a.Save(1, 2, "first", 120); REQUIRE(first.accepted);
    REQUIRE(!b.Save(1, 2, "other", 120).accepted && !b.IsReady());
    REQUIRE(b.Load(first.checkpoint.RecordDigest()).accepted);
    const auto previous = Read(t.File());
    const auto second = b.Save(2, 3, "second", 130); REQUIRE(second.accepted);
    REQUIRE(!a.Save(1, 2, "first", 120).accepted && !a.IsReady()); // Even duplicate needs fresh disk.
    REQUIRE(a.Load(second.checkpoint.RecordDigest()).accepted);
    Write(t.File(), previous);
    REQUIRE(!a.Load(first.checkpoint.RecordDigest()).accepted); // Live-handle anti-regression.
    StrategyCheckpointStore fresh(t.path, "state", d);
    REQUIRE(fresh.Load(first.checkpoint.RecordDigest()).accepted); // Explicit old pin is not globally fenced.
    const int lock = ::open((t.path + "/state.lock").c_str(), O_RDWR);
    REQUIRE(lock >= 0 && ::flock(lock, LOCK_EX | LOCK_NB) == 0);
    REQUIRE(!fresh.Save(2, 3, "new", 140).accepted);
    REQUIRE(::close(lock) == 0);
    REQUIRE(fresh.Load(first.checkpoint.RecordDigest()).accepted);
    REQUIRE(::unlink(t.File().c_str()) == 0);
    REQUIRE(!fresh.Load("").accepted); // A live handle cannot reset sequence on disappearance.
}

void TestPinnedDirectoryAndLockIdentity()
{
    const auto d = Descriptor();
    {
        Temp root;
        const std::string directory = root.path + "/private";
        REQUIRE(::mkdir(directory.c_str(), 0700) == 0);
        StrategyCheckpointStore s(directory, "state", d); REQUIRE(s.Load("").accepted);
        REQUIRE(::rename(directory.c_str(), (root.path + "/moved").c_str()) == 0);
        REQUIRE(::mkdir(directory.c_str(), 0700) == 0);
        REQUIRE(!s.Save(1, 2, "payload", 120).accepted);
        REQUIRE(!s.IsReady() && !std::filesystem::exists(directory + "/state"));
        REQUIRE(!s.Load("").accepted);
    }
    {
        Temp t; StrategyCheckpointStore s(t.path, "state", d); REQUIRE(s.Load("").accepted);
        // Keep the old inode alive to avoid inode-number reuse in the fixture.
        REQUIRE(::rename((t.path + "/state.lock").c_str(), (t.path + "/old.lock").c_str()) == 0);
        REQUIRE(!s.Save(1, 2, "payload", 120).accepted);
        REQUIRE(!s.IsReady() && !std::filesystem::exists(t.File()));
        REQUIRE(!s.Load("").accepted);
    }
}

void TestExactBudgetAndExhaustedSequence()
{
    Temp t; auto d = Descriptor(); StrategyCheckpointStore s(t.path, "state", d, 1024);
    REQUIRE(s.Load("").accepted);
    const auto full = s.Save(1, 2, std::string(1024, 'x'), 120); REQUIRE(full.accepted);
    StrategyCheckpointStore smaller(t.path, "state", d, 1023);
    REQUIRE(!smaller.Load(full.checkpoint.RecordDigest()).accepted);
    auto bytes = Read(t.File()); SetU64(bytes, MetadataOffset(d), std::uint64_t(-1)); Write(t.File(), bytes);
    StrategyCheckpointStore last(t.path, "state", d);
    const auto loaded = last.Load(Hash(bytes)); REQUIRE(loaded.accepted);
    REQUIRE(!last.Save(0, 3, "x", 130).accepted);
    REQUIRE(!last.Save(1, 3, "x", 130).accepted);
    REQUIRE(last.Save(std::uint64_t(-1), 2, std::string(1024, 'x'), 120).duplicate);
}

void TestInterruptedAndShortIo()
{
    Temp t; StrategyCheckpointStore s(t.path, "state", Descriptor()); REQUIRE(s.Load("").accepted);
    faults::partialWrite = true; faults::writes = 0;
    const auto saved = s.Save(1, 2, std::string(128, 'x'), 120);
    faults::partialWrite = false;
    REQUIRE(saved.accepted && faults::writes > 2);
    faults::partialRead = true; faults::reads = 0;
    const auto loaded = s.Load(saved.checkpoint.RecordDigest()); faults::partialRead = false;
    REQUIRE(loaded.accepted && faults::reads > 2);
}

void TestFailureBeforeAndAfterRename()
{
    for (int stage = 0; stage < 3; ++stage)
    {
        Temp t; StrategyCheckpointStore s(t.path, "state", Descriptor()); REQUIRE(s.Load("").accepted);
        const auto old = s.Save(1, 2, "old", 120); REQUIRE(old.accepted);
        const auto before = Read(t.File());
        faults::syncCalls = 0; faults::syncAt = stage == 0 ? 1 : stage == 2 ? 2 : 0;
        faults::rename = stage == 1;
        const auto failed = s.Save(2, 3, "new", 130);
        faults::rename = false; faults::syncAt = 0;
        REQUIRE(!failed.accepted && !failed.checkpoint.IsValid() && !s.IsReady());
        REQUIRE(failed.uncertain == (stage == 2));
        REQUIRE(Entries(t.path) == 2); // Private temp removed, existing records never deleted.
        if (stage < 2)
        { REQUIRE(Read(t.File()) == before); REQUIRE(s.Load(old.checkpoint.RecordDigest()).accepted); }
        else
        {
            REQUIRE(Read(t.File()) != before);
            REQUIRE(!s.Load(old.checkpoint.RecordDigest()).accepted);
            const auto resolved = s.Load(failed.attemptedRecordDigest);
            REQUIRE(resolved.accepted && resolved.checkpoint.Sequence() == 2);
        }
    }
}

void TestProcessExitAndRecovery()
{
    Temp t; int pipefd[2]; REQUIRE(::pipe(pipefd) == 0);
    const pid_t child = ::fork(); REQUIRE(child >= 0);
    if (child == 0)
    {
        ::close(pipefd[0]); StrategyCheckpointStore s(t.path, "state", Descriptor());
        if (!s.Load("").accepted) ::_exit(2);
        const auto result = s.Save(1, 42, "child durable bytes", 120);
        if (!result.accepted || ::write(pipefd[1], result.checkpoint.RecordDigest().data(), 71) != 71) ::_exit(3);
        ::_exit(0); // No C++ destructor flush can make this pass.
    }
    REQUIRE(::close(pipefd[1]) == 0); std::string digest(71, '\0'); size_t got = 0;
    while (got < digest.size()) { const auto n = ::read(pipefd[0], &digest[got], digest.size() - got);
                                 REQUIRE(n > 0); got += size_t(n); }
    REQUIRE(::close(pipefd[0]) == 0); int status = 0;
    REQUIRE(::waitpid(child, &status, 0) == child && WIFEXITED(status) && WEXITSTATUS(status) == 0);
    StrategyCheckpointStore parent(t.path, "state", Descriptor()); const auto loaded = parent.Load(digest);
    REQUIRE(loaded.accepted && loaded.checkpoint.Payload() == "child durable bytes");
    StrategyRuntimeControl control; REQUIRE(control.Admit(Descriptor(), 130).accepted);
    const auto restore = control.RestoreCheckpoint(Descriptor().moduleId, 1, loaded.checkpoint, 140);
    REQUIRE(restore.accepted && restore.snapshot.generation == 2); // Never inherit generation 42.
}

void TestCrashedWriterLeavesBoundedEvidence()
{
    Temp t; const auto d = Descriptor(); StrategyCheckpointStore s(t.path, "state", d);
    REQUIRE(s.Load("").accepted); const auto old = s.Save(1, 2, "old", 120); REQUIRE(old.accepted);
    const auto original = Read(t.File());
    const pid_t child = ::fork(); REQUIRE(child >= 0);
    if (child == 0)
    {
        StrategyCheckpointStore writer(t.path, "state", d);
        if (!writer.Load(old.checkpoint.RecordDigest()).accepted) ::_exit(2);
        faults::exitAfterWrite = true;
        (void)writer.Save(2, 3, "new", 130);
        ::_exit(3);
    }
    int status = 0;
    REQUIRE(::waitpid(child, &status, 0) == child && WIFEXITED(status) && WEXITSTATUS(status) == 23);
    REQUIRE(Read(t.File()) == original);
    const auto pending = Read(t.path + "/.state.pending");
    REQUIRE(Entries(t.path) == 3);
    REQUIRE(!s.Save(2, 3, "retry", 140).accepted && !s.IsReady());
    REQUIRE(s.Load(old.checkpoint.RecordDigest()).accepted);
    REQUIRE(!s.Save(2, 3, "retry", 140).accepted);
    REQUIRE(Read(t.path + "/.state.pending") == pending && Entries(t.path) == 3);
    // The fixture simulates an independently reviewed offline cleanup; the
    // production store never deletes a staging file it did not create.
    REQUIRE(::unlink((t.path + "/.state.pending").c_str()) == 0);
    REQUIRE(s.Load(old.checkpoint.RecordDigest()).accepted);
    REQUIRE(s.Save(2, 3, "recovered", 150).accepted);
}

void TestConcurrentCooperatingWriters()
{
    Temp t; std::vector<std::unique_ptr<StrategyCheckpointStore>> stores;
    for (int i = 0; i < 12; ++i)
    { stores.emplace_back(new StrategyCheckpointStore(t.path, "state", Descriptor())); REQUIRE(stores.back()->Load("").accepted); }
    std::mutex mutex; std::condition_variable cv; int ready = 0; bool go = false;
    std::vector<StrategyCheckpointResult> results(12); std::vector<std::thread> workers;
    for (int i = 0; i < 12; ++i) workers.emplace_back([&, i] {
        { std::unique_lock<std::mutex> lock(mutex); ++ready; cv.notify_all(); cv.wait(lock, [&] { return go; }); }
        results[i] = stores[i]->Save(1, 2, "writer-" + std::to_string(i), 120);
    });
    { std::unique_lock<std::mutex> lock(mutex); cv.wait(lock, [&] { return ready == 12; }); go = true; cv.notify_all(); }
    for (auto& w : workers) w.join();
    int accepted = 0; VerifiedStrategyCheckpoint checkpoint;
    for (const auto& r : results) if (r.accepted) { ++accepted; checkpoint = r.checkpoint; }
    REQUIRE(accepted == 1); StrategyCheckpointStore verify(t.path, "state", Descriptor());
    REQUIRE(verify.Load(checkpoint.RecordDigest()).accepted);
}

void TestAllocationFailuresAndImmutableReceipt()
{
    unsigned long failedSaves = 0, failedRestores = 0;
    bool completedSave = false, completedRestore = false;
    Temp t; const auto d = Descriptor(); StrategyCheckpointStore s(t.path, "state", d);
    REQUIRE(s.Load("").accepted); const auto initial = s.Save(1, 2, "old", 120); REQUIRE(initial.accepted);
    const auto original = Read(t.File()); const std::string next(512, 'x');
    for (long ordinal = 0; ordinal < 512; ++ordinal)
    {
        REQUIRE(s.Load(initial.checkpoint.RecordDigest()).accepted);
        bool threw = false; StrategyCheckpointResult result;
        faults::calls = 0; faults::allocation = ordinal;
        try { result = s.Save(2, 3, next, 130); } catch (const std::bad_alloc&) { threw = true; }
        faults::allocation = -1;
        if (threw) { ++failedSaves; REQUIRE(Read(t.File()) == original); REQUIRE(Entries(t.path) == 2); }
        else { REQUIRE(result.accepted); completedSave = true; break; }
    }
    REQUIRE(completedSave && failedSaves > 0);
    REQUIRE(initial.checkpoint.Payload() == "old"); // Later writes cannot mutate an issued receipt.
    for (long ordinal = 0; ordinal < 256; ++ordinal)
    {
        StrategyRuntimeControl control; REQUIRE(control.Admit(d, 140).accepted);
        StrategyRuntimeSnapshot before; REQUIRE(control.Get(d.moduleId, before));
        bool threw = false; StrategyRuntimeControlResult result;
        faults::calls = 0; faults::allocation = ordinal;
        try { result = control.RestoreCheckpoint(d.moduleId, 1, initial.checkpoint, 150); }
        catch (const std::bad_alloc&) { threw = true; }
        faults::allocation = -1; StrategyRuntimeSnapshot after; REQUIRE(control.Get(d.moduleId, after));
        if (threw) { ++failedRestores; REQUIRE(Fingerprint(before) == Fingerprint(after)); }
        else { REQUIRE(result.accepted && after.generation == 2); completedRestore = true; break; }
    }
    REQUIRE(completedRestore && failedRestores > 0);
    std::cout << "checkpoint_save_allocation_failures=" << failedSaves
              << " restore_allocation_failures=" << failedRestores << '\n';
}
}
int main()
{
    TestBinaryRoundTripAndIdentity(); TestRestoreAndNonAtomicControllerHandoff();
    TestCorruptionTruncationAndCanonicalEnvelope(); TestFilesystemAndConfigurationRejections();
    TestStaleWritersRollbackAndLockContention(); TestPinnedDirectoryAndLockIdentity();
    TestExactBudgetAndExhaustedSequence(); TestInterruptedAndShortIo();
    TestFailureBeforeAndAfterRename(); TestProcessExitAndRecovery();
    TestCrashedWriterLeavesBoundedEvidence(); TestConcurrentCooperatingWriters();
    TestAllocationFailuresAndImmutableReceipt();
    std::cout << "checkpoint_assertions=" << assertions << '\n';
}
