#pragma once

#include "execution_authority.h"

#include <cerrno>
#include <cstdint>
#include <cstdlib>
#include <fcntl.h>
#include <openssl/evp.h>
#include <sstream>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <utility>
#include <vector>

struct OmsRuntimeHistoricalCommand
{
    std::string agentId;
    std::string sessionId;
    std::string commandId;
    std::string requestHash;
    std::string operation;
    ExecutionCommandStatus status = ExecutionCommandStatus::Uncertain;
    long orderId = -1;
    std::string reasonCode;
    std::string venueCorrelationId;
    std::uint64_t lastSequence = 0;
    std::string account;
    std::string executionDomain;
    bool durableMutationIntent = false;
};

enum class OmsRuntimeHistoricalLookup
{
    Found = 0,
    Missing,
    Corrupt,
    IoError
};

struct OmsRuntimeGenerationView
{
    std::string generation;
    std::string generationDirectory;
    std::string segmentPath;
    std::string checkpointPath;
    std::string commandIndexPath;
    std::string sendAttemptIndexPath;
    std::string hotReplayPath;
    std::string journalPrefixSha256;
    std::uint64_t journalPrefixBytes = 0;
    std::uint64_t journalRecords = 0;
    std::uint64_t commandRecords = 0;
    std::uint64_t sendAttemptRecords = 0;
    std::uint64_t hotReplayRecords = 0;
};

// Read-only native consumer of hepta_oms_checkpoint.py's HOG1 runtime sidecars.
// The producer owns stopped-state publication. The runtime accepts only a
// complete CURRENT -> CURRENT.runtime -> manifest.json/runtime-manifest.txt
// digest chain and never falls back to an older generation on corruption.
class OmsRuntimeGenerationReader
{
public:
    explicit OmsRuntimeGenerationReader(const std::string& root) : m_root(root) {}

