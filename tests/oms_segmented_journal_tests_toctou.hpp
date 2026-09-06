void TestSegmentDirectoryOpenRejectsSymlinkTraversal()
{
    ResetBudgetTestEnvironment();
    const std::string root = MakeTempDirectory();
    const std::string real = root + "/real";
    const std::string child = real + "/child";
    const std::string finalLink = root + "/final-link";
    const std::string parentLink = root + "/parent-link";
    REQUIRE(::mkdir(real.c_str(), 0700) == 0);
    REQUIRE(::mkdir(child.c_str(), 0700) == 0);
    REQUIRE(::symlink("real/child", finalLink.c_str()) == 0);
    REQUIRE(::symlink("real", parentLink.c_str()) == 0);

    OmsSegmentedJournalLimits limits;
    limits.maximumActiveBytes = 4096;
    limits.maximumQueuedBytes = 4096;
    {
        OmsSegmentedJournal journal(limits);
        REQUIRE(!journal.Init(finalLink, "orders"));
    }
    {
        OmsSegmentedJournal journal(limits);
        REQUIRE(!journal.Init(parentLink + "/child", "orders"));
    }
    {
        OmsSegmentedJournal journal(limits);
        REQUIRE(!journal.Init(child + "/..", "orders"));
    }

    REQUIRE(::unlink(parentLink.c_str()) == 0);
    REQUIRE(::unlink(finalLink.c_str()) == 0);
    REQUIRE(::rmdir(child.c_str()) == 0);
    REQUIRE(::rmdir(real.c_str()) == 0);
    REQUIRE(::rmdir(root.c_str()) == 0);
}
