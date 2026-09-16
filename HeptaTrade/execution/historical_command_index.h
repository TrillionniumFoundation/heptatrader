#pragma once

#include "execution_authority.h"

#include <cerrno>
#include <climits>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <openssl/evp.h>
#include <sstream>
#include <string>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>
#include <vector>

// Disk-backed permanent command identity. The SHA-256 pathname is only a
// lookup accelerator: every read compares the complete identity and request
// hash stored inside the authenticated record. A collision or corrupt record
// is fail-closed, never equivalent to "not found".
struct HistoricalCommandIndexRecord
{
    std::string agentId;
    std::string sessionId;
    std::string commandId;
    std::string requestHash;
    std::string operation;
    ExecutionCommandStatus status = ExecutionCommandStatus::Uncertain;
    long orderId = -1;
    std::string reasonCode;
    std::string detail;
};

enum class HistoricalCommandIndexLookup
{
    Found = 0,
    Missing,
    Corrupt,
    IoError,
    Collision
};

class HistoricalCommandIndex
{
public:
    explicit HistoricalCommandIndex(const std::string& root) : m_root(root) {}

    bool Init(std::string& reason)
    {
        reason.clear();
        if (m_root.empty()) { reason = "HISTORICAL_INDEX_ROOT_REQUIRED"; return false; }
        struct stat info {};
        if (::lstat(m_root.c_str(), &info) == 0)
        {
            if (!S_ISDIR(info.st_mode) || S_ISLNK(info.st_mode))
            { reason = "HISTORICAL_INDEX_ROOT_UNSAFE"; return false; }
            return true;
        }
        if (errno != ENOENT) { reason = "HISTORICAL_INDEX_ROOT_STAT_FAILED"; return false; }
        if (::mkdir(m_root.c_str(), 0700) != 0)
        { reason = "HISTORICAL_INDEX_ROOT_CREATE_FAILED"; return false; }
        return SyncDirectory(m_root, reason);
    }

    HistoricalCommandIndexLookup Lookup(const std::string& agentId,
                                        const std::string& sessionId,
                                        const std::string& commandId,
                                        HistoricalCommandIndexRecord& out,
                                        std::string& reason) const
    {
        reason.clear();
        out = HistoricalCommandIndexRecord();
        if (!ValidIdentity(agentId, sessionId, commandId))
        { reason = "HISTORICAL_INDEX_IDENTITY_INVALID"; return HistoricalCommandIndexLookup::Corrupt; }
        const std::string path = PathForIdentity(agentId, sessionId, commandId);
        const int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
        if (fd < 0)
        {
            if (errno == ENOENT) return HistoricalCommandIndexLookup::Missing;
            reason = "HISTORICAL_INDEX_OPEN_FAILED";
            return HistoricalCommandIndexLookup::IoError;
        }
        std::string raw;
        HistoricalCommandIndexLookup result = HistoricalCommandIndexLookup::Corrupt;
        do
        {
            struct stat info {};
            if (::fstat(fd, &info) != 0 || !S_ISREG(info.st_mode) || info.st_nlink != 1 ||
                info.st_size < 5 || info.st_size > static_cast<off_t>(kMaxRecordBytes))
            { reason = "HISTORICAL_INDEX_RECORD_UNSAFE"; break; }
            raw.resize(static_cast<std::size_t>(info.st_size));
            std::size_t offset = 0;
            while (offset < raw.size())
            {
                const ssize_t got = ::read(fd, &raw[offset], raw.size() - offset);
                if (got < 0 && errno == EINTR) continue;
                if (got <= 0) { reason = "HISTORICAL_INDEX_READ_FAILED"; break; }
                offset += static_cast<std::size_t>(got);
            }
            if (offset != raw.size()) break;
            HistoricalCommandIndexRecord decoded;
            if (!Decode(raw, decoded, reason)) break;
            if (decoded.agentId != agentId || decoded.sessionId != sessionId ||
                decoded.commandId != commandId)
            {
                reason = "HISTORICAL_INDEX_HASH_COLLISION";
                result = HistoricalCommandIndexLookup::Collision;
                break;
            }
            out = decoded;
            result = HistoricalCommandIndexLookup::Found;
        } while (false);
        ::close(fd);
        return result;
    }