    bool Open(OmsRuntimeGenerationView& out, std::string& reason) const
    {
        reason.clear();
        out = OmsRuntimeGenerationView();
        if (!SafeDirectory(m_root, reason)) return false;

        std::string currentRaw, runtimeCurrentRaw;
        if (!ReadPrivateRegular(m_root + "/CURRENT", currentRaw, reason) ||
            !ReadPrivateRegular(m_root + "/CURRENT.runtime", runtimeCurrentRaw, reason)) return false;

        std::string generation, manifestSha, runtimeManifestSha;
        if (!ParseCanonicalCurrent(currentRaw, generation, manifestSha, runtimeManifestSha, reason)) return false;
        std::vector<std::pair<std::string, std::string>> runtimeCurrent;
        if (!ParseLineManifest(runtimeCurrentRaw, "HEPTA_OMS_RUNTIME_CURRENT_V1", runtimeCurrent, reason)) return false;
        std::string rcGeneration, currentSha, rcManifestSha, rcRuntimeManifestSha;
        if (!UniqueField(runtimeCurrent, "generation", rcGeneration) ||
            !UniqueField(runtimeCurrent, "current_sha256", currentSha) ||
            !UniqueField(runtimeCurrent, "manifest_sha256", rcManifestSha) ||
            !UniqueField(runtimeCurrent, "runtime_manifest_sha256", rcRuntimeManifestSha) ||
            runtimeCurrent.size() != 4)
        { reason = "OMS_RUNTIME_CURRENT_INVALID"; return false; }
        if (rcGeneration != generation || rcManifestSha != manifestSha ||
            rcRuntimeManifestSha != runtimeManifestSha || Sha256(currentRaw) != currentSha)
        { reason = "OMS_RUNTIME_GENERATION_POINTER_MISMATCH"; return false; }
        if (!SafeLeaf(generation) || !ValidSha(manifestSha) || !ValidSha(runtimeManifestSha))
        { reason = "OMS_RUNTIME_CURRENT_INVALID"; return false; }

        const std::string directory = m_root + "/" + generation;
        if (!SafeDirectory(directory, reason)) return false;
        if (!VerifyDigest(directory + "/manifest.json", manifestSha, reason)) return false;
        if (!VerifyDigest(directory + "/runtime-manifest.txt", runtimeManifestSha, reason)) return false;

        std::string runtimeManifestRaw;
        if (!ReadPrivateRegular(directory + "/runtime-manifest.txt", runtimeManifestRaw, reason)) return false;
        std::vector<std::pair<std::string, std::string>> fields;
        if (!ParseLineManifest(runtimeManifestRaw, "HEPTA_OMS_RUNTIME_GENERATION_V1", fields, reason)) return false;

        std::string manifestGeneration, prefixBytes, prefixSha, journalRecords;
        std::string commandRecords, sendRecords, hotRecords, segmentSha, checkpointSha;
        std::string commandIndexSha, runtimeCommandIndexSha, sendIndexSha, hotReplaySha;
        std::string authorization, paper, live;
        if (!Field(fields, "generation", manifestGeneration, reason) ||
            !Field(fields, "journal_prefix_bytes", prefixBytes, reason) ||
            !Field(fields, "journal_prefix_sha256", prefixSha, reason) ||
            !Field(fields, "journal_records", journalRecords, reason) ||
            !Field(fields, "command_records", commandRecords, reason) ||
            !Field(fields, "send_attempt_records", sendRecords, reason) ||
            !Field(fields, "hot_replay_records", hotRecords, reason) ||
            !Field(fields, "segment_sha256", segmentSha, reason) ||
            !Field(fields, "checkpoint_sha256", checkpointSha, reason) ||
            !Field(fields, "command_index_sha256", commandIndexSha, reason) ||
            !Field(fields, "runtime_command_index_sha256", runtimeCommandIndexSha, reason) ||
            !Field(fields, "send_attempt_index_sha256", sendIndexSha, reason) ||
            !Field(fields, "hot_replay_sha256", hotReplaySha, reason) ||
            !Field(fields, "authorization_effect", authorization, reason) ||
            !Field(fields, "paper_authorized", paper, reason) ||
            !Field(fields, "live_authorized", live, reason)) return false;
        if (manifestGeneration != generation || authorization != "NONE" || paper != "0" || live != "0" ||
            !ValidSha(prefixSha) || !ValidSha(segmentSha) || !ValidSha(checkpointSha) ||
            !ValidSha(commandIndexSha) || !ValidSha(runtimeCommandIndexSha) ||
            !ValidSha(sendIndexSha) || !ValidSha(hotReplaySha))
        { reason = "OMS_RUNTIME_MANIFEST_INVALID"; return false; }

        std::uint64_t parsedPrefixBytes = 0, parsedJournalRecords = 0, parsedCommandRecords = 0;
        std::uint64_t parsedSendRecords = 0, parsedHotRecords = 0;
        if (!ParseUint(prefixBytes, parsedPrefixBytes) || !ParseUint(journalRecords, parsedJournalRecords) ||
            !ParseUint(commandRecords, parsedCommandRecords) || !ParseUint(sendRecords, parsedSendRecords) ||
            !ParseUint(hotRecords, parsedHotRecords))
        { reason = "OMS_RUNTIME_MANIFEST_RANGE_INVALID"; return false; }

        const std::string segment = directory + "/segment-000001.jsonl";
        const std::string checkpoint = directory + "/checkpoint.json";
        const std::string legacyIndex = directory + "/command-index.tsv";
        const std::string runtimeIndex = directory + "/runtime-command-index.tsv";
        const std::string sendIndex = directory + "/send-attempt-index.tsv";
        const std::string hotReplay = directory + "/hot-replay.jsonl";
        std::uint64_t segmentBytes = 0; std::string actualSegmentSha;
        if (!HashPrivateRegular(segment, segmentBytes, actualSegmentSha, reason) ||
            segmentBytes != parsedPrefixBytes || actualSegmentSha != segmentSha || actualSegmentSha != prefixSha)
        { if (reason.empty()) reason = "OMS_RUNTIME_PREFIX_DIGEST_MISMATCH"; return false; }
        if (!VerifyDigest(checkpoint, checkpointSha, reason) ||
            !VerifyDigest(legacyIndex, commandIndexSha, reason) ||
            !VerifyDigest(runtimeIndex, runtimeCommandIndexSha, reason) ||
            !VerifyDigest(sendIndex, sendIndexSha, reason) ||
            !VerifyDigest(hotReplay, hotReplaySha, reason)) return false;

        out.generation = generation;
        out.generationDirectory = directory;
        out.segmentPath = segment;
        out.checkpointPath = checkpoint;
        out.commandIndexPath = runtimeIndex;
        out.sendAttemptIndexPath = sendIndex;
        out.hotReplayPath = hotReplay;
        out.journalPrefixSha256 = prefixSha;
        out.journalPrefixBytes = parsedPrefixBytes;
        out.journalRecords = parsedJournalRecords;
        out.commandRecords = parsedCommandRecords;
        out.sendAttemptRecords = parsedSendRecords;
        out.hotReplayRecords = parsedHotRecords;
        return true;
    }

