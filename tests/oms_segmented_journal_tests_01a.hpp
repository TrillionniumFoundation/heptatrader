OmsJournalEvent SegmentEvent(const std::string& id, std::size_t payloadBytes = 0)
{
    OmsJournalEvent event;
    event.eventType = "ack";
    event.tsMs = 100;
    event.reqId = id;
    event.clientReqId = id;
    event.eventId = id;
    event.venue = "SIMULATOR";
    event.account = "SIM";
    event.executionDomain = "SIM:test";
    event.brokerMessage.assign(payloadBytes, 'x');
    return event;
}

std::size_t MeasureSegmentEventBytes(const OmsJournalEvent& event)
{
    const std::string directory = MakeTempDirectory();
    const std::string path = directory + "/measure";
    std::size_t bytes = 0;
    {
        OmsJournal journal;
        REQUIRE(journal.Init(path));
        REQUIRE(journal.Append(event));
        REQUIRE(journal.Replay({}) == 1);
        struct stat metadata;
        REQUIRE(::stat(path.c_str(), &metadata) == 0 && metadata.st_size > 0);
        bytes = static_cast<std::size_t>(metadata.st_size);
    }
    REQUIRE(::unlink(path.c_str()) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
    return bytes;
}

void RemoveSegmentDirectory(const std::string& directory)
{
    DIR* stream = ::opendir(directory.c_str());
    REQUIRE(stream != nullptr);
    for (;;)
    {
        errno = 0;
        struct dirent* entry = ::readdir(stream);
        if (entry == nullptr)
        {
            REQUIRE(errno == 0);
            break;
        }
        const std::string name(entry->d_name);
        if (name == "." || name == "..") continue;
        REQUIRE(::unlink((directory + '/' + name).c_str()) == 0);
    }
    REQUIRE(::closedir(stream) == 0);
    REQUIRE(::rmdir(directory.c_str()) == 0);
}

std::vector<std::string> ReplaySegmentIds(OmsSegmentedJournal& journal)
{
    std::vector<std::string> ids;
    const int count = journal.Replay([&](const OmsJournalEvent& event) {
        ids.push_back(event.reqId);
    });
    REQUIRE(count == static_cast<int>(ids.size()));
    return ids;
}

void TestSegmentRotationRestartAndDigestIdentity()
{
    ResetBudgetTestEnvironment();
    const std::size_t bytes = MeasureSegmentEventBytes(SegmentEvent("e0", 64));
    const std::string directory = MakeTempDirectory();
    OmsSegmentedJournalLimits limits;
    limits.maximumActiveBytes = bytes * 2 - 1;
    limits.maximumQueuedBytes = limits.maximumActiveBytes;
    limits.maximumQueuedRecords = 10;
    limits.maximumTotalBytes = limits.maximumActiveBytes * 4;
    limits.maximumSealedSegments = 3;
    limits.maximumTotalRecords = 10;
    {
        OmsSegmentedJournal journal(limits);
        REQUIRE(journal.Init(directory, "orders"));
        REQUIRE(journal.Append(SegmentEvent("e0", 64)));
        REQUIRE(journal.Append(SegmentEvent("e1", 64)));
        OmsSegmentedJournalHealthSnapshot health = journal.GetHealthSnapshot();
        REQUIRE(health.rotations == 1 && health.sealedSegments == 1);
        REQUIRE(health.activeRecords == 1 && health.logicalTotalRecords == 2);
        REQUIRE(journal.Append(SegmentEvent("e2", 64)));
        REQUIRE((ReplaySegmentIds(journal) ==
                 std::vector<std::string>{"e0", "e1", "e2"}));
        const std::vector<OmsJournalSegmentDescriptor> segments =
            journal.GetSealedSegments();
        REQUIRE(segments.size() == 2);
        REQUIRE(segments[0].sequence == 1 && segments[1].sequence == 2);
        for (const OmsJournalSegmentDescriptor& segment : segments)
        {
