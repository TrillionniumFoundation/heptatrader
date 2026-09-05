    {
        OmsSegmentedJournal tampered(limits);
        REQUIRE(!tampered.Init(directory, "orders"));
    }
    RemoveSegmentDirectory(directory);

    directory = MakeTempDirectory();
    {
        OmsSegmentedJournal journal(limits);
        REQUIRE(journal.Init(directory, "orders"));
        REQUIRE(journal.Append(SegmentEvent("e0")));
        REQUIRE(journal.Rotate());
        segmentName = journal.GetSealedSegments()[0].filename;
    }
    std::string gapName = segmentName;
    const std::size_t sequence = gapName.find("00000000000000000001");
    REQUIRE(sequence != std::string::npos);
    gapName.replace(sequence, 20, "00000000000000000002");
    REQUIRE(::rename((directory + '/' + segmentName).c_str(),
                     (directory + '/' + gapName).c_str()) == 0);
    {
        OmsSegmentedJournal gap(limits);
        REQUIRE(!gap.Init(directory, "orders"));
    }
    RemoveSegmentDirectory(directory);

    directory = MakeTempDirectory();
    WritePrivateFile(directory + "/orders.segment.malformed", "x\n");
    {
        OmsSegmentedJournal malformed(limits);
        REQUIRE(!malformed.Init(directory, "orders"));
    }
    RemoveSegmentDirectory(directory);
}

void TestSegmentCapacityAndCrossSegmentCallbackAtomicity()
{
    ResetBudgetTestEnvironment();
    const std::size_t bytes = MeasureSegmentEventBytes(SegmentEvent("e0", 64));
    std::string directory = MakeTempDirectory();
    OmsSegmentedJournalLimits limits;
    limits.maximumActiveBytes = bytes;
    limits.maximumQueuedBytes = bytes;
    limits.maximumQueuedRecords = 2;
    limits.maximumTotalBytes = bytes * 2;
    limits.maximumSealedSegments = 1;
    limits.maximumTotalRecords = 2;
    {
        OmsSegmentedJournal journal(limits);
        REQUIRE(journal.Init(directory, "orders"));
        REQUIRE(journal.Append(SegmentEvent("e0", 64)));
        REQUIRE(journal.Append(SegmentEvent("e1", 64)));
        REQUIRE(!journal.Append(SegmentEvent("e2", 64)));
        const OmsSegmentedJournalHealthSnapshot health = journal.GetHealthSnapshot();
        REQUIRE(health.logicalTotalRecords == 2);
        REQUIRE(health.totalCapacityRejects == 1);
        REQUIRE((ReplaySegmentIds(journal) ==
                 std::vector<std::string>{"e0", "e1"}));
    }
    RemoveSegmentDirectory(directory);

    directory = MakeTempDirectory();
    limits.maximumTotalBytes = bytes * 4;
    limits.maximumSealedSegments = 3;
    limits.maximumTotalRecords = 10;
    limits.maximumQueuedRecords = 10;
    {
        OmsSegmentedJournal journal(limits);
        REQUIRE(journal.Init(directory, "orders"));
        REQUIRE(journal.Append(SegmentEvent("e0", 64)));
        REQUIRE(journal.Rotate());
        REQUIRE(journal.Append(SegmentEvent("e1", 64)));
        REQUIRE(journal.Replay({}) == 2);
        {
            std::ofstream corrupt(directory + "/orders.active.jsonl",
                                  std::ios::app | std::ios::binary);
            REQUIRE(corrupt.is_open());
            corrupt << "{\"unknown\":true}\n";
            REQUIRE(corrupt.good());
        }
        int callbacks = 0;
        REQUIRE(journal.Replay([&](const OmsJournalEvent&) { ++callbacks; }) == -1);
        REQUIRE(callbacks == 0);
    }
    RemoveSegmentDirectory(directory);
}


