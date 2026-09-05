            REQUIRE(segment.digest.size() == 64);
            REQUIRE(segment.records == 1 && segment.bytes == bytes);
        }
    }
    {
        OmsSegmentedJournal recovered(limits);
        REQUIRE(recovered.Init(directory, "orders"));
        REQUIRE((ReplaySegmentIds(recovered) ==
                 std::vector<std::string>{"e0", "e1", "e2"}));
        REQUIRE(recovered.GetHealthSnapshot().sealedSegments == 2);
    }
    RemoveSegmentDirectory(directory);

    // Independent known digest for the exact writer-produced record below.
    const std::string digestDirectory = MakeTempDirectory();
    {
        OmsSegmentedJournalLimits digestLimits;
        digestLimits.maximumActiveBytes = 4096;
        digestLimits.maximumQueuedBytes = 4096;
        OmsSegmentedJournal journal(digestLimits);
        REQUIRE(journal.Init(digestDirectory, "orders"));
        REQUIRE(journal.Append(SegmentEvent("e0")));
        REQUIRE(journal.Rotate());
        const OmsJournalSegmentDescriptor segment = journal.GetSealedSegments()[0];
        REQUIRE(segment.digest ==
            "7a9fbe449c25ee0921ea9f12ebc839648e02eebbec34f9f1abb373009a72fa19");
    }
    RemoveSegmentDirectory(digestDirectory);
}

void TestSegmentLockSecurityAndPostRenameRestart()
{
    ResetBudgetTestEnvironment();
    const std::string directory = MakeTempDirectory();
    OmsSegmentedJournalLimits limits;
    limits.maximumActiveBytes = 4096;
    limits.maximumQueuedBytes = 4096;
    {
        OmsSegmentedJournal first(limits);
        REQUIRE(first.Init(directory, "orders"));
        OmsSegmentedJournal second(limits);
        REQUIRE(!second.Init(directory, "orders"));
        REQUIRE(first.Append(SegmentEvent("e0")));
        REQUIRE(first.Rotate());
    }
    // Equivalent persistent layout to a crash after atomic rename and before
    // new-active creation: restart recreates active and replays the segment.
    REQUIRE(::unlink((directory + "/orders.active.jsonl").c_str()) == 0);
    {
        OmsSegmentedJournal recovered(limits);
        REQUIRE(recovered.Init(directory, "orders"));
        REQUIRE((ReplaySegmentIds(recovered) ==
                 std::vector<std::string>{"e0"}));
    }
    REQUIRE(::chmod(directory.c_str(), 0755) == 0);
    {
        OmsSegmentedJournal insecure(limits);
        REQUIRE(!insecure.Init(directory, "orders"));
    }
    REQUIRE(::chmod(directory.c_str(), 0700) == 0);
    {
        OmsSegmentedJournal badName(limits);
        REQUIRE(!badName.Init(directory, "../orders"));
    }
    RemoveSegmentDirectory(directory);
}

void TestSegmentTamperSequenceAndMalformedSurfaceReject()
{
    ResetBudgetTestEnvironment();
    OmsSegmentedJournalLimits limits;
    limits.maximumActiveBytes = 4096;
    limits.maximumQueuedBytes = 4096;
    std::string directory = MakeTempDirectory();
    std::string segmentName;
    {
        OmsSegmentedJournal journal(limits);
        REQUIRE(journal.Init(directory, "orders"));
        REQUIRE(journal.Append(SegmentEvent("e0")));
        REQUIRE(journal.Rotate());
        segmentName = journal.GetSealedSegments()[0].filename;
    }
    {
        std::fstream file(directory + '/' + segmentName,
                          std::ios::in | std::ios::out | std::ios::binary);
        REQUIRE(file.is_open());
        std::string bytes((std::istreambuf_iterator<char>(file)), {});
        const std::size_t position = bytes.find("e0");
        REQUIRE(position != std::string::npos);
        bytes[position] = 'x';
        file.seekp(0);
        file.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
        REQUIRE(file.good());
    }
