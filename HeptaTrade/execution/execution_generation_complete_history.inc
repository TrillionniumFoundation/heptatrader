// Complete-history projection for state that cannot be reconstructed from the
// bounded hot command set alone. This is deliberately separate from normal
// coordinator recovery: only callers that require historical economic events
// should pay O(retained history) startup work.

namespace
{
const std::size_t kMaximumCompleteHistoryGenerations = 1024U;

struct CompleteHistorySegment
{
    std::string generation;
    std::string sha256;
};

bool GenerationExtractCanonicalJsonString(
    const std::string& json,
    const std::string& key,
    std::string& value)
{
    const std::string needle = std::string("\"") + key + "\":\"";
    const std::size_t begin = json.find(needle);
    if (begin == std::string::npos ||
        json.find(needle, begin + needle.size()) != std::string::npos)
        return false;
    const std::size_t valueBegin = begin + needle.size();
    const std::size_t end = json.find('"', valueBegin);
    if (end == std::string::npos) return false;
    value = json.substr(valueBegin, end - valueBegin);
    return value.find('\\') == std::string::npos;
}

bool GenerationReplayVerifiedSegment(
    int storeFd,
    const CompleteHistorySegment& segment,
    std::size_t maxRecordBytes,
    const std::function<void(const OmsJournalEvent&)>& onEvent,
    std::uint64_t& totalRecords)
{
    const int generationFd = ::openat(storeFd, segment.generation.c_str(),
        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    struct stat generationMetadata;
    if (generationFd < 0 || ::fstat(generationFd, &generationMetadata) != 0 ||
        !GenerationPrivateDirectory(generationMetadata))
    {
        if (generationFd >= 0) ::close(generationFd);
        return false;
    }

    int segmentFd = -1;
    struct stat segmentIdentity;
    bool ok = GenerationOpenHashedFileAt(generationFd, "segment-000001.jsonl",
        segment.sha256, segmentFd, segmentIdentity);
    std::size_t replayed = 0;
    if (ok)
    {
        const std::uint64_t bytes = static_cast<std::uint64_t>(segmentIdentity.st_size);
        if (bytes > static_cast<std::uint64_t>(std::numeric_limits<std::size_t>::max()))
            ok = false;
        else
            ok = GenerationReplayRange(segmentFd, 0, segmentIdentity.st_size,
                static_cast<std::size_t>(bytes),
                std::numeric_limits<std::size_t>::max(),
                maxRecordBytes, onEvent, replayed);
    }
    if (segmentFd >= 0 && ::close(segmentFd) != 0) ok = false;
    if (::close(generationFd) != 0) ok = false;
    if (!ok || totalRecords > std::numeric_limits<std::uint64_t>::max() - replayed)
        return false;
    totalRecords += static_cast<std::uint64_t>(replayed);
    return true;
}
}

bool OmsGenerationStore::ReplayCompleteHistory(
    std::size_t maxRecordBytes,
    const std::function<void(const OmsJournalEvent&)>& onEvent,
    std::uint64_t& records,
    std::string& reason)
{
    records = 0;
    if (maxRecordBytes == 0 || !Prepare(reason)) return false;

    // Re-read and bind CURRENT so the lineage walk has the exact manifest
    // digest chosen by Prepare(), rather than trusting directory names alone.
    std::string runtimeCurrent;
    GenerationFields selected;
    static const char* const currentNames[] = {
        "generation", "current_sha256", "manifest_sha256",
        "runtime_manifest_sha256"
    };
    if (!GenerationReadPrivateFileAt(m_storeFd, "CURRENT.runtime",
            kMaximumGenerationMetadataBytes, runtimeCurrent) ||
        !GenerationParseFields(runtimeCurrent, kRuntimeCurrentHeader, selected) ||
        !GenerationExactFieldNames(selected, currentNames,
            sizeof(currentNames) / sizeof(currentNames[0])) ||
        selected["generation"] != m_generation ||
        !GenerationHexDigest(selected["manifest_sha256"]))
    {
        reason = "OMS_GENERATION_COMPLETE_CURRENT_CHANGED";
        Close();
        return false;
    }

    std::vector<CompleteHistorySegment> newestFirst;
    std::string generation = selected["generation"];
    std::string expectedManifestSha = selected["manifest_sha256"];
    bool reachedBase = false;

    for (std::size_t depth = 0;
         depth < kMaximumCompleteHistoryGenerations && !reachedBase;
         ++depth)
    {
        if (!GenerationSafeName(generation) ||
            !GenerationHexDigest(expectedManifestSha))
        {
            reason = "OMS_GENERATION_COMPLETE_LINEAGE_INVALID";
            Close();
            return false;
        }
        const int generationFd = ::openat(m_storeFd, generation.c_str(),
            O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
        struct stat generationMetadata;
        if (generationFd < 0 || ::fstat(generationFd, &generationMetadata) != 0 ||
            !GenerationPrivateDirectory(generationMetadata))
        {
            if (generationFd >= 0) ::close(generationFd);
            reason = "OMS_GENERATION_COMPLETE_DIRECTORY_UNSAFE";
            Close();
            return false;
        }

        std::string manifestJson, runtimeManifest;
        std::string runtimeManifestSha;
        bool ok = GenerationReadPrivateFileAt(generationFd, "manifest.json",
            kMaximumGenerationMetadataBytes, manifestJson) &&
            GenerationSha256(manifestJson.data(), manifestJson.size()) ==
                expectedManifestSha &&
            GenerationExtractCanonicalJsonString(manifestJson,
                "runtime_manifest_sha256", runtimeManifestSha) &&
            GenerationHexDigest(runtimeManifestSha) &&
            GenerationReadPrivateFileAt(generationFd, "runtime-manifest.txt",
                kMaximumGenerationMetadataBytes, runtimeManifest) &&
            GenerationSha256(runtimeManifest.data(), runtimeManifest.size()) ==
                runtimeManifestSha;
        if (!ok)
        {
            ::close(generationFd);
            reason = "OMS_GENERATION_COMPLETE_MANIFEST_BINDING_INVALID";
            Close();
            return false;
        }

        GenerationFields fields;
        bool v2 = GenerationParseFields(
            runtimeManifest, kRuntimeManifestHeaderV2, fields);
        bool v1 = false;
        if (!v2)
        {
            fields.clear();
            v1 = GenerationParseFields(
                runtimeManifest, kRuntimeManifestHeader, fields);
        }
        if ((!v2 && !v1) || fields["generation"] != generation ||
            !GenerationHexDigest(fields["segment_sha256"]) ||
            !GenerationVerifyHashedFileAt(generationFd, "segment-000001.jsonl",
                fields["segment_sha256"]))
        {
            ::close(generationFd);
            reason = "OMS_GENERATION_COMPLETE_SEGMENT_INVALID";
            Close();
            return false;
        }

        CompleteHistorySegment descriptor;
        descriptor.generation = generation;
        descriptor.sha256 = fields["segment_sha256"];
        newestFirst.push_back(descriptor);

        if (v1)
        {
            // A V1 segment is the complete immutable prefix at that generation;
            // walking its own historical parent would duplicate events.
            reachedBase = true;
        }
        else
        {
            const std::string parent = fields["parent_generation"];
            const std::string parentSha = fields["parent_manifest_sha256"];
            if (parent == "-")
            {
                if (parentSha != "-")
                {
                    ::close(generationFd);
                    reason = "OMS_GENERATION_COMPLETE_PARENT_INVALID";
                    Close();
                    return false;
                }
                reachedBase = true;
            }
            else
            {
                if (!GenerationSafeName(parent) || !GenerationHexDigest(parentSha))
                {
                    ::close(generationFd);
                    reason = "OMS_GENERATION_COMPLETE_PARENT_INVALID";
                    Close();
                    return false;
                }
                generation = parent;
                expectedManifestSha = parentSha;
            }
        }
        if (::close(generationFd) != 0)
        {
            reason = "OMS_GENERATION_COMPLETE_DIRECTORY_CLOSE_FAILED";
            Close();
            return false;
        }
    }

    if (!reachedBase || newestFirst.empty())
    {
        reason = "OMS_GENERATION_COMPLETE_CHAIN_LIMIT";
        Close();
        return false;
    }

    for (std::vector<CompleteHistorySegment>::const_reverse_iterator it =
             newestFirst.rbegin(); it != newestFirst.rend(); ++it)
    {
        if (!GenerationReplayVerifiedSegment(
                m_storeFd, *it, maxRecordBytes, onEvent, records))
        {
            reason = "OMS_GENERATION_COMPLETE_SEGMENT_REPLAY_FAILED";
            Close();
            return false;
        }
    }

    // Append only the active suffix: V1 uses the immutable prefix byte count,
    // while V2 uses the lineage sentinel byte count. Prepare() has already bound
    // m_journalPrefixSha256 to the corresponding bytes.
    const int journalFd = ::open(m_journalPath.c_str(),
        O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    struct stat before, named;
    if (journalFd < 0 || ::fstat(journalFd, &before) != 0 ||
        !GenerationPrivateFile(before) || before.st_size < 0 ||
        static_cast<std::uint64_t>(before.st_size) < m_journalPrefixBytes ||
        ::lstat(m_journalPath.c_str(), &named) != 0 ||
        !GenerationSameIdentity(before, named))
    {
        if (journalFd >= 0) ::close(journalFd);
        reason = "OMS_GENERATION_COMPLETE_ACTIVE_TAIL_UNSAFE";
        Close();
        return false;
    }
    std::string prefixDigest;
    if (!GenerationHashFd(journalFd, static_cast<off_t>(m_journalPrefixBytes),
            prefixDigest) || prefixDigest != m_journalPrefixSha256)
    {
        ::close(journalFd);
        reason = "OMS_GENERATION_COMPLETE_ACTIVE_PREFIX_MISMATCH";
        Close();
        return false;
    }

    const std::uint64_t suffixBytes =
        static_cast<std::uint64_t>(before.st_size) - m_journalPrefixBytes;
    bool ok = suffixBytes <=
        static_cast<std::uint64_t>(std::numeric_limits<std::size_t>::max());
    std::size_t suffixRecords = 0;
    if (ok)
        ok = GenerationReplayRange(journalFd,
            static_cast<off_t>(m_journalPrefixBytes), before.st_size,
            static_cast<std::size_t>(suffixBytes),
            std::numeric_limits<std::size_t>::max(), maxRecordBytes,
            onEvent, suffixRecords);
    struct stat after, namedAfter;
    ok = ok && ::fstat(journalFd, &after) == 0 &&
        ::lstat(m_journalPath.c_str(), &namedAfter) == 0 &&
        GenerationSameIdentity(before, after) &&
        GenerationSameIdentity(after, namedAfter);
    if (::close(journalFd) != 0) ok = false;
    if (!ok || records > std::numeric_limits<std::uint64_t>::max() - suffixRecords)
    {
        reason = "OMS_GENERATION_COMPLETE_ACTIVE_TAIL_REPLAY_FAILED";
        Close();
        return false;
    }
    records += static_cast<std::uint64_t>(suffixRecords);
    reason.clear();
    return true;
}