    OmsRuntimeHistoricalLookup Lookup(const OmsRuntimeGenerationView& view,
                                      const std::string& agentId,
                                      const std::string& sessionId,
                                      const std::string& commandId,
                                      OmsRuntimeHistoricalCommand& out,
                                      std::string& reason) const
    {
        reason.clear(); out = OmsRuntimeHistoricalCommand();
        if (agentId.empty() || sessionId.empty() || commandId.empty())
        { reason = "OMS_RUNTIME_COMMAND_IDENTITY_INVALID"; return OmsRuntimeHistoricalLookup::Corrupt; }
        std::string content;
        if (!ReadPrivateRegular(view.commandIndexPath, content, reason)) return OmsRuntimeHistoricalLookup::IoError;
        std::istringstream input(content); std::string line;
        const std::string expectedA = Hex(agentId), expectedS = Hex(sessionId), expectedC = Hex(commandId);
        while (std::getline(input, line))
        {
            if (line.empty()) continue;
            if (line.size() > 64U * 1024U)
            { reason = "OMS_RUNTIME_COMMAND_INDEX_LINE_LIMIT"; return OmsRuntimeHistoricalLookup::Corrupt; }
            const std::vector<std::string> f = Split(line, '\t');
            if (f.size() != 13)
            { reason = "OMS_RUNTIME_COMMAND_INDEX_RECORD_INVALID"; return OmsRuntimeHistoricalLookup::Corrupt; }
            if (f[0] != expectedA || f[1] != expectedS || f[2] != expectedC) continue;
            if (!DecodeRuntimeRecord(f, out, reason) || out.agentId != agentId ||
                out.sessionId != sessionId || out.commandId != commandId)
            { if (reason.empty()) reason = "OMS_RUNTIME_COMMAND_INDEX_FULL_KEY_MISMATCH";
              return OmsRuntimeHistoricalLookup::Corrupt; }
            return OmsRuntimeHistoricalLookup::Found;
        }
        return OmsRuntimeHistoricalLookup::Missing;
    }

private:
    static bool SafeLeaf(const std::string& value)
    {
        return !value.empty() && value != "." && value != ".." && value.find('/') == std::string::npos &&
            value.find('\\') == std::string::npos && value.find('\0') == std::string::npos;
    }

    static bool SafeDirectory(const std::string& path, std::string& reason)
    {
        struct stat info {};
        if (::lstat(path.c_str(), &info) != 0 || !S_ISDIR(info.st_mode) || S_ISLNK(info.st_mode) ||
            info.st_uid != ::geteuid() || (info.st_mode & 0777) != 0700)
        { reason = "OMS_RUNTIME_GENERATION_UNSAFE_DIRECTORY"; return false; }
        return true;
    }

    static bool ReadPrivateRegular(const std::string& path, std::string& out, std::string& reason)
    {
        const int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
        if (fd < 0) { reason = "OMS_RUNTIME_GENERATION_OPEN_FAILED"; return false; }
        struct stat before {};
        if (::fstat(fd, &before) != 0 || !S_ISREG(before.st_mode) || before.st_nlink != 1 ||
            before.st_uid != ::geteuid() || (before.st_mode & 0777) != 0600 ||
            before.st_size < 0 || before.st_size > 64 * 1024 * 1024)
        { ::close(fd); reason = "OMS_RUNTIME_GENERATION_UNSAFE_FILE"; return false; }
        out.assign(static_cast<std::size_t>(before.st_size), '\0'); std::size_t offset = 0;
        while (offset < out.size())
        {
            const ssize_t got = ::read(fd, &out[offset], out.size() - offset);
            if (got < 0 && errno == EINTR) continue;
            if (got <= 0) { ::close(fd); reason = "OMS_RUNTIME_GENERATION_READ_FAILED"; return false; }
            offset += static_cast<std::size_t>(got);
        }
        struct stat after {}, named {};
        const bool stable = ::fstat(fd, &after) == 0 && ::lstat(path.c_str(), &named) == 0 &&
            before.st_dev == after.st_dev && before.st_ino == after.st_ino && before.st_size == after.st_size &&
            before.st_mtime == after.st_mtime && after.st_dev == named.st_dev && after.st_ino == named.st_ino;
        ::close(fd);
        if (!stable) { reason = "OMS_RUNTIME_GENERATION_FILE_CHANGED"; return false; }
        return true;
    }

