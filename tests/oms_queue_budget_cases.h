// Included by the canonical durability executable; tests use real files.
void TestQueueBudgetsPreserveAdmittedRecordsAndCriticalExit()
{
    ClearReplayBudgets();
    ::setenv("HEPTA_OMS_ASYNC_FLUSH", "0", 1);
    ::setenv("HEPTA_OMS_SYNC_CRITICAL", "1", 1);
    ::setenv("HEPTA_OMS_BATCH_SIZE", "100000", 1);
    ::setenv("HEPTA_OMS_FLUSH_INTERVAL_MS", "100000", 1);
    ::setenv("HEPTA_OMS_QUEUE_MAX_RECORDS", "2", 1);
    const auto dir = MakeTempDirectory();
    const auto path = dir + "/budget.jsonl";
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        auto event = MakeCriticalEvent("buffered-1");
        event.eventType = "noncritical_observation";
        REQUIRE(journal.Append(event));
        const auto one = journal.GetHealthSnapshot();
        REQUIRE(one.pendingBytes > 0 && one.bufferedDepth == 1);
        REQUIRE(journal.Append(event));
        const auto two = journal.GetHealthSnapshot();
        REQUIRE(two.pendingBytes == 2 * one.pendingBytes && two.bufferedDepth == 2);
        REQUIRE(!journal.Append(event));
        auto full = journal.GetHealthSnapshot();
        REQUIRE(full.pendingBytes == two.pendingBytes && full.queueCapacityRejections == 1);
        REQUIRE(!full.writePoisoned);
        auto exit = MakeCriticalEvent("critical-exit-retained");
        exit.eventType = "cancel_send_attempt";
        REQUIRE(journal.Append(exit));
        full = journal.GetHealthSnapshot();
        REQUIRE(full.pendingBytes == 0 && full.bufferedDepth == 0 && full.currentRecords == 3);
        int seen = 0;
        REQUIRE(journal.Replay([&](const OmsJournalEvent& e) {
            if (seen++ == 2) REQUIRE(e.reqId == "critical-exit-retained");
        }) == 3);
    }
    ::unsetenv("HEPTA_OMS_QUEUE_MAX_RECORDS");
    ::unsetenv("HEPTA_OMS_BATCH_SIZE");
    ::unsetenv("HEPTA_OMS_FLUSH_INTERVAL_MS");
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(dir.c_str()) == 0);
}

void TestQueueByteBudgetAndMalformedConfiguration()
{
    const auto dir = MakeTempDirectory();
    const auto path = dir + "/bytes.jsonl";
    ::setenv("HEPTA_OMS_ASYNC_FLUSH", "0", 1);
    ::setenv("HEPTA_OMS_SYNC_CRITICAL", "1", 1);
    for (const char* value : {"0", "-1", " 12", "12x", "1073741825", ""})
    {
        ::setenv("HEPTA_OMS_QUEUE_MAX_BYTES", value, 1);
        OmsJournal journal;
        REQUIRE(!journal.Init(path));
        REQUIRE(::access(path.c_str(), F_OK) != 0);
        REQUIRE(journal.GetHealthSnapshot().replayReasonCode == "OMS_QUEUE_INVALID_BUDGET");
    }
    ::setenv("HEPTA_OMS_QUEUE_MAX_BYTES", "1", 1);
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        auto e = MakeCriticalEvent("buffered");
        e.eventType = "noncritical_observation";
        REQUIRE(!journal.Append(e));
        REQUIRE(journal.GetHealthSnapshot().pendingBytes == 0);
        REQUIRE(journal.Append(MakeCriticalEvent("durable-critical")));
        REQUIRE(journal.Replay({}) == 1);
    }
    REQUIRE(::unlink(path.c_str()) == 0);
    ::unsetenv("HEPTA_OMS_QUEUE_MAX_BYTES");
    std::size_t recordBytes = 0;
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        auto e = MakeCriticalEvent("same-length-record");
        e.eventType = "noncritical_observation";
        REQUIRE(journal.Append(e));
        REQUIRE(journal.Replay({}) == 1);
        recordBytes = journal.GetHealthSnapshot().currentBytes;
    }
    REQUIRE(::unlink(path.c_str()) == 0);
    ::setenv("HEPTA_OMS_BATCH_SIZE", "100000", 1);
    ::setenv("HEPTA_OMS_FLUSH_INTERVAL_MS", "100000", 1);
    ::setenv("HEPTA_OMS_QUEUE_MAX_BYTES", std::to_string(recordBytes).c_str(), 1);
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        auto e = MakeCriticalEvent("same-length-record");
        e.eventType = "noncritical_observation";
        REQUIRE(journal.Append(e));
        REQUIRE(journal.GetHealthSnapshot().pendingBytes == recordBytes);
        REQUIRE(!journal.Append(e));
        REQUIRE(journal.Replay({}) == 1);
        REQUIRE(journal.GetHealthSnapshot().pendingBytes == 0);
    }
    ::unsetenv("HEPTA_OMS_QUEUE_MAX_BYTES");
    ::unsetenv("HEPTA_OMS_BATCH_SIZE");
    ::unsetenv("HEPTA_OMS_FLUSH_INTERVAL_MS");
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(dir.c_str()) == 0);
}
