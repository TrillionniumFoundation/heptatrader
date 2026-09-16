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
    std::string journalPath;
    std::string commandIndexPath;
    std::string sendAttemptIndexPath;
    std::string hotReplayPath;
    std::string journalSha256;
    std::string journalPrefixSha256;
    std::uint64_t journalBytes = 0;
    std::uint64_t journalPrefixBytes = 0;
    std::uint64_t cutSequence = 0;
    std::uint64_t tailStartSequence = 0;
    std::uint64_t lastSequence = 0;
};

// Native reader for hepta_oms_checkpoint.py's HOG1 runtime sidecars. This
// class is intentionally read-only. The Python stopped-state tool owns atomic
// generation publication; runtime admission may consume only a completely
// verified CURRENT/CURRENT.runtime pair.
class OmsRuntimeGenerationReader
{
public:
    explicit OmsRuntimeGenerationReader(const std::string& root) : m_root(root) {}

    bool Open(OmsRuntimeGenerationView& out, std::string& reason) const
    {
        reason.clear();
        out = OmsRuntimeGenerationView();
        if (!SafeDirectory(m_root, reason)) return false;

        std::string currentRaw;
        if (!ReadPrivateRegular(m_root + "/CURRENT", currentRaw, reason)) return false;
        std::string runtimeRaw;
        if (!ReadPrivateRegular(m_root + "/CURRENT.runtime", runtimeRaw, reason)) return false;

        std::string generation;
        if (!ParseCurrent(currentRaw, "HEPTA_OMS_CURRENT_V1", generation, reason)) return false;
        std::string runtimeGeneration;
        if (!ParseCurrent(runtimeRaw, "HEPTA_OMS_RUNTIME_CURRENT_V1", runtimeGeneration, reason)) return false;
        if (generation != runtimeGeneration)
        { reason = "OMS_RUNTIME_GENERATION_POINTER_MISMATCH"; return false; }
        if (!SafeLeaf(generation))
        { reason = "OMS_RUNTIME_GENERATION_NAME_INVALID"; return false; }

        const std::string directory = m_root + "/generations/" + generation;
        if (!SafeDirectory(directory, reason)) return false;
        std::string manifest;
        if (!ReadPrivateRegular(directory + "/runtime-manifest.txt", manifest, reason)) return false;
        std::vector<std::pair<std::string, std::string>> fields;
        if (!ParseManifest(manifest, fields, reason)) return false;

        const auto required = [&](const char* key, std::string& value) -> bool {
            for (const auto& item : fields)
                if (item.first == key) { value = item.second; return true; }
            reason = std::string("OMS_RUNTIME_MANIFEST_MISSING_") + key;
            return false;
        };
        std::string manifestGeneration, journalLeaf, commandLeaf, sendLeaf, hotLeaf;
        std::string journalBytes, prefixBytes, cutSequence, tailStart, lastSequence;
        std::string journalSha, prefixSha, commandSha, sendSha, hotSha;
        if (!required("generation", manifestGeneration) ||
            !required("journal", journalLeaf) ||
            !required("journal_sha256", journalSha) ||
            !required("journal_bytes", journalBytes) ||
            !required("journal_prefix_sha256", prefixSha) ||
            !required("journal_prefix_bytes", prefixBytes) ||
            !required("cut_sequence", cutSequence) ||
            !required("tail_start_sequence", tailStart) ||
            !required("last_sequence", lastSequence) ||
            !required("command_index", commandLeaf) ||
            !required("command_index_sha256", commandSha) ||
            !required("send_attempt_index", sendLeaf) ||
            !required("send_attempt_index_sha256", sendSha) ||
            !required("hot_replay", hotLeaf) ||
            !required("hot_replay_sha256", hotSha)) return false;
        if (manifestGeneration != generation || !SafeLeaf(journalLeaf) ||
            !SafeLeaf(commandLeaf) || !SafeLeaf(sendLeaf) || !SafeLeaf(hotLeaf) ||
            !ValidSha(journalSha) || !ValidSha(prefixSha) || !ValidSha(commandSha) ||
            !ValidSha(sendSha) || !ValidSha(hotSha))
        { reason = "OMS_RUNTIME_MANIFEST_INVALID"; return false; }

        std::uint64_t parsedJournalBytes = 0, parsedPrefixBytes = 0;
        std::uint64_t parsedCut = 0, parsedTailStart = 0, parsedLast = 0;
        if (!ParseUint(journalBytes, parsedJournalBytes) ||
            !ParseUint(prefixBytes, parsedPrefixBytes) ||
            !ParseUint(cutSequence, parsedCut) ||
            !ParseUint(tailStart, parsedTailStart) ||
            !ParseUint(lastSequence, parsedLast) ||
            parsedPrefixBytes > parsedJournalBytes || parsedCut > parsedLast ||
            parsedTailStart != parsedCut + 1)
        { reason = "OMS_RUNTIME_MANIFEST_RANGE_INVALID"; return false; }

        const std::string journal = directory + "/" + journalLeaf;
        const std::string command = directory + "/" + commandLeaf;
        const std::string send = directory + "/" + sendLeaf;
        const std::string hot = directory + "/" + hotLeaf;
        std::uint64_t actualJournalBytes = 0;
        std::string actualJournalSha;
        if (!HashPrivateRegular(journal, actualJournalBytes, actualJournalSha, reason) ||
            actualJournalBytes != parsedJournalBytes || actualJournalSha != journalSha)
        { if (reason.empty()) reason = "OMS_RUNTIME_JOURNAL_DIGEST_MISMATCH"; return false; }
        if (!VerifyDigest(command, commandSha, reason) ||
            !VerifyDigest(send, sendSha, reason) ||
            !VerifyDigest(hot, hotSha, reason)) return false;
        std::string actualPrefixSha;
        if (!HashPrefix(journal, parsedPrefixBytes, actualPrefixSha, reason) ||
            actualPrefixSha != prefixSha)
        { if (reason.empty()) reason = "OMS_RUNTIME_PREFIX_DIGEST_MISMATCH"; return false; }

        out.generation = generation;
        out.generationDirectory = directory;
        out.journalPath = journal;
        out.commandIndexPath = command;
        out.sendAttemptIndexPath = send;
        out.hotReplayPath = hot;
        out.journalSha256 = journalSha;
        out.journalPrefixSha256 = prefixSha;
        out.journalBytes = parsedJournalBytes;
        out.journalPrefixBytes = parsedPrefixBytes;
        out.cutSequence = parsedCut;
        out.tailStartSequence = parsedTailStart;
        out.lastSequence = parsedLast;
        return true;
    }