    static bool ParseCanonicalCurrent(const std::string& raw, std::string& generation,
                                      std::string& manifestSha, std::string& runtimeManifestSha,
                                      std::string& reason)
    {
        const std::string a = "{\"generation\":\"";
        const std::string b = "\",\"manifest_sha256\":\"";
        const std::string c = "\",\"runtime_manifest_sha256\":\"";
        const std::string d = "\",\"schema\":\"heptatrader.oms-current.v1\"}\n";
        if (raw.compare(0, a.size(), a) != 0) { reason = "OMS_RUNTIME_CURRENT_INVALID"; return false; }
        const std::size_t pb = raw.find(b, a.size());
        const std::size_t pc = pb == std::string::npos ? pb : raw.find(c, pb + b.size());
        const std::size_t pd = pc == std::string::npos ? pc : raw.find(d, pc + c.size());
        if (pb == std::string::npos || pc == std::string::npos || pd == std::string::npos || pd + d.size() != raw.size())
        { reason = "OMS_RUNTIME_CURRENT_INVALID"; return false; }
        generation = raw.substr(a.size(), pb - a.size());
        manifestSha = raw.substr(pb + b.size(), pc - (pb + b.size()));
        runtimeManifestSha = raw.substr(pc + c.size(), pd - (pc + c.size()));
        if (!SafeLeaf(generation) || !ValidSha(manifestSha) || !ValidSha(runtimeManifestSha))
        { reason = "OMS_RUNTIME_CURRENT_INVALID"; return false; }
        return true;
    }

    static bool ParseLineManifest(const std::string& raw, const char* header,
                                  std::vector<std::pair<std::string, std::string>>& fields,
                                  std::string& reason)
    {
        std::istringstream in(raw); std::string line;
        if (!std::getline(in, line) || line != header)
        { reason = "OMS_RUNTIME_MANIFEST_HEADER_INVALID"; return false; }
        while (std::getline(in, line))
        {
            if (line.empty()) continue;
            const std::size_t split = line.find('=');
            if (split == std::string::npos || split == 0)
            { reason = "OMS_RUNTIME_MANIFEST_RECORD_INVALID"; return false; }
            const std::string key = line.substr(0, split), value = line.substr(split + 1);
            for (const auto& prior : fields)
                if (prior.first == key) { reason = "OMS_RUNTIME_MANIFEST_DUPLICATE_FIELD"; return false; }
            fields.push_back(std::make_pair(key, value));
        }
        return true;
    }

    static bool UniqueField(const std::vector<std::pair<std::string, std::string>>& fields,
                            const char* key, std::string& value)
    {
        bool found = false;
        for (const auto& item : fields) if (item.first == key)
        { if (found) return false; found = true; value = item.second; }
        return found;
    }

    static bool Field(const std::vector<std::pair<std::string, std::string>>& fields,
                      const char* key, std::string& value, std::string& reason)
    {
        if (UniqueField(fields, key, value)) return true;
        reason = std::string("OMS_RUNTIME_MANIFEST_MISSING_") + key; return false;
    }

    static bool ParseUint(const std::string& text, std::uint64_t& value)
    {
        if (text.empty()) return false; std::uint64_t result = 0;
        for (char c : text)
        {
            if (c < '0' || c > '9') return false; const std::uint64_t digit = static_cast<std::uint64_t>(c - '0');
            if (result > (UINT64_MAX - digit) / 10) return false; result = result * 10 + digit;
        }
        value = result; return true;
    }

    static bool ValidSha(const std::string& value)
    {
        if (value.size() != 64) return false;
        for (char c : value) if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
        return true;
    }

