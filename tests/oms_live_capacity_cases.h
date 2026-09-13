// Included in the existing OMS durability test namespace. No extra target or
// test-name counting gate: both sanitizer lanes execute these behaviors.
void TestLiveCapacityAndCheckpointRecovery()
{
    ClearReplayBudgets();
    ::setenv("HEPTA_OMS_ASYNC_FLUSH", "0", 1);
    ::setenv("HEPTA_OMS_SYNC_CRITICAL", "1", 1);
    ::setenv("HEPTA_OMS_REPLAY_MAX_RECORDS", "5", 1);
    const std::string directory = MakeTempDirectory();
    const std::string path = directory + "/live.jsonl";
    const std::string checkpoint = directory + "/offline-checkpoint.jsonl";
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        REQUIRE(journal.GetHealthSnapshot().capacityKnown);
        REQUIRE(journal.GetHealthSnapshot().currentRecords == 0);
        for (int i = 0; i < 4; ++i)
            REQUIRE(journal.Append(MakeCriticalEvent("retained-identity-" + std::to_string(i))));
        auto health = journal.GetHealthSnapshot();
        REQUIRE(health.capacityKnown && health.currentRecords == 4);
        REQUIRE(health.currentBytes > 0);
        const std::string warning = OmsCapacityObservation(health, 42);
        REQUIRE(warning.find("\"status\":\"WARNING\"") != std::string::npos);
        REQUIRE(warning.find("retained-identity") == std::string::npos);
        REQUIRE(warning.find("DU123456") == std::string::npos);
        REQUIRE(journal.Append(MakeCriticalEvent("at-limit")));
        REQUIRE(journal.GetHealthSnapshot().currentRecords == 5);
        OmsJournalEvent exit = MakeCriticalEvent("guarded-exit-identity");
        exit.eventType = "cancel_send_attempt";
        REQUIRE(journal.Append(exit));
        health = journal.GetHealthSnapshot();
        REQUIRE(health.currentRecords == 6 && !health.writePoisoned);
        REQUIRE(OmsCapacityObservation(health, 43).find("\"status\":\"EXCEEDED\"") != std::string::npos);
    }
    std::ifstream input(path, std::ios::binary);
    const std::string bytes((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    input.close();
    WritePrivateFile(checkpoint, bytes); // stopped-state copy; no lease/key migration claim
    for (const auto& candidate : {path, checkpoint})
    {
        OmsJournal journal;
        REQUIRE(journal.Init(candidate));
        REQUIRE(!journal.GetHealthSnapshot().capacityKnown);
        int callbacks = 0;
        REQUIRE(journal.Replay([&](const OmsJournalEvent&) { ++callbacks; }) == -1);
        REQUIRE(callbacks == 0);
        REQUIRE(!journal.GetHealthSnapshot().capacityKnown);
    }
    ::setenv("HEPTA_OMS_REPLAY_MAX_RECORDS", "8", 1);
    for (const auto& candidate : {path, checkpoint})
    {
        OmsJournal journal;
        REQUIRE(journal.Init(candidate));
        int callbacks = 0;
        REQUIRE(journal.Replay([&](const OmsJournalEvent& event) {
            if (callbacks < 4) REQUIRE(event.reqId == "retained-identity-" + std::to_string(callbacks));
            if (callbacks == 5) REQUIRE(event.reqId == "guarded-exit-identity");
            ++callbacks;
        }) == 6);
        auto health = journal.GetHealthSnapshot();
        REQUIRE(health.capacityKnown && health.currentRecords == 6 && health.currentBytes == bytes.size());
    }
    std::ifstream unchanged(path, std::ios::binary);
    REQUIRE(std::string((std::istreambuf_iterator<char>(unchanged)), std::istreambuf_iterator<char>()) == bytes);
    unchanged.close();
    ClearReplayBudgets();
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::unlink(checkpoint.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
}

void TestCapacityUnknownAndPendingAreNotHealthyZero()
{
    ClearReplayBudgets();
    ::setenv("HEPTA_OMS_ASYNC_FLUSH", "0", 1);
    ::setenv("HEPTA_OMS_BATCH_SIZE", "100", 1);
    ::setenv("HEPTA_OMS_FLUSH_INTERVAL_MS", "100000", 1);
    ::setenv("HEPTA_OMS_REPLAY_MAX_RECORDS", "5", 1);
    const std::string directory = MakeTempDirectory();
    const std::string path = directory + "/pending.jsonl";
    const std::string moved = directory + "/moved.jsonl";
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        OmsJournalEvent pending = MakeCriticalEvent("not-written-yet");
        pending.eventType = "noncritical_observation";
        for (int i = 0; i < 4; ++i) REQUIRE(journal.Append(pending));
        auto health = journal.GetHealthSnapshot();
        REQUIRE(health.capacityKnown && health.currentRecords == 0 && health.bufferedDepth == 4);
        REQUIRE(OmsCapacityObservation(health, 44).find("\"status\":\"WARNING\"") != std::string::npos);
        REQUIRE(journal.Append(MakeCriticalEvent("flush")));
        REQUIRE(journal.GetHealthSnapshot().currentRecords == 5);
        REQUIRE(::rename(path.c_str(), moved.c_str()) == 0);
        WritePrivateFile(path, "");
        health = journal.GetHealthSnapshot();
        REQUIRE(!health.capacityKnown);
        REQUIRE(OmsCapacityObservation(health, 45).find("\"bytes\":null") != std::string::npos);
        REQUIRE(!journal.Append(MakeCriticalEvent("must-not-reach-decoy")));
        REQUIRE(journal.GetHealthSnapshot().writePoisoned);
        REQUIRE(IsEmptyFile(path));
    }
    ::unsetenv("HEPTA_OMS_BATCH_SIZE");
    ::unsetenv("HEPTA_OMS_FLUSH_INTERVAL_MS");
    ClearReplayBudgets();
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::unlink(moved.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
}

void TestCapacitySerializationAndCadenceBoundaries()
{
    OmsJournalHealthSnapshot h;
    REQUIRE(OmsCapacityObservation(h, 1).find("\"status\":\"UNKNOWN\"") != std::string::npos);
    h.capacityKnown = true;
    h.replayMaxBytes = 100;
    h.replayMaxRecords = 100;
    h.currentBytes = 79;
    REQUIRE(OmsCapacityObservation(h, 2).find("\"status\":\"OK\"") != std::string::npos);
    h.currentBytes = 80;
    REQUIRE(OmsCapacityObservation(h, 3).find("\"status\":\"WARNING\"") != std::string::npos);
    h.currentBytes = 100;
    REQUIRE(OmsCapacityObservation(h, 4).find("\"status\":\"WARNING\"") != std::string::npos);
    h.currentBytes = 101;
    REQUIRE(OmsCapacityObservation(h, 5).find("\"status\":\"EXCEEDED\"") != std::string::npos);
    h.writePoisoned = true;
    REQUIRE(OmsCapacityObservation(h, 6).find("\"known\":false") != std::string::npos);
    OmsCapacityCadence cadence;
    const auto t = std::chrono::steady_clock::time_point();
    REQUIRE(cadence.Due(t));
    REQUIRE(!cadence.Due(t + std::chrono::milliseconds(4999)));
    REQUIRE(cadence.Due(t + std::chrono::milliseconds(5000)));
    REQUIRE(!cadence.Due(t + std::chrono::milliseconds(5001)));
}
