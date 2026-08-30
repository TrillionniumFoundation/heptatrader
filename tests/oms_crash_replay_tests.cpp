#include "../HeptaTrade/oms_journal.h"

#include <cstdlib>
#include <iostream>
#include <string>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

namespace
{
void Require(bool condition, const char* expression, int line)
{
    if (condition) return;
    std::cerr << "requirement failed at line " << line << ": "
    << expression << '\n';
    std::abort();
}
#define REQUIRE(expression) Require(static_cast<bool>(expression), #expression, __LINE__)
}

int main()
{
    char pattern[] = "/tmp/hepta-oms-crash-XXXXXX";
    char* directory = ::mkdtemp(pattern);
    REQUIRE(directory != nullptr);
    const std::string path = std::string(directory) + "/journal.jsonl";
    int ready[2];
    REQUIRE(::pipe(ready) == 0);
    const pid_t child = ::fork();
    REQUIRE(child >= 0);
    if (child == 0)
    {
        ::close(ready[0]);
        ::setenv("HEPTA_OMS_ASYNC_FLUSH", "0", 1);
        ::setenv("HEPTA_OMS_SYNC_CRITICAL", "1", 1);
        OmsJournal journal;
        if (!journal.Init(path)) ::_exit(20);
        OmsJournalEvent event;
        event.eventType = "order_intent";
        event.tsMs = OmsJournal::NowEpochMs();
        event.reqId = "crash-command";
        event.clientReqId = event.reqId;
        event.eventId = "crash-command:order_intent";
        event.venue = "SIMULATOR";
        event.account = "SIM";
        event.executionDomain = "SIM:crash";
        if (!journal.Append(event)) ::_exit(21);
        const char signal = '1';
        if (::write(ready[1], &signal, 1) != 1) ::_exit(22);
        ::_exit(0); // Deliberately skip destructors: process-crash boundary.
    }
    ::close(ready[1]);
    char signal = 0;
    REQUIRE(::read(ready[0], &signal, 1) == 1 && signal == '1');
    ::close(ready[0]);
    int status = 0;
    REQUIRE(::waitpid(child, &status, 0) == child);
    REQUIRE(WIFEXITED(status) && WEXITSTATUS(status) == 0);

    OmsJournal recovered;
    REQUIRE(recovered.Init(path));
    int callbacks = 0;
    REQUIRE(recovered.Replay([&](const OmsJournalEvent& event) {
        ++callbacks;
        REQUIRE(event.reqId == "crash-command");
        REQUIRE(event.eventType == "order_intent");
    }) == 1);
    REQUIRE(callbacks == 1);
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(directory) == 0);
    return 0;
}
