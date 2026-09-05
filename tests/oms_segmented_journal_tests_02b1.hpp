void TestSegmentInvalidLimitsAndIndependentCapacityDimensions()
{
    ResetBudgetTestEnvironment();
    const std::size_t bytes = MeasureSegmentEventBytes(SegmentEvent("e0", 64));
    for (int invalidCase = 0; invalidCase < 6; ++invalidCase)
    {
        const std::string directory = MakeTempDirectory();
        OmsSegmentedJournalLimits limits;
        switch (invalidCase)
        {
        case 0: limits.maximumActiveBytes = 0; break;
        case 1: limits.maximumActiveBytes =
                    OmsSegmentedJournalLimits::kActiveBytesCeiling + 1; break;
        case 2: limits.maximumQueuedBytes = limits.maximumActiveBytes + 1; break;
        case 3: limits.maximumTotalBytes = limits.maximumActiveBytes - 1; break;
        case 4: limits.maximumSealedSegments =
                    OmsSegmentedJournalLimits::kSealedSegmentsCeiling + 1; break;
        case 5: limits.maximumTotalRecords = 0; break;
        }
        OmsSegmentedJournal journal(limits);
        REQUIRE(!journal.Init(directory, "orders"));
        DIR* stream = ::opendir(directory.c_str());
        REQUIRE(stream != nullptr);
        unsigned int entries = 0;
        while (struct dirent* entry = ::readdir(stream))
            if (std::string(entry->d_name) != "." &&
                std::string(entry->d_name) != "..") ++entries;
        REQUIRE(entries == 0);
        REQUIRE(::closedir(stream) == 0);
        REQUIRE(::rmdir(directory.c_str()) == 0);
    }

    std::string directory = MakeTempDirectory();
    OmsSegmentedJournalLimits limits;
    limits.maximumActiveBytes = bytes;
    limits.maximumQueuedBytes = bytes;
    limits.maximumQueuedRecords = 10;
    limits.maximumTotalBytes = bytes * 4;
    limits.maximumSealedSegments = 1;
    limits.maximumTotalRecords = 10;
    {
        OmsSegmentedJournal journal(limits);
        REQUIRE(journal.Init(directory, "orders"));
        REQUIRE(journal.Append(SegmentEvent("e0", 64)));
