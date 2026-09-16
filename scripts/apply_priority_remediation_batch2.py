#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, value: str) -> None:
    (ROOT / path).write_text(value, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    value = read(path)
    count = value.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one replacement, found {count}")
    write(path, value.replace(old, new, 1))


# ---------------------------------------------------------------------------
# OMS generation: stream permanent terminal-history bindings instead of
# materializing every sealed mutation into RAM.
# ---------------------------------------------------------------------------
replace_once(
    "HeptaTrade/oms_generation_store.h",
    '''struct OmsGenerationMutationRecord
{
    std::string agentId;
    std::string sessionId;
    std::string commandId;
    std::string operation;
    std::string venueCorrelationId;
};
''',
    '''struct OmsGenerationMutationRecord
{
    std::string agentId;
    std::string sessionId;
    std::string commandId;
    std::string operation;
    std::string venueCorrelationId;
};

// Fixed-size digest/count summary of sealed durable mutation history.  The
// command binding hashes canonical command rows in permanent-index order.  The
// correlation binding hashes the correlation reference attached to each sealed
// mutation in that same order; HPM2 binds this versioned projection without
// loading the historical command universe into memory.
struct OmsGenerationMutationSummary
{
    std::uint64_t commandCount = 0;
    std::uint64_t correlationReferenceCount = 0;
    std::string commandBindingSha256;
    std::string correlationBindingSha256;
};
''',
)
replace_once(
    "HeptaTrade/oms_generation_store.h",
    '''    bool EnumerateMutationRecords(
        const std::string& account,
        const std::string& executionDomain,
        std::vector<OmsGenerationMutationRecord>& records,
        std::string& reason) const;
''',
    '''    bool EnumerateMutationRecords(
        const std::string& account,
        const std::string& executionDomain,
        std::vector<OmsGenerationMutationRecord>& records,
        std::string& reason) const;

    bool SummarizeMutationRecords(
        const std::string& account,
        const std::string& executionDomain,
        OmsGenerationMutationSummary& summary,
        std::string& reason) const;
''',
)

# The old enumerator remains for backwards-compatible callers, but remove the
# artificial fixed failure threshold. New terminalization uses the streaming
# summary below.
replace_once(
    "HeptaTrade/execution/execution_generation_support.inc",
    '''const std::size_t kMaximumTerminalMutationRecords = 4097U;
const std::size_t kHistoricalCommandCache = 256U;
''',
    '''const std::size_t kHistoricalCommandCache = 256U;
''',
)
replace_once(
    "HeptaTrade/execution/execution_generation_support.inc",
    '''            if (records.size() >= kMaximumTerminalMutationRecords)
            {
                reason = "OMS_GENERATION_TERMINAL_MUTATION_UNIVERSE_TOO_LARGE";
                return false;
            }
            OmsGenerationMutationRecord record;
''',
    '''            OmsGenerationMutationRecord record;
''',
)

insert_marker = '''ExecutionCoordinator::RequestRecordStore::RequestRecordStore(
    OmsGenerationStore* generationStore)
'''
summary_impl = r'''bool OmsGenerationStore::SummarizeMutationRecords(
    const std::string& account,
    const std::string& executionDomain,
    OmsGenerationMutationSummary& summary,
    std::string& reason) const
{
    summary = OmsGenerationMutationSummary();
    if (!m_active)
    {
        summary.commandBindingSha256 =
            "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
        summary.correlationBindingSha256 = summary.commandBindingSha256;
        reason.clear();
        return true;
    }
    if (!ValidatePinnedIndex(m_commandIndexFd, "runtime-command-index.tsv",
            m_commandIndexIdentity))
    {
        reason = "OMS_GENERATION_COMMAND_INDEX_CHANGED";
        return false;
    }

    EVP_MD_CTX* commandDigest = EVP_MD_CTX_new();
    EVP_MD_CTX* correlationDigest = EVP_MD_CTX_new();
    if (commandDigest == nullptr || correlationDigest == nullptr ||
        EVP_DigestInit_ex(commandDigest, EVP_sha256(), nullptr) != 1 ||
        EVP_DigestInit_ex(correlationDigest, EVP_sha256(), nullptr) != 1)
    {
        if (commandDigest != nullptr) EVP_MD_CTX_free(commandDigest);
        if (correlationDigest != nullptr) EVP_MD_CTX_free(correlationDigest);
        reason = "OMS_GENERATION_TERMINAL_DIGEST_FAILED";
        return false;
    }

    bool ok = true;
    off_t offset = 0;
    while (ok && offset < m_commandIndexIdentity.st_size)
    {
        off_t start = 0, end = 0;
        std::string line;
        std::vector<std::string> fields;
        if (!GenerationReadLineContaining(m_commandIndexFd,
                m_commandIndexIdentity.st_size, offset, start, end, line) ||
            start != offset || end <= offset ||
            !GenerationSplitTabs(line, fields) || fields.size() != 13U)
        {
            reason = "OMS_GENERATION_COMMAND_INDEX_INVALID";
            ok = false;
            break;
        }
        std::string rowAccount, rowDomain;
        if (!GenerationDecodeHex(fields[10], rowAccount) ||
            !GenerationDecodeHex(fields[11], rowDomain) ||
            (fields[12] != "0" && fields[12] != "1") ||
            (fields[4] != "place" && fields[4] != "cancel" &&
             fields[4] != "flatten"))
        {
            reason = "OMS_GENERATION_COMMAND_INDEX_INVALID";
            ok = false;
            break;
        }
        if (fields[12] == "1" && rowAccount == account &&
            rowDomain == executionDomain)
        {
            const std::string commandLine = std::string("command=") +
                fields[0] + "|" + fields[1] + "|" + fields[2] + "|" +
                fields[4] + "|" + fields[8] + "\n";
            ok = EVP_DigestUpdate(commandDigest, commandLine.data(),
                    commandLine.size()) == 1;
            if (!ok)
            {
                reason = "OMS_GENERATION_TERMINAL_DIGEST_FAILED";
                break;
            }
            if (summary.commandCount ==
                std::numeric_limits<std::uint64_t>::max())
            {
                reason = "OMS_GENERATION_TERMINAL_COUNT_OVERFLOW";
                ok = false;
                break;
            }
            ++summary.commandCount;
            if (!fields[8].empty())
            {
                const std::string correlationLine =
                    std::string("correlation-ref=") + fields[8] + "\n";
                ok = EVP_DigestUpdate(correlationDigest,
                        correlationLine.data(), correlationLine.size()) == 1;
                if (!ok)
                {
                    reason = "OMS_GENERATION_TERMINAL_DIGEST_FAILED";
                    break;
                }
                if (summary.correlationReferenceCount ==
                    std::numeric_limits<std::uint64_t>::max())
                {
                    reason = "OMS_GENERATION_TERMINAL_COUNT_OVERFLOW";
                    ok = false;
                    break;
                }
                ++summary.correlationReferenceCount;
            }
        }
        offset = end;
    }

    auto finish = [](EVP_MD_CTX* context, std::string& output) {
        unsigned char digest[EVP_MAX_MD_SIZE];
        unsigned int length = 0;
        if (EVP_DigestFinal_ex(context, digest, &length) != 1 || length != 32)
            return false;
        static const char digits[] = "0123456789abcdef";
        output = "sha256:";
        output.reserve(71);
        for (unsigned int i = 0; i < length; ++i)
        {
            output.push_back(digits[digest[i] >> 4]);
            output.push_back(digits[digest[i] & 15U]);
        }
        return true;
    };
    if (ok)
        ok = finish(commandDigest, summary.commandBindingSha256) &&
            finish(correlationDigest, summary.correlationBindingSha256);
    EVP_MD_CTX_free(commandDigest);
    EVP_MD_CTX_free(correlationDigest);
    if (!ok)
    {
        if (reason.empty()) reason = "OMS_GENERATION_TERMINAL_DIGEST_FAILED";
        summary = OmsGenerationMutationSummary();
        return false;
    }
    reason.clear();
    return true;
}

'''
value = read("HeptaTrade/execution/execution_generation_support.inc")
if value.count(insert_marker) != 1:
    raise RuntimeError("generation request-store marker missing")
write("HeptaTrade/execution/execution_generation_support.inc",
      value.replace(insert_marker, summary_impl + insert_marker, 1))

# ---------------------------------------------------------------------------
# Terminal manifest: HPM2 stores fixed-size versioned digest/count bindings.
# HPM1 remains readable for already persisted terminal evidence.
# ---------------------------------------------------------------------------
replace_once(
    "HeptaTrade/execution/paper_terminal_mutation_manifest.h",
    '''struct PaperTerminalMutationUniverse
{
    std::vector<PaperTerminalMutationRecord> commands;
    std::vector<std::string> correlations;
    std::string commandSetSha256;
    std::string correlationSetSha256;
};
''',
    '''struct PaperTerminalMutationUniverse
{
    std::vector<PaperTerminalMutationRecord> commands;
    std::vector<std::string> correlations;
    std::string commandSetSha256;
    std::string correlationSetSha256;
    std::uint64_t commandCount = 0;
    std::uint64_t correlationCount = 0;
    bool compactSummary = false;
};
''',
)
replace_once(
    "HeptaTrade/execution/paper_terminal_mutation_manifest.h",
    '''bool BuildPaperTerminalMutationUniverse(
    const std::vector<PaperTerminalMutationRecord>& records,
    PaperTerminalMutationUniverse& universe, std::string& reason);
''',
    '''bool BuildPaperTerminalMutationUniverse(
    const std::vector<PaperTerminalMutationRecord>& records,
    PaperTerminalMutationUniverse& universe, std::string& reason);
// HPM2 combines a fixed-size sealed-history summary with the bounded active
// tail.  The resulting hashes bind both partitions without copying permanent
// history into the coordinator or terminal manifest.
bool BuildPaperTerminalPartitionedUniverse(
    std::uint64_t sealedCommandCount,
    const std::string& sealedCommandBindingSha256,
    std::uint64_t sealedCorrelationReferenceCount,
    const std::string& sealedCorrelationBindingSha256,
    const std::vector<PaperTerminalMutationRecord>& activeTail,
    PaperTerminalMutationUniverse& universe,
    std::string& reason);
''',
)

# Remove hard 4096 caps; HPM2 no longer serializes the full universe.
replace_once(
    "HeptaTrade/execution/paper_terminal_mutation_manifest.cpp",
    '''const std::size_t kMaximumCommands = 4096;
const std::size_t kMaximumCorrelations = 4096;
const std::size_t kMaximumManifestBytes = 1024 * 1024;
''',
    '''const std::size_t kMaximumManifestBytes = 1024 * 1024;
''',
)
replace_once(
    "HeptaTrade/execution/paper_terminal_mutation_manifest.cpp",
    '''    if (records.size() > kMaximumCommands)
    {
        reason = "IB_PAPER_TERMINAL_MUTATION_UNIVERSE_TOO_LARGE";
        return false;
    }
    universe.commands = records;
''',
    '''    universe.commands = records;
''',
)
replace_once(
    "HeptaTrade/execution/paper_terminal_mutation_manifest.cpp",
    '''    if (correlations.size() > kMaximumCorrelations)
    {
        reason = "IB_PAPER_TERMINAL_CORRELATION_UNIVERSE_TOO_LARGE";
        return false;
    }
    universe.correlations.assign(correlations.begin(), correlations.end());
''',
    '''    universe.correlations.assign(correlations.begin(), correlations.end());
''',
)
replace_once(
    "HeptaTrade/execution/paper_terminal_mutation_manifest.cpp",
    '''    universe.commandSetSha256 = Sha256(commandCanonical);
    universe.correlationSetSha256 = Sha256(correlationCanonical);
''',
    '''    universe.commandSetSha256 = Sha256(commandCanonical);
    universe.correlationSetSha256 = Sha256(correlationCanonical);
    universe.commandCount = static_cast<std::uint64_t>(universe.commands.size());
    universe.correlationCount = static_cast<std::uint64_t>(universe.correlations.size());
    universe.compactSummary = false;
''',
)

# Add HPM2 partition binding helper before manifest construction.
marker = '''bool BuildPaperTerminalMutationManifest(
    const PaperTerminalFenceBinding& binding,
'''
partition_impl = r'''bool BuildPaperTerminalPartitionedUniverse(
    std::uint64_t sealedCommandCount,
    const std::string& sealedCommandBindingSha256,
    std::uint64_t sealedCorrelationReferenceCount,
    const std::string& sealedCorrelationBindingSha256,
    const std::vector<PaperTerminalMutationRecord>& activeTail,
    PaperTerminalMutationUniverse& universe,
    std::string& reason)
{
    if (!Sha256Value(sealedCommandBindingSha256) ||
        !Sha256Value(sealedCorrelationBindingSha256))
    {
        reason = "IB_PAPER_TERMINAL_PARTITION_DIGEST_INVALID";
        return false;
    }
    PaperTerminalMutationUniverse tail;
    if (!BuildPaperTerminalMutationUniverse(activeTail, tail, reason))
        return false;
    if (sealedCommandCount > std::numeric_limits<std::uint64_t>::max() -
            tail.commandCount ||
        sealedCorrelationReferenceCount >
            std::numeric_limits<std::uint64_t>::max() - tail.correlationCount)
    {
        reason = "IB_PAPER_TERMINAL_PARTITION_COUNT_OVERFLOW";
        return false;
    }
    std::ostringstream commandBinding;
    commandBinding << "HPM2-COMMAND-PARTITIONS\n"
        << "sealed_count=" << sealedCommandCount << '\n'
        << "sealed_binding_sha256=" << sealedCommandBindingSha256 << '\n'
        << "active_tail_count=" << tail.commandCount << '\n'
        << "active_tail_binding_sha256=" << tail.commandSetSha256 << '\n';
    std::ostringstream correlationBinding;
    correlationBinding << "HPM2-CORRELATION-PARTITIONS\n"
        << "sealed_reference_count=" << sealedCorrelationReferenceCount << '\n'
        << "sealed_binding_sha256=" << sealedCorrelationBindingSha256 << '\n'
        << "active_tail_unique_count=" << tail.correlationCount << '\n'
        << "active_tail_binding_sha256=" << tail.correlationSetSha256 << '\n';
    universe = PaperTerminalMutationUniverse();
    universe.commandCount = sealedCommandCount + tail.commandCount;
    universe.correlationCount =
        sealedCorrelationReferenceCount + tail.correlationCount;
    universe.commandSetSha256 = Sha256(commandBinding.str());
    universe.correlationSetSha256 = Sha256(correlationBinding.str());
    universe.compactSummary = true;
    if (universe.commandSetSha256.empty() ||
        universe.correlationSetSha256.empty())
    {
        reason = "IB_PAPER_TERMINAL_PARTITION_HASH_FAILED";
        return false;
    }
    reason.clear();
    return true;
}

'''
value = read("HeptaTrade/execution/paper_terminal_mutation_manifest.cpp")
if value.count(marker) != 1:
    raise RuntimeError("terminal manifest build marker missing")
write("HeptaTrade/execution/paper_terminal_mutation_manifest.cpp",
      value.replace(marker, partition_impl + marker, 1))

# Add HPM2 parser before legacy HPM1 parser. HPM1 path remains byte-for-byte
# compatible apart from the populated count fields below.
parse_marker = '''bool ParseManifest(const std::string& contents,
    const PaperTerminalFenceBinding& expected,
'''
parse_v2 = r'''bool ParseManifestV2(const std::string& contents,
    const PaperTerminalFenceBinding& expected,
    PaperTerminalMutationManifest& manifest, std::string& reason)
{
    if (contents.size() > kMaximumManifestBytes ||
        contents.compare(0, 5, "HPM2\n") != 0)
    {
        reason = "IB_PAPER_TERMINAL_MANIFEST_INVALID";
        return false;
    }
    std::istringstream input(contents.substr(5));
    std::map<std::string, std::string> scalars;
    std::string line;
    while (std::getline(input, line))
    {
        if (line.empty()) continue;
        const std::size_t separator = line.find('=');
        if (separator == std::string::npos || separator == 0 ||
            separator + 1 >= line.size() ||
            !scalars.insert(std::make_pair(line.substr(0, separator),
                line.substr(separator + 1))).second)
        {
            reason = "IB_PAPER_TERMINAL_MANIFEST_INVALID";
            return false;
        }
    }
    static const char* const names[] = {
        "schema", "version", "finalization_id",
        "preliminary_finalization_receipt_sha256", "owner_agent_id_hex",
        "owner_session_id_hex", "owner_account_hex",
        "owner_execution_domain_hex", "recovery_ingress_fence",
        "terminalization_service_epoch_hex",
        "terminalization_service_fencing_generation", "service_process_id",
        "service_process_start_ticks", "broker_connection_epoch",
        "broker_socket_identity_sha256", "command_count",
        "correlation_count", "known_mutation_command_binding_sha256",
        "known_correlation_binding_sha256"
    };
    if (scalars.size() != sizeof(names) / sizeof(names[0]))
    {
        reason = "IB_PAPER_TERMINAL_MANIFEST_INVALID";
        return false;
    }
    for (std::size_t i = 0; i < sizeof(names) / sizeof(names[0]); ++i)
        if (scalars.find(names[i]) == scalars.end())
        {
            reason = "IB_PAPER_TERMINAL_MANIFEST_INVALID";
            return false;
        }
    PaperTerminalFenceBinding observed;
    observed.finalizationId = scalars["finalization_id"];
    observed.preliminaryReceiptSha256 =
        scalars["preliminary_finalization_receipt_sha256"];
    if (!DecodeHex(scalars["owner_agent_id_hex"], observed.owner.agentId) ||
        !DecodeHex(scalars["owner_session_id_hex"], observed.owner.sessionId) ||
        !DecodeHex(scalars["owner_account_hex"], observed.owner.account) ||
        !DecodeHex(scalars["owner_execution_domain_hex"],
            observed.owner.executionDomain) ||
        !DecodeHex(scalars["terminalization_service_epoch_hex"],
            observed.serviceEpoch) ||
        !ParseUnsigned(scalars["recovery_ingress_fence"],
            observed.recoveryIngressFence) ||
        !ParseUnsigned(scalars["terminalization_service_fencing_generation"],
            observed.serviceFencingGeneration) ||
        !ParseUnsigned(scalars["service_process_id"],
            observed.serviceProcessId) ||
        !ParseUnsigned(scalars["service_process_start_ticks"],
            observed.serviceProcessStartTicks) ||
        !ParseUnsigned(scalars["broker_connection_epoch"],
            observed.brokerConnectionEpoch))
    {
        reason = "IB_PAPER_TERMINAL_MANIFEST_INVALID";
        return false;
    }
    observed.brokerSocketIdentitySha256 =
        scalars["broker_socket_identity_sha256"];
    std::uint64_t commandCount = 0;
    std::uint64_t correlationCount = 0;
    if (scalars["schema"] !=
            "hepta.ib-paper-terminal-mutation-manifest.v2" ||
        scalars["version"] != "2" ||
        !SamePaperTerminalFenceBinding(observed, expected) ||
        !ParseUnsigned(scalars["command_count"], commandCount) ||
        !ParseUnsigned(scalars["correlation_count"], correlationCount) ||
        !Sha256Value(scalars["known_mutation_command_binding_sha256"]) ||
        !Sha256Value(scalars["known_correlation_binding_sha256"]))
    {
        reason = "IB_PAPER_TERMINAL_MANIFEST_INVALID";
        return false;
    }
    manifest = PaperTerminalMutationManifest();
    manifest.contents = contents;
    manifest.fileSha256 = Sha256(contents);
    manifest.bodySha256 = Sha256(contents.substr(5));
    manifest.universe.commandSetSha256 =
        scalars["known_mutation_command_binding_sha256"];
    manifest.universe.correlationSetSha256 =
        scalars["known_correlation_binding_sha256"];
    manifest.universe.commandCount = commandCount;
    manifest.universe.correlationCount = correlationCount;
    manifest.universe.compactSummary = true;
    reason.clear();
    return !manifest.fileSha256.empty() && !manifest.bodySha256.empty();
}

'''
value = read("HeptaTrade/execution/paper_terminal_mutation_manifest.cpp")
if value.count(parse_marker) != 1:
    raise RuntimeError("terminal manifest parser marker missing")
write("HeptaTrade/execution/paper_terminal_mutation_manifest.cpp",
      value.replace(parse_marker, parse_v2 + parse_marker, 1))

# Route HPM2 first, retain legacy HPM1 parser unchanged otherwise.
replace_once(
    "HeptaTrade/execution/paper_terminal_mutation_manifest.cpp",
    '''{
    if (contents.size() > kMaximumManifestBytes ||
        contents.compare(0, 5, "HPM1\\n") != 0)
''',
    '''{
    if (contents.compare(0, 5, "HPM2\\n") == 0)
        return ParseManifestV2(contents, expected, manifest, reason);
    if (contents.size() > kMaximumManifestBytes ||
        contents.compare(0, 5, "HPM1\\n") != 0)
''',
)

# Legacy HPM1 parsing now records explicit counts in the common structure.
replace_once(
    "HeptaTrade/execution/paper_terminal_mutation_manifest.cpp",
    '''    manifest.universe = rebuilt;
    reason.clear();
''',
    '''    rebuilt.commandCount = commandCount;
    rebuilt.correlationCount = correlationCount;
    rebuilt.compactSummary = false;
    manifest.universe = rebuilt;
    reason.clear();
''',
)

# Replace HPM1 writer with compact HPM2 writer. Normal in-memory universes are
# revalidated first; pre-compacted partition universes already carry validated
# versioned hashes/counts.
start = '''bool BuildPaperTerminalMutationManifest(
    const PaperTerminalFenceBinding& binding,
    const PaperTerminalMutationUniverse& universe,
    PaperTerminalMutationManifest& manifest, std::string& reason)
{
'''
end = '''bool CommitPaperTerminalMutationManifest(
'''
value = read("HeptaTrade/execution/paper_terminal_mutation_manifest.cpp")
si = value.find(start)
ei = value.find(end, si)
if si < 0 or ei < 0:
    raise RuntimeError("terminal manifest writer block missing")
new_writer = r'''bool BuildPaperTerminalMutationManifest(
    const PaperTerminalFenceBinding& binding,
    const PaperTerminalMutationUniverse& universe,
    PaperTerminalMutationManifest& manifest, std::string& reason)
{
    if (!ValidPaperTerminalFenceBinding(binding, reason)) return false;
    PaperTerminalMutationUniverse verified;
    if (universe.compactSummary)
    {
        if (!Sha256Value(universe.commandSetSha256) ||
            !Sha256Value(universe.correlationSetSha256))
        {
            reason = "IB_PAPER_TERMINAL_UNIVERSE_MISMATCH";
            return false;
        }
        verified = universe;
        verified.commands.clear();
        verified.correlations.clear();
    }
    else
    {
        if (!BuildPaperTerminalMutationUniverse(
                universe.commands, verified, reason) ||
            verified.commandSetSha256 != universe.commandSetSha256 ||
            verified.correlationSetSha256 != universe.correlationSetSha256 ||
            verified.correlations != universe.correlations)
        {
            if (reason.empty()) reason = "IB_PAPER_TERMINAL_UNIVERSE_MISMATCH";
            return false;
        }
        verified.commands.clear();
        verified.correlations.clear();
        verified.compactSummary = true;
    }
    std::ostringstream body;
    body << "schema=hepta.ib-paper-terminal-mutation-manifest.v2\n"
        << "version=2\n"
        << "finalization_id=" << binding.finalizationId << '\n'
        << "preliminary_finalization_receipt_sha256="
        << binding.preliminaryReceiptSha256 << '\n'
        << "owner_agent_id_hex=" << Hex(binding.owner.agentId) << '\n'
        << "owner_session_id_hex=" << Hex(binding.owner.sessionId) << '\n'
        << "owner_account_hex=" << Hex(binding.owner.account) << '\n'
        << "owner_execution_domain_hex=" << Hex(binding.owner.executionDomain) << '\n'
        << "recovery_ingress_fence=" << binding.recoveryIngressFence << '\n'
        << "terminalization_service_epoch_hex=" << Hex(binding.serviceEpoch) << '\n'
        << "terminalization_service_fencing_generation="
        << binding.serviceFencingGeneration << '\n'
        << "service_process_id=" << binding.serviceProcessId << '\n'
        << "service_process_start_ticks=" << binding.serviceProcessStartTicks << '\n'
        << "broker_connection_epoch=" << binding.brokerConnectionEpoch << '\n'
        << "broker_socket_identity_sha256=" << binding.brokerSocketIdentitySha256 << '\n'
        << "command_count=" << verified.commandCount << '\n'
        << "correlation_count=" << verified.correlationCount << '\n'
        << "known_mutation_command_binding_sha256="
        << verified.commandSetSha256 << '\n'
        << "known_correlation_binding_sha256="
        << verified.correlationSetSha256 << '\n';
    manifest = PaperTerminalMutationManifest();
    manifest.contents = std::string("HPM2\n") + body.str();
    if (manifest.contents.size() > kMaximumManifestBytes)
    {
        reason = "IB_PAPER_TERMINAL_MANIFEST_TOO_LARGE";
        return false;
    }
    manifest.fileSha256 = Sha256(manifest.contents);
    manifest.bodySha256 = Sha256(body.str());
    manifest.universe = verified;
    if (manifest.fileSha256.empty() || manifest.bodySha256.empty())
    {
        reason = "IB_PAPER_TERMINAL_MANIFEST_HASH_FAILED";
        return false;
    }
    reason.clear();
    return true;
}

'''
write("HeptaTrade/execution/paper_terminal_mutation_manifest.cpp",
      value[:si] + new_writer + value[ei:])

# Common latch/witness fields use explicit summary counts, not vector sizes.
for path in [
    "HeptaTrade/execution/paper_terminal_mutation_manifest.cpp",
    "HeptaTrade/execution/ib_paper_execution_runtime_terminal_state.cpp",
]:
    value = read(path)
    value = value.replace("manifest->universe.commands.size()",
                          "manifest->universe.commandCount")
    value = value.replace("manifest->universe.correlations.size()",
                          "manifest->universe.correlationCount")
    value = value.replace("manifest.universe.commands.size()",
                          "manifest.universe.commandCount")
    value = value.replace("manifest.universe.correlations.size()",
                          "manifest.universe.correlationCount")
    value = value.replace("m_terminalMutationManifest.universe.commands.size()",
                          "m_terminalMutationManifest.universe.commandCount")
    value = value.replace("m_terminalMutationManifest.universe.correlations.size()",
                          "m_terminalMutationManifest.universe.correlationCount")
    write(path, value)

# Generation-aware terminal projection: stream sealed history summary, then add
# only active-tail commands not already present in the permanent index.
old_terminal = '''    std::vector<PaperTerminalMutationRecord> records;
    std::set<std::tuple<std::string, std::string, std::string,
                        std::string, std::string>> seen;
    if (m_generationStore.IsActive())
    {
        std::vector<OmsGenerationMutationRecord> historical;
        if (!m_generationStore.EnumerateMutationRecords(
                binding.owner.account, binding.owner.executionDomain,
                historical, reason))
            return false;
        for (std::size_t i = 0; i < historical.size(); ++i)
        {
            const OmsGenerationMutationRecord& source = historical[i];
            const std::tuple<std::string, std::string, std::string,
                             std::string, std::string> key(
                source.agentId, source.sessionId, source.commandId,
                source.operation, source.venueCorrelationId);
            if (!seen.insert(key).second) continue;
            PaperTerminalMutationRecord record;
            record.agentId = source.agentId;
            record.sessionId = source.sessionId;
            record.toolCallId = source.commandId;
            record.operation = source.operation;
            record.venueCorrelationId = source.venueCorrelationId;
            records.push_back(record);
        }
    }
    for (RequestRecordStore::Base::const_iterator it = m_requests.begin();
         it != m_requests.end(); ++it)
    {
        const RequestRecord& request = it->second;
        if (!request.durableMutationIntent ||
            request.context.account != binding.owner.account ||
            request.context.executionDomain != binding.owner.executionDomain)
            continue;
        const std::tuple<std::string, std::string, std::string,
                         std::string, std::string> key(
            request.context.agentId, request.context.sessionId,
            request.context.toolCallId, request.operation,
            request.venueCorrelationId);
        if (!seen.insert(key).second) continue;
        PaperTerminalMutationRecord record;
        record.agentId = request.context.agentId;
        record.sessionId = request.context.sessionId;
        record.toolCallId = request.context.toolCallId;
        record.operation = request.operation;
        record.venueCorrelationId = request.venueCorrelationId;
        records.push_back(record);
    }
    return BuildPaperTerminalMutationUniverse(records, universe, reason);
'''
new_terminal = '''    OmsGenerationMutationSummary sealed;
    if (!m_generationStore.SummarizeMutationRecords(
            binding.owner.account, binding.owner.executionDomain,
            sealed, reason))
        return false;

    std::vector<PaperTerminalMutationRecord> activeTail;
    for (RequestRecordStore::Base::const_iterator it = m_requests.begin();
         it != m_requests.end(); ++it)
    {
        const RequestRecord& request = it->second;
        if (!request.durableMutationIntent ||
            request.context.account != binding.owner.account ||
            request.context.executionDomain != binding.owner.executionDomain)
            continue;
        OmsGenerationCommandRecord historical;
        std::string lookupReason;
        const OmsGenerationLookupStatus lookup = m_generationStore.LookupCommand(
            request.context.agentId, request.context.sessionId,
            request.context.toolCallId, historical, lookupReason);
        if (lookup == OmsGenerationLookupStatus::Error)
        {
            reason = lookupReason.empty() ?
                "OMS_GENERATION_COMMAND_INDEX_FAILED" : lookupReason;
            return false;
        }
        if (lookup == OmsGenerationLookupStatus::Found)
            continue; // already sealed into the fixed-size history binding
        PaperTerminalMutationRecord record;
        record.agentId = request.context.agentId;
        record.sessionId = request.context.sessionId;
        record.toolCallId = request.context.toolCallId;
        record.operation = request.operation;
        record.venueCorrelationId = request.venueCorrelationId;
        activeTail.push_back(record);
    }
    return BuildPaperTerminalPartitionedUniverse(
        sealed.commandCount, sealed.commandBindingSha256,
        sealed.correlationReferenceCount, sealed.correlationBindingSha256,
        activeTail, universe, reason);
'''
replace_once("HeptaTrade/execution/execution_generation_support.inc",
             old_terminal, new_terminal)

# ---------------------------------------------------------------------------
# Tests: prove >4096 commands produce a small HPM2 manifest and that partition
# binding can represent arbitrarily larger sealed history without a vector.
# ---------------------------------------------------------------------------
new_test = r'''#pragma once

#include <cassert>
#include <string>
#include <vector>

inline PaperTerminalFenceBinding MakeTerminalCapacityBinding()
{
    PaperTerminalFenceBinding binding;
    binding.owner.agentId = "capacity-agent";
    binding.owner.sessionId = "capacity-session";
    binding.owner.account = "DU-CAPACITY";
    binding.owner.executionDomain = "PAPER";
    binding.finalizationId = "capacity-finalization";
    binding.preliminaryReceiptSha256 = "sha256:" + std::string(64, 'a');
    binding.recoveryIngressFence = 7;
    binding.serviceEpoch = "service-epoch-capacity";
    binding.serviceFencingGeneration = 9;
    binding.serviceProcessId = 123;
    binding.serviceProcessStartTicks = 456;
    binding.brokerConnectionEpoch = 11;
    binding.brokerSocketIdentitySha256 = "sha256:" + std::string(64, 'b');
    return binding;
}

inline void TestTerminalManifestHasNo4096HistoryCeiling()
{
    std::vector<PaperTerminalMutationRecord> records;
    records.reserve(5000);
    for (int i = 0; i < 5000; ++i)
    {
        PaperTerminalMutationRecord record;
        record.agentId = "agent";
        record.sessionId = "session";
        record.toolCallId = "command-" + std::to_string(i);
        record.operation = "place";
        record.venueCorrelationId = "corr-" + std::to_string(i);
        records.push_back(record);
    }
    PaperTerminalMutationUniverse universe;
    std::string reason;
    assert(BuildPaperTerminalMutationUniverse(records, universe, reason));
    assert(universe.commandCount == 5000);
    assert(universe.correlationCount == 5000);
    PaperTerminalMutationManifest manifest;
    assert(BuildPaperTerminalMutationManifest(
        MakeTerminalCapacityBinding(), universe, manifest, reason));
    assert(manifest.contents.compare(0, 5, "HPM2\n") == 0);
    assert(manifest.universe.commandCount == 5000);
    assert(manifest.universe.correlationCount == 5000);
    assert(manifest.contents.size() < 4096);
    assert(manifest.contents.find("command=") == std::string::npos);
    assert(manifest.contents.find("correlation=") == std::string::npos);

    PaperTerminalMutationUniverse partitioned;
    const std::string empty =
        "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
    std::vector<PaperTerminalMutationRecord> tail;
    assert(BuildPaperTerminalPartitionedUniverse(
        1000000000ULL, empty, 900000000ULL, empty,
        tail, partitioned, reason));
    assert(partitioned.compactSummary);
    assert(partitioned.commandCount == 1000000000ULL);
    assert(partitioned.correlationCount == 900000000ULL);
    assert(partitioned.commands.empty());
    assert(partitioned.correlations.empty());
    assert(BuildPaperTerminalMutationManifest(
        MakeTerminalCapacityBinding(), partitioned, manifest, reason));
    assert(manifest.contents.size() < 4096);
}
'''
write("tests/terminal_manifest_capacity_cases.h", new_test)
replace_once(
    "tests/execution_coordinator_tests.cpp",
    '''#include "oms_recovery_growth_probe.h"
''',
    '''#include "oms_recovery_growth_probe.h"
#include "terminal_manifest_capacity_cases.h"
''',
)
replace_once(
    "tests/execution_coordinator_tests.cpp",
    '''    RunRecoveryGrowthProbe(16); // exercise the same producer in normal CTest

''',
    '''    RunRecoveryGrowthProbe(16); // exercise the same producer in normal CTest
    TestTerminalManifestHasNo4096HistoryCeiling();

''',
)

print("priority remediation batch 2 applied")
