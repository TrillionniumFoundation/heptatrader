// V2 compatibility layer for lineage-sealed OMS generations.
//
// execution_generation_support.inc remains the exact V1 implementation. The
// owning translation unit compiles it under private *GenerationV1 method names,
// then this file provides the public Prepare/Recover/RecoveryCapacity dispatch.
// All lookup/index methods stay shared because V1 and V2 intentionally use the
// same cumulative full-key runtime index formats.

namespace
{
const char* const kRuntimeManifestHeaderV2 = "HEPTA_OMS_RUNTIME_GENERATION_V2";
const std::uint64_t kMaximumActiveTailHeaderBytes = 512U;

struct CompleteHistorySegment
{
    std::string generation;
    std::string sha256;
};

bool GenerationExtractCanonicalJsonDigest(const std::string& json,
                                          const char* field,
                                          std::string& digest)
{
    const std::string marker = std::string("\"") + field + "\":\"";
    const std::size_t start = json.find(marker);
    if (start == std::string::npos || json.find(marker, start + marker.size()) != std::string::npos)
        return false;
    const std::size_t valueStart = start + marker.size();
    const std::size_t valueEnd = json.find('"', valueStart);
    if (valueEnd == std::string::npos || valueEnd - valueStart != 64U)
        return false;
    digest = json.substr(valueStart, valueEnd - valueStart);
    return GenerationHexDigest(digest);
}
}

