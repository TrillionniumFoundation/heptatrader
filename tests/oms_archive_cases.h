// Included in the existing native journal durability target.
void TestArchiveReplayAppendAndLogicalBudgets()
{
    ClearReplayBudgets();
    const std::string dir = MakeTempDirectory(), path = dir + "/journal";
    std::string original;
    {
        OmsJournal j; REQUIRE(j.Init(path));
        for (int i=0; i<12; ++i) REQUIRE(j.Append(MakeCriticalEvent("archive-"+std::to_string(i))));
        REQUIRE(j.Replay([&](const OmsJournalEvent& e) { original += e.rawLine+"\n"; }) == 12);
    }
    std::string packed;
    REQUIRE(hepta_oms_archive::EncodeMember(original, packed));
    REQUIRE(packed.size() < original.size());
    WritePrivateFile(path, packed);
    {
        OmsJournal j; REQUIRE(j.Init(path));
        REQUIRE(!j.Append(MakeCriticalEvent("unvalidated")));
        std::string replay;
        REQUIRE(j.Replay([&](const OmsJournalEvent& e) { replay += e.rawLine+"\n"; }) == 12);
        REQUIRE(replay == original);
        const auto h = j.GetHealthSnapshot();
        REQUIRE(h.gzipStorage && h.capacityKnown);
        REQUIRE(h.currentBytes == original.size() && h.storageBytes == packed.size());
        REQUIRE(j.Append(MakeCriticalEvent("compressed-append")));
        auto first = MakeCriticalEvent("compressed-pair-intent");
        auto second = MakeCriticalEvent("compressed-pair-attempt");
        second.eventType = "place_send_attempt";
        REQUIRE(j.AppendDurablePair(first, second) == OmsJournal::DurablePairResult::Committed);
        // The intentionally refused unvalidated append above is already a
        // critical-write attempt. Assert only these three new record attempts
        // and their two completed barriers, without erasing failed attempts.
        REQUIRE(j.GetHealthSnapshot().durableSyncWrites == h.durableSyncWrites + 2);
        REQUIRE(j.GetHealthSnapshot().criticalSyncWrites == h.criticalSyncWrites + 3);
    }
    {
        OmsJournal j; REQUIRE(j.Init(path));
        REQUIRE(j.Replay({}) == 15);
        const auto h = j.GetHealthSnapshot();
        REQUIRE(h.currentBytes > original.size() && h.storageBytes < h.currentBytes);
    }
    ::setenv("HEPTA_OMS_REPLAY_MAX_BYTES", std::to_string(original.size()-1).c_str(), 1);
    {
        OmsJournal j; REQUIRE(j.Init(path)); int calls=0;
        REQUIRE(j.Replay([&](const OmsJournalEvent&) { ++calls; }) == -1);
        REQUIRE(calls == 0 && !j.GetHealthSnapshot().capacityKnown);
        REQUIRE(!j.Append(MakeCriticalEvent("over-budget")));
    }
    ClearReplayBudgets();
    REQUIRE(::unlink(path.c_str())==0); REQUIRE(::rmdir(dir.c_str())==0);
}

void TestArchiveCorruptionIsCallbackAtomic()
{
    ClearReplayBudgets();
    const std::string dir=MakeTempDirectory(), path=dir+"/journal";
    std::string plain="{\"event\":\"status\",\"ts_ms\":1}\n", good;
    REQUIRE(hepta_oms_archive::EncodeMember(plain, good));
    std::string corrupt=good; corrupt[corrupt.size()-8] ^= 1;
    std::vector<std::string> bad{good.substr(0,good.size()-1), good+"garbage\n", good+"\0", corrupt};
    // Explicit NUL padding, unlike a C-string append, must actually add a byte.
    bad[2]=good+std::string(1,'\0');
    for (const auto& data: bad)
    {
        WritePrivateFile(path, data);
        OmsJournal j; REQUIRE(j.Init(path)); int calls=0;
        REQUIRE(j.Replay([&](const OmsJournalEvent&) { ++calls; })==-1);
        REQUIRE(calls==0 && j.GetHealthSnapshot().replayReasonCode=="OMS_REPLAY_ARCHIVE_INVALID");
    }
    // A damaged later member cannot commit the complete first member.
    WritePrivateFile(path,good+corrupt);
    {
        OmsJournal j; REQUIRE(j.Init(path)); int calls=0;
        REQUIRE(j.Replay([&](const OmsJournalEvent&) { ++calls; })==-1); REQUIRE(calls==0);
    }
    REQUIRE(::unlink(path.c_str())==0); REQUIRE(::rmdir(dir.c_str())==0);
}

void TestArchiveMaintenanceLockExcludesRuntime()
{
    const std::string dir=MakeTempDirectory(), path=dir+"/journal";
    WritePrivateFile(path, "");
    int fd=::open(path.c_str(),O_RDONLY|O_CLOEXEC); REQUIRE(fd>=0);
    REQUIRE(::flock(fd,LOCK_EX|LOCK_NB)==0);
    { OmsJournal blocked; REQUIRE(!blocked.Init(path)); }
    REQUIRE(::flock(fd,LOCK_UN)==0);
    {
        OmsJournal active; REQUIRE(active.Init(path));
        REQUIRE(::flock(fd,LOCK_EX|LOCK_NB)!=0);
        REQUIRE(active.Append(MakeCriticalEvent("lock-test")));
    }
    REQUIRE(::flock(fd,LOCK_EX|LOCK_NB)==0); REQUIRE(::close(fd)==0);
    REQUIRE(::unlink(path.c_str())==0); REQUIRE(::rmdir(dir.c_str())==0);
}