    OmsRuntimeHistoricalLookup Lookup(const OmsRuntimeGenerationView& view,
                                      const std::string& agentId,
                                      const std::string& sessionId,
                                      const std::string& commandId,
                                      OmsRuntimeHistoricalCommand& out,
                                      std::string& reason) const
    {
        reason.clear();
        out = OmsRuntimeHistoricalCommand();
        if (agentId.empty() || sessionId.empty() || commandId.empty())
        { reason = "OMS_RUNTIME_COMMAND_IDENTITY_INVALID"; return OmsRuntimeHistoricalLookup::Corrupt; }
        std::string content;
        if (!ReadPrivateRegular(view.commandIndexPath, content, reason))
            return OmsRuntimeHistoricalLookup::IoError;
        std::istringstream input(content);
        std::string line;
        const std::string expectedA = Hex(agentId), expectedS = Hex(sessionId), expectedC = Hex(commandId);
        while (std::getline(input, line))
        {
            if (line.empty()) continue;
            if (line.size() > 64U * 1024U)
            { reason = "OMS_RUNTIME_COMMAND_INDEX_LINE_LIMIT"; return OmsRuntimeHistoricalLookup::Corrupt; }
            const std::vector<std::string> fields = Split(line, '\t');
            if (fields.size() != 13)
            { reason = "OMS_RUNTIME_COMMAND_INDEX_RECORD_INVALID"; return OmsRuntimeHistoricalLookup::Corrupt; }
            if (fields[0] != expectedA || fields[1] != expectedS || fields[2] != expectedC) continue;
            if (!DecodeRuntimeRecord(fields, out, reason))
                return OmsRuntimeHistoricalLookup::Corrupt;
            if (out.agentId != agentId || out.sessionId != sessionId || out.commandId != commandId)
            { reason = "OMS_RUNTIME_COMMAND_INDEX_FULL_KEY_MISMATCH"; return OmsRuntimeHistoricalLookup::Corrupt; }
            return OmsRuntimeHistoricalLookup::Found;
        }
        return OmsRuntimeHistoricalLookup::Missing;
    }

private:
    static bool SafeLeaf(const std::string& value)
    {
        return !value.empty() && value != "." && value != ".." &&
            value.find('/') == std::string::npos && value.find('\\') == std::string::npos &&
            value.find('\0') == std::string::npos;
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
        out.assign(static_cast<std::size_t>(before.st_size), '\0');
        std::size_t offset = 0;
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

    static bool ParseCurrent(const std::string& raw, const char* header,
                             std::string& generation, std::string& reason)
    {
        std::istringstream in(raw);
        std::string first, second, extra;
        if (!std::getline(in, first) || first != header || !std::getline(in, second) ||
            second.compare(0, 11, "generation=") != 0 || std::getline(in, extra))
        { reason = "OMS_RUNTIME_CURRENT_INVALID"; return false; }
        generation = second.substr(11);
        return SafeLeaf(generation);
    }

    static bool ParseManifest(const std::string& raw,
                              std::vector<std::pair<std::string, std::string>>& fields,
                              std::string& reason)
    {
        std::istringstream in(raw);
        std::string line;
        if (!std::getline(in, line) || line != "HEPTA_OMS_RUNTIME_GENERATION_V1")
        { reason = "OMS_RUNTIME_MANIFEST_HEADER_INVALID"; return false; }
        while (std::getline(in, line))
        {
            if (line.empty()) continue;
            const std::size_t split = line.find('=');
            if (split == std::string::npos || split == 0)
            { reason = "OMS_RUNTIME_MANIFEST_RECORD_INVALID"; return false; }
            const std::string key = line.substr(0, split), value = line.substr(split + 1);
            for (const auto& existing : fields)
                if (existing.first == key)
                { reason = "OMS_RUNTIME_MANIFEST_DUPLICATE_FIELD"; return false; }
            fields.push_back(std::make_pair(key, value));
        }
        return true;
    }

    static bool ParseUint(const std::string& text, std::uint64_t& value)
    {
        if (text.empty()) return false;
        std::uint64_t result = 0;
        for (char c : text)
        {
            if (c < '0' || c > '9') return false;
            const std::uint64_t digit = static_cast<std::uint64_t>(c - '0');
            if (result > (UINT64_MAX - digit) / 10) return false;
            result = result * 10 + digit;
        }
        value = result;
        return true;
    }

    static bool ValidSha(const std::string& value)
    {
        if (value.size() != 64) return false;
        for (char c : value)
            if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
        return true;
    }

    static std::string DigestHex(EVP_MD_CTX* context)
    {
        unsigned char digest[EVP_MAX_MD_SIZE]; unsigned int size = 0;
        if (EVP_DigestFinal_ex(context, digest, &size) != 1 || size != 32) return std::string();
        static const char hex[] = "0123456789abcdef";
        std::string out; out.reserve(64);
        for (unsigned int i = 0; i < size; ++i)
        { out.push_back(hex[digest[i] >> 4]); out.push_back(hex[digest[i] & 15]); }
        return out;
    }

    static bool HashPrivateRegular(const std::string& path, std::uint64_t& bytes,
                                   std::string& digest, std::string& reason)
    {
        const int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
        if (fd < 0) { reason = "OMS_RUNTIME_GENERATION_OPEN_FAILED"; return false; }
        struct stat info {};
        if (::fstat(fd, &info) != 0 || !S_ISREG(info.st_mode) || info.st_nlink != 1 ||
            info.st_uid != ::geteuid() || (info.st_mode & 0777) != 0600)
        { ::close(fd); reason = "OMS_RUNTIME_GENERATION_UNSAFE_FILE"; return false; }
        EVP_MD_CTX* ctx = EVP_MD_CTX_new();
        if (!ctx || EVP_DigestInit_ex(ctx, EVP_sha256(), nullptr) != 1)
        { if (ctx) EVP_MD_CTX_free(ctx); ::close(fd); reason = "OMS_RUNTIME_HASH_INIT_FAILED"; return false; }
        bytes = 0; char buffer[64 * 1024];
        for (;;)
        {
            const ssize_t got = ::read(fd, buffer, sizeof(buffer));
            if (got < 0 && errno == EINTR) continue;
            if (got < 0) { EVP_MD_CTX_free(ctx); ::close(fd); reason = "OMS_RUNTIME_GENERATION_READ_FAILED"; return false; }
            if (got == 0) break;
            bytes += static_cast<std::uint64_t>(got);
            if (EVP_DigestUpdate(ctx, buffer, static_cast<std::size_t>(got)) != 1)
            { EVP_MD_CTX_free(ctx); ::close(fd); reason = "OMS_RUNTIME_HASH_UPDATE_FAILED"; return false; }
        }
        digest = DigestHex(ctx); EVP_MD_CTX_free(ctx); ::close(fd);
        if (digest.empty()) { reason = "OMS_RUNTIME_HASH_FINAL_FAILED"; return false; }
        return true;
    }

    static bool HashPrefix(const std::string& path, std::uint64_t limit,
                           std::string& digest, std::string& reason)
    {
        const int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
        if (fd < 0) { reason = "OMS_RUNTIME_GENERATION_OPEN_FAILED"; return false; }
        EVP_MD_CTX* ctx = EVP_MD_CTX_new();
        if (!ctx || EVP_DigestInit_ex(ctx, EVP_sha256(), nullptr) != 1)
        { if (ctx) EVP_MD_CTX_free(ctx); ::close(fd); reason = "OMS_RUNTIME_HASH_INIT_FAILED"; return false; }
        std::uint64_t left = limit; char buffer[64 * 1024];
        while (left)
        {
            const std::size_t want = left < sizeof(buffer) ? static_cast<std::size_t>(left) : sizeof(buffer);
            const ssize_t got = ::read(fd, buffer, want);
            if (got < 0 && errno == EINTR) continue;
            if (got <= 0) { EVP_MD_CTX_free(ctx); ::close(fd); reason = "OMS_RUNTIME_PREFIX_SHORT_READ"; return false; }
            left -= static_cast<std::uint64_t>(got);
            if (EVP_DigestUpdate(ctx, buffer, static_cast<std::size_t>(got)) != 1)
            { EVP_MD_CTX_free(ctx); ::close(fd); reason = "OMS_RUNTIME_HASH_UPDATE_FAILED"; return false; }
        }
        digest = DigestHex(ctx); EVP_MD_CTX_free(ctx); ::close(fd);
        if (digest.empty()) { reason = "OMS_RUNTIME_HASH_FINAL_FAILED"; return false; }
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
        for (;;)
        {
            const std::size_t end = value.find(separator, start);
            out.push_back(value.substr(start, end == std::string::npos ? std::string::npos : end - start));
            if (end == std::string::npos) break; start = end + 1;
        }
        return out;
    }

    static int HexDigit(char value)
    {
        if (value >= '0' && value <= '9') return value - '0';
        if (value >= 'a' && value <= 'f') return 10 + value - 'a';
        if (value >= 'A' && value <= 'F') return 10 + value - 'A';
        return -1;
    }

    static bool Unhex(const std::string& value, std::string& out)
    {
        if (value.size() % 2) return false;
        out.clear(); out.reserve(value.size() / 2);
        for (std::size_t i = 0; i < value.size(); i += 2)
        {
            const int hi = HexDigit(value[i]), lo = HexDigit(value[i + 1]);
            if (hi < 0 || lo < 0) return false;
            out.push_back(static_cast<char>((hi << 4) | lo));
        }
        return true;
    }

    static std::string Hex(const std::string& value)
    {
        static const char digits[] = "0123456789abcdef";
        std::string out; out.reserve(value.size() * 2);
        for (unsigned char c : value)
        { out.push_back(digits[c >> 4]); out.push_back(digits[c & 15]); }
        return out;
    }

    static bool DecodeStatus(const std::string& value, ExecutionCommandStatus& out)
    {
        if (value == "accepted") out = ExecutionCommandStatus::Accepted;
        else if (value == "rejected") out = ExecutionCommandStatus::Rejected;
        else if (value == "uncertain") out = ExecutionCommandStatus::Uncertain;
        else return false;
        return true;
    }

    static bool DecodeRuntimeRecord(const std::vector<std::string>& f,
                                    OmsRuntimeHistoricalCommand& out,
                                    std::string& reason)
    {
        std::string orderText = f[6], seqText = f[9];
        char* end = nullptr; errno = 0;
        const long order = std::strtol(orderText.c_str(), &end, 10);
        if (errno || !end || *end) { reason = "OMS_RUNTIME_COMMAND_ORDER_INVALID"; return false; }
        std::uint64_t sequence = 0;
        if (!ParseUint(seqText, sequence) || (f[12] != "0" && f[12] != "1") ||
            !Unhex(f[0], out.agentId) || !Unhex(f[1], out.sessionId) ||
            !Unhex(f[2], out.commandId) || !Unhex(f[3], out.requestHash) ||
            !Unhex(f[7], out.reasonCode) || !Unhex(f[8], out.venueCorrelationId) ||
            !Unhex(f[10], out.account) || !Unhex(f[11], out.executionDomain) ||
            !DecodeStatus(f[5], out.status) ||
            (f[4] != "place" && f[4] != "cancel" && f[4] != "flatten"))
        { reason = "OMS_RUNTIME_COMMAND_INDEX_RECORD_INVALID"; return false; }
        out.operation = f[4]; out.orderId = order; out.lastSequence = sequence;
        out.durableMutationIntent = f[12] == "1";
        return true;
    }

    std::string m_root;
};