    static std::string Sha256(const std::string& value)
    {
        EVP_MD_CTX* ctx = EVP_MD_CTX_new(); if (!ctx) return std::string();
        unsigned char digest[EVP_MAX_MD_SIZE]; unsigned int size = 0;
        const bool ok = EVP_DigestInit_ex(ctx, EVP_sha256(), nullptr) == 1 &&
            EVP_DigestUpdate(ctx, value.data(), value.size()) == 1 &&
            EVP_DigestFinal_ex(ctx, digest, &size) == 1;
        EVP_MD_CTX_free(ctx); if (!ok || size != 32) return std::string();
        static const char hex[] = "0123456789abcdef"; std::string out; out.reserve(64);
        for (unsigned int i = 0; i < size; ++i) { out.push_back(hex[digest[i] >> 4]); out.push_back(hex[digest[i] & 15]); }
        return out;
    }

    static bool HashPrivateRegular(const std::string& path, std::uint64_t& bytes,
                                   std::string& digest, std::string& reason)
    {
        std::string raw; if (!ReadPrivateRegular(path, raw, reason)) return false;
        bytes = static_cast<std::uint64_t>(raw.size()); digest = Sha256(raw);
        if (digest.empty()) { reason = "OMS_RUNTIME_HASH_FAILED"; return false; }
        return true;
    }

    static bool VerifyDigest(const std::string& path, const std::string& expected, std::string& reason)
    {
        std::uint64_t bytes = 0; std::string digest;
        if (!HashPrivateRegular(path, bytes, digest, reason)) return false;
        if (digest != expected) { reason = "OMS_RUNTIME_SIDECAR_DIGEST_MISMATCH"; return false; }
        return true;
    }

    static std::vector<std::string> Split(const std::string& value, char separator)
    {
        std::vector<std::string> out; std::size_t start = 0;
        for (;;) { const std::size_t end = value.find(separator, start);
            out.push_back(value.substr(start, end == std::string::npos ? std::string::npos : end - start));
            if (end == std::string::npos) break; start = end + 1; }
        return out;
    }

    static int HexDigit(char value)
    {
        if (value >= '0' && value <= '9') return value - '0';
        if (value >= 'a' && value <= 'f') return 10 + value - 'a';
        if (value >= 'A' && value <= 'F') return 10 + value - 'A'; return -1;
    }

    static bool Unhex(const std::string& value, std::string& out)
    {
        if (value.size() % 2) return false; out.clear(); out.reserve(value.size() / 2);
        for (std::size_t i = 0; i < value.size(); i += 2)
        { const int hi = HexDigit(value[i]), lo = HexDigit(value[i + 1]); if (hi < 0 || lo < 0) return false;
          out.push_back(static_cast<char>((hi << 4) | lo)); }
        return true;
    }

    static std::string Hex(const std::string& value)
    {
        static const char digits[] = "0123456789abcdef"; std::string out; out.reserve(value.size() * 2);
        for (unsigned char c : value) { out.push_back(digits[c >> 4]); out.push_back(digits[c & 15]); } return out;
    }

    static bool DecodeStatus(const std::string& value, ExecutionCommandStatus& out)
    {
        if (value == "accepted") out = ExecutionCommandStatus::Accepted;
        else if (value == "rejected") out = ExecutionCommandStatus::Rejected;
        else if (value == "uncertain") out = ExecutionCommandStatus::Uncertain;
        else return false; return true;
    }

    static bool DecodeRuntimeRecord(const std::vector<std::string>& f, OmsRuntimeHistoricalCommand& out,
                                    std::string& reason)
    {
        char* end = nullptr; errno = 0; const long order = std::strtol(f[6].c_str(), &end, 10);
        if (errno || !end || *end) { reason = "OMS_RUNTIME_COMMAND_ORDER_INVALID"; return false; }
        std::uint64_t sequence = 0;
        if (!ParseUint(f[9], sequence) || (f[12] != "0" && f[12] != "1") ||
            !Unhex(f[0], out.agentId) || !Unhex(f[1], out.sessionId) || !Unhex(f[2], out.commandId) ||
            !Unhex(f[3], out.requestHash) || !Unhex(f[7], out.reasonCode) ||
            !Unhex(f[8], out.venueCorrelationId) || !Unhex(f[10], out.account) ||
            !Unhex(f[11], out.executionDomain) || !DecodeStatus(f[5], out.status) ||
            (f[4] != "place" && f[4] != "cancel" && f[4] != "flatten"))
        { reason = "OMS_RUNTIME_COMMAND_INDEX_RECORD_INVALID"; return false; }
        out.operation = f[4]; out.orderId = order; out.lastSequence = sequence;
        out.durableMutationIntent = f[12] == "1"; return true;
    }

    std::string m_root;
};
