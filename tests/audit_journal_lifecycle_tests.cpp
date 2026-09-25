#include "tool_host/session_supervisor_audit_journal.h"
#include <cassert>
#include <cerrno>
#include <cstring>
#include <dirent.h>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

static int failSyncAt = 0;
static bool failDataSync = false;
static int crashRename = 0;
extern "C" int __real_fsync(int);
extern "C" int __wrap_fsync(int fd)
{
    if (failSyncAt > 0 && --failSyncAt == 0) { errno = EIO; return -1; }
    return __real_fsync(fd);
}
extern "C" int __real_fdatasync(int);
extern "C" int __wrap_fdatasync(int fd)
{
    if (failDataSync) { failDataSync = false; errno = EIO; return -1; }
    return __real_fdatasync(fd);
}
extern "C" int __real_rename(const char*, const char*);
extern "C" int __wrap_rename(const char* from, const char* to)
{
    if (crashRename == 1) ::_exit(86);
    const int result = __real_rename(from, to);
    if (crashRename == 2) ::_exit(result == 0 ? 87 : 88);
    return result;
}

struct Fixture
{
    std::string root, path;
    Fixture()
    {
        char pattern[] = "/tmp/hepta-audit-lifecycle-XXXXXX";
        char* directory = ::mkdtemp(pattern); assert(directory);
        root = directory; path = root + "/audit";
    }
    static void RemoveDirectory(const std::string& path)
    {
        DIR* dir = ::opendir(path.c_str()); if (!dir) return;
        while (dirent* entry = ::readdir(dir))
        {
            const std::string name = entry->d_name;
            if (name != "." && name != "..") ::unlink((path + "/" + name).c_str());
        }
        ::closedir(dir); ::rmdir(path.c_str());
    }
    ~Fixture() { RemoveDirectory(path + ".segments"); RemoveDirectory(root); }
};
std::string Read(const std::string& path)
{
    std::ifstream input(path.c_str(), std::ios::binary);
    return std::string(std::istreambuf_iterator<char>(input), {});
}
ToolDecisionAuditRecord Record(const std::string& tool)
{
    ToolDecisionAuditRecord r;
    r.observational = tool.compare(0, 6, "trade.") != 0;
    r.peerCredentialAvailable = true; r.peerUid = ::geteuid();
    r.agentId = "synthetic-agent"; r.sessionId = "synthetic-session";
    r.toolName = tool; r.phase = "outcome"; r.outcome = "ok";
    r.reasonCode = std::string(200, 'x');
    return r;
}
void VerifyCount(const std::string& path, std::uint64_t expected)
{
    std::uint64_t records = 0; std::string reason;
    if (!SessionSupervisorAuditJournal::Verify(path, records, reason)) std::cerr << reason << '\n';
    assert(SessionSupervisorAuditJournal::Verify(path, records, reason));
    assert(records == expected);
}
void TestCapacityAndExitReserve()
{
    Fixture f; SessionSupervisorAuditJournal journal(16384, 4096); std::string reason;
    assert(journal.Init(f.path, reason));
    auto read = Record("market.get_quote");
    for (unsigned n = 0; n < 100; ++n) assert(journal.AppendToolDecision(read, reason));
    auto cap = journal.CapacitySnapshot();
    assert(cap.known && cap.observationsShed > 0 && cap.bytes <= 8192);
    const auto bytes = cap.bytes;
    assert(journal.AppendToolDecision(read, reason));
    assert(reason == "SUPERVISOR_AUDIT_OBSERVATION_SHED");
    assert(journal.CapacitySnapshot().bytes == bytes);
    auto place = Record("trade.place_order");
    unsigned admitted = 0;
    while (journal.AppendToolDecision(place, reason)) { assert(++admitted < 100); }
    assert(reason == "SUPERVISOR_AUDIT_EXIT_RESERVE_REQUIRED");
    auto exit = Record("trade.cancel_order");
    assert(journal.AppendToolDecision(exit, reason));
    assert(journal.CapacitySnapshot().bytes <= 16384);
    unsigned exits = 0;
    while (journal.AppendToolDecision(exit, reason)) assert(++exits < 100);
    assert(reason == "SUPERVISOR_AUDIT_SIZE_LIMIT");
    const auto fullBytes = journal.CapacitySnapshot().bytes;
    assert(journal.AppendToolDecision(read, reason));
    assert(reason == "SUPERVISOR_AUDIT_OBSERVATION_SHED");
    assert(journal.CapacitySnapshot().bytes == fullBytes);
    // A record without an authenticated owner cannot claim the exit reserve.
    exit.agentId.clear();
    assert(!journal.AppendToolDecision(exit, reason));
    assert(reason == "SUPERVISOR_AUDIT_EXIT_RESERVE_REQUIRED");
}
void TestSegmentsAndWriterHandoff()
{
    Fixture f; std::string reason;
    SessionSupervisorAuditJournal old;
    assert(old.Init(f.path, reason));
    assert(old.AppendToolDecision(Record("trade.place_order"), reason));
    VerifyCount(f.path, 1);
    assert(SessionSupervisorAuditJournal::SealSegment(f.path, reason));
    const auto anchor = Read(f.path);
    assert(anchor.find("HJA3\t") == 0);
    VerifyCount(f.path, 1);
    assert(SessionSupervisorAuditJournal::SealSegment(f.path, reason));
    assert(Read(f.path) == anchor);
    assert(!old.AppendToolDecision(Record("trade.place_order"), reason));
    assert(reason == "SUPERVISOR_AUDIT_PATH_IDENTITY_CHANGED");
    SessionSupervisorAuditJournal fresh;
    assert(fresh.Init(f.path, reason));
    assert(fresh.AppendToolDecision(Record("trade.cancel_order"), reason));
    VerifyCount(f.path, 2);
    assert(SessionSupervisorAuditJournal::SealSegment(f.path, reason));
    VerifyCount(f.path, 2);
    SessionSupervisorAuditJournal next;
    assert(next.Init(f.path, reason));
    assert(next.AppendToolDecision(Record("system.get_health"), reason));
    VerifyCount(f.path, 3);
}
void TestLegacyOnlySegmentsAndInitialPublication()
{
    Fixture f; std::string reason;
    SessionSupervisorAuditJournal failed;
    failSyncAt = 2; // File sync succeeds, initial parent-directory sync fails.
    assert(!failed.Init(f.path, reason));
    assert(reason == "SUPERVISOR_AUDIT_INIT_DIRECTORY_SYNC_FAILED");
    { std::ofstream legacy(f.path.c_str()); legacy << "retained-legacy-audit-record\n"; }
    assert(SessionSupervisorAuditJournal::SealSegment(f.path, reason));
    assert(Read(f.path).find("HJA3\t") == 0);
    VerifyCount(f.path, 0); // Legacy bytes are retained but are not HJA2 records.
    SessionSupervisorAuditJournal current;
    assert(current.Init(f.path, reason));
    assert(current.AppendToolDecision(Record("trade.cancel_order"), reason));
    VerifyCount(f.path, 1);
    assert(SessionSupervisorAuditJournal::SealSegment(f.path, reason));
    VerifyCount(f.path, 1);
    assert(::truncate(f.path.c_str(), 0) == 0);
    SessionSupervisorAuditJournal empty;
    assert(!empty.Init(f.path, reason));
    assert(reason == "SUPERVISOR_AUDIT_SEGMENT_ACTIVE_EMPTY");
}

