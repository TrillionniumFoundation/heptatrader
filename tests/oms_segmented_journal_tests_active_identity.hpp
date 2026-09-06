void TestSegmentActiveIdentityMismatchFailsClosed()
{
    ResetBudgetTestEnvironment();
    OmsSegmentedJournalLimits limits;
    limits.maximumActiveBytes = 4096;
    limits.maximumQueuedBytes = 4096;

    const std::string emptyDirectory = MakeTempDirectory();
    {
        OmsSegmentedJournal journal(limits);
        REQUIRE(journal.Init(emptyDirectory, "orders"));
        OmsJournalEvent first = SegmentEvent("e0");
        first.eventType = "place_sent";
        REQUIRE(journal.Append(first));

        const std::string active = emptyDirectory + "/orders.active.jsonl";
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
    RemoveSegmentDirectory(emptyDirectory);

    const std::string shortenedDirectory = MakeTempDirectory();
    {
        OmsSegmentedJournal journal(limits);
        REQUIRE(journal.Init(shortenedDirectory, "orders"));
        OmsJournalEvent first = SegmentEvent("e0");
        first.eventType = "place_sent";
        REQUIRE(journal.Append(first));
        const std::string active = shortenedDirectory + "/orders.active.jsonl";
        struct stat metadata;
        REQUIRE(::stat(active.c_str(), &metadata) == 0 && metadata.st_size > 1);
        REQUIRE(::truncate(active.c_str(), metadata.st_size - 1) == 0);
        OmsJournalEvent second = SegmentEvent("e1");
        second.eventType = "place_sent";
        REQUIRE(!journal.Append(second));
        REQUIRE(!journal.Rotate());
        REQUIRE(journal.GetSealedSegments().empty());
        REQUIRE(journal.GetHealthSnapshot().activeRecords == 1);
    }
    RemoveSegmentDirectory(shortenedDirectory);

    const std::string extendedDirectory = MakeTempDirectory();
    {
        OmsSegmentedJournal journal(limits);
        REQUIRE(journal.Init(extendedDirectory, "orders"));
        OmsJournalEvent first = SegmentEvent("e0");
        first.eventType = "place_sent";
        REQUIRE(journal.Append(first));
        const std::string active = extendedDirectory + "/orders.active.jsonl";
        struct stat metadata;
        REQUIRE(::stat(active.c_str(), &metadata) == 0 && metadata.st_size > 0);
        REQUIRE(static_cast<std::size_t>(metadata.st_size) + 1 <
                limits.maximumActiveBytes);
        REQUIRE(::truncate(active.c_str(), metadata.st_size + 1) == 0);
        OmsJournalEvent second = SegmentEvent("e1");
        second.eventType = "place_sent";
        REQUIRE(!journal.Append(second));
        REQUIRE(!journal.Rotate());
        REQUIRE(journal.GetSealedSegments().empty());
        REQUIRE(journal.GetHealthSnapshot().activeRecords == 1);
    }
    RemoveSegmentDirectory(extendedDirectory);

    const std::string oversizedDirectory = MakeTempDirectory();
    {
        OmsSegmentedJournal journal(limits);
        REQUIRE(journal.Init(oversizedDirectory, "orders"));
        OmsJournalEvent first = SegmentEvent("e0");
        first.eventType = "place_sent";
        REQUIRE(journal.Append(first));
        const std::string active = oversizedDirectory + "/orders.active.jsonl";
        REQUIRE(::truncate(active.c_str(),
                           static_cast<off_t>(limits.maximumActiveBytes + 1)) == 0);
        OmsJournalEvent second = SegmentEvent("e1");
        second.eventType = "place_sent";
        REQUIRE(!journal.Append(second));
        REQUIRE(!journal.Rotate());
        REQUIRE(journal.GetSealedSegments().empty());
        REQUIRE(journal.GetHealthSnapshot().activeRecords == 1);
    }
    RemoveSegmentDirectory(oversizedDirectory);
}
