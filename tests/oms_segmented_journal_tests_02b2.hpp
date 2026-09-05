        REQUIRE(journal.Append(SegmentEvent("e1", 64)));
        REQUIRE(!journal.Append(SegmentEvent("e2", 64)));
        const OmsSegmentedJournalHealthSnapshot health = journal.GetHealthSnapshot();
        REQUIRE(health.rotationCapacityRejects == 1);
        REQUIRE(health.totalCapacityRejects == 0);
        REQUIRE(health.sealedSegments == 1 && health.activeRecords == 1);
        REQUIRE((ReplaySegmentIds(journal) ==
                 std::vector<std::string>{"e0", "e1"}));
    }
    RemoveSegmentDirectory(directory);

    directory = MakeTempDirectory();
    limits.maximumSealedSegments = 3;
    limits.maximumTotalBytes = bytes * 2;
    {
        OmsSegmentedJournal journal(limits);
        REQUIRE(journal.Init(directory, "orders"));
        REQUIRE(journal.Append(SegmentEvent("e0", 64)));
        REQUIRE(journal.Append(SegmentEvent("e1", 64)));
        REQUIRE(!journal.Append(SegmentEvent("e2", 64)));
        const OmsSegmentedJournalHealthSnapshot health = journal.GetHealthSnapshot();
        REQUIRE(health.totalCapacityRejects == 1);
        REQUIRE(health.rotationCapacityRejects == 0);
    }
    RemoveSegmentDirectory(directory);

    directory = MakeTempDirectory();
    limits.maximumTotalBytes = bytes * 4;
    limits.maximumTotalRecords = 2;
    limits.maximumQueuedRecords = 2;
    {
        OmsSegmentedJournal journal(limits);
        REQUIRE(journal.Init(directory, "orders"));
        REQUIRE(journal.Append(SegmentEvent("e0", 64)));
        REQUIRE(journal.Append(SegmentEvent("e1", 64)));
        REQUIRE(!journal.Append(SegmentEvent("e2", 64)));
        REQUIRE(journal.GetHealthSnapshot().totalCapacityRejects == 1);
    }
    RemoveSegmentDirectory(directory);
}

void TestSegmentReplayReservationAndConcurrentProducers()
{
    ResetBudgetTestEnvironment();
