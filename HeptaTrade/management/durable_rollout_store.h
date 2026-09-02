#pragma once

#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iterator>
#include <limits>
#include <map>
#include <mutex>
#include <sstream>
#include <string>
#include <system_error>
#include <utility>
#include <vector>

#if !defined(_WIN32)
#include <fcntl.h>
#include <unistd.h>
#endif

struct DurableRolloutRecord
{
    std::string moduleId;
    std::string version;
    std::string artifactDigest;
    std::string configDigest;
    std::string modelDigest;
    std::string desiredState;
    std::uint64_t generation = 0;
    std::uint64_t updatedAtMs = 0;
};

struct DurableRolloutResult
{
    bool accepted = false;
    bool duplicate = false;
    std::string reasonCode;
    DurableRolloutRecord record;
};

struct ObservedRolloutState
{
    std::string moduleId;
    std::string artifactDigest;
    std::string configDigest;
    std::string state;
};

struct RolloutReconciliationAction
{
    std::string moduleId;
    std::string action;
    std::string reasonCode;
    std::uint64_t desiredGeneration = 0;
};

// Single-writer, local restart-persistent desired-state store. It provides
// checksummed parsing and atomic replacement. It is intentionally not a
// distributed consensus service or a multi-host rollout authority.
class DurableRolloutStore
{
public:
    explicit DurableRolloutStore(std::filesystem::path path,
                                 std::size_t maximumRecords = 1024,
                                 std::size_t maximumFileBytes = 4 * 1024 * 1024)
        : m_path(std::move(path)),
          m_maximumRecords(maximumRecords),
          m_maximumFileBytes(maximumFileBytes)
    {
    }

    static const char* Version() noexcept
    {
        return "hepta.durable-rollout-store.v1";
    }

    DurableRolloutResult Load()
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        std::error_code error;
        if (!std::filesystem::exists(m_path, error))
        {
            if (error) return Reject("ROLLOUT_STORE_STAT_FAILED", nullptr);
            m_records.clear();
            return Accept("ROLLOUT_STORE_EMPTY", nullptr);
        }
        const std::uintmax_t size = std::filesystem::file_size(m_path, error);
        if (error || size == 0 || size > m_maximumFileBytes)
            return Reject("ROLLOUT_STORE_SIZE_INVALID", nullptr);

        std::ifstream input(m_path, std::ios::binary);
        if (!input) return Reject("ROLLOUT_STORE_OPEN_FAILED", nullptr);
        std::string document((std::istreambuf_iterator<char>(input)),
                             std::istreambuf_iterator<char>());
        if (!input.good() && !input.eof())
            return Reject("ROLLOUT_STORE_READ_FAILED", nullptr);