void TestSyncFailuresAndRetry()
{
    Fixture f; std::string reason; SessionSupervisorAuditJournal journal;
    assert(journal.Init(f.path, reason));
    failDataSync = true;
    assert(!journal.AppendToolDecision(Record("trade.place_order"), reason));
    assert(journal.AppendToolDecision(Record("trade.cancel_order"), reason));
    const auto original = Read(f.path);
    failSyncAt = 1;
    assert(!SessionSupervisorAuditJournal::SealSegment(f.path, reason));
    assert(Read(f.path) == original);
    failSyncAt = 5;
    assert(!SessionSupervisorAuditJournal::SealSegment(f.path, reason));
    assert(reason == "SUPERVISOR_AUDIT_PUBLICATION_INDETERMINATE");
    VerifyCount(f.path, 2);
    failSyncAt = 1;
    assert(!SessionSupervisorAuditJournal::SealSegment(f.path, reason));
    assert(reason == "SUPERVISOR_AUDIT_PUBLICATION_INDETERMINATE");
    assert(SessionSupervisorAuditJournal::SealSegment(f.path, reason));
    VerifyCount(f.path, 2);
}
void TestCrashCuts()
{
    for (int cut = 1; cut <= 2; ++cut)
    {
        Fixture f; std::string reason;
        { SessionSupervisorAuditJournal journal; assert(journal.Init(f.path, reason));
          assert(journal.AppendToolDecision(Record("trade.place_order"), reason)); }
        const pid_t child = ::fork(); assert(child >= 0);
        if (child == 0) { crashRename = cut; SessionSupervisorAuditJournal::SealSegment(f.path, reason); ::_exit(89); }
        int status = 0; assert(::waitpid(child, &status, 0) == child);
        assert(WIFEXITED(status) && WEXITSTATUS(status) == (cut == 1 ? 86 : 87));
        VerifyCount(f.path, 1);
        assert(SessionSupervisorAuditJournal::SealSegment(f.path, reason));
        SessionSupervisorAuditJournal recovered; assert(recovered.Init(f.path, reason));
        assert(recovered.AppendToolDecision(Record("trade.cancel_order"), reason));
        VerifyCount(f.path, 2);
    }
}
void TestMissingAndCorruptHistory()
{
    Fixture f; std::string reason;
    { SessionSupervisorAuditJournal journal; assert(journal.Init(f.path, reason));
      assert(journal.AppendToolDecision(Record("trade.place_order"), reason)); }
    assert(SessionSupervisorAuditJournal::SealSegment(f.path, reason));
    const auto anchor = Read(f.path);
    const auto digest = anchor.substr(5, 64);
    const std::string archive = f.path + ".segments/" + digest + ".hja2";
    int fd = ::open(archive.c_str(), O_WRONLY); assert(fd >= 0);
    assert(::pwrite(fd, "X", 1, 0) == 1); ::close(fd);
    SessionSupervisorAuditJournal corrupt; assert(!corrupt.Init(f.path, reason));
    assert(reason == "SUPERVISOR_AUDIT_SEGMENT_INVALID");
    assert(::unlink(archive.c_str()) == 0);
    SessionSupervisorAuditJournal missing; assert(!missing.Init(f.path, reason));
    assert(::unlink(f.path.c_str()) == 0);
    SessionSupervisorAuditJournal noActive; assert(!noActive.Init(f.path, reason));
    assert(reason == "SUPERVISOR_AUDIT_SEGMENT_ACTIVE_MISSING");
    assert(::access(f.path.c_str(), F_OK) != 0);
}
int main()
{
    TestCapacityAndExitReserve();
    TestSegmentsAndWriterHandoff();
    TestLegacyOnlySegmentsAndInitialPublication();
    TestSyncFailuresAndRetry();
    TestCrashCuts();
    TestMissingAndCorruptHistory();
    std::cout << "audit capacity, segments, handoff, sync failure, crash cuts, history rejection: PASS\n";
}
