#pragma once

#include "../HeptaTrade/oms_journal.h"
#include "../HeptaTrade/oms_capacity_observation.h"
#include <atomic>
#include <cerrno>
#include <csignal>
#include <cstdlib>
#include <fcntl.h>
#include <fstream>
#include <limits>
#include <stdexcept>
#include <sys/stat.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
#include <vector>

namespace hepta_observation_test {
inline void Check(bool ok, const char* message)
{
    if (!ok) throw std::runtime_error(message);
}
struct Environment {
    std::vector<std::pair<std::string, std::pair<bool, std::string>>> previous;
    void Set(const char* key, const char* value) {
        const char* old = std::getenv(key);
        previous.push_back({key, {old != nullptr, old ? old : ""}});
        Check(value ? ::setenv(key, value, 1) == 0 : ::unsetenv(key) == 0, "set fixture environment");
    }
    Environment() {
        Set("HEPTA_OMS_ASYNC_FLUSH", "0");
        Set("HEPTA_OMS_SYNC_CRITICAL", "1");
        Set("HEPTA_OMS_BATCH_SIZE", "1");
        Set("HEPTA_OMS_FLUSH_INTERVAL_MS", "0");
        Set("HEPTA_OMS_REPLAY_MAX_BYTES", nullptr);
        Set("HEPTA_OMS_REPLAY_MAX_RECORDS", nullptr);
        Set("HEPTA_OMS_REPLAY_MAX_RECORD_BYTES", nullptr);
    }
    ~Environment() {
        for (auto it = previous.rbegin(); it != previous.rend(); ++it)
            if (it->second.first) ::setenv(it->first.c_str(), it->second.second.c_str(), 1);
            else ::unsetenv(it->first.c_str());
    }
};
struct Directory {
    std::string root;
    Directory() {
        char name[] = "/tmp/hepta-observation-XXXXXX";
        const char* made = ::mkdtemp(name);
        Check(made != nullptr, "create private fixture"); root = made;
    }
    std::string Path() const { return root + "/journal"; }
    ~Directory() {
        ::unlink(Path().c_str()); ::unlink((root + "/pinned").c_str());
        ::rmdir(root.c_str());
    }
};
inline OmsJournalEvent Event(const std::string& id) {
    OmsJournalEvent event; event.eventType = "order_intent";
    event.reqId = id; event.eventId = id; event.account = "PRIVATE_FIXTURE_ACCOUNT";
    event.tsMs = OmsJournal::NowEpochMs(); return event;
}
inline void CheckSummary(const OmsLatencySummary& s) {
    Check(s.maximumNs >= s.lastNs, "last <= max");
    Check(s.totalNs >= s.maximumNs, "max <= total");
}
inline void ArithmeticAndFinishOnce() {
    OmsLatencySummary s;
    const auto start = OmsScopedLatencySample::Clock::time_point();
    { OmsScopedLatencySample timer(s, start);
      timer.Finish(start + std::chrono::nanoseconds(125)); timer.Finish(start); }
    Check(s.samples == 1 && s.totalNs == 125 && s.lastNs == 125, "one deterministic sample");
    s.Observe(0); Check(s.samples == 2 && s.maximumNs == 125, "observed zero");
    s.totalNs = std::numeric_limits<std::uint64_t>::max() - 1;
    s.samples = std::numeric_limits<std::uint64_t>::max(); s.Observe(2);
    Check(s.saturated && s.totalNs == std::numeric_limits<std::uint64_t>::max(), "saturation, not wrap");
}
inline void AppendAndReentrantReplay() {
    Environment env; Directory dir; OmsJournal journal;
    Check(!journal.Append({}), "uninitialized append rejects");
    Check(journal.GetHealthSnapshot().appendLatency.samples == 1, "failed append measured");
    Check(journal.Init(dir.Path()), "initialize");
    const auto initial = journal.GetHealthSnapshot().dataSyncLatency.samples;
    Check(initial == 1, "creation fdatasync measured");
    Check(journal.Append(Event("PRIVATE_FIXTURE_ID")), "append");
    int calls = 0;
    Check(journal.Replay([&](const OmsJournalEvent&) {
        ++calls; const auto h = journal.GetHealthSnapshot();
        Check(h.replayValidationLatency.samples == 1, "finish before unlocked callback");
    }) == 1 && calls == 1, "replay");
    const auto h = journal.GetHealthSnapshot();
    Check(h.appendLatency.samples == 2 && h.dataSyncLatency.samples == initial + 2, "attempt counters");
    CheckSummary(h.appendLatency); CheckSummary(h.dataSyncLatency); CheckSummary(h.replayValidationLatency);
    bool thrown = false;
    try { journal.Replay([&](const OmsJournalEvent&) {
        Check(journal.GetHealthSnapshot().replayValidationLatency.samples == 2, "callback exception after observation");
        throw std::runtime_error("fixture callback"); }); }
    catch (const std::runtime_error&) { thrown = true; }
    Check(thrown, "callback failure propagates");
    Check(journal.GetHealthSnapshot().replayValidationLatency.samples == 2, "callback failure does not double count");
    const auto json = OmsCapacityObservation(h, 123);
    Check(json.find("PRIVATE_FIXTURE") == std::string::npos, "no account/command payload in telemetry");
    Check(json.find("\"append_latency\":{") != std::string::npos, "observation exported");
}
inline void OverBudgetPreservesHistoryAndExit() {
    Environment env; Directory dir; env.Set("HEPTA_OMS_REPLAY_MAX_RECORDS", "2");
    { OmsJournal journal; Check(journal.Init(dir.Path()), "initialize bounded journal");
      for (int i=0; i<3; ++i) Check(journal.Append(Event("entry-" + std::to_string(i))), "append above replay ceiling");
      Check(OmsCapacityObservation(journal.GetHealthSnapshot(), 1).find("EXCEEDED") != std::string::npos, "online capacity signal");
      int calls = 0; Check(journal.Replay([&](const OmsJournalEvent&) { ++calls; }) == -1 && calls == 0, "no valid-prefix replay");
      Check(journal.GetHealthSnapshot().replayValidationLatency.samples == 1, "failed replay measured");
      auto exit = Event("exit"); exit.eventType = "cancel_send_attempt";
      Check(journal.Append(exit), "exit evidence remains writable"); }
    env.Set("HEPTA_OMS_REPLAY_MAX_RECORDS", "10");
    { OmsJournal recovered; Check(recovered.Init(dir.Path()), "reopen same ledger");
      std::vector<std::string> ids;
      Check(recovered.Replay([&](const OmsJournalEvent& event) { ids.push_back(event.reqId); }) == 4, "full replay");
      Check(ids == std::vector<std::string>({"entry-0", "entry-1", "entry-2", "exit"}), "all identities retained"); }
}
inline void ReplacementStillPoisons() {
    Environment env; Directory dir; OmsJournal journal;
    Check(journal.Init(dir.Path()) && journal.Append(Event("first")), "initial write");
    const auto syncs = journal.GetHealthSnapshot().dataSyncLatency.samples;
    Check(::rename(dir.Path().c_str(), (dir.root + "/pinned").c_str()) == 0, "replace fixture path");
    const int fd = ::open(dir.Path().c_str(), O_WRONLY | O_CREAT | O_EXCL, 0600);
    Check(fd >= 0, "create decoy"); Check(::close(fd) == 0, "close decoy");
    Check(!journal.Append(Event("second")), "replacement must reject");
    const auto h = journal.GetHealthSnapshot();
    Check(h.writePoisoned && !h.capacityKnown && h.appendLatency.samples == 2, "failure remains visible");
    Check(h.dataSyncLatency.samples == syncs, "no fabricated sync attempt");
}
inline void ConcurrentWritersAndHealth() {
    Environment env; Directory dir; OmsJournal journal;
    Check(journal.Init(dir.Path()), "initialize concurrent fixture");
    std::atomic<bool> ok(true), done(false);
    std::thread reader([&] { std::uint64_t prior=0;
        while (!done.load()) { auto h=journal.GetHealthSnapshot();
            if (h.appendLatency.samples < prior || h.appendLatency.maximumNs < h.appendLatency.lastNs ||
                h.appendLatency.totalNs < h.appendLatency.maximumNs) ok=false;
            prior=h.appendLatency.samples; std::this_thread::yield(); }
    });
    std::vector<std::thread> writers;
    for (int t=0; t<4; ++t) writers.emplace_back([&, t] {
        for (int i=0; i<30; ++i)
            if (!journal.Append(Event(std::to_string(t) + ":" + std::to_string(i)))) ok=false;
    });
    for (auto& thread : writers) thread.join();
    done=true;
    reader.join();
    Check(ok && journal.GetHealthSnapshot().appendLatency.samples == 120, "coherent completed observations");
    Check(journal.Replay({}) == 120, "concurrent records preserved");
}
inline void DurableCrashRestart() {
    Environment env; Directory dir; int ready[2]; Check(::pipe(ready)==0, "fixture pipe");
    const pid_t child=::fork(); Check(child>=0, "fixture fork");
    if (child==0) {
        ::close(ready[0]); ::alarm(5);
        OmsJournal journal;
        if (!journal.Init(dir.Path()) || !journal.Append(Event("durable-before-kill"))) ::_exit(2);
        const char value='D'; if (::write(ready[1], &value, 1)!=1) ::_exit(3);
        ::pause(); ::_exit(4);
    }
    ::close(ready[1]); char value=0; ssize_t n;
    do { n=::read(ready[0], &value, 1); } while (n<0 && errno==EINTR);
    ::close(ready[0]); ::kill(child, SIGKILL); int status=0;
    while (::waitpid(child, &status, 0)<0 && errno==EINTR) {}
    Check(n==1 && value=='D' && WIFSIGNALED(status), "kill after durable receipt");
    OmsJournal recovered; Check(recovered.Init(dir.Path()), "restart after SIGKILL");
    std::string id; Check(recovered.Replay([&](const OmsJournalEvent& e) { id=e.reqId; })==1, "recover durable record");
    Check(id=="durable-before-kill", "crash does not reset identity");
}
inline void Run() {
    ArithmeticAndFinishOnce(); AppendAndReentrantReplay();
    OverBudgetPreservesHistoryAndExit(); ReplacementStillPoisons();
    ConcurrentWritersAndHealth(); DurableCrashRestart();
}
} // namespace hepta_observation_test