        std::map<std::string, DurableRolloutRecord> parsed;
        std::string reason;
        if (!ParseDocument(document, parsed, reason))
            return Reject(reason.c_str(), nullptr);
        m_records.swap(parsed);
        return Accept("ROLLOUT_STORE_LOADED", nullptr);
    }

    DurableRolloutResult Put(const DurableRolloutRecord& record)
    {
        if (!ValidRecord(record))
            return Reject("ROLLOUT_RECORD_INVALID", nullptr);

        std::lock_guard<std::mutex> lock(m_mutex);
        const auto found = m_records.find(record.moduleId);
        if (found == m_records.end())
        {
            if (record.generation != 1)
                return Reject("ROLLOUT_GENERATION_INITIAL_INVALID", nullptr);
            if (m_records.size() >= m_maximumRecords)
                return Reject("ROLLOUT_STORE_CAPACITY_EXHAUSTED", nullptr);
        }
        else
        {
            if (SameRecord(found->second, record))
            {
                DurableRolloutResult result =
                    Accept("ROLLOUT_RECORD_DUPLICATE", &found->second);
                result.duplicate = true;
                return result;
            }
            if (found->second.generation ==
                std::numeric_limits<std::uint64_t>::max())
                return Reject("ROLLOUT_GENERATION_EXHAUSTED", &found->second);
            if (record.generation <= found->second.generation)
                return Reject("ROLLOUT_GENERATION_STALE", &found->second);
            if (record.generation != found->second.generation + 1)
                return Reject("ROLLOUT_GENERATION_GAP", &found->second);
            if (record.updatedAtMs < found->second.updatedAtMs)
                return Reject("ROLLOUT_TIME_REGRESSION", &found->second);
        }

        std::map<std::string, DurableRolloutRecord> proposed = m_records;
        proposed[record.moduleId] = record;
        const std::string document = SerializeDocument(proposed);
        if (document.size() > m_maximumFileBytes)
            return Reject("ROLLOUT_STORE_SIZE_LIMIT", nullptr);

        const std::string writeReason = WriteAtomic(document);
        if (!writeReason.empty())
            return Reject(writeReason.c_str(), nullptr);
        m_records.swap(proposed);
        return Accept("ROLLOUT_RECORD_COMMITTED", &record);
    }

    bool Get(const std::string& moduleId, DurableRolloutRecord& out) const
    {
        out = DurableRolloutRecord();
        std::lock_guard<std::mutex> lock(m_mutex);
        const auto found = m_records.find(moduleId);
        if (found == m_records.end()) return false;
        out = found->second;
        return true;
    }

    std::vector<DurableRolloutRecord> List() const
    {
        std::vector<DurableRolloutRecord> result;
        std::lock_guard<std::mutex> lock(m_mutex);
        result.reserve(m_records.size());
        for (const auto& entry : m_records) result.push_back(entry.second);
        return result;
    }

    std::vector<RolloutReconciliationAction> Reconcile(
        const std::vector<ObservedRolloutState>& observed) const
    {
        std::map<std::string, ObservedRolloutState> byModule;
        for (const ObservedRolloutState& state : observed)
        {
            if (CanonicalId(state.moduleId, 128) &&
                byModule.find(state.moduleId) == byModule.end())
                byModule.emplace(state.moduleId, state);
        }

        std::vector<RolloutReconciliationAction> actions;
        std::lock_guard<std::mutex> lock(m_mutex);
        actions.reserve(m_records.size());
        for (const auto& entry : m_records)
        {
            const DurableRolloutRecord& desired = entry.second;
            RolloutReconciliationAction action;
            action.moduleId = desired.moduleId;
            action.desiredGeneration = desired.generation;
            const auto current = byModule.find(desired.moduleId);
            if (current == byModule.end())
            {
                action.action = "apply";
                action.reasonCode = "ROLLOUT_OBSERVED_MISSING";
            }
            else if (current->second.artifactDigest != desired.artifactDigest ||
                     current->second.configDigest != desired.configDigest)
            {
                action.action = "replace";
                action.reasonCode = "ROLLOUT_IDENTITY_DRIFT";
            }
            else if (current->second.state != desired.desiredState)
            {
                action.action = "transition";
                action.reasonCode = "ROLLOUT_STATE_DRIFT";
            }
            else
            {
                action.action = "noop";
                action.reasonCode = "ROLLOUT_CONVERGED";
            }
            actions.push_back(action);
        }
        return actions;
    }

