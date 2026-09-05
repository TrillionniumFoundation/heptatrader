    std::string directory = MakeTempDirectory();
    OmsSegmentedJournalLimits limits;
    limits.maximumActiveBytes = 4096;
    limits.maximumQueuedBytes = 4096;
    limits.maximumQueuedRecords = 1000;
    limits.maximumTotalBytes = 65536;
    limits.maximumTotalRecords = 1000;
    {
        OmsSegmentedJournal journal(limits);
        REQUIRE(journal.Init(directory, "orders"));
        REQUIRE(journal.Append(SegmentEvent("e0")));
        std::atomic<bool> entered{false};
        std::atomic<bool> release{false};
        std::thread reader([&]() {
            REQUIRE(journal.Replay([&](const OmsJournalEvent&) {
                entered.store(true, std::memory_order_release);
                while (!release.load(std::memory_order_acquire))
                    std::this_thread::yield();
            }) == 1);
        });
        const auto deadline = std::chrono::steady_clock::now() +
            std::chrono::seconds(5);
        while (!entered.load(std::memory_order_acquire) &&
               std::chrono::steady_clock::now() < deadline)
            std::this_thread::yield();
        REQUIRE(entered.load(std::memory_order_acquire));
        REQUIRE(journal.Replay({}) == -1);
        REQUIRE(journal.Append(SegmentEvent("e1")));
        release.store(true, std::memory_order_release);
        reader.join();
        REQUIRE(journal.Replay({}) == 2);
        REQUIRE(journal.GetHealthSnapshot().replayBusyRejects == 1);
    }
    RemoveSegmentDirectory(directory);

    directory = MakeTempDirectory();
    limits.maximumActiveBytes = 2048;
    limits.maximumQueuedBytes = 2048;
    limits.maximumQueuedRecords = 800;
    limits.maximumTotalBytes = 256 * 1024;
    limits.maximumSealedSegments = 200;
    limits.maximumTotalRecords = 800;
    {
        OmsSegmentedJournal journal(limits);
        REQUIRE(journal.Init(directory, "orders"));
        std::vector<std::vector<std::string>> accepted(4);
        std::vector<std::thread> producers;
        for (int worker = 0; worker < 4; ++worker)
            producers.emplace_back([&, worker]() {
                for (int index = 0; index < 100; ++index)
                {
                    const std::string id = std::to_string(worker) + ':' +
                        std::to_string(index);
                    if (journal.Append(SegmentEvent(id)))
                        accepted[worker].push_back(id);
                    const OmsSegmentedJournalHealthSnapshot health =
                        journal.GetHealthSnapshot();
                    REQUIRE(health.logicalTotalBytes <= health.limits.maximumTotalBytes);
                    REQUIRE(health.logicalTotalRecords <= health.limits.maximumTotalRecords);
                }
            });
        for (std::thread& producer : producers) producer.join();
        std::vector<std::string> expected;
        for (const std::vector<std::string>& ids : accepted)
            expected.insert(expected.end(), ids.begin(), ids.end());
        std::vector<std::string> replayed = ReplaySegmentIds(journal);
        REQUIRE(replayed.size() == expected.size());
        std::sort(expected.begin(), expected.end());
        std::sort(replayed.begin(), replayed.end());
        REQUIRE(expected == replayed);
    }
    RemoveSegmentDirectory(directory);
}