    bool Put(const HistoricalCommandIndexRecord& record, std::string& reason)
    {
        reason.clear();
        if (!Valid(record, reason)) return false;
        HistoricalCommandIndexRecord existing;
        std::string lookupReason;
        const HistoricalCommandIndexLookup prior = Lookup(
            record.agentId, record.sessionId, record.commandId, existing, lookupReason);
        if (prior == HistoricalCommandIndexLookup::Found)
        {
            if (existing.requestHash != record.requestHash ||
                existing.operation != record.operation)
            { reason = "HISTORICAL_INDEX_IDEMPOTENCY_CONFLICT"; return false; }
        }
        else if (prior != HistoricalCommandIndexLookup::Missing)
        {
            reason = lookupReason.empty() ? "HISTORICAL_INDEX_EXISTING_RECORD_UNSAFE" : lookupReason;
            return false;
        }

        const std::string finalPath = PathForIdentity(record.agentId, record.sessionId, record.commandId);
        const std::string tempPath = finalPath + ".tmp." + std::to_string(static_cast<long long>(::getpid()));
        const std::string encoded = Encode(record);
        const int fd = ::open(tempPath.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
        if (fd < 0) { reason = "HISTORICAL_INDEX_TEMP_CREATE_FAILED"; return false; }
        bool ok = true;
        std::size_t offset = 0;
        while (offset < encoded.size())
        {
            const ssize_t wrote = ::write(fd, encoded.data() + offset, encoded.size() - offset);
            if (wrote < 0 && errno == EINTR) continue;
            if (wrote <= 0) { ok = false; reason = "HISTORICAL_INDEX_WRITE_FAILED"; break; }
            offset += static_cast<std::size_t>(wrote);
        }
        if (ok && ::fsync(fd) != 0) { ok = false; reason = "HISTORICAL_INDEX_FILE_SYNC_FAILED"; }
        if (::close(fd) != 0 && ok) { ok = false; reason = "HISTORICAL_INDEX_CLOSE_FAILED"; }
        if (!ok) { ::unlink(tempPath.c_str()); return false; }
        if (::rename(tempPath.c_str(), finalPath.c_str()) != 0)
        { ::unlink(tempPath.c_str()); reason = "HISTORICAL_INDEX_RENAME_FAILED"; return false; }
        return SyncDirectory(m_root, reason);
    }

    std::string PathForIdentity(const std::string& agentId,
                                const std::string& sessionId,
                                const std::string& commandId) const
    {
        const std::string key = CanonicalKey(agentId, sessionId, commandId);
        return m_root + "/" + Sha256Hex(key) + ".hci";
    }

private:
    static const std::size_t kMaxFieldBytes = 16U * 1024U;
    static const std::size_t kMaxRecordBytes = 128U * 1024U;

    static bool ValidIdentity(const std::string& agentId,
                              const std::string& sessionId,
                              const std::string& commandId)
    {
        return !agentId.empty() && !sessionId.empty() && !commandId.empty() &&
            agentId.size() <= kMaxFieldBytes && sessionId.size() <= kMaxFieldBytes &&
            commandId.size() <= kMaxFieldBytes;
    }

    static bool Valid(const HistoricalCommandIndexRecord& record, std::string& reason)
    {
        if (!ValidIdentity(record.agentId, record.sessionId, record.commandId) ||
            record.requestHash.empty() || record.requestHash.size() > kMaxFieldBytes ||
            record.operation.empty() || record.operation.size() > 32 ||
            record.reasonCode.size() > kMaxFieldBytes || record.detail.size() > kMaxFieldBytes)
        { reason = "HISTORICAL_INDEX_RECORD_INVALID"; return false; }
        const int status = static_cast<int>(record.status);
        if (status < static_cast<int>(ExecutionCommandStatus::Accepted) ||
            status > static_cast<int>(ExecutionCommandStatus::Uncertain))
        { reason = "HISTORICAL_INDEX_STATUS_INVALID"; return false; }
        return true;
    }

    static std::string CanonicalKey(const std::string& agentId,
                                    const std::string& sessionId,
                                    const std::string& commandId)
    {
        return std::to_string(agentId.size()) + ":" + agentId + "|" +
            std::to_string(sessionId.size()) + ":" + sessionId + "|" +
            std::to_string(commandId.size()) + ":" + commandId;
    }

    static std::string Sha256Hex(const std::string& value)
    {
        unsigned char digest[EVP_MAX_MD_SIZE];
        unsigned int length = 0;
        EVP_MD_CTX* context = EVP_MD_CTX_new();
        if (context == nullptr) return std::string(64, '0');
        const bool ok = EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1 &&
            EVP_DigestUpdate(context, value.data(), value.size()) == 1 &&
            EVP_DigestFinal_ex(context, digest, &length) == 1;
        EVP_MD_CTX_free(context);
        if (!ok || length != 32) return std::string(64, '0');
        static const char hex[] = "0123456789abcdef";
        std::string result;
        result.reserve(64);
        for (unsigned int i = 0; i < length; ++i)
        { result.push_back(hex[digest[i] >> 4]); result.push_back(hex[digest[i] & 15]); }
        return result;
    }

    static std::string Hex(const std::string& value)
    {
        static const char hex[] = "0123456789abcdef";
        std::string result;
        result.reserve(value.size() * 2);
        for (unsigned char c : value)
        { result.push_back(hex[c >> 4]); result.push_back(hex[c & 15]); }
        return result;
    }

    static bool Unhex(const std::string& value, std::string& out)
    {
        if (value.size() % 2 != 0 || value.size() > kMaxFieldBytes * 2) return false;
        out.clear(); out.reserve(value.size() / 2);
        for (std::size_t i = 0; i < value.size(); i += 2)
        {
            const int high = HexDigit(value[i]); const int low = HexDigit(value[i + 1]);
            if (high < 0 || low < 0) return false;
            out.push_back(static_cast<char>((high << 4) | low));
        }
        return true;
    }

    static int HexDigit(char value)
    {
        if (value >= '0' && value <= '9') return value - '0';
        if (value >= 'a' && value <= 'f') return 10 + value - 'a';
        if (value >= 'A' && value <= 'F') return 10 + value - 'A';
        return -1;
    }

    static std::vector<std::string> Split(const std::string& value, char separator)
    {
        std::vector<std::string> parts;
        std::size_t start = 0;
        for (;;)
        {
            const std::size_t end = value.find(separator, start);
            parts.push_back(value.substr(start, end == std::string::npos ? std::string::npos : end - start));
            if (end == std::string::npos) break;
            start = end + 1;
        }
        return parts;
    }

    static std::string Payload(const HistoricalCommandIndexRecord& record)
    {
        std::ostringstream out;
        out << Hex(record.agentId) << '\t' << Hex(record.sessionId) << '\t'
            << Hex(record.commandId) << '\t' << Hex(record.requestHash) << '\t'
            << Hex(record.operation) << '\t' << static_cast<int>(record.status) << '\t'
            << record.orderId << '\t' << Hex(record.reasonCode) << '\t' << Hex(record.detail);
        return out.str();
    }

    static std::string Encode(const HistoricalCommandIndexRecord& record)
    {
        const std::string payload = Payload(record);
        return std::string("HCI1\n") + payload + "\t" + Sha256Hex(payload) + "\n";
    }

    static bool Decode(const std::string& raw,
                       HistoricalCommandIndexRecord& record,
                       std::string& reason)
    {
        if (raw.compare(0, 5, "HCI1\n") != 0 || raw.empty() || raw.back() != '\n')
        { reason = "HISTORICAL_INDEX_FORMAT_INVALID"; return false; }
        const std::string body = raw.substr(5, raw.size() - 6);
        const std::vector<std::string> fields = Split(body, '\t');
        if (fields.size() != 10)
        { reason = "HISTORICAL_INDEX_FIELD_COUNT_INVALID"; return false; }
        std::string agent, session, command, requestHash, operation, reasonCode, detail;
        if (!Unhex(fields[0], agent) || !Unhex(fields[1], session) || !Unhex(fields[2], command) ||
            !Unhex(fields[3], requestHash) || !Unhex(fields[4], operation) ||
            !Unhex(fields[7], reasonCode) || !Unhex(fields[8], detail))
        { reason = "HISTORICAL_INDEX_HEX_INVALID"; return false; }
        const std::string payload = body.substr(0, body.rfind('\t'));
        if (fields[9].size() != 64 || Sha256Hex(payload) != fields[9])
        { reason = "HISTORICAL_INDEX_CHECKSUM_INVALID"; return false; }
        char* end = nullptr;
        errno = 0;
        const long statusLong = std::strtol(fields[5].c_str(), &end, 10);
        if (errno != 0 || end == nullptr || *end != '\0' || statusLong < 0 || statusLong > 3)
        { reason = "HISTORICAL_INDEX_STATUS_INVALID"; return false; }
        errno = 0; end = nullptr;
        const long orderId = std::strtol(fields[6].c_str(), &end, 10);
        if (errno != 0 || end == nullptr || *end != '\0')
        { reason = "HISTORICAL_INDEX_ORDER_ID_INVALID"; return false; }
        record.agentId = agent; record.sessionId = session; record.commandId = command;
        record.requestHash = requestHash; record.operation = operation;
        record.status = static_cast<ExecutionCommandStatus>(statusLong); record.orderId = orderId;
        record.reasonCode = reasonCode; record.detail = detail;
        if (!Valid(record, reason)) return false;
        return true;
    }

    static bool SyncDirectory(const std::string& path, std::string& reason)
    {
        const int fd = ::open(path.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
        if (fd < 0) { reason = "HISTORICAL_INDEX_DIRECTORY_OPEN_FAILED"; return false; }
        const bool ok = ::fsync(fd) == 0;
        ::close(fd);
        if (!ok) reason = "HISTORICAL_INDEX_DIRECTORY_SYNC_FAILED";
        return ok;
    }

    std::string m_root;
};