bool OmsGenerationStore::Prepare(std::string& reason)
{
    m_segmentedTail = false;
    std::string v1Reason;
    if (PrepareGenerationV1(v1Reason))
    {
        reason.clear();
        return true;
    }

    // A V1 failure is never treated as absence. Try V2 only against the exact
    // same CURRENT selection; any malformed/corrupt state still fails closed.
    Close();
    m_segmentedTail = false;
    m_storeFd = ::open(m_storePath.c_str(),
        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    struct stat storeMetadata;
    if (m_storeFd < 0 || ::fstat(m_storeFd, &storeMetadata) != 0 ||
        !GenerationPrivateDirectory(storeMetadata))
    {
        reason = "OMS_GENERATION_STORE_UNSAFE";
        Close();
        return false;
    }

    std::string runtimeCurrent, current;
    if (!GenerationReadPrivateFileAt(m_storeFd, "CURRENT.runtime",
            kMaximumGenerationMetadataBytes, runtimeCurrent) ||
        !GenerationReadPrivateFileAt(m_storeFd, "CURRENT",
            kMaximumGenerationMetadataBytes, current))
    {
        reason = "OMS_GENERATION_CURRENT_UNSAFE";
        Close();
        return false;
    }
    GenerationFields selected;
    static const char* const currentNames[] = {
        "generation", "current_sha256", "manifest_sha256",
        "runtime_manifest_sha256"
    };
    if (!GenerationParseFields(runtimeCurrent, kRuntimeCurrentHeader, selected) ||
        !GenerationExactFieldNames(selected, currentNames,
            sizeof(currentNames) / sizeof(currentNames[0])) ||
        !GenerationSafeName(selected["generation"]) ||
        !GenerationHexDigest(selected["current_sha256"]) ||
        !GenerationHexDigest(selected["manifest_sha256"]) ||
        !GenerationHexDigest(selected["runtime_manifest_sha256"]) ||
        GenerationSha256(current.data(), current.size()) != selected["current_sha256"])
    {
        reason = "OMS_GENERATION_RUNTIME_CURRENT_INVALID";
        Close();
        return false;
    }
    const std::string expectedCurrent =
        std::string("{\"generation\":\"") + selected["generation"] +
        "\",\"manifest_sha256\":\"" + selected["manifest_sha256"] +
        "\",\"runtime_manifest_sha256\":\"" + selected["runtime_manifest_sha256"] +
        "\",\"schema\":\"heptatrader.oms-current.v1\"}\n";
    if (current != expectedCurrent)
    {
        reason = "OMS_GENERATION_CURRENT_BINDING_INVALID";
        Close();
        return false;
    }

    m_generation = selected["generation"];
    m_generationFd = ::openat(m_storeFd, m_generation.c_str(),
        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    struct stat generationMetadata;
    if (m_generationFd < 0 || ::fstat(m_generationFd, &generationMetadata) != 0 ||
        !GenerationPrivateDirectory(generationMetadata))
    {
        reason = "OMS_GENERATION_DIRECTORY_UNSAFE";
        Close();
        return false;
    }
    if (!GenerationVerifyHashedFileAt(m_generationFd, "manifest.json",
            selected["manifest_sha256"]))
    {
        reason = "OMS_GENERATION_MANIFEST_DIGEST_MISMATCH";
        Close();
        return false;
    }

    std::string runtimeManifest;
    if (!GenerationReadPrivateFileAt(m_generationFd, "runtime-manifest.txt",
            kMaximumGenerationMetadataBytes, runtimeManifest) ||
        GenerationSha256(runtimeManifest.data(), runtimeManifest.size()) !=
            selected["runtime_manifest_sha256"])
    {
        reason = "OMS_GENERATION_RUNTIME_MANIFEST_DIGEST_MISMATCH";
        Close();
        return false;
    }

    GenerationFields manifest;
    static const char* const manifestNames[] = {
        "generation", "parent_generation", "parent_manifest_sha256",
        "history_records", "segment_records", "command_records",
        "send_attempt_records", "hot_replay_records", "segment_sha256",
        "checkpoint_sha256", "command_index_sha256",
        "runtime_command_index_sha256", "send_attempt_index_sha256",
        "hot_replay_sha256", "active_tail_header_bytes",
        "active_tail_header_sha256", "authorization_effect",
        "paper_authorized", "live_authorized"
    };
    std::uint64_t historyRecords = 0, segmentRecords = 0;
    if (!GenerationParseFields(runtimeManifest, kRuntimeManifestHeaderV2, manifest) ||
        !GenerationExactFieldNames(manifest, manifestNames,
            sizeof(manifestNames) / sizeof(manifestNames[0])) ||
        manifest["generation"] != m_generation ||
        (manifest["parent_generation"] != "-" &&
         !GenerationSafeName(manifest["parent_generation"])) ||
        ((manifest["parent_generation"] == "-") !=
         (manifest["parent_manifest_sha256"] == "-")) ||
        (manifest["parent_generation"] != "-" &&
         !GenerationHexDigest(manifest["parent_manifest_sha256"])) ||
        !GenerationParseUnsigned(manifest["history_records"], historyRecords) ||
        !GenerationParseUnsigned(manifest["segment_records"], segmentRecords) ||
        segmentRecords > historyRecords ||
        !GenerationParseUnsigned(manifest["command_records"], m_commandRecords) ||
        !GenerationParseUnsigned(manifest["send_attempt_records"], m_sendAttemptRecords) ||
        !GenerationParseUnsigned(manifest["hot_replay_records"], m_hotReplayRecords) ||
        !GenerationParseUnsigned(manifest["active_tail_header_bytes"], m_journalPrefixBytes) ||
        m_journalPrefixBytes == 0 || m_journalPrefixBytes > kMaximumActiveTailHeaderBytes ||
        !GenerationHexDigest(manifest["active_tail_header_sha256"]) ||
        !GenerationHexDigest(manifest["segment_sha256"]) ||
        !GenerationHexDigest(manifest["checkpoint_sha256"]) ||
        !GenerationHexDigest(manifest["command_index_sha256"]) ||
        !GenerationHexDigest(manifest["runtime_command_index_sha256"]) ||
        !GenerationHexDigest(manifest["send_attempt_index_sha256"]) ||
        !GenerationHexDigest(manifest["hot_replay_sha256"]) ||
        manifest["authorization_effect"] != "NONE" ||
        manifest["paper_authorized"] != "0" || manifest["live_authorized"] != "0")
    {
        reason = "OMS_GENERATION_RUNTIME_MANIFEST_V2_INVALID";
        Close();
        return false;
    }
    m_journalPrefixSha256 = manifest["active_tail_header_sha256"];

    if (!GenerationVerifyHashedFileAt(m_generationFd, "segment-000001.jsonl",
            manifest["segment_sha256"]) ||
        !GenerationVerifyHashedFileAt(m_generationFd, "checkpoint.json",
            manifest["checkpoint_sha256"]) ||
        !GenerationVerifyHashedFileAt(m_generationFd, "command-index.tsv",
            manifest["command_index_sha256"]))
    {
        reason = "OMS_GENERATION_RUNTIME_FILE_DIGEST_MISMATCH";
        Close();
        return false;
    }
    if (!GenerationOpenHashedFileAt(m_generationFd, "runtime-command-index.tsv",
            manifest["runtime_command_index_sha256"],
            m_commandIndexFd, m_commandIndexIdentity) ||
        !GenerationOpenHashedFileAt(m_generationFd, "send-attempt-index.tsv",
            manifest["send_attempt_index_sha256"],
            m_sendIndexFd, m_sendIndexIdentity) ||
        !GenerationVerifyHashedFileAt(m_generationFd, "hot-replay.jsonl",
            manifest["hot_replay_sha256"]))
    {
        reason = "OMS_GENERATION_RUNTIME_INDEX_DIGEST_MISMATCH";
        Close();
        return false;
    }

    if (manifest["parent_generation"] != "-")
    {
        const int parentFd = ::openat(m_storeFd, manifest["parent_generation"].c_str(),
            O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
        struct stat parentMetadata;
        const bool parentOk = parentFd >= 0 &&
            ::fstat(parentFd, &parentMetadata) == 0 &&
            GenerationPrivateDirectory(parentMetadata) &&
            GenerationVerifyHashedFileAt(parentFd, "manifest.json",
                manifest["parent_manifest_sha256"]);
        if (parentFd >= 0) ::close(parentFd);
        if (!parentOk)
        {
            reason = "OMS_GENERATION_PARENT_BINDING_INVALID";
            Close();
            return false;
        }
    }

    m_segmentedTail = true;
    reason.clear();
    return true;
}

bool OmsGenerationStore::Recover(
    std::size_t maxTailBytes,
    std::size_t maxTailRecords,
    std::size_t maxRecordBytes,
    const std::function<void(const OmsJournalEvent&)>& onEvent,
    std::string& reason)
{
    if (!Prepare(reason)) return false;
    if (!m_segmentedTail)
    {
        // Re-run the unchanged V1 path so all of its prefix/alignment/identity
        // checks remain exactly authoritative.
        Close();
        m_segmentedTail = false;
        return RecoverGenerationV1(
            maxTailBytes, maxTailRecords, maxRecordBytes, onEvent, reason);
    }

    const int hotFd = ::openat(m_generationFd, "hot-replay.jsonl",
        O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    struct stat hotIdentity;
    bool ok = hotFd >= 0 && ::fstat(hotFd, &hotIdentity) == 0 &&
        GenerationPrivateFile(hotIdentity) && hotIdentity.st_size >= 0 &&
        static_cast<std::uint64_t>(hotIdentity.st_size) <= maxTailBytes;
    std::size_t hotRecords = 0;
    if (ok)
        ok = GenerationReplayRange(hotFd, 0, hotIdentity.st_size,
            maxTailBytes, maxTailRecords, maxRecordBytes, onEvent, hotRecords) &&
            hotRecords == m_hotReplayRecords;
    if (hotFd >= 0) ::close(hotFd);
    if (!ok)
    {
        reason = "OMS_GENERATION_HOT_REPLAY_FAILED";
        Close();
        return false;
    }

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
        reason = "OMS_GENERATION_ACTIVE_TAIL_UNSAFE";
        Close();
        return false;
    }
    std::string headerDigest;
    if (!GenerationHashFd(journalFd, static_cast<off_t>(m_journalPrefixBytes),
            headerDigest) || headerDigest != m_journalPrefixSha256)
    {
        ::close(journalFd);
        reason = "OMS_GENERATION_ACTIVE_TAIL_LINEAGE_MISMATCH";
        Close();
        return false;
    }
    char newline = 0;
    ssize_t count;
    do
    {
        count = ::pread(journalFd, &newline, 1,
            static_cast<off_t>(m_journalPrefixBytes - 1U));
    } while (count < 0 && errno == EINTR);
    if (count != 1 || newline != '\n')
    {
        ::close(journalFd);
        reason = "OMS_GENERATION_ACTIVE_TAIL_HEADER_INVALID";
        Close();
        return false;
    }

    std::size_t tailRecords = 0;
    ok = GenerationReplayRange(journalFd,
        static_cast<off_t>(m_journalPrefixBytes), before.st_size,
        maxTailBytes, maxTailRecords, maxRecordBytes, onEvent, tailRecords);
    struct stat after, namedAfter;
    ok = ok && ::fstat(journalFd, &after) == 0 &&
        ::lstat(m_journalPath.c_str(), &namedAfter) == 0 &&
        GenerationSameIdentity(before, after) && GenerationSameIdentity(after, namedAfter);
    if (::close(journalFd) != 0) ok = false;
    if (!ok)
    {
        reason = "OMS_GENERATION_TAIL_REPLAY_FAILED";
        Close();
        return false;
    }
    m_active = true;
    reason.clear();
    return true;
}

bool OmsGenerationStore::ReplayCompleteHistory(
    std::size_t maxRecordBytes,
    const std::function<void(const OmsJournalEvent&)>& onEvent,
    std::uint64_t& records,
    std::string& reason)
{
    records = 0;
    if (!Prepare(reason)) return false;
    if (!m_segmentedTail)
    {
        reason = "OMS_GENERATION_COMPLETE_REPLAY_REQUIRES_V2";
        Close();
        return false;
    }

    std::vector<CompleteHistorySegment> reverseSegments;
    std::set<std::string> seen;
    std::string generation = m_generation;
    for (;;)
    {
        if (!seen.insert(generation).second)
        {
            reason = "OMS_GENERATION_PARENT_CHAIN_INVALID";
            Close();
            return false;
        }
        const int generationFd = ::openat(m_storeFd, generation.c_str(),
            O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
        struct stat directoryIdentity;
        if (generationFd < 0 || ::fstat(generationFd, &directoryIdentity) != 0 ||
            !GenerationPrivateDirectory(directoryIdentity))
        {
            if (generationFd >= 0) ::close(generationFd);
            reason = "OMS_GENERATION_DIRECTORY_UNSAFE";
            Close();
            return false;
        }
        std::string runtimeManifest;
        if (!GenerationReadPrivateFileAt(generationFd, "runtime-manifest.txt",
                kMaximumGenerationMetadataBytes, runtimeManifest))
        {
            ::close(generationFd);
            reason = "OMS_GENERATION_RUNTIME_MANIFEST_DIGEST_MISMATCH";
            Close();
            return false;
        }
        GenerationFields fields;
        const bool v2 = GenerationParseFields(
            runtimeManifest, kRuntimeManifestHeaderV2, fields);
        if (!v2)
        {
            fields.clear();
            if (!GenerationParseFields(runtimeManifest, kRuntimeManifestHeader, fields) ||
                fields["generation"] != generation ||
                !GenerationHexDigest(fields["segment_sha256"]) ||
                !GenerationVerifyHashedFileAt(generationFd, "segment-000001.jsonl",
                    fields["segment_sha256"]))
            {
                ::close(generationFd);
                reason = "OMS_GENERATION_COMPLETE_REPLAY_MANIFEST_INVALID";
                Close();
                return false;
            }
            reverseSegments.push_back({generation, fields["segment_sha256"]});
            ::close(generationFd);
            break;
        }
        if (fields["generation"] != generation ||
            !GenerationHexDigest(fields["segment_sha256"]) ||
            (fields["parent_generation"] != "-" &&
             !GenerationSafeName(fields["parent_generation"])) ||
            ((fields["parent_generation"] == "-") !=
             (fields["parent_manifest_sha256"] == "-")) ||
            (fields["parent_generation"] != "-" &&
             !GenerationHexDigest(fields["parent_manifest_sha256"])) ||
            !GenerationVerifyHashedFileAt(generationFd, "segment-000001.jsonl",
                fields["segment_sha256"]))
        {
            ::close(generationFd);
            reason = "OMS_GENERATION_COMPLETE_REPLAY_MANIFEST_INVALID";
            Close();
            return false;
        }
        reverseSegments.push_back({generation, fields["segment_sha256"]});
        const std::string parent = fields["parent_generation"];
        const std::string parentManifestDigest = fields["parent_manifest_sha256"];
        ::close(generationFd);
        if (parent == "-") break;

        const int parentFd = ::openat(m_storeFd, parent.c_str(),
            O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
        struct stat parentIdentity;
        std::string parentManifestJson;
        std::string parentRuntime;
        std::string expectedRuntimeDigest;
        const bool parentOk = parentFd >= 0 &&
            ::fstat(parentFd, &parentIdentity) == 0 &&
            GenerationPrivateDirectory(parentIdentity) &&
            GenerationVerifyHashedFileAt(parentFd, "manifest.json", parentManifestDigest) &&
            GenerationReadPrivateFileAt(parentFd, "manifest.json",
                kMaximumGenerationMetadataBytes, parentManifestJson) &&
            GenerationExtractCanonicalJsonDigest(parentManifestJson,
                "runtime_manifest_sha256", expectedRuntimeDigest) &&
            GenerationReadPrivateFileAt(parentFd, "runtime-manifest.txt",
                kMaximumGenerationMetadataBytes, parentRuntime) &&
            GenerationSha256(parentRuntime.data(), parentRuntime.size()) == expectedRuntimeDigest;
        if (parentFd >= 0) ::close(parentFd);
        if (!parentOk)
        {
            reason = "OMS_GENERATION_PARENT_BINDING_INVALID";
            Close();
            return false;
        }
        generation = parent;
    }

    for (std::vector<CompleteHistorySegment>::const_reverse_iterator item =
             reverseSegments.rbegin(); item != reverseSegments.rend(); ++item)
    {
        const int generationFd = ::openat(m_storeFd, item->generation.c_str(),
            O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
        int segmentFd = -1;
        struct stat segmentIdentity;
        if (generationFd < 0 ||
            !GenerationOpenHashedFileAt(generationFd, "segment-000001.jsonl",
                item->sha256, segmentFd, segmentIdentity) || segmentIdentity.st_size < 0 ||
            static_cast<std::uint64_t>(segmentIdentity.st_size) >
                static_cast<std::uint64_t>(std::numeric_limits<std::size_t>::max()))
        {
            if (segmentFd >= 0) ::close(segmentFd);
            if (generationFd >= 0) ::close(generationFd);
            reason = "OMS_GENERATION_COMPLETE_REPLAY_SEGMENT_INVALID";
            Close();
            return false;
        }
        std::size_t segmentRecords = 0;
        const std::size_t segmentBytes = static_cast<std::size_t>(segmentIdentity.st_size);
        const bool ok = GenerationReplayRange(segmentFd, 0, segmentIdentity.st_size,
            segmentBytes, std::numeric_limits<std::size_t>::max(), maxRecordBytes,
            onEvent, segmentRecords);
        const bool closed = ::close(segmentFd) == 0 && ::close(generationFd) == 0;
        if (!ok || !closed ||
            records > std::numeric_limits<std::uint64_t>::max() - segmentRecords)
        {
            reason = "OMS_GENERATION_COMPLETE_REPLAY_SEGMENT_FAILED";
            Close();
            return false;
        }
        records += static_cast<std::uint64_t>(segmentRecords);
    }

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
        reason = "OMS_GENERATION_ACTIVE_TAIL_UNSAFE";
        Close();
        return false;
    }
    std::string headerDigest;
    if (!GenerationHashFd(journalFd, static_cast<off_t>(m_journalPrefixBytes),
            headerDigest) || headerDigest != m_journalPrefixSha256)
    {
        ::close(journalFd);
        reason = "OMS_GENERATION_ACTIVE_TAIL_LINEAGE_MISMATCH";
        Close();
        return false;
    }
    const std::uint64_t tailBytes64 = static_cast<std::uint64_t>(before.st_size) -
        m_journalPrefixBytes;
    if (tailBytes64 > static_cast<std::uint64_t>(std::numeric_limits<std::size_t>::max()))
    {
        ::close(journalFd);
        reason = "OMS_GENERATION_COMPLETE_REPLAY_TAIL_LIMIT";
        Close();
        return false;
    }
    std::size_t tailRecords = 0;
    bool ok = GenerationReplayRange(journalFd,
        static_cast<off_t>(m_journalPrefixBytes), before.st_size,
        static_cast<std::size_t>(tailBytes64),
        std::numeric_limits<std::size_t>::max(), maxRecordBytes, onEvent, tailRecords);
    struct stat after, namedAfter;
    ok = ok && ::fstat(journalFd, &after) == 0 &&
        ::lstat(m_journalPath.c_str(), &namedAfter) == 0 &&
        GenerationSameIdentity(before, after) && GenerationSameIdentity(after, namedAfter);
    if (::close(journalFd) != 0) ok = false;
    if (!ok || records > std::numeric_limits<std::uint64_t>::max() - tailRecords)
    {
        reason = "OMS_GENERATION_COMPLETE_REPLAY_TAIL_FAILED";
        Close();
        return false;
    }
    records += static_cast<std::uint64_t>(tailRecords);
    m_active = true;
    reason.clear();
    return true;
}

bool OmsGenerationStore::RecoveryCapacity(
    std::uint64_t& bytes,
    std::uint64_t& records,
    std::string& reason) const
{
    // Both formats represent their replay start in m_journalPrefixBytes:
    // full immutable prefix for V1; lineage sentinel for V2. The retained V1
    // scanner therefore measures the correct bounded hot+tail working set for
    // either format after Recover has already verified the prefix/sentinel.
    return RecoveryCapacityGenerationV1(bytes, records, reason);
}
