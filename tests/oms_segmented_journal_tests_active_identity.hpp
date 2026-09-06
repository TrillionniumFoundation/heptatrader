void TestSegmentActiveIdentityMismatchFailsClosed()
{
    ResetBudgetTestEnvironment();
    const std::string directory = MakeTempDirectory();
    OmsSegmentedJournalLimits limits;
    limits.maximumActiveBytes = 4096;
    limits.maximumQueuedBytes = 4096;
    {
        OmsSegmentedJournal journal(limits);
        REQUIRE(journal.Init(directory, "orders"));
        OmsJournalEvent first = SegmentEvent("e0");
        first.eventType = "place_sent";
        REQUIRE(journal.Append(first));

        const std::string active = directory + "/orders.active.jsonl";
        struct stat metadata;
        REQUIRE(::stat(active.c_str(), &metadata) == 0 && metadata.st_size > 0);
        REQUIRE(::truncate(active.c_str(), 0) == 0);

        OmsJournalEvent second = SegmentEvent("e1");
        second.eventType = "place_sent";
        REQUIRE(!journal.Append(second));
        REQUIRE(!journal.Rotate());
        REQUIRE(journal.GetSealedSegments().empty());
        const OmsSegmentedJournalHealthSnapshot health = journal.GetHealthSnapshot();
        REQUIRE(health.activeRecords == 1);
        REQUIRE(health.segmentIntegrityRejects >= 2);
    }
    RemoveSegmentDirectory(directory);
}