private:
    static bool CanonicalId(const std::string& value, std::size_t maximum)
    {
        if (value.empty() || value.size() > maximum) return false;
        for (unsigned char c : value)
        {
            const bool alnum = (c >= 'A' && c <= 'Z') ||
                (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9');
            if (!(alnum || c == '-' || c == '_' || c == '.' || c == ':'))
                return false;
        }
        return true;
    }

    static bool CanonicalDigest(const std::string& value)
    {
        if (value.size() != 71 || value.compare(0, 7, "sha256:") != 0)
            return false;
        for (std::size_t i = 7; i < value.size(); ++i)
        {
            const char c = value[i];
            if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')))
                return false;
        }
        return true;
    }

    static bool ValidRecord(const DurableRolloutRecord& record)
    {
        return CanonicalId(record.moduleId, 128) &&
            record.moduleId.compare(0, 6, "hepta.") == 0 &&
            CanonicalId(record.version, 64) &&
            CanonicalDigest(record.artifactDigest) &&
            CanonicalDigest(record.configDigest) &&
            (record.modelDigest.empty() ||
             CanonicalDigest(record.modelDigest)) &&
            CanonicalId(record.desiredState, 32) &&
            record.generation != 0 && record.updatedAtMs != 0;
    }

    static bool SameRecord(const DurableRolloutRecord& left,
                           const DurableRolloutRecord& right)
    {
        return left.moduleId == right.moduleId &&
            left.version == right.version &&
            left.artifactDigest == right.artifactDigest &&
            left.configDigest == right.configDigest &&
            left.modelDigest == right.modelDigest &&
            left.desiredState == right.desiredState &&
            left.generation == right.generation &&
            left.updatedAtMs == right.updatedAtMs;
    }

    static void AppendString(std::string& output, const std::string& value)
    {
        output.append(std::to_string(value.size()));
        output.push_back(':');
        output.append(value);
    }

    static bool ParseString(const std::string& token, std::string& value)
    {
        const std::size_t separator = token.find(':');
        if (separator == std::string::npos || separator == 0)
            return false;
        std::uint64_t length = 0;
        for (std::size_t i = 0; i < separator; ++i)
        {
            const char c = token[i];
            if (c < '0' || c > '9') return false;
            const std::uint64_t digit = static_cast<std::uint64_t>(c - '0');
            if (length > (std::numeric_limits<std::uint64_t>::max() - digit) / 10)
                return false;
            length = length * 10 + digit;
        }
        if (length != token.size() - separator - 1) return false;
        value.assign(token, separator + 1, std::string::npos);
        return true;
    }

    static bool ParseUnsigned(const std::string& value, std::uint64_t& out)
    {
        if (value.empty()) return false;
        std::uint64_t parsed = 0;
        for (char c : value)
        {
            if (c < '0' || c > '9') return false;
            const std::uint64_t digit = static_cast<std::uint64_t>(c - '0');
            if (parsed > (std::numeric_limits<std::uint64_t>::max() - digit) / 10)
                return false;
            parsed = parsed * 10 + digit;
        }
        out = parsed;
        return true;
    }

    static std::uint64_t Checksum(const std::string& value)
    {
        std::uint64_t hash = 1469598103934665603ULL;
        for (unsigned char c : value)
        {
            hash ^= static_cast<std::uint64_t>(c);
            hash *= 1099511628211ULL;
        }
        return hash;
    }

    static std::string ChecksumText(std::uint64_t value)
    {
        std::ostringstream output;
        output << std::hex << std::setfill('0') << std::setw(16) << value;
        return output.str();
    }

    static std::vector<std::string> Split(const std::string& line)
    {
        std::vector<std::string> fields;
        std::size_t start = 0;
        while (true)
        {
            const std::size_t separator = line.find('|', start);
            if (separator == std::string::npos)
            {
                fields.push_back(line.substr(start));
                break;
            }
            fields.push_back(line.substr(start, separator - start));
            start = separator + 1;
        }
        return fields;
    }

    static std::string SerializeDocument(
        const std::map<std::string, DurableRolloutRecord>& records)
    {
        std::string payload = "HEPTA_ROLLOUT_STORE_V1\n";
        for (const auto& entry : records)
        {
            const DurableRolloutRecord& record = entry.second;
            payload.append("R|");
            payload.append(std::to_string(record.generation));
            payload.push_back('|');
            payload.append(std::to_string(record.updatedAtMs));
            const std::string* strings[] = {
                &record.moduleId, &record.version, &record.artifactDigest,
                &record.configDigest, &record.modelDigest, &record.desiredState};
            for (const std::string* value : strings)
            {
                payload.push_back('|');
                AppendString(payload, *value);
            }
            payload.push_back('\n');
        }
        return payload + "CHECKSUM|" + ChecksumText(Checksum(payload)) + "\n";
    }

    bool ParseDocument(const std::string& document,
                       std::map<std::string, DurableRolloutRecord>& records,
                       std::string& reason) const
    {
        const std::size_t checksumStart = document.rfind("CHECKSUM|");
        if (checksumStart == std::string::npos || checksumStart == 0 ||
            document.back() != '\n')
        {
            reason = "ROLLOUT_STORE_FORMAT_INVALID";
            return false;
        }
        const std::string payload = document.substr(0, checksumStart);
        const std::string checksumLine =
            document.substr(checksumStart, document.size() - checksumStart - 1);
        if (checksumLine.size() != 25 ||
            checksumLine.compare(0, 9, "CHECKSUM|") != 0 ||
            ChecksumText(Checksum(payload)) != checksumLine.substr(9))
        {
            reason = "ROLLOUT_STORE_CHECKSUM_MISMATCH";
            return false;
        }

        std::istringstream input(payload);
        std::string line;
        if (!std::getline(input, line) || line != "HEPTA_ROLLOUT_STORE_V1")
        {
            reason = "ROLLOUT_STORE_VERSION_INVALID";
            return false;
        }
        while (std::getline(input, line))
        {
            if (line.empty()) continue;
            const std::vector<std::string> fields = Split(line);
            if (fields.size() != 9 || fields[0] != "R")
            {
                reason = "ROLLOUT_STORE_RECORD_FORMAT_INVALID";
                return false;
            }
            DurableRolloutRecord record;
            if (!ParseUnsigned(fields[1], record.generation) ||
                !ParseUnsigned(fields[2], record.updatedAtMs) ||
                !ParseString(fields[3], record.moduleId) ||
                !ParseString(fields[4], record.version) ||
                !ParseString(fields[5], record.artifactDigest) ||
                !ParseString(fields[6], record.configDigest) ||
                !ParseString(fields[7], record.modelDigest) ||
                !ParseString(fields[8], record.desiredState) ||
                !ValidRecord(record))
            {
                reason = "ROLLOUT_STORE_RECORD_INVALID";
                return false;
            }
            if (records.size() >= m_maximumRecords ||
                !records.emplace(record.moduleId, record).second)
            {
                reason = "ROLLOUT_STORE_RECORD_DUPLICATE_OR_LIMIT";
                return false;
            }
        }
        return true;
    }

    std::string WriteAtomic(const std::string& document) const
    {
        std::error_code error;
        std::filesystem::path parent = m_path.parent_path();
        if (parent.empty()) parent = ".";
        std::filesystem::create_directories(parent, error);
        if (error) return "ROLLOUT_STORE_DIRECTORY_FAILED";

        std::filesystem::path temporary = m_path;
        temporary += ".tmp";
        {
            std::ofstream output(temporary,
                                 std::ios::binary | std::ios::trunc);
            if (!output) return "ROLLOUT_STORE_TEMP_OPEN_FAILED";
            output.write(document.data(),
                         static_cast<std::streamsize>(document.size()));
            output.flush();
            if (!output) return "ROLLOUT_STORE_TEMP_WRITE_FAILED";
        }

#if !defined(_WIN32)
        const int descriptor = ::open(temporary.c_str(), O_RDONLY);
        if (descriptor < 0) return "ROLLOUT_STORE_TEMP_OPEN_SYNC_FAILED";
        const int syncResult = ::fsync(descriptor);
        const int closeResult = ::close(descriptor);
        if (syncResult != 0 || closeResult != 0)
            return "ROLLOUT_STORE_TEMP_SYNC_FAILED";
#endif

        std::filesystem::rename(temporary, m_path, error);
        if (error)
        {
            std::filesystem::remove(temporary);
            return "ROLLOUT_STORE_RENAME_FAILED";
        }

#if !defined(_WIN32)
        const int directory = ::open(parent.c_str(), O_RDONLY | O_DIRECTORY);
        if (directory < 0) return "ROLLOUT_STORE_DIRECTORY_OPEN_SYNC_FAILED";
        const int directorySyncResult = ::fsync(directory);
        const int directoryCloseResult = ::close(directory);
        if (directorySyncResult != 0 || directoryCloseResult != 0)
            return "ROLLOUT_STORE_DIRECTORY_SYNC_FAILED";
#endif
        return std::string();
    }

    static DurableRolloutResult Accept(
        const char* code, const DurableRolloutRecord* record)
    {
        DurableRolloutResult result;
        result.accepted = true;
        result.reasonCode = code;
        if (record != nullptr) result.record = *record;
        return result;
    }

    static DurableRolloutResult Reject(
        const char* code, const DurableRolloutRecord* record)
    {
        DurableRolloutResult result;
        result.reasonCode = code;
        if (record != nullptr) result.record = *record;
        return result;
    }

    std::filesystem::path m_path;
    std::size_t m_maximumRecords;
    std::size_t m_maximumFileBytes;
    mutable std::mutex m_mutex;
    std::map<std::string, DurableRolloutRecord> m_records;
};
